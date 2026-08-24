#!/usr/bin/env python3
"""Create a strict metadata-only SLSA v1 predicate for a Hormuz OCI digest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from tools._verification_runtime import (
        file_sha256,
        is_pinned_image_reference,
        is_sha256_digest,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        file_sha256,
        is_pinned_image_reference,
        is_sha256_digest,
        write_private_json_evidence,
    )


PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
BUILD_TYPE = "https://github.com/Xpounder-com/hormuz/.github/workflows/release-oci.yml@v1"
BASE_IMAGE = "docker.io/library/python"
BASE_DIGEST = "sha256:23c59390fc717bf09f9336908199a0ae75d9c4264bf296123f94ad772fea3b52"
FRONTEND_IMAGE = "docker.io/docker/dockerfile"
FRONTEND_DIGEST = "sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32"
EXPECTED_ISSUER = "https://token.actions.githubusercontent.com"
EXPECTED_REPOSITORY = "Xpounder-com/hormuz"
EXPECTED_IMAGE = "ghcr.io/xpounder-com/hormuz"


class ProvenanceError(RuntimeError):
    """Raised when release evidence cannot produce one bounded predicate."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--reproducibility", required=True, type=Path)
    parser.add_argument("--supply-chain", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--vulnerabilities", required=True, type=Path)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--invocation-uri", required=True)
    parser.add_argument("--build-lock", required=True, type=Path)
    parser.add_argument("--runtime-lock", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        predicate = create_predicate(
            preflight=_load_json(args.preflight),
            reproducibility=_load_json(args.reproducibility),
            supply_chain=_load_json(args.supply_chain),
            preflight_sha256=file_sha256(args.preflight),
            reproducibility_sha256=file_sha256(args.reproducibility),
            supply_chain_sha256=file_sha256(args.supply_chain),
            sbom_sha256=file_sha256(args.sbom),
            vulnerabilities_sha256=file_sha256(args.vulnerabilities),
            build_lock_sha256=file_sha256(args.build_lock),
            runtime_lock_sha256=file_sha256(args.runtime_lock),
            image_reference=args.image_reference,
            image_digest=args.image_digest,
            invocation_uri=args.invocation_uri,
        )
        write_private_json_evidence(args.output, predicate, indent=2)
    except (OSError, json.JSONDecodeError, ProvenanceError) as error:
        print(f"OCI release provenance failed: {error}", file=sys.stderr)
        return 1

    print(f"created bounded SLSA v1 predicate for {args.image_reference}@{args.image_digest}")
    return 0


def create_predicate(
    *,
    preflight: dict[str, Any],
    reproducibility: dict[str, Any],
    supply_chain: dict[str, Any],
    preflight_sha256: str,
    reproducibility_sha256: str,
    supply_chain_sha256: str,
    sbom_sha256: str,
    vulnerabilities_sha256: str,
    build_lock_sha256: str,
    runtime_lock_sha256: str,
    image_reference: str,
    image_digest: str,
    invocation_uri: str,
) -> dict[str, Any]:
    _require_schema(preflight, "hormuz.oci-release-preflight", 1)
    _require_schema(reproducibility, "hormuz.oci-reproducibility", 1)
    _require_schema(supply_chain, "hormuz.oci-supply-chain-summary", 1)
    for label, digest in {
        "image_digest": image_digest,
        "preflight_sha256": preflight_sha256,
        "reproducibility_sha256": reproducibility_sha256,
        "supply_chain_sha256": supply_chain_sha256,
        "sbom_sha256": sbom_sha256,
        "vulnerabilities_sha256": vulnerabilities_sha256,
        "build_lock_sha256": build_lock_sha256,
        "runtime_lock_sha256": runtime_lock_sha256,
    }.items():
        if not is_sha256_digest(digest):
            raise ProvenanceError(f"{label}_invalid")
    if image_reference != EXPECTED_IMAGE:
        raise ProvenanceError("first_publication_registry_mismatch")
    if not invocation_uri.startswith(
        f"https://github.com/{EXPECTED_REPOSITORY}/actions/runs/"
    ):
        raise ProvenanceError("invocation_uri_invalid")

    release = _mapping(preflight.get("release"), "preflight_release")
    artifact = _mapping(preflight.get("artifact"), "preflight_artifact")
    signing = _mapping(preflight.get("signing"), "preflight_signing")
    reproducible_artifact = _mapping(
        reproducibility.get("artifact"),
        "reproducibility_artifact",
    )
    reproducible_build = _mapping(
        reproducibility.get("build"),
        "reproducibility_build",
    )
    supply_candidate = _mapping(supply_chain.get("candidate"), "supply_chain_candidate")
    supply_artifacts = _mapping(supply_chain.get("artifacts"), "supply_chain_artifacts")
    sbom = _mapping(supply_artifacts.get("sbom"), "supply_chain_sbom")
    vulnerabilities = _mapping(
        supply_artifacts.get("vulnerabilities"),
        "supply_chain_vulnerabilities",
    )

    expected_pinned_reference = f"{image_reference}@{image_digest}"
    if not is_pinned_image_reference(expected_pinned_reference, image_name=image_reference):
        raise ProvenanceError("image_reference_invalid")
    if reproducible_artifact.get("digest") != image_digest:
        raise ProvenanceError("published_digest_not_reproducible_digest")
    if reproducible_artifact.get("platform") != "linux/amd64":
        raise ProvenanceError("published_platform_mismatch")
    if supply_candidate.get("image_reference") != expected_pinned_reference:
        raise ProvenanceError("supply_chain_candidate_mismatch")
    if supply_chain.get("verdict") != "pass":
        raise ProvenanceError("supply_chain_verdict_not_pass")
    if sbom.get("sha256") != sbom_sha256:
        raise ProvenanceError("sbom_hash_mismatch")
    if vulnerabilities.get("sha256") != vulnerabilities_sha256:
        raise ProvenanceError("vulnerability_hash_mismatch")
    if artifact.get("contract") != "signed_oci_digest":
        raise ProvenanceError("artifact_contract_mismatch")

    commit = _string(release.get("commit"), "release_commit")
    ref = _string(release.get("ref"), "release_ref")
    tag = _string(release.get("tag"), "release_tag")
    version = _string(release.get("package_version"), "release_version")
    expected_identity = (
        f"https://github.com/{EXPECTED_REPOSITORY}/.github/workflows/"
        f"release-oci.yml@{ref}"
    )
    if ref != f"refs/tags/{tag}":
        raise ProvenanceError("release_ref_tag_mismatch")
    if release.get("repository_visibility") != "public":
        raise ProvenanceError("release_repository_not_public")
    if release.get("tag_object") != "annotated" or release.get("tag_protected") is not True:
        raise ProvenanceError("release_tag_boundary_invalid")
    if artifact != {
        "contract": "signed_oci_digest",
        "first_publication_registry": EXPECTED_IMAGE,
        "platform": "linux/amd64",
        "registry_is_product_contract": False,
    }:
        raise ProvenanceError("artifact_boundary_invalid")
    if signing != {
        "issuer": EXPECTED_ISSUER,
        "key_management": "keyless_github_oidc",
        "transparency_log": "public_rekor",
        "workflow_identity": expected_identity,
    }:
        raise ProvenanceError("signing_boundary_invalid")
    if reproducibility.get("verdict") != "pass":
        raise ProvenanceError("reproducibility_verdict_not_pass")
    if reproducible_artifact.get("reference") != tag:
        raise ProvenanceError("reproducibility_tag_mismatch")
    if reproducible_artifact.get("revision") != commit:
        raise ProvenanceError("reproducibility_commit_mismatch")
    if reproducible_artifact.get("version") != version:
        raise ProvenanceError("reproducibility_version_mismatch")
    if reproducible_build.get("source_date_epoch") != release.get("source_date_epoch"):
        raise ProvenanceError("reproducibility_epoch_mismatch")

    return {
        "buildDefinition": {
            "buildType": BUILD_TYPE,
            "externalParameters": {
                "artifactContract": "signed_oci_digest",
                "firstPublicationRegistry": image_reference,
                "platform": "linux/amd64",
                "ref": ref,
                "repository": EXPECTED_REPOSITORY,
                "tag": tag,
                "version": version,
            },
            "internalParameters": {},
            "resolvedDependencies": [
                {
                    "digest": {"gitCommit": commit},
                    "uri": f"git+https://github.com/{EXPECTED_REPOSITORY}@{ref}",
                },
                {"digest": {"sha256": BASE_DIGEST.removeprefix("sha256:")}, "uri": BASE_IMAGE},
                {
                    "digest": {"sha256": FRONTEND_DIGEST.removeprefix("sha256:")},
                    "uri": FRONTEND_IMAGE,
                },
                {
                    "digest": {"sha256": build_lock_sha256.removeprefix("sha256:")},
                    "uri": f"git+https://github.com/{EXPECTED_REPOSITORY}#requirements/oci-build-linux-amd64.lock",
                },
                {
                    "digest": {"sha256": runtime_lock_sha256.removeprefix("sha256:")},
                    "uri": f"git+https://github.com/{EXPECTED_REPOSITORY}#requirements/oci-runtime-linux-amd64.lock",
                },
            ],
        },
        "runDetails": {
            "builder": {"id": expected_identity},
            "byproducts": [
                {"name": "preflight", "digest": {"sha256": preflight_sha256.removeprefix("sha256:")}},
                {
                    "name": "reproducibility",
                    "digest": {"sha256": reproducibility_sha256.removeprefix("sha256:")},
                },
                {
                    "name": "supply-chain-summary",
                    "digest": {"sha256": supply_chain_sha256.removeprefix("sha256:")},
                },
                {"name": "cyclonedx-sbom", "digest": {"sha256": sbom_sha256.removeprefix("sha256:")}},
                {
                    "name": "vulnerability-report",
                    "digest": {"sha256": vulnerabilities_sha256.removeprefix("sha256:")},
                },
            ],
            "metadata": {"invocationId": invocation_uri},
        },
    }


def _load_json(path: Path) -> dict[str, Any]:
    return _mapping(json.loads(path.read_text(encoding="utf-8")), path.name)


def _require_schema(value: dict[str, Any], schema_id: str, schema_version: int) -> None:
    if value.get("schema_id") != schema_id or value.get("schema_version") != schema_version:
        raise ProvenanceError(f"unsupported_evidence_schema:{schema_id}")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceError(f"{label}_must_be_object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProvenanceError(f"{label}_invalid")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
