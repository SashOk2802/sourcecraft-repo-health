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
from scripts.export_sourcecraft_appsec_snapshot import main


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
                {"reader_gid": DEFAULT_SNAPSHOT_READER_GID},
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
