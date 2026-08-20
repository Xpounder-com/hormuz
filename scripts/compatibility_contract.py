#!/usr/bin/env python3
"""Validate Hormuz's versioned support boundary and emit content-free evidence."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from typing import Any


MATRIX_SCHEMA = "hormuz.compatibility-matrix.v1"
EVIDENCE_SCHEMA = "hormuz.compatibility-evidence.v1"
MAX_MATRIX_BYTES = 512 * 1024
MAX_REFERENCE_BYTES = 8 * 1024 * 1024
SUPPORT_LEVELS = {
    "release_tested",
    "protocol_tested",
    "development_only",
    "unsupported",
    "pending_owner_decision",
}
CATEGORY_PREFIXES = {
    "employee_clients": "client.",
    "python_runtimes": "python.",
    "provider_protocols": "provider.",
    "identity": "identity.",
    "persistence": "persistence.",
    "deployment": "deployment.",
}
TOP_LEVEL_FIELDS = {
    "schema",
    "version",
    "product_stage",
    "enterprise_release_ready",
    "support_levels",
    "categories",
    "invariants",
}
ENTRY_FIELDS = {
    "id",
    "name",
    "version",
    "support_level",
    "production_supported",
    "tested_environments",
    "interfaces",
    "evidence",
    "limitations",
}
INVARIANT_FIELDS = {
    "latest_client_canary_release_blocking",
    "live_provider_conformance_verified",
    "real_idp_profile_verified",
    "production_postgresql_verified",
    "production_deployment_verified",
}
EXPECTED_CLIENTS = {
    "client.claude-code": ("@anthropic-ai/claude-code", "claude-code", "2.1.233"),
    "client.codex": ("@openai/codex", "codex", "0.147.0"),
}
EXPECTED_PYTHON_VERSIONS = ["3.11", "3.12", "3.13", "3.14"]
EXPECTED_PROVIDER_INTERFACES = {
    "provider.openai-responses": {
        "POST /v1/responses",
        "POST /v1/responses/compact",
    },
    "provider.anthropic-messages": {
        "POST /v1/messages",
        "POST /v1/messages/count_tokens",
        "GET /v1/models",
    },
    "provider.live-vendor-conformance": {"api.openai.com", "api.anthropic.com"},
}
EXPECTED_CONTAINER_BASE = "python:3.14.6-alpine3.23"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+$")
REFERENCE_PATH = re.compile(r"^[A-Za-z0-9_.\-/]+$")


class CompatibilityContractError(RuntimeError):
    """Raised when the compatibility matrix cannot support a release claim."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise CompatibilityContractError(
                "compatibility matrix JSON contains a duplicate member"
            )
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise CompatibilityContractError(
        "compatibility matrix JSON contains a non-standard number"
    )


def _read_json(path: Path, *, label: str, maximum: int) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise CompatibilityContractError(f"{label} is unavailable") from error
    if not raw or len(raw) > maximum:
        raise CompatibilityContractError(f"{label} size is invalid")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise CompatibilityContractError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict):
        raise CompatibilityContractError(f"{label} must be an object")
    return value, raw


