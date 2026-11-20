# API 工程规范重构设计说明 (v1.0)

本文档针对《智能学习网站项目 —— 技术与合作方案 (v1.0 草案)》中的示例接口进行系统性工程规范重构，作为**工程团队**与**高校 AI 团队**共同冻结的 M0 契约基准。

---

## 一、原草案示例接口存在的问题诊断

原方案草案聚焦于职责划分与业务流说明，其示意性路径在直接落地生产工程时存在以下反模式：

1. **RPC 动词混入资源路径**：如 `POST /v1/tutor/ask`、`POST /v1/tasks/video-chapterize`、`POST /v1/admin/index/rebuild`、`GET /v1/admin/index/status`，违反 RESTful 资源导向命名原则，导致后续扩展同类任务或子操作时路由碎片化。
2. **同类能力接口割裂**：普通智能出题（需求 `3c`）与个性化薄弱点出题（需求 `3e` `POST /v1/quiz/personalized`）底层共享结构化试题 Schema 与校验管线，拆成形容词路径不利于统一维护。
3. **回调接口缺乏版本化与安全防重**：草案中的 `POST /api/callback/task-result` 缺少版本号、外部与内部命名空间隔离、HMAC 签名防伪造及幂等键（重试投递易引发重复写库）。
4. **鉴权平面未严格物理拆分**：草案附录明确要求“AI 运营接口与普通业务接口并列、独立鉴权”，但路由前缀 `/v1/admin/*` 易与网站业务后台 `/api/v1/admin/*` 混淆。
5. **缺少统一响应信封、错误码规范、全链路追踪与降级标识**：未定义标准分页结构、`X-Request-ID` 透传规范及模型超时兜底字段（`degraded`）。

---

## 二、原草案接口 vs 重构后工程规范接口对照表

### 2.1 AI 助教服务契约接口（`ai-tutor-service`）

| 场景 | 原草案示意接口 | 重构后规范接口 | 重构工程理由 |
| :--- | :--- | :--- | :--- |
| 视频章节化长任务提交 | `POST /v1/tasks/video-chapterize` | `POST /v1/jobs` (`job_type="video_chapterization"`) | 统一异步作业资源池，支持 `Idempotency-Key`，返回 `202 Accepted` + `Location: /v1/jobs/{job_id}`，未来新增文档解析/批量评测任务无需改协议 |
| 异步任务轮询查询 | （草案仅文字提及轮询） | `GET /v1/jobs/{job_id}` | 标准单资源查询，统一返回进度百分比、状态机、时间戳章节与完整文字稿 |
| 课程资料/问答知识入库 | （草案文字提及建立索引） | `PUT /v1/knowledge-bases/{course_id}/documents/{document_id}` | 天然幂等（Upsert），同时覆盖“教师上传文字资料入库”“视频转写稿入库”“未解答收件箱一键回流知识库”三大场景 |
| 敏感/下架资料移除 | （草案域 2 提及敏感内容下架） | `DELETE /v1/knowledge-bases/{course_id}/documents/{document_id}` | 支持管理端下架资料时同步清理课程向量库切片，杜绝 AI 继续引用违规内容 |
| AI 助教 RAG 问答 & 边看边问 | `POST /v1/tutor/ask` | `POST /v1/tutor/replies` | 名词化资源创建；统一承载课程问答、视频秒级时间戳上下文（边看边问）、快捷追问动作、置信度与出处引用列表 |
| AI 出题 & 个性化薄弱点测验 | `POST /v1/quiz/personalized` 等 | `POST /v1/assessments/generations` | 通过 `mode`（`teacher_bank` / `chapter_practice` / `personalized_diagnostic`）与无状态 `learner_profile` 统一出题契约 |
| 学习内容轻量推荐 | （需求 `3f`） | `POST /v1/recommendations/generations` | 工程端传入候选章节/资料与学生近期行为，AI 服务无状态输出排序结果与可解释推荐理由 |
| 学情高频问题盲区聚类 | （需求教师端学情看板） | `POST /v1/analytics/question-clusters` | 工程端传入匿名化课程提问列表，AI 服务返回高频主题簇与代表性问题 |
| 运营：查询 AI 任务队列 | `GET /v1/admin/tasks?status=failed` | `GET /v1/ops/jobs` | 归入独立 `/v1/ops` 治理平面（使用独立 `X-Ops-Key` 鉴权），支持 `status`/`course_id`/`job_type` 组合过滤与标准分页 |
| 运营：失败任务一键重试 | `POST /v1/admin/tasks/{task_id}/retry` | `POST /v1/ops/jobs/{job_id}/retries` | 将重试建模为创建子资源 `retries`，返回 `202 Accepted` 并保留重试计数轨迹 |
| 运营：查询知识库索引健康度 | `GET /v1/admin/index/status` | `GET /v1/ops/knowledge-bases` & `GET /v1/ops/knowledge-bases/{course_id}` | 去除 `/status` 动词，直接查询知识库资源集合及各课程索引健康状态机（`unindexed`/`indexing`/`ready`/`stale`/`failed`） |
| 运营：重建课程索引 | `POST /v1/admin/index/rebuild` | `POST /v1/ops/knowledge-bases/{course_id}/rebuilds` | 路径显式绑定 `{course_id}`，避免误操作全库，返回 `202 Accepted` 异步任务 |
| 运营：用量与算力成本统计 | `GET /v1/admin/metrics/usage` | `GET /v1/ops/metrics/usage` | 统一挂载至 `/v1/ops`，输出按能力维度的调用量、平均耗时、Token 与估算成本 |
| 运营：问题样本回传与抽检池 | `POST /v1/admin/feedback/samples` | `POST /v1/ops/feedback-samples` & `GET /v1/ops/feedback-samples` | Keba-case 资源集合命名，支持工程端推送点踩/举报/抽检样本并查询评测候选池 |
| 运营：服务配置与特性开关 | `GET / PUT /v1/admin/config` | `GET / PATCH /v1/ops/runtime-config` | 使用 `PATCH` 支持局部热更新（敏感词过滤规则、V2 特性开关、默认模型策略），避免全量覆盖误删配置 |

