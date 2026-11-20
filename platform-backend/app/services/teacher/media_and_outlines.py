"""教师视频上传、分片续传、AI 章节大纲校准与讲义资料服务。"""

from pathlib import Path
from typing import Any
import uuid
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.config import settings
from app.core.enums import DocumentIndexStatus, OutlineStatus
from app.core.errors import PlatformError
from app.models.entities import ChunkUploadSession, CourseDocument, OutlineVersion, User, VideoResource
from app.services.audit_helper import record_audit
from app.services.file_parser_service import parse_document_bytes, parse_subtitle_bytes, probe_video_duration_sec
from app.services.storage_service import ALLOWED_VIDEO_EXTS, merge_chunks_to_video, prepare_chunk_temp_dir, save_document_upload, save_video_upload, unlink_media_rel_url

from app.services.teacher.courses import check_course_storage_quota, ensure_course_owner_or_admin

async def upload_video_and_trigger_ai(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    title: str,
    media_url: str,
    duration_sec: int,
    raw_transcript_text: str,
    knowledge_points: list[str],
    run_inline: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    est_size_mb = round(max(20.0, duration_sec * 0.15), 2)
    check_course_storage_quota(course, est_size_mb)

    existing_count = db.scalar(select(func.count()).select_from(VideoResource).where(VideoResource.course_id == course_id)) or 0
    video = VideoResource(
        course_id=course_id,
        title=title,
        media_url=media_url,
        duration_sec=duration_sec,
        order_index=existing_count + 1,
        file_size_mb=est_size_mb,
        outline_status=OutlineStatus.PROCESSING,
    )
    db.add(video)
    db.flush()
    course.storage_used_mb = round((course.storage_used_mb or 0.0) + est_size_mb, 2)

    job_data = await ai_client.submit_video_chapterization_job(
        course_id=course_id,
        payload={
            "video_id": video.id,
            "video_title": title,
            "media_url": media_url,
            "duration_sec": duration_sec,
            "raw_transcript_text": raw_transcript_text,
            "knowledge_points": knowledge_points,
        },
        idempotency_key=f"chapterize_{video.id}",
        request_id=request_id,
        run_inline=run_inline,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    video.ai_job_id = job_data.get("job_id")

    if job_data.get("status") == "succeeded" and job_data.get("result"):
        apply_chapterization_result_to_video(db, video, job_data["result"])
    elif job_data.get("status") == "failed":
        video.outline_status = OutlineStatus.FAILED

    record_audit(
        db,
        actor=teacher,
        action="UPLOAD_VIDEO_TRIGGER_AI",
        target_type="video_resource",
        target_id=video.id,
        detail=f"上传视频《{title}》并提交 AI 章节化任务 ({video.ai_job_id})",
    )
    db.commit()
    return {
        "video_id": video.id,
        "title": video.title,
        "outline_status": video.outline_status,
        "ai_job_id": video.ai_job_id,
        "job_status": job_data.get("status"),
    }


def apply_chapterization_result_to_video(db: Session, video: VideoResource, result: dict[str, Any]) -> OutlineVersion:
    """将 AI 生成的章节与时间戳幂等写入草稿版本（遵循 Human-in-the-Loop，默认不直接发布）。"""
    existing_ai_ver = db.scalars(
        select(OutlineVersion)
        .where(OutlineVersion.video_id == video.id, OutlineVersion.source == "ai_generated")
        .order_by(OutlineVersion.version_no.desc())
    ).first()
    if existing_ai_ver and video.outline_status != OutlineStatus.PROCESSING:
        return existing_ai_ver

    if result.get("duration_sec"):
        video.duration_sec = max(1, int(result["duration_sec"]))

    raw_chapters = result.get("chapters") or []
    normalized_chapters = [
        {
            "chapter_id": ch.get("chapter_id") or f"ch_{video.id}_{idx + 1}",
            "order_index": idx + 1,
            "title": ch.get("title", f"第 {idx + 1} 节"),
            "summary": ch.get("summary", ""),
            "start_sec": int(ch.get("start_sec", 0)),
            "end_sec": int(ch.get("end_sec", video.duration_sec)),
            "knowledge_points": ch.get("knowledge_points") or [],
        }
        for idx, ch in enumerate(raw_chapters)
    ]

    if result.get("transcript_segments"):
        video.transcript_json = result["transcript_segments"]

    asr_note = result.get("asr_provider_used") or "ai_pipeline"
    max_ver = db.scalar(select(func.max(OutlineVersion.version_no)).where(OutlineVersion.video_id == video.id)) or 0
    new_ver = OutlineVersion(
        video_id=video.id,
        version_no=max_ver + 1,
        source="ai_generated",
        chapters_json=normalized_chapters,
        change_note=f"AI 自动转写与章节切分初稿 (引擎: {asr_note}，待教师校准发布)",
        is_published=False,
        created_by="system_ai",
    )
    db.add(new_ver)
    if video.outline_status != OutlineStatus.PUBLISHED:
        video.outline_status = OutlineStatus.DRAFT_REVIEW
    db.flush()
    return new_ver


async def upload_real_video_file_and_trigger_ai(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    video_file: UploadFile,
    subtitle_file: UploadFile | None = None,
    title: str = "",
    knowledge_points: list[str] | None = None,
    run_inline: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    disk_path, rel_media_url, ai_fetch_url, size_mb = await save_video_upload(video_file)
    try:
        check_course_storage_quota(course, size_mb)
    except PlatformError:
        disk_path.unlink(missing_ok=True)
        raise

    probed_duration = probe_video_duration_sec(disk_path, default_sec=300)

    subtitle_segments: list[dict[str, Any]] = []
    if subtitle_file and subtitle_file.filename:
        sub_bytes = await subtitle_file.read()
        if sub_bytes:
            subtitle_segments = parse_subtitle_bytes(subtitle_file.filename, sub_bytes)
            if subtitle_segments:
                probed_duration = max(probed_duration, int(subtitle_segments[-1]["end_sec"]))

    resolved_title = title.strip() or (video_file.filename or "新上传授课视频").rsplit(".", 1)[0]
    existing_count = db.scalar(select(func.count()).select_from(VideoResource).where(VideoResource.course_id == course_id)) or 0
    video = VideoResource(
        course_id=course_id,
        title=resolved_title,
        media_url=rel_media_url,
        duration_sec=probed_duration,
        order_index=existing_count + 1,
        file_size_mb=size_mb,
        outline_status=OutlineStatus.PROCESSING,
    )
    db.add(video)
    db.flush()
    course.storage_used_mb = round((course.storage_used_mb or 0.0) + size_mb, 2)

    job_data = await ai_client.submit_video_chapterization_job(
        course_id=course_id,
        payload={
            "video_id": video.id,
            "video_title": resolved_title,
            "media_url": ai_fetch_url,
            "duration_sec": probed_duration,
            "transcript_segments": subtitle_segments,
            "knowledge_points": knowledge_points or [],
        },
        idempotency_key=f"chapterize_{video.id}",
        request_id=request_id,
        run_inline=run_inline,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    video.ai_job_id = job_data.get("job_id")

    if job_data.get("status") == "succeeded" and job_data.get("result"):
        apply_chapterization_result_to_video(db, video, job_data["result"])
    elif job_data.get("status") == "failed":
        video.outline_status = OutlineStatus.FAILED

    record_audit(
        db,
        actor=teacher,
        action="UPLOAD_REAL_VIDEO_FILE",
        target_type="video_resource",
        target_id=video.id,
        detail=f"上传真实视频文件《{resolved_title}》({size_mb}MB, {probed_duration}s) 并启动 AI 语音章节化任务 ({video.ai_job_id})",
    )
    db.commit()
    return {
        "video_id": video.id,
        "title": video.title,
        "media_url": video.media_url,
        "duration_sec": video.duration_sec,
        "file_size_mb": size_mb,
        "outline_status": video.outline_status,
        "ai_job_id": video.ai_job_id,
        "job_status": job_data.get("status"),
    }


def init_chunk_video_upload(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    filename: str,
    title: str,
    total_size: int,
    total_chunks: int,
) -> tuple[dict[str, Any], int]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_VIDEO_EXTS:
        raise PlatformError(
            "UNSUPPORTED_VIDEO_FORMAT",
            f"不支持的视频/音频格式 '{ext}'，请上传 {', '.join(sorted(ALLOWED_VIDEO_EXTS))}",
            status_code=400,
        )
    size_mb = round(max(0.1, total_size / (1024 * 1024)), 2)
    if size_mb > settings.max_video_upload_mb:
        raise PlatformError("VIDEO_TOO_LARGE", f"视频超出最大允许体积 ({settings.max_video_upload_mb} MB)", status_code=413)
    check_course_storage_quota(course, size_mb)

    existing = db.scalars(
        select(ChunkUploadSession)
        .where(
            ChunkUploadSession.course_id == course_id,
            ChunkUploadSession.user_id == teacher.id,
            ChunkUploadSession.filename == filename,
            ChunkUploadSession.total_size == total_size,
            ChunkUploadSession.total_chunks == total_chunks,
            ChunkUploadSession.status == "uploading",
        )
        .order_by(ChunkUploadSession.created_at.desc())
    ).first()

    if existing and existing.temp_dir:
        temp_dir = Path(existing.temp_dir)
        temp_dir.mkdir(parents=True, exist_ok=True)
        verified_chunks = sorted(
            idx
            for idx in (existing.received_chunks_json or [])
            if (temp_dir / f"chunk_{int(idx):06d}.part").is_file()
        )
        if verified_chunks != existing.received_chunks_json:
            existing.received_chunks_json = verified_chunks
            db.commit()
        return (
            {
                "upload_id": existing.upload_id,
                "resumed": True,
                "uploaded_chunks": verified_chunks,
                "total_chunks": existing.total_chunks,
            },
            200,
        )

    sess = ChunkUploadSession(
        course_id=course_id,
        user_id=teacher.id,
        filename=filename,
        title=title.strip() or Path(filename).stem,
        total_size=total_size,
        total_chunks=total_chunks,
        received_chunks_json=[],
        status="uploading",
    )
    db.add(sess)
    db.flush()
    t_dir = prepare_chunk_temp_dir(sess.upload_id)
    sess.temp_dir = str(t_dir)
    db.commit()
    return (
        {
            "upload_id": sess.upload_id,
            "resumed": False,
            "uploaded_chunks": [],
            "total_chunks": sess.total_chunks,
        },
        201,
    )


def save_chunk_part(
    db: Session,
    *,
    teacher: User,
    upload_id: str,
    chunk_index: int,
    chunk_bytes: bytes,
) -> dict[str, Any]:
    sess = db.get(ChunkUploadSession, upload_id)
    if not sess:
        raise PlatformError("UPLOAD_SESSION_NOT_FOUND", "分片上传会话不存在", status_code=404)
    ensure_course_owner_or_admin(db, sess.course_id, teacher)
    if sess.status != "uploading":
        raise PlatformError("UPLOAD_SESSION_CLOSED", "该分片上传会话已完成或已关闭", status_code=400)
    if chunk_index < 0 or chunk_index >= sess.total_chunks:
        raise PlatformError("INVALID_CHUNK_INDEX", f"非法的分片序号: {chunk_index}", status_code=400)
    if not chunk_bytes:
        raise PlatformError("EMPTY_CHUNK", "上传的分片内容为空", status_code=400)

    temp_dir = Path(sess.temp_dir) if sess.temp_dir else prepare_chunk_temp_dir(sess.upload_id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    part_path = temp_dir / f"chunk_{chunk_index:06d}.part"
    part_path.write_bytes(chunk_bytes)

    received = sorted(set(int(x) for x in (sess.received_chunks_json or [])) | {chunk_index})
    sess.received_chunks_json = received
    sess.temp_dir = str(temp_dir)
    db.commit()
    return {
        "upload_id": sess.upload_id,
        "chunk_index": chunk_index,
        "received_count": len(received),
        "uploaded_chunks": received,
        "total_chunks": sess.total_chunks,
    }


async def complete_chunk_video_upload(
    db: Session,
    *,
    teacher: User,
    upload_id: str,
    title: str = "",
    knowledge_points: list[str] | None = None,
    run_inline: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    sess = db.get(ChunkUploadSession, upload_id)
    if not sess:
        raise PlatformError("UPLOAD_SESSION_NOT_FOUND", "分片上传会话不存在", status_code=404)
    course = ensure_course_owner_or_admin(db, sess.course_id, teacher)
    if sess.status != "uploading":
        raise PlatformError("UPLOAD_SESSION_CLOSED", "该分片上传会话已合并完成", status_code=400)

    received_set = set(int(x) for x in (sess.received_chunks_json or []))
    missing = [i for i in range(sess.total_chunks) if i not in received_set]
    if missing:
        raise PlatformError("CHUNKS_INCOMPLETE", f"仍有分片未上传完成: {missing[:10]}", status_code=400)

    temp_dir = Path(sess.temp_dir)
    disk_path, rel_media_url, ai_fetch_url, size_mb = merge_chunks_to_video(
        sess.filename, temp_dir, sess.total_chunks
    )
    try:
        check_course_storage_quota(course, size_mb)
    except PlatformError:
        disk_path.unlink(missing_ok=True)
        raise

    probed_duration = probe_video_duration_sec(disk_path, default_sec=300)
    resolved_title = title.strip() or sess.title or Path(sess.filename).stem
    existing_count = (
        db.scalar(select(func.count()).select_from(VideoResource).where(VideoResource.course_id == course.id)) or 0
    )

    video = VideoResource(
        course_id=course.id,
        title=resolved_title,
        media_url=rel_media_url,
        duration_sec=probed_duration,
        order_index=existing_count + 1,
        file_size_mb=size_mb,
        outline_status=OutlineStatus.PROCESSING,
    )
    db.add(video)
    sess.status = "completed"
    course.storage_used_mb = round((course.storage_used_mb or 0.0) + size_mb, 2)
    db.flush()

    job_data = await ai_client.submit_video_chapterization_job(
        course_id=course.id,
        payload={
            "video_id": video.id,
            "video_title": resolved_title,
            "media_url": ai_fetch_url,
            "duration_sec": probed_duration,
            "transcript_segments": [],
            "knowledge_points": knowledge_points or [],
        },
        idempotency_key=f"chapterize_{video.id}",
        request_id=request_id,
        run_inline=run_inline,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    video.ai_job_id = job_data.get("job_id")
    if job_data.get("status") == "succeeded" and job_data.get("result"):
        apply_chapterization_result_to_video(db, video, job_data["result"])
    elif job_data.get("status") == "failed":
        video.outline_status = OutlineStatus.FAILED

    record_audit(
        db,
        actor=teacher,
        action="COMPLETE_CHUNK_VIDEO_UPLOAD",
        target_type="video_resource",
        target_id=video.id,
        detail=f"分片合并上传视频《{resolved_title}》({sess.total_chunks} 片, {size_mb}MB) 并启动 AI 章节化 ({video.ai_job_id})",
    )
    db.commit()
    return {
        "video_id": video.id,
        "title": video.title,
        "media_url": video.media_url,
        "duration_sec": video.duration_sec,
        "file_size_mb": size_mb,
        "outline_status": video.outline_status,
        "ai_job_id": video.ai_job_id,
        "job_status": job_data.get("status"),
    }


async def regenerate_single_chapter(
    db: Session,
    *,
    teacher: User,
    video_id: str,
    start_sec: int,
    end_sec: int,
    current_title: str = "",
    instruction: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频资源不存在", status_code=404)
    ensure_course_owner_or_admin(db, video.course_id, teacher)

    s_sec = max(0, int(start_sec))
    e_sec = max(s_sec + 1, int(end_sec))
    all_segs = video.transcript_json or []
    matched_segs = [
        seg
        for seg in all_segs
        if int(seg.get("end_sec", int(seg.get("start_sec", 0)) + 1)) > s_sec and int(seg.get("start_sec", 0)) < e_sec
    ]
    if not matched_segs and all_segs:
        matched_segs = all_segs[:10]
    transcript_text = "\n".join(str(seg.get("text", "")).strip() for seg in matched_segs if seg.get("text"))

    raw_res = await ai_client.regenerate_chapter(
        {
            "course_id": video.course_id,
            "video_id": video.id,
            "video_title": video.title,
            "start_sec": s_sec,
            "end_sec": e_sec,
            "current_title": current_title,
            "instruction": instruction,
            "transcript_segments": matched_segs,
            "transcript_text": transcript_text,
        },
        request_id=request_id,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    ch_obj = raw_res.get("chapter") if isinstance(raw_res.get("chapter"), dict) else raw_res
    result = {
        "title": str(ch_obj.get("title") or current_title or "重生成章节").strip(),
        "summary": str(ch_obj.get("summary") or "").strip(),
        "start_sec": int(ch_obj.get("start_sec", s_sec)),
        "end_sec": int(ch_obj.get("end_sec", e_sec)),
        "knowledge_points": ch_obj.get("knowledge_points") or [],
        "degraded": bool(raw_res.get("degraded", False)),
    }
    record_audit(
        db,
        actor=teacher,
        action="REGENERATE_VIDEO_CHAPTER",
        target_type="video_resource",
        target_id=video.id,
        detail=f"按时间段 [{s_sec}s-{e_sec}s] 重新生成单章提纲: {result['title']}",
    )
    db.commit()
    return result


async def sync_video_ai_job(
    db: Session,
    *,
    teacher: User,
    video_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """主动向 AI 服务轮询该视频异步任务进度（当后台 ASR 完成时自动落库初稿版本）。"""
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频不存在", status_code=404)
    ensure_course_owner_or_admin(db, video.course_id, teacher)

    if not video.ai_job_id:
        return {"video_id": video.id, "outline_status": video.outline_status, "job_status": "none", "progress": 100}

    job_data = await ai_client.get_job(video.ai_job_id, request_id=request_id)
    if not job_data:
        return {"video_id": video.id, "outline_status": video.outline_status, "job_status": "unreachable", "progress": 0}

    j_status = job_data.get("status", "running")
    if j_status == "succeeded" and job_data.get("result") and video.outline_status == OutlineStatus.PROCESSING:
        apply_chapterization_result_to_video(db, video, job_data["result"])
        db.commit()
    elif j_status == "failed" and video.outline_status == OutlineStatus.PROCESSING:
        video.outline_status = OutlineStatus.FAILED
        db.commit()

    return {
        "video_id": video.id,
        "outline_status": video.outline_status,
        "job_status": j_status,
        "progress": job_data.get("progress", 0),
        "error_message": job_data.get("error_message"),
    }


def query_video_outline_versions(db: Session, *, teacher: User, video_id: str) -> dict[str, Any]:
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频资源不存在", status_code=404)
    ensure_course_owner_or_admin(db, video.course_id, teacher)
    versions = db.scalars(
        select(OutlineVersion)
        .where(OutlineVersion.video_id == video_id)
        .order_by(OutlineVersion.version_no.desc())
    ).all()
    return {
        "video": {
            "id": video.id,
            "title": video.title,
            "duration_sec": video.duration_sec,
            "outline_status": video.outline_status,
            "published_version_id": video.published_version_id,
            "transcript_segments": video.transcript_json or [],
        },
        "versions": [
            {
                "id": v.id,
                "version_no": v.version_no,
                "source": v.source,
                "chapters": v.chapters_json,
                "change_note": v.change_note,
                "is_published": v.is_published,
                "created_by": v.created_by,
                "created_at": v.created_at,
            }
            for v in versions
        ],
    }


def save_calibrated_outline(
    db: Session,
    *,
    teacher: User,
    video_id: str,
    chapters: list[dict[str, Any]],
    change_note: str,
) -> OutlineVersion:
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频资源不存在", status_code=404)
    ensure_course_owner_or_admin(db, video.course_id, teacher)

    cleaned = [
        {
            "chapter_id": ch.get("chapter_id") or f"ch_{video.id}_{uuid.uuid4().hex[:6]}",
            "order_index": idx + 1,
            "title": str(ch.get("title", "")).strip() or f"第 {idx + 1} 节",
            "summary": str(ch.get("summary", "")).strip(),
            "start_sec": max(0, int(ch.get("start_sec", 0))),
            "end_sec": max(1, int(ch.get("end_sec", video.duration_sec))),
            "knowledge_points": ch.get("knowledge_points") or [],
        }
        for idx, ch in enumerate(chapters)
    ]

    max_ver = db.scalar(select(func.max(OutlineVersion.version_no)).where(OutlineVersion.video_id == video.id)) or 0
    ver = OutlineVersion(
        video_id=video.id,
        version_no=max_ver + 1,
        source="teacher_calibrated",
        chapters_json=cleaned,
        change_note=change_note or "教师人工编辑校准章节与时间戳",
        is_published=False,
        created_by=teacher.id,
    )
    db.add(ver)
    db.flush()
    if video.outline_status in (OutlineStatus.FAILED, OutlineStatus.PROCESSING):
        video.outline_status = OutlineStatus.DRAFT_REVIEW

    record_audit(
        db,
        actor=teacher,
        action="CALIBRATE_VIDEO_OUTLINE",
        target_type="video_resource",
        target_id=video.id,
        detail=f"保存第 v{ver.version_no} 版人工校准提纲（共 {len(cleaned)} 节）",
    )
    db.commit()
    return ver


async def publish_outline_version(
    db: Session,
    *,
    teacher: User,
    video_id: str,
    version_id: str,
    request_id: str | None = None,
) -> OutlineVersion:
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频资源不存在", status_code=404)
    ensure_course_owner_or_admin(db, video.course_id, teacher)

    target_ver = db.get(OutlineVersion, version_id)
    if not target_ver or target_ver.video_id != video.id:
        raise PlatformError("OUTLINE_VERSION_NOT_FOUND", "指定的提纲历史版本不存在", status_code=404)

    all_vers = db.scalars(select(OutlineVersion).where(OutlineVersion.video_id == video.id)).all()
    for v in all_vers:
        v.is_published = v.id == target_ver.id

    video.published_version_id = target_ver.id
    video.outline_status = OutlineStatus.PUBLISHED

    if not video.is_hidden:
        for ch in target_ver.chapters_json:
            ch_start = int(ch.get("start_sec", 0))
            ch_end = int(ch.get("end_sec", video.duration_sec))
            seg_text = "\n".join(
                s["text"]
                for s in (video.transcript_json or [])
                if int(s.get("start_sec", 0)) >= ch_start and int(s.get("start_sec", 0)) < max(ch_end, ch_start + 1)
            )
            await ai_client.upsert_document(
                course_id=video.course_id,
                document_id=f"transcript_{video.id}_{ch.get('order_index', 1)}",
                title=f"{video.title} - {ch.get('title', '')}",
                source_type="video_transcript",
                content=f"{ch.get('title', '')}\n{ch.get('summary', '')}\n{seg_text}",
                metadata={
                    "video_id": video.id,
                    "chapter_id": ch.get("chapter_id"),
                    "start_sec": ch_start,
                },
                request_id=request_id,
                caller_role=teacher.role,
                caller_id=teacher.id,
            )

    record_audit(
        db,
        actor=teacher,
        action="PUBLISH_VIDEO_OUTLINE",
        target_type="video_resource",
        target_id=video.id,
        detail=f"正式发布提纲版本 v{target_ver.version_no} 对学生端可见",
    )
    db.commit()
    return target_ver


async def upload_course_document(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    title: str,
    file_format: str,
    content_text: str,
    request_id: str | None = None,
) -> CourseDocument:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    est_mb = round(max(0.2, len(content_text) / 50000), 2)
    check_course_storage_quota(course, est_mb)

    doc = CourseDocument(
        course_id=course_id,
        title=title,
        file_format=file_format,
        content_text=content_text,
        index_status=DocumentIndexStatus.INDEXING,
        file_size_mb=est_mb,
    )
    db.add(doc)
    db.flush()
    course.storage_used_mb = round((course.storage_used_mb or 0.0) + doc.file_size_mb, 2)

    idx_res = await ai_client.upsert_document(
        course_id=course_id,
        document_id=doc.id,
        title=title,
        source_type="document",
        content=content_text,
        metadata={"file_format": file_format},
        request_id=request_id,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    doc.index_status = DocumentIndexStatus.READY if not idx_res.get("degraded") else DocumentIndexStatus.STALE
    record_audit(
        db,
        actor=teacher,
        action="UPLOAD_COURSE_DOCUMENT",
        target_type="course_document",
        target_id=doc.id,
        detail=f"上传课程讲义《{title}》并构建 RAG 知识索引 ({doc.index_status})",
    )
    db.commit()
    return doc


async def upload_real_document_file(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    document_file: UploadFile,
    title: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    orig_name = document_file.filename or "document.pdf"
    raw_bytes, rel_file_url, size_mb = await save_document_upload(document_file)
    try:
        check_course_storage_quota(course, size_mb)
        file_format, extracted_text = parse_document_bytes(orig_name, raw_bytes)
    except PlatformError:
        unlink_media_rel_url(rel_file_url)
        raise

    resolved_title = title.strip() or orig_name
    doc = CourseDocument(
        course_id=course_id,
        title=resolved_title,
        file_format=file_format,
        file_url=rel_file_url,
        content_text=extracted_text,
        index_status=DocumentIndexStatus.INDEXING,
        file_size_mb=size_mb,
    )
    db.add(doc)
    db.flush()
    course.storage_used_mb = round((course.storage_used_mb or 0.0) + size_mb, 2)

    idx_res = await ai_client.upsert_document(
        course_id=course_id,
        document_id=doc.id,
        title=resolved_title,
        source_type="document",
        content=extracted_text,
        metadata={"file_format": file_format, "file_url": rel_file_url},
        request_id=request_id,
        caller_role=teacher.role,
        caller_id=teacher.id,
    )
    doc.index_status = DocumentIndexStatus.READY if not idx_res.get("degraded") else DocumentIndexStatus.STALE
    record_audit(
        db,
        actor=teacher,
        action="UPLOAD_REAL_DOCUMENT_FILE",
        target_type="course_document",
        target_id=doc.id,
        detail=f"上传真实资料文件《{resolved_title}》({file_format}, {size_mb}MB, 提取 {len(extracted_text)} 字) 并构建 RAG 索引",
    )
    db.commit()
    return {
        "id": doc.id,
        "title": doc.title,
        "file_format": doc.file_format,
        "file_url": doc.file_url,
        "file_size_mb": doc.file_size_mb,
        "extracted_chars": len(extracted_text),
        "chunk_count": idx_res.get("document_chunk_count", 0),
        "index_status": doc.index_status,
    }


async def delete_course_document(
    db: Session,
    *,
    teacher: User,
    document_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    doc = db.get(CourseDocument, document_id)
    if not doc:
        raise PlatformError("DOCUMENT_NOT_FOUND", "讲义资料不存在", status_code=404)
    course = ensure_course_owner_or_admin(db, doc.course_id, teacher)

    if doc.index_status != DocumentIndexStatus.REMOVED:
        course.storage_used_mb = round(max(0.0, (course.storage_used_mb or 0.0) - (doc.file_size_mb or 0.0)), 2)

    unlink_media_rel_url(doc.file_url)
    await ai_client.delete_document(course_id=doc.course_id, document_id=doc.id, request_id=request_id)
    title_bak = doc.title
    db.delete(doc)
    record_audit(
        db,
        actor=teacher,
        action="DELETE_COURSE_DOCUMENT",
        target_type="course_document",
        target_id=document_id,
        detail=f"删除讲义《{title_bak}》并清理 AI 知识库索引",
    )
    db.commit()
    return {"deleted": True, "id": document_id, "storage_used_mb": course.storage_used_mb}
