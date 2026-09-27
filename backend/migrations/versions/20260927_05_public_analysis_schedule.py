"""Хранит расписание периодического public-анализа."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_05"
down_revision = "20260925_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт отдельную таблицу расписания, не меняя исторические отчёты."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "analysis_schedules" in inspector.get_table_names():
        return

    op.create_table(
        "analysis_schedules",
        sa.Column("repository_id", sa.Text(), primary_key=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_analysis_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("in_flight_analysis_id", sa.Text(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="analysis_schedules_failures_non_negative",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="analysis_schedules_complete_lease",
        ),
    )
    op.create_index(
        "ix_analysis_schedules_due",
        "analysis_schedules",
        ["next_analysis_at", "repository_id"],
        unique=False,
        postgresql_where=sa.text(
            "active = TRUE AND blocked = FALSE AND in_flight_analysis_id IS NULL"
        ),
    )


def downgrade() -> None:
    """Расписание не удаляется: downgrade не должен терять очередь пересчёта."""
