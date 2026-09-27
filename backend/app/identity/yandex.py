"""Authorization Code + PKCE для Яндекс ID без передачи токенов в браузер."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import SplitResult, urlencode, urlsplit
from uuid import uuid4

import asyncpg
import httpx

_AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
_TOKEN_URL = "https://oauth.yandex.ru/token"
_USERINFO_URL = "https://login.yandex.ru/info"
_ALLOWED_OAUTH_HOSTS = frozenset({"oauth.yandex.ru", "oauth.yandex.com"})
_ALLOWED_PROFILE_HOSTS = frozenset({"login.yandex.ru", "login.yandex.com"})
_CALLBACK_PATH = "/api/v1/auth/yandex/callback"
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class YandexAuthenticationError(RuntimeError):
    """Пользовательский OAuth-поток не подтверждён или уже истёк."""


class YandexProviderError(RuntimeError):
    """Яндекс ID временно недоступен или вернул некорректный ответ."""


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Минимальный профиль локального пользователя без токенов и email."""

    id: str
    yandex_subject: str
    login: str

    def __post_init__(self) -> None:
        if not all((self.id.strip(), self.yandex_subject.strip(), self.login.strip())):
            raise ValueError("authenticated user fields must not be blank")


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    """Одноразовый PKCE verifier, привязанный к state и короткому TTL."""

    state_digest: str
    code_verifier: str
    expires_at: datetime


class YandexAuthStore(Protocol):
    """Постоянное состояние login-attempts и серверных пользовательских сессий."""

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def create_login_attempt(self, attempt: LoginAttempt) -> None: ...

    async def consume_login_attempt(self, state_digest: str, now: datetime) -> LoginAttempt | None: ...

    async def upsert_user(self, yandex_subject: str, login: str, now: datetime) -> AuthenticatedUser: ...

    async def create_session(
        self,
        session_digest: str,
        user_id: str,
        expires_at: datetime,
    ) -> None: ...

    async def get_session_user(self, session_digest: str, now: datetime) -> AuthenticatedUser | None: ...

    async def revoke_session(self, session_digest: str) -> None: ...


class InMemoryYandexAuthStore:
    """Эфемерное хранилище для HTTP-тестов и локальной разработки без PostgreSQL."""

    def __init__(self) -> None:
        self._attempts: dict[str, LoginAttempt] = {}
        self._users_by_subject: dict[str, AuthenticatedUser] = {}
        self._sessions: dict[str, tuple[str, datetime]] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_login_attempt(self, attempt: LoginAttempt) -> None:
        async with self._lock:
            self._attempts[attempt.state_digest] = attempt

    async def consume_login_attempt(self, state_digest: str, now: datetime) -> LoginAttempt | None:
        async with self._lock:
            attempt = self._attempts.pop(state_digest, None)
            if attempt is None or attempt.expires_at <= now:
                return None
            return attempt

    async def upsert_user(self, yandex_subject: str, login: str, now: datetime) -> AuthenticatedUser:
        del now
        async with self._lock:
            current = self._users_by_subject.get(yandex_subject)
            user = AuthenticatedUser(
                id=current.id if current is not None else f"user-{uuid4().hex}",
                yandex_subject=yandex_subject,
                login=login,
            )
            self._users_by_subject[yandex_subject] = user
            return user

    async def create_session(self, session_digest: str, user_id: str, expires_at: datetime) -> None:
        async with self._lock:
            self._sessions[session_digest] = (user_id, expires_at)

    async def get_session_user(self, session_digest: str, now: datetime) -> AuthenticatedUser | None:
        async with self._lock:
            session = self._sessions.get(session_digest)
            if session is None:
                return None
            user_id, expires_at = session
            if expires_at <= now:
                self._sessions.pop(session_digest, None)
                return None
            return next((user for user in self._users_by_subject.values() if user.id == user_id), None)

    async def revoke_session(self, session_digest: str) -> None:
        async with self._lock:
            self._sessions.pop(session_digest, None)


