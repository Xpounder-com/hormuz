#!/usr/bin/env python3
"""Fail closed unless an OCI release has the approved repository and tag identity."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

try:
    from tools._verification_runtime import write_private_json_evidence
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import write_private_json_evidence  # type: ignore[no-redef]


SCHEMA_ID = "hormuz.oci-release-preflight"
SCHEMA_VERSION = 1
EXPECTED_REPOSITORY = "Xpounder-com/hormuz"
EXPECTED_WORKFLOW_PATH = ".github/workflows/release-oci.yml"
FIRST_REGISTRY = "ghcr.io/xpounder-com/hormuz"
SUPPORTED_PLATFORM = "linux/amd64"
TAG_PATTERN = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")


class PreflightError(RuntimeError):
    """Raised when a release context is not the approved immutable identity."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-visibility", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--workflow-ref", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ref-protected", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--pyproject", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        package_version = _read_package_version(args.pyproject)
        tag_object_type = _git("cat-file", "-t", f"refs/tags/{args.tag}")
        tag_commit = _git("rev-list", "-n", "1", f"refs/tags/{args.tag}")
        main_contains_commit = _git_is_ancestor(tag_commit, "refs/remotes/origin/main")
        source_date_epoch = _git("show", "-s", "--format=%ct", tag_commit)
        summary = validate_release_context(
            repository=args.repository,
            repository_visibility=args.repository_visibility,
            ref=args.ref,
            workflow_ref=args.workflow_ref,
            commit=args.commit,
            ref_protected=args.ref_protected,
            tag=args.tag,
            package_version=package_version,
            tag_object_type=tag_object_type,
            tag_commit=tag_commit,
            main_contains_commit=main_contains_commit,
            source_date_epoch=source_date_epoch,
        )
        write_private_json_evidence(args.output, summary, indent=2)
    except (OSError, PreflightError, subprocess.SubprocessError, tomllib.TOMLDecodeError) as error:
        print(f"OCI release preflight failed: {error}", file=sys.stderr)
        return 1

    print(
        "verified OCI release preflight: "
        f"tag={summary['release']['tag']} commit={summary['release']['commit']} "
        f"platform={summary['artifact']['platform']}"
    )
    return 0


def validate_release_context(
    *,
    repository: str,
    repository_visibility: str,
    ref: str,
    workflow_ref: str,
    commit: str,
    ref_protected: str,
    tag: str,
    package_version: str,
    tag_object_type: str,
    tag_commit: str,
    main_contains_commit: bool,
    source_date_epoch: str,
) -> dict[str, Any]:
    """Validate already collected GitHub and Git facts and return evidence."""

    if repository != EXPECTED_REPOSITORY:
        raise PreflightError("release_repository_mismatch")
    if repository_visibility != "public":
        raise PreflightError("release_repository_not_public")
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise PreflightError("release_tag_not_strict_semver")
    expected_ref = f"refs/tags/{tag}"
    if ref != expected_ref:
        raise PreflightError("release_ref_mismatch")
    expected_workflow_ref = f"{EXPECTED_REPOSITORY}/{EXPECTED_WORKFLOW_PATH}@{expected_ref}"
    if workflow_ref != expected_workflow_ref:
        raise PreflightError("release_workflow_identity_mismatch")
    if ref_protected.lower() != "true":
        raise PreflightError("release_tag_not_protected")
    if tag_object_type != "tag":
        raise PreflightError("release_tag_not_annotated")
    if COMMIT_PATTERN.fullmatch(commit) is None or COMMIT_PATTERN.fullmatch(tag_commit) is None:
        raise PreflightError("release_commit_invalid")
    if commit != tag_commit:
        raise PreflightError("release_tag_commit_mismatch")
    if not main_contains_commit:
        raise PreflightError("release_commit_not_reachable_from_main")
    if package_version != tag.removeprefix("v"):
        raise PreflightError("release_package_version_mismatch")
    if not source_date_epoch.isascii() or not source_date_epoch.isdigit():
        raise PreflightError("release_source_date_epoch_invalid")

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "artifact": {
            "contract": "signed_oci_digest",
            "first_publication_registry": FIRST_REGISTRY,
            "platform": SUPPORTED_PLATFORM,
            "registry_is_product_contract": False,
        },
        "release": {
            "commit": commit,
            "package_version": package_version,
            "ref": ref,
            "repository_visibility": repository_visibility,
            "source_date_epoch": int(source_date_epoch),
            "tag": tag,
            "tag_object": "annotated",
            "tag_protected": True,
        },
        "signing": {
            "issuer": "https://token.actions.githubusercontent.com",
            "key_management": "keyless_github_oidc",
            "transparency_log": "public_rekor",
            "workflow_identity": f"https://github.com/{expected_workflow_ref}",
        },
    }


def _read_package_version(path: Path) -> str:
    with path.open("rb") as source:
        value = tomllib.load(source)
    try:
        version = value["project"]["version"]
    except (KeyError, TypeError) as error:
        raise PreflightError("package_version_unavailable") from error
    if not isinstance(version, str) or not version:
        raise PreflightError("package_version_invalid")
    return version


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_is_ancestor(commit: str, reference: str) -> bool:
    completed = subprocess.run(
        ("git", "merge-base", "--is-ancestor", commit, reference),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
