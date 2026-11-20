"""
基于标准轮子 `PyJWT` 与 `pwdlib` (Argon2id) 的认证安全模块。
- 新密码统一使用 Argon2id 哈希，同时向后兼容验证存量 `pbkdf2$` 格式密码；
- 使用 `PyJWT` 签发与校验 Access/Refresh Token；
- 提供媒体文件 HMAC 能力签名与 Webhook 防重放验签。
"""
import hashlib
import hmac
import secrets
import time
from typing import Any, Callable
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from pwdlib import PasswordHash
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.errors import PlatformError

bearer_scheme = HTTPBearer(auto_error=False)
_password_hasher = PasswordHash.recommended()


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise PlatformError("WEAK_PASSWORD", "密码长度不能少于 8 位", status_code=400)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    # 向后兼容存量 PBKDF2 密码哈希
    if stored_hash.startswith("pbkdf2$"):
        try:
            _, salt_val, expected_digest = stored_hash.split("$", 2)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_val.encode("utf-8"), 120_000).hex()
            return secrets.compare_digest(actual, expected_digest)
        except Exception:
            return False
    try:
        return _password_hasher.verify(password, stored_hash)
    except Exception:
        return False


def create_access_token(*, user_id: str, username: str, role: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "username": username,
            "role": role,
            "token_use": "access",
            "iat": now,
            "exp": now + settings.jwt_expire_hours * 3600,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def create_refresh_token(*, user_id: str, username: str, role: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "username": username,
            "role": role,
            "token_use": "refresh",
            "jti": secrets.token_hex(8),
            "iat": now,
            "exp": now + settings.jwt_refresh_expire_days * 86400,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_jwt_token(token: str, *, expected_use: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        token_use = payload.get("token_use", "access")
        if token_use != expected_use:
            raise ValueError(f"unexpected token_use '{token_use}'")
        return payload
    except Exception as exc:
        raise PlatformError("UNAUTHORIZED", f"无效或已过期的认证令牌: {exc}", status_code=401) from exc


def sign_media_path(category: str, filename: str) -> str:
    """为受保护媒体路径生成防遍历枚举的 HMAC-SHA256 能力签名。"""
    msg = f"media:{category}/{filename}".encode("utf-8")
    return hmac.new(settings.jwt_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def verify_media_signature(category: str, filename: str, signature: str | None) -> bool:
    if not signature:
        return False
    expected = sign_media_path(category, filename)
    return secrets.compare_digest(signature, expected)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    from app.models.entities import User

    if not credentials or credentials.scheme.lower() != "bearer":
        raise PlatformError("UNAUTHORIZED", "请先登录并携带 Bearer 访问令牌", status_code=401)
    claims = decode_jwt_token(credentials.credentials, expected_use="access")
    user = db.get(User, claims["sub"])
    if not user or not user.is_active:
        raise PlatformError("ACCOUNT_DISABLED", "账号不存在或已被管理员停用", status_code=403)
    return user


def require_roles(*allowed_roles: str) -> Callable:
    def dependency(user=Depends(get_current_user)):
        if user.role not in allowed_roles:
            raise PlatformError(
                "FORBIDDEN_ROLE",
                f"当前角色 ({user.role}) 无权执行该操作，需要角色: {', '.join(allowed_roles)}",
                status_code=403,
            )
        if user.role == "teacher" and not user.teacher_approved:
            reason_suffix = f"（驳回原因：{user.reject_reason}）" if getattr(user, "reject_reason", "") else ""
            msg = (
                f"您的教师资质申请未通过审核{reason_suffix}"
                if getattr(user, "teacher_status", "") == "rejected"
                else "您的教师资格正在等待平台管理员审核开通"
            )
            raise PlatformError("TEACHER_PENDING_APPROVAL", msg, status_code=403)
        return user

    return dependency


def verify_webhook_signature(
    raw_body: bytes,
    timestamp: str | None = Header(default=None, alias="X-Webhook-Timestamp"),
    signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
) -> None:
    if not timestamp or not signature:
        raise PlatformError("INVALID_WEBHOOK_SIGNATURE", "缺少 Webhook 签名头", status_code=401)
    try:
        ts_int = int(timestamp)
    except ValueError as exc:
        raise PlatformError("INVALID_WEBHOOK_TIMESTAMP", "Webhook 时间戳格式非法", status_code=401) from exc

    if abs(int(time.time()) - ts_int) > settings.webhook_tolerance_seconds:
        raise PlatformError("WEBHOOK_REPLAY_EXPIRED", "Webhook 请求超出防重放时间窗口", status_code=401)

    message = timestamp.encode("utf-8") + b"." + raw_body
    expected = "sha256=" + hmac.new(
        settings.webhook_signing_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(signature, expected):
        raise PlatformError("INVALID_WEBHOOK_SIGNATURE", "Webhook HMAC 签名校验失败", status_code=401)
