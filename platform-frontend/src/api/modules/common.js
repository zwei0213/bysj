import { apiRequest } from '../client'

export const commonApi = {
  login(username, password) {
    return apiRequest('/api/v1/auth/tokens', {
      method: 'POST',
      body: { username, password },
    })
  },
  register(payload) {
    return apiRequest('/api/v1/auth/registrations', {
      method: 'POST',
      body: payload,
    })
  },
  fetchMe() {
    return apiRequest('/api/v1/users/me')
  },
  changePassword(payload) {
    return apiRequest('/api/v1/users/me/password', {
      method: 'PUT',
      body: payload,
    })
  },
  fetchAnnouncements() {
    return apiRequest('/api/v1/announcements')
  },
  fetchCategories() {
    return apiRequest('/api/v1/categories')
  },
  fetchMyNotifications() {
    return apiRequest('/api/v1/users/me/notifications')
  },
  markNotificationRead(notifId) {
    return apiRequest(`/api/v1/users/me/notifications/${encodeURIComponent(notifId)}`, {
      method: 'PATCH',
      body: { is_read: true },
    })
  },
  fetchMyTickets() {
    return apiRequest('/api/v1/users/me/support-tickets')
  },
  submitSupportTicket(payload) {
    return apiRequest('/api/v1/support-tickets', {
      method: 'POST',
      body: payload,
    })
  },
}
