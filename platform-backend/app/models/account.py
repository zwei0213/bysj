"""域 1：组织结构、用户 RBAC 账号、角色权限矩阵配置与站内消息通知实体模型。"""
from typing import Any
from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.enums import TeacherApprovalStatus, UserRole
from app.models.common import generate_uid, utc_now_iso


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("org"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    org_type: Mapped[str] = mapped_column(String(30), default="department")  # department | grade | class
    parent_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("usr"))
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(20), index=True, default=UserRole.STUDENT)
    org_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("organizations.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    teacher_approved: Mapped[bool] = mapped_column(Boolean, default=True)
    teacher_status: Mapped[str] = mapped_column(String(20), default=TeacherApprovalStatus.APPROVED)
    reject_reason: Mapped[str] = mapped_column(String(300), default="")
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_study_date: Mapped[str] = mapped_column(String(20), default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class RolePermissionConfig(Base):
    __tablename__ = "role_permissions"

    role: Mapped[str] = mapped_column(String(20), primary_key=True)
    permissions_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class UserNotification(Base):
    __tablename__ = "user_notifications"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("ntf"))
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    notif_type: Mapped[str] = mapped_column(String(30), default="system")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
