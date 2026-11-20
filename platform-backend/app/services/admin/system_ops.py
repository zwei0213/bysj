"""管理端域 6：系统公告、工单闭环、审计日志与全景健康探针。"""

import os
from pathlib import Path
import platform
import shutil
import sys
import time
from typing import Any
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.config import settings
from app.core.database import engine
from app.core.errors import PlatformError, utc_now_iso
from app.core.rate_limit import get_role_ai_rate_limits
from app.models.entities import Announcement, AuditLog, Course, CourseDocument, QuestionBankItem, QuizSession, SupportTicket, TutorMessage, User, VideoResource
from app.services.audit_helper import record_audit
from app.services.catalog_service import create_user_notification
from app.services.storage_service import ensure_storage_dirs

_PROCESS_START_MONOTONIC = time.monotonic()

def query_all_announcements(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(select(Announcement).order_by(Announcement.created_at.desc())).all()
    return [
        {
            "id": a.id,
            "title": a.title,
            "content": a.content,
            "target_role": a.target_role,
            "is_active": a.is_active,
            "created_at": a.created_at,
        }
        for a in rows
    ]


def create_system_announcement(
    db: Session,
    *,
    admin: User,
    title: str,
    content: str,
    target_role: str,
) -> dict[str, str]:
    ann = Announcement(title=title.strip(), content=content.strip(), target_role=target_role, is_active=True)
    db.add(ann)
    db.flush()
    record_audit(db, actor=admin, action="PUBLISH_ANNOUNCEMENT", target_type="announcement", target_id=ann.id, detail=ann.title)
    db.commit()
    return {"id": ann.id, "title": ann.title}


def delete_system_announcement(db: Session, *, admin: User, ann_id: str) -> dict[str, Any]:
    ann = db.get(Announcement, ann_id)
    if not ann:
        raise PlatformError("ANNOUNCEMENT_NOT_FOUND", "系统公告不存在", status_code=404)
    title_bak = ann.title
    db.delete(ann)
    record_audit(db, actor=admin, action="DELETE_ANNOUNCEMENT", target_type="announcement", target_id=ann_id, detail=title_bak)
    db.commit()
    return {"deleted": True, "id": ann_id}


def query_support_tickets(db: Session) -> list[dict[str, Any]]:
    rows = list(db.scalars(select(SupportTicket).order_by(SupportTicket.created_at.desc())).all())
    sub_ids = list({t.submitter_id for t in rows})
    sub_map = (
        {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(sub_ids))).all()}
        if sub_ids
        else {}
    )
    return [
        {
            "id": t.id,
            "submitter_id": t.submitter_id,
            "submitter_name": sub_map.get(t.submitter_id, t.submitter_id),
            "category": t.category,
            "subject": t.subject,
            "description": t.description,
            "status": t.status,
            "assignee_id": t.assignee_id,
            "assignee_name": t.assignee_name,
            "assignee_note": t.assignee_note,
            "created_at": t.created_at,
            "updated_at": t.updated_at or t.created_at,
        }
        for t in rows
    ]


def update_ticket_status(
    db: Session,
    *,
    admin: User,
    ticket_id: str,
    status: str | None = None,
    assignee_id: str | None = None,
    assignee_name: str | None = None,
    assignee_note: str | None = None,
) -> dict[str, Any]:
    tkt = db.get(SupportTicket, ticket_id)
    if not tkt:
        raise PlatformError("TICKET_NOT_FOUND", "工单不存在", status_code=404)

    if status is not None:
        tkt.status = status
    if assignee_id is not None:
        tkt.assignee_id = assignee_id or None
        if assignee_id and not assignee_name:
            assignee_user = db.get(User, assignee_id)
            tkt.assignee_name = assignee_user.display_name if assignee_user else admin.display_name
    if assignee_name is not None:
        tkt.assignee_name = assignee_name.strip()
    elif not tkt.assignee_name:
        tkt.assignee_name = admin.display_name
        tkt.assignee_id = tkt.assignee_id or admin.id
    if assignee_note is not None:
        tkt.assignee_note = assignee_note.strip()
    tkt.updated_at = utc_now_iso()

    reply_info = f"处理回复：{tkt.assignee_note}" if tkt.assignee_note else f"当前负责处理人：{tkt.assignee_name}"
    create_user_notification(
        db,
        user_id=tkt.submitter_id,
        title=f"工单进度更新：{tkt.subject}",
        content=f"您提交的工单状态已变更为 [{tkt.status}]。{reply_info}",
        notif_type="support_ticket",
    )

    record_audit(
        db,
        actor=admin,
        action="RESOLVE_TICKET",
        target_type="support_ticket",
        target_id=tkt.id,
        detail=f"status={tkt.status}, assignee={tkt.assignee_name}",
    )
    db.commit()
    return {
        "id": tkt.id,
        "status": tkt.status,
        "assignee_id": tkt.assignee_id,
        "assignee_name": tkt.assignee_name,
        "assignee_note": tkt.assignee_note,
        "updated_at": tkt.updated_at,
    }


