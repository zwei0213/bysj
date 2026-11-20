"""
领域 ORM 模型统一导出入口。
具体表结构已按业务子域拆分至：
- `account.py`（组织架构、用户账号、角色权限矩阵、站内通知）
- `course.py`（课程分类、课程、班级、视频、分片上传会话、多版本提纲、讲义资料）
- `tutor.py`（AI 助教配置、会话消息、未解答收件箱、精选 FAQ）
- `assessment.py`（题库、试卷、测验场次、错题本、学习行为、视频笔记）
- `governance.py`（Webhook 幂等回执、问题样本池、公告、工单、审计日志）
"""
from app.models.account import Organization, RolePermissionConfig, User, UserNotification
from app.models.assessment import (
    ExamPaper,
    LearningEvent,
    QuestionBankItem,
    QuizSession,
    VideoNote,
    WrongQuestionRecord,
)
from app.models.course import (
    ChunkUploadSession,
    ClassGroup,
    Course,
    CourseCategory,
    CourseDocument,
    Enrollment,
    OutlineVersion,
    VideoResource,
)
from app.models.governance import (
    Announcement,
    AuditLog,
    QualitySample,
    SupportTicket,
    WebhookReceipt,
)
from app.models.tutor import (
    CourseFaq,
    CourseTutorConfig,
    TutorMessage,
    TutorThread,
    UnansweredQuestion,
)

__all__ = [
    "Announcement",
    "AuditLog",
    "ChunkUploadSession",
    "ClassGroup",
    "Course",
    "CourseCategory",
    "CourseDocument",
    "CourseFaq",
    "CourseTutorConfig",
    "Enrollment",
    "ExamPaper",
    "LearningEvent",
    "Organization",
    "OutlineVersion",
    "QualitySample",
    "QuestionBankItem",
    "QuizSession",
    "RolePermissionConfig",
    "SupportTicket",
    "TutorMessage",
    "TutorThread",
    "UnansweredQuestion",
    "User",
    "UserNotification",
    "VideoNote",
    "VideoResource",
    "WebhookReceipt",
    "WrongQuestionRecord",
]
