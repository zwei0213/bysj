<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { formatSeconds } from '../../../api/client'
import { teacherApi } from '../../../api/modules/teacher'
import { formatStatus, formatSourceType } from '../../../utils/labels'

const props = defineProps({
  course: { type: Object, required: true },
})
const emit = defineEmits(['notify', 'refresh-courses'])

const selectedVideoId = ref('')
const videoMeta = ref(null)
const outlineVersions = ref([])
const editableChapters = ref([])
const changeNote = ref('教师人工微调章节标题、提要与跳转时间戳')
const syncingJob = ref(false)
const jobProgress = ref(0)
const savingVersion = ref(false)
const regeneratingIdx = ref(-1)
const dragIndex = ref(null)

// 提纲质量问题上报状态
const showFeedbackBar = ref(false)
const feedbackReason = ref('')
const submittingFeedback = ref(false)

let pollTimer = null

const isProcessing = computed(() => videoMeta.value?.outline_status === 'processing')

function clearPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

onUnmounted(clearPoll)

function decorateChapters(rawChapters) {
  return JSON.parse(JSON.stringify(rawChapters || [])).map((ch) => ({
    ...ch,
    knowledge_points: Array.isArray(ch.knowledge_points) ? ch.knowledge_points : [],
    _kpStr: (ch.knowledge_points || []).join(', '),
    _instruction: '',
  }))
}

