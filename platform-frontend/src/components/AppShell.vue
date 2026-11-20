<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { commonApi } from '../api/modules/common'
import { formatStatus, formatRole } from '../utils/labels'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const mobileNavOpen = ref(false)
const announcements = ref([])
const activeAnnIdx = ref(0)

// 系统通知中心
const showNotifPanel = ref(false)
const notifications = ref([])
const loadingNotifs = ref(false)

// 工单支持与历史工单
const showTicketModal = ref(false)
const submittingTicket = ref(false)
const ticketForm = ref({ category: '课程与平台反馈', subject: '', description: '' })
const ticketMsg = ref({ type: '', text: '' })
const myTickets = ref([])
const loadingTickets = ref(false)

// 修改密码
const showPwdModal = ref(false)
const submittingPwd = ref(false)
const pwdForm = ref({ current_password: '', new_password: '', confirm_password: '' })
const pwdMsg = ref({ type: '', text: '' })

const roleLabel = computed(() => {
  if (auth.role === 'teacher') {
    if (auth.teacherStatus === 'rejected') return '教师·已驳回'
    return auth.isTeacherApproved ? '授课教师' : '教师·待审'
  }
  return formatRole(auth.role || 'student')
})

const currentAnnouncement = computed(() => {
  if (!announcements.value.length) return null
  const idx = Math.min(activeAnnIdx.value, announcements.value.length - 1)
  return announcements.value[Math.max(0, idx)]
})

const unreadNotifCount = computed(() => {
  return notifications.value.filter((n) => !n.is_read).length
})

async function loadAnnouncements() {
  if (!auth.isAuthenticated) return
  try {
    const res = await commonApi.fetchAnnouncements()
    announcements.value = res?.items || (Array.isArray(res) ? res : [])
    activeAnnIdx.value = 0
  } catch {
    announcements.value = []
  }
}

async function loadNotifications() {
  if (!auth.isAuthenticated) return
  loadingNotifs.value = true
  try {
    const res = await commonApi.fetchMyNotifications()
    notifications.value = res?.items || (Array.isArray(res) ? res : [])
  } catch {
    notifications.value = []
  } finally {
    loadingNotifs.value = false
  }
}

async function toggleNotifPanel() {
  showNotifPanel.value = !showNotifPanel.value
  if (showNotifPanel.value) {
    await loadNotifications()
  }
}

async function handleMarkRead(notif) {
  if (notif.is_read) return
  try {
    await commonApi.markNotificationRead(notif.id)
    notif.is_read = true
  } catch {
    // 静默容错
  }
}

async function markAllRead() {
  const unread = notifications.value.filter((n) => !n.is_read)
  await Promise.all(unread.map((n) => handleMarkRead(n)))
}

async function loadMyTickets() {
  if (!auth.isAuthenticated) return
  loadingTickets.value = true
  try {
    const res = await commonApi.fetchMyTickets()
    myTickets.value = res?.items || (Array.isArray(res) ? res : [])
  } catch {
    myTickets.value = []
  } finally {
    loadingTickets.value = false
  }
}

async function openTicketModal() {
  ticketMsg.value = { type: '', text: '' }
  showTicketModal.value = true
  await loadMyTickets()
}

function prevAnnouncement() {
  if (!announcements.value.length) return
  activeAnnIdx.value = (activeAnnIdx.value - 1 + announcements.value.length) % announcements.value.length
}

function nextAnnouncement() {
  if (!announcements.value.length) return
  activeAnnIdx.value = (activeAnnIdx.value + 1) % announcements.value.length
}

function ticketStatusLabel(st) {
  return formatStatus(st || 'open')
}

function formatShortTime(ts) {
  if (!ts) return ''
  return String(ts).replace('T', ' ').slice(0, 16)
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    showTicketModal.value = false
    showPwdModal.value = false
    showNotifPanel.value = false
    mobileNavOpen.value = false
  }
}

onMounted(async () => {
  if (auth.isAuthenticated) {
    auth.refreshProfile()
    loadAnnouncements()
    loadNotifications()
  }
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})

