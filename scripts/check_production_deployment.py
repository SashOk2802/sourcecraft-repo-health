"""Проверяет production-стенд без авторизации и вывода содержимого ответов."""

from __future__ import annotations

import argparse
import ipaddress
import json
import ssl
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 10


class DeploymentSmokeError(RuntimeError):
    """Ошибка smoke-проверки с безопасным стабильным кодом."""


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes
    truncated: bool = False


HttpFetcher = Callable[[str, int], HttpResult]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or "\x00" in value:
        raise DeploymentSmokeError("base_url_invalid")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise DeploymentSmokeError("base_url_invalid")
    try:
        port = parsed.port
    except ValueError as error:
        raise DeploymentSmokeError("base_url_invalid") from error
    if port not in {None, 443}:
        raise DeploymentSmokeError("base_url_invalid")

    hostname = parsed.hostname.casefold()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise DeploymentSmokeError("base_url_invalid")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise DeploymentSmokeError("base_url_invalid")
    return urlunsplit(("https", parsed.netloc, "", "", ""))


def _fetch(url: str, timeout_seconds: int) -> HttpResult:
    opener = build_opener(
        ProxyHandler({}),
        HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    request = Request(
        url,
        headers={
            "Accept": "application/json,text/html;q=0.9,*/*;q=0.1",
            "User-Agent": "sourcecraft-repo-health-production-smoke/1",
        },
        method="GET",
    )
    try:
        response = opener.open(request, timeout=timeout_seconds)
    except HTTPError as error:
        response = error
    except (OSError, URLError) as error:
        raise DeploymentSmokeError("request_failed") from error

    try:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        headers = {name.casefold(): value for name, value in response.headers.items()}
        return HttpResult(
            status=int(response.status),
            headers=headers,
            body=body[:MAX_RESPONSE_BYTES],
            truncated=len(body) > MAX_RESPONSE_BYTES,
        )
    except (OSError, ValueError) as error:
        raise DeploymentSmokeError("request_failed") from error
    finally:
        response.close()


def check_production_deployment(
    base_url: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    fetcher: HttpFetcher = _fetch,
) -> tuple[str, ...]:
    """Возвращает стабильные коды нарушений без URL и содержимого ответов."""

    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30:
        raise DeploymentSmokeError("timeout_invalid")
    base = _base_url(base_url)
    failures: list[str] = []

    def get(path: str, code: str) -> HttpResult | None:
        try:
            return fetcher(f"{base}{path}", timeout_seconds)
        except Exception:  # noqa: BLE001 - граница удаляет URL и текст сетевой ошибки.
            failures.append(f"{code}_request_failed")
            return None

    root = get("/", "root")
    if root is not None:
        _check_root(root, failures)

    vite_client = get("/@vite/client", "vite_client")
    if vite_client is not None and _is_javascript_source(vite_client):
        failures.append("vite_client_exposed")

    source_entry = get("/src/main.tsx", "source_entry")
    if source_entry is not None and _is_javascript_source(source_entry):
        failures.append("source_tree_exposed")

    health = get("/api/v1/health", "api_health")
    if health is not None:
        _check_health(health, failures)

    methodology = get("/api/v1/methodology", "methodology")
    _check_public_json(methodology, "methodology", failures)

    leaderboard = get("/api/v1/leaderboard", "leaderboard")
    _check_public_json(leaderboard, "leaderboard", failures)

    for path, code in (
        ("/api/v1/me", "personal_session"),
        ("/api/v1/me/repositories", "personal_repositories"),
    ):
        response = get(path, code)
        if response is not None and response.status != 401:
            failures.append(f"{code}_not_protected")
        elif response is not None and "no-store" not in response.headers.get(
            "cache-control", ""
        ).casefold():
            failures.append(f"{code}_cacheable")

    return tuple(dict.fromkeys(failures))


def _check_root(response: HttpResult, failures: list[str]) -> None:
    if response.status != 200:
        failures.append("root_status_invalid")
        return
    if response.truncated:
        failures.append("root_response_too_large")
        return
    content_type = response.headers.get("content-type", "").casefold()
    if "text/html" not in content_type:
        failures.append("root_content_type_invalid")
    text = response.body.decode("utf-8", errors="replace").casefold()
    if "/@vite/client" in text or "/src/main.tsx" in text:
        failures.append("vite_development_html")

    required_headers = {
        "content-security-policy": "root_csp_missing",
        "strict-transport-security": "root_hsts_missing",
        "referrer-policy": "root_referrer_policy_missing",
        "permissions-policy": "root_permissions_policy_missing",
    }
    for header, failure in required_headers.items():
        if not response.headers.get(header, "").strip():
            failures.append(failure)
    if response.headers.get("x-content-type-options", "").casefold() != "nosniff":
        failures.append("root_nosniff_missing")


def _is_javascript_source(response: HttpResult) -> bool:
    if response.status != 200:
        return False
    content_type = response.headers.get("content-type", "").casefold()
    if any(marker in content_type for marker in ("javascript", "typescript")):
        return True
    prefix = response.body[:4096].decode("utf-8", errors="replace").casefold()
    return "@vite/client" in prefix or "from \"/node_modules/" in prefix


def _check_health(response: HttpResult, failures: list[str]) -> None:
    if response.status != 200 or response.truncated:
        failures.append("api_health_invalid")
        return
    try:
        payload = json.loads(response.body)
    except (TypeError, json.JSONDecodeError):
        failures.append("api_health_invalid")
        return
    if payload != {"status": "ok"}:
        failures.append("api_health_invalid")


def _check_public_json(
    response: HttpResult | None,
    code: str,
    failures: list[str],
) -> None:
    if response is None:
        return
    if response.status != 200:
        failures.append(f"{code}_unavailable")
        return
    if "application/json" not in response.headers.get("content-type", "").casefold():
        failures.append(f"{code}_content_type_invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Проверить production-стенд Repo Health.")
    parser.add_argument("base_url", help="корневой HTTPS URL стенда")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="таймаут одного запроса в секундах (1-30, по умолчанию 10)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        failures = check_production_deployment(args.base_url, timeout_seconds=args.timeout)
    except Exception:  # noqa: BLE001 - CLI не должен печатать URL или исходную ошибку.
        print("Production smoke не запущен: неверная конфигурация.", file=sys.stderr)
        return 2
    if failures:
        print(f"Production smoke failed: {', '.join(failures)}.", file=sys.stderr)
        return 1
    print("Production smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
