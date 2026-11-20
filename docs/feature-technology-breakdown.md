# 智能学习平台 (v1.0) —— 全功能技术实现拆解文档

本文档逐一列出《智能学习网站项目 —— 技术与合作方案 (v1.0)》中**契约层、高校 AI 团队服务、学生端、教师端、管理员六大治理域**每一个具体功能背后所采用的技术方案、核心算法/数据结构以及对应源码位置，便于技术评审、答辩汇报与后续二次开发。

---

## 一、总体技术栈速览

| 分层 | 核心技术选型 | 选型工程考量 |
| :--- | :--- | :--- |
| **展现层 (`platform-frontend`)** | **Vue 3.5 (`<script setup>` Composition API)** + **Vite 5** + **Pinia** + **Vue Router 4** + **原生 CSS Design Tokens** | 零重型 UI 库历史包袱，单文件组件职责清晰，构建产物体积小（核心包 gzip ~44KB），人类开发者极易上手修改样式与交互 |
| **业务层 (`platform-backend`)** | **Python 3.12** + **FastAPI** + **SQLAlchemy 2.0 (Typed ORM)** + **Pydantic v2** + **SQLite（默认零配置）/ PostgreSQL（生产就绪）** | 强类型 DTO 校验、清晰四层架构（`api -> services -> clients -> models`），一条环境变量 `DATABASE_URL` 即可平滑切换生产级关系库 |
| **契约层 (`contracts`)** | **OpenAPI 3.1.0 (YAML)** + **Python 标准库 `http.server` 零依赖 Mock Server** | 双方唯一耦合边界；即使未安装任何第三方包也能秒级拉起契约 Mock 供前端与业务后端联调 |
| **AI 能力层 (`ai-tutor-service`)** | **FastAPI** + **`typing.Protocol` 策略模式** + **中英文混合 TF-IDF/Bigram 检索引擎** + **`httpx` 多模型异步适配器（Local / OpenAI 兼容 / Gemini）** | 严格无业务状态；默认内置确定性本地混合引擎（无需外部 Key 即可离线跑通全流程），同时支持一行配置热切换云端大模型或挂载 V2 模块 |
| **安全与可观测** | **PBKDF2-HMAC-SHA256 密码哈希** + **HS256 JWT + RBAC** + **双密钥常量时间校验 (`X-Service-Key` / `X-Ops-Key`)** + **Webhook HMAC-SHA256 验签** + **`X-Request-ID` 全链路追踪** | 零平台编译依赖，完整覆盖端到端认证、跨服务双平面鉴权与异步回调防伪造/防重放 |
| **质量与交付** | **Pytest** + **`httpx.ASGITransport` 双服务真实协议联调** + **Docker 多阶段构建** + **Nginx 1.27** + **Docker Compose** | 单条命令即可在内存完成跨服务全链路回归验证，容器镜像开箱即交付 |

---

## 二、契约层与跨团队解耦机制所用技术

