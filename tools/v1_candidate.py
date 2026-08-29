#!/usr/bin/env python3
"""Create and verify the immutable Hormuz v1 candidate custody contract.

This tool never builds, uploads, tags, or publishes an artifact. It validates
the one source archive produced by the freeze workflow and emits owner-only,
non-overwriting JSON records for custody and promotion decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import tomllib
from datetime import UTC, datetime
from email.parser import BytesParser
from pathlib import Path


SCHEMA_ID = "hormuz.v1-candidate-manifest"
SCHEMA_VERSION = 1
PROOF_SCHEMA_ID = "hormuz.v1-candidate-custody-proof"
PROMOTION_SCHEMA_ID = "hormuz.v1-candidate-promotion-readiness"
FINAL_RELEASE_PROOF_SCHEMA_ID = "hormuz.v1-final-release-proof"
FINAL_TAG_PROOF_SCHEMA_ID = "hormuz.v1-final-tag-proof"
EVIDENCE_SNAPSHOT_SCHEMA_ID = "hormuz.v1-candidate-evidence-snapshot"
FREEZE_AUTHORIZATION_PROOF_SCHEMA_ID = "hormuz.v1-freeze-authorization-proof"
EXPECTED_REPOSITORY = "Xpounder-com/hormuz"
TARGET_VERSION = "v1.0.0"
PACKAGE_NAME = "hormuz"
PACKAGE_VERSION = "1.0.0"
FINAL_TAG = TARGET_VERSION
FINAL_RELEASE_TITLE = "Hormuz v1.0.0"
ARCHIVE_NAME = "hormuz-1.0.0.tar.gz"
MANIFEST_NAME = "hormuz-v1.0.0-candidate-manifest.json"
GATE_ISSUE = "https://github.com/Xpounder-com/hormuz/issues/173"
EVIDENCE_SCHEMA_ID = "hormuz.v1-internal-repeatability-evidence"
EVIDENCE_SCHEMA_VERSION = 1
WORKFLOW_PATH = ".github/workflows/freeze-v1-candidate.yml"
FREEZE_AUTHORIZATION_JOB_NAME = "Authorize the designated v1 release steward"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 20_000
REQUIRED_ARCHIVE_PATHS = (
    "PKG-INFO",
    "config.example.json",
    "docs/POLICY_ADMIN_USABILITY.md",
    "examples/policy-admin-usability-baseline.json",
    "examples/policy-admin-usability-scenarios.json",
    "hormuz/cli.py",
    "hormuz/commands/policy.py",
    "pyproject.toml",
    "requirements/v1-source-build.lock",
    "tools/promote_v1_candidate.sh",
    "tools/run_v1_internal_repeatability.py",
    "tools/v1_candidate.py",
    "tools/verify_policy_admin_usability_evidence.py",
    "tools/verify_v1_internal_repeatability_evidence.py",
)

_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CUSTODY_TAG_RE = re.compile(r"candidate-v1\.0\.0-([0-9a-f]{64})\Z")
_TAGGER_HEADER_RE = re.compile(
    r"tagger .+ <[^<>\r\n]*> ([0-9]+) ([+-])([0-9]{2})([0-9]{2})\Z"
)
_RUN_URL_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/actions/runs/([1-9][0-9]*)/attempts/([1-9][0-9]*)\Z"
)


class V1CandidateError(ValueError):
    """A fail-closed candidate custody contract violation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise V1CandidateError("duplicate_json_member")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise V1CandidateError("json_number_not_finite")


