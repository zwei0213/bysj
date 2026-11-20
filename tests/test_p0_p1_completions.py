"""
P0 缺陷修复与 P1 能力补齐 —— 双服务全链路回归验收测试套件：
覆盖超纲安全作答、多轮对话指代、输出敏感词脱敏、Human-in-the-Loop 草稿隔离、
角色 AI 配额限流、课程存储配额拦截、试卷次数/班级/时间窗口校验、违规回答屏蔽联动、
真实连续学习天数、分片断点续传、单章 AI 重生成、原生 .xlsx 导入与导出、
按比例随机抽检、权限矩阵、通知与工单闭环、系统全景探针及 Prometheus /metrics。
"""
import importlib
import io
import os
from pathlib import Path
import sys
import av
from fastapi.testclient import TestClient
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

PROD_JWT_SECRET = "jwt_p0p1_commercial_secret_0123456789abcdef"
PROD_SVC_KEY = "aisvc_p0p1_commercial_secret_0123456789abcdef"
PROD_OPS_KEY = "aiops_p0p1_commercial_secret_0123456789abcdef"
PROD_WH_SECRET = "whsec_p0p1_commercial_secret_0123456789abcdef"
ADMIN_PWD = "AdminP0P1!2026Safe"


def _setup_dual_apps(tmp_path: Path):
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
    ai_cfg.settings.data_dir = str(tmp_path / "ai_runtime")

    ai_asr_mod = importlib.import_module("app.providers.asr_provider")
    ai_main = importlib.import_module("app.main")
    ai_store_mod = importlib.import_module("app.services.vector_store")
    ai_app = ai_main.create_app()
    ai_knowledge_store = ai_store_mod.knowledge_store

    sys.path.remove(ai_dir)
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name.startswith("app."):
            sys.modules.pop(mod_name, None)

    plat_dir = str(ROOT / "platform-backend")
    if plat_dir in sys.path:
        sys.path.remove(plat_dir)
    sys.path.insert(0, plat_dir)

    db_url = f"sqlite:///{(tmp_path / 'p0p1_platform.db').as_posix()}"
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
    plat_cfg.settings.bootstrap_admin_password = ADMIN_PWD
    plat_cfg.settings.cors_allowed_origins = "https://learn.cracmheart.com"

    xlsx_mod = importlib.import_module("app.services.xlsx_service")
    plat_main = importlib.import_module("app.main")
    plat_client_mod = importlib.import_module("app.clients.ai_service_client")
    plat_client_mod.ai_client.set_transport(httpx.ASGITransport(app=ai_app))
    platform_app = plat_main.create_app()
    ai_asr_mod.asr_client.set_media_transport(httpx.ASGITransport(app=platform_app))

    return platform_app, ai_app, ai_knowledge_store, xlsx_mod


def _make_video_only_mp4_bytes(duration_sec: int = 3) -> bytes:
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as container:
        v_stream = container.add_stream("mpeg4", rate=5)
        v_stream.width = 160
        v_stream.height = 120
        v_stream.pix_fmt = "yuv420p"
        for idx in range(duration_sec * 5):
            img = np.zeros((120, 160, 3), dtype=np.uint8)
            img[:, :, 1] = (idx * 15) % 255
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            for pkt in v_stream.encode(frame):
                container.mux(pkt)
        for pkt in v_stream.encode(None):
            container.mux(pkt)
    return buf.getvalue()


def _auth_header(client: TestClient, username: str, password: str) -> dict[str, str]:
    r = client.post("/api/v1/auth/tokens", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}


