<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { teacherApi } from '../../../api/modules/teacher'
import { formatOrgType } from '../../../utils/labels'

const props = defineProps({
  orgs: { type: Array, required: true },
  users: { type: Array, required: true },
})
const emit = defineEmits(['notify', 'error', 'refresh'])

const newOrg = ref({ name: '', org_type: 'class', parent_id: '' })
const editingOrgId = ref('')
const editOrgName = ref('')

const studioCourses = ref([])
const assignForm = ref({
  course_id: '',
  class_id: '',
  group_name: '',
  source_org_id: '',
  selectedStudentIds: [],
  usernamesText: '',
})

const studentUsers = computed(() => (props.users || []).filter((u) => u.role === 'student'))
const selectedAssignCourseClasses = computed(() => {
  const found = studioCourses.value.find((c) => c.id === assignForm.value.course_id)
  return found?.classes || []
})

watch(
  () => assignForm.value.course_id,
  () => {
    assignForm.value.class_id = selectedAssignCourseClasses.value[0]?.id || ''
  }
)

const orgTreeNodes = computed(() => {
  const list = props.orgs || []
  const byParent = new Map()
  const idSet = new Set(list.map((o) => o.id))
  for (const o of list) {
    const pid = o.parent_id && idSet.has(o.parent_id) ? o.parent_id : '__root__'
    if (!byParent.has(pid)) byParent.set(pid, [])
    byParent.get(pid).push(o)
  }
  const out = []
  const visited = new Set()
  function dfs(pid, depth) {
    for (const node of byParent.get(pid) || []) {
      if (visited.has(node.id)) continue
      visited.add(node.id)
      out.push({ ...node, depth })
      dfs(node.id, depth + 1)
    }
  }
  dfs('__root__', 0)
  for (const node of list) {
    if (!visited.has(node.id)) out.push({ ...node, depth: 0 })
  }
  return out
})

async function createOrg() {
  if (!newOrg.value.name.trim()) return
  await adminApi.createOrganization({
    name: newOrg.value.name.trim(),
    org_type: newOrg.value.org_type,
    parent_id: newOrg.value.parent_id || null,
  })
  newOrg.value = { name: '', org_type: 'class', parent_id: '' }
  emit('notify', '组织架构节点已创建。')
  emit('refresh')
}

function startEditOrg(o) {
  editingOrgId.value = o.id
  editOrgName.value = o.name
}

async function saveEditOrg(o) {
  if (!editOrgName.value.trim()) return
  await adminApi.patchOrganization(o.id, { name: editOrgName.value.trim() })
  editingOrgId.value = ''
  emit('notify', '组织节点名称已更新。')
  emit('refresh')
}

async function removeOrg(o) {
  emit('error', '')
  try {
    await adminApi.deleteOrganization(o.id)
    emit('notify', `组织节点「${o.name}」已删除。`)
    emit('refresh')
  } catch (err) {
    emit('error', err.message || '删除组织节点失败')
  }
}

async function loadStudioCoursesWithClasses() {
  try {
    const res = await teacherApi.fetchCourses()
    studioCourses.value = res?.items || []
    if (!assignForm.value.course_id && studioCourses.value.length) {
      assignForm.value.course_id = studioCourses.value[0].id
      assignForm.value.class_id = studioCourses.value[0].classes?.[0]?.id || ''
    }
  } catch {
    studioCourses.value = []
  }
}

async function handleBatchAssignClass() {
  emit('error', '')
  if (!assignForm.value.course_id || !assignForm.value.class_id) {
    emit('error', '请选择目标课程及该课程下的教学班级')
    return
  }
  const extraUsernames = assignForm.value.usernamesText
    .split(/[\r\n,;，；]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean)
  const matchedIds = (props.users || [])
    .filter((u) => extraUsernames.includes(String(u.username || '').toLowerCase()))
    .map((u) => u.id)
  const mergedStudentIds = Array.from(new Set([...assignForm.value.selectedStudentIds, ...matchedIds]))
  if (!mergedStudentIds.length && !assignForm.value.source_org_id) {
    emit('error', '请勾选要分班的学生、输入用户名或选择来源组织节点')
    return
  }
  try {
    const res = await adminApi.batchAssignClasses({
      course_id: assignForm.value.course_id,
      class_id: assignForm.value.class_id,
      student_ids: mergedStudentIds,
      source_org_id: assignForm.value.source_org_id || null,
      group_name: assignForm.value.group_name.trim(),
    })
    assignForm.value.selectedStudentIds = []
    assignForm.value.usernamesText = ''
    emit('notify', `学生批量分班完成：共分配 ${res?.assigned_count ?? mergedStudentIds.length} 名学生。`)
    emit('refresh')
  } catch (err) {
    emit('error', err.message || '批量分班失败')
  }
}

onMounted(loadStudioCoursesWithClasses)
</script>

