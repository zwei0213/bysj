<script setup>
import { ref, watch } from 'vue'
import { teacherApi } from '../../../api/modules/teacher'

const props = defineProps({
  course: { type: Object, required: true },
})
const emit = defineEmits(['notify', 'error', 'refresh-courses'])

const newClassName = ref('')
const newClassTerm = ref('2026秋季学期')
const filterClassId = ref('')
const enrolledStudents = ref([])
const loadingStudents = ref(false)
const rosterImportMode = ref('file')
const selectedRosterFile = ref(null)
const rosterBatchText = ref('')
const importClassId = ref('')
const importGroupName = ref('')
const importingRoster = ref(false)

async function loadCourseStudents() {
  if (!props.course?.id) return
  loadingStudents.value = true
  try {
    const res = await teacherApi.fetchCourseStudents(props.course.id, filterClassId.value)
    const items = res?.items || (Array.isArray(res) ? res : [])
    enrolledStudents.value = items.map((s) => ({
      ...s,
      _enrollmentId: s.enrollment_id || s.id,
      _classId: s.class_id || '',
      _groupName: s.group_name || '',
    }))
  } catch {
    enrolledStudents.value = []
  } finally {
    loadingStudents.value = false
  }
}

watch(
  () => props.course?.id,
  () => {
    filterClassId.value = ''
    loadCourseStudents()
  },
  { immediate: true }
)

async function addClassGroup() {
  if (!newClassName.value.trim()) return
  await teacherApi.createClassGroup(props.course.id, {
    name: newClassName.value.trim(),
    term: newClassTerm.value.trim() || '2026秋季学期',
  })
  newClassName.value = ''
  emit('notify', '教学班级已创建。')
  emit('refresh-courses')
}

function onPickRosterFile(e) {
  selectedRosterFile.value = e.target.files?.[0] || null
}

async function handleImportRoster() {
  if (importingRoster.value) return
  emit('error', '')
  importingRoster.value = true
  try {
    if (rosterImportMode.value === 'file') {
      if (!selectedRosterFile.value) {
        emit('error', '请先选择要上传的电子表格名单文件')
        return
      }
      const res = await teacherApi.importCourseStudentsXlsx(props.course.id, selectedRosterFile.value, {
        class_id: importClassId.value,
        group_name: importGroupName.value.trim(),
      })
      selectedRosterFile.value = null
      emit('notify', `班级名单导入完成（新增/关联 ${res?.enrolled_count ?? res?.created_count ?? '若干'} 人）。`)
    } else {
      const raw = rosterBatchText.value.trim()
      if (!raw) {
        emit('error', '请输入学生账号列表（每行输入一个学生账号或学号，也可用逗号分隔）')
        return
      }
      const usernames = raw.split(/[\r\n,;，；]+/).map((s) => s.trim()).filter(Boolean)
      await teacherApi.batchAddCourseStudents(props.course.id, {
        usernames,
        csv_text: raw,
        class_id: importClassId.value || null,
        group_name: importGroupName.value.trim() || null,
      })
      rosterBatchText.value = ''
      emit('notify', '批量添加选课学员完成。')
    }
    await loadCourseStudents()
  } catch (err) {
    emit('error', err.message || '导入班级名单失败')
  } finally {
    importingRoster.value = false
  }
}

async function saveStudentEnrollment(stu) {
  try {
    await teacherApi.updateEnrollment(stu._enrollmentId, {
      class_id: stu._classId || null,
      group_name: stu._groupName.trim(),
    })
    emit('notify', `已更新学员「${stu.display_name || stu.username}」的班级与学习小组。`)
    await loadCourseStudents()
  } catch (err) {
    emit('error', err.message || '更新学生分组失败')
  }
}

async function removeStudentEnrollment(stu) {
  try {
    await teacherApi.removeEnrollment(stu._enrollmentId)
    emit('notify', `已将「${stu.display_name || stu.username}」移出本课程。`)
    await loadCourseStudents()
  } catch (err) {
    emit('error', err.message || '移除学生失败')
  }
}
</script>

