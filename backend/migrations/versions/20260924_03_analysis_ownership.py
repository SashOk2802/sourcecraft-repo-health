"""Добавляет владельца анализа (owner_subject) в задания и снимки."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260924_03"
down_revision = "20260921_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Сохраняет инициатора анализа для проверки прав на чтение.

    Новые записи всегда содержат owner_subject. Исторические строки до этой
    версии не сохраняли владельца и получают значение 'legacy': такой анализ
    не должен читаться по правилам нового владельца (fail-closed), пока его
    владелец не будет установлен явно.
    """

    for table_name in ("analysis_jobs", "analysis_snapshots"):
        inspector = sa.inspect(op.get_bind())
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "owner_subject" in columns:
            continue
        op.add_column(
            table_name,
            sa.Column(
                "owner_subject",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'legacy'"),
            ),
        )
        # Дальше значение обязан указывать прикладной код: INSERT без
        # owner_subject завершится ошибкой базы, а не создаст строку без владельца.
        op.alter_column(table_name, "owner_subject", server_default=None)


def downgrade() -> None:
    """Убирает столбцы владельца; при откате привязка к инициатору теряется."""

    for table_name in ("analysis_jobs", "analysis_snapshots"):
        inspector = sa.inspect(op.get_bind())
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "owner_subject" in columns:
            op.drop_column(table_name, "owner_subject")