| 解耦机制 / 功能点 | 所用具体技术与实现方式 | 关键源码位置 |
| :--- | :--- | :--- |
| **OpenAPI 契约冻结** | 采用 **OpenAPI 3.1.0 YAML** 明确定义路径、请求/响应 JSON Schema、鉴权 Scheme 与 Webhook 载荷；通过 **PyYAML** 在验收脚本中自动做语法完整性检查 | [ai-tutor-service.v1.openapi.yaml](../contracts/ai-tutor-service.v1.openapi.yaml)、[platform-backend.v1.openapi.yaml](../contracts/platform-backend.v1.openapi.yaml) |
| **零依赖契约 Mock Server** | 使用 Python 内置标准库 `http.server.HTTPServer` 与 `BaseHTTPRequestHandler` 实现轻量契约桩服务，模拟 `202 Accepted` 异步任务、RAG 引用与结构化试题返回 | [mock_ai_server.py](../contracts/mock_ai_server.py) |
| **统一响应信封与规范化异常处理** | 基于 FastAPI 全局异常处理器 `@app.exception_handler` 拦截自定义异常与 `RequestValidationError`（`422` 字段级错误明细），统一输出 `{data, meta}` 与 `{error}` 信封 | [ai-tutor-service/app/core/errors.py](../ai-tutor-service/app/core/errors.py)、[platform-backend/app/core/errors.py](../platform-backend/app/core/errors.py) |
| **全链路 Request-ID 追踪** | 基于 Starlette `@app.middleware("http")` 中间件读取或生成 `X-Request-ID`，挂载至 `request.state`，由 `AiServiceClient` 跨服务透传至 AI 服务与 Webhook 回调头 | [platform-backend/app/main.py](../platform-backend/app/main.py)、[ai_service_client.py](../platform-backend/app/clients/ai_service_client.py) |
| **双平面独立鉴权（业务 vs 运营）** | 使用 FastAPI 路由级依赖注入 `Depends(verify_service_key)` 与 `Depends(verify_ops_key)` 分别校验 `X-Service-Key` 和 `X-Ops-Key`，内部采用 `secrets.compare_digest` 常量时间比对防止时序侧信道攻击 | [ai-tutor-service/app/core/security.py](../ai-tutor-service/app/core/security.py) |
| **异步长任务协议与幂等控制** | 采用 `asyncio.create_task` 后台协程执行长任务，HTTP 立即返回 `202 Accepted` 与 `Location: /v1/jobs/{job_id}`；建立 `Idempotency-Key -> job_id` 内存+JSON 持久化映射表防止网络重试重复触发算法 | [job_manager.py](../ai-tutor-service/app/services/job_manager.py) |
| **Webhook 签名防伪造与消费幂等** | 发送端使用 `hmac.new(secret, f"{timestamp}.{raw_body}", hashlib.sha256)` 生成 `X-Webhook-Signature`；接收端验签后写入业务库 `WebhookReceipt` 主键表去重，确保重复回调绝对幂等 | [webhooks.py](../platform-backend/app/api/internal/webhooks.py) |
| **防腐层超时与优雅降级** | 工程端封装唯一 HTTP 客户端 `AiServiceClient`（基于 `httpx.AsyncClient` 设置超时阈值），捕获连接失败或 `5xx` 时自动返回 `degraded: true` 安全兜底结构，绝不拖垮主站 | [ai_service_client.py](../platform-backend/app/clients/ai_service_client.py) |

---

## 三、高校 AI 团队模块（`ai-tutor-service`）逐项功能技术拆解

