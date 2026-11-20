import json
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.errors import ok_response
from app.core.security import verify_webhook_signature
from app.models.entities import VideoResource, WebhookReceipt
from app.services.teacher_service import apply_chapterization_result_to_video

router = APIRouter(prefix="/internal/v1/webhooks", tags=["InternalWebhooks"])


@router.post("/ai-jobs")
async def receive_ai_job_webhook(
    request: Request,
    x_webhook_timestamp: str | None = Header(default=None, alias="X-Webhook-Timestamp"),
    x_webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    raw_body = await request.body()
    verify_webhook_signature(raw_body, timestamp=x_webhook_timestamp, signature=x_webhook_signature)

    payload = json.loads(raw_body.decode("utf-8"))
    job_id = payload.get("job_id", "")
    idem = idempotency_key or f"idem_{job_id}_{payload.get('status')}"

    # 幂等去重：重复投递的 Webhook 直接返回确认，避免重复生成草稿版本
    if db.get(WebhookReceipt, idem):
        return ok_response({"acknowledged": True, "duplicate": True, "job_id": job_id}, request)

    db.add(WebhookReceipt(idempotency_key=idem, job_id=job_id))

    if payload.get("job_type") == "video_chapterization":
        video_id = (payload.get("payload") or {}).get("video_id")
        video = db.get(VideoResource, video_id) if video_id else None
        if not video and job_id:
            video = db.scalar(select(VideoResource).where(VideoResource.ai_job_id == job_id))
        if video:
            if payload.get("status") == "succeeded" and payload.get("result"):
                apply_chapterization_result_to_video(db, video, payload["result"])
            elif payload.get("status") == "failed":
                video.outline_status = "failed"

    db.commit()
    return ok_response({"acknowledged": True, "duplicate": False, "job_id": job_id}, request)
