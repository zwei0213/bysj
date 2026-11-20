# AGENTS.md — 智能学习平台 (Smart Learning Platform v1.0) 协作与架构守则

本仓库实现了《智能学习网站项目 —— 技术与合作方案 (v1.0)》中**工程团队**与**高校 AI 团队**的全部基础版交付物，遵循 **AI-as-a-Service（AI 能力服务化）** 与 **契约先行（Contract-First）** 架构原则。

## 1. 团队物理边界与解耦铁律（严禁打破）

1. **双服务物理隔离**：
   - `platform-backend/` 与 `platform-frontend/` 属于**工程团队**：独占业务数据库（用户、权限、课程、大纲版本、题库、学习行为历史、错题本、工单与审计日志）。
   - `ai-tutor-service/` 属于**AI 技术团队**：独占模型推理、ASR/章节切分流水线、课程级向量知识库、AI 异步任务队列与运营遥测指标。
2. **严禁跨库直连**：
   - `ai-tutor-service` **绝不读写**业务数据库，也不持久化学生的业务历史；个性化测验与推荐所需的学习历史一律由 `platform-backend` 在请求体中按需传入（保持 AI 服务无业务状态，随时可容器热替换）。
   - 管理端（域 3 生产监控、域 4 质量治理）**绝不直连** AI 内部存储，必须且仅能通过 `platform-backend/app/clients/ai_service_client.py` 调用 `/v1/ops/*` 独立鉴权接口。
3. **Human-in-the-Loop（教师校准原则）**：
   - AI 生成的视频章节提纲和批量试题默认处于 `draft` / `pending_review` 状态，必须经教师在工作台校准或审核发布（`published` / `approved`）后，才对学生端可见（学生自主触发的即时自测与个性化练习除外）。

## 2. API 工程规范守则

所有新增或修改的 HTTP 接口必须遵守 [docs/api-redesign-specification.md](./docs/api-redesign-specification.md)：
- **资源导向命名**：使用复数名词集合（如 `/v1/jobs`、`/v1/tutor/replies`、`/v1/assessments/generations`），禁止在 URL 路径中堆砌动词（如 `/ask`、`/rebuild`）。
- **版本前缀**：AI 服务统一挂载 `/v1/...`（业务）与 `/v1/ops/...`（运营）；工程后端统一挂载 `/api/v1/...`（前端调用）与 `/internal/v1/webhooks/...`（内部回调）。
- **统一响应信封**：
  - 成功：`{"data": <payload>, "meta": {"request_id": "...", "timestamp": "...", "pagination": ...}}`
  - 失败：`{"error": {"code": "UPPER_SNAKE_CODE", "message": "...", "details": [...], "request_id": "..."}}`
- **异步长任务协议**：耗时任务统一返回 `202 Accepted` 及 `Location` 响应头，支持 `Idempotency-Key` 幂等控制与 `X-Webhook-Signature` (HMAC-SHA256) 回调验签。
- **全链路追踪**：所有跨服务调用必须透传 `X-Request-ID`。

## 3. V2 演进策略接口约定

修改或升级 AI 算法时，优先在 `ai-tutor-service/app/strategies/` 下新增策略实现类并通过 `FeatureFlags` 切换，不要硬编码覆盖 `v1_default` 实现，确保随时可回退对比。
