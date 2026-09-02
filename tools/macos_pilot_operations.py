#!/usr/bin/env python3
"""Prepare and assemble authenticated, content-free Mac pilot operations evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any
import zipfile
import zlib

if __package__:
    from tools import verify_external_pilot_deployment as deployment
    from tools import verify_macos_pilot_evidence as pilot
else:
    # Isolated mode deliberately omits both the checkout and script directory
    # from sys.path. Add only this resolved, reviewed tools directory so the
    # workflow can keep `python3 -I` without admitting cwd or user-site imports.
    tools_directory = str(Path(__file__).resolve().parent)
    if tools_directory not in sys.path:
        sys.path.insert(0, tools_directory)
    import verify_external_pilot_deployment as deployment  # type: ignore[no-redef]
    import verify_macos_pilot_evidence as pilot  # type: ignore[no-redef]


INPUT_SCHEMA_ID = "hormuz.macos-pilot-operations-inputs"
OPERATIONS_SCHEMA_ID = "hormuz.macos-pilot-operations-evidence"
SCHEMA_VERSION = 1
MAX_INPUT_BYTES = 1024 * 1024
_DISTRIBUTION_ARTIFACT_RE = re.compile(
    r"hormuz-macos-([0-9]+\.[0-9]+\.[0-9]+)-([1-9][0-9]{0,14})-([1-9][0-9]{0,2})\Z"
)
_INPUT_FIELDS = {
    "schema_id",
    "schema_version",
    "source_commit",
    "candidate",
    "previous",
    "gateway",
}
_DISTRIBUTION_INPUT_FIELDS = {
    "source_commit",
    "workflow_run_url",
    "archive_name",
    "archive_bytes",
    "archive_sha256",
    "version",
    "build",
}
_GATEWAY_INPUT_FIELDS = {
    "source_commit",
    "deployment_evidence_url",
    "origin",
    "service_id",
}


class MacPilotOperationsError(ValueError):
    """A fixed, content-free operations preparation or assembly failure."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path, label: str) -> object:
    try:
        payload = pilot._read_bounded_regular(path, MAX_INPUT_BYTES, label)
        return pilot._parse_json(payload, label)
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(str(error)) from error


def _write_exclusive(path: Path, value: object) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(payload) > MAX_INPUT_BYTES:
        raise MacPilotOperationsError("output_too_large")
    try:
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir() or parent.is_symlink() or path.exists() or path.is_symlink():
            raise MacPilotOperationsError("output_path_unsafe")
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
    except MacPilotOperationsError:
        raise
    except OSError as error:
        raise MacPilotOperationsError("output_path_unsafe") from error


def _run(url: str, label: str) -> dict[str, Any]:
    if pilot._ACTIONS_RUN_RE.fullmatch(url) is None:
        raise MacPilotOperationsError(f"{label}_run_not_trusted")
    run_id = int(url.rsplit("/", 1)[-1])
    try:
        value = pilot._github_api_json(
            f"repos/Xpounder-com/hormuz/actions/runs/{run_id}",
            f"{label}_run",
        )
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(f"{label}_run_unavailable") from error
    repository = value.get("repository") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or isinstance(value.get("id"), bool)
        or value.get("id") != run_id
        or value.get("html_url") != url
        or pilot._REVISION_RE.fullmatch(str(value.get("head_sha", ""))) is None
        or value.get("head_branch") != "main"
        or value.get("event") != "workflow_dispatch"
        or value.get("status") != "completed"
        or value.get("conclusion") != "success"
        or not isinstance(repository, dict)
        or repository.get("full_name") != "Xpounder-com/hormuz"
    ):
        raise MacPilotOperationsError(f"{label}_run_not_trusted")
    return value


def _run_timeline(run: dict[str, Any], label: str) -> tuple[datetime, datetime]:
    try:
        return pilot._validate_github_run_timeline(
            run,
            _now() + timedelta(minutes=5),
            label,
        )
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(str(error)) from error


def _artifacts(run: dict[str, Any], label: str) -> list[dict[str, Any]]:
    run_id, _, _ = pilot._require_github_run_identity(run, label)
    try:
        response = pilot._github_api_json(
            f"repos/Xpounder-com/hormuz/actions/runs/{run_id}/artifacts?per_page=100",
            f"{label}_artifacts",
        )
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(f"{label}_artifacts_unavailable") from error
    if not isinstance(response, dict):
        raise MacPilotOperationsError(f"{label}_artifacts_invalid")
    total = response.get("total_count")
    records = response.get("artifacts")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or not 0 <= total <= 100
        or not isinstance(records, list)
        or len(records) != total
        or not all(isinstance(item, dict) for item in records)
    ):
        raise MacPilotOperationsError(f"{label}_artifacts_invalid")
    return records