<template>
  <div>
    <!-- 组织架构层级树 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">组织架构层级树</h2>
          <div class="label-text">按 院系 / 部门 → 年级 / 专业 → 行政教学班 建立树形归属</div>
        </div>
      </div>

      <div class="inline-create">
        <input v-model="newOrg.name" class="input" style="flex: 1; min-width: 160px;" placeholder="新增院系、年级或班级名称..." />
        <select v-model="newOrg.org_type" class="select" style="width: 136px;">
          <option value="department">院系 / 部门</option>
          <option value="grade">年级 / 专业</option>
          <option value="class">行政教学班</option>
        </select>
        <select v-model="newOrg.parent_id" class="select" style="width: 170px;">
          <option value="">作为顶级节点</option>
          <option v-for="o in orgTreeNodes" :key="o.id" :value="o.id">{{ '　'.repeat(o.depth) }}{{ o.name }}</option>
        </select>
        <button type="button" class="btn btn-outline btn-sm" @click="createOrg">添加节点</button>
      </div>

      <div class="block-list" v-if="orgTreeNodes.length">
        <div v-for="o in orgTreeNodes" :key="o.id" class="block-row org-row" :style="{ paddingLeft: `${8 + o.depth * 22}px` }">
          <span class="tree-branch" aria-hidden="true">{{ o.depth > 0 ? '└─' : '●' }}</span>
          <div class="block-body org-line">
            <template v-if="editingOrgId === o.id">
              <div style="display: flex; gap: 6px; flex: 1;">
                <input v-model="editOrgName" class="input" @keyup.enter="saveEditOrg(o)" />
                <button type="button" class="btn btn-primary btn-sm" @click="saveEditOrg(o)">保存</button>
                <button type="button" class="btn btn-sm" @click="editingOrgId = ''">取消</button>
              </div>
            </template>
            <template v-else>
              <div>
                <strong>{{ o.name }}</strong>
                <span class="badge" style="margin-left: 8px;">{{ formatOrgType(o.org_type) }}</span>
              </div>
              <div class="row-actions">
                <button type="button" class="btn btn-sm" @click="startEditOrg(o)">改名</button>
                <button type="button" class="btn btn-sm btn-danger" @click="removeOrg(o)">删除</button>
              </div>
            </template>
          </div>
        </div>
      </div>
    </section>

    <!-- 学生批量分班调配 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">学生批量分班调配</h2>
          <div class="label-text">将所选学生批量划入指定课程教学班与学习小组</div>
        </div>
      </div>

      <div class="card form-col">
        <div class="grid-3">
          <div class="field">
            <label class="label-text">目标课程</label>
            <select v-model="assignForm.course_id" class="select">
              <option value="">请选择课程</option>
              <option v-for="c in studioCourses" :key="c.id" :value="c.id">{{ c.title }}</option>
            </select>
          </div>
          <div class="field">
            <label class="label-text">目标教学班</label>
            <select v-model="assignForm.class_id" class="select">
              <option value="">请选择教学班</option>
              <option v-for="cl in selectedAssignCourseClasses" :key="cl.id" :value="cl.id">
                {{ cl.name }}<template v-if="cl.term">（{{ cl.term }}）</template>
              </option>
            </select>
          </div>
          <div class="field">
            <label class="label-text">可选按来源组织整组导入</label>
            <select v-model="assignForm.source_org_id" class="select">
              <option value="">不限定来源组织</option>
              <option v-for="o in orgTreeNodes" :key="o.id" :value="o.id">{{ o.name }}（{{ formatOrgType(o.org_type) }}）</option>
            </select>
          </div>
        </div>

        <input v-model="assignForm.group_name" class="input" placeholder="选填：统一分配学习小组名称（如：第一研讨组）..." />

        <div v-if="studentUsers.length" class="student-pick-grid">
          <label v-for="stu in studentUsers" :key="stu.id" class="stu-chk">
            <input type="checkbox" :value="stu.id" v-model="assignForm.selectedStudentIds" />
            <span>{{ stu.display_name }}（<code>{{ stu.username }}</code>）</span>
          </label>
        </div>

        <input v-model="assignForm.usernamesText" class="input" placeholder="或直接输入学生登录账号列表（以逗号或换行分隔）..." />

        <div>
          <button type="button" class="btn btn-primary btn-sm" @click="handleBatchAssignClass">
            执行批量分班（已勾选 {{ assignForm.selectedStudentIds.length }} 人）
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.inline-create { display: flex; align-items: center; gap: var(--spacing-8); flex-wrap: wrap; margin-bottom: var(--spacing-16); background-color: var(--surface-white-page); }
.row-actions { display: flex; gap: var(--spacing-4); flex-wrap: wrap; }
.form-col { display: flex; flex-direction: column; gap: var(--spacing-12); }
.org-row { margin-left: 0; border-bottom: 1px solid var(--color-hairline); align-items: center; }
.tree-branch { font-family: var(--font-mono); font-size: 12px; color: var(--color-ink-muted); width: 20px; flex-shrink: 0; }
.org-line { display: flex; justify-content: space-between; align-items: center; gap: var(--spacing-8); }
.grid-3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--spacing-8); }
@media (max-width: 680px) { .grid-3 { grid-template-columns: 1fr; } }
.field { display: flex; flex-direction: column; gap: 2px; }
.student-pick-grid { display: flex; flex-wrap: wrap; gap: var(--spacing-8); max-height: 150px; overflow-y: auto; padding: var(--spacing-8); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--surface-white-page); }
.stu-chk { display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; cursor: pointer; }
</style>
