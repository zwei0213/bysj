"""管理端域 2：课程审核上架、学科分类、违规资源下架与过期清理。"""

from datetime import datetime
import re
import time
from typing import Any
from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from app.clients.ai_service_client import ai_client
from app.core.enums import CourseStatus, DocumentIndexStatus
from app.core.errors import PlatformError, utc_now_iso
from app.models.entities import Course, CourseCategory, CourseDocument, OutlineVersion, User, VideoResource
from app.services.audit_helper import record_audit
from app.services.catalog_service import create_user_notification
from app.services.storage_service import unlink_media_rel_url
from app.services.student_service import parse_flexible_datetime

def query_all_courses_for_governance(db: Session) -> list[dict[str, Any]]:
    courses = list(db.scalars(select(Course).order_by(Course.created_at.desc())).all())
    teacher_ids = list({c.teacher_id for c in courses})
    teacher_map = (
        {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(teacher_ids))).all()}
        if teacher_ids
        else {}
    )
    return [
        {
            "id": c.id,
            "code": c.code,
            "title": c.title,
            "subtitle": c.subtitle,
            "category": c.category,
            "cover_url": c.cover_url,
            "status": c.status,
            "reject_reason": c.reject_reason,
            "is_featured": c.is_featured,
            "teacher_id": c.teacher_id,
            "teacher_name": teacher_map.get(c.teacher_id, ""),
            "storage_used_mb": c.storage_used_mb,
            "storage_quota_mb": c.storage_quota_mb,
            "start_date": c.start_date,
            "end_date": c.end_date,
            "expires_at": c.expires_at,
            "created_at": c.created_at,
        }
        for c in courses
    ]


