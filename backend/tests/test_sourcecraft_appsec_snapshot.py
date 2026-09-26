"""Проверяет безопасный hand-off реальной AppSec-сводки в web-worker."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.analysis.providers import sourcecraft_analyzer_provider
from backend.app.analyzers.security import evaluate, make_context_analyzer
from backend.app.contracts import AnalysisContext, DataStatus, RepositoryRef
from backend.app.integrations.sourcecraft_appsec_probe import (
    AppSecFindingGroup,
    AppSecProbeResult,
)
from backend.app.integrations.sourcecraft_appsec_snapshot import (
    MAX_SNAPSHOT_BYTES,
    SourceCraftAppSecCliSnapshotExporter,
    SourceCraftAppSecSnapshotExportError,
    SourceCraftAppSecSnapshotSettings,
    SourceCraftAppSecSnapshotStore,
    repository_fingerprint,
    snapshot_filename,
    snapshot_settings_from_environment,
    write_snapshot,
)

COMMIT_SHA = "a" * 40


class SourceCraftAppSecSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 26, 12, tzinfo=UTC)
        self.context = AnalysisContext(
            repository=RepositoryRef("repository-id-redacted", "example-org", "example-repo"),
            commit_sha=COMMIT_SHA,
            analyzed_at=self.now,
            period_start=self.now - timedelta(days=30),
            period_end=self.now,
        )
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.snapshot_directory = Path(self.temporary_directory.name)
        self.store = SourceCraftAppSecSnapshotStore(
            SourceCraftAppSecSnapshotSettings(
                directory=self.snapshot_directory,
                max_age=timedelta(minutes=10),
            ),
            clock=lambda: self.now,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_complete_safe_snapshot_produces_measured_security_score(self) -> None:
        destination = self._write(_complete_results())

        facts = self.store.collect(self.context)
        result = evaluate(facts)

        self.assertEqual(destination.name, snapshot_filename(self.context.repository.id))
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 85.0)
        self.assertEqual(result.reason, "security_score_v1")
        self.assertNotIn(self.context.repository.id, destination.read_text(encoding="utf-8"))
        self.assertNotIn("example-org", destination.read_text(encoding="utf-8"))

    def test_current_cli_snapshot_is_real_evidence_but_not_a_false_score(self) -> None:
        self._write(
            (
                AppSecProbeResult(
                    "SAST",
                    "available",
                    1,
                    severities=("HIGH",),
                    finding_groups=(AppSecFindingGroup("HIGH", "OPEN", 1),),
                    completeness="unknown",
                    scan_commit_sha=COMMIT_SHA,
                ),
                AppSecProbeResult(
                    "SCA", "unavailable", None, reason="sourcecraft_appsec_unavailable"
                ),
                AppSecProbeResult(
                    "SECRETS", "unavailable", None, reason="sourcecraft_appsec_unavailable"
                ),
            )
        )

        result = make_context_analyzer(self.store.collect)(self.context)

        self.assertEqual(result.status, DataStatus.INSUFFICIENT_SAMPLE)
        self.assertIsNone(result.score)
        self.assertEqual(result.reason, "appsec_coverage_not_confirmed")

    def test_missing_stale_or_mismatched_snapshot_is_unavailable(self) -> None:
        cases = (
            (None, self.context.repository.id, COMMIT_SHA),
            (self.now - timedelta(minutes=11), self.context.repository.id, COMMIT_SHA),
            (self.now, "another-repository-id", COMMIT_SHA),
            (self.now, self.context.repository.id, "b" * 40),
            (self.now + timedelta(minutes=6), self.context.repository.id, COMMIT_SHA),
        )
        for collected_at, repository_id, commit_sha in cases:
            with self.subTest(collected_at=collected_at, repository_id=repository_id):
                for path in self.snapshot_directory.iterdir():
                    path.unlink()
                if collected_at is not None:
                    write_snapshot(
                        self.snapshot_directory,
                        repository_id=repository_id,
                        commit_sha=commit_sha,
                        collected_at=collected_at,
                        results=_complete_results(scan_commit_sha=commit_sha),
                    )

                result = evaluate(self.store.collect(self.context))

                self.assertEqual(result.status, DataStatus.UNAVAILABLE)
                self.assertIsNone(result.score)

    def test_unsafe_snapshot_becomes_redacted_source_error(self) -> None:
        destination = self._write(_complete_results())
        payload = json.loads(destination.read_text(encoding="utf-8"))
        payload["unexpected"] = "synthetic-secret-marker"
        destination.write_text(json.dumps(payload), encoding="utf-8")

        facts = self.store.collect(self.context)
        result = evaluate(facts)

        self.assertEqual(facts.source_error, "sourcecraft_appsec_snapshot_invalid")
        self.assertEqual(result.status, DataStatus.ERROR)
        self.assertNotIn("synthetic-secret-marker", repr(facts))
        self.assertNotIn("synthetic-secret-marker", result.summary)
        self.assertNotIn("synthetic-secret-marker", result.reason or "")

    def test_boolean_schema_version_and_non_hex_fingerprint_are_rejected(self) -> None:
        for field, invalid_value in (
            ("schema_version", True),
            ("repository_id_sha256", "z" * 64),
            ("commit_sha", "z" * 40),
        ):
            with self.subTest(field=field):
                destination = self._write(_complete_results())
                payload = json.loads(destination.read_text(encoding="utf-8"))
                payload[field] = invalid_value
                destination.write_text(json.dumps(payload), encoding="utf-8")

                facts = self.store.collect(self.context)

                self.assertEqual(facts.source_error, "sourcecraft_appsec_snapshot_invalid")

    def test_oversized_or_symlink_snapshot_is_never_read(self) -> None:
        destination = self.snapshot_directory / snapshot_filename(self.context.repository.id)
        destination.write_bytes(b"x" * (MAX_SNAPSHOT_BYTES + 1))
        oversized = self.store.collect(self.context)
        self.assertEqual(oversized.source_error, "sourcecraft_appsec_snapshot_unreadable")

        if not hasattr(os, "O_NOFOLLOW"):
            return
        destination.unlink()
        target = self.snapshot_directory / "raw-sourcecraft-output.json"
        target.write_text('{"private":"synthetic-secret-marker"}', encoding="utf-8")
        destination.symlink_to(target)

        symlinked = self.store.collect(self.context)

        self.assertEqual(symlinked.source_error, "sourcecraft_appsec_snapshot_unreadable")
        self.assertNotIn("synthetic-secret-marker", repr(symlinked))

    def test_symlink_snapshot_directory_is_never_traversed(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are not available on this platform")
        real_directory = self.snapshot_directory / "real-snapshots"
        real_directory.mkdir()
        symlink_directory = self.snapshot_directory / "linked-snapshots"
        symlink_directory.symlink_to(real_directory, target_is_directory=True)
        store = SourceCraftAppSecSnapshotStore(
            SourceCraftAppSecSnapshotSettings(
                directory=symlink_directory,
                max_age=timedelta(minutes=10),
            ),
            clock=lambda: self.now,
        )

        facts = store.collect(self.context)

        self.assertEqual(facts.source_error, "sourcecraft_appsec_snapshot_unreadable")

    def test_exporter_rejects_writable_shared_directory(self) -> None:
        shared_directory = self.snapshot_directory / "shared"
        shared_directory.mkdir(mode=0o700)
        shared_directory.chmod(0o770)

        with self.assertRaisesRegex(ValueError, "must not be group or world writable"):
            write_snapshot(
                shared_directory,
                repository_id=self.context.repository.id,
                commit_sha=self.context.commit_sha,
                collected_at=self.now,
                results=_complete_results(),
            )

    def test_environment_requires_absolute_directory_and_positive_age(self) -> None:
        self.assertIsNone(snapshot_settings_from_environment({}))
        with self.assertRaisesRegex(ValueError, "absolute"):
            snapshot_settings_from_environment({"SOURCECRAFT_APPSEC_SNAPSHOT_DIR": "snapshots"})
        with self.assertRaisesRegex(ValueError, "positive"):
            snapshot_settings_from_environment(
                {
                    "SOURCECRAFT_APPSEC_SNAPSHOT_DIR": str(self.snapshot_directory),
                    "SOURCECRAFT_APPSEC_SNAPSHOT_MAX_AGE_SECONDS": "0",
                }
            )
        settings = snapshot_settings_from_environment(
            {"SOURCECRAFT_APPSEC_SNAPSHOT_DIR": str(self.snapshot_directory)}
        )
        self.assertEqual(settings.directory, self.snapshot_directory)

    def test_snapshot_contains_only_fingerprint_not_repository_identifier(self) -> None:
        destination = self._write(_complete_results())
        payload = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(payload["repository_id_sha256"], repository_fingerprint(self.context.repository.id))
        self.assertNotIn("repository_id", payload)
        self.assertNotIn("repository_slug", payload)
        self.assertNotIn("organization_slug", payload)

    def test_production_provider_reads_only_configured_safe_snapshot_directory(self) -> None:
        write_snapshot(
            self.snapshot_directory,
            repository_id=self.context.repository.id,
            commit_sha=self.context.commit_sha,
            collected_at=datetime.now(UTC),
            results=_complete_results(),
        )
        with patch.dict(
            os.environ,
            {"SOURCECRAFT_APPSEC_SNAPSHOT_DIR": str(self.snapshot_directory)},
            clear=False,
        ):
            registrations = tuple(sourcecraft_analyzer_provider(self.context))

        security_registration = next(
            registration for registration in registrations if registration.category == "security"
        )
        result = security_registration.evaluate(self.context)

        self.assertEqual(result.status, DataStatus.MEASURED)
        self.assertEqual(result.score, 85.0)

    def test_cli_exporter_derives_repository_id_and_commit_from_sourcecraft(self) -> None:
        commands: list[list[str]] = []
        probe = Mock()
        probe.probe_all.return_value = _complete_results()

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            self.assertTrue(kwargs["capture_output"])
            self.assertFalse(kwargs["check"])
            self.assertTrue(kwargs["text"])
            self.assertEqual(kwargs["timeout"], 17)
            return subprocess.CompletedProcess(command, 0, stdout='{"id":"repository-id-redacted"}')

        destination = SourceCraftAppSecCliSnapshotExporter(
            probe,
            cli_binary="/safe/path/src",
            timeout_seconds=17,
            runner=runner,
            clock=lambda: self.now,
        ).export("example-org/example-repo", self.snapshot_directory)
        payload = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(probe.probe_all.call_args.args, ("example-org/example-repo",))
        self.assertEqual(
            commands,
            [["/safe/path/src", "api", "-X", "GET", "repos/example-org/example-repo", "--json"]],
        )
        self.assertEqual(payload["commit_sha"], COMMIT_SHA)
        self.assertEqual(payload["repository_id_sha256"], repository_fingerprint("repository-id-redacted"))
        self.assertNotIn("repository-id-redacted", destination.read_text(encoding="utf-8"))

    def test_cli_exporter_rejects_unbound_or_disagreeing_scan_commits(self) -> None:
        cases = (
            _complete_results(scan_commit_sha=None),
            _complete_results(scan_commit_sha="b" * 40, sca_commit_sha=COMMIT_SHA),
        )
        for results in cases:
            with self.subTest(results=results):
                probe = Mock()
                probe.probe_all.return_value = results
                runner = Mock()
                exporter = SourceCraftAppSecCliSnapshotExporter(
                    probe,
                    runner=runner,
                    clock=lambda: self.now,
                )

                with self.assertRaises(SourceCraftAppSecSnapshotExportError):
                    exporter.export("example-org/example-repo", self.snapshot_directory)

                runner.assert_not_called()

    def test_cli_exporter_rejects_unsafe_repository_before_calling_sourcecraft(self) -> None:
        probe = Mock()
        runner = Mock()
        exporter = SourceCraftAppSecCliSnapshotExporter(probe, runner=runner)

        for repository in ("example-org/../other", "example-org/repo/extra", "example org/repo"):
            with self.subTest(repository=repository), self.assertRaises((TypeError, ValueError)):
                exporter.export(repository, self.snapshot_directory)

        probe.probe_all.assert_not_called()
        runner.assert_not_called()

    def test_cli_exporter_redacts_repository_metadata_failure(self) -> None:
        private_marker = "private-sourcecraft-error-marker"
        probe = Mock()
        probe.probe_all.return_value = _complete_results()

        def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(command, 1, stderr=private_marker)

        exporter = SourceCraftAppSecCliSnapshotExporter(probe, runner=runner)

        with self.assertRaises(SourceCraftAppSecSnapshotExportError) as raised:
            exporter.export("example-org/example-repo", self.snapshot_directory)

        self.assertNotIn(private_marker, str(raised.exception))
        self.assertEqual(list(self.snapshot_directory.iterdir()), [])

    def test_manual_commit_cannot_disagree_with_sourcecraft_commit(self) -> None:
        with self.assertRaisesRegex(ValueError, "must match"):
            write_snapshot(
                self.snapshot_directory,
                repository_id=self.context.repository.id,
                commit_sha="b" * 40,
                collected_at=self.now,
                results=_complete_results(),
            )

    def _write(self, results: tuple[AppSecProbeResult, ...]) -> Path:
        return write_snapshot(
            self.snapshot_directory,
            repository_id=self.context.repository.id,
            commit_sha=self.context.commit_sha,
            collected_at=self.now,
            results=results,
        )


def _complete_results(
    *,
    scan_commit_sha: str | None = COMMIT_SHA,
    sca_commit_sha: str | None = None,
) -> tuple[AppSecProbeResult, ...]:
    sca_commit = scan_commit_sha if sca_commit_sha is None else sca_commit_sha
    return (
        AppSecProbeResult(
            "SAST",
            "available",
            1,
            severities=("HIGH",),
            finding_groups=(AppSecFindingGroup("HIGH", "OPEN", 1),),
            completeness="complete",
            scan_commit_sha=scan_commit_sha,
        ),
        AppSecProbeResult(
            "SCA",
            "available",
            0,
            finding_groups=(),
            completeness="complete",
            scan_commit_sha=sca_commit,
        ),
        AppSecProbeResult(
            "SECRETS",
            "available",
            0,
            finding_groups=(),
            completeness="complete",
            scan_commit_sha=scan_commit_sha,
        ),
    )
