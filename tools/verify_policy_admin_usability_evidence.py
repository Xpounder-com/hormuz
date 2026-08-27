#!/usr/bin/env python3
"""Validate content-free evidence for the v1 policy-administrator usability gate.

The contract intentionally has no fields for policy documents, request content,
credentials, personal identity, local paths, screenshots, logs, or free-form
feedback. Synthetic fixtures exercise the validator but can never satisfy the
human gate tracked by issue #173.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_ID = "hormuz.policy-admin-usability-evidence"
SCHEMA_VERSION = 1
GATE_ISSUE = "https://github.com/Xpounder-com/hormuz/issues/173"
EVIDENCE_KINDS = {"release_gate_evidence", "synthetic_test_fixture"}
# The v1 participant kit includes the protocol, configuration, examples, and
# evidence validator. Only the source archive ships that complete kit.
ARTIFACT_KINDS = {"source_archive"}
TRACKS = {"offline", "postgresql"}
OUTCOMES = {"completed", "blocked"}
STAGE_STATUSES = {"completed", "failed", "not_attempted"}
OFFLINE_STAGES = (
    "create_from_template",
    "modify_policy",
    "validate_policy",
    "compare_policy",
    "run_saved_scenarios",
    "evaluate_policy",
)
POSTGRESQL_STAGES = (
    "authenticate_administrator",
    "apply_policy",
    "verify_apply_state",
    "inspect_history",
    "rollback_policy",
    "verify_rollback_state",
)
GUIDANCE_SOURCES = {
    "shipped_documentation",
    "shipped_examples",
    "command_help",
    "other_public_material",
    "private_material",
}
ALLOWED_GUIDANCE_SOURCES = {
    "shipped_documentation",
    "shipped_examples",
    "command_help",
}
FRICTION_CATEGORIES = {
    "none",
    "command_discovery",
    "policy_authoring",
    "validation",
    "comparison",
    "scenario_execution",
    "evaluation",
    "authentication",
    "activation",
    "history",
    "rollback",
    "verification",
    "documentation",
    "installation",
    "other_bounded",
}
BLOCKER_REASONS = {
    "none",
    "published_guidance_failure",
    "misleading_success",
    "wrong_policy_state",
    "authentication_bypass",
    "history_inconsistency",
    "content_or_credential_exposure",
}
REFERENCE_TYPES = {"public_issue", "private_security_advisory"}
FINDING_STATUSES = {"open", "resolved"}
HISTORY_EVENT_TYPES = {"policy_staged", "policy_activated", "policy_rolled_back"}

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "gate_issue",
    "generated_at",
    "release",
    "operator_attestation",
    "sessions",
    "findings",
}
_RELEASE_FIELDS = {
    "version",
    "artifact_kind",
    "artifact_digest",
    "source_commit",
    "published_at",
}
_OPERATOR_FIELDS = {
    "distinct_humans_verified_off_repository",
    "identity_mapping_not_committed",
    "raw_intake_not_committed",
    "release_artifact_digest_verified",
}
_SESSION_FIELDS = {
    "session_id",
    "participant_id",
    "track",
    "release_artifact_digest",
    "started_at",
    "duration_seconds",
    "setup_excluded_from_duration",
    "independence",
    "guidance_usage",
    "stages",
    "outcome",
    "friction_category",
    "finding_id",
    "postgresql_verification",
    "content_free_attestations",
}
_INDEPENDENCE_FIELDS = {
    "workflow_author_or_reviewer",
    "prior_private_walkthrough",
    "assistance_count",
}
_GUIDANCE_FIELDS = {"sources", "lookup_count"}
_STAGE_FIELDS = {"name", "status"}
_CONTENT_FREE_FIELDS = {
    "policy_or_request_content_absent",
    "credential_or_token_absent",
    "personal_identity_absent",
    "identity_mapping_absent",
    "local_path_absent",
    "free_text_absent",
}
_POSTGRESQL_FIELDS = {"apply", "rollback", "history"}
_APPLY_FIELDS = {
    "previous_version_id",
    "previous_content_sha256",
    "previous_generation",
    "expected_version_id",
    "expected_content_sha256",
    "observed_version_id",
    "observed_content_sha256",
    "observed_generation",
}
_ROLLBACK_FIELDS = {
    "expected_version_id",
    "expected_content_sha256",
    "expected_predecessor_generation",
    "observed_version_id",
    "observed_content_sha256",
    "observed_generation",
}
_HISTORY_FIELDS = {
    "predecessor_event_type",
    "predecessor_version_id",
    "predecessor_content_sha256",
    "predecessor_generation",
    "apply_event_type",
    "apply_version_id",
    "apply_content_sha256",
    "apply_generation",
    "rollback_event_type",
    "rollback_version_id",
    "rollback_content_sha256",
    "rollback_generation",
}
_FINDING_FIELDS = {
    "finding_id",
    "origin_session_id",
    "track",
    "category",
    "blocker_reason",
    "reference_type",
    "reference",
    "status",
    "correction",
}
_CORRECTION_FIELDS = {
    "resolution_commit",
    "corrected_release_source_commit",
    "corrected_release_digest",
    "corrected_release_published_at",
    "resolution_commit_ancestor_verified",
    "automated_regression_url",
    "automated_regression_conclusion",
    "retest_session_id",
    "broad_workflow_change",
    "affected_tracks",
}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_SESSION_ID_RE = re.compile(rf"paus:{_UUID}\Z")
_PARTICIPANT_ID_RE = re.compile(rf"pau:{_UUID}\Z")
_FINDING_ID_RE = re.compile(rf"pauf:{_UUID}\Z")
_PRIVATE_ADVISORY_RE = re.compile(rf"private-advisory:{_UUID}\Z")
_ISSUE_RE = re.compile(r"https://github\.com/Xpounder-com/hormuz/issues/[1-9][0-9]*\Z")
_ACTIONS_RUN_RE = re.compile(r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]*\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTENT_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_V1_VERSION_RE = re.compile(
    r"v1\.0(?:\.(?:0|[1-9][0-9]*))?\Z"
)
_MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_GENERATION = 9_223_372_036_854_775_807


class PolicyAdminUsabilityEvidenceError(ValueError):
    """A fail-closed policy-administrator evidence contract violation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PolicyAdminUsabilityEvidenceError("duplicate_json_member")
        value[key] = item
    return value


