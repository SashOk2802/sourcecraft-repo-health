"""Activity и Issues доходят до общего run_analysis через провайдер диспетчера."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analysis.runner import run_analysis
from backend.app.analyzers.activity import CATEGORY_CODE as ACTIVITY_CODE
from backend.app.analyzers.issues import CATEGORY_CODE as ISSUES_CODE
from backend.app.analyzers.registration import project_life_analyzer_provider
from backend.app.contracts import AnalysisContext, CategoryResult, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft import SourceCraftClient
from backend.app.scoring.engine import CATEGORY_WEIGHTS

ANALYZED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
PERIOD_START = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
REPOSITORY_PATH = "/repos/team/platform"
PAGE_SIZE = "100"

# Код чужой категории берётся из ядра, а не из отдельной строковой копии теста.
SECURITY_CODE = next(code for code in CATEGORY_WEIGHTS if code == "security")

REPOSITORY = RepositoryRef(
    id="repo-1",
    organization_slug="team",
    repository_slug="platform",
    web_url="https://sourcecraft.dev/team/platform",
)

RequestKey = tuple[str, str | None, str | None]


def context() -> AnalysisContext:
    """Контекст анализа с зафиксированным временем: тесты не зависят от часов машины."""
    return AnalysisContext(
        repository=REPOSITORY,
        commit_sha="0" * 40,
        analyzed_at=ANALYZED_AT,
        period_start=PERIOD_START,
        period_end=ANALYZED_AT,
    )


def moment(days_before_analysis: int) -> str:
    return (ANALYZED_AT - timedelta(days=days_before_analysis)).isoformat()


def category(execution, code: str) -> CategoryResult:
    for item in execution.analysis.categories:
        if item.category == code:
            return item
    raise AssertionError(code)


def metric_codes(result: CategoryResult) -> set[str]:
    return {metric.code for metric in result.metrics}


class OwnedClient(SourceCraftClient):
    """Клиент теста: закрывает и свой пул, и переданный httpx.Client."""

    def __init__(self, http_client: httpx.Client) -> None:
        super().__init__("test-token", http_client=http_client)
        self.http_client = http_client
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()
        self.http_client.close()


class RecordingOpener:
    """Новый клиент на каждый вызов. Так проверяется срок жизни, а не общий пул."""

    def __init__(self, handler) -> None:
        self._handler = handler
        self.clients: list[OwnedClient] = []
        self.contexts: list[AnalysisContext] = []

    def __call__(self, analysis_context: AnalysisContext) -> SourceCraftClient:
        self.contexts.append(analysis_context)
        http_client = httpx.Client(
            base_url="https://api.sourcecraft.tech",
            transport=httpx.MockTransport(self._handler),
        )
        client = OwnedClient(http_client)
        self.clients.append(client)
        return client

    def close_leftovers(self) -> None:
        for client in self.clients:
            if not client.closed:
                client.close()


def issue(slug: str, *, created_days_ago: int, updated_days_ago: int, status_type: str) -> dict:
    payload = {
        "slug": slug,
        "title": slug,
        "created_at": moment(created_days_ago),
        "updated_at": moment(updated_days_ago),
        "status": {"status_type": status_type},
    }
    if status_type == "completed":
        payload["completed_at"] = moment(updated_days_ago)
    return payload


def live_payload(path: str, status_filter: str | None) -> dict:
    """Ответы, по которым видны все четыре метрики Activity и медиана Issues."""
    if path == f"{REPOSITORY_PATH}/issues":
        if status_filter == "status=open":
            issues = [issue("open-1", created_days_ago=10, updated_days_ago=1, status_type="initial")]
        elif status_filter == "status=in_progress":
            issues = []
        elif status_filter == "status=closed":
            issues = [
                issue(
                    f"closed-{index}",
                    created_days_ago=20,
                    updated_days_ago=5,
                    status_type="completed",
                )
                for index in range(3)
            ]
        else:
            raise AssertionError(status_filter)
        return {"issues": issues, "next_page_token": ""}
    if path == f"{REPOSITORY_PATH}/contributors":
        return {"contributors": [{"id": "u1", "username": "alice"}], "next_page_token": ""}
    if path == f"{REPOSITORY_PATH}/pulls":
        return {
            "pull_requests": [
                {
                    "slug": "1",
                    "title": "MR",
                    "status": "merged",
                    "created_at": moment(8),
                    "updated_at": moment(2),
                }
            ],
            "next_page_token": "",
        }
    if path == f"{REPOSITORY_PATH}/releases":
        return {
            "releases": [
                {
                    "tag": "v1",
                    "title": "v1",
                    "status": "published",
                    "released_at": moment(3),
                }
            ],
            "next_page_token": "",
        }
    if path == REPOSITORY_PATH:
        return {"last_updated": moment(2), "is_empty": False}
    raise AssertionError(path)


class ProjectLifeRegistrationTest(unittest.TestCase):
    """Проверяет шов между диспетчером и категориями Activity и Issues."""

    def test_provider_matches_dispatcher_and_opens_client_only_inside_evaluate(self) -> None:
        """submit получает регистрации без сети; клиент живёт только внутри evaluate."""
        opener = RecordingOpener(lambda request: httpx.Response(500))
        provider = project_life_analyzer_provider(opener)
        registrations = provider(context())

        self.assertEqual(
            tuple(item.category for item in registrations),
            (ACTIVITY_CODE, ISSUES_CODE),
        )
        self.assertEqual(opener.clients, [])

        try:
            run_analysis(context(), registrations)
        finally:
            opener.close_leftovers()

        self.assertEqual(len(opener.clients), 2)
        self.assertTrue(all(client.closed for client in opener.clients))
        self.assertEqual(len(opener.contexts), 2)

    def test_run_analysis_reads_every_activity_and_issues_endpoint(self) -> None:
        """Оценка держится на MR, релизах, участниках и задачах, не на одной давности."""
        seen: list[RequestKey] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            status_filter = request.url.params.get("filter")
            page_size = request.url.params.get("page_size")
            seen.append((path, status_filter, page_size))
            if path != REPOSITORY_PATH and page_size != PAGE_SIZE:
                return httpx.Response(500)
            try:
                payload = live_payload(path, status_filter)
            except AssertionError:
                return httpx.Response(500)
            return httpx.Response(200, json=payload)

        opener = RecordingOpener(handler)
        try:
            execution = run_analysis(context(), project_life_analyzer_provider(opener)(context()))
        finally:
            opener.close_leftovers()

        activity = category(execution, ACTIVITY_CODE)
        issues = category(execution, ISSUES_CODE)
        self.assertIs(activity.status, DataStatus.MEASURED)
        self.assertIs(issues.status, DataStatus.MEASURED)
        self.assertEqual(
            metric_codes(activity),
            {
                "last_activity_days",
                "merged_mr_in_period",
                "releases_in_period",
                "contributor_count",
            },
        )
        self.assertIn("stale_open_ratio", metric_codes(issues))
        self.assertIn("median_days_to_close", metric_codes(issues))
        self.assertEqual(category(execution, SECURITY_CODE).reason, "analyzer_not_configured")
        self.assertEqual(
            seen,
            [
                (REPOSITORY_PATH, None, None),
                (f"{REPOSITORY_PATH}/contributors", None, PAGE_SIZE),
                (f"{REPOSITORY_PATH}/pulls", None, PAGE_SIZE),
                (f"{REPOSITORY_PATH}/releases", None, PAGE_SIZE),
                (f"{REPOSITORY_PATH}/issues", "status=open", PAGE_SIZE),
                (f"{REPOSITORY_PATH}/issues", "status=in_progress", PAGE_SIZE),
                (f"{REPOSITORY_PATH}/issues", "status=closed", PAGE_SIZE),
            ],
        )
        self.assertTrue(all(client.closed for client in opener.clients))

    def test_issues_outage_keeps_activity_measured(self) -> None:
        """500 только на задачах не обнуляет Activity."""
        execution = self._run(fail_prefix=f"{REPOSITORY_PATH}/issues")

        self.assertIs(category(execution, ACTIVITY_CODE).status, DataStatus.MEASURED)
        self.assertIs(category(execution, ISSUES_CODE).status, DataStatus.UNAVAILABLE)
        self.assertIsNone(category(execution, ISSUES_CODE).score)

    def test_pulls_outage_keeps_issues_measured(self) -> None:
        """500 только на MR не обнуляет Issues."""
        execution = self._run(fail_prefix=f"{REPOSITORY_PATH}/pulls")

        self.assertIs(category(execution, ISSUES_CODE).status, DataStatus.MEASURED)
        self.assertIs(category(execution, ACTIVITY_CODE).status, DataStatus.MEASURED)
        self.assertNotIn("merged_mr_in_period", metric_codes(category(execution, ACTIVITY_CODE)))

    def test_total_source_failure_stays_inside_each_category(self) -> None:
        """Сбой обоих источников остаётся статусом категории, а не падением запуска."""
        execution = self._run(fail_prefix=REPOSITORY_PATH)

        self.assertIs(category(execution, ACTIVITY_CODE).status, DataStatus.UNAVAILABLE)
        self.assertIs(category(execution, ISSUES_CODE).status, DataStatus.UNAVAILABLE)
        self.assertIsNone(category(execution, ACTIVITY_CODE).score)
        self.assertIsNone(category(execution, ISSUES_CODE).score)

    def _run(self, *, fail_prefix: str):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.startswith(fail_prefix):
                return httpx.Response(500)
            payload = live_payload(request.url.path, request.url.params.get("filter"))
            return httpx.Response(200, json=payload)

        opener = RecordingOpener(handler)
        try:
            return run_analysis(context(), project_life_analyzer_provider(opener)(context()))
        finally:
            opener.close_leftovers()


if __name__ == "__main__":
    unittest.main()
