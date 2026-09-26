"""Проверяет безопасную публикацию CycloneDX SBOM."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from scripts.validate_sbom import MAX_SBOM_BYTES, SbomValidationError, validate_sbom


class ValidateSbomTest(unittest.TestCase):
    def test_accepts_valid_cyclonedx_document(self) -> None:
        document = self._validate(self._document())

        self.assertEqual(document["bomFormat"], "CycloneDX")
        self.assertEqual(len(document["components"]), 1)

    def test_rejects_wrong_format_and_unsupported_version(self) -> None:
        for field, value in (("bomFormat", "SPDX"), ("specVersion", "1.3")):
            with self.subTest(field=field):
                document = self._document()
                document[field] = value
                with self.assertRaises(SbomValidationError):
                    self._validate(document)

    def test_rejects_wrong_root_component(self) -> None:
        document = self._document()
        document["metadata"]["component"]["name"] = "another-project"

        with self.assertRaisesRegex(SbomValidationError, "root component"):
            self._validate(document)

    def test_accepts_expected_root_from_package_url(self) -> None:
        document = self._document()
        root = document["metadata"]["component"]
        root["name"] = "workspace-directory"
        root["purl"] = "pkg:pypi/sourcecraft-repo-health@0.1.0"

        validated = self._validate(document)

        self.assertEqual(validated["metadata"]["component"]["name"], "workspace-directory")

    def test_rejects_empty_component_identity(self) -> None:
        for field in ("name", "version"):
            with self.subTest(field=field):
                document = self._document()
                document["components"][0][field] = ""
                with self.assertRaises(SbomValidationError):
                    self._validate(document)

    def test_rejects_duplicate_component_reference_for_the_same_identity(self) -> None:
        document = self._document()
        duplicate = deepcopy(document["components"][0])
        duplicate["properties"] = [{"name": "install-path", "value": "nested/httpx"}]
        document["components"].append(duplicate)

        with self.assertRaisesRegex(SbomValidationError, "duplicate bom-ref"):
            self._validate(document)

    def test_rejects_duplicate_component_reference_for_different_identity(self) -> None:
        document = self._document()
        conflicting = deepcopy(document["components"][0])
        conflicting["version"] = "9.9.9"
        document["components"].append(conflicting)

        with self.assertRaisesRegex(SbomValidationError, "duplicate bom-ref"):
            self._validate(document)

    def test_rejects_component_reference_that_duplicates_root(self) -> None:
        document = self._document()
        document["components"][0]["bom-ref"] = document["metadata"]["component"][
            "bom-ref"
        ]

        with self.assertRaisesRegex(SbomValidationError, "duplicate bom-ref"):
            self._validate(document)

    def test_rejects_unknown_dependency_reference(self) -> None:
        document = self._document()
        document["dependencies"][0]["dependsOn"].append("pkg:unknown/missing@1")

        with self.assertRaisesRegex(SbomValidationError, "unknown component"):
            self._validate(document)

    def test_rejects_dependency_graph_when_components_have_no_bom_refs(self) -> None:
        document = self._document()
        del document["metadata"]["component"]["bom-ref"]
        del document["components"][0]["bom-ref"]

        with self.assertRaisesRegex(SbomValidationError, "unknown component"):
            self._validate(document)

    def test_accepts_empty_dependency_graph_when_components_have_no_bom_refs(self) -> None:
        document = self._document()
        del document["metadata"]["component"]["bom-ref"]
        del document["components"][0]["bom-ref"]
        document["dependencies"] = []

        validated = self._validate(document)

        self.assertEqual(validated["dependencies"], [])

    def test_rejects_duplicate_dependency_ref(self) -> None:
        document = self._document()
        document["dependencies"].append(deepcopy(document["dependencies"][0]))

        with self.assertRaisesRegex(SbomValidationError, "duplicate dependency ref"):
            self._validate(document)

    def test_rejects_duplicate_depends_on_reference(self) -> None:
        document = self._document()
        target = document["components"][0]["bom-ref"]
        document["dependencies"][0]["dependsOn"].append(target)

        with self.assertRaisesRegex(SbomValidationError, "duplicate reference"):
            self._validate(document)

    def test_rejects_credential_url(self) -> None:
        document = self._document()
        document["components"][0]["externalReferences"] = [
            {"type": "distribution", "url": "https://user:secret@example.test/package"}
        ]

        with self.assertRaisesRegex(SbomValidationError, "credentials"):
            self._validate(document)

    def test_rejects_sensitive_url_query(self) -> None:
        document = self._document()
        document["components"][0]["externalReferences"] = [
            {"type": "distribution", "url": "https://example.test/package?token=secret"}
        ]

        with self.assertRaisesRegex(SbomValidationError, "sensitive URL"):
            self._validate(document)

    def test_rejects_local_build_path(self) -> None:
        document = self._document()
        document["components"][0]["properties"] = [
            {"name": "build-path", "value": "/home/runner/work/private-project"}
        ]

        with self.assertRaisesRegex(SbomValidationError, "local build path"):
            self._validate(document)

    def test_checks_minimum_component_count(self) -> None:
        with self.assertRaisesRegex(SbomValidationError, "component count"):
            self._validate(self._document(), minimum_components=2)

    def test_rejects_oversized_file_before_json_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oversized.cdx.json"
            with path.open("wb") as file_handle:
                file_handle.truncate(MAX_SBOM_BYTES + 1)

            with self.assertRaisesRegex(SbomValidationError, "size limit"):
                validate_sbom(path, expected_root="sourcecraft-repo-health")

    def _validate(
        self, document: dict, *, minimum_components: int = 1
    ) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bom.cdx.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            return validate_sbom(
                path,
                expected_root="sourcecraft-repo-health",
                minimum_components=minimum_components,
            )

    @staticmethod
    def _document() -> dict:
        package_ref = "pkg:pypi/httpx@0.28.1"
        root_ref = "pkg:pypi/sourcecraft-repo-health@0.1.0"
        return {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": "sourcecraft-repo-health",
                    "version": "0.1.0",
                    "bom-ref": root_ref,
                }
            },
            "components": [
                {
                    "type": "library",
                    "name": "httpx",
                    "version": "0.28.1",
                    "bom-ref": package_ref,
                }
            ],
            "dependencies": [
                {"ref": root_ref, "dependsOn": [package_ref]},
                {"ref": package_ref, "dependsOn": []},
            ],
        }
