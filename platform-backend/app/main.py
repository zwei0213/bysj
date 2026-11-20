from collections import Counter
from contextlib import asynccontextmanager
import threading
import time
import uuid
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from app.api.internal.webhooks import router as webhooks_router
from app.api.v1.admin import router as admin_router
from app.api.v1.auth_and_public import router as public_router
from app.api.v1.media import router as media_router
from app.api.v1.student import router as student_router
from app.api.v1.teacher import router as teacher_router
from app.clients.ai_service_client import ai_client
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine, get_db
from app.core.errors import PlatformError, ok_response, register_platform_exception_handlers
from app.core.migrations import run_incremental_migrations
from app.models.entities import Course, QuizSession, TutorMessage, User
from app.services.bootstrap_service import ensure_production_bootstrap
from app.services.storage_service import ensure_storage_dirs

_METRICS_LOCK = threading.Lock()
_HTTP_REQUEST_TOTAL: Counter[tuple[str, str]] = Counter()
_HTTP_DURATION_SECONDS_SUM: float = 0.0


def init_database() -> None:
    ensure_storage_dirs()
    Base.metadata.create_all(bind=engine)
    run_incremental_migrations(engine)

    with SessionLocal() as db:
        ensure_production_bootstrap(db)


async def sync_backend_ai_model_config() -> None:
    """若在业务后端 .env / config.py 中填写了模型与接口密钥，启动时通过 /v1/ops/runtime-config 自动同步至 AI 服务。"""
    gw_patch: dict[str, str] = {}
    for field_name in (
        "openai_base_url",
        "openai_api_key",
        "openai_model",
        "openai_embedding_model",
        "openai_asr_model",
        "gemini_api_key",
        "gemini_model",
        "gemini_embedding_model",
        "asr_provider",
        "whisper_model_size",
    ):
        val = getattr(settings, field_name, "").strip()
        if val:
            gw_patch[field_name] = val

    patch_payload: dict[str, object] = {}
    prov = settings.ai_provider.strip().lower()
    if prov in ("local", "openai", "gemini"):
        patch_payload["active_provider"] = prov
    if gw_patch:
        patch_payload["model_gateway"] = gw_patch

    if patch_payload:
        await ai_client.ops_patch_runtime_config(patch_payload, request_id="bootstrap_ai_cfg_sync")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_production_security()
    init_database()
    await sync_backend_ai_model_config()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart Learning Platform Business Backend",
        version=settings.version,
        description="工程团队商业交付版：智能学习平台业务服务、RBAC 权限、人工校准工作台与管理端六大治理域 API",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Content-Range", "Accept-Ranges", "Location"],
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        global _HTTP_DURATION_SECONDS_SUM
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        t0 = time.perf_counter()
        response = await call_next(request)
        elapsed = max(0.0, time.perf_counter() - t0)
        with _METRICS_LOCK:
            _HTTP_REQUEST_TOTAL[(request.method, str(response.status_code))] += 1
            _HTTP_DURATION_SECONDS_SUM += elapsed
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    register_platform_exception_handlers(app)
    app.include_router(media_router)
    app.include_router(public_router)
    app.include_router(student_router)
    app.include_router(teacher_router)
    app.include_router(admin_router)
    app.include_router(webhooks_router)

    @app.get("/healthz", tags=["Probe"])
    async def liveness_probe(request: Request, db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception as exc:
            raise PlatformError("DATABASE_UNAVAILABLE", f"数据库连接探活失败: {exc}", status_code=503) from exc
        return ok_response(
            {
                "status": "ok",
                "database": "connected",
                "service": settings.service_name,
                "version": settings.version,
            },
            request,
        )

    @app.get("/metrics", tags=["Probe"])
    async def prometheus_metrics(db: Session = Depends(get_db)):
        user_total = int(db.scalar(select(func.count()).select_from(User)) or 0)
        course_total = int(db.scalar(select(func.count()).select_from(Course)) or 0)
        quiz_total = int(db.scalar(select(func.count()).select_from(QuizSession)) or 0)
        qa_total = int(db.scalar(select(func.count()).select_from(TutorMessage).where(TutorMessage.role == "user")) or 0)

        lines = [
            "# HELP slp_http_requests_total Total HTTP requests handled by platform-backend.",
            "# TYPE slp_http_requests_total counter",
        ]
        with _METRICS_LOCK:
            for (method, status_code), cnt in sorted(_HTTP_REQUEST_TOTAL.items()):
                lines.append(f'slp_http_requests_total{{method="{method}",status="{status_code}"}} {cnt}')
            dur_sum = _HTTP_DURATION_SECONDS_SUM

        lines.extend([
            "# HELP slp_http_request_duration_seconds_sum Total HTTP request processing time in seconds.",
            "# TYPE slp_http_request_duration_seconds_sum counter",
            f"slp_http_request_duration_seconds_sum {dur_sum:.6f}",
            "# HELP slp_users_total Total registered users in database.",
            "# TYPE slp_users_total gauge",
            f"slp_users_total {user_total}",
            "# HELP slp_courses_total Total courses in database.",
            "# TYPE slp_courses_total gauge",
            f"slp_courses_total {course_total}",
            "# HELP slp_quiz_sessions_total Total quiz sessions created.",
            "# TYPE slp_quiz_sessions_total gauge",
            f"slp_quiz_sessions_total {quiz_total}",
            "# HELP slp_tutor_questions_total Total AI tutor questions asked.",
            "# TYPE slp_tutor_questions_total gauge",
            f"slp_tutor_questions_total {qa_total}",
            "",
        ])
        return Response(
            content="\n".join(lines),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()
