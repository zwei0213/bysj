<script setup>
import { ref, computed, watch } from 'vue'
import { studentApi } from '../../../api/modules/student'
import { teacherApi } from '../../../api/modules/teacher'

const props = defineProps({
  courseId: { type: String, required: true },
})
const emit = defineEmits(['notify'])

const inboxItems = ref([])
const replyDrafts = ref({})
const tutorConfig = ref({
  strict_course_only: true,
  allow_out_of_syllabus: false,
  tone: 'encouraging_academic',
  forbidden_str: '',
})
const analytics = ref(null)

// 独立课程精选 FAQ 管理状态
const faqs = ref([])
const newFaq = ref({ question: '', answer: '', is_pinned: true })
const editingFaqId = ref('')
const editFaqForm = ref({ question: '', answer: '', is_pinned: true })

// 归一化分数段分布 (兼容数组 [{ bucket/label/range, count }] 或对象字典 {"0-59": 2, ...})
const scoreDistributionBars = computed(() => {
  const raw = analytics.value?.quiz_summary?.score_distribution
  if (!raw) return []

  let list = []
  if (Array.isArray(raw)) {
    list = raw.map((item) => ({
      label: item.range || item.bucket || item.label || '区间',
      count: Number(item.count ?? item.students ?? 0),
    }))
  } else if (typeof raw === 'object') {
    list = Object.entries(raw).map(([k, v]) => ({
      label: k,
      count: Number(v || 0),
    }))
  }
  const maxCnt = Math.max(1, ...list.map((b) => b.count))
  return list.map((b) => ({
    ...b,
    widthPct: Math.round((b.count / maxCnt) * 100),
  }))
})

const weakKnowledgePoints = computed(() => {
  const list = analytics.value?.quiz_summary?.weak_knowledge_points || []
  const maxWrong = Math.max(1, ...list.map((w) => Number(w.wrong_appearances || w.count || 0)))
  return list.map((w) => {
    const cnt = Number(w.wrong_appearances ?? w.count ?? 0)
    return {
      knowledge_point: w.knowledge_point,
      wrong_appearances: cnt,
      widthPct: Math.max(8, Math.round((cnt / maxWrong) * 100)),
    }
  })
})

const questionAccuracyRows = computed(() => {
  return analytics.value?.quiz_summary?.question_accuracy || []
})

async function loadFaqs() {
  if (!props.courseId) return
  try {
    const res = await teacherApi.fetchFaqs(props.courseId)
    faqs.value = res?.items || (Array.isArray(res) ? res : [])
  } catch {
    try {
      const detail = await studentApi.fetchCourseDetail(props.courseId)
      faqs.value = detail?.faqs || []
    } catch {
      faqs.value = []
    }
  }
}

async function loadPanelData() {
  if (!props.courseId) return
  const [inb, cfg, ana] = await Promise.all([
    teacherApi.fetchUnansweredInbox(props.courseId),
    teacherApi.fetchTutorConfig(props.courseId),
    teacherApi.fetchAnalytics(props.courseId),
    loadFaqs(),
  ])
  inboxItems.value = inb.items || []
  tutorConfig.value = {
    ...cfg,
    forbidden_str: (cfg.forbidden_topics || []).join(', '),
  }
  analytics.value = ana
}

watch(() => props.courseId, loadPanelData, { immediate: true })

async function resolveInbox(item, pinFaq = true) {
  const ans = (replyDrafts.value[item.id] || '').trim()
  if (!ans) return
  await teacherApi.resolveInboxQuestion(item.id, {
    teacher_answer: ans,
    sync_to_kb: true,
    pin_as_faq: pinFaq,
  })
  emit('notify', pinFaq ? '教师解答已回流知识库并沉淀为置顶精选问答。' : '教师解答已同步写入本课检索知识库。')
  await loadPanelData()
}

// ---------------- 精选问答独立增删改置顶 ----------------
async function addFaq() {
  if (!newFaq.value.question.trim() || !newFaq.value.answer.trim()) return
  await teacherApi.createFaq(props.courseId, {
    question: newFaq.value.question.trim(),
    answer: newFaq.value.answer.trim(),
    is_pinned: newFaq.value.is_pinned,
  })
  newFaq.value = { question: '', answer: '', is_pinned: true }
  emit('notify', '课程精选问答已添加。')
  await loadFaqs()
}

function startEditFaq(fq) {
  editingFaqId.value = fq.id
  editFaqForm.value = {
    question: fq.question,
    answer: fq.answer,
    is_pinned: Boolean(fq.is_pinned),
  }
}

