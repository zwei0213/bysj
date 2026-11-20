from contextlib import asynccontextmanager
from typing import Any
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from app.api.ops.routes import router as ops_router
from app.api.v1.routes import router as v1_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.schemas import ok_response
from app.services.job_manager import job_manager
from app.services.telemetry_store import telemetry_store
from app.services.vector_store import knowledge_store
from app.strategies.registry import strategy_registry


async def _handle_video_chapterization(job_record: dict[str, Any]) -> dict[str, Any]:
    chapterizer = strategy_registry.get_chapterizer()
    return await chapterizer.chapterize(
        course_id=job_record["course_id"],
        payload=job_record.get("payload") or {},
    )


async def _handle_knowledge_index_build(job_record: dict[str, Any]) -> dict[str, Any]:
    return await knowledge_store.rebuild_course_index_async(job_record["course_id"])


def register_job_handlers() -> None:
    job_manager.register_handler("video_chapterization", _handle_video_chapterization)
    job_manager.register_handler("knowledge_index_build", _handle_knowledge_index_build)


register_job_handlers()


def render_prometheus_metrics() -> str:
    """生成标准 Prometheus 0.0.4 文本暴露格式指标。"""
    usage = telemetry_store.summarize_usage()
    job_counts = job_manager.count_jobs_by_status()

    lines = [
        "# HELP ai_tutor_calls_total Total number of AI capability invocations",
        "# TYPE ai_tutor_calls_total counter",
    ]
    by_cap = usage.get("by_capability") or []
    if by_cap:
        for cap in by_cap:
            cap_name = str(cap["capability"]).replace('"', "")
            lines.append(f'ai_tutor_calls_total{{capability="{cap_name}"}} {int(cap["call_count"])}')
    else:
        lines.append('ai_tutor_calls_total{capability="all"} 0')

    lines.extend([
        "# HELP ai_tutor_tokens_total Total prompt and completion tokens processed",
        "# TYPE ai_tutor_tokens_total counter",
        f'ai_tutor_tokens_total{{type="prompt"}} {int(usage.get("prompt_tokens", 0))}',
        f'ai_tutor_tokens_total{{type="completion"}} {int(usage.get("completion_tokens", 0))}',
        "# HELP ai_tutor_degraded_calls_total Total number of degraded fallback AI calls",
        "# TYPE ai_tutor_degraded_calls_total counter",
        f'ai_tutor_degraded_calls_total {int(usage.get("degraded_calls", 0))}',
        "# HELP ai_tutor_jobs_count Current count of async AI jobs by status",
        "# TYPE ai_tutor_jobs_count gauge",
    ])
    for st in ("queued", "running", "succeeded", "failed", "cancelled"):
        lines.append(f'ai_tutor_jobs_count{{status="{st}"}} {int(job_counts.get(st, 0))}')
    lines.append("")
    return "\n".join(lines)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_production_security()
    telemetry_store.restore_persisted_provider_settings()
    register_job_handlers()
    job_manager.recover_interrupted_jobs()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Smart Learning AI Tutor Service",
        version=settings.version,
        description="高校 AI 团队交付：无业务状态的独立 AI 助教与智能内容生成服务 (v1.0)",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request.state.request_id)
        return response

    register_exception_handlers(app)
    app.include_router(v1_router)
    app.include_router(ops_router)

    @app.get("/healthz", tags=["Probe"])
    async def liveness_probe(request: Request):
        return ok_response({"status": "ok", "service": settings.service_name}, request)

    @app.get("/metrics", tags=["Probe"], response_class=PlainTextResponse)
    async def prometheus_metrics():
        return PlainTextResponse(
            content=render_prometheus_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app


app = create_app()
