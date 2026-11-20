<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { formatSeconds } from '../api/client'
import { studentApi } from '../api/modules/student'

const props = defineProps({
  playback: { type: Object, required: true },
  courseId: { type: String, required: true },
})

const emit = defineEmits(['context-change', 'ask-at-current', 'start-chapter-quiz'])

const videoEl = ref(null)
const currentSec = ref(0)
const lastSteadySec = ref(0)
const nativeDuration = ref(0)
const playbackRate = ref(1.0)
const isPlaying = ref(false)
const activeSubTab = ref('chapters')
const transcriptKeyword = ref('')

// 笔记与书签筛选/二次编辑状态
const notes = ref([])
const bookmarksOnly = ref(false)
const newNoteText = ref('')
const newNoteBookmark = ref(false)
const savingNote = ref(false)
const editingNoteId = ref('')
const editNoteText = ref('')
const editNoteBookmark = ref(false)

// 15 秒真实播放定时心跳累计秒数
let heartbeatTimer = null
let accumulatedPlaySec = 0
let programmaticSeeking = false

const hasRealVideo = computed(() => Boolean(props.playback?.media_url))
const chapters = computed(() => props.playback?.chapters || [])
const segments = computed(() => props.playback?.transcript_segments || [])
const duration = computed(() => Math.max(1, nativeDuration.value || props.playback?.duration_sec || 300))

const displayedNotes = computed(() => {
  if (!bookmarksOnly.value) return notes.value
  return notes.value.filter((n) => Boolean(n.is_bookmark))
})

const activeChapter = computed(() => {
  return (
    chapters.value.find((ch) => currentSec.value >= ch.start_sec && currentSec.value < ch.end_sec) ||
    chapters.value[chapters.value.length - 1] ||
    null
  )
})

const filteredSegments = computed(() => {
  const kw = transcriptKeyword.value.trim().toLowerCase()
  if (!kw) return segments.value
  return segments.value.filter((s) => s.text.toLowerCase().includes(kw))
})

watch(
  [currentSec, activeChapter],
  () => {
    emit('context-change', {
      video_id: props.playback?.video_id,
      video_title: props.playback?.title,
      chapter_id: activeChapter.value?.chapter_id || null,
      chapter_title: activeChapter.value?.title || '',
      timestamp_sec: currentSec.value,
    })
  },
  { immediate: true }
)

watch(
  () => props.playback?.video_id,
  async (vid) => {
    flushHeartbeatIfAny()
    stopPlayback()
    currentSec.value = 0
    lastSteadySec.value = 0
    nativeDuration.value = 0
    editingNoteId.value = ''
    if (vid) await loadNotes()
  },
  { immediate: true }
)

watch(bookmarksOnly, () => {
  if (props.playback?.video_id) {
    loadNotes()
  }
})

function startHeartbeatLoop() {
  if (heartbeatTimer) return
  heartbeatTimer = setInterval(() => {
    if (!isPlaying.value || !props.playback?.video_id) return
    accumulatedPlaySec += 1
    if (accumulatedPlaySec >= 15) {
      const secToSend = accumulatedPlaySec
      accumulatedPlaySec = 0
      reportWatchEvent('watch_progress', false, secToSend)
    }
  }, 1000)
}

function stopHeartbeatLoop() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

function flushHeartbeatIfAny() {
  if (accumulatedPlaySec >= 3 && props.playback?.video_id) {
    const secToSend = accumulatedPlaySec
    accumulatedPlaySec = 0
    reportWatchEvent('watch_progress', false, secToSend)
  } else {
    accumulatedPlaySec = 0
  }
}

onMounted(startHeartbeatLoop)

onUnmounted(() => {
  flushHeartbeatIfAny()
  stopHeartbeatLoop()
  stopPlayback()
})

function onNativeLoadedMetadata() {
  if (videoEl.value?.duration && Number.isFinite(videoEl.value.duration)) {
    nativeDuration.value = Math.floor(videoEl.value.duration)
  }
  if (videoEl.value) {
    videoEl.value.playbackRate = playbackRate.value
  }
}

function changePlaybackRate(rate) {
  playbackRate.value = Number(rate)
  if (videoEl.value) {
    videoEl.value.playbackRate = playbackRate.value
  }
}

