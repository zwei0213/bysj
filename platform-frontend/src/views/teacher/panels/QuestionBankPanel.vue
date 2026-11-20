<script setup>
import { ref, computed, watch } from 'vue'
import { teacherApi } from '../../../api/modules/teacher'
import { formatStatus, formatQuestionType, formatDifficulty } from '../../../utils/labels'

const props = defineProps({
  courseId: { type: String, required: true },
  course: { type: Object, default: null },
})
const emit = defineEmits(['notify'])

const questions = ref([])
const exams = ref([])
const courseChapters = ref([])
const loading = ref(false)
const generating = ref(false)

// 题库筛选栏状态
const filterStatus = ref('') // '' | pending_review | approved | rejected
const filterKp = ref('')

// AI 批量命题表单
const genForm = ref({
  chapter_ids: [],
  knowledge_points_str: '核心概念, 推导与应用',
  question_types: ['single_choice', 'multiple_choice', 'true_false'],
  difficulty: 'mixed',
  count: 3,
})

// 手动新增试题折叠表单
const showManualForm = ref(false)
const creatingManual = ref(false)
const manualForm = ref({
  question_type: 'single_choice',
  chapter_id: '',
  knowledge_point: '核心概念',
  difficulty: 'medium',
  stem: '',
  options: [
    { key: 'A', text: '' },
    { key: 'B', text: '' },
    { key: 'C', text: '' },
    { key: 'D', text: '' },
  ],
  answer: ['A'],
  explanation: '',
})

// 完整组卷配置表单
const selectedExamQids = ref([])
const examForm = ref({
  title: '阶段随堂测验',
  time_limit_min: 30,
  max_attempts: 3,
  target_class_id: '',
  start_time: '',
  end_time: '',
})

const displayedQuestions = computed(() => {
  const kw = filterKp.value.trim().toLowerCase()
  return questions.value.filter((q) => {
    if (filterStatus.value && q.review_status !== filterStatus.value) return false
    if (kw) {
      const hitKp = String(q.knowledge_point || '').toLowerCase().includes(kw)
      const hitStem = String(q.stem || '').toLowerCase().includes(kw)
      if (!hitKp && !hitStem) return false
    }
    return true
  })
})

async function loadCourseChapters() {
  const vids = props.course?.videos || []
  const all = []
  for (const v of vids) {
    try {
      const res = await teacherApi.fetchOutlineVersions(v.id)
      const latest = res?.versions?.[0]
      for (const ch of latest?.chapters || []) {
        if (ch.chapter_id) {
          all.push({
            chapter_id: ch.chapter_id,
            label: `${v.title} · ${ch.title}`,
          })
        }
      }
    } catch {
      // 忽略尚无章节的视频
    }
  }
  courseChapters.value = all
}

async function loadQuestionsAndExams() {
  if (!props.courseId) return
  loading.value = true
  try {
    const [qRes, exRes] = await Promise.all([
      teacherApi.fetchQuestions(props.courseId, {
        reviewStatus: filterStatus.value,
        knowledgePoint: filterKp.value,
      }),
      teacherApi.fetchExams(props.courseId).catch(() => ({ items: [] })),
    ])
    questions.value = (qRes.items || []).map((q) => ({
      ...q,
      options: Array.isArray(q.options) ? q.options : [],
      answer: Array.isArray(q.answer) ? q.answer : ['A'],
      _expandedEdit: false,
    }))
    exams.value = exRes?.items || (Array.isArray(exRes) ? exRes : [])
  } finally {
    loading.value = false
  }
}

watch(
  () => props.courseId,
  () => {
    selectedExamQids.value = []
    loadQuestionsAndExams()
    loadCourseChapters()
  },
  { immediate: true }
)

watch(() => props.course?.videos?.length, loadCourseChapters)

