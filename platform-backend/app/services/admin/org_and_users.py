"""管理端域 1：组织架构、用户与教师资质审批、批量导入及权限矩阵。"""

from collections import defaultdict
import csv
import io
from typing import Any
from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session
from app.core.enums import TeacherApprovalStatus, UserRole
from app.core.errors import PlatformError, utc_now_iso
from app.core.security import hash_password, validate_password_strength
from app.models.entities import ChunkUploadSession, ClassGroup, Course, Enrollment, LearningEvent, Organization, QuizSession, RolePermissionConfig, SupportTicket, TutorMessage, TutorThread, UnansweredQuestion, User, UserNotification, VideoNote, WrongQuestionRecord
from app.services.audit_helper import record_audit
from app.services.bootstrap_service import DEFAULT_ROLE_PERMISSIONS
from app.services.catalog_service import create_user_notification
from app.services.xlsx_service import parse_tabular_upload

def query_organizations(db: Session) -> list[dict[str, Any]]:
    orgs = db.scalars(select(Organization).order_by(Organization.created_at.asc())).all()
    return [
        {"id": o.id, "name": o.name, "org_type": o.org_type, "parent_id": o.parent_id, "created_at": o.created_at}
        for o in orgs
    ]


def create_org_node(
    db: Session,
    *,
    admin: User,
    name: str,
    org_type: str,
    parent_id: str | None,
) -> dict[str, Any]:
    org = Organization(name=name.strip(), org_type=org_type, parent_id=parent_id or None)
    db.add(org)
    db.flush()
    record_audit(db, actor=admin, action="CREATE_ORG", target_type="organization", target_id=org.id, detail=org.name)
    db.commit()
    return {"id": org.id, "name": org.name, "org_type": org.org_type, "parent_id": org.parent_id}


def update_org_node(
    db: Session,
    *,
    admin: User,
    org_id: str,
    name: str | None = None,
    org_type: str | None = None,
    parent_id: str | None = None,
) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    if not org:
        raise PlatformError("ORG_NOT_FOUND", "组织节点不存在", status_code=404)
    if name is not None and name.strip():
        org.name = name.strip()
    if org_type is not None:
        org.org_type = org_type
    if parent_id is not None:
        if parent_id == org.id:
            raise PlatformError("INVALID_PARENT_ORG", "不能将组织自身设为上级节点", status_code=400)
        org.parent_id = parent_id or None
    record_audit(db, actor=admin, action="UPDATE_ORG", target_type="organization", target_id=org.id, detail=org.name)
    db.commit()
    return {"id": org.id, "name": org.name, "org_type": org.org_type, "parent_id": org.parent_id}


def delete_org_node(db: Session, *, admin: User, org_id: str) -> dict[str, Any]:
    org = db.get(Organization, org_id)
    if not org:
        raise PlatformError("ORG_NOT_FOUND", "组织节点不存在", status_code=404)

    db.execute(update(User).where(User.org_id == org.id).values(org_id=org.parent_id))
    db.execute(update(Organization).where(Organization.parent_id == org.id).values(parent_id=org.parent_id))
    name_bak = org.name
    db.delete(org)
    record_audit(db, actor=admin, action="DELETE_ORG", target_type="organization", target_id=org_id, detail=name_bak)
    db.commit()
    return {"deleted": True, "id": org_id}


def query_users(
    db: Session,
    *,
    role: str | None = None,
    keyword: str | None = None,
) -> list[dict[str, Any]]:
    stmt = select(User).order_by(User.created_at.desc())
    if role:
        stmt = stmt.where(User.role == role)
    if keyword and keyword.strip():
        kw = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                User.username.ilike(kw),
                User.display_name.ilike(kw),
                User.email.ilike(kw),
            )
        )
    users = db.scalars(stmt).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "email": u.email,
            "role": u.role,
            "org_id": u.org_id,
            "is_active": u.is_active,
            "teacher_approved": u.teacher_approved,
            "teacher_status": u.teacher_status,
            "reject_reason": u.reject_reason,
            "streak_days": u.streak_days,
            "last_study_date": u.last_study_date,
            "created_at": u.created_at,
        }
        for u in users
    ]


