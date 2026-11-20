<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { formatSeconds } from '../../api/client'
import { commonApi } from '../../api/modules/common'
import { studentApi } from '../../api/modules/student'

const router = useRouter()
const dashboard = ref(null)
const courses = ref([])
const categories = ref([])
const selectedCategory = ref('')
const featuredOnly = ref(false)
const keyword = ref('')
const loading = ref(true)
const errorMsg = ref('')
const hoveredCurveIdx = ref(-1)

const displayedCourses = computed(() => {
  if (!featuredOnly.value) return courses.value
  return courses.value.filter((c) => Boolean(c.is_featured))
})

const categoryOptions = computed(() => {
  const names = new Set()
  for (const c of categories.value) {
    const label = typeof c === 'string' ? c : (c?.name || c?.title || '')
    if (label) names.add(label)
  }
  for (const crs of courses.value) {
    if (crs.category) names.add(crs.category)
  }
  return Array.from(names)
})

// 原生 SVG 测验成绩趋势图坐标计算 (640 x 180 画布)
const svgChart = computed(() => {
  const pts = dashboard.value?.quiz_score_curve || []
  const W = 640
  const H = 180
  const padL = 36
  const padR = 20
  const padT = 18
  const padB = 28
  const innerW = W - padL - padR
  const innerH = H - padT - padB

  const yTicks = [0, 25, 50, 75, 100].map((val) => ({
    val,
    y: Math.round((padT + innerH - (val / 100) * innerH) * 10) / 10,
  }))

  const nodes = pts.map((item, i) => {
    const score = Math.max(0, Math.min(100, Number(item.score || 0)))
    const ratioX = pts.length <= 1 ? 0.5 : i / (pts.length - 1)
    const x = Math.round((padL + ratioX * innerW) * 10) / 10
    const y = Math.round((padT + innerH - (score / 100) * innerH) * 10) / 10
    return {
      ...item,
      idx: i,
      score,
      x,
      y,
      shortDate: item.submitted_at ? String(item.submitted_at).slice(5, 10) : `第${i + 1}次`,
    }
  })

  const polyline = nodes.map((n) => `${n.x},${n.y}`).join(' ')
  const areaPolygon =
    nodes.length >= 2
      ? `${nodes[0].x},${padT + innerH} ${polyline} ${nodes[nodes.length - 1].x},${padT + innerH}`
      : ''

  return { W, H, padL, padR, padT, padB, yTicks, nodes, polyline, areaPolygon }
})

const activeCurveNode = computed(() => {
  const nodes = svgChart.value.nodes
  if (!nodes.length) return null
  if (hoveredCurveIdx.value >= 0 && hoveredCurveIdx.value < nodes.length) {
    return nodes[hoveredCurveIdx.value]
  }
  return nodes[nodes.length - 1]
})

async function loadCategories() {
  try {
    const res = await commonApi.fetchCategories()
    categories.value = res?.items || (Array.isArray(res) ? res : [])
  } catch {
    categories.value = []
  }
}

async function loadCoursesOnly() {
  try {
    const courseRes = await studentApi.fetchCourses({
      keyword: keyword.value,
      category: selectedCategory.value,
      featuredOnly: featuredOnly.value,
    })
    courses.value = courseRes.items || []
  } catch (err) {
    errorMsg.value = err.message || '筛选课程目录失败'
  }
}

async function selectCategory(cat) {
  selectedCategory.value = cat
  await loadCoursesOnly()
}

async function loadAll() {
  loading.value = true
  errorMsg.value = ''
  try {
    const [dashRes, courseRes] = await Promise.all([
      studentApi.fetchDashboard(),
      studentApi.fetchCourses({
        keyword: keyword.value,
        category: selectedCategory.value,
        featuredOnly: featuredOnly.value,
      }),
      loadCategories(),
    ])
    dashboard.value = dashRes
    courses.value = courseRes.items || []
  } catch (err) {
    errorMsg.value = err.message || '加载学习中心数据失败'
  } finally {
    loading.value = false
  }
}

async function enrollAndOpen(course) {
  if (!course.is_enrolled) {
    await studentApi.enrollCourse(course.id)
  }
  router.push(`/student/courses/${course.id}`)
}

function openRecommendation(rec) {
  router.push({
    path: `/student/courses/${rec.course_id}`,
    query: {
      videoId: rec.video_id || '',
      chapterId: rec.chapter_id || '',
      seek: rec.start_sec ?? 0,
    },
  })
}

onMounted(loadAll)
</script>

