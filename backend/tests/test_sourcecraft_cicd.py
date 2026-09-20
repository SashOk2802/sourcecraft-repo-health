"""Проверяет получение и нормализацию CI-запусков SourceCraft без сети."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx

from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftRequestError,
    SourceCraftResponseError,
)
from backend.app.integrations.sourcecraft_cicd import SourceCraftCicdClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sourcecraft" / "cicd_runs_page.json"


class SourceCraftCicdClientTest(unittest.TestCase):
    """Показывает поведение CI-клиента на реальной форме API-ответа."""

    def test_list_runs_maps_fixture_and_requests_official_endpoint(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=payload)

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))

        runs = client.list_runs(_repository())

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].slug, "17")
        self.assertEqual(runs[0].status, "success")
        self.assertEqual(runs[0].workflow_slugs, ("example-workflow",))
        self.assertEqual(runs[0].started_at.isoformat(), "2026-01-01T00:00:02+00:00")
        self.assertEqual(requests[0].url.path, "/repos/example-org/example-repo/cicd/runs")
        self.assertEqual(requests[0].url.params["page_size"], "100")

    def test_list_runs_rejects_unsafe_repository_slug_before_request(self) -> None:
        requests: list[httpx.Request] = []
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(200, json={"runs": []})
            ),
        )
        client = SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))
        unsafe_repository = RepositoryRef("repo-1", "example-org", "repo/other")

        with self.assertRaises(SourceCraftRequestError):
            client.list_runs(unsafe_repository)

        self.assertEqual(requests, [])

    def test_list_runs_rejects_unknown_run_status(self) -> None:
        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        payload["runs"][0]["status"] = "new-status"
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        )
        client = SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))

        with self.assertRaisesRegex(SourceCraftResponseError, "unknown CI run status"):
            client.list_runs(_repository())


def _repository() -> RepositoryRef:
    return RepositoryRef(
        id="repository-id-redacted",
        organization_slug="example-org",
        repository_slug="example-repo",
    )