def create_managed_user(
    db: Session,
    *,
    admin: User,
    username: str,
    display_name: str,
    email: str,
    password: str,
    role: str,
    org_id: str | None,
) -> dict[str, Any]:
    validate_password_strength(password)
    if db.scalar(select(User).where(User.username == username.strip())):
        raise PlatformError("USERNAME_CONFLICT", "该用户名已存在", status_code=409)
    user = User(
        username=username.strip(),
        display_name=display_name.strip(),
        email=email.strip(),
        password_hash=hash_password(password),
        role=role,
        org_id=org_id,
        is_active=True,
        teacher_approved=True,
        teacher_status=TeacherApprovalStatus.APPROVED,
    )
    db.add(user)
    db.flush()
    record_audit(db, actor=admin, action="CREATE_USER", target_type="user", target_id=user.id, detail=f"{user.username} ({user.role})")
    db.commit()
    return {"id": user.id, "username": user.username, "role": user.role}


def batch_import_users_from_csv(
    db: Session,
    *,
    admin: User,
    csv_text: str,
    org_id: str | None = None,
) -> dict[str, Any]:
    """批量导入师生账号（支持带表头动态列映射或默认顺序 username,display_name,role,email,password）。"""
    rows = [r for r in csv.reader(io.StringIO(csv_text.strip())) if r and any(c.strip() for c in r)]
    created_usernames: list[str] = []
    skipped_usernames: list[str] = []

    col_idx = {"username": 0, "display_name": 1, "role": 2, "email": 3, "password": 4}
    header_aliases = {
        "username": "username", "账号": "username", "用户名": "username", "学号": "username", "工号": "username",
        "display_name": "display_name", "姓名": "display_name", "显示名": "display_name",
        "role": "role", "角色": "role", "身份": "role",
        "email": "email", "邮箱": "email",
        "password": "password", "密码": "password", "初始密码": "password",
    }
    role_aliases = {
        "student": UserRole.STUDENT, "学生": UserRole.STUDENT, "学员": UserRole.STUDENT,
        "teacher": UserRole.TEACHER, "教师": UserRole.TEACHER, "老师": UserRole.TEACHER,
        "admin": UserRole.ADMIN, "管理员": UserRole.ADMIN,
    }
    start_row = 0
    if rows and rows[0][0].strip().lower() in header_aliases:
        header = [c.strip().lower() for c in rows[0]]
        for idx_col, col_name in enumerate(header):
            mapped = header_aliases.get(col_name)
            if mapped:
                col_idx[mapped] = idx_col
        start_row = 1

    def _cell(row: list[str], key: str) -> str:
        idx = col_idx[key]
        return row[idx].strip() if idx < len(row) else ""

    for row in rows[start_row:]:
        uname = _cell(row, "username")
        if not uname:
            continue
        dname = _cell(row, "display_name") or uname
        raw_role = _cell(row, "role").lower()
        role = role_aliases.get(raw_role, UserRole.STUDENT)
        email = _cell(row, "email")
        pwd = _cell(row, "password")
        if len(pwd) < 8:
            raise PlatformError("INVALID_IMPORT_PASSWORD", f"账号 '{uname}' 初始密码长度不得少于 8 位", status_code=400)

        if db.scalar(select(User).where(User.username == uname)):
            skipped_usernames.append(uname)
            continue

        db.add(
            User(
                username=uname,
                display_name=dname,
                email=email,
                password_hash=hash_password(pwd),
                role=role,
                org_id=org_id,
                is_active=True,
                teacher_approved=True,
                teacher_status=TeacherApprovalStatus.APPROVED,
            )
        )
        created_usernames.append(uname)

    db.flush()
    record_audit(
        db,
        actor=admin,
        action="BATCH_IMPORT_USERS",
        target_type="user",
        target_id="batch",
        detail=f"批量导入成功 {len(created_usernames)} 人，跳过已存在 {len(skipped_usernames)} 人",
    )
    db.commit()
    return {
        "created_count": len(created_usernames),
        "skipped_count": len(skipped_usernames),
        "created_usernames": created_usernames,
    }


