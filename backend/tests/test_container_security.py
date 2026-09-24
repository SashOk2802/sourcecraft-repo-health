"""Статические проверки безопасности Dockerfile'ов проекта."""

from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ContainerSecurityTest(unittest.TestCase):
    def test_backend_container_runs_as_named_non_root_user(self) -> None:
        dockerfile = _read("backend/Dockerfile")

        self.assertIn("groupadd --system app", dockerfile)
        self.assertIn("useradd --system --gid app", dockerfile)
        self.assertIn("chown -R app:app /app", dockerfile)
        self.assertEqual(_last_user(dockerfile), "app")

    def test_frontend_container_runs_as_named_non_root_user(self) -> None:
        dockerfile = _read("frontend/Dockerfile")

        self.assertIn("addgroup -S app", dockerfile)
        self.assertIn("adduser -S app -G app", dockerfile)
        self.assertIn("chown -R app:app /app", dockerfile)
        self.assertEqual(_last_user(dockerfile), "app")


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _last_user(dockerfile: str) -> str | None:
    users = [line.split(maxsplit=1)[1].strip() for line in dockerfile.splitlines() if line.startswith("USER ")]
    return users[-1] if users else None