function onNativeTimeUpdate() {
  if (!videoEl.value) return
  const nowSec = Math.floor(videoEl.value.currentTime || 0)
  // 正常连续播放更新稳态游标（<= 3 秒变化视为连续播放，大幅跳变交由 onNativeSeeked 判断）
  if (Math.abs(nowSec - currentSec.value) <= 3) {
    lastSteadySec.value = nowSec
  }
  currentSec.value = nowSec
}

function onNativeSeeked() {
  if (programmaticSeeking || !videoEl.value) {
    programmaticSeeking = false
    return
  }
  const target = Math.floor(videoEl.value.currentTime || 0)
  const delta = target - lastSteadySec.value
  currentSec.value = target
  lastSteadySec.value = target
  if (delta > 30) {
    reportWatchEvent('skip_segment', false, 1)
  } else if (delta < -5) {
    reportWatchEvent('replay_segment', false, 5)
  }
}

function onNativePlay() {
  isPlaying.value = true
}

function onNativePause() {
  isPlaying.value = false
  flushHeartbeatIfAny()
}

function onNativeEnded() {
  isPlaying.value = false
  const extraSec = Math.max(1, accumulatedPlaySec || 15)
  accumulatedPlaySec = 0
  reportWatchEvent('watch_progress', true, extraSec)
}

function togglePlay() {
  if (hasRealVideo.value && videoEl.value) {
    if (videoEl.value.paused) {
      videoEl.value.play().catch(() => {})
    } else {
      videoEl.value.pause()
    }
  } else {
    // 无实体视频讲次的阅读计时模式
    isPlaying.value = !isPlaying.value
    if (!isPlaying.value) flushHeartbeatIfAny()
  }
}

function stopPlayback() {
  isPlaying.value = false
  if (videoEl.value && !videoEl.value.paused) {
    videoEl.value.pause()
  }
}

function seekTo(sec, explicitReplay = false) {
  const prev = currentSec.value
  const target = Math.max(0, Math.min(duration.value, Math.floor(Number(sec) || 0)))
  const delta = target - prev

  currentSec.value = target
  lastSteadySec.value = target

  if (hasRealVideo.value && videoEl.value) {
    programmaticSeeking = true
    videoEl.value.currentTime = target
    if (explicitReplay) {
      videoEl.value.play().catch(() => {})
    }
  }

  if (delta > 30 && !explicitReplay) {
    reportWatchEvent('skip_segment', false, 1)
  } else if (explicitReplay || delta < -5) {
    reportWatchEvent('replay_segment', false, 5)
  } else {
    reportWatchEvent('watch_progress', false, 5)
  }
}

function pauseAndAskTutor() {
  stopPlayback()
  emit('ask-at-current', {
    video_id: props.playback?.video_id,
    video_title: props.playback?.title,
    chapter_id: activeChapter.value?.chapter_id || null,
    chapter_title: activeChapter.value?.title || '',
    timestamp_sec: currentSec.value,
  })
}

async function reportWatchEvent(eventType, completed, watchedSec = 15) {
  if (!props.playback?.video_id) return
  try {
    await studentApi.reportLearningEvent({
      course_id: props.courseId,
      video_id: props.playback.video_id,
      event_type: eventType,
      position_sec: currentSec.value,
      watched_seconds: Math.max(1, Math.min(3600, Math.floor(watchedSec))),
      completed: Boolean(completed),
    })
  } catch {
    // 若旧后端尚未枚举 skip_segment，降级上报 watch_progress 保证不丢失进度
    if (eventType === 'skip_segment') {
      studentApi.reportLearningEvent({
        course_id: props.courseId,
        video_id: props.playback.video_id,
        event_type: 'watch_progress',
        position_sec: currentSec.value,
        watched_seconds: 1,
        completed: false,
      }).catch(() => {})
    }
  }
}

async function loadNotes() {
  try {
    const res = await studentApi.fetchNotes(props.playback.video_id, {
      bookmarksOnly: bookmarksOnly.value,
    })
    notes.value = res.items || []
  } catch {
    notes.value = []
  }
}

