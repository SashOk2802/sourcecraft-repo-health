"""Безопасное персональное подключение SourceCraft.

SourceCraft PAT приходит от браузера только в момент подключения. В БД хранится
зашифрованный контейнер, а API и ``repr`` объектов никогда не содержат сам PAT.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import asyncpg
from cryptography.fernet import Fernet, InvalidToken

from backend.app.integrations.sourcecraft import (
    SourceCraftAuthenticationError,
    SourceCraftClient,
    SourceCraftClientError,
)
from backend.app.integrations.sourcecraft_repositories import (
    SourceCraftRepository,
    SourceCraftRepositoryCatalogClient,
)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MAX_TOKEN_LENGTH = 4096
_MAX_LOGIN_LENGTH = 256
_CREDENTIAL_LEASE_TTL = timedelta(minutes=10)


class SourceCraftConnectionError(RuntimeError):
    """Базовая безопасная ошибка персонального подключения SourceCraft."""


class SourceCraftConnectionRejectedError(SourceCraftConnectionError):
    """SourceCraft не принял предоставленный PAT."""


class SourceCraftConnectionUnavailableError(SourceCraftConnectionError):
    """Профиль SourceCraft временно нельзя проверить."""


@dataclass(frozen=True, slots=True)
class StoredSourceCraftConnection:
    """Запись в хранилище; зашифрованный PAT намеренно не печатается."""

    encrypted_token: bytes = field(repr=False)
    login: str = field(repr=False)
    connected_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SourceCraftConnectionStatus:
    """Безопасное состояние для ответа браузеру."""

    connected: bool
    login: str | None
    connected_at: datetime | None

    def __post_init__(self) -> None:
        if self.connected != (self.login is not None and self.connected_at is not None):
            raise ValueError("SourceCraft connection status fields are inconsistent")


@dataclass(frozen=True, slots=True)
class SourceCraftCredentialLease:
    """Короткоживущая in-memory capability без открытого PAT.

    Lease не сериализуется и не входит в AnalysisContext/AnalysisJob. Даже его
    ``repr`` скрывает ciphertext, чтобы диагностический вывод не превращался в
    переносимую копию пользовательского credential.
    """

    owner_subject: str
    expires_at: datetime
    encrypted_token: bytes = field(repr=False)

    def __post_init__(self) -> None:
        _validate_user_id(self.owner_subject)
        if self.expires_at.tzinfo is None:
            raise ValueError("SourceCraft credential lease expiry must be timezone-aware")
        if not isinstance(self.encrypted_token, bytes) or not self.encrypted_token:
            raise ValueError("SourceCraft credential lease is invalid")


class SourceCraftConnectionStore(Protocol):
    """Хранилище одной персональной записи на пользователя приложения."""

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def get(self, user_id: str) -> StoredSourceCraftConnection | None: ...

    async def upsert(
        self,
        user_id: str,
        encrypted_token: bytes,
        login: str,
        now: datetime,
    ) -> StoredSourceCraftConnection: ...

    async def delete(self, user_id: str) -> None: ...


class InMemorySourceCraftConnectionStore:
    """Эфемерное хранилище для HTTP-тестов и локальной разработки."""

    def __init__(self) -> None:
        self._connections: dict[str, StoredSourceCraftConnection] = {}
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def get(self, user_id: str) -> StoredSourceCraftConnection | None:
        async with self._lock:
            return self._connections.get(user_id)

    async def upsert(
        self,
        user_id: str,
        encrypted_token: bytes,
        login: str,
        now: datetime,
    ) -> StoredSourceCraftConnection:
        async with self._lock:
            existing = self._connections.get(user_id)
            record = StoredSourceCraftConnection(
                encrypted_token=encrypted_token,
                login=login,
                connected_at=existing.connected_at if existing is not None else now,
                updated_at=now,
            )
            self._connections[user_id] = record
            return record

    async def delete(self, user_id: str) -> None:
        async with self._lock:
            self._connections.pop(user_id, None)


class PostgresSourceCraftConnectionStore:
    """PostgreSQL-хранилище ciphertext; ключа шифрования здесь нет."""

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

    async def get(self, user_id: str) -> StoredSourceCraftConnection | None:
        row = await self._require_pool().fetchrow(
            """
            SELECT encrypted_token, sourcecraft_login, connected_at, updated_at
            FROM sourcecraft_connections
            WHERE user_id = $1
            """,
            user_id,
        )
        return _stored_connection_from_row(row) if row is not None else None

    async def upsert(
        self,
        user_id: str,
        encrypted_token: bytes,
        login: str,
        now: datetime,
    ) -> StoredSourceCraftConnection:
        row = await self._require_pool().fetchrow(
            """
            INSERT INTO sourcecraft_connections (
                user_id, encrypted_token, sourcecraft_login, connected_at, updated_at
            )
            VALUES ($1, $2, $3, $4, $4)
            ON CONFLICT (user_id) DO UPDATE
            SET encrypted_token = EXCLUDED.encrypted_token,
                sourcecraft_login = EXCLUDED.sourcecraft_login,
                updated_at = EXCLUDED.updated_at
            RETURNING encrypted_token, sourcecraft_login, connected_at, updated_at
            """,
            user_id,
            encrypted_token,
            login,
            now,
        )
        if row is None:
            raise RuntimeError("could not save SourceCraft connection")
        return _stored_connection_from_row(row)

    async def delete(self, user_id: str) -> None:
        await self._require_pool().execute(
            "DELETE FROM sourcecraft_connections WHERE user_id = $1",
            user_id,
        )

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("SourceCraft connection store is not started")
        return self._pool


class SourceCraftTokenVault:
    """Версионированный authenticated-encryption контейнер для PAT.

    Fernet добавляет authenticity к шифрованию: изменить ciphertext незаметно
    нельзя. Префикс версии позволяет когда-нибудь безопасно провести ротацию.
    """

    _PREFIX = b"v1:"

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (UnicodeEncodeError, ValueError) as error:
            raise ValueError("SourceCraft connection encryption key is invalid") from error

    def encrypt(self, token: str) -> bytes:
        return self._PREFIX + self._fernet.encrypt(token.encode("utf-8"))

    def decrypt(self, encrypted_token: bytes) -> str:
        if not encrypted_token.startswith(self._PREFIX):
            raise SourceCraftConnectionUnavailableError("stored SourceCraft connection cannot be read")
        try:
            return self._fernet.decrypt(encrypted_token[len(self._PREFIX) :]).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as error:
            raise SourceCraftConnectionUnavailableError(
                "stored SourceCraft connection cannot be read"
            ) from error


SourceCraftClientFactory = Callable[[str], SourceCraftClient]
Clock = Callable[[], datetime]


class SourceCraftConnectionService:
    """Проверяет и сохраняет PAT без раскрытия его в HTTP-контракте."""

    def __init__(
        self,
        vault: SourceCraftTokenVault,
        store: SourceCraftConnectionStore,
        *,
        sourcecraft_client_factory: SourceCraftClientFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._vault = vault
        self._store = store
        self._sourcecraft_client_factory = sourcecraft_client_factory or SourceCraftClient
        self._clock = clock or _utc_now

    async def start(self) -> None:
        await self._store.start()

    async def close(self) -> None:
        await self._store.close()

    async def status(self, user_id: str) -> SourceCraftConnectionStatus:
        record = await self._store.get(_validate_user_id(user_id))
        if record is None:
            return SourceCraftConnectionStatus(False, None, None)
        return SourceCraftConnectionStatus(True, record.login, record.connected_at)

    async def connect(self, user_id: str, token: str) -> SourceCraftConnectionStatus:
        safe_user_id = _validate_user_id(user_id)
        safe_token = _validate_token(token)
        login = await asyncio.to_thread(self._verify_token, safe_token)
        record = await self._store.upsert(
            safe_user_id,
            self._vault.encrypt(safe_token),
            login,
            self._clock(),
        )
        return SourceCraftConnectionStatus(True, record.login, record.connected_at)

    async def disconnect(self, user_id: str) -> None:
        await self._store.delete(_validate_user_id(user_id))

    async def issue_lease(self, user_id: str) -> SourceCraftCredentialLease | None:
        """Возвращает только ephemeral ciphertext capability для текущего subject."""

        safe_user_id = _validate_user_id(user_id)
        record = await self._store.get(safe_user_id)
        if record is None:
            return None
        return SourceCraftCredentialLease(
            owner_subject=safe_user_id,
            expires_at=self._clock() + _CREDENTIAL_LEASE_TTL,
            encrypted_token=record.encrypted_token,
        )

    def open_client(
        self,
        lease: SourceCraftCredentialLease,
        user_id: str,
    ) -> SourceCraftClient:
        """Расшифровывает PAT только на время создания короткоживущего API-клиента."""

        safe_user_id = _validate_user_id(user_id)
        if not isinstance(lease, SourceCraftCredentialLease):
            raise TypeError("SourceCraft credential lease is required")
        if not hmac.compare_digest(
            lease.owner_subject.encode("utf-8"),
            safe_user_id.encode("utf-8"),
        ):
            raise PermissionError("SourceCraft credential lease belongs to another user")
        if self._clock() >= lease.expires_at:
            raise SourceCraftConnectionUnavailableError("SourceCraft credential lease expired")

        token = self._vault.decrypt(lease.encrypted_token)
        try:
            return self._sourcecraft_client_factory(token)
        except (TypeError, ValueError):
            raise SourceCraftConnectionUnavailableError(
                "SourceCraft connection client could not be created"
            ) from None
        finally:
            token = ""

    async def list_repositories(
        self,
        user_id: str,
    ) -> tuple[SourceCraftRepository, ...] | None:
        """Читает личный каталог, не возвращая PAT за пределы сервиса."""

        record = await self._store.get(_validate_user_id(user_id))
        if record is None:
            return None
        return await asyncio.to_thread(
            self._list_repositories,
            record.encrypted_token,
        )

    def _list_repositories(
        self,
        encrypted_token: bytes,
    ) -> tuple[SourceCraftRepository, ...]:
        token = self._vault.decrypt(encrypted_token)
        try:
            try:
                client = self._sourcecraft_client_factory(token)
            except (TypeError, ValueError):
                raise SourceCraftConnectionUnavailableError(
                    "SourceCraft personal repository catalog is unavailable"
                ) from None
            try:
                return SourceCraftRepositoryCatalogClient(
                    client
                ).list_personal_repositories()
            except SourceCraftAuthenticationError as error:
                raise SourceCraftConnectionRejectedError(
                    "SourceCraft rejected the stored token"
                ) from error
            except SourceCraftClientError as error:
                raise SourceCraftConnectionUnavailableError(
                    "SourceCraft personal repository catalog is unavailable"
                ) from error
            finally:
                client.close()
        finally:
            token = ""

    def _verify_token(self, token: str) -> str:
        client = self._sourcecraft_client_factory(token)
        try:
            payload = client.get_json("/user")
        except SourceCraftAuthenticationError as error:
            raise SourceCraftConnectionRejectedError("SourceCraft rejected the token") from error
        except SourceCraftClientError as error:
            raise SourceCraftConnectionUnavailableError(
                "SourceCraft profile verification is unavailable"
            ) from error
        finally:
            client.close()

        if not isinstance(payload, dict):
            raise SourceCraftConnectionUnavailableError(
                "SourceCraft profile verification returned an invalid response"
            )
        login = payload.get("username")
        if (
            not isinstance(login, str)
            or not login.strip()
            or len(login) > _MAX_LOGIN_LENGTH
            or _CONTROL_CHARACTERS.search(login)
        ):
            raise SourceCraftConnectionUnavailableError(
                "SourceCraft profile verification returned an invalid response"
            )
        return login.strip()


def create_sourcecraft_connection_service_from_environment(
    database_url: str | None,
    environ: dict[str, str] | None = None,
) -> SourceCraftConnectionService | None:
    """Включает vault только при явном ключе; plaintext fallback запрещён."""

    values = environ if environ is not None else os.environ
    key = values.get("SOURCECRAFT_CONNECTION_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    try:
        vault = SourceCraftTokenVault(key)
    except ValueError as error:
        raise RuntimeError("SOURCECRAFT_CONNECTION_ENCRYPTION_KEY is invalid") from error
    store: SourceCraftConnectionStore = (
        PostgresSourceCraftConnectionStore(database_url)
        if database_url
        else InMemorySourceCraftConnectionStore()
    )
    return SourceCraftConnectionService(vault, store)


def _validate_token(token: str) -> str:
    if not isinstance(token, str):
        raise TypeError("SourceCraft token must be a string")
    value = token.strip()
    if (
        not value
        or value != token
        or len(value) > _MAX_TOKEN_LENGTH
        or _CONTROL_CHARACTERS.search(value)
        or any(character.isspace() for character in value)
    ):
        raise ValueError("SourceCraft token has an invalid format")
    return value


def _validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 256:
        raise ValueError("authenticated user id is invalid")
    return user_id


def _normalize_database_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _stored_connection_from_row(row: asyncpg.Record) -> StoredSourceCraftConnection:
    encrypted_token = row["encrypted_token"]
    if not isinstance(encrypted_token, bytes) or not encrypted_token:
        raise RuntimeError("stored SourceCraft connection is invalid")
    return StoredSourceCraftConnection(
        encrypted_token=encrypted_token,
        login=str(row["sourcecraft_login"]),
        connected_at=row["connected_at"],
        updated_at=row["updated_at"],
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
