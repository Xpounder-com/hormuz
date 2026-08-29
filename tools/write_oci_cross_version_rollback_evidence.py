#!/usr/bin/env python3
"""Write strict content-free evidence for the signed OCI rollback drill."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from tools._verification_runtime import (
        is_pinned_image_reference,
        is_sha256_digest,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        is_pinned_image_reference,
        is_sha256_digest,
        write_private_json_evidence,
    )


SCHEMA_ID = "hormuz.oci-cross-version-rollback"
SCHEMA_VERSION = 1
SOURCE_IMAGE = "ghcr.io/xpounder-com/hormuz"
REGISTRY_IMAGE = (
    "docker.io/library/registry:3.0.0@"
    "sha256:6c5666b861f3505b116bb9aa9b25175e71210414bd010d92035ff64018f9457e"
)
ORAS_VERSION = "1.3.4"
COSIGN_VERSION = "v3.1.3"
RELEASES = {
    "current": {
        "tag": "v1.0.0",
        "digest": "sha256:e74fd7c527d257ff337510436f25a0eaf1e2fb799e1258566c9f393025e6b5a3",
        "commit": "2fc0605252e41f731c85cc9146fbff6eb3b34669",
        "referrers": (
            "sha256:3f30ee1c54a56cc147de770d85d0516341a702ddddf38839da66262905c89bcc",
            "sha256:855c82db527c83e05ca7221bf1e9564ccfc56c9b39b29aaa99c2f1c46357fe3c",
            "sha256:a1ef2e402f8e96d18d3598fdf0d5315c9c9e7d1299372b0d9943185e6976bf81",
        ),
    },
    "rollback": {
        "tag": "v0.1.1",
        "digest": "sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a",
        "commit": "b9388cba8945dbdc86a55d79dd92283841aeecc4",
        "referrers": (
            "sha256:5a525fc0e77bf0b201ded100dd04c5d4ec56968f4f281d61825d851b1dd3796a",
            "sha256:93092ea586a8eb468891779ecee6b4020618d3271e13ad7c4e7300e2e054f1ef",
            "sha256:9ea1753be3a7afbfe77060d90f2970e6ab18b63b6b3d63ee8b4f80869a9b1679",
        ),
    },
}


class RollbackEvidenceError(RuntimeError):
    """Raised when rollback evidence is incomplete or mismatched."""


def _workflow_identity(tag: str) -> str:
    return (
        "https://github.com/Xpounder-com/hormuz/.github/workflows/"
        f"release-oci.yml@refs/tags/{tag}"
    )


def _parse_referrers(value: str, *, release: str) -> list[str]:
    items = value.split(",") if value else []
    if (
        len(items) != 3
        or items != sorted(items)
        or len(set(items)) != 3
        or any(not is_sha256_digest(item) for item in items)
    ):
        raise RollbackEvidenceError(f"{release}_referrer_set_invalid")
    return items


def _validate_duration(value: int, *, release: str) -> int:
    if isinstance(value, bool) or value < 1 or value > 600_000:
        raise RollbackEvidenceError(f"{release}_runtime_duration_invalid")
    return value


def create_summary(
    *,
    current_digest: str,
    current_tag: str,
    current_commit: str,
    current_referrers: str,
    current_runtime_ms: int,
    rollback_digest: str,
    rollback_tag: str,
    rollback_commit: str,
    rollback_referrers: str,
    rollback_runtime_ms: int,
    registry_image: str,
    oras_version: str,
    cosign_version: str,
    docker_version: str,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    supplied = {
        "current": {
            "tag": current_tag,
            "digest": current_digest,
            "commit": current_commit,
        },
        "rollback": {
            "tag": rollback_tag,
            "digest": rollback_digest,
            "commit": rollback_commit,
        },
    }
    expected_identities = {
        name: {key: release[key] for key in ("tag", "digest", "commit")}
        for name, release in RELEASES.items()
    }
    if supplied != expected_identities:
        raise RollbackEvidenceError("release_identity_mismatch")
    if not is_pinned_image_reference(
        registry_image, image_name="docker.io/library/registry:3.0.0"
    ) or registry_image != REGISTRY_IMAGE:
        raise RollbackEvidenceError("registry_image_mismatch")
    if oras_version != ORAS_VERSION:
        raise RollbackEvidenceError("oras_version_mismatch")
    if cosign_version != COSIGN_VERSION:
        raise RollbackEvidenceError("cosign_version_mismatch")
    if re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", docker_version) is None:
        raise RollbackEvidenceError("docker_version_invalid")

    release_values: dict[str, dict[str, Any]] = {}
    for name, referrers, runtime_ms in (
        ("current", current_referrers, current_runtime_ms),
        ("rollback", rollback_referrers, rollback_runtime_ms),
    ):
        identity = RELEASES[name]
        parsed_referrers = _parse_referrers(referrers, release=name)
        if tuple(parsed_referrers) != identity["referrers"]:
            raise RollbackEvidenceError(f"{name}_referrer_identity_mismatch")
        release_values[name] = {
            "commit": identity["commit"],
            "digest": identity["digest"],
            "platform": "linux/amd64",
            "referrer_manifest_digests": parsed_referrers,
            "runtime_verification_ms": _validate_duration(
                runtime_ms, release=name
            ),
            "tag": identity["tag"],
            "workflow_identity": _workflow_identity(identity["tag"]),
        }

    timestamp = evaluated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise RollbackEvidenceError("evaluated_at_invalid") from error
    if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() != UTC.utcoffset(None):
        raise RollbackEvidenceError("evaluated_at_invalid")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "evaluated_at": timestamp,
        "source": {
            "image": SOURCE_IMAGE,
            "releases": release_values,
        },
        "mirror": {
            "kind": "disposable_loopback_registry",
            "registry_image": registry_image,
            "subject_digests_preserved": True,
            "referrer_manifest_digests_preserved": True,
            "source_and_destination_crypto_verified": True,
        },
        "runtime": {
            "sequence": [
                "current_started_ready_and_stopped_cleanly",
                "rollback_selected_by_immutable_digest",
                "rollback_started_ready_and_stopped_cleanly",
            ],
            "external_provider_calls": 0,
            "persistent_customer_content": False,
            "storage_boundary": (
                "fresh_disposable_sqlite_per_release; no cross-version storage "
                "reuse or database down-migration"
            ),
        },
        "mutation_boundary": {
            "artifact_build_performed": False,
            "artifact_resigning_performed": False,
            "database_down_migration_performed": False,
            "source_semantic_tag_changed_during_drill": False,
        },
        "tools": {
            "cosign": cosign_version,
            "docker_server": docker_version,
            "oras": oras_version,
        },
        "nonclaims": [
            "production_deployment_rollback",
            "customer_data_recovery",
            "schema_downgrade_compatibility",
            "high_availability_or_disaster_recovery",
            "registry_retention_sla",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for release in ("current", "rollback"):
        parser.add_argument(f"--{release}-digest", required=True)
        parser.add_argument(f"--{release}-tag", required=True)
        parser.add_argument(f"--{release}-commit", required=True)
        parser.add_argument(f"--{release}-referrers", required=True)
        parser.add_argument(f"--{release}-runtime-ms", required=True, type=int)
    parser.add_argument("--registry-image", required=True)
    parser.add_argument("--oras-version", required=True)
    parser.add_argument("--cosign-version", required=True)
    parser.add_argument("--docker-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if args.output.exists():
            raise RollbackEvidenceError("output_already_exists")
        summary = create_summary(
            current_digest=args.current_digest,
            current_tag=args.current_tag,
            current_commit=args.current_commit,
            current_referrers=args.current_referrers,
            current_runtime_ms=args.current_runtime_ms,
            rollback_digest=args.rollback_digest,
            rollback_tag=args.rollback_tag,
            rollback_commit=args.rollback_commit,
            rollback_referrers=args.rollback_referrers,
            rollback_runtime_ms=args.rollback_runtime_ms,
            registry_image=args.registry_image,
            oras_version=args.oras_version,
            cosign_version=args.cosign_version,
            docker_version=args.docker_version,
        )
        write_private_json_evidence(args.output, summary, indent=2, parent_mode=0o700)
    except (OSError, RollbackEvidenceError) as error:
        print(f"OCI cross-version rollback evidence failed: {error}", file=sys.stderr)
        return 1

    print(f"wrote content-free OCI rollback evidence to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
