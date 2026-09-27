"""Проверяет GET /me/repositories без обращения к SourceCraft и Яндекс ID."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import patch

import httpx

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.integrations.sourcecraft_public_catalog import PublicRepositoryCatalog
from backend.app.integrations.sourcecraft_repositories import SourceCraftRepository
from backend.app.integrations.sourcecraft_repository import SourceCraftRepositoryUnavailableError
from backend.app.main import create_app


class MyRepositoriesApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_sorted_public_repository_projection_for_authenticated_user(self) -> None:
        catalog = StaticCatalog(
            (
                repository("repository-z", "team", "zeta", language=None, is_empty=True),
                repository("repository-a", "alpha", "api"),
            )
        )
        app = create_test_app(repository_catalog=catalog)

        async with api_client(app) as client:
            response = await client.get("/api/v1/me/repositories", headers=authenticated_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "repositories": [
                    {
                        "id": "repository-a",
                        "organizationSlug": "alpha",
                        "repositorySlug": "api",
                        "name": "alpha/api",
                        "url": "https://sourcecraft.dev/alpha/api",
                        "defaultBranch": "main",
                        "language": "Python",
                        "isEmpty": False,
                    },
                    {
                        "id": "repository-z",
                        "organizationSlug": "team",
                        "repositorySlug": "zeta",
                        "name": "team/zeta",
                        "url": "https://sourcecraft.dev/team/zeta",
                        "defaultBranch": None,
                        "language": None,
                        "isEmpty": True,
                    },
                ],
                "total": 2,
            },
        )
        self.assertEqual(catalog.calls, 1)

    async def test_requires_yandex_session_before_reading_catalog(self) -> None:
        catalog = StaticCatalog((repository("repository-a", "alpha", "api"),))
        app = create_test_app(repository_catalog=catalog)

        async with api_client(app) as client:
            response = await client.get("/api/v1/me/repositories")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Authentication required."})
        self.assertEqual(catalog.calls, 0)

    async def test_returns_service_unavailable_for_missing_or_failed_catalog_without_secret(
        self,
    ) -> None:
        with patch("backend.app.main._create_default_public_repository_catalog", return_value=None):
            unconfigured_app = create_test_app(repository_catalog=None)
        failed_catalog = FailingCatalog("sourcecraft-token-must-not-leak")
        failed_app = create_test_app(repository_catalog=failed_catalog)

        async with api_client(unconfigured_app) as client:
            unconfigured = await client.get(
                "/api/v1/me/repositories",
                headers=authenticated_headers(),
            )
        async with api_client(failed_app) as client:
            failed = await client.get(
                "/api/v1/me/repositories",
                headers=authenticated_headers(),
            )

        self.assertEqual(unconfigured.status_code, 503)
        self.assertEqual(
            unconfigured.json(),
            {"detail": "SourceCraft repository catalog is not configured."},
        )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(
            failed.json(),
            {"detail": "SourceCraft repository catalog is unavailable."},
        )
        self.assertNotIn("sourcecraft-token-must-not-leak", failed.text)

    async def test_rejects_catalog_that_attempts_to_return_nonpublic_repository(self) -> None:
        catalog = StaticCatalog(
            (repository("private-id", "team", "private", visibility="private"),)
        )
        app = create_test_app(repository_catalog=catalog)

        async with api_client(app) as client:
            response = await client.get("/api/v1/me/repositories", headers=authenticated_headers())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"detail": "SourceCraft repository catalog is unavailable."},
        )


@dataclass(frozen=True, slots=True)
class User:
    id: str
    login: str


@dataclass(frozen=True, slots=True)
class AuthSettings:
    cookie_name: str = "repo_health_session"


class FakeYandexAuth:
    def __init__(self) -> None:
        self.settings = AuthSettings()
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.closed = True

    async def require_user(self, session_token: str | None) -> User:
        if session_token != "valid-session":
            raise PermissionError("session is missing")
        return User(id="user-42", login="alex")


class StaticCatalog(PublicRepositoryCatalog):
    def __init__(self, repositories: tuple[SourceCraftRepository, ...]) -> None:
        self._repositories = repositories
        self.calls = 0

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        self.calls += 1
        return self._repositories


class FailingCatalog(PublicRepositoryCatalog):
    def __init__(self, secret: str) -> None:
        self._secret = secret

    async def list_repositories(self) -> tuple[SourceCraftRepository, ...]:
        raise SourceCraftRepositoryUnavailableError(f"SourceCraft failed for {self._secret}")


def create_test_app(*, repository_catalog: PublicRepositoryCatalog | None):
    return create_app(
        analysis_store=InMemoryAnalysisStore(),
        job_store=InMemoryAnalysisJobStore(),
        yandex_auth_service=FakeYandexAuth(),  # type: ignore[arg-type]
        repository_catalog=repository_catalog,
    )


def repository(
    repository_id: str,
    organization_slug: str,
    slug: str,
    *,
    visibility: str = "public",
    language: str | None = "Python",
    is_empty: bool = False,
) -> SourceCraftRepository:
    return SourceCraftRepository(
        id=repository_id,
        name=slug,
        organization_slug=organization_slug,
        slug=slug,
        default_branch="" if is_empty else "main",
        visibility=visibility,  # type: ignore[arg-type]
        is_empty=is_empty,
        language=language,
        branch_count=0 if is_empty else 1,
        web_url=f"https://sourcecraft.dev/{organization_slug}/{slug}",
    )


def authenticated_headers() -> dict[str, str]:
    return {"cookie": "repo_health_session=valid-session"}


@asynccontextmanager
async def api_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
