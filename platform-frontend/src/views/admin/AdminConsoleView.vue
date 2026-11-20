<script setup>
import { ref, onMounted } from 'vue'
import { adminApi } from '../../api/modules/admin'
import AdminOverviewPanel from './panels/AdminOverviewPanel.vue'
import AdminAiOpsPanel from './panels/AdminAiOpsPanel.vue'
import AdminAiQualityPanel from './panels/AdminAiQualityPanel.vue'
import AdminCourseGovPanel from './panels/AdminCourseGovPanel.vue'
import AdminOrgUsersPanel from './panels/AdminOrgUsersPanel.vue'
import AdminSystemOpsPanel from './panels/AdminSystemOpsPanel.vue'

const activeDomain = ref('overview') // overview | ai_ops | ai_quality | courses | org_users | system_ops
const notice = ref('')
const loading = ref(true)
const errorMsg = ref('')

const overview = ref(null)
const users = ref([])
const orgs = ref([])
const courses = ref([])
const aiJobs = ref([])
const aiIndexes = ref([])
const samples = ref([])
const runtimeConfig = ref({
  active_provider: 'local',
  confidence_threshold: 0.28,
  rate_limit_per_minute: { student: 30, teacher: 60 },
  feature_flags: {},
  safety_filter: { blocked_keywords: [] },
})
const tickets = ref([])
const auditLogs = ref([])

function flash(msg) {
  notice.value = msg
  setTimeout(() => {
    if (notice.value === msg) notice.value = ''
  }, 3200)
}

async function quickExport(format) {
  try {
    if (format === 'xlsx') {
      await adminApi.downloadCoursesXlsx()
      flash('电子表格运营报表已触发下载。')
    } else {
      await adminApi.downloadCoursesCsv()
      flash('纯文本运营报表已触发下载。')
    }
  } catch (err) {
    errorMsg.value = err.message || '导出报表失败'
  }
}

async function loadAllGovernanceData() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [ov, us, og, cs, jb, ix, sp, rc, tk, au] = await Promise.all([
      adminApi.fetchOverview(),
      adminApi.fetchUsers(),
      adminApi.fetchOrganizations(),
      adminApi.fetchCourses(),
      adminApi.fetchAiJobs(),
      adminApi.fetchAiIndexes(),
      adminApi.fetchFeedbackSamples(),
      adminApi.fetchAiRuntimeConfig(),
      adminApi.fetchTickets(),
      adminApi.fetchAuditLogs(),
    ])
    overview.value = ov
    users.value = us.items || []
    orgs.value = og.items || []
    courses.value = cs.items || []
    aiJobs.value = jb.items || []
    aiIndexes.value = ix.items || []
    samples.value = sp.items || []
    runtimeConfig.value = rc
    tickets.value = tk.items || []
    auditLogs.value = au.items || []
  } catch (err) {
    errorMsg.value = err.message || '加载治理中枢数据失败'
  } finally {
    loading.value = false
  }
}

onMounted(loadAllGovernanceData)
</script>

<template>
  <article class="doc-page">
    <header class="doc-header">
      <div class="section-label">平台运营与智能治理中心</div>
      <div class="section-header" style="margin-top: 6px; margin-bottom: 0;">
        <h1 class="doc-display">运营与智能治理中枢</h1>
        <div style="display: flex; gap: 6px; flex-wrap: wrap;">
          <button type="button" class="btn btn-primary btn-sm" @click="quickExport('xlsx')">
            导出电子表格
          </button>
          <button type="button" class="btn btn-outline btn-sm" @click="quickExport('csv')">
            导出纯文本报表
          </button>
        </div>
      </div>
      <p class="doc-subtitle">
        覆盖组织与教师资质审批、课程上架与分类治理、智能异步任务监控、按比例随机抽检回流及系统健康探针。
      </p>
    </header>

    <div v-if="errorMsg" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorMsg }}</span>
    </div>

    <div v-if="notice" class="callout" style="margin-bottom: 24px;" role="status">
      <span class="callout-icon" aria-hidden="true">✓</span>
      <span>{{ notice }}</span>
    </div>

    <div class="tabs-bar">
      <button type="button" class="tab-pill" :class="{ active: activeDomain === 'overview' }" @click="activeDomain = 'overview'">
        数据总览与报表
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeDomain === 'org_users' }" @click="activeDomain = 'org_users'">
        账号 · 组织 · 权限
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeDomain === 'courses' }" @click="activeDomain = 'courses'">
        课程与分类治理
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeDomain === 'ai_ops' }" @click="activeDomain = 'ai_ops'">
        智能任务监控
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeDomain === 'ai_quality' }" @click="activeDomain = 'ai_quality'">
        智能质量与安全
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeDomain === 'system_ops' }" @click="activeDomain = 'system_ops'">
        状态 · 公告 · 工单 · 审计
      </button>
    </div>

    <p v-if="loading && !overview" class="muted-text">正在聚合全站运营与智能服务遥测指标...</p>

    <template v-else>
      <AdminOverviewPanel
        v-if="activeDomain === 'overview' && overview"
        :overview="overview"
        :courses="courses"
        :orgs="orgs"
        @notify="flash"
      />

      <AdminAiOpsPanel
        v-else-if="activeDomain === 'ai_ops'"
        :ai-jobs="aiJobs"
        :ai-indexes="aiIndexes"
        :courses="courses"
        :telemetry="overview?.ai_usage_telemetry"
        @notify="flash"
        @refresh="loadAllGovernanceData"
      />

      <AdminAiQualityPanel
        v-else-if="activeDomain === 'ai_quality'"
        :samples="samples"
        :courses="courses"
        :initial-runtime-config="runtimeConfig"
        @notify="flash"
        @refresh="loadAllGovernanceData"
      />

      <AdminCourseGovPanel
        v-else-if="activeDomain === 'courses'"
        :courses="courses"
        @notify="flash"
        @refresh="loadAllGovernanceData"
      />

      <AdminOrgUsersPanel
        v-else-if="activeDomain === 'org_users'"
        :users="users"
        :orgs="orgs"
        :courses="courses"
        @notify="flash"
        @refresh="loadAllGovernanceData"
      />

      <AdminSystemOpsPanel
        v-else-if="activeDomain === 'system_ops'"
        :tickets="tickets"
        :audit-logs="auditLogs"
        @notify="flash"
        @refresh="loadAllGovernanceData"
      />
    </template>
  </article>
</template>
