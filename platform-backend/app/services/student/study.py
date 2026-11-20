"""学生学习域：连续学习打卡、视频播放门禁、学习心跳、笔记书签与收藏夹。"""

from datetime import date, datetime, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.enums import OutlineStatus, UserRole
from app.core.errors import PlatformError
from app.models.entities import Enrollment, LearningEvent, OutlineVersion, TutorMessage, User, VideoNote, VideoResource

def parse_flexible_datetime(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def touch_user_study_streak(db: Session, user: User) -> None:
    """按日期更新用户真实连续学习天数 (streak_days & last_study_date)。"""
    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()
    prev_str = (user.last_study_date or "").strip()

    if prev_str == today_str:
        if (user.streak_days or 0) < 1:
            user.streak_days = 1
        return

    if prev_str:
        try:
            prev_date = date.fromisoformat(prev_str[:10])
            delta_days = (today - prev_date).days
            if delta_days == 1:
                user.streak_days = (user.streak_days or 0) + 1
            else:
                user.streak_days = 1
        except ValueError:
            user.streak_days = 1
    else:
        user.streak_days = 1

    user.last_study_date = today_str
    db.add(user)


def get_student_video_playback(db: Session, *, video_id: str, user: User) -> dict[str, Any]:
    video = db.get(VideoResource, video_id)
    if not video:
        raise PlatformError("VIDEO_NOT_FOUND", "视频不存在", status_code=404)
    if video.is_hidden and user.role == UserRole.STUDENT:
        raise PlatformError("VIDEO_TAKEN_DOWN", "该视频资源已下架，暂不可观看", status_code=403)

    # 核心设计原则：AI 生成内容未经教师审核发布，对学生不可见
    if user.role == UserRole.STUDENT and video.outline_status != OutlineStatus.PUBLISHED:
        raise PlatformError(
            "OUTLINE_NOT_PUBLISHED",
            "该视频章节提纲尚在教师校准审核中，发布后即可观看章节导航",
            status_code=403,
        )

    chapters: list[dict[str, Any]] = []
    if video.published_version_id:
        pub_ver = db.get(OutlineVersion, video.published_version_id)
        if pub_ver:
            chapters = pub_ver.chapters_json
    elif user.role in (UserRole.TEACHER, UserRole.ADMIN):
        latest = db.scalars(
            select(OutlineVersion)
            .where(OutlineVersion.video_id == video.id)
            .order_by(OutlineVersion.version_no.desc())
        ).first()
        if latest:
            chapters = latest.chapters_json

    return {
        "video_id": video.id,
        "course_id": video.course_id,
        "title": video.title,
        "media_url": video.media_url,
        "duration_sec": video.duration_sec,
        "outline_status": video.outline_status,
        "chapters": chapters,
        "transcript_segments": video.transcript_json or [],
    }


def record_watch_event(
    db: Session,
    *,
    user: User,
    course_id: str,
    video_id: str | None,
    event_type: str,
    position_sec: int,
    watched_seconds: int,
    completed: bool,
) -> str:
    ev = LearningEvent(
        user_id=user.id,
        course_id=course_id,
        video_id=video_id,
        event_type=event_type,
        position_sec=position_sec,
        watched_seconds=watched_seconds,
        completed=completed,
    )
    db.add(ev)
    enr = db.scalar(
        select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == user.id)
    )
    if enr and event_type != "skip_segment":
        enr.progress_pct = min(100.0, round(enr.progress_pct + (12.0 if completed else 2.5), 1))
    touch_user_study_streak(db, user)
    db.commit()
    return ev.id


def query_user_notes(
    db: Session,
    *,
    user: User,
    video_id: str | None = None,
    course_id: str | None = None,
    bookmarks_only: bool = False,
) -> list[dict[str, Any]]:
    stmt = select(VideoNote).where(VideoNote.user_id == user.id)
    if video_id:
        stmt = stmt.where(VideoNote.video_id == video_id)
    if course_id:
        stmt = stmt.where(VideoNote.course_id == course_id)
    if bookmarks_only:
        stmt = stmt.where(VideoNote.is_bookmark.is_(True))
    notes = db.scalars(stmt.order_by(VideoNote.timestamp_sec.asc())).all()
    return [
        {
            "id": n.id,
            "course_id": n.course_id,
            "video_id": n.video_id,
            "timestamp_sec": n.timestamp_sec,
            "content": n.content,
            "is_bookmark": n.is_bookmark,
            "created_at": n.created_at,
        }
        for n in notes
    ]


def create_user_note(
    db: Session,
    *,
    user: User,
    course_id: str,
    video_id: str,
    timestamp_sec: int,
    content: str,
    is_bookmark: bool,
) -> dict[str, Any]:
    note = VideoNote(
        user_id=user.id,
        course_id=course_id,
        video_id=video_id,
        timestamp_sec=timestamp_sec,
        content=content,
        is_bookmark=is_bookmark,
    )
    db.add(note)
    db.commit()
    return {
        "id": note.id,
        "course_id": note.course_id,
        "video_id": note.video_id,
        "timestamp_sec": note.timestamp_sec,
        "content": note.content,
        "is_bookmark": note.is_bookmark,
        "created_at": note.created_at,
    }


def update_user_note(
    db: Session,
    *,
    user: User,
    note_id: str,
    content: str | None = None,
    is_bookmark: bool | None = None,
    timestamp_sec: int | None = None,
) -> dict[str, Any]:
    note = db.get(VideoNote, note_id)
    if not note or note.user_id != user.id:
        raise PlatformError("NOTE_NOT_FOUND", "笔记不存在", status_code=404)
    if content is not None:
        note.content = content.strip()
    if is_bookmark is not None:
        note.is_bookmark = is_bookmark
    if timestamp_sec is not None:
        note.timestamp_sec = timestamp_sec
    db.commit()
    return {
        "id": note.id,
        "course_id": note.course_id,
        "video_id": note.video_id,
        "timestamp_sec": note.timestamp_sec,
        "content": note.content,
        "is_bookmark": note.is_bookmark,
        "created_at": note.created_at,
    }


def delete_user_note(db: Session, *, user: User, note_id: str) -> None:
    note = db.get(VideoNote, note_id)
    if not note or note.user_id != user.id:
        raise PlatformError("NOTE_NOT_FOUND", "笔记不存在", status_code=404)
    db.delete(note)
    db.commit()


def query_bookmarked_messages(
    db: Session,
    *,
    user: User,
    course_id: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(TutorMessage).where(
        TutorMessage.user_id == user.id,
        TutorMessage.is_bookmarked.is_(True),
        TutorMessage.is_blocked.is_(False),
    )
    if course_id:
        stmt = stmt.where(TutorMessage.course_id == course_id)
    rows = db.scalars(stmt.order_by(TutorMessage.created_at.desc())).all()
    return [
        {
            "id": m.id,
            "message_id": m.id,
            "course_id": m.course_id,
            "thread_id": m.thread_id,
            "content": m.content,
            "citations": m.citations_json,
            "created_at": m.created_at,
        }
        for m in rows
    ]
