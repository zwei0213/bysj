"""业务平面路由总装配器 (/v1/*，统一校验 X-Service-Key)。"""
from fastapi import APIRouter, Depends
from app.api.v1 import analytics, assessments, chapters, jobs, knowledge_bases, recommendations, tutor
from app.core.security import verify_service_key

router = APIRouter(prefix="/v1", dependencies=[Depends(verify_service_key)])

router.include_router(jobs.router)
router.include_router(chapters.router)
router.include_router(knowledge_bases.router)
router.include_router(tutor.router)
router.include_router(assessments.router)
router.include_router(recommendations.router)
router.include_router(analytics.router)
