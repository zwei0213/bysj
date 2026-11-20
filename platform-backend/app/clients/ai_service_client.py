"""
AI 服务防腐调用层 (Anti-Corruption Layer HTTP Client)。
原则：
1. 网站后端与管理端绝不直连 AI 内部数据库或文件，100% 通过此客户端经 HTTP 契约调用；
2. 严格区分 `X-Service-Key`（业务平面 `/v1/*`）与 `X-Ops-Key`（治理平面 `/v1/ops/*`）；
3. 全链路透传 `X-Request-ID`、`X-Caller-Role`、`X-Caller-ID`，并在网络抖动或超时时记录告警并返回明确降级结构，绝不阻断主站正常教学流程。
"""
from collections.abc import AsyncIterator
import json
import logging
from typing import Any, Literal
import uuid
import httpx
from app.core.config import settings
from app.core.errors import PlatformError

logger = logging.getLogger(__name__)


class AiServiceClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def set_transport(self, transport: httpx.AsyncBaseTransport | None) -> None:
        self._transport = transport

    async def _request(
        self,
        plane: Literal["service", "ops"],
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "X-Request-ID": request_id or f"req_{uuid.uuid4().hex[:12]}",
        }
        if plane == "service":
            headers["X-Service-Key"] = settings.ai_service_key
        else:
            headers["X-Ops-Key"] = settings.ai_ops_key

        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if caller_role:
            headers["X-Caller-Role"] = str(caller_role)
        if caller_id:
            headers["X-Caller-ID"] = str(caller_id)

        async with httpx.AsyncClient(
            base_url=settings.ai_service_base_url.rstrip("/"),
            timeout=settings.ai_http_timeout_seconds,
            transport=self._transport,
        ) as client:
            resp = await client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=params,
            )
            if resp.status_code == 429:
                raise PlatformError("RATE_LIMIT_EXCEEDED", "AI 服务角色调用配额已达每分钟上限，请稍后再试", status_code=429)
            resp.raise_for_status()
            return resp.json()["data"]

    # ==========================
    # 业务平面 (/v1/*)
    # ==========================
    async def submit_video_chapterization_job(
        self,
        *,
        course_id: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
        request_id: str | None = None,
        run_inline: bool = False,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "POST",
                "/v1/jobs",
                json_body={
                    "job_type": "video_chapterization",
                    "course_id": course_id,
                    "callback_url": settings.platform_callback_url,
                    "payload": payload,
                    "run_inline": run_inline,
                },
                request_id=request_id,
                idempotency_key=idempotency_key,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI job submission degraded for course %s: %s", course_id, exc)
            return {
                "job_id": f"degraded_{uuid.uuid4().hex[:8]}",
                "status": "failed",
                "error_message": f"AI 助教服务暂不可达: {exc}",
                "degraded": True,
            }

    async def get_job(self, job_id: str, *, request_id: str | None = None) -> dict[str, Any] | None:
        try:
            return await self._request("service", "GET", f"/v1/jobs/{job_id}", request_id=request_id)
        except Exception as exc:
            logger.warning("Failed to poll AI job %s: %s", job_id, exc)
            return None

    async def regenerate_chapter(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "POST",
                "/v1/chapters/regenerations",
                json_body=payload,
                request_id=request_id,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI single chapter regeneration degraded: %s", exc)
            raw_txt = str(payload.get("transcript_text") or "").strip()
            snippet = raw_txt[:140] if raw_txt else "依据选定时间段字幕重生成的章节内容提要。"
            base_title = str(payload.get("current_title") or "重构章节要点").strip()
            instr = str(payload.get("instruction") or "").strip()
            return {
                "title": f"{base_title}（精修）" if not instr else f"{base_title}",
                "summary": snippet,
                "start_sec": int(payload.get("start_sec", 0)),
                "end_sec": int(payload.get("end_sec", 60)),
                "knowledge_points": payload.get("knowledge_points") or [],
                "degraded": True,
            }

    async def upsert_document(
        self,
        *,
        course_id: str,
        document_id: str,
        title: str,
        source_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "PUT",
                f"/v1/knowledge-bases/{course_id}/documents/{document_id}",
                json_body={
                    "title": title,
                    "source_type": source_type,
                    "content": content,
                    "metadata": metadata or {},
                },
                request_id=request_id,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI document upsert degraded (%s/%s): %s", course_id, document_id, exc)
            return {"status": "stale", "degraded": True, "error": str(exc)}

    async def delete_document(
        self,
        *,
        course_id: str,
        document_id: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "DELETE",
                f"/v1/knowledge-bases/{course_id}/documents/{document_id}",
                request_id=request_id,
            )
        except Exception as exc:
            logger.warning("AI document delete degraded (%s/%s): %s", course_id, document_id, exc)
            return {"removed": False, "degraded": True, "error": str(exc)}

    async def generate_tutor_reply(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "POST",
                "/v1/tutor/replies",
                json_body=payload,
                request_id=request_id,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI tutor reply degraded: %s", exc)
            return {
                "answer": "当前 AI 助教计算服务正在繁忙或维护中（已启用降级保护）。您的问题已被自动记录并转交授课教师收件箱，稍后可在对话记录中查看回复。",
                "confidence": 0.0,
                "needs_teacher_review": True,
                "degraded": True,
                "citations": [],
                "suggested_followups": ["查看课程置顶精选 FAQ", "先阅读当前视频文字稿"],
            }

    async def stream_tutor_reply(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """流式代理调用 `/v1/tutor/replies/stream`，逐条产出 `delta` 与 `done` 事件。"""
        headers = {
            "X-Request-ID": request_id or f"req_{uuid.uuid4().hex[:12]}",
            "X-Service-Key": settings.ai_service_key,
        }
        if caller_role:
            headers["X-Caller-Role"] = str(caller_role)
        if caller_id:
            headers["X-Caller-ID"] = str(caller_id)

        try:
            async with httpx.AsyncClient(
                base_url=settings.ai_service_base_url.rstrip("/"),
                timeout=max(settings.ai_http_timeout_seconds, 90.0),
                transport=self._transport,
            ) as client:
                async with client.stream("POST", "/v1/tutor/replies/stream", headers=headers, json=payload) as resp:
                    if resp.status_code == 429:
                        raise PlatformError("RATE_LIMIT_EXCEEDED", "AI 服务角色调用配额已达每分钟上限，请稍后再试", status_code=429)
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        line = raw_line.strip()
                        if not line.startswith("data:"):
                            continue
                        try:
                            yield json.loads(line[5:].strip())
                        except ValueError:
                            continue
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI tutor streaming degraded: %s", exc)
            fallback = {
                "answer": "当前 AI 助教计算服务正在繁忙或维护中（已启用降级保护）。您的问题已被自动记录并转交授课教师收件箱，稍后可在对话记录中查看回复。",
                "confidence": 0.0,
                "needs_teacher_review": True,
                "degraded": True,
                "citations": [],
                "suggested_followups": ["查看课程置顶精选 FAQ", "先阅读当前视频文字稿"],
            }
            yield {"event": "delta", "delta": fallback["answer"]}
            yield {"event": "done", "data": fallback}

    async def generate_assessments(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "POST",
                "/v1/assessments/generations",
                json_body=payload,
                request_id=request_id,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI assessment generation degraded: %s", exc)
            return {"questions": [], "degraded": True}

    async def generate_recommendations(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "POST",
                "/v1/recommendations/generations",
                json_body=payload,
                request_id=request_id,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI recommendation degraded: %s", exc)
            return {"items": [], "degraded": True}

    async def cluster_questions(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
        caller_role: str | None = None,
        caller_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request(
                "service",
                "POST",
                "/v1/analytics/question-clusters",
                json_body=payload,
                request_id=request_id,
                caller_role=caller_role,
                caller_id=caller_id,
            )
        except PlatformError:
            raise
        except Exception as exc:
            logger.warning("AI question clustering degraded: %s", exc)
            return {"clusters": [], "degraded": True}

    # ==========================
    # 运营治理平面 (/v1/ops/*)
    # ==========================
    async def ops_health(self, *, request_id: str | None = None) -> dict[str, Any]:
        try:
            return await self._request("ops", "GET", "/v1/ops/health", request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops health unreachable: %s", exc)
            return {"status": "unreachable", "error": str(exc)}

    async def ops_list_jobs(
        self,
        *,
        status: str | None = None,
        course_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        params = {
            k: v
            for k, v in {
                "status": status,
                "course_id": course_id,
                "start_time": start_time,
                "end_time": end_time,
            }.items()
            if v
        }
        try:
            return await self._request("ops", "GET", "/v1/ops/jobs", params=params, request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops list jobs degraded: %s", exc)
            return {"items": []}

    async def ops_retry_job(
        self,
        job_id: str,
        *,
        run_inline: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "ops",
            "POST",
            f"/v1/ops/jobs/{job_id}/retries",
            json_body={"run_inline": run_inline},
            request_id=request_id,
        )

    async def ops_list_indexes(self, *, request_id: str | None = None) -> dict[str, Any]:
        try:
            return await self._request("ops", "GET", "/v1/ops/knowledge-bases", request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops list indexes degraded: %s", exc)
            return {"items": []}

    async def ops_rebuild_index(
        self,
        course_id: str,
        *,
        run_inline: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._request(
            "ops",
            "POST",
            f"/v1/ops/knowledge-bases/{course_id}/rebuilds",
            json_body={"callback_url": settings.platform_callback_url, "run_inline": run_inline},
            request_id=request_id,
        )

    async def ops_usage_metrics(self, *, request_id: str | None = None) -> dict[str, Any]:
        try:
            return await self._request("ops", "GET", "/v1/ops/metrics/usage", request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops usage metrics degraded: %s", exc)
            return {
                "total_calls": 0,
                "total_tokens": 0,
                "estimated_total_cost_usd": 0.0,
                "avg_latency_ms": 0.0,
                "by_capability": [],
            }

    async def ops_submit_feedback_sample(
        self,
        payload: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request("ops", "POST", "/v1/ops/feedback-samples", json_body=payload, request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops submit feedback sample degraded: %s", exc)
            return {"degraded": True, "error": str(exc)}

    async def ops_get_runtime_config(self, *, request_id: str | None = None) -> dict[str, Any]:
        try:
            return await self._request("ops", "GET", "/v1/ops/runtime-config", request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops get runtime config degraded: %s", exc)
            return {"feature_flags": {}, "safety_filter": {"blocked_keywords": []}}

    async def ops_patch_runtime_config(
        self,
        patch: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._request("ops", "PATCH", "/v1/ops/runtime-config", json_body=patch, request_id=request_id)
        except Exception as exc:
            logger.warning("AI ops patch runtime config degraded: %s", exc)
            return {**patch, "degraded": True}


ai_client = AiServiceClient()
