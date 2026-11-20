"""工程端业务领域状态与角色枚举常量（继承 StrEnum，与数据库及 JSON 字符串完全兼容）。"""
from enum import StrEnum


class UserRole(StrEnum):
    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


class TeacherApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CourseStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"
    PUBLISHED = "published"
    CLOSED = "closed"
    ARCHIVED = "archived"
    TAKEDOWN = "takedown"


class OutlineStatus(StrEnum):
    PROCESSING = "processing"
    DRAFT_REVIEW = "draft_review"
    PUBLISHED = "published"
    FAILED = "failed"


class DocumentIndexStatus(StrEnum):
    INDEXING = "indexing"
    READY = "ready"
    STALE = "stale"
    REMOVED = "removed"


class QuestionReviewStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class QuizSessionType(StrEnum):
    CHAPTER_PRACTICE = "chapter_practice"
    PERSONALIZED = "personalized"
    EXAM = "exam"
    WRONG_RETRY = "wrong_retry"


class QuizSessionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"


class InboxStatus(StrEnum):
    PENDING = "pending"
    RESOLVED = "resolved"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
