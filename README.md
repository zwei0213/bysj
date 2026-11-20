# 知心智学 · 智能学习平台 (v1.0 商业正式版)

本项目依据《智能学习网站项目 —— 技术与合作方案 (v1.0)》完成**工程团队**与**高校 AI 团队**的全部商业生产级交付物，遵循 **AI-as-a-Service（AI 能力服务化）**、**契约先行（Contract-First）** 与 **生产零演示污染** 工程准则，可直接部署于云服务器或私有化生产集群。

---

## 一、核心商业生产特性

1. **彻底物理隔离与契约解耦（AI-as-a-Service）**：
   - **工程团队 (`platform-backend/` + `platform-frontend/`)**：独占 PostgreSQL 生产业务数据库（用户/组织/RBAC、课程/班级、多版本章节提纲、题库/试卷/错题本、学习行为日志、时间戳笔记、工单与操作审计日志）与对象存储媒体流（支持 HTTP `206 Partial Content` 视频拖动秒开与上传进度流控）。
   - **高校 AI 团队 (`ai-tutor-service/`)**：独立无业务状态的推理微服务；内置 **SQLite WAL 持久化混合检索引擎（稠密 Embedding 向量 + BM25 词法混合排序）**、并发受控异步作业队列（进程重启断点自愈）与级联语音识别流水线（云端 Whisper/Gemini ASR -> 本地离线 `Faster-Whisper` 自动回退）；业务接口（`X-Service-Key`）与运营治理接口（`X-Ops-Key`）双平面密钥物理隔离。
   - **契约层 (`contracts/`)**：冻结版 OpenAPI 3.1 规范文档与契约联调 Mock Server。
2. **商业级认证、权限审批与安全加固**：
   - **零虚拟演示数据**：空库首次启动仅初始化根组织节点与唯一超级管理员（由 `scripts/init-prod-env.py` 生成 256-bit 随机强密钥与初始密码），启动阶段强制执行 `validate_production_security()` 拦截弱口令与非法 CORS 配置。
   - **完整账号生命周期**：支持门户自助注册（学生注册即用；教师注册默认进入**待管理员资质审核**队列）、JWT Access + Refresh Token 无感续期、滑动窗口登录防爆破限流、在线修改强密码、以及管理端 **CSV 批量导入师生账号**。
   - **容器与网关加固**：非 root 容器运行（`uid=10001`）、容器级健康检查、Nginx 公网屏蔽 `/internal/*` 内部回调路径、Webhook HMAC-SHA256 签名 + 300 秒时间戳防重放校验。
3. **Human-in-the-Loop（教师把控闭环）与 V2 可插拔策略预留**：
   - AI 生成的视频章节提纲与批量试题默认处于待审状态，经教师工作台校准/审核发布后才对学生可见；低置信度问题自动转入教师「未解答收件箱」，教师作答后一键回流课程向量知识库。
   - `ai-tutor-service/app/strategies/` 预留五大 `Protocol` 策略接口与 `FeatureFlags`，支持平滑挂载 V2 **Harness 评测体系**、**深度个性化引擎** 与 **RSI 自进化智能体**。

---

## 二、目录结构与核心文档导航

```text
├── AGENTS.md                                    # 双团队物理隔离红线与 API 架构守则
├── .agents/skills/                              # 3 个工程演进专属 Agent Skills
├── contracts/                                   # OpenAPI 3.1 正式契约文档 + 契约 Mock Server
├── ai-tutor-service/                            # 【AI 团队交付】独立 AI 助教与推理微服务
├── platform-backend/                            # 【工程团队交付】业务与六大治理域后端 (PostgreSQL / SQLAlchemy 2.0)
├── platform-frontend/                           # 【工程团队交付】商业门户、学生端、教师工作台与管理后台 (Vue 3 + Nginx)
├── tests/                                       # 生产安全校验、双端契约 E2E 与真实音视频/PDF/Word 全链路测试
├── scripts/                                     # 生产密钥生成工具 (init-prod-env.py)、本地启动与全量验收脚本
├── docs/
│   ├── production-deployment-guide.md           # ★ 商业服务器部署、HTTPS、GPU 加速与备份运维手册
│   ├── api-redesign-specification.md            # HTTP API 工程规范重构对照文档
│   ├── feature-technology-breakdown.md          # 全平台各模块技术选型与实现拆解
│   └── architecture-and-maintainers-guide.md    # 代码架构与后续二次开发维护指南
└── docker-compose.yml                           # PostgreSQL 16 + 双后端 + Nginx 生产级容器编排
```

---

## 三、部署与启动指南

### 1. 服务器 Docker Compose 商业生产部署（推荐）

完整服务器上线步骤、HTTPS 配置与 PostgreSQL 备份方案详见 **[docs/production-deployment-guide.md](./docs/production-deployment-guide.md)**：

```bash
# 1. 生成 256-bit 随机生产密钥与初始超级管理员强密码（请妥善保存终端输出的初始密码）
python3 scripts/init-prod-env.py --domain https://learn.your-company.com

# 2. 一键构建并启动 PostgreSQL 16 + AI 助教服务 + 业务后端 + Nginx 网关
docker compose up --build -d
```

### 2. Windows 本地商业模式联调启动

```powershell
# 自动生成本地生产级 .env 密钥（首次运行将在终端输出初始 admin 密码），并拉起三端服务
.\scripts\start-dev.ps1
```

启动后访问 `http://localhost:5173`：
- 使用终端打印的初始超级管理员账号（`admin`）登录管理后台，审批教师资质、批量导入账号或管理课程上架；
- 师生也可直接在门户登录页切换至 **「注册新账号」** 自助注册（教师注册后由管理员一键审核开通）。

### 3. 运行自动化生产验收套件

```powershell
.\scripts\verify-all.ps1
```