| 需求编号与功能名称 | 核心实现技术与算法细节 | 关键源码位置 |
| :--- | :--- | :--- |
| **V1/V2 可插拔策略底座** | 使用 Python `typing.Protocol` 定义五大抽象策略协议（章节化、RAG、出题、推荐、提问聚类），通过 `StrategyRegistry` 结合运行时 `feature_flags` 动态解析实现类，V2 新算法无需改动路由层 | [interfaces.py](../ai-tutor-service/app/strategies/interfaces.py)、[registry.py](../ai-tutor-service/app/strategies/registry.py) |
| **多模型推理适配层** | `LlmProviderClient` 封装统一文本与结构化 JSON 生成接口：支持 `local`（本地确定性混合生成）、`openai`（通过 `httpx` 调用兼容 DeepSeek/Qwen/vLLM/OpenAI 的 `/chat/completions`）、`gemini`（调用 Gemini `generateContent` REST 接口），远端异常自动降级回本地引擎 | [llm_provider.py](../ai-tutor-service/app/providers/llm_provider.py) |
| **2c & 2d：视频自动语音识别 (ASR)、章节化提纲与秒级跳转节点** | 1. **云端 + 本地多源级联 ASR (`CascadingAsrClient`)**：通过 **PyAV (`av`)** 免外部 `ffmpeg.exe` 提取 16kHz 单声道音轨并探测真实时长；配置了云端 Key 时优先调用 **OpenAI Whisper API (`/audio/transcriptions`)** 或 **Gemini 多模态接口**，未配置 API Key（或云端异常）时自动回退至本地离线 **Faster-Whisper (`ctranslate2` int8 + Silero VAD 静音过滤，默认挂载 `hf-mirror.com` 国内加速镜像)**；<br>2. **语义时间窗聚合**：按语义组块切分为结构化章节并调用 LLM 提炼标题、内容提要与知识点；<br>3. **转写副产品自动入库**：自动将每章对应的秒级字幕写入课程知识库（携带 `video_id` 与 `start_sec` 元数据），支撑问答秒级溯源 | [asr_provider.py](../ai-tutor-service/app/providers/asr_provider.py)、[chapterizer.py](../ai-tutor-service/app/strategies/v1_default/chapterizer.py) |
| **2b：课程资料分块与课程隔离向量/混合索引** | 1. **语义自适应分块 (`_split_chunks`)**：优先按双换行段落与中文句末标点切分（默认窗口 320 字符），避免硬截断破坏公式上下文；<br>2. **中英文混合轻量分词 (`_tokenize_mixed`)**：同时提取英文/数字词元、中文字符二元组（CJK Bigram）与单字特征，兼顾专业缩写（如 `RoPE`、`d_k`）与中文短语召回；<br>3. **课程隔离检索 (`CourseKnowledgeStore`)**：按 `course_id` 独立存储文档字典与词频统计，基于平滑 IDF 加权余弦相似度排序，物理杜绝跨课程串味 | [vector_store.py](../ai-tutor-service/app/services/vector_store.py) |
| **3b：AI 助教 RAG 问答、边看边问与引导追问** | 1. **双向安全护栏**：前置执行全局敏感词匹配与教师课程级 `forbidden_topics` 拦截；<br>2. **边看边问上下文加权**：当携带 `video_context` 时，对命中当前 `chapter_id` (+0.14) 与 `video_id` (+0.08) 的知识切片动态提权，并对教师权威回流问答 `teacher_qa` (+0.12) 赋予最高优先级；<br>3. **低置信度拒答分流**：检索最高分低于 `confidence_threshold` 且开启严格本课范围时，返回 `needs_teacher_review=True` 驱动教师收件箱流转；<br>4. **快捷指令与追问**：支持 `explain_simply`（通俗类比）、`give_example`（实例拆解）、`summarize_key_points`（要点提炼）Prompt 切换，并生成 3 条引导追问 | [rag_engine.py](../ai-tutor-service/app/strategies/v1_default/rag_engine.py) |
| **3c & 3e：智能结构化出题与个性化薄弱点测验** | 1. **无状态画像注入**：在 `personalized_diagnostic` 模式下读取工程端请求体传入的 `weak_knowledge_points` 与近期错题题干作为第一优先命题靶点；<br>2. **知识锚定多题型合成**：结合课程知识库真实片段生成 `single_choice`（单选）、`multiple_choice`（多选）、`true_false`（判断）三类严格符合 Pydantic Schema 的 JSON 题目，附带答案、解析、难度与 `chapter_id` | [quiz_generator.py](../ai-tutor-service/app/strategies/v1_default/quiz_generator.py) |
| **3f：轻量个性化学习内容推荐** | 基于多因子可解释规则评分模型：基础分 `0.50` + 命中错题薄弱知识点 `+0.35`（标记“薄弱点攻坚”）+ 命中近期向助教提问词 `+0.20`（标记“疑问延伸”）+ 未完成章节 `+0.15`（标记“进度衔接”），按得分降序输出推荐单元与中文解释理由 | [recommender.py](../ai-tutor-service/app/strategies/v1_default/recommender.py) |
| **学情高频提问盲区聚类** | 过滤中文口语停用词后，优先向课程已知知识点对齐归类，未命中则基于英文术语正则与中文短语提取主题簇标签，按提问频次与未解答率排序并生成课堂补充教学建议 | [question_clusterer.py](../ai-tutor-service/app/strategies/v1_default/question_clusterer.py) |
| **AI 算力遥测、样本回收与热配置** | 采用 `time.perf_counter()` 统计每次推理毫秒级延迟，按模块聚合调用量、Token 消耗与估算算力成本（USD），持久化评测候选样本池与运行时安全/V2 开关配置 | [telemetry_store.py](../ai-tutor-service/app/services/telemetry_store.py) |

---

## 四、工程团队学生端逐项功能技术拆解

