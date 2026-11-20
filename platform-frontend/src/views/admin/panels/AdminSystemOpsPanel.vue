<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import {
  formatStatus,
  formatRole,
  formatDialect,
  formatProvider,
  formatStrategyKey,
  formatStrategyName,
  formatAuditAction,
  formatTargetType,
} from '../../../utils/labels'

const props = defineProps({
  tickets: { type: Array, required: true },
  auditLogs: { type: Array, required: true },
})
const emit = defineEmits(['notify', 'refresh'])

// 1. 系统运行健康状态探针
const systemStatus = ref(null)
const loadingStatus = ref(false)

// 2. 公告管理（含目标角色下拉框 + 历史公告撤回删除）
const newAnn = ref({ title: '', content: '', target_role: 'all' })
const announcements = ref([])

// 3. 工单分派处理与回复
const localTickets = ref([])

// 4. 操作审计日志多条件筛选
const localAuditLogs = ref([])
const auditFilters = ref({
  role: '',
  action: '',
  startDate: '',
  endDate: '',
})
const queryingAudit = ref(false)

watch(
  () => props.tickets,
  (list) => {
    localTickets.value = (list || []).map((t) => ({
      ...t,
      _assigneeName: t.assignee_name || '',
      _assigneeNote: t.assignee_note || '',
    }))
  },
  { immediate: true }
)

watch(
  () => props.auditLogs,
  (list) => {
    localAuditLogs.value = [...(list || [])]
  },
  { immediate: true }
)

const EXTRA_AUDIT_ACTIONS = {
  UPDATE_USER_ACCESS: '更新用户账号与权限',
  FLAG_SPOT_CHECK_SAMPLE: '抽检标记质量问题样本',
  RESOLVE_TICKET: '分派与办结反馈工单',
}

const AUDIT_SEARCH_ALIASES = {
  CREATE_COURSE: '新建课程 创建课程',
  UPDATE_COURSE: '修改课程 编辑课程',
  GOVERN_COURSE: '课程审核 上架 下架 精选 归档 驳回',
  CREATE_USER: '新建账号 新建用户 开通账号',
  UPDATE_USER: '修改用户 教师审批 重置密码',
  UPDATE_USER_ACCESS: '修改用户 教师审批 重置密码',
  TAKEDOWN_DOCUMENT: '下架讲义 下架资料',
  TAKEDOWN_VIDEO: '下架视频',
}

const TICKET_CATEGORY_LABELS = {
  general: '综合咨询',
  technical: '系统故障反馈',
  bug: '功能异常上报',
  course: '课程内容问题',
  course_content: '课程资料问题',
  ai_tutor: '智能助教反馈',
  ai_feedback: '智能生成质量反馈',
  quiz: '测验与题库反馈',
  account: '账号与权限问题',
  suggestion: '产品改进建议',
  other: '其他问题',
}

function displayAuditAction(val) {
  if (!val) return '系统操作'
  if (EXTRA_AUDIT_ACTIONS[val]) return EXTRA_AUDIT_ACTIONS[val]
  return formatAuditAction(val)
}

function formatTicketCategory(val) {
  if (!val) return '综合工单'
  return TICKET_CATEGORY_LABELS[val] || val
}

