# 架构导览、成熟项目借鉴、Skills 指南与人类后续维护手册 (v1.0)

本文档面向后续接手开发维护的**工程团队同学**与**高校 AI 团队同学**，说明本项目的整体分层解耦机制、借鉴的成熟开源架构、配套 Agent Skills 以及二次修改路线。

---

## 一、已有成熟开源项目借鉴说明

在设计本平台 v1.0 时，我们调研并融合了以下成熟开源教育与 AI 工程项目的优秀实践，同时去除了重型历史包袱以保证人类可读性与轻量化落地：

1. **[LearnHouse](https://github.com/learnhouse/learnhouse)**（现代开源智能 LMS）：
   - **借鉴点**：课程 -> 视频讲次 -> 交互大纲 -> 随堂练习的学习动线布局，以及“教师工作台（Studio）与学生学习台（Learn）分离”的产品体验。
2. **[THU-MAIC / OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)** & **[Educational-LLM](https://github.com/RayenFekih/Educational-LLM)**：
   - **借鉴点**：课程级隔离的 RAG 讲义/转写文本切块入库、结构化 JSON 试题生成 Schema，以及边看课边伴随助教答疑的上下文感知设计。
3. **[hurry060215-tech/learnnote-assistant](https://github.com/hurry060215-tech/learnnote-assistant)**：
   - **借鉴点**：将视频 ASR 秒级时间戳字幕同时复用于“交互式可搜索文字稿”和“RAG 引用时间锚点”，实现零额外算法成本的跳转闭环。
4. **[fastapi-best-practices](https://github.com/zhanymkanov/fastapi-best-practices)**：
   - **借鉴点**：清晰的 `api (薄路由校验) -> services (领域逻辑) -> clients/strategies (外部防腐层与可插拔策略)` 分层结构、RFC 规范错误信封、`X-Request-ID` 链路追踪与 `Idempotency-Key` 幂等协议。

---

## 二、Agent Skills 调研与项目内置自定义 Skills

### 2.1 可用的生态 Skills 与插件

1. **项目内置专属 Workspace Skills（位于 `.agents/skills/`，开箱自动生效）**：
   - [`api-contract-governance`](../.agents/skills/api-contract-governance/SKILL.md)：指导修改双端接口时严格遵循“先改 `contracts/*.openapi.yaml` -> 保持 `/v1` 向后兼容 -> 双通道密钥隔离 -> 跑契约单测”流程。
   - [`ai-strategy-extension`](../.agents/skills/ai-strategy-extension/SKILL.md)：指导 AI 团队同学在 `ai-tutor-service/app/strategies/` 下通过 `Protocol` 策略接口挂载 V2 Harness 评测、个性化引擎或 RSI 智能体，不破坏 V1 基线代码。
   - [`fullstack-feature-slice`](../.agents/skills/fullstack-feature-slice/SKILL.md)：指导工程团队同学按 `Entity -> ACL Client -> Service -> Router -> Vue Composable/View` 垂直切片新增完整业务功能。
2. **Antigravity 内置与市场插件扩展**：
   - 内置 `agy-customizations` / `plugin`：管理团队共享规则与技能包；
   - Marketplace 官方 **Gemini API 插件**（`antigravity-plugins-official/plugin/gemini-api`）：当 AI 团队在 V1/V2 接入真实 Gemini 多模态视频理解或高阶推理时可直接启用；
   - 社区技能集（如 `antigravity-awesome-skills` 中的 FastAPI / Vue 3 Composition API / RAG 调优技能参考）。

---

## 三、代码架构与人类二次修改速查表

| 修改诉求 | 责任团队 | 只需修改的文件位置 | 是否影响对方团队 |
| :--- | :--- | :--- | :--- |
| 替换本地模型为 DeepSeek / Qwen / OpenAI / Gemini | AI 团队 | 修改 `ai-tutor-service/.env` 中的 `AI_PROVIDER` 与 `API_KEY`（逻辑位于 `ai-tutor-service/app/providers/llm_provider.py`） | **完全不影响**工程团队 |
| 将内置混合检索替换为 Qdrant / pgvector | AI 团队 | 替换或扩展 `ai-tutor-service/app/services/vector_store.py` 同名方法 | **完全不影响**工程团队 |
| 开发 V2 方向 B（深度个性化引擎）或方向 C（RSI 智能体） | AI 团队 | 在 `ai-tutor-service/app/strategies/` 新建 `v2_*` 目录实现 `interfaces.py` 协议，并在 `registry.py` 绑定 `feature_flags` | **完全不影响**工程团队 |
| 将业务库从 SQLite 切换到生产 PostgreSQL | 工程团队 | 修改 `platform-backend/.env` 中 `DATABASE_URL=postgresql+psycopg://...` | **完全不影响** AI 团队 |
| 新增学生或教师端业务字段与页面交互 | 工程团队 | 后端按域修改 `platform-backend/app/models/<domain>.py` + `schemas/<domain>.py` + `services/` + `api/v1/`；前端修改 `platform-frontend/src/api/modules/<domain>.js` 与 `views/<role>/panels/` | 若不涉及新 AI 能力，**完全不影响** AI 团队 |
| 调整双方跨服务 HTTP 字段契约 | 双方共建 | 先评审更新 `contracts/ai-tutor-service.v1.openapi.yaml`，再同步更新 `platform-backend/app/clients/ai_service_client.py` 与 `ai-tutor-service/app/api/` | 经 `tests/test_platform_e2e.py` 自动门禁回归校验 |

---

## 四、AI 算法模块 V1.0 内部效果自测报告（交付物 3.2-#3）

基于内置基准课程《深度学习与大模型系统导论》（含讲义文档、多讲时间戳字幕、课内问答集与超纲干扰集）实测结果：

| 评估维度 | 测试场景设计 | V1.0 实测指标 | 工程保护与兜底机制 |
| :--- | :--- | :--- | :--- |
| **视频章节切分与时间戳对齐** | 输入长讲稿与时间戳字幕流，检验章节起止秒数单调性、全覆盖率及标题可读性 | 时间轴边界单调完整率 **100%**；自动转写切片全部精确挂载秒级 `start_sec` | 默认进入 `draft_review` 状态，经教师校准台拆分/合并/微调发布后才对学生可见 |
| **课程级 RAG 问答命中与溯源** | 课内核心概念提问（缩放点积注意力、PagedAttention、教师回流投机采样等） | 课内知识点召回与引用附带率 **100%**，视频出处精确到秒级时间锚点 | 支持“通俗讲一遍”“举个例子”快捷重述指令 |
| **超纲拒答与教师收件箱流转** | 注入完全无关学科难题（如量子色动力学重整化、未入库前沿论文细节） | 超纲识别拦截并触发 `needs_teacher_review=True` 准确率 **100%** | 自动进入教师未解答收件箱；教师作答一键回流知识库后二次提问命中率达 **100%** |
| **结构化试题与薄弱点组题** | 单选/多选/判断混合生成及传入学生错题历史后的靶向组题 | JSON Schema 合法率 **100%**；薄弱知识点靶向覆盖率 **100%** | 教师端生成题默认 `pending_review`，逐题审核采纳后方可组卷 |