<template>
  <article class="doc-page">
    <header class="doc-header">
      <div class="section-label">个人学习总览</div>
      <h1 class="doc-display" style="margin-top: 6px;">学习中心</h1>
      <p class="doc-subtitle">
        跟踪个人阅读与视频修读进度，查看基于历史薄弱点实时计算的复习建议，并进入已发布的课程知识文档。
      </p>
    </header>

    <div v-if="errorMsg" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorMsg }}</span>
    </div>

    <p v-if="loading" class="muted-text doc-section">正在同步个人学习记录与课程目录...</p>

    <template v-else>
      <!-- 1. 个人学情属性条 -->
      <section v-if="dashboard" class="doc-section">
        <div class="section-header">
          <h2 class="section-title">个人学情摘要</h2>
          <button type="button" class="btn btn-link" @click="router.push('/student/wrong-book')">
            打开错题本（{{ dashboard.unmastered_wrong_count }} 道待掌握）→
          </button>
        </div>

        <div class="prop-grid">
          <div class="prop-cell">
            <div class="label-text">专注学习时长</div>
            <div class="prop-val">{{ dashboard.study_minutes }} <small>分钟</small></div>
          </div>
          <div class="prop-cell">
            <div class="label-text">连续学习</div>
            <div class="prop-val">{{ dashboard.streak_days }} <small>天</small></div>
          </div>
          <div class="prop-cell">
            <div class="label-text">在修课程</div>
            <div class="prop-val">{{ dashboard.enrolled_courses?.length || 0 }} <small>门</small></div>
          </div>
          <div class="prop-cell">
            <div class="label-text">待复习薄弱点</div>
            <div class="prop-val">{{ dashboard.weak_knowledge_points?.length || 0 }} <small>个</small></div>
          </div>
        </div>

        <div v-if="dashboard.weak_knowledge_points?.length" class="weak-strip">
          <span class="label-text">薄弱知识点：</span>
          <span v-for="wp in dashboard.weak_knowledge_points" :key="wp" class="badge">{{ wp }}</span>
        </div>

        <!-- 在修课程修读进度列表 -->
        <div v-if="dashboard.enrolled_courses?.length" class="enrolled-block">
          <div class="label-text" style="margin-bottom: 8px;">正在修读的课程进度</div>
          <div class="enrolled-grid">
            <div v-for="ec in dashboard.enrolled_courses" :key="ec.course_id" class="card enrolled-card">
              <div class="enrolled-head">
                <div>
                  <div class="enrolled-title">{{ ec.title }}</div>
                  <span v-if="ec.category" class="badge" style="margin-top: 4px;">{{ ec.category }}</span>
                </div>
                <button
                  type="button"
                  class="btn btn-primary btn-sm"
                  @click="router.push(`/student/courses/${ec.course_id}`)"
                >
                  继续学习 →
                </button>
              </div>
              <div class="enrolled-progress-row">
                <div class="ink-meter" style="flex: 1;">
                  <div class="ink-meter-fill" :style="{ width: `${Math.min(100, Math.max(0, Number(ec.progress_pct || 0)))}%` }"></div>
                </div>
                <span class="pct-text">{{ Math.round(Number(ec.progress_pct || 0)) }}%</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 2. 个性化学习推荐块列表 -->
      <section v-if="dashboard?.recommendations?.length" class="doc-section">
        <div class="section-header">
          <div>
            <h2 class="section-title">个性化复习建议</h2>
            <div class="label-text">依据错题本薄弱知识点与近期智能助教提问实时排序</div>
          </div>
        </div>

        <div class="block-list">
          <div
            v-for="rec in dashboard.recommendations"
            :key="rec.item_id"
            class="block-row rec-row"
            tabindex="0"
            @click="openRecommendation(rec)"
            @keydown.enter="openRecommendation(rec)"
          >
            <span class="block-handle" aria-hidden="true">⋮⋮</span>
            <div class="block-body">
              <div class="rec-head">
                <span class="rec-title">{{ rec.title }}</span>
                <span class="badge">{{ rec.priority_tag }} · {{ Math.round(rec.score * 100) }}%</span>
              </div>
              <div class="muted-text" style="font-size: 13px;">{{ rec.reason }}</div>
              <span class="jump-link">跳转至 {{ formatSeconds(rec.start_sec) }} 开始阅读 →</span>
            </div>
          </div>
        </div>
      </section>

      <!-- 3. 测验成绩趋势折线图 (原生内联 SVG) + 明细表 -->
      <section v-if="dashboard?.quiz_score_curve?.length" class="doc-section">
        <div class="section-header">
          <div>
            <h2 class="section-title">测验成绩趋势曲线</h2>
            <div class="label-text">悬停圆点查看各场测验得分与时间轨迹</div>
          </div>
          <div v-if="activeCurveNode" class="curve-tooltip">
            <span class="badge badge-link">{{ activeCurveNode.score }} 分</span>
            <span class="curve-tip-title">{{ activeCurveNode.title }}</span>
            <span class="label-text">{{ activeCurveNode.shortDate }}</span>
          </div>
        </div>

        <div class="card surface-white chart-card">
          <svg
            :viewBox="`0 0 ${svgChart.W} ${svgChart.H}`"
            class="score-svg"
            role="img"
            aria-label="近期测验成绩折线趋势图"
          >
            <!-- 水平刻度网格线 -->
            <g v-for="tk in svgChart.yTicks" :key="tk.val">
              <line
                :x1="svgChart.padL"
                :x2="svgChart.W - svgChart.padR"
                :y1="tk.y"
                :y2="tk.y"
                stroke="#e9e7e4"
                stroke-dasharray="3 3"
              />
              <text
                :x="svgChart.padL - 8"
                :y="tk.y + 4"
                text-anchor="end"
                class="svg-axis-text"
              >
                {{ tk.val }}
              </text>
            </g>

            <!-- 面积渐变衬底 -->
            <polygon
              v-if="svgChart.areaPolygon"
              :points="svgChart.areaPolygon"
              fill="rgba(46, 170, 220, 0.08)"
            />

            <!-- 折线主干 -->
            <polyline
              v-if="svgChart.nodes.length > 1"
              :points="svgChart.polyline"
              fill="none"
              stroke="#37352f"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            />

            <!-- 数据节点与交互热区 -->
            <g
              v-for="pt in svgChart.nodes"
              :key="pt.session_id"
              class="svg-point-group"
              tabindex="0"
              @mouseenter="hoveredCurveIdx = pt.idx"
              @focus="hoveredCurveIdx = pt.idx"
              @mouseleave="hoveredCurveIdx = -1"
            >
              <circle
                :cx="pt.x"
                :cy="pt.y"
                :r="hoveredCurveIdx === pt.idx ? 6 : 4"
                :fill="hoveredCurveIdx === pt.idx ? '#2eaadc' : '#ffffff'"
                stroke="#37352f"
                stroke-width="2"
              >
                <title>{{ pt.title }}：{{ pt.score }} 分{{ pt.submitted_at ? `（${String(pt.submitted_at).replace('T', ' ').slice(0, 16)}）` : '' }}</title>
              </circle>
              <text
                :x="pt.x"
                :y="pt.y - 9"
                text-anchor="middle"
                class="svg-val-text"
              >
                {{ pt.score }}
              </text>
              <text
                :x="pt.x"
                :y="svgChart.H - 8"
                text-anchor="middle"
                class="svg-axis-text"
              >
                {{ pt.shortDate }}
              </text>
            </g>
          </svg>
        </div>

        <div class="table-scroll" style="margin-top: 12px;">
          <table class="data-table">
            <thead>
              <tr>
                <th>测验场次</th>
                <th>交卷时间</th>
                <th>得分</th>
                <th>掌握度示意</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in dashboard.quiz_score_curve" :key="s.session_id">
                <td>{{ s.title }}</td>
                <td class="label-text">{{ s.submitted_at ? String(s.submitted_at).replace('T', ' ').slice(0, 16) : '—' }}</td>
                <td><strong>{{ s.score }}</strong> 分</td>
                <td style="width: 160px;">
                  <div class="ink-meter">
                    <div class="ink-meter-fill" :style="{ width: `${Math.max(4, s.score)}%` }"></div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <!-- 4. 课程目录文档块列表（支持分类导航 + 精品筛选 + 封面渲染） -->
      <section class="doc-section">
        <div class="section-header">
          <h2 class="section-title">课程目录</h2>
          <div class="catalog-controls">
            <label class="chk-featured">
              <input type="checkbox" v-model="featuredOnly" @change="loadCoursesOnly" />
              <span>仅看精品推荐课</span>
            </label>
            <div class="search-box">
              <input
                v-model="keyword"
                class="inline-input"
                placeholder="搜索课程名称..."
                @keyup.enter="loadCoursesOnly"
              />
              <button type="button" class="btn btn-sm" @click="loadCoursesOnly">查询</button>
            </div>
          </div>
        </div>

        <!-- 分类导航筛选标签栏 -->
        <div v-if="categoryOptions.length" class="tabs-bar category-tabs">
          <button
            type="button"
            class="tab-pill"
            :class="{ active: selectedCategory === '' }"
            @click="selectCategory('')"
          >
            全部分类
          </button>
          <button
            v-for="cat in categoryOptions"
            :key="cat"
            type="button"
            class="tab-pill"
            :class="{ active: selectedCategory === cat }"
            @click="selectCategory(cat)"
          >
            {{ cat }}
          </button>
        </div>

        <div class="block-list" v-if="displayedCourses.length">
          <div
            v-for="c in displayedCourses"
            :key="c.id"
            class="block-row course-row"
            tabindex="0"
            @click="enrollAndOpen(c)"
            @keydown.enter="enrollAndOpen(c)"
          >
            <span class="block-handle" aria-hidden="true">⋮⋮</span>

            <!-- 课程封面图或学术色带 -->
            <img
              v-if="c.cover_url"
              :src="c.cover_url"
              :alt="c.title"
              class="course-cover-thumb"
            />
            <span
              v-else
              class="course-color-bar"
              :style="{ backgroundColor: c.cover_color || '#37352f' }"
              aria-hidden="true"
            ></span>

            <div class="block-body">
              <div class="course-head">
                <div>
                  <span class="course-title">{{ c.title }}</span>
                  <span v-if="c.subtitle" class="muted-text" style="margin-left: 8px; font-size: 13px;">{{ c.subtitle }}</span>
                </div>
                <div class="course-badges">
                  <span class="badge">{{ c.category }}</span>
                  <span v-if="c.is_featured" class="badge badge-link">精品推荐</span>
                </div>
              </div>
              <p v-if="c.description" class="course-desc">{{ c.description }}</p>
              <div class="course-meta">
                <span class="label-text">授课教师：{{ c.teacher_name }}</span>
                <span class="jump-link">{{ c.is_enrolled ? '进入课程文档 →' : '选修并打开 →' }}</span>
              </div>
            </div>
          </div>
        </div>

        <p v-else class="muted-text">未匹配到符合当前分类或筛选条件的已上架课程。</p>
      </section>
    </template>
  </article>