function formatAuditDetail(detail) {
  if (!detail) return '常规管理操作留痕'
  return String(detail)
    .replace(/\brole=(\w+)/g, (_, r) => `角色：${formatRole(r)}`)
    .replace(/\bactive=(True|False|true|false)/g, (_, v) => `启用状态：${String(v).toLowerCase() === 'true' ? '正常' : '已停用'}`)
    .replace(/\bteacher_status=(\w+)/g, (_, s) => `教师资质：${formatStatus(s)}`)
    .replace(/\bstatus=(\w+)/g, (_, s) => `状态：${formatStatus(s)}`)
    .replace(/\bassignee=([^,)]+)/g, '负责人：$1')
    .replace(/\(message_blocked=(True|False|true|false)\)/g, (_, v) => `（前台回答屏蔽：${String(v).toLowerCase() === 'true' ? '是' : '否'}）`)
    .replace(/状态=(published|pending_review|draft|rejected|closed|archived|takedown)/g, (_, s) => `状态：${formatStatus(s)}`)
    .replace(/精品=(True|False|true|false)/g, (_, v) => `精选：${String(v).toLowerCase() === 'true' ? '是' : '否'}`)
    .replace(/(\d+(?:\.\d+)?)\s*MB\b/gi, '$1 兆')
    .replace(/\((student|teacher|admin)(?:,\s*(student|teacher|admin))*\)/g, (m) =>
      '（' +
      m
        .slice(1, -1)
        .split(',')
        .map((x) => formatRole(x.trim()))
        .join('、') +
      '）'
    )
    .replace(/处置为\s*(corrected|blocked|synced_eval|pending)/g, (_, s) => `处置为「${formatStatus(s)}」`)
}

const displayedAuditLogs = computed(() => {
  const actKw = auditFilters.value.action.trim().toLowerCase()
  const startMs = auditFilters.value.startDate ? Date.parse(auditFilters.value.startDate) : null
  const endMs = auditFilters.value.endDate ? Date.parse(auditFilters.value.endDate) + 86399000 : null

  return localAuditLogs.value.filter((l) => {
    if (auditFilters.value.role && l.actor_role !== auditFilters.value.role) return false
    if (actKw) {
      const actionCn = displayAuditAction(l.action).toLowerCase()
      const rawActionCn = formatAuditAction(l.action).toLowerCase()
      const targetCn = formatTargetType(l.target_type).toLowerCase()
      const detailCn = formatAuditDetail(l.detail).toLowerCase()
      const aliasCn = String(AUDIT_SEARCH_ALIASES[l.action] || '').toLowerCase()
      const hit =
        String(l.action || '').toLowerCase().includes(actKw) ||
        actionCn.includes(actKw) ||
        rawActionCn.includes(actKw) ||
        aliasCn.includes(actKw) ||
        targetCn.includes(actKw) ||
        String(l.target_type || '').toLowerCase().includes(actKw) ||
        String(l.detail || '').toLowerCase().includes(actKw) ||
        detailCn.includes(actKw) ||
        String(l.actor_name || '').toLowerCase().includes(actKw)
      if (!hit) return false
    }
    if (startMs || endMs) {
      const ts = Date.parse(l.created_at || '')
      if (!Number.isNaN(ts)) {
        if (startMs && ts < startMs) return false
        if (endMs && ts > endMs) return false
      }
    }
    return true
  })
})

const activeStrategyPairs = computed(() => {
  const map =
    systemStatus.value?.ai_service?.health?.active_strategies ||
    systemStatus.value?.ai_service?.active_strategies ||
    systemStatus.value?.active_strategies ||
    {}
  return Object.entries(map)
})

async function loadSystemStatus() {
  loadingStatus.value = true
  try {
    systemStatus.value = await adminApi.fetchSystemStatus()
  } catch {
    systemStatus.value = null
  } finally {
    loadingStatus.value = false
  }
}

async function loadAnnouncements() {
  try {
    const res = await adminApi.fetchAnnouncements()
    announcements.value = res?.items || (Array.isArray(res) ? res : [])
  } catch {
    announcements.value = []
  }
}

onMounted(() => {
  loadSystemStatus()
  loadAnnouncements()
})

// ---------------- 公告发布与撤回 ----------------
function roleTargetLabel(r) {
  if (r === 'student') return '仅学生可见'
  if (r === 'teacher') return '仅授课教师可见'
  return '全站师生可见'
}