<template>
  <section class="doc-section">
    <div class="section-header">
      <div>
        <h2 class="section-title">教学班级、学生名单与学习小组</h2>
        <div class="label-text">支持电子表格导入班级名单、调整所属教学班及划分学习小组</div>
      </div>
    </div>

    <div class="class-tags" v-if="course.classes?.length">
      <span v-for="cl in course.classes" :key="cl.id" class="badge">{{ cl.name }} ({{ cl.term }})</span>
    </div>

    <div class="inline-bar" style="margin-bottom: 16px;">
      <input v-model="newClassName" class="input" style="max-width: 220px;" placeholder="新增教学班名称..." @keyup.enter="addClassGroup" />
      <input v-model="newClassTerm" class="input" style="max-width: 150px;" placeholder="学期（如2026秋）" />
      <button type="button" class="btn btn-outline btn-sm" @click="addClassGroup">添加教学班</button>
    </div>

    <div class="card form-col" style="margin-bottom: 16px;">
      <div class="section-header" style="margin-bottom: 4px;">
        <span class="label-text">导入选课学生名单</span>
        <div class="mode-switch">
          <button type="button" class="btn btn-sm" :class="{ 'is-mode': rosterImportMode === 'file' }" @click="rosterImportMode = 'file'">
            电子表格文件导入
          </button>
          <button type="button" class="btn btn-sm" :class="{ 'is-mode': rosterImportMode === 'text' }" @click="rosterImportMode = 'text'">
            账号文本批量添加
          </button>
        </div>
      </div>

      <div class="grid-2">
        <select v-model="importClassId" class="select">
          <option value="">归入默认班级</option>
          <option v-for="cl in (course.classes || [])" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
        </select>
        <input v-model="importGroupName" class="input" placeholder="可选：统一指定学习小组（如：第一研讨组）" />
      </div>

      <div v-if="rosterImportMode === 'file'" class="file-row">
        <label class="label-text">支持上传电子表格文件（包含“学号/账号、姓名、学习小组、邮箱”等列）</label>
        <input type="file" accept=".xlsx,.csv" @change="onPickRosterFile" />
      </div>
      <textarea
        v-else
        v-model="rosterBatchText"
        class="textarea"
        rows="2"
        placeholder="每行输入一个学生账号或学号（也可用逗号分隔）"
      ></textarea>

      <div>
        <button type="button" class="btn btn-primary btn-sm" :disabled="importingRoster" @click="handleImportRoster">
          {{ importingRoster ? '导入中...' : '执行名单导入' }}
        </button>
      </div>
    </div>

    <div class="section-header" style="margin-bottom: 8px;">
      <span class="label-text">选课学员花名册 ({{ enrolledStudents.length }} 人)</span>
      <select v-model="filterClassId" class="select" style="width: auto; min-width: 160px;" @change="loadCourseStudents">
        <option value="">全部教学班</option>
        <option v-for="cl in (course.classes || [])" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
      </select>
    </div>

    <p v-if="loadingStudents" class="muted-text">正在读取班级花名册...</p>
    <div v-else-if="enrolledStudents.length" class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>学员姓名 / 账号</th>
            <th>所属教学班</th>
            <th>学习小组</th>
            <th>修读进度</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="stu in enrolledStudents" :key="stu._enrollmentId">
            <td>
              <div><strong>{{ stu.display_name || stu.student_name || stu.username }}</strong></div>
              <div class="label-text"><code>{{ stu.username || stu.student_id }}</code></div>
            </td>
            <td>
              <select v-model="stu._classId" class="select" style="min-width: 130px;">
                <option value="">未分班</option>
                <option v-for="cl in (course.classes || [])" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
              </select>
            </td>
            <td>
              <input v-model="stu._groupName" class="input" style="min-width: 120px;" placeholder="输入小组名称..." @keyup.enter="saveStudentEnrollment(stu)" />
            </td>
            <td>{{ Math.round(Number(stu.progress_pct || 0)) }}%</td>
            <td>
              <div style="display: flex; gap: 4px;">
                <button type="button" class="btn btn-sm btn-outline" @click="saveStudentEnrollment(stu)">保存</button>
                <button type="button" class="btn btn-sm btn-danger" @click="removeStudentEnrollment(stu)">移除</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="muted-text">当前班级暂无选课学员，可通过上方电子表格导入或等待学生自主选课。</p>
  </section>
</template>

<style scoped>
.form-col { display: flex; flex-direction: column; gap: var(--spacing-12); }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--spacing-12); }
@media (max-width: 640px) { .grid-2 { grid-template-columns: 1fr; } }
.file-row { display: flex; flex-direction: column; gap: var(--spacing-4); padding: var(--spacing-8) var(--spacing-12); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--surface-white-page); }
.mode-switch { display: flex; gap: var(--spacing-4); }
.is-mode { background-color: var(--color-hover-grey); font-weight: var(--font-weight-semibold); }
.class-tags { display: flex; gap: var(--spacing-8); flex-wrap: wrap; margin-bottom: var(--spacing-12); }
.inline-bar { display: flex; align-items: center; gap: var(--spacing-8); flex-wrap: wrap; }
</style>
