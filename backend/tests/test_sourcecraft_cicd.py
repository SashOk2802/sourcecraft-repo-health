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
OBSERVED_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sourcecraft" / "ci_runs.json"


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
        self.assertEqual(runs[0].id, "")
        self.assertEqual(runs[0].slug, "17")
        self.assertEqual(runs[0].status, "success")
        self.assertEqual(runs[0].workflow_slugs, ("example-workflow",))
        self.assertEqual(runs[0].started_at.isoformat(), "2026-01-01T00:00:02+00:00")
        self.assertEqual(requests[0].url.path, "/repos/example-org/example-repo/cicd/runs")
        self.assertEqual(requests[0].url.params["page_size"], "100")

    def test_list_runs_accepts_all_documented_event_types(self) -> None:
        # Перечень взят из API-контракта, а не из константы самого клиента.
        for event_type in (
            "push",
            "pr_update",
            "manual",
            "restart",
            "schedule",
            "repository_event",
        ):
            with self.subTest(event_type=event_type):
                payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
                payload["runs"][0]["event_type"] = event_type
                with httpx.Client(
                    base_url="https://api.sourcecraft.tech",
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    ),
                ) as http_client:
                    client = SourceCraftCicdClient(
                        SourceCraftClient("test-token", http_client=http_client)
                    )

                    runs = client.list_runs(_repository())

                self.assertEqual(len(runs), 1)
                self.assertEqual(runs[0].event_type, event_type)
                self.assertEqual(runs[0].slug, "17")
                self.assertEqual(runs[0].status, "success")

    def test_list_runs_rejects_invalid_event_type_values(self) -> None:
        for event_type in (None, "", 123, True, [], {}, "unknown-event", "issue_comment.create"):
            with self.subTest(event_type=event_type):
                payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
                payload["runs"][0]["event_type"] = event_type
                with httpx.Client(
                    base_url="https://api.sourcecraft.tech",
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    ),
                ) as http_client:
                    client = SourceCraftCicdClient(
                        SourceCraftClient("test-token", http_client=http_client)
                    )

                    with self.assertRaises(SourceCraftResponseError):
                        client.list_runs(_repository())

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

    def test_list_runs_accepts_observed_empty_run_id(self) -> None:
        # Реальная обезличенная CLI fixture содержит пустой id, но стабильный slug.
        payload = {
            "runs": json.loads(OBSERVED_FIXTURE_PATH.read_text(encoding="utf-8")),
            "next_page_token": "",
        }
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        )
        client = SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))

        runs = client.list_runs(_repository())

        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].id, "")
        self.assertEqual(runs[0].slug, "1")
        self.assertEqual(runs[0].event_type, "manual")

    def test_list_runs_rejects_missing_or_non_string_run_id(self) -> None:
        for invalid_id in (None, 123, True, [], {}):
            with self.subTest(invalid_id=invalid_id):
                payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
                payload["runs"][0]["id"] = invalid_id
                with httpx.Client(
                    base_url="https://api.sourcecraft.tech",
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    ),
                ) as http_client:
                    client = SourceCraftCicdClient(
                        SourceCraftClient("test-token", http_client=http_client)
                    )

                    with self.assertRaisesRegex(SourceCraftResponseError, "string id"):
                        client.list_runs(_repository())

        payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        del payload["runs"][0]["id"]
        with httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ) as http_client:
            client = SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))

            with self.assertRaisesRegex(SourceCraftResponseError, "string id"):
                client.list_runs(_repository())

    def test_list_runs_combines_pages_and_normalizes_timezones(self) -> None:
        first_page = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        first_page["next_page_token"] = "next-page"
        second_page = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        second_page["runs"][0]["id"] = "second-run-id-redacted"
        second_page["runs"][0]["slug"] = "18"
        second_page["runs"][0]["event_type"] = "repository_event"
        second_page["runs"][0]["dates"]["created_at"] = "2026-01-01T03:00:00+03:00"
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = (
                second_page if request.url.params.get("page_token") == "next-page" else first_page
            )
            return httpx.Response(200, json=payload)

        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(handler),
        )
        client = SourceCraftCicdClient(SourceCraftClient("test-token", http_client=http_client))

        runs = client.list_runs(_repository(), page_size=1)

        self.assertEqual(tuple(run.slug for run in runs), ("17", "18"))
        self.assertEqual(tuple(run.id for run in runs), ("", "second-run-id-redacted"))
        self.assertEqual(tuple(run.event_type for run in runs), ("manual", "repository_event"))
        self.assertEqual(runs[1].created_at.isoformat(), "2026-01-01T00:00:00+00:00")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].url.params["page_size"], "1")
        self.assertEqual(requests[1].url.params["page_token"], "next-page")

    def test_list_runs_rejects_unknown_event_type_and_naive_timestamp(self) -> None:
        for field, value, message in (
            ("event_type", "new-event", "unknown CI event type"),
            ("dates.created_at", "2026-01-01T00:00:00", "must include a timezone"),
        ):
            with self.subTest(field=field):
                payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
                if field == "dates.created_at":
                    payload["runs"][0]["dates"]["created_at"] = value
                else:
                    payload["runs"][0][field] = value
                http_client = httpx.Client(
                    base_url="https://api.sourcecraft.tech",
                    transport=httpx.MockTransport(
                        lambda request, payload=payload: httpx.Response(200, json=payload)
                    ),
                )
                client = SourceCraftCicdClient(
                    SourceCraftClient("test-token", http_client=http_client)
                )

                with self.assertRaisesRegex(SourceCraftResponseError, message):
                    client.list_runs(_repository())


def _repository() -> RepositoryRef:
    return RepositoryRef(
        id="repository-id-redacted",
        organization_slug="example-org",
        repository_slug="example-repo",
    )
