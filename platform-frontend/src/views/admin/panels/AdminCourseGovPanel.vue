<script setup>
import { ref, watch, onMounted } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { formatStatus } from '../../../utils/labels'

const props = defineProps({
  courses: { type: Array, required: true },
})
const emit = defineEmits(['notify', 'refresh'])

const errorTip = ref('')
const localCourses = ref([])
const cleaningExpired = ref(false)

// 驳回课程弹窗
const rejectTarget = ref(null)
const rejectNote = ref('')

// 展开课程下视频与讲义合规审查
const expandedCourseId = ref('')
const courseResources = ref({ videos: [], documents: [] })
const loadingResources = ref(false)
const takedownReason = ref('涉及敏感内容或版权合规问题下架')

// 课程分类与导航管理
const categories = ref([])
const newCat = ref({ name: '', sort_order: 10 })
const editingCatId = ref('')
const editCatForm = ref({ name: '', sort_order: 10 })

watch(
  () => props.courses,
  (list) => {
    localCourses.value = (list || []).map((c) => ({
      ...c,
      _quotaMb: c.storage_quota_mb ?? 2048,
      _expiresAt: c.expires_at ? String(c.expires_at).slice(0, 10) : '',
    }))
  },
  { immediate: true }
)

async function loadCategories() {
  try {
    const res = await adminApi.fetchCategories()
    const items = res?.items || (Array.isArray(res) ? res : [])
    categories.value = items
      .map((item, idx) =>
        typeof item === 'string'
          ? { id: item, name: item, sort_order: (idx + 1) * 10 }
          : { id: item.id || item.name, name: item.name || item.title, sort_order: item.sort_order ?? (idx + 1) * 10 }
      )
      .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0))
  } catch {
    categories.value = []
  }
}

onMounted(loadCategories)

async function governCourse(course, patch) {
  errorTip.value = ''
  try {
    await adminApi.patchCourseGovernance(course.id, patch)
    emit('notify', `课程《${course.title}》治理配置已更新。`)
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message || '课程治理操作失败'
  }
}

async function confirmRejectCourse() {
  if (!rejectTarget.value) return
  const reason = rejectNote.value.trim() || '课程章节提纲或讲义尚未完备，请补充后重新提交审核'
  errorTip.value = ''
  try {
    try {
      await adminApi.patchCourseGovernance(rejectTarget.value.id, {
        status: 'rejected',
        reject_reason: reason,
        review_note: reason,
      })
    } catch (err) {
      // 兼容旧枚举退回 draft 附带审核意见
      if (err?.status === 422) {
        await adminApi.patchCourseGovernance(rejectTarget.value.id, {
          status: 'draft',
          reject_reason: reason,
          review_note: reason,
        })
      } else {
        throw err
      }
    }
    emit('notify', `已驳回课程《${rejectTarget.value.title}》并附上审核意见。`)
    rejectTarget.value = null
    rejectNote.value = ''
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message
  }
}

async function saveQuotaAndExpiry(c) {
  await governCourse(c, {
    storage_quota_mb: Math.max(50, Number(c._quotaMb) || 2048),
    expires_at: c._expiresAt || null,
  })
}

async function handleCleanupExpired() {
  cleaningExpired.value = true
  errorTip.value = ''
  try {
    const res = await adminApi.cleanupExpiredCourses()
    emit('notify', `过期课程清理执行完毕（归档/清理 ${res?.cleaned_count ?? res?.archived_count ?? 0} 门到期课程）。`)
    emit('refresh')
  } catch (err) {
    errorTip.value = err.message || '清理过期课程失败'
  } finally {
    cleaningExpired.value = false
  }
}

async function toggleExpandResources(course) {
  if (expandedCourseId.value === course.id) {
    expandedCourseId.value = ''
    return
  }
  expandedCourseId.value = course.id
  loadingResources.value = true
  try {
    const res = await adminApi.fetchCourseResources(course.id)
    courseResources.value = {
      videos: res?.videos || [],
      documents: res?.documents || [],
    }
  } catch (err) {
    errorTip.value = err.message || '读取课程下属资源失败'
    courseResources.value = { videos: [], documents: [] }
  } finally {
    loadingResources.value = false
  }
}

