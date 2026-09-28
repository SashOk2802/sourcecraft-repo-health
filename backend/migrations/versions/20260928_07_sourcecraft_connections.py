"""Хранит зашифрованные персональные подключения SourceCraft."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_07"
down_revision = "20260927_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создаёт отдельную таблицу ciphertext без plaintext токенов."""

    inspector = sa.inspect(op.get_bind())
    if "sourcecraft_connections" in inspector.get_table_names():
        return

    op.create_table(
        "sourcecraft_connections",
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("encrypted_token", sa.LargeBinary(), nullable=False),
        sa.Column("sourcecraft_login", sa.Text(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "octet_length(encrypted_token) > 0",
            name="sourcecraft_connections_encrypted_token_non_empty",
        ),
    )


def downgrade() -> None:
    """Не удаляет пользовательские подключения при rollback приложения."""
