"""Одноразовый прогон категории Issues по живому репозиторию SourceCraft."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.analyzers.issues import collect, evaluate
from backend.app.contracts import AnalysisContext, RepositoryRef
from backend.app.integrations.sourcecraft import SourceCraftClient, SourceCraftClientError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read_token() -> str:
    token = os.environ.get("SOURCECRAFT_TOKEN", "").strip()
    if token:
        return token
    for raw in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("SOURCECRAFT_TOKEN="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    raise SystemExit("SOURCECRAFT_TOKEN не найден в .env")


def main() -> int:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        print("Использование: python scripts/run_issues_live.py org/repo", file=sys.stderr)
        return 2

    org, _, slug = sys.argv[1].partition("/")
    client = SourceCraftClient(read_token())
    try:
        payload = client.get_json(f"/repos/{org}/{slug}")
        if not isinstance(payload, dict):
            raise SystemExit("метаданные репозитория — не объект")
        repository = RepositoryRef(
            id=str(payload.get("id") or ""),
            organization_slug=org,
            repository_slug=slug,
            web_url=payload.get("web_url"),
        )
        analyzed_at = datetime.now(UTC)
        context = AnalysisContext(
            repository=repository,
            commit_sha="0" * 40,
            analyzed_at=analyzed_at,
            period_start=analyzed_at - timedelta(days=180),
            period_end=analyzed_at,
        )
        facts = collect(client, repository)
        result = evaluate(facts, context)
    except SourceCraftClientError as error:
        print(f"Ошибка API: {error}", file=sys.stderr)
        return 1
    finally:
        client.close()

    counters = payload.get("counters") or {}
    print(f"репозиторий: {org}/{slug}")
    print(f"web_url: {repository.web_url}")
    print(f"last_updated: {payload.get('last_updated')}")
    print(f"counters.issues={counters.get('issues')}  MR={counters.get('pull_requests')}")
    print(f"собрано: open={len(facts.open_issues)} closed={len(facts.closed_issues)}")
    print(f"truncated: open={facts.open_truncated} closed={facts.closed_truncated}")
    print(f"errors: open={facts.open_error!r} closed={facts.closed_error!r}")
    print(f"skipped={facts.skipped_count}")
    print()
    print(f"status={result.status.value}")
    print(f"score={result.score}")
    print(f"summary={result.summary}")
    print(f"reason={result.reason}")
    for metric in result.metrics:
        print(
            f"  metric {metric.code}: value={metric.value} "
            f"score={metric.normalized_score} evidence={len(metric.evidence)}"
        )
        for item in metric.evidence[:3]:
            print(f"    - {item.summary} {item.url or ''}")
    print("recommendations:")
    for rec in result.recommendations:
        print(f"  [{rec.priority.value}] {rec.code}: {rec.problem}")
        print(f"      action: {rec.action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