async function handleTakedownVideo(vid) {
  await adminApi.takedownVideo(vid.id, { reason: takedownReason.value.trim() || '违规视频合规下架' })
  emit('notify', `视频《${vid.title}》已合规下架并清理索引切片。`)
  const current = localCourses.value.find((c) => c.id === expandedCourseId.value)
  if (current) {
    expandedCourseId.value = ''
    await toggleExpandResources(current)
  }
}

async function handleTakedownDoc(doc) {
  await adminApi.takedownDocument(doc.id, { reason: takedownReason.value.trim() || '违规讲义合规下架' })
  emit('notify', `讲义《${doc.title}》已合规下架并从向量知识库移除。`)
  const current = localCourses.value.find((c) => c.id === expandedCourseId.value)
  if (current) {
    expandedCourseId.value = ''
    await toggleExpandResources(current)
  }
}

// ---------------- 课程分类增删改排序 ----------------
async function addCategory() {
  if (!newCat.value.name.trim()) return
  errorTip.value = ''
  try {
    await adminApi.createCategory({
      name: newCat.value.name.trim(),
      sort_order: Number(newCat.value.sort_order) || 10,
    })
    newCat.value = { name: '', sort_order: (categories.value.length + 1) * 10 }
    emit('notify', '课程分类导航已新增。')
    await loadCategories()
  } catch (err) {
    errorTip.value = String(err.message || '新增分类失败').replace(/\bslug\b/gi, '导航标识')
  }
}

function startEditCat(cat) {
  editingCatId.value = cat.id
  editCatForm.value = { name: cat.name, sort_order: cat.sort_order ?? 10 }
}

async function saveEditCat(cat) {
  if (!editCatForm.value.name.trim()) return
  await adminApi.patchCategory(cat.id, {
    name: editCatForm.value.name.trim(),
    sort_order: Number(editCatForm.value.sort_order) || 0,
  })
  editingCatId.value = ''
  emit('notify', '分类信息与排序权重已更新。')
  await loadCategories()
}

async function nudgeCatOrder(cat, delta) {
  const nextOrder = Math.max(0, Number(cat.sort_order || 10) + delta)
  await adminApi.patchCategory(cat.id, { sort_order: nextOrder })
  await loadCategories()
}

async function removeCategory(cat) {
  errorTip.value = ''
  try {
    await adminApi.deleteCategory(cat.id)
    emit('notify', `分类「${cat.name}」已移除。`)
    await loadCategories()
  } catch (err) {
    errorTip.value = err.message
  }
}
</script>

<template>
  <div>
    <div v-if="errorTip" class="callout callout-danger doc-section" role="alert">
      <span class="callout-icon" aria-hidden="true">!</span>
      <span>{{ errorTip }}</span>
    </div>

    <!-- 1. 课程审核、配额/有效期与单资源合规下架 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">课程上架审核、存储配额与单资源合规下架</h2>
          <div class="label-text">共 {{ localCourses.length }} 门课程 · 支持展开核查单讲次视频与讲义合规性</div>
        </div>
        <button type="button" class="btn btn-outline btn-sm" :disabled="cleaningExpired" @click="handleCleanupExpired">
          {{ cleaningExpired ? '清理中...' : '一键清理过期课程' }}
        </button>
      </div>

      <div v-if="localCourses.length" class="table-scroll">
        <table class="data-table">
          <thead>
            <tr>
              <th>课程 / 授课教师</th>
              <th>状态 / 推荐</th>
              <th>存储配额（兆）与有效期</th>
              <th>治理与审查操作</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="c in localCourses" :key="c.id">
              <tr>
                <td>
                  <div><strong>{{ c.title }}</strong></div>
                  <div class="label-text">{{ c.teacher_name }} · {{ c.category }}</div>
                </td>
                <td>
                  <span class="badge" :class="{ 'badge-danger': c.status === 'takedown' || c.status === 'rejected' }">
                    {{ formatStatus(c.status) }}
                  </span>
                  <span v-if="c.is_featured" class="badge badge-link" style="margin-left: 4px;">精选</span>
                </td>
                <td>
                  <div class="quota-cell">
                    <span class="label-text">{{ c.storage_used_mb }} 兆 /</span>
                    <input v-model.number="c._quotaMb" type="number" class="input quota-input" title="存储配额（兆）" />
                    <span class="label-text">兆</span>
                    <input v-model="c._expiresAt" type="date" class="input date-input" title="课程到期归档日期" />
                    <button type="button" class="btn btn-sm" @click="saveQuotaAndExpiry(c)">保存配额</button>
                  </div>
                </td>
                <td>
                  <div class="row-actions">
                    <button
                      v-if="c.status !== 'published'"
                      type="button"
                      class="btn btn-primary btn-sm"
                      @click="governCourse(c, { status: 'published' })"
                    >
                      批准上架
                    </button>
                    <button
                      v-if="c.status === 'pending_review'"
                      type="button"
                      class="btn btn-sm btn-danger"
                      @click="rejectTarget = c; rejectNote = ''"
                    >
                      驳回
                    </button>
                    <button
                      v-if="c.status === 'published'"
                      type="button"
                      class="btn btn-danger btn-sm"
                      @click="governCourse(c, { status: 'takedown' })"
                    >
                      下架
                    </button>
                    <button
                      v-if="c.status !== 'archived'"
                      type="button"
                      class="btn btn-sm"
                      @click="governCourse(c, { status: 'archived' })"
                    >
                      归档
                    </button>
                    <button
                      type="button"
                      class="btn btn-sm"
                      @click="governCourse(c, { is_featured: !c.is_featured })"
                    >
                      {{ c.is_featured ? '取消精选' : '设为精选' }}
                    </button>
                    <button type="button" class="btn btn-outline btn-sm" @click="toggleExpandResources(c)">
                      {{ expandedCourseId === c.id ? '收起资源' : '审查资源' }}
                    </button>
                  </div>
                </td>
              </tr>

              <!-- 展开的单课视频与讲义合规下架抽屉行 -->
              <tr v-if="expandedCourseId === c.id">
                <td colspan="4" class="res-drawer-cell">
                  <div class="res-drawer">
                    <div class="res-reason-bar">
                      <span class="label-text">合规下架处置原因：</span>
                      <input v-model="takedownReason" class="input" style="flex: 1;" />
                    </div>

                    <p v-if="loadingResources" class="muted-text">正在读取本课程视频与讲义清单...</p>
                    <div v-else class="res-columns">
                      <div>
                        <div class="label-text" style="margin-bottom: 6px;">授课视频（{{ courseResources.videos.length }} 个）</div>
                        <div v-if="courseResources.videos.length" class="res-list">
                          <div v-for="v in courseResources.videos" :key="v.id" class="res-item">
                            <div>
                              <span>{{ v.title }}</span>
                              <span class="badge" style="margin-left: 6px;">{{ formatStatus(v.outline_status) }}</span>
                              <span v-if="v.is_hidden" class="badge badge-danger" style="margin-left: 4px;">已下架屏蔽</span>
                            </div>
                            <button
                              v-if="!v.is_hidden && v.outline_status !== 'removed' && v.outline_status !== 'takedown'"
                              type="button"
                              class="btn btn-sm btn-danger"
                              @click="handleTakedownVideo(v)"
                            >
                              合规下架
                            </button>
                          </div>
                        </div>
                        <p v-else class="muted-text" style="font-size: 12px;">暂无视频资源。</p>
                      </div>

                      <div>
                        <div class="label-text" style="margin-bottom: 6px;">课程讲义文档（{{ courseResources.documents.length }} 份）</div>
                        <div v-if="courseResources.documents.length" class="res-list">
                          <div v-for="d in courseResources.documents" :key="d.id" class="res-item">
                            <div>
                              <span>{{ d.title }}</span>
                              <span class="badge" style="margin-left: 6px;">{{ formatStatus(d.index_status) }}</span>
                            </div>
                            <button
                              v-if="d.index_status !== 'removed'"
                              type="button"
                              class="btn btn-sm btn-danger"
                              @click="handleTakedownDoc(d)"
                            >
                              合规下架
                            </button>
                          </div>
                        </div>
                        <p v-else class="muted-text" style="font-size: 12px;">暂无讲义文档。</p>
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>

      <p v-else class="muted-text">当前暂无待审核或已创建的课程。</p>
    </section>

    <!-- 2. 课程分类与导航管理 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">课程分类与导航管理</h2>
          <div class="label-text">配置学生端学习中心分类标签栏与展示优先级（排序权重数值越小越靠前）</div>
        </div>
      </div>

      <div class="cat-create-bar">
        <input v-model="newCat.name" class="input" style="max-width: 240px;" placeholder="新增分类名称（如：量子计算）" @keyup.enter="addCategory" />
        <input v-model.number="newCat.sort_order" type="number" class="input" style="width: 90px;" title="排序权重" />
        <button type="button" class="btn btn-primary btn-sm" @click="addCategory">新增分类</button>
      </div>

      <div v-if="categories.length" class="block-list">
        <div v-for="cat in categories" :key="cat.id" class="block-row cat-row">
          <span class="block-handle" aria-hidden="true">⋮⋮</span>
          <div class="block-body cat-line">
            <template v-if="editingCatId === cat.id">
              <div style="display: flex; gap: 8px; align-items: center; flex: 1;">
                <input v-model="editCatForm.name" class="input" style="max-width: 200px;" />
                <input v-model.number="editCatForm.sort_order" type="number" class="input" style="width: 80px;" title="排序权重" />
                <button type="button" class="btn btn-primary btn-sm" @click="saveEditCat(cat)">保存</button>
                <button type="button" class="btn btn-sm" @click="editingCatId = ''">取消</button>
              </div>
            </template>
            <template v-else>
              <div>
                <strong>{{ cat.name }}</strong>
                <span class="badge" style="margin-left: 8px;">排序权重 {{ cat.sort_order }}</span>
              </div>
              <div class="row-actions">
                <button type="button" class="btn btn-sm" title="上移" @click="nudgeCatOrder(cat, -5)">↑</button>
                <button type="button" class="btn btn-sm" title="下移" @click="nudgeCatOrder(cat, 5)">↓</button>
                <button type="button" class="btn btn-sm" @click="startEditCat(cat)">编辑</button>
                <button type="button" class="btn btn-sm btn-danger" @click="removeCategory(cat)">删除</button>
              </div>
            </template>
          </div>
        </div>
      </div>
      <p v-else class="muted-text">暂无自定义导航分类。</p>
    </section>

    <!-- 课程驳回审核意见弹层 -->
    <div v-if="rejectTarget" class="modal-backdrop" @click.self="rejectTarget = null">
      <div class="popover-surface modal-sm" role="dialog" aria-modal="true">
        <div class="section-header">
          <h3 class="section-title">驳回课程上架：《{{ rejectTarget.title }}》</h3>
          <button type="button" class="btn btn-sm" @click="rejectTarget = null">关闭</button>
        </div>
        <div style="display: flex; flex-direction: column; gap: 10px;">
          <label class="label-text">审核驳回意见（将通过系统通知发送给授课教师）</label>
          <textarea v-model="rejectNote" class="textarea" rows="3" placeholder="请说明需要补充的章节提纲或合规修改点..."></textarea>
          <div style="display: flex; justify-content: flex-end; gap: 8px;">
            <button type="button" class="btn" @click="rejectTarget = null">取消</button>
            <button type="button" class="btn btn-primary" @click="confirmRejectCourse">确认驳回</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-actions {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.quota-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.quota-input {
  width: 72px;
  font-family: var(--font-mono);
}

.date-input {
  width: 132px;
  font-size: 12px;
}

.res-drawer-cell {
  background-color: var(--surface-white-page);
  padding: var(--spacing-12) var(--spacing-16);
}

.res-drawer {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.res-reason-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
}

.res-columns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-16);
}

@media (max-width: 640px) {
  .res-columns {
    grid-template-columns: 1fr;
  }
}

.res-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.res-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  padding: 6px 8px;
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--color-paper);
  font-size: 13px;
}

.cat-create-bar {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
  margin-bottom: var(--spacing-12);
}

.cat-row {
  margin-left: 0;
  border-bottom: 1px solid var(--color-hairline);
}

.cat-line {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
}

.modal-sm {
  width: min(440px, 100%);
}
</style>
