"""独立鉴权的 AI 运营与质量治理接口 (/v1/ops/*，统一校验 X-Ops-Key)。"""
from fastapi import APIRouter, Depends, Query, Request
from app.core.config import settings
from app.core.enums import JobType
from app.core.schemas import get_request_id, ok_response, paginate_items
from app.core.security import verify_ops_key
from app.schemas.ops import FeedbackSampleDTO, RebuildIndexDTO, RetryJobDTO, RuntimeConfigPatchDTO
from app.services.job_manager import job_manager
from app.services.telemetry_store import telemetry_store
from app.services.vector_store import knowledge_store
from app.strategies.registry import strategy_registry

router = APIRouter(prefix="/v1/ops", tags=["Operations"], dependencies=[Depends(verify_ops_key)])


@router.get("/health")
async def get_ops_health(request: Request):
    active_provider = telemetry_store.get_runtime_config().get("active_provider", settings.ai_provider)
    return ok_response(
        {
            "service": settings.service_name,
            "version": settings.version,
            "status": "healthy",
            "provider": active_provider,
            "active_strategies": strategy_registry.describe_active_strategies(),
        },
        request,
    )


@router.get("/jobs")
async def list_ops_jobs(
    request: Request,
    status: str | None = Query(default=None),
    course_id: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    start_time: str | None = Query(default=None),
    end_time: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    all_jobs = job_manager.list_jobs(
        status=status,
        course_id=course_id,
        job_type=job_type,
        start_time=start_time,
        end_time=end_time,
    )
    items, pagination = paginate_items(all_jobs, page=page, page_size=page_size)
    return ok_response({"items": items}, request, pagination=pagination)


@router.post("/jobs/{job_id}/retries", status_code=202)
async def retry_failed_job(job_id: str, request: Request, body: RetryJobDTO | None = None):
    job = await job_manager.retry_job(
        job_id,
        override_payload=body.override_payload if body else None,
        run_inline=bool(body and body.run_inline),
    )
    return ok_response(
        job,
        request,
        status_code=202,
        headers={"Location": f"/v1/jobs/{job_id}"},
    )


@router.get("/knowledge-bases")
async def list_knowledge_base_statuses(request: Request):
    items = knowledge_store.list_all_statuses()
    return ok_response({"items": items}, request)


@router.get("/knowledge-bases/{course_id}")
async def get_knowledge_base_status(course_id: str, request: Request):
    return ok_response(knowledge_store.get_course_status(course_id), request)


@router.post("/knowledge-bases/{course_id}/rebuilds", status_code=202)
async def rebuild_course_knowledge_base(
    course_id: str,
    request: Request,
    body: RebuildIndexDTO | None = None,
):
    job = await job_manager.submit_job(
        job_type=str(JobType.KNOWLEDGE_INDEX_BUILD),
        course_id=course_id,
        payload={"action": "full_rebuild"},
        callback_url=body.callback_url if body else None,
        request_id=get_request_id(request),
        run_inline=bool(body and body.run_inline),
    )
    return ok_response(
        job,
        request,
        status_code=202,
        headers={"Location": f"/v1/jobs/{job['job_id']}"},
    )


@router.get("/metrics/usage")
async def get_usage_metrics(
    request: Request,
    course_id: str | None = Query(default=None),
    start_time: str | None = Query(default=None),
    end_time: str | None = Query(default=None),
):
    return ok_response(
        telemetry_store.summarize_usage(
            course_id=course_id,
            start_time=start_time,
            end_time=end_time,
        ),
        request,
    )


@router.post("/feedback-samples", status_code=201)
async def create_feedback_sample(body: FeedbackSampleDTO, request: Request):
    sample = telemetry_store.add_feedback_sample(body.model_dump())
    return ok_response(sample, request, status_code=201)


@router.get("/feedback-samples")
async def list_feedback_samples(
    request: Request,
    course_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    all_samples = telemetry_store.list_feedback_samples(course_id=course_id)
    items, pagination = paginate_items(all_samples, page=page, page_size=page_size)
    return ok_response({"items": items}, request, pagination=pagination)


@router.get("/runtime-config")
async def get_runtime_config(request: Request):
    return ok_response(telemetry_store.get_runtime_config(), request)


@router.patch("/runtime-config")
async def patch_runtime_config(body: RuntimeConfigPatchDTO, request: Request):
    updated = telemetry_store.patch_runtime_config(body.model_dump(exclude_none=True))
    return ok_response(updated, request)