function onManualTypeChange() {
  if (manualForm.value.question_type === 'true_false') {
    manualForm.value.options = [
      { key: 'A', text: '正确' },
      { key: 'B', text: '错误' },
    ]
    manualForm.value.answer = ['A']
  } else if (manualForm.value.options.length < 4) {
    manualForm.value.options = [
      { key: 'A', text: '' },
      { key: 'B', text: '' },
      { key: 'C', text: '' },
      { key: 'D', text: '' },
    ]
    manualForm.value.answer = ['A']
  }
}

function toggleAnswerKey(targetObj, key) {
  if (targetObj.question_type === 'multiple_choice') {
    const cur = targetObj.answer || []
    if (cur.includes(key)) {
      if (cur.length > 1) {
        targetObj.answer = cur.filter((k) => k !== key)
      }
    } else {
      targetObj.answer = [...cur, key].sort()
    }
  } else {
    targetObj.answer = [key]
  }
}

async function createManualQuestion() {
  if (!manualForm.value.stem.trim() || creatingManual.value) return
  creatingManual.value = true
  try {
    await teacherApi.createQuestion(props.courseId, {
      question_type: manualForm.value.question_type,
      chapter_id: manualForm.value.chapter_id || null,
      knowledge_point: manualForm.value.knowledge_point.trim() || '核心概念',
      difficulty: manualForm.value.difficulty,
      stem: manualForm.value.stem.trim(),
      options: manualForm.value.options,
      answer: manualForm.value.answer,
      explanation: manualForm.value.explanation.trim(),
      review_status: 'approved',
    })
    manualForm.value.stem = ''
    manualForm.value.explanation = ''
    if (manualForm.value.question_type !== 'true_false') {
      manualForm.value.options.forEach((o) => {
        o.text = ''
      })
    }
    showManualForm.value = false
    emit('notify', '手动新增试题已保存并自动采纳入库。')
    await loadQuestionsAndExams()
  } catch (err) {
    emit('notify', `新增试题失败：${err.message}`)
  } finally {
    creatingManual.value = false
  }
}

async function generateAiQuestions() {
  if (generating.value) return
  generating.value = true
  try {
    const kps = genForm.value.knowledge_points_str.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    const types = genForm.value.question_types.length
      ? genForm.value.question_types
      : ['single_choice', 'multiple_choice', 'true_false']

    const res = await teacherApi.generateQuestions(props.courseId, {
      chapter_ids: genForm.value.chapter_ids,
      knowledge_points: kps,
      question_types: types,
      difficulty: genForm.value.difficulty,
      count: Number(genForm.value.count) || 3,
    })
    emit('notify', `已生成 ${res.generated_count} 道待审试题，请逐题校对选项与标准答案后采纳。`)
    await loadQuestionsAndExams()
  } finally {
    generating.value = false
  }
}

async function saveOrReviewQuestion(q, nextStatus = null) {
  const targetStatus = nextStatus || q.review_status
  await teacherApi.reviewQuestion(q.id, {
    stem: q.stem,
    options: q.options,
    answer: q.answer,
    explanation: q.explanation,
    difficulty: q.difficulty,
    knowledge_point: q.knowledge_point,
    review_status: targetStatus,
  })
  q.review_status = targetStatus
  emit('notify', nextStatus ? `题目状态已更新为：${formatStatus(targetStatus)}` : '题目选项、标准答案与解析已保存。')
}

async function removeQuestion(q) {
  await teacherApi.deleteQuestion(q.id)
  questions.value = questions.value.filter((item) => item.id !== q.id)
  selectedExamQids.value = selectedExamQids.value.filter((id) => id !== q.id)
  emit('notify', '题目已从题库删除。')
}

async function reportQuestionQuality(q) {
  await teacherApi.submitQualityFeedback(props.courseId, {
    sample_type: 'question',
    question_id: q.id,
    input_text: `命题知识点：${q.knowledge_point}`,
    model_output: `${q.stem}\n答案：${(q.answer || []).join(',')}`,
    reason: '智能候选题目干扰项或标准答案存在瑕疵',
    note: '教师审题标记问题样本',
  })
  emit('notify', '已将该题上报至管理端智能命题质量样本池。')
}

