<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import { formatSeconds } from '../api/client'
import { studentApi } from '../api/modules/student'
import { formatSourceType, formatRole } from '../utils/labels'
import { renderMarkdownWithMath } from '../utils/markdown'

const props = defineProps({
  courseId: { type: String, required: true },
  videoContext: { type: Object, default: null },
})

const emit = defineEmits(['jump-to-timestamp', 'open-document'])

const viewMode = ref('chat') // chat | bookmarks
const threads = ref([])
const activeThreadId = ref('')
const messages = ref([])
const bookmarkedMessages = ref([])
const loadingBookmarks = ref(false)

const questionInput = ref('')
const attachVideoCtx = ref(true)
const sending = ref(false)
const loadingThreads = ref(false)
const errorTip = ref('')
const inputEl = ref(null)

// 举报原因输入状态
const reportingMsgId = ref('')
const reportReason = ref('')

async function loadThreads() {
  loadingThreads.value = true
  try {
    const res = await studentApi.fetchTutorThreads(props.courseId)
    threads.value = res.items || []
    if (threads.value.length && !activeThreadId.value) {
      await selectThread(threads.value[0].id)
    }
  } finally {
    loadingThreads.value = false
  }
}

async function loadBookmarks() {
  loadingBookmarks.value = true
  try {
    const res = await studentApi.fetchBookmarkedMessages(props.courseId)
    bookmarkedMessages.value = res?.items || (Array.isArray(res) ? res : [])
  } catch {
    // 兼容降级：若接口暂未返回，从当前会话聚合已收藏消息
    bookmarkedMessages.value = messages.value.filter((m) => m.is_bookmarked)
  } finally {
    loadingBookmarks.value = false
  }
}

async function switchViewMode(mode) {
  viewMode.value = mode
  if (mode === 'bookmarks') {
    await loadBookmarks()
  }
}

async function selectThread(tid) {
  activeThreadId.value = tid
  viewMode.value = 'chat'
  if (!tid) {
    messages.value = []
    return
  }
  const res = await studentApi.fetchThreadMessages(tid)
  messages.value = res.items || []
}

function startNewThread() {
  viewMode.value = 'chat'
  activeThreadId.value = ''
  messages.value = []
  errorTip.value = ''
}

async function sendQuestion(customText = null, quickAction = null) {
  const q = (customText ?? questionInput.value).trim()
  if (!q || sending.value) return
  viewMode.value = 'chat'
  sending.value = true
  errorTip.value = ''
  if (!customText) questionInput.value = ''

  const body = {
    question: q,
    quick_action: quickAction,
    video_context: attachVideoCtx.value ? props.videoContext : null,
  }

  // 乐观插入学生提问与流式助教占位气泡
  const tempUserId = `tmp_u_${Date.now()}`
  const tempAsstId = `tmp_a_${Date.now()}`
  const streamingMsg = {
    id: tempAsstId,
    role: 'assistant',
    content: '',
    citations: [],
    suggested_followups: [],
    confidence: 0,
    needs_teacher_review: false,
    is_bookmarked: false,
    is_blocked: false,
    feedback_vote: null,
    _streaming: true,
  }
  messages.value.push(
    { id: tempUserId, role: 'user', content: q, created_at: new Date().toISOString() },
    streamingMsg
  )

  const wasNewThread = !activeThreadId.value
  try {
    const onEvent = (eventName, payload) => {
      const target = messages.value.find((m) => m.id === tempAsstId)
      if (!target) return
      if (eventName === 'meta' && payload?.thread_id) {
        activeThreadId.value = payload.thread_id
      } else if (eventName === 'delta' && payload?.delta) {
        target.content += payload.delta
      } else if (eventName === 'done' && payload?.assistant_message) {
        if (payload.thread_id) activeThreadId.value = payload.thread_id
        Object.assign(target, payload.assistant_message, { _streaming: false })
      }
    }

    if (wasNewThread) {
      await studentApi.createThreadAndAskStream(props.courseId, body, onEvent)
      await loadThreads()
    } else {
      await studentApi.appendThreadMessageStream(activeThreadId.value, body, onEvent)
    }

    const idx = messages.value.findIndex((m) => m.id === tempAsstId)
    if (idx !== -1 && messages.value[idx]._streaming) {
      await selectThread(activeThreadId.value)
    }
  } catch (streamErr) {
    // 若代理不支持流式，优雅降级至标准同步问答接口
    messages.value = messages.value.filter((m) => m.id !== tempUserId && m.id !== tempAsstId)
    try {
      if (activeThreadId.value) {
        await studentApi.appendThreadMessage(activeThreadId.value, body)
      } else {
        const res = await studentApi.createThreadAndAsk(props.courseId, body)
        activeThreadId.value = res.thread_id
        await loadThreads()
      }
      await selectThread(activeThreadId.value)
    } catch (err) {
      errorTip.value = err.message || streamErr.message || '智能助教响应失败，请稍后重试'
    }
  } finally {
    sending.value = false
  }
}

