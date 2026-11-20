"""
商业生产模式真实二进制媒体与文档上传全链路测试：
- 真实 .pdf / .docx 解析入库与混合检索问答命中；
- 真实 .mp4 视频流（PyAV 合成含 16kHz 音轨与视频帧）+ .srt 字幕上传；
- HTTP 206 Partial Content (Range 字节流分段点播跳转) 与防目录穿越校验。
"""
import importlib
import io
import math
import os
from pathlib import Path
import sys
import av
import docx
from fastapi.testclient import TestClient
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]

PROD_JWT_SECRET = "jwt_commercial_media_secret_0123456789abcdef"
PROD_SVC_KEY = "aisvc_commercial_media_secret_0123456789abcdef"
PROD_OPS_KEY = "aiops_commercial_media_secret_0123456789abcdef"
PROD_WH_SECRET = "whsec_commercial_media_secret_0123456789abcdef"
ADMIN_PWD = "AdminMedia!2026Safe"


def _make_minimal_pdf_with_text(text_ascii: str) -> bytes:
    """构造带真实可提取文字流的标准单页 PDF 字节流。"""
    stream = f"BT /F1 14 Tf 50 720 Td ({text_ascii}) Tj ET".encode("latin-1")
    objs = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
        f"4 0 obj << /Length {len(stream)} >> stream\n".encode("latin-1") + stream + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(buf.tell())
        buf.write(obj)
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    buf.write(
        f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("latin-1")
    )
    return buf.getvalue()


