<script setup>
import { ref, watch, onMounted } from 'vue'
import { commonApi } from '../../../api/modules/common'
import { studentApi } from '../../../api/modules/student'
import { teacherApi } from '../../../api/modules/teacher'
import { formatStatus } from '../../../utils/labels'
import CourseRosterSection from '../components/CourseRosterSection.vue'
import CourseVideoBatchUploader from '../components/CourseVideoBatchUploader.vue'

const props = defineProps({
  course: { type: Object, default: null },
})
const emit = defineEmits(['notify', 'refresh-courses', 'switch-outline'])

const errorTip = ref('')
const categoryOptions = ref(['人工智能', '计算机科学', '数据科学', '数学基础', '通识课程'])

const editCourseForm = ref({
  title: '',
  subtitle: '',
  category: '人工智能',
  description: '',
  start_date: '',
  end_date: '',
  cover_url: '',
})
const savingCourse = ref(false)
const uploadingCover = ref(false)
const coverProgress = ref(0)

const courseDocuments = ref([])
const docMode = ref('file')
const selectedDocFile = ref(null)
const uploadingDoc = ref(false)
const docProgress = ref(0)
const docForm = ref({ title: '', file_format: 'markdown', content_text: '' })

const newCourse = ref({ title: '', subtitle: '', category: '人工智能', description: '', submit_for_review: true })
const creatingCourse = ref(false)

function formatFileFormat(fmt) {
  const map = { pdf: '便携文档', docx: '办公文档', doc: '办公文档', pptx: '演示课件', ppt: '演示课件', markdown: '结构化讲义', md: '结构化讲义', csv: '电子表格', xlsx: '电子表格' }
  return map[String(fmt || '').toLowerCase()] || '纯文本讲义'
}

async function loadCategories() {
  try {
    const res = await commonApi.fetchCategories()
    const items = res?.items || (Array.isArray(res) ? res : [])
    const names = items.map((c) => (typeof c === 'string' ? c : (c.name || c.title))).filter(Boolean)
    if (names.length) categoryOptions.value = Array.from(new Set([...names, ...categoryOptions.value]))
  } catch {
    // 保留默认分类
  }
}

async function syncSelectedCourseDetails() {
  if (!props.course?.id) {
    courseDocuments.value = []
    return
  }
  editCourseForm.value = {
    title: props.course.title || '',
    subtitle: props.course.subtitle || '',
    category: props.course.category || '人工智能',
    description: props.course.description || '',
    start_date: props.course.start_date ? String(props.course.start_date).slice(0, 10) : '',
    end_date: props.course.end_date ? String(props.course.end_date).slice(0, 10) : '',
    cover_url: props.course.cover_url || '',
  }
  if (Array.isArray(props.course.documents)) courseDocuments.value = props.course.documents

  try {
    const detail = await studentApi.fetchCourseDetail(props.course.id)
    if (detail) {
      editCourseForm.value.description = detail.description ?? editCourseForm.value.description
      editCourseForm.value.cover_url = detail.cover_url || editCourseForm.value.cover_url
      if (detail.start_date) editCourseForm.value.start_date = String(detail.start_date).slice(0, 10)
      if (detail.end_date) editCourseForm.value.end_date = String(detail.end_date).slice(0, 10)
      courseDocuments.value = detail.documents || courseDocuments.value
    }
  } catch {
    // 草稿态回退列表自带字段
  }
}

watch(() => props.course?.id, syncSelectedCourseDetails, { immediate: true })
onMounted(loadCategories)

async function saveCourseMetadata(overrideStatus = null) {
  if (!props.course?.id || savingCourse.value) return
  errorTip.value = ''
  savingCourse.value = true
  try {
    const payload = {
      title: editCourseForm.value.title.trim(),
      subtitle: editCourseForm.value.subtitle.trim(),
      category: editCourseForm.value.category.trim(),
      description: editCourseForm.value.description,
      start_date: editCourseForm.value.start_date || null,
      end_date: editCourseForm.value.end_date || null,
    }
    if (overrideStatus) payload.status = overrideStatus
    await teacherApi.updateCourse(props.course.id, payload)
    emit('notify', overrideStatus ? `课程状态已更新为「${formatStatus(payload.status)}」。` : '课程基础信息已保存。')
    emit('refresh-courses')
  } catch (err) {
    errorTip.value = err.message || '保存课程信息失败'
  } finally {
    savingCourse.value = false
  }
}