async function saveNote() {
  if (!newNoteText.value.trim() || savingNote.value) return
  savingNote.value = true
  try {
    await studentApi.createNote({
      course_id: props.courseId,
      video_id: props.playback.video_id,
      timestamp_sec: currentSec.value,
      content: newNoteText.value.trim(),
      is_bookmark: newNoteBookmark.value,
    })
    newNoteText.value = ''
    newNoteBookmark.value = false
    await loadNotes()
  } finally {
    savingNote.value = false
  }
}

function startEditNote(note) {
  editingNoteId.value = note.id
  editNoteText.value = note.content
  editNoteBookmark.value = Boolean(note.is_bookmark)
}

function cancelEditNote() {
  editingNoteId.value = ''
  editNoteText.value = ''
}

async function submitEditNote(note) {
  if (!editNoteText.value.trim()) return
  await studentApi.updateNote(note.id, {
    content: editNoteText.value.trim(),
    is_bookmark: editNoteBookmark.value,
  })
  editingNoteId.value = ''
  await loadNotes()
}

async function removeNote(id) {
  await studentApi.deleteNote(id)
  await loadNotes()
}

defineExpose({ seekTo })
</script>

<template>
  <div class="media-doc-block">
    <div class="media-frame">
      <video
        v-if="hasRealVideo"
        ref="videoEl"
        :src="playback.media_url"
        controls
        preload="metadata"
        class="native-video"
        @loadedmetadata="onNativeLoadedMetadata"
        @timeupdate="onNativeTimeUpdate"
        @seeked="onNativeSeeked"
        @play="onNativePlay"
        @pause="onNativePause"
        @ended="onNativeEnded"
      ></video>

      <div v-else class="callout empty-video-callout">
        <span class="callout-icon" aria-hidden="true">
          <svg class="icon-16" viewBox="0 0 16 16"><rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="m6.5 6 3.5 2-3.5 2V6Z"/></svg>
        </span>
        <div>
          <strong>{{ playback.title }}</strong>
          <div class="muted-text" style="margin-top: 2px;">
            当前讲次收录章节提纲与转写文稿，可直接点击下方时间锚点阅读、开启专注计时或发起章节自测。
          </div>
        </div>
      </div>

      <!-- 极简纸面进度轴与章节刻度 -->
      <div class="scrubber-bar">
        <input
          type="range"
          min="0"
          :max="duration"
          :value="currentSec"
          aria-label="章节时间进度"
          @change="seekTo($event.target.value, false)"
          class="timeline-range"
        />
        <div class="chapter-ticks" v-if="chapters.length">
          <button
            v-for="ch in chapters"
            :key="ch.chapter_id"
            type="button"
            class="tick-mark"
            :style="{ left: `${Math.min(99, (ch.start_sec / duration) * 100)}%` }"
            :title="`${formatSeconds(ch.start_sec)} ${ch.title}`"
            @click="seekTo(ch.start_sec, true)"
          ></button>
        </div>
      </div>

      <!-- Ghost 工具栏 -->
      <div class="controls-row">
        <div class="controls-left">
          <button type="button" class="btn" @click="togglePlay">
            {{ isPlaying ? (hasRealVideo ? '暂停' : '暂停阅读计时') : (hasRealVideo ? '播放' : '开始阅读计时') }}
          </button>
          <span class="time-readout">{{ formatSeconds(currentSec) }} / {{ formatSeconds(duration) }}</span>
          <select
            v-if="hasRealVideo"
            class="rate-select"
            aria-label="播放倍速"
            :value="playbackRate"
            @change="changePlaybackRate($event.target.value)"
          >
            <option :value="0.75">0.75 倍速</option>
            <option :value="1.0">1.0 倍速</option>
            <option :value="1.25">1.25 倍速</option>
            <option :value="1.5">1.5 倍速</option>
            <option :value="2.0">2.0 倍速</option>
          </select>
          <span v-if="activeChapter" class="chapter-live-tag">{{ activeChapter.title }}</span>
        </div>

        <div class="controls-right">
          <button type="button" class="btn btn-link" @click="pauseAndAskTutor">
            边看边问智能助教（{{ formatSeconds(currentSec) }}）
          </button>
          <button
            v-if="activeChapter"
            type="button"
            class="btn btn-outline"
            @click="emit('start-chapter-quiz', activeChapter)"
          >
            本节随堂测验
          </button>
        </div>
      </div>
    </div>

    <!-- 文档内视图切换条 -->
    <div class="tabs-bar" style="margin-top: 24px;">
      <button type="button" class="tab-pill" :class="{ active: activeSubTab === 'chapters' }" @click="activeSubTab = 'chapters'">
        章节提纲 ({{ chapters.length }})
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeSubTab === 'transcript' }" @click="activeSubTab = 'transcript'">
        完整转写稿 ({{ segments.length }})
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeSubTab === 'notes' }" @click="activeSubTab = 'notes'">
        时间戳笔记 ({{ displayedNotes.length }})
      </button>
    </div>

    <!-- 1. 章节提纲文档块列表 -->
    <div v-if="activeSubTab === 'chapters'" class="block-list">
      <div
        v-for="ch in chapters"
        :key="ch.chapter_id"
        class="block-row chapter-block"
        :class="{ 'is-selected': activeChapter?.chapter_id === ch.chapter_id }"
        tabindex="0"
        @click="seekTo(ch.start_sec, true)"
        @keydown.enter="seekTo(ch.start_sec, true)"
      >
        <span class="block-handle" aria-hidden="true">⋮⋮</span>
        <div class="block-body">
          <div class="ch-head">
            <span class="ch-title">{{ ch.title }}</span>
            <span class="ts-anchor">{{ formatSeconds(ch.start_sec) }} – {{ formatSeconds(ch.end_sec) }}</span>
          </div>
          <p class="ch-summary">{{ ch.summary }}</p>
          <div class="ch-footer">
            <div class="kp-tags">
              <span v-for="kp in (ch.knowledge_points || [])" :key="kp" class="badge">{{ kp }}</span>
            </div>
            <button type="button" class="btn btn-sm" @click.stop="emit('start-chapter-quiz', ch)">章节自测</button>
          </div>
        </div>
      </div>
      <p v-if="!chapters.length" class="muted-text">当前讲次暂无已发布的章节提纲。</p>
    </div>

    <!-- 2. 视频转写文字稿块列表 -->
    <div v-else-if="activeSubTab === 'transcript'">
      <div class="search-line">
        <svg class="icon-16" viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3" stroke-linecap="round"/></svg>
        <input
          v-model="transcriptKeyword"
          class="inline-input"
          placeholder="检索视频转写正文（点击任意行跳转对应时间点）..."
        />
      </div>
      <div class="block-list transcript-scroll">
        <div
          v-for="(seg, idx) in filteredSegments"
          :key="idx"
          class="block-row"
          :class="{ 'is-selected': currentSec >= seg.start_sec && currentSec < seg.end_sec }"
          tabindex="0"
          @click="seekTo(seg.start_sec, true)"
          @keydown.enter="seekTo(seg.start_sec, true)"
        >
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <span class="ts-anchor">{{ formatSeconds(seg.start_sec) }}</span>
          <span class="block-body">{{ seg.text }}</span>
        </div>
        <p v-if="!filteredSegments.length" class="muted-text">未匹配到对应的转写段落。</p>
      </div>
    </div>

    <!-- 3. 时间戳笔记与书签（支持书签过滤与二次编辑） -->
    <div v-else>
      <div class="note-composer">
        <input
          v-model="newNoteText"
          class="input"
          :placeholder="`在 ${formatSeconds(currentSec)} 处记录学习笔记...`"
          @keyup.enter="saveNote"
        />
        <label class="bookmark-chk">
          <input type="checkbox" v-model="newNoteBookmark" />
          <span>设为重点书签</span>
        </label>
        <button type="button" class="btn btn-primary btn-sm" :disabled="savingNote || !newNoteText.trim()" @click="saveNote">
          {{ savingNote ? '保存中...' : '记录' }}
        </button>
      </div>

      <div class="note-filter-bar">
        <label class="bookmark-chk">
          <input type="checkbox" v-model="bookmarksOnly" />
          <span>仅看重点书签</span>
        </label>
        <span class="label-text">点击时间戳可回跳对应视频片段</span>
      </div>

      <div class="block-list">
        <div v-for="n in displayedNotes" :key="n.id" class="block-row note-row-wrap">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>

          <template v-if="editingNoteId === n.id">
            <div class="block-body note-edit-box">
              <input v-model="editNoteText" class="input" @keyup.enter="submitEditNote(n)" />
              <div class="note-edit-actions">
                <label class="bookmark-chk">
                  <input type="checkbox" v-model="editNoteBookmark" />
                  <span>重点书签</span>
                </label>
                <div style="display: flex; gap: 4px;">
                  <button type="button" class="btn btn-sm" @click="cancelEditNote">取消</button>
                  <button type="button" class="btn btn-primary btn-sm" @click="submitEditNote(n)">保存修改</button>
                </div>
              </div>
            </div>
          </template>

          <template v-else>
            <div class="block-body note-body" @click="seekTo(n.timestamp_sec, true)">
              <span class="ts-anchor">{{ formatSeconds(n.timestamp_sec) }}</span>
              <span v-if="n.is_bookmark" class="badge badge-link">重点书签</span>
              <span>{{ n.content }}</span>
            </div>
            <div class="note-actions">
              <button type="button" class="btn btn-sm" @click="startEditNote(n)">编辑</button>
              <button type="button" class="btn btn-sm btn-danger" @click="removeNote(n.id)">删除</button>
            </div>
          </template>
        </div>
        <p v-if="!displayedNotes.length" class="muted-text">
          {{ bookmarksOnly ? '当前视频暂无重点书签记录。' : '暂无笔记。输入文字保存后将自动绑定当前播放时间点。' }}
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.media-frame {
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--surface-white-page);
  overflow: hidden;
}

