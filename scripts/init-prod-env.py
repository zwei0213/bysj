"""
商业生产环境高强度密钥与 `.env` 初始化工具。

用途：
1. 服务器 Docker Compose 部署初始化：
   `python3 scripts/init-prod-env.py --domain https://learn.yourcompany.com`
   在仓库根目录生成 `.env`（含 256-bit 随机 JWT、双平面隔离 AI 密钥、Webhook HMAC 密钥、PostgreSQL 密码与初始超级管理员密码）。

2. 本地商业模式联调初始化（供进程直接启动）：
   `python scripts/init-prod-env.py --local`
   同时生成匹配一致的 `ai-tutor-service/.env` 与 `platform-backend/.env`，开启 `APP_ENV=production` 强校验。
"""
import argparse
from pathlib import Path
import secrets
import string

ROOT = Path(__file__).resolve().parents[1]


def _alnum_secret(length: int = 36) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _read_env_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        result[k.strip()] = v.strip()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化智能学习平台商业生产环境密钥 (.env)")
    parser.add_argument("--domain", default="http://localhost", help="前端商业访问域名，如 https://learn.example.com")
    parser.add_argument("--admin-user", default="admin", help="首次空库初始化的超级管理员用户名")
    parser.add_argument("--admin-email", default="admin@learning-platform.com", help="超级管理员联系邮箱")
    parser.add_argument("--local", action="store_true", help="同时生成本地直跑所需的子服务 .env 文件")
    parser.add_argument("--force", action="store_true", help="强制重新生成并覆盖已存在的 .env 文件")
    args = parser.parse_args()

    root_env = ROOT / ".env"
    ai_env = ROOT / "ai-tutor-service" / ".env"
    plat_env = ROOT / "platform-backend" / ".env"

    if not args.force:
        if args.local and ai_env.exists() and plat_env.exists():
            existing = _read_env_map(plat_env)
            print("[INFO] 检测到本地商业配置文件已存在（未覆盖）：")
            print(f"  - {ai_env}")
            print(f"  - {plat_env}")
            print(f"  - 初始超级管理员账号: {existing.get('BOOTSTRAP_ADMIN_USERNAME', 'admin')}")
            if existing.get("BOOTSTRAP_ADMIN_PASSWORD"):
                print(f"  - 初始超级管理员密码: {existing['BOOTSTRAP_ADMIN_PASSWORD']} （若已在后台修改请使用新密码）")
            return
        if not args.local and root_env.exists():
            existing = _read_env_map(root_env)
            print(f"[INFO] 根目录 {root_env} 已存在，为保护生产库凭证未覆盖（如需轮换请加 --force）。")
            print(f"  - 初始超级管理员账号: {existing.get('BOOTSTRAP_ADMIN_USERNAME', 'admin')}")
            return

    jwt_secret = f"jwt_{_alnum_secret(44)}"
    ai_service_key = f"aisvc_{_alnum_secret(40)}"
    ai_ops_key = f"aiops_{_alnum_secret(40)}"
    webhook_secret = f"whsec_{_alnum_secret(40)}"
    pg_password = _alnum_secret(32)
    admin_password = f"Adm!{_alnum_secret(14)}"

    cors_origins = args.domain
    if args.local:
        cors_origins = "http://localhost:5173,http://127.0.0.1:5173"

    root_content = f"""# 自动生成的商业生产环境配置（请妥善保管，严禁提交至公开代码库）
HTTP_PORT=80
CORS_ALLOWED_ORIGINS={cors_origins}

POSTGRES_DB=smart_learning
POSTGRES_USER=slp_user
POSTGRES_PASSWORD={pg_password}
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
UVICORN_WORKERS=2

JWT_SECRET={jwt_secret}
JWT_EXPIRE_HOURS=12
JWT_REFRESH_EXPIRE_DAYS=14
BOOTSTRAP_ADMIN_USERNAME={args.admin_user}
BOOTSTRAP_ADMIN_PASSWORD={admin_password}
BOOTSTRAP_ADMIN_EMAIL={args.admin_email}
MAX_VIDEO_UPLOAD_MB=2048
MAX_DOCUMENT_UPLOAD_MB=100

AI_SERVICE_KEY={ai_service_key}
AI_OPS_KEY={ai_ops_key}
WEBHOOK_SIGNING_SECRET={webhook_secret}

AI_PROVIDER=local
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_ASR_MODEL=whisper-1

GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=text-embedding-004

ASR_PROVIDER=auto
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
HF_ENDPOINT=https://hf-mirror.com
MAX_CONCURRENT_JOBS=2
"""
    root_env.write_text(root_content, encoding="utf-8")

    if args.local:
        ai_content = f"""APP_ENV=production
AI_SERVICE_KEY={ai_service_key}
AI_OPS_KEY={ai_ops_key}
WEBHOOK_SIGNING_SECRET={webhook_secret}

AI_PROVIDER=local
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_ASR_MODEL=whisper-1

GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
GEMINI_EMBEDDING_MODEL=text-embedding-004

ASR_PROVIDER=auto
WHISPER_MODEL_SIZE=base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
HF_ENDPOINT=https://hf-mirror.com
MAX_CONCURRENT_JOBS=2
"""
        plat_content = f"""APP_ENV=production
DATABASE_URL=sqlite:///./platform_data.db
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
PUBLIC_BACKEND_ORIGIN=http://127.0.0.1:8000

JWT_SECRET={jwt_secret}
JWT_EXPIRE_HOURS=12
JWT_REFRESH_EXPIRE_DAYS=14

AI_SERVICE_BASE_URL=http://127.0.0.1:8001
AI_SERVICE_KEY={ai_service_key}
AI_OPS_KEY={ai_ops_key}
WEBHOOK_SIGNING_SECRET={webhook_secret}
PLATFORM_CALLBACK_URL=http://127.0.0.1:8000/internal/v1/webhooks/ai-jobs

BOOTSTRAP_ADMIN_USERNAME={args.admin_user}
BOOTSTRAP_ADMIN_PASSWORD={admin_password}
BOOTSTRAP_ADMIN_EMAIL={args.admin_email}
MAX_VIDEO_UPLOAD_MB=2048
MAX_DOCUMENT_UPLOAD_MB=100

# 可选：在业务后端集中配置大模型与 API 密钥（非空项启动时自动同步下发至 AI 服务）
AI_PROVIDER=
OPENAI_BASE_URL=
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_EMBEDDING_MODEL=
GEMINI_API_KEY=
GEMINI_MODEL=
ASR_PROVIDER=
WHISPER_MODEL_SIZE=
"""
        ai_env.write_text(ai_content, encoding="utf-8")
        plat_env.write_text(plat_content, encoding="utf-8")

    print("=" * 72)
    print("[SUCCESS] 商业生产级高强度安全配置已生成！")
    print(f"  - 根环境文件: {root_env}")
    if args.local:
        print(f"  - AI 服务配置: {ai_env}")
        print(f"  - 业务后端配置: {plat_env}")
    print("-" * 72)
    print(f"  初始超级管理员用户名 : {args.admin_user}")
    print(f"  初始超级管理员密码   : {admin_password}")
    print("  安全提示：首次登录管理后台后，请立即点击右上角「修改密码」完成口令更新！")
    print("=" * 72)


if __name__ == "__main__":
    main()
