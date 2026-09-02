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
import stat
import sys
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


SCHEMA_ID = "hormuz.macos-pilot-qualification"
SCHEMA_VERSION = 1
CLAIM_SCOPE = "signed_macos_controlled_external_pilot_readiness"
EVIDENCE_KINDS = {"pilot_qualification", "synthetic_test_fixture"}
PRODUCTION_BUNDLE_IDENTIFIER = "com.xpounder.hormuz"
SYNTHETIC_BUNDLE_IDENTIFIER = "com.example.hormuzpilot"

_MAX_FILE_BYTES = 1024 * 1024
_MAX_ARCHIVE_BYTES = 1024 * 1024 * 1024
_MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)

_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_TEAM_ID_RE = re.compile(r"[A-Z0-9]{10}\Z")
_VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_BUILD_RE = re.compile(r"[1-9][0-9]*\Z")
_BUNDLE_ID_RE = re.compile(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)+\Z")
_CLIENT_VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?\Z")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_RUN_ID_RE = re.compile(rf"mcr:{_UUID}\Z")
_SUBMISSION_ID_RE = re.compile(_UUID + r"\Z", re.IGNORECASE)
_ACTIONS_RUN_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]*\Z"
)
_ISSUE_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/issues/[1-9][0-9]*\Z"
)
_PRIVATE_REFERENCE_RE = re.compile(rf"private-(?:security-)?review:{_UUID}\Z")

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "generated_at",
    "claim_scope",
    "artifact",
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
_HOSTED_GATEWAY_FIELDS = {
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
_REVIEWS_FIELDS = {"security", "accessibility"}
_REVIEW_FIELDS = {
    "status",
    "independent_reviewer",
    "reference_type",
    "reference",
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


def _parse_json(payload: bytes, label: str) -> object:
    try:
        return json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
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


def _validate_distribution_proof(value: object, evidence_kind: str) -> dict[str, Any]:
    proof = _require_fields(value, _DISTRIBUTION_PROOF_FIELDS, "distribution_proof")
    _require_int(proof["schema_version"], 1, 1, "distribution_proof_schema_version")
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
            or team_id == "ABCDEFGHIJ"
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
) -> dict[str, Any]:
    artifact = _require_fields(value, _ARTIFACT_FIELDS, "artifact")
    _require_pattern(artifact["source_commit"], _REVISION_RE, "artifact_source_commit")
    _require_pattern(artifact["workflow_run_url"], _ACTIONS_RUN_RE, "artifact_workflow_run_url")
    _require_pattern(artifact["bundle_identifier"], _BUNDLE_ID_RE, "artifact_bundle_identifier")
    _require_pattern(artifact["version"], _VERSION_RE, "artifact_version")
    _require_pattern(artifact["build"], _BUILD_RE, "artifact_build")
    _require_pattern(artifact["team_identifier"], _TEAM_ID_RE, "artifact_team_identifier")
    _require_pattern(artifact["archive_sha256"], _SHA256_RE, "artifact_archive_sha256")
    _require_pattern(
        artifact["distribution_proof_sha256"], _SHA256_RE, "artifact_distribution_proof_sha256"
    )
    _require_pattern(
        artifact["notarization_summary_sha256"], _SHA256_RE, "artifact_notarization_summary_sha256"
    )
    _require_pattern(artifact["submission_id"], _SUBMISSION_ID_RE, "artifact_submission_id")
    _require_int(artifact["archive_bytes"], 1, _MAX_ARCHIVE_BYTES, "artifact_archive_bytes")
    expected_name = f"Hormuz-{proof['version']}-notarized.zip"
    expected = {
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
        raise MacPilotEvidenceError("artifact_proof_binding_invalid")
    if archive_path.name != expected_name:
        raise MacPilotEvidenceError("artifact_archive_name_invalid")
    if archive_size != proof["archive_bytes"] or archive_sha256 != proof["archive_sha256"]:
        raise MacPilotEvidenceError("artifact_archive_binding_invalid")
    return artifact


def _validate_clean_machines(
    value: object, artifact_sha256: str, generated_at: datetime, reasons: list[str]
) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
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


def _validate_lifecycle(value: object, artifact_build: str, reasons: list[str]) -> None:
    lifecycle = _require_fields(value, _LIFECYCLE_FIELDS, "lifecycle")
    source_build = int(_require_pattern(lifecycle["update_from_build"], _BUILD_RE, "update_from_build"))
    target_build = int(_require_pattern(lifecycle["update_to_build"], _BUILD_RE, "update_to_build"))
    rollback_build = int(_require_pattern(lifecycle["rollback_to_build"], _BUILD_RE, "rollback_to_build"))
    if not (target_build > source_build and rollback_build == source_build and str(target_build) == artifact_build):
        reasons.append("update_rollback_build_sequence_invalid")
    lifecycle_checks = {
        field: _require_bool(lifecycle[field], f"lifecycle_{field}")
        for field in _LIFECYCLE_TRUE_FIELDS
    }
    if not all(lifecycle_checks.values()):
        reasons.append("keychain_and_session_lifecycle_incomplete")


def _validate_client_recovery(value: object, artifact_sha256: str, reasons: list[str]) -> None:
    if not isinstance(value, list) or not 1 <= len(value) <= 2:
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


def _validate_hosted_gateway(value: object, reasons: list[str]) -> None:
    gateway = _require_fields(value, _HOSTED_GATEWAY_FIELDS, "hosted_gateway")
    _require_pattern(gateway["source_commit"], _REVISION_RE, "gateway_source_commit")
    _require_pattern(gateway["deployment_evidence_url"], _ACTIONS_RUN_RE, "deployment_evidence_url")
    _require_pattern(gateway["recovery_evidence_url"], _ACTIONS_RUN_RE, "recovery_evidence_url")
    protocols = gateway["provider_protocols"]
    if (
        not isinstance(protocols, list)
        or not protocols
        or not all(isinstance(protocol, str) for protocol in protocols)
        or protocols != sorted(set(protocols))
        or not set(protocols) <= {"openai", "anthropic"}
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
    if attempts <= live_requests or failover_links < 1 or failover_hops != 1:
        reasons.append("live_provider_failover_evidence_incomplete")
    if sla_claimed:
        reasons.append("unsupported_availability_sla_claimed")


def _validate_review(value: object, label: str, reasons: list[str]) -> None:
    review = _require_fields(value, _REVIEW_FIELDS, label)
    status = review["status"]
    reference_type = review["reference_type"]
    reference = review["reference"]
    independent = _require_bool(review["independent_reviewer"], f"{label}_independent_reviewer")
    if not isinstance(status, str) or status not in {"not_started", "failed", "passed"}:
        raise MacPilotEvidenceError(f"{label}_status_invalid")
    if reference_type == "none":
        if reference != "none" or status == "passed":
            raise MacPilotEvidenceError(f"{label}_reference_invalid")
    elif reference_type == "public_issue":
        _require_pattern(reference, _ISSUE_RE, f"{label}_reference")
    elif reference_type == "private_review":
        _require_pattern(reference, _PRIVATE_REFERENCE_RE, f"{label}_reference")
    else:
        raise MacPilotEvidenceError(f"{label}_reference_type_invalid")
    if status != "passed" or not independent:
        reasons.append(f"{label}_incomplete")


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

    reasons: list[str] = []
    architectures = _validate_clean_machines(
        root["clean_machine_runs"], artifact["archive_sha256"], generated_at, reasons
    )
    _validate_lifecycle(root["lifecycle"], artifact["build"], reasons)
    _validate_client_recovery(root["client_auth_recovery"], artifact["archive_sha256"], reasons)
    _validate_hosted_gateway(root["hosted_gateway"], reasons)

    reviews = _require_fields(root["reviews"], _REVIEWS_FIELDS, "reviews")
    _validate_review(reviews["security"], "security_review", reasons)
    _validate_review(reviews["accessibility"], "accessibility_review", reasons)

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
        archive_size, archive_sha256 = _digest_bounded_regular(
            args.archive, _MAX_ARCHIVE_BYTES, "archive"
        )
        evidence = _parse_json(evidence_payload, "evidence")
        if (
            isinstance(evidence, dict)
            and evidence.get("evidence_kind") == "synthetic_test_fixture"
            and not args.allow_synthetic_fixture
        ):
            raise MacPilotEvidenceError("synthetic_fixture_not_allowed")
        result = validate_evidence(
            evidence,
            distribution_proof=_parse_json(proof_payload, "distribution_proof"),
            distribution_proof_payload=proof_payload,
            notarization_summary=_parse_json(notarization_payload, "notarization_summary"),
            notarization_summary_payload=notarization_payload,
            archive_path=args.archive,
            archive_size=archive_size,
            archive_sha256=archive_sha256,
        )
    except MacPilotEvidenceError as error:
        print(f"macos_pilot_evidence=invalid code={error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["ready_for_controlled_external_pilot"]:
        return 0
    if "synthetic_fixture" in result["reasons"] and args.allow_synthetic_fixture:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
