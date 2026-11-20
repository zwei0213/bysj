<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { studentApi } from '../../api/modules/student'
import QuizRunnerModal from '../../components/QuizRunnerModal.vue'

const router = useRouter()
const records = ref([])
const courseOptions = ref([])
const loading = ref(true)
const startingQuiz = ref(false)
const errorTip = ref('')
const filterCourseId = ref('')
const filterTopic = ref('')
const hideMastered = ref(false)
const activeQuiz = ref(null)
const activeQuizCourseId = ref('')

const courseTitleMap = computed(() => {
  const map = {}
  for (const c of courseOptions.value) {
    map[c.id] = c.title
  }
  return map
})

const targetCourseId = computed(() => {
  return filterCourseId.value || records.value[0]?.course_id || ''
})

async function loadCourseList() {
  try {
    const [dash, catalog] = await Promise.all([
      studentApi.fetchDashboard().catch(() => null),
      studentApi.fetchCourses().catch(() => ({ items: [] })),
    ])
    const seen = new Map()
    for (const ec of dash?.enrolled_courses || []) {
      seen.set(ec.course_id, { id: ec.course_id, title: ec.title })
    }
    for (const c of catalog?.items || []) {
      if (!seen.has(c.id)) seen.set(c.id, { id: c.id, title: c.title })
    }
    courseOptions.value = Array.from(seen.values())
  } catch {
    courseOptions.value = []
  }
}

async function loadWrongBook() {
  loading.value = true
  errorTip.value = ''
  try {
    const res = await studentApi.fetchWrongQuestions({
      courseId: filterCourseId.value,
      knowledgePoint: filterTopic.value,
      includeMastered: !hideMastered.value,
    })
    records.value = res.items || []
    // 补充任何仅存在于错题记录中的课程选项
    for (const r of records.value) {
      if (r.course_id && !courseOptions.value.some((c) => c.id === r.course_id)) {
        courseOptions.value.push({ id: r.course_id, title: r.course_title || '在修课程' })
      }
    }
  } catch (err) {
    errorTip.value = err.message || '加载错题记录失败'
  } finally {
    loading.value = false
  }
}

async function toggleMastered(item) {
  const res = await studentApi.patchWrongQuestion(item.id, {
    is_mastered: !item.is_mastered,
    review_note: item.review_note,
  })
  item.is_mastered = res.is_mastered
}

async function saveNote(item) {
  await studentApi.patchWrongQuestion(item.id, { review_note: item.review_note })
}

/**
 * 模式 1：原错题重做 (session_type: 'wrong_retry', wrong_record_ids: [...])
 */
async function startOriginalWrongRetry() {
  const cid = targetCourseId.value
  if (!cid || startingQuiz.value) return
  startingQuiz.value = true
  errorTip.value = ''
  try {
    const courseRecords = records.value.filter((r) => r.course_id === cid)
    const unmastered = courseRecords.filter((r) => !r.is_mastered)
    const picked = (unmastered.length ? unmastered : courseRecords).slice(0, 10)
    const wrongIds = picked.map((r) => r.id)

    activeQuizCourseId.value = cid
    activeQuiz.value = await studentApi.startQuizSession(cid, {
      session_type: 'wrong_retry',
      wrong_record_ids: wrongIds,
      count: Math.max(1, wrongIds.length),
    })
  } catch (err) {
    errorTip.value = err.message || '发起原错题重做失败'
  } finally {
    startingQuiz.value = false
  }
}

/**
 * 模式 2：AI 举一反三变式测验 (session_type: 'personalized')
 */
async function startVariantPractice() {
  const cid = targetCourseId.value
  if (!cid || startingQuiz.value) return
  startingQuiz.value = true
  errorTip.value = ''
  try {
    activeQuizCourseId.value = cid
    activeQuiz.value = await studentApi.startQuizSession(cid, {
      session_type: 'personalized',
      count: 3,
    })
  } catch (err) {
    errorTip.value = err.message || '发起智能变式测验失败'
  } finally {
    startingQuiz.value = false
  }
}

function jumpToChapter(courseId, chapterId) {
  const cid = courseId || activeQuizCourseId.value || targetCourseId.value
  if (!cid || !chapterId) return
  activeQuiz.value = null
  router.push({
    path: `/student/courses/${cid}`,
    query: { chapterId },
  })
}

onMounted(async () => {
  await Promise.all([loadCourseList(), loadWrongBook()])
})
</script>

