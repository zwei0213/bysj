from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS = {
    "",
    "dev-service-key-change-in-prod",
    "dev-ops-key-change-in-prod",
    "dev-webhook-hmac-secret-change-in-prod",
    "changeme",
}


class AiServiceSettings(BaseSettings):
    """
    AI 助教服务生产级配置中心。
    - `APP_ENV=production` 时强制校验跨服务鉴权密钥与 Webhook 签名密钥强度，杜绝弱口令上线；
    - 配置了云端大模型 Key 时自动启用「稠密向量 Embedding + BM25 混合检索」与「云端推理/ASR」；
    - 未配置外部 Key 时自动运行「本地 Faster-Whisper 离线语音识别 + 本地混合检索引擎」。
    """

    app_env: Literal["development", "staging", "production"] = "production"
    service_name: str = "smart-learning-ai-tutor-service"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8001

    # 跨服务双平面独立鉴权密钥 + Webhook HMAC-SHA256 签名密钥
    ai_service_key: str = "dev-service-key-change-in-prod"
    ai_ops_key: str = "dev-ops-key-change-in-prod"
    webhook_signing_secret: str = "dev-webhook-hmac-secret-change-in-prod"

    # 文本与向量模型提供方：local | openai（兼容 DeepSeek/通义千问/vLLM/OpenAI）| gemini
    ai_provider: str = "local"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_asr_model: str = "whisper-1"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "text-embedding-004"
    llm_timeout_seconds: float = 30.0

    # 可选 Qdrant HTTP 向量检索后端配置（未配置时默认使用 SQLite WAL 本地稠密特征哈希 + BM25 混合检索）
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection_prefix: str = "slp_kb"

    # 视频语音识别 (ASR) 策略：auto | openai | gemini | local_whisper
    asr_provider: str = "auto"
    whisper_model_size: str = "base"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    hf_endpoint: str = "https://hf-mirror.com"

    # 异步长任务最大并发 Worker 数（防止多路长视频同时转写耗尽 CPU/GPU 显存）
    max_concurrent_jobs: int = 2

    # AI 服务独占持久化目录（SQLite WAL 向量切片库、任务队列库、遥测库与模型缓存）
    data_dir: str = str(Path(__file__).resolve().parents[2] / ".ai_runtime_data")

    # V2 模块灰度开关初始默认值
    enable_v2_harness: bool = False
    enable_v2_personalization: bool = False
    enable_v2_rsi: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def validate_production_security(self) -> None:
        if self.app_env != "production":
            return
        weak_fields: list[str] = []
        for field_name, val in (
            ("AI_SERVICE_KEY", self.ai_service_key),
            ("AI_OPS_KEY", self.ai_ops_key),
            ("WEBHOOK_SIGNING_SECRET", self.webhook_signing_secret),
        ):
            if val in _INSECURE_DEFAULTS or len(val) < 24:
                weak_fields.append(field_name)
        if self.ai_service_key == self.ai_ops_key:
            weak_fields.append("AI_SERVICE_KEY != AI_OPS_KEY (业务密钥与运营密钥必须物理分离)")
        if weak_fields:
            raise RuntimeError(
                f"[SECURITY_FATAL] 生产环境 (APP_ENV=production) 检测到未配置或过弱的安全密钥: {', '.join(weak_fields)}。"
                "请先运行 `python scripts/init-prod-env.py` 生成高强度商业部署密钥！"
            )


settings = AiServiceSettings()
