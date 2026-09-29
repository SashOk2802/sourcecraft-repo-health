"""История Score публичного репозитория: только сохранённые снимки и только public."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from backend.app.analysis import InMemoryAnalysisStore
from backend.app.contracts import RepositoryRef
from backend.app.main import create_app
from backend.app.scoring.methodology import CATEGORY_WEIGHTS
from backend.tests.test_public_health_api import (
    _api_client,
    _Catalog,
    _execution,
    _private_repository,
    _public_repository,
    _UnavailableCatalog,
)

_HISTORY_URL = "/api/v1/public/repositories/demo-org/health-api/history"
_ALLOWED_POINT_FIELDS = {"analyzedAt", "score", "coverage", "status", "methodologyVersion"}


def _repository_ref(repository_id: str = "repo-public", repository_slug: str = "health-api") -> RepositoryRef:
    return RepositoryRef(repository_id, "demo-org", repository_slug, f"https://sourcecraft.dev/demo-org/{repository_slug}")


class PublicHistoryApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.start = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        self.store = InMemoryAnalysisStore()
        self.catalog = _Catalog((_public_repository(),))
        self.app = create_app(analysis_store=self.store, repository_catalog=self.catalog)

    async def _save(self, index: int, score: float, *, repository: RepositoryRef | None = None) -> None:
        await self.store.save(
            f"analysis-{index:03d}",
            _execution(
                repository=repository or _repository_ref(),
                analyzed_at=self.start + timedelta(days=index),
                category_scores={category: score for category in CATEGORY_WEIGHTS},
                include_private_recommendation=True,
            ),
        )

    async def test_returns_last_twenty_points_oldest_first_with_allowed_fields_only(self) -> None:
        for index in range(25):
            await self._save(index, 50 + index)

        async with _api_client(self.app) as client:
            response = await client.get(_HISTORY_URL)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"points"})
        points = body["points"]
        self.assertEqual(len(points), 20)
        # Последние 20 снимков из 25, по возрастанию даты.
        self.assertEqual(points[0]["analyzedAt"], "2026-09-06T12:00:00Z")
        self.assertEqual(points[-1]["analyzedAt"], "2026-09-25T12:00:00Z")
        self.assertEqual([point["score"] for point in points], [float(55 + index) for index in range(20)])
        for point in points:
            self.assertEqual(set(point), _ALLOWED_POINT_FIELDS)
            self.assertEqual(point["status"], "completed")
            self.assertEqual(point["coverage"], 1.0)
        # Ни рекомендаций, ни фактов, ни ID анализа.
        self.assertNotIn("private-finding-marker", response.text)
        self.assertNotIn("private-evidence-marker", response.text)
        self.assertNotIn("analysis-", response.text)

    async def test_public_repository_without_snapshots_has_honest_empty_history(self) -> None:
        async with _api_client(self.app) as client:
            response = await client.get(_HISTORY_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"points": []})

    async def test_unlisted_and_unknown_repositories_are_indistinguishable_404(self) -> None:
        """Закрытый репозиторий в публичный каталог не попадает: его история не отдаётся."""

        await self._save(0, 80)
        closed_app = create_app(analysis_store=self.store, repository_catalog=_Catalog(()))

        async with _api_client(closed_app) as client:
            closed = await client.get(_HISTORY_URL)
        async with _api_client(self.app) as client:
            unknown = await client.get("/api/v1/public/repositories/demo-org/missing/history")

        self.assertEqual(closed.status_code, 404)
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(closed.json(), unknown.json())
        self.assertEqual(closed.headers["cache-control"], "no-store")
        self.assertNotIn("80", closed.text)

    async def test_private_record_in_public_catalog_is_rejected_not_exposed(self) -> None:
        """Как у /health: private-запись в публичном каталоге — испорченные данные, 503."""

        await self._save(0, 80)
        app = create_app(analysis_store=self.store, repository_catalog=_Catalog((_private_repository(),)))

        async with _api_client(app) as client:
            response = await client.get(_HISTORY_URL)

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("points", response.text)

    async def test_visibility_is_rechecked_on_every_request(self) -> None:
        await self._save(0, 80)

        async with _api_client(self.app) as client:
            public = await client.get(_HISTORY_URL)
            # Репозиторий закрыли — он пропал из публичного каталога: история больше не отдаётся.
            self.catalog.repositories = ()
            closed = await client.get(_HISTORY_URL)

        self.assertEqual(public.status_code, 200)
        self.assertEqual(len(public.json()["points"]), 1)
        self.assertEqual(closed.status_code, 404)

    async def test_snapshots_of_other_repository_identity_are_excluded(self) -> None:
        await self._save(0, 70)
        # Тот же id, но другой slug: снимок до переименования не выдаётся за этот репозиторий.
        await self._save(1, 90, repository=_repository_ref(repository_slug="old-name"))
        # Другой репозиторий с тем же slug-префиксом.
        await self._save(2, 10, repository=_repository_ref(repository_id="repo-other", repository_slug="health-api-2"))

        async with _api_client(self.app) as client:
            response = await client.get(_HISTORY_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([point["score"] for point in response.json()["points"]], [70.0])

    async def test_unavailable_catalog_is_503_and_not_cached(self) -> None:
        app = create_app(analysis_store=self.store, repository_catalog=_UnavailableCatalog())

        async with _api_client(app) as client:
            response = await client.get(_HISTORY_URL)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")

    async def test_success_is_public_and_cacheable_for_five_minutes(self) -> None:
        await self._save(0, 80)

        async with _api_client(self.app) as client:
            response = await client.get(_HISTORY_URL)

        self.assertEqual(response.headers["access-control-allow-origin"], "*")
        self.assertEqual(response.headers["cache-control"], "public, max-age=300, s-maxage=300")


class InMemoryRecentSnapshotsTest(unittest.IsolatedAsyncioTestCase):
    async def test_lists_newest_first_within_limit(self) -> None:
        store = InMemoryAnalysisStore()
        start = datetime(2026, 9, 1, tzinfo=UTC)
        for index in range(3):
            await store.save(
                f"analysis-{index}",
                _execution(
                    repository=_repository_ref(),
                    analyzed_at=start + timedelta(days=index),
                    category_scores={category: 60 for category in CATEGORY_WEIGHTS},
                ),
            )

        recent = await store.list_recent_for_repository("repo-public", 2)

        self.assertEqual([item.analysis_id for item in recent], ["analysis-2", "analysis-1"])
        self.assertEqual(await store.list_recent_for_repository("repo-public", 0), ())
        self.assertEqual(await store.list_recent_for_repository("repo-missing", 5), ())


if __name__ == "__main__":
    unittest.main()
