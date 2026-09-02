#!/usr/bin/env python3
"""Validate content-free readiness evidence for the signed Hormuz Mac pilot.

This gate binds operational observations to the exact notarized archive and to
the content-free proof files emitted by the Mac distribution workflow.  It
does not collect human onboarding evidence and never changes the independent
onboarding counts maintained by the separate v1.0.0 study.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from tools.client_release_versions import (
        SUPPORTED_CLAUDE_CODE_VERSION,
        SUPPORTED_CODEX_VERSION,
    )
except ModuleNotFoundError:  # Direct execution sets tools/ as sys.path[0].
    from client_release_versions import (  # type: ignore[no-redef]
        SUPPORTED_CLAUDE_CODE_VERSION,
        SUPPORTED_CODEX_VERSION,
    )

try:
    from tools.verify_macos_distribution import (
        VerificationError as DistributionVerificationError,
        verify_archive,
    )
except ModuleNotFoundError:  # Direct execution sets tools/ as sys.path[0].
    from verify_macos_distribution import (  # type: ignore[no-redef]
        VerificationError as DistributionVerificationError,
        verify_archive,
    )


SCHEMA_ID = "hormuz.macos-pilot-qualification"
SCHEMA_VERSION = 1
CLAIM_SCOPE = "signed_macos_controlled_external_pilot_readiness"
EVIDENCE_KINDS = {"pilot_qualification", "synthetic_test_fixture"}
PRODUCTION_BUNDLE_IDENTIFIER = "com.xpounder.hormuz"
PRODUCTION_TEAM_IDENTIFIER = "R267LZMUTY"
SYNTHETIC_BUNDLE_IDENTIFIER = "com.example.hormuzpilot"
MACOS_DISTRIBUTION_WORKFLOW = ".github/workflows/macos-distribution.yml"
MACOS_PILOT_OPERATIONS_WORKFLOW = ".github/workflows/macos-pilot-operations.yml"
EXTERNAL_PILOT_WORKFLOW = ".github/workflows/external-pilot-qualification.yml"

_MAX_FILE_BYTES = 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_ACTIONS_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_JSON_ARTIFACT_BYTES = 2 * 1024 * 1024
_MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)

_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TEAM_ID_RE = re.compile(r"[A-Z0-9]{10}\Z")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_BUILD_RE = re.compile(r"[1-9][0-9]{0,17}\Z")
_BUNDLE_ID_RE = re.compile(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)+\Z")
_CLIENT_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?\Z")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_RUN_ID_RE = re.compile(rf"mcr:{_UUID}\Z")
_SUBMISSION_ID_RE = re.compile(_UUID + r"\Z", re.IGNORECASE)
_ACTIONS_RUN_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]{0,19}\Z"
)
_ISSUE_COMMENT_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/issues/([1-9][0-9]*)"
    r"#issuecomment-([1-9][0-9]{0,19})\Z"
)
_PRIVATE_REFERENCE_RE = re.compile(rf"private-(?:security-)?review:{_UUID}\Z")
_GITHUB_LOGIN_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z"
)

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "generated_at",
    "claim_scope",
    "artifact",
    "previous_artifact",
    "macos_operational_evidence_url",
    "clean_machine_runs",
    "lifecycle",
    "client_auth_recovery",
    "hosted_gateway",
    "reviews",
    "operator_attestations",
    "open_blockers",
}
_ARTIFACT_FIELDS = {
    "source_commit",
    "workflow_run_url",
    "bundle_identifier",
    "version",
    "build",
    "team_identifier",
    "archive_name",
    "archive_bytes",
    "archive_sha256",
    "distribution_proof_sha256",
    "notarization_summary_sha256",
    "submission_id",
}
_DISTRIBUTION_PROOF_FIELDS = {
    "schema_id",
    "schema_version",
    "passed",
    "mode",
    "distribution_ready",
    "bundle_identifier",
    "version",
    "build",
    "architectures",
    "minimum_macos",
    "hardened_runtime",
    "entitlements",
    "system_runtime_dependencies_only",
    "executable_version_verified",
    "notarization_ticket_stapled",
    "team_identifier",
    "signing_authority",
    "archive_bytes",
    "archive_sha256",
    "executable_sha256",
    "icon_sha256",
    "source_commit",
    "workflow_run_url",
}
_NOTARIZATION_FIELDS = {
    "schema_id",
    "schema_version",
    "submission_id",
    "status",
    "accepted",
    "issue_count",
    "issue_severities",
    "ticket_entry_count",
}
_CLEAN_MACHINE_FIELDS = {
    "run_id",
    "artifact_sha256",
    "started_at",
    "architecture",
    "macos_major",
    "developer_tools_absent",
    "quarantine_present",
    "gatekeeper_accepted",
    "installed_in_applications",
    "launch_succeeded",
}
_LIFECYCLE_FIELDS = {
    "update_from_build",
    "update_to_build",
    "rollback_to_build",
    "real_oidc_login",
    "keychain_session_created",
    "restart_preserved_session",
    "lock_unlock_preserved_session",
    "refresh_rotated_session",
    "sign_out_removed_session",
    "server_revocation_denied_session",
    "same_build_reinstall_verified",
    "newer_build_update_verified",
    "previous_build_rollback_verified",
    "credential_file_absent",
    "native_helper_used",
    "previous_notarized_archive_retained",
}
_CLIENT_FIELDS = {
    "client",
    "client_version",
    "artifact_sha256",
    "first_status",
    "refresh_count",
    "automatic_replay_count",
    "explicit_retry_count",
    "provider_egress_on_rejected_turn",
    "provider_egress_after_success",
    "completed",
    "native_keychain_helper",
}
_MACOS_OPERATIONS_EVIDENCE_FIELDS = {
    "schema_id",
    "schema_version",
    "claim_scope",
    "source_commit",
    "workflow_run_url",
    "candidate_archive_sha256",
    "candidate_distribution_run_url",
    "previous_source_commit",
    "previous_archive_sha256",
    "previous_distribution_run_url",
    "clean_machine_runs",
    "lifecycle",
    "client_auth_recovery",
}
_HOSTED_GATEWAY_FIELDS = {
    "evidence_kind",
    "profile",
    "source_commit",
    "deployment_evidence_url",
    "recovery_evidence_url",
    "identity_provider",
    "provider_protocols",
    "https",
    "inference_enabled",
    "provider_credentials_server_only",
    "postgresql_durable",
    "tenant_rls",
    "durable_sessions",
    "streaming_verified",
    "streaming_first_chunk_before_completion",
    "cancellation_verified",
    "cancellation_upstream_closed",
    "cancellation_outcome_unknown_recorded",
    "cancellation_replay_count",
    "latency_measurement_verified",
    "latency_header_sample_count",
    "latency_first_body_byte_sample_count",
    "latency_total_sample_count",
    "policy_bounded_same_protocol_failover_verified",
    "failover_rehearsal_passed",
    "failover_link_record_count",
    "failover_hop_limit",
    "monitoring_configured",
    "worker_saturation_monitoring",
    "postgresql_pool_wait_monitoring",
    "recovery_drill_passed",
    "support_path_published",
    "single_region_acknowledged",
    "availability_sla_claimed",
    "live_provider_request_count",
    "provider_attempt_record_count",
    "max_inflight_streams",
}
_GATEWAY_DEPLOYMENT_EVIDENCE_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "profile",
    "source_commit",
    "workflow_run_url",
    "identity_provider",
    "provider_protocols",
    "https",
    "inference_enabled",
    "provider_credentials_server_only",
    "postgresql_durable",
    "tenant_rls",
    "durable_sessions",
    "monitoring_configured",
    "worker_saturation_monitoring",
    "postgresql_pool_wait_monitoring",
    "support_path_published",
    "single_region_acknowledged",
    "availability_sla_claimed",
    "max_inflight_streams",
}
_GATEWAY_QUALIFICATION_EVIDENCE_FIELDS = {
    "schema_id",
    "schema_version",
    *_HOSTED_GATEWAY_FIELDS,
}
_REVIEWS_FIELDS = {"security", "accessibility"}
_REVIEW_FIELDS = {
    "status",
    "independent_reviewer",
    "reference_type",
    "reference",
    "artifact_sha256",
    "source_commit",
    "completed_at",
}
_REVIEW_ATTESTATION_FIELDS = {
    "schema_id",
    "schema_version",
    "claim_scope",
    "review_kind",
    "status",
    "independent_reviewer",
    "artifact_sha256",
    "source_commit",
}
_OPERATOR_FIELDS = {
    "artifact_workflow_head_matches_source",
    "artifact_default_branch_source",
    "artifact_download_digest_verified",
    "evidence_content_free",
    "credentials_absent",
    "prompts_responses_absent",
    "customer_data_absent",
    "all_failed_runs_included",
    "existing_external_onboarding_counts_unchanged",
    "no_availability_sla_claim",
}

_CLIENT_EXPECTATIONS = {
    "codex": {
        "version": SUPPORTED_CODEX_VERSION,
        "first_status": 401,
        "refresh_count": 1,
        "automatic_replay_count": 1,
        "explicit_retry_count": 0,
        "provider_egress_on_rejected_turn": 0,
        "provider_egress_after_success": 1,
    },
    "claude-code": {
        "version": SUPPORTED_CLAUDE_CODE_VERSION,
        "first_status": 401,
        "refresh_count": 1,
        "automatic_replay_count": 0,
        "explicit_retry_count": 1,
        "provider_egress_on_rejected_turn": 0,
        "provider_egress_after_success": 1,
    },
}
_HOSTED_TRUE_FIELDS = {
    "https",
    "inference_enabled",
    "provider_credentials_server_only",
    "postgresql_durable",
    "tenant_rls",
    "durable_sessions",
    "streaming_verified",
    "streaming_first_chunk_before_completion",
    "cancellation_verified",
    "cancellation_upstream_closed",
    "cancellation_outcome_unknown_recorded",
    "latency_measurement_verified",
    "policy_bounded_same_protocol_failover_verified",
    "failover_rehearsal_passed",
    "monitoring_configured",
    "worker_saturation_monitoring",
    "postgresql_pool_wait_monitoring",
    "recovery_drill_passed",
    "support_path_published",
    "single_region_acknowledged",
}
_LIFECYCLE_TRUE_FIELDS = _LIFECYCLE_FIELDS - {
    "update_from_build",
    "update_to_build",
    "rollback_to_build",
}
_BLOCKERS = {
    "artifact_not_available",
    "clean_machine_coverage",
    "keychain_lifecycle",
    "client_auth_recovery",
    "hosted_gateway",
    "provider_failover",
    "monitoring",
    "recovery",
    "security_review",
    "accessibility_review",
    "support_path",
    "other_bounded",
}


class MacPilotEvidenceError(ValueError):
    """A fail-closed signed-Mac pilot evidence contract violation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise MacPilotEvidenceError("duplicate_json_member")
        value[key] = item
    return value


