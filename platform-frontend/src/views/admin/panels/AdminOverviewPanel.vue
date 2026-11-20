<script setup>
import { ref, computed } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { formatStatus } from '../../../utils/labels'

const props = defineProps({
  overview: { type: Object, required: true },
  courses: { type: Array, default: () => [] },
  orgs: { type: Array, default: () => [] },
})
const emit = defineEmits(['notify'])

const sortMode = ref('popular') // popular | ai_active | dropout_risk
const exporting = ref(false)
const reportFilters = ref({
  startDate: '',
  endDate: '',
  courseId: '',
  classId: '',
})

const classOrgs = computed(() => {
  return (props.orgs || []).filter((o) => o.org_type === 'class')
})

const sortedRankings = computed(() => {
  const list = [...(props.overview?.course_rankings || [])]
  if (sortMode.value === 'popular') {
    list.sort((a, b) => (b.enrolled_count || 0) - (a.enrolled_count || 0))
  } else if (sortMode.value === 'ai_active') {
    list.sort((a, b) => (b.ai_qa_count || 0) - (a.ai_qa_count || 0))
  } else if (sortMode.value === 'dropout_risk') {
    list.sort((a, b) => (b.dropout_risk_count || 0) - (a.dropout_risk_count || 0))
  }
  return list
})

async function handleExport(type) {
  if (exporting.value) return
  exporting.value = true
  try {
    if (type === 'xlsx') {
      await adminApi.downloadCoursesXlsx(reportFilters.value)
      emit('notify', '电子表格运营报表导出完成。')
    } else {
      await adminApi.downloadCoursesCsv(reportFilters.value)
      emit('notify', '纯文本运营报表导出完成。')
    }
  } catch (err) {
    emit('notify', `导出失败：${err.message}`)
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div>
    <!-- 1. 核心运行指标 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">核心运行指标</h2>
        <span class="label-text">全站实时聚合学情与智能服务遥测概览</span>
      </div>

      <div class="prop-grid">
        <div class="prop-cell">
          <div class="label-text">活跃用户 / 注册总数</div>
          <div class="prop-val">
            {{ overview.platform_metrics.active_users }}
            <small>/ {{ overview.platform_metrics.total_registered_users ?? overview.platform_metrics.total_users ?? overview.platform_metrics.active_users }} 人</small>
          </div>
        </div>
        <div class="prop-cell">
          <div class="label-text">视频观看总时长</div>
          <div class="prop-val">
            {{ overview.platform_metrics.watch_hours ?? 0 }} <small>小时</small>
          </div>
        </div>
        <div class="prop-cell">
          <div class="label-text">累计智能提问 / 题库量</div>
          <div class="prop-val">
            {{ overview.platform_metrics.ai_qa_count }} <small>/ {{ overview.platform_metrics.generated_questions }} 题</small>
          </div>
        </div>
        <div class="prop-cell">
          <div class="label-text">测验完成率 / 待审课</div>
          <div class="prop-val">
            {{ overview.platform_metrics.quiz_completion_rate_pct }}%
            <small>· 待审 {{ overview.platform_metrics.pending_course_reviews }} 门</small>
          </div>
        </div>
      </div>

      <div class="sub-metrics">
        <span class="badge">智能助教服务：{{ formatStatus(overview.ai_service_health?.status || 'healthy') }}</span>
        <span class="badge">算力估算费用（美元）：{{ overview.ai_usage_telemetry?.estimated_total_cost_usd ?? 0 }}</span>
        <span class="badge">待处理工单：{{ overview.platform_metrics.open_tickets ?? 0 }} 单</span>
        <span class="badge">待复核样本：{{ overview.platform_metrics.pending_quality_samples ?? 0 }} 条</span>
      </div>
    </section>

    <!-- 2. 多维条件过滤运营报表导出 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">条件筛选运营报表导出</h2>
          <div class="label-text">支持按时间区间、指定课程或行政教学班导出电子表格与纯文本报表</div>
        </div>
      </div>

      <div class="card export-card">
        <div class="export-filters">
          <div class="field">
            <label class="label-text">起始日期</label>
            <input v-model="reportFilters.startDate" type="date" class="input" />
          </div>
          <div class="field">
            <label class="label-text">截止日期</label>
            <input v-model="reportFilters.endDate" type="date" class="input" />
          </div>
          <div class="field">
            <label class="label-text">限定课程</label>
            <select v-model="reportFilters.courseId" class="select">
              <option value="">全部课程</option>
              <option v-for="c in courses" :key="c.id" :value="c.id">{{ c.title }}</option>
            </select>
          </div>
          <div class="field">
            <label class="label-text">限定班级</label>
            <select v-model="reportFilters.classId" class="select">
              <option value="">全部班级</option>
              <option v-for="cl in classOrgs" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
            </select>
          </div>
        </div>

        <div class="export-actions">
          <button type="button" class="btn btn-primary btn-sm" :disabled="exporting" @click="handleExport('xlsx')">
            导出电子表格
          </button>
          <button type="button" class="btn btn-outline btn-sm" :disabled="exporting" @click="handleExport('csv')">
            导出纯文本报表
          </button>
          <button
            type="button"
            class="btn btn-sm"
            @click="reportFilters = { startDate: '', endDate: '', courseId: '', classId: '' }"
          >
            清空筛选
          </button>
        </div>
      </div>
    </section>

    <!-- 3. 课程排行看板 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">课程活跃度与弃学预警排行</h2>
        <div class="sort-pills">
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'is-sort-active': sortMode === 'popular' }"
            @click="sortMode = 'popular'"
          >
            最受欢迎（选课数）
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'is-sort-active': sortMode === 'ai_active' }"
            @click="sortMode = 'ai_active'"
          >
            智能提问最活跃
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'is-sort-active': sortMode === 'dropout_risk' }"
            @click="sortMode = 'dropout_risk'"
          >
            弃学预警
          </button>
        </div>
      </div>

      <div v-if="sortedRankings.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>课程名称</th>
              <th>状态</th>
              <th>选课人数</th>
              <th>智能提问</th>
              <th>弃学预警（进度低于20%）</th>
              <th>存储用量</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in sortedRankings" :key="r.course_id">
              <td><strong>{{ r.title }}</strong></td>
              <td><span class="badge">{{ formatStatus(r.status) }}</span></td>
              <td><strong>{{ r.enrolled_count }}</strong></td>
              <td>{{ r.ai_qa_count }}</td>
              <td>
                <span class="badge" :class="{ 'badge-danger': r.dropout_risk_count > 0 }">
                  {{ r.dropout_risk_count }} 人
                </span>
              </td>
              <td>{{ r.storage_used_mb }} / {{ r.storage_quota_mb }} 兆</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted-text">当前暂无课程运行记录。</p>
    </section>
  </div>
</template>

<style scoped>
.prop-val {
  margin-top: var(--spacing-4);
  font-size: var(--text-subheading);
  font-weight: var(--font-weight-semibold);
}

.prop-val small {
  font-size: var(--text-label);
  font-weight: var(--font-weight-regular);
  color: var(--color-ink-muted);
}

.sub-metrics {
  display: flex;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-top: var(--spacing-12);
}

.export-card {
  background-color: var(--surface-white-page);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.export-filters {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--spacing-8);
}

@media (max-width: 680px) {
  .export-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.export-actions,
.sort-pills {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.is-sort-active {
  background-color: var(--color-hover-grey);
  font-weight: var(--font-weight-semibold);
}
</style>