async function publishExam() {
  if (!selectedExamQids.value.length || !examForm.value.title.trim()) return
  const payload = {
    title: examForm.value.title.trim(),
    question_ids: selectedExamQids.value,
    time_limit_min: Math.max(5, Number(examForm.value.time_limit_min) || 30),
    max_attempts: Math.max(1, Number(examForm.value.max_attempts) || 3),
    target_class_id: examForm.value.target_class_id || null,
    start_time: examForm.value.start_time ? new Date(examForm.value.start_time).toISOString() : null,
    end_time: examForm.value.end_time ? new Date(examForm.value.end_time).toISOString() : null,
  }
  const res = await teacherApi.publishExam(props.courseId, payload)
  selectedExamQids.value = []
  emit('notify', `试卷《${res.title}》已发布至学生端。`)
  await loadQuestionsAndExams()
}

async function removeExam(ex) {
  await teacherApi.deleteExam(ex.id)
  exams.value = exams.value.filter((item) => item.id !== ex.id)
  emit('notify', `试卷《${ex.title}》已撤回删除。`)
}
</script>

<template>
  <div>
    <!-- 1. 智能批量命题与完整组卷工具卡 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">章节/知识点命题与组卷配置</h2>
          <div class="label-text">智能生成题目默认处于待审状态，校对选项与标准答案并采纳后可勾选组卷</div>
        </div>
        <button type="button" class="btn btn-outline btn-sm" @click="showManualForm = !showManualForm">
          {{ showManualForm ? '收起手动录入' : '+ 手动新增试题' }}
        </button>
      </div>

      <!-- 手动新增试题折叠面板 -->
      <div v-if="showManualForm" class="card form-col" style="margin-bottom: 16px; background-color: var(--surface-white-page);">
        <div class="label-text">手动新增标准试题（保存后直接采纳入库）</div>
        <div class="gen-row">
          <select v-model="manualForm.question_type" class="select" style="width: 120px;" @change="onManualTypeChange">
            <option value="single_choice">单选题</option>
            <option value="multiple_choice">多选题</option>
            <option value="true_false">判断题</option>
          </select>
          <select v-model="manualForm.difficulty" class="select" style="width: 110px;">
            <option value="easy">基础</option>
            <option value="medium">中等</option>
            <option value="hard">进阶</option>
          </select>
          <select v-if="courseChapters.length" v-model="manualForm.chapter_id" class="select" style="width: 190px;">
            <option value="">不限定关联章节</option>
            <option v-for="ch in courseChapters" :key="ch.chapter_id" :value="ch.chapter_id">{{ ch.label }}</option>
          </select>
          <input v-model="manualForm.knowledge_point" class="input" style="flex: 1; min-width: 140px;" placeholder="所属知识点" />
        </div>

        <textarea v-model="manualForm.stem" class="textarea" rows="2" placeholder="输入题干内容..."></textarea>

        <div class="opt-editor-grid">
          <div v-for="opt in manualForm.options" :key="opt.key" class="opt-edit-line">
            <button
              type="button"
              class="btn btn-sm ans-pill"
              :class="{ 'ans-active': manualForm.answer.includes(opt.key) }"
              title="点击设为标准答案"
              @click="toggleAnswerKey(manualForm, opt.key)"
            >
              {{ manualForm.answer.includes(opt.key) ? `✓ ${opt.key}` : opt.key }}
            </button>
            <input v-model="opt.text" class="input" :placeholder="`选项 ${opt.key} 文本...`" />
          </div>
        </div>

        <input v-model="manualForm.explanation" class="input" placeholder="答案解析说明..." />

        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span class="label-text">点击选项左侧字母切换标准答案（当前答案：{{ manualForm.answer.join(',') }}）</span>
          <button type="button" class="btn btn-primary btn-sm" :disabled="creatingManual || !manualForm.stem.trim()" @click="createManualQuestion">
            {{ creatingManual ? '保存中...' : '保存并入库' }}
          </button>
        </div>
      </div>

      <!-- 智能批量命题 + 组卷卡片 -->
      <div class="card tool-stack">
        <div class="label-text">智能批量命题</div>
        <div class="gen-row">
          <input
            v-model="genForm.knowledge_points_str"
            class="input kp-input"
            placeholder="输入考查知识点（逗号分隔）"
          />
          <select v-model="genForm.difficulty" class="select diff-select">
            <option value="mixed">综合难度</option>
            <option value="easy">基础</option>
            <option value="medium">中等</option>
            <option value="hard">进阶</option>
          </select>
          <input v-model.number="genForm.count" type="number" min="1" max="15" class="input cnt-input" title="题量" />
          <button type="button" class="btn btn-primary btn-sm" :disabled="generating" @click="generateAiQuestions">
            {{ generating ? '生成中...' : '智能批量命题' }}
          </button>
        </div>

        <!-- 题型多选复选框 -->
        <div class="check-strip">
          <span class="label-text">生成题型：</span>
          <label class="chk-pill">
            <input type="checkbox" value="single_choice" v-model="genForm.question_types" />
            <span>单选题</span>
          </label>
          <label class="chk-pill">
            <input type="checkbox" value="multiple_choice" v-model="genForm.question_types" />
            <span>多选题</span>
          </label>
          <label class="chk-pill">
            <input type="checkbox" value="true_false" v-model="genForm.question_types" />
            <span>判断题</span>
          </label>
        </div>

        <!-- 关联章节多选 -->
        <div v-if="courseChapters.length" class="check-strip">
          <span class="label-text">限定章节（可选多选）：</span>
          <label v-for="ch in courseChapters" :key="ch.chapter_id" class="chk-pill">
            <input type="checkbox" :value="ch.chapter_id" v-model="genForm.chapter_ids" />
            <span>{{ ch.label }}</span>
          </label>
        </div>

        <!-- 完整组卷发布区 -->
        <div class="exam-section">
          <div class="label-text" style="margin-bottom: 6px;">
            勾选下方「已采纳」试题发布正式试卷（当前已勾选 {{ selectedExamQids.length }} 题）
          </div>
          <div class="exam-grid">
            <div class="field">
              <label class="label-text">试卷标题</label>
              <input v-model="examForm.title" class="input" placeholder="如：期中阶段测验" />
            </div>
            <div class="field">
              <label class="label-text">限时（分钟）</label>
              <input v-model.number="examForm.time_limit_min" type="number" min="5" max="180" class="input" />
            </div>
            <div class="field">
              <label class="label-text">允许作答次数</label>
              <input v-model.number="examForm.max_attempts" type="number" min="1" max="20" class="input" />
            </div>
            <div class="field">
              <label class="label-text">发放目标班级</label>
              <select v-model="examForm.target_class_id" class="select">
                <option value="">本课全部班级</option>
                <option v-for="cl in (course?.classes || [])" :key="cl.id" :value="cl.id">{{ cl.name }}</option>
              </select>
            </div>
            <div class="field">
              <label class="label-text">开放时间（可选）</label>
              <input v-model="examForm.start_time" type="datetime-local" class="input" />
            </div>
            <div class="field">
              <label class="label-text">截止时间（可选）</label>
              <input v-model="examForm.end_time" type="datetime-local" class="input" />
            </div>
          </div>
          <div style="margin-top: 8px;">
            <button type="button" class="btn btn-outline btn-sm" :disabled="!selectedExamQids.length" @click="publishExam">
              发布勾选组卷（已选 {{ selectedExamQids.length }} 题）
            </button>
          </div>
        </div>
      </div>

      <!-- 已发布试卷列表 -->
      <div v-if="exams.length" style="margin-top: 16px;">
        <div class="label-text" style="margin-bottom: 8px;">已发布试卷列表 ({{ exams.length }})</div>
        <div class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>试卷名称</th>
                <th>题量 / 时长 / 限次</th>
                <th>开放时段</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="ex in exams" :key="ex.id">
                <td><strong>{{ ex.title }}</strong></td>
                <td>
                  {{ ex.question_count ?? ex.question_ids?.length ?? '—' }} 题 ·
                  {{ ex.time_limit_min || 30 }} 分钟 ·
                  限 {{ ex.max_attempts || 3 }} 次
                </td>
                <td class="label-text">
                  {{ ex.start_time ? String(ex.start_time).slice(0, 16).replace('T', ' ') : '即刻' }}
                  ~
                  {{ ex.end_time ? String(ex.end_time).slice(0, 16).replace('T', ' ') : '长期有效' }}
                </td>
                <td>
                  <button type="button" class="btn btn-sm btn-danger" @click="removeExam(ex)">删除试卷</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- 2. 课程题库审阅与编辑列表 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">题库审阅与编辑列表</h2>
        <span class="label-text">显示 {{ displayedQuestions.length }} / {{ questions.length }} 题</span>
      </div>

      <!-- 状态与知识点筛选栏 -->
      <div class="filter-bar">
        <div class="status-pills">
          <button type="button" class="btn btn-sm" :class="{ 'is-filter-on': filterStatus === '' }" @click="filterStatus = ''; loadQuestionsAndExams()">
            全部
          </button>
          <button type="button" class="btn btn-sm" :class="{ 'is-filter-on': filterStatus === 'pending_review' }" @click="filterStatus = 'pending_review'; loadQuestionsAndExams()">
            待审核
          </button>
          <button type="button" class="btn btn-sm" :class="{ 'is-filter-on': filterStatus === 'approved' }" @click="filterStatus = 'approved'; loadQuestionsAndExams()">
            已采纳
          </button>
          <button type="button" class="btn btn-sm" :class="{ 'is-filter-on': filterStatus === 'rejected' }" @click="filterStatus = 'rejected'; loadQuestionsAndExams()">
            已淘汰
          </button>
        </div>

        <input
          v-model="filterKp"
          class="inline-input"
          style="width: 220px; border-bottom: 1px solid var(--color-hairline);"
          placeholder="按知识点或题干关键词过滤..."
        />
      </div>

      <p v-if="loading" class="muted-text">正在加载题库...</p>

      <div v-else-if="displayedQuestions.length" class="q-list">
        <div v-for="q in displayedQuestions" :key="q.id" class="block-row q-block">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body q-fields">
            <div class="q-top">
              <div class="q-badges">
                <input
                  v-if="q.review_status === 'approved'"
                  type="checkbox"
                  :value="q.id"
                  v-model="selectedExamQids"
                  title="勾选纳入组卷"
                />
                <span class="badge" :class="{ 'badge-danger': q.review_status === 'rejected' }">
                  {{ formatStatus(q.review_status) }}
                </span>
                <input v-model="q.knowledge_point" class="inline-input kp-badge-input" title="修改知识点" />
                <span class="badge">{{ formatQuestionType(q.question_type) }} · {{ formatDifficulty(q.difficulty) }}</span>
              </div>
              <div class="btn-row">
                <button type="button" class="btn btn-sm" @click="q._expandedEdit = !q._expandedEdit">
                  {{ q._expandedEdit ? '收起选项' : '编辑选项/答案' }}
                </button>
                <button type="button" class="btn btn-sm btn-outline" @click="saveOrReviewQuestion(q, null)">保存修改</button>
                <button type="button" class="btn btn-sm btn-primary" @click="saveOrReviewQuestion(q, 'approved')">采纳</button>
                <button type="button" class="btn btn-sm" @click="saveOrReviewQuestion(q, 'rejected')">淘汰</button>
                <button type="button" class="btn btn-sm" title="上报智能命题质量问题" @click="reportQuestionQuality(q)">上报问题</button>
                <button type="button" class="btn btn-sm btn-danger" @click="removeQuestion(q)">删除</button>
              </div>
            </div>

            <input v-model="q.stem" class="input stem-edit" aria-label="题干编辑" />

            <!-- 展开态：逐项编辑选项文本与标准答案 -->
            <div v-if="q._expandedEdit" class="opt-editor-grid">
              <div v-for="o in q.options" :key="o.key" class="opt-edit-line">
                <button
                  type="button"
                  class="btn btn-sm ans-pill"
                  :class="{ 'ans-active': (q.answer || []).includes(o.key) }"
                  title="点击切换是否为标准答案"
                  @click="toggleAnswerKey(q, o.key)"
                >
                  {{ (q.answer || []).includes(o.key) ? `✓ ${o.key}` : o.key }}
                </button>
                <input v-model="o.text" class="input" :placeholder="`选项 ${o.key} 内容`" />
              </div>
            </div>

            <!-- 折叠态：紧凑展示选项与答案 -->
            <div v-else class="opt-summary">
              <span v-for="o in q.options" :key="o.key" style="margin-right: 12px;">
                <strong>{{ o.key }}.</strong> {{ o.text }}
              </span>
              <span class="badge badge-link">标准答案：{{ (q.answer || []).join(',') }}</span>
            </div>

            <input v-model="q.explanation" class="inline-input exp-edit" placeholder="答案解析（学生交卷后可见）..." />
          </div>
        </div>
      </div>

      <p v-else class="muted-text">未找到符合条件的试题，可调整筛选条件或在上方新增/生成试题。</p>
    </section>
  </div>
