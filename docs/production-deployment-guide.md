# 智能学习平台 (Smart Learning Platform v1.0) — 商业生产服务器部署与运维手册

本文档面向商业生产上线场景（Linux 云服务器 / 高校或企业私有化集群），完整说明安全密钥初始化、PostgreSQL 16 + Docker Compose 生产编排、Nginx HTTPS 证书接入、GPU 算力加速、数据备份恢复与日常运维 SOP。

---

## 一、生产拓扑与安全隔离架构

生产编排（[docker-compose.yml](../docker-compose.yml)）默认划分为两个隔离网络平面，严格落实《技术与合作方案》与 [AGENTS.md](../AGENTS.md) 边界要求：

- **`public_edge`（公网接入平面）**：仅暴露 `platform-frontend`（Nginx 反向代理网关），对外监听 `80` / `443` 端口；自动拦截一切针对 `/internal/*` 的公网请求（返回 `403`）。
- **`backend_internal`（内网受控平面）**：承载 `postgres`（PostgreSQL 16 业务数据库）、`platform-backend`（工程业务中枢）与 `ai-tutor-service`（AI 助教与推理微服务）。
  - `postgres` 与 `ai-tutor-service` **不暴露任何宿主机公网端口**；
  - `ai-tutor-service` 与 `postgres` **零连接**，自身课程向量库（`knowledge_index.db`）、异步任务队列（`async_jobs.db`）与运营遥测（`ops_telemetry.db`）均以 SQLite WAL 模式持久化在独立卷 `ai_runtime_data`；
  - 所有容器均以非 root 系统账号（`uid=10001 appuser`）运行，并内置 `HEALTHCHECK` 探针与 `restart: unless-stopped` 故障自愈。

---

## 二、服务器软硬件建议配置

| 部署规模 | 推荐 CPU / 内存 | 磁盘存储 | 视频语音转写 (ASR) 与大模型建议 |
| :--- | :--- | :--- | :--- |
| **标准商业节点**（500~2000 活跃师生） | 8 vCPU / 16 GB RAM | 200 GB SSD（对象存储挂载卷） | 配置云端 API（OpenAI / DeepSeek / 通义千问 / Gemini），零本地算力压力 |
| **私有化离线/混合节点**（含本地语音识别） | 8~16 vCPU / 32 GB RAM（可选 1 张 NVIDIA T4/RTX 4090 GPU） | 500 GB NVMe SSD | 开启本地 `Faster-Whisper`（GPU `cuda` + `float16` 或 CPU `int8`）+ 私有化 vLLM 接口 |

前置环境：Ubuntu 22.04 / Debian 12 / Rocky Linux 9，已安装 **Docker Engine >= 24.0** 与 **Docker Compose Plugin v2**，以及 `python3`。

---

## 三、正式上线五步部署流程

### 第 1 步：拉取代码并生成 256-bit 生产级密钥配置

在服务器项目根目录执行内置初始化脚本（切勿手工使用弱口令）：

```bash
python3 scripts/init-prod-env.py \
  --domain https://learn.your-company.com \
  --admin-user admin \
  --admin-email ops@your-company.com
```

脚本将自动在根目录生成 `.env`（已配置 `.gitignore` 忽略），并在终端**一次性输出初始超级管理员随机强密码**（形如 `Adm!xxxxxxxxxxxxxx`）。请立即妥善记录。

> **关键安全校验机制**：两个后端在 `APP_ENV=production` 启动阶段均内置 `validate_production_security()` 强校验——若检测到默认密钥、密钥长度不足 24 位、`AI_SERVICE_KEY` 与 `AI_OPS_KEY` 相同、或 `CORS_ALLOWED_ORIGINS` 包含通配符 `*`，容器将立即拒绝启动并打印精确告警。

### 第 2 步：（按需）配置商用大模型与 Embedding API

编辑根目录 `.env`，接入企业采购的大模型服务（若留空则自动启用内置离线混合检索与本地 `Faster-Whisper`）：

- **接入 OpenAI / DeepSeek / 通义千问 DashScope / 私有化 vLLM（OpenAI 兼容协议）**：
  ```ini
  AI_PROVIDER=openai
  OPENAI_BASE_URL=https://api.openai.com/v1
  OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
  OPENAI_MODEL=gpt-4o-mini
  OPENAI_EMBEDDING_MODEL=text-embedding-3-small
  ASR_PROVIDER=auto
  ```