function syncKpArray(ch) {
  ch.knowledge_points = String(ch._kpStr || '')
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

async function selectVideo(vid) {
  selectedVideoId.value = vid
  clearPoll()
  jobProgress.value = 0
  if (!vid) {
    videoMeta.value = null
    outlineVersions.value = []
    editableChapters.value = []
    return
  }
  const res = await teacherApi.fetchOutlineVersions(vid)
  videoMeta.value = res.video || null
  outlineVersions.value = res.versions || []
  editableChapters.value = outlineVersions.value.length
    ? decorateChapters(outlineVersions.value[0].chapters || [])
    : []

  if (videoMeta.value?.outline_status === 'processing') {
    await pollVideoJob()
    if (videoMeta.value?.outline_status === 'processing') {
      pollTimer = setInterval(() => pollVideoJob(), 3000)
    }
  }
}

async function pollVideoJob() {
  if (!selectedVideoId.value || syncingJob.value) return
  syncingJob.value = true
  try {
    const syncRes = await teacherApi.syncVideoJob(selectedVideoId.value)
    jobProgress.value = Math.max(0, Math.min(100, Number(syncRes?.progress ?? 0)))
    if (syncRes.outline_status !== 'processing') {
      clearPoll()
      emit('refresh-courses')
      await selectVideo(selectedVideoId.value)
      if (syncRes.outline_status === 'draft_review') {
        emit('notify', '视频语音转写与章节初稿已生成，请校准后发布。')
      } else if (syncRes.outline_status === 'failed') {
        emit('notify', `智能处理任务失败：${syncRes.error_message || '可在管理端重试'}`)
      }
    }
  } finally {
    syncingJob.value = false
  }
}

watch(
  () => props.course,
  (crs) => {
    const vids = crs?.videos || []
    if (selectedVideoId.value && vids.some((v) => v.id === selectedVideoId.value)) {
      return
    }
    const preferred =
      vids.find((v) => v.outline_status === 'processing') ||
      vids.find((v) => v.outline_status !== 'published') ||
      vids[0]
    selectVideo(preferred?.id || '')
  },
  { immediate: true }
)

function loadVersionIntoEditor(ver) {
  editableChapters.value = decorateChapters(ver.chapters || [])
}

function onDragStart(idx) {
  dragIndex.value = idx
}

function onDropChapter(targetIdx) {
  if (dragIndex.value === null || dragIndex.value === targetIdx) return
  const moved = editableChapters.value.splice(dragIndex.value, 1)[0]
  editableChapters.value.splice(targetIdx, 0, moved)
  editableChapters.value.forEach((c, i) => {
    c.order_index = i + 1
  })
  dragIndex.value = null
}

function addEmptyChapter() {
  const last = editableChapters.value[editableChapters.value.length - 1]
  const startSec = last ? Number(last.end_sec || 0) : 0
  const maxDur = Number(videoMeta.value?.duration_sec || startSec + 120)
  const endSec = Math.max(startSec + 60, maxDur)
  editableChapters.value.push({
    chapter_id: '',
    order_index: editableChapters.value.length + 1,
    title: `新增第 ${editableChapters.value.length + 1} 节`,
    summary: '',
    start_sec: startSec,
    end_sec: endSec,
    knowledge_points: [],
    _kpStr: '',
    _instruction: '',
  })
}

function deleteChapter(idx) {
  editableChapters.value.splice(idx, 1)
  editableChapters.value.forEach((c, i) => {
    c.order_index = i + 1
  })
}

function splitChapter(idx) {
  const ch = editableChapters.value[idx]
  syncKpArray(ch)
  const mid = Math.floor((Number(ch.start_sec) + Number(ch.end_sec)) / 2)
  editableChapters.value.splice(idx + 1, 0, {
    chapter_id: '',
    order_index: idx + 2,
    title: `${ch.title}（续）`,
    summary: ch.summary,
    start_sec: mid,
    end_sec: ch.end_sec,
    knowledge_points: [...(ch.knowledge_points || [])],
    _kpStr: (ch.knowledge_points || []).join(', '),
    _instruction: '',
  })
  ch.end_sec = mid
}

function mergeWithNext(idx) {
  if (idx >= editableChapters.value.length - 1) return
  const cur = editableChapters.value[idx]
  const nxt = editableChapters.value[idx + 1]
  syncKpArray(cur)
  syncKpArray(nxt)
  cur.title = `${cur.title} 与 ${nxt.title}`
  cur.summary = `${cur.summary} ${nxt.summary}`.trim()
  cur.end_sec = nxt.end_sec
  cur.knowledge_points = Array.from(new Set([...(cur.knowledge_points || []), ...(nxt.knowledge_points || [])]))
  cur._kpStr = cur.knowledge_points.join(', ')
  editableChapters.value.splice(idx + 1, 1)
}

async function regenerateSingleChapter(idx) {
  const ch = editableChapters.value[idx]
  if (!selectedVideoId.value || regeneratingIdx.value !== -1) return
  regeneratingIdx.value = idx
  try {
    const res = await teacherApi.regenerateChapter(selectedVideoId.value, {
      start_sec: Number(ch.start_sec || 0),
      end_sec: Number(ch.end_sec || 60),
      current_title: ch.title || '',
      instruction: (ch._instruction || '').trim(),
    })
    const generated = res?.chapter || res || {}
    if (generated.title) ch.title = generated.title
    if (generated.summary !== undefined) ch.summary = generated.summary
    if (Array.isArray(generated.knowledge_points)) {
      ch.knowledge_points = generated.knowledge_points
      ch._kpStr = generated.knowledge_points.join(', ')
    }
    emit('notify', `第 ${idx + 1} 节已按最新时间段智能重新生成提要，确认无误后请保存版本。`)
  } catch (err) {
    emit('notify', `单章节重生成失败：${err.message}`)
  } finally {
    regeneratingIdx.value = -1
  }
}

async function saveCalibratedVersion() {
  if (!selectedVideoId.value || !editableChapters.value.length || savingVersion.value) return
  savingVersion.value = true
  try {
    const normalized = editableChapters.value.map((ch, idx) => {
      syncKpArray(ch)
      return {
        chapter_id: ch.chapter_id || '',
        order_index: idx + 1,
        title: ch.title,
        summary: ch.summary,
        start_sec: Number(ch.start_sec || 0),
        end_sec: Number(ch.end_sec || 60),
        knowledge_points: ch.knowledge_points,
      }
    })
    const ver = await teacherApi.saveOutlineVersion(selectedVideoId.value, {
      chapters: normalized,
      change_note: changeNote.value,
    })
    emit('notify', `已保存人工校准快照（第 ${ver.version_no} 版）`)
    await selectVideo(selectedVideoId.value)
  } finally {
    savingVersion.value = false
  }
}

async function publishVersion(versionId) {
  const res = await teacherApi.publishOutlineVersion(selectedVideoId.value, versionId)
  emit('notify', `提纲第 ${res.version_no} 版已正式发布，学生端现可阅读与跳转。`)
  emit('refresh-courses')
  await selectVideo(selectedVideoId.value)
}

async function submitOutlineQualityFeedback() {
  if (!props.course?.id || submittingFeedback.value) return
  submittingFeedback.value = true
  try {
    const note = feedbackReason.value.trim() || '章节切分边界或摘要不够准确'
    await teacherApi.submitQualityFeedback(props.course.id, {
      sample_type: 'outline',
      video_id: selectedVideoId.value || null,
      input_text: `视频提纲校准反馈：${videoMeta.value?.title || selectedVideoId.value}`,
      model_output: JSON.stringify(editableChapters.value.slice(0, 3).map((c) => ({ title: c.title, summary: c.summary }))),
      reason: note,
      note,
      admin_note: note,
    })
    feedbackReason.value = ''
    showFeedbackBar.value = false
    emit('notify', '章节提纲质量问题已上报至管理端评测样本池。')
  } catch (err) {
    emit('notify', `上报失败：${err.message}`)
  } finally {
    submittingFeedback.value = false
  }
}

function statusLabel(status) {
  return formatStatus(status)
}

function formatChangeNote(note) {
  return String(note || '')
    .replace(/ai_pipeline/gi, '智能流水线')
    .replace(/local_asr_mock|local/gi, '本地语音引擎')
    .replace(/\bAI\b/g, '智能')
}
</script>

<template>
  <div>
    <!-- 1. 授课视频讲次列表 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">选择授课视频</h2>
        <span class="label-text">共 {{ course.videos?.length || 0 }} 讲</span>
      </div>

      <div v-if="course.videos?.length" class="block-list">
        <div
          v-for="v in course.videos"
          :key="v.id"
          class="block-row vid-row"
          :class="{ 'is-selected': selectedVideoId === v.id }"
          tabindex="0"
          @click="selectVideo(v.id)"
          @keydown.enter="selectVideo(v.id)"
        >
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body vid-line">
            <div>
              <span class="vid-title">{{ v.title }}</span>
              <span class="label-text" style="margin-left: 8px;">{{ formatSeconds(v.duration_sec) }}</span>
            </div>
            <span class="badge" :class="{ 'badge-danger': v.outline_status === 'failed' }">
              {{ statusLabel(v.outline_status) }}
            </span>
          </div>
        </div>
      </div>

      <p v-else class="muted-text">当前课程暂无视频讲次，请在「课程 · 班级 · 资料上传」上传授课视频。</p>

      <!-- 异步章节切分实时百分比进度条 -->
      <div v-if="isProcessing" class="callout" style="margin-top: 16px; align-items: center;">
        <span class="callout-icon" aria-hidden="true">…</span>
        <div class="block-body">
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px;">
            <strong>正在执行语音转写与章节切分</strong>
            <span class="badge badge-link">{{ jobProgress }}%</span>
          </div>
          <div class="ink-progress" style="margin: 8px 0 6px;">
            <div class="ink-progress-fill" :style="{ width: `${Math.max(5, jobProgress)}%` }"></div>
          </div>
          <div class="muted-text" style="font-size: 12px;">
            后台每 3 秒自动同步任务进度，完成后章节草稿将自动载入下方编辑器。
          </div>
        </div>
        <button type="button" class="btn btn-sm btn-outline" :disabled="syncingJob" @click="pollVideoJob">
          {{ syncingJob ? '同步中...' : '刷新状态' }}
        </button>
      </div>
    </section>

    <!-- 2. 章节提纲块编辑器（拖拽排序 + 单章节重生成 + 增删节 + 质量问题上报） -->
    <section class="doc-section" v-if="selectedVideoId">
      <div class="section-header">
        <div>
          <h2 class="section-title">章节边界与提要编辑</h2>
          <div class="label-text">悬停章节块左侧拖拽手柄可排序；支持按章智能重新生成、拆合与增删</div>
        </div>
        <div class="btn-row">
          <button type="button" class="btn btn-outline btn-sm" @click="addEmptyChapter">+ 新增空章节</button>
          <button type="button" class="btn btn-sm btn-danger" @click="showFeedbackBar = !showFeedbackBar">
            上报智能提纲质量问题
          </button>
          <button
            type="button"
            class="btn btn-primary btn-sm"
            :disabled="!editableChapters.length || savingVersion"
            @click="saveCalibratedVersion"
          >
            {{ savingVersion ? '保存中...' : '保存为新校准版本' }}
          </button>
        </div>
      </div>

      <!-- 上报智能提纲质量问题折叠条 -->
      <div v-if="showFeedbackBar" class="card feedback-bar" style="margin-bottom: 16px;">
        <input
          v-model="feedbackReason"
          class="input"
          style="flex: 1;"
          placeholder="描述智能生成提纲存在的质量问题（如：时间戳切分过碎、专业术语识别有误）..."
          @keyup.enter="submitOutlineQualityFeedback"
        />
        <button type="button" class="btn btn-sm" @click="showFeedbackBar = false">取消</button>
        <button type="button" class="btn btn-primary btn-sm" :disabled="submittingFeedback" @click="submitOutlineQualityFeedback">
          {{ submittingFeedback ? '上报中...' : '提交至质量样本池' }}
        </button>
      </div>

      <input
        v-model="changeNote"
        class="input"
        style="margin-bottom: 16px;"
        placeholder="填写本次人工校准说明..."
      />

      <div v-if="editableChapters.length" class="chapter-editor-list">
        <div
          v-for="(ch, idx) in editableChapters"
          :key="idx"
          class="block-row editor-block"
          draggable="true"
          @dragstart="onDragStart(idx)"
          @dragover.prevent
          @drop="onDropChapter(idx)"
        >
          <span class="block-handle" title="拖拽调整章节顺序" aria-label="拖拽调整顺序">⋮⋮</span>
          <div class="block-body editor-fields">
            <div class="ch-input-line">
              <input v-model="ch.title" class="input title-input" placeholder="章节标题" />
              <div class="sec-pair">
                <label class="label-text">起</label>
                <input v-model.number="ch.start_sec" type="number" class="input sec-input" title="起始秒" />
                <label class="label-text">止</label>
                <input v-model.number="ch.end_sec" type="number" class="input sec-input" title="结束秒" />
              </div>
            </div>

            <textarea v-model="ch.summary" class="textarea" rows="2" placeholder="章节内容提要..."></textarea>

            <!-- 知识点标签编辑行 -->
            <div class="kp-edit-row">
              <span class="label-text" style="flex-shrink: 0;">知识点标签：</span>
              <input
                v-model="ch._kpStr"
                class="inline-input kp-line-input"
                placeholder="输入知识点（逗号分隔）..."
                @blur="syncKpArray(ch)"
              />
            </div>

            <!-- 单章节智能重新生成指令与工具栏 -->
            <div class="ch-action-line">
              <div class="regen-box">
                <input
                  v-model="ch._instruction"
                  class="inline-input regen-input"
                  placeholder="可选重生成提示（如：侧重数学推导）..."
                />
                <button
                  type="button"
                  class="btn btn-outline btn-sm"
                  :disabled="regeneratingIdx !== -1"
                  @click="regenerateSingleChapter(idx)"
                >
                  {{ regeneratingIdx === idx ? '智能重生成中...' : '智能重新生成本章' }}
                </button>
              </div>

              <div class="btn-row">
                <span class="label-text" style="align-self: center; margin-right: 4px;">
                  {{ formatSeconds(ch.start_sec) }}–{{ formatSeconds(ch.end_sec) }}
                </span>
                <button type="button" class="btn btn-sm" @click="splitChapter(idx)">拆分</button>
                <button
                  v-if="idx < editableChapters.length - 1"
                  type="button"
                  class="btn btn-sm"
                  @click="mergeWithNext(idx)"
                >
                  向下合并
                </button>
                <button type="button" class="btn btn-sm btn-danger" @click="deleteChapter(idx)">删除</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <p v-else-if="!isProcessing" class="muted-text">
        当前讲次暂无章节内容，可点击右上角「+ 新增空章节」手工创建。
      </p>
    </section>

    <!-- 3. 历史版本留痕与审核发布 -->
    <section class="doc-section" v-if="outlineVersions.length">
      <div class="section-header">
        <h2 class="section-title">提纲版本历史</h2>
        <span class="label-text">支持载入任意历史快照或一键切换线上发布版</span>
      </div>

      <div class="block-list">
        <div v-for="ver in outlineVersions" :key="ver.id" class="block-row ver-row">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body ver-body">
            <div>
              <strong>第 {{ ver.version_no }} 版</strong>
              <span class="badge" style="margin-left: 8px;">
                {{ ver.is_published ? '线上发布版' : formatSourceType(ver.source) }}
              </span>
              <div class="muted-text" style="font-size: 13px;">{{ formatChangeNote(ver.change_note) }}</div>
            </div>
            <div class="btn-row">
              <button type="button" class="btn btn-sm" @click="loadVersionIntoEditor(ver)">载入编辑</button>
              <button
                v-if="!ver.is_published"
                type="button"
                class="btn btn-primary btn-sm"
                @click="publishVersion(ver.id)"
              >
                发布此版本
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.vid-row {
  cursor: pointer;
}

.vid-line,
.ver-body {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.vid-title {
  font-weight: var(--font-weight-medium);
}

.ink-progress {
  height: 6px;
  background-color: var(--surface-white-page);
  border-radius: var(--radius-cards);
  overflow: hidden;
}

.ink-progress-fill {
  height: 100%;
  background-color: var(--color-ink);
  transition: width 250ms ease;
}

.feedback-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  background-color: var(--surface-white-page);
}

.chapter-editor-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.editor-block {
  padding: var(--spacing-12) var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
}

.editor-fields {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.ch-input-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-12);
  flex-wrap: wrap;
}

.title-input {
  flex: 1;
  min-width: 200px;
  font-weight: var(--font-weight-medium);
}

.sec-pair {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
}

.sec-input {
  width: 72px;
  font-family: var(--font-mono);
}

.kp-edit-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  border-bottom: 1px dashed var(--color-hairline);
}

.kp-line-input {
  flex: 1;
  font-size: 12.5px;
}

.ch-action-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.regen-box {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.regen-input {
  width: 210px;
  font-size: 12px;
  border-bottom: 1px solid var(--color-hairline);
}

.btn-row {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.ver-row {
  padding-top: var(--spacing-8);
  padding-bottom: var(--spacing-8);
  border-bottom: 1px solid var(--color-hairline);
}
</style>
