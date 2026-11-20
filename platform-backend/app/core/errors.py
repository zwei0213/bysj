from datetime import datetime, timezone
import logging
from typing import Any
import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.config import settings

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def get_request_id(request: Request) -> str:
    req_id = getattr(request.state, "request_id", None)
    if req_id:
        return req_id
    return request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"


def ok_response(
    data: Any,
    request: Request,
    *,
    status_code: int = 200,
    pagination: dict[str, int] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    req_id = get_request_id(request)
    meta: dict[str, Any] = {"request_id": req_id, "timestamp": utc_now_iso()}
    if pagination is not None:
        meta["pagination"] = pagination
    resp_headers = {"X-Request-ID": req_id}
    if headers:
        resp_headers.update(headers)
    return JSONResponse(status_code=status_code, content={"data": data, "meta": meta}, headers=resp_headers)


def paginate_list(items: list[Any], page: int = 1, page_size: int = 20) -> tuple[list[Any], dict[str, int]]:
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    return items[start : start + page_size], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


class PlatformError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def register_platform_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def handle_platform_error(request: Request, exc: PlatformError) -> JSONResponse:
        req_id = get_request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            headers={"X-Request-ID": req_id},
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        req_id = get_request_id(request)
        details = [
            {"field": ".".join(str(x) for x in err.get("loc", [])), "reason": err.get("msg", "invalid")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            headers={"X-Request-ID": req_id},
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求字段格式校验未通过",
                    "details": details,
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        req_id = get_request_id(request)
        logger.exception("Unhandled platform error [request_id=%s]: %s", req_id, exc)
        safe_msg = (
            "业务后端内部异常，请携带 request_id 联系管理员"
            if settings.app_env == "production"
            else f"业务后端内部异常: {exc}"
        )
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": req_id},
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": safe_msg,
                    "details": [],
                    "request_id": req_id,
                }
            },
        )