async function saveEditFaq(fq) {
  if (!editFaqForm.value.question.trim() || !editFaqForm.value.answer.trim()) return
  await teacherApi.updateFaq(fq.id, {
    question: editFaqForm.value.question.trim(),
    answer: editFaqForm.value.answer.trim(),
    is_pinned: editFaqForm.value.is_pinned,
  })
  editingFaqId.value = ''
  emit('notify', '精选问答内容已更新。')
  await loadFaqs()
}

async function toggleFaqPin(fq) {
  await teacherApi.updateFaq(fq.id, { is_pinned: !fq.is_pinned })
  fq.is_pinned = !fq.is_pinned
  emit('notify', fq.is_pinned ? '已设为置顶精选问答。' : '已取消置顶。')
  await loadFaqs()
}

async function removeFaq(fq) {
  await teacherApi.deleteFaq(fq.id)
  faqs.value = faqs.value.filter((item) => item.id !== fq.id)
  emit('notify', '精选问答条目已删除。')
}

async function saveConfig() {
  const forbidden = tutorConfig.value.forbidden_str.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  await teacherApi.saveTutorConfig(props.courseId, {
    strict_course_only: tutorConfig.value.strict_course_only,
    allow_out_of_syllabus: tutorConfig.value.allow_out_of_syllabus,
    tone: tutorConfig.value.tone,
    forbidden_topics: forbidden,
  })
  emit('notify', '助教行为规范配置已保存。')
}

function heatCellStyle(intensity) {
  if (intensity >= 2) return { backgroundColor: 'rgba(55, 53, 47, 0.24)', fontWeight: 600 }
  if (intensity === 1) return { backgroundColor: 'rgba(55, 53, 47, 0.11)' }
  return { backgroundColor: '#efedea' }
}

function formatAccuracyPct(row) {
  const raw = row.accuracy_pct ?? (row.correct_rate !== undefined ? Number(row.correct_rate) * 100 : 0)
  return Math.round(Number(raw || 0) * 10) / 10
}
</script>

