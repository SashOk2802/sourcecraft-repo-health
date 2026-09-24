from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import asyncpg


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class LegacyMigrationSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_name = f"legacy_{uuid4().hex}"
        self.admin_url = _to_asyncpg_url(os.environ["DATABASE_URL"])
        self.target_url = _with_database_name(
            os.environ["DATABASE_URL"],
            self.database_name,
        )
        self._admin = await asyncpg.connect(self.admin_url)
        await self._admin.execute(f'CREATE DATABASE "{self.database_name}"')

        connection = await asyncpg.connect(_to_asyncpg_url(self.target_url))
        try:
            await connection.execute(
                "CREATE TABLE analysis_snapshots (legacy_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            await connection.execute(
                "CREATE TABLE analysis_jobs (legacy_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            await connection.execute(
                "INSERT INTO analysis_snapshots (legacy_id, payload) VALUES ('report-1', 'legacy report')"
            )
            await connection.execute(
                "INSERT INTO analysis_jobs (legacy_id, payload) VALUES ('job-1', 'legacy job')"
            )
        finally:
            await connection.close()

    async def asyncTearDown(self) -> None:
        await self._admin.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            self.database_name,
        )
        await self._admin.execute(f'DROP DATABASE IF EXISTS "{self.database_name}"')
        await self._admin.close()

    async def test_downgrade_does_not_remove_baselined_legacy_tables_or_rows(self) -> None:
        self._run_alembic("upgrade", "head")
        self._run_alembic("downgrade", "base")

        connection = await asyncpg.connect(_to_asyncpg_url(self.target_url))
        try:
            snapshot = await connection.fetchval(
                "SELECT payload FROM analysis_snapshots WHERE legacy_id = 'report-1'"
            )
            job = await connection.fetchval(
                "SELECT payload FROM analysis_jobs WHERE legacy_id = 'job-1'"
            )
        finally:
            await connection.close()

        self.assertEqual(snapshot, "legacy report")
        self.assertEqual(job, "legacy job")

    async def test_downgrade_preserves_local_identity_tables(self) -> None:
        self._run_alembic("upgrade", "head")
        self._run_alembic("downgrade", "base")

        connection = await asyncpg.connect(_to_asyncpg_url(self.target_url))
        try:
            existing_tables = {
                row["table_name"]
                for row in await connection.fetch(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    """
                )
            }
        finally:
            await connection.close()

        self.assertTrue(
            {"app_users", "app_sessions", "yandex_login_attempts"} <= existing_tables,
        )

    def _run_alembic(self, *arguments: str) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=Path(__file__).resolve().parents[2],
            env={**os.environ, "DATABASE_URL": self.target_url},
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            self.fail(
                "Alembic command failed:\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )


def _to_asyncpg_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _with_database_name(database_url: str, database_name: str) -> str:
    parsed = urlsplit(database_url)
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{database_name}",
            parsed.query,
            parsed.fragment,
        )
    )