- **接入 Google Gemini**：
  ```ini
  AI_PROVIDER=gemini
  GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxx
  GEMINI_MODEL=gemini-2.5-flash
  GEMINI_EMBEDDING_MODEL=text-embedding-004
  ASR_PROVIDER=auto
  ```

### 第 3 步：（可选）启用 NVIDIA GPU 加速本地视频转写

若服务器配备 NVIDIA GPU 且已安装 `nvidia-container-toolkit`：
1. 修改 `.env` 中 `WHISPER_DEVICE=cuda`、`WHISPER_COMPUTE_TYPE=float16`、`WHISPER_MODEL_SIZE=medium`；
2. 取消 `docker-compose.yml` 中 `ai-tutor-service` 下的 `deploy.resources.reservations.devices` 注释。

### 第 4 步：构建并后台启动生产集群

```bash
docker compose up --build -d
docker compose ps
```

确认四个容器（`slp-postgres`、`slp-ai-tutor-service`、`slp-platform-backend`、`slp-platform-frontend`）状态均为 `Up (healthy)`。

### 第 5 步：首次登录安全加固与正式业务开通

1. 浏览器访问服务器域名，使用第 1 步生成的 `admin` 账号与初始强密码登录**平台管理后台**；
2. 点击顶部导航栏右上角 **「修改密码」**，更换为运维团队正式托管口令；
3. 在 **「1. 账号与组织管理」** 中创建院系/部门组织树，通过 **CSV 批量导入** 或让师生在登录页自助注册：
   - 学生自助注册后立即可选课学习；
   - 教师自助注册后默认处于**待资质审核**状态，需管理员在「账号与组织管理」点击 **「通过教师审核」** 后方可创建课程与上传教学资源；
4. 教师上传课程视频（支持 `.mp4`/`.webm`/`.mov`/`.mkv` 流式上传与实时进度条）及讲义（`.pdf`/`.docx`/`.pptx`/`.md`），校准并发布 AI 章节提纲，提交课程由管理员在 **「2. 课程上架与审核」** 发布上线。

---

## 四、HTTPS / SSL 证书配置指南

商业域名上线强烈建议启用 HTTPS（TLS 1.2/1.3）：

1. **云厂商负载均衡 / WAF 前置卸载（推荐）**：将阿里云 SLB / 腾讯云 CLB / Cloudflare / 宿主机 Caddy 绑定 SSL 证书并转发至本机 `HTTP_PORT`（如 `127.0.0.1:8080`），同时确保 `.env` 中 `CORS_ALLOWED_ORIGINS=https://learn.your-company.com`。
2. **容器 Nginx 直接挂载证书**：将全链证书 `fullchain.pem` 与私钥 `privkey.pem` 挂载至 `platform-frontend` 容器 `/etc/nginx/certs/`，并在 [platform-frontend/nginx.conf](../platform-frontend/nginx.conf) 中增加 `listen 443 ssl http2;` 及 `80 -> 443` 跳转。

---

## 五、生产数据备份、恢复与滚动升级 SOP

### 1. 核心持久化数据卷

| Docker Volume 名称 | 归属团队 | 存储内容 | 备份优先级 |
| :--- | :--- | :--- | :--- |
| `postgres_data` | 工程团队 | 全部账号权限、课程、章节提纲历史版本、题库、测验记录、错题本、工单与审计日志 | **最高（每日定时全备）** |
| `platform_media_data` | 工程团队 | 教师上传的原始课程视频文件、字幕与 PDF/Word/PPT 讲义原件 | **高（增量同步至对象存储/异地盘）** |
| `ai_runtime_data` | AI 团队 | 课程向量/词法混合索引库、异步任务队列日志、AI 用量遥测与问题样本池 | **中（支持从管理端一键重建索引）** |

### 2. PostgreSQL 在线热备份与恢复命令

```bash
# 在线逻辑热备（不中断线上服务）
docker exec slp-postgres pg_dump -U slp_user -Fc smart_learning > backup_$(date +%F).dump

# 灾难恢复
cat backup_2026-11-18.dump | docker exec -i slp-postgres pg_restore -U slp_user -d smart_learning --clean --if-exists
```

### 3. 零停机感滚动更新

```bash
git pull origin main
# 仅当某端升级时可单独重构建，例如仅热替换 AI 助教服务（工程业务零中断）：
docker compose up --build -d ai-tutor-service
# 或全量平滑滚动升级：
docker compose up --build -d
```
