"""Создаёт серверные сессии и временные PKCE state для Яндекс ID."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260925_03"
down_revision = "20260921_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет только данные локальной идентичности, без OAuth-токенов Яндекса."""

    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())
    existing_indexes = {
        table_name: {index["name"] for index in inspector.get_indexes(table_name)}
        for table_name in existing_tables
    }

    if "app_users" not in existing_tables:
        op.create_table(
            "app_users",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("yandex_subject", sa.Text(), nullable=False, unique=True),
            sa.Column("login", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "yandex_login_attempts" not in existing_tables:
        op.create_table(
            "yandex_login_attempts",
            sa.Column("state_digest", sa.Text(), primary_key=True),
            sa.Column("code_verifier", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        existing_indexes["yandex_login_attempts"] = set()
    if "ix_yandex_login_attempts_expires_at" not in existing_indexes.get(
        "yandex_login_attempts",
        set(),
    ):
        op.create_index(
            "ix_yandex_login_attempts_expires_at",
            "yandex_login_attempts",
            ["expires_at"],
        )

    if "app_sessions" not in existing_tables:
        op.create_table(
            "app_sessions",
            sa.Column("session_digest", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("app_users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        existing_indexes["app_sessions"] = set()
    if "ix_app_sessions_user_id" not in existing_indexes.get("app_sessions", set()):
        op.create_index("ix_app_sessions_user_id", "app_sessions", ["user_id"])
    if "ix_app_sessions_expires_at" not in existing_indexes.get("app_sessions", set()):
        op.create_index("ix_app_sessions_expires_at", "app_sessions", ["expires_at"])


def downgrade() -> None:
    """Сохраняет идентичности и сессии, чтобы откат не удалил пользовательские данные.

    Миграция намеренно необратима: запись о пользователе уже могла использоваться
    для аудита запусков анализа, а upgrade допускает заранее существующие таблицы.
    """
