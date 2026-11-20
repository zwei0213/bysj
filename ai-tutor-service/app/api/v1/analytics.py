from fastapi import APIRouter, Request
from app.core.schemas import ok_response
from app.schemas.business import QuestionClusterDTO
from app.strategies.registry import strategy_registry

router = APIRouter(tags=["Analytics"])


@router.post("/analytics/question-clusters")
async def cluster_student_questions(body: QuestionClusterDTO, request: Request):
    clusterer = strategy_registry.get_question_clusterer()
    result = await clusterer.cluster_questions(body.model_dump())
    return ok_response(result, request)