### 2.2 工程后端内部回调与前端业务接口（`platform-backend`）

| 平面 | 路径前缀 | 鉴权机制 | 代表性核心资源 |
| :--- | :--- | :--- | :--- |
| 内部回调平面 | `/internal/v1/webhooks/ai-jobs` | `X-Webhook-Signature` (HMAC-SHA256) + `X-Webhook-Timestamp` + `Idempotency-Key` | 接收 AI 服务异步章节化/索引任务完成通知，幂等更新业务库与章节草稿版本 |
| 认证与当前用户 | `/api/v1/auth/tokens`, `/api/v1/users/me` | Bearer JWT | 登录签发令牌、获取当前登录角色与权限信息 |
| 公共与学生学习域 | `/api/v1/courses/*`, `/api/v1/tutor-threads/*`, `/api/v1/quiz-sessions/*`, `/api/v1/users/me/*` | Bearer JWT (`student` / `teacher` / `admin`) | 选课、视频已发布章节与文字稿、边看边问对话、章节自测/个性化测验、错题本、时间戳笔记、个人学习仪表盘与推荐 |
| 教师教学与校准域 | `/api/v1/teacher/courses/*`, `/api/v1/teacher/videos/*` | Bearer JWT (`teacher` / `admin`) | 课程与班级管理、视频/文档上传与 AI 任务触发、大纲多版本人工校准（拆/合/改/回滚/发布）、AI 批量出题与逐题审题组卷、未解答问题收件箱（一键回流知识库）、助教策略配置、学情看板 |
| 管理与 AI 治理域 | `/api/v1/admin/*` | Bearer JWT (`admin`) | 域 1 组织与账号权限、域 2 课程审核上下架、域 3 AI 生产监控台（代理 `/v1/ops`）、域 4 AI 质量抽检与问题样本队列、域 5 平台数据看板与报表导出、域 6 全站公告/反馈工单/操作审计日志 |

---

## 三、统一协议细节规范

### 3.1 统一响应信封（Envelope）

**成功响应 (`200` / `201` / `202`)**：
```json
{
  "data": { "id": "crs_deep_learning", "title": "深度学习与大模型导论" },
  "meta": {
    "request_id": "req_9f82c1d4a7b3",
    "timestamp": "2026-11-18T09:30:00Z",
    "pagination": {
      "page": 1,
      "page_size": 20,
      "total": 42,
      "total_pages": 3
    }
  }
}
```
> 注：非列表接口 `pagination` 字段省略；`204 No Content` 无响应体。

**错误响应 (`4xx` / `5xx`)**：
```json
{
  "error": {
    "code": "OUTLINE_NOT_PUBLISHED",
    "message": "该视频章节提纲尚未经教师审核发布，学生端暂不可见",
    "details": [
      { "field": "video_id", "reason": "current status is draft_review" }
    ],
    "request_id": "req_9f82c1d4a7b3"
  }
}
```

### 3.2 HTTP 状态码约定

| 状态码 | 适用场景 |
| :--- | :--- |
| `200 OK` | 同步查询、更新、同步推理生成（如 RAG 问答返回、测验批改返回） |
| `201 Created` | 同步创建持久化业务实体（如新建课程、新建笔记、提交测验试卷） |
| `202 Accepted` | 提交异步长耗时任务（如视频章节化、索引重建、任务重试），响应头附带 `Location` |
| `400 Bad Request` | 业务前置条件不满足或非法状态流转 |
| `401 Unauthorized` | 缺少凭证、JWT 过期、或 `X-Service-Key` / `X-Ops-Key` / Webhook 签名无效 |
| `403 Forbidden` | 已认证但角色权限不足（如学生访问教师校准接口）或非本课程归属教师 |
| `404 Not Found` | 目标资源不存在 |
| `409 Conflict` | 资源唯一键冲突或并发状态冲突（如重复选课、正在构建索引时重复强制锁定） |
| `422 Unprocessable Entity` | 请求 JSON 字段类型或格式校验失败（附带字段级 `details`） |
| `429 Too Many Requests` | 触发角色调用配额或接口限流 |
| `503 Service Unavailable` | 下游依赖不可用且无法启用本地降级兜底时返回 |

### 3.3 幂等性、超时与优雅降级约定

1. **幂等键 (`Idempotency-Key`)**：`POST /v1/jobs` 与 `POST /internal/v1/webhooks/ai-jobs` 支持 `Idempotency-Key` 请求头；24 小时内相同键重复请求直接返回首次受理结果，不重复执行算法或重复写库。
2. **明确超时与降级 (`degraded` 标记)**：
   - `POST /v1/tutor/replies`：建议调用方超时 `15s`；当知识库检索置信度低于阈值（默认 `0.45`）或触发超纲限制时，返回 `needs_teacher_review: true`，工程后端自动将该问题转入教师端“未解答问题收件箱”。
   - 若外部大模型服务抖动，AI 服务自动切换本地确定性检索摘要兜底并标记 `"degraded": true`；若整个 AI 容器不可达，工程后端 `AiServiceClient` 捕获网络异常并返回友好兜底提示，绝不阻断学生正常看课、记笔记与做题库已有试题。
