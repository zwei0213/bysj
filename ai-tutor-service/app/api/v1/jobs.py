from fastapi import APIRouter, Header, Request
from app.core.schemas import get_request_id, ok_response
from app.schemas.business import JobCreateDTO
from app.services.job_manager import job_manager

router = APIRouter(tags=["AsyncJobs"])


@router.post("/jobs", status_code=202)
async def create_async_job(
    body: JobCreateDTO,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    job = await job_manager.submit_job(
        job_type=str(body.job_type),
        course_id=body.course_id,
        payload=body.payload,
        callback_url=body.callback_url,
        idempotency_key=idempotency_key,
        request_id=get_request_id(request),
        run_inline=body.run_inline,
    )
    return ok_response(
        job,
        request,
        status_code=202,
        headers={"Location": f"/v1/jobs/{job['job_id']}"},
    )


@router.get("/jobs/{job_id}")
async def get_async_job(job_id: str, request: Request):
    job = job_manager.get_job(job_id)
    return ok_response(job, request)
