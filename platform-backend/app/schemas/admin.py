from typing import Any, Literal
from pydantic import BaseModel, Field


class OrgCreateDTO(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    org_type: Literal["department", "grade", "class"] = "class"
    parent_id: str | None = None


class OrgPatchDTO(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    org_type: Literal["department", "grade", "class"] | None = None
    parent_id: str | None = None


class UserCreateDTO(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=80)
    email: str = ""
    password: str = Field(..., min_length=8)
    role: Literal["student", "teacher", "admin"] = "student"
    org_id: str | None = None


class BatchUserImportDTO(BaseModel):
    csv_text: str = Field(..., min_length=3, description="CSV 行格式: username,display_name,role,email,password")
    org_id: str | None = None


class UserPatchDTO(BaseModel):
    display_name: str | None = None
    email: str | None = None
    org_id: str | None = None
    role: Literal["student", "teacher", "admin"] | None = None
    is_active: bool | None = None
    teacher_approved: bool | None = None
    teacher_status: Literal["pending", "approved", "rejected"] | None = None
    reject_reason: str | None = None
    reset_password: str | None = None


class PermissionsMatrixPutDTO(BaseModel):
    roles: dict[str, dict[str, Any]] | None = None
    items: list[dict[str, Any]] | None = None


class BatchClassAssignDTO(BaseModel):
    course_id: str
    class_id: str
    student_ids: list[str] = Field(default_factory=list)
    source_org_id: str | None = None
    group_name: str = ""


class CategoryCreateDTO(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    slug: str = ""
    sort_order: int = 0
    is_visible: bool = True


class CategoryPatchDTO(BaseModel):
    name: str | None = None
    slug: str | None = None
    sort_order: int | None = None
    is_visible: bool | None = None


class CourseGovernancePatchDTO(BaseModel):
    status: Literal["published", "pending_review", "rejected", "archived", "takedown"] | None = None
    reject_reason: str | None = None
    is_featured: bool | None = None
    storage_quota_mb: int | None = None
    expires_at: str | None = None


class TakedownDTO(BaseModel):
    reason: str = "敏感或版权合规处置下架"


class AdminRetryDTO(BaseModel):
    run_inline: bool = True


class SpotCheckDrawDTO(BaseModel):
    sample_type: Literal["all", "tutor_reply", "outline", "question"] = "all"
    sample_ratio_pct: float = Field(default=20.0, ge=1.0, le=100.0)
    max_count: int = Field(default=10, ge=1, le=100)
    course_id: str | None = None


class SpotCheckCreateDTO(BaseModel):
    course_id: str
    sample_type: Literal["tutor_reply", "outline", "question"] = "tutor_reply"
    message_id: str | None = None
    input_text: str = Field(..., min_length=1)
    model_output: str = ""
    admin_note: str = ""


class SampleResolutionDTO(BaseModel):
    status: Literal["corrected", "blocked", "synced_eval"]
    admin_note: str = ""
    corrected_output: str = ""


class AdminRuntimePatchDTO(BaseModel):
    confidence_threshold: float | None = None
    feature_flags: dict[str, bool] | None = None
    safety_filter: dict[str, Any] | None = None
    rate_limit_per_minute: dict[str, int] | None = None
    active_provider: str | None = None
    model_gateway: dict[str, Any] | None = None
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_embedding_model: str | None = None
    openai_asr_model: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None
    gemini_embedding_model: str | None = None
    asr_provider: str | None = None
    whisper_model_size: str | None = None


class AnnouncementCreateDTO(BaseModel):
    title: str = Field(..., min_length=2, max_length=160)
    content: str = Field(..., min_length=2)
    target_role: Literal["all", "student", "teacher"] = "all"


class TicketPatchDTO(BaseModel):
    status: Literal["open", "in_progress", "resolved"] | None = None
    assignee_id: str | None = None
    assignee_name: str | None = None
    assignee_note: str | None = None
