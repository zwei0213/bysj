"""题库、组卷、测验会话、错题本、学习行为与时间戳笔记实体模型。"""
from typing import Any
from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.enums import QuestionReviewStatus, QuizSessionStatus, QuizSessionType
from app.models.common import generate_uid, utc_now_iso


class QuestionBankItem(Base):
    __tablename__ = "question_bank_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("qst"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    chapter_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    question_type: Mapped[str] = mapped_column(String(30), default="single_choice")
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    options_json: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    answer_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    difficulty: Mapped[str] = mapped_column(String(20), default="medium")
    knowledge_point: Mapped[str] = mapped_column(String(100), default="核心概念")
    review_status: Mapped[str] = mapped_column(String(25), index=True, default=QuestionReviewStatus.PENDING_REVIEW)
    source: Mapped[str] = mapped_column(String(20), default="ai")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class ExamPaper(Base):
    __tablename__ = "exam_papers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("exm"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    question_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    time_limit_min: Mapped[int] = mapped_column(Integer, default=30)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    target_class_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    start_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class QuizSession(Base):
    __tablename__ = "quiz_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("qzs"))
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    exam_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    session_type: Mapped[str] = mapped_column(String(30), default=QuizSessionType.CHAPTER_PRACTICE)
    title: Mapped[str] = mapped_column(String(160), default="章节自测")
    questions_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    answers_json: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    grading_detail_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_score: Mapped[float] = mapped_column(Float, default=100.0)
    status: Mapped[str] = mapped_column(String(20), default=QuizSessionStatus.IN_PROGRESS)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    submitted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WrongQuestionRecord(Base):
    __tablename__ = "wrong_question_records"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("wrq"))
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    knowledge_point: Mapped[str] = mapped_column(String(100), index=True)
    question_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    last_wrong_answer_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    wrong_count: Mapped[int] = mapped_column(Integer, default=1)
    is_mastered: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str] = mapped_column(String(300), default="")
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class LearningEvent(Base):
    __tablename__ = "learning_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("lev"))
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    video_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(30), default="watch_progress")
    position_sec: Mapped[int] = mapped_column(Integer, default=0)
    watched_seconds: Mapped[int] = mapped_column(Integer, default=30)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class VideoNote(Base):
    __tablename__ = "video_notes"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("not"))
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(40), ForeignKey("courses.id"), index=True)
    video_id: Mapped[str] = mapped_column(String(40), ForeignKey("video_resources.id"), index=True)
    timestamp_sec: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_bookmark: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
