<script setup>
import { ref, computed, onMounted } from 'vue'
import { teacherApi } from '../../api/modules/teacher'
import { formatStatus } from '../../utils/labels'
import OutlineCalibrationPanel from './panels/OutlineCalibrationPanel.vue'
import CourseResourcesPanel from './panels/CourseResourcesPanel.vue'
import QuestionBankPanel from './panels/QuestionBankPanel.vue'
import TeacherOpsAnalyticsPanel from './panels/TeacherOpsAnalyticsPanel.vue'

const activeTab = ref('outline') // outline | resources | questions | operations
const courses = ref([])
const loading = ref(true)
const selectedCourseId = ref('')
const statusMsg = ref('')

const currentCourse = computed(() => courses.value.find((c) => c.id === selectedCourseId.value) || null)

function toast(msg) {
  statusMsg.value = msg
  setTimeout(() => {
    if (statusMsg.value === msg) statusMsg.value = ''
  }, 3400)
}

async function loadCourses() {
  loading.value = true
  try {
    const res = await teacherApi.fetchCourses()
    courses.value = res.items || []
    if (selectedCourseId.value && !courses.value.some((c) => c.id === selectedCourseId.value)) {
      selectedCourseId.value = courses.value[0]?.id || ''
    } else if (!selectedCourseId.value && courses.value.length) {
      selectedCourseId.value = courses.value[0].id
    }
    if (!courses.value.length) {
      activeTab.value = 'resources'
    }
  } finally {
    loading.value = false
  }
}

onMounted(loadCourses)
</script>

<template>
  <article class="doc-page">
    <header class="doc-header">
      <div class="section-label">教师智能教学工作台</div>
      <h1 class="doc-display" style="margin-top: 6px;">教师教学工作台</h1>
      <p class="doc-subtitle">
        智能引擎负责视频语音转写、章节草稿与候选题目生成；授课教师在工作台中完成校准、审题与版本发布。
      </p>

      <div class="course-picker-bar" v-if="courses.length">
        <label class="label-text" for="studio-course-select">当前工作课程</label>
        <select id="studio-course-select" v-model="selectedCourseId" class="select course-select">
          <option v-for="c in courses" :key="c.id" :value="c.id">
            {{ c.title }}（{{ formatStatus(c.status) }}）
          </option>
        </select>
        <span v-if="currentCourse" class="label-text">
          存储空间：{{ currentCourse.storage_used_mb || 0 }} / {{ currentCourse.storage_quota_mb || 2048 }} 兆
        </span>
      </div>
    </header>

    <div v-if="statusMsg" class="callout" style="margin-bottom: 24px;" role="status">
      <span class="callout-icon" aria-hidden="true">✓</span>
      <span>{{ statusMsg }}</span>
    </div>

    <div class="tabs-bar">
      <button type="button" class="tab-pill" :class="{ active: activeTab === 'outline' }" @click="activeTab = 'outline'">
        提纲校准与版本
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeTab === 'resources' }" @click="activeTab = 'resources'">
        课程 · 班级 · 资料上传
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeTab === 'questions' }" @click="activeTab = 'questions'">
        命题审题与组卷
      </button>
      <button type="button" class="tab-pill" :class="{ active: activeTab === 'operations' }" @click="activeTab = 'operations'">
        收件箱 · 精选问答 · 学情
        <span v-if="currentCourse?.pending_unanswered_count > 0" class="badge badge-danger" style="margin-left: 4px;">
          {{ currentCourse.pending_unanswered_count }}
        </span>
      </button>
    </div>

    <p v-if="loading" class="muted-text">正在读取授课课程列表...</p>

    <template v-else>
      <OutlineCalibrationPanel
        v-if="activeTab === 'outline' && currentCourse"
        :course="currentCourse"
        @notify="toast"
        @refresh-courses="loadCourses"
      />

      <p v-else-if="activeTab === 'outline' && !currentCourse" class="muted-text">
        尚未创建任何课程。请先切换至「课程 · 班级 · 资料上传」创建您的第一门课程。
      </p>

      <CourseResourcesPanel
        v-else-if="activeTab === 'resources'"
        :course="currentCourse"
        @notify="toast"
        @refresh-courses="loadCourses"
        @switch-outline="activeTab = 'outline'"
      />

      <QuestionBankPanel
        v-else-if="activeTab === 'questions' && selectedCourseId"
        :course-id="selectedCourseId"
        :course="currentCourse"
        @notify="toast"
      />

      <TeacherOpsAnalyticsPanel
        v-else-if="activeTab === 'operations' && selectedCourseId"
        :course-id="selectedCourseId"
        @notify="toast"
      />

      <p v-else class="muted-text">请先在「课程 · 班级 · 资料上传」中创建或选择课程。</p>
    </template>
  </article>
</template>

<style scoped>
.course-picker-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-12);
  flex-wrap: wrap;
  margin-top: var(--spacing-16);
}

.course-select {
  width: auto;
  min-width: 260px;
  max-width: 100%;
}
</style>
