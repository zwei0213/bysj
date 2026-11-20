"""公共认证、注册、刷新令牌、密码安全、课程分类与广场目录、选课、站内通知与反馈工单领域服务。"""
from typing import Any
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.core.enums import (
    CourseStatus,
    DocumentIndexStatus,
    OutlineStatus,
    TeacherApprovalStatus,
    UserRole,
)
from app.core.errors import PlatformError, paginate_list, utc_now_iso
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_jwt_token,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.models.entities import (
    ClassGroup,
    Course,
    CourseCategory,
    CourseDocument,
    CourseFaq,
    Enrollment,
    ExamPaper,
    Organization,
    QuizSession,
    SupportTicket,
    User,
    UserNotification,
    VideoResource,
)
from app.services.audit_helper import record_audit


def create_user_notification(
    db: Session,
    *,
    user_id: str,
    title: str,
    content: str,
    notif_type: str = "system",
) -> UserNotification:
    notif = UserNotification(
        user_id=user_id,
        title=title,
        content=content,
        notif_type=notif_type,
        is_read=False,
    )
    db.add(notif)
    return notif


def _build_token_bundle(user: User) -> dict[str, Any]:
    return {
        "access_token": create_access_token(user_id=user.id, username=user.username, role=user.role),
        "refresh_token": create_refresh_token(user_id=user.id, username=user.username, role=user.role),
        "token_type": "Bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "email": user.email,
            "role": user.role,
            "org_id": user.org_id,
            "teacher_approved": user.teacher_approved,
            "teacher_status": user.teacher_status,
            "reject_reason": user.reject_reason,
            "streak_days": user.streak_days,
            "last_study_date": user.last_study_date,
        },
    }


def authenticate_and_issue_token(db: Session, *, username: str, password: str) -> dict[str, Any]:
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not verify_password(password, user.password_hash):
        raise PlatformError("INVALID_CREDENTIALS", "用户名或密码不正确", status_code=401)
    if not user.is_active:
        raise PlatformError("ACCOUNT_DISABLED", "该账号已被停用，请联系平台管理员", status_code=403)
    return _build_token_bundle(user)


def register_user_account(
    db: Session,
    *,
    username: str,
    display_name: str,
    email: str,
    password: str,
    role: str,
) -> dict[str, Any]:
    validate_password_strength(password)
    clean_username = username.strip()
    if db.scalar(select(User).where(User.username == clean_username)):
        raise PlatformError("USERNAME_CONFLICT", "该用户名已被注册", status_code=409)

    default_org = db.scalars(select(Organization).order_by(Organization.created_at.asc())).first()
    is_teacher = role == UserRole.TEACHER
    user = User(
        username=clean_username,
        display_name=display_name.strip(),
        email=email.strip(),
        password_hash=hash_password(password),
        role=UserRole.TEACHER if is_teacher else UserRole.STUDENT,
        org_id=default_org.id if default_org else None,
        is_active=True,
        # 商业合规：自主申请教师身份默认进入待管理员审核状态，学生注册即开通
        teacher_approved=not is_teacher,
        teacher_status=TeacherApprovalStatus.PENDING if is_teacher else TeacherApprovalStatus.APPROVED,
        reject_reason="",
        streak_days=0,
        last_study_date="",
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        actor=user,
        action="SELF_REGISTER",
        target_type="user",
        target_id=user.id,
        detail=f"用户自主注册 ({user.role}, teacher_status={user.teacher_status})",
    )
    db.commit()
    return _build_token_bundle(user)


def refresh_auth_tokens(db: Session, *, refresh_token: str) -> dict[str, Any]:
    claims = decode_jwt_token(refresh_token, expected_use="refresh")
    user = db.get(User, claims["sub"])
    if not user or not user.is_active:
        raise PlatformError("ACCOUNT_DISABLED", "账号不存在或已被停用", status_code=403)
    return _build_token_bundle(user)


def change_user_password(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise PlatformError("INVALID_CURRENT_PASSWORD", "当前原密码输入不正确", status_code=400)
    validate_password_strength(new_password)
    user.password_hash = hash_password(new_password)
    record_audit(
        db,
        actor=user,
        action="CHANGE_PASSWORD",
        target_type="user",
        target_id=user.id,
        detail="用户自主更新登录密码",
    )
    db.commit()


def list_visible_categories(db: Session) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(CourseCategory)
        .where(CourseCategory.is_visible.is_(True))
        .order_by(CourseCategory.sort_order.asc(), CourseCategory.created_at.asc())
    ).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "sort_order": r.sort_order,
            "is_visible": r.is_visible,
        }
        for r in rows
    ]


