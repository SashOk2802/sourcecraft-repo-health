from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.analysis.providers import repo_content_analyzer_provider
from backend.app.analyzers import code_health, documentation
from backend.app.contracts import (
    AnalysisContext,
    DataStatus,
    Recommendation,
    RecommendationPriority,
    RepositoryRef,
)
from backend.app.integrations.git_repository import GitCloneError, LocalGitRepository


class FileAnalyzersTest(unittest.TestCase):
    def test_documentation_and_code_health_measure_prepared_clone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text(
                "# Demo\n\n## Запуск\n\n"
                + chr(96) * 3
                + "bash\npython -m pytest\n"
                + chr(96) * 3
                + "\n",
                encoding="utf-8",
            )
            (root / "CONTRIBUTING.md").write_text("# Правила\n", encoding="utf-8")
            (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
            (root / "CODEOWNERS").write_text("* @team\n", encoding="utf-8")
            (root / "service.py").write_text(
                "# TODO: add validation\n# FIXME: handle retry\n",
                encoding="utf-8",
            )

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            context = analysis_context()

            documentation_result = documentation.evaluate(
                context,
                documentation.collect(repository),
            )
            code_health_result = code_health.evaluate(
                context,
                code_health.collect(repository),
            )

        self.assertEqual(documentation_result.status, DataStatus.MEASURED)
        self.assertEqual(documentation_result.score, 100)
        self.assertEqual(code_health_result.status, DataStatus.MEASURED)
        self.assertIsNotNone(code_health_result.score)
        self.assertLess(code_health_result.score, 100)
        self.assertEqual(
            {metric.code for metric in code_health_result.metrics},
            {"total_analyzed_files", "todo_count", "fixme_count"},
        )

    def test_code_health_small_repo_single_marker_density_semantics(self) -> None:
        """Одинокий FIXME в репозитории из 1–5 файлов обнуляет категорию (V.1).

        Плотность — задокументированная семантика формулы (методика §4.3, ревью
        V.1): штраф растёт как 1/total_files, поэтому один FIXME даёт penalty
        >= 100 при total_files <= 5 и score 0. Тест фиксирует кривую 1/3/6/100
        файлов; корректность порога ожидает согласования владельцем методики
        (PENDING_APPROVAL в §4.3) и этим тестом не доказывается.
        """
        context = analysis_context()

        def score_for(total_files: int) -> float:
            result = code_health.evaluate(
                context,
                {
                    "total_files": total_files,
                    "todo_count": 0,
                    "fixme_count": 1,
                    "files_with_debt": 1,
                },
            )
            assert result.score is not None
            return result.score

        # penalty = (1*5)/1*100 = 500 → score 0
        self.assertEqual(score_for(1), 0.0)
        # penalty = (1*5)/3*100 ≈ 166.67 → score 0
        self.assertEqual(score_for(3), 0.0)
        # Градиент непрерывен: за пределами «обнуляющего» диапазона score > 0.
        self.assertAlmostEqual(score_for(6), 16.67, places=2)
        self.assertGreater(score_for(6), score_for(3))
        # В обычном по размеру репо одинокий FIXME категорию не обнуляет.
        self.assertEqual(score_for(100), 95.0)

    def test_code_health_fixme_critical_count_boundary(self) -> None:
        """Граница FIXME_CRITICAL_COUNT = 2 зафиксирована тестом (V.2).

        Единичный FIXME — P2; от FIXME_CRITICAL_COUNT включительно рекомендация
        эскалируется до жёсткой P1 с формулировкой про опасный код.
        """
        self.assertEqual(code_health.FIXME_CRITICAL_COUNT, 2)
        context = analysis_context()

        def recommendation_for(fixme_count: int) -> Recommendation:
            result = code_health.evaluate(
                context,
                {
                    "total_files": 100,
                    "todo_count": 0,
                    "fixme_count": fixme_count,
                    "files_with_debt": 1,
                },
            )
            return next(
                r for r in result.recommendations if r.code == "code_health_resolve_fixme"
            )

        single = recommendation_for(code_health.FIXME_CRITICAL_COUNT - 1)
        self.assertEqual(single.priority, RecommendationPriority.P2)
        self.assertNotIn("опасный", single.rationale)

        at_threshold = recommendation_for(code_health.FIXME_CRITICAL_COUNT)
        self.assertEqual(at_threshold.priority, RecommendationPriority.P1)
        self.assertIn("опасный", at_threshold.rationale)

        above_threshold = recommendation_for(code_health.FIXME_CRITICAL_COUNT + 1)
        self.assertEqual(above_threshold.priority, RecommendationPriority.P1)
        self.assertIn("опасный", above_threshold.rationale)

    def test_documentation_bad_set_missing_readme_and_license_scores_low(self) -> None:
        """Плохой набор документации (нет README и лицензии) даёт низкий балл.

        Контрольный сценарий напротив успешного прогона: отсутствие README
        (−35, P1) и LICENSE (−15, P2) снижают оценку до 50 из 100. Штраф за
        отсутствие инструкций запуска при отсутствующем README не применяется
        (одна причина — нет README — даёт одну рекомендацию).
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "CONTRIBUTING.md").write_text("# Правила\n", encoding="utf-8")
            (root / "CODEOWNERS").write_text("* @team\n", encoding="utf-8")

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            context = analysis_context()

            result = documentation.evaluate(
                context,
                documentation.collect(repository),
            )

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 50.0)
        self.assertEqual(
            {recommendation.code for recommendation in result.recommendations},
            {"doc_missing_has_readme", "doc_missing_has_license"},
        )
        self.assertEqual(
            next(
                recommendation.priority
                for recommendation in result.recommendations
                if recommendation.code == "doc_missing_has_readme"
            ),
            RecommendationPriority.P1,
        )

    def test_code_health_not_applicable_without_code_files(self) -> None:
        """Нет файлов кода → категория not_applicable, а не 0 баллов.

        Репозиторий только с документацией и текстом не содержит
        поддерживаемых файлов кода: total_files = 0, категория исключается и
        из Score, и из знаменателя Coverage (методика §1.4) — score равен None.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            (root / "notes.txt").write_text("plain text\n", encoding="utf-8")

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            context = analysis_context()

            facts = code_health.collect(repository)
            result = code_health.evaluate(context, facts)

        self.assertEqual(facts["total_files"], 0)
        self.assertEqual(result.status, DataStatus.NOT_APPLICABLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.recommendations, ())

    def test_clone_error_maps_to_error_with_null_score(self) -> None:
        """Ошибка клона даёт status=ERROR и score=None, а не низкий балл.

        Недоступный репозиторий не должен оцениваться как «очень плохая
        документация» или «очень грязный код»: недоступность данных отделена
        от измеренных фактов (I.6) — категория получает ERROR и пустой score.
        """
        context = analysis_context()

        for module in (documentation, code_health):
            result = module.evaluate(context, {"error": "git clone failed"})
            self.assertEqual(result.status, DataStatus.ERROR)
            self.assertIsNone(result.score)

        providers_by_category = {
            registration.category: registration.evaluate
            for registration in repo_content_analyzer_provider(context)
        }
        with patch.object(
            LocalGitRepository,
            "clone",
            side_effect=GitCloneError("failed to clone repository"),
        ):
            documentation_result = providers_by_category["documentation"](context)
            code_health_result = providers_by_category["code_health"](context)

        for result in (documentation_result, code_health_result):
            self.assertEqual(result.status, DataStatus.ERROR)
            self.assertIsNone(result.score)

    def test_code_health_counts_markers_in_comments_only(self) -> None:
        """TODO/FIXME считаются только в комментариях; строки и большие файлы — нет.

        Метки внутри строковых литералов ложными не считаются (перенесено из
        codex/rebuild-file-analysis): строка ``"TODO FIXME"`` и ``"TODO"`` не дают
        счётчиков. Файлы больше лимита пропускаются как «большие», каталог
        vendor исключён.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "app.py").write_text(
                'label = "TODO FIXME"\n# TODO: write tests\n',
                encoding="utf-8",
            )
            (root / "web.js").write_text(
                'const label = "TODO"; // FIXME: replace\n',
                encoding="utf-8",
            )
            (root / "vendor").mkdir()
            (root / "vendor" / "ignored.py").write_text("# FIXME\n", encoding="utf-8")
            (root / "large.py").write_bytes(b"# TODO\n" + b"x" * (1024 * 1024))

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            facts = code_health.collect(repository)

        self.assertEqual(
            (
                facts["total_files"],
                facts["todo_count"],
                facts["fixme_count"],
                facts["skipped_large_files"],
            ),
            (2, 1, 1, 1),
        )

    def test_code_health_ignores_javascript_regex_but_keeps_real_comments(self) -> None:
        """TODO/FIXME внутри JS-регулярных выражений не считаются (V.3).

        Контекстный tokenizer отличает regex-литерал от деления: после обычной
        ``)``, ``}`` объекта и вызова свойства ``/`` — это деление; после
        ``if (...)`` и ``export default`` — может начинаться regex. Считаются
        только метки в настоящих комментариях (перенесено из
        codex/rebuild-file-analysis).
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
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

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            facts = code_health.collect(repository)

        self.assertEqual(facts["total_files"], 1)
        self.assertEqual(facts["todo_count"], 4)
        self.assertEqual(facts["fixme_count"], 1)
        self.assertEqual(facts["files_with_debt"], 1)

    def test_code_health_resource_limit_returns_insufficient_sample(self) -> None:
        """Превышение бюджета файлов даёт insufficient_sample, а не низкий балл.

        При ``max_files=2`` третий файл-кандидат останавливает сканирование:
        категория получает INSUFFICIENT_SAMPLE с пустым score, чтобы частичный
        обход не превратился в заниженную оценку (codex/rebuild-file-analysis).
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for index in range(3):
                (root / f"file_{index}.py").write_text("# TODO\n", encoding="utf-8")

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            facts = code_health.collect(repository, max_files=2)

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["total_files"], 2)
        result = code_health.evaluate(analysis_context(), facts)
        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "code_health_scan_limit_exceeded")

    def test_code_health_binary_files_consume_byte_budget_before_read(self) -> None:
        """Бюджет байт учитывает файлы до их чтения; бинарные файлы его тратят."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "first.py").write_bytes(b"\x00binary")
            (root / "second.js").write_bytes(b"\x00binary")

            repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
            repository.temp_dir = temporary_directory
            with patch.object(
                repository,
                "read_file_safe",
                wraps=repository.read_file_safe,
            ) as read_file:
                facts = code_health.collect(repository, max_total_bytes=7)

        self.assertTrue(facts["truncated"])
        self.assertEqual(facts["total_files"], 0)
        self.assertEqual(read_file.call_count, 1)

    def test_code_health_resource_limits_must_be_valid(self) -> None:
        """Невалидные бюджеты сканирования отклоняются (TypeError/ValueError)."""
        repository = LocalGitRepository("https://sourcecraft.dev/team/platform-api.git")
        for limit_name in ("max_files", "max_total_bytes"):
            with (
                self.subTest(limit_name=limit_name, value=0),
                self.assertRaises(ValueError),
            ):
                code_health.collect(repository, **{limit_name: 0})
            for value in (True, 1.0, float("inf"), float("nan"), "100"):
                with (
                    self.subTest(limit_name=limit_name, value=value),
                    self.assertRaises(TypeError),
                ):
                    code_health.collect(repository, **{limit_name: value})


def analysis_context() -> AnalysisContext:
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    return AnalysisContext(
        repository=RepositoryRef(
            id="repo-42",
            organization_slug="team",
            repository_slug="platform-api",
        ),
        commit_sha="main",
        analyzed_at=now,
        period_start=now,
        period_end=now,
    )