function handleCitationClick(cit) {
  if (cit.timestamp_sec !== null && cit.timestamp_sec !== undefined) {
    emit('jump-to-timestamp', cit)
  } else {
    emit('open-document', cit)
  }
}

async function handleFeedback(msg, payload) {
  const res = await studentApi.submitMessageFeedback(msg.id, payload)
  msg.feedback_vote = res.feedback_vote
  msg.is_bookmarked = res.is_bookmarked
  if (viewMode.value === 'bookmarks' && payload.bookmarked === false) {
    bookmarkedMessages.value = bookmarkedMessages.value.filter((item) => item.id !== msg.id)
  }
}

function openReportInput(msg) {
  if (reportingMsgId.value === msg.id) {
    reportingMsgId.value = ''
    return
  }
  reportingMsgId.value = msg.id
  reportReason.value = ''
}

async function submitReport(msg) {
  await handleFeedback(msg, {
    vote: 'reported',
    reason: reportReason.value.trim() || '内容违规或严重失实举报',
  })
  reportingMsgId.value = ''
  reportReason.value = ''
}

async function focusWithPrompt(prefill = '') {
  viewMode.value = 'chat'
  attachVideoCtx.value = true
  if (prefill) questionInput.value = prefill
  await nextTick()
  inputEl.value?.focus()
}

onMounted(loadThreads)
watch(() => props.courseId, () => {
  activeThreadId.value = ''
  loadThreads()
  if (viewMode.value === 'bookmarks') loadBookmarks()
})
defineExpose({ focusWithPrompt })
</script>

