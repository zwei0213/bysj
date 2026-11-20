"""AI 服务领域枚举常量（继承 StrEnum，与 JSON 字符串线上格式 100% 兼容）。"""
from enum import StrEnum


class JobType(StrEnum):
    VIDEO_CHAPTERIZATION = "video_chapterization"
    KNOWLEDGE_INDEX_BUILD = "knowledge_index_build"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IndexStatus(StrEnum):
    UNINDEXED = "unindexed"
    INDEXING = "indexing"
    READY = "ready"
    STALE = "stale"
    FAILED = "failed"


class KnowledgeSourceType(StrEnum):
    DOCUMENT = "document"
    VIDEO_TRANSCRIPT = "video_transcript"
    TEACHER_QA = "teacher_qa"
    FAQ = "faq"


class AssessmentMode(StrEnum):
    TEACHER_BANK = "teacher_bank"
    CHAPTER_PRACTICE = "chapter_practice"
    PERSONALIZED_DIAGNOSTIC = "personalized_diagnostic"
