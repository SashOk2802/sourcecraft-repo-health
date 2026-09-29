"""Ускоряет безопасное чтение истории анализов в личном каталоге."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260929_08"
down_revision = "20260928_07"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_analysis_jobs_owner_repository_latest"


def upgrade() -> None:
    """Добавляет partial-index только для заданий с владельцем сессии."""

    inspector = sa.inspect(op.get_bind())
    if "analysis_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("analysis_jobs")}
    required_columns = {
        "analysis_id",
        "repository_id",
        "owner_subject",
        "created_at",
    }
    if not required_columns <= columns:
        return
    indexes = {index["name"] for index in inspector.get_indexes("analysis_jobs")}
    if _INDEX_NAME in indexes:
        return
    op.execute(
        sa.text(
            """
            CREATE INDEX ix_analysis_jobs_owner_repository_latest
            ON analysis_jobs (owner_subject, repository_id, created_at DESC, analysis_id DESC)
            WHERE owner_subject IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    """Индекс не содержит данных, поэтому его можно безопасно удалить."""

    inspector = sa.inspect(op.get_bind())
    if _INDEX_NAME in {index["name"] for index in inspector.get_indexes("analysis_jobs")}:
        op.drop_index(_INDEX_NAME, table_name="analysis_jobs")
