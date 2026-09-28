from __future__ import annotations

import hashlib
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.identity import (
    InMemorySourceCraftConnectionStore,
    InMemoryYandexAuthStore,
    SourceCraftConnectionService,
    SourceCraftConnectionUnavailableError,
    SourceCraftTokenVault,
    YandexAuthService,
    YandexAuthSettings,
    create_sourcecraft_connection_service_from_environment,
)
from backend.app.integrations.sourcecraft import SourceCraftAuthenticationError
from backend.app.main import create_app


class SourceCraftConnectionApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.now = datetime(2026, 9, 28, 12, tzinfo=UTC)
        self.yandex_store = InMemoryYandexAuthStore()
        self.settings = YandexAuthSettings(
            client_id="client-42",
            redirect_uri="http://localhost:5173/api/v1/auth/yandex/callback",
            cookie_secure=False,
        )
        self.auth = YandexAuthService(
            self.settings,
            self.yandex_store,
            clock=lambda: self.now,
        )
        self.connection_store = InMemorySourceCraftConnectionStore()
        self.profile_client_factory = ProfileClientFactory()
        self.connections = SourceCraftConnectionService(
            SourceCraftTokenVault("Vx6o0Rf3l4EBNIIhIzrcXtizNeDbIDLPD--yxeCENAs="),
            self.connection_store,
            sourcecraft_client_factory=self.profile_client_factory,
            clock=lambda: self.now,
        )
        self.app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            yandex_auth_service=self.auth,
            sourcecraft_connection_service=self.connections,
        )
        self.alex = await self._create_session("yandex-alex", "alex")
        self.sasha = await self._create_session("yandex-sasha", "sasha")

    async def test_connect_encrypts_pat_and_never_returns_it_to_browser(self) -> None:
        secret = "sourcecraft-pat-secret-42"
        async with api_client(self.app) as client:
            self._set_session(client, self.alex)
            initially = await client.get("/api/v1/connections/sourcecraft")
            connected = await client.post(
                "/api/v1/connections/sourcecraft",
                json={"token": secret},
                headers=trusted_browser_headers(self.settings),
            )
            after = await client.get("/api/v1/connections/sourcecraft")

        stored = await self.connection_store.get(self.alex.user_id)
        self.assertEqual(initially.json(), {"connected": False, "login": None, "connectedAt": None})
        self.assertEqual(connected.status_code, 200)
        self.assertEqual(
            connected.json(),
            {"connected": True, "login": "artem", "connectedAt": "2026-09-28T12:00:00Z"},
        )
        self.assertEqual(after.json(), connected.json())
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertNotEqual(stored.encrypted_token, secret.encode())
        self.assertNotIn(secret, repr(stored))
        self.assertNotIn(secret, connected.text)
        self.assertNotIn(secret, after.text)
        self.assertEqual(self.profile_client_factory.tokens, [secret])
        self.assertEqual(self.profile_client_factory.paths, ["/user"])
        self.assertTrue(self.profile_client_factory.clients[0].closed)

    async def test_connection_is_visible_only_to_its_owner_and_disconnect_deletes_it(self) -> None:
        async with api_client(self.app) as client:
            self._set_session(client, self.alex)
            await client.post(
                "/api/v1/connections/sourcecraft",
                json={"token": "sourcecraft-pat-secret-42"},
                headers=trusted_browser_headers(self.settings),
            )

            self._set_session(client, self.sasha)
            other_user = await client.get("/api/v1/connections/sourcecraft")

            self._set_session(client, self.alex)
            disconnected = await client.delete(
                "/api/v1/connections/sourcecraft",
                headers=trusted_browser_headers(self.settings),
            )
            after = await client.get("/api/v1/connections/sourcecraft")

        self.assertEqual(other_user.json(), {"connected": False, "login": None, "connectedAt": None})
        self.assertEqual(disconnected.status_code, 204)
        self.assertEqual(after.json(), {"connected": False, "login": None, "connectedAt": None})
        self.assertIsNone(await self.connection_store.get(self.alex.user_id))

    async def test_rejected_or_malformed_token_is_safe_and_is_not_stored(self) -> None:
        rejected_token = "rejected-sourcecraft-pat"
        malformed_token = " has-whitespace "
        async with api_client(self.app) as client:
            self._set_session(client, self.alex)
            rejected = await client.post(
                "/api/v1/connections/sourcecraft",
                json={"token": rejected_token},
                headers=trusted_browser_headers(self.settings),
            )
            malformed = await client.post(
                "/api/v1/connections/sourcecraft",
                json={"token": malformed_token},
                headers=trusted_browser_headers(self.settings),
            )

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.json(), {"detail": "SourceCraft rejected the token."})
        self.assertNotIn(rejected_token, rejected.text)
        self.assertEqual(malformed.status_code, 422)
        self.assertNotIn(malformed_token.strip(), malformed.text)
        self.assertEqual(self.profile_client_factory.tokens, [rejected_token])
        self.assertIsNone(await self.connection_store.get(self.alex.user_id))

    async def test_invalid_pat_never_appears_in_validation_response(self) -> None:
        cases = (
            ("overlong", {"token": "long-pat-secret-marker-" + "x" * 4096}, "long-pat-secret-marker-"),
            ("wrong type", {"token": ["nested-pat-secret-marker"]}, "nested-pat-secret-marker"),
        )
        async with api_client(self.app) as client:
            self._set_session(client, self.alex)
            for name, payload, secret_marker in cases:
                with self.subTest(name=name):
                    response = await client.post(
                        "/api/v1/connections/sourcecraft",
                        json=payload,
                        headers=trusted_browser_headers(self.settings),
                    )
                    self.assertEqual(response.status_code, 422)
                    self.assertFalse(secret_marker in response.text, "PAT leaked in 422 response")
                    self.assertEqual(
                        response.json(),
                        {"detail": "SourceCraft token has an invalid format."},
                    )

        self.assertEqual(self.profile_client_factory.tokens, [])
        self.assertIsNone(await self.connection_store.get(self.alex.user_id))

    async def test_unrelated_endpoint_keeps_standard_validation_response(self) -> None:
        async with api_client(self.app) as client:
            response = await client.get("/api/v1/leaderboard?page=0")

        self.assertEqual(response.status_code, 422)
        self.assertIsInstance(response.json()["detail"], list)

    async def test_cross_site_request_is_rejected_before_token_is_checked(self) -> None:
        secret = "sourcecraft-pat-secret-42"
        async with api_client(self.app) as client:
            self._set_session(client, self.alex)
            response = await client.post(
                "/api/v1/connections/sourcecraft",
                json={"token": secret},
                headers={"origin": "https://attacker.example", "sec-fetch-site": "cross-site"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Cross-site request rejected."})
        self.assertEqual(self.profile_client_factory.tokens, [])
        self.assertIsNone(await self.connection_store.get(self.alex.user_id))

    async def test_connection_endpoint_is_hidden_when_encryption_key_is_not_configured(self) -> None:
        app = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            yandex_auth_service=self.auth,
        )
        async with api_client(app) as client:
            self._set_session(client, self.alex)
            response = await client.get("/api/v1/connections/sourcecraft")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            create_sourcecraft_connection_service_from_environment(None, {}),
            None,
        )
        with self.assertRaisesRegex(RuntimeError, "ENCRYPTION_KEY is invalid"):
            create_sourcecraft_connection_service_from_environment(
                None,
                {"SOURCECRAFT_CONNECTION_ENCRYPTION_KEY": "not-a-fernet-key"},
            )

    async def _create_session(self, subject: str, login: str) -> Session:
        user = await self.yandex_store.upsert_user(subject, login, self.now)
        token = f"session-{subject}"
        await self.yandex_store.create_session(
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            user.id,
            self.now + timedelta(days=1),
        )
        return Session(user.id, token)

    def _set_session(self, client: httpx.AsyncClient, session: Session) -> None:
        # ASGITransport не устанавливает cookie-domain автоматически: задаём тот
        # же браузерный заголовок, который сервер получает после Yandex login.
        client.headers["cookie"] = f"{self.settings.cookie_name}={session.token}"


