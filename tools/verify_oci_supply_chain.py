#!/usr/bin/env python3
"""Validate OCI SBOM and fix-aware vulnerability evidence for Hormuz."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SUMMARY_SCHEMA_ID = "hormuz.oci-supply-chain-summary"
SUMMARY_SCHEMA_VERSION = 1
BLOCKING_SEVERITIES = ("HIGH", "CRITICAL")
KNOWN_SEVERITIES = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class EvidenceError(RuntimeError):
    """Raised when supply-chain evidence is malformed, mismatched, or blocking."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--scanner-image", required=True)
    parser.add_argument("--scanner-version", required=True)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--vulnerabilities", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        summary = verify_evidence(
            image_reference=args.image_reference,
            image_id=args.image_id,
            scanner_image=args.scanner_image,
            scanner_version=args.scanner_version,
            sbom_path=args.sbom,
            vulnerabilities_path=args.vulnerabilities,
            output_path=args.output,
        )
    except EvidenceError as error:
        print(f"OCI supply-chain verification failed: {error}", file=sys.stderr)
        return 1

    print(
        "verified OCI supply-chain evidence: "
        f"candidate={summary['candidate']['image_id']} "
        f"fixable_high_or_critical={summary['findings']['high_or_critical']['fixable']}"
    )
    return 0


