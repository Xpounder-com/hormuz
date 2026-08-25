#!/usr/bin/env python3
"""Verify the installed license boundary for Hormuz and every declared extra."""

from __future__ import annotations

import argparse
import re
import sys
from importlib.metadata import Distribution, distributions
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    from tools._verification_runtime import write_private_json_evidence
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import write_private_json_evidence  # type: ignore[no-redef]


SCHEMA_ID = "hormuz.dependency-license-inventory"
SCHEMA_VERSION = 1
IGNORED_TOOLING = {"pip", "setuptools"}
EXPECTED_LICENSES = {
    "boto3": ("Apache-2.0", "permissive"),
    "botocore": ("Apache-2.0", "permissive"),
    "cffi": ("MIT-0", "permissive"),
    "cryptography": ("Apache-2.0 OR BSD-3-Clause", "permissive"),
    "hormuz": ("Apache-2.0", "application"),
    "hormuz-context-experiment": ("Apache-2.0", "experimental_package"),
    "jmespath": ("MIT", "permissive"),
    "psycopg": ("LGPL-3.0-only", "weak_copyleft_dependency"),
    "psycopg-binary": ("LGPL-3.0-only", "weak_copyleft_dependency"),
    "psycopg-pool": ("LGPL-3.0-only", "weak_copyleft_dependency"),
    "pycparser": ("BSD-3-Clause", "permissive"),
    "pyjwt": ("MIT", "permissive"),
    "python-dateutil": ("Apache-2.0 OR BSD-3-Clause", "permissive"),
    "s3transfer": ("Apache-2.0", "permissive"),
    "six": ("MIT", "permissive"),
    "typing-extensions": ("PSF-2.0", "permissive"),
    "urllib3": ("MIT", "permissive"),
}
LEGACY_LICENSES = {
    "Apache-2.0": "Apache-2.0",
    "Apache License 2.0": "Apache-2.0",
    "MIT": "MIT",
}
DATEUTIL_CLASSIFIERS = {
    "License :: OSI Approved :: Apache Software License",
    "License :: OSI Approved :: BSD License",
}


class DependencyLicenseError(RuntimeError):
    """Raised when the resolved distribution set is unknown or incompatible."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        summary = validate_inventory(_installed_records())
        write_private_json_evidence(args.output, summary, indent=2)
    except (DependencyLicenseError, OSError) as error:
        print(f"dependency license verification failed: {error}", file=sys.stderr)
        return 1

    print(
        "verified dependency licenses: "
        f"packages={len(summary['packages'])} weak_copyleft="
        f"{summary['counts']['weak_copyleft_dependency']}"
    )
    return 0


def validate_inventory(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate normalized distribution records and return content-free evidence."""

    installed: dict[str, Mapping[str, Any]] = {}
    for record in records:
        name = _canonical_name(record.get("name"))
        if name in IGNORED_TOOLING:
            continue
        if name in installed:
            raise DependencyLicenseError(f"duplicate_distribution:{name}")
        installed[name] = record

    expected_names = set(EXPECTED_LICENSES)
    if set(installed) != expected_names:
        missing = sorted(expected_names - set(installed))
        unexpected = sorted(set(installed) - expected_names)
        raise DependencyLicenseError(
            "distribution_set_mismatch:"
            f"missing={','.join(missing) or 'none'}:"
            f"unexpected={','.join(unexpected) or 'none'}"
        )

    packages = []
    category_counts = {
        "application": 0,
        "experimental_package": 0,
        "permissive": 0,
        "weak_copyleft_dependency": 0,
    }
    for name in sorted(installed):
        record = installed[name]
        version = record.get("version")
        if not isinstance(version, str) or not version or len(version) > 128:
            raise DependencyLicenseError(f"distribution_version_invalid:{name}")
        identity = _license_identity(record, name)
        expected_identity, category = EXPECTED_LICENSES[name]
        if identity != expected_identity:
            raise DependencyLicenseError(f"distribution_license_mismatch:{name}")
        license_files = record.get("license_files")
        if not isinstance(license_files, int) or isinstance(license_files, bool) or license_files < 1:
            raise DependencyLicenseError(f"distribution_license_file_missing:{name}")
        category_counts[category] += 1
        packages.append(
            {
                "category": category,
                "license": identity,
                "license_file_count": license_files,
                "name": name,
                "version": version,
            }
        )

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "application_license": "Apache-2.0",
        "counts": category_counts,
        "coverage": "resolved_python_distribution_closure_all_declared_extras",
        "packages": packages,
        "policy": {
            "dependencies_retain_their_own_licenses": True,
            "lgpl_dependencies_are_separate_runtime_components": True,
            "legal_opinion_claimed": False,
            "unexpected_distribution_action": "deny",
            "unknown_or_changed_license_action": "deny",
        },
        "verdict": "pass",
    }


def _installed_records() -> list[dict[str, Any]]:
    records = []
    for distribution in distributions():
        metadata = distribution.metadata
        files = [str(path) for path in (distribution.files or ())]
        records.append(
            {
                "classifiers": metadata.get_all("Classifier") or [],
                "legacy_license": metadata.get("License"),
                "license_expression": metadata.get("License-Expression"),
                "license_files": sum(
                    any(token in path.lower() for token in ("license", "copying", "notice"))
                    for path in files
                ),
                "name": metadata.get("Name"),
                "version": distribution.version,
            }
        )
    return records


def _license_identity(record: Mapping[str, Any], name: str) -> str:
    expression = record.get("license_expression")
    if isinstance(expression, str) and expression.strip():
        return expression.strip()

    legacy = record.get("legacy_license")
    if legacy == "Dual License" and name == "python-dateutil":
        classifiers = record.get("classifiers")
        if not isinstance(classifiers, list) or not DATEUTIL_CLASSIFIERS.issubset(classifiers):
            raise DependencyLicenseError("python_dateutil_dual_license_unverified")
        return "Apache-2.0 OR BSD-3-Clause"
    if isinstance(legacy, str) and legacy in LEGACY_LICENSES:
        return LEGACY_LICENSES[legacy]
    raise DependencyLicenseError(f"distribution_license_unknown:{name}")


def _canonical_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DependencyLicenseError("distribution_name_invalid")
    normalized = re.sub(r"[-_.]+", "-", value.strip().lower())
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", normalized) is None:
        raise DependencyLicenseError("distribution_name_invalid")
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
