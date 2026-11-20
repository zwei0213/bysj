"""admin 服务兼容门面：已按业务域拆分至 `app/services/admin/` 子模块，保持既有导入路径兼容。"""
from app.services.admin import *  # noqa: F401,F403
