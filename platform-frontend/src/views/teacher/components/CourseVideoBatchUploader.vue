<script setup>
import { ref, computed } from 'vue'
import { teacherApi } from '../../../api/modules/teacher'

const props = defineProps({
  course: { type: Object, required: true },
})
const emit = defineEmits(['notify', 'error', 'refresh-courses', 'switch-outline'])

const CHUNK_SIZE_BYTES = 2 * 1024 * 1024
const AUTO_CHUNK_THRESHOLD_BYTES = 5 * 1024 * 1024

const videoQueue = ref([])
const selectedSubtitleFile = ref(null)
const batchKnowledgePointsStr = ref('')
const forceChunkMode = ref(false)
const uploadingBatch = ref(false)

const batchOverallPct = computed(() => {
  if (!videoQueue.value.length) return 0
  const total = videoQueue.value.reduce((acc, item) => acc + (item.progress || 0), 0)
  return Math.round(total / videoQueue.value.length)
})

function onPickVideos(e) {
  const files = Array.from(e.target.files || [])
  for (const f of files) {
    videoQueue.value.push({
      uid: `${f.name}_${f.size}_${f.lastModified}_${Math.random().toString(36).slice(2, 6)}`,
      file: f,
      title: f.name.replace(/\.[^.]+$/, ''),
      sizeMb: (f.size / 1048576).toFixed(2),
      progress: 0,
      status: 'pending',
      detailText: '',
    })
  }
  e.target.value = ''
}

function removeQueueItem(idx) {
  if (!uploadingBatch.value) videoQueue.value.splice(idx, 1)
}

function onPickSubtitle(e) {
  selectedSubtitleFile.value = e.target.files?.[0] || null
}

async function uploadSingleItem(item, kps) {
  const file = item.file
  const useChunked = forceChunkMode.value || file.size > AUTO_CHUNK_THRESHOLD_BYTES
  item.status = 'uploading'
  item.progress = 0
  item.detailText = ''

  if (!useChunked) {
    const fd = new FormData()
    fd.append('video_file', file)
    if (selectedSubtitleFile.value && videoQueue.value.length === 1) {
      fd.append('subtitle_file', selectedSubtitleFile.value)
    }
    fd.append('title', item.title.trim())
    fd.append('knowledge_points', kps.join(','))
    item.detailText = '整包直传中...'
    await teacherApi.uploadVideoFile(props.course.id, fd, (pct) => {
      item.progress = pct
    })
    item.progress = 100
    item.status = 'done'
    item.detailText = '上传完成，已触发章节切分'
    return
  }

  const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_SIZE_BYTES))
  const resumeCacheKey = `slp_chunk_${props.course.id}_${file.name}_${file.size}_${file.lastModified}`
  const cachedUploadId = sessionStorage.getItem(resumeCacheKey) || undefined

  const initRes = await teacherApi.initVideoChunkUpload(props.course.id, {
    upload_id: cachedUploadId,
    filename: file.name,
    file_size: file.size,
    total_size: file.size,
    chunk_size: CHUNK_SIZE_BYTES,
    total_chunks: totalChunks,
    title: item.title.trim(),
    knowledge_points: kps,
  })

  const uploadId = initRes.upload_id || initRes.id
  if (uploadId) sessionStorage.setItem(resumeCacheKey, uploadId)
  const uploadedSet = new Set((initRes.uploaded_chunks || []).map((n) => Number(n)))
  let skippedCount = 0

  for (let idx = 0; idx < totalChunks; idx += 1) {
    if (uploadedSet.has(idx)) {
      skippedCount += 1
      item.progress = Math.round(((idx + 1) / totalChunks) * 95)
      item.detailText = `断点续传跳过已传分片 ${idx + 1}/${totalChunks}`
      continue
    }
    const start = idx * CHUNK_SIZE_BYTES
    const blob = file.slice(start, Math.min(file.size, start + CHUNK_SIZE_BYTES))
    await teacherApi.uploadVideoChunk(props.course.id, uploadId, idx, blob, (chunkPct) => {
      const ratio = (idx + chunkPct / 100) / totalChunks
      item.progress = Math.min(96, Math.round(ratio * 96))
      item.detailText = `分片 ${idx + 1}/${totalChunks} (${chunkPct}%)${skippedCount ? ` · 已续传跳过 ${skippedCount} 片` : ''}`
    })
  }

  item.detailText = '正在合并分片并启动智能语音转写...'
  await teacherApi.completeVideoChunkUpload(props.course.id, uploadId, {
    title: item.title.trim(),
    knowledge_points: kps,
  })
  sessionStorage.removeItem(resumeCacheKey)
  item.progress = 100
  item.status = 'done'
  item.detailText = `分片合并完成（共 ${totalChunks} 片${skippedCount ? `，续传跳过 ${skippedCount} 片` : ''}）`
}

