"""Статические проверки безопасности Dockerfile'ов проекта."""

from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ContainerSecurityTest(unittest.TestCase):
    def test_base_images_are_pinned_to_immutable_digests(self) -> None:
        backend_dockerfile = _read("backend/Dockerfile")
        frontend_dockerfile = _read("frontend/Dockerfile")

        self.assertRegex(
            _first_instruction(backend_dockerfile),
            r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$",
        )
        self.assertRegex(
            _first_instruction(frontend_dockerfile),
            r"^FROM node:22-alpine@sha256:[0-9a-f]{64}$",
        )

    def test_backend_container_runs_as_named_non_root_user(self) -> None:
        dockerfile = _read("backend/Dockerfile")

        self.assertIn("groupadd --gid 10001 app", dockerfile)
        self.assertIn("useradd --uid 10001 --gid app", dockerfile)
        self.assertIn("chown -R app:app /app", dockerfile)
        self.assertEqual(_last_user(dockerfile), "app")

    def test_appsec_snapshot_mount_is_read_only_for_the_backend_user(self) -> None:
        compose = _read("compose.yaml")

        self.assertIn("SOURCECRAFT_APPSEC_SNAPSHOT_DIR: /run/sourcecraft-appsec", compose)
        self.assertIn("SOURCECRAFT_APPSEC_SNAPSHOT_READER_GID", compose)
        self.assertIn(":/run/sourcecraft-appsec:ro", compose)

    def test_frontend_container_runs_as_named_non_root_user(self) -> None:
        dockerfile = _read("frontend/Dockerfile")

        self.assertIn("addgroup -S app", dockerfile)
        self.assertIn("adduser -S app -G app", dockerfile)
        self.assertIn("chown -R app:app /app", dockerfile)
        self.assertEqual(_last_user(dockerfile), "app")

    def test_frontend_vite_cache_is_outside_the_node_modules_volume(self) -> None:
        dockerfile = _read("frontend/Dockerfile")
        package_json = _read("frontend/package.json")
        vite_config = _read("frontend/vite.config.ts")

        self.assertIn("ENV VITE_CACHE_DIR=/tmp/vite-cache", dockerfile)
        self.assertIn('"dev": "vite --configLoader runner"', package_json)
        self.assertIn('cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite"', vite_config)

    def test_backend_test_service_is_opt_in_and_mounts_test_inputs_read_only(self) -> None:
        compose = _read("compose.yaml")

        self.assertIn("  backend-test:\n", compose)
        self.assertIn('profiles: ["test"]', compose)
        self.assertIn("- ./frontend:/app/frontend:ro", compose)
        self.assertIn("- ./scripts:/app/scripts:ro", compose)
        self.assertIn("- ./.github:/app/.github:ro", compose)
        self.assertIn("- ./compose.yaml:/app/compose.yaml:ro", compose)
        self.assertIn("python -m unittest discover -s backend/tests -v", compose)
        self.assertIn("ruff check backend --ignore EXE002", compose)


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _last_user(dockerfile: str) -> str | None:
    users = [line.split(maxsplit=1)[1].strip() for line in dockerfile.splitlines() if line.startswith("USER ")]
    return users[-1] if users else None


def _first_instruction(dockerfile: str) -> str:
    return next(line for line in dockerfile.splitlines() if line and not line.startswith("#"))
