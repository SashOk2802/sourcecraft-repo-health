"""Контрольные сценарии сбора документации и состояния кода."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.analyzers import code_health, documentation
from backend.app.analyzers.code_health import (
    DEFAULT_MAX_SOURCE_FILES,
    MARKER_AGE_SUMMARY,
    MAX_FILE_READ_BYTES,
)
from backend.app.contracts import (
    AnalysisContext,
    DataStatus,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository
from backend.app.integrations.sourcecraft import SourceCraftClient


class CodeHealthCollectionTest(unittest.TestCase):
    def test_markers_in_comments_are_counted_and_strings_are_not(self) -> None:
        with self._workspace() as root:
            (root / "main.py").write_text(
                'value = "TODO: inside a string"\n# todo: real\n# FIXME: critical\n',
                encoding="utf-8",
            )
            (root / "script.js").write_text("console.log('clean code');\n", encoding="utf-8")
            (root / "styles.css").write_text("body { color: red; }\n", encoding="utf-8")

            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["total_files"], 2)
        self.assertEqual(facts["todo_count"], 1)
        self.assertEqual(facts["fixme_count"], 1)
        self.assertEqual(facts["files_with_debt"], 1)
        self.assertEqual(facts["occurrences"][0]["path"], "main.py")
        self.assertEqual(facts["occurrences"][0]["line"], 2)

    def test_excluded_directories_do_not_add_markers(self) -> None:
        with self._workspace() as root:
            (root / "app.py").write_text("# TODO: fix\n", encoding="utf-8")
            vendor = root / "node_modules"
            vendor.mkdir()
            (vendor / "dep.js").write_text("// FIXME: secret\n", encoding="utf-8")
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "config").write_text("# TODO: remove\n", encoding="utf-8")

            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["todo_count"], 1)
        self.assertEqual(facts["fixme_count"], 0)

    def test_tsx_jsx_and_rust_extensions_are_supported(self) -> None:
        with self._workspace() as root:
            (root / "component.tsx").write_text("// TODO: split\n", encoding="utf-8")
            (root / "index.jsx").write_text("// FIXME: rename\n", encoding="utf-8")
            (root / "lib.rs").write_text("// todo: document\n", encoding="utf-8")

            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["total_files"], 3)
        self.assertEqual(facts["todo_count"], 2)
        self.assertEqual(facts["fixme_count"], 1)

    def test_lowercase_markers_are_counted(self) -> None:
        with self._workspace() as root:
            (root / "app.py").write_text(
                "# todo: first\n# TODO: second\n# fixme: third\n",
                encoding="utf-8",
            )
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["todo_count"], 2)
        self.assertEqual(facts["fixme_count"], 1)

    def test_python_indentation_error_excludes_the_file(self) -> None:
        with self._workspace() as root:
            (root / "broken.py").write_text(
                "def outer():\n\tif True:\n        pass\n",
                encoding="utf-8",
            )
            (root / "app.py").write_text("# TODO: real\n", encoding="utf-8")
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["todo_count"], 1)
        self.assertEqual([item["path"] for item in facts["occurrences"]], ["app.py"])

    def test_only_malformed_python_is_not_applicable(self) -> None:
        with self._workspace() as root:
            (root / "broken.py").write_text(
                "def outer():\n\tif True:\n        pass\n",
                encoding="utf-8",
            )
            facts = code_health.collect(self._repository(root))
            result = code_health.evaluate(self._context(), facts)

        self.assertEqual(facts["total_files"], 0)
        self.assertEqual(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)

    def test_rust_raw_string_is_not_a_comment(self) -> None:
        with self._workspace() as root:
            (root / "raw.rs").write_text(
                'let s = r#"label "quoted // TODO: inside raw string"#;\n'
                "// FIXME: real comment\n",
                encoding="utf-8",
            )
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["todo_count"], 0)
        self.assertEqual(facts["fixme_count"], 1)
        self.assertEqual(facts["occurrences"][0]["line"], 2)

    def test_template_interpolation_comment_is_found(self) -> None:
        with self._workspace() as root:
            (root / "view.ts").write_text(
                "const label = `prefix ${value // FIXME: interpolated comment\n"
                "} suffix`;\n"
                "const other = `plain // TODO: not a comment`;\n",
                encoding="utf-8",
            )
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["fixme_count"], 1)
        self.assertEqual(facts["todo_count"], 0)
        self.assertEqual(facts["occurrences"][0]["line"], 1)

    def test_javascript_regex_markers_are_not_comments(self) -> None:
        with self._workspace() as root:
            (root / "regex.js").write_text(
                "const re = /[//] TODO/; // FIXME: real comment\n"
                "const block = /[/*] FIXME/;\n"
                "function build() { return /[//] TODO/; }\n"
                "if (ok) /[//] TODO/.test(value);\n"
                "if ((ok && check())) {} /[//] TODO/.test(value);\n"
                "export default /[//] TODO/;\n"
                "const grouped = (left + right) / divisor; // TODO: grouped division\n"
                "const objectRatio = {value: 2} / divisor; // TODO: object division\n"
                "const propertyRatio = obj.if(value) / divisor; // TODO: property call\n"
                "const ratio = left / right; // TODO: real comment\n",
                encoding="utf-8",
            )
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["todo_count"], 4)
        self.assertEqual(facts["fixme_count"], 1)

    def test_unterminated_python_string_excludes_the_file(self) -> None:
        with self._workspace() as root:
            (root / "broken.py").write_text(
                "# FIXME: real defect above the break\n"
                'value = """unterminated triple-quoted string\n',
                encoding="utf-8",
            )
            (root / "app.py").write_text("# TODO: ok\n", encoding="utf-8")
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["fixme_count"], 0)
        self.assertEqual(facts["todo_count"], 1)

    def test_collect_requires_prepared_workspace(self) -> None:
        repository = LocalGitRepository("https://example.invalid/repo.git")
        with self.assertRaises(GitCloneError):
            code_health.collect(repository)

    def test_symlink_outside_clone_is_ignored(self) -> None:
        with self._workspace() as root:
            outside = root.parent / f"evil-outside-{os.getpid()}.py"
            outside.write_text("# TODO: evil\n# FIXME: secret\n", encoding="utf-8")
            try:
                try:
                    os.symlink(outside, root / "evil.py")
                except OSError:
                    self.skipTest("symlink creation is not permitted")
                (root / "app.py").write_text("# TODO: real\n", encoding="utf-8")
                facts = code_health.collect(self._repository(root))
            finally:
                outside.unlink(missing_ok=True)

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["todo_count"], 1)
        self.assertEqual(facts["fixme_count"], 0)

    def test_oversized_file_is_skipped_without_reading(self) -> None:
        with self._workspace() as root:
            repository = self._repository(root)
            big = root / "big.py"
            with big.open("w", encoding="utf-8") as handle:
                handle.write("# TODO: near start\n")
                handle.write("x" * (MAX_FILE_READ_BYTES + 64))
            calls: list[str] = []
            original = LocalGitRepository.read_file_safe

            def spy(self, relative_path: str, max_bytes: int = MAX_FILE_READ_BYTES) -> str | None:
                calls.append(relative_path)
                return original(self, relative_path, max_bytes=max_bytes)

            with patch.object(LocalGitRepository, "read_file_safe", spy):
                facts = code_health.collect(repository)

        self.assertEqual(calls, [])
        self.assertEqual(facts["total_files"], 0)
        self.assertEqual(facts["skipped_large_files"], 1)
        self.assertFalse(facts["truncated"])

    def test_generated_and_minified_files_are_skipped(self) -> None:
        with self._workspace() as root:
            (root / "app.min.js").write_text("// FIXME: minified bundle\n", encoding="utf-8")
            (root / "api.generated.ts").write_text("// TODO: generated client\n", encoding="utf-8")
            (root / "app.py").write_text("# TODO: real\n", encoding="utf-8")
            facts = code_health.collect(self._repository(root))

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["todo_count"], 1)
        self.assertEqual(facts["fixme_count"], 0)
        self.assertEqual(facts["skipped_generated_files"], 2)

    def test_file_budget_stops_without_a_partial_score(self) -> None:
        with self._workspace() as root:
            (root / "a.py").write_text("# TODO: one\n", encoding="utf-8")
            (root / "b.py").write_text("# FIXME: two\n", encoding="utf-8")
            facts = code_health.collect(self._repository(root), max_files=1)
            result = code_health.evaluate(self._context(), facts)

        self.assertTrue(facts["truncated"])
        self.assertGreater(facts["bytes_read"], 0)
        self.assertEqual(facts["candidate_files"], 2)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "code_health_scan_limit_exceeded")
        metrics = {metric.code: metric.value for metric in result.metrics}
        self.assertEqual(metrics["partial_analyzed_files"], 1)
        self.assertEqual(metrics["partial_bytes_read"], facts["bytes_read"])
        self.assertEqual(metrics["partial_candidate_files"], 2)

    def test_byte_budget_reports_volume_without_a_score(self) -> None:
        with self._workspace() as root:
            (root / "a.py").write_text("# TODO: kept\n", encoding="utf-8")
            (root / "b.py").write_text("# FIXME: " + ("x" * 80) + "\n", encoding="utf-8")
            first_size = (root / "a.py").stat().st_size
            facts = code_health.collect(
                self._repository(root),
                max_total_bytes=first_size,
            )
            result = code_health.evaluate(self._context(), facts)

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["bytes_read"], first_size)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        metrics = {metric.code: metric.value for metric in result.metrics}
        self.assertEqual(metrics["partial_analyzed_files"], 1)
        self.assertEqual(metrics["partial_bytes_read"], first_size)

    @contextmanager
    def _workspace(self) -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            yield Path(temporary_directory)

    def _repository(self, root: Path) -> LocalGitRepository:
        repository = LocalGitRepository("https://example.invalid/repo.git")
        repository.temp_dir = str(root)
        return repository

    def _context(self) -> AnalysisContext:
        now = datetime(2026, 9, 27, 12, tzinfo=UTC)
        return AnalysisContext(
            repository=RepositoryRef(
                id="repo-42",
                organization_slug="team",
                repository_slug="platform-api",
            ),
            commit_sha="abc123",
            analyzed_at=now,
            period_start=now,
            period_end=now,
        )


