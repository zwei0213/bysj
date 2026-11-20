"""AI 助教配置、会话历史、未解答收件箱与精选 FAQ 实体模型。"""
from typing import Any
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.enums import InboxStatus
from app.models.common import generate_uid, utc_now_iso


class CourseTutorConfig(Base):
    __tablename__ = "course_tutor_configs"

    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), primary_key=True)
    strict_course_only: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_out_of_syllabus: Mapped[bool] = mapped_column(Boolean, default=False)
    tone: Mapped[str] = mapped_column(String(40), default="encouraging_academic")
    forbidden_topics_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class TutorThread(Base):
    __tablename__ = "tutor_threads"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("thd"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), default="课程提问对话")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class TutorMessage(Base):
    __tablename__ = "tutor_messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("msg"))
    thread_id: Mapped[str] = mapped_column(String(40), ForeignKey("tutor_threads.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    followups_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    video_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_teacher_review: Mapped[bool] = mapped_column(Boolean, default=False)
    is_bookmarked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    feedback_vote: Mapped[str | None] = mapped_column(String(20), nullable=True)  # up | down | reported
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class UnansweredQuestion(Base):
    """未解答问题收件箱：AI 低置信度自动入箱；教师解答后可一键回流课程知识库。"""

    __tablename__ = "unanswered_questions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("unq"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    student_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"))
    message_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    video_context_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=InboxStatus.PENDING)
    teacher_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_to_kb: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    resolved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CourseFaq(Base):
    __tablename__ = "course_faqs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("faq"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    question: Mapped[str] = mapped_column(String(300), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
