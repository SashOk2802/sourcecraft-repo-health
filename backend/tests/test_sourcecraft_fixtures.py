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

    def test_appsec_null_fixture_becomes_unavailable(self) -> None:
        payload = load_fixture("appsec_defects_null.json")

        self.assertIsNone(payload)
        self.assertEqual(appsec_payload_status(payload), DataStatus.UNAVAILABLE)

    def test_appsec_sast_summary_fixture_contains_only_safe_aggregate(self) -> None:
        payload = load_fixture("appsec_sast_summary.json")

        self.assertEqual(
            set(payload),
            {
                "engine",
                "availability",
                "finding_count",
                "severities",
                "reason",
                "finding_groups",
                "completeness",
            },
        )
        self.assertEqual(payload["engine"], "SAST")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(payload["finding_count"], 23)
        self.assertEqual(payload["severities"], ["HIGH", "LOW", "MEDIUM"])
        self.assertIsNone(payload["reason"])
        # Эта fixture создана до безопасной агрегации status. Нельзя
        # выдумывать распределение по статусам для живого наблюдения.
        self.assertIsNone(payload["finding_groups"])
        self.assertEqual(payload["completeness"], "unknown")
        self.assertEqual(appsec_payload_status(payload), DataStatus.MEASURED)

        result = AppSecProbeResult(
            engine=payload["engine"],
            availability=payload["availability"],
            finding_count=payload["finding_count"],
            severities=tuple(payload["severities"]),
            reason=payload["reason"],
            finding_groups=payload["finding_groups"],
            completeness=payload["completeness"],
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
