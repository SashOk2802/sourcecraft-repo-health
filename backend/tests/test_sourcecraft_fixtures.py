"""Проверяет обезличенные примеры ответов SourceCraft для будущих анализаторов."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from backend.app.analyzers.security import appsec_payload_status
from backend.app.contracts import DataStatus

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

    def test_empty_appsec_findings_remain_measured(self) -> None:
        self.assertEqual(appsec_payload_status([]), DataStatus.MEASURED)
