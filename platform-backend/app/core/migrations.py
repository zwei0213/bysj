"""
幂等数据库增量迁移工具 (Idempotent Schema Evolution)。
基于 SQLAlchemy `inspect(engine)` 自动探测既有表列结构，在 SQLite 与 PostgreSQL 上无损补齐新增字段。
"""
import logging
from sqlalchemy import Engine, inspect, text

logger = logging.getLogger(__name__)

# (table_name, column_name, sqlite_column_ddl, postgres_column_ddl)
_INCREMENTAL_COLUMNS: list[tuple[str, str, str, str]] = [
    # course_documents
    ("course_documents", "file_url", "VARCHAR(500) DEFAULT ''", "VARCHAR(500) DEFAULT ''"),
    # users
    ("users", "teacher_status", "VARCHAR(20) DEFAULT 'approved'", "VARCHAR(20) DEFAULT 'approved'"),
    ("users", "reject_reason", "VARCHAR(300) DEFAULT ''", "VARCHAR(300) DEFAULT ''"),
    ("users", "last_study_date", "VARCHAR(20) DEFAULT ''", "VARCHAR(20) DEFAULT ''"),
    # courses
    ("courses", "code", "VARCHAR(60) DEFAULT ''", "VARCHAR(60) DEFAULT ''"),
    ("courses", "cover_url", "VARCHAR(500) DEFAULT ''", "VARCHAR(500) DEFAULT ''"),
    ("courses", "reject_reason", "VARCHAR(300) DEFAULT ''", "VARCHAR(300) DEFAULT ''"),
    ("courses", "start_date", "VARCHAR(30) DEFAULT ''", "VARCHAR(30) DEFAULT ''"),
    ("courses", "end_date", "VARCHAR(30) DEFAULT ''", "VARCHAR(30) DEFAULT ''"),
    ("courses", "expires_at", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    # enrollments
    ("enrollments", "group_name", "VARCHAR(80) DEFAULT ''", "VARCHAR(80) DEFAULT ''"),
    # video_resources
    ("video_resources", "is_hidden", "BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE"),
    # exam_papers
    ("exam_papers", "target_class_id", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    ("exam_papers", "start_time", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    ("exam_papers", "end_time", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    # quiz_sessions
    ("quiz_sessions", "exam_id", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    # tutor_messages
    ("tutor_messages", "is_blocked", "BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE"),
    # quality_samples
    ("quality_samples", "message_id", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    ("quality_samples", "corrected_output", "TEXT DEFAULT ''", "TEXT DEFAULT ''"),
    # support_tickets
    ("support_tickets", "assignee_id", "VARCHAR(40) DEFAULT NULL", "VARCHAR(40) DEFAULT NULL"),
    ("support_tickets", "assignee_name", "VARCHAR(80) DEFAULT ''", "VARCHAR(80) DEFAULT ''"),
    ("support_tickets", "updated_at", "VARCHAR(40) DEFAULT ''", "VARCHAR(40) DEFAULT ''"),
]


def run_incremental_migrations(bind_engine: Engine) -> list[str]:
    """检查当前数据库表结构并幂等补齐缺失列，返回本次实际新增的 `table.column` 列表。"""
    applied: list[str] = []
    insp = inspect(bind_engine)
    existing_tables = set(insp.get_table_names())
    is_postgres = bind_engine.dialect.name == "postgresql"

    cols_cache: dict[str, set[str]] = {}
    statements: list[tuple[str, str]] = []

    for table_name, col_name, sqlite_ddl, pg_ddl in _INCREMENTAL_COLUMNS:
        if table_name not in existing_tables:
            continue
        if table_name not in cols_cache:
            cols_cache[table_name] = {col["name"] for col in insp.get_columns(table_name)}
        if col_name in cols_cache[table_name]:
            continue

        ddl = pg_ddl if is_postgres else sqlite_ddl
        statements.append((f"{table_name}.{col_name}", f"ALTER TABLE {table_name} ADD COLUMN {col_name} {ddl}"))
        cols_cache[table_name].add(col_name)

    if statements:
        with bind_engine.begin() as conn:
            for label, sql_str in statements:
                conn.execute(text(sql_str))
                applied.append(label)
                logger.info("Applied incremental column migration: %s", label)

    return applied