async function publishAnnouncement() {
  if (!newAnn.value.title.trim() || !newAnn.value.content.trim()) return
  await adminApi.publishAnnouncement({
    title: newAnn.value.title.trim(),
    content: newAnn.value.content.trim(),
    target_role: newAnn.value.target_role,
  })
  newAnn.value = { title: '', content: '', target_role: 'all' }
  emit('notify', '系统公告已推送发布。')
  await loadAnnouncements()
  emit('refresh')
}

async function revokeAnnouncement(ann) {
  await adminApi.deleteAnnouncement(ann.id)
  announcements.value = announcements.value.filter((a) => a.id !== ann.id)
  emit('notify', `公告《${ann.title}》已撤回删除。`)
  emit('refresh')
}

// ---------------- 工单分派与状态流转 ----------------
async function updateTicket(ticket, nextStatus) {
  await adminApi.patchTicket(ticket.id, {
    status: nextStatus,
    assignee_name: ticket._assigneeName.trim(),
    assignee_note: ticket._assigneeNote.trim() || (nextStatus === 'resolved' ? '已核实处理完毕' : '正在跟进核查'),
  })
  ticket.status = nextStatus
  emit('notify', `工单状态已更新为「${formatStatus(nextStatus)}」并同步回复内容。`)
  emit('refresh')
}

// ---------------- 审计日志查询 ----------------
async function queryAudit() {
  queryingAudit.value = true
  try {
    const rawAct = auditFilters.value.action.trim()
    const res = await adminApi.fetchAuditLogs({
      ...auditFilters.value,
      action: /^[\x00-\x7F]*$/.test(rawAct) ? rawAct : '',
    })
    localAuditLogs.value = res.items || []
  } finally {
    queryingAudit.value = false
  }
}

function resetAudit() {
  auditFilters.value = { role: '', action: '', startDate: '', endDate: '' }
  queryAudit()
}
</script>

