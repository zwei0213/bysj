<script setup>
import { ref, computed, watch } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { formatRole, formatOrgType, formatStatus } from '../../../utils/labels'
import AdminOrgTreeAndAssign from '../components/AdminOrgTreeAndAssign.vue'
import AdminPermissionsMatrix from '../components/AdminPermissionsMatrix.vue'

const props = defineProps({
  users: { type: Array, required: true },
  orgs: { type: Array, required: true },
  courses: { type: Array, default: () => [] },
})
const emit = defineEmits(['notify', 'refresh'])

const errorTip = ref('')
const filterRole = ref('')
const filterKeyword = ref('')
const localUsers = ref([])

const newUser = ref({ username: '', display_name: '', role: 'student', email: '', password: '', org_id: '' })
const editingUser = ref(null)
const editForm = ref({ display_name: '', email: '', role: 'student', org_id: '' })
const resetPwdTarget = ref(null)
const resetPwdValue = ref('')
const rejectTeacherTarget = ref(null)
const rejectTeacherReason = ref('')

const importMode = ref('xlsx')
const importOrgId = ref('')
const selectedXlsxFile = ref(null)
const batchCsv = ref('')
const importingUsers = ref(false)

watch(() => props.users, (list) => { localUsers.value = [...(list || [])] }, { immediate: true })

const orgMap = computed(() => Object.fromEntries((props.orgs || []).map((o) => [o.id, o])))

const displayedUsers = computed(() => {
  const kw = filterKeyword.value.trim().toLowerCase()
  return localUsers.value.filter((u) => {
    if (filterRole.value && u.role !== filterRole.value) return false
    if (kw) {
      return [u.username, u.display_name, u.email].some((f) => String(f || '').toLowerCase().includes(kw))
    }
    return true
  })
})

async function queryFilteredUsers() {
  try {
    const res = await adminApi.fetchUsers({ role: filterRole.value, keyword: filterKeyword.value })
    localUsers.value = res.items || []
  } catch (err) {
    errorTip.value = err.message
  }
}

async function createUser() {
  errorTip.value = ''
  if (!newUser.value.username.trim() || !newUser.value.display_name.trim()) return
  if (newUser.value.password.length < 8) {
    errorTip.value = '初始密码长度至少为 8 位'
    return
  }
  try {
    await adminApi.createUser({
      username: newUser.value.username.trim(),
      display_name: newUser.value.display_name.trim(),
      email: newUser.value.email.trim(),
      password: newUser.value.password,
      role: newUser.value.role,
      org_id: newUser.value.org_id || null,
    })
    newUser.value = { username: '', display_name: '', role: 'student', email: '', password: '', org_id: '' }
    emit('notify', '新账号已成功开通。')
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message
  }
}

function openEditUser(u) {
  editingUser.value = u
  editForm.value = { display_name: u.display_name || '', email: u.email || '', role: u.role || 'student', org_id: u.org_id || '' }
}

async function saveEditUser() {
  if (!editingUser.value) return
  errorTip.value = ''
  try {
    await adminApi.patchUser(editingUser.value.id, {
      display_name: editForm.value.display_name.trim(),
      email: editForm.value.email.trim(),
      role: editForm.value.role,
      org_id: editForm.value.org_id || null,
    })
    editingUser.value = null
    emit('notify', '用户资料与角色组织已更新。')
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message
  }
}

async function confirmResetPassword() {
  if (!resetPwdTarget.value) return
  if (resetPwdValue.value.length < 8) {
    errorTip.value = '重置的新密码至少需要 8 位字符'
    return
  }
  errorTip.value = ''
  try {
    await adminApi.patchUser(resetPwdTarget.value.id, { reset_password: resetPwdValue.value })
    emit('notify', `账号 ${resetPwdTarget.value.username} 的登录密码已重置。`)
    resetPwdTarget.value = null
    resetPwdValue.value = ''
  } catch (err) {
    errorTip.value = err.message
  }
}

async function toggleUserActive(user) {
  await adminApi.patchUser(user.id, { is_active: !user.is_active })
  emit('notify', `账号 ${user.username} 状态已更新。`)
  emit('refresh')
}

