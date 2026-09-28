"""Экспортирует обезличенную AppSec-сводку из локального SourceCraft CLI.

Пример:
    python scripts/export_sourcecraft_appsec_snapshot.py OWNER/REPOSITORY \
        --output-dir /absolute/path/to/appsec-snapshots --src-bin /path/to/src

CLI использует уже выполненный ``src auth login`` только в процессе этой
команды. В файл не попадают токен, slug, пути файлов, правила, код или
значения секретов. Backend читает output-dir только как read-only volume.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Команду документируем как ``python scripts/...``. При таком запуске Python
# добавляет в sys.path только каталог scripts, поэтому явно добавляем корень
# проекта до импортов backend. Запуск ``python -m scripts...`` уже содержит
# корень и эту ветку не выполняет.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.integrations.sourcecraft_appsec_api import SourceCraftAppSecApiProbe
from backend.app.integrations.sourcecraft_appsec_probe import SourceCraftAppSecCliProbe
from backend.app.integrations.sourcecraft_appsec_snapshot import (
    DEFAULT_SNAPSHOT_READER_GID,
    SourceCraftAppSecCliSnapshotExporter,
    SourceCraftAppSecSnapshotExportError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Создать безопасный AppSec snapshot для SourceCraft Repo Health."
    )
    parser.add_argument("repository", help="репозиторий в формате OWNER/REPOSITORY")
    parser.add_argument(
        "--appsec-env",
        help="локальный профиль src для полного AppSec API (без него — ограниченный CLI-зонд)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="абсолютный каталог для read-only mount в backend",
    )
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
    parser.add_argument(
        "--reader-gid",
        default=DEFAULT_SNAPSHOT_READER_GID,
        type=int,
        help=(
            "GID backend-пользователя, которому разрешено только читать snapshot "
            f"(по умолчанию: {DEFAULT_SNAPSHOT_READER_GID})"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        options = {"cli_binary": args.src_bin, "timeout_seconds": args.timeout}
        probe = (
            SourceCraftAppSecApiProbe(appsec_environment=args.appsec_env, **options)
            if args.appsec_env
            else SourceCraftAppSecCliProbe(**options)
        )
        destination = SourceCraftAppSecCliSnapshotExporter(
            probe,
            cli_binary=args.src_bin,
            timeout_seconds=args.timeout,
        ).export(
            args.repository,
            args.output_dir,
            reader_gid=args.reader_gid,
        )
    except (OSError, TypeError, ValueError, SourceCraftAppSecSnapshotExportError):
        # Не печатаем вывод SourceCraft CLI, содержимое snapshot и текст ОС:
        # все они могут включить пользовательский путь или данные AppSec.
        print("Не удалось создать безопасный AppSec snapshot.", file=sys.stderr)
        return 2

    print(f"Безопасный AppSec snapshot создан: {destination.name}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
