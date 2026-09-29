from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from backend.app.scheduling.runner import (
    PostgresAnalysisScheduleStore,
    ScheduleCandidate,
)


@unittest.skipUnless(
    os.getenv("TEST_POSTGRES") == "1",
    "Для интеграционного теста PostgreSQL установите TEST_POSTGRES=1.",
)
class PostgresAnalysisScheduleStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime(2026, 9, 27, 12, tzinfo=UTC)
        self.repository_id = f"schedule-{uuid4().hex}"
        database_url = os.environ["DATABASE_URL"]
        self.first = PostgresAnalysisScheduleStore(database_url)
        self.second = PostgresAnalysisScheduleStore(database_url)
        await self.first.start()
        await self.second.start()

    async def asyncTearDown(self) -> None:
        pool = self.first._require_pool()
        await pool.execute(
            "DELETE FROM analysis_schedules WHERE repository_id = $1",
            self.repository_id,
        )
        await self.second.close()
        await self.first.close()

    async def test_second_store_cannot_claim_the_same_due_repository(self) -> None:
        await self.first.reconcile_catalog(
            (ScheduleCandidate(self.repository_id, self.now),),
            observed_at=self.now,
        )

        first_claim = await self.first.claim_due(
            now=self.now,
            limit=1,
            lease_owner="scheduler-first",
            lease_expires_at=self.now + timedelta(minutes=5),
        )
        second_claim = await self.second.claim_due(
            now=self.now,
            limit=1,
            lease_owner="scheduler-second",
            lease_expires_at=self.now + timedelta(minutes=5),
        )
        reserved = await self.first.reserve_submission(
            self.repository_id,
            lease_owner="scheduler-first",
            analysis_id="analysis-reserved",
            updated_at=self.now,
        )
        released = await self.first.release_submission(
            self.repository_id,
            lease_owner="scheduler-first",
            analysis_id="analysis-reserved",
            next_analysis_at=self.now + timedelta(minutes=15),
            consecutive_failures=1,
            updated_at=self.now,
        )

        self.assertEqual([entry.repository_id for entry in first_claim], [self.repository_id])
        self.assertEqual(second_claim, ())
        self.assertTrue(reserved)
        self.assertTrue(released)

    async def test_ondemand_claim_observes_periodic_reservation(self) -> None:
        await self.first.reconcile_catalog(
            (ScheduleCandidate(self.repository_id, self.now),),
            observed_at=self.now,
        )

        periodic_claim = await self.first.claim_due(
            now=self.now,
            limit=1,
            lease_owner="scheduler-first",
            lease_expires_at=self.now + timedelta(minutes=5),
        )
        ondemand_claim = await self.second.claim_on_demand(
            self.repository_id,
            now=self.now,
            lease_owner="ondemand-second",
            lease_expires_at=self.now + timedelta(minutes=5),
        )

        self.assertEqual([entry.repository_id for entry in periodic_claim], [self.repository_id])
        self.assertIsNone(ondemand_claim)