def list_recent_audit_logs(
    db: Session,
    *,
    actor_role: str | None = None,
    action: str | None = None,
    keyword: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if actor_role:
        stmt = stmt.where(AuditLog.actor_role == actor_role)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                AuditLog.actor_name.ilike(kw),
                AuditLog.action.ilike(kw),
                AuditLog.target_id.ilike(kw),
                AuditLog.detail.ilike(kw),
            )
        )
    if start_date:
        stmt = stmt.where(AuditLog.created_at >= str(start_date)[:10])
    if end_date:
        stmt = stmt.where(AuditLog.created_at <= f"{str(end_date)[:10]}T23:59:59Z")

    logs = db.scalars(stmt.limit(max(1, min(500, limit)))).all()
    return [
        {
            "id": l.id,
            "actor_id": l.actor_id,
            "actor_name": l.actor_name,
            "actor_role": l.actor_role,
            "action": l.action,
            "target_type": l.target_type,
            "target_id": l.target_id,
            "detail": l.detail,
            "created_at": l.created_at,
        }
        for l in logs
    ]


def list_active_announcements(db: Session, role: str | None = None) -> list[Announcement]:
    stmt = select(Announcement).where(Announcement.is_active.is_(True))
    if role:
        stmt = stmt.where(Announcement.target_role.in_(["all", role]))
    return list(db.scalars(stmt.order_by(Announcement.created_at.desc())).all())


async def build_system_status_panorama(db: Session, *, request_id: str | None = None) -> dict[str, Any]:
    db_ok = True
    db_t0 = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    db_latency_ms = round((time.perf_counter() - db_t0) * 1000, 2)

    table_counts = {
        "users": int(db.scalar(select(func.count()).select_from(User)) or 0),
        "courses": int(db.scalar(select(func.count()).select_from(Course)) or 0),
        "videos": int(db.scalar(select(func.count()).select_from(VideoResource)) or 0),
        "documents": int(db.scalar(select(func.count()).select_from(CourseDocument)) or 0),
        "questions": int(db.scalar(select(func.count()).select_from(QuestionBankItem)) or 0),
        "quiz_sessions": int(db.scalar(select(func.count()).select_from(QuizSession)) or 0),
        "tutor_messages": int(db.scalar(select(func.count()).select_from(TutorMessage)) or 0),
        "audit_logs": int(db.scalar(select(func.count()).select_from(AuditLog)) or 0),
    }

    storage_root = ensure_storage_dirs()
    file_count = 0
    used_bytes = 0
    for fp in Path(storage_root).rglob("*"):
        if fp.is_file():
            file_count += 1
            used_bytes += fp.stat().st_size
    disk_usage = shutil.disk_usage(storage_root)

    ai_health = await ai_client.ops_health(request_id=request_id)
    ai_runtime = await ai_client.ops_get_runtime_config(request_id=request_id)

    return {
        "backend": {
            "service": settings.service_name,
            "version": settings.version,
            "app_env": settings.app_env,
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
            "uptime_sec": round(time.monotonic() - _PROCESS_START_MONOTONIC, 1),
        },
        "database": {
            "status": "healthy" if db_ok else "unreachable",
            "connected": db_ok,
            "latency_ms": db_latency_ms,
            "dialect": engine.dialect.name,
            "table_counts": table_counts,
        },
        "storage": {
            "storage_dir": str(storage_root),
            "file_count": file_count,
            "used_mb": round(used_bytes / (1024 * 1024), 2),
            "disk_total_gb": round(disk_usage.total / (1024**3), 2),
            "disk_free_gb": round(disk_usage.free / (1024**3), 2),
        },
        "ai_service": {
            "health": ai_health,
            "runtime_config": ai_runtime,
            "rate_limit_per_minute": get_role_ai_rate_limits(),
        },
    }