class CodeHealthEvaluationTest(unittest.TestCase):
    def test_measured_result_keeps_score_and_shows_unknown_age(self) -> None:
        result = code_health.evaluate(
            context(),
            {
                "total_files": 10,
                "todo_count": 2,
                "fixme_count": 1,
                "files_with_debt": 3,
                "occurrences": [{"kind": "FIXME", "path": "app.py", "line": 4}],
            },
        )

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 30.0)
        self.assertIn(MARKER_AGE_SUMMARY, result.summary)
        age = next(metric for metric in result.metrics if metric.code == "code_health.marker_age")
        self.assertIsNone(age.value)
        self.assertIsNone(age.normalized_score)
        self.assertIn("недоступен", age.summary)
        ratio = next(
            metric for metric in result.metrics if metric.code == "code_health.debt_file_ratio"
        )
        self.assertAlmostEqual(float(ratio.value), 0.3)
        fixme = next(item for item in result.recommendations if item.code == "code_health_resolve_fixme")
        self.assertEqual(fixme.priority, RecommendationPriority.P2)
        self.assertEqual(fixme.evidence[0].reference, "app.py:4")

    def test_zero_supported_files_are_not_applicable(self) -> None:
        result = code_health.evaluate(
            context(),
            {"total_files": 0, "todo_count": 0, "fixme_count": 0},
        )
        self.assertEqual(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)

    def test_repository_error_is_not_a_low_score(self) -> None:
        result = code_health.evaluate(context(), {"error": "clone failed"})
        self.assertEqual(result.status, DataStatus.ERROR)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "clone failed")