<template>
  <div class="tutor-doc">
    <div class="section-header">
      <div>
        <h3 class="section-title">课程文献检索智能助教</h3>
        <div class="label-text">严格限定本课讲义与转写稿 · 每条解答附带可核验出处</div>
      </div>
      <div class="tutor-top-actions">
        <div class="mode-toggle">
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'is-active-mode': viewMode === 'chat' }"
            @click="switchViewMode('chat')"
          >
            当前对话
          </button>
          <button
            type="button"
            class="btn btn-sm"
            :class="{ 'is-active-mode': viewMode === 'bookmarks' }"
            @click="switchViewMode('bookmarks')"
          >
            已收藏回答
          </button>
        </div>

        <select
          v-if="viewMode === 'chat' && threads.length"
          class="select thread-select"
          aria-label="切换历史问答会话"
          :value="activeThreadId"
          @change="selectThread($event.target.value)"
        >
          <option value="">新建提问页</option>
          <option v-for="t in threads" :key="t.id" :value="t.id">{{ t.title }}</option>
        </select>
        <button v-if="viewMode === 'chat'" type="button" class="btn btn-outline btn-sm" @click="startNewThread">
          + 新对话
        </button>
      </div>
    </div>

    <!-- 视图 A：已收藏回答夹 -->
    <div v-if="viewMode === 'bookmarks'" class="qa-stream">
      <p v-if="loadingBookmarks" class="muted-text">正在读取本课已收藏的优质解答...</p>
      <div v-else-if="!bookmarkedMessages.length" class="empty-qa">
        <p class="muted-text">当前课程暂未收藏任何助教解答。在对话中点击「收藏」即可沉淀至此方便考前复习。</p>
      </div>
      <article v-else v-for="bm in bookmarkedMessages" :key="bm.id" class="qa-entry assistant">
        <div class="qa-meta">
          <span class="qa-author">已收藏解答 {{ bm.thread_title ? `· ${bm.thread_title}` : '' }}</span>
          <button type="button" class="btn btn-sm" @click="handleFeedback(bm, { bookmarked: false })">
            取消收藏
          </button>
        </div>
        <div v-if="bm.question" class="bm-question">原提问：{{ bm.question }}</div>
        <div v-if="bm.is_blocked || bm.status === 'blocked'" class="callout">
          <span class="callout-icon" aria-hidden="true">!</span>
          <span class="muted-text">该条回答因质量治理复核已被管理员屏蔽。</span>
        </div>
        <div v-else class="qa-body md-content" v-html="renderMarkdownWithMath(bm.content)"></div>

        <div v-if="bm.citations?.length && !bm.is_blocked" class="citations-block">
          <div class="label-text" style="margin-bottom: 4px;">引用来源（点击跳转视频锚点或展开讲义）</div>
          <div
            v-for="(cit, cidx) in bm.citations"
            :key="cidx"
            class="block-row cit-row"
            tabindex="0"
            @click="handleCitationClick(cit)"
            @keydown.enter="handleCitationClick(cit)"
          >
            <span class="block-handle" aria-hidden="true">⋮⋮</span>
            <div class="block-body">
              <span class="cit-link">
                {{ cit.source_type === 'video_transcript' ? `[${formatSourceType(cit.source_type)} ${formatSeconds(cit.timestamp_sec)}]` : `[${formatSourceType(cit.source_type)}]` }}
                {{ cit.title }} →
              </span>
              <div class="cit-snippet">{{ cit.snippet }}</div>
            </div>
          </div>
        </div>
      </article>
    </div>

    <!-- 视图 B：当前对话流 -->
    <template v-else>
      <div v-if="videoContext" class="callout" style="margin-bottom: 16px;">
        <span class="callout-icon" aria-hidden="true">
          <svg class="icon-16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3l2 1.5"/></svg>
        </span>
        <label class="ctx-toggle">
          <input type="checkbox" v-model="attachVideoCtx" />
          <span>
            提问时自动附带当前观看位置：<strong>{{ videoContext.chapter_title || '当前视频' }}</strong>
            ({{ formatSeconds(videoContext.timestamp_sec) }})
          </span>
        </label>
      </div>

      <div v-if="errorTip" class="callout callout-danger" role="alert" style="margin-bottom: 12px;">
        <span class="callout-icon" aria-hidden="true">!</span>
        <span>{{ errorTip }}</span>
      </div>

      <div class="qa-stream">
        <p v-if="loadingThreads" class="muted-text">正在加载历史问答记录...</p>

        <div v-else-if="!messages.length" class="empty-qa">
          <p class="muted-text">
            在下方输入课程相关问题。智能助教将检索本课程已发布的讲义、视频转写稿与教师精选问答，并标注精确引用位置；超纲或低置信度问题将自动流转至授课教师收件箱。
          </p>
        </div>

        <article v-for="m in messages" :key="m.id" class="qa-entry" :class="m.role">
          <div class="qa-meta">
            <span class="qa-author">{{ formatRole(m.role) }}</span>
            <span
              v-if="m.role === 'assistant'"
              class="badge"
              :class="{ 'badge-danger': m.needs_teacher_review || m.is_blocked }"
            >
              <template v-if="m.is_blocked || m.status === 'blocked'">已屏蔽</template>
              <template v-else>
                {{ m.needs_teacher_review ? '已转交教师收件箱' : `检索置信度 ${Math.round((m.confidence || 0.85) * 100)}%` }}
              </template>
            </span>
          </div>

          <!-- 被管理员治理屏蔽的回答优雅降级提示 -->
          <div v-if="m.is_blocked || m.status === 'blocked'" class="callout" style="margin-top: 6px;">
            <span class="callout-icon" aria-hidden="true">!</span>
            <span class="muted-text">该条智能助教解答因质量或安全合规复核已被管理员屏蔽，如有疑问请重新提问或等待授课教师解答。</span>
          </div>

          <template v-else>
            <div v-if="m.role === 'assistant'" class="qa-body md-content">
              <div v-html="renderMarkdownWithMath(m.content || (m._streaming ? '正在检索课程知识库...' : ''))"></div>
              <span v-if="m._streaming" class="stream-cursor" aria-hidden="true">▍</span>
            </div>
            <div v-else class="qa-body">{{ m.content }}</div>

            <!-- 引用文献与视频锚点（支持视频秒数跳转 + 课程讲义自动展开定位） -->
            <div v-if="m.citations?.length" class="citations-block">
              <div class="label-text" style="margin-bottom: 4px;">引用来源（点击跳转视频或展开对应讲义）</div>
              <div
                v-for="(cit, cidx) in m.citations"
                :key="cidx"
                class="block-row cit-row"
                tabindex="0"
                @click="handleCitationClick(cit)"
                @keydown.enter="handleCitationClick(cit)"
              >
                <span class="block-handle" aria-hidden="true">⋮⋮</span>
                <div class="block-body">
                  <span class="cit-link">
                    {{ cit.source_type === 'video_transcript' ? `[${formatSourceType(cit.source_type)} ${formatSeconds(cit.timestamp_sec)}]` : `[${formatSourceType(cit.source_type)}]` }}
                    {{ cit.title }} →
                  </span>
                  <div class="cit-snippet">{{ cit.snippet }}</div>
                </div>
              </div>
            </div>

            <!-- Ghost 工具栏：细分 有帮助 / 答非所问 / 内容举报 -->
            <div v-if="m.role === 'assistant'" class="qa-toolbar">
              <button type="button" class="btn btn-sm" :disabled="sending" @click="sendQuestion('请把刚才的回答用更通俗易懂的方式讲一遍', 'explain_simply')">
                通俗解释
              </button>
              <button type="button" class="btn btn-sm" :disabled="sending" @click="sendQuestion('请针对刚才的知识点举一个具体例子', 'give_example')">
                举例说明
              </button>
              <button type="button" class="btn btn-sm" @click="handleFeedback(m, { bookmarked: !m.is_bookmarked })">
                {{ m.is_bookmarked ? '★ 已收藏' : '☆ 收藏' }}
              </button>
              <button
                type="button"
                class="btn btn-sm"
                :class="{ 'is-active-vote': m.feedback_vote === 'up' }"
                @click="handleFeedback(m, { vote: 'up' })"
              >
                有帮助
              </button>
              <button
                type="button"
                class="btn btn-sm"
                :class="{ 'is-active-vote': m.feedback_vote === 'down' }"
                @click="handleFeedback(m, { vote: 'down', reason: '答非所问或引用不够准确' })"
              >
                答非所问
              </button>
              <button
                type="button"
                class="btn btn-sm btn-danger"
                :class="{ 'is-active-vote': m.feedback_vote === 'reported' }"
                @click="openReportInput(m)"
              >
                {{ m.feedback_vote === 'reported' ? '已举报' : '内容举报' }}
              </button>
            </div>

            <!-- 内容举报填写原因行 -->
            <div v-if="reportingMsgId === m.id" class="report-box">
              <input
                v-model="reportReason"
                class="input"
                placeholder="填写举报原因（如：事实性错误、不当表述等，可留空直接提交）..."
                @keyup.enter="submitReport(m)"
              />
              <button type="button" class="btn btn-sm" @click="reportingMsgId = ''">取消</button>
              <button type="button" class="btn btn-primary btn-sm" @click="submitReport(m)">提交举报</button>
            </div>

            <!-- 延伸追问 -->
            <div v-if="m.suggested_followups?.length" class="followup-list">
              <button
                v-for="fq in m.suggested_followups"
                :key="fq"
                type="button"
                class="followup-link"
                :disabled="sending"
                @click="sendQuestion(fq)"
              >
                ↳ {{ fq }}
              </button>
            </div>
          </template>
        </article>
      </div>

      <!-- 底部提问撰写区 -->
      <div class="ask-composer">
        <textarea
          ref="inputEl"
          v-model="questionInput"
          class="textarea"
          rows="2"
          placeholder="请输入课程疑问（回车键发送，换行请按上档键加回车）..."
          @keydown.enter.exact.prevent="sendQuestion()"
        ></textarea>
        <div class="ask-actions">
          <span class="label-text">{{ sending ? '正在检索课程知识库并生成引用...' : '回车键发送，换行请按上档键加回车' }}</span>
          <button type="button" class="btn btn-primary" :disabled="sending || !questionInput.trim()" @click="sendQuestion()">
            {{ sending ? '生成中...' : '发送提问' }}
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.tutor-doc {
  border-top: 1px solid var(--color-hairline);
  padding-top: var(--spacing-24);
}

