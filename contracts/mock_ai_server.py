"""
零依赖契约联调 Mock Server (M0 契约冻结完整交付物)
用途：当 AI 助教真实容器尚未就绪时，工程团队仅需 Python 标准库即可在 8001 端口启动完整协议兼容的 Mock 服务。
特性：
- 严格校验双平面独立鉴权头 `X-Service-Key`（`/v1/*`）与 `X-Ops-Key`（`/v1/ops/*`），支持默认测试密钥或环境变量配置；
- 完整覆盖 GET / POST / PUT / DELETE / PATCH 全部业务接口（含章节重生成、推荐、聚类）与运营治理接口；
- 暴露无需鉴权的 `/healthz` 与 `/metrics` Prometheus 文本指标。
启动方式：python contracts/mock_ai_server.py
"""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import secrets
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

SERVICE_KEY = os.environ.get("AI_SERVICE_KEY", "dev-service-key-change-in-prod")
OPS_KEY = os.environ.get("AI_OPS_KEY", "dev-ops-key-change-in-prod")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_MOCK_STATE: dict[str, Any] = {
    "jobs": {},
    "idempotency": {},
    "kb_docs": {},
    "feedback_samples": [],
    "calls_total": 0,
    "tokens_total": 0,
    "runtime_config": {
        "active_provider": "local",
        "confidence_threshold": 0.28,
        "feature_flags": {
            "enable_v2_harness": False,
            "enable_v2_personalization": False,
            "enable_v2_rsi": False,
        },
        "safety_filter": {
            "input_filter_enabled": True,
            "output_filter_enabled": True,
            "blocked_keywords": ["代考", "作弊器", "破解题库", "违法违规"],
        },
        "rate_limit_per_minute": {
            "student": 30,
            "teacher": 60,
        },
        "updated_at": _now(),
    },
}


def _envelope(data: Any, req_id: str, pagination: dict[str, int] | None = None) -> bytes:
    meta: dict[str, Any] = {
        "request_id": req_id,
        "timestamp": _now(),
    }
    if pagination is not None:
        meta["pagination"] = pagination
    return json.dumps({"data": data, "meta": meta}, ensure_ascii=False).encode("utf-8")


def _error_envelope(code: str, message: str, req_id: str, details: list[dict[str, Any]] | None = None) -> bytes:
    return json.dumps(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details or [],
                "request_id": req_id,
            }
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _paginate(items: list[Any], query: dict[str, list[str]]) -> tuple[list[Any], dict[str, int]]:
    page = max(1, int((query.get("page") or ["1"])[0]))
    page_size = max(1, min(100, int((query.get("page_size") or ["20"])[0])))
    total = len(items)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    return items[start : start + page_size], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
    }


class ContractMockHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _req_id(self) -> str:
        return self.headers.get("X-Request-ID") or f"mock_{uuid.uuid4().hex[:10]}"

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw.strip() else {}

    def _send(
        self,
        status: int,
        payload: Any,
        *,
        headers: dict[str, str] | None = None,
        pagination: dict[str, int] | None = None,
    ) -> None:
        req_id = self._req_id()
        raw = _envelope(payload, req_id, pagination=pagination)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Request-ID", req_id)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, status: int, code: str, message: str) -> None:
        req_id = self._req_id()
        raw = _error_envelope(code, message, req_id)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Request-ID", req_id)
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, status: int, text: str, content_type: str = "text/plain; version=0.0.4; charset=utf-8") -> None:
        req_id = self._req_id()
        raw = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Request-ID", req_id)
        self.end_headers()
        self.wfile.write(raw)

    def _check_auth(self, route_path: str) -> bool:
        if route_path in ("/healthz", "/metrics"):
            return True
        if route_path.startswith("/v1/ops"):
            key = self.headers.get("X-Ops-Key") or ""
            if not key or not secrets.compare_digest(key, OPS_KEY):
                self._send_error(401, "INVALID_OPS_KEY", "缺少或无效的 X-Ops-Key 运营治理凭证")
                return False
            return True
        if route_path.startswith("/v1/"):
            key = self.headers.get("X-Service-Key") or ""
            if not key or not secrets.compare_digest(key, SERVICE_KEY):
                self._send_error(401, "INVALID_SERVICE_KEY", "缺少或无效的 X-Service-Key 业务调用凭证")
                return False
            return True
        return True

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if not self._check_auth(path):
            return

        if path == "/healthz":
            self._send(200, {"status": "ok", "service": "smart-learning-ai-tutor-service-mock"})
            return

        if path == "/metrics":
            jobs_list = list(_MOCK_STATE["jobs"].values())
            succeeded_cnt = sum(1 for j in jobs_list if j.get("status") == "succeeded")
            failed_cnt = sum(1 for j in jobs_list if j.get("status") == "failed")
            queued_cnt = sum(1 for j in jobs_list if j.get("status") == "queued")
            prom_lines = [
                "# HELP ai_tutor_calls_total Total number of AI capability calls",
                "# TYPE ai_tutor_calls_total counter",
                f'ai_tutor_calls_total{{capability="all"}} {_MOCK_STATE["calls_total"]}',
                "# HELP ai_tutor_tokens_total Total tokens processed",
                "# TYPE ai_tutor_tokens_total counter",
                f'ai_tutor_tokens_total{{type="total"}} {_MOCK_STATE["tokens_total"]}',
                "# HELP ai_tutor_degraded_calls_total Total degraded fallback calls",
                "# TYPE ai_tutor_degraded_calls_total counter",
                "ai_tutor_degraded_calls_total 0",
                "# HELP ai_tutor_jobs_count Current count of async AI jobs by status",
                "# TYPE ai_tutor_jobs_count gauge",
                f'ai_tutor_jobs_count{{status="queued"}} {queued_cnt}',
                f'ai_tutor_jobs_count{{status="succeeded"}} {succeeded_cnt}',
                f'ai_tutor_jobs_count{{status="failed"}} {failed_cnt}',
                "",
            ]
            self._send_text(200, "\n".join(prom_lines))
            return

        if path == "/v1/ops/health":
            self._send(
                200,
                {
                    "service": "smart-learning-ai-tutor-service-mock",
                    "version": "1.0.0",
                    "status": "healthy",
                    "provider": _MOCK_STATE["runtime_config"]["active_provider"],
                    "active_strategies": {
                        "chapterizer": "v1_semantic_window_chapterizer",
                        "rag_engine": "v1_course_isolated_rag",
                        "assessment_generator": "v1_knowledge_driven_quiz_generator",
                        "recommender": "v1_rule_and_topic_similarity",
                        "question_clusterer": "v1_keyword_semantic_clusterer",
                    },
                },
            )
            return

        if path == "/v1/ops/jobs":
            jobs = list(_MOCK_STATE["jobs"].values())
            status_f = (query.get("status") or [None])[0]
            course_f = (query.get("course_id") or [None])[0]
            type_f = (query.get("job_type") or [None])[0]
            if status_f:
                jobs = [j for j in jobs if j["status"] == status_f]
            if course_f:
                jobs = [j for j in jobs if j["course_id"] == course_f]
            if type_f:
                jobs = [j for j in jobs if j["job_type"] == type_f]
            items, pagination = _paginate(jobs, query)
            self._send(200, {"items": items}, pagination=pagination)
            return

        if path == "/v1/ops/knowledge-bases":
            statuses = []
            for cid, docs in _MOCK_STATE["kb_docs"].items():
                statuses.append(
                    {
                        "course_id": cid,
                        "status": "ready" if docs else "unindexed",
                        "document_count": len(docs),
                        "chunk_count": len(docs) * 4,
                        "last_built_at": _now() if docs else None,
                        "updated_at": _now(),
                    }
                )
            self._send(200, {"items": statuses})
            return

        if path.startswith("/v1/ops/knowledge-bases/"):
            course_id = path.rsplit("/", 1)[-1]
            docs = _MOCK_STATE["kb_docs"].get(course_id, {})
            self._send(
                200,
                {
                    "course_id": course_id,
                    "status": "ready" if docs else "unindexed",
                    "document_count": len(docs),
                    "chunk_count": len(docs) * 4,
                    "last_built_at": _now() if docs else None,
                    "updated_at": _now(),
                },
            )
            return

        if path == "/v1/ops/metrics/usage":
            calls = _MOCK_STATE["calls_total"]
            tokens = _MOCK_STATE["tokens_total"]
            prov = _MOCK_STATE["runtime_config"]["active_provider"]
            cost = 0.0 if str(prov).startswith("local") else round(tokens * 0.0000006, 6)
            self._send(
                200,
                {
                    "total_calls": calls,
                    "total_tokens": tokens,
                    "estimated_total_cost_usd": cost,
                    "avg_latency_ms": 12.5 if calls > 0 else 0.0,
                    "degraded_calls": 0,
                    "by_capability": [
                        {
                            "capability": "tutor_rag_reply",
                            "call_count": calls,
                            "total_tokens": tokens,
                            "avg_latency_ms": 12.5 if calls > 0 else 0.0,
                            "estimated_cost_usd": cost,
                        }
                    ]
                    if calls > 0
                    else [],
                },
            )
            return

        if path == "/v1/ops/feedback-samples":
            samples = list(_MOCK_STATE["feedback_samples"])
            course_f = (query.get("course_id") or [None])[0]
            if course_f:
                samples = [s for s in samples if s["course_id"] == course_f]
            items, pagination = _paginate(samples, query)
            self._send(200, {"items": items}, pagination=pagination)
            return

        if path == "/v1/ops/runtime-config":
            self._send(200, _MOCK_STATE["runtime_config"])
            return

        if path.startswith("/v1/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            existing = _MOCK_STATE["jobs"].get(job_id)
            if existing:
                existing["status"] = "succeeded"
                existing["progress"] = 100
                self._send(200, existing)
                return
            self._send(
                200,
                {
                    "job_id": job_id,
                    "job_type": "video_chapterization",
                    "course_id": "crs_mock",
                    "status": "succeeded",
                    "progress": 100,
                    "retry_count": 0,
                    "result": {
                        "chapters": [
                            {
                                "order_index": 1,
                                "title": "课程导引与核心概念",
                                "summary": "介绍本节背景与总体脉络。",
                                "start_sec": 0,
                                "end_sec": 180,
                                "knowledge_points": ["核心概念"],
                            },
                            {
                                "order_index": 2,
                                "title": "关键算法推导与案例",
                                "summary": "结合实例拆解核心步骤。",
                                "start_sec": 180,
                                "end_sec": 420,
                                "knowledge_points": ["算法推导"],
                            },
                        ],
                        "transcript_segments": [
                            {"start_sec": 0, "end_sec": 30, "text": "欢迎同学们来到本门智能课程的第一讲。"},
                            {"start_sec": 180, "end_sec": 215, "text": "接下来我们进入核心算法的推导环节。"},
                        ],
                    },
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            )
            return

        self._send_error(404, "NOT_FOUND", f"未找到路由: {path}")

    def do_PUT(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._check_auth(path):
            return
        body = self._read_json()
        parts = path.strip("/").split("/")
        # /v1/knowledge-bases/{course_id}/documents/{document_id}
        if len(parts) == 5 and parts[0] == "v1" and parts[1] == "knowledge-bases" and parts[3] == "documents":
            course_id, doc_id = parts[2], parts[4]
            course_docs = _MOCK_STATE["kb_docs"].setdefault(course_id, {})
            course_docs[doc_id] = body
            self._send(
                200,
                {
                    "course_id": course_id,
                    "document_id": doc_id,
                    "status": "ready",
                    "document_chunk_count": 4,
                    "total_chunk_count": len(course_docs) * 4,
                    "last_built_at": _now(),
                },
            )
            return
        self._send_error(404, "NOT_FOUND", f"未找到路由: {path}")

    def do_DELETE(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._check_auth(path):
            return
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[0] == "v1" and parts[1] == "knowledge-bases" and parts[3] == "documents":
            course_id, doc_id = parts[2], parts[4]
            course_docs = _MOCK_STATE["kb_docs"].setdefault(course_id, {})
            removed = course_docs.pop(doc_id, None) is not None
            self._send(
                200,
                {
                    "course_id": course_id,
                    "document_id": doc_id,
                    "removed": removed,
                    "status": "ready" if course_docs else "unindexed",
                    "total_chunk_count": len(course_docs) * 4,
                },
            )
            return
        self._send_error(404, "NOT_FOUND", f"未找到路由: {path}")

    def do_PATCH(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._check_auth(path):
            return
        body = self._read_json()
        if path == "/v1/ops/runtime-config":
            cfg = _MOCK_STATE["runtime_config"]
            if body.get("active_provider") in ("local", "openai", "gemini"):
                cfg["active_provider"] = body["active_provider"]
            if body.get("confidence_threshold") is not None:
                cfg["confidence_threshold"] = float(body["confidence_threshold"])
            if isinstance(body.get("feature_flags"), dict):
                cfg["feature_flags"].update(body["feature_flags"])
            if isinstance(body.get("safety_filter"), dict):
                cfg["safety_filter"].update(body["safety_filter"])
            if isinstance(body.get("rate_limit_per_minute"), dict):
                cfg["rate_limit_per_minute"].update(body["rate_limit_per_minute"])
            cfg["updated_at"] = _now()
            self._send(200, cfg)
            return
        self._send_error(404, "NOT_FOUND", f"未找到路由: {path}")

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if not self._check_auth(path):
            return
        body = self._read_json()

        if path == "/v1/jobs":
            idem_key = self.headers.get("Idempotency-Key")
            if idem_key and idem_key in _MOCK_STATE["idempotency"]:
                cached_job = _MOCK_STATE["jobs"][_MOCK_STATE["idempotency"][idem_key]]
                self._send(202, cached_job, headers={"Location": f"/v1/jobs/{cached_job['job_id']}"})
                return

            job_id = f"job_mock_{uuid.uuid4().hex[:8]}"
            job_obj = {
                "job_id": job_id,
                "job_type": body.get("job_type", "video_chapterization"),
                "course_id": body.get("course_id", "crs_mock"),
                "status": "succeeded" if body.get("run_inline") else "queued",
                "progress": 100 if body.get("run_inline") else 0,
                "retry_count": 0,
                "payload": body.get("payload", {}),
                "result": {
                    "chapters": [
                        {
                            "order_index": 1,
                            "title": "第 1 节：课程导引与核心概念",
                            "summary": "介绍本讲背景与关键定义。",
                            "start_sec": 0,
                            "end_sec": 180,
                            "knowledge_points": ["核心概念"],
                        },
                        {
                            "order_index": 2,
                            "title": "第 2 节：机制推导与工程实践",
                            "summary": "拆解核心推导步骤与复杂度边界。",
                            "start_sec": 180,
                            "end_sec": 360,
                            "knowledge_points": ["机制推导"],
                        },
                    ],
                    "transcript_segments": [
                        {"start_sec": 0, "end_sec": 180, "text": "第一部分介绍课程背景与关键定义。"},
                        {"start_sec": 180, "end_sec": 360, "text": "第二部分拆解核心推导步骤与工程实践。"},
                    ],
                    "degraded": False,
                }
                if body.get("run_inline")
                else None,
                "error_message": None,
                "duration_ms": 18 if body.get("run_inline") else None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            _MOCK_STATE["jobs"][job_id] = job_obj
            if idem_key:
                _MOCK_STATE["idempotency"][idem_key] = job_id
            self._send(202, job_obj, headers={"Location": f"/v1/jobs/{job_id}"})
            return

        if path == "/v1/chapters/regenerations":
            _MOCK_STATE["calls_total"] += 1
            _MOCK_STATE["tokens_total"] += 120
            start_sec = int(body.get("start_sec", 0))
            end_sec = max(start_sec + 1, int(body.get("end_sec", 60)))
            instr = body.get("instruction") or "结构化精修"
            base_title = body.get("current_title") or body.get("video_title") or "核心章节"
            self._send(
                200,
                {
                    "title": f"{base_title}（{instr[:12]}）",
                    "summary": "根据指定时间区间字幕与教师指令重新提炼的章节核心摘要。",
                    "start_sec": start_sec,
                    "end_sec": end_sec,
                    "knowledge_points": [base_title[:16]],
                    "strategy_used": "v1_semantic_window_chapterizer",
                    "degraded": False,
                },
            )
            return

        if path == "/v1/tutor/replies":
            _MOCK_STATE["calls_total"] += 1
            _MOCK_STATE["tokens_total"] += 160
            self._send(
                200,
                {
                    "answer": f"【Mock 助教回答】针对您的问题「{body.get('question', '')}」，根据课程讲义，核心要点在于分层解耦与契约先行。",
                    "confidence": 0.91,
                    "needs_teacher_review": False,
                    "policy_blocked": False,
                    "degraded": False,
                    "strategy_used": "v1_course_isolated_rag",
                    "citations": [
                        {
                            "source_type": "document",
                            "source_id": "doc_mock_1",
                            "title": "第一章讲义",
                            "snippet": "核心要点在于分层解耦与契约先行。",
                            "timestamp_sec": None,
                            "score": 0.89,
                        }
                    ],
                    "suggested_followups": ["能举一个实际场景的例子吗？", "这一节常考的知识点有哪些？"],
                },
            )
            return

        if path == "/v1/assessments/generations":
            _MOCK_STATE["calls_total"] += 1
            _MOCK_STATE["tokens_total"] += 220
            scope = body.get("scope") or {}
            count = max(1, min(20, int(scope.get("count") or 1)))
            keys = ["A", "B", "C", "D"]
            questions = []
            for idx in range(count):
                ans_key = keys[idx % 4]
                opts = []
                for pos, k in enumerate(keys):
                    text = (
                        "提交异步作业 (202 Accepted) + 状态轮询或签名 Webhook 回调"
                        if pos == (idx % 4)
                        else f"干扰项 {k}：跨层直连或阻塞式反模式设计"
                    )
                    opts.append({"key": k, "text": text})
                questions.append(
                    {
                        "question_type": "single_choice",
                        "stem": f"（第 {idx + 1} 题）在前后端与 AI 服务解耦架构中，长耗时视频章节化应采用哪种通信模式？",
                        "options": opts,
                        "answer": [ans_key],
                        "explanation": f"正确答案 {ans_key}：通过异步任务协议可彻底解耦并避免网关超时。",
                        "difficulty": "medium",
                        "knowledge_point": "异步任务协议",
                        "chapter_id": None,
                    }
                )
            self._send(
                200,
                {
                    "mode": body.get("mode", "chapter_practice"),
                    "strategy_used": "v1_knowledge_driven_quiz_generator",
                    "targeted_knowledge_points": ["异步任务协议"],
                    "degraded": False,
                    "questions": questions,
                },
            )
            return

        if path == "/v1/recommendations/generations":
            _MOCK_STATE["calls_total"] += 1
            _MOCK_STATE["tokens_total"] += 90
            candidates = body.get("candidate_items") or [
                {"item_id": "ch_mock_1", "item_type": "chapter", "course_id": body.get("course_id", "crs_mock"), "title": "第一章重点复习"}
            ]
            top_k = max(1, min(10, int(body.get("top_k") or 4)))
            items = [
                {
                    "item_id": c.get("item_id", f"item_{i}"),
                    "item_type": c.get("item_type", "chapter"),
                    "course_id": c.get("course_id", body.get("course_id", "crs_mock")),
                    "video_id": c.get("video_id"),
                    "chapter_id": c.get("chapter_id"),
                    "start_sec": c.get("start_sec"),
                    "title": c.get("title", "推荐学习单元"),
                    "score": round(0.92 - i * 0.05, 3),
                    "priority_tag": "薄弱点攻坚",
                    "reason": "针对近期薄弱知识点进行定向巩固复习",
                }
                for i, c in enumerate(candidates[:top_k])
            ]
            self._send(200, {"strategy_used": "v1_rule_and_topic_similarity", "items": items})
            return

        if path == "/v1/analytics/question-clusters":
            _MOCK_STATE["calls_total"] += 1
            _MOCK_STATE["tokens_total"] += 80
            q_list = body.get("questions") or []
            sample_qs = [str(q.get("question", "")) for q in q_list[:3] if q.get("question")] or ["什么是注意力机制？"]
            self._send(
                200,
                {
                    "strategy_used": "v1_keyword_semantic_clusterer",
                    "clusters": [
                        {
                            "topic_label": (body.get("known_knowledge_points") or ["核心知识点"])[0],
                            "question_count": max(1, len(q_list)),
                            "avg_confidence": 0.82,
                            "unanswered_count": 0,
                            "sample_questions": sample_qs,
                            "teaching_suggestion": "建议结合课堂实例进一步强化推导边界讲解。",
                        }
                    ],
                },
            )
            return

        if path.startswith("/v1/ops/jobs/") and path.endswith("/retries"):
            job_id = path.split("/")[4]
            job_obj = _MOCK_STATE["jobs"].get(job_id) or {
                "job_id": job_id,
                "job_type": "video_chapterization",
                "course_id": "crs_mock",
                "status": "queued",
                "progress": 0,
                "retry_count": 0,
                "payload": {},
                "created_at": _now(),
            }
            if isinstance(body.get("override_payload"), dict):
                job_obj.setdefault("payload", {}).update(body["override_payload"])
            job_obj["retry_count"] = int(job_obj.get("retry_count", 0)) + 1
            job_obj["status"] = "succeeded" if body.get("run_inline") else "queued"
            job_obj["progress"] = 100 if body.get("run_inline") else 0
            job_obj["error_message"] = None
            job_obj["updated_at"] = _now()
            _MOCK_STATE["jobs"][job_id] = job_obj
            self._send(202, job_obj, headers={"Location": f"/v1/jobs/{job_id}"})
            return

        if path.startswith("/v1/ops/knowledge-bases/") and path.endswith("/rebuilds"):
            course_id = path.split("/")[4]
            job_id = f"job_reb_{uuid.uuid4().hex[:8]}"
            job_obj = {
                "job_id": job_id,
                "job_type": "knowledge_index_build",
                "course_id": course_id,
                "status": "succeeded" if body.get("run_inline") else "queued",
                "progress": 100 if body.get("run_inline") else 0,
                "retry_count": 0,
                "payload": {"action": "full_rebuild"},
                "result": {"course_id": course_id, "status": "ready"} if body.get("run_inline") else None,
                "error_message": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            _MOCK_STATE["jobs"][job_id] = job_obj
            self._send(202, job_obj, headers={"Location": f"/v1/jobs/{job_id}"})
            return

        if path == "/v1/ops/feedback-samples":
            sample = {
                "sample_id": body.get("sample_id") or f"smp_mock_{uuid.uuid4().hex[:8]}",
                "course_id": body.get("course_id", "crs_mock"),
                "sample_type": body.get("sample_type", "tutor_reply"),
                "source_reason": body.get("source_reason", "student_downvote"),
                "input_text": body.get("input_text", ""),
                "model_output": body.get("model_output", ""),
                "expected_correction": body.get("expected_correction"),
                "tags": body.get("tags", []),
                "status": "queued_for_harness_eval",
                "created_at": _now(),
            }
            _MOCK_STATE["feedback_samples"].insert(0, sample)
            self._send(201, sample)
            return

        self._send_error(404, "NOT_FOUND", f"未找到路由: {path}")


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 8001), ContractMockHandler)
    print("Contract Mock AI Server listening on http://127.0.0.1:8001")
    server.serve_forever()
