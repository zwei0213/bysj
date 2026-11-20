"""管理端域 3 & 4：AI 任务/索引治理、质量样本复核与随机抽检。"""

from collections import defaultdict
import math
import random
from typing import Any
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import DocumentIndexStatus
from app.core.errors import PlatformError
from app.models.entities import Course, CourseDocument, OutlineVersion, QualitySample, QuestionBankItem, TutorMessage, User, VideoResource
from app.services.audit_helper import record_audit
from app.services.student_service import parse_flexible_datetime

from app.services.admin.overview_and_reports import _in_date_window

async def list_admin_ai_jobs(
    *,
    status: str | None = None,
    course_id: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    data = await ai_client.ops_list_jobs(
        status=status,
        course_id=course_id,
        start_time=start_time,
        end_time=end_time,
        request_id=request_id,
    )
    items = list(data.get("items") or [])
    if start_time or end_time:
        s_dt = parse_flexible_datetime(start_time)
        e_dt = parse_flexible_datetime(end_time)
        filtered: list[dict[str, Any]] = []
        for job in items:
            j_ts = job.get("created_at") or job.get("updated_at")
            j_dt = parse_flexible_datetime(j_ts)
            if j_dt is not None:
                if s_dt is not None:
                    cmp_s = s_dt if (s_dt.tzinfo is not None) == (j_dt.tzinfo is not None) else s_dt.replace(tzinfo=j_dt.tzinfo)
                    if j_dt < cmp_s:
                        continue
                if e_dt is not None:
                    cmp_e = e_dt if (e_dt.tzinfo is not None) == (j_dt.tzinfo is not None) else e_dt.replace(tzinfo=j_dt.tzinfo)
                    if j_dt > cmp_e:
                        continue
            elif not _in_date_window(str(j_ts or ""), start_time, end_time):
                continue
            filtered.append(job)
        items = filtered
    return {"items": items}


async def list_merged_ai_indexes(db: Session, *, request_id: str | None = None) -> dict[str, Any]:
    ai_data = await ai_client.ops_list_indexes(request_id=request_id)
    ai_items = {str(it.get("course_id")): dict(it) for it in (ai_data.get("items") or []) if it.get("course_id")}

    courses = list(db.scalars(select(Course).order_by(Course.created_at.desc())).all())
    doc_counts = dict(
        db.execute(
            select(CourseDocument.course_id, func.count())
            .where(CourseDocument.index_status != DocumentIndexStatus.REMOVED)
            .group_by(CourseDocument.course_id)
        ).all()
    )

    merged: list[dict[str, Any]] = []
    seen_course_ids: set[str] = set()
    for c in courses:
        seen_course_ids.add(c.id)
        if c.id in ai_items:
            entry = ai_items[c.id]
            entry.setdefault("course_title", c.title)
            entry.setdefault("course_status", c.status)
            merged.append(entry)
        else:
            merged.append({
                "course_id": c.id,
                "course_title": c.title,
                "course_status": c.status,
                "status": "unindexed",
                "document_count": int(doc_counts.get(c.id, 0)),
                "chunk_count": 0,
                "updated_at": c.updated_at,
            })

    for cid, extra in ai_items.items():
        if cid not in seen_course_ids:
            merged.append(extra)

    return {"items": merged}


def query_quality_samples(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(QualitySample).order_by(QualitySample.created_at.desc())).all()
    return [
        {
            "id": r.id,
            "course_id": r.course_id,
            "message_id": r.message_id,
            "sample_type": r.sample_type,
            "source_reason": r.source_reason,
            "input_text": r.input_text,
            "model_output": r.model_output,
            "corrected_output": r.corrected_output,
            "status": r.status,
            "admin_note": r.admin_note,
            "synced_to_ai_eval": r.synced_to_ai_eval,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def draw_spot_check_candidates(
    db: Session,
    *,
    sample_type: str = "all",
    sample_ratio_pct: float = 20.0,
    max_count: int = 10,
    course_id: str | None = None,
) -> dict[str, Any]:
    pool: list[dict[str, Any]] = []

    if sample_type in ("all", "tutor_reply"):
        msg_stmt = select(TutorMessage).order_by(TutorMessage.created_at.asc())
        if course_id:
            msg_stmt = msg_stmt.where(TutorMessage.course_id == course_id)
        all_msgs = list(db.scalars(msg_stmt).all())
        by_thread: dict[str, list[TutorMessage]] = defaultdict(list)
        for m in all_msgs:
            by_thread[m.thread_id].append(m)
        for t_msgs in by_thread.values():
            for idx, m in enumerate(t_msgs):
                if m.role != "assistant":
                    continue
                prev_q = t_msgs[idx - 1].content if idx > 0 and t_msgs[idx - 1].role == "user" else "课程提问"
                pool.append({
                    "candidate_id": f"cand_msg_{m.id}",
                    "course_id": m.course_id,
                    "sample_type": "tutor_reply",
                    "message_id": m.id,
                    "input_text": prev_q,
                    "model_output": m.content,
                    "confidence": m.confidence,
                    "is_blocked": bool(m.is_blocked),
                    "created_at": m.created_at,
                })

    if sample_type in ("all", "outline"):
        vid_stmt = select(VideoResource)
        if course_id:
            vid_stmt = vid_stmt.where(VideoResource.course_id == course_id)
        vid_map = {v.id: v for v in db.scalars(vid_stmt).all()}
        if vid_map:
            vers = db.scalars(
                select(OutlineVersion).where(OutlineVersion.video_id.in_(list(vid_map.keys())))
            ).all()
            for ver in vers:
                vid = vid_map.get(ver.video_id)
                if not vid:
                    continue
                ch_lines = [
                    f"[{ch.get('start_sec', 0)}s-{ch.get('end_sec', 0)}s] {ch.get('title', '')}: {ch.get('summary', '')}"
                    for ch in (ver.chapters_json or [])[:6]
                ]
                pool.append({
                    "candidate_id": f"cand_olv_{ver.id}",
                    "course_id": vid.course_id,
                    "sample_type": "outline",
                    "message_id": None,
                    "input_text": f"视频《{vid.title}》章节提纲 (v{ver.version_no}, {ver.source})",
                    "model_output": "\n".join(ch_lines) or "空章节提纲",
                    "confidence": 1.0,
                    "is_blocked": False,
                    "created_at": ver.created_at,
                })

    if sample_type in ("all", "question"):
        q_stmt = select(QuestionBankItem).where(QuestionBankItem.source == "ai")
        if course_id:
            q_stmt = q_stmt.where(QuestionBankItem.course_id == course_id)
        for q in db.scalars(q_stmt).all():
            pool.append({
                "candidate_id": f"cand_qst_{q.id}",
                "course_id": q.course_id,
                "sample_type": "question",
                "message_id": None,
                "input_text": f"[{q.knowledge_point}] {q.stem}",
                "model_output": f"选项: {q.options_json} | 标准答案: {q.answer_json} | 解析: {q.explanation}",
                "confidence": 1.0,
                "is_blocked": False,
                "created_at": q.created_at,
            })

    total_pool = len(pool)
    if total_pool == 0:
        return {
            "total_pool_size": 0,
            "drawn_count": 0,
            "sample_ratio_pct": sample_ratio_pct,
            "items": [],
        }

    draw_k = min(max_count, total_pool, max(1, math.ceil(total_pool * (sample_ratio_pct / 100.0))))
    drawn = random.sample(pool, draw_k)
    return {
        "total_pool_size": total_pool,
        "drawn_count": len(drawn),
        "sample_ratio_pct": sample_ratio_pct,
        "items": drawn,
    }


async def create_spot_check(
    db: Session,
    *,
    admin: User,
    course_id: str,
    sample_type: str,
    input_text: str,
    model_output: str,
    admin_note: str,
    message_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    sample = QualitySample(
        course_id=course_id,
        message_id=message_id,
        sample_type=sample_type,
        source_reason="spot_check",
        input_text=input_text,
        model_output=model_output,
        admin_note=admin_note,
        status="synced_eval",
    )
    db.add(sample)
    db.flush()
    res = await ai_client.ops_submit_feedback_sample(
        {
            "sample_id": sample.id,
            "course_id": sample.course_id,
            "sample_type": sample.sample_type,
            "source_reason": "spot_check",
            "input_text": sample.input_text,
            "model_output": sample.model_output,
            "expected_correction": admin_note or None,
        },
        request_id=request_id,
    )
    sample.synced_to_ai_eval = not res.get("degraded", False)
    record_audit(
        db,
        actor=admin,
        action="FLAG_SPOT_CHECK_SAMPLE",
        target_type="quality_sample",
        target_id=sample.id,
        detail="抽检标记问题样本回流评测池",
    )
    db.commit()
    return {"id": sample.id, "synced_to_ai_eval": sample.synced_to_ai_eval}


async def resolve_sample_item(
    db: Session,
    *,
    admin: User,
    sample_id: str,
    status: str,
    admin_note: str = "",
    corrected_output: str = "",
    request_id: str | None = None,
) -> dict[str, Any]:
    sample = db.get(QualitySample, sample_id)
    if not sample:
        raise PlatformError("SAMPLE_NOT_FOUND", "问题样本不存在", status_code=404)

    sample.status = status
    if admin_note:
        sample.admin_note = admin_note
    if corrected_output:
        sample.corrected_output = corrected_output

    blocked_message = False
    if status == "blocked" and sample.message_id:
        msg = db.get(TutorMessage, sample.message_id)
        if msg:
            msg.is_blocked = True
            blocked_message = True

    if status in ("corrected", "synced_eval") and (
        corrected_output or admin_note or not sample.synced_to_ai_eval
    ):
        res = await ai_client.ops_submit_feedback_sample(
            {
                "sample_id": sample.id,
                "course_id": sample.course_id,
                "sample_type": sample.sample_type,
                "source_reason": sample.source_reason,
                "input_text": sample.input_text,
                "model_output": sample.model_output,
                "expected_correction": corrected_output or admin_note,
            },
            request_id=request_id,
        )
        sample.synced_to_ai_eval = not res.get("degraded", False)

    record_audit(
        db,
        actor=admin,
        action="RESOLVE_QUALITY_SAMPLE",
        target_type="quality_sample",
        target_id=sample.id,
        detail=f"处置为 {sample.status} (message_blocked={blocked_message})",
    )
    db.commit()
    return {
        "id": sample.id,
        "status": sample.status,
        "message_id": sample.message_id,
        "message_blocked": blocked_message,
        "corrected_output": sample.corrected_output,
        "synced_to_ai_eval": sample.synced_to_ai_eval,
    }
