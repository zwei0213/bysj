"""教师课程管理、封面上传、班级与学生花名册服务。"""

from collections import defaultdict
import csv
import io
from typing import Any
from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.enums import CourseStatus, DocumentIndexStatus, InboxStatus, TeacherApprovalStatus, UserRole
from app.core.errors import PlatformError, utc_now_iso
from app.core.security import hash_password
from app.models.entities import ClassGroup, Course, CourseDocument, Enrollment, ExamPaper, UnansweredQuestion, User, VideoResource
from app.services.audit_helper import record_audit
from app.services.storage_service import save_cover_upload
from app.services.xlsx_service import parse_tabular_upload

def ensure_course_owner_or_admin(db: Session, course_id: str, user: User) -> Course:
    course = db.get(Course, course_id)
    if not course:
        raise PlatformError("COURSE_NOT_FOUND", f"未找到课程: {course_id}", status_code=404)
    if user.role != UserRole.ADMIN and course.teacher_id != user.id:
        raise PlatformError("FORBIDDEN_COURSE_ACCESS", "您不是该课程的负责教师", status_code=403)
    return course


def check_course_storage_quota(course: Course, additional_mb: float) -> None:
    quota = float(course.storage_quota_mb or 2048)
    used = float(course.storage_used_mb or 0.0)
    if used + max(0.0, additional_mb) > quota:
        raise PlatformError("STORAGE_QUOTA_EXCEEDED", "已超出该课程存储配额上限", status_code=400)


def query_teacher_courses(db: Session, *, teacher: User) -> list[dict[str, Any]]:
    stmt = select(Course).order_by(Course.created_at.desc())
    if teacher.role != UserRole.ADMIN:
        stmt = stmt.where(Course.teacher_id == teacher.id)
    courses = list(db.scalars(stmt).all())
    if not courses:
        return []

    course_ids = [c.id for c in courses]
    videos_by_course: dict[str, list[VideoResource]] = defaultdict(list)
    for v in db.scalars(
        select(VideoResource).where(VideoResource.course_id.in_(course_ids)).order_by(VideoResource.order_index)
    ).all():
        videos_by_course[v.course_id].append(v)

    classes_by_course: dict[str, list[ClassGroup]] = defaultdict(list)
    for cl in db.scalars(select(ClassGroup).where(ClassGroup.course_id.in_(course_ids))).all():
        classes_by_course[cl.course_id].append(cl)

    docs_by_course: dict[str, list[CourseDocument]] = defaultdict(list)
    for d in db.scalars(
        select(CourseDocument)
        .where(
            CourseDocument.course_id.in_(course_ids),
            CourseDocument.index_status != DocumentIndexStatus.REMOVED,
        )
        .order_by(CourseDocument.created_at.desc())
    ).all():
        docs_by_course[d.course_id].append(d)

    exams_by_course: dict[str, list[ExamPaper]] = defaultdict(list)
    for ex in db.scalars(
        select(ExamPaper).where(ExamPaper.course_id.in_(course_ids)).order_by(ExamPaper.created_at.desc())
    ).all():
        exams_by_course[ex.course_id].append(ex)

    pending_counts = dict(
        db.execute(
            select(UnansweredQuestion.course_id, func.count())
            .where(
                UnansweredQuestion.course_id.in_(course_ids),
                UnansweredQuestion.status == InboxStatus.PENDING,
            )
            .group_by(UnansweredQuestion.course_id)
        ).all()
    )

    return [
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
            "expires_at": c.expires_at,
            "status": c.status,
            "reject_reason": c.reject_reason,
            "storage_used_mb": c.storage_used_mb,
            "storage_quota_mb": c.storage_quota_mb,
            "pending_unanswered_count": int(pending_counts.get(c.id, 0)),
            "classes": [{"id": cl.id, "name": cl.name, "term": cl.term} for cl in classes_by_course[c.id]],
            "videos": [
                {
                    "id": v.id,
                    "title": v.title,
                    "media_url": v.media_url,
                    "duration_sec": v.duration_sec,
                    "file_size_mb": v.file_size_mb,
                    "is_hidden": bool(v.is_hidden),
                    "outline_status": v.outline_status,
                    "ai_job_id": v.ai_job_id,
                    "published_version_id": v.published_version_id,
                }
                for v in videos_by_course[c.id]
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
                for d in docs_by_course[c.id]
            ],
            "exams": [
                {
                    "id": ex.id,
                    "title": ex.title,
                    "question_ids": ex.question_ids_json,
                    "question_count": len(ex.question_ids_json),
                    "time_limit_min": ex.time_limit_min,
                    "max_attempts": ex.max_attempts,
                    "target_class_id": ex.target_class_id,
                    "start_time": ex.start_time,
                    "end_time": ex.end_time,
                    "is_published": ex.is_published,
                    "created_at": ex.created_at,
                }
                for ex in exams_by_course[c.id]
            ],
        }
        for c in courses
    ]