def _pick_col(row: dict[str, str], keys: tuple[str, ...]) -> str:
    lowered = {k.strip().lower(): v.strip() for k, v in row.items() if k}
    for key in keys:
        if key.lower() in lowered and lowered[key.lower()]:
            return lowered[key.lower()]
    return ""


def batch_import_users_from_tabular(
    db: Session,
    *,
    admin: User,
    filename: str,
    raw_bytes: bytes,
    org_id: str | None = None,
) -> dict[str, Any]:
    parsed = parse_tabular_upload(filename, raw_bytes)
    created_usernames: list[str] = []
    skipped_usernames: list[str] = []

    role_aliases = {
        "student": UserRole.STUDENT,
        "学生": UserRole.STUDENT,
        "学员": UserRole.STUDENT,
        "teacher": UserRole.TEACHER,
        "教师": UserRole.TEACHER,
        "老师": UserRole.TEACHER,
        "admin": UserRole.ADMIN,
        "管理员": UserRole.ADMIN,
    }

    for row in parsed:
        uname = _pick_col(row, ("username", "用户名", "账号", "学号", "工号"))
        if not uname:
            continue
        dname = _pick_col(row, ("display_name", "姓名", "显示名", "name")) or uname
        raw_role = _pick_col(row, ("role", "角色", "身份")).lower()
        role = role_aliases.get(raw_role, UserRole.STUDENT)
        email = _pick_col(row, ("email", "邮箱"))
        pwd = _pick_col(row, ("password", "密码", "初始密码")) or "Pass#2026Safe"
        if len(pwd) < 8:
            raise PlatformError("INVALID_IMPORT_PASSWORD", f"账号 '{uname}' 初始密码长度不得少于 8 位", status_code=400)

        if db.scalar(select(User).where(User.username == uname)):
            skipped_usernames.append(uname)
            continue

        db.add(
            User(
                username=uname,
                display_name=dname,
                email=email,
                password_hash=hash_password(pwd),
                role=role,
                org_id=org_id,
                is_active=True,
                teacher_approved=True,
                teacher_status=TeacherApprovalStatus.APPROVED,
            )
        )
        created_usernames.append(uname)

    db.flush()
    record_audit(
        db,
        actor=admin,
        action="XLSX_IMPORT_USERS",
        target_type="user",
        target_id="batch",
        detail=f"表格导入 ({filename}) 成功 {len(created_usernames)} 人，跳过已存在 {len(skipped_usernames)} 人",
    )
    db.commit()
    return {
        "created_count": len(created_usernames),
        "skipped_count": len(skipped_usernames),
        "created_usernames": created_usernames,
    }


