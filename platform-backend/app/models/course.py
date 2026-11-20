"""课程分类、课程、班级、选课、授课视频、分片上传会话、多版本提纲与讲义资料实体模型。"""
from typing import Any
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.enums import CourseStatus, DocumentIndexStatus, OutlineStatus
from app.models.common import generate_uid, utc_now_iso


class CourseCategory(Base):
    __tablename__ = "course_categories"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("cat"))
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("crs"))
    code: Mapped[str] = mapped_column(String(60), default="")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    subtitle: Mapped[str] = mapped_column(String(260), default="")
    category: Mapped[str] = mapped_column(String(60), default="人工智能")
    cover_color: Mapped[str] = mapped_column(String(40), default="#2563eb")
    cover_url: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    teacher_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True, default=CourseStatus.PUBLISHED)
    reject_reason: Mapped[str] = mapped_column(String(300), default="")
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_quota_mb: Mapped[int] = mapped_column(Integer, default=2048)
    storage_used_mb: Mapped[float] = mapped_column(Float, default=0.0)
    start_date: Mapped[str] = mapped_column(String(30), default="")
    end_date: Mapped[str] = mapped_column(String(30), default="")
    expires_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class ClassGroup(Base):
    __tablename__ = "class_groups"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("cls"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    term: Mapped[str] = mapped_column(String(40), default="2026秋季学期")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("course_id", "student_id", name="uq_course_student_enrollment"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("enr"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    class_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("class_groups.id"), nullable=True)
    student_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    group_name: Mapped[str] = mapped_column(String(80), default="")
    progress_pct: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class VideoResource(Base):
    __tablename__ = "video_resources"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("vid"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    media_url: Mapped[str] = mapped_column(String(500), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, default=600)
    order_index: Mapped[int] = mapped_column(Integer, default=1)
    file_size_mb: Mapped[float] = mapped_column(Float, default=85.0)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    ai_job_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    outline_status: Mapped[str] = mapped_column(String(30), default=OutlineStatus.DRAFT_REVIEW)
    published_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transcript_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class ChunkUploadSession(Base):
    __tablename__ = "chunk_upload_sessions"

    upload_id: Mapped[str] = mapped_column(String(60), primary_key=True, default=lambda: generate_uid("upl"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(260), nullable=False)
    title: Mapped[str] = mapped_column(String(160), default="")
    total_size: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=1)
    received_chunks_json: Mapped[list[int]] = mapped_column(JSON, default=list)
    temp_dir: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class OutlineVersion(Base):
    """每次 AI 自动生成或教师编辑/合并/拆分均生成独立快照，支持一键版本回滚与审核发布。"""

    __tablename__ = "outline_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("olv"))
    video_id: Mapped[str] = mapped_column(String(40), ForeignKey("video_resources.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(30), default="ai_generated")
    chapters_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    change_note: Mapped[str] = mapped_column(String(240), default="")
    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(40), default="system_ai")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class CourseDocument(Base):
    __tablename__ = "course_documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("doc"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    file_format: Mapped[str] = mapped_column(String(30), default="markdown")
    file_url: Mapped[str] = mapped_column(String(500), default="")
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    index_status: Mapped[str] = mapped_column(String(30), default=DocumentIndexStatus.READY)
    file_size_mb: Mapped[float] = mapped_column(Float, default=2.5)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
