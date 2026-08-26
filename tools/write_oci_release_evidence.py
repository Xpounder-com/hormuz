#!/usr/bin/env python3
"""Write the final allowlisted, content-free OCI release evidence summary."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from tools._verification_runtime import (
        file_sha256,
        is_sha256_digest,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        file_sha256,
        is_sha256_digest,
        write_private_json_evidence,
    )


SCHEMA_ID = "hormuz.oci-release-evidence"
SCHEMA_VERSION = 2
IMAGE = "ghcr.io/xpounder-com/hormuz"
ISSUER = "https://token.actions.githubusercontent.com"
TAG_PATTERN = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")


class ReleaseEvidenceError(RuntimeError):
    """Raised when the final release evidence is incomplete or mismatched."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--workflow-identity", required=True)
    parser.add_argument("--first-publication-visibility", required=True)
    parser.add_argument("--preflight", required=True, type=Path)
    parser.add_argument("--reproducibility", required=True, type=Path)
    parser.add_argument("--supply-chain", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--vulnerabilities", required=True, type=Path)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--public-metadata-validation", required=True, type=Path)
    parser.add_argument("--signature-verification", required=True, type=Path)
    parser.add_argument("--sbom-verification", required=True, type=Path)
    parser.add_argument("--provenance-verification", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        summary = create_summary(
            digest=args.digest,
            tag=args.tag,
            commit=args.commit,
            workflow_identity=args.workflow_identity,
            first_publication_visibility=args.first_publication_visibility,
            evidence_paths={
                "preflight": args.preflight,
                "provenance": args.provenance,
                "provenance_verification": args.provenance_verification,
                "public_metadata_validation": args.public_metadata_validation,
                "reproducibility": args.reproducibility,
                "sbom": args.sbom,
                "sbom_verification": args.sbom_verification,
                "signature_verification": args.signature_verification,
                "supply_chain": args.supply_chain,
                "vulnerabilities": args.vulnerabilities,
            },
        )
        write_private_json_evidence(args.output, summary, indent=2)
    except (OSError, ReleaseEvidenceError) as error:
        print(f"OCI release evidence failed: {error}", file=sys.stderr)
        return 1

    print(f"wrote signed OCI release evidence for {IMAGE}@{args.digest}")
    return 0


def create_summary(
    *,
    digest: str,
    tag: str,
    commit: str,
    workflow_identity: str,
    first_publication_visibility: str,
    evidence_paths: dict[str, Path],
) -> dict[str, Any]:
    if not is_sha256_digest(digest):
        raise ReleaseEvidenceError("release_digest_invalid")
    if TAG_PATTERN.fullmatch(tag) is None:
        raise ReleaseEvidenceError("release_tag_invalid")
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ReleaseEvidenceError("release_commit_invalid")
    expected_identity = (
        "https://github.com/Xpounder-com/hormuz/.github/workflows/"
        f"release-oci.yml@refs/tags/{tag}"
    )
    if workflow_identity != expected_identity:
        raise ReleaseEvidenceError("release_workflow_identity_mismatch")
    if first_publication_visibility != "public":
        raise ReleaseEvidenceError("first_publication_visibility_mismatch")
    expected_names = {
        "preflight",
        "provenance",
        "provenance_verification",
        "public_metadata_validation",
        "reproducibility",
        "sbom",
        "sbom_verification",
        "signature_verification",
        "supply_chain",
        "vulnerabilities",
    }
    if set(evidence_paths) != expected_names:
        raise ReleaseEvidenceError("release_evidence_set_mismatch")
    evidence_hashes = {}
    for name in sorted(evidence_paths):
        path = evidence_paths[name]
        if not path.is_file() or path.stat().st_size == 0:
            raise ReleaseEvidenceError(f"release_evidence_missing:{name}")
        evidence_hashes[name] = file_sha256(path)

    try:
        public_validation = json.loads(
            evidence_paths["public_metadata_validation"].read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as error:
        raise ReleaseEvidenceError("public_metadata_validation_unreadable") from error
    if not isinstance(public_validation, dict):
        raise ReleaseEvidenceError("public_metadata_validation_invalid")
    validation_artifact = public_validation.get("artifact")
    validation_release = public_validation.get("release")
    if (
        public_validation.get("schema_id") != "hormuz.oci-public-metadata-validation"
        or public_validation.get("schema_version") != 1
        or public_validation.get("verdict") != "pass"
        or not isinstance(validation_artifact, dict)
        or validation_artifact.get("digest") != digest
        or validation_artifact.get("image_reference") != IMAGE
        or validation_artifact.get("platform") != "linux/amd64"
        or validation_artifact.get("repository_visibility") != "public"
        or not isinstance(validation_release, dict)
        or validation_release.get("commit") != commit
        or validation_release.get("tag") != tag
    ):
        raise ReleaseEvidenceError("public_metadata_validation_mismatch")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "commit_tag": f"sha-{commit}",
            "contract": "signed_oci_digest",
            "digest": digest,
            "first_publication_registry": IMAGE,
            "first_publication_registry_visibility": first_publication_visibility,
            "platform": "linux/amd64",
            "registry_is_product_contract": False,
            "release_tag": tag,
        },
        "disclosure": {
            "public_rekor_may_expose": [
                "artifact_digest",
                "repository_name",
                "workflow_path",
                "commit_or_ref",
                "signing_event",
            ],
            "public_rekor_must_not_contain": [
                "source_code",
                "image_layers",
                "credentials",
                "customer_data",
                "prompts_or_responses",
                "private_workspace_paths",
            ],
        },
        "registry_attestations": {
            "initial_visibility": first_publication_visibility,
            "public_rekor_upload": False,
            "types": ["cyclonedx", "slsaprovenance1"],
        },
        "evidence": evidence_hashes,
        "mirroring": {
            "destination_digest_must_match": True,
            "recursive_signature_and_attestation_copy_required": True,
        },
        "signing": {
            "issuer": ISSUER,
            "key_management": "keyless_github_oidc",
            "transparency_log": "public_rekor",
            "workflow_identity": workflow_identity,
        },
        "verdict": "pass",
    }


if __name__ == "__main__":
    raise SystemExit(main())