watch(() => auth.role, () => {
  loadAnnouncements()
  loadNotifications()
})

watch(() => route.fullPath, () => {
  mobileNavOpen.value = false
  showNotifPanel.value = false
})

function handleLogout() {
  auth.logout()
  router.push('/login')
}

async function submitTicket() {
  ticketMsg.value = { type: '', text: '' }
  if (!ticketForm.value.subject.trim()) {
    ticketMsg.value = { type: 'err', text: '请填写工单主题' }
    return
  }
  submittingTicket.value = true
  try {
    await commonApi.submitSupportTicket(ticketForm.value)
    ticketMsg.value = { type: 'ok', text: '工单已提交，下方可实时跟踪分派与管理员回复进度。' }
    ticketForm.value = { category: '课程与平台反馈', subject: '', description: '' }
    await loadMyTickets()
  } catch (err) {
    ticketMsg.value = { type: 'err', text: err.message || '工单提交失败' }
  } finally {
    submittingTicket.value = false
  }
}

async function submitPasswordChange() {
  pwdMsg.value = { type: '', text: '' }
  if (!pwdForm.value.current_password) {
    pwdMsg.value = { type: 'err', text: '请输入当前原密码' }
    return
  }
  if (pwdForm.value.new_password.length < 8) {
    pwdMsg.value = { type: 'err', text: '新密码长度不能少于 8 位' }
    return
  }
  if (pwdForm.value.new_password !== pwdForm.value.confirm_password) {
    pwdMsg.value = { type: 'err', text: '两次输入的新密码不一致' }
    return
  }
  submittingPwd.value = true
  try {
    await commonApi.changePassword({
      current_password: pwdForm.value.current_password,
      new_password: pwdForm.value.new_password,
    })
    pwdMsg.value = { type: 'ok', text: '密码已更新。' }
    pwdForm.value = { current_password: '', new_password: '', confirm_password: '' }
    setTimeout(() => {
      pwdMsg.value = { type: '', text: '' }
      showPwdModal.value = false
    }, 900)
  } catch (err) {
    pwdMsg.value = { type: 'err', text: err.message || '修改失败' }
  } finally {
    submittingPwd.value = false
  }
}
</script>

