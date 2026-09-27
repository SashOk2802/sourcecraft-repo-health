"""Проверяет адаптацию SourceCraft CI runs к фактам анализатора без сети."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import httpx

from backend.app.analyzers.cicd import make_analyzer
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftResponseError
from backend.app.integrations.sourcecraft_cicd import SourceCraftCicdClient, SourceCraftCiRun
from backend.app.integrations.sourcecraft_cicd_facts import (
    collect_cicd_facts,
    make_cicd_facts_provider,
)

OBSERVED_RUNS_PATH = Path(__file__).parent / "fixtures" / "sourcecraft" / "ci_runs.json"
OBSERVED_AUTOMATED_RUNS_PATH = (
    Path(__file__).parent / "fixtures" / "sourcecraft" / "ci_runs_automated_success.json"
)


class SourceCraftCicdFactsTest(unittest.TestCase):
    def test_collects_observed_empty_id_without_putting_it_in_facts(self) -> None:
        payload = {
            "runs": json.loads(OBSERVED_RUNS_PATH.read_text(encoding="utf-8")),
            "next_page_token": "",
        }
        with _http_client(lambda _: httpx.Response(200, json=payload)) as http_client:
            facts = collect_cicd_facts(_cicd_client(http_client), _repository())

        self.assertIsNone(facts.source_error)
        self.assertEqual(len(facts.runs), 1)
        self.assertEqual(facts.runs[0].slug, "1")
        self.assertEqual(facts.runs[0].event_type, "manual")
        self.assertNotIn("id", repr(facts))

    def test_collects_paginated_repository_event_without_dropping_history(self) -> None:
        first_page = _page("1", "push", next_page_token="next-page")
        second_page = _page("2", "repository_event")
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = (
                second_page if request.url.params.get("page_token") == "next-page" else first_page
            )
            return httpx.Response(200, json=payload)

        with _http_client(handler) as http_client:
            facts = collect_cicd_facts(_cicd_client(http_client), _repository())

        self.assertIsNone(facts.source_error)
        self.assertEqual(tuple(run.slug for run in facts.runs), ("1", "2"))
        self.assertEqual(tuple(run.event_type for run in facts.runs), ("push", "repository_event"))
        self.assertEqual(len(requests), 2)

    def test_client_error_becomes_safe_unavailable_facts(self) -> None:
        marker = "Bearer sourcecraft-private-token"
        client = Mock()
        client.list_runs.side_effect = SourceCraftResponseError(marker)

        facts = collect_cicd_facts(client, _repository())

        self.assertIsNone(facts.runs)
        self.assertEqual(facts.source_error, "sourcecraft_cicd_client_failed")
        self.assertNotIn(marker, repr(facts))

    def test_mapping_error_does_not_return_partial_history(self) -> None:
        client = Mock()
        client.list_runs.return_value = (
            SourceCraftCiRun(
                id="",
                slug="run-1",
                status="success",
                event_type="future-event-type",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                started_at=None,
                finished_at=None,
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                workflow_slugs=(),
            ),
        )

        facts = collect_cicd_facts(client, _repository())

        self.assertIsNone(facts.runs)
        self.assertEqual(facts.source_error, "sourcecraft_cicd_mapping_failed")

    def test_provider_feeds_cicd_analyzer_with_real_client_shape(self) -> None:
        client = Mock()
        client.list_runs.return_value = tuple(
            _run(str(index), "success", "push") for index in range(1, 6)
        )
        provider = make_cicd_facts_provider(client)

        result = make_analyzer(provider)(_context())

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(client.list_runs.call_args.args, (_repository(),))

    def test_observed_automated_successes_reach_measured_cicd_score(self) -> None:
        payload = {
            "runs": json.loads(OBSERVED_AUTOMATED_RUNS_PATH.read_text(encoding="utf-8")),
            "next_page_token": "",
        }
        with _http_client(lambda _: httpx.Response(200, json=payload)) as http_client:
            result = make_analyzer(make_cicd_facts_provider(_cicd_client(http_client)))(_context())

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.metrics[0].value, 5)


def _http_client(handler):
    return httpx.Client(
        base_url="https://api.sourcecraft.tech",
        transport=httpx.MockTransport(handler),
    )


def _cicd_client(http_client: httpx.Client) -> SourceCraftCicdClient:
    return SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))


def _page(slug: str, event_type: str, *, next_page_token: str = "") -> dict[str, object]:
    return {
        "runs": [
            {
                "id": "",
                "slug": slug,
                "status": "success",
                "event_type": event_type,
                "dates": {
                    "created_at": "2026-01-01T00:00:00Z",
                    "started_at": "2026-01-01T00:00:00Z",
                    "finished_at": "2026-01-01T00:00:01Z",
                    "updated_at": "2026-01-01T00:00:01Z",
                },
                "workflows": [],
            }
        ],
        "next_page_token": next_page_token,
    }


def _run(slug: str, status: str, event_type: str) -> SourceCraftCiRun:
    timestamp = datetime(2026, 1, 15, tzinfo=UTC)
    return SourceCraftCiRun(
        id="",
        slug=slug,
        status=status,
        event_type=event_type,
        created_at=timestamp,
        started_at=timestamp,
        finished_at=timestamp,
        updated_at=timestamp,
        workflow_slugs=(),
    )


def _repository() -> RepositoryRef:
    return RepositoryRef("repository-id-redacted", "example-org", "example-repo")


def _context() -> AnalysisContext:
    timestamp = datetime(2026, 2, 1, tzinfo=UTC)
    return AnalysisContext(
        repository=_repository(),
        commit_sha="commit-sha-redacted",
        analyzed_at=timestamp,
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=timestamp,
    )