| 功能模块 | 前端实现技术 (`platform-frontend`) | 后端实现技术 (`platform-backend`) | 关键源码位置 |
| :--- | :--- | :--- | :--- |
| **1：多角色登录与 RBAC 访问控制** | Pinia `useAuthStore` 管理响应式登录态并同步 `localStorage`；Vue Router `router.beforeEach` 校验路由 `meta.roles`；顶栏内置三角色一键切换器方便演示验收 | `hashlib.pbkdf2_hmac('sha256', ..., 100_000)` 加盐哈希存储密码；标准 `HS256 JWT` 令牌签发与验签；FastAPI `require_roles` 依赖守卫 | [security.py](../platform-backend/app/core/security.py)、[auth.js](../platform-frontend/src/stores/auth.js) |
| **3a：视频播放 + 章节提纲联动跳转** | 原生 `<video>` 与模拟授课画布双模式兼容；通过 `computed` 实时计算 `currentSec` 命中的活跃章节高亮；自定义带蓝色章节锚点刻度的进度条，通过 `defineExpose({ seekTo })` 暴露秒级跳转接口 | 查询 `VideoResource` 关联的 `published_version_id` 对应 `OutlineVersion` JSON；对学生角色严格校验 `outline_status == "published"`，未发布返回 `403` | [VideoChapterPlayer.vue](../platform-frontend/src/components/VideoChapterPlayer.vue)、[student_service.py](../platform-backend/app/services/student_service.py) |
| **视频完整文字稿搜索与点击跳秒** | 基于 Vue `computed` 对 `transcript_segments` 做毫秒级本地关键词实时过滤，当前播放秒数所在句子自动高亮，点击任意句子调用 `seekTo(seg.start_sec)` | 复用 AI 章节化流水线产出的秒级对齐 `transcript_json` 字段随播放接口一次性下发 | [VideoChapterPlayer.vue](../platform-frontend/src/components/VideoChapterPlayer.vue) |
| **时间戳笔记与重点书签** | 输入笔记自动捕获播放器当前 `currentSec`，支持勾选“重点书签”星标徽章，点击笔记时间标签一键跳回对应视频位置 | SQLAlchemy `VideoNote` 实体持久化（按 `timestamp_sec` 升序索引查询），支持增删与按课程/视频过滤 | [student.py](../platform-backend/app/api/v1/student.py) |
| **边看边问 AI 助教、引用溯源跳回与反馈** | 播放器暂停提问时通过自定义事件将 `{video_id, chapter_id, chapter_title, timestamp_sec}` 实时传给助教侧栏；渲染置信度徽章、快捷指令芯片与出处卡片，点击视频出处自动跨视频切换并跳至对应秒数 | `TutorThread` / `TutorMessage` 持久化多轮会话；读取 `CourseTutorConfig` 注入课程回答策略；当学生点踩/举报时自动创建 `QualitySample` 并调用 `/v1/ops/feedback-samples` 推送至 AI 评测池 | [AiTutorPanel.vue](../platform-frontend/src/components/AiTutorPanel.vue)、[CourseStudyView.vue](../platform-frontend/src/views/student/CourseStudyView.vue) |
| **章节自测、组卷考试与个性化测验** | `QuizRunnerModal` 弹窗组件统一承载单选（单值覆盖）、多选（数组切换）、判断三种题型；交卷后原位展开标准答案、解析与“跳回关联章节复习”按钮 | 创建 `QuizSession` 快照，下发前端前脱敏剔除标准答案 `answer` 字段防止作弊；个性化测验自动查询 `WrongQuestionRecord` 与近期 `TutorMessage` 组装 `learner_profile` 传给 AI 服务 | [QuizRunnerModal.vue](../platform-frontend/src/components/QuizRunnerModal.vue)、[student_service.py](../platform-backend/app/services/student_service.py) |
| **3d：错题本自动归集与复盘管理** | 支持按知识点模糊检索与“仅看未掌握”过滤、在线编辑错因复盘心得、切换已掌握状态、一键基于错题启动靶向强化测验 | 交卷批改时对答错题目自动在 `WrongQuestionRecord` 中按知识点+题干幂等合并，重复答错自动递增 `wrong_count` 并重置 `is_mastered=False` | [WrongBookView.vue](../platform-frontend/src/views/student/WrongBookView.vue) |
| **个人学习仪表盘与智能推荐** | 响应式 4 宫格 KPI 卡片 + CSS 动态高度比例成绩柱状图 + 带匹配度与优先级标签的推荐跳转卡片 | SQLAlchemy `func.sum` 汇总 `LearningEvent` 学习时长、查询打卡天数与历次 `QuizSession` 得分曲线，实时调用 AI 推荐接口组装学习建议 | [StudentHubView.vue](../platform-frontend/src/views/student/StudentHubView.vue) |