<template>
  <div class="workspace-shell">
    <!-- 窄屏顶部文档栏 -->
    <header class="mobile-bar">
      <button class="btn" type="button" aria-label="展开页面导航" @click="mobileNavOpen = !mobileNavOpen">
        <svg class="icon-16" viewBox="0 0 16 16"><path d="M2.5 4h11M2.5 8h11M2.5 12h11" stroke-linecap="round"/></svg>
        <span>导航</span>
      </button>
      <span class="mobile-title" @click="router.push('/')">知心智学</span>
      <div class="mobile-right">
        <button v-if="auth.isAuthenticated" type="button" class="btn btn-sm" @click="toggleNotifPanel">
          通知
          <span v-if="unreadNotifCount > 0" class="notif-pill">{{ unreadNotifCount }}</span>
        </button>
        <span class="badge">{{ roleLabel }}</span>
      </div>
    </header>

    <!-- 240px Notion 页面树侧边栏 -->
    <aside class="sidebar" :class="{ open: mobileNavOpen }" aria-label="工作区导航">
      <div class="sidebar-workspace" @click="router.push('/')" tabindex="0" @keydown.enter="router.push('/')">
        <span class="ws-monogram">知</span>
        <div class="ws-meta">
          <div class="ws-name">知心智学</div>
          <div class="ws-user" v-if="auth.isAuthenticated">
            {{ auth.user?.display_name }} · {{ roleLabel }}
          </div>
        </div>
      </div>

      <div class="sidebar-group" v-if="auth.isAuthenticated">
        <div class="sidebar-section-label">工作区页面</div>
        <nav class="page-tree">
          <router-link
            to="/student"
            class="tree-row"
            :class="{ active: route.path.startsWith('/student') && route.path !== '/student/wrong-book' }"
          >
            <span class="tree-handle" aria-hidden="true">
              <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
                <circle cx="3" cy="3" r="1.1"/><circle cx="7" cy="3" r="1.1"/>
                <circle cx="3" cy="7" r="1.1"/><circle cx="7" cy="7" r="1.1"/>
                <circle cx="3" cy="11" r="1.1"/><circle cx="7" cy="11" r="1.1"/>
              </svg>
            </span>
            <svg class="icon-16" viewBox="0 0 16 16"><rect x="2.5" y="2.5" width="11" height="11" rx="1.5"/><path d="M5.5 6h5M5.5 9h5M5.5 12h3"/></svg>
            <span>学习中心</span>
          </router-link>

          <router-link
            to="/student/wrong-book"
            class="tree-row"
            :class="{ active: route.path === '/student/wrong-book' }"
          >
            <span class="tree-handle" aria-hidden="true">
              <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
                <circle cx="3" cy="3" r="1.1"/><circle cx="7" cy="3" r="1.1"/>
                <circle cx="3" cy="7" r="1.1"/><circle cx="7" cy="7" r="1.1"/>
                <circle cx="3" cy="11" r="1.1"/><circle cx="7" cy="11" r="1.1"/>
              </svg>
            </span>
            <svg class="icon-16" viewBox="0 0 16 16"><path d="M4 2.5h8.5v11H4a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 4 2.5Z"/><path d="M5.5 6h4.5M5.5 9h3"/></svg>
            <span>错题本与靶向重练</span>
          </router-link>

          <router-link
            v-if="(auth.role === 'teacher' && auth.isTeacherApproved) || auth.role === 'admin'"
            to="/teacher"
            class="tree-row"
            :class="{ active: route.path.startsWith('/teacher') }"
          >
            <span class="tree-handle" aria-hidden="true">
              <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
                <circle cx="3" cy="3" r="1.1"/><circle cx="7" cy="3" r="1.1"/>
                <circle cx="3" cy="7" r="1.1"/><circle cx="7" cy="7" r="1.1"/>
                <circle cx="3" cy="11" r="1.1"/><circle cx="7" cy="11" r="1.1"/>
              </svg>
            </span>
            <svg class="icon-16" viewBox="0 0 16 16"><path d="M2.5 3.5h11v9h-11z"/><path d="M6 3.5v9M8.5 6.5h3M8.5 9.5h2"/></svg>
            <span>教师教学工作台</span>
          </router-link>

          <router-link
            v-if="auth.role === 'admin'"
            to="/admin"
            class="tree-row"
            :class="{ active: route.path.startsWith('/admin') }"
          >
            <span class="tree-handle" aria-hidden="true">
              <svg width="10" height="14" viewBox="0 0 10 14" fill="currentColor">
                <circle cx="3" cy="3" r="1.1"/><circle cx="7" cy="3" r="1.1"/>
                <circle cx="3" cy="7" r="1.1"/><circle cx="7" cy="7" r="1.1"/>
                <circle cx="3" cy="11" r="1.1"/><circle cx="7" cy="11" r="1.1"/>
              </svg>
            </span>
            <svg class="icon-16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.5"/></svg>
            <span>运营与智能治理中枢</span>
          </router-link>
        </nav>
      </div>

      <div class="sidebar-footer" v-if="auth.isAuthenticated">
        <div class="sidebar-section-label">账户与支持</div>
        <button type="button" class="btn sidebar-action" @click="toggleNotifPanel">
          <svg class="icon-16" viewBox="0 0 16 16"><path d="M8 2.5a3.5 3.5 0 0 1 3.5 3.5v2.5l1.2 1.8H3.3L4.5 8.5V6A3.5 3.5 0 0 1 8 2.5ZM6.5 12a1.5 1.5 0 0 0 3 0"/></svg>
          <span>消息通知</span>
          <span v-if="unreadNotifCount > 0" class="notif-pill" style="margin-left: auto;">{{ unreadNotifCount }}</span>
        </button>
        <button type="button" class="btn sidebar-action" @click="showPwdModal = true">
          <svg class="icon-16" viewBox="0 0 16 16"><rect x="3.5" y="7" width="9" height="6.5" rx="1"/><path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2"/></svg>
          <span>修改密码</span>
        </button>
        <button type="button" class="btn sidebar-action" @click="openTicketModal">
          <svg class="icon-16" viewBox="0 0 16 16"><path d="M2.5 4h11v7.5H6l-3.5 2V4Z"/></svg>
          <span>工单支持</span>
        </button>
        <button type="button" class="btn sidebar-action" @click="handleLogout">
          <svg class="icon-16" viewBox="0 0 16 16"><path d="M6 2.5H3.5v11H6M10.5 5.5 13 8l-2.5 2.5M6.5 8H13" stroke-linecap="round" stroke-linejoin="round"/></svg>
          <span>退出登录</span>
        </button>
      </div>
    </aside>

    <!-- 正文纸面画布：左右留白 24px~96px，内部 .container 上限 708px 不随侧边栏压缩 -->
    <div class="paper-canvas">
      <!-- 顶栏通知与快捷状态条 -->
      <div v-if="auth.isAuthenticated" class="canvas-topbar">
        <div class="topbar-left">
          <span class="label-text">{{ auth.user?.display_name }}（{{ roleLabel }}）</span>
        </div>
        <div class="topbar-right">
          <div class="notif-anchor">
            <button type="button" class="btn btn-sm btn-outline" @click="toggleNotifPanel">
              <svg class="icon-16" viewBox="0 0 16 16"><path d="M8 2.5a3.5 3.5 0 0 1 3.5 3.5v2.5l1.2 1.8H3.3L4.5 8.5V6A3.5 3.5 0 0 1 8 2.5ZM6.5 12a1.5 1.5 0 0 0 3 0"/></svg>
              <span>消息通知</span>
              <span v-if="unreadNotifCount > 0" class="notif-pill">{{ unreadNotifCount }}</span>
            </button>

            <!-- 系统通知下拉面板 -->
            <div v-if="showNotifPanel" class="popover-surface notif-dropdown" role="dialog" aria-label="系统通知中心">
              <div class="section-header" style="margin-bottom: 8px;">
                <strong style="font-size: 13px;">系统通知中心</strong>
                <div style="display: flex; gap: 4px;">
                  <button v-if="unreadNotifCount > 0" type="button" class="btn btn-sm btn-link" @click="markAllRead">
                    全部标为已读
                  </button>
                  <button type="button" class="btn btn-sm" @click="showNotifPanel = false">关闭</button>
                </div>
              </div>

              <p v-if="loadingNotifs" class="muted-text" style="font-size: 12px;">正在同步通知...</p>
              <div v-else-if="notifications.length" class="notif-list">
                <div
                  v-for="n in notifications"
                  :key="n.id"
                  class="notif-item"
                  :class="{ unread: !n.is_read }"
                  tabindex="0"
                  @click="handleMarkRead(n)"
                  @keydown.enter="handleMarkRead(n)"
                >
                  <div class="notif-item-head">
                    <span class="notif-title">
                      <span v-if="!n.is_read" class="unread-dot" aria-hidden="true"></span>
                      {{ n.title || '系统消息' }}
                    </span>
                    <span class="label-text">{{ formatShortTime(n.created_at) }}</span>
                  </div>
                  <div class="notif-content">{{ n.content || n.message }}</div>
                  <div v-if="!n.is_read" style="margin-top: 4px;">
                    <span class="cit-hint">点击标记已读</span>
                  </div>
                </div>
              </div>
              <p v-else class="muted-text" style="font-size: 12px;">暂无个人系统通知。</p>
            </div>
          </div>

          <button type="button" class="btn btn-sm" @click="openTicketModal">工单支持</button>
        </div>
      </div>

      <!-- 独立 v-if 横幅区：教师审核/驳回提示 与 全站多条公告互不遮蔽 -->
      <div
        v-if="(auth.role === 'teacher' && !auth.isTeacherApproved) || announcements.length"
        class="top-notices"
      >
        <div
          v-if="auth.role === 'teacher' && auth.teacherStatus === 'rejected'"
          class="callout callout-danger"
        >
          <span class="callout-icon" aria-hidden="true">!</span>
          <div>
            <strong>教师授课资质未通过审核：</strong>
            <span>{{ auth.teacherRejectReason || '管理员驳回了本次授课申请，请核实个人资料或通过「工单支持」补充说明。' }}</span>
          </div>
        </div>

        <div
          v-else-if="auth.role === 'teacher' && !auth.isTeacherApproved"
          class="callout"
        >
          <span class="callout-icon" aria-hidden="true">
            <svg class="icon-16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M8 5v3.5M8 11h.01" stroke-linecap="round"/></svg>
          </span>
          <div>
            <strong>教师资质待审核：</strong>您的授课权限正在等待管理员在「账号与组织」模块开通；审核期间可正常使用学员学习功能。
          </div>
        </div>

        <div v-if="currentAnnouncement" class="callout ann-banner">
          <span class="callout-icon" aria-hidden="true">
            <svg class="icon-16" viewBox="0 0 16 16"><path d="M3 3.5h10v9H3z"/><path d="M5.5 6.5h5M5.5 9.5h3"/></svg>
          </span>
          <div class="ann-body">
            <div>
              <strong>{{ currentAnnouncement.title }}：</strong>{{ currentAnnouncement.content }}
            </div>
            <div v-if="announcements.length > 1" class="ann-switcher">
              <button type="button" class="btn btn-sm" aria-label="上一条公告" @click="prevAnnouncement">‹</button>
              <span class="label-text">{{ activeAnnIdx + 1 }} / {{ announcements.length }}</span>
              <button type="button" class="btn btn-sm" aria-label="下一条公告" @click="nextAnnouncement">›</button>
            </div>
          </div>
        </div>
      </div>

      <main>
        <slot />
      </main>
    </div>

    <!-- 工单支持与历史工单进度弹层 -->
    <div v-if="showTicketModal" class="modal-backdrop" @click.self="showTicketModal = false">
      <div class="popover-surface ticket-modal-box" role="dialog" aria-modal="true" aria-labelledby="ticket-modal-title">
        <div class="section-header">
          <h3 id="ticket-modal-title" class="section-title">支持工单与处理进度</h3>
          <button type="button" class="btn btn-sm" @click="showTicketModal = false">关闭</button>
        </div>

        <div class="form-stack">
          <div class="label-text">提交新反馈工单</div>
          <select v-model="ticketForm.category" class="select">
            <option>课程与平台反馈</option>
            <option>智能助教回答质量反馈</option>
            <option>账号与权限申请</option>
          </select>
          <input v-model="ticketForm.subject" class="input" placeholder="简要描述遇到的问题主题..." />
          <textarea v-model="ticketForm.description" class="textarea" rows="3" placeholder="附上相关课程或现象描述..."></textarea>
          <div v-if="ticketMsg.text" :class="ticketMsg.type === 'err' ? 'msg-err' : 'msg-ok'" role="alert">
            {{ ticketMsg.text }}
          </div>
          <div class="modal-actions">
            <button type="button" class="btn btn-primary btn-sm" :disabled="submittingTicket" @click="submitTicket">
              {{ submittingTicket ? '提交中...' : '提交新工单' }}
            </button>
          </div>
        </div>

        <hr class="hairline" style="margin: 16px 0;" />

        <div>
          <div class="section-header" style="margin-bottom: 8px;">
            <span class="label-text">我的历史反馈工单 ({{ myTickets.length }})</span>
            <button type="button" class="btn btn-sm" @click="loadMyTickets">刷新进度</button>
          </div>

          <p v-if="loadingTickets" class="muted-text" style="font-size: 12px;">正在加载工单记录...</p>
          <div v-else-if="myTickets.length" class="my-ticket-list">
            <div v-for="t in myTickets" :key="t.id" class="my-ticket-card">
              <div class="my-ticket-head">
                <strong>[{{ String(t.category || '').replace(/AI/g, '智能') }}] {{ t.subject }}</strong>
                <span class="badge" :class="{ 'badge-link': t.status === 'in_progress' }">
                  {{ ticketStatusLabel(t.status) }}
                </span>
              </div>
              <div v-if="t.description" class="muted-text" style="font-size: 12px; margin-top: 2px;">
                {{ t.description }}
              </div>
              <div class="my-ticket-reply">
                <span>指派处理人：<strong>{{ t.assignee_name || '待运营分派' }}</strong></span>
                <span v-if="t.created_at" class="label-text">{{ formatShortTime(t.created_at) }}</span>
              </div>
              <div v-if="t.assignee_note" class="reply-note">
                管理员回复：{{ t.assignee_note }}
              </div>
            </div>
          </div>
          <p v-else class="muted-text" style="font-size: 12px;">暂无历史提交工单记录。</p>
        </div>
      </div>
    </div>

    <!-- 修改密码弹层 -->
    <div v-if="showPwdModal" class="modal-backdrop" @click.self="showPwdModal = false">
      <div class="popover-surface modal-box" role="dialog" aria-modal="true" aria-labelledby="pwd-modal-title">
        <div class="section-header">
          <h3 id="pwd-modal-title" class="section-title">修改登录密码</h3>
          <button type="button" class="btn btn-sm" @click="showPwdModal = false">关闭</button>
        </div>
        <div class="form-stack">
          <label class="label-text">当前原密码</label>
          <input v-model="pwdForm.current_password" type="password" class="input" placeholder="输入原密码" />
          <label class="label-text">新密码（不少于 8 位）</label>
          <input v-model="pwdForm.new_password" type="password" class="input" placeholder="至少 8 位字符" />
          <label class="label-text">确认新密码</label>
          <input v-model="pwdForm.confirm_password" type="password" class="input" placeholder="再次输入新密码" />
          <div v-if="pwdMsg.text" :class="pwdMsg.type === 'err' ? 'msg-err' : 'msg-ok'" role="alert">
            {{ pwdMsg.text }}
          </div>
          <div class="modal-actions">
            <button type="button" class="btn" @click="showPwdModal = false">取消</button>
            <button type="button" class="btn btn-primary" :disabled="submittingPwd" @click="submitPasswordChange">
              {{ submittingPwd ? '更新中...' : '确认更新' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.workspace-shell {
  min-height: 100vh;
  display: flex;
  align-items: stretch;
  background-color: var(--color-paper);
}

.mobile-bar {
  display: none;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-8) var(--spacing-16);
  border-bottom: 1px solid var(--color-hairline);
  background-color: var(--color-paper);
}

.mobile-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
}