<template>
  <div>
    <!-- 1. 系统运行健康状态探针 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">系统运行健康状态</h2>
          <div class="label-text">实时探测业务后端、数据库连通延迟、对象存储空间及智能助教活跃策略</div>
        </div>
        <button type="button" class="btn btn-outline btn-sm" :disabled="loadingStatus" @click="loadSystemStatus">
          {{ loadingStatus ? '探测中...' : '刷新健康探针' }}
        </button>
      </div>

      <div v-if="systemStatus" class="prop-grid">
        <div class="prop-cell">
          <div class="label-text">业务后端服务</div>
          <div class="status-val">
            <span class="badge">{{ formatStatus(systemStatus.backend?.status || 'healthy') }}</span>
          </div>
          <div class="label-text" style="margin-top: 4px;">
            {{ systemStatus.backend?.version ? `版本 ${systemStatus.backend.version}` : '核心业务服务' }}
            <template v-if="systemStatus.backend?.pid"> · 进程号 {{ systemStatus.backend.pid }}</template>
            <template v-if="systemStatus.backend?.uptime_sec !== undefined"> · 已运行 {{ systemStatus.backend.uptime_sec }} 秒</template>
          </div>
        </div>

        <div class="prop-cell">
          <div class="label-text">数据库连通与延迟</div>
          <div class="status-val">
            <span class="badge" :class="{ 'badge-danger': systemStatus.database?.connected === false }">
              {{ formatStatus(systemStatus.database?.connected === false ? 'disconnected' : 'connected') }}
            </span>
            <small v-if="systemStatus.database?.latency_ms !== undefined"> {{ systemStatus.database.latency_ms }} 毫秒</small>
          </div>
          <div class="label-text" style="margin-top: 4px;">
            引擎：{{ formatDialect(systemStatus.database?.dialect || systemStatus.database?.engine) }}
          </div>
        </div>

        <div class="prop-cell">
          <div class="label-text">对象存储空间</div>
          <div class="status-val">
            <strong>{{ systemStatus.storage?.used_mb ?? 0 }}</strong>
            <small> 兆（{{ systemStatus.storage?.file_count ?? 0 }} 个文件）</small>
          </div>
          <div class="label-text" style="margin-top: 4px;">
            剩余可用 {{ systemStatus.storage?.disk_free_gb ?? systemStatus.storage?.free_gb ?? '充足' }}<template v-if="systemStatus.storage?.disk_free_gb !== undefined || systemStatus.storage?.free_gb !== undefined"> 吉</template>
          </div>
        </div>

        <div class="prop-cell">
          <div class="label-text">智能助教服务状态</div>
          <div class="status-val">
            <span class="badge" :class="{ 'badge-danger': (systemStatus.ai_service?.health?.status || systemStatus.ai_service?.status || 'healthy') !== 'healthy' }">
              {{ formatStatus(systemStatus.ai_service?.health?.status || systemStatus.ai_service?.status || 'healthy') }}
            </span>
          </div>
          <div class="label-text" style="margin-top: 4px;">
            推理引擎：{{ formatProvider(systemStatus.ai_service?.health?.provider || systemStatus.ai_service?.runtime_config?.active_provider || 'local') }}
          </div>
        </div>
      </div>
      <p v-else class="muted-text">正在获取系统健康遥测快照...</p>

      <div v-if="activeStrategyPairs.length" class="strategy-strip">
        <span class="label-text">智能服务活跃算法策略：</span>
        <span v-for="[k, v] in activeStrategyPairs" :key="k" class="badge">
          {{ formatStrategyKey(k) }}：{{ formatStrategyName(v) }}
        </span>
      </div>
    </section>

    <!-- 2. 发布与撤回系统公告 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">全站系统公告管理</h2>
        <span class="label-text">共 {{ announcements.length }} 条有效公告</span>
      </div>

      <div class="card form-col" style="margin-bottom: 16px;">
        <div class="ann-top-row">
          <input v-model="newAnn.title" class="input" style="flex: 1; min-width: 180px;" placeholder="输入公告标题..." />
          <select v-model="newAnn.target_role" class="select" style="width: 160px;">
            <option value="all">全站师生可见</option>
            <option value="student">仅学生可见</option>
            <option value="teacher">仅授课教师可见</option>
          </select>
        </div>
        <textarea v-model="newAnn.content" class="textarea" rows="2" placeholder="公告正文内容..."></textarea>
        <div>
          <button
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="!newAnn.title.trim() || !newAnn.content.trim()"
            @click="publishAnnouncement"
          >
            推送发布公告
          </button>
        </div>
      </div>

      <div v-if="announcements.length" class="block-list">
        <div v-for="a in announcements" :key="a.id" class="block-row ann-item">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body">
            <div class="ticket-head">
              <div>
                <strong>{{ a.title }}</strong>
                <span class="badge" style="margin-left: 6px;">{{ roleTargetLabel(a.target_role) }}</span>
              </div>
              <button type="button" class="btn btn-sm btn-danger" @click="revokeAnnouncement(a)">
                撤回删除
              </button>
            </div>
            <div class="muted-text" style="font-size: 13px; margin-top: 2px;">{{ a.content }}</div>
          </div>
        </div>
      </div>
    </section>

    <!-- 3. 师生反馈工单分派与回复跟踪 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">支持工单分派与办结</h2>
        <span class="label-text">共 {{ localTickets.length }} 单</span>
      </div>

      <div v-if="localTickets.length" class="block-list">
        <div v-for="t in localTickets" :key="t.id" class="block-row ticket-row">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body">
            <div class="ticket-head">
              <strong>【{{ formatTicketCategory(t.category) }}】{{ t.subject }}</strong>
              <span class="badge" :class="{ 'badge-link': t.status === 'in_progress' }">{{ formatStatus(t.status) }}</span>
            </div>
            <div class="muted-text" style="font-size: 13px; margin: 4px 0 8px;">{{ t.description }}</div>

            <div class="ticket-ops">
              <input
                v-model="t._assigneeName"
                class="input"
                style="max-width: 160px;"
                placeholder="填写分派处理人姓名..."
              />
              <input
                v-model="t._assigneeNote"
                class="input"
                style="flex: 1; min-width: 200px;"
                placeholder="填写处理进展或回复说明（提交人可见）..."
              />
              <div class="btn-row">
                <button
                  type="button"
                  class="btn btn-outline btn-sm"
                  @click="updateTicket(t, 'in_progress')"
                >
                  设为处理中
                </button>
                <button
                  type="button"
                  class="btn btn-primary btn-sm"
                  @click="updateTicket(t, 'resolved')"
                >
                  标记已办结
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <p v-else class="muted-text">当前暂无师生提交的支持工单。</p>
    </section>

    <!-- 4. 关键操作审计日志 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">关键操作审计日志</h2>
        <span class="label-text">显示 {{ displayedAuditLogs.length }} 条留痕记录</span>
      </div>

      <div class="card audit-filter-grid" style="margin-bottom: 12px;">
        <div class="field">
          <label class="label-text">操作人角色</label>
          <select v-model="auditFilters.role" class="select" @change="queryAudit">
            <option value="">全部角色</option>
            <option value="admin">平台管理员</option>
            <option value="teacher">授课教师</option>
            <option value="student">学生</option>
          </select>
        </div>
        <div class="field">
          <label class="label-text">检索关键词</label>
          <input
            v-model="auditFilters.action"
            class="input"
            placeholder="输入操作人姓名、动作名称或详情关键词..."
            @keyup.enter="queryAudit"
          />
        </div>
        <div class="field">
          <label class="label-text">起始日期</label>
          <input v-model="auditFilters.startDate" type="date" class="input" />
        </div>
        <div class="field">
          <label class="label-text">截止日期</label>
          <input v-model="auditFilters.endDate" type="date" class="input" />
        </div>
        <div style="display: flex; gap: 4px; align-self: end;">
          <button type="button" class="btn btn-primary btn-sm" :disabled="queryingAudit" @click="queryAudit">查询</button>
          <button type="button" class="btn btn-sm" @click="resetAudit">重置</button>
        </div>
      </div>

      <div v-if="displayedAuditLogs.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>操作时间 / 操作人</th>
              <th>操作动作</th>
              <th>作用对象</th>
              <th>留痕摘要</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="l in displayedAuditLogs" :key="l.id">
              <td>
                <div>{{ l.actor_name }} <span class="badge">{{ formatRole(l.actor_role) }}</span></div>
                <div v-if="l.created_at" class="label-text">{{ String(l.created_at).replace('T', ' ').slice(0, 19) }}</div>
              </td>
              <td><span class="badge">{{ displayAuditAction(l.action) }}</span></td>
              <td>
                <div>{{ formatTargetType(l.target_type) }}</div>
                <div v-if="l.target_id === 'global'" class="label-text">全站全局</div>
                <div v-else-if="l.target_id === 'batch'" class="label-text">批量操作</div>
                <div v-else-if="l.target_id" class="label-text">编号：{{ l.target_id }}</div>
              </td>
              <td>{{ formatAuditDetail(l.detail) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted-text">未查询到符合条件的审计日志记录。</p>
    </section>
  </div>
</template>

<style scoped>
.status-val {
  margin-top: var(--spacing-4);
  font-size: var(--text-subheading);
  font-weight: var(--font-weight-semibold);
}

.status-val small {
  font-size: var(--text-label);
  font-weight: var(--font-weight-regular);
  color: var(--color-ink-muted);
}

.strategy-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-top: var(--spacing-12);
}

.form-col {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.ann-top-row {
  display: flex;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.ann-item,
.ticket-row {
  margin-left: 0;
  padding-top: var(--spacing-8);
  padding-bottom: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
}

.ticket-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.ticket-ops {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.btn-row {
  display: flex;
  gap: var(--spacing-4);
}

.audit-filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
  gap: var(--spacing-8);
  background-color: var(--surface-white-page);
}

@media (max-width: 720px) {
  .audit-filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
</style>
