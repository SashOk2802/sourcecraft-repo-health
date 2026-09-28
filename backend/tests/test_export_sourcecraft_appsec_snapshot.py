"""Проверяет CLI exporter безопасного AppSec snapshot без SourceCraft."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.integrations.sourcecraft_appsec_snapshot import DEFAULT_SNAPSHOT_READER_GID
from scripts.export_sourcecraft_appsec_snapshot import (
    MAX_REPOSITORIES,
    MAX_REPOSITORIES_FILE_BYTES,
    main,
)


class ExportSourceCraftAppSecSnapshotTest(unittest.TestCase):
    def test_explicit_api_profile_selects_paginated_source(self) -> None:
        with (
            patch("scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecApiProbe") as api,
            patch("scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliProbe") as legacy,
            patch(
                "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliSnapshotExporter"
            ) as exporter,
            redirect_stdout(io.StringIO()),
        ):
            exporter.return_value.export.return_value = Path("safe-snapshot.json")
            result = main(
                [
                    "example-org/example-repo",
                    "--output-dir",
                    str(Path.cwd()),
                    "--appsec-env",
                    "AppSecRead",
                ]
            )
        self.assertEqual(result, 0)
        api.assert_called_once_with(
            appsec_environment="AppSecRead", cli_binary="src", timeout_seconds=20
        )
        legacy.assert_not_called()
        self.assertIs(exporter.call_args.args[0], api.return_value)

    def test_documented_direct_python_invocation_finds_project_package(self) -> None:
        script = Path(__file__).parents[2] / "scripts" / "export_sourcecraft_appsec_snapshot.py"

        completed = subprocess.run(
            [sys.executable, str(script), "--help"],
            check=False,
            capture_output=True,
            text=True,
            cwd=script.parents[1],
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Создать безопасный AppSec snapshot", completed.stdout)

    def test_exporter_writes_safe_snapshot_without_repository_slug(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = io.StringIO()
            probe = Mock()
            exporter = Mock()
            exporter.export.return_value = Path(temporary_directory) / "safe-snapshot.json"
            with (
                patch(
                    "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliProbe",
                    return_value=probe,
                ),
                patch(
                    "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliSnapshotExporter",
                    return_value=exporter,
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "example-org/example-repo",
                        "--output-dir",
                        temporary_directory,
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                exporter.export.call_args.args,
                ("example-org/example-repo", Path(temporary_directory)),
            )
            self.assertEqual(
                exporter.export.call_args.kwargs,
                {"reader_gid": DEFAULT_SNAPSHOT_READER_GID, "public_only": False},
            )
            self.assertNotIn("example-org", output.getvalue())
            self.assertIn("safe-snapshot.json", output.getvalue())

    def test_exporter_redacts_operating_system_error(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        with (
            patch(
                "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliProbe",
                return_value=Mock(),
            ),
            patch(
                "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliSnapshotExporter",
                side_effect=OSError("private-path/synthetic-secret-marker"),
            ),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            exit_code = main(
                [
                    "example-org/example-repo",
                    "--output-dir",
                    "/tmp/sourcecraft-appsec-test",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertNotIn("synthetic-secret-marker", errors.getvalue())

    def test_batch_refresh_validates_deduplicates_and_redacts_repositories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repositories_file = root / "repositories.txt"
            repositories_file.write_text(
                "# public repositories\nexample-org/first\nexample-org/second\nexample-org/first\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            exporter = Mock()
            exporter.export.side_effect = [root / "first.json", root / "second.json"]
            with (
                patch("scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecApiProbe"),
                patch(
                    "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliSnapshotExporter",
                    return_value=exporter,
                ),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "--repositories-file",
                        str(repositories_file),
                        "--output-dir",
                        str(root),
                        "--appsec-env",
                        "AppSecRead",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(exporter.export.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in exporter.export.call_args_list],
            ["example-org/first", "example-org/second"],
        )
        self.assertTrue(all(call.kwargs["public_only"] for call in exporter.export.call_args_list))
        self.assertIn("обновлены: 2; ошибок: 0", output.getvalue())
        self.assertNotIn("example-org", output.getvalue())

    def test_batch_refresh_continues_after_failure_without_exposing_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            repositories_file = root / "repositories.txt"
            repositories_file.write_text(
                "private-owner/first\nprivate-owner/second\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            errors = io.StringIO()
            exporter = Mock()
            exporter.export.side_effect = [
                OSError("private-owner/first synthetic-secret-marker"),
                root / "safe.json",
            ]
            with (
                patch("scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecApiProbe"),
                patch(
                    "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliSnapshotExporter",
                    return_value=exporter,
                ),
                redirect_stdout(output),
                redirect_stderr(errors),
            ):
                exit_code = main(
                    [
                        "--repositories-file",
                        str(repositories_file),
                        "--output-dir",
                        str(root),
                        "--appsec-env",
                        "AppSecRead",
                    ]
                )

        self.assertEqual(exit_code, 2)
        self.assertEqual(exporter.export.call_count, 2)
        combined = output.getvalue() + errors.getvalue()
        self.assertIn("обновлены: 1; ошибок: 1", combined)
        self.assertNotIn("private-owner", combined)
        self.assertNotIn("synthetic-secret-marker", combined)

    def test_batch_refresh_requires_complete_source_before_reading_file(self) -> None:
        errors = io.StringIO()
        marker = "private-owner/private-repository"
        with (
            patch("pathlib.Path.open", side_effect=AssertionError(marker)),
            redirect_stderr(errors),
        ):
            exit_code = main(
                [
                    "--repositories-file",
                    str(Path.cwd() / "repositories.txt"),
                    "--output-dir",
                    str(Path.cwd()),
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertNotIn(marker, errors.getvalue())

    def test_batch_refresh_rejects_oversized_or_excessive_input_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cases = {
                "oversized": b"a" * (MAX_REPOSITORIES_FILE_BYTES + 1),
                "too-many": "".join(
                    f"example-org/repository-{index}\n" for index in range(MAX_REPOSITORIES + 1)
                ).encode(),
                "invalid": b"https://sourcecraft.invalid/private-owner/private-repository\n",
            }
            for name, content in cases.items():
                with self.subTest(name=name):
                    repositories_file = root / f"{name}.txt"
                    repositories_file.write_bytes(content)
                    exporter = Mock()
                    with (
                        patch("scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecApiProbe"),
                        patch(
                            "scripts.export_sourcecraft_appsec_snapshot.SourceCraftAppSecCliSnapshotExporter",
                            return_value=exporter,
                        ),
                        redirect_stderr(io.StringIO()),
                    ):
                        exit_code = main(
                            [
                                "--repositories-file",
                                str(repositories_file),
                                "--output-dir",
                                str(root),
                                "--appsec-env",
                                "AppSecRead",
                            ]
                        )
                    self.assertEqual(exit_code, 2)
                    exporter.assert_not_called()