<template>
  <article class="doc-page">
    <header class="doc-header">
      <div class="section-label">错题巩固本</div>
      <h1 class="doc-display" style="margin-top: 6px;">错题本与靶向重练</h1>
      <p class="doc-subtitle">
        每次章节自测或试卷批改的答错题目自动归档至此，支持原错题重做巩固与智能举一反三变式诊断。
      </p>

      <div class="toolbar-line">
        <select v-model="filterCourseId" class="select course-filter-select" @change="loadWrongBook">
          <option value="">全部课程</option>
          <option v-for="c in courseOptions" :key="c.id" :value="c.id">{{ c.title }}</option>
        </select>

        <input
          v-model="filterTopic"
          class="inline-input filter-input"
          placeholder="按知识点关键词筛选..."
          @keyup.enter="loadWrongBook"
        />

        <label class="chk-label">
          <input type="checkbox" v-model="hideMastered" @change="loadWrongBook" />
          <span>仅看未掌握</span>
        </label>

        <button type="button" class="btn btn-sm" @click="loadWrongBook">查询</button>
      </div>

      <div class="practice-actions">
        <button
          type="button"
          class="btn btn-outline btn-sm"
          :disabled="!records.length || startingQuiz"
          @click="startOriginalWrongRetry"
        >
          原错题重做
        </button>
        <button
          type="button"
          class="btn btn-primary btn-sm"
          :disabled="!records.length || startingQuiz"
          @click="startVariantPractice"
        >
          智能变式测验
        </button>
      </div>
    </header>

    <div v-if="errorTip" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorTip }}</span>
    </div>

    <p v-if="loading" class="muted-text">正在读取错题归档...</p>

    <section v-else class="doc-section">
      <div v-if="records.length" class="wrong-stack">
        <div v-for="item in records" :key="item.id" class="wrong-entry">
          <div class="wrong-head">
            <div class="meta-tags">
              <span v-if="courseTitleMap[item.course_id]" class="badge">{{ courseTitleMap[item.course_id] }}</span>
              <span class="badge">{{ item.knowledge_point }}</span>
              <span class="badge badge-danger">误答 {{ item.wrong_count }} 次</span>
              <span class="badge">{{ item.is_mastered ? '已掌握' : '待复习' }}</span>
            </div>
            <div style="display: flex; gap: 4px; align-items: center;">
              <button
                v-if="item.question?.chapter_id"
                type="button"
                class="btn btn-sm btn-link"
                @click="jumpToChapter(item.course_id, item.question.chapter_id)"
              >
                跳转至关联章节 →
              </button>
              <button type="button" class="btn btn-sm" @click="toggleMastered(item)">
                {{ item.is_mastered ? '标为待复习' : '标记已掌握' }}
              </button>
            </div>
          </div>

          <div class="stem-text">{{ item.question?.stem }}</div>

          <div class="opt-list">
            <div v-for="opt in (item.question?.options || [])" :key="opt.key" class="opt-line">
              <strong>{{ opt.key }}.</strong>
              <span>{{ opt.text }}</span>
            </div>
          </div>

          <div class="callout" style="margin-top: 8px;">
            <span class="callout-icon" aria-hidden="true">
              <svg class="icon-16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6"/><path d="M8 7v4M8 5h.01" stroke-linecap="round"/></svg>
            </span>
            <div>
              <div>
                上次作答：<code>{{ (item.last_wrong_answer || []).join(',') || '未选' }}</code>
                · 标准答案：<strong>{{ (item.question?.answer || []).join(',') }}</strong>
              </div>
              <div class="muted-text" style="margin-top: 2px;">解析：{{ item.question?.explanation }}</div>
            </div>
          </div>

          <div class="note-row">
            <input
              v-model="item.review_note"
              class="inline-input"
              placeholder="在此补充错因复盘笔记..."
              @keyup.enter="saveNote(item)"
            />
            <button type="button" class="btn btn-sm" @click="saveNote(item)">保存笔记</button>
          </div>
        </div>
      </div>

      <p v-else class="muted-text">暂无符合筛选条件的错题记录。</p>
    </section>

    <QuizRunnerModal
      v-if="activeQuiz"
      :session="activeQuiz"
      @close="activeQuiz = null; loadWrongBook()"
      @submitted="loadWrongBook"
      @jump-chapter="(chapterId) => jumpToChapter(activeQuizCourseId, chapterId)"
    />
  </article>
</template>

<style scoped>
.toolbar-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-12);
  flex-wrap: wrap;
  margin-top: var(--spacing-16);
}

.course-filter-select {
  width: auto;
  min-width: 180px;
}

.filter-input {
  width: 200px;
  border-bottom: 1px solid var(--color-hairline);
}

.chk-label {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-4);
  font-size: 13px;
  color: var(--color-ink-secondary);
  cursor: pointer;
}

.practice-actions {
  display: flex;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-top: var(--spacing-12);
}

.wrong-stack {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-24);
}

.wrong-entry {
  padding-bottom: var(--spacing-24);
  border-bottom: 1px solid var(--color-hairline);
}

.wrong-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.meta-tags {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.stem-text {
  font-weight: var(--font-weight-medium);
  margin: var(--spacing-8) 0;
}

.opt-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--color-ink-secondary);
}

.opt-line {
  display: flex;
  gap: var(--spacing-8);
  padding: 2px 0;
}

.note-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  margin-top: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
}

.note-row .inline-input {
  flex: 1;
}
</style>
