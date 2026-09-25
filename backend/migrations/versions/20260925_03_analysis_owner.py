"""Сохраняет несекретный subject владельца задания анализа."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_03"
down_revision = "20260921_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет отпечаток инициатора. Старые строки остаются без владельца."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "analysis_jobs" not in inspector.get_table_names():
        return

    job_columns = {column["name"] for column in inspector.get_columns("analysis_jobs")}
    if "owner_subject" not in job_columns:
        op.add_column(
            "analysis_jobs",
            sa.Column("owner_subject", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Не удаляет subject: downgrade не должен открывать чужие отчёты заново."""