async function onPickCover(e) {
  const file = e.target.files?.[0]
  if (!file || !props.course?.id) return
  errorTip.value = ''
  uploadingCover.value = true
  coverProgress.value = 0
  try {
    const res = await teacherApi.uploadCourseCover(props.course.id, file, (pct) => { coverProgress.value = pct })
    if (res?.cover_url) editCourseForm.value.cover_url = res.cover_url
    emit('notify', '课程封面图已上传更新。')
    emit('refresh-courses')
  } catch (err) {
    errorTip.value = err.message || '课程封面上传失败'
  } finally {
    uploadingCover.value = false
    coverProgress.value = 0
    e.target.value = ''
  }
}

function onPickDoc(e) {
  const f = e.target.files?.[0] || null
  selectedDocFile.value = f
  if (f && !docForm.value.title.trim()) docForm.value.title = f.name
}

async function handleDocSubmit() {
  if (!props.course) {
    errorTip.value = '请先创建或选择一门课程后再上传讲义资料'
    return
  }
  errorTip.value = ''
  uploadingDoc.value = true
  docProgress.value = 0
  try {
    if (docMode.value === 'file') {
      if (!selectedDocFile.value) {
        errorTip.value = '请先选择要上传的讲义文件'
        return
      }
      const fd = new FormData()
      fd.append('document_file', selectedDocFile.value)
      fd.append('title', docForm.value.title.trim())
      const res = await teacherApi.uploadDocumentFile(props.course.id, fd, (pct) => { docProgress.value = pct })
      selectedDocFile.value = null
      docForm.value = { title: '', file_format: 'markdown', content_text: '' }
      emit('notify', `讲义《${res.title}》解析入库完成（提取 ${res.extracted_chars} 字，${res.chunk_count} 个检索切片）。`)
    } else {
      if (!docForm.value.title.trim() || !docForm.value.content_text.trim()) {
        errorTip.value = '请填写资料标题与正文内容'
        return
      }
      const res = await teacherApi.uploadDocument(props.course.id, docForm.value)
      docForm.value = { title: '', file_format: 'markdown', content_text: '' }
      emit('notify', `讲义《${res.title}》已同步构建课程检索索引（${formatStatus(res.index_status)}）。`)
    }
    await syncSelectedCourseDetails()
    emit('refresh-courses')
  } catch (err) {
    errorTip.value = err.message || '资料解析上传失败'
  } finally {
    uploadingDoc.value = false
    docProgress.value = 0
  }
}

async function handleDeleteDoc(doc) {
  try {
    await teacherApi.deleteDocument(doc.id)
    courseDocuments.value = courseDocuments.value.filter((d) => d.id !== doc.id)
    emit('notify', `讲义《${doc.title}》已删除并同步移除向量检索切片。`)
    emit('refresh-courses')
  } catch (err) {
    errorTip.value = err.message || '删除讲义失败'
  }
}

async function createCourse(submitForReview = true) {
  if (!newCourse.value.title.trim() || creatingCourse.value) return
  creatingCourse.value = true
  errorTip.value = ''
  try {
    const res = await teacherApi.createCourse({ ...newCourse.value, submit_for_review: submitForReview })
    newCourse.value = { title: '', subtitle: '', category: '人工智能', description: '', submit_for_review: true }
    emit('notify', submitForReview ? `课程《${res.title}》已创建并提交审核。` : `课程《${res.title}》已存为草稿。`)
    emit('refresh-courses')
  } catch (err) {
    errorTip.value = err.message || '课程创建失败'
  } finally {
    creatingCourse.value = false
  }
}
</script>

