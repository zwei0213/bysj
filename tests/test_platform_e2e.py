"""
商业生产模式端到端全链路契约联调测试 (Engineering Platform Backend <-> AI Tutor Service)。
通过 httpx.ASGITransport 将工程后端的 AiServiceClient 直接挂载到真实 AI 助教服务 ASGI 实例，
在 `APP_ENV=production` 空库冷启动条件下完整验证：
1. 空库仅初始化唯一超级管理员（零虚拟演示数据）、门户自助注册、教师资质待审拦截与管理员开通闭环；
2. Refresh Token 令牌轮换续期、用户自主修改强密码、管理员 CSV 批量导入师生账号；
3. 三角色 RBAC 权限隔离与 Human-in-the-Loop 未发布内容保护；
4. 场景 A：视频登记 -> AI 异步章节化 -> 教师人工校准版本演进 -> 发布前对学生隐藏 / 发布后秒级跳转可见；
5. 签名 Webhook 验签（含时间戳防重放）与 Idempotency-Key 防重幂等；
6. 场景 B：边看边问 RAG 引用溯源 + 低置信度问题自动转入教师收件箱 + 教师一键回流知识库后二次提问命中闭环；
7. 场景 C：AI 批量出题与教师审题 + 个性化薄弱点测验 + 错题本自动归集 + 轻量学习推荐；
8. 管理端六大域治理（课程审核上架、AI 索引重建、运营总览、CSV 报表导出与审计日志留痕）。
"""
import importlib
import json
import os
from pathlib import Path
import sys
import time
from fastapi.testclient import TestClient
import httpx

ROOT = Path(__file__).resolve().parents[1]

PROD_JWT_SECRET = "jwt_commercial_e2e_secret_0123456789abcdef"
PROD_SVC_KEY = "aisvc_commercial_e2e_secret_0123456789abcdef"
PROD_OPS_KEY = "aiops_commercial_e2e_secret_0123456789abcdef"
PROD_WH_SECRET = "whsec_commercial_e2e_secret_0123456789abcdef"
ADMIN_BOOTSTRAP_PWD = "AdminProd!2026Safe"


def _load_isolated_apps(tmp_path: Path):
    """在独立命名空间分别以 APP_ENV=production 加载 AI 服务与工程后端。"""
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name.startswith("app."):
            sys.modules.pop(mod_name, None)

    ai_dir = str(ROOT / "ai-tutor-service")
    if ai_dir in sys.path:
        sys.path.remove(ai_dir)
    sys.path.insert(0, ai_dir)

    ai_cfg = importlib.import_module("app.core.config")
    ai_cfg.settings.app_env = "production"
    ai_cfg.settings.ai_service_key = PROD_SVC_KEY
    ai_cfg.settings.ai_ops_key = PROD_OPS_KEY
    ai_cfg.settings.webhook_signing_secret = PROD_WH_SECRET
    ai_cfg.settings.data_dir = str(tmp_path / "ai_data")

    ai_main = importlib.import_module("app.main")
    ai_app = ai_main.create_app()
    ai_sec = importlib.import_module("app.core.security")
    sign_webhook_fn = ai_sec.sign_webhook_payload

    sys.path.remove(ai_dir)
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name.startswith("app."):
            sys.modules.pop(mod_name, None)

    plat_dir = str(ROOT / "platform-backend")
    if plat_dir in sys.path:
        sys.path.remove(plat_dir)
    sys.path.insert(0, plat_dir)

    db_url = f"sqlite:///{(tmp_path / 'prod_platform.db').as_posix()}"
    os.environ["DATABASE_URL"] = db_url

    plat_cfg = importlib.import_module("app.core.config")
    plat_cfg.settings.app_env = "production"
    plat_cfg.settings.database_url = db_url
    plat_cfg.settings.storage_dir = str(tmp_path / "storage")
    plat_cfg.settings.jwt_secret = PROD_JWT_SECRET
    plat_cfg.settings.ai_service_key = PROD_SVC_KEY
    plat_cfg.settings.ai_ops_key = PROD_OPS_KEY
    plat_cfg.settings.webhook_signing_secret = PROD_WH_SECRET
    plat_cfg.settings.bootstrap_admin_username = "admin"
    plat_cfg.settings.bootstrap_admin_password = ADMIN_BOOTSTRAP_PWD
    plat_cfg.settings.cors_allowed_origins = "https://learn.enterprise.cn"

    plat_main = importlib.import_module("app.main")
    plat_client_mod = importlib.import_module("app.clients.ai_service_client")
    plat_client_mod.ai_client.set_transport(httpx.ASGITransport(app=ai_app))
    platform_app = plat_main.create_app()

    return platform_app, sign_webhook_fn


