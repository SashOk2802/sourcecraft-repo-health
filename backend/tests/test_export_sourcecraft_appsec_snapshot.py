"""Проверяет CLI exporter безопасного AppSec snapshot без SourceCraft."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.export_sourcecraft_appsec_snapshot import main


class ExportSourceCraftAppSecSnapshotTest(unittest.TestCase):
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
            self.assertEqual(exporter.export.call_args.args, ("example-org/example-repo", Path(temporary_directory)))
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