def _json_values_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_values_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _reject_json_constant(_value: str) -> object:
    raise MacPilotEvidenceError("non_finite_json_number")


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_bounded_regular(path: Path, maximum: int, label: str) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise MacPilotEvidenceError(f"{label}_not_bounded_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(before) != _file_identity(opened)
        ):
            raise MacPilotEvidenceError(f"{label}_changed_during_open")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            payload = source.read(maximum + 1)
        after = path.lstat()
    except OSError as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > maximum
        or not stat.S_ISREG(after.st_mode)
        or _file_identity(before) != _file_identity(after)
    ):
        raise MacPilotEvidenceError(f"{label}_changed_during_read")
    return payload


def _digest_bounded_regular(path: Path, maximum: int, label: str) -> tuple[int, str]:
    try:
        before = path.lstat()
    except OSError as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
        raise MacPilotEvidenceError(f"{label}_not_bounded_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = -1
    digest = hashlib.sha256()
    total = 0
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(before) != _file_identity(opened)
        ):
            raise MacPilotEvidenceError(f"{label}_changed_during_open")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise MacPilotEvidenceError(f"{label}_not_bounded_regular_file")
                digest.update(chunk)
        after = path.lstat()
    except OSError as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        total != before.st_size
        or not stat.S_ISREG(after.st_mode)
        or _file_identity(before) != _file_identity(after)
    ):
        raise MacPilotEvidenceError(f"{label}_changed_during_read")
    return total, digest.hexdigest()


def _snapshot_bounded_regular(
    path: Path, maximum: int, label: str, destination_directory: Path
) -> tuple[Path, int, str]:
    try:
        before = path.lstat()
    except OSError as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
        raise MacPilotEvidenceError(f"{label}_not_bounded_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = -1
    digest = hashlib.sha256()
    total = 0
    snapshot = destination_directory / path.name
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(before) != _file_identity(opened)
        ):
            raise MacPilotEvidenceError(f"{label}_changed_during_open")
        source = os.fdopen(descriptor, "rb")
        descriptor = -1
        with source, snapshot.open("xb") as destination:
            os.chmod(snapshot, 0o600)
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise MacPilotEvidenceError(f"{label}_not_bounded_regular_file")
                digest.update(chunk)
                destination.write(chunk)
        after = path.lstat()
    except OSError as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if (
        total != before.st_size
        or not stat.S_ISREG(after.st_mode)
        or _file_identity(before) != _file_identity(after)
    ):
        raise MacPilotEvidenceError(f"{label}_changed_during_read")
    return snapshot, total, digest.hexdigest()


def _parse_json(payload: bytes, label: str) -> object:
    try:
        return json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except MacPilotEvidenceError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise MacPilotEvidenceError(f"{label}_json_invalid") from error


