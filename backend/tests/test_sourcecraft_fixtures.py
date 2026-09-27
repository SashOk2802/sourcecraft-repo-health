"""Проверяет обезличенные примеры ответов SourceCraft для будущих анализаторов."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from backend.app.analyzers.security import appsec_payload_status
from backend.app.contracts import DataStatus
from backend.app.integrations.sourcecraft_appsec_probe import AppSecProbeResult

FIXTURES_DIRECTORY = Path(__file__).parent / "fixtures" / "sourcecraft"


def load_fixture(name: str) -> Any:
    """Загружает сохранённый JSON-ответ без обращения к сети."""

    return json.loads((FIXTURES_DIRECTORY / name).read_text(encoding="utf-8"))


class SourceCraftFixtureTest(unittest.TestCase):
    """Проверяет минимальные поля уже подтверждённых ответов платформы."""

    def test_repositories_page_fixture_preserves_page_token_contract(self) -> None:
        payload = load_fixture("repositories_page.json")

        self.assertIsInstance(payload["repositories"], list)
        self.assertIn("next_page_token", payload)
        self.assertEqual(payload["next_page_token"], "")
        self.assertEqual(payload["repositories"][0]["visibility"], "private")

    def test_ci_runs_fixture_preserves_observed_run_fields(self) -> None:
        payload = load_fixture("ci_runs.json")

        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]["status"], "success")
        self.assertEqual(payload[0]["event_type"], "manual")
        self.assertIn("workflows", payload[0])

    def test_ci_failure_fixture_preserves_observed_failed_status(self) -> None:
        payload = load_fixture("ci_runs_failure.json")
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]["status"], "failed")
        self.assertEqual(payload[0]["event_type"], "manual")
        self.assertEqual(payload[0]["workflows"][0]["status"], "failed")
        self.assertNotIn("sourcecraft-health-repo-demo", serialized)
        self.assertNotIn("ci/sourcecraft-failure-evidence", serialized)

    def test_automated_ci_success_fixture_has_the_minimum_scoreable_sample(self) -> None:
        payload = load_fixture("ci_runs_automated_success.json")
        serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(len(payload), 5)
        self.assertTrue(all(run["status"] == "success" for run in payload))
        self.assertTrue(all(run["event_type"] == "push" for run in payload))
        self.assertNotIn("sourcecraft-health-repo-demo", serialized)
        self.assertNotIn("ci/sourcecraft-smoke", serialized)

    def test_appsec_null_fixture_becomes_unavailable(self) -> None:
        payload = load_fixture("appsec_defects_null.json")

        self.assertIsNone(payload)
        self.assertEqual(appsec_payload_status(payload), DataStatus.UNAVAILABLE)

    def test_appsec_sast_summary_fixture_contains_only_safe_aggregate(self) -> None:
        payload = load_fixture("appsec_sast_summary.json")

        self.assertEqual(
            set(payload),
            {"engine", "availability", "finding_count", "severities", "reason"},
        )
        self.assertEqual(payload["engine"], "SAST")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(payload["finding_count"], 23)
        self.assertEqual(payload["severities"], ["HIGH", "LOW", "MEDIUM"])
        self.assertIsNone(payload["reason"])
        self.assertEqual(appsec_payload_status(payload), DataStatus.MEASURED)

        result = AppSecProbeResult(
            engine=payload["engine"],
            availability=payload["availability"],
            finding_count=payload["finding_count"],
            severities=tuple(payload["severities"]),
            reason=payload["reason"],
        )
        self.assertEqual(result.as_dict(), payload)

        serialized = repr(payload)
        for forbidden_field in (
            "repository",
            "slug",
            "path",
            "file",
            "snippet",
            "description",
            "message",
            "secret",
            "timestamp",
        ):
            with self.subTest(forbidden_field=forbidden_field):
                self.assertNotIn(forbidden_field, serialized)

    def test_empty_appsec_findings_remain_measured(self) -> None:
        self.assertEqual(appsec_payload_status([]), DataStatus.MEASURED)
