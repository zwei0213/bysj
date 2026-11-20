<script setup>
import { ref, computed, watch } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { formatStatus, formatJobType, formatCapability } from '../../../utils/labels'

const props = defineProps({
  aiJobs: { type: Array, required: true },
  aiIndexes: { type: Array, required: true },
  courses: { type: Array, default: () => [] },
  telemetry: { type: Object, default: null },
})
const emit = defineEmits(['notify', 'refresh'])

const localJobs = ref([])
const jobFilters = ref({
  status: '',
  courseId: '',
  startTime: '',
  endTime: '',
})
const queryingJobs = ref(false)

watch(
  () => props.aiJobs,
  (list) => {
    localJobs.value = [...(list || [])]
  },
  { immediate: true }
)

const courseTitleMap = computed(() => {
  const m = {}
  for (const c of props.courses || []) {
    m[c.id] = c.title
  }
  return m
})

const displayedJobs = computed(() => {
  const startMs = jobFilters.value.startTime ? Date.parse(jobFilters.value.startTime) : null
  const endMs = jobFilters.value.endTime ? Date.parse(jobFilters.value.endTime) + 86399000 : null
  const cid = jobFilters.value.courseId.trim().toLowerCase()

  return localJobs.value.filter((j) => {
    if (jobFilters.value.status && j.status !== jobFilters.value.status) return false
    if (cid) {
      const title = String(courseTitleMap.value[j.course_id] || '').toLowerCase()
      if (!String(j.course_id || '').toLowerCase().includes(cid) && !title.includes(cid)) return false
    }
    if (startMs || endMs) {
      const ts = Date.parse(j.created_at || j.updated_at || '')
      if (!Number.isNaN(ts)) {
        if (startMs && ts < startMs) return false
        if (endMs && ts > endMs) return false
      }
    }
    return true
  })
})

/**
 * 合并已建索引记录与全站课程列表，完整呈现 unindexed / indexing / ready / stale / failed 五种状态。
 */
const mergedIndexRows = computed(() => {
  const map = new Map()
  for (const ix of props.aiIndexes || []) {
    map.set(ix.course_id, {
      course_id: ix.course_id,
      course_title: ix.course_title || '',
      status: ix.status || 'ready',
      document_count: ix.document_count ?? 0,
      chunk_count: ix.chunk_count ?? 0,
    })
  }
  for (const c of props.courses || []) {
    if (map.has(c.id)) {
      const existing = map.get(c.id)
      if (!existing.course_title) existing.course_title = c.title
    } else {
      map.set(c.id, {
        course_id: c.id,
        course_title: c.title,
        status: 'unindexed',
        document_count: 0,
        chunk_count: 0,
      })
    }
  }
  return Array.from(map.values())
})

function indexStatusMeta(status) {
  const s = status || 'unindexed'
  if (s === 'ready') return { label: formatStatus('ready'), cls: '' }
  if (s === 'indexing' || s === 'building') return { label: formatStatus(s), cls: 'badge-link' }
  if (s === 'stale') return { label: formatStatus('stale'), cls: 'badge-danger' }
  if (s === 'unindexed') return { label: formatStatus('unindexed'), cls: 'badge-unindexed' }
  if (s === 'failed') return { label: formatStatus('failed'), cls: 'badge-danger' }
  return { label: formatStatus(s), cls: '' }
}

async function applyJobFilter() {
  queryingJobs.value = true
  try {
    const rawCourseKw = jobFilters.value.courseId.trim()
    const matchedCourse = (props.courses || []).find((c) => c.title === rawCourseKw || c.id === rawCourseKw)
    const res = await adminApi.fetchAiJobs({
      ...jobFilters.value,
      courseId: matchedCourse ? matchedCourse.id : (/^[\x00-\x7F]*$/.test(rawCourseKw) ? rawCourseKw : ''),
    })
    localJobs.value = res.items || []
  } finally {
    queryingJobs.value = false
  }
}

function resetJobFilter() {
  jobFilters.value = { status: '', courseId: '', startTime: '', endTime: '' }
  applyJobFilter()
}

async function handleRetry(jobId) {
  await adminApi.retryAiJob(jobId)
  emit('notify', `异步任务（编号：${jobId}）已触发重试。`)
  emit('refresh')
}

async function handleRebuild(courseId) {
  await adminApi.rebuildAiIndex(courseId)
  emit('notify', `课程「${courseTitleMap.value[courseId] || courseId}」知识库索引已触发全量重建。`)
  emit('refresh')
}
</script>