def _bounded_string(value: Any, *, field: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise CompatibilityContractError(f"compatibility {field} is invalid")
    return value


def _string_list(
    value: Any,
    *,
    field: str,
    allow_empty: bool = False,
    maximum_items: int = 32,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise CompatibilityContractError(f"compatibility {field} is invalid")
    if not value and not allow_empty:
        raise CompatibilityContractError(f"compatibility entry requires {field}")
    result = [
        _bounded_string(item, field=field, maximum=1024)
        for item in value
    ]
    if len(set(result)) != len(result):
        raise CompatibilityContractError(f"compatibility {field} contains duplicates")
    return result


def _resolve_evidence_reference(reference: str, *, project_root: Path) -> None:
    raw_path, separator, selector = reference.partition("#")
    if (
        REFERENCE_PATH.fullmatch(raw_path) is None
        or raw_path.startswith("/")
        or ".." in Path(raw_path).parts
        or (separator and (not selector or len(selector) > 256))
        or (separator and any(ord(character) < 32 for character in selector))
    ):
        raise CompatibilityContractError("compatibility evidence reference is invalid")
    root = project_root.resolve()
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise CompatibilityContractError(
            "compatibility evidence reference escapes the project"
        ) from error
    if not candidate.is_file():
        raise CompatibilityContractError("compatibility evidence reference is unavailable")
    if selector:
        try:
            raw = candidate.read_bytes()
        except OSError as error:
            raise CompatibilityContractError(
                "compatibility evidence reference is unavailable"
            ) from error
        if len(raw) > MAX_REFERENCE_BYTES:
            raise CompatibilityContractError(
                "compatibility evidence reference is too large"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise CompatibilityContractError(
                "compatibility evidence reference is not UTF-8"
            ) from error
        if selector not in text:
            raise CompatibilityContractError(
                "compatibility evidence selector does not resolve"
            )


def _validate_entries(
    categories: dict[str, Any], *, project_root: Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if set(categories) != set(CATEGORY_PREFIXES):
        raise CompatibilityContractError("compatibility categories are incomplete")
    entries: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    identifiers: set[str] = set()
    for category, prefix in CATEGORY_PREFIXES.items():
        values = categories.get(category)
        if not isinstance(values, list) or not values or len(values) > 32:
            raise CompatibilityContractError(
                f"compatibility category {category} is invalid"
            )
        category_counts[category] = len(values)
        for entry in values:
            if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
                raise CompatibilityContractError(
                    "compatibility entry has unexpected fields"
                )
            identifier = _bounded_string(entry.get("id"), field="entry id", maximum=96)
            if (
                IDENTIFIER.fullmatch(identifier) is None
                or not identifier.startswith(prefix)
                or identifier in identifiers
            ):
                raise CompatibilityContractError("compatibility entry id is invalid")
            identifiers.add(identifier)
            _bounded_string(entry.get("name"), field="entry name")
            _bounded_string(entry.get("version"), field="entry version")
            support_level = entry.get("support_level")
            if support_level not in SUPPORT_LEVELS:
                raise CompatibilityContractError("compatibility support level is invalid")
            if not isinstance(entry.get("production_supported"), bool):
                raise CompatibilityContractError(
                    "compatibility production support flag is invalid"
                )
            if entry["production_supported"]:
                raise CompatibilityContractError(
                    "alpha compatibility matrix cannot claim production support"
                )
            tested = _string_list(
                entry.get("tested_environments"),
                field="tested environments",
                allow_empty=support_level in {"unsupported", "pending_owner_decision"},
            )
            if support_level in {"unsupported", "pending_owner_decision"} and tested:
                raise CompatibilityContractError(
                    "unsupported compatibility entry cannot claim tested environments"
                )
            interfaces = _string_list(entry.get("interfaces"), field="interfaces")
            evidence = _string_list(entry.get("evidence"), field="evidence")
            limitations = _string_list(entry.get("limitations"), field="limitations")
            if support_level == "release_tested" and len(evidence) < 2:
                raise CompatibilityContractError(
                    "release-tested compatibility entry requires multiple evidence references"
                )
            for reference in evidence:
                _resolve_evidence_reference(reference, project_root=project_root)
            normalized = dict(entry)
            normalized["tested_environments"] = tested
            normalized["interfaces"] = interfaces
            normalized["evidence"] = evidence
            normalized["limitations"] = limitations
            normalized["category"] = category
            entries.append(normalized)
    return entries, category_counts


def _parse_project(project_root: Path) -> dict[str, Any]:
    try:
        value = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CompatibilityContractError("project metadata is unavailable") from error
    project = value.get("project")
    if not isinstance(project, dict):
        raise CompatibilityContractError("project metadata is invalid")
    return project


def _validate_clients(entries: list[dict[str, Any]], *, project_root: Path) -> dict[str, str]:
    package, _ = _read_json(
        project_root / "deploy" / "clients" / "package.json",
        label="client package manifest",
        maximum=1024 * 1024,
    )
    dependencies = package.get("dependencies")
    engines = package.get("engines")
    if (
        not isinstance(dependencies, dict)
        or engines != {"node": "24.19.0"}
        or package.get("packageManager") != "npm@11.17.0"
    ):
        raise CompatibilityContractError("client fixture toolchain is invalid")
    clients = {
        entry["id"]: entry
        for entry in entries
        if entry["category"] == "employee_clients"
    }
    if set(clients) != set(EXPECTED_CLIENTS):
        raise CompatibilityContractError("compatibility client set is invalid")
    result: dict[str, str] = {}
    expected_environment = (
        "ubuntu-latest linux-x64 with Node.js 24.19.0 and npm 11.17.0"
    )
    for identifier, (package_name, output_name, expected_version) in EXPECTED_CLIENTS.items():
        entry = clients[identifier]
        if (
            dependencies.get(package_name) != expected_version
            or entry["version"] != expected_version
            or entry["support_level"] != "release_tested"
            or entry["tested_environments"] != [expected_environment]
        ):
            raise CompatibilityContractError(
                "compatibility client versions do not match the locked fixture"
            )
        result[output_name] = expected_version
    return dict(sorted(result.items()))


def _validate_python(entries: list[dict[str, Any]], *, project_root: Path) -> list[str]:
    runtimes = [
        entry
        for entry in entries
        if entry["category"] == "python_runtimes"
    ]
    versions = sorted((entry["version"] for entry in runtimes), key=lambda item: tuple(map(int, item.split("."))))
    identifiers = {entry["id"] for entry in runtimes}
    if (
        versions != EXPECTED_PYTHON_VERSIONS
        or identifiers != {f"python.{version}" for version in EXPECTED_PYTHON_VERSIONS}
        or any(entry["support_level"] != "release_tested" for entry in runtimes)
    ):
        raise CompatibilityContractError(
            "compatibility Python versions do not match the release matrix"
        )
    project = _parse_project(project_root)
    if project.get("requires-python") != ">=3.11":
        raise CompatibilityContractError(
            "compatibility Python versions do not match project metadata"
        )
    try:
        workflow = (project_root / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeDecodeError) as error:
        raise CompatibilityContractError("CI workflow is unavailable") from error
    if 'python-version: ["3.11", "3.12", "3.13", "3.14"]' not in workflow:
        raise CompatibilityContractError(
            "compatibility Python versions do not match CI"
        )
    return versions


def _validate_provider_interfaces(entries: list[dict[str, Any]], *, project_root: Path) -> None:
    providers = {
        entry["id"]: entry
        for entry in entries
        if entry["category"] == "provider_protocols"
    }
    if set(providers) != set(EXPECTED_PROVIDER_INTERFACES):
        raise CompatibilityContractError("compatibility provider set is invalid")
    for identifier, expected in EXPECTED_PROVIDER_INTERFACES.items():
        if set(providers[identifier]["interfaces"]) != expected:
            raise CompatibilityContractError(
                "compatibility provider interfaces do not match the gateway"
            )
    if (
        providers["provider.openai-responses"]["support_level"] != "protocol_tested"
        or providers["provider.anthropic-messages"]["support_level"] != "protocol_tested"
        or providers["provider.live-vendor-conformance"]["support_level"] != "unsupported"
    ):
        raise CompatibilityContractError("compatibility provider support level is invalid")
    try:
        server = (project_root / "hormuz" / "server.py").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CompatibilityContractError("gateway route source is unavailable") from error
    for route in (
        "/v1/responses",
        "/v1/responses/compact",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/models",
    ):
        if f'"{route}"' not in server:
            raise CompatibilityContractError(
                "compatibility provider interfaces do not match route source"
            )


def _validate_identity_and_persistence(entries: list[dict[str, Any]]) -> None:
    identity = {
        entry["id"]: entry for entry in entries if entry["category"] == "identity"
    }
    if (
        set(identity) != {"identity.generic-oidc", "identity.real-idp-profile"}
        or identity["identity.generic-oidc"]["support_level"] != "protocol_tested"
        or identity["identity.real-idp-profile"]["support_level"] != "unsupported"
    ):
        raise CompatibilityContractError("compatibility identity boundary is invalid")
    persistence = {
        entry["id"]: entry
        for entry in entries
        if entry["category"] == "persistence"
    }
    if (
        set(persistence) != {"persistence.sqlite", "persistence.postgresql"}
        or persistence["persistence.sqlite"]["support_level"] != "development_only"
        or persistence["persistence.postgresql"]["support_level"]
        != "development_only"
        or persistence["persistence.postgresql"]["version"]
        != "PostgreSQL 16.14 / Hormuz schema 2"
    ):
        raise CompatibilityContractError("compatibility persistence boundary is invalid")


def _validate_deployment(entries: list[dict[str, Any]], *, project_root: Path) -> None:
    deployments = {
        entry["id"]: entry
        for entry in entries
        if entry["category"] == "deployment"
    }
    if set(deployments) != {
        "deployment.python-package",
        "deployment.oci",
        "deployment.production-multinode",
    }:
        raise CompatibilityContractError("compatibility deployment set is invalid")
    project = _parse_project(project_root)
    if deployments["deployment.python-package"]["version"] != project.get("version"):
        raise CompatibilityContractError(
            "compatibility package version does not match project metadata"
        )
    try:
        dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CompatibilityContractError("container source is unavailable") from error
    match = re.search(r"^ARG PYTHON_IMAGE=([^@\s]+)@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)
    if (
        match is None
        or match.group(1) != EXPECTED_CONTAINER_BASE
        or deployments["deployment.oci"]["version"] != EXPECTED_CONTAINER_BASE
    ):
        raise CompatibilityContractError(
            "compatibility container base does not match the Dockerfile"
        )
    if (
        deployments["deployment.oci"]["support_level"] != "release_tested"
        or set(deployments["deployment.oci"]["interfaces"])
        != {"linux/amd64", "linux/arm64"}
        or deployments["deployment.production-multinode"]["support_level"]
        != "unsupported"
    ):
        raise CompatibilityContractError("compatibility deployment boundary is invalid")


def validate_compatibility_matrix(
    matrix_path: Path, *, project_root: Path
) -> dict[str, Any]:
    matrix, raw = _read_json(
        matrix_path,
        label="compatibility matrix",
        maximum=MAX_MATRIX_BYTES,
    )
    if set(matrix) != TOP_LEVEL_FIELDS:
        raise CompatibilityContractError(
            "compatibility matrix has unexpected top-level fields"
        )
    if matrix.get("schema") != MATRIX_SCHEMA:
        raise CompatibilityContractError("compatibility matrix schema is invalid")
    version = matrix.get("version")
    if not isinstance(version, str):
        raise CompatibilityContractError("compatibility matrix version is invalid")
    try:
        date.fromisoformat(version)
    except ValueError as error:
        raise CompatibilityContractError(
            "compatibility matrix version is invalid"
        ) from error
    if matrix.get("product_stage") != "alpha":
        raise CompatibilityContractError("compatibility product stage is invalid")
    if matrix.get("enterprise_release_ready") is not False:
        raise CompatibilityContractError(
            "compatibility matrix cannot claim enterprise release readiness"
        )
    support_descriptions = matrix.get("support_levels")
    if not isinstance(support_descriptions, dict) or set(support_descriptions) != SUPPORT_LEVELS:
        raise CompatibilityContractError("compatibility support levels are incomplete")
    for level, description in support_descriptions.items():
        _bounded_string(description, field=f"support description {level}", maximum=1024)
    invariants = matrix.get("invariants")
    if (
        not isinstance(invariants, dict)
        or set(invariants) != INVARIANT_FIELDS
        or any(value is not False for value in invariants.values())
    ):
        raise CompatibilityContractError(
            "compatibility invariants cannot claim unverified release surfaces"
        )
    categories = matrix.get("categories")
    if not isinstance(categories, dict):
        raise CompatibilityContractError("compatibility categories are invalid")
    root = project_root.resolve()
    entries, category_counts = _validate_entries(categories, project_root=root)
    clients = _validate_clients(entries, project_root=root)
    python_versions = _validate_python(entries, project_root=root)
    _validate_provider_interfaces(entries, project_root=root)
    _validate_identity_and_persistence(entries)
    _validate_deployment(entries, project_root=root)

    support_counts = {level: 0 for level in sorted(SUPPORT_LEVELS)}
    for entry in entries:
        support_counts[entry["support_level"]] += 1
    unsupported_or_pending = (
        support_counts["unsupported"] + support_counts["pending_owner_decision"]
    )
    return {
        "schema": EVIDENCE_SCHEMA,
        "matrix_schema": MATRIX_SCHEMA,
        "matrix_version": version,
        "matrix_sha256": hashlib.sha256(raw).hexdigest(),
        "product_stage": "alpha",
        "enterprise_release_ready": False,
        "category_counts": dict(sorted(category_counts.items())),
        "support_level_counts": support_counts,
        "entry_count": len(entries),
        "unsupported_or_pending_count": unsupported_or_pending,
        "clients": clients,
        "python_versions": python_versions,
        "real_idp_profiles_verified": 0,
        "production_persistence_profiles_verified": 0,
        "production_deployment_profiles_verified": 0,
    }


def _write_evidence(path: Path, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
    except OSError as error:
        raise CompatibilityContractError(
            "cannot write compatibility evidence"
        ) from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the versioned Hormuz compatibility matrix."
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=Path("compatibility/compatibility-matrix.json"),
    )
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        evidence = validate_compatibility_matrix(
            arguments.matrix,
            project_root=arguments.project_root,
        )
        if arguments.output is None:
            print(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        else:
            _write_evidence(arguments.output, evidence)
    except CompatibilityContractError as error:
        print(f"compatibility contract failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
