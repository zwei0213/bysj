"""AI 业务接口 (/v1/*) 请求数据传输对象 (Pydantic v2 DTOs)。"""
from typing import Any, Literal
from pydantic import BaseModel, Field
from app.core.enums import AssessmentMode, JobType, KnowledgeSourceType


class JobCreateDTO(BaseModel):
    job_type: JobType
    course_id: str = Field(..., min_length=1)
    callback_url: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    run_inline: bool = Field(
        default=False,
        description="开发联调/单测可选同步快速完成；生产默认 False 走后台异步队列",
    )


class ChapterRegenerateDTO(BaseModel):
    course_id: str = Field(..., min_length=1)
    video_id: str = ""
    video_title: str = ""
    start_sec: int = Field(default=0, ge=0)
    end_sec: int = Field(default=60, ge=1)
    transcript_segments: list[dict[str, Any]] = Field(default_factory=list)
    transcript_text: str = ""
    current_title: str = ""
    instruction: str = ""
    caller_role: str | None = None
    caller_id: str | None = None


class DocumentUpsertDTO(BaseModel):
    title: str = Field(..., min_length=1)
    source_type: KnowledgeSourceType = KnowledgeSourceType.DOCUMENT
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoContextDTO(BaseModel):
    video_id: str | None = None
    video_title: str | None = None
    chapter_id: str | None = None
    chapter_title: str | None = None
    timestamp_sec: float | None = None


class TutorPolicyDTO(BaseModel):
    strict_course_only: bool = True
    allow_out_of_syllabus: bool = False
    tone: str = "encouraging_academic"
    forbidden_topics: list[str] = Field(default_factory=list)


class TutorReplyDTO(BaseModel):
    course_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=2000)
    quick_action: Literal["explain_simply", "give_example", "summarize_key_points"] | None = None
    video_context: VideoContextDTO | None = None
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    tutor_policy: TutorPolicyDTO = Field(default_factory=TutorPolicyDTO)
    caller_role: str | None = None
    caller_id: str | None = None


class AssessmentScopeDTO(BaseModel):
    chapter_ids: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    question_types: list[Literal["single_choice", "multiple_choice", "true_false"]] = Field(
        default_factory=lambda: ["single_choice", "multiple_choice", "true_false"]
    )
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "mixed"
    count: int = Field(default=4, ge=1, le=20)


class LearnerProfileDTO(BaseModel):
    weak_knowledge_points: list[str] = Field(default_factory=list)
    recent_wrong_question_stems: list[str] = Field(default_factory=list)
    recent_asked_questions: list[str] = Field(default_factory=list)
    mastery_by_topic: dict[str, float] = Field(default_factory=dict)


class AssessmentGenerationDTO(BaseModel):
    course_id: str = Field(..., min_length=1)
    mode: AssessmentMode = AssessmentMode.CHAPTER_PRACTICE
    scope: AssessmentScopeDTO = Field(default_factory=AssessmentScopeDTO)
    learner_profile: LearnerProfileDTO | None = None
    caller_role: str | None = None
    caller_id: str | None = None


class RecommendationDTO(BaseModel):
    course_id: str = "global"
    candidate_items: list[dict[str, Any]] = Field(default_factory=list)
    learner_history: dict[str, Any] = Field(default_factory=dict)
    top_k: int = Field(default=4, ge=1, le=10)


class QuestionClusterDTO(BaseModel):
    course_id: str = Field(..., min_length=1)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    known_knowledge_points: list[str] = Field(default_factory=list)
