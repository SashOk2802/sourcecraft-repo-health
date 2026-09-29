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

    def test_backend_image_contains_alembic_configuration_for_startup_migrations(self) -> None:
        dockerfile = _read("backend/Dockerfile")

        self.assertIn("COPY pyproject.toml README.md alembic.ini ./", dockerfile)
        self.assertIn("COPY backend ./backend", dockerfile)

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

    def test_production_frontend_is_static_non_root_and_digest_pinned(self) -> None:
        dockerfile = _read("frontend/Dockerfile.prod")
        from_lines = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]

        self.assertEqual(len(from_lines), 2)
        for line in from_lines:
            self.assertRegex(line, r"@sha256:[0-9a-f]{64}(?: AS build)?$")
        self.assertIn("RUN npm run build", dockerfile)
        self.assertIn("ARG VITE_YANDEX_AUTH=false", dockerfile)
        self.assertIn("VITE_YANDEX_AUTH=${VITE_YANDEX_AUTH}", dockerfile)
        self.assertIn("COPY --from=build /app/dist /usr/share/nginx/html", dockerfile)
        self.assertNotIn("npm run dev", dockerfile)
        self.assertEqual(_last_user(dockerfile), "101")

    def test_production_nginx_blocks_dev_sources_and_sets_browser_headers(self) -> None:
        nginx = _read("frontend/nginx/default.conf.template")

        self.assertIn("location /api/", nginx)
        self.assertIn("proxy_pass $api_upstream", nginx)
        self.assertIn("location ~ ^/(?:@vite|src)(?:/|$)", nginx)
        self.assertIn("Content-Security-Policy", nginx)
        self.assertIn("Strict-Transport-Security", nginx)
        self.assertIn('X-Content-Type-Options "nosniff"', nginx)
        self.assertIn('X-Frame-Options "DENY"', nginx)
        self.assertIn("Referrer-Policy", nginx)
        self.assertIn("Permissions-Policy", nginx)

    def test_production_compose_does_not_expose_development_runtime(self) -> None:
        compose = _read("compose.production.yaml")

        self.assertIn("dockerfile: Dockerfile.prod", compose)
        self.assertIn("VITE_DATA_SOURCE: api", compose)
        self.assertIn('VITE_YANDEX_AUTH: "${VITE_YANDEX_AUTH:-false}"', compose)
        self.assertIn("APP_ENV: production", compose)
        self.assertIn('YANDEX_SESSION_COOKIE_SECURE: "true"', compose)
        self.assertIn("127.0.0.1:${FRONTEND_PORT:-5173}:8080", compose)
        self.assertIn('SOURCECRAFT_TOKEN: "${SOURCECRAFT_TOKEN:-}"', compose)
        self.assertIn(
            'SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES: "${SOURCECRAFT_DISCOVER_PUBLIC_REPOSITORIES:-false}"',
            compose,
        )
        self.assertIn(
            'PUBLIC_ANALYSIS_SCHEDULER_ENABLED: "${PUBLIC_ANALYSIS_SCHEDULER_ENABLED:-false}"',
            compose,
        )
        self.assertNotIn("name: sourcecraft-repo-health-production", compose)
        self.assertNotIn("--reload", compose)
        self.assertNotIn("./backend:/app/backend", compose)
        self.assertNotIn("./frontend:/app", compose)
        self.assertNotIn("VITE_USE_MOCKS", compose)
        self.assertRegex(
            compose,
            r"image: postgres:16-alpine@sha256:[0-9a-f]{64}",
        )
        self.assertRegex(
            compose,
            r"image: redis:7-alpine@sha256:[0-9a-f]{64}",
        )

        deployment_docs = _read("docs/production-deployment-smoke.md")
        self.assertIn(
            "docker compose -f compose.production.yaml config --quiet",
            deployment_docs,
        )
        self.assertNotIn("compose.production.yaml config\n", deployment_docs)

    def test_versioned_traefik_route_targets_production_nginx(self) -> None:
        route = _read("deploy/traefik/dynamic.yaml")

        self.assertIn('url: "http://backend:8000"', route)
        self.assertIn('url: "http://frontend:8080"', route)
        self.assertNotIn("frontend:5173", route)

    def test_frontend_vite_cache_is_outside_the_node_modules_volume(self) -> None:
        dockerfile = _read("frontend/Dockerfile")
        package_json = _read("frontend/package.json")
        vite_config = _read("frontend/vite.config.ts")

        self.assertIn("ENV VITE_CACHE_DIR=/tmp/vite-cache", dockerfile)
        self.assertIn('"dev": "vite --configLoader runner"', package_json)
        self.assertIn('cacheDir: process.env.VITE_CACHE_DIR ?? "node_modules/.vite"', vite_config)

    def test_compose_uses_real_frontend_api_unless_mocks_are_explicit(self) -> None:
        compose = _read("compose.yaml")

        self.assertIn('VITE_USE_MOCKS: "${VITE_USE_MOCKS:-false}"', compose)

    def test_backend_test_service_is_opt_in_and_mounts_test_inputs_read_only(self) -> None:
        compose = _read("compose.yaml")

        self.assertIn("  backend-test:\n", compose)
        self.assertIn('profiles: ["test"]', compose)
        self.assertIn("- ./frontend:/app/frontend:ro", compose)
        self.assertIn("- ./scripts:/app/scripts:ro", compose)
        self.assertIn("- ./.github:/app/.github:ro", compose)
        self.assertIn("- ./compose.yaml:/app/compose.yaml:ro", compose)
        self.assertIn(
            "- ./compose.production.yaml:/app/compose.production.yaml:ro",
            compose,
        )
        self.assertIn("python -m unittest discover -s backend/tests -v", compose)
        self.assertIn("ruff check backend --ignore EXE002", compose)


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _last_user(dockerfile: str) -> str | None:
    users = [line.split(maxsplit=1)[1].strip() for line in dockerfile.splitlines() if line.startswith("USER ")]
    return users[-1] if users else None


def _first_instruction(dockerfile: str) -> str:
    return next(line for line in dockerfile.splitlines() if line and not line.startswith("#"))
