<script setup>
import { ref, computed, watch } from 'vue'
import { adminApi } from '../../../api/modules/admin'
import { formatStatus, formatSampleType, formatSourceReason } from '../../../utils/labels'

const props = defineProps({
  samples: { type: Array, required: true },
  initialRuntimeConfig: { type: Object, required: true },
  courses: { type: Array, default: () => [] },
})
const emit = defineEmits(['notify', 'refresh'])

// 1. 按比例随机抽检真实生成记录
const drawForm = ref({
  sample_type: 'all', // all | tutor_reply | outline | question
  ratio_pct: 20,
  course_id: '',
})
const drawingSamples = ref(false)
const drawnRecords = ref([])

// 2. 问题样本队列本地可编辑副本（修正标准答案 + 复核批注）
const localSamples = ref([])

// 3. 手工录入问题样本（含 sample_type 与 course_id 下拉）
const spotCheck = ref({
  course_id: '',
  sample_type: 'tutor_reply',
  input_text: '',
  model_output: '',
  admin_note: '',
})

// 4. 运行时策略配置（模型通道 + 接口地址/模型名/密钥 + 学生/教师限流配额 + 安全词库 + 二期开关）
const localConfig = ref({
  active_provider: 'local',
  confidence_threshold: 0.28,
  student_rate_limit: 30,
  teacher_rate_limit: 60,
  feature_flags: {},
  openai_base_url: 'https://api.openai.com/v1',
  openai_api_key_input: '',
  openai_api_key_masked: '',
  has_openai_api_key: false,
  openai_model: 'gpt-4o-mini',
  openai_embedding_model: '',
  gemini_api_key_input: '',
  gemini_api_key_masked: '',
  has_gemini_api_key: false,
  gemini_model: 'gemini-2.5-flash',
  gemini_embedding_model: 'text-embedding-004',
  asr_provider: 'auto',
  whisper_model_size: 'base',
})
const blockedKwStr = ref('')

function applyGatewayPreset(presetKey) {
  localConfig.value.active_provider = 'openai'
  if (presetKey === 'deepseek') {
    localConfig.value.openai_base_url = 'https://api.deepseek.com/v1'
    localConfig.value.openai_model = 'deepseek-chat'
    localConfig.value.openai_embedding_model = ''
  } else if (presetKey === 'qwen') {
    localConfig.value.openai_base_url = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    localConfig.value.openai_model = 'qwen-plus'
    localConfig.value.openai_embedding_model = 'text-embedding-v3'
  } else if (presetKey === 'siliconflow') {
    localConfig.value.openai_base_url = 'https://api.siliconflow.cn/v1'
    localConfig.value.openai_model = 'Qwen/Qwen2.5-72B-Instruct'
    localConfig.value.openai_embedding_model = 'BAAI/bge-m3'
  } else if (presetKey === 'local_ollama') {
    localConfig.value.openai_base_url = 'http://127.0.0.1:11434/v1'
    localConfig.value.openai_model = 'qwen2.5:7b'
    localConfig.value.openai_embedding_model = 'bge-m3'
  }
}

const courseTitleMap = computed(() => {
  const m = {}
  for (const c of props.courses || []) {
    m[c.id] = c.title
  }
  return m
})

const EXTRA_REASON_LABELS = {
  spot_check: '管理员随机抽检',
  downvote: '学生评价需改进',
  report: '学生内容举报',
  low_confidence: '低置信度自动捕获',
}

function displaySourceReason(val) {
  if (!val) return '人工抽检'
  if (EXTRA_REASON_LABELS[val]) return EXTRA_REASON_LABELS[val]
  return formatSourceReason(val)
}

watch(
  () => props.samples,
  (list) => {
    localSamples.value = (list || []).map((s) => ({
      ...s,
      _corrected: s.corrected_output ?? s.expected_correction ?? '',
      _note: s.admin_note ?? '',
    }))
  },
  { immediate: true }
)

