"""Локальная идентичность пользователей и вход через Яндекс ID."""

from backend.app.identity.yandex import (
    AuthenticatedUser,
    InMemoryYandexAuthStore,
    PostgresYandexAuthStore,
    YandexAuthenticationError,
    YandexAuthService,
    YandexAuthSettings,
    YandexProviderError,
    create_yandex_auth_service_from_environment,
)

__all__ = [
    "AuthenticatedUser",
    "InMemoryYandexAuthStore",
    "PostgresYandexAuthStore",
    "YandexAuthService",
    "YandexAuthSettings",
    "YandexAuthenticationError",
    "YandexProviderError",
    "create_yandex_auth_service_from_environment",
]
