import logging
from typing import Any
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.schemas import get_request_id

logger = logging.getLogger(__name__)


class AppError(Exception):
    """标准业务与协议异常，自动渲染为契约规范错误信封。"""

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


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
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
            {
                "field": ".".join(str(loc) for loc in err.get("loc", [])),
                "reason": err.get("msg", "invalid value"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            headers={"X-Request-ID": req_id},
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不符合契约格式规范",
                    "details": details,
                    "request_id": req_id,
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        req_id = get_request_id(request)
        logger.exception("Unhandled AI service error [request_id=%s]: %s", req_id, exc)
        safe_msg = (
            "AI 助教服务内部异常，请携带 request_id 联系管理员"
            if settings.app_env == "production"
            else f"AI 助教服务内部异常: {exc}"
        )
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": req_id},
            content={
                "error": {
                    "code": "INTERNAL_AI_SERVICE_ERROR",
                    "message": safe_msg,
                    "details": [],
                    "request_id": req_id,
                }
            },
        )
