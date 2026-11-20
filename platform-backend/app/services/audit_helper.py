from sqlalchemy.orm import Session
from app.models.entities import AuditLog, User


def record_audit(
    db: Session,
    *,
    actor: User,
    action: str,
    target_type: str,
    target_id: str,
    detail: str = "",
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor.id,
        actor_name=actor.display_name,
        actor_role=actor.role,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    )
    db.add(entry)
    return entry
