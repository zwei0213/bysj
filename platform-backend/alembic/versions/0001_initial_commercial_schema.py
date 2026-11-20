"""Initial commercial platform schema and P1 incremental tables/columns.

Revision ID: 0001_initial_commercial_schema
Revises: None
Create Date: 2026-11-18 12:00:00
"""
from typing import Sequence, Union
from alembic import op

from app.core.database import Base
from app.core.migrations import run_incremental_migrations
import app.models.entities  # noqa: F401

revision: str = "0001_initial_commercial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    run_incremental_migrations(bind.engine)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
