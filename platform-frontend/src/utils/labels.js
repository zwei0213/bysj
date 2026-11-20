/**
 * 全站统一中文本地化字典与状态格式化工具
 * 将后端技术枚举值、任务编码、审计动作、策略标识统一映射为面向中国师生的规范中文表达。
 */

const STATUS_LABELS = {
  // 系统健康与连通状态
  ok: '运行正常',
  healthy: '运行健康',
  connected: '已连接',
  disconnected: '连接中断',
  unreachable: '暂不可达',
  degraded: '降级保护中',

  // 课程与大纲生命周期状态
  published: '已发布上架',
  pending_review: '待审核',
  draft: '草稿',
  draft_review: '待教师校准',
  processing: '正在处理',
  closed: '已结课',
  archived: '已归档',
  takedown: '已下架',
  rejected: '已驳回',
  approved: '已采纳通过',

  // 教师资质状态
  pending: '待处理',

  // 知识库索引状态
  unindexed: '尚未建立索引',
  indexing: '正在构建索引',
  building: '正在构建索引',
  ready: '索引就绪',
  stale: '待重建更新',
  removed: '已移除下架',

  // 异步任务队列状态
  queued: '排队等待中',
  running: '正在执行',
  succeeded: '执行成功',
  failed: '执行失败',

  // 问题样本与质检状态
  corrected: '已人工修正',
  blocked: '已合规屏蔽',
  synced_eval: '已回流评测集',
  queued_for_harness_eval: '已纳入回归评测池',

  // 工单与收件箱状态
  open: '待受理',
  in_progress: '处理中',
  resolved: '已办结',
  submitted: '已交卷',
}

const ROLE_LABELS = {
  student: '学生',
  teacher: '授课教师',
  admin: '平台管理员',
  all: '全站师生',
  user: '学生提问',
  assistant: '智能助教',
}

const ORG_TYPE_LABELS = {
  department: '院系 / 部门',
  grade: '年级 / 专业',
  class: '行政教学班',
}

const QUESTION_TYPE_LABELS = {
  single_choice: '单选题',
  multiple_choice: '多选题',
  true_false: '判断题',
}

const DIFFICULTY_LABELS = {
  easy: '基础',
  medium: '中等',
  hard: '进阶',
  mixed: '综合难度',
}

const JOB_TYPE_LABELS = {
  video_chapterization: '视频语音转写与章节切分',
  knowledge_index_build: '课程知识库全量索引重建',
}

const CAPABILITY_LABELS = {
  tutor_rag_reply: '智能助教课程答疑',
  video_chapterization: '视频章节提纲生成',
  chapter_regeneration: '单章节智能重生成',
  assessment_teacher_bank: '教师题库批量命题',
  assessment_chapter_practice: '章节随堂即时自测',
  assessment_personalized_diagnostic: '薄弱知识点靶向命题',
  recommendation_generation: '个性化学习内容推荐',
  question_clustering: '高频提问盲区聚类',
}

const SAMPLE_TYPE_LABELS = {
  all: '全部生成内容',
  tutor_reply: '智能助教回答',
  outline: '视频章节提纲',
  question: '智能生成试题',
}

const SOURCE_REASON_LABELS = {
  student_downvote: '学生评价需改进',
  student_report: '学生内容举报',
  teacher_feedback: '授课教师质量上报',
  admin_spot_check: '管理员随机抽检',
}

const PROVIDER_LABELS = {
  local: '本地离线混合引擎',
  'local-guardrail': '本地安全规则护栏',
  openai: '通用大模型接口',
  gemini: '多模态大模型接口',
}

const DIALECT_LABELS = {
  sqlite: '轻量级嵌入式关系库',
  postgresql: '企业级关系数据库',
}

const STRATEGY_KEY_LABELS = {
  chapterizer: '视频章节切分',
  rag: '课程知识检索答疑',
  rag_engine: '课程知识检索答疑',
  assessment: '结构化智能命题',
  quiz_generator: '结构化智能命题',
  recommender: '个性化内容推荐',
  question_clusterer: '学情高频问题聚类',
  clustering: '学情高频问题聚类',
}

const STRATEGY_NAME_LABELS = {
  v1_semantic_window_chapterizer: '一阶段语义时间窗切分策略',
  v1_course_isolated_rag: '一阶段课程隔离混合检索策略',
  v1_knowledge_driven_quiz_generator: '一阶段知识点驱动命题策略',
  v1_explainable_rule_recommender: '一阶段可解释多因子推荐策略',
  v1_topic_aligned_question_clusterer: '一阶段知识点对齐聚类策略',
}

