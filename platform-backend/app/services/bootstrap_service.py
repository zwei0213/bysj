"""
商业生产环境空库首次启动初始化服务 (Bootstrap Service)。
- 仅在系统首次部署（`users` 表为空）时创建根组织节点与初始超级管理员账号，不写入任何虚拟演示业务课程；
- 若 `course_categories` 或 `role_permissions` 元数据表为空，幂等初始化基础学科分类目录与三角色默认权限矩阵。
"""
import logging
import secrets
from typing import Any
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.enums import TeacherApprovalStatus, UserRole
from app.core.security import hash_password
from app.models.entities import CourseCategory, Organization, RolePermissionConfig, User

logger = logging.getLogger(__name__)

DEFAULT_COURSE_CATEGORIES: list[tuple[str, str, int]] = [
    ("人工智能与系统", "ai-systems", 1),
    ("计算机基础", "cs-fundamentals", 2),
    ("数学与统计", "math-stats", 3),
    ("交叉学科", "interdisciplinary", 4),
]

DEFAULT_ROLE_PERMISSIONS: dict[str, dict[str, Any]] = {
    UserRole.STUDENT: {
        "course:browse": True,
        "course:enroll": True,
        "video:watch": True,
        "note:manage": True,
        "tutor:ask": True,
        "quiz:participate": True,
        "wrong_book:manage": True,
        "ticket:submit": True,
        "course:manage": False,
        "outline:calibrate": False,
        "question:review": False,
        "admin:governance": False,
    },
    UserRole.TEACHER: {
        "course:browse": True,
        "course:enroll": True,
        "video:watch": True,
        "note:manage": True,
        "tutor:ask": True,
        "quiz:participate": True,
        "ticket:submit": True,
        "course:manage": True,
        "class:manage": True,
        "outline:calibrate": True,
        "question:review": True,
        "exam:publish": True,
        "analytics:view": True,
        "admin:governance": False,
    },
    UserRole.ADMIN: {
        "course:browse": True,
        "course:enroll": True,
        "video:watch": True,
        "tutor:ask": True,
        "course:manage": True,
        "class:manage": True,
        "outline:calibrate": True,
        "question:review": True,
        "exam:publish": True,
        "analytics:view": True,
        "admin:governance": True,
        "admin:users": True,
        "admin:ai_ops": True,
        "admin:quality": True,
        "admin:reports": True,
        "admin:audit": True,
    },
}


def ensure_production_bootstrap(db: Session) -> None:
    dirty = False

    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    if user_count == 0:
        init_pwd = settings.bootstrap_admin_password.strip()
        generated_once = False
        if not init_pwd:
            if settings.app_env == "production":
                raise RuntimeError(
                    "[SECURITY_FATAL] 首次空库初始化生产环境必须通过环境变量 BOOTSTRAP_ADMIN_PASSWORD 指定初始管理员密码！"
                )
            init_pwd = f"Admin@{secrets.token_urlsafe(8)}"
            generated_once = True
        elif settings.app_env == "production" and len(init_pwd) < 8:
            raise RuntimeError("[SECURITY_FATAL] BOOTSTRAP_ADMIN_PASSWORD 密码长度不能少于 8 位！")

        root_org = Organization(name="平台总部管理中心", org_type="department")
        db.add(root_org)
        db.flush()

        admin = User(
            username=settings.bootstrap_admin_username.strip() or "admin",
            display_name=settings.bootstrap_admin_display_name,
            email=settings.bootstrap_admin_email,
            password_hash=hash_password(init_pwd),
            role=UserRole.ADMIN,
            org_id=root_org.id,
            is_active=True,
            teacher_approved=True,
            teacher_status=TeacherApprovalStatus.APPROVED,
            streak_days=0,
        )
        db.add(admin)
        dirty = True

        if generated_once:
            logger.warning(
                "Initialized bootstrap admin '%s' with generated password: %s (change immediately after login)",
                admin.username,
                init_pwd,
            )
        else:
            logger.info("Initialized bootstrap admin '%s'.", admin.username)

    cat_count = db.scalar(select(func.count()).select_from(CourseCategory)) or 0
    if cat_count == 0:
        for name, slug, sort_order in DEFAULT_COURSE_CATEGORIES:
            db.add(CourseCategory(name=name, slug=slug, sort_order=sort_order, is_visible=True))
        dirty = True

    perm_count = db.scalar(select(func.count()).select_from(RolePermissionConfig)) or 0
    if perm_count == 0:
        for role_key, perm_dict in DEFAULT_ROLE_PERMISSIONS.items():
            db.add(RolePermissionConfig(role=str(role_key), permissions_json=dict(perm_dict)))
        dirty = True

    if dirty:
        db.commit()