def query_published_courses(
    db: Session,
    *,
    user: User,
    category: str | None = None,
    keyword: str | None = None,
    featured_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stmt = select(Course).where(Course.status == CourseStatus.PUBLISHED).order_by(Course.is_featured.desc(), Course.created_at.desc())
    if featured_only:
        stmt = stmt.where(Course.is_featured.is_(True))
    if category and category.strip():
        stmt = stmt.where(Course.category == category.strip())
    if keyword and keyword.strip():
        kw_like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                Course.title.ilike(kw_like),
                Course.subtitle.ilike(kw_like),
                Course.code.ilike(kw_like),
            )
        )

    courses = list(db.scalars(stmt).all())
    if not courses:
        return [], {"page": page, "page_size": page_size, "total": 0, "total_pages": 1}

    course_ids = [c.id for c in courses]
    teacher_ids = list({c.teacher_id for c in courses})

    enrolled_ids = set(
        db.scalars(
            select(Enrollment.course_id).where(
                Enrollment.student_id == user.id,
                Enrollment.course_id.in_(course_ids),
            )
        ).all()
    )
    teacher_map = {
        u.id: u.display_name
        for u in db.scalars(select(User).where(User.id.in_(teacher_ids))).all()
    }

    serialized = [
        {
            "id": c.id,
            "code": c.code,
            "title": c.title,
            "subtitle": c.subtitle,
            "category": c.category,
            "cover_color": c.cover_color,
            "cover_url": c.cover_url,
            "description": c.description,
            "start_date": c.start_date,
            "end_date": c.end_date,
            "teacher_name": teacher_map.get(c.teacher_id, "授课教师"),
            "is_featured": c.is_featured,
            "is_enrolled": c.id in enrolled_ids,
        }
        for c in courses
    ]
    return paginate_list(serialized, page=page, page_size=page_size)


def build_course_detail(db: Session, *, course_id: str, user: User) -> dict[str, Any]:
    course = db.get(Course, course_id)
    if not course:
        raise PlatformError("COURSE_NOT_FOUND", "课程不存在", status_code=404)
    if user.role == UserRole.STUDENT and course.status != CourseStatus.PUBLISHED:
        raise PlatformError("COURSE_NOT_PUBLISHED", "该课程尚未审核上架", status_code=403)

    vid_stmt = (
        select(VideoResource)
        .where(VideoResource.course_id == course_id, VideoResource.is_hidden.is_(False))
        .order_by(VideoResource.order_index)
    )
    if user.role == UserRole.STUDENT:
        vid_stmt = vid_stmt.where(VideoResource.outline_status == OutlineStatus.PUBLISHED)
    visible_videos = db.scalars(vid_stmt).all()

    docs = db.scalars(
        select(CourseDocument).where(
            CourseDocument.course_id == course_id,
            CourseDocument.index_status != DocumentIndexStatus.REMOVED,
        )
    ).all()
    faqs = db.scalars(
        select(CourseFaq).where(CourseFaq.course_id == course_id).order_by(CourseFaq.is_pinned.desc())
    ).all()
    exams = list(
        db.scalars(
            select(ExamPaper).where(ExamPaper.course_id == course_id, ExamPaper.is_published.is_(True))
        ).all()
    )
    exam_ids = [ex.id for ex in exams]
    used_attempts_map: dict[str, int] = {}
    if exam_ids:
        used_attempts_map = dict(
            db.execute(
                select(QuizSession.exam_id, func.count())
                .where(
                    QuizSession.user_id == user.id,
                    QuizSession.exam_id.in_(exam_ids),
                )
                .group_by(QuizSession.exam_id)
            ).all()
        )

    enr = db.scalar(
        select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == user.id)
    )
    teacher_user = db.get(User, course.teacher_id)

    return {
        "id": course.id,
        "code": course.code,
        "title": course.title,
        "subtitle": course.subtitle,
        "category": course.category,
        "cover_color": course.cover_color,
        "cover_url": course.cover_url,
        "description": course.description,
        "start_date": course.start_date,
        "end_date": course.end_date,
        "expires_at": course.expires_at,
        "status": course.status,
        "teacher_name": teacher_user.display_name if teacher_user else "授课教师",
        "is_enrolled": enr is not None,
        "class_id": enr.class_id if enr else None,
        "group_name": enr.group_name if enr else "",
        "progress_pct": enr.progress_pct if enr else 0.0,
        "videos": [
            {
                "id": v.id,
                "title": v.title,
                "duration_sec": v.duration_sec,
                "order_index": v.order_index,
                "outline_status": v.outline_status,
            }
            for v in visible_videos
        ],
        "documents": [
            {
                "id": d.id,
                "title": d.title,
                "file_format": d.file_format,
                "file_url": d.file_url,
                "excerpt": (d.content_text or "")[:200],
                "content_text": d.content_text,
                "index_status": d.index_status,
            }
            for d in docs
        ],
        "faqs": [
            {"id": f.id, "question": f.question, "answer": f.answer, "is_pinned": f.is_pinned}
            for f in faqs
        ],
        "exams": [
            {
                "id": ex.id,
                "title": ex.title,
                "question_count": len(ex.question_ids_json),
                "time_limit_min": ex.time_limit_min,
                "max_attempts": ex.max_attempts,
                "used_attempts": int(used_attempts_map.get(ex.id, 0)),
                "start_time": ex.start_time,
                "end_time": ex.end_time,
                "target_class_id": ex.target_class_id,
            }
            for ex in exams
        ],
    }