---

## 五、工程团队教师端逐项功能技术拆解

| 功能模块 | 所用具体技术与实现方式 | 关键源码位置 |
| :--- | :--- | :--- |
| **2a & 2b：真实视频 (`.mp4`) 与多格式资料 (`.pdf`/`.docx`/`.pptx`/`.md`) 上传解析** | 1. **大文件流式落盘与静态 Range 流服务**：`storage_service.py` 按 256KB 分块流式写入本地对象存储目录 `.storage/`，通过 Starlette `StaticFiles` 挂载 `/media` 支持浏览器 `<video>` 拖拽进度条 (`206 Partial Content`)；<br>2. **多格式全文解析 (`file_parser_service.py`)**：集成 **pypdf**（逐页提取 PDF 正文并标注页码）、**python-docx**（提取 Word 段落与表格）、原生 `zipfile + ElementTree`（提取 `.pptx` 幻灯片文本）以及 `.srt/.vtt` 字幕时间轴解析，上传后同步写入课程专属 RAG 索引 | [file_parser_service.py](../platform-backend/app/services/file_parser_service.py)、[storage_service.py](../platform-backend/app/services/storage_service.py)、[CourseResourcesPanel.vue](../platform-frontend/src/views/teacher/panels/CourseResourcesPanel.vue) |
| **AI 提纲人工校准（拆/合/改）与版本历史回滚** | 1. **不可变多版本模型**：数据库设计 `OutlineVersion` 版本表（`version_no` 递增），每次 AI 生成或教师人工校准均新增快照而不覆盖旧版；<br>2. **前端结构化章节编辑器**：基于深拷贝 JSON 数组实现章节标题/摘要/起止秒数双向绑定编辑、时间中点二分**拆分章节**、相邻区间自动合并去重知识点**合并章节**；<br>3. **Human-in-the-Loop 发布门禁**：仅当教师点击“审核发布此版”更新 `VideoResource.published_version_id` 后，学生端才解锁可见，同时自动将该版章节字幕增量同步至 AI 知识库 | [OutlineVersion 模型](../platform-backend/app/models/entities.py)、[TeacherStudioView.vue](../platform-frontend/src/views/teacher/TeacherStudioView.vue) |
| **AI 批量命题、逐题审题与组卷发布** | 调用 AI 出题接口批量生成默认处于 `pending_review` 状态的 `QuestionBankItem`；教师可在前端直接修改题干与解析并一键标记 `approved`（采纳）或 `rejected`（淘汰），勾选已审题 ID 数组创建 `ExamPaper` 发布测验 | [teacher.py](../platform-backend/app/api/v1/teacher.py) |
| **未解答问题收件箱（一键回流知识库 + FAQ）** | 当学生提问触发 AI 低置信度（`needs_teacher_review=True`）时，后端自动写入 `UnansweredQuestion` 队列；教师填写权威解答提交后，后端自动调用 `PUT /v1/knowledge-bases/{course_id}/documents/teacher_qa_{id}` 写入向量库（带权威加权），并可选同步写入 `CourseFaq` 置顶展示，实现“AI 助教越用越懂这门课” | [resolve_unanswered_inbox_item](../platform-backend/app/services/teacher_service.py) |
| **助教行为边界配置** | `CourseTutorConfig` 持久化每门课独立的“仅限本课程资料”“是否允许超纲”“语气风格（严谨鼓励/苏格拉底启发/简明定义）”及“禁答话题词表”，每次学生提问时随请求体动态注入 AI 服务执行 | [teacher.py](../platform-backend/app/api/v1/teacher.py) |
| **学情看板（回看热力图 + 提问热点聚类 + 测验薄弱点）** | 1. **观看热力图**：对 `LearningEvent` 按 60 秒时间桶聚合计数，其中 `replay_segment`（反复回看）赋予 2 倍热度权重，前端按热度映射深浅色阶条；<br>2. **提问盲区聚类**：提取课程历史提问流调用 AI 聚类接口识别高频疑惑主题；<br>3. **班级薄弱点排行**：基于 `Counter` 聚合全班错题知识点频次 Top 6 | [build_teacher_course_analytics](../platform-backend/app/services/teacher_service.py) |

