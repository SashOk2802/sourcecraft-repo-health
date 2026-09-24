"""Печатает безопасную сводку доступности AppSec для репозитория SourceCraft.

Пример:
    python scripts/probe_sourcecraft_appsec.py example-org/example-repo \\
        --src-bin /path/to/src

Команда не запускает сканирование и не изменяет репозиторий. Она использует уже
выполненную локальную авторизацию ``src auth login`` и не выводит сырые findings.
"""

from __future__ import annotations

import argparse
import json
import sys

from backend.app.integrations.sourcecraft_appsec_probe import SourceCraftAppSecCliProbe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Безопасно проверить доступность SAST, SCA и secret scanning в SourceCraft."
    )
    parser.add_argument("repository", help="репозиторий в формате OWNER/REPOSITORY")
    parser.add_argument(
        "--src-bin",
        default="src",
        help="путь к SourceCraft CLI (по умолчанию: src из PATH)",
    )
    parser.add_argument(
        "--timeout",
        default=20,
        type=int,
        help="максимальное ожидание каждого ответа в секундах (по умолчанию: 20)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        results = SourceCraftAppSecCliProbe(
            cli_binary=args.src_bin,
            timeout_seconds=args.timeout,
        ).probe_all(args.repository)
    except (TypeError, ValueError) as error:
        # Это только ошибка локального запуска; CLI-ответы и finding'и сюда не попадают.
        print(f"Ошибка аргументов: {error}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {"results": [result.as_dict() for result in results]},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if any(result.availability == "error" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