def apply_course_governance(
    db: Session,
    *,
    admin: User,
    course_id: str,
    status: str | None = None,
    reject_reason: str | None = None,
    is_featured: bool | None = None,
    storage_quota_mb: int | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    course = db.get(Course, course_id)
    if not course:
        raise PlatformError("COURSE_NOT_FOUND", "课程不存在", status_code=404)
    prev_status = course.status
    if status is not None:
        course.status = status
        if status == CourseStatus.PUBLISHED:
            course.reject_reason = ""
    if reject_reason is not None:
        course.reject_reason = reject_reason.strip()
    if is_featured is not None:
        course.is_featured = is_featured
    if storage_quota_mb is not None:
        course.storage_quota_mb = storage_quota_mb
    if expires_at is not None:
        course.expires_at = expires_at.strip() or None
    course.updated_at = utc_now_iso()

    if status is not None and status != prev_status:
        reason_tail = f"（说明：{course.reject_reason}）" if course.reject_reason else ""
        create_user_notification(
            db,
            user_id=course.teacher_id,
            title=f"课程审核与状态变更：《{course.title}》",
            content=f"您的课程《{course.title}》状态已更新为：{course.status}{reason_tail}",
            notif_type="course_governance",
        )

    record_audit(
        db,
        actor=admin,
        action="GOVERN_COURSE",
        target_type="course",
        target_id=course.id,
        detail=f"课程《{course.title}》状态={course.status}, 精品={course.is_featured}, 配额={course.storage_quota_mb}MB",
    )
    db.commit()
    return {
        "id": course.id,
        "status": course.status,
        "reject_reason": course.reject_reason,
        "is_featured": course.is_featured,
        "storage_quota_mb": course.storage_quota_mb,
        "expires_at": course.expires_at,
    }


def query_all_categories(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(CourseCategory).order_by(CourseCategory.sort_order.asc(), CourseCategory.created_at.asc())
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "sort_order": r.sort_order,
            "is_visible": r.is_visible,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def _slugify(text_val: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text_val.strip().lower()).strip("-")
    return cleaned or f"cat-{int(time.time())}"


def create_category(
    db: Session,
    *,
    admin: User,
    name: str,
    slug: str = "",
    sort_order: int = 0,
    is_visible: bool = True,
) -> dict[str, Any]:
    clean_name = name.strip()
    clean_slug = _slugify(slug or clean_name)
    if db.scalar(select(CourseCategory).where(or_(CourseCategory.name == clean_name, CourseCategory.slug == clean_slug))):
        raise PlatformError("CATEGORY_CONFLICT", "分类名称或标识 slug 已存在", status_code=409)
    cat = CourseCategory(name=clean_name, slug=clean_slug, sort_order=sort_order, is_visible=is_visible)
    db.add(cat)
    db.flush()
    record_audit(db, actor=admin, action="CREATE_CATEGORY", target_type="course_category", target_id=cat.id, detail=cat.name)
    db.commit()
    return {
        "id": cat.id,
        "name": cat.name,
        "slug": cat.slug,
        "sort_order": cat.sort_order,
        "is_visible": cat.is_visible,
    }


def update_category(
    db: Session,
    *,
    admin: User,
    cat_id: str,
    name: str | None = None,
    slug: str | None = None,
    sort_order: int | None = None,
    is_visible: bool | None = None,
) -> dict[str, Any]:
    cat = db.get(CourseCategory, cat_id)
    if not cat:
        raise PlatformError("CATEGORY_NOT_FOUND", "课程分类不存在", status_code=404)
    if name is not None and name.strip():
        cat.name = name.strip()
    if slug is not None and slug.strip():
        cat.slug = _slugify(slug)
    if sort_order is not None:
        cat.sort_order = sort_order
    if is_visible is not None:
        cat.is_visible = is_visible
    record_audit(db, actor=admin, action="UPDATE_CATEGORY", target_type="course_category", target_id=cat.id, detail=cat.name)
    db.commit()
    return {
        "id": cat.id,
        "name": cat.name,
        "slug": cat.slug,
        "sort_order": cat.sort_order,
        "is_visible": cat.is_visible,
    }


def delete_category(db: Session, *, admin: User, cat_id: str) -> dict[str, Any]:
    cat = db.get(CourseCategory, cat_id)
    if not cat:
        raise PlatformError("CATEGORY_NOT_FOUND", "课程分类不存在", status_code=404)
    name_bak = cat.name
    db.delete(cat)
    record_audit(db, actor=admin, action="DELETE_CATEGORY", target_type="course_category", target_id=cat_id, detail=name_bak)
    db.commit()
    return {"deleted": True, "id": cat_id}


def query_course_resources_for_admin(db: Session, *, course_id: str) -> dict[str, Any]:
    course = db.get(Course, course_id)
    if not course:
        raise PlatformError("COURSE_NOT_FOUND", "课程不存在", status_code=404)
    videos = db.scalars(
        select(VideoResource).where(VideoResource.course_id == course_id).order_by(VideoResource.order_index)
    ).all()
    docs = db.scalars(
        select(CourseDocument).where(CourseDocument.course_id == course_id).order_by(CourseDocument.created_at.desc())
    ).all()
    return {
        "course_id": course.id,
        "title": course.title,
        "videos": [
            {
                "id": v.id,
                "title": v.title,
                "media_url": v.media_url,
                "duration_sec": v.duration_sec,
                "file_size_mb": v.file_size_mb,
                "outline_status": v.outline_status,
                "is_hidden": bool(v.is_hidden),
                "created_at": v.created_at,
            }
            for v in videos
        ],
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "file_format": d.file_format,
                "file_url": d.file_url,
                "index_status": d.index_status,
                "file_size_mb": d.file_size_mb,
                "created_at": d.created_at,
            }
            for d in docs
        ],
    }


