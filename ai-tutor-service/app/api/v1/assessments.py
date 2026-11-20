from fastapi import APIRouter, Request
from app.core.schemas import ok_response
from app.schemas.business import AssessmentGenerationDTO
from app.services.telemetry_store import telemetry_store
from app.strategies.registry import strategy_registry

router = APIRouter(tags=["Assessments"])


@router.post("/assessments/generations")
async def generate_assessments(body: AssessmentGenerationDTO, request: Request):
    caller_role = request.headers.get("X-Caller-Role") or body.caller_role
    caller_id = request.headers.get("X-Caller-ID") or body.caller_id
    telemetry_store.check_rate_limit(caller_role, caller_id)

    generator = strategy_registry.get_assessment_generator()
    result = await generator.generate_questions(body.model_dump())
    return ok_response(result, request)