def _safe_read(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise V1CandidateError(f"{label}_unavailable") from error
    if not stat.S_ISREG(before.st_mode):
        raise V1CandidateError(f"{label}_not_regular")
    if before.st_size < 1 or before.st_size > maximum:
        raise V1CandidateError(f"{label}_size_invalid")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise V1CandidateError(f"{label}_unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
        ):
            raise V1CandidateError(f"{label}_changed_during_open")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise V1CandidateError(f"{label}_size_invalid")
        after = os.fstat(descriptor)
        if (
            (after.st_dev, after.st_ino, after.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or total != opened.st_size
        ):
            raise V1CandidateError(f"{label}_changed_during_read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _decode_json(payload: bytes, *, label: str) -> object:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except V1CandidateError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise V1CandidateError(f"{label}_invalid_json") from error


def _read_json(path: Path, *, label: str) -> object:
    return _decode_json(
        _safe_read(path, maximum=MAX_JSON_BYTES, label=label),
        label=label,
    )


def validate_freeze_run_authorization(
    runs_api_path: Path,
    jobs_directory: Path,
    *,
    source_commit: str,
    current_run_id: int,
) -> dict[str, object]:
    if _REVISION_RE.fullmatch(source_commit) is None:
        raise V1CandidateError("freeze_authorization_source_commit_invalid")
    if (
        isinstance(current_run_id, bool)
        or not isinstance(current_run_id, int)
        or current_run_id < 1
    ):
        raise V1CandidateError("freeze_authorization_current_run_invalid")
    try:
        jobs_directory_mode = jobs_directory.lstat().st_mode
    except OSError as error:
        raise V1CandidateError("freeze_authorization_jobs_unavailable") from error
    if not stat.S_ISDIR(jobs_directory_mode):
        raise V1CandidateError("freeze_authorization_jobs_not_directory")

    pages = _read_json(runs_api_path, label="freeze_runs_api")
    if not isinstance(pages, list) or not pages:
        raise V1CandidateError("freeze_runs_api_invalid")
    run_ids: list[int] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(
            page.get("workflow_runs"), list
        ):
            raise V1CandidateError("freeze_runs_api_invalid")
        for run in page["workflow_runs"]:
            if not isinstance(run, dict):
                raise V1CandidateError("freeze_runs_api_invalid")
            run_id = run.get("id")
            workflow_path = run.get("path")
            if (
                isinstance(run_id, bool)
                or not isinstance(run_id, int)
                or run_id < 1
                or run.get("event") != "workflow_dispatch"
                or run.get("head_sha") != source_commit
                or not isinstance(workflow_path, str)
                or workflow_path.split("@", 1)[0] != WORKFLOW_PATH
                or run_id in run_ids
            ):
                raise V1CandidateError("freeze_runs_api_invalid")
            run_ids.append(run_id)
    if current_run_id not in run_ids:
        raise V1CandidateError("freeze_authorization_current_run_missing")

    authorized_run_ids: list[int] = []
    for run_id in run_ids:
        job_pages = _read_json(
            jobs_directory / f"{run_id}.json",
            label=f"freeze_run_{run_id}_jobs_api",
        )
        if not isinstance(job_pages, list) or not job_pages:
            raise V1CandidateError("freeze_jobs_api_invalid")
        authorization_succeeded = False
        for page in job_pages:
            if not isinstance(page, dict) or not isinstance(page.get("jobs"), list):
                raise V1CandidateError("freeze_jobs_api_invalid")
            for job in page["jobs"]:
                if (
                    not isinstance(job, dict)
                    or job.get("run_id") != run_id
                    or job.get("head_sha") != source_commit
                ):
                    raise V1CandidateError("freeze_jobs_api_invalid")
                if (
                    job.get("name") == FREEZE_AUTHORIZATION_JOB_NAME
                    and job.get("status") == "completed"
                    and job.get("conclusion") == "success"
                ):
                    authorization_succeeded = True
        if authorization_succeeded:
            authorized_run_ids.append(run_id)

    if authorized_run_ids != [current_run_id]:
        raise V1CandidateError("freeze_run_authorization_invalid")
    return {
        "schema_id": FREEZE_AUTHORIZATION_PROOF_SCHEMA_ID,
        "schema_version": 1,
        "status": "only_current_run_authorized",
        "source_commit": source_commit,
        "current_run_id": current_run_id,
        "observed_run_count": len(run_ids),
        "authorized_run_ids": authorized_run_ids,
        "authorization_job_name": FREEZE_AUTHORIZATION_JOB_NAME,
    }


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _require_fields(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise V1CandidateError(f"{label}_fields_invalid")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise V1CandidateError(f"{label}_invalid")
    return value


def _require_timestamp(value: object, label: str) -> str:
    timestamp = _require_string(value, label)
    try:
        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as error:
        raise V1CandidateError(f"{label}_invalid") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != timestamp:
        raise V1CandidateError(f"{label}_invalid")
    return timestamp


def _timestamp_value(value: object, label: str) -> datetime:
    return datetime.strptime(_require_timestamp(value, label), "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=UTC
    )


def _archive_member_bytes(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    *,
    maximum: int,
    label: str,
) -> bytes:
    if not member.isfile() or member.size < 1 or member.size > maximum:
        raise V1CandidateError(f"{label}_invalid")
    source = archive.extractfile(member)
    if source is None:
        raise V1CandidateError(f"{label}_unavailable")
    payload = source.read(maximum + 1)
    if len(payload) != member.size or len(payload) > maximum:
        raise V1CandidateError(f"{label}_invalid")
    return payload


def inspect_archive(path: Path) -> dict[str, object]:
    """Inspect one exact v1 source archive without extracting it."""

    if path.name != ARCHIVE_NAME:
        raise V1CandidateError("archive_name_invalid")
    payload = _safe_read(path, maximum=MAX_ARCHIVE_BYTES, label="archive")
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise V1CandidateError("archive_member_count_invalid")
            normalized: dict[str, tarfile.TarInfo] = {}
            roots: set[str] = set()
            for member in members:
                name = member.name.rstrip("/")
                parts = name.split("/")
                if (
                    not name
                    or member.name.startswith("/")
                    or any(part in {"", ".", ".."} for part in parts)
                    or not (member.isfile() or member.isdir())
                ):
                    raise V1CandidateError("archive_member_unsafe")
                if name in normalized:
                    raise V1CandidateError("archive_member_duplicate")
                normalized[name] = member
                roots.add(parts[0])

            expected_root = f"{PACKAGE_NAME}-{PACKAGE_VERSION}"
            if roots != {expected_root}:
                raise V1CandidateError("archive_root_invalid")
            required_members: dict[str, tarfile.TarInfo] = {}
            for relative in REQUIRED_ARCHIVE_PATHS:
                member = normalized.get(f"{expected_root}/{relative}")
                if member is None or not member.isfile():
                    raise V1CandidateError(f"archive_required_member_missing:{relative}")
                required_members[relative] = member

            package_metadata = BytesParser().parsebytes(
                _archive_member_bytes(
                    archive,
                    required_members["PKG-INFO"],
                    maximum=256 * 1024,
                    label="archive_package_metadata",
                )
            )
            if (
                package_metadata.get("Name") != PACKAGE_NAME
                or package_metadata.get("Version") != PACKAGE_VERSION
            ):
                raise V1CandidateError("archive_package_identity_invalid")

            try:
                pyproject = tomllib.loads(
                    _archive_member_bytes(
                        archive,
                        required_members["pyproject.toml"],
                        maximum=1024 * 1024,
                        label="archive_pyproject",
                    ).decode("utf-8")
                )
            except (UnicodeError, tomllib.TOMLDecodeError) as error:
                raise V1CandidateError("archive_pyproject_invalid") from error
            project = pyproject.get("project")
            if not isinstance(project, dict) or (
                project.get("name"), project.get("version")
            ) != (PACKAGE_NAME, PACKAGE_VERSION):
                raise V1CandidateError("archive_pyproject_identity_invalid")
    except V1CandidateError:
        raise
    except (tarfile.TarError, OSError, EOFError) as error:
        raise V1CandidateError("archive_invalid") from error

    return {
        "name": ARCHIVE_NAME,
        "size_bytes": len(payload),
        "digest": _sha256(payload),
        "package_name": PACKAGE_NAME,
        "package_version": PACKAGE_VERSION,
        "required_member_count": len(REQUIRED_ARCHIVE_PATHS),
    }


def _custody_tag(digest: str) -> str:
    return f"candidate-v1.0.0-{digest.removeprefix('sha256:')}"


def create_manifest(
    archive: Path,
    *,
    source_commit: str,
    frozen_at: str,
    invocation_url: str,
    run_id: int,
    run_attempt: int,
) -> dict[str, object]:
    artifact = inspect_archive(archive)
    if _REVISION_RE.fullmatch(source_commit) is None:
        raise V1CandidateError("source_commit_invalid")
    frozen_at = _require_timestamp(frozen_at, "frozen_at")
    match = _RUN_URL_RE.fullmatch(invocation_url)
    if (
        match is None
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or not isinstance(run_attempt, int)
        or isinstance(run_attempt, bool)
        or run_attempt != 1
        or int(match.group(1)) != run_id
        or int(match.group(2)) != run_attempt
    ):
        raise V1CandidateError("build_invocation_invalid")

    digest = str(artifact["digest"])
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "candidate": {
            "target_version": TARGET_VERSION,
            "artifact_kind": "source_archive",
            "artifact_name": ARCHIVE_NAME,
            "artifact_size_bytes": artifact["size_bytes"],
            "artifact_digest": digest,
            "source_commit": source_commit,
            "frozen_at": frozen_at,
            "package_name": PACKAGE_NAME,
            "package_version": PACKAGE_VERSION,
        },
        "custody": {
            "repository": EXPECTED_REPOSITORY,
            "release_tag": _custody_tag(digest),
            "release_state": "published_immutable_candidate",
            "immutable_releases_required": True,
            "asset_overwrite_permitted": False,
        },
        "gate": {
            "issue": GATE_ISSUE,
            "evidence_schema_id": EVIDENCE_SCHEMA_ID,
            "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
            "session_digest_binding_required": True,
            "changed_artifact_invalidates_evidence": True,
        },
        "build": {
            "workflow_path": WORKFLOW_PATH,
            "invocation_url": invocation_url,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "archive_build_count": 1,
            "promotion_rebuild_permitted": False,
        },
    }


def validate_manifest(value: object) -> dict[str, object]:
    root = _require_fields(
        value,
        {"schema_id", "schema_version", "candidate", "custody", "gate", "build"},
        "manifest",
    )
    if root["schema_id"] != SCHEMA_ID or root["schema_version"] != SCHEMA_VERSION:
        raise V1CandidateError("manifest_schema_invalid")

    candidate = _require_fields(
        root["candidate"],
        {
            "target_version",
            "artifact_kind",
            "artifact_name",
            "artifact_size_bytes",
            "artifact_digest",
            "source_commit",
            "frozen_at",
            "package_name",
            "package_version",
        },
        "candidate",
    )
    digest = candidate["artifact_digest"]
    size = candidate["artifact_size_bytes"]
    source_commit = candidate["source_commit"]
    if (
        candidate["target_version"] != TARGET_VERSION
        or candidate["artifact_kind"] != "source_archive"
        or candidate["artifact_name"] != ARCHIVE_NAME
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or size > MAX_ARCHIVE_BYTES
        or not isinstance(digest, str)
        or _DIGEST_RE.fullmatch(digest) is None
        or not isinstance(source_commit, str)
        or _REVISION_RE.fullmatch(source_commit) is None
        or candidate["package_name"] != PACKAGE_NAME
        or candidate["package_version"] != PACKAGE_VERSION
    ):
        raise V1CandidateError("candidate_identity_invalid")
    _require_timestamp(candidate["frozen_at"], "candidate_frozen_at")

    custody = _require_fields(
        root["custody"],
        {
            "repository",
            "release_tag",
            "release_state",
            "immutable_releases_required",
            "asset_overwrite_permitted",
        },
        "custody",
    )
    if (
        custody["repository"] != EXPECTED_REPOSITORY
        or custody["release_tag"] != _custody_tag(digest)
        or _CUSTODY_TAG_RE.fullmatch(str(custody["release_tag"])) is None
        or custody["release_state"] != "published_immutable_candidate"
        or custody["immutable_releases_required"] is not True
        or custody["asset_overwrite_permitted"] is not False
    ):
        raise V1CandidateError("custody_contract_invalid")

    gate = _require_fields(
        root["gate"],
        {
            "issue",
            "evidence_schema_id",
            "evidence_schema_version",
            "session_digest_binding_required",
            "changed_artifact_invalidates_evidence",
        },
        "gate",
    )
    if gate != {
        "issue": GATE_ISSUE,
        "evidence_schema_id": EVIDENCE_SCHEMA_ID,
        "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
        "session_digest_binding_required": True,
        "changed_artifact_invalidates_evidence": True,
    }:
        raise V1CandidateError("gate_contract_invalid")

    build = _require_fields(
        root["build"],
        {
            "workflow_path",
            "invocation_url",
            "run_id",
            "run_attempt",
            "archive_build_count",
            "promotion_rebuild_permitted",
        },
        "build",
    )
    run_id = build["run_id"]
    run_attempt = build["run_attempt"]
    match = _RUN_URL_RE.fullmatch(str(build["invocation_url"]))
    if (
        build["workflow_path"] != WORKFLOW_PATH
        or not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id < 1
        or run_attempt != 1
        or isinstance(run_attempt, bool)
        or match is None
        or int(match.group(1)) != run_id
        or int(match.group(2)) != run_attempt
        or build["archive_build_count"] != 1
        or isinstance(build["archive_build_count"], bool)
        or build["promotion_rebuild_permitted"] is not False
    ):
        raise V1CandidateError("build_contract_invalid")
    return root


def _write_new_private_bytes(path: Path, encoded: bytes) -> None:
    if path.name == "":
        raise V1CandidateError("output_path_invalid")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent = path.parent.lstat()
    except OSError as error:
        raise V1CandidateError("output_parent_unavailable") from error
    if not stat.S_ISDIR(parent.st_mode):
        raise V1CandidateError("output_parent_not_directory")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise V1CandidateError("output_unavailable") from error
    else:
        raise V1CandidateError("output_exists")

    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            destination.write(encoded)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temporary_path, path, follow_symlinks=False)
        except FileExistsError as error:
            raise V1CandidateError("output_exists") from error
        os.chmod(path, 0o600)
        temporary_path.unlink()
        temporary_path = None
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _write_new_private_json(path: Path, value: dict[str, object]) -> None:
    encoded = (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")
    _write_new_private_bytes(path, encoded)


def _validate_archive_binding(manifest: dict[str, object], archive: Path) -> dict[str, object]:
    inspected = inspect_archive(archive)
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)
    if (
        inspected["name"] != candidate["artifact_name"]
        or inspected["size_bytes"] != candidate["artifact_size_bytes"]
        or inspected["digest"] != candidate["artifact_digest"]
        or inspected["package_name"] != candidate["package_name"]
        or inspected["package_version"] != candidate["package_version"]
    ):
        raise V1CandidateError("archive_manifest_mismatch")
    return inspected


def _validate_candidate_release(
    release: object,
    immutable_settings: object,
    *,
    manifest: dict[str, object],
    manifest_payload: bytes,
    archive: dict[str, object],
) -> str:
    if not isinstance(immutable_settings, dict) or immutable_settings.get("enabled") is not True:
        raise V1CandidateError("immutable_releases_not_enabled")
    if not isinstance(release, dict):
        raise V1CandidateError("release_invalid")
    candidate = manifest["candidate"]
    custody = manifest["custody"]
    assert isinstance(candidate, dict) and isinstance(custody, dict)
    release_tag = release.get("tag_name")
    if (
        release_tag != custody["release_tag"]
        or release.get("target_commitish") != candidate["source_commit"]
        or release.get("draft") is not False
        or release.get("prerelease") is not True
        or release.get("immutable") is not True
    ):
        raise V1CandidateError("candidate_release_state_invalid")

    assets = release.get("assets")
    if not isinstance(assets, list) or len(assets) != 2:
        raise V1CandidateError("release_assets_invalid")
    by_name: dict[str, dict[str, object]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise V1CandidateError("release_asset_invalid")
        name = asset.get("name")
        if not isinstance(name, str) or name in by_name:
            raise V1CandidateError("release_asset_invalid")
        by_name[name] = asset
    if set(by_name) != {ARCHIVE_NAME, MANIFEST_NAME}:
        raise V1CandidateError("release_asset_set_invalid")

    expected = {
        ARCHIVE_NAME: (archive["size_bytes"], archive["digest"]),
        MANIFEST_NAME: (len(manifest_payload), _sha256(manifest_payload)),
    }
    for name, (size, digest) in expected.items():
        asset = by_name[name]
        if (
            asset.get("state") != "uploaded"
            or asset.get("size") != size
            or asset.get("digest") != digest
        ):
            raise V1CandidateError(f"release_asset_binding_invalid:{name}")
    return str(release_tag)


def _validate_custody_snapshot(
    manifest_payload: bytes,
    archive_path: Path,
    release_path: Path,
    immutable_settings_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    manifest = validate_manifest(_decode_json(manifest_payload, label="manifest"))
    archive = _validate_archive_binding(manifest, archive_path)
    release = _read_json(release_path, label="release_api")
    release_tag = _validate_candidate_release(
        release,
        _read_json(immutable_settings_path, label="immutable_api"),
        manifest=manifest,
        manifest_payload=manifest_payload,
        archive=archive,
    )
    candidate = manifest["candidate"]
    custody = manifest["custody"]
    assert isinstance(candidate, dict) and isinstance(custody, dict)
    proof = {
        "schema_id": PROOF_SCHEMA_ID,
        "schema_version": 1,
        "status": "frozen_in_immutable_candidate_custody",
        "repository": EXPECTED_REPOSITORY,
        "release_tag": release_tag,
        "custody_release_tag": custody["release_tag"],
        "target_version": TARGET_VERSION,
        "source_commit": candidate["source_commit"],
        "candidate_artifact_digest": candidate["artifact_digest"],
        "candidate_artifact_size_bytes": candidate["artifact_size_bytes"],
        "archive_reverified": True,
        "release_asset_digests_verified": True,
        "immutable_releases_enabled": True,
        "release_immutable": True,
        "asset_overwrite_permitted": False,
        "promotion_rebuild_permitted": False,
    }
    assert isinstance(release, dict)
    return proof, manifest, release


def validate_custody(
    manifest_path: Path,
    archive_path: Path,
    release_path: Path,
    immutable_settings_path: Path,
) -> dict[str, object]:
    manifest_payload = _safe_read(manifest_path, maximum=MAX_JSON_BYTES, label="manifest")
    proof, _manifest, _release = _validate_custody_snapshot(
        manifest_payload,
        archive_path,
        release_path,
        immutable_settings_path,
    )
    return proof


def _validate_freeze_run(run: object, manifest: dict[str, object]) -> str:
    if not isinstance(run, dict):
        raise V1CandidateError("freeze_run_invalid")
    candidate = manifest["candidate"]
    build = manifest["build"]
    assert isinstance(candidate, dict) and isinstance(build, dict)
    repository = run.get("repository")
    head_repository = run.get("head_repository")
    run_id = run.get("id")
    run_attempt = run.get("run_attempt")
    invocation_url = str(build["invocation_url"])
    run_url = invocation_url.removesuffix("/attempts/1")
    if (
        not isinstance(run_id, int)
        or isinstance(run_id, bool)
        or run_id != build["run_id"]
        or run_attempt != 1
        or isinstance(run_attempt, bool)
        or run.get("name") != "Freeze v1.0.0 candidate"
        or run.get("path") != WORKFLOW_PATH
        or run.get("event") != "workflow_dispatch"
        or run.get("head_branch") != "main"
        or run.get("head_sha") != candidate["source_commit"]
        or run.get("status") != "completed"
        or run.get("conclusion") != "success"
        or run.get("html_url") != run_url
        or not isinstance(repository, dict)
        or repository.get("full_name") != EXPECTED_REPOSITORY
        or not isinstance(head_repository, dict)
        or head_repository.get("full_name") != EXPECTED_REPOSITORY
    ):
        raise V1CandidateError("freeze_run_binding_invalid")
    created_at = _timestamp_value(run.get("created_at"), "freeze_run_created_at")
    started_at = _timestamp_value(run.get("run_started_at"), "freeze_run_started_at")
    updated_at = _timestamp_value(run.get("updated_at"), "freeze_run_updated_at")
    frozen_at = _timestamp_value(candidate["frozen_at"], "candidate_frozen_at")
    if not created_at <= started_at <= frozen_at <= updated_at:
        raise V1CandidateError("freeze_run_chronology_invalid")
    return run_url


def _validate_asset_chronology(
    release: dict[str, object],
    run: dict[str, object],
    manifest: dict[str, object],
) -> None:
    candidate = manifest["candidate"]
    assert isinstance(candidate, dict)
    frozen_at = _timestamp_value(candidate["frozen_at"], "candidate_frozen_at")
    run_updated_at = _timestamp_value(run.get("updated_at"), "freeze_run_updated_at")
    release_created_at = _timestamp_value(
        release.get("created_at"), "candidate_release_created_at"
    )
    release_published_at = _timestamp_value(
        release.get("published_at"), "candidate_release_published_at"
    )
    # GitHub's release API defines created_at as the date of the commit used for
    # the release, not the time at which the release record was created. Keep it
    # as validated source metadata, but use assets and published_at for custody
    # chronology.
    if not release_created_at <= release_published_at <= run_updated_at:
        raise V1CandidateError("candidate_release_chronology_invalid")
    assets = release.get("assets")
    assert isinstance(assets, list)
    for asset in assets:
        assert isinstance(asset, dict)
        created_at = _timestamp_value(asset.get("created_at"), "release_asset_created_at")
        updated_at = _timestamp_value(asset.get("updated_at"), "release_asset_updated_at")
        if not frozen_at <= created_at <= updated_at <= release_published_at:
            raise V1CandidateError("release_asset_chronology_invalid")


def _validate_custody_tag(ref: object, manifest: dict[str, object]) -> None:
    if not isinstance(ref, dict):
        raise V1CandidateError("custody_tag_invalid")
    candidate = manifest["candidate"]
    custody = manifest["custody"]
    assert isinstance(candidate, dict) and isinstance(custody, dict)
    tag = custody["release_tag"]
    target = ref.get("object")
    if (
        ref.get("ref") != f"refs/tags/{tag}"
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != candidate["source_commit"]
    ):
        raise V1CandidateError("custody_tag_binding_invalid")


def _validate_gate_evidence(
    evidence_path: Path, manifest: dict[str, object]
) -> tuple[dict[str, object], str, str]:
    try:
        try:
            from tools import verify_v1_internal_repeatability_evidence as repeatability
        except ImportError:
            import verify_v1_internal_repeatability_evidence as repeatability  # type: ignore[no-redef]

        evidence_payload = _safe_read(
            evidence_path,
            maximum=1024 * 1024,
            label="gate_evidence",
        )
        evidence = json.loads(
            evidence_payload.decode("utf-8"),
            object_pairs_hook=repeatability._strict_object,
            parse_constant=_reject_json_constant,
        )
        result = repeatability.validate_evidence(evidence)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise V1CandidateError("gate_evidence_invalid") from error
    if not isinstance(evidence, dict):
        raise V1CandidateError("gate_evidence_invalid")
    candidate = manifest["candidate"]
    evidence_candidate = evidence.get("candidate")
    assert isinstance(candidate, dict)
    if not isinstance(evidence_candidate, dict):
        raise V1CandidateError("gate_candidate_invalid")
    if result.get("evidence_kind") != "candidate_gate_evidence":
        raise V1CandidateError("gate_evidence_not_real")
    if (
        result.get("status") != "eligible_for_unchanged_promotion"
        or result.get("eligible_for_v1_0_0_promotion") is not True
        or result.get("promotion_requires_exact_candidate_digest") is not True
    ):
        raise V1CandidateError("gate_not_eligible")
    expected_candidate = {
        "target_version": candidate["target_version"],
        "artifact_kind": candidate["artifact_kind"],
        "artifact_digest": candidate["artifact_digest"],
        "source_commit": candidate["source_commit"],
        "frozen_at": candidate["frozen_at"],
    }
    if any(evidence_candidate.get(key) != value for key, value in expected_candidate.items()):
        raise V1CandidateError("gate_candidate_mismatch")
    if result.get("candidate_artifact_digest") != candidate["artifact_digest"]:
        raise V1CandidateError("gate_candidate_mismatch")
    generated_at = evidence.get("generated_at")
    _require_timestamp(generated_at, "gate_generated_at")
    assert isinstance(generated_at, str)
    return result, _sha256(evidence_payload), generated_at


def validate_promotion(
    manifest_path: Path,
    archive_path: Path,
    evidence_path: Path,
    release_path: Path,
    immutable_settings_path: Path,
    freeze_run_path: Path,
    custody_tag_path: Path,
) -> dict[str, object]:
    manifest_payload = _safe_read(manifest_path, maximum=MAX_JSON_BYTES, label="manifest")
    custody_proof, manifest, release = _validate_custody_snapshot(
        manifest_payload,
        archive_path,
        release_path,
        immutable_settings_path,
    )
    freeze_run = _read_json(freeze_run_path, label="freeze_run_api")
    freeze_run_url = _validate_freeze_run(freeze_run, manifest)
    assert isinstance(freeze_run, dict)
    _validate_asset_chronology(release, freeze_run, manifest)
    _validate_custody_tag(
        _read_json(custody_tag_path, label="custody_tag_api"),
        manifest,
    )
    gate_result, evidence_digest, gate_generated_at = _validate_gate_evidence(
        evidence_path, manifest
    )
    candidate = manifest["candidate"]
    custody = manifest["custody"]
    build = manifest["build"]
    assert isinstance(candidate, dict) and isinstance(custody, dict) and isinstance(build, dict)
    return {
        "schema_id": PROMOTION_SCHEMA_ID,
        "schema_version": 1,
        "status": "eligible_for_metadata_promotion",
        "repository": EXPECTED_REPOSITORY,
        "release_tag": custody_proof["release_tag"],
        "custody_release_tag": custody["release_tag"],
        "target_version": TARGET_VERSION,
        "source_commit": candidate["source_commit"],
        "candidate_artifact_digest": candidate["artifact_digest"],
        "gate_evidence_digest": evidence_digest,
        "gate_generated_at": gate_generated_at,
        "gate_issue": gate_result["gate_issue"],
        "gate_status": gate_result["status"],
        "claim_scope": gate_result["claim_scope"],
        "freeze_workflow_run_id": build["run_id"],
        "freeze_workflow_run_attempt": build["run_attempt"],
        "freeze_workflow_run_url": freeze_run_url,
        "archive_reverified": True,
        "release_asset_digests_verified": True,
        "immutable_releases_enabled": True,
        "release_immutable": True,
        "final_tag_creation_permitted": True,
        "asset_overwrite_permitted": False,
        "promotion_rebuild_permitted": False,
    }


def _final_release_notes(
    manifest: dict[str, object], gate_evidence_digest: str
) -> str:
    if _DIGEST_RE.fullmatch(gate_evidence_digest) is None:
        raise V1CandidateError("gate_evidence_digest_invalid")
    candidate = manifest["candidate"]
    custody = manifest["custody"]
    assert isinstance(candidate, dict) and isinstance(custody, dict)
    candidate_tag = str(custody["release_tag"])
    archive_url = (
        f"https://github.com/{EXPECTED_REPOSITORY}/releases/download/"
        f"{candidate_tag}/{ARCHIVE_NAME}"
    )
    manifest_url = (
        f"https://github.com/{EXPECTED_REPOSITORY}/releases/download/"
        f"{candidate_tag}/{MANIFEST_NAME}"
    )
    return (
        "Hormuz v1.0.0 promotes the immutable, gate-tested source archive "
        "without rebuilding or copying it.\n\n"
        f"Canonical source archive: [{ARCHIVE_NAME}]({archive_url})\n\n"
        f"Candidate manifest: [{MANIFEST_NAME}]({manifest_url})\n\n"
        f"- Candidate tag: `{candidate_tag}`\n"
        f"- Source archive: `{candidate['artifact_digest']}`\n"
        f"- Gate evidence: `{gate_evidence_digest}`\n"
        f"- Source commit: `{candidate['source_commit']}`\n\n"
        "The final GitHub Release intentionally carries no copied assets; the "
        "digest-addressed candidate release remains the canonical immutable custody object."
    )


def create_final_release_notes(
    manifest_path: Path, evidence_path: Path
) -> tuple[bytes, dict[str, object]]:
    manifest = validate_manifest(_read_json(manifest_path, label="manifest"))
    _gate_result, evidence_digest, _generated_at = _validate_gate_evidence(
        evidence_path, manifest
    )
    payload = _final_release_notes(manifest, evidence_digest).encode("utf-8")
    return payload, {
        "schema_id": "hormuz.v1-final-release-notes",
        "schema_version": 1,
        "digest": _sha256(payload),
        "size_bytes": len(payload),
        "gate_evidence_digest": evidence_digest,
    }


def validate_final_release(
    manifest_path: Path,
    evidence_path: Path,
    release_path: Path,
    immutable_settings_path: Path,
) -> dict[str, object]:
    manifest = validate_manifest(_read_json(manifest_path, label="manifest"))
    gate_result, evidence_digest, gate_generated_at = _validate_gate_evidence(
        evidence_path, manifest
    )
    immutable_settings = _read_json(immutable_settings_path, label="immutable_api")
    if immutable_settings.get("enabled") is not True:
        raise V1CandidateError("immutable_releases_not_enabled")
    release = _read_json(release_path, label="final_release_api")
    candidate = manifest["candidate"]
    custody = manifest["custody"]
    assert isinstance(candidate, dict) and isinstance(custody, dict)
    if (
        release.get("tag_name") != FINAL_TAG
        or release.get("name") != FINAL_RELEASE_TITLE
        or release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("immutable") is not True
        or release.get("assets") != []
        or release.get("body") != _final_release_notes(manifest, evidence_digest)
    ):
        raise V1CandidateError("final_release_contract_invalid")
    created_at = _timestamp_value(release.get("created_at"), "final_release_created_at")
    published_at = _timestamp_value(
        release.get("published_at"), "final_release_published_at"
    )
    gate_time = _timestamp_value(gate_generated_at, "gate_generated_at")
    if not gate_time <= created_at <= published_at:
        raise V1CandidateError("final_release_chronology_invalid")
    return {
        "schema_id": FINAL_RELEASE_PROOF_SCHEMA_ID,
        "schema_version": 1,
        "status": "published_metadata_for_exact_candidate",
        "repository": EXPECTED_REPOSITORY,
        "release_tag": FINAL_TAG,
        "custody_release_tag": custody["release_tag"],
        "source_commit": candidate["source_commit"],
        "candidate_artifact_digest": candidate["artifact_digest"],
        "gate_evidence_digest": evidence_digest,
        "gate_status": gate_result["status"],
        "release_immutable": True,
        "authoritative_binding": "protected_annotated_tag",
        "release_assets_copied": False,
        "promotion_rebuild_permitted": False,
    }


def _expected_final_tag_annotation_lines(
    candidate_digest: str,
    gate_evidence_digest: str,
    candidate_tag: str,
) -> list[str]:
    candidate_match = _DIGEST_RE.fullmatch(candidate_digest)
    gate_match = _DIGEST_RE.fullmatch(gate_evidence_digest)
    custody_match = _CUSTODY_TAG_RE.fullmatch(candidate_tag)
    if (
        candidate_match is None
        or gate_match is None
        or custody_match is None
        or custody_match.group(1) != candidate_digest.removeprefix("sha256:")
    ):
        raise V1CandidateError("final_tag_annotation_input_invalid")
    return [
        "Hormuz v1.0.0",
        "",
        f"Frozen source archive: {candidate_digest}",
        "",
        f"Gate evidence: {gate_evidence_digest}",
        "",
        f"Candidate custody tag: {candidate_tag}",
    ]


def _validate_final_tag_annotation_text(
    message: str,
    *,
    candidate_digest: str,
    gate_evidence_digest: str,
    candidate_tag: str,
) -> None:
    expected = _expected_final_tag_annotation_lines(
        candidate_digest,
        gate_evidence_digest,
        candidate_tag,
    )
    if "\r" in message or message.splitlines() != expected:
        raise V1CandidateError("final_tag_annotation_invalid")


def validate_final_tag_annotation(
    message_path: Path,
    *,
    candidate_digest: str,
    gate_evidence_digest: str,
    candidate_tag: str,
) -> dict[str, object]:
    payload = _safe_read(
        message_path,
        maximum=16 * 1024,
        label="final_tag_annotation",
    )
    return _validate_final_tag_annotation_payload(
        payload,
        candidate_digest=candidate_digest,
        gate_evidence_digest=gate_evidence_digest,
        candidate_tag=candidate_tag,
    )


def _validate_final_tag_annotation_payload(
    payload: bytes,
    *,
    candidate_digest: str,
    gate_evidence_digest: str,
    candidate_tag: str,
) -> dict[str, object]:
    try:
        message = payload.decode("utf-8")
    except UnicodeError as error:
        raise V1CandidateError("final_tag_annotation_invalid") from error
    _validate_final_tag_annotation_text(
        message,
        candidate_digest=candidate_digest,
        gate_evidence_digest=gate_evidence_digest,
        candidate_tag=candidate_tag,
    )
    return {
        "schema_id": FINAL_TAG_PROOF_SCHEMA_ID,
        "schema_version": 1,
        "status": "exact_annotation_valid",
        "release_tag": FINAL_TAG,
        "custody_release_tag": candidate_tag,
        "candidate_artifact_digest": candidate_digest,
        "gate_evidence_digest": gate_evidence_digest,
    }


def validate_local_final_tag_object(
    tag_object_path: Path,
    *,
    source_commit: str,
    gate_generated_at: str,
    candidate_digest: str,
    gate_evidence_digest: str,
    candidate_tag: str,
) -> dict[str, object]:
    if _REVISION_RE.fullmatch(source_commit) is None:
        raise V1CandidateError("local_final_tag_source_commit_invalid")
    payload = _safe_read(
        tag_object_path,
        maximum=16 * 1024,
        label="local_final_tag_object",
    )
    header_payload, separator, message_payload = payload.partition(b"\n\n")
    if not separator or not message_payload:
        raise V1CandidateError("local_final_tag_object_invalid")
    try:
        header_lines = header_payload.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise V1CandidateError("local_final_tag_object_invalid") from error
    if (
        len(header_lines) != 4
        or header_lines[0] != f"object {source_commit}"
        or header_lines[1] != "type commit"
        or header_lines[2] != f"tag {FINAL_TAG}"
    ):
        raise V1CandidateError("local_final_tag_object_invalid")
    tagger_match = _TAGGER_HEADER_RE.fullmatch(header_lines[3])
    if tagger_match is None:
        raise V1CandidateError("local_final_tag_object_invalid")
    timezone_hour = int(tagger_match.group(3))
    timezone_minute = int(tagger_match.group(4))
    if timezone_hour > 23 or timezone_minute > 59:
        raise V1CandidateError("local_final_tag_object_invalid")
    try:
        tagged_at = datetime.fromtimestamp(int(tagger_match.group(1)), tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise V1CandidateError("local_final_tag_object_invalid") from error
    gate_time = _timestamp_value(gate_generated_at, "gate_generated_at")
    if tagged_at < gate_time:
        raise V1CandidateError("local_final_tag_chronology_invalid")
    annotation = _validate_final_tag_annotation_payload(
        message_payload,
        candidate_digest=candidate_digest,
        gate_evidence_digest=gate_evidence_digest,
        candidate_tag=candidate_tag,
    )
    return {
        **annotation,
        "status": "exact_local_tag_object_valid",
        "source_commit": source_commit,
        "tagged_at": tagged_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "direct_target_type": "commit",
    }


def validate_final_tag_object(
    tag_object_path: Path,
    *,
    source_commit: str,
    gate_generated_at: str,
    candidate_digest: str,
    gate_evidence_digest: str,
    candidate_tag: str,
) -> dict[str, object]:
    if _REVISION_RE.fullmatch(source_commit) is None:
        raise V1CandidateError("final_tag_source_commit_invalid")
    value = _read_json(tag_object_path, label="final_tag_object")
    target = value.get("object")
    tagger = value.get("tagger")
    message = value.get("message")
    if (
        value.get("tag") != FINAL_TAG
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != source_commit
        or not isinstance(tagger, dict)
        or not isinstance(message, str)
    ):
        raise V1CandidateError("final_tag_object_invalid")
    tagged_at = _timestamp_value(tagger.get("date"), "final_tag_created_at")
    gate_time = _timestamp_value(gate_generated_at, "gate_generated_at")
    if tagged_at < gate_time:
        raise V1CandidateError("final_tag_chronology_invalid")
    _validate_final_tag_annotation_text(
        message,
        candidate_digest=candidate_digest,
        gate_evidence_digest=gate_evidence_digest,
        candidate_tag=candidate_tag,
    )
    return {
        "schema_id": FINAL_TAG_PROOF_SCHEMA_ID,
        "schema_version": 1,
        "status": "exact_protected_tag_valid",
        "release_tag": FINAL_TAG,
        "custody_release_tag": candidate_tag,
        "source_commit": source_commit,
        "candidate_artifact_digest": candidate_digest,
        "gate_evidence_digest": gate_evidence_digest,
        "tagged_at": tagger["date"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    manifest = commands.add_parser("manifest", help="seal one already-built source archive")
    manifest.add_argument("--archive", required=True, type=Path)
    manifest.add_argument("--source-commit", required=True)
    manifest.add_argument("--frozen-at", required=True)
    manifest.add_argument("--invocation-url", required=True)
    manifest.add_argument("--run-id", required=True, type=int)
    manifest.add_argument("--run-attempt", required=True, type=int)
    manifest.add_argument("--output", required=True, type=Path)

    custody = commands.add_parser("custody", help="verify immutable candidate custody")
    custody.add_argument("--manifest", required=True, type=Path)
    custody.add_argument("--archive", required=True, type=Path)
    custody.add_argument("--release-api", required=True, type=Path)
    custody.add_argument("--immutable-api", required=True, type=Path)
    custody.add_argument("--output", required=True, type=Path)

    evidence_snapshot = commands.add_parser(
        "evidence-snapshot",
        help="pin one owner-only gate-evidence snapshot before promotion",
    )
    evidence_snapshot.add_argument("--evidence", required=True, type=Path)
    evidence_snapshot.add_argument("--output", required=True, type=Path)

    final_notes = commands.add_parser(
        "final-notes", help="create deterministic metadata-only v1 release notes"
    )
    final_notes.add_argument("--manifest", required=True, type=Path)
    final_notes.add_argument("--evidence", required=True, type=Path)
    final_notes.add_argument("--output", required=True, type=Path)

    final_release = commands.add_parser(
        "final-release", help="verify the immutable metadata-only v1 release"
    )
    final_release.add_argument("--manifest", required=True, type=Path)
    final_release.add_argument("--evidence", required=True, type=Path)
    final_release.add_argument("--release-api", required=True, type=Path)
    final_release.add_argument("--immutable-api", required=True, type=Path)
    final_release.add_argument("--output", required=True, type=Path)

    tag_annotation = commands.add_parser(
        "tag-annotation", help="verify the exact protected final-tag annotation"
    )
    tag_annotation.add_argument("--message", required=True, type=Path)
    tag_annotation.add_argument("--candidate-digest", required=True)
    tag_annotation.add_argument("--gate-evidence-digest", required=True)
    tag_annotation.add_argument("--candidate-tag", required=True)
    tag_annotation.add_argument("--output", required=True, type=Path)

    local_tag = commands.add_parser(
        "local-tag",
        help="verify an exact raw local final-tag object before push",
    )
    local_tag.add_argument("--tag-object", required=True, type=Path)
    local_tag.add_argument("--source-commit", required=True)
    local_tag.add_argument("--gate-generated-at", required=True)
    local_tag.add_argument("--candidate-digest", required=True)
    local_tag.add_argument("--gate-evidence-digest", required=True)
    local_tag.add_argument("--candidate-tag", required=True)
    local_tag.add_argument("--output", required=True, type=Path)

    freeze_authorization = commands.add_parser(
        "freeze-authorization",
        help="prove only the current exact-commit freeze run was authorized",
    )
    freeze_authorization.add_argument("--runs-api", required=True, type=Path)
    freeze_authorization.add_argument("--jobs-directory", required=True, type=Path)
    freeze_authorization.add_argument("--source-commit", required=True)
    freeze_authorization.add_argument("--current-run-id", required=True, type=int)
    freeze_authorization.add_argument("--output", required=True, type=Path)

    final_tag = commands.add_parser(
        "final-tag", help="verify the exact protected final-tag object"
    )
    final_tag.add_argument("--tag-object", required=True, type=Path)
    final_tag.add_argument("--source-commit", required=True)
    final_tag.add_argument("--gate-generated-at", required=True)
    final_tag.add_argument("--candidate-digest", required=True)
    final_tag.add_argument("--gate-evidence-digest", required=True)
    final_tag.add_argument("--candidate-tag", required=True)
    final_tag.add_argument("--output", required=True, type=Path)

    promotion = commands.add_parser("promotion", help="bind real gate evidence to exact bytes")
    promotion.add_argument("--manifest", required=True, type=Path)
    promotion.add_argument("--archive", required=True, type=Path)
    promotion.add_argument("--evidence", required=True, type=Path)
    promotion.add_argument("--release-api", required=True, type=Path)
    promotion.add_argument("--immutable-api", required=True, type=Path)
    promotion.add_argument("--freeze-run-api", required=True, type=Path)
    promotion.add_argument("--custody-tag-api", required=True, type=Path)
    promotion.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_is_json = True
    try:
        if args.command == "manifest":
            if args.output.name != MANIFEST_NAME:
                raise V1CandidateError("manifest_output_name_invalid")
            result = create_manifest(
                args.archive,
                source_commit=args.source_commit,
                frozen_at=args.frozen_at,
                invocation_url=args.invocation_url,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
            )
        elif args.command == "custody":
            result = validate_custody(
                args.manifest,
                args.archive,
                args.release_api,
                args.immutable_api,
            )
        elif args.command == "evidence-snapshot":
            evidence_payload = _safe_read(
                args.evidence,
                maximum=1024 * 1024,
                label="gate_evidence",
            )
            _write_new_private_bytes(args.output, evidence_payload)
            result = {
                "schema_id": EVIDENCE_SNAPSHOT_SCHEMA_ID,
                "schema_version": 1,
                "digest": _sha256(evidence_payload),
                "size_bytes": len(evidence_payload),
            }
            output_is_json = False
        elif args.command == "final-notes":
            notes_payload, result = create_final_release_notes(
                args.manifest, args.evidence
            )
            _write_new_private_bytes(args.output, notes_payload)
            output_is_json = False
        elif args.command == "final-release":
            result = validate_final_release(
                args.manifest,
                args.evidence,
                args.release_api,
                args.immutable_api,
            )
        elif args.command == "tag-annotation":
            result = validate_final_tag_annotation(
                args.message,
                candidate_digest=args.candidate_digest,
                gate_evidence_digest=args.gate_evidence_digest,
                candidate_tag=args.candidate_tag,
            )
        elif args.command == "local-tag":
            result = validate_local_final_tag_object(
                args.tag_object,
                source_commit=args.source_commit,
                gate_generated_at=args.gate_generated_at,
                candidate_digest=args.candidate_digest,
                gate_evidence_digest=args.gate_evidence_digest,
                candidate_tag=args.candidate_tag,
            )
        elif args.command == "freeze-authorization":
            result = validate_freeze_run_authorization(
                args.runs_api,
                args.jobs_directory,
                source_commit=args.source_commit,
                current_run_id=args.current_run_id,
            )
        elif args.command == "final-tag":
            result = validate_final_tag_object(
                args.tag_object,
                source_commit=args.source_commit,
                gate_generated_at=args.gate_generated_at,
                candidate_digest=args.candidate_digest,
                gate_evidence_digest=args.gate_evidence_digest,
                candidate_tag=args.candidate_tag,
            )
        else:
            result = validate_promotion(
                args.manifest,
                args.archive,
                args.evidence,
                args.release_api,
                args.immutable_api,
                args.freeze_run_api,
                args.custody_tag_api,
            )
        if output_is_json:
            _write_new_private_json(args.output, result)
    except V1CandidateError as error:
        print(f"v1 candidate failed: {error}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError, ValueError, RecursionError):
        print("v1 candidate failed: unexpected_input", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