---

## 六、工程团队管理端（六大治理域）逐项功能技术拆解

| 管理域 | 所用具体技术与实现方式 | 关键源码位置 |
| :--- | :--- | :--- |
| **域 1：组织与账号权限** | `Organization` 采用 `parent_id` 自引用邻接表构建“院系 -> 年级 -> 教学班”树形层级；提供用户创建、密码哈希重置、账号启停用与教师资格审核接口 | [admin.py](../platform-backend/app/api/v1/admin.py) |
| **域 2：课程审核与敏感内容处置** | 课程上架审核状态机（`pending_review -> published / takedown`）与首页精品位 `is_featured` 开关；敏感资料下架时同步调用 `DELETE /v1/knowledge-bases/{course_id}/documents/{doc_id}` 立即清理 AI 向量库切片，防止 AI 继续引用违规内容 | [takedown_course_document](../platform-backend/app/services/admin_service.py) |
| **域 3：AI 生产监控台** | 严格遵守“不直连 AI 内部存储”原则，全部通过 `AiServiceClient` 携带独立 `X-Ops-Key` 代理调用 `/v1/ops/*`：实时拉取异步任务队列、对失败任务发起一键重试、监控各课程向量索引健康状态并触发全量重建、按能力维度展示平均延迟与估算费用 | [AdminConsoleView.vue](../platform-frontend/src/views/admin/AdminConsoleView.vue)、[ai_service_client.py](../platform-backend/app/clients/ai_service_client.py) |
| **域 4：AI 质量抽检与内容安全** | 汇总学生点踩举报与管理员人工抽检样本至 `QualitySample` 并同步至 AI 评测池；通过 `PATCH /v1/ops/runtime-config` 在线热更新问答输入/输出双向拦截敏感词列表、RAG 置信度阈值以及 V2 三大前沿方向（Harness / 个性化引擎 / RSI）特性开关 | [admin.py](../platform-backend/app/api/v1/admin.py) |
| **域 5：平台总览看板与 Excel 报表导出** | SQLAlchemy 多维聚合活跃人数、观看时长、AI 问答量、测验完成率，自动识别学习进度 `<20%` 的选课学生输出弃学预警；使用 Python 标准库 `csv` + `io.StringIO` 写入 **UTF-8 BOM (`\ufeff`)** 字节流，配合前端 `Blob` 下载，确保中文报表在 Windows Excel 中直接双击无乱码打开 | [export_platform_report_csv](../platform-backend/app/services/admin_service.py) |
| **域 6：系统运营与操作审计日志** | 支持按角色范围（全站/学生/教师）推送顶部横幅公告、跟踪办结师生反馈工单；通过 `record_audit` 统一记录所有教师与管理员关键写操作的操作人、角色、动作编码、目标对象与变更摘要，全程留痕可追溯 | [audit_helper.py](../platform-backend/app/services/audit_helper.py) |

---

## 七、自动化测试与容器化部署技术

1. **进程内零端口双服务真实 HTTP 契约联调 (`tests/test_platform_e2e.py`)**：
   - 利用 `httpx.ASGITransport(app=ai_app)` 将业务后端的 `AiServiceClient` 直接桥接到真实的 `ai-tutor-service` FastAPI 实例，在单个测试进程内完整走通 HTTP 序列化、双密钥校验、HMAC Webhook 签名验签与 SQLite 事务隔离，3 秒内即可验证全套跨团队业务流。
2. **多阶段容器化编排 (`docker-compose.yml`)**：
   - 后端与 AI 服务基于 `python:3.12-slim` 构建独立镜像；前端采用 `node:22-alpine` 编译静态产物后交由 `nginx:1.27-alpine` 托管并通过反向代理转发 `/api/` 请求，完美契合方案文档中“技术团队独立推送镜像、工程团队一键滚动更新”的交付模式。