</template>

<style scoped>
.tool-stack,
.form-col {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.gen-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.check-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.chk-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  padding: 2px 8px;
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-pills);
  background-color: var(--surface-white-page);
  cursor: pointer;
}

.exam-section {
  padding-top: var(--spacing-12);
  border-top: 1px solid var(--color-hairline);
}

.exam-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--spacing-8);
}

@media (max-width: 680px) {
  .exam-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.field {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kp-input {
  flex: 1;
  min-width: 180px;
}

.diff-select {
  width: 110px;
}

.cnt-input {
  width: 68px;
}

.filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-bottom: var(--spacing-12);
  padding-bottom: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
}

.status-pills {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.is-filter-on {
  background-color: var(--color-hover-grey);
  font-weight: var(--font-weight-semibold);
}

.q-list {
  display: flex;
  flex-direction: column;
}

.q-block {
  padding-top: var(--spacing-12);
  padding-bottom: var(--spacing-12);
  border-bottom: 1px solid var(--color-hairline);
}

.q-fields {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.q-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.q-badges,
.btn-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.kp-badge-input {
  width: 110px;
  font-size: 12px;
  padding: 1px 6px;
  background-color: var(--color-hover-grey);
  border-radius: var(--radius-cards);
}

.stem-edit {
  font-weight: var(--font-weight-medium);
}

.opt-editor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-8);
}

@media (max-width: 640px) {
  .opt-editor-grid {
    grid-template-columns: 1fr;
  }
}

.opt-edit-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.ans-pill {
  border: 1px solid var(--color-hairline);
  min-width: 42px;
  font-family: var(--font-mono);
}

.ans-active {
  background-color: var(--color-ink);
  color: #ffffff;
  border-color: var(--color-ink);
}

.opt-summary {
  font-size: 13px;
  color: var(--color-ink-secondary);
}

.exp-edit {
  border-bottom: 1px solid var(--color-hairline);
  font-size: 13px;
}
</style>