async function approveTeacher(user) {
  await adminApi.patchUser(user.id, { teacher_approved: true, teacher_status: 'approved', reject_reason: '' })
  emit('notify', `已批准教师「${user.display_name}」的授课资质。`)
  emit('refresh')
}

async function confirmRejectTeacher() {
  if (!rejectTeacherTarget.value) return
  const reason = rejectTeacherReason.value.trim() || '请补充院系教工证明后重新申请'
  try {
    await adminApi.patchUser(rejectTeacherTarget.value.id, { teacher_approved: false, teacher_status: 'rejected', reject_reason: reason })
    emit('notify', `已驳回教师「${rejectTeacherTarget.value.display_name}」的资质申请。`)
    rejectTeacherTarget.value = null
    rejectTeacherReason.value = ''
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message
  }
}

async function removeUser(user) {
  errorTip.value = ''
  try {
    await adminApi.deleteUser(user.id)
    emit('notify', `账号 ${user.username} 已删除。`)
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message || '删除用户失败'
  }
}

function onPickXlsx(e) {
  selectedXlsxFile.value = e.target.files?.[0] || null
}

async function handleBatchImport() {
  errorTip.value = ''
  importingUsers.value = true
  try {
    if (importMode.value === 'xlsx') {
      if (!selectedXlsxFile.value) {
        errorTip.value = '请先选择要上传的表格数据文件'
        return
      }
      const res = await adminApi.importUsersXlsx(selectedXlsxFile.value, importOrgId.value)
      selectedXlsxFile.value = null
      emit('notify', `表格导入完成：新增 ${res?.created_count ?? 0} 人，跳过已存在 ${res?.skipped_count ?? 0} 人。`)
    } else {
      if (!batchCsv.value.trim()) return
      const res = await adminApi.importUsers({ csv_text: batchCsv.value, org_id: importOrgId.value || null })
      batchCsv.value = ''
      emit('notify', `文本批量导入完成：新增 ${res.created_count} 人，跳过已存在 ${res.skipped_count} 人。`)
    }
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message
  } finally {
    importingUsers.value = false
  }
}
</script>