.native-video {
  width: 100%;
  max-height: 420px;
  display: block;
  background-color: #000000;
}

.empty-video-callout {
  border-radius: 0;
  border-bottom: 1px solid var(--color-hairline);
}

.scrubber-bar {
  position: relative;
  padding: var(--spacing-8) var(--spacing-12) var(--spacing-4);
  border-bottom: 1px solid var(--color-hairline);
  background-color: var(--color-paper);
}

.timeline-range {
  width: 100%;
  accent-color: var(--color-ink);
  cursor: pointer;
}

.chapter-ticks {
  position: relative;
  height: 8px;
}

.tick-mark {
  position: absolute;
  top: -2px;
  width: 6px;
  height: 6px;
  padding: 0;
  border: none;
  border-radius: var(--radius-pills);
  background-color: var(--color-link-blue);
  cursor: pointer;
}

.controls-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  padding: var(--spacing-8) var(--spacing-12);
  background-color: var(--color-paper);
  flex-wrap: wrap;
}

.controls-left,
.controls-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.time-readout {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-ink-muted);
}

.rate-select {
  background-color: transparent;
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-inputs);
  padding: 2px 6px;
  font-size: var(--text-label);
  color: var(--color-ink);
}

.chapter-live-tag {
  font-size: var(--text-label);
  color: var(--color-ink-muted);
}

.chapter-block {
  cursor: pointer;
  padding-top: var(--spacing-8);
  padding-bottom: var(--spacing-8);
}

.ch-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.ch-title {
  font-weight: var(--font-weight-medium);
}

.ts-anchor {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-link-blue);
  flex-shrink: 0;
  cursor: pointer;
}

.ch-summary {
  margin: var(--spacing-4) 0 var(--spacing-8);
  color: var(--color-ink-secondary);
}

.ch-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.kp-tags {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.search-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
  margin-bottom: var(--spacing-12);
  color: var(--color-ink-muted);
}

.transcript-scroll {
  max-height: 320px;
  overflow-y: auto;
}

.note-composer {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  margin-bottom: var(--spacing-12);
  flex-wrap: wrap;
}

.note-composer .input {
  flex: 1;
  min-width: 200px;
}

.note-filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-12);
  padding-bottom: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
}

.bookmark-chk {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-4);
  font-size: var(--text-label);
  color: var(--color-ink-secondary);
  cursor: pointer;
}

.note-body {
  display: flex;
  align-items: baseline;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  cursor: pointer;
}

.note-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}

.note-edit-box {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
  padding: var(--spacing-4) 0;
}

.note-edit-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
}
</style>
