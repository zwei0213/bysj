<script setup>
import { ref, watch, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { studentApi } from '../../api/modules/student'
import { formatStatus } from '../../utils/labels'
import VideoChapterPlayer from '../../components/VideoChapterPlayer.vue'
import AiTutorPanel from '../../components/AiTutorPanel.vue'
import QuizRunnerModal from '../../components/QuizRunnerModal.vue'

const route = useRoute()
const router = useRouter()
const courseId = route.params.courseId

const course = ref(null)
const loading = ref(true)
const selectedVideoId = ref('')
const playback = ref(null)
const liveVideoCtx = ref(null)
const activeQuizSession = ref(null)

// 讲义展开与引用高亮状态
const expandedDocId = ref('')
const highlightedDocId = ref('')
const highlightedSnippet = ref('')

const playerSectionEl = ref(null)
const docSectionEl = ref(null)
const playerRef = ref(null)
const tutorRef = ref(null)
const tutorSectionEl = ref(null)
const errorBanner = ref('')

function scrollSmooth(el) {
  if (!el) return
  const prefersReduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  el.scrollIntoView({ behavior: prefersReduced ? 'auto' : 'smooth', block: 'start' })
}

async function loadCourse() {
  loading.value = true
  errorBanner.value = ''
  try {
    course.value = await studentApi.fetchCourseDetail(courseId)
    const queryChapterId = route.query.chapterId ? String(route.query.chapterId) : ''
    const initialVid = route.query.videoId ? String(route.query.videoId) : (course.value.videos?.[0]?.id || '')
    const initialSeek = Number(route.query.seek || 0)

    if (queryChapterId) {
      await jumpToChapterById(queryChapterId)
    } else if (initialVid) {
      await selectVideo(initialVid, initialSeek)
    }
  } catch (err) {
    errorBanner.value = err.message || '无法加载课程内容'
  } finally {
    loading.value = false
  }
}

watch(
  () => route.query.chapterId,
  (newChId) => {
    if (newChId && course.value) {
      jumpToChapterById(String(newChId))
    }
  }
)

async function selectVideo(videoId, initialSeek = 0) {
  selectedVideoId.value = videoId
  playback.value = await studentApi.fetchVideoPlayback(videoId)
  if (initialSeek > 0) {
    await nextTick()
    setTimeout(() => playerRef.value?.seekTo(initialSeek, false), 80)
  }
}

function handleAskAtCurrent(ctx) {
  liveVideoCtx.value = ctx
  scrollSmooth(tutorSectionEl.value)
  tutorRef.value?.focusWithPrompt(`我在阅读「${ctx.chapter_title || '当前片段'}」时有个问题：`)
}

async function handleCitationJump(cit) {
  scrollSmooth(playerSectionEl.value)
  if (cit.video_id && cit.video_id !== selectedVideoId.value) {
    await selectVideo(cit.video_id, cit.timestamp_sec || 0)
  } else if (cit.timestamp_sec !== null && cit.timestamp_sec !== undefined) {
    playerRef.value?.seekTo(cit.timestamp_sec, true)
  }
}

/**
 * 处理点击 [课程讲义] 引用：自动展开讲义阅读区并高亮对应引用片段（或打开原件链接）。
 */
async function handleOpenDocument(cit) {
  const docs = course.value?.documents || []
  if (!docs.length) {
    if (cit.file_url) {
      window.open(cit.file_url, '_blank', 'noopener')
    }
    return
  }

  const citDocId = cit.document_id || cit.doc_id || cit.source_id || ''
  const citTitle = (cit.title || '').trim()

  const target =
    docs.find((d) => citDocId && d.id === citDocId) ||
    docs.find((d) => citTitle && (d.title === citTitle || citTitle.includes(d.title) || d.title.includes(citTitle))) ||
    docs[0]

  if (target) {
    expandedDocId.value = target.id
    highlightedDocId.value = target.id
    highlightedSnippet.value = (cit.snippet || '').trim()
    await nextTick()
    scrollSmooth(docSectionEl.value)
    if (!target.content_text && target.file_url) {
      window.open(target.file_url, '_blank', 'noopener')
    }
  }
}

/**
 * 安全分段渲染讲义正文中的引用高亮片段（无 v-html XSS 风险）。
 */
function splitDocTextWithHighlight(doc) {
  const full = doc.content_text || ''
  if (highlightedDocId.value !== doc.id || !highlightedSnippet.value) {
    return [{ text: full, highlight: false }]
  }
  const needle = highlightedSnippet.value.slice(0, 60).trim()
  if (!needle) return [{ text: full, highlight: false }]

  const idx = full.indexOf(needle)
  if (idx === -1) {
    return [{ text: full, highlight: false }]
  }
  return [
    { text: full.slice(0, idx), highlight: false },
    { text: full.slice(idx, idx + needle.length), highlight: true },
    { text: full.slice(idx + needle.length), highlight: false },
  ]
}

function getExamBlockReason(ex) {
  const used = Number(ex.used_attempts ?? 0)
  const max = Number(ex.max_attempts ?? 0)
  if (max > 0 && used >= max) {
    return `作答次数已用尽 (${used}/${max})`
  }
  const now = Date.now()
  if (ex.start_time) {
    const st = Date.parse(ex.start_time)
    if (!Number.isNaN(st) && now < st) return '尚未到开放时间'
  }
  if (ex.end_time) {
    const et = Date.parse(ex.end_time)
    if (!Number.isNaN(et) && now > et) return '考试已截止'
  }
  return ''
}

function formatExamWindow(ex) {
  const parts = []
  if (ex.start_time) parts.push(`起 ${String(ex.start_time).replace('T', ' ').slice(0, 16)}`)
  if (ex.end_time) parts.push(`止 ${String(ex.end_time).replace('T', ' ').slice(0, 16)}`)
  return parts.join(' · ')
}

async function launchQuiz(sessionType, extra = {}) {
  errorBanner.value = ''
  try {
    activeQuizSession.value = await studentApi.startQuizSession(courseId, {
      session_type: sessionType,
      chapter_id: extra.chapter_id || null,
      exam_id: extra.exam_id || null,
      count: 3,
    })
  } catch (err) {
    errorBanner.value = err.message || '当前课程暂无可用试题'
  }
}

/**
 * 支持本视频及跨视频章节精确定位并跳转播放。
 */
async function jumpToChapterById(chapterId) {
  activeQuizSession.value = null
  if (!chapterId) return

  // 1. 先在当前已加载视频中查找
  const inCurrent = playback.value?.chapters?.find((c) => c.chapter_id === chapterId)
  if (inCurrent) {
    scrollSmooth(playerSectionEl.value)
    await nextTick()
    setTimeout(() => playerRef.value?.seekTo(inCurrent.start_sec, true), 60)
    return
  }

  // 2. 跨视频遍历定位包含该 chapterId 的讲次
  const vids = course.value?.videos || []
  for (const v of vids) {
    try {
      const pb = await studentApi.fetchVideoPlayback(v.id)
      const hit = pb?.chapters?.find((c) => c.chapter_id === chapterId)
      if (hit) {
        selectedVideoId.value = v.id
        playback.value = pb
        scrollSmooth(playerSectionEl.value)
        await nextTick()
        setTimeout(() => playerRef.value?.seekTo(hit.start_sec, true), 90)
        return
      }
    } catch {
      // 忽略未发布讲次继续查找
    }
  }

  // 兜底：若仍未匹配，至少加载首讲
  if (!selectedVideoId.value && vids.length) {
    await selectVideo(vids[0].id, 0)
  }
}

onMounted(loadCourse)
</script>

<template>
  <article class="doc-page">
    <div style="margin-bottom: 16px;">
      <button type="button" class="btn btn-sm" style="margin-left: -8px;" @click="router.push('/student')">
        ← 返回学习中心
      </button>
    </div>

    <div v-if="errorBanner" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorBanner }}</span>
    </div>

    <p v-if="loading" class="muted-text">正在打开课程文档...</p>

    <template v-else-if="course">
      <!-- 课程文档页眉 -->
      <header class="doc-header">
        <div class="meta-line">
          <span class="badge">{{ course.category }}</span>
          <span class="badge">修读进度 {{ course.progress_pct }}%</span>
          <span class="label-text">授课教师：{{ course.teacher_name }}</span>
        </div>
        <h1 class="doc-display" style="margin-top: 8px;">{{ course.title }}</h1>
        <p v-if="course.subtitle || course.description" class="doc-subtitle">
          {{ course.subtitle || course.description }}
        </p>

        <div class="header-actions">
          <button type="button" class="btn btn-primary" @click="launchQuiz('personalized')">
            发起个性化薄弱点测验
          </button>
        </div>

        <!-- 正式组卷考试卡片区（显示已考/限次、时长、开放时段与超限禁用提示） -->
        <div v-if="course.exams?.length" class="exam-cards">
          <div v-for="ex in course.exams" :key="ex.id" class="exam-item-row">
            <div class="exam-info">
              <div>
                <strong>试卷：{{ ex.title }}</strong>
                <span class="badge" style="margin-left: 6px;">限时 {{ ex.time_limit_min || 30 }} 分钟</span>
                <span class="badge" style="margin-left: 4px;">
                  次数 {{ ex.used_attempts ?? 0 }} / {{ ex.max_attempts ?? '不限' }}
                </span>
              </div>
              <div v-if="formatExamWindow(ex)" class="label-text" style="margin-top: 2px;">
                开放时段：{{ formatExamWindow(ex) }}
              </div>
            </div>
            <div>
              <button
                type="button"
                class="btn btn-outline btn-sm"
                :disabled="Boolean(getExamBlockReason(ex))"
                @click="launchQuiz('exam', { exam_id: ex.id })"
              >
                {{ getExamBlockReason(ex) || '开始作答' }}
              </button>
            </div>
          </div>
        </div>
      </header>

      <!-- 1. 视频讲次与章节阅读 -->
      <section ref="playerSectionEl" class="doc-section">
        <div class="section-header">
          <h2 class="section-title">授课视频与章节提纲</h2>
        </div>

        <div v-if="course.videos?.length" class="tabs-bar">
          <button
            v-for="v in course.videos"
            :key="v.id"
            type="button"
            class="tab-pill"
            :class="{ active: selectedVideoId === v.id }"
            @click="selectVideo(v.id)"
          >
            {{ v.title }}
          </button>
        </div>

        <VideoChapterPlayer
          v-if="playback"
          ref="playerRef"
          :playback="playback"
          :course-id="courseId"
          @context-change="liveVideoCtx = $event"
          @ask-at-current="handleAskAtCurrent"
          @start-chapter-quiz="(ch) => launchQuiz('chapter_practice', { chapter_id: ch.chapter_id })"
        />

        <p v-else class="muted-text">当前课程暂未发布授课视频章节。</p>
      </section>

      <!-- 2. 课程配套讲义文档块列表 -->
      <section ref="docSectionEl" class="doc-section">
        <div class="section-header">
          <h2 class="section-title">课程配套讲义</h2>
          <span class="label-text">已同步纳入本课检索知识库 · 点击智能助教讲义引用自动定位至此</span>
        </div>

        <div v-if="course.documents?.length" class="block-list">
          <div
            v-for="doc in course.documents"
            :key="doc.id"
            class="doc-item-wrap"
            :class="{ 'doc-highlighted': highlightedDocId === doc.id }"
          >
            <div
              class="block-row doc-row"
              tabindex="0"
              @click="expandedDocId = expandedDocId === doc.id ? '' : doc.id"
              @keydown.enter="expandedDocId = expandedDocId === doc.id ? '' : doc.id"
            >
              <span class="block-handle" aria-hidden="true">⋮⋮</span>
              <div class="block-body doc-head">
                <span>
                  {{ expandedDocId === doc.id ? '▾' : '▸' }} {{ doc.title }}
                  <span v-if="highlightedDocId === doc.id" class="badge badge-link" style="margin-left: 6px;">智能助教引用定位</span>
                </span>
                <div class="doc-right">
                  <a
                    v-if="doc.file_url"
                    :href="doc.file_url"
                    target="_blank"
                    rel="noopener"
                    class="doc-download"
                    @click.stop
                  >
                    下载/预览原件
                  </a>
                  <span class="badge">{{ formatStatus(doc.index_status) }}</span>
                </div>
              </div>
            </div>

            <div v-if="expandedDocId === doc.id" class="doc-drawer">
              <div v-if="highlightedDocId === doc.id && highlightedSnippet" class="callout callout-accent-blue" style="margin-bottom: 8px;">
                <span class="callout-icon" aria-hidden="true">❝</span>
                <div>
                  <div class="label-text">智能助教引用摘录</div>
                  <div style="font-size: 13px;">{{ highlightedSnippet }}</div>
                </div>
              </div>
              <pre class="doc-preview"><template v-for="(seg, sidx) in splitDocTextWithHighlight(doc)" :key="sidx"><mark v-if="seg.highlight" class="cit-mark">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template></template></pre>
            </div>
          </div>
        </div>
        <p v-else class="muted-text">当前课程暂无配套文字讲义。</p>
      </section>

      <!-- 3. 教师置顶精选问答 -->
      <section v-if="course.faqs?.length" class="doc-section">
        <div class="section-header">
          <h2 class="section-title">教师精选问答</h2>
        </div>
        <div class="block-list">
          <div v-for="fq in course.faqs" :key="fq.id" class="block-row">
            <span class="block-handle" aria-hidden="true">⋮⋮</span>
            <div class="block-body">
              <div>
                <strong>问：{{ fq.question }}</strong>
                <span v-if="fq.is_pinned" class="badge" style="margin-left: 6px;">置顶</span>
              </div>
              <div class="muted-text" style="margin-top: 2px;">答：{{ fq.answer }}</div>
            </div>
          </div>
        </div>
      </section>

      <!-- 4. 课程专属文献溯源 AI 助教 -->
      <section ref="tutorSectionEl" class="doc-section">
        <AiTutorPanel
          ref="tutorRef"
          :course-id="courseId"
          :video-context="liveVideoCtx"
          @jump-to-timestamp="handleCitationJump"
          @open-document="handleOpenDocument"
        />
      </section>

      <QuizRunnerModal
        v-if="activeQuizSession"
        :session="activeQuizSession"
        @close="activeQuizSession = null"
        @submitted="loadCourse"
        @jump-chapter="jumpToChapterById"
      />
    </template>
  </article>
</template>

<style scoped>
.meta-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.header-actions {
  display: flex;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-top: var(--spacing-16);
}

.exam-cards {
  margin-top: var(--spacing-12);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.exam-item-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-12);
  flex-wrap: wrap;
  padding: var(--spacing-8) var(--spacing-12);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--surface-white-page);
}

.doc-item-wrap {
  border-bottom: 1px solid var(--color-hairline);
}

.doc-highlighted {
  background-color: rgba(46, 170, 220, 0.04);
  border-radius: var(--radius-cards);
}

.doc-row {
  cursor: pointer;
}

.doc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.doc-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
}

.doc-download {
  font-size: var(--text-label);
  color: var(--color-link-blue);
}

.doc-drawer {
  margin: var(--spacing-4) 0 var(--spacing-12);
}

.doc-preview {
  margin: 0;
  padding: var(--spacing-12) var(--spacing-16);
  background-color: var(--surface-white-page);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  white-space: pre-wrap;
  font-size: 13px;
  line-height: var(--leading-body);
  overflow-x: auto;
}

.cit-mark {
  background-color: rgba(46, 170, 220, 0.25);
  color: var(--color-ink);
  padding: 1px 3px;
  border-radius: 2px;
}
</style>
