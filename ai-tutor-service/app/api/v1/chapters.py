from fastapi import APIRouter, Request
from app.core.schemas import ok_response
from app.schemas.business import ChapterRegenerateDTO
from app.services.telemetry_store import telemetry_store
from app.strategies.registry import strategy_registry

router = APIRouter(tags=["Chapters"])


@router.post("/chapters/regenerations")
async def regenerate_single_chapter(body: ChapterRegenerateDTO, request: Request):
    caller_role = request.headers.get("X-Caller-Role") or body.caller_role
    caller_id = request.headers.get("X-Caller-ID") or body.caller_id
    telemetry_store.check_rate_limit(caller_role, caller_id)

    chapterizer = strategy_registry.get_chapterizer()
    result = await chapterizer.regenerate_single_chapter(
        course_id=body.course_id,
        payload=body.model_dump(),
    )
    return ok_response(result, request)