def test_p0_fixes_and_p1_commercial_completions(tmp_path: Path):
    platform_app, ai_app, ai_kb_store, xlsx_mod = _setup_dual_apps(tmp_path)

    with TestClient(ai_app) as ai_test_client:
        m_ai = ai_test_client.get("/metrics")
        assert m_ai.status_code == 200
        assert "ai_tutor_calls_total" in m_ai.text

    with TestClient(platform_app) as client:
        # 1. 健康探针 (SELECT 1) 与 Prometheus /metrics
        assert client.get("/healthz").status_code == 200
        m_plat = client.get("/metrics")
        assert m_plat.status_code == 200
        assert "platform_" in m_plat.text or "slp_" in m_plat.text or "users" in m_plat.text

        adm_h = _auth_header(client, "admin", ADMIN_PWD)

        # 2. 域 1：组织树层级创建/编辑、教师驳回与站内通知、原生 .xlsx 批量导号、权限矩阵
        dept = client.post(
            "/api/v1/admin/organizations",
            headers=adm_h,
            json={"name": "计算机科学与技术学院", "org_type": "department"},
        ).json()["data"]
        cls_org = client.post(
            "/api/v1/admin/organizations",
            headers=adm_h,
            json={"name": "2026级智能科学1班", "org_type": "class", "parent_id": dept["id"]},
        ).json()["data"]
        assert cls_org["parent_id"] == dept["id"]

        reg_tea = client.post(
            "/api/v1/auth/registrations",
            json={"username": "tea_p0", "password": "TeaPassword#2026", "display_name": "赵老师", "role": "teacher"},
        ).json()["data"]
        tea_id = reg_tea["user"]["id"]
        tea_h = {"Authorization": f"Bearer {reg_tea['access_token']}"}

        # 先驳回教师资质并验证站内通知，随后批准开通
        rej = client.patch(
            f"/api/v1/admin/users/{tea_id}",
            headers=adm_h,
            json={"teacher_status": "rejected", "teacher_approved": False, "reject_reason": "请补充教工号证明"},
        )
        assert rej.status_code == 200
        notifs = client.get("/api/v1/users/me/notifications", headers=tea_h).json()["data"]
        assert notifs["unread_count"] >= 1

        client.patch(
            f"/api/v1/admin/users/{tea_id}",
            headers=adm_h,
            json={"teacher_status": "approved", "teacher_approved": True},
        )

        # 原生 .xlsx 批量导入学生账号
        xlsx_users_bytes = xlsx_mod.build_xlsx_bytes(
            "师生名单",
            ["username", "display_name", "role", "email", "password"],
            [
                ["stu_xlsx_1", "孙同学", "student", "sun@edu.cn", "StuXlsx#2026"],
                ["stu_xlsx_2", "钱同学", "student", "qian@edu.cn", "StuXlsx#2026"],
            ],
        )
        up_xlsx = client.post(
            "/api/v1/admin/users/xlsx-import",
            headers=adm_h,
            data={"org_id": cls_org["id"]},
            files={"file": ("students.xlsx", xlsx_users_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert up_xlsx.status_code == 201, up_xlsx.text
        assert up_xlsx.json()["data"]["created_count"] == 2

        stu1_h = _auth_header(client, "stu_xlsx_1", "StuXlsx#2026")
        stu2_h = _auth_header(client, "stu_xlsx_2", "StuXlsx#2026")

        perm = client.get("/api/v1/admin/permissions-matrix", headers=adm_h)
        assert perm.status_code == 200

        # 3. 教师创建课程、编辑课程、存储配额拦截校验
        crs = client.post(
            "/api/v1/teacher/courses",
            headers=tea_h,
            json={"code": "CS-401", "title": "大模型推理系统工程", "category": "人工智能与系统", "submit_for_review": True},
        ).json()["data"]
        course_id = crs["id"]

        # 管理员将配额设为 1MB 测试超配额拦截，再调回 512MB 并上架
        client.patch(
            f"/api/v1/admin/courses/{course_id}/governance",
            headers=adm_h,
            json={"status": "published", "storage_quota_mb": 1},
        )
        big_bytes = b"A" * (2 * 1024 * 1024)
        over_quota = client.post(
            f"/api/v1/teacher/courses/{course_id}/document-uploads",
            headers=tea_h,
            data={"title": "超配额大讲义.md"},
            files={"document_file": ("huge.md", big_bytes, "text/markdown")},
        )
        assert over_quota.status_code == 400
        assert over_quota.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"

        client.patch(
            f"/api/v1/admin/courses/{course_id}/governance",
            headers=adm_h,
            json={"storage_quota_mb": 512},
        )

        # 4. P0 验证：开启 allow_out_of_syllabus=True 且知识库为空时绝不触发 IndexError 500
        client.put(
            f"/api/v1/teacher/courses/{course_id}/tutor-config",
            headers=tea_h,
            json={"strict_course_only": False, "allow_out_of_syllabus": True, "tone": "encouraging_academic", "forbidden_topics": []},
        )
        client.post(f"/api/v1/courses/{course_id}/enrollments", headers=stu1_h, json={})

        oos_ask = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads",
            headers=stu1_h,
            json={"question": "请简要介绍拓扑量子计算中的马约拉纳零能模是什么？"},
        )
        assert oos_ask.status_code == 201, oos_ask.text
        oos_msg = oos_ask.json()["data"]["assistant_message"]
        assert oos_msg["citations"] == []
        assert len(oos_msg["content"]) > 15

        # 恢复课程严格范围配置并上传包含测试词的讲义验证输出敏感词脱敏 + 多轮对话历史
        client.put(
            f"/api/v1/teacher/courses/{course_id}/tutor-config",
            headers=tea_h,
            json={"strict_course_only": True, "allow_out_of_syllabus": False, "tone": "encouraging_academic", "forbidden_topics": []},
        )
        doc_res = client.post(
            f"/api/v1/teacher/courses/{course_id}/documents",
            headers=tea_h,
            json={
                "title": "FlashAttention IO 感知算子融合讲义",
                "file_format": "markdown",
                "content_text": "FlashAttention 利用 SRAM 分块平铺（Tiling）与在线 Softmax 重计算，把 HBM 访存复杂度降低数倍，严禁使用作弊器应付实验。",
            },
        )
        assert doc_res.status_code == 201

        t1 = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads",
            headers=stu1_h,
            json={"question": "FlashAttention 是如何降低 HBM 访存开销的？"},
        ).json()["data"]
        thread_id = t1["thread_id"]
        first_msg = t1["assistant_message"]
        # 验证输出敏感词“作弊器”在本地兜底模式下也被脱敏为 ***
        assert "作弊器" not in first_msg["content"]

        # 多轮指代追问（依赖后端自动携带 conversation_history）
        t2 = client.post(
            f"/api/v1/tutor-threads/{thread_id}/messages",
            headers=stu1_h,
            json={"question": "它核心用到了哪两项技术？"},
        )
        assert t2.status_code == 201
        assert t2.json()["data"]["assistant_message"]["needs_teacher_review"] is False

        # 5. 分片断点续传 + Human-in-the-Loop 未发布前草稿不提前入库 + 单章节 AI 重生成
        full_mp4 = _make_video_only_mp4_bytes(duration_sec=4)
        mid_idx = len(full_mp4) // 2
        part0 = full_mp4[:mid_idx]
        part1 = full_mp4[mid_idx:]
        init1 = client.post(
            f"/api/v1/teacher/courses/{course_id}/video-uploads/init",
            headers=tea_h,
            json={"filename": "speculative_decoding.mp4", "title": "投机采样专题课", "total_size": len(full_mp4), "total_chunks": 2},
        )
        assert init1.status_code in (200, 201)
        upload_id = init1.json()["data"]["upload_id"]

        c0 = client.put(f"/api/v1/teacher/video-uploads/{upload_id}/chunks/0", headers=tea_h, content=part0)
        assert c0.status_code == 200

        # 模拟网络中断后重新 init：应返回已上传分片 [0]
        init_resume = client.post(
            f"/api/v1/teacher/courses/{course_id}/video-uploads/init",
            headers=tea_h,
            json={"filename": "speculative_decoding.mp4", "title": "投机采样专题课", "total_size": len(full_mp4), "total_chunks": 2},
        ).json()["data"]
        assert init_resume["upload_id"] == upload_id
        assert 0 in init_resume["uploaded_chunks"]

        c1 = client.put(f"/api/v1/teacher/video-uploads/{upload_id}/chunks/1", headers=tea_h, content=part1)
        assert c1.status_code == 200

        comp = client.post(
            f"/api/v1/teacher/video-uploads/{upload_id}/complete",
            headers=tea_h,
            json={"title": "投机采样分片合并视频", "knowledge_points": ["Draft Model", "Rejection Sampling"], "run_inline": True},
        )
        assert comp.status_code == 202, comp.text
        vid_id = comp.json()["data"]["video_id"]

        # 验证未点击发布前，视频字幕未混入课程 RAG 索引库
        kb_chunks_before_pub = ai_kb_store.list_course_chunks(course_id)
        assert not any(c["source_type"] == "video_transcript" and vid_id in c["source_id"] for c in kb_chunks_before_pub)

        # 单章节 AI 重生成
        regen = client.post(
            f"/api/v1/teacher/videos/{vid_id}/chapter-regenerations",
            headers=tea_h,
            json={"start_sec": 0, "end_sec": 120, "current_title": "旧标题", "instruction": "突出拒绝采样无损性质"},
        )
        assert regen.status_code == 200
        assert regen.json()["data"]["title"]

        # 发布提纲后验证视频字幕正式入库
        vers = client.get(f"/api/v1/teacher/videos/{vid_id}/outline-versions", headers=tea_h).json()["data"]["versions"]
        client.post(f"/api/v1/teacher/videos/{vid_id}/outline-versions/{vers[0]['id']}/publications", headers=tea_h)
        kb_chunks_after_pub = ai_kb_store.list_course_chunks(course_id)
        assert any(c["source_type"] == "video_transcript" for c in kb_chunks_after_pub)

        # 6. 班级分班分组、手动出题/编辑选项答案、组卷班级与次数限制校验
        cls_a = client.post(
            f"/api/v1/teacher/courses/{course_id}/classes",
            headers=tea_h,
            json={"name": "实验卓越班", "term": "2026秋"},
        ).json()["data"]
        cls_b = client.post(
            f"/api/v1/teacher/courses/{course_id}/classes",
            headers=tea_h,
            json={"name": "普通平行班", "term": "2026秋"},
        ).json()["data"]

        # 将 stu_xlsx_2 导入普通平行班，将 stu_xlsx_1 分入实验卓越班第1组
        client.post(
            f"/api/v1/teacher/courses/{course_id}/students/import",
            headers=tea_h,
            json={"class_id": cls_b["id"], "group_name": "B组", "usernames_or_csv": "stu_xlsx_2"},
        )
        roster = client.get(f"/api/v1/teacher/courses/{course_id}/students", headers=tea_h).json()["data"]["items"]
        stu1_enr = next(r for r in roster if r["username"] == "stu_xlsx_1")
        client.patch(
            f"/api/v1/teacher/enrollments/{stu1_enr['enrollment_id']}",
            headers=tea_h,
            json={"class_id": cls_a["id"], "group_name": "A1攻坚组"},
        )

        # 手动新增题目并修改选项与答案
        mq = client.post(
            f"/api/v1/teacher/courses/{course_id}/questions",
            headers=tea_h,
            json={
                "question_type": "single_choice",
                "stem": "FlashAttention 主要利用 GPU 的哪一层高速存储进行分块计算？",
                "options": [{"key": "A", "text": "HDD 机械盘"}, {"key": "B", "text": "片上 SRAM"}],
                "answer": ["A"],
                "explanation": "初稿待修正",
                "knowledge_point": "FlashAttention",
            },
        ).json()["data"]
        patched_q = client.patch(
            f"/api/v1/teacher/questions/{mq['id']}",
            headers=tea_h,
            json={
                "options": [
                    {"key": "A", "text": "HDD 机械盘"},
                    {"key": "B", "text": "GPU 片上 SRAM"},
                    {"key": "C", "text": "网卡缓冲区"},
                ],
                "answer": ["B"],
                "explanation": "利用容量小但带宽极高的片上 SRAM 避免反复读写 HBM。",
            },
        )
        assert patched_q.status_code == 200
        assert patched_q.json()["data"]["answer"] == ["B"]

        # 发布仅面向“实验卓越班”、最多允许作答 1 次的正式试卷
        exam = client.post(
            f"/api/v1/teacher/courses/{course_id}/exams",
            headers=tea_h,
            json={
                "title": "实验卓越班随堂小测",
                "question_ids": [mq["id"]],
                "time_limit_min": 15,
                "max_attempts": 1,
                "target_class_id": cls_a["id"],
            },
        ).json()["data"]

        # 普通平行班 stu_xlsx_2 尝试参加应被拦截 (403 EXAM_CLASS_MISMATCH)
        wrong_cls_try = client.post(
            f"/api/v1/courses/{course_id}/quiz-sessions",
            headers=stu2_h,
            json={"session_type": "exam", "exam_id": exam["id"]},
        )
        assert wrong_cls_try.status_code == 403
        assert wrong_cls_try.json()["error"]["code"] == "EXAM_CLASS_MISMATCH"

        # 实验卓越班 stu_xlsx_1 第 1 次参加成功（故意答错以进入错题本），第 2 次重考被 max_attempts=1 拦截！
        attempt1 = client.post(
            f"/api/v1/courses/{course_id}/quiz-sessions",
            headers=stu1_h,
            json={"session_type": "exam", "exam_id": exam["id"]},
        )
        assert attempt1.status_code == 201
        sess1 = attempt1.json()["data"]
        assert sess1["time_limit_min"] == 15
        client.post(
            f"/api/v1/quiz-sessions/{sess1['session_id']}/submissions",
            headers=stu1_h,
            json={"answers": {mq["id"]: ["A"]}},
        )

        attempt2 = client.post(
            f"/api/v1/courses/{course_id}/quiz-sessions",
            headers=stu1_h,
            json={"session_type": "exam", "exam_id": exam["id"]},
        )
        assert attempt2.status_code == 400
        assert attempt2.json()["error"]["code"] == "EXAM_MAX_ATTEMPTS_REACHED"

        # 7. 学生端：原错题重做 (wrong_retry)、笔记编辑与书签过滤、收藏回答夹、连续学习天数 >= 1
        wrs = client.get(f"/api/v1/users/me/wrong-questions?course_id={course_id}", headers=stu1_h).json()["data"]["items"]
        assert len(wrs) == 1
        retry_sess = client.post(
            f"/api/v1/courses/{course_id}/quiz-sessions",
            headers=stu1_h,
            json={"session_type": "wrong_retry", "wrong_record_ids": [wrs[0]["id"]]},
        )
        assert retry_sess.status_code == 201
        assert retry_sess.json()["data"]["questions"][0]["stem"].startswith("FlashAttention")

        note = client.post(
            "/api/v1/users/me/notes",
            headers=stu1_h,
            json={"course_id": course_id, "video_id": vid_id, "timestamp_sec": 42, "content": "初版笔记", "is_bookmark": False},
        ).json()["data"]
        ed_note = client.patch(
            f"/api/v1/users/me/notes/{note['id']}",
            headers=stu1_h,
            json={"content": "精修重点书签笔记", "is_bookmark": True},
        )
        assert ed_note.status_code == 200
        bm_notes = client.get(f"/api/v1/users/me/notes?video_id={vid_id}&bookmarks_only=true", headers=stu1_h).json()["data"]["items"]
        assert len(bm_notes) == 1 and bm_notes[0]["content"] == "精修重点书签笔记"

        # 收藏回答 -> 在收藏夹可见 -> 点踩并由管理员屏蔽后 -> 对话中屏蔽且收藏夹自动隐藏！
        client.post(f"/api/v1/tutor-messages/{first_msg['id']}/feedback", headers=stu1_h, json={"bookmarked": True})
        bms = client.get(f"/api/v1/users/me/bookmarked-messages?course_id={course_id}", headers=stu1_h).json()["data"]["items"]
        assert any(m["id"] == first_msg["id"] for m in bms)

        client.post(f"/api/v1/tutor-messages/{first_msg['id']}/feedback", headers=stu1_h, json={"vote": "down", "reason": "表述需屏蔽复核"})
        samples = client.get("/api/v1/admin/feedback-samples", headers=adm_h).json()["data"]["items"]
        target_sample = next(s for s in samples if s.get("message_id") == first_msg["id"])
        client.patch(
            f"/api/v1/admin/feedback-samples/{target_sample['id']}",
            headers=adm_h,
            json={"status": "blocked", "admin_note": "违规表述已屏蔽"},
        )

        msgs_after_block = client.get(f"/api/v1/tutor-threads/{thread_id}/messages", headers=stu1_h).json()["data"]["items"]
        blocked_msg = next(m for m in msgs_after_block if m["id"] == first_msg["id"])
        assert blocked_msg["is_blocked"] is True
        assert "屏蔽" in blocked_msg["content"]

        bms_after = client.get(f"/api/v1/users/me/bookmarked-messages?course_id={course_id}", headers=stu1_h).json()["data"]["items"]
        assert not any(m["id"] == first_msg["id"] for m in bms_after)

        dash = client.get("/api/v1/users/me/dashboard", headers=stu1_h).json()["data"]
        assert dash["streak_days"] >= 1

        # 8. 教师学情看板（含分数段分布与逐题正确率）
        ana = client.get(f"/api/v1/teacher/courses/{course_id}/analytics", headers=tea_h).json()["data"]
        assert "score_distribution" in ana["quiz_summary"]
        assert "question_accuracy" in ana["quiz_summary"]

        # 9. 管理端：随机比例抽检、原生 .xlsx 报表导出、工单分派回复闭环、系统全景状态、角色 AI 配额限流 429
        drawn = client.post(
            "/api/v1/admin/spot-checks/draw",
            headers=adm_h,
            json={"sample_type": "all", "sample_ratio_pct": 50.0, "max_count": 10},
        )
        assert drawn.status_code == 200
        assert len(drawn.json()["data"]["items"]) >= 1

        xlsx_rep = client.get(f"/api/v1/admin/reports/courses.xlsx?course_id={course_id}", headers=adm_h)
        assert xlsx_rep.status_code == 200
        parsed_rows = xlsx_mod.parse_tabular_upload("report.xlsx", xlsx_rep.content)
        assert len(parsed_rows) == 1
        assert "大模型推理系统工程" in str(parsed_rows[0].values())

        tkt = client.post(
            "/api/v1/support-tickets",
            headers=stu1_h,
            json={"category": "课程建议", "subject": "希望增加算子实验", "description": "请补充 Triton 案例"},
        ).json()["data"]
        client.patch(
            f"/api/v1/admin/tickets/{tkt['id']}",
            headers=adm_h,
            json={"status": "resolved", "assignee_name": "运维王工", "assignee_note": "已协调授课教师下周补充"},
        )
        my_tkts = client.get("/api/v1/users/me/support-tickets", headers=stu1_h).json()["data"]["items"]
        assert my_tkts[0]["assignee_name"] == "运维王工"

        sys_st = client.get("/api/v1/admin/system-status", headers=adm_h)
        assert sys_st.status_code == 200
        assert sys_st.json()["data"]["database"]["status"] == "healthy"

        # 将学生每分钟 AI 配额临时收紧为 1 次，验证第 2 次连续提问触发 429 拦截
        client.patch(
            "/api/v1/admin/ai-runtime-config",
            headers=adm_h,
            json={"rate_limit_per_minute": {"student": 1, "teacher": 60}},
        )
        r_ok = client.post(f"/api/v1/tutor-threads/{thread_id}/messages", headers=stu2_h if False else stu1_h, json={"question": "第一次配额内提问"})
        # stu1_h 在本分钟前面已提问过，或再追问一次必定触发 429
        r_limit = client.post(f"/api/v1/tutor-threads/{thread_id}/messages", headers=stu1_h, json={"question": "超出每分钟配额提问"})
        assert 429 in (r_ok.status_code, r_limit.status_code)
