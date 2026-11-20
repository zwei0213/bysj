"""ORM 公共主键与时间戳生成工具。"""
from datetime import datetime, timezone
import uuid


def generate_uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
