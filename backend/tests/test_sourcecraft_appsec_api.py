"""Observed AppSec pagination contract, with synthetic data and no credentials."""

from __future__ import annotations

import json
import subprocess
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from backend.app.analyzers import security
from backend.app.contracts import RepositoryRef
from backend.app.integrations.sourcecraft_appsec_api import SourceCraftAppSecApiProbe
from backend.app.integrations.sourcecraft_appsec_facts import build_security_facts_from_results
from backend.app.integrations.sourcecraft_appsec_snapshot import (
    SourceCraftAppSecCliSnapshotExporter,
)

REPOSITORY_ID = str(UUID(int=1))
SCAN_ID = str(UUID(int=2))
COMMIT = "a" * 40
PRIVATE = "synthetic-private-marker"


def finding(index: int) -> dict:
    return {
        "uuid": str(UUID(int=index + 100)),
        "gitRepo": "internal-repository",
        "latestCommit": COMMIT,
        "engineType": 3,
        "severity": 3,
        "status": 0,
        "codeBlock": PRIVATE,
        "fileName": PRIVATE,
        "ruleName": PRIVATE,
    }


class FakeSource:
    def __init__(self, count: int = 1):
        self.items = [finding(i) for i in range(count)]
        self.scan = {
            "uuid": SCAN_ID,
            "isLatest": True,
            "scanType": "SCAN_TYPE_DEFAULT",
            "commitHash": COMMIT,
            "totalDefectGroups": count,
            "status": "FINISHED",
            "gitRepo": "internal-repository",
            "timeFinished": 1790000000000,
        }
        self.commands: list[list[str]] = []
        self.page_mutation = lambda page, fields: page
        self.scan_reads = 0
        self.change_scan = False
        self.api_url = "https://appsec.sourcecraft.tech"

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        assert kwargs == {"capture_output": True, "check": False, "text": True, "timeout": 20}
        if command[1:3] == ["envs", "list"]:
            payload = [
                {"name": "AppSecRead", "api": self.api_url, "active": False},
                {"name": "ExtProd", "api": "https://api.sourcecraft.tech", "active": True},
            ]
        elif "repos/example-org/example-repo" in command:
            payload = {"id": REPOSITORY_ID}
        else:
            assert command[1:3] == ["--env", "AppSecRead"]
            path = command[command.index("GET") + 1]
            fields = dict(
                command[i + 1].split("=", 1) for i, arg in enumerate(command) if arg == "-f"
            )
            assert fields["gitRepo"] == REPOSITORY_ID
            if path == "v1/scans":
                self.scan_reads += 1
                if self.change_scan and self.scan_reads == 2:
                    self.scan["uuid"] = str(UUID(int=3))
                payload = {"data": [deepcopy(self.scan)], "nextPageToken": "", "totalSize": 1}
            elif path.startswith("v1/scans/"):
                payload = deepcopy(self.scan)
            else:
                assert path == "v1/defect-groups"
                assert fields["scanUuid"] == SCAN_ID
                items = self.items if fields["type"] == "SAST" else []
                start = int(fields.get("pageToken", 0))
                end = start + int(fields["pageSize"])
                payload = {
                    "data": deepcopy(items[start:end]),
                    "totalSize": len(items),
                    "nextPageToken": str(end) if end < len(items) else "",
                }
                payload = self.page_mutation(payload, fields)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), PRIVATE)


def probe(source) -> tuple:
    return SourceCraftAppSecApiProbe(appsec_environment="AppSecRead", runner=source).probe_all(
        "example-org/example-repo"
    )