<template>
  <div>
    <!-- 1. 未解答问题收件箱 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">未解答问题收件箱</h2>
          <div class="label-text">低置信度提问自动流转至此 · 教师解答后一键回流课程知识库</div>
        </div>
      </div>

      <div v-if="inboxItems.length" class="inbox-list">
        <div v-for="item in inboxItems" :key="item.id" class="inbox-entry">
          <div class="inbox-head">
            <strong>学生提问：{{ item.question }}</strong>
            <span class="badge">{{ item.status === 'resolved' ? '已回流知识库' : '待解答' }}</span>
          </div>

          <div v-if="item.status === 'resolved'" class="muted-text" style="margin-top: 6px;">
            教师解答：{{ item.teacher_answer }}
          </div>

          <div v-else class="reply-stack">
            <textarea
              v-model="replyDrafts[item.id]"
              class="textarea"
              rows="2"
              placeholder="输入标准解答，提交后将自动同步至本课智能助教知识库..."
            ></textarea>
            <div class="btn-row">
              <button type="button" class="btn btn-primary btn-sm" @click="resolveInbox(item, true)">
                解答并回流知识库 + 置顶精选问答
              </button>
              <button type="button" class="btn btn-outline btn-sm" @click="resolveInbox(item, false)">
                仅回流知识库
              </button>
            </div>
          </div>
        </div>
      </div>

      <p v-else class="muted-text">当前收件箱暂无待解答的学生提问。</p>
    </section>

    <!-- 2. 独立「课程精选问答管理」区块 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">课程精选问答管理</h2>
          <div class="label-text">维护课程高频标准问答，置顶条目将在学生端课程文档页优先展示</div>
        </div>
        <span class="label-text">共 {{ faqs.length }} 条</span>
      </div>

      <div class="card form-col" style="margin-bottom: 16px;">
        <input v-model="newFaq.question" class="input" placeholder="新增典型问题（如：实验报告提交截止日期与评分标准是什么？）" />
        <textarea v-model="newFaq.answer" class="textarea" rows="2" placeholder="输入权威解答内容..."></textarea>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <label class="chk-line">
            <input type="checkbox" v-model="newFaq.is_pinned" />
            <span style="font-size: 13px;">设为课程置顶展示</span>
          </label>
          <button
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="!newFaq.question.trim() || !newFaq.answer.trim()"
            @click="addFaq"
          >
            + 新增精选问答
          </button>
        </div>
      </div>

      <div v-if="faqs.length" class="block-list">
        <div v-for="fq in faqs" :key="fq.id" class="block-row faq-row">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body">
            <template v-if="editingFaqId === fq.id">
              <div class="form-col" style="padding: 4px 0;">
                <input v-model="editFaqForm.question" class="input" />
                <textarea v-model="editFaqForm.answer" class="textarea" rows="2"></textarea>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                  <label class="chk-line">
                    <input type="checkbox" v-model="editFaqForm.is_pinned" />
                    <span style="font-size: 12px;">置顶展示</span>
                  </label>
                  <div class="btn-row">
                    <button type="button" class="btn btn-sm" @click="editingFaqId = ''">取消</button>
                    <button type="button" class="btn btn-primary btn-sm" @click="saveEditFaq(fq)">保存修改</button>
                  </div>
                </div>
              </div>
            </template>

            <template v-else>
              <div class="inbox-head">
                <div>
                  <strong>问：{{ fq.question }}</strong>
                  <span v-if="fq.is_pinned" class="badge badge-link" style="margin-left: 6px;">置顶</span>
                </div>
                <div class="btn-row">
                  <button type="button" class="btn btn-sm" @click="toggleFaqPin(fq)">
                    {{ fq.is_pinned ? '取消置顶' : '置顶' }}
                  </button>
                  <button type="button" class="btn btn-sm" @click="startEditFaq(fq)">编辑</button>
                  <button type="button" class="btn btn-sm btn-danger" @click="removeFaq(fq)">删除</button>
                </div>
              </div>
              <div class="muted-text" style="font-size: 13px; margin-top: 4px;">答：{{ fq.answer }}</div>
            </template>
          </div>
        </div>
      </div>
      <p v-else class="muted-text">当前课程暂未沉淀精选问答。</p>
    </section>

    <!-- 3. 助教行为规范配置 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">课程助教行为边界</h2>
      </div>

      <div class="card form-col">
        <label class="chk-line">
          <input type="checkbox" v-model="tutorConfig.strict_course_only" />
          <span>严格限定仅依据本课程已发布资料作答（防幻觉）</span>
        </label>
        <label class="chk-line">
          <input type="checkbox" v-model="tutorConfig.allow_out_of_syllabus" />
          <span>允许超纲拓展解答</span>
        </label>

        <div class="field">
          <label class="label-text">回答语气风格</label>
          <select v-model="tutorConfig.tone" class="select">
            <option value="encouraging_academic">严谨且鼓励引导（默认）</option>
            <option value="socratic">苏格拉底启发式提问</option>
            <option value="strict">简明学术定义模式</option>
          </select>
        </div>

        <div class="field">
          <label class="label-text">禁答话题关键词（逗号分隔，如：期末原题答案）</label>
          <input v-model="tutorConfig.forbidden_str" class="input" placeholder="例如：期末考试原题, 作业直接答案" />
        </div>

        <div>
          <button type="button" class="btn btn-primary btn-sm" @click="saveConfig">保存助教策略</button>
        </div>
      </div>
    </section>

    <!-- 4. 课程学情看板（补齐薄弱知识点排行 + 分数段分布条形图 + 逐题正确率表） -->
    <section class="doc-section" v-if="analytics">
      <div class="section-header">
        <h2 class="section-title">课程学情、成绩分布与盲区诊断</h2>
      </div>

      <div class="prop-grid" style="margin-bottom: 24px;">
        <div class="prop-cell">
          <div class="label-text">选课人数</div>
          <div class="prop-val">{{ analytics.enrolled_students }}</div>
        </div>
        <div class="prop-cell">
          <div class="label-text">测验平均分</div>
          <div class="prop-val">{{ analytics.quiz_summary?.average_score }}</div>
        </div>
        <div class="prop-cell">
          <div class="label-text">累计交卷</div>
          <div class="prop-val">{{ analytics.quiz_summary?.submitted_count }}</div>
        </div>
        <div class="prop-cell">
          <div class="label-text">高频提问簇</div>
          <div class="prop-val">{{ analytics.question_hotspots?.length || 0 }}</div>
        </div>
      </div>

      <!-- 4.1 班级薄弱知识点排行 -->
      <div style="margin-bottom: 24px;">
        <div class="label-text" style="margin-bottom: 8px;">班级薄弱知识点排行（按错题累计误答频次）</div>
        <div v-if="weakKnowledgePoints.length" class="card bar-stack">
          <div v-for="(wp, idx) in weakKnowledgePoints" :key="wp.knowledge_point" class="bar-row">
            <div class="bar-label">
              <span class="rank-num">#{{ idx + 1 }}</span>
              <span>{{ wp.knowledge_point }}</span>
            </div>
            <div class="bar-track">
              <div class="bar-fill bar-fill-danger" :style="{ width: `${wp.widthPct}%` }"></div>
            </div>
            <span class="bar-count">误答 {{ wp.wrong_appearances }} 次</span>
          </div>
        </div>
        <p v-else class="muted-text">当前班级暂无高频误答薄弱知识点记录。</p>
      </div>

      <!-- 4.2 测验成绩分数段分布条形图 -->
      <div v-if="scoreDistributionBars.length" style="margin-bottom: 24px;">
        <div class="label-text" style="margin-bottom: 8px;">测验成绩分数段分布</div>
        <div class="card bar-stack">
          <div v-for="b in scoreDistributionBars" :key="b.label" class="bar-row">
            <div class="bar-label mono-text">{{ b.label }} 分</div>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: `${Math.max(b.count > 0 ? 6 : 0, b.widthPct)}%` }"></div>
            </div>
            <span class="bar-count">{{ b.count }} 人次</span>
          </div>
        </div>
      </div>

      <!-- 4.3 逐题正确率统计表 -->
      <div v-if="questionAccuracyRows.length" style="margin-bottom: 24px;">
        <div class="label-text" style="margin-bottom: 8px;">试卷与练习逐题正确率统计</div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>题目摘要</th>
                <th>考查知识点</th>
                <th>作答人次</th>
                <th>正确率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(qa, qidx) in questionAccuracyRows" :key="qa.question_id || qidx">
                <td>{{ qa.stem }}</td>
                <td><span class="badge">{{ qa.knowledge_point || '核心概念' }}</span></td>
                <td>{{ qa.attempt_count ?? qa.total_attempts ?? qa.attempts ?? 0 }}</td>
                <td>
                  <span
                    class="badge"
                    :class="{ 'badge-danger': formatAccuracyPct(qa) < 60 }"
                  >
                    {{ formatAccuracyPct(qa) }}%
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 4.4 学生提问高频盲区聚类 -->
      <div style="margin-bottom: 24px;" v-if="analytics.question_hotspots?.length">
        <div class="label-text" style="margin-bottom: 8px;">学生提问高频盲区聚类</div>
        <div class="block-list">
          <div v-for="cl in analytics.question_hotspots" :key="cl.topic_label" class="block-row">
            <span class="block-handle" aria-hidden="true">⋮⋮</span>
            <div class="block-body">
              <div class="inbox-head">
                <strong>{{ cl.topic_label }}</strong>
                <span class="badge">提问 {{ cl.question_count }} 次 · 置信度 {{ Math.round(Number(cl.avg_confidence || 0) * 100) }}%</span>
              </div>
              <div class="muted-text" style="font-size: 13px;">{{ String(cl.teaching_suggestion || '').replace(/FAQ/g, '精选问答') }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 4.5 视频完播率与分钟级回看热度 -->
      <div v-if="analytics.video_analytics?.length">
        <div class="label-text" style="margin-bottom: 8px;">视频完播率与分钟级回看热度</div>
        <div v-for="va in analytics.video_analytics" :key="va.video_id" class="va-row">
          <div style="font-weight: 500;">{{ va.title }} <span class="label-text">（完播率 {{ va.completion_rate_pct }}%）</span></div>
          <div class="heat-strip">
            <span
              v-for="b in va.heatmap"
              :key="b.minute_start_sec"
              class="heat-cell"
              :style="heatCellStyle(b.intensity)"
              :title="`${b.label} 热度=${b.intensity}`"
            >
              {{ b.label }}
            </span>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.inbox-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-16);
}

