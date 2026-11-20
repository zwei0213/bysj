from fastapi import APIRouter, Request
from app.core.schemas import ok_response
from app.schemas.business import RecommendationDTO
from app.strategies.registry import strategy_registry

router = APIRouter(tags=["Recommendations"])


@router.post("/recommendations/generations")
async def generate_recommendations(body: RecommendationDTO, request: Request):
    recommender = strategy_registry.get_recommender()
    result = await recommender.recommend(body.model_dump())
    return ok_response(result, request)