def _require_fields(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise MacPilotEvidenceError(f"{label}_fields_invalid")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise MacPilotEvidenceError(f"{label}_invalid")
    return value


def _require_int(value: object, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MacPilotEvidenceError(f"{label}_invalid")
    return value


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MacPilotEvidenceError(f"{label}_invalid")
    return value


def _require_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise MacPilotEvidenceError(f"{label}_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise MacPilotEvidenceError(f"{label}_invalid") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise MacPilotEvidenceError(f"{label}_invalid")
    return parsed


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _authenticate_github_run(
    url: str,
    source_commit: str,
    workflow_path: str,
    label: str,
) -> dict[str, Any]:
    if (
        _ACTIONS_RUN_RE.fullmatch(url) is None
        or _REVISION_RE.fullmatch(source_commit) is None
    ):
        raise MacPilotEvidenceError(f"{label}_github_run_not_trusted")
    run_id = url.rsplit("/", 1)[-1]
    try:
        payload = _command_output_bounded(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                f"repos/Xpounder-com/hormuz/actions/runs/{run_id}",
            ],
            _MAX_FILE_BYTES,
            30,
            f"{label}_github_run",
        )
    except MacPilotEvidenceError as error:
        raise MacPilotEvidenceError(f"{label}_github_run_unavailable") from error
    value = _parse_json(payload, f"{label}_github_run")
    repository = value.get("repository") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or isinstance(value.get("id"), bool)
        or value.get("id") != int(run_id)
        or value.get("html_url") != url
        or value.get("head_sha") != source_commit
        or value.get("head_branch") != "main"
        or value.get("event") != "workflow_dispatch"
        or value.get("status") != "completed"
        or value.get("conclusion") != "success"
        or value.get("path") != workflow_path
        or not isinstance(repository, dict)
        or repository.get("full_name") != "Xpounder-com/hormuz"
    ):
        raise MacPilotEvidenceError(f"{label}_github_run_not_trusted")
    return value


def _github_api_json(endpoint: str, label: str) -> object:
    try:
        payload = _command_output_bounded(
            ["gh", "api", "--hostname", "github.com", "--method", "GET", endpoint],
            _MAX_FILE_BYTES,
            30,
            label,
        )
    except MacPilotEvidenceError as error:
        raise MacPilotEvidenceError(f"{label}_github_api_unavailable") from error
    return _parse_json(payload, label)


def _stream_command_to_file(
    command: list[str],
    destination: Path,
    expected_size: int | None,
    maximum: int,
    timeout: float,
    label: str,
) -> None:
    if expected_size is not None and (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or not 1 <= expected_size <= maximum
    ):
        raise MacPilotEvidenceError(f"{label}_too_large")
    process: subprocess.Popen[bytes] | None = None
    stdout = None
    total = 0
    return_code: int | None = None
    try:
        with destination.open("xb") as output:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout = process.stdout
            if stdout is None:
                raise OSError("subprocess stdout pipe unavailable")
            descriptor = stdout.fileno()
            os.set_blocking(descriptor, False)
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                selector.register(descriptor, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    if not selector.select(remaining):
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        chunk = os.read(descriptor, 64 * 1024)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        break
                    next_total = total + len(chunk)
                    if next_total > maximum or (
                        expected_size is not None and next_total > expected_size
                    ):
                        raise MacPilotEvidenceError(f"{label}_too_large")
                    output.write(chunk)
                    total = next_total
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            return_code = process.wait(timeout=remaining)
    except MacPilotEvidenceError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise MacPilotEvidenceError(f"{label}_unavailable") from error
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            if stdout is not None:
                try:
                    stdout.close()
                except (OSError, ValueError):
                    pass
            try:
                process.wait(timeout=5)
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    if (
        return_code != 0
        or total < 1
        or (expected_size is not None and total != expected_size)
    ):
        raise MacPilotEvidenceError(f"{label}_unavailable")


def _command_output_bounded(
    command: list[str], maximum: int, timeout: float, label: str
) -> bytes:
    with tempfile.TemporaryDirectory(prefix="hormuz-command-output-") as temporary:
        destination = Path(temporary) / "stdout"
        _stream_command_to_file(
            command,
            destination,
            None,
            maximum,
            timeout,
            label,
        )
        return _read_bounded_regular(destination, maximum, label)


def _download_github_artifact(
    artifact_id: int,
    destination: Path,
    expected_size: int,
    maximum: int,
    timeout: float,
    label: str,
) -> None:
    _stream_command_to_file(
        [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            f"repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}/zip",
        ],
        destination,
        expected_size,
        maximum,
        timeout,
        f"{label}_github_artifact",
    )


def _validate_github_run_timeline(
    run: dict[str, Any], generated_at: datetime, label: str
) -> tuple[datetime, datetime]:
    created_at = _require_timestamp(run.get("created_at"), f"{label}_created_at")
    started_at = _require_timestamp(
        run.get("run_started_at"), f"{label}_started_at"
    )
    completed_at = _require_timestamp(
        run.get("updated_at"), f"{label}_completed_at"
    )
    if (
        created_at > started_at
        or started_at > completed_at
        or completed_at > generated_at
    ):
        raise MacPilotEvidenceError(f"{label}_chronology_invalid")
    return started_at, completed_at


def _validate_gateway_run_timeline(
    deployment_run: dict[str, Any],
    recovery_run: dict[str, Any],
    generated_at: datetime,
) -> None:
    _, deployment_completed_at = _validate_github_run_timeline(
        deployment_run, generated_at, "gateway_deployment"
    )
    recovery_started_at, _ = _validate_github_run_timeline(
        recovery_run, generated_at, "gateway_recovery"
    )
    if deployment_completed_at > recovery_started_at:
        raise MacPilotEvidenceError("gateway_run_sequence_invalid")


def _read_single_json_artifact_zip(
    artifact_zip: Path, member_name: str, label: str
) -> object:
    try:
        with zipfile.ZipFile(artifact_zip) as package:
            members = package.infolist()
            if len(members) != 1 or members[0].filename != member_name:
                raise MacPilotEvidenceError(
                    f"{label}_github_artifact_members_invalid"
                )
            member = members[0]
            file_type = (member.external_attr >> 16) & 0o170000
            if (
                member.is_dir()
                or "/" in member.filename
                or "\\" in member.filename
                or file_type not in {0, stat.S_IFREG}
                or member.flag_bits & 0x1
                or not 1 <= member.file_size <= _MAX_FILE_BYTES
            ):
                raise MacPilotEvidenceError(
                    f"{label}_github_artifact_members_invalid"
                )
            with package.open(member) as source:
                payload = source.read(_MAX_FILE_BYTES + 1)
            if len(payload) != member.file_size or len(payload) > _MAX_FILE_BYTES:
                raise MacPilotEvidenceError(
                    f"{label}_github_artifact_member_changed"
                )
    except MacPilotEvidenceError:
        raise
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
        raise MacPilotEvidenceError(f"{label}_github_artifact_zip_invalid") from error
    return _parse_json(payload, f"{label}_evidence")


def _require_github_run_identity(
    run: dict[str, Any], label: str
) -> tuple[int, int, int]:
    run_id = run.get("id")
    run_number = run.get("run_number")
    run_attempt = run.get("run_attempt")
    if (
        isinstance(run_id, bool)
        or not isinstance(run_id, int)
        or run_id < 1
        or isinstance(run_number, bool)
        or not isinstance(run_number, int)
        or not 1 <= run_number <= 999_999_999_999_999
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or not 1 <= run_attempt < 1000
    ):
        raise MacPilotEvidenceError(f"{label}_github_run_identity_invalid")
    return run_id, run_number, run_attempt


def _authenticate_run_json_artifact(
    run: dict[str, Any],
    source_commit: str,
    expected_name: str,
    member_name: str,
    label: str,
) -> tuple[object, datetime]:
    run_id, _, _ = _require_github_run_identity(run, label)
    response = _github_api_json(
        f"repos/Xpounder-com/hormuz/actions/runs/{run_id}/artifacts?per_page=100",
        f"{label}_github_artifacts",
    )
    if not isinstance(response, dict):
        raise MacPilotEvidenceError(f"{label}_github_artifacts_invalid")
    total_count = response.get("total_count")
    artifacts = response.get("artifacts")
    if (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or not 0 <= total_count <= 100
        or not isinstance(artifacts, list)
        or len(artifacts) != total_count
    ):
        raise MacPilotEvidenceError(f"{label}_github_artifacts_invalid")
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("name") == expected_name
    ]
    if len(matches) != 1:
        raise MacPilotEvidenceError(f"{label}_github_artifact_not_unique")
    artifact = matches[0]
    artifact_id = artifact.get("id")
    artifact_size = artifact.get("size_in_bytes")
    workflow_run = artifact.get("workflow_run")
    artifact_created_at = _require_timestamp(
        artifact.get("created_at"), f"{label}_github_artifact_created_at"
    )
    run_started_at = _require_timestamp(
        run.get("run_started_at"), f"{label}_github_run_started_at"
    )
    run_completed_at = _require_timestamp(
        run.get("updated_at"), f"{label}_github_run_completed_at"
    )
    if (
        isinstance(artifact_id, bool)
        or not isinstance(artifact_id, int)
        or artifact_id < 1
        or isinstance(artifact_size, bool)
        or not isinstance(artifact_size, int)
        or not 1 <= artifact_size <= _MAX_JSON_ARTIFACT_BYTES
        or artifact.get("expired") is not False
        or artifact.get("url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}"
        or artifact.get("archive_download_url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}/zip"
        or not isinstance(workflow_run, dict)
        or isinstance(workflow_run.get("id"), bool)
        or workflow_run.get("id") != run_id
        or workflow_run.get("head_branch") != "main"
        or workflow_run.get("head_sha") != source_commit
        or not run_started_at <= artifact_created_at <= run_completed_at
    ):
        raise MacPilotEvidenceError(f"{label}_github_artifact_not_trusted")

    with tempfile.TemporaryDirectory(prefix="hormuz-json-artifact-") as temporary:
        artifact_zip = Path(temporary) / "artifact.zip"
        _download_github_artifact(
            artifact_id,
            artifact_zip,
            artifact_size,
            _MAX_JSON_ARTIFACT_BYTES,
            30,
            label,
        )
        payload = _read_single_json_artifact_zip(
            artifact_zip,
            member_name,
            label,
        )
    return payload, artifact_created_at


def _authenticate_gateway_evidence_artifact(
    run: dict[str, Any],
    gateway: dict[str, Any],
    evidence_role: str,
    label: str,
) -> None:
    if evidence_role not in {"deployment", "qualification"}:
        raise MacPilotEvidenceError(f"{label}_role_invalid")
    _, run_number, run_attempt = _require_github_run_identity(run, label)
    payload, _ = _authenticate_run_json_artifact(
        run,
        gateway["source_commit"],
        f"hormuz-external-pilot-{evidence_role}-{run_number}-{run_attempt}",
        f"external-pilot-{evidence_role}-evidence.json",
        label,
    )
    _validate_gateway_evidence_payload(payload, gateway, evidence_role, label)


def _verify_distribution_artifact_zip(
    artifact_zip: Path,
    proof: dict[str, Any],
    proof_payload: bytes,
    notarization_payload: bytes,
    archive_size: int,
    archive_sha256: str,
    label: str,
) -> None:
    archive_name = f"Hormuz-{proof['version']}-notarized.zip"
    dsym_name = f"Hormuz-{proof['version']}.dSYM.zip"
    expected = {
        archive_name: (archive_size, archive_sha256),
        "distribution-proof.json": (len(proof_payload), _sha256(proof_payload)),
        "notarization.json": (
            len(notarization_payload),
            _sha256(notarization_payload),
        ),
    }
    try:
        with zipfile.ZipFile(artifact_zip) as package:
            members = package.infolist()
            names = [member.filename for member in members]
            if (
                len(names) != len(set(names))
                or not set(expected) <= set(names)
                or not set(names) <= {*expected, dsym_name}
            ):
                raise MacPilotEvidenceError(f"{label}_github_artifact_members_invalid")
            total_size = 0
            for member in members:
                file_type = (member.external_attr >> 16) & 0o170000
                if (
                    member.is_dir()
                    or member.filename in {"", ".", ".."}
                    or "/" in member.filename
                    or "\\" in member.filename
                    or file_type == stat.S_IFLNK
                    or member.flag_bits & 0x1
                    or member.file_size < 1
                ):
                    raise MacPilotEvidenceError(f"{label}_github_artifact_members_invalid")
                total_size += member.file_size
                if total_size > _MAX_ACTIONS_ARTIFACT_BYTES:
                    raise MacPilotEvidenceError(f"{label}_github_artifact_too_large")
                digest = hashlib.sha256()
                observed = 0
                with package.open(member) as source:
                    while chunk := source.read(1024 * 1024):
                        observed += len(chunk)
                        if observed > member.file_size:
                            raise MacPilotEvidenceError(
                                f"{label}_github_artifact_member_changed"
                            )
                        digest.update(chunk)
                if observed != member.file_size:
                    raise MacPilotEvidenceError(f"{label}_github_artifact_member_changed")
                binding = expected.get(member.filename)
                if binding is not None and (observed, digest.hexdigest()) != binding:
                    raise MacPilotEvidenceError(f"{label}_github_artifact_binding_invalid")
    except MacPilotEvidenceError:
        raise
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
        raise MacPilotEvidenceError(f"{label}_github_artifact_zip_invalid") from error


def _authenticate_distribution_artifact(
    url: str,
    source_commit: str,
    proof: dict[str, Any],
    proof_payload: bytes,
    notarization_payload: bytes,
    archive_size: int,
    archive_sha256: str,
    generated_at: datetime,
    label: str,
) -> dict[str, object]:
    run = _authenticate_github_run(
        url,
        source_commit,
        MACOS_DISTRIBUTION_WORKFLOW,
        label,
    )
    run_started_at, run_completed_at = _validate_github_run_timeline(
        run, generated_at, label
    )
    run_id = run["id"]
    run_number = run.get("run_number")
    run_attempt = run.get("run_attempt")
    if (
        isinstance(run_number, bool)
        or not isinstance(run_number, int)
        or not 1 <= run_number <= 999_999_999_999_999
        or isinstance(run_attempt, bool)
        or not isinstance(run_attempt, int)
        or not 1 <= run_attempt < 1000
        or proof["build"] != str(run_number * 1000 + run_attempt)
    ):
        raise MacPilotEvidenceError(f"{label}_github_run_build_invalid")
    actor_logins: set[str] = set()
    for actor_label in ("actor", "triggering_actor"):
        actor = run.get(actor_label)
        login = actor.get("login") if isinstance(actor, dict) else None
        actor_logins.add(
            _require_pattern(
                login,
                _GITHUB_LOGIN_RE,
                f"{label}_github_run_{actor_label}",
            ).casefold()
        )

    response = _github_api_json(
        f"repos/Xpounder-com/hormuz/actions/runs/{run_id}/artifacts?per_page=100",
        f"{label}_github_artifacts",
    )
    if not isinstance(response, dict):
        raise MacPilotEvidenceError(f"{label}_github_artifacts_invalid")
    total_count = response.get("total_count")
    artifacts = response.get("artifacts")
    if (
        isinstance(total_count, bool)
        or not isinstance(total_count, int)
        or not 0 <= total_count <= 100
        or not isinstance(artifacts, list)
        or len(artifacts) != total_count
    ):
        raise MacPilotEvidenceError(f"{label}_github_artifacts_invalid")
    expected_name = f"hormuz-macos-{proof['version']}-{run_number}-{run_attempt}"
    matches = [
        artifact
        for artifact in artifacts
        if isinstance(artifact, dict) and artifact.get("name") == expected_name
    ]
    if len(matches) != 1:
        raise MacPilotEvidenceError(f"{label}_github_artifact_not_unique")
    artifact = matches[0]
    artifact_id = artifact.get("id")
    artifact_size = artifact.get("size_in_bytes")
    workflow_run = artifact.get("workflow_run")
    artifact_created_at = _require_timestamp(
        artifact.get("created_at"), f"{label}_github_artifact_created_at"
    )
    if (
        isinstance(artifact_id, bool)
        or not isinstance(artifact_id, int)
        or artifact_id < 1
        or isinstance(artifact_size, bool)
        or not isinstance(artifact_size, int)
        or not 1 <= artifact_size <= _MAX_ACTIONS_ARTIFACT_BYTES
        or artifact.get("expired") is not False
        or artifact.get("url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}"
        or artifact.get("archive_download_url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/actions/artifacts/{artifact_id}/zip"
        or not isinstance(workflow_run, dict)
        or isinstance(workflow_run.get("id"), bool)
        or workflow_run.get("id") != run_id
        or workflow_run.get("head_branch") != "main"
        or workflow_run.get("head_sha") != source_commit
        or not run_started_at <= artifact_created_at <= run_completed_at
    ):
        raise MacPilotEvidenceError(f"{label}_github_artifact_not_trusted")

    with tempfile.TemporaryDirectory(prefix="hormuz-pilot-artifact-") as temporary:
        artifact_zip = Path(temporary) / "artifact.zip"
        _download_github_artifact(
            artifact_id,
            artifact_zip,
            artifact_size,
            _MAX_ACTIONS_ARTIFACT_BYTES,
            120,
            label,
        )
        _verify_distribution_artifact_zip(
            artifact_zip,
            proof,
            proof_payload,
            notarization_payload,
            archive_size,
            archive_sha256,
            label,
        )
    return {
        "run_number": run_number,
        "run_attempt": run_attempt,
        "artifact_created_at": artifact_created_at,
        "actor_logins": actor_logins,
    }


def _validate_authenticated_distribution_history(
    artifact: dict[str, object], previous_artifact: dict[str, object]
) -> None:
    run_number = artifact.get("run_number")
    previous_run_number = previous_artifact.get("run_number")
    run_attempt = artifact.get("run_attempt")
    created_at = artifact.get("artifact_created_at")
    previous_created_at = previous_artifact.get("artifact_created_at")
    if (
        isinstance(run_number, bool)
        or not isinstance(run_number, int)
        or isinstance(previous_run_number, bool)
        or not isinstance(previous_run_number, int)
        or run_number != previous_run_number + 1
        or isinstance(run_attempt, bool)
        or run_attempt != 1
        or not isinstance(created_at, datetime)
        or not isinstance(previous_created_at, datetime)
        or created_at <= previous_created_at
    ):
        raise MacPilotEvidenceError("previous_artifact_not_immediate")


def _verify_production_archive(
    archive_path: Path,
    proof: dict[str, Any],
    label: str,
) -> None:
    before = _digest_bounded_regular(archive_path, _MAX_ARCHIVE_BYTES, label)
    expected = (proof["archive_bytes"], proof["archive_sha256"])
    if before != expected:
        raise MacPilotEvidenceError(f"{label}_platform_archive_binding_invalid")
    try:
        signature = verify_archive(
            archive_path,
            None,
            "notarized",
            PRODUCTION_BUNDLE_IDENTIFIER,
        )
    except (DistributionVerificationError, OSError) as error:
        raise MacPilotEvidenceError(f"{label}_platform_verification_failed") from error
    after = _digest_bounded_regular(archive_path, _MAX_ARCHIVE_BYTES, label)
    if after != before:
        raise MacPilotEvidenceError(f"{label}_platform_archive_changed")
    if signature != {
        "team_identifier": PRODUCTION_TEAM_IDENTIFIER,
        "authority": proof["signing_authority"],
    }:
        raise MacPilotEvidenceError(f"{label}_platform_identity_invalid")


def _validate_distribution_proof(value: object, evidence_kind: str) -> dict[str, Any]:
    proof = _require_fields(value, _DISTRIBUTION_PROOF_FIELDS, "distribution_proof")
    _require_int(proof["schema_version"], 2, 2, "distribution_proof_schema_version")
    if (
        proof["schema_id"] != "hormuz.macos-distribution-proof"
        or proof["passed"] is not True
        or proof["mode"] != "notarized"
        or proof["distribution_ready"] is not True
        or proof["architectures"] != ["arm64", "x86_64"]
        or proof["minimum_macos"] != "14.0"
        or proof["hardened_runtime"] is not True
        or proof["entitlements"] != []
        or proof["system_runtime_dependencies_only"] is not True
        or proof["notarization_ticket_stapled"] is not True
    ):
        raise MacPilotEvidenceError("distribution_proof_not_ready")
    bundle_id = _require_pattern(proof["bundle_identifier"], _BUNDLE_ID_RE, "proof_bundle_identifier")
    if bundle_id.endswith(".local"):
        raise MacPilotEvidenceError("proof_bundle_identifier_local")
    _require_bool(proof["executable_version_verified"], "proof_executable_version_verified")
    _require_pattern(proof["source_commit"], _REVISION_RE, "proof_source_commit")
    _require_pattern(proof["workflow_run_url"], _ACTIONS_RUN_RE, "proof_workflow_run_url")
    _require_pattern(proof["version"], _VERSION_RE, "proof_version")
    _require_pattern(proof["build"], _BUILD_RE, "proof_build")
    team_id = _require_pattern(proof["team_identifier"], _TEAM_ID_RE, "proof_team_identifier")
    authority = proof["signing_authority"]
    if (
        not isinstance(authority, str)
        or not authority.startswith("Developer ID Application: ")
        or not authority.endswith(f"({team_id})")
    ):
        raise MacPilotEvidenceError("proof_signing_authority_invalid")
    if evidence_kind == "pilot_qualification":
        if (
            bundle_id != PRODUCTION_BUNDLE_IDENTIFIER
            or team_id != PRODUCTION_TEAM_IDENTIFIER
            or "Synthetic Fixture" in authority
        ):
            raise MacPilotEvidenceError("distribution_proof_product_identity_invalid")
    elif (
        bundle_id != SYNTHETIC_BUNDLE_IDENTIFIER
        or team_id != "ABCDEFGHIJ"
        or authority != "Developer ID Application: Synthetic Fixture (ABCDEFGHIJ)"
    ):
        raise MacPilotEvidenceError("synthetic_distribution_proof_identity_invalid")
    _require_int(proof["archive_bytes"], 1, _MAX_ARCHIVE_BYTES, "proof_archive_bytes")
    for field in ("archive_sha256", "executable_sha256", "icon_sha256"):
        _require_pattern(proof[field], _SHA256_RE, f"proof_{field}")
    return proof


def _validate_notarization(value: object) -> dict[str, Any]:
    summary = _require_fields(value, _NOTARIZATION_FIELDS, "notarization_summary")
    _require_int(summary["schema_version"], 1, 1, "notarization_schema_version")
    issue_count = _require_int(summary["issue_count"], 0, 1_000, "notarization_issue_count")
    if (
        summary["schema_id"] != "hormuz.apple-notarization"
        or summary["status"] != "Accepted"
        or summary["accepted"] is not True
        or issue_count != 0
        or summary["issue_severities"] != {}
    ):
        raise MacPilotEvidenceError("notarization_not_cleanly_accepted")
    _require_pattern(summary["submission_id"], _SUBMISSION_ID_RE, "notarization_submission_id")
    _require_int(summary["ticket_entry_count"], 2, 100, "notarization_ticket_entry_count")
    return summary


def _validate_artifact(
    value: object,
    proof: dict[str, Any],
    proof_payload: bytes,
    notarization: dict[str, Any],
    notarization_payload: bytes,
    archive_path: Path,
    archive_size: int,
    archive_sha256: str,
    *,
    label: str = "artifact",
) -> dict[str, Any]:
    artifact = _require_fields(value, _ARTIFACT_FIELDS, label)
    _require_pattern(artifact["source_commit"], _REVISION_RE, f"{label}_source_commit")
    _require_pattern(artifact["workflow_run_url"], _ACTIONS_RUN_RE, f"{label}_workflow_run_url")
    _require_pattern(artifact["bundle_identifier"], _BUNDLE_ID_RE, f"{label}_bundle_identifier")
    _require_pattern(artifact["version"], _VERSION_RE, f"{label}_version")
    _require_pattern(artifact["build"], _BUILD_RE, f"{label}_build")
    _require_pattern(artifact["team_identifier"], _TEAM_ID_RE, f"{label}_team_identifier")
    _require_pattern(artifact["archive_sha256"], _SHA256_RE, f"{label}_archive_sha256")
    _require_pattern(
        artifact["distribution_proof_sha256"], _SHA256_RE, f"{label}_distribution_proof_sha256"
    )
    _require_pattern(
        artifact["notarization_summary_sha256"], _SHA256_RE, f"{label}_notarization_summary_sha256"
    )
    _require_pattern(artifact["submission_id"], _SUBMISSION_ID_RE, f"{label}_submission_id")
    _require_int(artifact["archive_bytes"], 1, _MAX_ARCHIVE_BYTES, f"{label}_archive_bytes")
    expected_name = f"Hormuz-{proof['version']}-notarized.zip"
    expected = {
        "source_commit": proof["source_commit"],
        "workflow_run_url": proof["workflow_run_url"],
        "bundle_identifier": proof["bundle_identifier"],
        "version": proof["version"],
        "build": proof["build"],
        "team_identifier": proof["team_identifier"],
        "archive_name": expected_name,
        "archive_bytes": proof["archive_bytes"],
        "archive_sha256": proof["archive_sha256"],
        "distribution_proof_sha256": _sha256(proof_payload),
        "notarization_summary_sha256": _sha256(notarization_payload),
        "submission_id": notarization["submission_id"],
    }
    if any(artifact[field] != expected_value for field, expected_value in expected.items()):
        raise MacPilotEvidenceError(f"{label}_proof_binding_invalid")
    if archive_path.name != expected_name:
        raise MacPilotEvidenceError(f"{label}_archive_name_invalid")
    if archive_size != proof["archive_bytes"] or archive_sha256 != proof["archive_sha256"]:
        raise MacPilotEvidenceError(f"{label}_archive_binding_invalid")
    return artifact


def _validate_clean_machines(
    value: object,
    artifact_sha256: str,
    artifact_created_at: datetime | None,
    generated_at: datetime,
    reasons: list[str],
) -> list[str]:
    if not isinstance(value, list) or len(value) > 8:
        raise MacPilotEvidenceError("clean_machine_runs_invalid")
    seen_ids: set[str] = set()
    qualifying_architectures: set[str] = set()
    for index, raw in enumerate(value):
        label = f"clean_machine_run_{index}"
        run = _require_fields(raw, _CLEAN_MACHINE_FIELDS, label)
        run_id = _require_pattern(run["run_id"], _RUN_ID_RE, f"{label}_run_id")
        if run_id in seen_ids:
            raise MacPilotEvidenceError("clean_machine_run_duplicate")
        seen_ids.add(run_id)
        if run["artifact_sha256"] != artifact_sha256:
            raise MacPilotEvidenceError(f"{label}_artifact_binding_invalid")
        started_at = _require_timestamp(run["started_at"], f"{label}_started_at")
        if artifact_created_at is not None and started_at < artifact_created_at:
            raise MacPilotEvidenceError(f"{label}_predates_artifact")
        if started_at > generated_at:
            raise MacPilotEvidenceError(f"{label}_after_generated_at")
        architecture = run["architecture"]
        if not isinstance(architecture, str) or architecture not in {"arm64", "x86_64"}:
            raise MacPilotEvidenceError(f"{label}_architecture_invalid")
        _require_int(run["macos_major"], 14, 99, f"{label}_macos_major")
        booleans = {
            field: _require_bool(run[field], f"{label}_{field}")
            for field in _CLEAN_MACHINE_FIELDS
            - {"run_id", "artifact_sha256", "started_at", "architecture", "macos_major"}
        }
        if all(booleans.values()):
            qualifying_architectures.add(architecture)
    missing = {"arm64", "x86_64"} - qualifying_architectures
    if missing:
        reasons.append("clean_machine_architecture_coverage_incomplete")
    return sorted(qualifying_architectures)


def _validate_lifecycle(
    value: object, previous_artifact_build: str, artifact_build: str, reasons: list[str]
) -> None:
    lifecycle = _require_fields(value, _LIFECYCLE_FIELDS, "lifecycle")
    source_build = int(_require_pattern(lifecycle["update_from_build"], _BUILD_RE, "update_from_build"))
    target_build = int(_require_pattern(lifecycle["update_to_build"], _BUILD_RE, "update_to_build"))
    rollback_build = int(_require_pattern(lifecycle["rollback_to_build"], _BUILD_RE, "rollback_to_build"))
    if not (
        source_build == int(previous_artifact_build)
        and target_build == int(artifact_build)
        and rollback_build == int(previous_artifact_build)
        and target_build > source_build
    ):
        reasons.append("update_rollback_build_sequence_invalid")
    lifecycle_checks = {
        field: _require_bool(lifecycle[field], f"lifecycle_{field}")
        for field in _LIFECYCLE_TRUE_FIELDS
    }
    if not all(lifecycle_checks.values()):
        reasons.append("keychain_and_session_lifecycle_incomplete")


def _validate_client_recovery(value: object, artifact_sha256: str, reasons: list[str]) -> None:
    if not isinstance(value, list) or len(value) > 2:
        raise MacPilotEvidenceError("client_auth_recovery_invalid")
    seen: set[str] = set()
    complete: set[str] = set()
    for index, raw in enumerate(value):
        label = f"client_auth_recovery_{index}"
        record = _require_fields(raw, _CLIENT_FIELDS, label)
        client = record["client"]
        if not isinstance(client, str) or client not in _CLIENT_EXPECTATIONS or client in seen:
            raise MacPilotEvidenceError(f"{label}_client_invalid")
        seen.add(client)
        version = _require_pattern(record["client_version"], _CLIENT_VERSION_RE, f"{label}_version")
        if record["artifact_sha256"] != artifact_sha256:
            raise MacPilotEvidenceError(f"{label}_artifact_binding_invalid")
        observed = {
            "first_status": _require_int(
                record["first_status"], 100, 599, f"{label}_first_status"
            )
        }
        observed.update(
            {
                field: _require_int(record[field], 0, 100, f"{label}_{field}")
                for field in {
                    "refresh_count",
                    "automatic_replay_count",
                    "explicit_retry_count",
                    "provider_egress_on_rejected_turn",
                    "provider_egress_after_success",
                }
            }
        )
        completed = _require_bool(record["completed"], f"{label}_completed")
        native_helper = _require_bool(record["native_keychain_helper"], f"{label}_native_keychain_helper")
        expected = _CLIENT_EXPECTATIONS[client]
        if (
            version == expected["version"]
            and all(observed[field] == expected[field] for field in observed)
            and completed
            and native_helper
        ):
            complete.add(client)
    if complete != set(_CLIENT_EXPECTATIONS):
        reasons.append("signed_client_401_recovery_incomplete")


def _validate_macos_operations_evidence_payload(
    value: object,
    operations_url: str,
    artifact: dict[str, Any],
    previous_artifact: dict[str, Any],
    clean_machine_runs: object,
    lifecycle: object,
    client_auth_recovery: object,
) -> None:
    evidence = _require_fields(
        value,
        _MACOS_OPERATIONS_EVIDENCE_FIELDS,
        "macos_operations_evidence",
    )
    _require_int(
        evidence["schema_version"],
        1,
        1,
        "macos_operations_evidence_schema_version",
    )
    expected = {
        "schema_id": "hormuz.macos-pilot-operations-evidence",
        "schema_version": 1,
        "claim_scope": CLAIM_SCOPE,
        "source_commit": artifact["source_commit"],
        "workflow_run_url": operations_url,
        "candidate_archive_sha256": artifact["archive_sha256"],
        "candidate_distribution_run_url": artifact["workflow_run_url"],
        "previous_source_commit": previous_artifact["source_commit"],
        "previous_archive_sha256": previous_artifact["archive_sha256"],
        "previous_distribution_run_url": previous_artifact["workflow_run_url"],
        "clean_machine_runs": clean_machine_runs,
        "lifecycle": lifecycle,
        "client_auth_recovery": client_auth_recovery,
    }
    if any(
        not _json_values_equal(evidence[field], expected_value)
        for field, expected_value in expected.items()
    ):
        raise MacPilotEvidenceError("macos_operations_evidence_binding_invalid")


def _authenticate_macos_operational_evidence(
    operations_url: str,
    artifact: dict[str, Any],
    previous_artifact: dict[str, Any],
    clean_machine_runs: object,
    lifecycle: object,
    client_auth_recovery: object,
    artifact_created_at: datetime | None,
    generated_at: datetime,
) -> None:
    label = "macos_operations"
    run = _authenticate_github_run(
        operations_url,
        artifact["source_commit"],
        MACOS_PILOT_OPERATIONS_WORKFLOW,
        label,
    )
    run_started_at, _ = _validate_github_run_timeline(run, generated_at, label)
    if artifact_created_at is None or run_started_at < artifact_created_at:
        raise MacPilotEvidenceError("macos_operations_predates_artifact")
    _, run_number, run_attempt = _require_github_run_identity(run, label)
    payload, operations_artifact_created_at = _authenticate_run_json_artifact(
        run,
        artifact["source_commit"],
        f"hormuz-macos-pilot-operations-{run_number}-{run_attempt}",
        "macos-pilot-operations-evidence.json",
        label,
    )
    _validate_macos_operations_evidence_payload(
        payload,
        operations_url,
        artifact,
        previous_artifact,
        clean_machine_runs,
        lifecycle,
        client_auth_recovery,
    )
    if not isinstance(clean_machine_runs, list):
        raise MacPilotEvidenceError("clean_machine_runs_invalid")
    for index, raw in enumerate(clean_machine_runs):
        run_record = _require_fields(
            raw, _CLEAN_MACHINE_FIELDS, f"clean_machine_run_{index}"
        )
        started_at = _require_timestamp(
            run_record["started_at"], f"clean_machine_run_{index}_started_at"
        )
        if started_at > operations_artifact_created_at:
            raise MacPilotEvidenceError(
                f"clean_machine_run_{index}_after_operations_artifact"
            )


def _validate_hosted_gateway(
    value: object, evidence_kind: str, reasons: list[str]
) -> dict[str, Any]:
    gateway = _require_fields(value, _HOSTED_GATEWAY_FIELDS, "hosted_gateway")
    expected_evidence_kind = (
        "live_external_pilot"
        if evidence_kind == "pilot_qualification"
        else "synthetic_test_fixture"
    )
    if gateway["evidence_kind"] != expected_evidence_kind:
        raise MacPilotEvidenceError("gateway_evidence_kind_invalid")
    _require_pattern(gateway["source_commit"], _REVISION_RE, "gateway_source_commit")
    _require_pattern(gateway["deployment_evidence_url"], _ACTIONS_RUN_RE, "deployment_evidence_url")
    _require_pattern(gateway["recovery_evidence_url"], _ACTIONS_RUN_RE, "recovery_evidence_url")
    if gateway["deployment_evidence_url"] == gateway["recovery_evidence_url"]:
        raise MacPilotEvidenceError("gateway_evidence_urls_not_distinct")
    protocols = gateway["provider_protocols"]
    if (
        not isinstance(protocols, list)
        or not protocols
        or not all(isinstance(protocol, str) for protocol in protocols)
        or protocols != sorted(set(protocols))
        or protocols != ["anthropic", "openai"]
    ):
        raise MacPilotEvidenceError("gateway_provider_protocols_invalid")
    live_requests = _require_int(
        gateway["live_provider_request_count"], 0, 1_000_000, "live_provider_request_count"
    )
    attempts = _require_int(
        gateway["provider_attempt_record_count"], 0, 2_000_000, "provider_attempt_record_count"
    )
    cancellation_replays = _require_int(
        gateway["cancellation_replay_count"], 0, 1_000_000, "cancellation_replay_count"
    )
    header_samples = _require_int(
        gateway["latency_header_sample_count"], 0, 2_000_000, "latency_header_sample_count"
    )
    first_byte_samples = _require_int(
        gateway["latency_first_body_byte_sample_count"],
        0,
        2_000_000,
        "latency_first_body_byte_sample_count",
    )
    total_samples = _require_int(
        gateway["latency_total_sample_count"], 0, 2_000_000, "latency_total_sample_count"
    )
    failover_links = _require_int(
        gateway["failover_link_record_count"], 0, 1_000_000, "failover_link_record_count"
    )
    failover_hops = _require_int(gateway["failover_hop_limit"], 0, 10, "failover_hop_limit")
    maximum_streams = _require_int(gateway["max_inflight_streams"], 1, 8, "max_inflight_streams")
    del maximum_streams
    true_values = {
        field: _require_bool(gateway[field], f"gateway_{field}") for field in _HOSTED_TRUE_FIELDS
    }
    sla_claimed = _require_bool(gateway["availability_sla_claimed"], "availability_sla_claimed")
    if gateway["profile"] != "external_pilot" or gateway["identity_provider"] != "okta":
        reasons.append("external_pilot_gateway_profile_incomplete")
    if not all(true_values.values()):
        reasons.append("external_pilot_gateway_controls_incomplete")
    if (
        live_requests < 1
        or cancellation_replays != 0
        or header_samples < 1
        or first_byte_samples < 1
        or total_samples < 1
    ):
        reasons.append("live_streaming_latency_cancellation_evidence_incomplete")
    extra_attempts = attempts - live_requests
    if (
        live_requests < 1
        or extra_attempts < 1
        or attempts > live_requests * 2
        or failover_links != extra_attempts
        or failover_hops != 1
    ):
        reasons.append("live_provider_failover_evidence_incomplete")
    if sla_claimed:
        reasons.append("unsupported_availability_sla_claimed")
    return gateway


def _validate_gateway_evidence_payload(
    value: object,
    gateway: dict[str, Any],
    evidence_role: str,
    label: str,
) -> None:
    if evidence_role == "deployment":
        evidence = _require_fields(
            value, _GATEWAY_DEPLOYMENT_EVIDENCE_FIELDS, f"{label}_evidence"
        )
        _require_int(
            evidence["schema_version"],
            1,
            1,
            f"{label}_evidence_schema_version",
        )
        expected = {
            "schema_id": "hormuz.external-pilot-deployment-evidence",
            "schema_version": 1,
            "evidence_kind": gateway["evidence_kind"],
            "profile": gateway["profile"],
            "source_commit": gateway["source_commit"],
            "workflow_run_url": gateway["deployment_evidence_url"],
            "identity_provider": gateway["identity_provider"],
            "provider_protocols": gateway["provider_protocols"],
            "https": gateway["https"],
            "inference_enabled": gateway["inference_enabled"],
            "provider_credentials_server_only": gateway[
                "provider_credentials_server_only"
            ],
            "postgresql_durable": gateway["postgresql_durable"],
            "tenant_rls": gateway["tenant_rls"],
            "durable_sessions": gateway["durable_sessions"],
            "monitoring_configured": gateway["monitoring_configured"],
            "worker_saturation_monitoring": gateway[
                "worker_saturation_monitoring"
            ],
            "postgresql_pool_wait_monitoring": gateway[
                "postgresql_pool_wait_monitoring"
            ],
            "support_path_published": gateway["support_path_published"],
            "single_region_acknowledged": gateway["single_region_acknowledged"],
            "availability_sla_claimed": gateway["availability_sla_claimed"],
            "max_inflight_streams": gateway["max_inflight_streams"],
        }
        if any(
            not _json_values_equal(evidence[field], expected_value)
            for field, expected_value in expected.items()
        ):
            raise MacPilotEvidenceError(f"{label}_evidence_binding_invalid")
        return
    if evidence_role != "qualification":
        raise MacPilotEvidenceError(f"{label}_role_invalid")
    evidence = _require_fields(
        value, _GATEWAY_QUALIFICATION_EVIDENCE_FIELDS, f"{label}_evidence"
    )
    _require_int(
        evidence["schema_version"],
        1,
        1,
        f"{label}_evidence_schema_version",
    )
    if evidence["schema_id"] != "hormuz.external-pilot-qualification-evidence":
        raise MacPilotEvidenceError(f"{label}_evidence_identity_invalid")
    evidence_gateway = {field: evidence[field] for field in _HOSTED_GATEWAY_FIELDS}
    evidence_reasons: list[str] = []
    _validate_hosted_gateway(
        evidence_gateway, "pilot_qualification", evidence_reasons
    )
    if evidence_reasons or evidence_gateway != gateway:
        raise MacPilotEvidenceError(f"{label}_evidence_binding_invalid")


def _validate_review(
    value: object,
    label: str,
    artifact_sha256: str,
    source_commit: str,
    artifact_created_at: datetime | None,
    generated_at: datetime,
    reasons: list[str],
) -> dict[str, Any]:
    review = _require_fields(value, _REVIEW_FIELDS, label)
    status = review["status"]
    reference_type = review["reference_type"]
    reference = review["reference"]
    independent = _require_bool(review["independent_reviewer"], f"{label}_independent_reviewer")
    if not isinstance(status, str) or status not in {"not_started", "failed", "passed"}:
        raise MacPilotEvidenceError(f"{label}_status_invalid")
    if (
        review["artifact_sha256"] != artifact_sha256
        or review["source_commit"] != source_commit
    ):
        raise MacPilotEvidenceError(f"{label}_candidate_binding_invalid")
    if status == "not_started":
        if review["completed_at"] != "none":
            raise MacPilotEvidenceError(f"{label}_completion_invalid")
    else:
        completed_at = _require_timestamp(
            review["completed_at"], f"{label}_completed_at"
        )
        if artifact_created_at is not None and completed_at < artifact_created_at:
            raise MacPilotEvidenceError(f"{label}_predates_artifact")
        if completed_at > generated_at:
            raise MacPilotEvidenceError(f"{label}_after_generated_at")
    if reference_type == "none":
        if reference != "none" or status != "not_started":
            raise MacPilotEvidenceError(f"{label}_reference_invalid")
    elif reference_type == "public_issue_comment":
        _require_pattern(reference, _ISSUE_COMMENT_RE, f"{label}_reference")
    elif reference_type == "private_review":
        _require_pattern(reference, _PRIVATE_REFERENCE_RE, f"{label}_reference")
    else:
        raise MacPilotEvidenceError(f"{label}_reference_type_invalid")
    if status != "passed" or not independent or reference_type == "private_review":
        reasons.append(f"{label}_incomplete")
    return review


def _authenticate_review_reference(
    review: dict[str, Any],
    review_kind: str,
    workflow_actor_logins: set[str],
    generated_at: datetime,
    label: str,
) -> None:
    reference = review["reference"]
    matched = _ISSUE_COMMENT_RE.fullmatch(reference)
    if matched is None:
        raise MacPilotEvidenceError(f"{label}_reference_not_authenticatable")
    issue_number, comment_id_text = matched.groups()
    comment_id = int(comment_id_text)
    response = _github_api_json(
        f"repos/Xpounder-com/hormuz/issues/comments/{comment_id}",
        f"{label}_github_comment",
    )
    user = response.get("user") if isinstance(response, dict) else None
    reviewer_login = user.get("login") if isinstance(user, dict) else None
    created_at = _require_timestamp(
        response.get("created_at") if isinstance(response, dict) else None,
        f"{label}_github_comment_created_at",
    )
    updated_at = _require_timestamp(
        response.get("updated_at") if isinstance(response, dict) else None,
        f"{label}_github_comment_updated_at",
    )
    if (
        not isinstance(response, dict)
        or isinstance(response.get("id"), bool)
        or response.get("id") != comment_id
        or response.get("html_url") != reference
        or response.get("issue_url")
        != f"https://api.github.com/repos/Xpounder-com/hormuz/issues/{issue_number}"
        or not isinstance(user, dict)
        or user.get("type") != "User"
        or not isinstance(reviewer_login, str)
        or _GITHUB_LOGIN_RE.fullmatch(reviewer_login) is None
        or reviewer_login.casefold() in workflow_actor_logins
        or created_at > updated_at
        or updated_at > generated_at
        or review["completed_at"] != updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    ):
        raise MacPilotEvidenceError(f"{label}_github_comment_not_trusted")
    body = response.get("body")
    if not isinstance(body, str):
        raise MacPilotEvidenceError(f"{label}_github_comment_not_trusted")
    body_payload = body.encode("utf-8")
    if not 1 <= len(body_payload) <= _MAX_FILE_BYTES:
        raise MacPilotEvidenceError(f"{label}_github_comment_not_trusted")
    attestation = _require_fields(
        _parse_json(body_payload, f"{label}_attestation"),
        _REVIEW_ATTESTATION_FIELDS,
        f"{label}_attestation",
    )
    _require_int(
        attestation["schema_version"],
        1,
        1,
        f"{label}_attestation_schema_version",
    )
    if (
        attestation["schema_id"] != "hormuz.macos-pilot-review"
        or attestation["claim_scope"] != CLAIM_SCOPE
        or attestation["review_kind"] != review_kind
        or attestation["status"] != "passed"
        or attestation["independent_reviewer"] is not True
        or attestation["artifact_sha256"] != review["artifact_sha256"]
        or attestation["source_commit"] != review["source_commit"]
    ):
        raise MacPilotEvidenceError(f"{label}_attestation_invalid")


def validate_evidence(
    value: object,
    *,
    distribution_proof: object,
    distribution_proof_payload: bytes,
    notarization_summary: object,
    notarization_summary_payload: bytes,
    archive_path: Path,
    archive_size: int,
    archive_sha256: str,
    previous_distribution_proof: object,
    previous_distribution_proof_payload: bytes,
    previous_notarization_summary: object,
    previous_notarization_summary_payload: bytes,
    previous_archive_path: Path,
    previous_archive_size: int,
    previous_archive_sha256: str,
    now: datetime | None = None,
) -> dict[str, object]:
    root = _require_fields(value, _ROOT_FIELDS, "evidence")
    _require_int(root["schema_version"], SCHEMA_VERSION, SCHEMA_VERSION, "schema_version")
    if root["schema_id"] != SCHEMA_ID:
        raise MacPilotEvidenceError("schema_identity_invalid")
    evidence_kind = root["evidence_kind"]
    if not isinstance(evidence_kind, str) or evidence_kind not in EVIDENCE_KINDS:
        raise MacPilotEvidenceError("evidence_kind_invalid")
    if root["claim_scope"] != CLAIM_SCOPE:
        raise MacPilotEvidenceError("claim_scope_invalid")
    operations_url = root["macos_operational_evidence_url"]
    if (
        not isinstance(operations_url, str)
        or (
            operations_url != "none"
            and _ACTIONS_RUN_RE.fullmatch(operations_url) is None
        )
    ):
        raise MacPilotEvidenceError("macos_operational_evidence_url_invalid")
    generated_at = _require_timestamp(root["generated_at"], "generated_at")
    current = now or datetime.now(timezone.utc)
    if generated_at > current + _MAX_FUTURE_CLOCK_SKEW:
        raise MacPilotEvidenceError("generated_at_in_future")

    proof = _validate_distribution_proof(distribution_proof, evidence_kind)
    notarization = _validate_notarization(notarization_summary)
    artifact = _validate_artifact(
        root["artifact"],
        proof,
        distribution_proof_payload,
        notarization,
        notarization_summary_payload,
        archive_path,
        archive_size,
        archive_sha256,
    )
    previous_proof = _validate_distribution_proof(previous_distribution_proof, evidence_kind)
    previous_notarization = _validate_notarization(previous_notarization_summary)
    previous_artifact = _validate_artifact(
        root["previous_artifact"],
        previous_proof,
        previous_distribution_proof_payload,
        previous_notarization,
        previous_notarization_summary_payload,
        previous_archive_path,
        previous_archive_size,
        previous_archive_sha256,
        label="previous_artifact",
    )
    if (
        artifact["bundle_identifier"] != previous_artifact["bundle_identifier"]
        or artifact["team_identifier"] != previous_artifact["team_identifier"]
        or int(artifact["build"]) <= int(previous_artifact["build"])
        or artifact["archive_sha256"] == previous_artifact["archive_sha256"]
        or artifact["workflow_run_url"] == previous_artifact["workflow_run_url"]
        or artifact["submission_id"] == previous_artifact["submission_id"]
    ):
        raise MacPilotEvidenceError("artifact_history_binding_invalid")
    artifact_authentication: dict[str, object] | None = None
    artifact_created_at: datetime | None = None
    if evidence_kind == "pilot_qualification":
        _verify_production_archive(archive_path, proof, "artifact")
        _verify_production_archive(
            previous_archive_path, previous_proof, "previous_artifact"
        )
        artifact_authentication = _authenticate_distribution_artifact(
            artifact["workflow_run_url"],
            artifact["source_commit"],
            proof,
            distribution_proof_payload,
            notarization_summary_payload,
            archive_size,
            archive_sha256,
            generated_at,
            "artifact",
        )
        previous_authentication = _authenticate_distribution_artifact(
            previous_artifact["workflow_run_url"],
            previous_artifact["source_commit"],
            previous_proof,
            previous_distribution_proof_payload,
            previous_notarization_summary_payload,
            previous_archive_size,
            previous_archive_sha256,
            generated_at,
            "previous_artifact",
        )
        _validate_authenticated_distribution_history(
            artifact_authentication, previous_authentication
        )
        authenticated_creation = artifact_authentication["artifact_created_at"]
        if not isinstance(authenticated_creation, datetime):
            raise MacPilotEvidenceError("artifact_github_artifact_created_at_invalid")
        if authenticated_creation > generated_at:
            raise MacPilotEvidenceError("artifact_created_after_evidence")
        artifact_created_at = authenticated_creation

    reasons: list[str] = []
    macos_operations_reason_count = len(reasons)
    architectures = _validate_clean_machines(
        root["clean_machine_runs"],
        artifact["archive_sha256"],
        artifact_created_at,
        generated_at,
        reasons,
    )
    _validate_lifecycle(
        root["lifecycle"], previous_artifact["build"], artifact["build"], reasons
    )
    _validate_client_recovery(root["client_auth_recovery"], artifact["archive_sha256"], reasons)
    if (
        evidence_kind == "pilot_qualification"
        and len(reasons) == macos_operations_reason_count
    ):
        if operations_url == "none":
            reasons.append("macos_operational_evidence_missing")
        else:
            _authenticate_macos_operational_evidence(
                operations_url,
                artifact,
                previous_artifact,
                root["clean_machine_runs"],
                root["lifecycle"],
                root["client_auth_recovery"],
                artifact_created_at,
                generated_at,
            )
    gateway_reason_count = len(reasons)
    gateway = _validate_hosted_gateway(root["hosted_gateway"], evidence_kind, reasons)
    if evidence_kind == "pilot_qualification" and len(reasons) == gateway_reason_count:
        deployment_run = _authenticate_github_run(
            gateway["deployment_evidence_url"],
            gateway["source_commit"],
            EXTERNAL_PILOT_WORKFLOW,
            "gateway_deployment",
        )
        recovery_run = _authenticate_github_run(
            gateway["recovery_evidence_url"],
            gateway["source_commit"],
            EXTERNAL_PILOT_WORKFLOW,
            "gateway_recovery",
        )
        _validate_gateway_run_timeline(
            deployment_run, recovery_run, generated_at
        )
        _authenticate_gateway_evidence_artifact(
            deployment_run,
            gateway,
            "deployment",
            "gateway_deployment",
        )
        _authenticate_gateway_evidence_artifact(
            recovery_run,
            gateway,
            "qualification",
            "gateway_recovery",
        )

    reviews = _require_fields(root["reviews"], _REVIEWS_FIELDS, "reviews")
    validated_reviews = {
        "security": _validate_review(
            reviews["security"],
            "security_review",
            artifact["archive_sha256"],
            artifact["source_commit"],
            artifact_created_at,
            generated_at,
            reasons,
        ),
        "accessibility": _validate_review(
            reviews["accessibility"],
            "accessibility_review",
            artifact["archive_sha256"],
            artifact["source_commit"],
            artifact_created_at,
            generated_at,
            reasons,
        ),
    }
    if artifact_authentication is not None:
        actor_logins = artifact_authentication["actor_logins"]
        if not isinstance(actor_logins, set):
            raise MacPilotEvidenceError("artifact_github_run_actor_invalid")
        for review_kind, review in validated_reviews.items():
            if (
                review["status"] == "passed"
                and review["independent_reviewer"] is True
                and review["reference_type"] == "public_issue_comment"
            ):
                _authenticate_review_reference(
                    review,
                    review_kind,
                    actor_logins,
                    generated_at,
                    f"{review_kind}_review",
                )

    attestations = _require_fields(root["operator_attestations"], _OPERATOR_FIELDS, "operator_attestations")
    attestation_checks = {
        field: _require_bool(attestations[field], f"attestation_{field}")
        for field in _OPERATOR_FIELDS
    }
    if not all(attestation_checks.values()):
        reasons.append("operator_attestations_incomplete")

    blockers = root["open_blockers"]
    if (
        not isinstance(blockers, list)
        or not all(isinstance(blocker, str) for blocker in blockers)
        or blockers != sorted(set(blockers))
        or not set(blockers) <= _BLOCKERS
    ):
        raise MacPilotEvidenceError("open_blockers_invalid")
    if blockers:
        reasons.append("open_blockers_present")
    if evidence_kind == "synthetic_test_fixture":
        reasons.append("synthetic_fixture")

    reasons = sorted(set(reasons))
    ready = evidence_kind == "pilot_qualification" and not reasons
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "ready_for_controlled_external_pilot" if ready else "not_ready",
        "ready_for_controlled_external_pilot": ready,
        "claim_scope": CLAIM_SCOPE,
        "artifact_sha256": artifact["archive_sha256"],
        "previous_artifact_sha256": previous_artifact["archive_sha256"],
        "source_commit": artifact["source_commit"],
        "clean_machine_architectures": architectures,
        "external_initial_completion_count": 0,
        "external_returning_completion_count": 0,
        "reasons": reasons,
        "nonclaims": [
            "not_external_human_validation",
            "not_multi_region",
            "not_zero_downtime",
            "not_availability_or_latency_sla",
            "not_customer_production_readiness",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--distribution-proof", type=Path, required=True)
    parser.add_argument("--notarization-summary", type=Path, required=True)
    parser.add_argument("--previous-archive", type=Path, required=True)
    parser.add_argument("--previous-distribution-proof", type=Path, required=True)
    parser.add_argument("--previous-notarization-summary", type=Path, required=True)
    parser.add_argument("--allow-synthetic-fixture", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        evidence_payload = _read_bounded_regular(args.evidence, _MAX_FILE_BYTES, "evidence")
        proof_payload = _read_bounded_regular(
            args.distribution_proof, _MAX_FILE_BYTES, "distribution_proof"
        )
        notarization_payload = _read_bounded_regular(
            args.notarization_summary, _MAX_FILE_BYTES, "notarization_summary"
        )
        previous_proof_payload = _read_bounded_regular(
            args.previous_distribution_proof,
            _MAX_FILE_BYTES,
            "previous_distribution_proof",
        )
        previous_notarization_payload = _read_bounded_regular(
            args.previous_notarization_summary,
            _MAX_FILE_BYTES,
            "previous_notarization_summary",
        )
        evidence = _parse_json(evidence_payload, "evidence")
        if (
            isinstance(evidence, dict)
            and evidence.get("evidence_kind") == "synthetic_test_fixture"
            and not args.allow_synthetic_fixture
        ):
            raise MacPilotEvidenceError("synthetic_fixture_not_allowed")
        with tempfile.TemporaryDirectory(prefix="hormuz-pilot-archives-") as temporary:
            snapshot_root = Path(temporary)
            archive_directory = snapshot_root / "candidate"
            previous_archive_directory = snapshot_root / "previous"
            archive_directory.mkdir(mode=0o700)
            previous_archive_directory.mkdir(mode=0o700)
            archive_path, archive_size, archive_sha256 = _snapshot_bounded_regular(
                args.archive, _MAX_ARCHIVE_BYTES, "archive", archive_directory
            )
            (
                previous_archive_path,
                previous_archive_size,
                previous_archive_sha256,
            ) = _snapshot_bounded_regular(
                args.previous_archive,
                _MAX_ARCHIVE_BYTES,
                "previous_archive",
                previous_archive_directory,
            )
            result = validate_evidence(
                evidence,
                distribution_proof=_parse_json(proof_payload, "distribution_proof"),
                distribution_proof_payload=proof_payload,
                notarization_summary=_parse_json(
                    notarization_payload, "notarization_summary"
                ),
                notarization_summary_payload=notarization_payload,
                archive_path=archive_path,
                archive_size=archive_size,
                archive_sha256=archive_sha256,
                previous_distribution_proof=_parse_json(
                    previous_proof_payload, "previous_distribution_proof"
                ),
                previous_distribution_proof_payload=previous_proof_payload,
                previous_notarization_summary=_parse_json(
                    previous_notarization_payload, "previous_notarization_summary"
                ),
                previous_notarization_summary_payload=previous_notarization_payload,
                previous_archive_path=previous_archive_path,
                previous_archive_size=previous_archive_size,
                previous_archive_sha256=previous_archive_sha256,
            )
    except MacPilotEvidenceError as error:
        print(f"macos_pilot_evidence=invalid code={error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["ready_for_controlled_external_pilot"]:
        return 0
    if result["reasons"] == ["synthetic_fixture"] and args.allow_synthetic_fixture:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
