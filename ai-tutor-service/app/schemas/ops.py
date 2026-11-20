"""AI 运营治理接口 (/v1/ops/*) 请求数据传输对象 (Pydantic v2 DTOs)。"""
from typing import Any, Literal
from pydantic import BaseModel, Field


class RetryJobDTO(BaseModel):
    override_payload: dict[str, Any] | None = None
    run_inline: bool = False


class RebuildIndexDTO(BaseModel):
    callback_url: str | None = None
    run_inline: bool = False


class FeedbackSampleDTO(BaseModel):
    sample_id: str | None = None
    course_id: str = Field(..., min_length=1)
    sample_type: str = "tutor_reply"
    source_reason: str = "student_downvote"
    input_text: str = Field(..., min_length=1)
    model_output: str = ""
    expected_correction: str | None = None
    tags: list[str] = Field(default_factory=list)


class RuntimeConfigPatchDTO(BaseModel):
    active_provider: Literal["local", "openai", "gemini"] | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.05, le=0.95)
    feature_flags: dict[str, bool] | None = None
    safety_filter: dict[str, Any] | None = None
    rate_limit_per_minute: dict[str, int] | None = None
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