.mobile-title {
  font-weight: var(--font-weight-semibold);
  font-size: var(--text-body);
  cursor: pointer;
}

.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  border-right: 1px solid var(--color-hairline);
  background-color: var(--color-paper);
  padding: var(--spacing-16) var(--spacing-8);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-24);
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}

.sidebar-workspace {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  padding: var(--spacing-4) var(--spacing-8);
  border-radius: var(--radius-cards);
  cursor: pointer;
  transition: background-color 150ms ease;
}

.sidebar-workspace:hover {
  background-color: var(--color-hover-grey);
}

.ws-monogram {
  width: 22px;
  height: 22px;
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--surface-white-page);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-ink);
  flex-shrink: 0;
}

.ws-meta {
  min-width: 0;
}

.ws-name {
  font-size: var(--text-body);
  font-weight: var(--font-weight-semibold);
  line-height: 1.3;
}

.ws-user {
  font-size: var(--text-label);
  color: var(--color-ink-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-section-label {
  padding: 0 var(--spacing-8) var(--spacing-4);
  font-size: var(--text-label);
  font-weight: var(--font-weight-medium);
  letter-spacing: var(--tracking-label);
  color: var(--color-ink-muted);
}

.page-tree {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tree-row {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: var(--spacing-4) var(--spacing-8) var(--spacing-4) 2px;
  border-radius: var(--radius-cards);
  color: var(--color-ink-secondary);
  font-size: var(--text-body);
  line-height: var(--leading-body);
  text-decoration: none;
  transition: background-color 150ms ease, color 150ms ease;
}

.tree-row:hover,
.tree-row.active {
  background-color: var(--color-hover-grey);
  color: var(--color-ink);
  text-decoration: none;
}

.tree-row.active {
  font-weight: var(--font-weight-medium);
}

.tree-handle {
  width: 16px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-ink-subtle);
  opacity: 0;
  transition: opacity 150ms ease;
}

.tree-row:hover .tree-handle,
.tree-row:focus-visible .tree-handle {
  opacity: 1;
}

.sidebar-footer {
  margin-top: auto;
  padding-top: var(--spacing-12);
  border-top: 1px solid var(--color-hairline);
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-action {
  width: 100%;
  justify-content: flex-start;
  font-weight: var(--font-weight-regular);
  color: var(--color-ink-secondary);
}

.paper-canvas {
  flex: 1;
  min-width: 0;
  padding: 0 clamp(24px, 6vw, 96px);
}

.canvas-topbar {
  max-width: var(--page-max-width);
  margin: 0 auto;
  padding: var(--spacing-12) 0 var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
}

.notif-anchor {
  position: relative;
}

.notif-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pills);
  background-color: var(--color-marker-red);
  color: #ffffff;
  font-size: 11px;
  font-weight: var(--font-weight-semibold);
}

.notif-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  width: min(360px, 88vw);
  max-height: 400px;
  overflow-y: auto;
  padding: var(--spacing-16);
  z-index: 90;
}

