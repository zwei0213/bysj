from typing import Any, Literal
from pydantic import BaseModel, Field


class CourseCreateDTO(BaseModel):
    code: str = ""
    title: str = Field(..., min_length=2, max_length=160)
    subtitle: str = ""
    category: str = "人工智能"
    cover_url: str = ""
    description: str = ""
    start_date: str = ""
    end_date: str = ""
    submit_for_review: bool = True


class CoursePatchDTO(BaseModel):
    code: str | None = None
    title: str | None = None
    subtitle: str | None = None
    category: str | None = None
    cover_url: str | None = None
    cover_color: str | None = None
    description: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    status: Literal["draft", "pending_review", "closed", "archived"] | None = None


class ClassCreateDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    term: str = "2026秋季学期"


class StudentRosterImportDTO(BaseModel):
    class_id: str | None = None
    group_name: str = ""
    usernames_or_csv: str | list[str] = ""


class EnrollmentPatchDTO(BaseModel):
    class_id: str | None = None
    group_name: str | None = None


class VideoUploadDTO(BaseModel):
    title: str = Field(..., min_length=2, max_length=160)
    media_url: str = ""
    duration_sec: int = Field(default=480, ge=30, le=14400)
    raw_transcript_text: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    run_inline: bool = False


class ChunkUploadInitDTO(BaseModel):
    filename: str = Field(..., min_length=1, max_length=260)
    title: str = ""
    total_size: int = Field(..., ge=1)
    total_chunks: int = Field(..., ge=1, le=10000)


class ChunkUploadCompleteDTO(BaseModel):
    title: str = ""
    knowledge_points: list[str] = Field(default_factory=list)
    run_inline: bool = False


class ChapterRegenerateDTO(BaseModel):
    start_sec: int = Field(default=0, ge=0)
    end_sec: int = Field(default=60, ge=1)
    current_title: str = ""
    instruction: str = ""


class CalibratedOutlineDTO(BaseModel):
    chapters: list[dict[str, Any]] = Field(..., min_length=1)
    change_note: str = "教师人工编辑章节标题、摘要与时间戳"


class DocumentUploadDTO(BaseModel):
    title: str = Field(..., min_length=2, max_length=160)
    file_format: str = "markdown"
    content_text: str = Field(..., min_length=5)


class QuestionBatchGenDTO(BaseModel):
    chapter_ids: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    question_types: list[str] = Field(default_factory=lambda: ["single_choice", "multiple_choice", "true_false"])
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "mixed"
    count: int = Field(default=3, ge=1, le=15)


class QuestionManualCreateDTO(BaseModel):
    chapter_id: str | None = None
    question_type: str = "single_choice"
    stem: str = Field(..., min_length=2)
    options: list[dict[str, str]] = Field(default_factory=list)
    answer: list[str] = Field(..., min_length=1)
    explanation: str = ""
    difficulty: str = "medium"
    knowledge_point: str = "核心概念"


class QuestionReviewPatchDTO(BaseModel):
    stem: str | None = None
    options: list[dict[str, str]] | None = None
    answer: list[str] | None = None
    explanation: str | None = None
    difficulty: str | None = None
    knowledge_point: str | None = None
    question_type: str | None = None
    chapter_id: str | None = None
    review_status: Literal["pending_review", "approved", "rejected"] | None = None


class ExamCreateDTO(BaseModel):
    title: str = Field(..., min_length=2, max_length=160)
    question_ids: list[str] = Field(..., min_length=1)
    time_limit_min: int = Field(default=30, ge=5, le=300)
    max_attempts: int = Field(default=3, ge=1, le=50)
    target_class_id: str | None = None
    start_time: str | None = None
    end_time: str | None = None


class InboxResolveDTO(BaseModel):
    teacher_answer: str = Field(..., min_length=2)
    sync_to_kb: bool = True
    pin_as_faq: bool = False


class TutorConfigDTO(BaseModel):
    strict_course_only: bool = True
    allow_out_of_syllabus: bool = False
    tone: str = "encouraging_academic"
    forbidden_topics: list[str] = Field(default_factory=list)


class FaqCreateDTO(BaseModel):
    question: str = Field(..., min_length=2, max_length=280)
    answer: str = Field(..., min_length=2)
    is_pinned: bool = True


class FaqPatchDTO(BaseModel):
    question: str | None = Field(default=None, min_length=2, max_length=280)
    answer: str | None = Field(default=None, min_length=2)
    is_pinned: bool | None = None


class TeacherQualityFeedbackDTO(BaseModel):
    sample_type: Literal["outline", "question", "tutor_reply"] = "outline"
    input_text: str = Field(..., min_length=1)
    model_output: str = ""
    reason: str = ""