</template>

<style scoped>
.prop-val {
  margin-top: var(--spacing-4);
  font-size: var(--text-heading);
  font-weight: var(--font-weight-semibold);
  line-height: var(--leading-heading);
}

.prop-val small {
  font-size: var(--text-label);
  font-weight: var(--font-weight-regular);
  color: var(--color-ink-muted);
}

.weak-strip {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-top: var(--spacing-12);
}

.enrolled-block {
  margin-top: var(--spacing-24);
}

.enrolled-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: var(--spacing-12);
}

.enrolled-card {
  background-color: var(--surface-white-page);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.enrolled-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--spacing-8);
}

.enrolled-title {
  font-weight: var(--font-weight-semibold);
  font-size: var(--text-body);
}

.enrolled-progress-row {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
}

.pct-text {
  font-family: var(--font-mono);
  font-size: var(--text-label);
  color: var(--color-ink-secondary);
  min-width: 34px;
  text-align: right;
}

.chart-card {
  padding: var(--spacing-12) var(--spacing-16);
}

.score-svg {
  width: 100%;
  height: auto;
  display: block;
}

.svg-axis-text {
  font-size: 11px;
  fill: rgba(55, 53, 47, 0.55);
  font-family: var(--font-mono);
}

.svg-val-text {
  font-size: 11px;
  font-weight: 600;
  fill: #37352f;
  font-family: var(--font-mono);
}

