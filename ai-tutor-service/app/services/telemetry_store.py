"""
AI 运营遥测、问题样本池、角色滑动时间窗限流与运行时配置存储：
- 统一使用 `closing()` 管理 SQLite WAL 连接，杜绝连接泄漏；
- `summarize_usage` 改由 SQL 侧 `WHERE + GROUP BY` 聚合，避免全表拉入内存；
- 持久化的大模型 API Key 使用 `cryptography.Fernet` 加密落盘，不再明文存储。
"""
import base64
from collections import deque
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any
import uuid
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings
from app.core.errors import AppError
from app.core.schemas import utc_now_iso

logger = logging.getLogger(__name__)

_GATEWAY_FIELDS = (
    "openai_base_url",
    "openai_api_key",
    "openai_model",
    "openai_embedding_model",
    "openai_asr_model",
    "gemini_api_key",
    "gemini_model",
    "gemini_embedding_model",
    "asr_provider",
    "whisper_model_size",
)


def parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _mask_secret(secret: str | None) -> str:
    val = (secret or "").strip()
    if not val:
        return ""
    if len(val) <= 8:
        return "****"
    return f"{val[:3]}****{val[-4:]}"


def _estimate_cloud_cost_usd(provider: str, total_tokens: int) -> float:
    prov = (provider or "").lower().strip()
    if prov.startswith("local"):
        return 0.0
    if any(cloud in prov for cloud in ("openai", "gemini", "anthropic", "deepseek", "qwen", "cloud")):
        return round(total_tokens * 0.0000006, 6)
    return 0.0


