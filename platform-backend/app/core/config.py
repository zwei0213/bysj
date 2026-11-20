from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

_INSECURE_DEFAULTS = {
    "",
    "dev-platform-jwt-secret-change-in-prod",
    "dev-service-key-change-in-prod",
    "dev-ops-key-change-in-prod",
    "dev-webhook-hmac-secret-change-in-prod",
    "changeme",
}


class PlatformSettings(BaseSettings):
    """
    智能学习平台业务后端商业生产配置中心。
    - 空库启动仅初始化根租户组织与唯一超级管理员，绝不写入任何虚拟课程或测试账号；
    - `app_env="production"` 时启动阶段强制校验 JWT / 跨服务密钥 / CORS 与初始管理员凭证。
    """

    app_env: Literal["development", "staging", "production"] = "production"
    service_name: str = "smart-learning-platform-backend"
    version: str = "1.0.0"
    host: str = "0.0.0.0"
    port: int = 8000

    # 生产推荐 PostgreSQL: postgresql+psycopg://user:pass@postgres:5432/smart_learning
    database_url: str = f"sqlite:///{(Path(__file__).resolve().parents[2] / 'platform_data.db').as_posix()}"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # CORS 允许的前端商业域名列表（逗号分隔，生产环境严禁通配符 *）
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 对象存储目录与上传配额上限
    storage_dir: str = str(Path(__file__).resolve().parents[2] / ".storage")
    public_backend_origin: str = "http://127.0.0.1:8000"
    max_video_upload_mb: int = 2048
    max_document_upload_mb: int = 100

    # JWT 访问令牌与刷新令牌配置
    jwt_secret: str = "dev-platform-jwt-secret-change-in-prod"
    jwt_expire_hours: int = 12
    jwt_refresh_expire_days: int = 14

    # 与 AI 助教服务通信的唯一契约密钥
    ai_service_base_url: str = "http://127.0.0.1:8001"
    ai_service_key: str = "dev-service-key-change-in-prod"
    ai_ops_key: str = "dev-ops-key-change-in-prod"
    webhook_signing_secret: str = "dev-webhook-hmac-secret-change-in-prod"
    webhook_tolerance_seconds: int = 300
    platform_callback_url: str = "http://127.0.0.1:8000/internal/v1/webhooks/ai-jobs"
    ai_http_timeout_seconds: float = 30.0

    # 可选：在业务后端集中配置大模型与接口密钥（非空项将在启动时经 /v1/ops/runtime-config 自动下发至 AI 服务）
    ai_provider: str = ""  # 可选 local | openai | gemini
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    openai_embedding_model: str = ""
    openai_asr_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""
    gemini_embedding_model: str = ""
    asr_provider: str = ""
    whisper_model_size: str = ""

    # 首次空库初始化的系统超级管理员凭证（仅当 users 表为空时执行一次初始化）
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = ""
    bootstrap_admin_display_name: str = "系统管理员"
    bootstrap_admin_email: str = "admin@learning-platform.com"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def parsed_cors_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]
        return origins or ["http://localhost:5173"]

    def validate_production_security(self) -> None:
        if self.app_env != "production":
            return
        issues: list[str] = []
        for name, val in (
            ("JWT_SECRET", self.jwt_secret),
            ("AI_SERVICE_KEY", self.ai_service_key),
            ("AI_OPS_KEY", self.ai_ops_key),
            ("WEBHOOK_SIGNING_SECRET", self.webhook_signing_secret),
        ):
            if val in _INSECURE_DEFAULTS or len(val) < 24:
                issues.append(name)
        if self.ai_service_key == self.ai_ops_key:
            issues.append("AI_SERVICE_KEY != AI_OPS_KEY (业务密钥与运营密钥必须物理分离)")
        if "*" in self.parsed_cors_origins():
            issues.append("CORS_ALLOWED_ORIGINS (生产环境禁止配置通配符 '*')")
        if issues:
            raise RuntimeError(
                f"[SECURITY_FATAL] 商业生产环境配置校验未通过: {', '.join(issues)}。"
                "请先执行 `python scripts/init-prod-env.py` 初始化生产级 `.env` 配置文件！"
            )


settings = PlatformSettings()
