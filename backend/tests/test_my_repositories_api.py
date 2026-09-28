"""Проверяет GET /me/repositories без обращения к SourceCraft и Яндекс ID."""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import patch

import httpx
from cryptography.fernet import Fernet

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.identity import (
    InMemorySourceCraftConnectionStore,
    SourceCraftConnectionService,
    SourceCraftTokenVault,
)
from backend.app.integrations.sourcecraft import (
    SourceCraftAuthenticationError,
    SourceCraftNetworkError,
)
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
                        "visibility": "public",
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
                        "visibility": "public",
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

    async def test_personal_catalog_isolated_by_authenticated_user(self) -> None:
        store = InMemorySourceCraftConnectionStore()
        factory = PersonalCatalogClientFactory()
        connections = SourceCraftConnectionService(
            SourceCraftTokenVault(Fernet.generate_key().decode("ascii")),
            store,
            sourcecraft_client_factory=factory,  # type: ignore[arg-type]
            clock=lambda: datetime(2026, 9, 28, 12, tzinfo=UTC),
        )
        await connections.connect("user-42", "owner-personal-token")
        await connections.connect("user-99", "other-personal-token")
        app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            yandex_auth_service=FakeYandexAuth(),  # type: ignore[arg-type]
            sourcecraft_connection_service=connections,
            repository_catalog=StaticCatalog((repository("public-id", "public", "fallback"),)),
        )

        async with api_client(app) as client:
            owner = await client.get(
                "/api/v1/me/repositories",
                headers=authenticated_headers(),
            )
            other = await client.get(
                "/api/v1/me/repositories",
                headers={"cookie": "repo_health_session=other-session"},
            )

        self.assertEqual(owner.status_code, 200)
        self.assertEqual(other.status_code, 200)
        self.assertEqual(owner.json()["repositories"][0]["id"], "owner-private-id")
        self.assertEqual(owner.json()["repositories"][0]["visibility"], "private")
        self.assertEqual(other.json()["repositories"][0]["id"], "other-internal-id")
        self.assertEqual(other.json()["repositories"][0]["visibility"], "internal")
        self.assertNotIn("other-internal-id", owner.text)
        self.assertNotIn("owner-private-id", other.text)
        self.assertNotIn("owner-personal-token", owner.text)
        self.assertNotIn("other-personal-token", other.text)
        self.assertEqual(
            factory.catalog_tokens,
            ["owner-personal-token", "other-personal-token"],
        )

    async def test_revoked_or_corrupt_personal_connection_fails_without_fallback(self) -> None:
        store = InMemorySourceCraftConnectionStore()
        factory = PersonalCatalogClientFactory()
        connections = SourceCraftConnectionService(
            SourceCraftTokenVault(Fernet.generate_key().decode("ascii")),
            store,
            sourcecraft_client_factory=factory,  # type: ignore[arg-type]
        )
        await connections.connect("user-42", "revoked-personal-token")
        factory.revoked_tokens.add("revoked-personal-token")
        app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            yandex_auth_service=FakeYandexAuth(),  # type: ignore[arg-type]
            sourcecraft_connection_service=connections,
            repository_catalog=StaticCatalog((repository("public-id", "public", "fallback"),)),
        )

        async with api_client(app) as client:
            revoked = await client.get(
                "/api/v1/me/repositories",
                headers=authenticated_headers(),
            )
            await store.upsert("user-42", b"v1:corrupt", "owner", datetime.now(UTC))
            corrupt = await client.get(
                "/api/v1/me/repositories",
                headers=authenticated_headers(),
            )

        self.assertEqual(revoked.status_code, 401)
        self.assertEqual(
            revoked.json(),
            {"detail": "SourceCraft connection must be renewed."},
        )
        self.assertEqual(corrupt.status_code, 503)
        self.assertEqual(
            corrupt.json(),
            {"detail": "SourceCraft repository catalog is unavailable."},
        )
        self.assertNotIn("revoked-personal-token", revoked.text + corrupt.text)


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
        users = {
            "valid-session": User(id="user-42", login="alex"),
            "other-session": User(id="user-99", login="sasha"),
        }
        try:
            return users[session_token]
        except KeyError:
            raise PermissionError("session is missing") from None


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


class PersonalCatalogClientFactory:
    def __init__(self) -> None:
        self.catalog_tokens: list[str] = []
        self.revoked_tokens: set[str] = set()

    def __call__(self, token: str):
        return PersonalCatalogClient(token, self)


class PersonalCatalogClient:
    def __init__(self, token: str, factory: PersonalCatalogClientFactory) -> None:
        self._token = token
        self._factory = factory
        self.closed = False

    def get_json(self, path: str) -> dict[str, object]:
        if path != "/user":
            raise AssertionError(path)
        return {"username": "connected-user"}

    def get_paginated_objects(
        self,
        path: str,
        *,
        items_field: str,
        params: object = None,
        page_size: int = 100,
        max_pages: int = 100,
    ) -> list[dict[str, object]]:
        if self._token in self._factory.revoked_tokens:
            raise SourceCraftAuthenticationError("denied", status_code=401)
        if path != "/me/repos" or items_field != "repositories":
            raise SourceCraftNetworkError("unexpected request")
        if params is not None or page_size != 100 or max_pages != 100:
            raise SourceCraftNetworkError("unexpected pagination")
        self._factory.catalog_tokens.append(self._token)
        if self._token == "owner-personal-token":
            return [personal_repository_payload("owner-private-id", "owner-repo", "private")]
        if self._token == "other-personal-token":
            return [personal_repository_payload("other-internal-id", "other-repo", "internal")]
        raise SourceCraftNetworkError("unknown test credential")

    def close(self) -> None:
        self.closed = True


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


def personal_repository_payload(
    repository_id: str,
    slug: str,
    visibility: str,
) -> dict[str, object]:
    return {
        "id": repository_id,
        "name": slug,
        "organization": {"id": "personal-org-id", "slug": "personal-org"},
        "slug": slug,
        "default_branch": "main",
        "visibility": visibility,
        "is_empty": False,
        "language": {"name": "Python"},
        "counters": {"branches": "1"},
        "web_url": f"https://sourcecraft.dev/personal-org/{slug}",
    }


def authenticated_headers() -> dict[str, str]:
    return {"cookie": "repo_health_session=valid-session"}


@asynccontextmanager
async def api_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
