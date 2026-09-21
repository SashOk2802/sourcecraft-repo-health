"""Добавляет lease владельца задания анализа."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260921_02"
down_revision = "20260919_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет необязательного владельца задания и heartbeat его worker."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    job_columns = {
        column["name"]
        for column in inspector.get_columns("analysis_jobs")
    }
    if "worker_id" not in job_columns:
        op.add_column(
            "analysis_jobs",
            sa.Column("worker_id", sa.Text(), nullable=True),
        )

    if "analysis_worker_leases" not in set(inspector.get_table_names()):
        op.create_table(
            "analysis_worker_leases",
            sa.Column("worker_id", sa.Text(), primary_key=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    """Не удаляет lease и worker_id, чтобы downgrade не потерял состояние заданий."""
