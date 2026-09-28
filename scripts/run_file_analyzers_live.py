"""Одноразовый прогон Documentation и Code health по живому репозиторию SourceCraft."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.analyzers import code_health, documentation
from backend.app.contracts import AnalysisContext, CategoryResult, RepositoryRef
from backend.app.integrations.git_repository import (
    GitCloneError,
    LocalGitRepository,
    sourcecraft_git_http_authorization,
)
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def split_repository_slug(value: str) -> tuple[str, str] | None:
    """Возвращает org и slug либо None, если аргумент не вида org/repo."""

    organization, separator, slug = value.partition("/")
    if not separator or not organization or not slug or "/" in slug:
        return None
    return organization, slug


def analyze_workspace(
    repository: LocalGitRepository,
    context: AnalysisContext,
) -> tuple[CategoryResult, CategoryResult]:
    """Считает Documentation и Code health по уже подготовленному клону."""

    documentation_result = documentation.evaluate(context, documentation.collect(repository))
    code_health_result = code_health.evaluate(context, code_health.collect(repository))
    return documentation_result, code_health_result


def read_token() -> str:
    token = os.environ.get("SOURCECRAFT_TOKEN", "").strip()
    if token:
        return token
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("SOURCECRAFT_TOKEN="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    raise SystemExit("SOURCECRAFT_TOKEN не найден в окружении или .env")


def remote_head_sha(repo_url: str, token: str) -> str:
    """SHA текущего HEAD. Токен уходит только в заголовок git и не печатается."""

    command = ["git", "ls-remote", repo_url, "HEAD"]
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.extraheader"
    env["GIT_CONFIG_VALUE_0"] = sourcecraft_git_http_authorization(token)
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Без цепочки: текст CalledProcessError не должен попасть в CLI-вывод.
        raise SystemExit("Не удалось прочитать SHA ветки по умолчанию.")
    fields = completed.stdout.split()
    if not fields:
        raise SystemExit("У репозитория нет SHA ветки по умолчанию.")
    return fields[0]


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv if argv is None else argv
    parsed = split_repository_slug(arguments[1]) if len(arguments) == 2 else None
    if parsed is None:
        print(
            "Использование: python scripts/run_file_analyzers_live.py org/repo",
            file=sys.stderr,
        )
        return 2

    organization, slug = parsed
    client: SourceCraftClient | None = None
    workspace: LocalGitRepository | None = None
    try:
        # На main у SourceCraftClient нет отдельного accessor: тот же секрет,
        # что ушёл в API-клиент, передаётся в git строкой.
        token = read_token()
        client = SourceCraftClient(token)
        payload = client.get_json(f"/repos/{organization}/{slug}")
        if not isinstance(payload, dict):
            print("метаданные репозитория — не объект", file=sys.stderr)
            return 1
        web_url = payload.get("web_url")
        repository = RepositoryRef(
            id=str(payload.get("id") or ""),
            organization_slug=organization,
            repository_slug=slug,
            web_url=web_url if isinstance(web_url, str) else None,
        )
        analyzed_at = datetime.now(UTC)
        repo_url = SourceCraftClient.resolve_git_clone_url(
            organization,
            slug,
            repository.web_url,
        )
        context = AnalysisContext(
            repository=repository,
            commit_sha=remote_head_sha(repo_url, token),
            analyzed_at=analyzed_at,
            period_start=analyzed_at - timedelta(days=180),
            period_end=analyzed_at,
        )
        workspace = LocalGitRepository(
            repo_url,
            ref=context.commit_sha,
            auth_token=token,
        )
        workspace.clone()
        documentation_result, code_health_result = analyze_workspace(workspace, context)
    except SourceCraftClientError as error:
        print(f"Ошибка API: {error}", file=sys.stderr)
        return 1
    except GitCloneError as error:
        print(f"Ошибка git: {error}", file=sys.stderr)
        return 1
    finally:
        if workspace is not None:
            workspace.cleanup()
        if client is not None:
            client.close()

    print(f"репозиторий: {organization}/{slug}")
    print(f"commit: {context.commit_sha}")
    for result in (documentation_result, code_health_result):
        print()
        print(f"category={result.category}")
        print(f"status={result.status.value}")
        print(f"score={result.score}")
        print(f"summary={result.summary}")
        print(f"reason={result.reason}")
        for metric in result.metrics:
            print(f"  metric {metric.code}: value={metric.value} score={metric.normalized_score}")
        print("recommendations:")
        for recommendation in result.recommendations:
            print(
                f"  [{recommendation.priority.value}] {recommendation.code}: "
                f"{recommendation.problem}"
            )
            print(f"      delta: {recommendation.expected_score_delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