def update_managed_user(
    db: Session,
    *,
    admin: User,
    user_id: str,
    display_name: str | None = None,
    email: str | None = None,
    org_id: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
    teacher_approved: bool | None = None,
    teacher_status: str | None = None,
    reject_reason: str | None = None,
    reset_password: str | None = None,
) -> dict[str, Any]:
    target = db.get(User, user_id)
    if not target:
        raise PlatformError("USER_NOT_FOUND", "用户不存在", status_code=404)

    prev_status = target.teacher_status
    prev_approved = target.teacher_approved

    if display_name is not None and display_name.strip():
        target.display_name = display_name.strip()
    if email is not None:
        target.email = email.strip()
    if org_id is not None:
        target.org_id = org_id or None
    if role is not None:
        target.role = role
    if is_active is not None:
        target.is_active = is_active
    if reject_reason is not None:
        target.reject_reason = reject_reason.strip()

    if teacher_status is not None:
        target.teacher_status = teacher_status
        if teacher_status == TeacherApprovalStatus.APPROVED:
            target.teacher_approved = True
            target.reject_reason = ""
        else:
            target.teacher_approved = False
    elif teacher_approved is not None:
        target.teacher_approved = teacher_approved
        if teacher_approved:
            target.teacher_status = TeacherApprovalStatus.APPROVED
            target.reject_reason = ""
        else:
            target.teacher_status = (
                TeacherApprovalStatus.REJECTED if target.reject_reason else TeacherApprovalStatus.PENDING
            )

    if reset_password:
        validate_password_strength(reset_password)
        target.password_hash = hash_password(reset_password)

    # 教师审批通过或驳回时自动推送站内通知
    if target.role == UserRole.TEACHER:
        if target.teacher_approved and not prev_approved:
            create_user_notification(
                db,
                user_id=target.id,
                title="教师资质审核已通过",
                content="您的教师工作台权限已由管理员正式开通，现在可以新建课程并上传教学资源。",
                notif_type="account_review",
            )
        elif target.teacher_status == TeacherApprovalStatus.REJECTED and prev_status != TeacherApprovalStatus.REJECTED:
            reason_note = f"原因：{target.reject_reason}" if target.reject_reason else "请联系管理员补充资质材料。"
            create_user_notification(
                db,
                user_id=target.id,
                title="教师资质申请未获通过",
                content=f"很抱歉，您的教师资格审核未通过。{reason_note}",
                notif_type="account_review",
            )

    record_audit(
        db,
        actor=admin,
        action="UPDATE_USER_ACCESS",
        target_type="user",
        target_id=target.id,
        detail=f"role={target.role}, active={target.is_active}, teacher_status={target.teacher_status}",
    )
    db.commit()
    return {
        "id": target.id,
        "username": target.username,
        "display_name": target.display_name,
        "email": target.email,
        "org_id": target.org_id,
        "role": target.role,
        "is_active": target.is_active,
        "teacher_approved": target.teacher_approved,
        "teacher_status": target.teacher_status,
        "reject_reason": target.reject_reason,
    }


def delete_managed_user(db: Session, *, admin: User, user_id: str) -> dict[str, Any]:
    if user_id == admin.id:
        raise PlatformError("CANNOT_DELETE_SELF", "不能删除当前登录的管理员自身账号", status_code=400)
    target = db.get(User, user_id)
    if not target:
        raise PlatformError("USER_NOT_FOUND", "用户不存在", status_code=404)

    # 若被删除教师名下存在课程，转交当前管理员托管避免外键约束冲突
    db.execute(update(Course).where(Course.teacher_id == target.id).values(teacher_id=admin.id))
    db.execute(delete(Enrollment).where(Enrollment.student_id == target.id))
    db.execute(delete(UserNotification).where(UserNotification.user_id == target.id))
    db.execute(delete(SupportTicket).where(SupportTicket.submitter_id == target.id))
    db.execute(delete(VideoNote).where(VideoNote.user_id == target.id))
    db.execute(delete(LearningEvent).where(LearningEvent.user_id == target.id))
    db.execute(delete(WrongQuestionRecord).where(WrongQuestionRecord.user_id == target.id))
    db.execute(delete(QuizSession).where(QuizSession.user_id == target.id))
    db.execute(delete(UnansweredQuestion).where(UnansweredQuestion.student_id == target.id))
    db.execute(delete(ChunkUploadSession).where(ChunkUploadSession.user_id == target.id))

    thread_ids = list(db.scalars(select(TutorThread.id).where(TutorThread.user_id == target.id)).all())
    if thread_ids:
        db.execute(delete(TutorMessage).where(TutorMessage.thread_id.in_(thread_ids)))
    db.execute(delete(TutorMessage).where(TutorMessage.user_id == target.id))
    db.execute(delete(TutorThread).where(TutorThread.user_id == target.id))

    uname = target.username
    db.delete(target)
    record_audit(db, actor=admin, action="DELETE_USER", target_type="user", target_id=user_id, detail=f"删除账号 {uname}")
    db.commit()
    return {"deleted": True, "id": user_id}