const AUDIT_ACTION_LABELS = {
  CREATE_ORG: '新建组织节点',
  UPDATE_ORG: '修改组织节点',
  DELETE_ORG: '删除组织节点',
  CREATE_USER: '新建用户账号',
  UPDATE_USER: '更新用户账号与权限',
  DELETE_USER: '删除用户账号',
  BATCH_IMPORT_USERS: '文本批量导入账号',
  XLSX_IMPORT_USERS: '表格文件批量导入账号',
  UPDATE_PERMISSIONS_MATRIX: '更新角色权限矩阵',
  BATCH_ASSIGN_CLASS: '批量分配教学班级',
  GOVERN_COURSE: '课程审核与治理变更',
  CREATE_CATEGORY: '新增课程分类',
  UPDATE_CATEGORY: '修改课程分类',
  DELETE_CATEGORY: '删除课程分类',
  CLEANUP_EXPIRED_COURSES: '归档清理过期课程',
  TAKEDOWN_DOCUMENT: '下架课程讲义资料',
  TAKEDOWN_VIDEO: '下架课程视频资源',
  RETRY_AI_JOB: '重试异步智能任务',
  REBUILD_COURSE_INDEX: '重建课程知识库索引',
  CREATE_SPOT_CHECK: '登记质量抽检样本',
  RESOLVE_QUALITY_SAMPLE: '处置质量问题样本',
  PATCH_AI_RUNTIME_CONFIG: '更新智能服务运行策略',
  PUBLISH_ANNOUNCEMENT: '发布全站系统公告',
  DELETE_ANNOUNCEMENT: '撤回删除系统公告',
  UPDATE_SUPPORT_TICKET: '分派与回复反馈工单',
  CREATE_COURSE: '新建教学课程',
  UPDATE_COURSE: '修改课程信息与状态',
  UPLOAD_COURSE_COVER: '上传课程封面图片',
  CREATE_CLASS_GROUP: '新建课程教学班',
  IMPORT_COURSE_STUDENTS: '批量导入班级学员',
  XLSX_IMPORT_COURSE_STUDENTS: '表格导入班级花名册',
  UPDATE_ENROLLMENT: '调整学员分班与小组',
  REMOVE_ENROLLMENT: '移出课程选课学员',
  UPLOAD_VIDEO_TASK: '登记视频并启动章节化',
  UPLOAD_REAL_VIDEO_FILE: '上传视频文件并启动章节化',
  COMPLETE_CHUNK_VIDEO_UPLOAD: '完成大视频分片合并上传',
  SAVE_CALIBRATED_OUTLINE: '保存人工校准章节提纲',
  PUBLISH_OUTLINE_VERSION: '审核发布视频章节提纲',
  REGENERATE_SINGLE_CHAPTER: '单章节智能重新生成',
  UPLOAD_COURSE_DOCUMENT: '在线发布课程讲义',
  UPLOAD_REAL_DOCUMENT_FILE: '上传并解析课程讲义文件',
  DELETE_COURSE_DOCUMENT: '删除课程讲义资料',
  CREATE_MANUAL_QUESTION: '手动录入题库试题',
  AI_GENERATE_QUESTIONS: '批量生成候选试题',
  REVIEW_QUESTION: '审核与校对题库试题',
  DELETE_QUESTION: '删除题库试题',
  CREATE_EXAM_PAPER: '组卷发布正式测验',
  DELETE_EXAM_PAPER: '删除已发布试卷',
  RESOLVE_UNANSWERED_QUESTION: '解答疑问并回流知识库',
  UPDATE_TUTOR_CONFIG: '更新课程助教行为策略',
  CREATE_COURSE_FAQ: '新增课程精选问答',
  UPDATE_COURSE_FAQ: '编辑课程精选问答',
  DELETE_COURSE_FAQ: '删除课程精选问答',
  SUBMIT_TEACHER_QUALITY_FEEDBACK: '教师上报生成质量反馈',
}

const TARGET_TYPE_LABELS = {
  user: '用户账号',
  organization: '组织架构',
  role_permission: '角色权限',
  role_permissions: '角色权限',
  course: '课程',
  course_category: '课程分类',
  class_group: '教学班级',
  enrollment: '选课分班',
  video_resource: '授课视频',
  outline_version: '章节提纲版本',
  course_document: '课程讲义',
  question_bank_item: '题库试题',
  exam_paper: '测验试卷',
  unanswered_question: '待解答疑问',
  course_faq: '精选问答',
  ai_job: '智能异步任务',
  ai_service: '智能助教服务',
  quality_sample: '质量问题样本',
  announcement: '系统公告',
  support_ticket: '反馈工单',
}