def _make_docx_bytes(paragraph_text: str) -> bytes:
    doc = docx.Document()
    doc.add_heading("智能学习真实 Word 讲义", level=1)
    doc.add_paragraph(paragraph_text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_real_mp4_bytes(duration_sec: int = 4) -> bytes:
    """使用 PyAV 在内存合成包含真实视频帧与 16kHz 音频流的合法 .mp4 文件。"""
    buf = io.BytesIO()
    with av.open(buf, mode="w", format="mp4") as container:
        v_stream = container.add_stream("mpeg4", rate=10)
        v_stream.width = 320
        v_stream.height = 180
        v_stream.pix_fmt = "yuv420p"

        a_stream = container.add_stream("aac", rate=16000)

        for frame_idx in range(duration_sec * 10):
            img = np.zeros((180, 320, 3), dtype=np.uint8)
            img[:, :, 0] = (frame_idx * 5) % 255
            v_frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            for packet in v_stream.encode(v_frame):
                container.mux(packet)

        samples = np.array(
            [int(3000 * math.sin(2 * math.pi * 440 * t / 16000)) for t in range(16000 * duration_sec)],
            dtype=np.int16,
        ).reshape(1, -1)
        a_frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        a_frame.sample_rate = 16000
        for packet in a_stream.encode(a_frame):
            container.mux(packet)

        for packet in v_stream.encode(None):
            container.mux(packet)
        for packet in a_stream.encode(None):
            container.mux(packet)

    return buf.getvalue()


def _setup_apps(tmp_path: Path):
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

    ai_asr_mod = importlib.import_module("app.providers.asr_provider")
    ai_main = importlib.import_module("app.main")
    ai_app = ai_main.create_app()

    sys.path.remove(ai_dir)
    for mod_name in list(sys.modules.keys()):
        if mod_name == "app" or mod_name.startswith("app."):
            sys.modules.pop(mod_name, None)

    plat_dir = str(ROOT / "platform-backend")
    if plat_dir in sys.path:
        sys.path.remove(plat_dir)
    sys.path.insert(0, plat_dir)

    db_url = f"sqlite:///{(tmp_path / 'media_prod.db').as_posix()}"
    os.environ["DATABASE_URL"] = db_url
    plat_cfg = importlib.import_module("app.core.config")
    plat_cfg.settings.app_env = "production"
    plat_cfg.settings.storage_dir = str(tmp_path / "storage")
    plat_cfg.settings.database_url = db_url
    plat_cfg.settings.jwt_secret = PROD_JWT_SECRET
    plat_cfg.settings.ai_service_key = PROD_SVC_KEY
    plat_cfg.settings.ai_ops_key = PROD_OPS_KEY
    plat_cfg.settings.webhook_signing_secret = PROD_WH_SECRET
    plat_cfg.settings.bootstrap_admin_username = "admin"
    plat_cfg.settings.bootstrap_admin_password = ADMIN_PWD

    plat_main = importlib.import_module("app.main")
    plat_client_mod = importlib.import_module("app.clients.ai_service_client")
    plat_client_mod.ai_client.set_transport(httpx.ASGITransport(app=ai_app))
    platform_app = plat_main.create_app()

    ai_asr_mod.asr_client.set_media_transport(httpx.ASGITransport(app=platform_app))
    return platform_app, ai_asr_mod.asr_client


def test_real_pdf_docx_mp4_upload_and_http206_range_streaming(tmp_path: Path):
    platform_app, asr_client_inst = _setup_apps(tmp_path)

    with TestClient(platform_app) as client:
        adm_token = client.post(
            "/api/v1/auth/tokens", json={"username": "admin", "password": ADMIN_PWD}
        ).json()["data"]["access_token"]
        adm_h = {"Authorization": f"Bearer {adm_token}"}

        tea_reg = client.post(
            "/api/v1/auth/registrations",
            json={"username": "media_teacher", "password": "TeaMedia#2026", "display_name": "林老师", "role": "teacher"},
        ).json()["data"]
        client.patch(
            f"/api/v1/admin/users/{tea_reg['user']['id']}",
            headers=adm_h,
            json={"teacher_approved": True},
        )
        tea_h = {"Authorization": f"Bearer {tea_reg['access_token']}"}

        stu_reg = client.post(
            "/api/v1/auth/registrations",
            json={"username": "media_student", "password": "StuMedia#2026", "display_name": "周同学", "role": "student"},
        ).json()["data"]
        stu_h = {"Authorization": f"Bearer {stu_reg['access_token']}"}

        course = client.post(
            "/api/v1/teacher/courses",
            headers=tea_h,
            json={"code": "AI-302", "title": "多模态大模型工程实践", "category": "人工智能", "submit_for_review": True},
        ).json()["data"]
        course_id = course["id"]
        client.patch(
            f"/api/v1/admin/courses/{course_id}/governance",
            headers=adm_h,
            json={"status": "published"},
        )
        client.post(f"/api/v1/courses/{course_id}/enrollments", headers=stu_h, json={})

        # 1. 上传真实 PDF 讲义并检验自动提取文字
        pdf_bytes = _make_minimal_pdf_with_text(
            "Chinchilla scaling law states compute optimal training balances model parameters and tokens equally."
        )
        up_pdf = client.post(
            f"/api/v1/teacher/courses/{course_id}/document-uploads",
            headers=tea_h,
            data={"title": "Chinchilla Scaling Law Lecture.pdf"},
            files={"document_file": ("scaling.pdf", pdf_bytes, "application/pdf")},
        )
        assert up_pdf.status_code == 201, up_pdf.text
        pdf_data = up_pdf.json()["data"]
        assert pdf_data["file_format"] == "pdf"
        assert pdf_data["extracted_chars"] > 30
        assert client.get(pdf_data["file_url"]).status_code == 200

        # 2. 上传真实 Word (.docx) 实验指导书并验证 RAG 问答引用
        docx_bytes = _make_docx_bytes(
            "LoRA 低秩自适应通过在冻结预训练权重旁并联低秩矩阵 A 与 B，显著降低微调显存开销。"
        )
        up_docx = client.post(
            f"/api/v1/teacher/courses/{course_id}/document-uploads",
            headers=tea_h,
            data={"title": "第四章 LoRA 微调实验手册.docx"},
            files={
                "document_file": (
                    "lora_guide.docx",
                    docx_bytes,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert up_docx.status_code == 201, up_docx.text
        assert up_docx.json()["data"]["file_format"] == "docx"

        ask_lora = client.post(
            f"/api/v1/courses/{course_id}/tutor-threads",
            headers=stu_h,
            json={"question": "LoRA 低秩自适应是如何降低微调显存开销的？"},
        )
        assert ask_lora.status_code == 201
        assert ask_lora.json()["data"]["assistant_message"]["needs_teacher_review"] is False

        # 3. 上传真实 .mp4 视频 + 验证 PyAV 音轨重采样探测 + HTTP 206 Range 分段流式点播
        mp4_bytes = _make_real_mp4_bytes(duration_sec=4)
        mp4_path = tmp_path / "probe_sample.mp4"
        mp4_path.write_bytes(mp4_bytes)
        wav_out = tmp_path / "probe_out.wav"
        has_audio, probed_sec = asr_client_inst._extract_16k_mono_wav(mp4_path, wav_out)
        assert has_audio is True
        assert probed_sec >= 3
        assert wav_out.exists() and wav_out.stat().st_size > 1000

        srt_bytes = (
            "1\n00:00:00,000 --> 00:00:02,000\n本节剖析真实 MP4 视频流上传与低秩矩阵初始化策略。\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\n通常矩阵 A 采用高斯初始化，矩阵 B 初始化为零以保证训练起点恒等。\n"
        ).encode("utf-8")

        up_vid = client.post(
            f"/api/v1/teacher/courses/{course_id}/video-uploads",
            headers=tea_h,
            data={"title": "第 4 讲：LoRA 参数高效微调实战视频", "run_inline": "true"},
            files={
                "video_file": ("lecture_lora.mp4", mp4_bytes, "video/mp4"),
                "subtitle_file": ("lecture_lora.srt", srt_bytes, "text/plain"),
            },
        )
        assert up_vid.status_code == 202, up_vid.text
        v_res = up_vid.json()["data"]
        vid_id = v_res["video_id"]
        assert v_res["media_url"].startswith("/media/videos/")
        assert v_res["outline_status"] == "draft_review"

        # 完整读取与 HTTP 206 Range 分片读取验证
        full_resp = client.get(v_res["media_url"])
        assert full_resp.status_code == 200
        assert full_resp.headers.get("Accept-Ranges") == "bytes"
        assert len(full_resp.content) == len(mp4_bytes)

        range_resp = client.get(v_res["media_url"], headers={"Range": "bytes=0-1023"})
        assert range_resp.status_code == 206
        assert range_resp.headers["Content-Range"] == f"bytes 0-1023/{len(mp4_bytes)}"
        assert len(range_resp.content) == 1024
        assert range_resp.content == mp4_bytes[:1024]

        # 非法类别访问返回 404，未携带签名或认证访问私有视频返回 401
        assert client.get("/media/secrets/passwd").status_code == 404
        unsigned_video_path = v_res["media_url"].split("?", 1)[0]
        assert client.get(unsigned_video_path).status_code == 401

        # 教师发布提纲后，学生端拿到真实 media_url 与秒级对齐字幕
        vers = client.get(f"/api/v1/teacher/videos/{vid_id}/outline-versions", headers=tea_h).json()["data"]["versions"]
        pub = client.post(
            f"/api/v1/teacher/videos/{vid_id}/outline-versions/{vers[0]['id']}/publications",
            headers=tea_h,
        )
        assert pub.status_code == 200

        play = client.get(f"/api/v1/videos/{vid_id}/playback", headers=stu_h)
        assert play.status_code == 200
        play_data = play.json()["data"]
        assert play_data["media_url"] == v_res["media_url"]
        assert len(play_data["transcript_segments"]) == 2