class PostgresYandexAuthStore:
    """PostgreSQL-хранилище одноразовых OAuth state и непрозрачных сессий."""

    def __init__(self, database_url: str) -> None:
        self._database_url = _normalize_database_url(database_url)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._database_url)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_login_attempt(self, attempt: LoginAttempt) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO yandex_login_attempts (state_digest, code_verifier, expires_at)
            VALUES ($1, $2, $3)
            """,
            attempt.state_digest,
            attempt.code_verifier,
            attempt.expires_at,
        )

    async def consume_login_attempt(self, state_digest: str, now: datetime) -> LoginAttempt | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM yandex_login_attempts WHERE expires_at <= $1", now)
            row = await connection.fetchrow(
                """
                DELETE FROM yandex_login_attempts
                WHERE state_digest = $1 AND expires_at > $2
                RETURNING state_digest, code_verifier, expires_at
                """,
                state_digest,
                now,
            )
        if row is None:
            return None
        return LoginAttempt(
            state_digest=str(row["state_digest"]),
            code_verifier=str(row["code_verifier"]),
            expires_at=row["expires_at"],
        )

    async def upsert_user(self, yandex_subject: str, login: str, now: datetime) -> AuthenticatedUser:
        row = await self._require_pool().fetchrow(
            """
            INSERT INTO app_users (id, yandex_subject, login, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $4)
            ON CONFLICT (yandex_subject) DO UPDATE
            SET login = EXCLUDED.login, updated_at = EXCLUDED.updated_at
            RETURNING id, yandex_subject, login
            """,
            f"user-{uuid4().hex}",
            yandex_subject,
            login,
            now,
        )
        if row is None:
            raise RuntimeError("could not save authenticated user")
        return AuthenticatedUser(
            id=str(row["id"]),
            yandex_subject=str(row["yandex_subject"]),
            login=str(row["login"]),
        )

    async def create_session(self, session_digest: str, user_id: str, expires_at: datetime) -> None:
        await self._require_pool().execute(
            """
            INSERT INTO app_sessions (session_digest, user_id, expires_at)
            VALUES ($1, $2, $3)
            """,
            session_digest,
            user_id,
            expires_at,
        )

    async def get_session_user(self, session_digest: str, now: datetime) -> AuthenticatedUser | None:
        pool = self._require_pool()
        async with pool.acquire() as connection:
            await connection.execute("DELETE FROM app_sessions WHERE expires_at <= $1", now)
            row = await connection.fetchrow(
                """
                SELECT user_record.id, user_record.yandex_subject, user_record.login
                FROM app_sessions AS session_record
                JOIN app_users AS user_record ON user_record.id = session_record.user_id
                WHERE session_record.session_digest = $1 AND session_record.expires_at > $2
                """,
                session_digest,
                now,
            )
        if row is None:
            return None
        return AuthenticatedUser(
            id=str(row["id"]),
            yandex_subject=str(row["yandex_subject"]),
            login=str(row["login"]),
        )

    async def revoke_session(self, session_digest: str) -> None:
        await self._require_pool().execute(
            "DELETE FROM app_sessions WHERE session_digest = $1",
            session_digest,
        )

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("Yandex auth store is not started")
        return self._pool


@dataclass(frozen=True, slots=True)
class YandexAuthSettings:
    """Явная конфигурация OAuth-клиента и серверной cookie-сессии."""

    client_id: str
    redirect_uri: str
    client_secret: str | None = None
    cookie_name: str = "repo_health_session"
    cookie_secure: bool = True
    session_ttl: timedelta = timedelta(days=14)
    attempt_ttl: timedelta = timedelta(minutes=10)
    success_redirect_path: str = "/me/repositories"
    authorize_url: str = _AUTHORIZE_URL
    token_url: str = _TOKEN_URL
    userinfo_url: str = _USERINFO_URL

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.redirect_uri.strip():
            raise ValueError("Yandex client_id and redirect_uri must not be blank")
        if not self.cookie_name.isascii() or not self.cookie_name.replace("_", "").isalnum():
            raise ValueError("cookie_name must contain only ASCII letters, digits and underscores")
        if self.session_ttl <= timedelta() or self.attempt_ttl <= timedelta():
            raise ValueError("Yandex auth TTL values must be positive")
        if not self.success_redirect_path.startswith("/") or self.success_redirect_path.startswith("//"):
            raise ValueError("success_redirect_path must be a local absolute path")
        _validate_redirect_uri(self.redirect_uri, cookie_secure=self.cookie_secure)
        _validate_https_url(self.authorize_url, _ALLOWED_OAUTH_HOSTS, "/authorize")
        _validate_https_url(self.token_url, _ALLOWED_OAUTH_HOSTS, "/token")
        _validate_https_url(self.userinfo_url, _ALLOWED_PROFILE_HOSTS, "/info")

    @property
    def callback_origin(self) -> str:
        """Канонический browser origin для CSRF-проверки cookie-сессии."""
        return _url_origin(urlsplit(self.redirect_uri))


HttpClientFactory = Callable[[], httpx.AsyncClient]
Clock = Callable[[], datetime]


class YandexOAuthClient:
    """Обменивает code на краткоживущий токен и сразу читает минимальный профиль."""

    def __init__(self, settings: YandexAuthSettings, *, http_client_factory: HttpClientFactory | None = None) -> None:
        self._settings = settings
        self._http_client_factory = http_client_factory or _new_http_client

    def authorization_url(self, state: str, code_verifier: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self._settings.client_id,
            "redirect_uri": self._settings.redirect_uri,
            "scope": "login:info",
            "state": state,
            "code_challenge": _pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        return f"{self._settings.authorize_url}?{urlencode(params)}"

    async def resolve_user(self, code: str, code_verifier: str) -> tuple[str, str]:
        token = await self._exchange_code(code, code_verifier)
        return await self._fetch_profile(token)

    async def _exchange_code(self, code: str, code_verifier: str) -> str:
        request: dict[str, object] = {
            "data": {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self._settings.client_id,
                "code_verifier": code_verifier,
            },
            "follow_redirects": False,
        }
        if self._settings.client_secret:
            request["auth"] = httpx.BasicAuth(self._settings.client_id, self._settings.client_secret)

        try:
            async with self._http_client_factory() as client:
                response = await client.post(self._settings.token_url, **request)
        except httpx.HTTPError as error:
            raise YandexProviderError("Yandex ID token request failed") from error

        payload = _response_object(response)
        token = payload.get("access_token") if not response.is_error else None
        if not isinstance(token, str) or not token.strip():
            raise YandexProviderError("Yandex ID did not issue an access token")
        return token

    async def _fetch_profile(self, token: str) -> tuple[str, str]:
        try:
            async with self._http_client_factory() as client:
                response = await client.get(
                    self._settings.userinfo_url,
                    headers={"Authorization": f"OAuth {token}"},
                    follow_redirects=False,
                )
        except httpx.HTTPError as error:
            raise YandexProviderError("Yandex ID profile request failed") from error

        payload = _response_object(response)
        subject = payload.get("id") if not response.is_error else None
        login = payload.get("login") if not response.is_error else None
        if not isinstance(subject, str | int) or not str(subject).strip() or not isinstance(login, str) or not login.strip():
            raise YandexProviderError("Yandex ID profile response is incomplete")
        return str(subject), login.strip()


class YandexAuthService:
    """Управляет коротким OAuth-потоком и длительной серверной сессией приложения."""

    def __init__(
        self,
        settings: YandexAuthSettings,
        store: YandexAuthStore,
        *,
        oauth_client: YandexOAuthClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.settings = settings
        self._store = store
        self._oauth_client = oauth_client or YandexOAuthClient(settings)
        self._clock = clock or _utc_now

    async def start(self) -> None:
        await self._store.start()

    async def close(self) -> None:
        await self._store.close()

    async def begin(self) -> str:
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        now = self._clock()
        await self._store.create_login_attempt(
            LoginAttempt(
                state_digest=_digest(state),
                code_verifier=verifier,
                expires_at=now + self.settings.attempt_ttl,
            )
        )
        return self._oauth_client.authorization_url(state, verifier)

    async def complete(self, code: str, state: str) -> tuple[str, AuthenticatedUser]:
        if (
            not code.strip()
            or not state.strip()
            or len(code) > 4096
            or len(state) > 1024
        ):
            raise YandexAuthenticationError("Yandex ID callback is incomplete")
        attempt = await self._store.consume_login_attempt(_digest(state), self._clock())
        if attempt is None:
            raise YandexAuthenticationError("Yandex ID login state is invalid or expired")

        subject, login = await self._oauth_client.resolve_user(code.strip(), attempt.code_verifier)
        now = self._clock()
        user = await self._store.upsert_user(subject, login, now)
        session_token = secrets.token_urlsafe(32)
        await self._store.create_session(
            _digest(session_token),
            user.id,
            now + self.settings.session_ttl,
        )
        return session_token, user

    async def cancel(self, state: str | None) -> None:
        """Одноразово инвалидирует начатый поток при отказе у провайдера."""

        if state and len(state) <= 1024:
            await self._store.consume_login_attempt(_digest(state), self._clock())

    async def current_user(self, session_token: str | None) -> AuthenticatedUser | None:
        if not session_token:
            return None
        return await self._store.get_session_user(_digest(session_token), self._clock())

    async def require_user(self, session_token: str | None) -> AuthenticatedUser:
        user = await self.current_user(session_token)
        if user is None:
            raise PermissionError("authenticated Yandex user required")
        return user

    async def logout(self, session_token: str | None) -> None:
        if session_token:
            await self._store.revoke_session(_digest(session_token))


def create_yandex_auth_service_from_environment(
    database_url: str | None,
) -> YandexAuthService | None:
    """Собирает сервис только при полной конфигурации, без тихого insecure fallback."""

    client_id = os.getenv("YANDEX_CLIENT_ID", "").strip()
    redirect_uri = os.getenv("YANDEX_REDIRECT_URI", "").strip()
    client_secret = os.getenv("YANDEX_CLIENT_SECRET", "").strip() or None
    configured_values = (client_id, redirect_uri)
    if not any(configured_values):
        return None
    if not all(configured_values):
        raise RuntimeError("YANDEX_CLIENT_ID and YANDEX_REDIRECT_URI must be configured together")

    secure_value = os.getenv("YANDEX_SESSION_COOKIE_SECURE", "true").strip().lower()
    if secure_value not in {"true", "false"}:
        raise RuntimeError("YANDEX_SESSION_COOKIE_SECURE must be true or false")
    settings = YandexAuthSettings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        cookie_secure=secure_value == "true",
    )
    store: YandexAuthStore = PostgresYandexAuthStore(database_url) if database_url else InMemoryYandexAuthStore()
    return YandexAuthService(settings, store)


def _new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0, follow_redirects=False, trust_env=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return encoded.rstrip(b"=").decode("ascii")


def _response_object(response: httpx.Response) -> dict[str, object]:
    if response.is_redirect:
        raise YandexProviderError("Yandex ID redirect is not allowed")
    try:
        payload = response.json()
    except ValueError as error:
        raise YandexProviderError("Yandex ID returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise YandexProviderError("Yandex ID response must be an object")
    return payload


def _validate_https_url(value: str, allowed_hosts: frozenset[str], path: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in allowed_hosts
        or parsed.port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Yandex OAuth endpoint must use an official HTTPS URL")


def _validate_redirect_uri(value: str, *, cookie_secure: bool) -> None:
    """Не позволяет конфигурации OAuth ослабить CSRF-границу приложения."""
    if value != value.strip() or _CONTROL_CHARACTERS.search(value):
        raise ValueError("Yandex redirect_uri must not contain control characters")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Yandex redirect_uri must be a valid callback URL") from error

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != _CALLBACK_PATH
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ValueError("Yandex redirect_uri must be a valid callback URL")

    if parsed.scheme == "http" and (cookie_secure or not _is_loopback_host(parsed.hostname)):
        raise ValueError("insecure Yandex redirect_uri is allowed only for local development")


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url_origin(parsed: SplitResult) -> str:
    """Возвращает origin URL без пути и канонических default-портов."""
    host = parsed.hostname
    if host is None:
        raise ValueError("URL has no host")

    host_value = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = "" if parsed.port in (None, default_port) else f":{parsed.port}"
    return f"{parsed.scheme}://{host_value}{port_suffix}"


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    return database_url


def _utc_now() -> datetime:
    return datetime.now(UTC)