<template>
  <div>
    <!-- 1. 智能异步任务监控队列 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">智能异步任务监控队列</h2>
        <button type="button" class="btn btn-sm btn-outline" @click="applyJobFilter">刷新队列</button>
      </div>

      <div class="card filter-grid">
        <div class="field">
          <label class="label-text">任务状态</label>
          <select v-model="jobFilters.status" class="select" @change="applyJobFilter">
            <option value="">全部状态</option>
            <option value="queued">排队等待中</option>
            <option value="running">正在执行</option>
            <option value="succeeded">执行成功</option>
            <option value="failed">执行失败</option>
          </select>
        </div>
        <div class="field">
          <label class="label-text">课程筛选</label>
          <input
            v-model="jobFilters.courseId"
            list="ops-course-list"
            class="input"
            placeholder="输入课程名称或编号..."
            @keyup.enter="applyJobFilter"
          />
          <datalist id="ops-course-list">
            <option v-for="c in courses" :key="c.id" :value="c.id">{{ c.title }}</option>
          </datalist>
        </div>
        <div class="field">
          <label class="label-text">起始日期</label>
          <input v-model="jobFilters.startTime" type="date" class="input" />
        </div>
        <div class="field">
          <label class="label-text">截止日期</label>
          <input v-model="jobFilters.endTime" type="date" class="input" />
        </div>
        <div class="filter-btns">
          <button type="button" class="btn btn-primary btn-sm" :disabled="queryingJobs" @click="applyJobFilter">
            查询
          </button>
          <button type="button" class="btn btn-sm" @click="resetJobFilter">重置</button>
        </div>
      </div>

      <div v-if="displayedJobs.length" class="table-scroll" style="margin-top: 12px;">
        <table class="data-table">
          <thead>
            <tr>
              <th>任务编号 / 创建时间</th>
              <th>任务类型 / 关联课程</th>
              <th>执行状态</th>
              <th>执行耗时 / 重试</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="j in displayedJobs" :key="j.job_id">
              <td>
                <code>{{ j.job_id }}</code>
                <div v-if="j.created_at" class="label-text">{{ String(j.created_at).replace('T', ' ').slice(0, 16) }}</div>
              </td>
              <td>
                <div>{{ formatJobType(j.job_type) }}</div>
                <div class="label-text">{{ courseTitleMap[j.course_id] || (j.course_id === 'global' ? '全站全局' : j.course_id) }}</div>
              </td>
              <td>
                <span class="badge" :class="{ 'badge-danger': j.status === 'failed', 'badge-link': j.status === 'running' }">
                  {{ formatStatus(j.status) }}
                </span>
                <div v-if="j.error_message" class="err-msg">{{ j.error_message }}</div>
              </td>
              <td>{{ j.duration_ms || 0 }} 毫秒 · {{ j.retry_count ?? 0 }} 次</td>
              <td>
                <button
                  v-if="j.status === 'failed'"
                  type="button"
                  class="btn btn-primary btn-sm"
                  @click="handleRetry(j.job_id)"
                >
                  一键重试
                </button>
                <span v-else class="label-text">正常</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted-text" style="margin-top: 12px;">未查询到符合筛选条件的异步任务记录。</p>
    </section>

    <!-- 2. 课程知识库索引健康度 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">课程知识库索引健康度</h2>
          <div class="label-text">覆盖 尚未建立索引 · 正在构建 · 索引就绪 · 待重建更新</div>
        </div>
      </div>

      <div v-if="mergedIndexRows.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>课程名称 / 课程编号</th>
              <th>索引健康状态</th>
              <th>文档数 / 切片数</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ix in mergedIndexRows" :key="ix.course_id">
              <td>
                <div v-if="ix.course_title"><strong>{{ ix.course_title }}</strong></div>
                <code>{{ ix.course_id }}</code>
              </td>
              <td>
                <span class="badge" :class="indexStatusMeta(ix.status).cls">
                  {{ indexStatusMeta(ix.status).label }}
                </span>
              </td>
              <td>{{ ix.document_count }} 篇 / {{ ix.chunk_count }} 块</td>
              <td>
                <button type="button" class="btn btn-sm btn-outline" @click="handleRebuild(ix.course_id)">
                  {{ ix.status === 'unindexed' ? '初始化索引' : '重建索引' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted-text">暂无课程与检索索引记录。</p>
    </section>

    <!-- 3. 模型算力遥测统计 -->
    <section class="doc-section" v-if="telemetry">
      <div class="section-header">
        <h2 class="section-title">模型调用耗时与用量拆解</h2>
      </div>

      <div v-if="telemetry.by_capability?.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>能力模块</th>
              <th>调用次数</th>
              <th>平均耗时</th>
              <th>累计词元消耗</th>
              <th>算力估算费用（美元）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="cap in telemetry.by_capability" :key="cap.capability">
              <td>{{ formatCapability(cap.capability) }}</td>
              <td>{{ cap.call_count }} 次</td>
              <td>{{ cap.avg_latency_ms }} 毫秒</td>
              <td>{{ cap.total_tokens }}</td>
              <td>{{ cap.estimated_cost_usd }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted-text">暂无模型调用遥测记录。</p>
    </section>
  </div>
</template>

<style scoped>
.filter-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
  gap: var(--spacing-8);
  align-items: end;
  background-color: var(--surface-white-page);
}

@media (max-width: 720px) {
  .filter-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.filter-btns {
  display: flex;
  gap: 4px;
}

.badge-unindexed {
  background-color: transparent;
  border: 1px dashed var(--color-ink-subtle);
  color: var(--color-ink-muted);
}

.err-msg {
  margin-top: 2px;
  font-size: var(--text-label);
  color: var(--color-marker-red);
}
</style>