async def takedown_course_document(
    db: Session,
    *,
    admin: User,
    document_id: str,
    reason: str,
    request_id: str | None = None,
) -> CourseDocument:
    doc = db.get(CourseDocument, document_id)
    if not doc:
        raise PlatformError("DOCUMENT_NOT_FOUND", "课程资料不存在", status_code=404)
    course = db.get(Course, doc.course_id)
    if course and doc.index_status != DocumentIndexStatus.REMOVED:
        course.storage_used_mb = round(max(0.0, (course.storage_used_mb or 0.0) - (doc.file_size_mb or 0.0)), 2)

    doc.index_status = DocumentIndexStatus.REMOVED
    unlink_media_rel_url(doc.file_url)
    await ai_client.delete_document(course_id=doc.course_id, document_id=doc.id, request_id=request_id)

    if course:
        create_user_notification(
            db,
            user_id=course.teacher_id,
            title=f"课程资料合规下架通知：《{doc.title}》",
            content=f"您课程《{course.title}》中的讲义资料《{doc.title}》已被管理员合规下架。原因：{reason}",
            notif_type="compliance_takedown",
        )

    record_audit(
        db,
        actor=admin,
        action="TAKEDOWN_DOCUMENT",
        target_type="course_document",
        target_id=doc.id,
        detail=f"敏感/合规处置下架资料《{doc.title}》并同步移除向量切片：{reason}",
    )
    db.commit()
    return doc


async def takedown_course_video(
    db: Session,
    *,
    admin: User,
    video_id: str,
    reason: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频资源不存在", status_code=404)
    course = db.get(Course, video.course_id)
    if course and not video.is_hidden:
        course.storage_used_mb = round(max(0.0, (course.storage_used_mb or 0.0) - (video.file_size_mb or 0.0)), 2)

    video.is_hidden = True

    # 收集该视频在所有提纲版本中可能同步进 AI 向量库的 transcript_{video_id}_{order_index} 文档 ID
    chapter_indices: set[int] = set()
    versions = db.scalars(select(OutlineVersion).where(OutlineVersion.video_id == video.id)).all()
    for ver in versions:
        for idx, ch in enumerate(ver.chapters_json or [], start=1):
            chapter_indices.add(int(ch.get("order_index") or idx))
    if not chapter_indices:
        chapter_indices = set(range(1, 9))

    for ch_idx in sorted(chapter_indices):
        await ai_client.delete_document(
            course_id=video.course_id,
            document_id=f"transcript_{video.id}_{ch_idx}",
            request_id=request_id,
        )

    if course:
        create_user_notification(
            db,
            user_id=course.teacher_id,
            title=f"授课视频合规下架通知：《{video.title}》",
            content=f"您课程《{course.title}》中的视频《{video.title}》已被管理员下架屏蔽并清理检索索引。原因：{reason}",
            notif_type="compliance_takedown",
        )

    record_audit(
        db,
        actor=admin,
        action="TAKEDOWN_VIDEO",
        target_type="video_resource",
        target_id=video.id,
        detail=f"单视频合规下架《{video.title}》并清理 AI 转写稿索引：{reason}",
    )
    db.commit()
    return {"id": video.id, "course_id": video.course_id, "is_hidden": True}


def cleanup_expired_courses(db: Session, *, admin: User) -> dict[str, Any]:
    candidates = db.scalars(
        select(Course).where(
            Course.expires_at.is_not(None),
            Course.expires_at != "",
            Course.status != CourseStatus.ARCHIVED,
        )
    ).all()
    archived_ids: list[str] = []
    now_str = utc_now_iso()

    for c in candidates:
        exp_dt = parse_flexible_datetime(c.expires_at)
        if exp_dt is None:
            continue
        cmp_now = datetime.now(exp_dt.tzinfo) if exp_dt.tzinfo else datetime.now()
        if cmp_now > exp_dt:
            c.status = CourseStatus.ARCHIVED
            c.updated_at = now_str
            archived_ids.append(c.id)
            create_user_notification(
                db,
                user_id=c.teacher_id,
                title=f"过期课程自动归档通知：《{c.title}》",
                content=f"课程《{c.title}》已超过有效期 ({c.expires_at})，系统已执行归档治理。",
                notif_type="course_governance",
            )

    record_audit(
        db,
        actor=admin,
        action="CLEANUP_EXPIRED_COURSES",
        target_type="course",
        target_id="batch",
        detail=f"归档已超有效期课程共 {len(archived_ids)} 门",
    )
    db.commit()
    return {"archived_count": len(archived_ids), "archived_course_ids": archived_ids}