class AppSecApiTest(unittest.TestCase):
    def test_retries_transient_read_only_cli_failures(self):
        source = FakeSource()
        attempts = 0

        def runner(command, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return subprocess.CompletedProcess(command, 1, PRIVATE, PRIVATE)
            return source(command, **kwargs)

        results = probe(runner)

        self.assertEqual(results[0].finding_count, 1)
        self.assertEqual(attempts, len(source.commands) + 1)
        self.assertNotIn(PRIVATE, repr(results))

    def test_retries_transient_cli_timeout(self):
        source = FakeSource()
        attempts = 0

        def runner(command, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=PRIVATE)
            return source(command, **kwargs)

        results = probe(runner)

        self.assertEqual(results[0].finding_count, 1)
        self.assertEqual(attempts, len(source.commands) + 1)
        self.assertNotIn(PRIVATE, repr(results))

    def test_collects_over_100_items_and_keeps_empty_engines_unavailable(self):
        source = FakeSource(201)
        results = probe(source)
        sast, sca, secrets = results
        self.assertEqual(
            (sast.finding_count, sast.completeness, sast.scan_commit_sha), (201, "complete", COMMIT)
        )
        self.assertEqual(sast.finding_groups[0].count, 201)
        self.assertEqual(sast.finding_groups[0].severity, "HIGH")
        self.assertEqual(sast.scan_uuid, SCAN_ID)
        self.assertEqual(sca.availability, "unavailable")
        self.assertEqual(secrets.availability, "unavailable")
        self.assertIsNone(sca.scan_commit_sha)
        self.assertNotIn(PRIVATE, repr(results))
        self.assertNotIn(COMMIT, json.dumps([r.as_dict() for r in results]))
        self.assertNotIn(SCAN_ID, json.dumps([r.as_dict() for r in results]))
        self.assertTrue(any("pageToken=200" in c for c in source.commands))
        facts = build_security_facts_from_results(results)
        self.assertEqual(facts.scan_uuid, SCAN_ID)
        result = security.evaluate(
            facts, repository=RepositoryRef(REPOSITORY_ID, "example-org", "example-repo")
        )
        self.assertEqual(result.status, "insufficient_sample")
        self.assertIsNone(result.score)
        self.assertIn("scan_id", result.metrics[1].evidence[0].url or "")

    def test_snapshot_bridge_uses_source_commit_for_complete_nonempty_data(self):
        results = probe(FakeSource())
        with patch("backend.app.integrations.sourcecraft_appsec_snapshot.write_snapshot") as write:
            exporter = SourceCraftAppSecCliSnapshotExporter(
                type("Probe", (), {"probe_all": lambda self, repo: results})(),
                runner=lambda *args, **kwargs: subprocess.CompletedProcess(
                    args[0], 0, json.dumps({"id": REPOSITORY_ID}), ""
                ),
            )
            exporter.export("example-org/example-repo", Path.cwd())
            self.assertEqual(write.call_args.kwargs["commit_sha"], COMMIT)
            self.assertEqual(write.call_args.kwargs["results"][0].completeness, "complete")

    def test_invalid_pages_never_return_a_partial_success(self):
        def duplicate(page, fields):
            if fields.get("pageToken"):
                page["data"][0]["uuid"] = finding(0)["uuid"]
            return page

        def changed_total(page, fields):
            if fields.get("pageToken"):
                page["totalSize"] += 1
            return page

        mutations = {
            "duplicate": duplicate,
            "changed_total": changed_total,
            "truncated": lambda p, f: {**p, "nextPageToken": ""},
            "loop": lambda p, f: {**p, "nextPageToken": "100"},
            "empty_nonterminal": lambda p, f: {**p, "data": []},
            "missing_total": lambda p, f: {k: v for k, v in p.items() if k != "totalSize"},
            "bool_total": lambda p, f: {**p, "totalSize": True},
            "oversized_total": lambda p, f: {**p, "totalSize": 10001},
            "missing_token": lambda p, f: {k: v for k, v in p.items() if k != "nextPageToken"},
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name):
                source = FakeSource(201)
                source.page_mutation = mutation
                self.assertTrue(all(r.availability == "error" for r in probe(source)))

    def test_scan_rollover_even_at_the_same_commit_is_rejected(self):
        source = FakeSource()
        source.change_scan = True
        self.assertTrue(all(r.availability == "error" for r in probe(source)))

    def test_inconsistent_scan_or_finding_is_rejected(self):
        changes = [
            ("item", "latestCommit", "b" * 40),
            ("item", "gitRepo", "another-repository"),
            ("item", "engineType", 999),
            ("item", "uuid", "not-a-uuid"),
            ("scan", "commitHash", "invalid"),
            ("scan", "totalDefectGroups", 2),
            ("scan", "timeFinished", None),
            ("scan", "scanType", "UNKNOWN"),
        ]
        for target, key, value in changes:
            with self.subTest(target=target, key=key):
                source = FakeSource()
                (source.items[0] if target == "item" else source.scan)[key] = value
                self.assertTrue(all(r.availability == "error" for r in probe(source)))

    def test_unfinished_scan_and_no_findings_are_not_clean_security(self):
        source = FakeSource()
        source.scan["status"] = "PROCESSING"
        self.assertTrue(all(r.availability == "unavailable" for r in probe(source)))
        self.assertFalse(any("v1/defect-groups" in c for c in source.commands))
        self.assertTrue(all(r.availability == "unavailable" for r in probe(FakeSource(0))))

    def test_unknown_numeric_severity_or_status_remains_unknown(self):
        source = FakeSource()
        source.items[0].update(severity=999, status=999)
        sast = probe(source)[0]
        self.assertEqual(sast.completeness, "complete")
        self.assertIsNone(sast.finding_groups[0].severity)
        self.assertIsNone(sast.finding_groups[0].status)

    def test_wrong_environment_stops_before_repository_requests(self):
        source = FakeSource()
        source.api_url = "https://untrusted.example"
        self.assertTrue(all(r.availability == "error" for r in probe(source)))
        self.assertEqual(len(source.commands), 1)

    def test_failures_do_not_expose_raw_output(self):
        for response in [
            subprocess.CompletedProcess([], 1, PRIVATE, PRIVATE),
            subprocess.CompletedProcess([], 0, PRIVATE, PRIVATE),
            subprocess.TimeoutExpired(PRIVATE, 20, output=PRIVATE, stderr=PRIVATE),
            OSError(PRIVATE),
        ]:
            with self.subTest(response=type(response).__name__):

                def runner(*args, response=response, **kwargs):
                    if isinstance(response, Exception):
                        raise response
                    return response

                results = probe(runner)
                self.assertTrue(all(r.availability == "error" for r in results))
                self.assertNotIn(PRIVATE, repr(results))

    def test_invalid_repository_never_calls_cli(self):
        source = FakeSource()
        with self.assertRaises(ValueError):
            SourceCraftAppSecApiProbe(appsec_environment="AppSecRead", runner=source).probe_all(
                "../repo"
            )
        self.assertEqual(source.commands, [])