watch(
  () => props.initialRuntimeConfig,
  (cfg) => {
    const rl = cfg?.rate_limit_per_minute
    const stuLimit =
      typeof rl === 'object' && rl !== null
        ? (rl.student ?? 30)
        : (cfg?.student_rate_limit_per_minute ?? (typeof rl === 'number' ? rl : 30))
    const teaLimit =
      typeof rl === 'object' && rl !== null
        ? (rl.teacher ?? 60)
        : (cfg?.teacher_rate_limit_per_minute ?? (typeof rl === 'number' ? rl * 2 : 60))

    const gw = cfg?.model_gateway || {}
    localConfig.value = {
      active_provider: cfg?.active_provider || 'local',
      confidence_threshold: cfg?.confidence_threshold ?? 0.28,
      student_rate_limit: Number(stuLimit || 30),
      teacher_rate_limit: Number(teaLimit || 60),
      feature_flags: { ...(cfg?.feature_flags || {}) },
      openai_base_url: gw.openai_base_url || 'https://api.openai.com/v1',
      openai_api_key_input: '',
      openai_api_key_masked: gw.openai_api_key_masked || '',
      has_openai_api_key: Boolean(gw.has_openai_api_key),
      openai_model: gw.openai_model || 'gpt-4o-mini',
      openai_embedding_model: gw.openai_embedding_model ?? '',
      gemini_api_key_input: '',
      gemini_api_key_masked: gw.gemini_api_key_masked || '',
      has_gemini_api_key: Boolean(gw.has_gemini_api_key),
      gemini_model: gw.gemini_model || 'gemini-2.5-flash',
      gemini_embedding_model: gw.gemini_embedding_model || 'text-embedding-004',
      asr_provider: gw.asr_provider || 'auto',
      whisper_model_size: gw.whisper_model_size || 'base',
    }
    blockedKwStr.value = (cfg?.safety_filter?.blocked_keywords || []).join(', ')
  },
  { immediate: true }
)

async function handleDrawSpotCheck() {
  if (drawingSamples.value) return
  drawingSamples.value = true
  try {
    const ratio = Math.max(1, Math.min(100, Number(drawForm.value.ratio_pct) || 20))
    const res = await adminApi.drawSpotCheckSamples({
      sample_type: drawForm.value.sample_type || 'all',
      sample_ratio_pct: ratio,
      max_count: 10,
      course_id: drawForm.value.course_id || null,
    })
    const items = res?.items || (Array.isArray(res) ? res : [])
    drawnRecords.value = items.map((r, i) => ({
      ...r,
      _uid: r.candidate_id || r.id || `draw_${i}_${Date.now()}`,
      _flagged: false,
      _flagNote: '',
    }))
    emit('notify', `随机抽检完成：从候选池（共 ${res?.total_pool_size ?? items.length} 条）抽取 ${drawnRecords.value.length} 条记录。`)
  } catch (err) {
    emit('notify', `抽检拉取失败：${err.message}`)
  } finally {
    drawingSamples.value = false
  }
}

async function flagDrawnItemAsSample(rec) {
  await adminApi.createSpotCheckSample({
    course_id: rec.course_id || drawForm.value.course_id || props.courses?.[0]?.id || 'global',
    sample_type: rec.sample_type || 'tutor_reply',
    message_id: rec.message_id || null,
    input_text: rec.input_text || rec.question || rec.title || '抽检内容',
    model_output: rec.model_output || rec.content || rec.summary || '',
    admin_note: rec._flagNote?.trim() || '随机比例抽检标记为待改进问题样本',
  })
  rec._flagged = true
  emit('notify', '已将该抽检记录标记入库并同步至智能回归评测池。')
  emit('refresh')
}

async function submitManualSpotCheck() {
  if (!spotCheck.value.input_text.trim()) return
  const targetCourseId = spotCheck.value.course_id || props.courses?.[0]?.id || 'global'
  await adminApi.createSpotCheckSample({
    ...spotCheck.value,
    course_id: targetCourseId,
  })
  spotCheck.value = {
    course_id: spotCheck.value.course_id,
    sample_type: spotCheck.value.sample_type,
    input_text: '',
    model_output: '',
    admin_note: '',
  }
  emit('notify', '手工录入问题样本已回传至评测候选池。')
  emit('refresh')
}

async function resolveSample(sample, status) {
  const corrected = (sample._corrected || '').trim()
  const note = (sample._note || '').trim() || corrected || '管理员复核完成并归档至回归评测集'
  await adminApi.resolveFeedbackSample(sample.id, {
    status,
    corrected_output: corrected,
    admin_note: note,
  })
  emit('notify', status === 'blocked' ? '该问题回答已在前台合规屏蔽。' : '样本修正答案已保存并回流至质量回归评测集。')
  emit('refresh')
}

async function saveRuntimeConfig() {
  const kws = blockedKwStr.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  const modelGateway = {
    openai_base_url: (localConfig.value.openai_base_url || '').trim(),
    openai_model: (localConfig.value.openai_model || '').trim(),
    openai_embedding_model: (localConfig.value.openai_embedding_model || '').trim(),
    gemini_model: (localConfig.value.gemini_model || '').trim(),
    gemini_embedding_model: (localConfig.value.gemini_embedding_model || '').trim(),
    asr_provider: localConfig.value.asr_provider || 'auto',
    whisper_model_size: localConfig.value.whisper_model_size || 'base',
  }
  if ((localConfig.value.openai_api_key_input || '').trim()) {
    modelGateway.openai_api_key = localConfig.value.openai_api_key_input.trim()
  }
  if ((localConfig.value.gemini_api_key_input || '').trim()) {
    modelGateway.gemini_api_key = localConfig.value.gemini_api_key_input.trim()
  }

  await adminApi.patchAiRuntimeConfig({
    active_provider: localConfig.value.active_provider,
    confidence_threshold: Number(localConfig.value.confidence_threshold),
    model_gateway: modelGateway,
    rate_limit_per_minute: {
      student: Math.max(1, Number(localConfig.value.student_rate_limit) || 30),
      teacher: Math.max(1, Number(localConfig.value.teacher_rate_limit) || 60),
    },
    feature_flags: localConfig.value.feature_flags,
    safety_filter: {
      input_filter_enabled: true,
      output_filter_enabled: true,
      blocked_keywords: kws,
    },
  })
  localConfig.value.openai_api_key_input = ''
  localConfig.value.gemini_api_key_input = ''
  emit('notify', '大模型接口配置、密钥、调用限额与安全策略已在线保存并即时生效。')
  emit('refresh')
}
</script>

<template>
  <div>
    <!-- 1. 按比例随机抽检真实生成记录 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">按比例随机质量抽检</h2>
          <div class="label-text">按指定比例随机抽取真实智能助教回答、视频章节提纲与智能生成试题记录进行人工巡检</div>
        </div>
      </div>

      <div class="card form-col">
        <div class="draw-toolbar">
          <div class="field">
            <label class="label-text">抽检内容类型</label>
            <select v-model="drawForm.sample_type" class="select">
              <option value="all">全部生成内容</option>
              <option value="tutor_reply">智能助教回答</option>
              <option value="outline">视频章节提纲</option>
              <option value="question">智能生成试题</option>
            </select>
          </div>

          <div class="field">
            <label class="label-text">抽检比例（%）</label>
            <input v-model.number="drawForm.ratio_pct" type="number" min="1" max="100" class="input" />
          </div>

          <div class="field">
            <label class="label-text">限定课程</label>
            <select v-model="drawForm.course_id" class="select">
              <option value="">全站全部课程</option>
              <option v-for="c in courses" :key="c.id" :value="c.id">{{ c.title }}</option>
            </select>
          </div>

          <button
            type="button"
            class="btn btn-primary btn-sm"
            style="align-self: end;"
            :disabled="drawingSamples"
            @click="handleDrawSpotCheck"
          >
            {{ drawingSamples ? '抽取中...' : '随机抽取记录' }}
          </button>
        </div>

        <div v-if="drawnRecords.length" class="drawn-list">
          <div class="label-text">本次抽检命中的 {{ drawnRecords.length }} 条生成记录：</div>
          <div v-for="rec in drawnRecords" :key="rec._uid" class="drawn-card">
            <div class="sample-head">
              <span class="badge">{{ formatSampleType(rec.sample_type || 'tutor_reply') }} · {{ courseTitleMap[rec.course_id] || '全站课程' }}</span>
              <span v-if="rec._flagged" class="badge badge-danger">已入库问题样本</span>
            </div>
            <div style="margin-top: 4px; font-size: 13px;"><strong>输入：</strong>{{ rec.input_text || rec.question }}</div>
            <div class="muted-text" style="font-size: 12.5px; margin-top: 2px;">
              输出：{{ String(rec.model_output || rec.content || '').slice(0, 220) }}
            </div>
            <div v-if="!rec._flagged" class="drawn-actions">
              <input v-model="rec._flagNote" class="inline-input" style="flex: 1;" placeholder="选填问题批注..." />
              <button type="button" class="btn btn-outline btn-sm btn-danger" @click="flagDrawnItemAsSample(rec)">
                标记为问题样本
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 2. 问题样本队列复核、修正标准答案与回流评测集 -->
    <section class="doc-section">
      <div class="section-header">
        <div>
          <h2 class="section-title">问题样本队列与修正回流</h2>
          <div class="label-text">汇总学生评价反馈、内容举报、教师质量上报及管理员抽检样本，填写修正标准答案后回流质量回归评测集</div>
        </div>
        <span class="label-text">共 {{ localSamples.length }} 条</span>
      </div>

      <div v-if="localSamples.length" class="sample-list">
        <div v-for="s in localSamples" :key="s.id" class="sample-entry">
          <div class="sample-head">
            <span class="badge">{{ displaySourceReason(s.source_reason) }} · {{ formatSampleType(s.sample_type) }}</span>
            <span class="badge" :class="{ 'badge-link': s.synced_to_ai_eval, 'badge-danger': s.status === 'blocked' }">
              {{ s.synced_to_ai_eval && s.status !== 'blocked' ? formatStatus('synced_eval') : formatStatus(s.status) }}
            </span>
          </div>
          <div style="margin-top: 6px;"><strong>输入：</strong>{{ s.input_text }}</div>
          <div class="muted-text" style="font-size: 13px; margin-top: 2px;">
            原模型输出：{{ String(s.model_output || '').slice(0, 200) }}
          </div>

          <div class="correction-box">
            <textarea
              v-model="s._corrected"
              class="textarea"
              rows="2"
              placeholder="请输入人工修正后的期望标准答案..."
            ></textarea>
            <div class="corr-bottom">
              <input
                v-model="s._note"
                class="inline-input"
                style="flex: 1;"
                placeholder="请输入管理员复核批注说明..."
              />
              <div class="btn-row">
                <button type="button" class="btn btn-primary btn-sm" @click="resolveSample(s, 'synced_eval')">
                  保存修正并回流评测集
                </button>
                <button type="button" class="btn btn-sm btn-danger" @click="resolveSample(s, 'blocked')">
                  屏蔽问题回答
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      <p v-else class="muted-text" style="margin-bottom: 16px;">当前暂无待复核的问题样本。</p>

      <!-- 手工录入问题样本 -->
      <div class="card form-col" style="margin-top: 16px;">
        <div class="label-text">手工登记问题样本</div>
        <div class="grid-2">
          <select v-model="spotCheck.sample_type" class="select">
            <option value="tutor_reply">智能助教回答</option>
            <option value="outline">视频章节提纲</option>
            <option value="question">智能生成试题</option>
          </select>
          <select v-model="spotCheck.course_id" class="select">
            <option value="">默认首门课程 / 全站通用</option>
            <option v-for="c in courses" :key="c.id" :value="c.id">{{ c.title }}</option>
          </select>
        </div>
        <input v-model="spotCheck.input_text" class="input" placeholder="问题输入原文 / 考查知识点..." />
        <input v-model="spotCheck.model_output" class="input" placeholder="模型错误输出摘要..." />
        <input v-model="spotCheck.admin_note" class="input" placeholder="期望修正说明..." />
        <div>
          <button
            type="button"
            class="btn btn-outline btn-sm"
            :disabled="!spotCheck.input_text.trim()"
            @click="submitManualSpotCheck"
          >
            手工登记并同步至评测池
          </button>
        </div>
      </div>
    </section>

    <!-- 3. 模型推理通道、接口密钥配置、调用限流与二期功能开关 -->
    <section class="doc-section">
      <div class="section-header">
        <h2 class="section-title">模型推理通道、接口密钥与运行策略配置</h2>
      </div>

      <div class="card form-col">
        <div class="grid-2">
          <div class="field">
            <label class="label-text">当前活跃推理模型通道</label>
            <select v-model="localConfig.active_provider" class="select">
              <option value="local">本地离线混合引擎（零外部依赖 / 私有化部署）</option>
              <option value="openai">通用大模型通道（兼容主流国产与国际接口）</option>
              <option value="gemini">多模态大模型通道</option>
            </select>
          </div>

          <div class="field">
            <label class="label-text">知识库检索低置信度转人工阈值（0.05 ~ 0.95）</label>
            <input v-model.number="localConfig.confidence_threshold" type="number" step="0.05" min="0.05" max="0.95" class="input" />
          </div>
        </div>

        <!-- 通用大模型接口地址、模型名与密钥在线配置 -->
        <div v-if="localConfig.active_provider === 'openai'" class="drawn-card form-col">
          <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px;">
            <span class="label-text">通用大模型接口与密钥参数（保存后立即热生效，无需重启服务）</span>
            <div style="display: flex; flex-wrap: wrap; gap: 6px;">
              <button type="button" class="btn btn-outline btn-sm" @click="applyGatewayPreset('deepseek')">填入深度求索预设</button>
              <button type="button" class="btn btn-outline btn-sm" @click="applyGatewayPreset('qwen')">填入通义千问预设</button>
              <button type="button" class="btn btn-outline btn-sm" @click="applyGatewayPreset('siliconflow')">填入硅基流动预设</button>
              <button type="button" class="btn btn-outline btn-sm" @click="applyGatewayPreset('local_ollama')">填入本地私有服务预设</button>
            </div>
          </div>

          <div class="grid-2">
            <div class="field">
              <label class="label-text">接口服务地址</label>
              <input v-model="localConfig.openai_base_url" class="input" placeholder="请输入大模型接口基础地址..." />
            </div>
            <div class="field">
              <label class="label-text">
                接口访问密钥
                <span v-if="localConfig.has_openai_api_key" class="badge" style="margin-left: 6px;">
                  已配置（{{ localConfig.openai_api_key_masked }}）
                </span>
                <span v-else class="muted-text" style="margin-left: 6px;">（尚未配置密钥）</span>
              </label>
              <input
                v-model="localConfig.openai_api_key_input"
                type="password"
                autocomplete="new-password"
                class="input"
                :placeholder="localConfig.has_openai_api_key ? '留空保持原密钥不变，输入新密钥将覆盖更新...' : '请输入大模型接口访问密钥...'"
              />
            </div>
          </div>

          <div class="grid-2">
            <div class="field">
              <label class="label-text">对话与智能出题模型名称</label>
              <input v-model="localConfig.openai_model" class="input" placeholder="请输入对话模型名称..." />
            </div>
            <div class="field">
              <label class="label-text">文本向量嵌入模型名称（选填，留空自动启用本地混合检索）</label>
              <input v-model="localConfig.openai_embedding_model" class="input" placeholder="留空则使用内置本地混合向量检索..." />
            </div>
          </div>
        </div>

        <!-- 多模态大模型接口与密钥在线配置 -->
        <div v-if="localConfig.active_provider === 'gemini'" class="drawn-card form-col">
          <span class="label-text">多模态大模型接口与密钥参数（保存后立即热生效）</span>
          <div class="grid-2">
            <div class="field">
              <label class="label-text">
                接口访问密钥
                <span v-if="localConfig.has_gemini_api_key" class="badge" style="margin-left: 6px;">
                  已配置（{{ localConfig.gemini_api_key_masked }}）
                </span>
                <span v-else class="muted-text" style="margin-left: 6px;">（尚未配置密钥）</span>
              </label>
              <input
                v-model="localConfig.gemini_api_key_input"
                type="password"
                autocomplete="new-password"
                class="input"
                :placeholder="localConfig.has_gemini_api_key ? '留空保持原密钥不变，输入新密钥将覆盖更新...' : '请输入多模态模型接口访问密钥...'"
              />
            </div>
            <div class="field">
              <label class="label-text">对话与章节分析模型名称</label>
              <input v-model="localConfig.gemini_model" class="input" placeholder="请输入多模态对话模型名称..." />
            </div>
          </div>
          <div class="field">
            <label class="label-text">文本向量嵌入模型名称</label>
            <input v-model="localConfig.gemini_embedding_model" class="input" placeholder="请输入向量模型名称..." />
          </div>
        </div>

        <!-- 视频语音识别策略配置 -->
        <div class="grid-2">
          <div class="field">
            <label class="label-text">视频语音识别通道</label>
            <select v-model="localConfig.asr_provider" class="select">
              <option value="auto">智能自动级联（有云端密钥优先走云端，否则走本地离线识别）</option>
              <option value="local_whisper">强制本地离线语音识别</option>
              <option value="openai">通用云端语音识别接口</option>
              <option value="gemini">多模态音频识别接口</option>
            </select>
          </div>
          <div class="field">
            <label class="label-text">本地离线语音识别模型规格</label>
            <select v-model="localConfig.whisper_model_size" class="select">
              <option value="tiny">超轻量极速规格（内存占用最小）</option>
              <option value="base">标准均衡规格（默认推荐）</option>
              <option value="small">增强精准规格</option>
              <option value="medium">专业高精度规格（建议配合独立显卡）</option>
            </select>
          </div>
        </div>

        <div class="grid-2">
          <div class="field">
            <label class="label-text">学生每分钟智能服务调用限额（次/分钟）</label>
            <input v-model.number="localConfig.student_rate_limit" type="number" min="1" max="600" class="input" />
          </div>
          <div class="field">
            <label class="label-text">授课教师每分钟智能服务调用限额（次/分钟）</label>
            <input v-model.number="localConfig.teacher_rate_limit" type="number" min="1" max="1200" class="input" />
          </div>
        </div>

        <div class="field">
          <label class="label-text">输入与输出双向拦截敏感词（以逗号分隔）</label>
          <input v-model="blockedKwStr" class="input" placeholder="输入需拦截的敏感词汇，多个词以逗号分隔..." />
        </div>

        <div class="label-text" style="margin-top: 8px;">二期前沿功能模块开关</div>
        <label class="chk-line">
          <input type="checkbox" v-model="localConfig.feature_flags.enable_v2_harness" />
          <span>启用方向一：自动化质量回归评测门禁</span>
        </label>
        <label class="chk-line">
          <input type="checkbox" v-model="localConfig.feature_flags.enable_v2_personalization" />
          <span>启用方向二：深度个性化学习路径引擎</span>
        </label>
        <label class="chk-line">
          <input type="checkbox" v-model="localConfig.feature_flags.enable_v2_rsi" />
          <span>启用方向三：多步推理与自进化智能体</span>
        </label>

        <div>
          <button type="button" class="btn btn-primary" @click="saveRuntimeConfig">保存运行时配置</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.draw-toolbar {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
  gap: var(--spacing-8);
}

@media (max-width: 680px) {
  .draw-toolbar {
    grid-template-columns: 1fr;
  }
}

.grid-2 {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--spacing-12);
}

@media (max-width: 640px) {
  .grid-2 {
    grid-template-columns: 1fr;
  }
}

.drawn-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
  padding-top: var(--spacing-8);
  border-top: 1px solid var(--color-hairline);
}

.drawn-card {
  padding: var(--spacing-8) var(--spacing-12);
  border: 1px solid var(--color-hairline);
  border-radius: var(--radius-cards);
  background-color: var(--surface-white-page);
}

.drawn-actions {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  margin-top: var(--spacing-8);
  border-top: 1px dashed var(--color-hairline);
  padding-top: var(--spacing-4);
}

.sample-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-16);
}

.sample-entry {
  padding-bottom: var(--spacing-16);
  border-bottom: 1px solid var(--color-hairline);
}

.sample-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
}

.correction-box {
  margin-top: var(--spacing-8);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-8);
}

.corr-bottom {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--spacing-8);
  flex-wrap: wrap;
}

.btn-row {
  display: flex;
  gap: var(--spacing-4);
  flex-wrap: wrap;
}

.form-col {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-12);
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.chk-line {
  display: flex;
  align-items: center;
  gap: var(--spacing-8);
  cursor: pointer;
}
</style>
