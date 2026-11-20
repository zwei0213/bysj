"""
异步长任务调度队列（SQLite WAL 持久化 + `closing()` 无泄漏连接 + Semaphore 并发限流 + 24h 幂等 + Webhook 指数退避重试）。
"""
import asyncio
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Coroutine
import uuid
import httpx
from app.core.config import settings
from app.core.errors import AppError
from app.core.schemas import utc_now_iso
from app.core.security import sign_webhook_payload
from app.services.telemetry_store import parse_iso_timestamp

logger = logging.getLogger(__name__)

IDEMPOTENCY_TTL_SECONDS = 24 * 3600
_WEBHOOK_RETRY_DELAYS = (0.3, 0.8)


class AsyncJobManager:
    def __init__(self, storage_dir: str | None = None) -> None:
        self._override_dir = storage_dir
        self._lock = threading.RLock()
        self._initialized_path: str | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._handlers: dict[str, Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]] = {}

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrent_jobs))
        return self._semaphore

    def _db_path(self) -> Path:
        base = Path(self._override_dir or settings.data_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base / "async_jobs.db"

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        db_file = self._db_path()
        conn = sqlite3.connect(str(db_file), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        with closing(conn):
            if self._initialized_path != str(db_file):
                with self._lock:
                    conn.execute("PRAGMA journal_mode=WAL;")
                    conn.execute("PRAGMA synchronous=NORMAL;")
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS ai_jobs (
                            job_id TEXT PRIMARY KEY,
                            job_type TEXT NOT NULL,
                            course_id TEXT NOT NULL,
                            status TEXT NOT NULL,
                            progress INTEGER NOT NULL DEFAULT 0,
                            retry_count INTEGER NOT NULL DEFAULT 0,
                            payload_json TEXT NOT NULL,
                            callback_url TEXT,
                            request_id TEXT NOT NULL,
                            result_json TEXT,
                            error_message TEXT,
                            duration_ms INTEGER,
                            idempotency_key TEXT UNIQUE,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_ai_jobs_status ON ai_jobs(status);
                        CREATE INDEX IF NOT EXISTS idx_ai_jobs_course ON ai_jobs(course_id);
                        CREATE INDEX IF NOT EXISTS idx_ai_jobs_created ON ai_jobs(created_at);
                        """
                    )
                    conn.commit()
                    self._initialized_path = str(db_file)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def register_handler(
        self,
        job_type: str,
        handler: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]],
    ) -> None:
        self._handlers[job_type] = handler

    def recover_interrupted_jobs(self) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE ai_jobs
                SET status = 'failed',
                    error_message = '服务容器重启导致执行中断，可在监控台一键重试恢复',
                    updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (utc_now_iso(),),
            )
            return cur.rowcount

    async def submit_job(
        self,
        *,
        job_type: str,
        course_id: str,
        payload: dict[str, Any],
        callback_url: str | None = None,
        idempotency_key: str | None = None,
        request_id: str | None = None,
        run_inline: bool = False,
    ) -> dict[str, Any]:
        if job_type not in self._handlers:
            raise AppError("UNSUPPORTED_JOB_TYPE", f"不支持的异步任务类型: {job_type}", status_code=400)

        with self._lock, self._conn() as conn:
            if idempotency_key:
                existing = conn.execute(
                    "SELECT * FROM ai_jobs WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if existing:
                    created_dt = parse_iso_timestamp(existing["created_at"])
                    now_dt = datetime.now(timezone.utc)
                    if created_dt is None or (now_dt - created_dt).total_seconds() < IDEMPOTENCY_TTL_SECONDS:
                        return self._row_to_public(existing)
                    conn.execute("UPDATE ai_jobs SET idempotency_key = NULL WHERE job_id = ?", (existing["job_id"],))

            job_id = f"job_{uuid.uuid4().hex[:12]}"
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO ai_jobs (
                    job_id, job_type, course_id, status, progress, retry_count,
                    payload_json, callback_url, request_id, idempotency_key, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', 0, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    job_type,
                    course_id,
                    json.dumps(payload, ensure_ascii=False),
                    callback_url,
                    request_id or f"req_{uuid.uuid4().hex[:10]}",
                    idempotency_key,
                    now,
                    now,
                ),
            )

        if run_inline:
            await self._execute_job(job_id)
        else:
            asyncio.create_task(self._execute_job(job_id))
        return self.get_job(job_id)

    async def retry_job(
        self,
        job_id: str,
        *,
        override_payload: dict[str, Any] | None = None,
        run_inline: bool = False,
    ) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise AppError("JOB_NOT_FOUND", f"未找到任务: {job_id}", status_code=404)
            payload = json.loads(row["payload_json"] or "{}")
            if override_payload:
                payload.update(override_payload)
            conn.execute(
                """
                UPDATE ai_jobs
                SET status = 'queued', progress = 0, error_message = NULL,
                    retry_count = retry_count + 1, payload_json = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), utc_now_iso(), job_id),
            )

        if run_inline:
            await self._execute_job(job_id)
        else:
            asyncio.create_task(self._execute_job(job_id))
        return self.get_job(job_id)

    async def _execute_job(self, job_id: str) -> None:
        async with self._get_semaphore():
            with self._lock, self._conn() as conn:
                row = conn.execute("SELECT * FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
                if not row:
                    return
                conn.execute(
                    "UPDATE ai_jobs SET status = 'running', progress = 25, updated_at = ? WHERE job_id = ?",
                    (utc_now_iso(), job_id),
                )
                record_dict = self._row_to_internal(row)

            handler = self._handlers[record_dict["job_type"]]
            started = time.perf_counter()

            try:
                result = await handler(record_dict)
                elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
                with self._lock, self._conn() as conn:
                    conn.execute(
                        """
                        UPDATE ai_jobs
                        SET status = 'succeeded', progress = 100, result_json = ?,
                            error_message = NULL, duration_ms = ?, updated_at = ?
                        WHERE job_id = ?
                        """,
                        (json.dumps(result, ensure_ascii=False), elapsed_ms, utc_now_iso(), job_id),
                    )
            except Exception as exc:
                elapsed_ms = max(1, int((time.perf_counter() - started) * 1000))
                logger.error("Async AI job %s failed: %s", job_id, exc)
                with self._lock, self._conn() as conn:
                    conn.execute(
                        """
                        UPDATE ai_jobs
                        SET status = 'failed', progress = 100, error_message = ?,
                            duration_ms = ?, updated_at = ?
                        WHERE job_id = ?
                        """,
                        (str(exc), elapsed_ms, utc_now_iso(), job_id),
                    )

            await self._send_webhook_if_configured(job_id)

    async def _send_webhook_if_configured(self, job_id: str) -> None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row or not row["callback_url"]:
            return
        body_dict = self._row_to_public(row)
        raw_bytes = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
        idem_key = f"cb_{row['job_id']}_r{row['retry_count']}_{row['status']}"

        for attempt, delay in enumerate((0.0, *_WEBHOOK_RETRY_DELAYS)):
            if delay > 0:
                await asyncio.sleep(delay)
            ts = str(int(time.time()))
            sig = sign_webhook_payload(ts, raw_bytes)
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=2.0)) as client:
                    resp = await client.post(
                        row["callback_url"],
                        content=raw_bytes,
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Timestamp": ts,
                            "X-Webhook-Signature": sig,
                            "Idempotency-Key": idem_key,
                            "X-Request-ID": row["request_id"],
                        },
                    )
                    if resp.status_code < 500:
                        return
                    logger.warning("Webhook attempt %d returned HTTP %s for job %s", attempt + 1, resp.status_code, job_id)
            except Exception as exc:
                logger.warning("Webhook attempt %d failed for job %s: %s", attempt + 1, job_id, exc)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM ai_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise AppError("JOB_NOT_FOUND", f"未找到任务: {job_id}", status_code=404)
        return self._row_to_public(row)

    def list_jobs(
        self,
        *,
        status: str | None = None,
        course_id: str | None = None,
        job_type: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        if job_type:
            clauses.append("job_type = ?")
            params.append(job_type)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._conn() as conn:
            rows = conn.execute(f"SELECT * FROM ai_jobs {where_sql} ORDER BY created_at DESC", tuple(params)).fetchall()

        start_dt = parse_iso_timestamp(start_time)
        end_dt = parse_iso_timestamp(end_time)
        results: list[dict[str, Any]] = []
        for r in rows:
            if start_dt or end_dt:
                created_dt = parse_iso_timestamp(r["created_at"])
                if created_dt and ((start_dt and created_dt < start_dt) or (end_dt and created_dt > end_dt)):
                    continue
            results.append(self._row_to_public(r))
        return results

    def count_jobs_by_status(self) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
        with self._conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS cnt FROM ai_jobs GROUP BY status").fetchall()
        for r in rows:
            counts[str(r["status"])] = int(r["cnt"])
        return counts

    @staticmethod
    def _row_to_internal(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "course_id": row["course_id"],
            "payload": json.loads(row["payload_json"] or "{}"),
            "request_id": row["request_id"],
        }

    @staticmethod
    def _row_to_public(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "course_id": row["course_id"],
            "status": row["status"],
            "progress": int(row["progress"]),
            "retry_count": int(row["retry_count"]),
            "payload": json.loads(row["payload_json"] or "{}"),
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
            "error_message": row["error_message"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


job_manager = AsyncJobManager()
