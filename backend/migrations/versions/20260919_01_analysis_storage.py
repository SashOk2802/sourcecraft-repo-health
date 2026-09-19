"""Создаёт постоянное хранилище снимков и заданий анализа."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260919_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт таблицы, если их не создал ранний MVP-выпуск приложения."""

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "analysis_snapshots" not in existing_tables:
        op.create_table(
            "analysis_snapshots",
            sa.Column("analysis_id", sa.Text(), primary_key=True),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column("report", postgresql.JSONB(), nullable=False),
            sa.Column("markdown", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    if "analysis_jobs" not in existing_tables:
        op.create_table(
            "analysis_jobs",
            sa.Column("analysis_id", sa.Text(), primary_key=True),
            sa.Column("repository_id", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_code", sa.Text(), nullable=True),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "status IN ('queued', 'running', 'completed', 'partial', 'failed')",
                name="ck_analysis_jobs_status",
            ),
        )


def downgrade() -> None:
    """Удаляет таблицы постоянных анализов при откате до пустой схемы."""

    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "analysis_jobs" in existing_tables:
        op.drop_table("analysis_jobs")
    if "analysis_snapshots" in existing_tables:
        op.drop_table("analysis_snapshots")
