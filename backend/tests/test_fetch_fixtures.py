"""Проверяет, что скрипт фикстур режет поля и не снимает приватные репозитории молча."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "fetch_fixtures.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fetch_fixtures_script", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"не удалось загрузить {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FetchFixturesRedactionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = load_script()

    def test_contributor_keeps_only_id_and_username(self) -> None:
        projected = self.script.project_contributor(
            {
                "id": "u1",
                "username": "alice",
                "display_name": "Алиса",
                "bio": "DevOps",
                "location": {"city": "Москва"},
                "links": [{"type": "telegram", "link": "https://t.me/alice"}],
                "avatar": {"url": "https://example.test/avatar"},
            }
        )
        self.assertEqual(projected, {"id": "u1", "username": "alice"})

    def test_issue_drops_description_and_author(self) -> None:
        projected = self.script.project_issue(
            {
                "id": "i1",
                "slug": "12",
                "title": "Bug",
                "description": "AppSec / Trivy details",
                "author": {"id": "u1", "slug": "alice"},
                "updated_by": {"id": "u1", "slug": "alice"},
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-02T00:00:00Z",
                "completed_at": None,
                "status": {
                    "id": "1",
                    "slug": "open",
                    "name": "Open",
                    "status_type": "initial",
                    "extra": "drop-me",
                },
            }
        )
        self.assertNotIn("description", projected)
        self.assertNotIn("author", projected)
        self.assertNotIn("updated_by", projected)
        self.assertEqual(
            projected["status"],
            {
                "id": "1",
                "slug": "open",
                "name": "Open",
                "status_type": "initial",
            },
        )

    def test_private_repository_is_rejected_without_explicit_flag(self) -> None:
        with self.assertRaises(self.script.FixtureError):
            self.script.ensure_capture_allowed(
                {"visibility": "private"},
                allow_private=False,
            )

    def test_public_repository_is_allowed(self) -> None:
        self.script.ensure_capture_allowed({"visibility": "public"}, allow_private=False)

    def test_private_repository_is_allowed_with_confirmation(self) -> None:
        self.script.ensure_capture_allowed({"visibility": "private"}, allow_private=True)

    def test_capture_parser_requires_allow_private_flag(self) -> None:
        parser = self.script.build_parser()
        args = parser.parse_args(
            ["capture", "org/repo", "--label", "active", "--allow-private"]
        )
        self.assertTrue(args.allow_private)
        without_flag = parser.parse_args(["capture", "org/repo", "--label", "active"])
        self.assertFalse(without_flag.allow_private)


if __name__ == "__main__":
    unittest.main()
