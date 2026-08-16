#!/usr/bin/env python3
"""Validate and render the content-free Hormuz container release contract."""

from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any


EXPECTED_REPOSITORY = "Xpounder-com/hormuz"
EXPECTED_IMAGE = "ghcr.io/xpounder-com/hormuz"
EXPECTED_WORKFLOW = ".github/workflows/release.yml"
EXPECTED_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
SEMVER_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class ReleaseContractError(RuntimeError):
    """Raised when a release input violates the fail-closed contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseContractError(f"invalid JSON evidence: {path.name}") from error


def _read_json_records(path: Path) -> list[Any]:
    try:
        raw = path.read_text().strip()
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseContractError(f"invalid JSON evidence: {path.name}") from error
    if not raw:
        raise ReleaseContractError(f"empty JSON evidence: {path.name}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        values: list[Any] = []
        try:
            for line in raw.splitlines():
                if line.strip():
                    values.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ReleaseContractError(f"invalid JSON evidence: {path.name}") from error
        return values
    return value if isinstance(value, list) else [value]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReleaseContractError(f"cannot read evidence file: {path.name}") from error
    return digest.hexdigest()


def _project_version(project_file: Path) -> str:
    try:
        project = tomllib.loads(project_file.read_text())
        version = project["project"]["version"]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, KeyError) as error:
        raise ReleaseContractError("cannot read project version") from error
    if not isinstance(version, str) or not SEMVER_TAG.fullmatch(f"v{version}"):
        raise ReleaseContractError("project version is not strict X.Y.Z")
    return version


def _git(arguments: list[str], project_root: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseContractError("git release validation failed") from error


def _validate_git_release(
    *,
    tag: str,
    sha: str,
    main_ref: str,
    project_root: Path,
) -> None:
    tag_ref = f"refs/tags/{tag}"
    tag_type = _git(["cat-file", "-t", tag_ref], project_root)
    if tag_type.returncode != 0 or tag_type.stdout.strip() != "tag":
        raise ReleaseContractError("release tag must be annotated")
    resolved = _git(["rev-parse", f"{tag_ref}^{{}}"], project_root)
    if resolved.returncode != 0 or resolved.stdout.strip() != sha:
        raise ReleaseContractError("release tag does not resolve to the workflow commit")
    ancestor = _git(["merge-base", "--is-ancestor", sha, main_ref], project_root)
    if ancestor.returncode != 0:
        raise ReleaseContractError("release commit is not reachable from origin/main")


def validate_release(
    *,
    tag: str,
    ref: str,
    sha: str,
    repository: str,
    event: str,
    project_file: Path,
    main_ref: str | None = None,
    runner_environment: str | None = None,
    deny_self_hosted_runners: bool = False,
) -> dict[str, Any]:
    match = SEMVER_TAG.fullmatch(tag)
    if match is None:
        raise ReleaseContractError("release tag must be strict vX.Y.Z")
    if ref != f"refs/tags/{tag}":
        raise ReleaseContractError("release ref does not match the tag")
    if not COMMIT_SHA.fullmatch(sha):
        raise ReleaseContractError("release commit must be a full lowercase SHA")
    if repository != EXPECTED_REPOSITORY:
        raise ReleaseContractError("release repository is not the approved repository")
    if event != "push":
        raise ReleaseContractError("container publication requires a tag push event")
    if deny_self_hosted_runners and runner_environment != "github-hosted":
        raise ReleaseContractError("container publication requires a GitHub-hosted runner")

    version = _project_version(project_file)
    if tag != f"v{version}":
        raise ReleaseContractError("release tag does not match the package version")
    if main_ref is not None:
        _validate_git_release(
            tag=tag,
            sha=sha,
            main_ref=main_ref,
            project_root=project_file.resolve().parent,
        )

    identity = (
        f"https://github.com/{repository}/{EXPECTED_WORKFLOW}@refs/tags/{tag}"
    )
    return {
        "schema": "hormuz.release-contract.v1",
        "repository": repository,
        "tag": tag,
        "version": version,
        "prerelease": match.group(1) == "0",
        "commit_sha": sha,
        "image": EXPECTED_IMAGE,
        "version_tag": f"{EXPECTED_IMAGE}:{version}",
        "revision_tag": f"{EXPECTED_IMAGE}:sha-{sha}",
        "signing_identity": identity,
        "oidc_issuer": EXPECTED_OIDC_ISSUER,
        "workflow": EXPECTED_WORKFLOW,
    }


def validate_package(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ReleaseContractError("package response must be an object")
    if value.get("package_type") != "container" or value.get("name") != "hormuz":
        raise ReleaseContractError("release package is not the Hormuz container")
    if value.get("visibility") != "private":
        raise ReleaseContractError("Hormuz container package must remain private")
    repository = value.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != EXPECTED_REPOSITORY:
        raise ReleaseContractError("Hormuz package is not linked to the source repository")
    return {
        "name": "hormuz",
        "package_type": "container",
        "visibility": "private",
        "repository": EXPECTED_REPOSITORY,
    }


def parse_digest(value: str) -> str:
    candidates = re.findall(r"sha256:[0-9a-f]{64}", value)
    unique = list(dict.fromkeys(candidates))
    if len(unique) != 1 or not DIGEST.fullmatch(unique[0]):
        raise ReleaseContractError("registry output did not contain exactly one digest")
    return unique[0]


def validate_image_inspection(
    value: Any,
    *,
    version: str,
    sha: str,
) -> dict[str, str]:
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ReleaseContractError("image inspection must contain one image")
    image = value[0]
    config = image.get("Config")
    if not isinstance(config, dict):
        raise ReleaseContractError("image inspection has no configuration")
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        raise ReleaseContractError("image inspection has no labels")
    expected = {
        "org.opencontainers.image.source": f"https://github.com/{EXPECTED_REPOSITORY}",
        "org.opencontainers.image.revision": sha,
        "org.opencontainers.image.version": version,
    }
    for key, expected_value in expected.items():
        if labels.get(key) != expected_value:
            raise ReleaseContractError(f"image label failed validation: {key}")
    if config.get("User") != "65532:65532":
        raise ReleaseContractError("release image is not configured for UID/GID 65532")
    if image.get("Architecture") != "amd64" or image.get("Os") != "linux":
        raise ReleaseContractError("release inspection did not resolve linux/amd64")
    return {
        "source": expected["org.opencontainers.image.source"],
        "revision": sha,
        "version": version,
        "user": "65532:65532",
        "platform": "linux/amd64",
    }


def render_slsa_predicate(
    *,
    contract: dict[str, Any],
    dockerfile: Path,
    dependency_lock: Path,
    workflow_run_url: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
) -> dict[str, Any]:
    if contract.get("schema") != "hormuz.release-contract.v1":
        raise ReleaseContractError("invalid release contract schema")
    sha = str(contract.get("commit_sha", ""))
    tag = str(contract.get("tag", ""))
    version = str(contract.get("version", ""))
    if not COMMIT_SHA.fullmatch(sha) or not SEMVER_TAG.fullmatch(tag):
        raise ReleaseContractError("invalid release source in provenance contract")
    if tag != f"v{version}":
        raise ReleaseContractError("provenance version does not match its tag")
    if not re.fullmatch(r"[1-9][0-9]*", workflow_run_id):
        raise ReleaseContractError("invalid workflow run ID")
    if not re.fullmatch(r"[1-9][0-9]*", workflow_run_attempt):
        raise ReleaseContractError("invalid workflow run attempt")
    if not workflow_run_url.startswith(
        f"https://github.com/{EXPECTED_REPOSITORY}/actions/runs/{workflow_run_id}"
    ):
        raise ReleaseContractError("invalid workflow run URL")

    try:
        dockerfile_text = dockerfile.read_text()
    except (OSError, UnicodeDecodeError) as error:
        raise ReleaseContractError("cannot read Dockerfile for provenance") from error
    base_match = re.search(
        r"^ARG PYTHON_IMAGE=([^\s@]+)@(sha256:[0-9a-f]{64})$",
        dockerfile_text,
        flags=re.MULTILINE,
    )
    if base_match is None:
        raise ReleaseContractError("Dockerfile base image is not digest pinned")
    base_name, base_digest = base_match.groups()
    repository_url = f"https://github.com/{EXPECTED_REPOSITORY}"
    source_uri = f"git+{repository_url}@refs/tags/{tag}"
    lock_uri = f"git+{repository_url}@{sha}#path=deploy/container/requirements.lock"
    return {
        "buildDefinition": {
            "buildType": (
                f"{repository_url}/blob/{sha}/docs/RELEASES.md#publication-contract"
            ),
            "externalParameters": {
                "repository": repository_url,
                "ref": f"refs/tags/{tag}",
                "commit": sha,
                "version": version,
                "dockerfile": "Dockerfile",
                "dependencyLock": "deploy/container/requirements.lock",
                "platforms": ["linux/amd64", "linux/arm64"],
            },
            "internalParameters": {
                "runner": "github-hosted/ubuntu-latest",
                "workflow": EXPECTED_WORKFLOW,
                "runId": int(workflow_run_id),
                "runAttempt": int(workflow_run_attempt),
            },
            "resolvedDependencies": [
                {"uri": source_uri, "digest": {"gitCommit": sha}},
                {
                    "uri": f"docker://docker.io/library/{base_name}",
                    "digest": {"sha256": base_digest.removeprefix("sha256:")},
                },
                {"uri": lock_uri, "digest": {"sha256": _sha256(dependency_lock)}},
            ],
        },
        "runDetails": {
            "builder": {"id": contract["signing_identity"]},
            "metadata": {
                "invocationId": workflow_run_url,
            },
            "byproducts": [],
        },
    }


def validate_slsa_verification(
    records: list[Any],
    *,
    predicate: dict[str, Any],
    image: str,
    digest: str,
) -> dict[str, Any]:
    if image != EXPECTED_IMAGE or not DIGEST.fullmatch(digest):
        raise ReleaseContractError("invalid provenance subject")
    expected_hex = digest.removeprefix("sha256:")
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("payload"), str):
            continue
        try:
            decoded = base64.b64decode(record["payload"], validate=True)
            statement = json.loads(decoded)
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(statement, dict):
            continue
        if statement.get("predicateType") != SLSA_PREDICATE_TYPE:
            continue
        if statement.get("predicate") != predicate:
            continue
        subjects = statement.get("subject")
        if not isinstance(subjects, list):
            continue
        subject_matches = any(
            isinstance(subject, dict)
            and subject.get("name") == image
            and isinstance(subject.get("digest"), dict)
            and subject["digest"].get("sha256") == expected_hex
            for subject in subjects
        )
        if not subject_matches:
            continue
        return {
            "schema": "hormuz.slsa-verification.v1",
            "predicate_type": SLSA_PREDICATE_TYPE,
            "subject": f"{image}@{digest}",
            "source_ref": predicate["buildDefinition"]["externalParameters"]["ref"],
            "source_commit": predicate["buildDefinition"]["externalParameters"][
                "commit"
            ],
            "builder": predicate["runDetails"]["builder"]["id"],
        }
    raise ReleaseContractError("no verified SLSA statement matched the release contract")


def render_evidence(
    *,
    contract: dict[str, Any],
    package: dict[str, str],
    digest: str,
    workflow_run_url: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
    cosign_version: str,
    cosign_verification: Path,
    provenance_verification: Path,
    provenance_validation: Path,
) -> dict[str, Any]:
    if contract.get("schema") != "hormuz.release-contract.v1":
        raise ReleaseContractError("invalid release contract schema")
    if package.get("visibility") != "private":
        raise ReleaseContractError("release evidence requires a private package")
    if not DIGEST.fullmatch(digest):
        raise ReleaseContractError("invalid image digest")
    if not re.fullmatch(r"[1-9][0-9]*", workflow_run_id):
        raise ReleaseContractError("invalid workflow run ID")
    if not re.fullmatch(r"[1-9][0-9]*", workflow_run_attempt):
        raise ReleaseContractError("invalid workflow run attempt")
    if not workflow_run_url.startswith(
        f"https://github.com/{EXPECTED_REPOSITORY}/actions/runs/{workflow_run_id}"
    ):
        raise ReleaseContractError("invalid workflow run URL")
    cosign_result = _read_json_records(cosign_verification)
    provenance_result = _read_json_records(provenance_verification)
    if not cosign_result:
        raise ReleaseContractError("Cosign verification evidence is empty")
    if not provenance_result:
        raise ReleaseContractError("provenance verification evidence is empty")

    image = str(contract["image"])
    version = str(contract["version"])
    sha = str(contract["commit_sha"])
    provenance_summary = _read_json(provenance_validation)
    if (
        not isinstance(provenance_summary, dict)
        or provenance_summary.get("schema") != "hormuz.slsa-verification.v1"
        or provenance_summary.get("subject") != f"{image}@{digest}"
        or provenance_summary.get("source_ref") != f"refs/tags/{contract['tag']}"
        or provenance_summary.get("source_commit") != sha
        or provenance_summary.get("builder") != contract["signing_identity"]
    ):
        raise ReleaseContractError("SLSA verification summary does not match release")
    return {
        "schema": "hormuz.release-evidence.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repository": EXPECTED_REPOSITORY,
        "tag": contract["tag"],
        "version": version,
        "commit_sha": sha,
        "image": image,
        "digest": digest,
        "immutable_image": f"{image}@{digest}",
        "version_tag": f"{image}:{version}",
        "revision_tag": f"{image}:sha-{sha}",
        "package": package,
        "signature": {
            "scheme": "sigstore-keyless",
            "verified": True,
            "identity": contract["signing_identity"],
            "oidc_issuer": contract["oidc_issuer"],
            "cosign_version": cosign_version.strip(),
            "verification_sha256": _sha256(cosign_verification),
        },
        "provenance": {
            "scheme": "cosign-slsa-provenance-v1",
            "verified": True,
            "predicate_type": SLSA_PREDICATE_TYPE,
            "transparency_service": "sigstore-public-good",
            "verification_sha256": _sha256(provenance_verification),
            "validation_sha256": _sha256(provenance_validation),
        },
        "workflow": {
            "url": workflow_run_url,
            "run_id": int(workflow_run_id),
            "run_attempt": int(workflow_run_attempt),
        },
        "privacy": (
            "metadata-only release evidence; no prompts, responses, source content, "
            "or credentials"
        ),
    }


def render_notes(evidence: dict[str, Any]) -> str:
    image = evidence["immutable_image"]
    identity = evidence["signature"]["identity"]
    tag = evidence["tag"]
    prerelease = int(str(evidence["version"]).split(".", 1)[0]) == 0
    phase = "private alpha" if prerelease else "private release"
    return f"""# Hormuz {evidence['version']} ({phase})