.inbox-entry {
  padding-bottom: var(--spacing-16);
  border-bottom: 1px solid var(--color-hairline);
}

.faq-row {
  padding-top: var(--spacing-8);
  padding-bottom: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
}

.inbox-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.reply-stack {
  margin-top: var(--spacing-8);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.btn-row {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.form-col {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.chk-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  cursor: pointer;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.prop-val {
  margin-top: var(--spacing-4);
  font-size: var(--text-heading);
  font-weight: var(--font-weight-semibold);
}

.bar-stack {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
  background-color: var(--surface-white-page);
}

.bar-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-12);
}

.bar-label {
  width: 160px;
  flex-shrink: 0;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mono-text {
  font-family: var(--font-mono);
}

.rank-num {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-ink-muted);
}

.bar-track {
  flex: 1;
  height: 8px;
  background-color: var(--color-hover-grey);
  border-radius: var(--radius-cards);
  overflow: hidden;
}

.bar-fill {
  height: 100%;
  background-color: var(--color-ink);
  transition: width 200ms ease;
}

.bar-fill-danger {
  background-color: var(--color-marker-red);
}

.bar-count {
  width: 78px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--color-ink-secondary);
  flex-shrink: 0;
}

.va-row {
  padding: var(--spacing-8) 0;
  border-bottom: 1px solid var(--color-hairline);
}

.heat-strip {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
  margin-top: var(--spacing-8);
}

.heat-cell {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-cards);
  border: 1px solid var(--color-hairline);
  color: var(--color-ink);
}
</style>
