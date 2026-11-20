import { apiRequest, apiUploadWithProgress, apiDownloadBlob } from '../client'

function buildReportQuery({ startDate = '', endDate = '', courseId = '', classId = '' } = {}) {
  const params = new URLSearchParams()
  if (startDate) params.set('start_date', startDate)
  if (endDate) params.set('end_date', endDate)
  if (courseId) params.set('course_id', courseId)
  if (classId) params.set('class_id', classId)
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export const adminApi = {
  // 域 5：总览与报表导出
  fetchOverview() {
    return apiRequest('/api/v1/admin/overview')
  },
  downloadCoursesCsv(filters = {}) {
    const qs = buildReportQuery(filters)
    return apiDownloadBlob(`/api/v1/admin/reports/courses.csv${qs}`, 'platform_operations_report.csv')
  },
  downloadCoursesXlsx(filters = {}) {
    const qs = buildReportQuery(filters)
    return apiDownloadBlob(`/api/v1/admin/reports/courses.xlsx${qs}`, 'platform_operations_report.xlsx')
  },

  // 域 1：账号、组织架构、批量分班与权限矩阵
  fetchUsers({ role = '', keyword = '' } = {}) {
    const params = new URLSearchParams()
    if (role) params.set('role', role)
    if (keyword.trim()) params.set('keyword', keyword.trim())
    const qs = params.toString()
    return apiRequest(`/api/v1/admin/users${qs ? `?${qs}` : ''}`)
  },
  createUser(payload) {
    return apiRequest('/api/v1/admin/users', { method: 'POST', body: payload })
  },
  importUsers(payload) {
    return apiRequest('/api/v1/admin/users/import', { method: 'POST', body: payload })
  },
  async importUsersXlsx(file, orgId = '', onProgress = null) {
    const makeFd = (fieldName) => {
      const fd = new FormData()
      fd.append(fieldName, file, file.name || 'users.xlsx')
      if (orgId) fd.append('org_id', orgId)
      return fd
    }
    try {
      return await apiUploadWithProgress('/api/v1/admin/users/xlsx-import', makeFd('file'), onProgress)
    } catch (err) {
      if (err?.status === 422 && Array.isArray(err.details)) {
        const d = err.details.find((item) => String(item.field || '').startsWith('body.'))
        const expected = d ? String(d.field).replace(/^body\./, '') : ''
        if (expected && expected !== 'file') {
          return apiUploadWithProgress('/api/v1/admin/users/xlsx-import', makeFd(expected), onProgress)
        }
      }
      throw err
    }
  },
  patchUser(userId, payload) {
    return apiRequest(`/api/v1/admin/users/${encodeURIComponent(userId)}`, { method: 'PATCH', body: payload })
  },
  deleteUser(userId) {
    return apiRequest(`/api/v1/admin/users/${encodeURIComponent(userId)}`, { method: 'DELETE' })
  },
  fetchOrganizations() {
    return apiRequest('/api/v1/admin/organizations')
  },
  createOrganization(payload) {
    return apiRequest('/api/v1/admin/organizations', { method: 'POST', body: payload })
  },
  patchOrganization(orgId, payload) {
    return apiRequest(`/api/v1/admin/organizations/${encodeURIComponent(orgId)}`, { method: 'PATCH', body: payload })
  },
  deleteOrganization(orgId) {
    return apiRequest(`/api/v1/admin/organizations/${encodeURIComponent(orgId)}`, { method: 'DELETE' })
  },
  batchAssignClasses(payload) {
    return apiRequest('/api/v1/admin/classes/batch-assign', { method: 'POST', body: payload })
  },
  fetchPermissionsMatrix() {
    return apiRequest('/api/v1/admin/permissions-matrix')
  },
  savePermissionsMatrix(payload) {
    return apiRequest('/api/v1/admin/permissions-matrix', { method: 'PUT', body: payload })
  },

  // 域 2：课程治理、单资源合规下架、过期清理与分类管理
  fetchCourses() {
    return apiRequest('/api/v1/admin/courses')
  },
  patchCourseGovernance(courseId, payload) {
    return apiRequest(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/governance`, {
      method: 'PATCH',
      body: payload,
    })
  },
  cleanupExpiredCourses() {
    return apiRequest('/api/v1/admin/courses/cleanup-expired', { method: 'POST' })
  },
  fetchCourseResources(courseId) {
    return apiRequest(`/api/v1/admin/courses/${encodeURIComponent(courseId)}/resources`)
  },
  takedownVideo(videoId, payload = { reason: '敏感或版权合规处置下架' }) {
    return apiRequest(`/api/v1/admin/videos/${encodeURIComponent(videoId)}/takedown`, {
      method: 'POST',
      body: payload,
    })
  },
  takedownDocument(docId, payload = { reason: '敏感或版权合规处置下架' }) {
    return apiRequest(`/api/v1/admin/documents/${encodeURIComponent(docId)}/takedown`, {
      method: 'POST',
      body: payload,
    })
  },
  async fetchCategories() {
    try {
      return await apiRequest('/api/v1/admin/categories')
    } catch (err) {
      if (err?.status === 404 || err?.status === 405) {
        return apiRequest('/api/v1/categories')
      }
      throw err
    }
  },
  createCategory(payload) {
    return apiRequest('/api/v1/admin/categories', { method: 'POST', body: payload })
  },
  patchCategory(catId, payload) {
    return apiRequest(`/api/v1/admin/categories/${encodeURIComponent(catId)}`, { method: 'PATCH', body: payload })
  },
  deleteCategory(catId) {
    return apiRequest(`/api/v1/admin/categories/${encodeURIComponent(catId)}`, { method: 'DELETE' })
  },

  // 域 3：AI 生产监控台
  fetchAiJobs({ status = '', courseId = '', startTime = '', endTime = '' } = {}) {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (courseId.trim()) params.set('course_id', courseId.trim())
    if (startTime) params.set('start_time', startTime)
    if (endTime) params.set('end_time', endTime)
    const qs = params.toString()
    return apiRequest(`/api/v1/admin/ai-jobs${qs ? `?${qs}` : ''}`)
  },
  retryAiJob(jobId) {
    return apiRequest(`/api/v1/admin/ai-jobs/${encodeURIComponent(jobId)}/retries`, {
      method: 'POST',
      body: { run_inline: true },
    })
  },
  fetchAiIndexes() {
    return apiRequest('/api/v1/admin/ai-indexes')
  },
  rebuildAiIndex(courseId) {
    return apiRequest(`/api/v1/admin/ai-indexes/${encodeURIComponent(courseId)}/rebuilds`, {
      method: 'POST',
      body: { run_inline: true },
    })
  },

  // 域 4：AI 质量抽检、问题样本队列与运行时策略配置
  drawSpotCheckSamples(payload) {
    return apiRequest('/api/v1/admin/spot-checks/draw', { method: 'POST', body: payload })
  },
  fetchFeedbackSamples() {
    return apiRequest('/api/v1/admin/feedback-samples')
  },
  createSpotCheckSample(payload) {
    return apiRequest('/api/v1/admin/feedback-samples', { method: 'POST', body: payload })
  },
  resolveFeedbackSample(sampleId, payload) {
    return apiRequest(`/api/v1/admin/feedback-samples/${encodeURIComponent(sampleId)}`, {
      method: 'PATCH',
      body: payload,
    })
  },
  fetchAiRuntimeConfig() {
    return apiRequest('/api/v1/admin/ai-runtime-config')
  },
  patchAiRuntimeConfig(payload) {
    return apiRequest('/api/v1/admin/ai-runtime-config', { method: 'PATCH', body: payload })
  },

  // 域 6：系统公告、工单支持、操作审计与健康状态
  async fetchAnnouncements() {
    try {
      return await apiRequest('/api/v1/admin/announcements')
    } catch (err) {
      if (err?.status === 404 || err?.status === 405) {
        return apiRequest('/api/v1/announcements')
      }
      throw err
    }
  },
  publishAnnouncement(payload) {
    return apiRequest('/api/v1/admin/announcements', { method: 'POST', body: payload })
  },
  deleteAnnouncement(annId) {
    return apiRequest(`/api/v1/admin/announcements/${encodeURIComponent(annId)}`, { method: 'DELETE' })
  },
  fetchTickets() {
    return apiRequest('/api/v1/admin/tickets')
  },
  patchTicket(ticketId, payload) {
    return apiRequest(`/api/v1/admin/tickets/${encodeURIComponent(ticketId)}`, { method: 'PATCH', body: payload })
  },
  fetchAuditLogs({ role = '', action = '', keyword = '', startDate = '', endDate = '' } = {}) {
    const params = new URLSearchParams()
    if (role) {
      params.set('actor_role', role)
    }
    const kw = (keyword || action || '').trim()
    if (kw) params.set('keyword', kw)
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
    const qs = params.toString()
    return apiRequest(`/api/v1/admin/audit-logs${qs ? `?${qs}` : ''}`)
  },
  fetchSystemStatus() {
    return apiRequest('/api/v1/admin/system-status')
  },
}