def _login(client: TestClient, username: str, password: str) -> tuple[dict[str, str], dict]:
    resp = client.post("/api/v1/auth/tokens", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    headers = {"Authorization": f"Bearer {data['access_token']}", "X-Request-ID": f"e2e_{username}"}
    return headers, data


def test_commercial_production_end_to_end_workflows(tmp_path: Path):
    platform_app, sign_webhook_fn = _load_isolated_apps(tmp_path)

    with TestClient(platform_app) as client:
        # -----------------------------------------------------
        # 0. 验证商业空库冷启动：未登录拒绝访问，登录后无任何虚拟课程，仅存在初始化管理员
        # -----------------------------------------------------
        assert client.get("/api/v1/courses").status_code == 401

        adm_h, _ = _login(client, "admin", ADMIN_BOOTSTRAP_PWD)
        empty_catalog = client.get("/api/v1/courses", headers=adm_h)
        assert empty_catalog.status_code == 200
        assert empty_catalog.json()["data"]["items"] == []

        # 门户自助注册教师账号 -> 默认处于待管理员审核状态，拦截教师工作台写操作
        reg_tea = client.post(
            "/api/v1/auth/registrations",
            json={
                "username": "prof_zhang",
                "password": "Teacher#2026Strong",
                "display_name": "张教授",
                "email": "zhang@university.edu.cn",
                "role": "teacher",
            },
        )
        assert reg_tea.status_code == 201, reg_tea.text
        tea_data = reg_tea.json()["data"]
        tea_id = tea_data["user"]["id"]
        assert tea_data["user"]["teacher_approved"] is False
        tea_h = {"Authorization": f"Bearer {tea_data['access_token']}", "X-Request-ID": "e2e_teacher"}

        pending_block = client.get("/api/v1/teacher/courses", headers=tea_h)
        assert pending_block.status_code == 403
        assert pending_block.json()["error"]["code"] == "TEACHER_PENDING_APPROVAL"

        # 管理员审核通过教师资质
        approve_tea = client.patch(
            f"/api/v1/admin/users/{tea_id}",
            headers=adm_h,
            json={"teacher_approved": True},
        )
        assert approve_tea.status_code == 200
        assert approve_tea.json()["data"]["teacher_approved"] is True

        # 管理员 CSV 批量导入账号验证
        csv_import = client.post(
            "/api/v1/admin/users/import",
            headers=adm_h,
            json={
                "csv_text": "username,display_name,role,password,email\nbatch_stu_01,李同学,student,StuBatch#2026,li@edu.cn\nbatch_stu_02,王同学,student,StuBatch#2026,wang@edu.cn"
            },
        )
        assert csv_import.status_code == 201
        assert csv_import.json()["data"]["created_count"] == 2

        # 门户自助注册学生账号 + 验证 Refresh Token 轮换 + 修改密码闭环
        reg_stu = client.post(
            "/api/v1/auth/registrations",
            json={
                "username": "stu_chen",
                "password": "Student#2026Init",
                "display_name": "陈同学",
                "email": "chen@student.edu.cn",
                "role": "student",
            },
        )
        assert reg_stu.status_code == 201
        stu_tokens = reg_stu.json()["data"]
        refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": stu_tokens["refresh_token"]})
        assert refreshed.status_code == 200
        stu_h = {"Authorization": f"Bearer {refreshed.json()['data']['access_token']}", "X-Request-ID": "e2e_student"}

        chg_pwd = client.put(
            "/api/v1/users/me/password",
            headers=stu_h,
            json={"current_password": "Student#2026Init", "new_password": "Student#2026Updated"},
        )
        assert chg_pwd.status_code == 200
        assert client.post("/api/v1/auth/tokens", json={"username": "stu_chen", "password": "Student#2026Init"}).status_code == 401
        stu_h, _ = _login(client, "stu_chen", "Student#2026Updated")

        # 教师创建正式商业课程并录入核心讲义，管理员审核上架，学生选课
        create_crs = client.post(
            "/api/v1/teacher/courses",
            headers=tea_h,
            json={
                "code": "CS-LLM-2026",
                "title": "深度学习与大模型系统导论",
                "category": "人工智能与系统",
                "description": "面向商业生产的大模型训练与高性能推理系统课程",
                "submit_for_review": True,
            },
        )
        assert create_crs.status_code == 201, create_crs.text
        course_id = create_crs.json()["data"]["id"]

        pub_crs = client.patch(
            f"/api/v1/admin/courses/{course_id}/governance",
            headers=adm_h,
            json={"status": "published", "is_featured": True},
        )
        assert pub_crs.status_code == 200

        up_doc = client.post(
            f"/api/v1/teacher/courses/{course_id}/documents",
            headers=tea_h,
            json={
                "title": "第一章：注意力机制与 KV Cache 核心讲义",
                "file_format": "markdown",
                "content_text": (
                    "缩放点积注意力通过除以根号 dk 防止内积过大导致 Softmax 梯度消失。"
                    "在大模型自回归推理中，KV Cache 缓存历史键值张量消除重复计算，PagedAttention 通过分页块管理消除显存碎片。"
                ),
            },
        )
        assert up_doc.status_code == 201

        enroll = client.post(f"/api/v1/courses/{course_id}/enrollments", headers=stu_h, json={})
        assert enroll.status_code == 201

        # -----------------------------------------------------
        # 1. RBAC 严格权限校验
        # -----------------------------------------------------
        forbid = client.get("/api/v1/admin/overview", headers=stu_h)
        assert forbid.status_code == 403
        assert forbid.json()["error"]["code"] == "FORBIDDEN_ROLE"

        # -----------------------------------------------------
        # 2. 场景 A：教师上传视频 -> AI 自动切分章节 -> 教师人工校准发布 -> 学生端解锁
        # -----------------------------------------------------
        up_vid = client.post(
            f"/api/v1/teacher/courses/{course_id}/videos",
            headers=tea_h,
            json={
                "title": "第 1 讲：KV Cache 推理显存优化与分页管理",
                "duration_sec": 300,
                "raw_transcript_text": (
                    "自回归解码阶段每生成一个新词都需要复用历史键值向量。"
                    "如果不做缓存会导致重复计算，而预分配连续显存又会造成严重内存碎片。"
                    "PagedAttention 借鉴操作系统虚拟内存分页思想，将 KV Cache 切分为固定大小物理块按需映射。"
                ),
                "knowledge_points": ["KV Cache", "PagedAttention"],
                "run_inline": True,
            },
        )
        assert up_vid.status_code == 202, up_vid.text
        new_vid_id = up_vid.json()["data"]["video_id"]
        assert up_vid.json()["data"]["outline_status"] == "draft_review"

        # 未经教师人工校准发布前，学生严禁观看（Human-in-the-Loop）
        unpub = client.get(f"/api/v1/videos/{new_vid_id}/playback", headers=stu_h)
        assert unpub.status_code == 403
        assert unpub.json()["error"]["code"] == "OUTLINE_NOT_PUBLISHED"

        # 教师人工校准编辑并创建 v2 版本，随后正式发布
        cal = client.post(
            f"/api/v1/teacher/videos/{new_vid_id}/outline-versions",
            headers=tea_h,
            json={
                "change_note": "人工精修 PagedAttention 分页机制章节边界",
                "chapters": [
                    {
                        "title": "1.1 自回归解码冗余与 KV Cache 原理",
                        "summary": "剖析增量推理缓存历史 K/V 张量的时间复杂度收益。",
                        "start_sec": 0,
                        "end_sec": 145,
                        "knowledge_points": ["KV Cache"],
                    },
                    {
                        "title": "1.2 PagedAttention 非连续显存分页机制",
                        "summary": "借鉴虚拟内存页表消除显存内外碎片，提升吞吐率。",
                        "start_sec": 145,
                        "end_sec": 300,
                        "knowledge_points": ["PagedAttention"],
                    },
                ],
            },
        )
        assert cal.status_code == 201
        v2_id = cal.json()["data"]["id"]

        pub = client.post(
            f"/api/v1/teacher/videos/{new_vid_id}/outline-versions/{v2_id}/publications",
            headers=tea_h,
        )
        assert pub.status_code == 200

        play = client.get(f"/api/v1/videos/{new_vid_id}/playback", headers=stu_h)
        assert play.status_code == 200
        assert len(play.json()["data"]["chapters"]) == 2

        # -----------------------------------------------------
        # 3. 内部签名 Webhook 防伪造、防重放与幂等去重校验
        # -----------------------------------------------------
        wh_body = json.dumps(
            {
                "job_id": "job_wh_001",
                "job_type": "knowledge_index_build",
                "course_id": course_id,
                "status": "succeeded",
                "progress": 100,
            }
        ).encode("utf-8")
        ts = str(int(time.time()))
        valid_sig = sign_webhook_fn(ts, wh_body)

        bad_wh = client.post(
            "/internal/v1/webhooks/ai-jobs",
            content=wh_body,
            headers={"X-Webhook-Timestamp": ts, "X-Webhook-Signature": "sha256=bad", "Idempotency-Key": "wh_k1"},
        )
        assert bad_wh.status_code == 401

        expired_ts = str(int(time.time()) - 900)
        expired_wh = client.post(
            "/internal/v1/webhooks/ai-jobs",
            content=wh_body,
            headers={
                "X-Webhook-Timestamp": expired_ts,
                "X-Webhook-Signature": sign_webhook_fn(expired_ts, wh_body),
                "Idempotency-Key": "wh_expired",
            },
        )
        assert expired_wh.status_code == 401
        assert expired_wh.json()["error"]["code"] == "WEBHOOK_REPLAY_EXPIRED"

        ok_wh1 = client.post(
            "/internal/v1/webhooks/ai-jobs",
            content=wh_body,
            headers={"X-Webhook-Timestamp": ts, "X-Webhook-Signature": valid_sig, "Idempotency-Key": "wh_k1"},
        )
        assert ok_wh1.status_code == 200
        assert ok_wh1.json()["data"]["duplicate"] is False

        ok_wh2 = client.post(
            "/internal/v1/webhooks/ai-jobs",
            content=wh_body,
            headers={"X-Webhook-Timestamp": ts, "X-Webhook-Signature": valid_sig, "Idempotency-Key": "wh_k1"},
        )
        assert ok_wh2.json()["data"]["duplicate"] is True

        # -----------------------------------------------------
        # 4. 场景 B：边看边问 RAG + 未解答问题收件箱一键回流知识库闭环
        # -----------------------------------------------------
        ask_hit = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads",
            headers=stu_h,
            json={
                "question": "PagedAttention 是如何解决显存碎片问题的？",
                "video_context": {"video_id": new_vid_id, "chapter_title": "1.2 PagedAttention", "timestamp_sec": 160},
            },
        )
        assert ask_hit.status_code == 201
        hit_msg = ask_hit.json()["data"]["assistant_message"]
        assert hit_msg["needs_teacher_review"] is False
        assert len(hit_msg["citations"]) >= 1

        novel_q = "请问 Speculative Decoding 投机采样中的拒绝采样接受率公式如何证明无损分布？"
        ask_miss = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads",
            headers=stu_h,
            json={"question": novel_q},
        )
        assert ask_miss.status_code == 201
        miss_msg = ask_miss.json()["data"]["assistant_message"]
        assert miss_msg["needs_teacher_review"] is True
        inbox_id = miss_msg["routed_inbox_id"]
        assert inbox_id is not None

        resolve = client.post(
            f"/api/v1/teacher/unanswered-questions/{inbox_id}/resolutions",
            headers=tea_h,
            json={
                "teacher_answer": (
                    "投机采样（Speculative Decoding）使用草稿模型快速生成候选序列，目标模型并行计算概率后按 "
                    "min(1, p(x)/q(x)) 接受，若拒绝则从残差分布 norm(max(0, p-q)) 重新采样，因此在数学上严格保持目标模型原始分布无损。"
                ),
                "sync_to_kb": True,
                "pin_as_faq": True,
            },
        )
        assert resolve.status_code == 200
        assert resolve.json()["data"]["synced_to_kb"] is True

        ask_after = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads",
            headers=stu_h,
            json={"question": "投机采样 Speculative Decoding 为什么能保证目标模型分布无损？"},
        )
        assert ask_after.status_code == 201
        after_msg = ask_after.json()["data"]["assistant_message"]
        assert after_msg["needs_teacher_review"] is False
        assert any(c["source_type"] == "teacher_qa" for c in after_msg["citations"])

        # 4b. 双微服务端到端 SSE 流式问答代理与落库校验
        sse_resp = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads/stream",
            headers=stu_h,
            json={"question": "PagedAttention 是如何解决显存碎片问题的？"},
        )
        assert sse_resp.status_code == 200
        assert "event: meta" in sse_resp.text and "event: delta" in sse_resp.text and "event: done" in sse_resp.text

        # -----------------------------------------------------
        # 5. 场景 C：AI 出题审题 + 个性化测验 + 错题本归集 + 仪表盘推荐
        # -----------------------------------------------------
        gen_q = client.post(
            f"/api/v1/teacher/courses/{course_id}/question-generations",
            headers=tea_h,
            json={"knowledge_points": ["KV Cache", "PagedAttention"], "count": 3},
        )
        assert gen_q.status_code == 201
        for qid in gen_q.json()["data"]["question_ids"]:
            rev = client.patch(
                f"/api/v1/teacher/questions/{qid}",
                headers=tea_h,
                json={"review_status": "approved"},
            )
            assert rev.status_code == 200

        # 上报真实观看时长事件（验证仪表盘按真实学习日志累计时长）
        ev = client.post(
            "/api/v1/users/me/learning-events",
            headers=stu_h,
            json={"course_id": course_id, "video_id": new_vid_id, "position_sec": 180, "watched_seconds": 240},
        )
        assert ev.status_code == 201

        quiz_start = client.post(
            f"/api/v1/courses/{course_id}/quiz-sessions",
            headers=stu_h,
            json={"session_type": "personalized", "count": 3},
        )
        assert quiz_start.status_code == 201
        sess_data = quiz_start.json()["data"]
        sess_id = sess_data["session_id"]
        wrong_ans_map = {q["id"]: ["Z"] for q in sess_data["questions"]}
        quiz_sub = client.post(
            f"/api/v1/quiz-sessions/{sess_id}/submissions",
            headers=stu_h,
            json={"answers": wrong_ans_map},
        )
        assert quiz_sub.status_code == 201
        assert quiz_sub.json()["data"]["score"] == 0.0

        wb = client.get("/api/v1/users/me/wrong-questions", headers=stu_h)
        assert wb.status_code == 200
        assert len(wb.json()["data"]["items"]) >= 2

        dash = client.get("/api/v1/users/me/dashboard", headers=stu_h)
        assert dash.status_code == 200
        assert dash.json()["data"]["study_minutes"] == 4.0
        assert len(dash.json()["data"]["recommendations"]) >= 1

        analytics = client.get(f"/api/v1/teacher/courses/{course_id}/analytics", headers=tea_h)
        assert analytics.status_code == 200
        assert len(analytics.json()["data"]["question_hotspots"]) >= 1

        # -----------------------------------------------------
        # 6. 管理端六大域治理（重建索引、运营总览、CSV 报表导出、审计留痕）
        # -----------------------------------------------------
        reb = client.post(f"/api/v1/admin/ai-indexes/{course_id}/rebuilds", headers=adm_h, json={"run_inline": True})
        assert reb.status_code == 202

        overview = client.get("/api/v1/admin/overview", headers=adm_h)
        assert overview.status_code == 200
        assert overview.json()["data"]["platform_metrics"]["active_users"] >= 5

        csv_resp = client.get("/api/v1/admin/reports/courses.csv", headers=adm_h)
        assert csv_resp.status_code == 200
        assert "深度学习与大模型系统导论" in csv_resp.text

        audits = client.get("/api/v1/admin/audit-logs", headers=adm_h)
        assert audits.status_code == 200
        assert len(audits.json()["data"]["items"]) >= 4
