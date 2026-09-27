"""Добавляет блокировку постоянных ошибок public-расписания."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_06"
down_revision = "20260927_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Дополняет ранее применённую ревизию без изменения её истории."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "analysis_schedules" not in inspector.get_table_names():
        return

    column_names = {
        column["name"] for column in inspector.get_columns("analysis_schedules")
    }
    if "blocked" not in column_names:
        op.add_column(
            "analysis_schedules",
            sa.Column(
                "blocked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    index_names = {
        index["name"] for index in inspector.get_indexes("analysis_schedules")
    }
    if "ix_analysis_schedules_due" in index_names:
        op.drop_index("ix_analysis_schedules_due", table_name="analysis_schedules")
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
    """Не удаляет состояние очереди и не делает rollback разрушительным."""
