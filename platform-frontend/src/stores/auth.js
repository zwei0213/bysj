import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { commonApi } from '../api/modules/common'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('slp_access_token') || '')
  const refreshToken = ref(localStorage.getItem('slp_refresh_token') || '')
  const user = ref(JSON.parse(localStorage.getItem('slp_user') || 'null'))

  const isAuthenticated = computed(() => Boolean(token.value && user.value))
  const role = computed(() => user.value?.role || '')
  const isTeacherApproved = computed(() => {
    if (!user.value) return false
    if (user.value.teacher_status) {
      return user.value.teacher_status === 'approved' || Boolean(user.value.teacher_approved)
    }
    return Boolean(user.value.teacher_approved)
  })
  const teacherStatus = computed(() => {
    if (!user.value || user.value.role !== 'teacher') return ''
    if (user.value.teacher_status) return user.value.teacher_status
    return user.value.teacher_approved ? 'approved' : 'pending'
  })
  const teacherRejectReason = computed(() => {
    return user.value?.reject_reason || user.value?.teacher_reject_reason || ''
  })

  function _persistSession(data) {
    token.value = data.access_token
    refreshToken.value = data.refresh_token || ''
    user.value = data.user
    localStorage.setItem('slp_access_token', data.access_token)
    if (data.refresh_token) {
      localStorage.setItem('slp_refresh_token', data.refresh_token)
    }
    localStorage.setItem('slp_user', JSON.stringify(data.user))
  }

  async function login(username, password) {
    const data = await commonApi.login(username, password)
    _persistSession(data)
    return data.user
  }

  async function register(payload) {
    const data = await commonApi.register(payload)
    _persistSession(data)
    return data.user
  }

  async function refreshProfile() {
    if (!token.value) return null
    try {
      const me = await commonApi.fetchMe()
      user.value = me
      localStorage.setItem('slp_user', JSON.stringify(me))
      return me
    } catch {
      return user.value
    }
  }

  function logout() {
    token.value = ''
    refreshToken.value = ''
    user.value = null
    localStorage.removeItem('slp_access_token')
    localStorage.removeItem('slp_refresh_token')
    localStorage.removeItem('slp_user')
  }

  return {
    token,
    refreshToken,
    user,
    isAuthenticated,
    role,
    isTeacherApproved,
    teacherStatus,
    teacherRejectReason,
    login,
    register,
    refreshProfile,
    logout,
  }
})
