<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { formatSeconds } from '../api/client'
import { studentApi } from '../api/modules/student'
import { formatQuestionType, formatDifficulty } from '../utils/labels'

const props = defineProps({
  session: { type: Object, required: true },
})

const emit = defineEmits(['close', 'submitted', 'jump-chapter'])

const selectedAnswers = reactive({})
const submitting = ref(false)
const errorTip = ref('')
const result = ref(null)
const autoSubmittedByTimer = ref(false)

// 考试实时倒计时 (MM:SS)
const hasTimeLimit = computed(() => Number(props.session?.time_limit_min || 0) > 0)
const remainingSec = ref(0)
let countdownTimer = null

function stopCountdown() {
  if (countdownTimer) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

function startCountdownIfNeeded() {
  stopCountdown()
  if (!hasTimeLimit.value) return
  remainingSec.value = Math.max(1, Math.floor(Number(props.session.time_limit_min) * 60))
  countdownTimer = setInterval(() => {
    if (result.value || submitting.value) {
      stopCountdown()
      return
    }
    remainingSec.value -= 1
    if (remainingSec.value <= 0) {
      remainingSec.value = 0
      stopCountdown()
      autoSubmittedByTimer.value = true
      submitPaper()
    }
  }, 1000)
}

function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  startCountdownIfNeeded()
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  stopCountdown()
})

function toggleOption(q, key) {
  if (result.value) return
  if (q.question_type === 'multiple_choice') {
    const cur = selectedAnswers[q.id] || []
    selectedAnswers[q.id] = cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]
  } else {
    selectedAnswers[q.id] = [key]
  }
}

async function submitPaper() {
  if (submitting.value || result.value) return
  stopCountdown()
  submitting.value = true
  errorTip.value = ''
  try {
    const res = await studentApi.submitQuizAnswers(props.session.session_id, selectedAnswers)
    result.value = res
    emit('submitted', res)
  } catch (err) {
    errorTip.value = err.message || '试卷提交失败'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="modal-backdrop" @click.self="emit('close')">
    <div class="popover-surface quiz-modal" role="dialog" aria-modal="true" :aria-label="session.title">
      <div class="section-header quiz-head">
        <div>
          <h3 class="section-title">{{ session.title }}</h3>
          <div class="label-text">共 {{ session.questions?.length || 0 }} 题 · 答错自动归入个人错题本</div>
        </div>
        <div class="quiz-head-right">
          <span
            v-if="hasTimeLimit && !result"
            class="badge countdown-badge"
            :class="{ 'badge-danger': remainingSec <= 60 }"
          >
            倒计时 {{ formatSeconds(remainingSec) }}
          </span>
          <button type="button" class="btn btn-sm" @click="emit('close')">关闭</button>
        </div>
      </div>

      <div v-if="errorTip" class="callout callout-danger" role="alert" style="margin-bottom: 12px;">
        <span class="callout-icon" aria-hidden="true">!</span>
        <span>{{ errorTip }}</span>
      </div>

      <div v-if="result" class="callout" style="margin-bottom: 16px;">
        <span class="callout-icon" aria-hidden="true">✓</span>
        <div>
          <strong>批改得分：{{ result.score }} / {{ result.total_score }}</strong>
          <div class="muted-text">
            {{ autoSubmittedByTimer ? '考试时间到已自动交卷判分，' : '已完成自动判分，' }}错题已归集至「错题本与靶向重练」。
          </div>
        </div>
      </div>

      <div class="questions-scroll">
        <div v-for="(q, idx) in session.questions" :key="q.id" class="q-item">
          <div class="q-meta">
            <span class="label-text">
              {{ idx + 1 }}. {{ formatQuestionType(q.question_type) }}
            </span>
            <span class="badge">{{ q.knowledge_point }}</span>
            <span class="badge">{{ formatDifficulty(q.difficulty) }}</span>
          </div>

          <div class="q-stem">{{ q.stem }}</div>

          <div class="block-list">
            <div
              v-for="opt in q.options"
              :key="opt.key"
              class="block-row opt-row"
              :class="{ 'is-selected': (selectedAnswers[q.id] || []).includes(opt.key) }"
              tabindex="0"
              @click="toggleOption(q, opt.key)"
              @keydown.enter.prevent="toggleOption(q, opt.key)"
              @keydown.space.prevent="toggleOption(q, opt.key)"
            >
              <span class="opt-check">{{ (selectedAnswers[q.id] || []).includes(opt.key) ? '[✓]' : '[ ]' }}</span>
              <strong>{{ opt.key }}.</strong>
              <span class="block-body">{{ opt.text }}</span>
            </div>
          </div>

          <div v-if="result" class="explain-block">
            <template v-for="det in (result.details || []).filter((d) => d.question_id === q.id)" :key="det.question_id">
              <div class="verdict" :class="{ wrong: !det.is_correct }">
                {{ det.is_correct ? '回答正确' : `回答有误（作答：${(det.user_answer || []).join(',') || '未选'} · 标准答案：${(det.standard_answer || []).join(',')}）` }}
              </div>
              <div class="muted-text" style="margin-top: 4px;">解析：{{ det.explanation }}</div>
              <button
                v-if="det.chapter_id"
                type="button"
                class="btn btn-sm btn-link"
                style="margin-top: 4px; padding-left: 0;"
                @click="emit('jump-chapter', det.chapter_id)"
              >
                跳转至关联章节 →
              </button>
            </template>
          </div>
        </div>
      </div>

      <div class="quiz-footer">
        <button v-if="!result" type="button" class="btn btn-primary" :disabled="submitting" @click="submitPaper">
          {{ submitting ? '判分中...' : '提交并查看解析' }}
        </button>
        <button v-else type="button" class="btn btn-primary" @click="emit('close')">完成复盘</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.quiz-modal {
  width: min(708px, 100%);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
}

.quiz-head {
  padding-bottom: var(--spacing-12);
  border-bottom: 1px solid var(--color-hairline);
}

.quiz-head-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
}

.countdown-badge {
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 2px 10px;
}

.questions-scroll {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-24);
  padding: var(--spacing-8) 0;
}

.q-item {
  padding-bottom: var(--spacing-16);
  border-bottom: 1px solid var(--color-hairline);
}

.q-item:last-child {
  border-bottom: none;
}

.q-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-bottom: var(--spacing-8);
}

.q-stem {
  font-weight: var(--font-weight-medium);
  margin-bottom: var(--spacing-8);
}

.opt-row {
  margin-left: 0;
  cursor: pointer;
}

.opt-check {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-ink-muted);
  width: 24px;
  flex-shrink: 0;
}

.explain-block {
  margin-top: var(--spacing-12);
  padding: var(--spacing-8) var(--spacing-12);
  background-color: var(--color-hover-grey);
  border-radius: var(--radius-cards);
}

.verdict {
  font-size: 13px;
  font-weight: var(--font-weight-semibold);
  color: var(--color-ink);
}

.verdict.wrong {
  color: var(--color-marker-red);
}

.quiz-footer {
  padding-top: var(--spacing-12);
  border-top: 1px solid var(--color-hairline);
  display: flex;
  justify-content: flex-end;
}
</style>
