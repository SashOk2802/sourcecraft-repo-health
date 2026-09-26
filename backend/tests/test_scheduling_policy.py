from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.scheduling import (
    AnalysisCadence,
    decide_manual_request,
    schedule_periodic_analysis,
    schedule_temporary_retry,
)
from backend.app.scheduling.policy import (
    ACTIVE_ANALYSIS_INTERVAL,
    DORMANT_ANALYSIS_INTERVAL,
    INITIAL_RETRY_DELAY,
    MANUAL_ANALYSIS_INTERVAL,
    MAX_RETRY_DELAY,
    STANDARD_ANALYSIS_INTERVAL,
)


class SchedulingPolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 25, 12, tzinfo=UTC)

    def test_active_repository_runs_every_six_hours_plus_stable_jitter(self) -> None:
        first = schedule_periodic_analysis(
            "repo-42",
            scheduled_from=self.now,
            last_activity_at=self.now - timedelta(days=6),
        )
        repeated = schedule_periodic_analysis(
            "repo-42",
            scheduled_from=self.now,
            last_activity_at=self.now - timedelta(days=6),
        )

        self.assertEqual(first.cadence, AnalysisCadence.ACTIVE)
        self.assertEqual(first.interval, ACTIVE_ANALYSIS_INTERVAL)
        self.assertEqual(first, repeated)
        self.assertGreaterEqual(first.due_at, self.now + ACTIVE_ANALYSIS_INTERVAL)
        self.assertLessEqual(
            first.due_at,
            self.now + ACTIVE_ANALYSIS_INTERVAL * 1.1,
        )

    def test_unknown_and_recent_activity_use_standard_cadence(self) -> None:
        for last_activity_at in (None, self.now - timedelta(days=8), self.now - timedelta(days=90)):
            with self.subTest(last_activity_at=last_activity_at):
                schedule = schedule_periodic_analysis(
                    "repo-42",
                    scheduled_from=self.now,
                    last_activity_at=last_activity_at,
                )
                self.assertEqual(schedule.cadence, AnalysisCadence.STANDARD)
                self.assertEqual(schedule.interval, STANDARD_ANALYSIS_INTERVAL)

    def test_dormant_repository_runs_every_seventy_two_hours(self) -> None:
        schedule = schedule_periodic_analysis(
            "repo-42",
            scheduled_from=self.now,
            last_activity_at=self.now - timedelta(days=90, seconds=1),
        )

        self.assertEqual(schedule.cadence, AnalysisCadence.DORMANT)
        self.assertEqual(schedule.interval, DORMANT_ANALYSIS_INTERVAL)

    def test_rejects_timezone_naive_schedule_origin(self) -> None:
        with self.assertRaisesRegex(ValueError, "scheduled_from"):
            schedule_periodic_analysis(
                "repo-42",
                scheduled_from=self.now.replace(tzinfo=None),
                last_activity_at=None,
            )

    def test_rejects_future_or_timezone_naive_activity(self) -> None:
        invalid_values = (
            self.now + timedelta(seconds=1),
            self.now.replace(tzinfo=None),
        )
        for last_activity_at in invalid_values:
            with (
                self.subTest(last_activity_at=last_activity_at),
                self.assertRaisesRegex(ValueError, "last_activity_at"),
            ):
                schedule_periodic_analysis(
                        "repo-42",
                        scheduled_from=self.now,
                        last_activity_at=last_activity_at,
                    )

    def test_temporary_retry_doubles_delay_and_stops_at_six_hours(self) -> None:
        first = schedule_temporary_retry(
            "repo-42",
            failed_at=self.now,
            consecutive_failures=1,
        )
        third = schedule_temporary_retry(
            "repo-42",
            failed_at=self.now,
            consecutive_failures=3,
        )
        capped = schedule_temporary_retry(
            "repo-42",
            failed_at=self.now,
            consecutive_failures=100,
        )

        self.assertEqual(first.delay, INITIAL_RETRY_DELAY)
        self.assertGreaterEqual(first.due_at, self.now + INITIAL_RETRY_DELAY)
        self.assertEqual(third.delay, timedelta(hours=1))
        self.assertEqual(capped.delay, MAX_RETRY_DELAY)
        self.assertEqual(capped.jitter, timedelta())
        self.assertEqual(capped.due_at, self.now + MAX_RETRY_DELAY)

    def test_rejects_invalid_retry_counter(self) -> None:
        for failures in (0, -1, True, False, 1.5, "1"):
            with (
                self.subTest(failures=failures),
                self.assertRaisesRegex(ValueError, "positive integer"),
            ):
                schedule_temporary_retry(
                        "repo-42",
                        failed_at=self.now,
                        consecutive_failures=failures,
                    )

    def test_manual_request_has_a_fifteen_minute_cooldown(self) -> None:
        first = decide_manual_request(requested_at=self.now, previous_request_at=None)
        early = decide_manual_request(
            requested_at=self.now + MANUAL_ANALYSIS_INTERVAL - timedelta(seconds=1),
            previous_request_at=self.now,
        )
        allowed_again = decide_manual_request(
            requested_at=self.now + MANUAL_ANALYSIS_INTERVAL,
            previous_request_at=self.now,
        )

        self.assertTrue(first.accepted)
        self.assertEqual(first.next_allowed_at, self.now + MANUAL_ANALYSIS_INTERVAL)
        self.assertFalse(early.accepted)
        self.assertEqual(early.next_allowed_at, self.now + MANUAL_ANALYSIS_INTERVAL)
        self.assertTrue(allowed_again.accepted)
        self.assertEqual(
            allowed_again.next_allowed_at,
            self.now + MANUAL_ANALYSIS_INTERVAL * 2,
        )

    def test_rejects_request_history_from_the_future(self) -> None:
        with self.assertRaisesRegex(ValueError, "previous_request_at"):
            decide_manual_request(
                requested_at=self.now,
                previous_request_at=self.now + timedelta(seconds=1),
            )

    def test_rejects_blank_repository_and_naive_request_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "repository_id"):
            schedule_periodic_analysis(
                " ",
                scheduled_from=self.now,
                last_activity_at=None,
            )
        with self.assertRaisesRegex(ValueError, "requested_at"):
            decide_manual_request(
                requested_at=self.now.replace(tzinfo=None),
                previous_request_at=None,
            )
