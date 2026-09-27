from __future__ import annotations

import unittest

import httpx

from backend.app.main import create_app
from backend.app.scoring import CATEGORY_WEIGHTS, METHODOLOGY_VERSION
from backend.app.scoring.methodology import (
    SECURITY_ACTIVE_STATUSES,
    SECURITY_CONFIRMED_OPEN_STATUSES,
    SECURITY_SEVERITY_RULES,
)


class MethodologyApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_current_score_rules_from_the_core(self) -> None:
        app = create_app()
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/v1/methodology")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["version"], METHODOLOGY_VERSION)
        self.assertEqual(payload["scoreRange"], {"minimum": 0, "maximum": 100})
        self.assertEqual(
            [(category["code"], category["weight"]) for category in payload["categories"]],
            list(CATEGORY_WEIGHTS.items()),
        )
        self.assertEqual(sum(category["weight"] for category in payload["categories"]), 100)
        self.assertEqual(
            payload["aggregation"]["code"],
            "weighted_average_of_measured_categories",
        )
        self.assertEqual(
            {status["code"] for status in payload["dataStatuses"]},
            {"measured", "unavailable", "not_applicable", "insufficient_sample", "error"},
        )
        self.assertEqual(
            payload["scoreLimits"],
            [
                {
                    "code": "security-open-critical",
                    "maximumScore": 60,
                    "summary": (
                        "Подтверждённая открытая критическая AppSec-уязвимость "
                        "ограничивает Score."
                    ),
                }
            ],
        )
        self.assertEqual(payload["security"]["code"], "appsec_severity_status_v1")
        self.assertEqual(payload["security"]["activeStatuses"], sorted(SECURITY_ACTIVE_STATUSES))
        self.assertEqual(
            payload["security"]["confirmedOpenCriticalStatuses"],
            sorted(SECURITY_CONFIRMED_OPEN_STATUSES),
        )
        self.assertEqual(
            payload["security"]["severityPenalties"],
            [
                {
                    "severity": severity,
                    "penaltyPerFinding": penalty,
                    "maximumFindings": maximum_findings,
                }
                for severity, penalty, maximum_findings in SECURITY_SEVERITY_RULES
            ],
        )
