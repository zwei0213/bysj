import hashlib
import hmac
import secrets
from fastapi import Header
from app.core.config import settings
from app.core.errors import AppError


async def verify_service_key(
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> str:
    """校验业务接口调用密钥（Platform Backend -> AI Service /v1/*）。"""
    if not x_service_key or not secrets.compare_digest(x_service_key, settings.ai_service_key):
        raise AppError(
            code="INVALID_SERVICE_KEY",
            message="缺少或无效的 X-Service-Key 业务调用凭证",
            status_code=401,
        )
    return x_service_key


async def verify_ops_key(
    x_ops_key: str | None = Header(default=None, alias="X-Ops-Key"),
) -> str:
    """校验独立运营治理密钥（Platform Admin Proxy -> AI Service /v1/ops/*）。"""
    if not x_ops_key or not secrets.compare_digest(x_ops_key, settings.ai_ops_key):
        raise AppError(
            code="INVALID_OPS_KEY",
            message="缺少或无效的 X-Ops-Key 运营治理凭证（运营接口与业务接口独立鉴权）",
            status_code=401,
        )
    return x_ops_key


def sign_webhook_payload(timestamp: str, raw_body: bytes) -> str:
    """使用 HMAC-SHA256 对异步任务回调载荷进行防篡改签名。"""
    message = timestamp.encode("utf-8") + b"." + raw_body
    digest = hmac.new(
        settings.webhook_signing_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"
