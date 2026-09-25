"""Проверяет CycloneDX SBOM перед публикацией как CI-артефакта."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

MAX_SBOM_BYTES = 10 * 1024 * 1024
MAX_COMPONENTS = 10_000
MAX_STRING_LENGTH = 20_000
SUPPORTED_SPEC_VERSIONS = frozenset({"1.4", "1.5", "1.6", "1.7"})
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "apikey",
        "api_key",
        "key",
        "password",
        "secret",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-signature",
    }
)
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


class SbomValidationError(ValueError):
    """SBOM не соответствует безопасному контракту публикации."""


def validate_sbom(
    path: Path,
    *,
    expected_root: str,
    minimum_components: int = 1,
) -> dict[str, Any]:
    """Читает и проверяет один CycloneDX JSON-файл.

    Проверка ограничивает размер и число компонентов, валидирует граф зависимостей
    и не позволяет опубликовать credential URL или локальные пути сборочной машины.
    """

    if not isinstance(minimum_components, int) or isinstance(minimum_components, bool):
        raise TypeError("minimum_components must be an integer")
    if minimum_components <= 0 or minimum_components > MAX_COMPONENTS:
        raise ValueError("minimum_components must fit the configured component limit")
    if not expected_root or not isinstance(expected_root, str):
        raise ValueError("expected_root must be a non-empty string")
    if path.is_symlink() or not path.is_file():
        raise SbomValidationError("SBOM path must be a regular file")

    size = path.stat().st_size
    if size <= 0:
        raise SbomValidationError("SBOM file is empty")
    if size > MAX_SBOM_BYTES:
        raise SbomValidationError("SBOM file exceeds the size limit")

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SbomValidationError("SBOM must be valid UTF-8 JSON") from error

    if not isinstance(document, dict):
        raise SbomValidationError("SBOM root must be an object")
    if document.get("bomFormat") != "CycloneDX":
        raise SbomValidationError("SBOM must use the CycloneDX format")
    if document.get("specVersion") not in SUPPORTED_SPEC_VERSIONS:
        raise SbomValidationError("SBOM uses an unsupported CycloneDX version")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        raise SbomValidationError("SBOM metadata must be an object")
    root_component = metadata.get("component")
    if not isinstance(root_component, dict) or not _matches_expected_root(
        root_component, expected_root
    ):
        raise SbomValidationError("SBOM root component does not match the project")

    components = document.get("components")
    if not isinstance(components, list):
        raise SbomValidationError("SBOM components must be an array")
    if not minimum_components <= len(components) <= MAX_COMPONENTS:
        raise SbomValidationError("SBOM component count is outside the configured limits")

    known_refs: dict[str, tuple[str, str, str | None]] = {}
    root_ref = _optional_non_empty_string(root_component, "bom-ref", "root component")
    if root_ref is not None:
        known_refs[root_ref] = _component_identity(root_component, "root component")

    for index, component in enumerate(components):
        label = f"component {index}"
        if not isinstance(component, dict):
            raise SbomValidationError(f"{label} must be an object")
        identity = _component_identity(component, label)
        bom_ref = _optional_non_empty_string(component, "bom-ref", label)
        if bom_ref is not None:
            previous_identity = known_refs.get(bom_ref)
            if previous_identity is not None and previous_identity != identity:
                raise SbomValidationError("SBOM contains a conflicting bom-ref value")
            known_refs[bom_ref] = identity

    dependencies = document.get("dependencies")
    if dependencies is not None:
        if not isinstance(dependencies, list):
            raise SbomValidationError("SBOM dependencies must be an array")
        _validate_dependency_graph(dependencies, set(known_refs))

    _validate_safe_strings(document)
    return document


def _required_non_empty_string(container: dict[str, Any], key: str, label: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SbomValidationError(f"{label} must have a non-empty {key}")
    return value


def _component_identity(
    component: dict[str, Any], label: str
) -> tuple[str, str, str | None]:
    name = _required_non_empty_string(component, "name", label)
    version = _required_non_empty_string(component, "version", label)
    purl = component.get("purl")
    if purl is not None and (not isinstance(purl, str) or not purl.strip()):
        raise SbomValidationError(f"{label} has an invalid purl")
    return name, version, purl


def _matches_expected_root(component: dict[str, Any], expected_root: str) -> bool:
    if component.get("name") == expected_root:
        return True

    purl = component.get("purl")
    if not isinstance(purl, str) or not purl.startswith("pkg:"):
        return False
    without_suffix = purl.split("?", 1)[0].split("#", 1)[0]
    try:
        coordinates = without_suffix.split("/", 1)[1]
        package_name = coordinates.rsplit("@", 1)[0]
    except (IndexError, ValueError):
        return False
    return unquote(package_name) == expected_root


def _optional_non_empty_string(
    container: dict[str, Any], key: str, label: str
) -> str | None:
    value = container.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SbomValidationError(f"{label} has an invalid {key}")
    return value


def _validate_dependency_graph(dependencies: list[Any], known_refs: set[str]) -> None:
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, dict):
            raise SbomValidationError(f"dependency {index} must be an object")
        reference = _required_non_empty_string(dependency, "ref", f"dependency {index}")
        targets = dependency.get("dependsOn", [])
        if not isinstance(targets, list) or not all(isinstance(item, str) for item in targets):
            raise SbomValidationError("dependency dependsOn must be an array of strings")
        if known_refs and (reference not in known_refs or not set(targets) <= known_refs):
            raise SbomValidationError("dependency graph refers to an unknown component")


def _validate_safe_strings(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SbomValidationError("SBOM object keys must be strings")
            _validate_safe_string(key)
            _validate_safe_strings(item)
    elif isinstance(value, list):
        for item in value:
            _validate_safe_strings(item)
    elif isinstance(value, str):
        _validate_safe_string(value)


def _validate_safe_string(value: str) -> None:
    if len(value) > MAX_STRING_LENGTH:
        raise SbomValidationError("SBOM contains an oversized string")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise SbomValidationError("SBOM contains a forbidden control character")
    if value.startswith(("file://", "/home/", "/Users/", "/tmp/")):
        raise SbomValidationError("SBOM contains a local build path")
    if WINDOWS_ABSOLUTE_PATH.match(value):
        raise SbomValidationError("SBOM contains a local build path")

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return
    if parsed.username is not None or parsed.password is not None:
        raise SbomValidationError("SBOM contains credentials in a URL")
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    if query_keys & SENSITIVE_QUERY_KEYS:
        raise SbomValidationError("SBOM contains a sensitive URL query parameter")


def _positive_int(raw_value: str) -> int:
    value = int(raw_value)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-root", required=True)
    parser.add_argument("--minimum-components", type=_positive_int, default=1)
    arguments = parser.parse_args(argv)

    try:
        document = validate_sbom(
            arguments.path,
            expected_root=arguments.expected_root,
            minimum_components=arguments.minimum_components,
        )
    except (OSError, SbomValidationError, TypeError, ValueError) as error:
        print(f"SBOM validation failed: {error}", file=sys.stderr)
        return 1

    print(
        f"Validated {arguments.path}: {len(document['components'])} components, "
        f"CycloneDX {document['specVersion']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