.tutor-top-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.mode-toggle {
  display: inline-flex;
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-buttons);
  padding: 2px;
  gap: 2px;
}

.is-active-mode {
  background-color: var(--color-hover-grey);
  font-weight: var(--font-weight-semibold);
}

.thread-select {
  width: auto;
  max-width: 200px;
  font-size: var(--text-label);
}

.ctx-toggle {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-8);
  cursor: pointer;
}

.qa-stream {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-16);
  margin-bottom: var(--spacing-16);
}

.empty-qa {
  padding: var(--spacing-12) 0;
  border-bottom: 1px solid var(--color-hairline);
}

.qa-entry {
  padding: var(--spacing-12) var(--spacing-16);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--surface-white-page);
}

.qa-entry.user {
  background-color: transparent;
  border: none;
  border-left: 2px solid var(--color-hairline);
  border-radius: 0;
  padding: var(--spacing-4) 0 var(--spacing-4) var(--spacing-12);
}

.qa-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-4);
}

.qa-author {
  font-size: var(--text-label);
  font-weight: var(--font-weight-semibold);
  color: var(--color-ink-muted);
}

.bm-question {
  font-size: var(--text-label);
  color: var(--color-ink-secondary);
  margin-bottom: var(--spacing-8);
  padding-left: var(--spacing-8);
  border-left: 2px solid var(--color-hairline);
}

