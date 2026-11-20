from pathlib import Path
import pytest
import sys
from fastapi.testclient import TestClient

AI_ROOT = Path(__file__).resolve().parents[1] / "ai-tutor-service"
if str(AI_ROOT) not in sys.path:
    sys.path.insert(0, str(AI_ROOT))

PROD_SVC_KEY = "aisvc_commercial_test_key_0123456789abcdef"
PROD_OPS_KEY = "aiops_commercial_test_key_0123456789abcdef"
PROD_WH_SECRET = "whsec_commercial_test_key_0123456789abcdef"

SERVICE_HEADERS = {"X-Service-Key": PROD_SVC_KEY, "X-Request-ID": "req_test_ai_001"}
OPS_HEADERS = {"X-Ops-Key": PROD_OPS_KEY, "X-Request-ID": "req_test_ops_001"}


def _build_prod_ai_app(tmp_path: Path):
    from app.core.config import settings
    from app.main import create_app
    from app.services.vector_store import CourseKnowledgeStore

    settings.app_env = "production"
    settings.ai_service_key = PROD_SVC_KEY
    settings.ai_ops_key = PROD_OPS_KEY
    settings.webhook_signing_secret = PROD_WH_SECRET
    settings.data_dir = str(tmp_path / "ai_prod_runtime")

    return create_app(), CourseKnowledgeStore


def test_production_security_fail_fast():
    from app.core.config import AiServiceSettings

    # 默认弱密钥在 production 模式下必须拒绝启动（屏蔽本地 .env 以测试纯默认值）
    insecure_cfg = AiServiceSettings(app_env="production", _env_file=None)
    with pytest.raises(RuntimeError, match="SECURITY_FATAL"):
        insecure_cfg.validate_production_security()

    # 业务密钥与运营密钥相同时必须拒绝启动
    same_key_cfg = AiServiceSettings(
        app_env="production",
        _env_file=None,
        ai_service_key="identical_long_secret_key_0123456789",
        ai_ops_key="identical_long_secret_key_0123456789",
        webhook_signing_secret=PROD_WH_SECRET,
    )
    with pytest.raises(RuntimeError, match="SECURITY_FATAL"):
        same_key_cfg.validate_production_security()


def test_dual_channel_auth_isolation(tmp_path: Path):
    ai_app, _ = _build_prod_ai_app(tmp_path)
    with TestClient(ai_app) as client:
        # 缺少密钥返回 401 标准错误信封
        r1 = client.post("/v1/tutor/replies", json={"course_id": "c1", "question": "什么是注意力机制？"})
        assert r1.status_code == 401
        assert r1.json()["error"]["code"] == "INVALID_SERVICE_KEY"

        # 业务密钥不能越权访问 /v1/ops 治理接口
        r2 = client.get("/v1/ops/health", headers=SERVICE_HEADERS)
        assert r2.status_code == 401
        assert r2.json()["error"]["code"] == "INVALID_OPS_KEY"

        # 正确运营密钥访问成功且原样透传 X-Request-ID
        r3 = client.get("/v1/ops/health", headers=OPS_HEADERS)
        assert r3.status_code == 200
        assert r3.headers["X-Request-ID"] == "req_test_ops_001"
        assert r3.json()["data"]["status"] == "healthy"