def _read_evidence(path: Path) -> object:
    before_open = path.lstat()
    if stat.S_ISLNK(before_open.st_mode):
        raise PolicyAdminUsabilityEvidenceError("evidence_not_regular")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PolicyAdminUsabilityEvidenceError("evidence_not_regular")
        if (metadata.st_dev, metadata.st_ino) != (
            before_open.st_dev,
            before_open.st_ino,
        ):
            raise PolicyAdminUsabilityEvidenceError("evidence_changed_during_open")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            encoded = source.read(_MAX_EVIDENCE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(encoded) > _MAX_EVIDENCE_BYTES:
        raise PolicyAdminUsabilityEvidenceError("evidence_too_large")
    return json.loads(encoded.decode("utf-8"), object_pairs_hook=_strict_object)


def _require_fields(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_fields_invalid")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return value


def _require_int(value: object, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return value


def _require_enum(value: object, choices: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return value


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return value


def _require_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return parsed


def _require_sorted_enums(
    value: object,
    choices: set[str],
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    if any(not isinstance(item, str) or item not in choices for item in value):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    if value != sorted(set(value)):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return value


def _validate_release(value: object) -> tuple[dict[str, Any], datetime]:
    release = _require_fields(value, _RELEASE_FIELDS, "release")
    _require_pattern(release["version"], _V1_VERSION_RE, "release_version")
    _require_enum(release["artifact_kind"], ARTIFACT_KINDS, "release_artifact_kind")
    _require_pattern(release["artifact_digest"], _SHA256_RE, "release_artifact_digest")
    _require_pattern(release["source_commit"], _REVISION_RE, "release_source_commit")
    return release, _require_timestamp(release["published_at"], "release_published_at")


def _validate_independence(value: object, label: str) -> None:
    independence = _require_fields(value, _INDEPENDENCE_FIELDS, f"{label}_independence")
    _require_bool(
        independence["workflow_author_or_reviewer"],
        f"{label}_workflow_author_or_reviewer",
    )
    _require_bool(
        independence["prior_private_walkthrough"],
        f"{label}_prior_private_walkthrough",
    )
    _require_int(independence["assistance_count"], 0, 100, f"{label}_assistance_count")


def _validate_guidance(value: object, label: str) -> None:
    guidance = _require_fields(value, _GUIDANCE_FIELDS, f"{label}_guidance")
    sources = _require_sorted_enums(
        guidance["sources"],
        GUIDANCE_SOURCES,
        minimum=0,
        maximum=len(GUIDANCE_SOURCES),
        label=f"{label}_guidance_sources",
    )
    lookup_count = _require_int(
        guidance["lookup_count"], 0, 100, f"{label}_guidance_lookup_count"
    )
    if bool(sources) != (lookup_count > 0):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_guidance_usage_inconsistent")
    if lookup_count < len(sources):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_guidance_usage_inconsistent")


def _validate_stages(value: object, track: str, label: str) -> None:
    expected = OFFLINE_STAGES if track == "offline" else POSTGRESQL_STAGES
    if not isinstance(value, list) or len(value) != len(expected):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_stages_invalid")
    statuses: list[str] = []
    for index, (raw_stage, expected_name) in enumerate(zip(value, expected, strict=True)):
        stage = _require_fields(raw_stage, _STAGE_FIELDS, f"{label}_stage_{index}")
        if stage["name"] != expected_name:
            raise PolicyAdminUsabilityEvidenceError(f"{label}_stage_order_invalid")
        statuses.append(
            _require_enum(stage["status"], STAGE_STATUSES, f"{label}_stage_{index}_status")
        )
    if all(status == "completed" for status in statuses):
        return
    first_incomplete = next(index for index, status in enumerate(statuses) if status != "completed")
    if statuses[first_incomplete] != "failed" or any(
        status != "not_attempted" for status in statuses[first_incomplete + 1 :]
    ):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_stage_progression_invalid")


def _validate_policy_identity(version_id: object, content_sha256: object, label: str) -> None:
    version = _require_pattern(version_id, _SHA256_RE, f"{label}_version_id")
    digest = _require_pattern(content_sha256, _CONTENT_SHA256_RE, f"{label}_content_sha256")
    if version != f"sha256:{digest}":
        raise PolicyAdminUsabilityEvidenceError(f"{label}_identity_inconsistent")


def _validate_observed_policy_identity(
    version_id: object, content_sha256: object, label: str
) -> None:
    _require_pattern(version_id, _SHA256_RE, f"{label}_version_id")
    _require_pattern(content_sha256, _CONTENT_SHA256_RE, f"{label}_content_sha256")


def _validate_postgresql(value: object, label: str) -> None:
    verification = _require_fields(value, _POSTGRESQL_FIELDS, f"{label}_postgresql")
    apply = _require_fields(verification["apply"], _APPLY_FIELDS, f"{label}_apply")
    rollback = _require_fields(
        verification["rollback"], _ROLLBACK_FIELDS, f"{label}_rollback"
    )
    history = _require_fields(verification["history"], _HISTORY_FIELDS, f"{label}_history")

    _validate_policy_identity(
        apply["previous_version_id"],
        apply["previous_content_sha256"],
        f"{label}_apply_previous",
    )
    _validate_policy_identity(
        apply["expected_version_id"],
        apply["expected_content_sha256"],
        f"{label}_apply_expected",
    )
    _validate_observed_policy_identity(
        apply["observed_version_id"],
        apply["observed_content_sha256"],
        f"{label}_apply_observed",
    )
    _require_int(
        apply["previous_generation"],
        1,
        _MAX_GENERATION,
        f"{label}_apply_previous_generation",
    )
    _require_int(
        apply["observed_generation"],
        1,
        _MAX_GENERATION,
        f"{label}_apply_observed_generation",
    )
    if apply["previous_version_id"] == apply["expected_version_id"]:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_candidate_not_distinct")

    _validate_policy_identity(
        rollback["expected_version_id"],
        rollback["expected_content_sha256"],
        f"{label}_rollback_expected",
    )
    _validate_observed_policy_identity(
        rollback["observed_version_id"],
        rollback["observed_content_sha256"],
        f"{label}_rollback_observed",
    )
    _require_int(
        rollback["expected_predecessor_generation"],
        1,
        _MAX_GENERATION,
        f"{label}_rollback_predecessor_generation",
    )
    _require_int(
        rollback["observed_generation"],
        1,
        _MAX_GENERATION,
        f"{label}_rollback_observed_generation",
    )

    for prefix in ("predecessor", "apply", "rollback"):
        _require_enum(
            history[f"{prefix}_event_type"],
            HISTORY_EVENT_TYPES,
            f"{label}_history_{prefix}_event_type",
        )
        _validate_observed_policy_identity(
            history[f"{prefix}_version_id"],
            history[f"{prefix}_content_sha256"],
            f"{label}_history_{prefix}",
        )
        _require_int(
            history[f"{prefix}_generation"],
            1,
            _MAX_GENERATION,
            f"{label}_history_{prefix}_generation",
        )


def _postgresql_state_errors(session: dict[str, Any]) -> set[str]:
    verification = session["postgresql_verification"]
    if not isinstance(verification, dict):
        return {"wrong_policy_state", "history_inconsistency"}
    apply = verification["apply"]
    rollback = verification["rollback"]
    history = verification["history"]
    errors: set[str] = set()
    if (
        apply["observed_version_id"] != apply["expected_version_id"]
        or apply["observed_content_sha256"] != apply["expected_content_sha256"]
        or apply["observed_version_id"] != f"sha256:{apply['observed_content_sha256']}"
        or apply["observed_generation"] != apply["previous_generation"] + 1
        or rollback["expected_version_id"] != apply["previous_version_id"]
        or rollback["expected_content_sha256"] != apply["previous_content_sha256"]
        or rollback["expected_predecessor_generation"] != apply["previous_generation"]
        or rollback["observed_version_id"] != rollback["expected_version_id"]
        or rollback["observed_content_sha256"] != rollback["expected_content_sha256"]
        or rollback["observed_version_id"] != f"sha256:{rollback['observed_content_sha256']}"
        or rollback["observed_generation"] != apply["observed_generation"] + 1
    ):
        errors.add("wrong_policy_state")
    if (
        history["predecessor_event_type"] not in {"policy_activated", "policy_rolled_back"}
        or history["predecessor_version_id"] != apply["previous_version_id"]
        or history["predecessor_content_sha256"] != apply["previous_content_sha256"]
        or history["predecessor_generation"] != apply["previous_generation"]
        or history["apply_event_type"] != "policy_activated"
        or history["apply_version_id"] != apply["observed_version_id"]
        or history["apply_content_sha256"] != apply["observed_content_sha256"]
        or history["apply_generation"] != apply["observed_generation"]
        or history["rollback_event_type"] != "policy_rolled_back"
        or history["rollback_version_id"] != rollback["observed_version_id"]
        or history["rollback_content_sha256"] != rollback["observed_content_sha256"]
        or history["rollback_generation"] != rollback["observed_generation"]
    ):
        errors.add("history_inconsistency")
    return errors


def _actions_complete(session: dict[str, Any]) -> bool:
    return all(stage["status"] == "completed" for stage in session["stages"])


def _is_independent(session: dict[str, Any]) -> bool:
    independence = session["independence"]
    return (
        independence["workflow_author_or_reviewer"] is False
        and independence["prior_private_walkthrough"] is False
        and independence["assistance_count"] == 0
        and set(session["guidance_usage"]["sources"]) <= ALLOWED_GUIDANCE_SOURCES
    )


def _session_qualifies(session: dict[str, Any]) -> bool:
    return (
        session["outcome"] == "completed"
        and _actions_complete(session)
        and _is_independent(session)
        and (
            session["track"] == "offline"
            or not _postgresql_state_errors(session)
        )
    )


def _validate_session(value: object, index: int) -> dict[str, Any]:
    label = f"session_{index}"
    session = _require_fields(value, _SESSION_FIELDS, label)
    _require_pattern(session["session_id"], _SESSION_ID_RE, f"{label}_id")
    _require_pattern(session["participant_id"], _PARTICIPANT_ID_RE, f"{label}_participant")
    track = _require_enum(session["track"], TRACKS, f"{label}_track")
    _require_pattern(
        session["release_artifact_digest"], _SHA256_RE, f"{label}_release_digest"
    )
    _require_timestamp(session["started_at"], f"{label}_started_at")
    _require_int(session["duration_seconds"], 1, 86_400, f"{label}_duration")
    if session["setup_excluded_from_duration"] is not True:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_timing_boundary_invalid")
    _validate_independence(session["independence"], label)
    _validate_guidance(session["guidance_usage"], label)
    _validate_stages(session["stages"], track, label)
    outcome = _require_enum(session["outcome"], OUTCOMES, f"{label}_outcome")
    if outcome == "completed" and not _actions_complete(session):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_outcome_inconsistent")
    _require_enum(
        session["friction_category"], FRICTION_CATEGORIES, f"{label}_friction"
    )
    if session["finding_id"] is not None:
        _require_pattern(session["finding_id"], _FINDING_ID_RE, f"{label}_finding")
    if (session["friction_category"] == "none") != (session["finding_id"] is None):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_finding_inconsistent")

    verification = session["postgresql_verification"]
    if track == "offline":
        if verification is not None:
            raise PolicyAdminUsabilityEvidenceError(f"{label}_postgresql_unexpected")
    elif verification is not None:
        _validate_postgresql(verification, label)
    elif _actions_complete(session):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_postgresql_verification_missing")

    attestations = _require_fields(
        session["content_free_attestations"], _CONTENT_FREE_FIELDS, f"{label}_content_free"
    )
    if any(attestations[field] is not True for field in _CONTENT_FREE_FIELDS):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_content_free_invalid")
    if track == "postgresql" and outcome == "completed" and _postgresql_state_errors(session):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_outcome_inconsistent")
    return session


def _validate_correction(value: object, label: str) -> dict[str, Any]:
    correction = _require_fields(value, _CORRECTION_FIELDS, f"{label}_correction")
    _require_pattern(
        correction["resolution_commit"], _REVISION_RE, f"{label}_resolution_commit"
    )
    _require_pattern(
        correction["corrected_release_source_commit"],
        _REVISION_RE,
        f"{label}_corrected_release_source_commit",
    )
    _require_pattern(
        correction["corrected_release_digest"],
        _SHA256_RE,
        f"{label}_corrected_release_digest",
    )
    _require_timestamp(
        correction["corrected_release_published_at"], f"{label}_corrected_release_at"
    )
    if correction["resolution_commit_ancestor_verified"] is not True:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_resolution_commit_ancestor_not_verified"
        )
    _require_pattern(
        correction["automated_regression_url"],
        _ACTIONS_RUN_RE,
        f"{label}_automated_regression",
    )
    if correction["automated_regression_conclusion"] != "success":
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_automated_regression_conclusion_invalid"
        )
    _require_pattern(
        correction["retest_session_id"], _SESSION_ID_RE, f"{label}_retest_session"
    )
    _require_bool(
        correction["broad_workflow_change"], f"{label}_broad_workflow_change"
    )
    _require_sorted_enums(
        correction["affected_tracks"],
        TRACKS,
        minimum=1,
        maximum=len(TRACKS),
        label=f"{label}_affected_tracks",
    )
    return correction


def _validate_finding(value: object, index: int) -> dict[str, Any]:
    label = f"finding_{index}"
    finding = _require_fields(value, _FINDING_FIELDS, label)
    _require_pattern(finding["finding_id"], _FINDING_ID_RE, f"{label}_id")
    _require_pattern(
        finding["origin_session_id"], _SESSION_ID_RE, f"{label}_origin_session"
    )
    _require_enum(finding["track"], TRACKS, f"{label}_track")
    _require_enum(finding["category"], FRICTION_CATEGORIES - {"none"}, f"{label}_category")
    blocker_reason = _require_enum(
        finding["blocker_reason"], BLOCKER_REASONS, f"{label}_blocker"
    )
    reference_type = _require_enum(
        finding["reference_type"], REFERENCE_TYPES, f"{label}_reference_type"
    )
    if reference_type == "public_issue":
        _require_pattern(finding["reference"], _ISSUE_RE, f"{label}_reference")
    else:
        _require_pattern(
            finding["reference"], _PRIVATE_ADVISORY_RE, f"{label}_reference"
        )
        if blocker_reason not in {
            "authentication_bypass",
            "content_or_credential_exposure",
        }:
            raise PolicyAdminUsabilityEvidenceError(f"{label}_private_reference_invalid")
    status = _require_enum(finding["status"], FINDING_STATUSES, f"{label}_status")
    is_blocker = blocker_reason != "none"
    if status == "open" or not is_blocker:
        if finding["correction"] is not None:
            raise PolicyAdminUsabilityEvidenceError(f"{label}_correction_inconsistent")
    else:
        finding["correction"] = _validate_correction(finding["correction"], label)
    return finding


def validate_evidence(value: object) -> dict[str, object]:
    """Validate one aggregate and return its computed administrator gate result."""

    root = _require_fields(value, _ROOT_FIELDS, "root")
    if root["schema_id"] != SCHEMA_ID or root["schema_version"] != SCHEMA_VERSION:
        raise PolicyAdminUsabilityEvidenceError("schema_identity_invalid")
    evidence_kind = _require_enum(root["evidence_kind"], EVIDENCE_KINDS, "evidence_kind")
    if root["gate_issue"] != GATE_ISSUE:
        raise PolicyAdminUsabilityEvidenceError("gate_issue_invalid")
    generated_at = _require_timestamp(root["generated_at"], "generated_at")
    release, release_published_at = _validate_release(root["release"])
    if release_published_at > generated_at:
        raise PolicyAdminUsabilityEvidenceError("release_after_generation")

    attestation = _require_fields(root["operator_attestation"], _OPERATOR_FIELDS, "operator")
    for field in _OPERATOR_FIELDS:
        _require_bool(attestation[field], f"operator_{field}")

    raw_sessions = root["sessions"]
    if not isinstance(raw_sessions, list) or not 1 <= len(raw_sessions) <= 80:
        raise PolicyAdminUsabilityEvidenceError("sessions_invalid")
    sessions = [_validate_session(item, index) for index, item in enumerate(raw_sessions)]
    session_ids = [session["session_id"] for session in sessions]
    if len(session_ids) != len(set(session_ids)):
        raise PolicyAdminUsabilityEvidenceError("session_ids_duplicated")
    session_keys = [
        (session["participant_id"], session["track"], session["release_artifact_digest"])
        for session in sessions
    ]
    if len(session_keys) != len(set(session_keys)):
        raise PolicyAdminUsabilityEvidenceError("participant_track_release_duplicated")
    for session in sessions:
        started_at = _require_timestamp(session["started_at"], "session_started_at")
        if started_at > generated_at:
            raise PolicyAdminUsabilityEvidenceError("session_after_generation")
        if (
            session["release_artifact_digest"] == release["artifact_digest"]
            and started_at < release_published_at
        ):
            raise PolicyAdminUsabilityEvidenceError("session_before_release")

    raw_findings = root["findings"]
    if not isinstance(raw_findings, list) or len(raw_findings) > 100:
        raise PolicyAdminUsabilityEvidenceError("findings_invalid")
    findings = [_validate_finding(item, index) for index, item in enumerate(raw_findings)]
    finding_ids = [finding["finding_id"] for finding in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise PolicyAdminUsabilityEvidenceError("finding_ids_duplicated")
    sessions_by_id = {session["session_id"]: session for session in sessions}
    findings_by_id = {finding["finding_id"]: finding for finding in findings}

    for session in sessions:
        finding_id = session["finding_id"]
        if finding_id is None:
            if session["outcome"] == "blocked":
                raise PolicyAdminUsabilityEvidenceError("blocked_session_finding_missing")
            continue
        finding = findings_by_id.get(finding_id)
        if (
            finding is None
            or finding["origin_session_id"] != session["session_id"]
            or finding["track"] != session["track"]
            or finding["category"] != session["friction_category"]
        ):
            raise PolicyAdminUsabilityEvidenceError("session_finding_invalid")
        is_blocker = finding["blocker_reason"] != "none"
        if (session["outcome"] == "blocked") != is_blocker:
            raise PolicyAdminUsabilityEvidenceError("session_blocker_inconsistent")
        state_errors = (
            _postgresql_state_errors(session)
            if session["track"] == "postgresql"
            and session["postgresql_verification"] is not None
            else set()
        )
        if (
            state_errors
            and finding["blocker_reason"] not in state_errors
            and finding["blocker_reason"] != "misleading_success"
        ):
            raise PolicyAdminUsabilityEvidenceError("postgresql_blocker_inconsistent")

    for finding in findings:
        origin = sessions_by_id.get(finding["origin_session_id"])
        if origin is None or origin["finding_id"] != finding["finding_id"]:
            raise PolicyAdminUsabilityEvidenceError("finding_origin_invalid")
        correction = finding["correction"]
        if correction is None:
            continue
        if finding["track"] not in correction["affected_tracks"]:
            raise PolicyAdminUsabilityEvidenceError("finding_affected_tracks_invalid")
        if not correction["broad_workflow_change"] and correction["affected_tracks"] != [
            finding["track"]
        ]:
            raise PolicyAdminUsabilityEvidenceError("finding_affected_tracks_invalid")
        corrected_at = _require_timestamp(
            correction["corrected_release_published_at"], "corrected_release_at"
        )
        if corrected_at > generated_at:
            raise PolicyAdminUsabilityEvidenceError("correction_after_generation")
        if (
            correction["corrected_release_digest"] != release["artifact_digest"]
            or correction["corrected_release_source_commit"] != release["source_commit"]
            or corrected_at != release_published_at
        ):
            raise PolicyAdminUsabilityEvidenceError(
                "finding_correction_not_in_gated_release"
            )
        if (
            _require_timestamp(origin["started_at"], "origin_started_at") >= corrected_at
            or origin["release_artifact_digest"] == correction["corrected_release_digest"]
        ):
            raise PolicyAdminUsabilityEvidenceError("finding_correction_not_fresh")
        retest = sessions_by_id.get(correction["retest_session_id"])
        if (
            retest is None
            or retest["session_id"] == origin["session_id"]
            or retest["track"] != finding["track"]
            or retest["release_artifact_digest"] != correction["corrected_release_digest"]
            or _require_timestamp(retest["started_at"], "retest_started_at") <= corrected_at
            or not _session_qualifies(retest)
        ):
            raise PolicyAdminUsabilityEvidenceError("finding_retest_invalid")

    current_sessions = [
        session
        for session in sessions
        if session["release_artifact_digest"] == release["artifact_digest"]
    ]
    offline_sessions = [session for session in current_sessions if session["track"] == "offline"]
    postgresql_sessions = [
        session for session in current_sessions if session["track"] == "postgresql"
    ]
    successful_offline = [session for session in offline_sessions if _session_qualifies(session)]
    successful_postgresql = [
        session for session in postgresql_sessions if _session_qualifies(session)
    ]
    offline_within_15 = [
        session for session in successful_offline if session["duration_seconds"] <= 900
    ]
    offline_over_25 = [
        session for session in offline_sessions if session["duration_seconds"] > 1_500
    ]
    unresolved_blockers = [
        finding
        for finding in findings
        if finding["blocker_reason"] != "none" and finding["status"] == "open"
    ]
    resolved_blockers = [
        finding
        for finding in findings
        if finding["blocker_reason"] != "none" and finding["status"] == "resolved"
    ]
    broad_changes = [
        finding
        for finding in findings
        if finding["correction"] is not None
        and finding["correction"]["broad_workflow_change"] is True
    ]
    broad_tracks_not_rerun: set[str] = set()
    for finding in broad_changes:
        correction = finding["correction"]
        corrected_at = _require_timestamp(
            correction["corrected_release_published_at"], "corrected_release_at"
        )
        for track in correction["affected_tracks"]:
            track_sessions = [session for session in current_sessions if session["track"] == track]
            if (
                any(
                    _require_timestamp(session["started_at"], "session_started_at")
                    <= corrected_at
                    for session in track_sessions
                )
            ):
                broad_tracks_not_rerun.add(track)

    reasons: list[str] = []
    if evidence_kind != "release_gate_evidence":
        reasons.append("synthetic_fixture")
    if attestation["distinct_humans_verified_off_repository"] is not True:
        reasons.append("distinct_humans_not_attested")
    if attestation["identity_mapping_not_committed"] is not True:
        reasons.append("identity_mapping_boundary_not_attested")
    if attestation["raw_intake_not_committed"] is not True:
        reasons.append("raw_intake_boundary_not_attested")
    if attestation["release_artifact_digest_verified"] is not True:
        reasons.append("release_artifact_digest_not_attested")
    if len(offline_sessions) != 5:
        reasons.append("offline_participant_count_not_five")
    if len(successful_offline) != 5:
        reasons.append("offline_completion_count_not_five")
    if len(offline_within_15) < 4:
        reasons.append("offline_under_15_minute_count_below_four")
    if offline_over_25:
        reasons.append("offline_duration_over_25_minutes")
    if len(postgresql_sessions) != 3:
        reasons.append("postgresql_participant_count_not_three")
    if len(successful_postgresql) != 3:
        reasons.append("postgresql_completion_or_state_verification_incomplete")
    if unresolved_blockers:
        reasons.append("blocker_open")
    if broad_tracks_not_rerun:
        reasons.append("broad_workflow_gate_not_fully_rerun")

    ready = not reasons
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed_release_gate" if ready else "not_ready",
        "evidence_kind": evidence_kind,
        "gate_issue": GATE_ISSUE,
        "release_artifact_digest": release["artifact_digest"],
        "ready_for_v1_policy_admin_claim": ready,
        "offline_participant_count": len(offline_sessions),
        "offline_completed_unaided_count": len(successful_offline),
        "offline_within_15_minutes_count": len(offline_within_15),
        "offline_over_25_minutes_count": len(offline_over_25),
        "postgresql_participant_count": len(postgresql_sessions),
        "postgresql_completed_verified_count": len(successful_postgresql),
        "finding_count": len(findings),
        "unresolved_blocker_count": len(unresolved_blockers),
        "resolved_blocker_count": len(resolved_blockers),
        "broad_workflow_change_count": len(broad_changes),
        "reasons": reasons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate strict, content-free policy-administrator usability evidence."
    )
    parser.add_argument("evidence", type=Path)
    parser.add_argument(
        "--allow-synthetic-fixture",
        action="store_true",
        help="validate a synthetic contract fixture without treating it as human evidence",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value = _read_evidence(args.evidence)
        result = validate_evidence(value)
        if (
            result["evidence_kind"] == "synthetic_test_fixture"
            and not args.allow_synthetic_fixture
        ):
            raise PolicyAdminUsabilityEvidenceError(
                "synthetic_fixture_requires_explicit_flag"
            )
    except OSError:
        print("policy-admin usability evidence failed: evidence_unavailable", file=sys.stderr)
        return 2
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        print("policy-admin usability evidence failed: evidence_invalid_json", file=sys.stderr)
        return 2
    except PolicyAdminUsabilityEvidenceError as error:
        print(f"policy-admin usability evidence failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result["evidence_kind"] == "synthetic_test_fixture":
        return 0
    return 0 if result["ready_for_v1_policy_admin_claim"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
