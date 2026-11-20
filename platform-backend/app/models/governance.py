"""管理治理、Webhook 幂等回执、问题样本池、公告、工单与操作审计日志实体模型。"""
from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.enums import TicketStatus
from app.models.common import generate_uid, utc_now_iso


class WebhookReceipt(Base):
    """记录已处理的 Webhook Idempotency-Key，保证异步回调严格幂等。"""

    __tablename__ = "webhook_receipts"

    idempotency_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(60), index=True)
    processed_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class QualitySample(Base):
    """管理端域 4：问题样本队列与 AI 内容抽检台账。"""

    __tablename__ = "quality_samples"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("qsp"))
    course_id: Mapped[str] = mapped_column(String(40), index=True)
    message_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    sample_type: Mapped[str] = mapped_column(String(30), default="tutor_reply")
    source_reason: Mapped[str] = mapped_column(String(40), default="student_downvote")
    input_text: Mapped[str] = mapped_column(Text, default="")
    model_output: Mapped[str] = mapped_column(Text, default="")
    corrected_output: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    admin_note: Mapped[str] = mapped_column(Text, default="")
    synced_to_ai_eval: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class Announcement(Base):
    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("ann"))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    target_role: Mapped[str] = mapped_column(String(20), default="all")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("tkt"))
    submitter_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(40), default="AI助教反馈")
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.OPEN)
    assignee_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    assignee_name: Mapped[str] = mapped_column(String(80), default="")
    assignee_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
    updated_at: Mapped[str] = mapped_column(String(40), default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: generate_uid("aud"))
    actor_id: Mapped[str] = mapped_column(String(40), index=True)
    actor_name: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now_iso)
