"""Проверяет CLI-зонд AppSec без SourceCraft, сети и настоящих finding'ов."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock

from backend.app.integrations.sourcecraft_appsec_probe import (
    APPSEC_ENGINES,
    AppSecProbeResult,
    SourceCraftAppSecCliProbe,
)


class SourceCraftAppSecCliProbeTest(unittest.TestCase):
    def test_available_response_returns_only_safe_aggregate(self) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            self.assertTrue(kwargs["capture_output"])
            self.assertFalse(kwargs["check"])
            self.assertTrue(kwargs["text"])
            self.assertEqual(kwargs["timeout"], 17)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='[{"severity":"high","snippet":"synthetic-secret-marker"},'
                '{"severity":"CRITICAL","rule":"synthetic-rule-marker"},'
                '{"severity":"synthetic-unknown-marker"}]',
            )

        result = SourceCraftAppSecCliProbe(
            cli_binary="/safe/path/src",
            timeout_seconds=17,
            runner=runner,
        ).probe("example-org/example-repo", "SAST")

        self.assertEqual(result.availability, "available")
        self.assertEqual(result.finding_count, 3)
        self.assertEqual(result.severities, ("CRITICAL", "HIGH"))
        self.assertIsNone(result.reason)
        self.assertNotIn("synthetic-secret-marker", repr(result))
        self.assertNotIn("synthetic-rule-marker", repr(result))
        self.assertNotIn("synthetic-unknown-marker", repr(result))
        self.assertNotIn("synthetic-unknown-marker", repr(result.as_dict()))
        self.assertEqual(
            commands,
            [
                [
                    "/safe/path/src",
                    "appsec",
                    "defect",
                    "list",
                    "--repo",
                    "example-org/example-repo",
                    "--type",
                    "SAST",
                    "--limit",
                    "100",
                    "--json",
                ]
            ],
        )

    def test_null_response_is_unavailable_not_zero_findings(self) -> None:
        runner = _runner(stdout="null")

        result = SourceCraftAppSecCliProbe(runner=runner).probe("example-org/example-repo", "SCA")

        self.assertEqual(result.availability, "unavailable")
        self.assertIsNone(result.finding_count)
        self.assertEqual(result.reason, "sourcecraft_appsec_unavailable")

    def test_rejects_unsafe_repository_before_starting_cli(self) -> None:
        runner = Mock()
        probe = SourceCraftAppSecCliProbe(runner=runner)

        for repository in ("", "owner", "owner/repo/extra", "../repo", "owner/../repo", "owner/repo?x=1"):
            with self.subTest(repository=repository), self.assertRaisesRegex(
                ValueError, "safe OWNER/REPOSITORY"
            ):
                probe.probe(repository, "SAST")

        runner.assert_not_called()

    def test_cli_error_does_not_expose_stderr(self) -> None:
        result = SourceCraftAppSecCliProbe(
            runner=_runner(returncode=1, stderr="private-repo synthetic-secret-marker")
        ).probe("example-org/example-repo", "SECRETS")

        self.assertEqual(result.availability, "error")
        self.assertEqual(result.reason, "sourcecraft_cli_error")
        self.assertNotIn("synthetic-secret-marker", repr(result))

    def test_invalid_json_and_payload_are_safe_errors(self) -> None:
        cases = (
            ("not-json", "sourcecraft_cli_invalid_json"),
            ("{}", "sourcecraft_cli_invalid_payload"),
            ('["raw-synthetic-marker"]', "sourcecraft_cli_invalid_payload"),
        )
        for stdout, reason in cases:
            with self.subTest(stdout=stdout):
                result = SourceCraftAppSecCliProbe(runner=_runner(stdout=stdout)).probe(
                    "example-org/example-repo", "SAST"
                )
                self.assertEqual(result.availability, "error")
                self.assertEqual(result.reason, reason)
                self.assertNotIn("raw-synthetic-marker", repr(result))

    def test_timeout_and_missing_cli_return_safe_errors(self) -> None:
        def timeout_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired("src", timeout=20, output="raw-synthetic-marker")

        def missing_runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("raw-synthetic-marker")

        for runner, reason in (
            (timeout_runner, "sourcecraft_cli_timeout"),
            (missing_runner, "sourcecraft_cli_unavailable"),
        ):
            with self.subTest(reason=reason):
                result = SourceCraftAppSecCliProbe(runner=runner).probe(
                    "example-org/example-repo", "SAST"
                )
                self.assertEqual(result.availability, "error")
                self.assertEqual(result.reason, reason)
                self.assertNotIn("raw-synthetic-marker", repr(result))

    def test_probe_all_attempts_every_engine_independently(self) -> None:
        engine_responses = iter(("null", "[]", "invalid"))
        commands: list[list[str]] = []

        def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=next(engine_responses))

        results = SourceCraftAppSecCliProbe(runner=runner).probe_all("example-org/example-repo")

        self.assertEqual([result.engine for result in results], list(APPSEC_ENGINES))
        self.assertEqual(
            [result.availability for result in results],
            ["unavailable", "available", "error"],
        )
        self.assertEqual(len(commands), 3)

    def test_result_rejects_non_safe_reason_and_unknown_severity(self) -> None:
        with self.assertRaisesRegex(ValueError, "known safe reason"):
            AppSecProbeResult("SAST", "error", None, reason="synthetic-secret-marker")
        with self.assertRaisesRegex(ValueError, "unknown severity"):
            AppSecProbeResult("SAST", "available", 1, severities=("synthetic-secret-marker",))


def _runner(
    *,
    stdout: str = "[]",
    returncode: int = 0,
    stderr: str = "",
):
    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)

    return runner
