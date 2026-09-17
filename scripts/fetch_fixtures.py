"""Снимает настоящие ответы SourceCraft API в файлы-фикстуры.

Инструмент разработки, а не часть сервиса. Он нужен, чтобы анализаторы категорий
можно было писать и проверять на зафиксированных данных, без обращения к сети.

Обход страниц реализован здесь временно: как только в SourceCraftClient появится
собственный метод пагинации, `fetch_all` должен перейти туда.

Использование:
    python scripts/fetch_fixtures.py discover
    python scripts/fetch_fixtures.py capture <org>/<repo> --label active
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from backend.app.integrations.sourcecraft import (
    SourceCraftClient,
    SourceCraftClientError,
    SourceCraftRateLimitError,
)

API_BASE = "https://api.sourcecraft.tech"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = PROJECT_ROOT / "backend" / "tests" / "fixtures"

# Максимум, который принимает API; меньшие значения только увеличивают число запросов.
PAGE_SIZE = 100

# Ограничение по умолчанию: фикстура должна быть представительной, но не бесконечной.
DEFAULT_MAX_PAGES = 5

TOKEN_VARIABLE = "SOURCECRAFT_TOKEN"


class FixtureError(RuntimeError):
    """Ошибка инструмента, текст которой можно показать пользователю."""


def read_token() -> str:
    """Берёт токен из переменной окружения, иначе из локального .env."""
    token = os.environ.get(TOKEN_VARIABLE, "").strip()
    if token:
        return token

    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == TOKEN_VARIABLE:
                token = value.strip().strip('"').strip("'")
                if token:
                    return token

    raise FixtureError(
        f"Не найден {TOKEN_VARIABLE}. Впишите персональный токен в файл .env "
        f"строкой {TOKEN_VARIABLE}=<токен> или задайте переменную окружения."
    )


def get_object(client: SourceCraftClient, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """Один GET-запрос, ожидающий JSON-объект.

    Ошибки клиента переводятся в FixtureError: текст клиента намеренно не содержит
    токена, поэтому его можно показывать и сохранять.
    """
    try:
        payload = client.get_json(path, params=params)
    except SourceCraftRateLimitError as error:
        retry = error.retry_after_seconds
        hint = f" Повторить можно через {retry} с." if retry else ""
        raise FixtureError(f"GET {path}: превышен лимит запросов.{hint}") from error
    except SourceCraftClientError as error:
        raise FixtureError(f"GET {path}: {error}") from error

    if not isinstance(payload, dict):
        raise FixtureError(f"GET {path}: ожидался JSON-объект, получен список.")
    return payload


def fetch_all(
    client: SourceCraftClient,
    path: str,
    items_key: str,
    *,
    params: dict[str, Any] | None = None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> dict[str, Any]:
    """Обходит страницы и возвращает конверт с данными и признаком полноты выборки.

    Признак `truncated` обязателен: без него нельзя отличить «в проекте нет задач»
    от «мы посмотрели только первую страницу».
    """
    query: dict[str, Any] = {"page_size": PAGE_SIZE, **(params or {})}
    items: list[Any] = []
    pages = 0
    truncated = False

    while True:
        payload = get_object(client, path, query)
        pages += 1
        items.extend(payload.get(items_key) or [])

        next_token = payload.get("next_page_token") or ""
        if not next_token:
            break
        if pages >= max_pages:
            truncated = True
            break
        query["page_token"] = next_token

    return {
        "_meta": {
            "endpoint": path,
            "params": {key: value for key, value in query.items() if key != "page_token"},
            "items_key": items_key,
            "fetched_at": datetime.now(UTC).isoformat(),
            "pages_fetched": pages,
            "truncated": truncated,
            "item_count": len(items),
        },
        "items": items,
    }


def write_fixture(label: str, name: str, payload: dict[str, Any]) -> Path:
    """Сохраняет конверт в backend/tests/fixtures/<label>/<name>.json."""
    directory = FIXTURES_ROOT / label
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{name}.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def cmd_discover(client: SourceCraftClient, args: argparse.Namespace) -> int:
    """Показывает публичные репозитории каталога, чтобы выбрать образцы для фикстур."""
    catalog = fetch_all(
        client,
        "/repos",
        "repositories",
        params={"sort_by": args.sort_by},
        max_pages=args.pages,
    )

    columns = f"{'org/repo':<42} {'язык':<12} {'обновлён':<20}"
    header = f"{columns} {'рейтинг':>8} {'issues':>7} {'MR':>5}"
    print(header)
    print("-" * len(header))

    for repository in catalog["items"]:
        org = (repository.get("organization") or {}).get("slug", "?")
        slug = repository.get("slug", "?")
        language = (repository.get("language") or {}).get("name") or "—"
        updated = (repository.get("last_updated") or "—")[:19]
        rating = (repository.get("rating") or {}).get("value")
        counters = repository.get("counters") or {}

        shown_rating = "—" if rating is None else f"{rating:.1f}"
        print(
            f"{org + '/' + slug:<42} {language:<12} {updated:<20} {shown_rating:>8} "
            f"{counters.get('issues', '—'):>7} {counters.get('pull_requests', '—'):>5}"
        )

    meta = catalog["_meta"]
    print(
        f"\nПоказано {meta['item_count']} репозиториев (страниц: {meta['pages_fetched']}, "
        f"обрезано: {'да' if meta['truncated'] else 'нет'})."
    )
    return 0


def cmd_capture(client: SourceCraftClient, args: argparse.Namespace) -> int:
    """Снимает все нужные категориям Activity и Issues данные одного репозитория."""
    if "/" not in args.repository:
        raise FixtureError("Репозиторий указывается как <org>/<repo>.")
    org, _, slug = args.repository.partition("/")
    base = f"/repos/{org}/{slug}"

    metadata = get_object(client, base, {})
    saved = [
        write_fixture(
            args.label,
            "repository",
            {
                "_meta": {"endpoint": base, "fetched_at": datetime.now(UTC).isoformat()},
                "item": metadata,
            },
        )
    ]

    # Открытые и закрытые задачи запрашиваются отдельно: так видно и объём, и динамику,
    # а фильтрация по статусу выполняется на стороне API.
    collections = [
        ("issues_open", f"{base}/issues", "issues", {"filter": "status=open"}),
        ("issues_closed", f"{base}/issues", "issues", {"filter": "status=closed"}),
        ("pulls", f"{base}/pulls", "pull_requests", {}),
        ("releases", f"{base}/releases", "releases", {}),
        ("contributors", f"{base}/contributors", "contributors", {}),
    ]

    for name, path, items_key, params in collections:
        try:
            payload = fetch_all(client, path, items_key, params=params, max_pages=args.max_pages)
        except FixtureError as error:
            # Неудача — тоже факт о репозитории; она сохраняется, а не прячется.
            payload = {
                "_meta": {
                    "endpoint": path,
                    "params": params,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "pages_fetched": 0,
                    "truncated": False,
                    "item_count": 0,
                    "error": str(error),
                },
                "items": [],
            }
            print(f"  ! {name}: {error}")

        saved.append(write_fixture(args.label, name, payload))
        meta = payload["_meta"]
        print(f"  {name}: {meta['item_count']} шт., страниц {meta['pages_fetched']}")

    print(f"\nСохранено в {FIXTURES_ROOT / args.label}:")
    for path in saved:
        print(f"  {path.relative_to(PROJECT_ROOT)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Сбор фикстур SourceCraft для анализаторов.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    discover = subcommands.add_parser("discover", help="показать публичные репозитории каталога")
    discover.add_argument(
        "--sort-by",
        default="-rating",
        help="поле сортировки API: -rating, created_at, -created_at",
    )
    discover.add_argument("--pages", type=int, default=1, help="сколько страниц запросить")

    capture = subcommands.add_parser("capture", help="снять фикстуры одного репозитория")
    capture.add_argument("repository", help="репозиторий в виде <org>/<repo>")
    capture.add_argument("--label", required=True, help="имя набора фикстур, например active")
    capture.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = read_token()
    except FixtureError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2

    with httpx.Client(base_url=API_BASE, timeout=30.0) as http_client:
        client = SourceCraftClient(token, http_client=http_client)
        try:
            if args.command == "discover":
                return cmd_discover(client, args)
            return cmd_capture(client, args)
        except FixtureError as error:
            print(f"Ошибка: {error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