This release publishes the signed Hormuz reference container for the exact commit `{evidence['commit_sha']}`.

Immutable image:

```text
{image}
```

Verify the keyless signature:

```bash
cosign verify \\
  --certificate-identity '{identity}' \\
  --certificate-oidc-issuer '{EXPECTED_OIDC_ISSUER}' \\
  '{image}'
```

Verify signed SLSA build provenance:

```bash
cosign verify-attestation \\
  --type slsaprovenance1 \\
  --certificate-identity '{identity}' \\
  --certificate-oidc-issuer '{EXPECTED_OIDC_ISSUER}' \\
  '{image}'
```

The attached evidence is metadata-only. This remains a single-node SQLite reference image; it is not evidence of TLS termination, shared persistence, HA, backup/PITR, KMS/BYOK custody, or independent security review.
"""


def _github_outputs(path: Path, values: dict[str, str]) -> None:
    with path.open("a") as output:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ReleaseContractError("multiline GitHub output is not allowed")
            output.write(f"{key}={value}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--tag", required=True)
    validate.add_argument("--ref", required=True)
    validate.add_argument("--sha", required=True)
    validate.add_argument("--repository", required=True)
    validate.add_argument("--event", required=True)
    validate.add_argument("--project-file", type=Path, default=Path("pyproject.toml"))
    validate.add_argument("--main-ref")
    validate.add_argument("--runner-environment")
    validate.add_argument("--deny-self-hosted-runners", action="store_true")
    validate.add_argument("--output", type=Path, required=True)
    validate.add_argument("--github-output", type=Path)

    package = subparsers.add_parser("validate-package")
    package.add_argument("--input", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)

    digest = subparsers.add_parser("parse-digest")
    digest.add_argument("--input", type=Path, required=True)
    digest.add_argument("--github-output", type=Path, required=True)

    image = subparsers.add_parser("validate-image")
    image.add_argument("--input", type=Path, required=True)
    image.add_argument("--version", required=True)
    image.add_argument("--sha", required=True)
    image.add_argument("--output", type=Path, required=True)

    predicate = subparsers.add_parser("predicate")
    predicate.add_argument("--contract", type=Path, required=True)
    predicate.add_argument("--dockerfile", type=Path, required=True)
    predicate.add_argument("--dependency-lock", type=Path, required=True)
    predicate.add_argument("--workflow-run-url", required=True)
    predicate.add_argument("--workflow-run-id", required=True)
    predicate.add_argument("--workflow-run-attempt", required=True)
    predicate.add_argument("--output", type=Path, required=True)

    provenance = subparsers.add_parser("validate-provenance")
    provenance.add_argument("--input", type=Path, required=True)
    provenance.add_argument("--predicate", type=Path, required=True)
    provenance.add_argument("--image", required=True)
    provenance.add_argument("--digest", required=True)
    provenance.add_argument("--output", type=Path, required=True)

    evidence = subparsers.add_parser("evidence")
    evidence.add_argument("--contract", type=Path, required=True)
    evidence.add_argument("--package", type=Path, required=True)
    evidence.add_argument("--digest", required=True)
    evidence.add_argument("--workflow-run-url", required=True)
    evidence.add_argument("--workflow-run-id", required=True)
    evidence.add_argument("--workflow-run-attempt", required=True)
    evidence.add_argument("--cosign-version", required=True)
    evidence.add_argument("--cosign-verification", type=Path, required=True)
    evidence.add_argument("--provenance-verification", type=Path, required=True)
    evidence.add_argument("--provenance-validation", type=Path, required=True)
    evidence.add_argument("--output", type=Path, required=True)
    evidence.add_argument("--notes-output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "validate":
            contract = validate_release(
                tag=args.tag,
                ref=args.ref,
                sha=args.sha,
                repository=args.repository,
                event=args.event,
                project_file=args.project_file,
                main_ref=args.main_ref,
                runner_environment=args.runner_environment,
                deny_self_hosted_runners=args.deny_self_hosted_runners,
            )
            _write_json(args.output, contract)
            if args.github_output is not None:
                _github_outputs(
                    args.github_output,
                    {
                        "version": contract["version"],
                        "prerelease": str(contract["prerelease"]).lower(),
                        "image": contract["image"],
                        "version_tag": contract["version_tag"],
                        "revision_tag": contract["revision_tag"],
                        "signing_identity": contract["signing_identity"],
                        "oidc_issuer": contract["oidc_issuer"],
                    },
                )
        elif args.command == "validate-package":
            _write_json(args.output, validate_package(_read_json(args.input)))
        elif args.command == "parse-digest":
            try:
                raw = args.input.read_text()
            except OSError as error:
                raise ReleaseContractError("cannot read registry digest") from error
            _github_outputs(
                args.github_output,
                {"exists": "true", "digest": parse_digest(raw)},
            )
        elif args.command == "validate-image":
            if not SEMVER_TAG.fullmatch(f"v{args.version}"):
                raise ReleaseContractError("invalid image version")
            if not COMMIT_SHA.fullmatch(args.sha):
                raise ReleaseContractError("invalid image revision")
            _write_json(
                args.output,
                validate_image_inspection(
                    _read_json(args.input),
                    version=args.version,
                    sha=args.sha,
                ),
            )
        elif args.command == "predicate":
            _write_json(
                args.output,
                render_slsa_predicate(
                    contract=_read_json(args.contract),
                    dockerfile=args.dockerfile,
                    dependency_lock=args.dependency_lock,
                    workflow_run_url=args.workflow_run_url,
                    workflow_run_id=args.workflow_run_id,
                    workflow_run_attempt=args.workflow_run_attempt,
                ),
            )
        elif args.command == "validate-provenance":
            _write_json(
                args.output,
                validate_slsa_verification(
                    _read_json_records(args.input),
                    predicate=_read_json(args.predicate),
                    image=args.image,
                    digest=args.digest,
                ),
            )
        elif args.command == "evidence":
            contract = _read_json(args.contract)
            package = _read_json(args.package)
            value = render_evidence(
                contract=contract,
                package=package,
                digest=args.digest,
                workflow_run_url=args.workflow_run_url,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
                cosign_version=args.cosign_version,
                cosign_verification=args.cosign_verification,
                provenance_verification=args.provenance_verification,
                provenance_validation=args.provenance_validation,
            )
            _write_json(args.output, value)
            args.notes_output.write_text(render_notes(value))
    except ReleaseContractError as error:
        print(f"release contract failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
