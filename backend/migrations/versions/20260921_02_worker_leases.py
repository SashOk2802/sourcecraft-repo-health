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

    existing_tables = set(inspector.get_table_names())
    if "analysis_worker_leases" not in existing_tables:
        op.create_table(
            "analysis_worker_leases",
            sa.Column("worker_id", sa.Text(), primary_key=True),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "analysis_job_recovery_state" not in existing_tables:
        op.create_table(
            "analysis_job_recovery_state",
            sa.Column("id", sa.SmallInteger(), primary_key=True),
            sa.Column(
                "ownerless_recovery_after",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.CheckConstraint("id = 1", name="ck_analysis_job_recovery_state_id"),
        )

    op.execute(
        sa.text(
            """
            INSERT INTO analysis_job_recovery_state (id, ownerless_recovery_after)
            VALUES (1, CURRENT_TIMESTAMP + INTERVAL '5 minutes')
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    """Не удаляет lease и recovery-state, чтобы downgrade не потерял состояние."""