class DocumentationCollectionTest(unittest.TestCase):
    def test_run_section_and_widened_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.rst").write_text(
                "How to run\n\n.. code:: bash\n\n   pytest\n",
                encoding="utf-8",
            )
            (root / "LICENCE").write_text("MIT\n", encoding="utf-8")
            owners = root / ".github"
            owners.mkdir()
            (owners / "CODEOWNERS").write_text("* @team\n", encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            facts = documentation.collect(repository)

        self.assertTrue(facts["has_readme"])
        self.assertTrue(facts["has_license"])
        self.assertTrue(facts["has_codeowners"])
        self.assertFalse(facts["has_contributing"])
        self.assertFalse(facts["has_shortcuts"])

    def test_heading_detects_run_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text(
                "## How to run\n\n```bash\ndocker compose up\n```\n",
                encoding="utf-8",
            )
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            facts = documentation.collect(repository)

        self.assertTrue(facts["has_shortcuts"])

    def test_latest_and_runtime_are_not_run_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text(
                "## Latest features\n\nThe runtime supports hot reload.\n",
                encoding="utf-8",
            )
            repository = LocalGitRepository("https://example.invalid/repo.git")
            repository.temp_dir = temporary_directory
            facts = documentation.collect(repository)

        self.assertTrue(facts["has_readme"])
        self.assertFalse(facts["has_shortcuts"])

    def test_missing_file_evidence_names_that_file(self) -> None:
        result = documentation.evaluate(
            context(),
            {
                "has_readme": False,
                "has_shortcuts": False,
                "has_contributing": True,
                "has_license": True,
                "has_codeowners": True,
            },
        )
        self.assertEqual(result.score, 65)
        readme = next(item for item in result.recommendations if item.code == "doc_missing_has_readme")
        self.assertEqual(readme.evidence[0].reference, "README.md")
        self.assertEqual([item.code for item in result.recommendations], ["doc_missing_has_readme"])

        shortcuts = documentation.evaluate(
            context(),
            {
                "has_readme": True,
                "has_shortcuts": False,
                "has_contributing": True,
                "has_license": True,
                "has_codeowners": True,
            },
        )
        missing = next(
            item for item in shortcuts.recommendations if item.code == "doc_missing_has_shortcuts"
        )
        self.assertEqual(missing.evidence[0].reference, "README.md")
        self.assertIn("README.md", missing.evidence[0].summary)


class MethodologyAlignmentTest(unittest.TestCase):
    def test_documented_penalties_match_code_and_stay_unapproved(self) -> None:
        root = Path(__file__).resolve().parents[2]
        methodology = (root / "docs/scoring-methodology.md").read_text(encoding="utf-8")
        approval = (root / "docs/methodology-owner-approval.md").read_text(encoding="utf-8")

        self.assertIn("### 4.4. Documentation", methodology)
        self.assertIn("### 4.1. CI/CD", methodology)
        self.assertIn("PENDING_APPROVAL", methodology)
        self.assertIn("PENDING_APPROVAL", approval)
        self.assertNotIn("**APPROVED**", approval)
        self.assertEqual(documentation.PENALTY_README, 35)
        self.assertEqual(documentation.PENALTY_CONTRIBUTING, 20)
        self.assertEqual(documentation.PENALTY_LICENSE, 15)
        self.assertEqual(documentation.PENALTY_CODEOWNERS, 15)
        self.assertEqual(documentation.PENALTY_INSTRUCTIONS, 15)
        self.assertIn("| `has_readme` | `README.md`, `README.rst` | 35 | P1 |", methodology)
        self.assertEqual(code_health.FIXME_PENALTY_PER_MARKER, 5)
        self.assertEqual(code_health.TODO_PENALTY_PER_MARKER, 1)
        self.assertEqual(code_health.FIXME_CRITICAL_COUNT, 2)


class LocalCloneScenarioTest(unittest.TestCase):
    def test_complete_repository_scores_both_categories_at_100(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote = self._publish(
                root / "origin",
                {
                    "README.md": "# Demo\n\n## Запуск\n\n```bash\npython -m pytest\n```\n",
                    "CONTRIBUTING.md": "# Правила\n",
                    "LICENSE": "MIT\n",
                    "CODEOWNERS": "* @team\n",
                    "service.py": "value = 1\n",
                },
            )
            repository = LocalGitRepository(remote, ref="main", timeout_seconds=30)
            repository.clone()
            try:
                facts_documentation = documentation.collect(repository)
                facts_health = code_health.collect(repository)
            finally:
                repository.cleanup()

        documented = documentation.evaluate(context(), facts_documentation)
        health = code_health.evaluate(context(), facts_health)
        self.assertEqual(documented.status, DataStatus.MEASURED)
        self.assertEqual(documented.score, 100)
        self.assertEqual(health.status, DataStatus.MEASURED)
        self.assertEqual(health.score, 100)
        self.assertIn(MARKER_AGE_SUMMARY, health.summary)

    def test_missing_regulations_and_one_fixme_are_measured_penalties(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            remote = self._publish(root / "origin", {"service.py": "# FIXME: broken\n"})
            repository = LocalGitRepository(remote, ref="main", timeout_seconds=30)
            repository.clone()
            try:
                facts_documentation = documentation.collect(repository)
                facts_health = code_health.collect(repository)
            finally:
                repository.cleanup()

        documented = documentation.evaluate(context(), facts_documentation)
        health = code_health.evaluate(context(), facts_health)
        self.assertEqual(documented.status, DataStatus.MEASURED)
        self.assertEqual(documented.score, 15)
        self.assertEqual(health.status, DataStatus.MEASURED)
        self.assertEqual(health.score, 0)
        self.assertEqual(health.recommendations[0].priority, RecommendationPriority.P2)

    def test_missing_remote_is_an_error_without_a_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = str(Path(temporary_directory) / "missing.git")
            token = "super-secret-token"
            repository = LocalGitRepository(
                missing,
                ref="main",
                auth_token=token,
                timeout_seconds=30,
            )
            with self.assertRaises(GitCloneError) as captured:
                repository.clone()

        message = str(captured.exception)
        self.assertNotIn(token, message)
        self.assertNotIn(missing, message)
        for module in (documentation, code_health):
            result = module.evaluate(context(), {"error": message})
            self.assertEqual(result.status, DataStatus.ERROR)
            self.assertIsNone(result.score)

    def _publish(self, origin: Path, files: dict[str, str]) -> str:
        origin.mkdir()
        self._git(origin, "init", "-b", "main")
        for name, content in files.items():
            path = origin / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self._git(origin, "add", ".")
        environment = os.environ.copy()
        environment.update(
            {
                "GIT_AUTHOR_NAME": "Repo Health Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Repo Health Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
            }
        )
        self._git(origin, "commit", "-m", "init", env=environment)
        return origin.as_uri()

    def _git(self, repository: Path, *arguments: str, env: dict[str, str] | None = None) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )


class LargeTreeScanTest(unittest.TestCase):
    def test_ten_thousand_files_stay_inside_the_budget(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for index in range(10_001):
                (root / f"f{index:05d}.py").write_text("value = 1\n", encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/large.git")
            repository.temp_dir = temporary_directory
            facts = code_health.collect(repository)
            result = code_health.evaluate(context(), facts)

        self.assertEqual(facts["total_files"], 10_001)
        self.assertFalse(facts["truncated"])
        self.assertGreater(facts["bytes_read"], 0)
        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 100)

    def test_past_the_file_budget_reports_volume_without_a_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for index in range(DEFAULT_MAX_SOURCE_FILES + 1):
                (root / f"f{index:05d}.py").write_text("value = 1\n", encoding="utf-8")
            repository = LocalGitRepository("https://example.invalid/larger.git")
            repository.temp_dir = temporary_directory
            facts = code_health.collect(repository)
            result = code_health.evaluate(context(), facts)

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["total_files"], DEFAULT_MAX_SOURCE_FILES)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        metrics = {metric.code: metric.value for metric in result.metrics}
        self.assertEqual(metrics["partial_analyzed_files"], DEFAULT_MAX_SOURCE_FILES)
        self.assertGreater(metrics["partial_bytes_read"], 0)
        self.assertEqual(metrics["partial_candidate_files"], DEFAULT_MAX_SOURCE_FILES + 1)


@unittest.skipUnless(os.environ.get("SOURCECRAFT_LIVE") == "1", "set SOURCECRAFT_LIVE=1")
class LiveSourceCraftFileAnalysisTest(unittest.TestCase):
    def test_public_clone_url_and_three_control_repositories(self) -> None:
        self.assertEqual(
            SourceCraftClient.resolve_git_clone_url(
                "k-5-45mm",
                "dozzle-plus",
                "https://sourcecraft.dev/k-5-45mm/dozzle-plus",
            ),
            "https://git.sourcecraft.dev/k-5-45mm/dozzle-plus.git",
        )
        healthy = self._measure("k-5-45mm", "dozzle-plus")
        weaker = self._measure("brothersandksu", "casesc")
        self.assertEqual(healthy[0].status, DataStatus.MEASURED)
        self.assertEqual(weaker[0].status, DataStatus.MEASURED)
        self.assertGreater(healthy[0].score, weaker[0].score)
        self.assertEqual(healthy[1].status, DataStatus.MEASURED)
        self.assertEqual(weaker[1].status, DataStatus.MEASURED)
        self.assertGreater(healthy[1].score, 90)
        self.assertIn(MARKER_AGE_SUMMARY, healthy[1].summary)

        missing_url = SourceCraftClient.resolve_git_clone_url(
            "missing-org",
            "missing-repo",
            "https://sourcecraft.dev/missing-org/missing-repo",
        )
        missing = LocalGitRepository(missing_url, ref="main", timeout_seconds=30)
        with self.assertRaises(GitCloneError) as captured:
            missing.clone()
        self.assertNotIn(missing_url, str(captured.exception))
        for module in (documentation, code_health):
            result = module.evaluate(context(), {"error": str(captured.exception)})
            self.assertEqual(result.status, DataStatus.ERROR)
            self.assertIsNone(result.score)

    def _measure(self, organization: str, slug: str):
        url = SourceCraftClient.resolve_git_clone_url(
            organization,
            slug,
            f"https://sourcecraft.dev/{organization}/{slug}",
        )
        repository = LocalGitRepository(url, ref="main", timeout_seconds=90)
        repository.clone()
        try:
            documented = documentation.evaluate(context(), documentation.collect(repository))
            health = code_health.evaluate(context(), code_health.collect(repository))
        finally:
            repository.cleanup()
        return documented, health


def context() -> AnalysisContext:
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
        ),
        commit_sha="abc123",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )
