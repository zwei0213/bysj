<script setup>
import { ref, onMounted } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { formatPermissionKey } from '../../../utils/labels'

const emit = defineEmits(['notify', 'error'])

const permRows = ref([])
const savingPerms = ref(false)

const DEFAULT_PERM_DEFS = [
  { key: 'course:browse_enroll', label: '浏览与选修上架课程' },
  { key: 'tutor:ask_rag', label: '调用课程智能助教问答与资料溯源' },
  { key: 'quiz:self_practice', label: '发起章节自测、错题重做与变式练习' },
  { key: 'studio:manage_course', label: '创建课程、上传音视频讲义与班级管理' },
  { key: 'studio:calibrate_outline', label: '校准发布视频提纲与审阅题库组卷' },
  { key: 'admin:govern_platform', label: '全站账号审批、智能监控抽检与合规下架' },
]

async function loadPermissionsMatrix() {
  try {
    const res = await adminApi.fetchPermissionsMatrix()
    const byRole = res?.roles || res?.matrix || res
    if (byRole && typeof byRole === 'object' && byRole.student && typeof byRole.student === 'object' && !Array.isArray(byRole.student)) {
      const keys = Object.keys({ ...byRole.student, ...byRole.teacher, ...byRole.admin })
      permRows.value = keys.map((k) => {
        const def = DEFAULT_PERM_DEFS.find((d) => d.key === k)
        const mapped = formatPermissionKey(k)
        return {
          key: k,
          label: mapped !== k ? mapped : (res?.labels?.[k] || def?.label || '系统功能权限'),
          student: Boolean(byRole.student?.[k]),
          teacher: Boolean(byRole.teacher?.[k]),
          admin: byRole.admin?.[k] !== undefined ? Boolean(byRole.admin[k]) : true,
        }
      })
      return
    }
  } catch {
    // 回退默认矩阵
  }
  permRows.value = DEFAULT_PERM_DEFS.map((d) => ({
    key: d.key,
    label: formatPermissionKey(d.key) !== d.key ? formatPermissionKey(d.key) : d.label,
    student: d.key.startsWith('course:') || d.key.startsWith('tutor:') || d.key.startsWith('quiz:'),
    teacher: !d.key.startsWith('admin:'),
    admin: true,
  }))
}

async function savePermissionsMatrix() {
  savingPerms.value = true
  emit('error', '')
  try {
    const rolesPayload = { student: {}, teacher: {}, admin: {} }
    for (const r of permRows.value) {
      rolesPayload.student[r.key] = Boolean(r.student)
      rolesPayload.teacher[r.key] = Boolean(r.teacher)
      rolesPayload.admin[r.key] = Boolean(r.admin)
    }
    await adminApi.savePermissionsMatrix({ roles: rolesPayload })
    emit('notify', '三角色权限矩阵配置已保存生效。')
  } catch (err) {
    emit('error', err.message || '保存权限矩阵失败')
  } finally {
    savingPerms.value = false
  }
}

onMounted(loadPermissionsMatrix)
</script>

<template>
  <section class="doc-section">
    <div class="section-header">
      <div>
        <h2 class="section-title">三角色权限矩阵配置</h2>
        <div class="label-text">集中管控 学生 / 授课教师 / 平台管理员 的功能域访问权限</div>
      </div>
      <button type="button" class="btn btn-primary btn-sm" :disabled="savingPerms" @click="savePermissionsMatrix">
        {{ savingPerms ? '保存中...' : '保存权限矩阵' }}
      </button>
    </div>

    <div class="table-scroll">
      <table class="data-table">
        <thead>
          <tr>
            <th>功能权限项</th>
            <th>学生</th>
            <th>授课教师</th>
            <th>平台管理员</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in permRows" :key="p.key">
            <td>{{ formatPermissionKey(p.key) !== p.key ? formatPermissionKey(p.key) : p.label }}</td>
            <td><input type="checkbox" v-model="p.student" /></td>
            <td><input type="checkbox" v-model="p.teacher" /></td>
            <td><input type="checkbox" v-model="p.admin" disabled /></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