<template>
  <div>
    <div v-if="errorTip" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorTip }}</span>
    </div>

    <!-- 1. 课程信息、封面与状态流转 -->
    <section class="doc-section" v-if="course">
      <div class="section-header">
        <div>
          <h2 class="section-title">课程信息、封面与生命周期管理</h2>
          <div class="label-text">当前状态：{{ formatStatus(course.status) }} · 支持随时修订课程档案、更换封面与流转审核状态</div>
        </div>
      </div>

      <div class="card form-col">
        <div class="grid-2">
          <div class="field">
            <label class="label-text">课程名称</label>
            <input v-model="editCourseForm.title" class="input" placeholder="课程名称" />
          </div>
          <div class="field">
            <label class="label-text">学科分类</label>
            <input v-model="editCourseForm.category" list="teacher-cat-list" class="input" placeholder="选择或输入分类" />
            <datalist id="teacher-cat-list">
              <option v-for="c in categoryOptions" :key="c" :value="c" />
            </datalist>
          </div>
        </div>

        <input v-model="editCourseForm.subtitle" class="input" placeholder="副标题或适用专业年级" />
        <textarea v-model="editCourseForm.description" class="textarea" rows="3" placeholder="课程简介与教学大纲说明..."></textarea>

        <div class="grid-2">
          <div class="field">
            <label class="label-text">开课日期</label>
            <input v-model="editCourseForm.start_date" type="date" class="input" />
          </div>
          <div class="field">
            <label class="label-text">结课日期</label>
            <input v-model="editCourseForm.end_date" type="date" class="input" />
          </div>
        </div>

        <div class="file-row cover-row">
          <div style="flex: 1;">
            <div class="label-text">课程封面图片（推荐十六比九横版图片）</div>
            <input type="file" accept="image/*" style="margin-top: 4px;" @change="onPickCover" />
            <div v-if="uploadingCover" class="label-text" style="margin-top: 4px;">封面上传中 {{ coverProgress }}%...</div>
          </div>
          <img v-if="editCourseForm.cover_url" :src="editCourseForm.cover_url" alt="课程封面预览" class="cover-preview-img" />
        </div>

        <div class="lifecycle-bar">
          <button type="button" class="btn btn-primary btn-sm" :disabled="savingCourse" @click="saveCourseMetadata(null)">保存课程信息</button>
          <button type="button" class="btn btn-outline btn-sm" :disabled="savingCourse" @click="saveCourseMetadata('draft')">存为草稿</button>
          <button type="button" class="btn btn-outline btn-sm" :disabled="savingCourse" @click="saveCourseMetadata('pending_review')">重新提交审核</button>
          <button type="button" class="btn btn-outline btn-sm" :disabled="savingCourse" @click="saveCourseMetadata('closed')">标记结课</button>
          <button type="button" class="btn btn-sm btn-danger" :disabled="savingCourse" @click="saveCourseMetadata('archived')">归档课程</button>
        </div>
      </div>
    </section>

    <!-- 2. 批量授课视频与分片断点续传 -->
    <CourseVideoBatchUploader
      v-if="course"
      :course="course"
      @notify="(msg) => emit('notify', msg)"
      @error="(msg) => (errorTip = msg)"
      @refresh-courses="emit('refresh-courses')"
      @switch-outline="emit('switch-outline')"
    />

    <!-- 3. 课程讲义上传与索引管理 -->
    <section class="doc-section" v-if="course">
      <div class="section-header">
        <div>
          <h2 class="section-title">课程讲义上传与索引管理</h2>
          <div class="label-text">自动提取正文并构建本课专属检索切片</div>
        </div>
        <div class="mode-switch">
          <button type="button" class="btn btn-sm" :class="{ 'is-mode': docMode === 'file' }" @click="docMode = 'file'">文件上传</button>
          <button type="button" class="btn btn-sm" :class="{ 'is-mode': docMode === 'text' }" @click="docMode = 'text'">在线撰写</button>
        </div>
      </div>

      <div class="card form-col">
        <template v-if="docMode === 'file'">
          <div class="file-row">
            <label class="label-text">支持 PDF、Word、PPTX、Markdown 与纯文本讲义</label>
            <input type="file" accept=".pdf,.docx,.pptx,.md,.markdown,.txt,.csv" @change="onPickDoc" />
            <div v-if="selectedDocFile" class="muted-text" style="font-size: 12px;">
              已选择：{{ selectedDocFile.name }}（{{ (selectedDocFile.size / 1024).toFixed(1) }} 千字节）
            </div>
          </div>
          <input v-model="docForm.title" class="input" placeholder="讲义显示标题" />
        </template>

        <template v-else>
          <input v-model="docForm.title" class="input" placeholder="讲义标题" />
          <textarea v-model="docForm.content_text" class="textarea" rows="4" placeholder="请输入结构化讲义或纯文本正文内容..."></textarea>
        </template>

        <div v-if="uploadingDoc && docMode === 'file'" class="progress-line">
          <div class="ink-progress"><div class="ink-progress-fill" :style="{ width: `${docProgress}%` }"></div></div>
          <span class="label-text">上传与全文解析 {{ docProgress }}%</span>
        </div>

        <div>
          <button type="button" class="btn btn-primary btn-sm" :disabled="uploadingDoc || !course" @click="handleDocSubmit">
            {{ uploadingDoc ? '解析并构建索引中...' : '保存讲义并更新检索索引' }}
          </button>
        </div>
      </div>

      <div style="margin-top: 16px;">
        <div class="label-text" style="margin-bottom: 8px;">已上传讲义资料 ({{ courseDocuments.length }})</div>
        <div v-if="courseDocuments.length" class="table-scroll">
          <table class="data-table">
            <thead>
              <tr>
                <th>讲义标题</th>
                <th>格式 / 大小</th>
                <th>检索索引状态</th>
                <th>原件与操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="doc in courseDocuments" :key="doc.id">
                <td><strong>{{ doc.title }}</strong></td>
                <td>
                  <span class="badge">{{ formatFileFormat(doc.file_format) }}</span>
                  <span v-if="doc.file_size_mb" class="label-text" style="margin-left: 6px;">{{ doc.file_size_mb }} 兆</span>
                </td>
                <td>
                  <span class="badge" :class="{ 'badge-danger': doc.index_status === 'stale' || doc.index_status === 'removed' }">
                    {{ formatStatus(doc.index_status) }}
                  </span>
                </td>
                <td>
                  <div style="display: flex; gap: 8px; align-items: center;">
                    <a v-if="doc.file_url" :href="doc.file_url" target="_blank" rel="noopener" style="font-size: 12px;">预览/下载</a>
                    <button type="button" class="btn btn-sm btn-danger" @click="handleDeleteDoc(doc)">删除讲义</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="muted-text">当前课程暂未上传讲义资料。</p>
      </div>
    </section>

    <!-- 4. 教学班、选课学生名单与学习小组管理 -->
    <CourseRosterSection
      v-if="course"
      :course="course"
      @notify="(msg) => emit('notify', msg)"
      @error="(msg) => (errorTip = msg)"
      @refresh-courses="emit('refresh-courses')"
    />

    <!-- 5. 开设新课程 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">开设新课程</h2>
          <div class="label-text">支持保存草稿备课或直接提交管理员上架审核</div>
        </div>
      </div>

      <div class="card form-col">
        <input v-model="newCourse.title" class="input" placeholder="新课程名称" />
        <input v-model="newCourse.subtitle" class="input" placeholder="副标题或适用年级专业" />
        <input v-model="newCourse.category" list="teacher-cat-list" class="input" placeholder="学科分类（如：计算机科学）" />
        <textarea v-model="newCourse.description" class="textarea" rows="3" placeholder="课程简介与教学目标..."></textarea>
        <div style="display: flex; gap: 8px; flex-wrap: wrap;">
          <button type="button" class="btn btn-primary" :disabled="creatingCourse || !newCourse.title.trim()" @click="createCourse(true)">
            {{ creatingCourse ? '提交中...' : '创建课程并提交审核' }}
          </button>
          <button type="button" class="btn btn-outline" :disabled="creatingCourse || !newCourse.title.trim()" @click="createCourse(false)">
            创建并存为草稿
          </button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.form-col { display: flex; flex-direction: column; gap: var(--spacing-12); }
