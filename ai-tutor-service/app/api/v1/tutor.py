import json
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from app.core.schemas import get_request_id, ok_response
from app.schemas.business import TutorReplyDTO
from app.services.telemetry_store import telemetry_store
from app.strategies.registry import strategy_registry

router = APIRouter(tags=["Tutor"])


@router.post("/tutor/replies")
async def create_tutor_reply(body: TutorReplyDTO, request: Request):
    caller_role = request.headers.get("X-Caller-Role") or body.caller_role
    caller_id = request.headers.get("X-Caller-ID") or body.caller_id
    telemetry_store.check_rate_limit(caller_role, caller_id)

    engine = strategy_registry.get_rag_engine()
    reply = await engine.generate_reply(body.model_dump())
    return ok_response(reply, request)


@router.post("/tutor/replies/stream")
async def stream_tutor_reply(body: TutorReplyDTO, request: Request):
    """SSE 流式问答接口，实时推送 delta 文本增量与最终 done 元数据。"""
    caller_role = request.headers.get("X-Caller-Role") or body.caller_role
    caller_id = request.headers.get("X-Caller-ID") or body.caller_id
    telemetry_store.check_rate_limit(caller_role, caller_id)

    engine = strategy_registry.get_rag_engine()
    req_id = get_request_id(request)
    payload = body.model_dump()

    async def event_generator():
        if hasattr(engine, "stream_reply"):
            async for item in engine.stream_reply(payload):
                ev = item.get("event", "delta")
                yield f"event: {ev}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
        else:
            reply = await engine.generate_reply(payload)
            yield f"event: delta\ndata: {json.dumps({'event': 'delta', 'delta': reply.get('answer', '')}, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'event': 'done', 'data': reply}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Request-ID": req_id},
    )