.notif-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.notif-item {
  padding: var(--spacing-8);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  cursor: pointer;
  transition: background-color 150ms ease;
}

.notif-item.unread {
  background-color: rgba(46, 170, 220, 0.06);
}

.notif-item:hover {
  background-color: var(--color-hover-grey);
}

.notif-item-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--spacing-8);
}

.notif-title {
  font-size: 13px;
  font-weight: var(--font-weight-semibold);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.unread-dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pills);
  background-color: var(--color-link-blue);
  flex-shrink: 0;
}

.notif-content {
  margin-top: 2px;
  font-size: 12.5px;
  color: var(--color-ink-secondary);
}

.cit-hint {
  font-size: 11px;
  color: var(--color-link-blue);
}

.top-notices {
  max-width: var(--page-max-width);
  margin: var(--spacing-16) auto 0;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.ann-banner {
  align-items: center;
}

.ann-body {
  flex: 1;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-12);
  flex-wrap: wrap;
}

.ann-switcher {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-4);
  flex-shrink: 0;
}

.modal-box {
  width: min(440px, 100%);
}

.ticket-modal-box {
  width: min(560px, 100%);
  max-height: 86vh;
  overflow-y: auto;
}

.form-stack {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--spacing-8);
  margin-top: var(--spacing-4);
}