def test_full_ai_capabilities_sqlite_persistence_and_ops_governance(tmp_path: Path):
    ai_app, CourseKnowledgeStore = _build_prod_ai_app(tmp_path)
    with TestClient(ai_app) as client:
        course_id = "crs_pytest_dl"

        # 1. 文档入库（写入 SQLite WAL 混合检索引擎）
        up = client.put(
            f"/v1/knowledge-bases/{course_id}/documents/doc_attn",
            headers=SERVICE_HEADERS,
            json={
                "title": "Transformer 自注意力机制讲义",
                "source_type": "document",
                "content": "缩放点积注意力通过 Query 与 Key 的点积除以根号 dk，再经 Softmax 加权 Value，有效缓解梯度消失。",
                "metadata": {"file_format": "markdown"},
            },
        )
        assert up.status_code == 200
        assert up.json()["data"]["status"] == "ready"

        # 验证重启后 SQLite WAL 持久化不丢失
        reopened_store = CourseKnowledgeStore()
        assert len(reopened_store.search(course_id=course_id, query="缩放点积注意力", top_k=2)) >= 1

        # 2. 异步视频章节化任务（测试幂等键与 Location 头）
        idem_headers = {**SERVICE_HEADERS, "Idempotency-Key": "idem_vid_001"}
        j1 = client.post(
            "/v1/jobs",
            headers=idem_headers,
            json={
                "job_type": "video_chapterization",
                "course_id": course_id,
                "run_inline": True,
                "payload": {
                    "video_id": "vid_01",
                    "video_title": "自注意力机制推导",
                    "duration_sec": 360,
                    "raw_transcript_text": "首先我们回顾序列建模瓶颈。接着推导缩放点积注意力公式除以根号dk的原因。最后对比多头注意力并行子空间表示。",
                },
            },
        )
        assert j1.status_code == 202
        assert j1.headers["Location"].startswith("/v1/jobs/job_")
        job_id = j1.json()["data"]["job_id"]
        assert j1.json()["data"]["status"] == "succeeded"
        assert len(j1.json()["data"]["result"]["chapters"]) >= 2

        # 重复提交相同 Idempotency-Key 返回同一个 job_id
        j_dup = client.post(
            "/v1/jobs",
            headers=idem_headers,
            json={"job_type": "video_chapterization", "course_id": course_id, "run_inline": True, "payload": {}},
        )
        assert j_dup.json()["data"]["job_id"] == job_id

        # 3. RAG 助教问答（高置信度带引用出处 + 边看边问上下文）
        qa = client.post(
            "/v1/tutor/replies",
            headers=SERVICE_HEADERS,
            json={
                "course_id": course_id,
                "question": "缩放点积注意力为什么要除以根号 dk？",
                "quick_action": "explain_simply",
                "video_context": {"video_id": "vid_01", "chapter_title": "缩放点积注意力", "timestamp_sec": 125},
            },
        )
        assert qa.status_code == 200
        qa_data = qa.json()["data"]
        assert qa_data["needs_teacher_review"] is False
        assert len(qa_data["citations"]) >= 1

        # 3b. SSE 流式问答接口验证（delta 增量 + done 完整引用元数据）
        stream_resp = client.post(
            "/v1/tutor/replies/stream",
            headers=SERVICE_HEADERS,
            json={"course_id": course_id, "question": "缩放点积注意力为什么要除以根号 dk？"},
        )
        assert stream_resp.status_code == 200
        assert "text/event-stream" in stream_resp.headers.get("content-type", "")
        assert "event: delta" in stream_resp.text and "event: done" in stream_resp.text

        # 4. 超纲无关问题自动触发 needs_teacher_review=True
        low_qa = client.post(
            "/v1/tutor/replies",
            headers=SERVICE_HEADERS,
            json={
                "course_id": course_id,
                "question": "量子色动力学中的夸克禁闭拉格朗日量如何重整化？",
            },
        )
        assert low_qa.status_code == 200
        assert low_qa.json()["data"]["needs_teacher_review"] is True

        # 5. 个性化薄弱点测验生成
        quiz = client.post(
            "/v1/assessments/generations",
            headers=SERVICE_HEADERS,
            json={
                "course_id": course_id,
                "mode": "personalized_diagnostic",
                "scope": {"count": 3, "difficulty": "mixed"},
                "learner_profile": {"weak_knowledge_points": ["缩放点积注意力", "Softmax 梯度"]},
            },
        )
        assert quiz.status_code == 200
        assert len(quiz.json()["data"]["questions"]) == 3

        # 6. 真实不可达媒体流触发任务失败 -> 运营接口修正参数一键重试恢复
        failed_job = client.post(
            "/v1/jobs",
            headers=SERVICE_HEADERS,
            json={
                "job_type": "video_chapterization",
                "course_id": course_id,
                "run_inline": True,
                "payload": {
                    "video_id": "vid_unreachable",
                    "video_title": "网络异常测试视频",
                    "media_url": "http://127.0.0.1:9/nonexistent_lecture.mp4",
                },
            },
        )
        failed_id = failed_job.json()["data"]["job_id"]
        assert failed_job.json()["data"]["status"] == "failed"
        assert failed_job.json()["data"]["error_message"]

        retried = client.post(
            f"/v1/ops/jobs/{failed_id}/retries",
            headers=OPS_HEADERS,
            json={
                "run_inline": True,
                "override_payload": {
                    "media_url": "",
                    "raw_transcript_text": "修复存储地址后补充完整字幕转写稿，成功完成章节结构化切分。",
                },
            },
        )
        assert retried.status_code == 202
        assert retried.json()["data"]["status"] == "succeeded"
        assert retried.json()["data"]["retry_count"] == 1

        # 7. 运营指标、问题样本回传与 SQLite 遥测统计
        smp = client.post(
            "/v1/ops/feedback-samples",
            headers=OPS_HEADERS,
            json={
                "course_id": course_id,
                "sample_type": "tutor_reply",
                "source_reason": "student_downvote",
                "input_text": "注意力复杂度是多少？",
                "model_output": "回答不够精确",
            },
        )
        assert smp.status_code == 201

        usage = client.get("/v1/ops/metrics/usage", headers=OPS_HEADERS)
        assert usage.status_code == 200
        assert usage.json()["data"]["total_calls"] >= 3