.grid-2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--spacing-12); }
@media (max-width: 640px) { .grid-2 { grid-template-columns: 1fr; } }
.field { display: flex; flex-direction: column; gap: var(--spacing-4); }
.file-row { display: flex; flex-direction: column; gap: var(--spacing-4); padding: var(--spacing-8) var(--spacing-12); border: 1px solid var(--color-hairline); border-radius: var(--radius-cards); background-color: var(--surface-white-page); }
.cover-row { flex-direction: row; align-items: center; justify-content: space-between; gap: var(--spacing-12); flex-wrap: wrap; }
.cover-preview-img { width: 112px; height: 68px; object-fit: cover; border-radius: var(--radius-cards); border: 1px solid var(--color-hairline); }
.lifecycle-bar { display: flex; gap: var(--spacing-8); flex-wrap: wrap; padding-top: var(--spacing-4); }
.mode-switch { display: flex; gap: var(--spacing-4); }
.is-mode { background-color: var(--color-hover-grey); font-weight: var(--font-weight-semibold); }
.progress-line { display: flex; align-items: center; gap: var(--spacing-12); }
.ink-progress { flex: 1; height: 6px; background-color: var(--color-hover-grey); border-radius: var(--radius-cards); overflow: hidden; }
.ink-progress-fill { height: 100%; background-color: var(--color-ink); transition: width 150ms ease; }
</style>