.qa-body {
  white-space: pre-wrap;
  line-height: var(--leading-body);
}

.md-content {
  white-space: normal;
  word-break: break-word;
}

.md-content :deep(p) {
  margin: 0 0 8px;
}

.md-content :deep(p:last-child) {
  margin-bottom: 0;
}

.md-content :deep(ul),
.md-content :deep(ol) {
  margin: 6px 0 8px;
  padding-left: 20px;
}

.md-content :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--color-hairline);
  background: var(--color-paper);
  color: var(--color-ink-secondary);
}

.md-content :deep(pre) {
  margin: 8px 0;
  padding: 10px 12px;
  overflow-x: auto;
  border-radius: var(--radius-cards);
  background: #1e2430;
  color: #f4f6fa;
  font-size: 13px;
}

.md-content :deep(code) {
  font-family: var(--font-mono);
  font-size: 0.92em;
  background: var(--color-paper);
  padding: 1px 4px;
  border-radius: 3px;
}

.md-content :deep(pre code) {
  background: transparent;
  padding: 0;
}

.md-content :deep(.katex-display) {
  margin: 8px 0;
  overflow-x: auto;
}

.stream-cursor {
  display: inline-block;
  margin-left: 2px;
  animation: blink-cursor 0.9s steps(1) infinite;
  color: var(--color-link-blue);
}

@keyframes blink-cursor {
  50% {
    opacity: 0;
  }
}

.citations-block {
  margin-top: var(--spacing-12);
  padding-top: var(--spacing-8);
  border-top: 1px solid var(--color-hairline);
}

.cit-row {
  margin-left: 0;
  cursor: pointer;
}

.cit-link {
  color: var(--color-link-blue);
  font-weight: var(--font-weight-medium);
  font-size: 13px;
}

.cit-snippet {
  font-size: var(--text-label);
  color: var(--color-ink-muted);
  margin-top: 2px;
}

.qa-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-4);
  margin-top: var(--spacing-8);
  padding-top: var(--spacing-8);
  border-top: 1px solid var(--color-hairline);
}

.report-box {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  margin-top: var(--spacing-8);
  padding: var(--spacing-8);
  background-color: var(--color-paper);
  border-radius: var(--radius-cards);
}

.is-active-vote {
  background-color: var(--color-hover-grey);
  font-weight: var(--font-weight-semibold);
}

.followup-list {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  margin-top: var(--spacing-8);
}

.followup-link {
  border: none;
  background: transparent;
  padding: 2px 6px;
  border-radius: var(--radius-buttons);
  font-size: var(--text-label);
  color: var(--color-link-blue);
  cursor: pointer;
  text-align: left;
  transition: background-color 150ms ease;
}

.followup-link:hover:not(:disabled) {
  background-color: var(--color-hover-grey);
}

.ask-composer {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.ask-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