def _build_fernet() -> Fernet:
    raw = hashlib.sha256(f"slp_ai_kv_enc:{settings.ai_ops_key}".encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def _encrypt_json(obj: dict[str, Any]) -> str:
    token = _build_fernet().encrypt(json.dumps(obj, ensure_ascii=False).encode("utf-8")).decode("ascii")
    return f"enc:v1:{token}"


def _decrypt_json(raw_value: str) -> dict[str, Any]:
    if raw_value.startswith("enc:v1:"):
        try:
            plain = _build_fernet().decrypt(raw_value[7:].encode("ascii")).decode("utf-8")
            return json.loads(plain)
        except (InvalidToken, ValueError):
            logger.warning("Failed to decrypt persisted provider_gateway_secrets (ops key may have changed)")
            return {}
    # 向后兼容存量明文配置
    try:
        return json.loads(raw_value)
    except ValueError:
        return {}


class TelemetryAndConfigStore:
    def __init__(self, storage_dir: str | None = None) -> None:
        self._override_dir = storage_dir
        self._lock = threading.RLock()
        self._initialized_path: str | None = None
        self._rate_windows: dict[str, deque[float]] = {}
        self._rate_scope_path: str | None = None

    def _default_runtime_config(self) -> dict[str, Any]:
        return {
            "active_provider": settings.ai_provider,
            "confidence_threshold": 0.28,
            "feature_flags": {
                "enable_v2_harness": settings.enable_v2_harness,
                "enable_v2_personalization": settings.enable_v2_personalization,
                "enable_v2_rsi": settings.enable_v2_rsi,
            },
            "safety_filter": {
                "input_filter_enabled": True,
                "output_filter_enabled": True,
                "blocked_keywords": ["代考", "作弊器", "破解题库", "违法违规"],
            },
            "rate_limit_per_minute": {
                "student": 30,
                "teacher": 60,
                "admin": 120,
            },
            "updated_at": utc_now_iso(),
        }

    def _db_path(self) -> Path:
        base = Path(self._override_dir or settings.data_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base / "ops_telemetry.db"

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
                        CREATE TABLE IF NOT EXISTS usage_events (
                            event_id TEXT PRIMARY KEY,
                            capability TEXT NOT NULL,
                            course_id TEXT NOT NULL,
                            latency_ms INTEGER NOT NULL,
                            prompt_tokens INTEGER NOT NULL,
                            completion_tokens INTEGER NOT NULL,
                            total_tokens INTEGER NOT NULL,
                            estimated_cost_usd REAL NOT NULL,
                            provider TEXT NOT NULL,
                            degraded INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_usage_course ON usage_events(course_id);
                        CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at);
                        CREATE TABLE IF NOT EXISTS feedback_samples (
                            sample_id TEXT PRIMARY KEY,
                            course_id TEXT NOT NULL,
                            sample_type TEXT NOT NULL,
                            source_reason TEXT NOT NULL,
                            input_text TEXT NOT NULL,
                            model_output TEXT NOT NULL,
                            expected_correction TEXT,
                            tags_json TEXT NOT NULL,
                            status TEXT NOT NULL,
                            created_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS runtime_kv (
                            key TEXT PRIMARY KEY,
                            value_json TEXT NOT NULL
                        );
                        """
                    )
                    if not conn.execute("SELECT 1 FROM runtime_kv WHERE key = 'runtime_config'").fetchone():
                        conn.execute(
                            "INSERT INTO runtime_kv (key, value_json) VALUES ('runtime_config', ?)",
                            (json.dumps(self._default_runtime_config(), ensure_ascii=False),),
                        )
                    conn.commit()
                    self._initialized_path = str(db_file)
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def record_usage(
        self,
        *,
        capability: str,
        course_id: str,
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        provider: str,
        degraded: bool = False,
    ) -> None:
        total_tokens = prompt_tokens + completion_tokens
        est_cost_usd = _estimate_cloud_cost_usd(provider, total_tokens)
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO usage_events (
                    event_id, capability, course_id, latency_ms, prompt_tokens,
                    completion_tokens, total_tokens, estimated_cost_usd, provider, degraded, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"evt_{uuid.uuid4().hex[:12]}",
                    capability,
                    course_id,
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    est_cost_usd,
                    provider,
                    1 if degraded else 0,
                    utc_now_iso(),
                ),
            )

    def summarize_usage(
        self,
        course_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> dict[str, Any]:
        """在 SQL 层完成时间过滤与分组聚合，避免全量拉取 usage_events 进内存。"""
        clauses: list[str] = []
        params: list[Any] = []
        if course_id:
            clauses.append("course_id = ?")
            params.append(course_id)
        start_dt = parse_iso_timestamp(start_time)
        end_dt = parse_iso_timestamp(end_time)
        if start_dt:
            clauses.append("created_at >= ?")
            params.append(start_dt.isoformat())
        if end_dt:
            clauses.append("created_at <= ?")
            params.append(end_dt.isoformat())
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._conn() as conn:
            agg = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_calls,
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0.0) AS total_cost,
                    COALESCE(AVG(latency_ms), 0.0) AS avg_latency_ms,
                    COALESCE(SUM(degraded), 0) AS degraded_calls
                FROM usage_events {where_sql}
                """,
                tuple(params),
            ).fetchone()
            cap_rows = conn.execute(
                f"""
                SELECT
                    capability,
                    COUNT(*) AS call_count,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(AVG(latency_ms), 0.0) AS avg_latency_ms,
                    COALESCE(SUM(estimated_cost_usd), 0.0) AS estimated_cost_usd
                FROM usage_events {where_sql}
                GROUP BY capability
                ORDER BY call_count DESC
                """,
                tuple(params),
            ).fetchall()

        return {
            "total_calls": int(agg["total_calls"]),
            "prompt_tokens": int(agg["prompt_tokens"]),
            "completion_tokens": int(agg["completion_tokens"]),
            "total_tokens": int(agg["total_tokens"]),
            "estimated_total_cost_usd": round(float(agg["total_cost"]), 6),
            "avg_latency_ms": round(float(agg["avg_latency_ms"]), 1),
            "degraded_calls": int(agg["degraded_calls"]),
            "by_capability": [
                {
                    "capability": r["capability"],
                    "call_count": int(r["call_count"]),
                    "total_tokens": int(r["total_tokens"]),
                    "avg_latency_ms": round(float(r["avg_latency_ms"]), 1),
                    "estimated_cost_usd": round(float(r["estimated_cost_usd"]), 6),
                }
                for r in cap_rows
            ],
        }

    def add_feedback_sample(self, payload: dict[str, Any]) -> dict[str, Any]:
        sample = {
            "sample_id": payload.get("sample_id") or f"smp_{uuid.uuid4().hex[:10]}",
            "course_id": payload.get("course_id", ""),
            "sample_type": payload.get("sample_type", "tutor_reply"),
            "source_reason": payload.get("source_reason", "student_downvote"),
            "input_text": payload.get("input_text", ""),
            "model_output": payload.get("model_output", ""),
            "expected_correction": payload.get("expected_correction"),
            "tags": payload.get("tags", []),
            "status": "queued_for_harness_eval",
            "created_at": utc_now_iso(),
        }
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO feedback_samples (
                    sample_id, course_id, sample_type, source_reason, input_text,
                    model_output, expected_correction, tags_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sample["sample_id"],
                    sample["course_id"],
                    sample["sample_type"],
                    sample["source_reason"],
                    sample["input_text"],
                    sample["model_output"],
                    sample["expected_correction"],
                    json.dumps(sample["tags"], ensure_ascii=False),
                    sample["status"],
                    sample["created_at"],
                ),
            )
        return sample

    def list_feedback_samples(self, course_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            if course_id:
                rows = conn.execute(
                    "SELECT * FROM feedback_samples WHERE course_id = ? ORDER BY created_at DESC",
                    (course_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM feedback_samples ORDER BY created_at DESC").fetchall()
        return [
            {
                "sample_id": r["sample_id"],
                "course_id": r["course_id"],
                "sample_type": r["sample_type"],
                "source_reason": r["source_reason"],
                "input_text": r["input_text"],
                "model_output": r["model_output"],
                "expected_correction": r["expected_correction"],
                "tags": json.loads(r["tags_json"] or "[]"),
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def _build_masked_model_gateway(self) -> dict[str, Any]:
        return {
            "openai_base_url": settings.openai_base_url,
            "openai_model": settings.openai_model,
            "openai_embedding_model": settings.openai_embedding_model,
            "openai_asr_model": settings.openai_asr_model,
            "has_openai_api_key": bool((settings.openai_api_key or "").strip()),
            "openai_api_key_masked": _mask_secret(settings.openai_api_key),
            "gemini_model": settings.gemini_model,
            "gemini_embedding_model": settings.gemini_embedding_model,
            "has_gemini_api_key": bool((settings.gemini_api_key or "").strip()),
            "gemini_api_key_masked": _mask_secret(settings.gemini_api_key),
            "asr_provider": settings.asr_provider,
            "whisper_model_size": settings.whisper_model_size,
        }

    def restore_persisted_provider_settings(self) -> None:
        with self._lock, self._conn() as conn:
            cfg_row = conn.execute("SELECT value_json FROM runtime_kv WHERE key = 'runtime_config'").fetchone()
            gw_row = conn.execute("SELECT value_json FROM runtime_kv WHERE key = 'provider_gateway_secrets'").fetchone()
        if cfg_row:
            cfg = json.loads(cfg_row["value_json"])
            if cfg.get("active_provider") in ("local", "openai", "gemini"):
                settings.ai_provider = cfg["active_provider"]
        if gw_row:
            gw = _decrypt_json(gw_row["value_json"])
            for field in _GATEWAY_FIELDS:
                if field in gw and gw[field] is not None:
                    setattr(settings, field, str(gw[field]))

    def get_runtime_config(self) -> dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute("SELECT value_json FROM runtime_kv WHERE key = 'runtime_config'").fetchone()
        cfg = json.loads(row["value_json"]) if row else self._default_runtime_config()
        cfg.setdefault("active_provider", settings.ai_provider)
        cfg.setdefault("rate_limit_per_minute", {"student": 30, "teacher": 60, "admin": 120})
        cfg["model_gateway"] = self._build_masked_model_gateway()
        return cfg

    def patch_runtime_config(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT value_json FROM runtime_kv WHERE key = 'runtime_config'").fetchone()
            gw_row = conn.execute("SELECT value_json FROM runtime_kv WHERE key = 'provider_gateway_secrets'").fetchone()
            cfg = json.loads(row["value_json"]) if row else self._default_runtime_config()
            gw_persisted: dict[str, str] = _decrypt_json(gw_row["value_json"]) if gw_row else {}

            if "active_provider" in patch and patch["active_provider"] in ("local", "openai", "gemini"):
                cfg["active_provider"] = patch["active_provider"]
                settings.ai_provider = patch["active_provider"]

            gw_in: dict[str, Any] = {}
            if isinstance(patch.get("model_gateway"), dict):
                gw_in.update(patch["model_gateway"])
            for k in _GATEWAY_FIELDS:
                if k in patch and patch[k] is not None:
                    gw_in[k] = patch[k]

            for k, raw_v in gw_in.items():
                if raw_v is None:
                    continue
                v = str(raw_v).strip()
                if k in ("openai_api_key", "gemini_api_key"):
                    if v == "CLEAR":
                        setattr(settings, k, "")
                        gw_persisted[k] = ""
                    elif v and "****" not in v:
                        setattr(settings, k, v)
                        gw_persisted[k] = v
                else:
                    setattr(settings, k, v)
                    gw_persisted[k] = v

            if gw_in:
                conn.execute(
                    "INSERT OR REPLACE INTO runtime_kv (key, value_json) VALUES ('provider_gateway_secrets', ?)",
                    (_encrypt_json(gw_persisted),),
                )

            if "confidence_threshold" in patch and patch["confidence_threshold"] is not None:
                cfg["confidence_threshold"] = float(patch["confidence_threshold"])
            if "feature_flags" in patch and isinstance(patch["feature_flags"], dict):
                cfg.setdefault("feature_flags", {}).update(patch["feature_flags"])
            if "safety_filter" in patch and isinstance(patch["safety_filter"], dict):
                cfg.setdefault("safety_filter", {}).update(patch["safety_filter"])
            if "rate_limit_per_minute" in patch and isinstance(patch["rate_limit_per_minute"], dict):
                cfg.setdefault("rate_limit_per_minute", {}).update(
                    {str(k): int(v) for k, v in patch["rate_limit_per_minute"].items()}
                )
            cfg["updated_at"] = utc_now_iso()
            conn.execute(
                "INSERT OR REPLACE INTO runtime_kv (key, value_json) VALUES ('runtime_config', ?)",
                (json.dumps(cfg, ensure_ascii=False),),
            )
        cfg["model_gateway"] = self._build_masked_model_gateway()
        return cfg

    def check_safety_violation(self, text: str, *, direction: str = "input") -> str | None:
        if not text:
            return None
        sf = self.get_runtime_config().get("safety_filter", {})
        enabled = sf.get("input_filter_enabled", True) if direction == "input" else sf.get("output_filter_enabled", True)
        if not enabled:
            return None
        lowered = text.lower()
        for kw in sf.get("blocked_keywords", []):
            if kw and str(kw).lower() in lowered:
                return str(kw)
        return None

    def sanitize_output_text(self, text: str) -> tuple[str, list[str]]:
        if not text:
            return text, []
        sf = self.get_runtime_config().get("safety_filter", {})
        if not sf.get("output_filter_enabled", True):
            return text, []

        sanitized = text
        matched: list[str] = []
        for raw_kw in sf.get("blocked_keywords", []):
            kw = str(raw_kw or "").strip()
            if not kw:
                continue
            pattern = re.compile(re.escape(kw), flags=re.IGNORECASE)
            if pattern.search(sanitized):
                matched.append(kw)
                sanitized = pattern.sub("***", sanitized)
        return sanitized, matched

    def check_rate_limit(self, caller_role: str | None, caller_id: str | None) -> bool:
        cid = (caller_id or "").strip()
        if not cid:
            return True

        role = (caller_role or "student").lower().strip() or "student"
        limits = self.get_runtime_config().get("rate_limit_per_minute") or {}
        quota = int(limits.get(role, limits.get("student", 30)))

        db_key = str(self._db_path())
        now_mono = time.monotonic()

        with self._lock:
            if self._rate_scope_path != db_key:
                self._rate_windows.clear()
                self._rate_scope_path = db_key

            win = self._rate_windows.setdefault(f"{role}:{cid}", deque())
            cutoff = now_mono - 60.0
            while win and win[0] <= cutoff:
                win.popleft()

            if quota <= 0 or len(win) >= quota:
                raise AppError("RATE_LIMIT_EXCEEDED", "已超出当前角色每分钟 AI 调用配额", status_code=429)

            win.append(now_mono)
            return True


telemetry_store = TelemetryAndConfigStore()