.svg-point-group {
  cursor: pointer;
}

.curve-tooltip {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.curve-tip-title {
  font-size: 13px;
  font-weight: var(--font-weight-medium);
}

.catalog-controls {
  display: flex;
  align-items: center;
  gap: var(--spacing-12);
  flex-wrap: wrap;
}

.chk-featured {
  display: inline-flex;
  align-items: center;
  gap: var(--spacing-4);
  font-size: var(--text-label);
  color: var(--color-ink-secondary);
  cursor: pointer;
}

.category-tabs {
  margin-bottom: var(--spacing-16);
}

.rec-row,
.course-row {
  cursor: pointer;
  padding-top: var(--spacing-12);
  padding-bottom: var(--spacing-12);
  border-bottom: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
}

.course-cover-thumb {
  width: 76px;
  height: 52px;
  object-fit: cover;
  border-radius: var(--radius-cards);
  border: 1px solid var(--color-hairline);
  flex-shrink: 0;
  margin-top: 2px;
}

.course-color-bar {
  width: 4px;
  align-self: stretch;
  min-height: 44px;
  border-radius: var(--radius-cards);
  flex-shrink: 0;
}

.rec-head,
.course-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.rec-title,
.course-title {
  font-size: var(--text-body-lg);
  font-weight: var(--font-weight-medium);
}

.course-badges {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.course-desc {
  margin: var(--spacing-4) 0 var(--spacing-8);
  color: var(--color-ink-secondary);
}

.course-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
}

.jump-link {
  font-size: var(--text-label);
  color: var(--color-link-blue);
  font-weight: var(--font-weight-medium);
}

.ink-meter {
  height: 6px;
  background-color: var(--color-hover-grey);
  border-radius: var(--radius-cards);
  overflow: hidden;
}

.ink-meter-fill {
  height: 100%;
  background-color: var(--color-ink);
  transition: width 200ms ease;
}

.search-box {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  border-bottom: 1px solid var(--color-hairline);
}

.search-box .inline-input {
  width: 180px;
}
</style>