<template>
  <div>
    <div v-if="errorTip" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorTip }}</span>
    </div>

    <!-- 1. 账号筛选、开通、教师审批与密码重置 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">平台账号管理与教师资质审批</h2>
        <span class="label-text">显示 {{ displayedUsers.length }} / {{ localUsers.length }} 名用户</span>
      </div>

      <div class="filter-bar">
        <select v-model="filterRole" class="select" style="width: 130px;" @change="queryFilteredUsers">
          <option value="">全部角色</option>
          <option value="student">学生</option>
          <option value="teacher">授课教师</option>
          <option value="admin">平台管理员</option>
        </select>
        <input v-model="filterKeyword" class="input" style="flex: 1; min-width: 180px;" placeholder="搜索登录账号、姓名或邮箱..." @keyup.enter="queryFilteredUsers" />
        <button type="button" class="btn btn-outline btn-sm" @click="queryFilteredUsers">筛选</button>
      </div>

      <div class="card inline-create">
        <input v-model="newUser.username" class="input" style="width: 115px;" placeholder="登录账号" />
        <input v-model="newUser.display_name" class="input" style="width: 105px;" placeholder="姓名" />
        <input v-model="newUser.email" class="input" style="width: 145px;" placeholder="联系邮箱（选填）" />
        <input v-model="newUser.password" type="password" class="input" style="width: 145px;" placeholder="初始密码（不少于8位）" />
        <select v-model="newUser.role" class="select" style="width: 116px;">
          <option value="student">学生</option>
          <option value="teacher">授课教师</option>
          <option value="admin">平台管理员</option>
        </select>
        <select v-model="newUser.org_id" class="select" style="width: 145px;">
          <option value="">所属组织（选填）</option>
          <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.name }}</option>
        </select>
        <button type="button" class="btn btn-primary btn-sm" @click="createUser">开通账号</button>
      </div>

      <div class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>登录账号 / 邮箱</th>
              <th>姓名 / 所属组织</th>
              <th>角色与资质</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in displayedUsers" :key="u.id">
              <td>
                <code>{{ u.username }}</code>
                <div v-if="u.email" class="label-text">{{ u.email }}</div>
              </td>
              <td>
                <div>{{ u.display_name }}</div>
                <div v-if="u.org_id && orgMap[u.org_id]" class="label-text">{{ orgMap[u.org_id].name }}</div>
              </td>
              <td>
                <span class="badge">{{ formatRole(u.role) }}</span>
                <span v-if="u.role === 'teacher' && u.teacher_status === 'rejected'" class="badge badge-danger" style="margin-left: 4px;" :title="u.reject_reason">
                  {{ formatStatus('rejected') }}
                </span>
                <span v-else-if="u.role === 'teacher' && !u.teacher_approved" class="badge badge-danger" style="margin-left: 4px;">
                  待审资质
                </span>
              </td>
              <td>
                <span class="badge" :class="{ 'badge-danger': !u.is_active }">{{ u.is_active ? '正常' : '已停用' }}</span>
              </td>
              <td>
                <div class="row-actions">
                  <button v-if="u.role === 'teacher' && !u.teacher_approved" type="button" class="btn btn-primary btn-sm" @click="approveTeacher(u)">批准</button>
                  <button v-if="u.role === 'teacher' && !u.teacher_approved" type="button" class="btn btn-sm btn-danger" @click="rejectTeacherTarget = u; rejectTeacherReason = ''">驳回</button>
                  <button type="button" class="btn btn-sm" @click="openEditUser(u)">编辑</button>
                  <button type="button" class="btn btn-sm" @click="resetPwdTarget = u; resetPwdValue = ''">重置密码</button>
                  <button v-if="u.role !== 'admin'" type="button" class="btn btn-sm" @click="toggleUserActive(u)">{{ u.is_active ? '停用' : '启用' }}</button>
                  <button v-if="u.role !== 'admin'" type="button" class="btn btn-sm btn-danger" @click="removeUser(u)">删除</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 2. 批量导入师生账号 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">批量导入师生账号</h2>
          <div class="label-text">支持上传电子表格、纯文本表格文件，或直接粘贴多行账号数据</div>
        </div>
        <div class="mode-switch">
          <button type="button" class="btn btn-sm" :class="{ 'is-mode': importMode === 'xlsx' }" @click="importMode = 'xlsx'">上传表格文件</button>
          <button type="button" class="btn btn-sm" :class="{ 'is-mode': importMode === 'csv' }" @click="importMode = 'csv'">粘贴文本数据</button>
        </div>
      </div>

      <div class="card form-col">
        <select v-model="importOrgId" class="select" style="max-width: 280px;">
          <option value="">归属组织节点（选填）</option>
          <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.name }}（{{ formatOrgType(o.org_type) }}）</option>
        </select>
        <div class="label-text">表头格式：账号,姓名,角色（学生/教师/管理员）,邮箱,初始密码（不少于8位）</div>
        <div v-if="importMode === 'xlsx'" class="file-box">
          <label class="label-text">选择本地电子表格或逗号分隔文本文件上传</label>
          <input type="file" accept=".xlsx,.csv" @change="onPickXlsx" />
        </div>
        <textarea v-else v-model="batchCsv" class="textarea" rows="4" placeholder="账号,姓名,角色,邮箱,初始密码&#10;2026001,王晓明,学生,wangxm@edu.cn,88888888!"></textarea>
        <div>
          <button type="button" class="btn btn-primary btn-sm" :disabled="importingUsers" @click="handleBatchImport">
            {{ importingUsers ? '导入执行中...' : '执行批量导入' }}
          </button>
        </div>
      </div>
    </section>

    <!-- 3 & 4. 组织树与学生批量分班 -->
    <AdminOrgTreeAndAssign
      :orgs="orgs"
      :users="localUsers"
      @notify="(msg) => emit('notify', msg)"
      @error="(msg) => (errorTip = msg)"
      @refresh="emit('refresh')"
    />

    <!-- 5. 三角色权限矩阵 -->
    <AdminPermissionsMatrix
      @notify="(msg) => emit('notify', msg)"
      @error="(msg) => (errorTip = msg)"
    />

    <!-- 弹窗：编辑用户 / 重置密码 / 驳回教师 -->
    <div v-if="editingUser" class="modal-backdrop" @click.self="editingUser = null">
      <div class="popover-surface modal-sm" role="dialog" aria-modal="true">
        <div class="section-header">
          <h3 class="section-title">编辑用户：{{ editingUser.username }}</h3>
          <button type="button" class="btn btn-sm" @click="editingUser = null">关闭</button>
        </div>
        <div class="form-col">
          <label class="label-text">姓名 / 昵称</label>
          <input v-model="editForm.display_name" class="input" />
          <label class="label-text">联系邮箱</label>
          <input v-model="editForm.email" class="input" />
          <label class="label-text">系统角色</label>
          <select v-model="editForm.role" class="select">
            <option value="student">学生</option>
            <option value="teacher">授课教师</option>
            <option value="admin">平台管理员</option>
          </select>
          <label class="label-text">所属组织节点</label>
          <select v-model="editForm.org_id" class="select">
            <option value="">未绑定组织</option>
            <option v-for="o in orgs" :key="o.id" :value="o.id">{{ o.name }}</option>
          </select>
          <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px;">
            <button type="button" class="btn" @click="editingUser = null">取消</button>
            <button type="button" class="btn btn-primary" @click="saveEditUser">保存更改</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="resetPwdTarget" class="modal-backdrop" @click.self="resetPwdTarget = null">
      <div class="popover-surface modal-sm" role="dialog" aria-modal="true">
        <div class="section-header">
          <h3 class="section-title">重置密码：{{ resetPwdTarget.username }}</h3>
          <button type="button" class="btn btn-sm" @click="resetPwdTarget = null">关闭</button>
        </div>
        <div class="form-col">
          <label class="label-text">输入新登录密码（不少于 8 位）</label>
          <input v-model="resetPwdValue" type="password" class="input" placeholder="至少 8 位字符..." />
          <div style="display: flex; justify-content: flex-end; gap: 8px;">
            <button type="button" class="btn" @click="resetPwdTarget = null">取消</button>
            <button type="button" class="btn btn-primary" @click="confirmResetPassword">确认重置</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="rejectTeacherTarget" class="modal-backdrop" @click.self="rejectTeacherTarget = null">
      <div class="popover-surface modal-sm" role="dialog" aria-modal="true">
        <div class="section-header">
          <h3 class="section-title">驳回教师资质：{{ rejectTeacherTarget.display_name }}</h3>
          <button type="button" class="btn btn-sm" @click="rejectTeacherTarget = null">关闭</button>
        </div>
        <div class="form-col">
          <label class="label-text">驳回原因（教师登录后将在顶栏看到此意见）</label>
          <textarea v-model="rejectTeacherReason" class="textarea" rows="3" placeholder="例如：请提供所属院系工号核验信息..."></textarea>
          <div style="display: flex; justify-content: flex-end; gap: 8px;">
            <button type="button" class="btn" @click="rejectTeacherTarget = null">取消</button>
            <button type="button" class="btn btn-primary" @click="confirmRejectTeacher">确认驳回</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.filter-bar { display: flex; align-items: center; gap: var(--spacing-8); flex-wrap: wrap; margin-bottom: var(--spacing-12); }
.inline-create { display: flex; align-items: center; gap: var(--spacing-8); flex-wrap: wrap; margin-bottom: var(--spacing-16); background-color: var(--surface-white-page); }
.row-actions { display: flex; gap: var(--spacing-4); flex-wrap: wrap; }
.form-col { display: flex; flex-direction: column; gap: var(--spacing-12); }
.mode-switch { display: flex; gap: var(--spacing-4); }
.is-mode { background-color: var(--color-hover-grey); font-weight: var(--font-weight-semibold); }
.file-box { display: flex; flex-direction: column; gap: var(--spacing-4); padding: var(--spacing-8) var(--spacing-12); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--surface-white-page); }
.modal-sm { width: min(440px, 100%); }
</style>
