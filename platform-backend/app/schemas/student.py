from typing import Any, Literal
from pydantic import BaseModel, Field


class LearningEventDTO(BaseModel):
    course_id: str
    video_id: str | None = None
    event_type: Literal["watch_progress", "replay_segment", "skip_segment"] = "watch_progress"
    position_sec: int = Field(default=0, ge=0)
    watched_seconds: int = Field(default=30, ge=0, le=3600)
    completed: bool = False


class NoteCreateDTO(BaseModel):
    course_id: str
    video_id: str
    timestamp_sec: int = Field(default=0, ge=0)
    content: str = Field(..., min_length=1, max_length=1000)
    is_bookmark: bool = False


class NotePatchDTO(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=1000)
    is_bookmark: bool | None = None
    timestamp_sec: int | None = Field(default=None, ge=0)


class TutorAskDTO(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    quick_action: Literal["explain_simply", "give_example", "summarize_key_points"] | None = None
    video_context: dict[str, Any] | None = None


class MessageFeedbackDTO(BaseModel):
    vote: Literal["up", "down", "reported"] | None = None
    bookmarked: bool | None = None
    reason: str = ""


class QuizSessionStartDTO(BaseModel):
    session_type: Literal["chapter_practice", "personalized", "exam", "wrong_retry"] = "chapter_practice"
    chapter_id: str | None = None
    exam_id: str | None = None
    wrong_record_ids: list[str] = Field(default_factory=list)
    count: int = Field(default=3, ge=1, le=25)


class QuizSubmissionDTO(BaseModel):
    answers: dict[str, list[str]] = Field(default_factory=dict)


class WrongQuestionPatchDTO(BaseModel):
    is_mastered: bool | None = None
    review_note: str | None = None
