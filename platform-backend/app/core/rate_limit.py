"""滑动时间窗接口限流守卫（保护认证接口防暴力破解、保护按角色高频 AI 调用配额）。"""
from collections import defaultdict, deque
import threading
import time
from typing import Callable
from fastapi import Depends, Request
from app.core.errors import PlatformError
from app.core.security import get_current_user


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._role_ai_limits: dict[str, int] = {
            "student": 30,
            "teacher": 60,
            "admin": 120,
        }
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_sec: int = 60, message: str | None = None) -> None:
        now = time.monotonic()
        cutoff = now - window_sec
        with self._lock:
            q = self._buckets[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                raise PlatformError(
                    "RATE_LIMIT_EXCEEDED",
                    message or "请求过于频繁，请稍后再试",
                    status_code=429,
                )
            q.append(now)

    def update_role_limits(self, new_limits: dict[str, int]) -> dict[str, int]:
        with self._lock:
            for role, val in (new_limits or {}).items():
                if isinstance(val, int) and val >= 1:
                    self._role_ai_limits[str(role)] = val
            return dict(self._role_ai_limits)

    def get_role_limits(self) -> dict[str, int]:
        with self._lock:
            return dict(self._role_ai_limits)

    def get_limit_for_role(self, role: str) -> int:
        with self._lock:
            return int(self._role_ai_limits.get(role, 30))


rate_limiter = SlidingWindowLimiter()


def update_role_ai_rate_limits(limits: dict[str, int]) -> dict[str, int]:
    return rate_limiter.update_role_limits(limits)


def get_role_ai_rate_limits() -> dict[str, int]:
    return rate_limiter.get_role_limits()


def limit_requests(scope: str, *, max_per_minute: int = 30) -> Callable:
    def dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "local"
        rate_limiter.check(f"{scope}:{client_ip}", limit=max_per_minute, window_sec=60)

    return dependency


def limit_ai_quota(user=Depends(get_current_user)):
    """按 (user.role, user.id) 执行分钟级滑动时间窗 AI 调用配额限流。"""
    limit = rate_limiter.get_limit_for_role(user.role)
    rate_limiter.check(
        f"ai_quota:{user.role}:{user.id}",
        limit=limit,
        window_sec=60,
        message=f"当前角色 ({user.role}) AI 调用频率已达每分钟 {limit} 次上限，请稍后再试",
    )
    return user