def enroll_student_in_course(db: Session, *, course_id: str, user: User) -> tuple[dict[str, Any], int]:
    course = db.get(Course, course_id)
    if not course or course.status != CourseStatus.PUBLISHED:
        raise PlatformError("COURSE_NOT_AVAILABLE", "课程不存在或暂未开放选课", status_code=404)

    existing = db.scalar(
        select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == user.id)
    )
    if existing:
        return {"enrollment_id": existing.id, "already_enrolled": True}, 200

    default_cls = db.scalar(select(ClassGroup).where(ClassGroup.course_id == course_id))
    enr = Enrollment(
        course_id=course_id,
        class_id=default_cls.id if default_cls else None,
        student_id=user.id,
        progress_pct=0.0,
    )
    db.add(enr)
    db.commit()
    return {"enrollment_id": enr.id, "course_id": course_id}, 201


def submit_user_support_ticket(
    db: Session,
    *,
    user: User,
    category: str,
    subject: str,
    description: str,
) -> dict[str, str]:
    now = utc_now_iso()
    tkt = SupportTicket(
        submitter_id=user.id,
        category=category,
        subject=subject,
        description=description,
        updated_at=now,
    )
    db.add(tkt)
    db.commit()
    return {"id": tkt.id, "status": tkt.status}


def query_my_support_tickets(db: Session, *, user: User) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(SupportTicket)
        .where(SupportTicket.submitter_id == user.id)
        .order_by(SupportTicket.created_at.desc())
    ).all()
    return [
        {
            "id": t.id,
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


def query_user_notifications(db: Session, *, user: User) -> dict[str, Any]:
    rows = list(
        db.scalars(
            select(UserNotification)
            .where(UserNotification.user_id == user.id)
            .order_by(UserNotification.created_at.desc())
            .limit(100)
        ).all()
    )
    unread_count = sum(1 for n in rows if not n.is_read)
    return {
        "items": [
            {
                "id": n.id,
                "title": n.title,
                "content": n.content,
                "notif_type": n.notif_type,
                "is_read": n.is_read,
                "created_at": n.created_at,
            }
            for n in rows
        ],
        "unread_count": unread_count,
    }


def mark_user_notification_read(
    db: Session,
    *,
    user: User,
    notif_id: str,
    is_read: bool = True,
) -> dict[str, Any]:
    if notif_id in ("all", "read-all"):
        rows = db.scalars(
            select(UserNotification).where(UserNotification.user_id == user.id, UserNotification.is_read.is_(False))
        ).all()
        for r in rows:
            r.is_read = True
        db.commit()
        return {"id": "all", "is_read": True, "updated_count": len(rows)}

    notif = db.get(UserNotification, notif_id)
    if not notif or notif.user_id != user.id:
        raise PlatformError("NOTIFICATION_NOT_FOUND", "通知不存在", status_code=404)
    notif.is_read = is_read
    db.commit()
    return {"id": notif.id, "is_read": notif.is_read}
