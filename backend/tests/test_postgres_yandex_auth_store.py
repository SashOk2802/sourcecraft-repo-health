from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.identity import PostgresYandexAuthStore
from backend.app.identity.yandex import LoginAttempt


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class PostgresYandexAuthStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = PostgresYandexAuthStore(os.environ["DATABASE_URL"])
        self.subject = f"yandex-{uuid4().hex}"
        self.attempt_digest = f"attempt-{uuid4().hex}"
        self.session_digest = f"session-{uuid4().hex}"
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)
        await self.store.start()

    async def asyncTearDown(self) -> None:
        pool = self.store._require_pool()
        await pool.execute(
            "DELETE FROM yandex_login_attempts WHERE state_digest = $1",
            self.attempt_digest,
        )
        await pool.execute(
            "DELETE FROM app_users WHERE yandex_subject = $1",
            self.subject,
        )
        await self.store.close()

    async def test_consumes_attempt_once_and_revokes_opaque_session(self) -> None:
        attempt = LoginAttempt(
            state_digest=self.attempt_digest,
            code_verifier="verifier-42",
            expires_at=self.now + timedelta(minutes=10),
        )
        await self.store.create_login_attempt(attempt)

        consumed = await self.store.consume_login_attempt(self.attempt_digest, self.now)
        repeated = await self.store.consume_login_attempt(self.attempt_digest, self.now)
        user = await self.store.upsert_user(self.subject, "alex", self.now)
        await self.store.create_session(
            self.session_digest,
            user.id,
            self.now + timedelta(days=14),
        )
        current_user = await self.store.get_session_user(self.session_digest, self.now)
        await self.store.revoke_session(self.session_digest)
        logged_out_user = await self.store.get_session_user(self.session_digest, self.now)

        self.assertEqual(consumed, attempt)
        self.assertIsNone(repeated)
        self.assertEqual(current_user, user)
        self.assertIsNone(logged_out_user)
