"""Экспортирует обезличенную AppSec-сводку из локального SourceCraft CLI.

Пример:
    python scripts/export_sourcecraft_appsec_snapshot.py OWNER/REPOSITORY \
        --output-dir /absolute/path/to/appsec-snapshots --src-bin /path/to/src

Пакетное обновление публичных репозиториев:
    python scripts/export_sourcecraft_appsec_snapshot.py \
        --repositories-file /absolute/path/public-repositories.txt \
        --output-dir /absolute/path/to/appsec-snapshots --appsec-env AppSecRead

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
from backend.app.integrations.sourcecraft_appsec_probe import (
    SourceCraftAppSecCliProbe,
    _validate_repository,
)
from backend.app.integrations.sourcecraft_appsec_snapshot import (
    DEFAULT_SNAPSHOT_READER_GID,
    SourceCraftAppSecCliSnapshotExporter,
    SourceCraftAppSecSnapshotExportError,
)

MAX_REPOSITORIES = 1_000
MAX_REPOSITORIES_FILE_BYTES = 128 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Создать безопасный AppSec snapshot для SourceCraft Repo Health."
    )
    repositories = parser.add_mutually_exclusive_group(required=True)
    repositories.add_argument(
        "repository",
        nargs="?",
        help="репозиторий в формате OWNER/REPOSITORY",
    )
    repositories.add_argument(
        "--repositories-file",
        type=Path,
        help=(
            "абсолютный путь к UTF-8 файлу с публичными репозиториями; "
            "по одному OWNER/REPOSITORY в строке"
        ),
    )
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


def _repositories_from_file(path: Path) -> tuple[str, ...]:
    if not path.is_absolute():
        raise ValueError("repositories file path must be absolute")
    with path.open("rb") as source:
        encoded = source.read(MAX_REPOSITORIES_FILE_BYTES + 1)
    if len(encoded) > MAX_REPOSITORIES_FILE_BYTES:
        raise ValueError("repositories file exceeds the safe size limit")
    text = encoded.decode("utf-8")

    repositories: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        repository = raw_line.strip()
        if not repository or repository.startswith("#"):
            continue
        _validate_repository(repository)
        if repository in seen:
            continue
        seen.add(repository)
        repositories.append(repository)
        if len(repositories) > MAX_REPOSITORIES:
            raise ValueError("repositories file contains too many repositories")
    if not repositories:
        raise ValueError("repositories file is empty")
    return tuple(repositories)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    is_batch = args.repositories_file is not None
    try:
        if is_batch:
            if not args.appsec_env:
                raise ValueError("batch refresh requires the complete AppSec source")
            repositories = _repositories_from_file(args.repositories_file)
        else:
            repositories = (args.repository,)
        options = {"cli_binary": args.src_bin, "timeout_seconds": args.timeout}
        probe = (
            SourceCraftAppSecApiProbe(appsec_environment=args.appsec_env, **options)
            if args.appsec_env
            else SourceCraftAppSecCliProbe(**options)
        )
        exporter = SourceCraftAppSecCliSnapshotExporter(
            probe,
            cli_binary=args.src_bin,
            timeout_seconds=args.timeout,
        )
    except (OSError, TypeError, ValueError, SourceCraftAppSecSnapshotExportError):
        # Не печатаем вывод SourceCraft CLI, содержимое snapshot и текст ОС:
        # все они могут включить пользовательский путь или данные AppSec.
        print("Не удалось создать безопасный AppSec snapshot.", file=sys.stderr)
        return 2

    succeeded = 0
    failed = 0
    destination: Path | None = None
    for repository in repositories:
        try:
            destination = exporter.export(
                repository,
                args.output_dir,
                reader_gid=args.reader_gid,
                public_only=is_batch,
            )
        except (OSError, TypeError, ValueError, SourceCraftAppSecSnapshotExportError):
            failed += 1
        else:
            succeeded += 1

    if is_batch:
        print(f"Безопасные AppSec snapshots обновлены: {succeeded}; ошибок: {failed}.")
    elif destination is not None:
        print(f"Безопасный AppSec snapshot создан: {destination.name}.")
    if failed:
        message = (
            "Не все безопасные AppSec snapshots удалось обновить."
            if is_batch
            else "Не удалось создать безопасный AppSec snapshot."
        )
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