def _trusted_artifact(
    artifact: dict[str, Any],
    run: dict[str, Any],
    label: str,
    maximum: int,
) -> tuple[int, int, datetime]:
    run_id, _, _ = pilot._require_github_run_identity(run, label)
    artifact_id = artifact.get("id")
    size = artifact.get("size_in_bytes")
    workflow_run = artifact.get("workflow_run")
    try:
        created_at = pilot._require_timestamp(
            artifact.get("created_at"), f"{label}_artifact_created_at"
        )
        started_at, completed_at = _run_timeline(run, label)
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(str(error)) from error
    if (
        isinstance(artifact_id, bool)
        or not isinstance(artifact_id, int)
        or artifact_id < 1
        or isinstance(size, bool)
        or not isinstance(size, int)
        or not 1 <= size <= maximum
        or artifact.get("expired") is not False
        or artifact.get("url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}"
        or artifact.get("archive_download_url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}/zip"
        or not isinstance(workflow_run, dict)
        or workflow_run.get("id") != run_id
        or workflow_run.get("head_branch") != "main"
        or workflow_run.get("head_sha") != run.get("head_sha")
        or not started_at <= created_at <= completed_at
    ):
        raise MacPilotOperationsError(f"{label}_artifact_not_trusted")
    return artifact_id, size, created_at


def _zip_member(package: zipfile.ZipFile, name: str, maximum: int, label: str) -> bytes:
    matches = [member for member in package.infolist() if member.filename == name]
    if len(matches) != 1:
        raise MacPilotOperationsError(f"{label}_member_not_unique")
    member = matches[0]
    file_type = (member.external_attr >> 16) & 0o170000
    if (
        member.is_dir()
        or "/" in member.filename
        or "\\" in member.filename
        or file_type not in {0, stat.S_IFREG}
        or member.flag_bits & 0x1
        or not 1 <= member.file_size <= maximum
    ):
        raise MacPilotOperationsError(f"{label}_member_unsafe")
    with package.open(member) as source:
        payload = source.read(maximum + 1)
    if len(payload) != member.file_size or len(payload) > maximum:
        raise MacPilotOperationsError(f"{label}_member_changed")
    return payload


def _distribution(run: dict[str, Any], label: str) -> tuple[dict[str, Any], datetime]:
    if run.get("path") != pilot.MACOS_DISTRIBUTION_WORKFLOW:
        raise MacPilotOperationsError(f"{label}_workflow_invalid")
    _, run_number, run_attempt = pilot._require_github_run_identity(run, label)
    matches: list[tuple[dict[str, Any], re.Match[str]]] = []
    for artifact in _artifacts(run, label):
        match = _DISTRIBUTION_ARTIFACT_RE.fullmatch(str(artifact.get("name", "")))
        if (
            match is not None
            and int(match.group(2)) == run_number
            and int(match.group(3)) == run_attempt
        ):
            matches.append((artifact, match))
    if len(matches) != 1:
        raise MacPilotOperationsError(f"{label}_distribution_artifact_not_unique")
    artifact, name_match = matches[0]
    artifact_id, artifact_size, artifact_created_at = _trusted_artifact(
        artifact, run, label, pilot._MAX_ACTIONS_ARTIFACT_BYTES
    )
    with tempfile.TemporaryDirectory(prefix="hormuz-macos-operations-") as temporary:
        archive = Path(temporary) / "artifact.zip"
        try:
            pilot._download_github_artifact(
                artifact_id,
                archive,
                artifact_size,
                pilot._MAX_ACTIONS_ARTIFACT_BYTES,
                120,
                label,
            )
            with zipfile.ZipFile(archive) as package:
                proof_payload = _zip_member(
                    package, "distribution-proof.json", MAX_INPUT_BYTES, f"{label}_proof"
                )
                notarization_payload = _zip_member(
                    package, "notarization.json", MAX_INPUT_BYTES, f"{label}_notarization"
                )
                proof = pilot._validate_distribution_proof(
                    pilot._parse_json(proof_payload, f"{label}_proof"),
                    "pilot_qualification",
                )
                notarization = pilot._validate_notarization(
                    pilot._parse_json(notarization_payload, f"{label}_notarization")
                )
                archive_name = f"Hormuz-{proof['version']}-notarized.zip"
                archive_member = [
                    member for member in package.infolist() if member.filename == archive_name
                ]
                if len(archive_member) != 1:
                    raise MacPilotOperationsError(f"{label}_archive_not_unique")
                member = archive_member[0]
                if not 1 <= member.file_size <= pilot._MAX_ARCHIVE_BYTES:
                    raise MacPilotOperationsError(f"{label}_archive_size_invalid")
                digest = hashlib.sha256()
                observed = 0
                with package.open(member) as source:
                    while chunk := source.read(1024 * 1024):
                        observed += len(chunk)
                        if observed > pilot._MAX_ARCHIVE_BYTES:
                            raise MacPilotOperationsError(f"{label}_archive_size_invalid")
                        digest.update(chunk)
                archive_sha256 = digest.hexdigest()
            pilot._verify_distribution_artifact_zip(
                archive,
                proof,
                proof_payload,
                notarization_payload,
                observed,
                archive_sha256,
                label,
            )
        except MacPilotOperationsError:
            raise
        except pilot.MacPilotEvidenceError as error:
            raise MacPilotOperationsError(str(error)) from error
        except (
            OSError,
            RuntimeError,
            EOFError,
            KeyError,
            ValueError,
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
        ) as error:
            raise MacPilotOperationsError(f"{label}_artifact_zip_invalid") from error
    expected = {
        "source_commit": run["head_sha"],
        "workflow_run_url": run["html_url"],
        "version": name_match.group(1),
        "build": str(run_number * 1000 + run_attempt),
        "archive_bytes": observed,
        "archive_sha256": archive_sha256,
        "submission_id": notarization["submission_id"],
    }
    if any(proof.get(field) != value for field, value in expected.items()):
        raise MacPilotOperationsError(f"{label}_proof_binding_invalid")
    return {
        "source_commit": proof["source_commit"],
        "workflow_run_url": proof["workflow_run_url"],
        "archive_name": f"Hormuz-{proof['version']}-notarized.zip",
        "archive_bytes": proof["archive_bytes"],
        "archive_sha256": proof["archive_sha256"],
        "version": proof["version"],
        "build": proof["build"],
    }, artifact_created_at


def _gateway(run: dict[str, Any]) -> dict[str, str]:
    label = "gateway_deployment"
    if run.get("path") != pilot.EXTERNAL_PILOT_WORKFLOW:
        raise MacPilotOperationsError("gateway_deployment_workflow_invalid")
    _, run_number, run_attempt = pilot._require_github_run_identity(run, label)
    try:
        value, _ = pilot._authenticate_run_json_artifact(
            run,
            run["head_sha"],
            f"hormuz-external-pilot-deployment-{run_number}-{run_attempt}",
            "external-pilot-deployment-evidence.json",
            label,
        )
        evidence = pilot._require_fields(
            value,
            pilot._GATEWAY_DEPLOYMENT_EVIDENCE_FIELDS,
            "gateway_deployment_evidence",
        )
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(str(error)) from error
    expected = {
        "schema_id": deployment.SCHEMA_ID,
        "schema_version": deployment.SCHEMA_VERSION,
        "evidence_kind": "live_external_pilot",
        **deployment.EXPECTED_CONTRACT,
        "source_commit": run["head_sha"],
        "workflow_run_url": run["html_url"],
        "support_path_published": True,
    }
    if (
        any(evidence.get(field) != value for field, value in expected.items())
        or pilot._RENDER_SERVICE_ID_RE.fullmatch(
            str(evidence.get("render_service_id", ""))
        )
        is None
    ):
        raise MacPilotOperationsError("gateway_deployment_evidence_invalid")
    try:
        deployment._origin(str(evidence.get("gateway_origin", "")))
    except deployment.DeploymentEvidenceError as error:
        raise MacPilotOperationsError("gateway_deployment_evidence_invalid") from error
    return {
        "source_commit": run["head_sha"],
        "deployment_evidence_url": run["html_url"],
        "origin": str(evidence["gateway_origin"]),
        "service_id": str(evidence["render_service_id"]),
    }


def prepare(
    *,
    candidate_url: str,
    previous_url: str,
    gateway_url: str,
    expected_source_commit: str,
) -> dict[str, Any]:
    if pilot._REVISION_RE.fullmatch(expected_source_commit) is None:
        raise MacPilotOperationsError("expected_source_commit_invalid")
    candidate_run = _run(candidate_url, "candidate")
    previous_run = _run(previous_url, "previous")
    gateway_run = _run(gateway_url, "gateway_deployment")
    if candidate_run["head_sha"] != expected_source_commit:
        raise MacPilotOperationsError("candidate_source_commit_invalid")
    candidate, candidate_created_at = _distribution(candidate_run, "candidate")
    previous, previous_created_at = _distribution(previous_run, "previous")
    _, candidate_number, candidate_attempt = pilot._require_github_run_identity(
        candidate_run, "candidate"
    )
    _, previous_number, previous_attempt = pilot._require_github_run_identity(
        previous_run, "previous"
    )
    if (
        candidate_attempt != 1
        or previous_attempt != 1
        or candidate_number != previous_number + 1
        or candidate_created_at <= previous_created_at
        or int(candidate["build"]) <= int(previous["build"])
        or candidate["version"] == previous["version"]
    ):
        raise MacPilotOperationsError("distribution_history_not_immediate")
    gateway = _gateway(gateway_run)
    if gateway["source_commit"] != expected_source_commit:
        raise MacPilotOperationsError("gateway_source_commit_invalid")
    _, gateway_completed_at = _run_timeline(gateway_run, "gateway_deployment")
    if gateway_completed_at > _now() + timedelta(minutes=5):
        raise MacPilotOperationsError("gateway_deployment_chronology_invalid")
    return {
        "schema_id": INPUT_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "source_commit": expected_source_commit,
        "candidate": candidate,
        "previous": previous,
        "gateway": gateway,
    }


def _validate_inputs(value: object, source_commit: str) -> dict[str, Any]:
    try:
        root = pilot._require_fields(value, _INPUT_FIELDS, "operations_inputs")
        pilot._require_int(root["schema_version"], 1, 1, "operations_inputs_schema_version")
        candidate = pilot._require_fields(
            root["candidate"], _DISTRIBUTION_INPUT_FIELDS, "candidate_input"
        )
        previous = pilot._require_fields(
            root["previous"], _DISTRIBUTION_INPUT_FIELDS, "previous_input"
        )
        gateway = pilot._require_fields(
            root["gateway"], _GATEWAY_INPUT_FIELDS, "gateway_input"
        )
        for label, item in (("candidate", candidate), ("previous", previous)):
            pilot._require_pattern(item["source_commit"], pilot._REVISION_RE, f"{label}_source")
            pilot._require_pattern(item["workflow_run_url"], pilot._ACTIONS_RUN_RE, f"{label}_run")
            pilot._require_pattern(item["version"], pilot._VERSION_RE, f"{label}_version")
            pilot._require_pattern(item["build"], pilot._BUILD_RE, f"{label}_build")
            pilot._require_pattern(item["archive_sha256"], pilot._SHA256_RE, f"{label}_archive")
            pilot._require_int(item["archive_bytes"], 1, pilot._MAX_ARCHIVE_BYTES, f"{label}_bytes")
            if item["archive_name"] != f"Hormuz-{item['version']}-notarized.zip":
                raise MacPilotOperationsError(f"{label}_archive_name_invalid")
        pilot._require_pattern(gateway["source_commit"], pilot._REVISION_RE, "gateway_source")
        pilot._require_pattern(
            gateway["deployment_evidence_url"], pilot._ACTIONS_RUN_RE, "gateway_run"
        )
        if not isinstance(gateway["origin"], str):
            raise MacPilotOperationsError("gateway_origin_invalid")
        deployment._origin(gateway["origin"])
        pilot._require_pattern(
            gateway["service_id"], pilot._RENDER_SERVICE_ID_RE, "gateway_service_id"
        )
    except (pilot.MacPilotEvidenceError, deployment.DeploymentEvidenceError) as error:
        raise MacPilotOperationsError(str(error)) from error
    if (
        root["schema_id"] != INPUT_SCHEMA_ID
        or root["source_commit"] != source_commit
        or candidate["source_commit"] != source_commit
        or gateway["source_commit"] != source_commit
        or int(candidate["build"]) <= int(previous["build"])
        or candidate["version"] == previous["version"]
        or candidate["workflow_run_url"] == previous["workflow_run_url"]
        or candidate["archive_sha256"] == previous["archive_sha256"]
        or gateway["deployment_evidence_url"]
        in {candidate["workflow_run_url"], previous["workflow_run_url"]}
    ):
        raise MacPilotOperationsError("operations_inputs_binding_invalid")
    return root


def assemble(
    *,
    inputs: object,
    arm64_record: object,
    x86_64_record: object,
    lifecycle: object,
    codex_record: object,
    claude_record: object,
    source_commit: str,
    workflow_run_url: str,
) -> dict[str, Any]:
    if (
        pilot._REVISION_RE.fullmatch(source_commit) is None
        or pilot._ACTIONS_RUN_RE.fullmatch(workflow_run_url) is None
    ):
        raise MacPilotOperationsError("operations_identity_invalid")
    root = _validate_inputs(inputs, source_commit)
    candidate = root["candidate"]
    previous = root["previous"]
    gateway = root["gateway"]
    clean = [arm64_record, x86_64_record]
    clients = [codex_record, claude_record]
    reasons: list[str] = []
    try:
        architectures = pilot._validate_clean_machines(
            clean,
            candidate["archive_sha256"],
            None,
            _now() + timedelta(minutes=5),
            reasons,
        )
        pilot._validate_lifecycle(
            lifecycle, previous["build"], candidate["build"], reasons
        )
        pilot._validate_client_recovery(
            clients, candidate["archive_sha256"], reasons
        )
    except pilot.MacPilotEvidenceError as error:
        raise MacPilotOperationsError(str(error)) from error
    if architectures != ["arm64", "x86_64"] or reasons:
        raise MacPilotOperationsError("operations_records_incomplete")
    return {
        "schema_id": OPERATIONS_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "claim_scope": pilot.CLAIM_SCOPE,
        "source_commit": source_commit,
        "workflow_run_url": workflow_run_url,
        "candidate_archive_sha256": candidate["archive_sha256"],
        "candidate_distribution_run_url": candidate["workflow_run_url"],
        "previous_source_commit": previous["source_commit"],
        "previous_archive_sha256": previous["archive_sha256"],
        "previous_distribution_run_url": previous["workflow_run_url"],
        "gateway_source_commit": gateway["source_commit"],
        "gateway_deployment_evidence_url": gateway["deployment_evidence_url"],
        "clean_machine_runs": clean,
        "lifecycle": lifecycle,
        "client_auth_recovery": clients,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare")
    prepare_command.add_argument("--candidate-distribution-run-url", required=True)
    prepare_command.add_argument("--previous-distribution-run-url", required=True)
    prepare_command.add_argument("--gateway-deployment-evidence-url", required=True)
    prepare_command.add_argument("--expected-source-commit", required=True)
    prepare_command.add_argument("--output", type=Path, required=True)
    assemble_command = commands.add_parser("assemble")
    assemble_command.add_argument("--inputs", type=Path, required=True)
    assemble_command.add_argument("--arm64-record", type=Path, required=True)
    assemble_command.add_argument("--x86-64-record", type=Path, required=True)
    assemble_command.add_argument("--lifecycle", type=Path, required=True)
    assemble_command.add_argument("--codex-record", type=Path, required=True)
    assemble_command.add_argument("--claude-record", type=Path, required=True)
    assemble_command.add_argument("--source-commit", required=True)
    assemble_command.add_argument("--workflow-run-url", required=True)
    assemble_command.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "prepare":
            value = prepare(
                candidate_url=arguments.candidate_distribution_run_url,
                previous_url=arguments.previous_distribution_run_url,
                gateway_url=arguments.gateway_deployment_evidence_url,
                expected_source_commit=arguments.expected_source_commit,
            )
        else:
            value = assemble(
                inputs=_load_json(arguments.inputs, "operations_inputs"),
                arm64_record=_load_json(arguments.arm64_record, "arm64_record"),
                x86_64_record=_load_json(arguments.x86_64_record, "x86_64_record"),
                lifecycle=_load_json(arguments.lifecycle, "lifecycle"),
                codex_record=_load_json(arguments.codex_record, "codex_record"),
                claude_record=_load_json(arguments.claude_record, "claude_record"),
                source_commit=arguments.source_commit,
                workflow_run_url=arguments.workflow_run_url,
            )
        _write_exclusive(arguments.output, value)
    except (MacPilotOperationsError, OSError, UnicodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "schema_id": value["schema_id"],
                "schema_version": SCHEMA_VERSION,
                "status": "passed",
                "source_commit": value["source_commit"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
