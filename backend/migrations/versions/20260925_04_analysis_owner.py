"""Сохраняет subject сессии Яндекс ID как владельца задания анализа."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_04"
down_revision = "20260925_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет владельца. Старые строки остаются без него и не читаются."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    job_columns = {column["name"] for column in inspector.get_columns("analysis_jobs")}
    if "owner_subject" not in job_columns:
        op.add_column(
            "analysis_jobs",
            sa.Column("owner_subject", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    """Не удаляет subject: downgrade не должен открывать чужие отчёты заново."""