async function handleVideoBatchSubmit() {
  const pendingItems = videoQueue.value.filter((i) => i.status !== 'done')
  if (!pendingItems.length) {
    emit('error', '请先选择一个或多个要上传的视频/音频文件')
    return
  }

  emit('error', '')
  uploadingBatch.value = true
  const kps = batchKnowledgePointsStr.value
    .split(/[,，]/)
    .map((s) => s.trim())
    .filter(Boolean)

  let okCount = 0
  try {
    for (const item of pendingItems) {
      try {
        await uploadSingleItem(item, kps)
        okCount += 1
      } catch (err) {
        item.status = 'failed'
        item.detailText = `中断（可再次点击继续断点续传）：${err.message}`
        emit('error', `视频《${item.title}》上传中断：${err.message}`)
      }
    }
    if (okCount > 0) {
      selectedSubtitleFile.value = null
      videoQueue.value = videoQueue.value.filter((i) => i.status !== 'done')
      emit('notify', `成功上传 ${okCount} 个授课视频并启动后台智能章节切分任务。`)
      emit('refresh-courses')
      if (!videoQueue.value.length) emit('switch-outline')
    }
  } finally {
    uploadingBatch.value = false
  }
}
</script>

<template>
  <section class="doc-section">
    <div class="section-header">
      <div>
        <h2 class="section-title">批量上传授课视频（支持分片断点续传）</h2>
        <div class="label-text">支持多选队列上传；大于 5 兆的视频自动按 2 兆分片续传，断网重试自动跳过已传分片</div>
      </div>
    </div>

    <div class="card form-col">
      <div class="file-row">
        <label class="label-text">选择授课视频或音频（支持高清视频与音频文件，可多选）</label>
        <input type="file" multiple accept="video/*,audio/*,.mp4,.webm,.mov,.mkv,.mp3,.wav" @change="onPickVideos" />
      </div>

      <div class="grid-2">
        <div class="file-row">
          <label class="label-text">可选外挂字幕（.srt / .vtt，单视频上传时绑定）</label>
          <input type="file" accept=".srt,.vtt" @change="onPickSubtitle" />
        </div>
        <div class="field" style="justify-content: center;">
          <label class="label-text">本批知识点标签（逗号分隔）</label>
          <input v-model="batchKnowledgePointsStr" class="input" placeholder="例如：注意力机制、反向传播" />
        </div>
      </div>

      <label class="chk-inline">
        <input type="checkbox" v-model="forceChunkMode" />
        <span>对所有大小视频强制启用 2 兆分片断点续传协议（默认大于 5 兆自动启用）</span>
      </label>

      <div v-if="videoQueue.length" class="queue-box">
        <div class="section-header" style="margin-bottom: 8px;">
          <span class="label-text">待上传视频队列 ({{ videoQueue.length }} 个文件)</span>
          <span v-if="uploadingBatch" class="badge badge-link">队列总进度 {{ batchOverallPct }}%</span>
        </div>

        <div v-for="(item, idx) in videoQueue" :key="item.uid" class="queue-item">
          <div class="queue-item-head">
            <input v-model="item.title" class="inline-input queue-title-input" :disabled="uploadingBatch" />
            <div class="queue-badges">
              <span class="badge">{{ item.sizeMb }} 兆</span>
              <span class="badge">
                {{ forceChunkMode || item.file.size > AUTO_CHUNK_THRESHOLD_BYTES ? '2 兆分片续传' : '整包直传' }}
              </span>
              <button v-if="!uploadingBatch" type="button" class="btn btn-sm btn-danger" @click="removeQueueItem(idx)">
                移除
              </button>
            </div>
          </div>
          <div class="progress-line" style="margin-top: 6px;">
            <div class="ink-progress">
              <div class="ink-progress-fill" :style="{ width: `${item.progress}%` }"></div>
            </div>
            <span class="label-text">{{ item.progress }}%</span>
          </div>
          <div v-if="item.detailText" class="label-text" style="margin-top: 2px;">{{ item.detailText }}</div>
        </div>
      </div>

      <div>
        <button type="button" class="btn btn-primary" :disabled="uploadingBatch || !videoQueue.length" @click="handleVideoBatchSubmit">
          {{ uploadingBatch ? `正在批量上传 (${batchOverallPct}%)...` : `启动队列上传并触发章节切分 (${videoQueue.length})` }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.form-col { display: flex; flex-direction: column; gap: var(--spacing-12); }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--spacing-12); }
@media (max-width: 640px) { .grid-2 { grid-template-columns: 1fr; } }
.field { display: flex; flex-direction: column; gap: var(--spacing-4); }
.file-row { display: flex; flex-direction: column; gap: var(--spacing-4); padding: var(--spacing-8) var(--spacing-12); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--surface-white-page); }
.chk-inline { display: inline-flex; align-items: center; gap: var(--spacing-8); font-size: 13px; color: var(--color-ink-secondary); cursor: pointer; }
.queue-box { padding: var(--spacing-12); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--surface-white-page); display: flex; flex-direction: column; gap: var(--spacing-8); }
.queue-item { padding: var(--spacing-8); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--color-paper); }
.queue-item-head { display: flex; justify-content: space-between; align-items: center; gap: var(--spacing-8); flex-wrap: wrap; }
.queue-title-input { flex: 1; min-width: 160px; font-weight: var(--font-weight-medium); border-bottom: 1px solid var(--color-hairline); }
.queue-badges { display: flex; align-items: center; gap: var(--spacing-4); }
.progress-line { display: flex; align-items: center; gap: var(--spacing-12); }
.ink-progress { flex: 1; height: 6px; background-color: var(--color-hover-grey); border-radius: var(--radius-cards); overflow: hidden; }
.ink-progress-fill { height: 100%; background-color: var(--color-ink); transition: width 150ms ease; }
</style>