const SOURCE_TYPE_LABELS = {
  document: '课程讲义',
  video_transcript: '授课视频字幕',
  teacher_qa: '教师权威解答',
  manual: '教师手工命题',
  ai: '智能辅助生成',
  ai_generated: '智能自动生成',
  teacher_calibrated: '教师人工精修',
}

export function formatStatus(val) {
  if (!val) return '未知状态'
  return STATUS_LABELS[val] || val
}

export function formatRole(val) {
  if (!val) return '未知角色'
  return ROLE_LABELS[val] || val
}

export function formatOrgType(val) {
  if (!val) return '组织节点'
  return ORG_TYPE_LABELS[val] || val
}

export function formatQuestionType(val) {
  if (!val) return '单选题'
  return QUESTION_TYPE_LABELS[val] || val
}

export function formatDifficulty(val) {
  if (!val) return '中等'
  return DIFFICULTY_LABELS[val] || val
}

export function formatJobType(val) {
  if (!val) return '智能处理任务'
  return JOB_TYPE_LABELS[val] || val
}

export function formatCapability(val) {
  if (!val) return '智能推理能力'
  return CAPABILITY_LABELS[val] || val
}

export function formatSampleType(val) {
  if (!val) return '智能助教回答'
  return SAMPLE_TYPE_LABELS[val] || val
}

export function formatSourceReason(val) {
  if (!val) return '人工抽检'
  return SOURCE_REASON_LABELS[val] || val
}

export function formatProvider(val) {
  if (!val) return '本地离线混合引擎'
  if (PROVIDER_LABELS[val]) return PROVIDER_LABELS[val]
  if (String(val).includes('local')) return '本地离线混合引擎'
  return val
}

export function formatDialect(val) {
  if (!val) return '关系型数据库'
  return DIALECT_LABELS[String(val).toLowerCase()] || val
}

export function formatStrategyKey(val) {
  if (!val) return '智能策略'
  return STRATEGY_KEY_LABELS[val] || val
}

export function formatStrategyName(val) {
  if (!val) return '标准基准策略'
  return STRATEGY_NAME_LABELS[val] || val
}

export function formatAuditAction(val) {
  if (!val) return '系统操作'
  return AUDIT_ACTION_LABELS[val] || val
}

export function formatTargetType(val) {
  if (!val) return '业务对象'
  return TARGET_TYPE_LABELS[val] || val
}

const PERMISSION_KEY_LABELS = {
  'course:browse': '浏览课程目录',
  'course:browse_enroll': '浏览与选修上架课程',
  'course:enroll': '选修上架课程',
  'video:watch': '观看课程视频与字幕',
  'note:manage': '记录与管理时间戳笔记',
  'tutor:ask': '向智能助教提问答疑',
  'tutor:ask_rag': '调用课程智能助教问答与资料溯源',
  'quiz:participate': '参加随堂测验与正式考试',
  'quiz:self_practice': '发起章节自测、错题重做与变式练习',
  'wrong_book:manage': '使用错题本复盘与重练',
  'ticket:submit': '提交平台意见与反馈工单',
  'course:manage': '创建课程与管理教学资源',
  'studio:manage_course': '创建课程、上传音视频讲义与班级管理',
  'class:manage': '创建教学班与管理学员分组',
  'outline:calibrate': '人工校准与发布视频章节提纲',
  'studio:calibrate_outline': '校准发布视频提纲与审阅题库组卷',
  'question:review': '智能批量命题与题库审校',
  'exam:publish': '选题组卷与发布测验试卷',
  'analytics:view': '查看班级学情与提问热点看板',
  'admin:governance': '课程上架审核与违规内容下架',
  'admin:govern_platform': '全站账号审批、智能监控抽检与合规下架',
  'admin:users': '全站账号、角色与组织架构管理',
  'admin:ai_ops': '智能任务监控与知识库索引运维',
  'admin:quality': '生成内容抽检、样本回流与安全配置',
  'admin:reports': '全站运营数据看板与报表导出',
  'admin:audit': '系统公告、工单分派与操作审计查询',
}

export function formatPermissionKey(val) {
  if (!val) return '系统权限项'
  return PERMISSION_KEY_LABELS[val] || val
}

export function formatSourceType(val) {
  if (!val) return '课程资料'
  return SOURCE_TYPE_LABELS[val] || val
}