def get_permissions_matrix(db: Session) -> dict[str, Any]:
    rows = list(db.scalars(select(RolePermissionConfig)).all())
    by_role = {r.role: dict(r.permissions_json or {}) for r in rows}
    for role_key, defaults in DEFAULT_ROLE_PERMISSIONS.items():
        by_role.setdefault(str(role_key), dict(defaults))
    items = [
        {"role": role_name, "permissions": perms}
        for role_name, perms in by_role.items()
    ]
    return {"roles": by_role, "items": items}


def update_permissions_matrix(
    db: Session,
    *,
    admin: User,
    roles: dict[str, dict[str, Any]] | None = None,
    items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    merged_updates: dict[str, dict[str, Any]] = {}
    if roles:
        for rk, pv in roles.items():
            if isinstance(pv, dict):
                merged_updates[str(rk)] = pv
    if items:
        for entry in items:
            rk = str(entry.get("role", "")).strip()
            pv = entry.get("permissions")
            if rk and isinstance(pv, dict):
                merged_updates[rk] = pv

    now = utc_now_iso()
    for role_name, perm_dict in merged_updates.items():
        cfg = db.get(RolePermissionConfig, role_name)
        if not cfg:
            cfg = RolePermissionConfig(role=role_name, permissions_json=perm_dict, updated_at=now)
            db.add(cfg)
        else:
            cfg.permissions_json = perm_dict
            cfg.updated_at = now

    record_audit(
        db,
        actor=admin,
        action="UPDATE_PERMISSIONS_MATRIX",
        target_type="role_permissions",
        target_id="global",
        detail=f"更新角色权限矩阵 ({', '.join(merged_updates.keys())})",
    )
    db.commit()
    return get_permissions_matrix(db)


def batch_assign_students_to_class(
    db: Session,
    *,
    admin: User,
    course_id: str,
    class_id: str,
    student_ids: list[str] | None = None,
    source_org_id: str | None = None,
    group_name: str = "",
) -> dict[str, Any]:
    course = db.get(Course, course_id)
    if not course:
        raise PlatformError("COURSE_NOT_FOUND", "课程不存在", status_code=404)
    cls = db.get(ClassGroup, class_id)
    if not cls or cls.course_id != course_id:
        raise PlatformError("CLASS_NOT_FOUND", "教学班级不存在或不属于该课程", status_code=404)

    target_stu_ids = {s.strip() for s in (student_ids or []) if s and s.strip()}
    if source_org_id:
        all_orgs = list(db.scalars(select(Organization)).all())
        children_by_parent: dict[str, list[str]] = defaultdict(list)
        for o in all_orgs:
            if o.parent_id:
                children_by_parent[o.parent_id].append(o.id)
        org_tree_ids = {source_org_id}
        stack = [source_org_id]
        while stack:
            curr = stack.pop()
            for child_id in children_by_parent.get(curr, []):
                if child_id not in org_tree_ids:
                    org_tree_ids.add(child_id)
                    stack.append(child_id)
        org_students = db.scalars(
            select(User.id).where(User.role == UserRole.STUDENT, User.org_id.in_(org_tree_ids))
        ).all()
        target_stu_ids.update(org_students)

    valid_students = list(
        db.scalars(select(User).where(User.id.in_(target_stu_ids))).all()
    ) if target_stu_ids else []

    assigned_count = 0
    for stu in valid_students:
        enr = db.scalar(
            select(Enrollment).where(Enrollment.course_id == course_id, Enrollment.student_id == stu.id)
        )
        if enr:
            enr.class_id = class_id
            if group_name:
                enr.group_name = group_name.strip()
        else:
            db.add(
                Enrollment(
                    course_id=course_id,
                    class_id=class_id,
                    student_id=stu.id,
                    group_name=group_name.strip(),
                    progress_pct=0.0,
                )
            )
        assigned_count += 1

    record_audit(
        db,
        actor=admin,
        action="BATCH_ASSIGN_CLASS",
        target_type="class_group",
        target_id=class_id,
        detail=f"批量分班至《{course.title}》-{cls.name}: 共 {assigned_count} 名学生",
    )
    db.commit()
    return {
        "assigned_count": assigned_count,
        "course_id": course_id,
        "class_id": class_id,
    }