def create_new_course(
    db: Session,
    *,
    teacher: User,
    code: str = "",
    title: str,
    subtitle: str,
    category: str,
    cover_url: str = "",
    description: str,
    start_date: str = "",
    end_date: str = "",
    submit_for_review: bool,
) -> dict[str, Any]:
    course = Course(
        code=code.strip(),
        title=title.strip(),
        subtitle=subtitle.strip(),
        category=category.strip() or "人工智能",
        cover_url=cover_url.strip(),
        description=description.strip(),
        start_date=start_date.strip(),
        end_date=end_date.strip(),
        teacher_id=teacher.id,
        status=CourseStatus.PENDING_REVIEW if submit_for_review else CourseStatus.DRAFT,
        storage_used_mb=0.0,
    )
    db.add(course)
    db.flush()
    db.add(ClassGroup(course_id=course.id, name="默认教学1班"))
    record_audit(
        db,
        actor=teacher,
        action="CREATE_COURSE",
        target_type="course",
        target_id=course.id,
        detail=f"新建课程《{course.title}》({course.status})",
    )
    db.commit()
    return {
        "id": course.id,
        "code": course.code,
        "title": course.title,
        "status": course.status,
        "storage_used_mb": course.storage_used_mb,
    }


def update_teacher_course(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    code: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    category: str | None = None,
    cover_url: str | None = None,
    cover_color: str | None = None,
    description: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    if code is not None:
        course.code = code.strip()
    if title is not None and title.strip():
        course.title = title.strip()
    if subtitle is not None:
        course.subtitle = subtitle.strip()
    if category is not None and category.strip():
        course.category = category.strip()
    if cover_url is not None:
        course.cover_url = cover_url.strip()
    if cover_color is not None and cover_color.strip():
        course.cover_color = cover_color.strip()
    if description is not None:
        course.description = description.strip()
    if start_date is not None:
        course.start_date = start_date.strip()
    if end_date is not None:
        course.end_date = end_date.strip()
    if status is not None:
        course.status = status
        if status == CourseStatus.PENDING_REVIEW:
            course.reject_reason = ""
    course.updated_at = utc_now_iso()

    record_audit(
        db,
        actor=teacher,
        action="UPDATE_COURSE",
        target_type="course",
        target_id=course.id,
        detail=f"更新课程《{course.title}》信息 (status={course.status})",
    )
    db.commit()
    return {
        "id": course.id,
        "code": course.code,
        "title": course.title,
        "subtitle": course.subtitle,
        "category": course.category,
        "cover_url": course.cover_url,
        "description": course.description,
        "start_date": course.start_date,
        "end_date": course.end_date,
        "status": course.status,
        "reject_reason": course.reject_reason,
    }


async def upload_course_cover(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    cover_file: UploadFile,
) -> dict[str, Any]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    rel_url, _size_mb = await save_cover_upload(cover_file)
    course.cover_url = rel_url
    course.updated_at = utc_now_iso()
    record_audit(
        db,
        actor=teacher,
        action="UPLOAD_COURSE_COVER",
        target_type="course",
        target_id=course.id,
        detail=f"上传课程封面 {rel_url}",
    )
    db.commit()
    return {"course_id": course.id, "cover_url": rel_url}


def create_class_for_course(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    name: str,
    term: str,
) -> dict[str, str]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    cls = ClassGroup(course_id=course_id, name=name.strip(), term=term.strip())
    db.add(cls)
    db.flush()
    record_audit(
        db,
        actor=teacher,
        action="CREATE_CLASS_GROUP",
        target_type="class_group",
        target_id=cls.id,
        detail=f"创建教学班级 {cls.name} ({cls.term})",
    )
    db.commit()
    return {"id": cls.id, "name": cls.name, "term": cls.term}


def query_course_students(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    class_id: str | None = None,
) -> list[dict[str, Any]]:
    ensure_course_owner_or_admin(db, course_id, teacher)
    stmt = select(Enrollment).where(Enrollment.course_id == course_id)
    if class_id:
        stmt = stmt.where(Enrollment.class_id == class_id)
    enrs = list(db.scalars(stmt.order_by(Enrollment.created_at.desc())).all())
    if not enrs:
        return []

    stu_ids = list({e.student_id for e in enrs})
    cls_ids = list({e.class_id for e in enrs if e.class_id})
    stu_map = {u.id: u for u in db.scalars(select(User).where(User.id.in_(stu_ids))).all()} if stu_ids else {}
    cls_map = (
        {c.id: c.name for c in db.scalars(select(ClassGroup).where(ClassGroup.id.in_(cls_ids))).all()}
        if cls_ids
        else {}
    )

    items: list[dict[str, Any]] = []
    for e in enrs:
        stu = stu_map.get(e.student_id)
        if not stu:
            continue
        items.append({
            "enrollment_id": e.id,
            "student_id": stu.id,
            "username": stu.username,
            "display_name": stu.display_name,
            "email": stu.email,
            "class_id": e.class_id,
            "class_name": cls_map.get(e.class_id or "", "未分班"),
            "group_name": e.group_name,
            "progress_pct": e.progress_pct,
            "created_at": e.created_at,
        })
    return items


def _pick_dict_field(row: dict[str, str], aliases: tuple[str, ...]) -> str:
    lowered = {k.strip().lower(): v.strip() for k, v in row.items() if k}
    for alias in aliases:
        if alias.lower() in lowered and lowered[alias.lower()]:
            return lowered[alias.lower()]
    return ""


def _upsert_course_roster_records(
    db: Session,
    *,
    teacher: User,
    course: Course,
    records: list[dict[str, str]],
    default_class_id: str | None,
    default_group_name: str,
) -> tuple[int, int, int]:
    resolved_cls_id = default_class_id
    if not resolved_cls_id:
        first_cls = db.scalar(select(ClassGroup).where(ClassGroup.course_id == course.id))
        resolved_cls_id = first_cls.id if first_cls else None

    enrolled_count = 0
    created_accounts_count = 0
    updated_count = 0

    for rec in records:
        uname = rec.get("username", "").strip()
        if not uname:
            continue
        dname = rec.get("display_name", "").strip() or uname
        email = rec.get("email", "").strip()
        grp = rec.get("group_name", "").strip() or default_group_name.strip()
        pwd = rec.get("password", "").strip() or "Student#2026"
        if len(pwd) < 8:
            pwd = "Student#2026"

        stu = db.scalar(select(User).where(User.username == uname))
        if not stu:
            stu = User(
                username=uname,
                display_name=dname,
                email=email,
                password_hash=hash_password(pwd),
                role=UserRole.STUDENT,
                org_id=teacher.org_id,
                is_active=True,
                teacher_approved=True,
                teacher_status=TeacherApprovalStatus.APPROVED,
            )
            db.add(stu)
            db.flush()
            created_accounts_count += 1

        existing_enr = db.scalar(
            select(Enrollment).where(Enrollment.course_id == course.id, Enrollment.student_id == stu.id)
        )
        if existing_enr:
            if resolved_cls_id:
                existing_enr.class_id = resolved_cls_id
            if grp:
                existing_enr.group_name = grp
            updated_count += 1
        else:
            db.add(
                Enrollment(
                    course_id=course.id,
                    class_id=resolved_cls_id,
                    student_id=stu.id,
                    group_name=grp,
                    progress_pct=0.0,
                )
            )
            enrolled_count += 1

    return enrolled_count, created_accounts_count, updated_count


def import_course_students_text(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    class_id: str | None = None,
    group_name: str = "",
    usernames_or_csv: str | list[str] = "",
) -> dict[str, int]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    records: list[dict[str, str]] = []

    if isinstance(usernames_or_csv, list):
        for item in usernames_or_csv:
            u = str(item).strip()
            if u:
                records.append({"username": u})
    else:
        raw_text = (usernames_or_csv or "").strip()
        if raw_text:
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            if len(lines) == 1 and "," in lines[0] and "username" not in lines[0].lower():
                # 逗号分隔纯用户名列表（如 "stu01, stu02, stu03"）
                parts = [p.strip() for p in lines[0].replace("，", ",").split(",") if p.strip()]
                records = [{"username": p} for p in parts]
            else:
                csv_rows = [r for r in csv.reader(io.StringIO(raw_text)) if r and any(c.strip() for c in r)]
                start_idx = 0
                has_header = False
                header_cols: list[str] = []
                if csv_rows and csv_rows[0][0].strip().lower() in ("username", "学号", "账号", "用户名"):
                    has_header = True
                    header_cols = [h.strip() for h in csv_rows[0]]
                    start_idx = 1
                for r in csv_rows[start_idx:]:
                    if has_header:
                        row_dict = {header_cols[i]: (r[i].strip() if i < len(r) else "") for i in range(len(header_cols))}
                        records.append({
                            "username": _pick_dict_field(row_dict, ("username", "学号", "账号", "用户名")),
                            "display_name": _pick_dict_field(row_dict, ("display_name", "姓名", "学生姓名")),
                            "email": _pick_dict_field(row_dict, ("email", "邮箱")),
                            "group_name": _pick_dict_field(row_dict, ("group_name", "小组", "分组")),
                            "password": _pick_dict_field(row_dict, ("password", "密码")),
                        })
                    else:
                        records.append({
                            "username": r[0].strip() if len(r) > 0 else "",
                            "display_name": r[1].strip() if len(r) > 1 else "",
                            "email": r[2].strip() if len(r) > 2 else "",
                            "group_name": r[3].strip() if len(r) > 3 else "",
                        })

    enrolled, created_acc, updated = _upsert_course_roster_records(
        db,
        teacher=teacher,
        course=course,
        records=records,
        default_class_id=class_id,
        default_group_name=group_name,
    )
    record_audit(
        db,
        actor=teacher,
        action="IMPORT_COURSE_STUDENTS",
        target_type="course",
        target_id=course.id,
        detail=f"导入班级学生：新增选课 {enrolled} 人，自动建号 {created_acc} 人，更新 {updated} 人",
    )
    db.commit()
    return {
        "enrolled_count": enrolled,
        "created_accounts_count": created_acc,
        "updated_count": updated,
    }


def import_course_students_tabular(
    db: Session,
    *,
    teacher: User,
    course_id: str,
    filename: str,
    raw_bytes: bytes,
    class_id: str | None = None,
    group_name: str = "",
) -> dict[str, int]:
    course = ensure_course_owner_or_admin(db, course_id, teacher)
    parsed_rows = parse_tabular_upload(filename, raw_bytes)
    records = [
        {
            "username": _pick_dict_field(r, ("username", "学号", "账号", "用户名", "student_no")),
            "display_name": _pick_dict_field(r, ("display_name", "姓名", "学生姓名", "name")),
            "email": _pick_dict_field(r, ("email", "邮箱")),
            "group_name": _pick_dict_field(r, ("group_name", "学习小组", "小组", "分组", "group")),
            "password": _pick_dict_field(r, ("password", "密码", "初始密码")),
        }
        for r in parsed_rows
    ]
    enrolled, created_acc, updated = _upsert_course_roster_records(
        db,
        teacher=teacher,
        course=course,
        records=records,
        default_class_id=class_id,
        default_group_name=group_name,
    )
    record_audit(
        db,
        actor=teacher,
        action="XLSX_IMPORT_COURSE_STUDENTS",
        target_type="course",
        target_id=course.id,
        detail=f"表格导入班级名单 ({filename})：新增选课 {enrolled} 人，自动建号 {created_acc} 人，更新 {updated} 人",
    )
    db.commit()
    return {
        "enrolled_count": enrolled,
        "created_accounts_count": created_acc,
        "updated_count": updated,
    }


def update_student_enrollment(
    db: Session,
    *,
    teacher: User,
    enrollment_id: str,
    class_id: str | None = None,
    group_name: str | None = None,
) -> dict[str, Any]:
    enr = db.get(Enrollment, enrollment_id)
    if not enr:
        raise PlatformError("ENROLLMENT_NOT_FOUND", "选课记录不存在", status_code=404)
    ensure_course_owner_or_admin(db, enr.course_id, teacher)

    if class_id is not None:
        if class_id == "":
            enr.class_id = None
        else:
            cls = db.get(ClassGroup, class_id)
            if not cls or cls.course_id != enr.course_id:
                raise PlatformError("CLASS_NOT_FOUND", "指定的班级不属于该课程", status_code=404)
            enr.class_id = cls.id
    if group_name is not None:
        enr.group_name = group_name.strip()

    record_audit(
        db,
        actor=teacher,
        action="UPDATE_ENROLLMENT",
        target_type="enrollment",
        target_id=enr.id,
        detail=f"调整学生分班/小组: class_id={enr.class_id}, group={enr.group_name}",
    )
    db.commit()
    return {
        "enrollment_id": enr.id,
        "class_id": enr.class_id,
        "group_name": enr.group_name,
    }


def remove_student_enrollment(db: Session, *, teacher: User, enrollment_id: str) -> dict[str, Any]:
    enr = db.get(Enrollment, enrollment_id)
    if not enr:
        raise PlatformError("ENROLLMENT_NOT_FOUND", "选课记录不存在", status_code=404)
    ensure_course_owner_or_admin(db, enr.course_id, teacher)
    stu_id = enr.student_id
    db.delete(enr)
    record_audit(
        db,
        actor=teacher,
        action="REMOVE_ENROLLMENT",
        target_type="enrollment",
        target_id=enrollment_id,
        detail=f"将学生 {stu_id} 移出课程班级",
    )
    db.commit()
    return {"deleted": True, "enrollment_id": enrollment_id}