class SourceCraftTokenVaultTest(unittest.TestCase):
    def test_ciphertext_round_trip_is_versioned_and_tamper_evident(self) -> None:
        secret = "sourcecraft-pat-secret-42"
        vault = SourceCraftTokenVault("Vx6o0Rf3l4EBNIIhIzrcXtizNeDbIDLPD--yxeCENAs=")

        encrypted = vault.encrypt(secret)

        self.assertTrue(encrypted.startswith(b"v1:"))
        self.assertNotEqual(encrypted, secret.encode())
        self.assertEqual(vault.decrypt(encrypted), secret)
        with self.assertRaises(SourceCraftConnectionUnavailableError) as error:
            vault.decrypt(encrypted[:-1] + b"x")
        self.assertNotIn(secret, str(error.exception))


class ProfileClientFactory:
    def __init__(self) -> None:
        self.tokens: list[str] = []
        self.paths: list[str] = []
        self.clients: list[ProfileClient] = []

    def __call__(self, token: str) -> ProfileClient:
        self.tokens.append(token)
        client = ProfileClient(token, self.paths)
        self.clients.append(client)
        return client


class ProfileClient:
    def __init__(self, token: str, paths: list[str]) -> None:
        self._token = token
        self._paths = paths
        self.closed = False

    def get_json(self, path: str) -> dict[str, object]:
        self._paths.append(path)
        if self._token == "rejected-sourcecraft-pat":
            raise SourceCraftAuthenticationError("SourceCraft denied access with HTTP 401")
        return {"username": "artem"}

    def close(self) -> None:
        self.closed = True


class Session:
    def __init__(self, user_id: str, token: str) -> None:
        self.user_id = user_id
        self.token = token


def trusted_browser_headers(settings: YandexAuthSettings) -> dict[str, str]:
    return {"origin": settings.callback_origin, "sec-fetch-site": "same-origin"}


@asynccontextmanager
async def api_client(app):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=False,
        ) as client:
            yield client