.my-ticket-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
  max-height: 240px;
  overflow-y: auto;
}

.my-ticket-card {
  padding: var(--spacing-8) var(--spacing-12);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--color-paper);
}

.my-ticket-head,
.my-ticket-reply {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.my-ticket-reply {
  margin-top: var(--spacing-4);
  font-size: 12px;
  color: var(--color-ink-secondary);
}

.reply-note {
  margin-top: var(--spacing-4);
  padding: var(--spacing-4) var(--spacing-8);
  background-color: var(--surface-white-page);
  border-left: 2px solid var(--color-ink);
  font-size: 12.5px;
}

.msg-ok {
  font-size: var(--text-label);
  color: var(--color-ink);
}

.msg-err {
  font-size: var(--text-label);
  color: var(--color-marker-red);
}

@media (max-width: 860px) {
  .workspace-shell {
    flex-direction: column;
  }
  .mobile-bar {
    display: flex;
  }
  .canvas-topbar {
    display: none;
  }
  .sidebar {
    display: none;
    position: static;
    width: 100%;
    height: auto;
    border-right: none;
    border-bottom: 1px solid var(--color-hairline);
  }
  .sidebar.open {
    display: flex;
  }
  .paper-canvas {
    padding: 0 var(--spacing-24);
  }
}
</style>