def verify_evidence(
    *,
    image_reference: str,
    image_id: str,
    scanner_image: str,
    scanner_version: str,
    sbom_path: Path,
    vulnerabilities_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Validate evidence, write a normalized summary, and enforce the PR policy."""

    image_reference = _nonempty_string(image_reference, "image reference")
    image_id = _sha256(image_id, "image ID")
    scanner_image = _pinned_scanner_image(scanner_image)
    scanner_version = _nonempty_string(scanner_version, "scanner version")

    sbom = _load_json(sbom_path, "SBOM")
    vulnerabilities = _load_json(vulnerabilities_path, "vulnerability report")
    sbom_metadata = _validate_sbom(
        sbom,
        image_reference=image_reference,
        image_id=image_id,
        scanner_version=scanner_version,
    )
    finding_counts = _validate_vulnerability_report(
        vulnerabilities,
        image_reference=image_reference,
    )

    fixable_blocking = finding_counts["fixable_blocking"]
    blocking_total = finding_counts["blocking_total"]
    summary = {
        "schema_id": SUMMARY_SCHEMA_ID,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "candidate": {
            "image_reference": image_reference,
            "image_id": image_id,
        },
        "scanner": {
            "image": scanner_image,
            "version": scanner_version,
        },
        "coverage": "reference_oci_image_only",
        "artifacts": {
            "sbom": {
                "format": sbom_metadata["format"],
                "spec_version": sbom_metadata["spec_version"],
                "sha256": _file_sha256(sbom_path),
            },
            "vulnerabilities": {
                "format": "trivy-json",
                "schema_version": 2,
                "sha256": _file_sha256(vulnerabilities_path),
            },
        },
        "policy": {
            "blocking_severities": list(BLOCKING_SEVERITIES),
            "block_requires_scanner_reported_fixed_version": True,
        },
        "findings": {
            "total": finding_counts["total"],
            "by_severity": finding_counts["by_severity"],
            "high_or_critical": {
                "total": blocking_total,
                "fixable": fixable_blocking,
                "unfixed": blocking_total - fixable_blocking,
            },
        },
        "verdict": "fail" if fixable_blocking else "pass",
    }
    _write_json(output_path, summary)

    if fixable_blocking:
        raise EvidenceError(
            "scanner reported "
            f"{fixable_blocking} HIGH/CRITICAL finding(s) with a fixed version; "
            f"see {vulnerabilities_path.name}"
        )
    return summary


def _validate_sbom(
    sbom: dict[str, Any],
    *,
    image_reference: str,
    image_id: str,
    scanner_version: str,
) -> dict[str, str]:
    if sbom.get("bomFormat") != "CycloneDX":
        raise EvidenceError("SBOM is not CycloneDX")
    spec_version = _nonempty_string(sbom.get("specVersion"), "SBOM specVersion")
    components = _list(sbom.get("components"), "SBOM components")
    if not components:
        raise EvidenceError("SBOM components must not be empty")

    metadata = _mapping(sbom.get("metadata"), "SBOM metadata")
    component = _mapping(metadata.get("component"), "SBOM metadata.component")
    if component.get("type") != "container":
        raise EvidenceError("SBOM metadata.component must describe a container")
    properties = _list(component.get("properties"), "SBOM metadata.component.properties")
    _require_property(properties, "aquasecurity:trivy:ImageID", image_id)
    _require_property(properties, "aquasecurity:trivy:Reference", image_reference)

    tools = _mapping(metadata.get("tools"), "SBOM metadata.tools")
    tool_components = _list(tools.get("components"), "SBOM metadata.tools.components")
    if not any(
        _mapping(tool, "SBOM metadata.tools.components item").get("name") == "trivy"
        and _mapping(tool, "SBOM metadata.tools.components item").get("version") == scanner_version
        for tool in tool_components
    ):
        raise EvidenceError("SBOM does not identify the pinned Trivy scanner version")

    return {"format": "CycloneDX", "spec_version": spec_version}


def _validate_vulnerability_report(
    report: dict[str, Any],
    *,
    image_reference: str,
) -> dict[str, Any]:
    if report.get("SchemaVersion") != 2:
        raise EvidenceError("vulnerability report must use supported Trivy schema version 2")
    if report.get("ArtifactName") != image_reference:
        raise EvidenceError("vulnerability report does not identify the candidate image reference")

    severity_counts: Counter[str] = Counter()
    total = 0
    blocking_total = 0
    fixable_blocking = 0
    for result_index, result_value in enumerate(_list(report.get("Results"), "vulnerability report Results")):
        result = _mapping(result_value, f"vulnerability report Results[{result_index}]")
        vulnerabilities = result.get("Vulnerabilities", [])
        for vulnerability_index, vulnerability_value in enumerate(
            _list(vulnerabilities, f"vulnerability report Results[{result_index}].Vulnerabilities")
        ):
            vulnerability = _mapping(
                vulnerability_value,
                f"vulnerability report Results[{result_index}].Vulnerabilities[{vulnerability_index}]",
            )
            _nonempty_string(vulnerability.get("VulnerabilityID"), "vulnerability ID")
            _nonempty_string(vulnerability.get("PkgName"), "vulnerability package name")
            severity = _nonempty_string(vulnerability.get("Severity"), "vulnerability severity")
            if severity not in KNOWN_SEVERITIES:
                raise EvidenceError(f"vulnerability severity is unsupported: {severity}")
            fixed_version = vulnerability.get("FixedVersion", "")
            if not isinstance(fixed_version, str):
                raise EvidenceError("vulnerability FixedVersion must be a string when present")

            total += 1
            severity_counts[severity] += 1
            if severity in BLOCKING_SEVERITIES:
                blocking_total += 1
                if fixed_version.strip():
                    fixable_blocking += 1

    return {
        "total": total,
        "by_severity": {severity: severity_counts[severity] for severity in KNOWN_SEVERITIES},
        "blocking_total": blocking_total,
        "fixable_blocking": fixable_blocking,
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EvidenceError(f"{label} is missing: {path.name}") from error
    except json.JSONDecodeError as error:
        raise EvidenceError(f"{label} is not valid JSON: {path.name}") from error
    return _mapping(value, label)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _require_property(properties: list[Any], name: str, expected_value: str) -> None:
    matching_values: list[str] = []
    for index, property_value in enumerate(properties):
        item = _mapping(property_value, f"SBOM metadata.component.properties[{index}]")
        if item.get("name") == name:
            matching_values.append(_nonempty_string(item.get("value"), f"SBOM property {name}"))
    if matching_values != [expected_value]:
        raise EvidenceError(f"SBOM property {name} does not match the candidate image")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise EvidenceError(f"{label} must be an array")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{label} must be a non-empty string")
    return value


def _sha256(value: str, label: str) -> str:
    if not SHA256_PATTERN.fullmatch(value):
        raise EvidenceError(f"{label} must be a sha256 digest")
    return value


def _pinned_scanner_image(value: str) -> str:
    value = _nonempty_string(value, "scanner image")
    name, separator, digest = value.rpartition("@")
    if not name or not separator or not SHA256_PATTERN.fullmatch(digest):
        raise EvidenceError("scanner image must be pinned by sha256 digest")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
