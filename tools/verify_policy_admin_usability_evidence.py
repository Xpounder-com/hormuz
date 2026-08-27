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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_ID = "hormuz.policy-admin-usability-evidence"
SCHEMA_VERSION = 2
GATE_ISSUE = "https://github.com/Xpounder-com/hormuz/issues/173"
EVIDENCE_KINDS = {"candidate_gate_evidence", "synthetic_test_fixture"}
TARGET_VERSION = "v1.0.0"
CLAIM_SCOPE = "administrator_workflow_usability_and_state_correctness"
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
REGRESSION_WORKFLOW_PATH = ".github/workflows/ci.yml"
_MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "gate_issue",
    "generated_at",
    "candidate",
    "operator_attestation",
    "sessions",
    "findings",
}
_CANDIDATE_FIELDS = {
    "target_version",
    "artifact_kind",
    "artifact_digest",
    "source_commit",
    "frozen_at",
}
_OPERATOR_FIELDS = {
    "distinct_humans_verified_off_repository",
    "cohorts_preregistered_before_testing",
    "all_started_sessions_included",
    "participant_replacement_absent",
    "identity_mapping_not_committed",
    "raw_intake_not_committed",
    "candidate_artifact_digest_verified",
}
_SESSION_FIELDS = {
    "session_id",
    "participant_id",
    "track",
    "candidate_artifact_digest",
    "started_at",
    "duration_seconds",
    "setup_excluded_from_duration",
    "independence",
    "guidance_usage",
    "stages",
    "outcome",
    "friction_categories",
    "finding_ids",
    "offline_verification",
    "postgresql_isolation",
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
_OFFLINE_VERIFICATION_FIELDS = {
    "baseline_asset_sha256",
    "scenario_suite_asset_sha256",
    "comparison",
    "evaluation",
}
_OFFLINE_COMPARISON_FIELDS = {
    "baseline_version_id",
    "baseline_content_sha256",
    "candidate_version_id",
    "candidate_content_sha256",
    "change_count",
    "change_type",
    "path",
    "before",
    "after",
}
_OFFLINE_EVALUATION_FIELDS = {
    "suite_id",
    "suite_content_sha256",
    "usage_basis",
    "current_usage_zero_attested",
    "baseline_version_id",
    "baseline_content_sha256",
    "candidate_version_id",
    "candidate_content_sha256",
    "scenario_count",
    "changed_count",
    "baseline_allowed_count",
    "candidate_allowed_count",
    "scenario_id",
    "scenario_changed",
    "baseline_allowed",
    "candidate_allowed",
    "baseline_max_output_tokens",
    "candidate_max_output_tokens",
}
_POSTGRESQL_ISOLATION_FIELDS = {
    "run_scope_id",
    "isolated_tenant_attested",
}
_POSTGRESQL_FIELDS = {"apply", "rollback", "history"}
_APPLY_FIELDS = {
    "previous_version_id",
    "previous_content_sha256",
    "previous_generation",
    "if_active_guard_used",
    "if_active_version_id",
    "expected_version_id",
    "expected_content_sha256",
    "observed_version_id",
    "observed_content_sha256",
    "observed_generation",
}
_ROLLBACK_FIELDS = {
    "if_active_guard_used",
    "if_active_version_id",
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
    "corrected_candidate_source_commit",
    "corrected_candidate_digest",
    "corrected_candidate_frozen_at",
    "resolution_commit_ancestor_verified",
    "automated_regression_url",
    "automated_regression_source_commit",
    "automated_regression_workflow_path",
    "automated_regression_binding_verified",
    "automated_regression_conclusion",
    "retest_session_id",
    "broad_workflow_change",
    "affected_tracks",
}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_SESSION_ID_RE = re.compile(rf"paus:{_UUID}\Z")
_PARTICIPANT_ID_RE = re.compile(rf"pau:{_UUID}\Z")
_RUN_SCOPE_ID_RE = re.compile(rf"pauscope:{_UUID}\Z")
_FINDING_ID_RE = re.compile(rf"pauf:{_UUID}\Z")
_PRIVATE_ADVISORY_RE = re.compile(rf"private-advisory:{_UUID}\Z")
_ISSUE_RE = re.compile(r"https://github\.com/Xpounder-com/hormuz/issues/[1-9][0-9]*\Z")
_ACTIONS_RUN_RE = re.compile(r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]*\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CONTENT_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_GENERATION = 9_223_372_036_854_775_807
# These identities bind a qualifying offline session to the exact public task
# assets and normalized semantic outputs shipped beside this validator. The
# regression suite recomputes them so an asset change cannot drift silently.
_OFFLINE_BASELINE_ASSET_SHA256 = (
    "sha256:fbd93fa23264617604d80732dfb8821089a58d0cfa2c0af939f08dbe471cc826"
)
_OFFLINE_SCENARIO_SUITE_ASSET_SHA256 = (
    "sha256:e62e9c6bc1df830c2049f59e1ab0804f0bcd5a50d1debb8dc1d6ac2fc6476f91"
)
_OFFLINE_BASELINE_CONTENT_SHA256 = (
    "9c0003887b437aa19de4274e8cb04a46ad402cbbfe06b02a16164c012500ea8a"
)
_OFFLINE_CANDIDATE_CONTENT_SHA256 = (
    "a160954974f6d63e5863dc787cd2f9343d924bdbd261f0b1906ca882c3bbf212"
)
_OFFLINE_SCENARIO_SUITE_CONTENT_SHA256 = (
    "792b02e08c5b3920898e85f9e828dc1cbc647cc8356e737546a35803f0e15ea9"
)
_POSTGRESQL_ONLY_BLOCKER_REASONS = {
    "authentication_bypass",
    "history_inconsistency",
    "wrong_policy_state",
}


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
    if not stat.S_ISREG(before_open.st_mode):
        raise PolicyAdminUsabilityEvidenceError("evidence_not_regular")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
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


def _require_sorted_patterns(
    value: object,
    pattern: re.Pattern[str],
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    if any(
        not isinstance(item, str) or pattern.fullmatch(item) is None
        for item in value
    ):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    if value != sorted(set(value)):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_invalid")
    return value


def _validate_candidate(value: object) -> tuple[dict[str, Any], datetime]:
    candidate = _require_fields(value, _CANDIDATE_FIELDS, "candidate")
    if candidate["target_version"] != TARGET_VERSION:
        raise PolicyAdminUsabilityEvidenceError("candidate_target_version_invalid")
    _require_enum(
        candidate["artifact_kind"], ARTIFACT_KINDS, "candidate_artifact_kind"
    )
    _require_pattern(
        candidate["artifact_digest"], _SHA256_RE, "candidate_artifact_digest"
    )
    _require_pattern(
        candidate["source_commit"], _REVISION_RE, "candidate_source_commit"
    )
    return candidate, _require_timestamp(candidate["frozen_at"], "candidate_frozen_at")


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


def _validate_offline(value: object, label: str) -> None:
    verification = _require_fields(
        value,
        _OFFLINE_VERIFICATION_FIELDS,
        f"{label}_offline_verification",
    )
    baseline_asset = _require_pattern(
        verification["baseline_asset_sha256"],
        _SHA256_RE,
        f"{label}_offline_baseline_asset",
    )
    if baseline_asset != _OFFLINE_BASELINE_ASSET_SHA256:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_offline_baseline_asset_unexpected"
        )
    scenario_asset = _require_pattern(
        verification["scenario_suite_asset_sha256"],
        _SHA256_RE,
        f"{label}_offline_scenario_asset",
    )
    if scenario_asset != _OFFLINE_SCENARIO_SUITE_ASSET_SHA256:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_offline_scenario_asset_unexpected"
        )

    comparison = _require_fields(
        verification["comparison"],
        _OFFLINE_COMPARISON_FIELDS,
        f"{label}_offline_comparison",
    )
    _validate_policy_identity(
        comparison["baseline_version_id"],
        comparison["baseline_content_sha256"],
        f"{label}_offline_comparison_baseline",
    )
    _validate_policy_identity(
        comparison["candidate_version_id"],
        comparison["candidate_content_sha256"],
        f"{label}_offline_comparison_candidate",
    )
    if (
        comparison["baseline_content_sha256"]
        != _OFFLINE_BASELINE_CONTENT_SHA256
        or comparison["candidate_content_sha256"]
        != _OFFLINE_CANDIDATE_CONTENT_SHA256
    ):
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_offline_comparison_identity_unexpected"
        )
    _require_int(
        comparison["change_count"],
        1,
        1,
        f"{label}_offline_comparison_change_count",
    )
    _require_enum(
        comparison["change_type"],
        {"changed"},
        f"{label}_offline_comparison_change_type",
    )
    _require_enum(
        comparison["path"],
        {"policies.organization.max_output_tokens"},
        f"{label}_offline_comparison_path",
    )
    _require_int(
        comparison["before"],
        16_000,
        16_000,
        f"{label}_offline_comparison_before",
    )
    _require_int(
        comparison["after"],
        4_000,
        4_000,
        f"{label}_offline_comparison_after",
    )

    evaluation = _require_fields(
        verification["evaluation"],
        _OFFLINE_EVALUATION_FIELDS,
        f"{label}_offline_evaluation",
    )
    _validate_policy_identity(
        evaluation["suite_id"],
        evaluation["suite_content_sha256"],
        f"{label}_offline_evaluation_suite",
    )
    _validate_policy_identity(
        evaluation["baseline_version_id"],
        evaluation["baseline_content_sha256"],
        f"{label}_offline_evaluation_baseline",
    )
    _validate_policy_identity(
        evaluation["candidate_version_id"],
        evaluation["candidate_content_sha256"],
        f"{label}_offline_evaluation_candidate",
    )
    if (
        evaluation["suite_content_sha256"]
        != _OFFLINE_SCENARIO_SUITE_CONTENT_SHA256
        or evaluation["baseline_content_sha256"]
        != _OFFLINE_BASELINE_CONTENT_SHA256
        or evaluation["candidate_content_sha256"]
        != _OFFLINE_CANDIDATE_CONTENT_SHA256
    ):
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_offline_evaluation_identity_unexpected"
        )
    _require_enum(
        evaluation["usage_basis"],
        {"current"},
        f"{label}_offline_evaluation_usage_basis",
    )
    if evaluation["current_usage_zero_attested"] is not True:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_offline_evaluation_usage_invalid"
        )
    for field, expected in (
        ("scenario_count", 1),
        ("changed_count", 1),
        ("baseline_allowed_count", 1),
        ("candidate_allowed_count", 1),
        ("baseline_max_output_tokens", 16_000),
        ("candidate_max_output_tokens", 4_000),
    ):
        _require_int(
            evaluation[field],
            expected,
            expected,
            f"{label}_offline_evaluation_{field}",
        )
    _require_enum(
        evaluation["scenario_id"],
        {"output-cap"},
        f"{label}_offline_evaluation_scenario_id",
    )
    for field in ("scenario_changed", "baseline_allowed", "candidate_allowed"):
        if evaluation[field] is not True:
            raise PolicyAdminUsabilityEvidenceError(
                f"{label}_offline_evaluation_{field}_invalid"
            )


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
    apply_guard_used = _require_bool(
        apply["if_active_guard_used"], f"{label}_apply_if_active_guard_used"
    )
    if apply_guard_used:
        _require_pattern(
            apply["if_active_version_id"],
            _SHA256_RE,
            f"{label}_apply_if_active_version_id",
        )
    elif apply["if_active_version_id"] is not None:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_apply_if_active_guard_inconsistent"
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
    rollback_guard_used = _require_bool(
        rollback["if_active_guard_used"],
        f"{label}_rollback_if_active_guard_used",
    )
    if rollback_guard_used:
        _require_pattern(
            rollback["if_active_version_id"],
            _SHA256_RE,
            f"{label}_rollback_if_active_version_id",
        )
    elif rollback["if_active_version_id"] is not None:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_rollback_if_active_guard_inconsistent"
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


def _postgresql_guards_valid(session: dict[str, Any]) -> bool:
    verification = session["postgresql_verification"]
    if not isinstance(verification, dict):
        return False
    apply = verification["apply"]
    rollback = verification["rollback"]
    return (
        apply["if_active_guard_used"] is True
        and apply["if_active_version_id"] == apply["previous_version_id"]
        and rollback["if_active_guard_used"] is True
        and rollback["if_active_version_id"] == apply["observed_version_id"]
    )


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
            or (
                not _postgresql_state_errors(session)
                and _postgresql_guards_valid(session)
            )
        )
    )


def _validate_session(value: object, index: int) -> dict[str, Any]:
    label = f"session_{index}"
    session = _require_fields(value, _SESSION_FIELDS, label)
    _require_pattern(session["session_id"], _SESSION_ID_RE, f"{label}_id")
    _require_pattern(session["participant_id"], _PARTICIPANT_ID_RE, f"{label}_participant")
    track = _require_enum(session["track"], TRACKS, f"{label}_track")
    _require_pattern(
        session["candidate_artifact_digest"],
        _SHA256_RE,
        f"{label}_candidate_digest",
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
    friction_categories = _require_sorted_enums(
        session["friction_categories"],
        FRICTION_CATEGORIES,
        minimum=1,
        maximum=len(FRICTION_CATEGORIES),
        label=f"{label}_friction_categories",
    )
    finding_ids = _require_sorted_patterns(
        session["finding_ids"],
        _FINDING_ID_RE,
        minimum=0,
        maximum=20,
        label=f"{label}_finding_ids",
    )
    if (friction_categories == ["none"]) != (not finding_ids):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_finding_inconsistent")
    if "none" in friction_categories and friction_categories != ["none"]:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_finding_inconsistent")

    offline_verification = session["offline_verification"]
    verification = session["postgresql_verification"]
    if track == "offline":
        if session["postgresql_isolation"] is not None:
            raise PolicyAdminUsabilityEvidenceError(
                f"{label}_postgresql_isolation_unexpected"
            )
        if verification is not None:
            raise PolicyAdminUsabilityEvidenceError(f"{label}_postgresql_unexpected")
        if offline_verification is not None:
            _validate_offline(offline_verification, label)
        if _actions_complete(session) != (offline_verification is not None):
            raise PolicyAdminUsabilityEvidenceError(
                f"{label}_offline_verification_inconsistent"
            )
    else:
        if offline_verification is not None:
            raise PolicyAdminUsabilityEvidenceError(
                f"{label}_offline_verification_unexpected"
            )
        isolation = _require_fields(
            session["postgresql_isolation"],
            _POSTGRESQL_ISOLATION_FIELDS,
            f"{label}_postgresql_isolation",
        )
        _require_pattern(
            isolation["run_scope_id"],
            _RUN_SCOPE_ID_RE,
            f"{label}_postgresql_run_scope",
        )
        if isolation["isolated_tenant_attested"] is not True:
            raise PolicyAdminUsabilityEvidenceError(
                f"{label}_postgresql_isolation_not_attested"
            )
        if verification is not None:
            _validate_postgresql(verification, label)
        elif _actions_complete(session):
            raise PolicyAdminUsabilityEvidenceError(
                f"{label}_postgresql_verification_missing"
            )

    attestations = _require_fields(
        session["content_free_attestations"], _CONTENT_FREE_FIELDS, f"{label}_content_free"
    )
    if any(attestations[field] is not True for field in _CONTENT_FREE_FIELDS):
        raise PolicyAdminUsabilityEvidenceError(f"{label}_content_free_invalid")
    if track == "postgresql" and outcome == "completed":
        if _postgresql_state_errors(session) or not _postgresql_guards_valid(session):
            raise PolicyAdminUsabilityEvidenceError(f"{label}_outcome_inconsistent")
    return session


def _validate_correction(value: object, label: str) -> dict[str, Any]:
    correction = _require_fields(value, _CORRECTION_FIELDS, f"{label}_correction")
    _require_pattern(
        correction["resolution_commit"], _REVISION_RE, f"{label}_resolution_commit"
    )
    _require_pattern(
        correction["corrected_candidate_source_commit"],
        _REVISION_RE,
        f"{label}_corrected_candidate_source_commit",
    )
    _require_pattern(
        correction["corrected_candidate_digest"],
        _SHA256_RE,
        f"{label}_corrected_candidate_digest",
    )
    _require_timestamp(
        correction["corrected_candidate_frozen_at"], f"{label}_corrected_candidate_at"
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
    _require_pattern(
        correction["automated_regression_source_commit"],
        _REVISION_RE,
        f"{label}_automated_regression_source_commit",
    )
    if correction["automated_regression_workflow_path"] != REGRESSION_WORKFLOW_PATH:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_automated_regression_workflow_invalid"
        )
    if correction["automated_regression_binding_verified"] is not True:
        raise PolicyAdminUsabilityEvidenceError(
            f"{label}_automated_regression_binding_not_verified"
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
    track = _require_enum(finding["track"], TRACKS, f"{label}_track")
    _require_enum(finding["category"], FRICTION_CATEGORIES - {"none"}, f"{label}_category")
    blocker_reason = _require_enum(
        finding["blocker_reason"], BLOCKER_REASONS, f"{label}_blocker"
    )
    if track == "offline" and blocker_reason in _POSTGRESQL_ONLY_BLOCKER_REASONS:
        raise PolicyAdminUsabilityEvidenceError(f"{label}_blocker_track_invalid")
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
    validation_time = datetime.now(timezone.utc).replace(tzinfo=None)
    if generated_at > validation_time + _MAX_FUTURE_CLOCK_SKEW:
        raise PolicyAdminUsabilityEvidenceError("generation_in_future")
    candidate, candidate_frozen_at = _validate_candidate(root["candidate"])
    if candidate_frozen_at > generated_at:
        raise PolicyAdminUsabilityEvidenceError("candidate_frozen_after_generation")

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
        (session["participant_id"], session["track"], session["candidate_artifact_digest"])
        for session in sessions
    ]
    if len(session_keys) != len(set(session_keys)):
        raise PolicyAdminUsabilityEvidenceError("participant_track_candidate_duplicated")
    postgresql_scope_ids = [
        session["postgresql_isolation"]["run_scope_id"]
        for session in sessions
        if session["track"] == "postgresql"
    ]
    if len(postgresql_scope_ids) != len(set(postgresql_scope_ids)):
        raise PolicyAdminUsabilityEvidenceError("postgresql_run_scope_ids_duplicated")
    participant_intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    session_intervals: dict[str, tuple[datetime, datetime]] = {}
    for session in sessions:
        started_at = _require_timestamp(session["started_at"], "session_started_at")
        if started_at > generated_at:
            raise PolicyAdminUsabilityEvidenceError("session_after_generation")
        try:
            completed_at = started_at + timedelta(seconds=session["duration_seconds"])
        except OverflowError as error:
            raise PolicyAdminUsabilityEvidenceError(
                "session_ends_after_generation"
            ) from error
        if completed_at > generated_at:
            raise PolicyAdminUsabilityEvidenceError("session_ends_after_generation")
        participant_intervals.setdefault(session["participant_id"], []).append(
            (started_at, completed_at)
        )
        session_intervals[session["session_id"]] = (started_at, completed_at)
        if (
            session["candidate_artifact_digest"] == candidate["artifact_digest"]
            and started_at < candidate_frozen_at
        ):
            raise PolicyAdminUsabilityEvidenceError("session_before_candidate_frozen")
    for intervals in participant_intervals.values():
        ordered = sorted(intervals)
        for (_, previous_end), (current_start, _) in zip(
            ordered,
            ordered[1:],
            strict=False,
        ):
            if current_start < previous_end:
                raise PolicyAdminUsabilityEvidenceError(
                    "participant_sessions_overlap"
                )

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
        linked_findings: list[dict[str, Any]] = []
        for finding_id in session["finding_ids"]:
            finding = findings_by_id.get(finding_id)
            if (
                finding is None
                or finding["origin_session_id"] != session["session_id"]
                or finding["track"] != session["track"]
            ):
                raise PolicyAdminUsabilityEvidenceError("session_finding_invalid")
            linked_findings.append(finding)
        expected_categories = (
            sorted({finding["category"] for finding in linked_findings})
            if linked_findings
            else ["none"]
        )
        if session["friction_categories"] != expected_categories:
            raise PolicyAdminUsabilityEvidenceError("session_finding_invalid")
        blocker_reasons = {
            finding["blocker_reason"]
            for finding in linked_findings
            if finding["blocker_reason"] != "none"
        }
        if (session["outcome"] == "blocked") != bool(blocker_reasons):
            raise PolicyAdminUsabilityEvidenceError("session_blocker_inconsistent")
        state_errors = (
            _postgresql_state_errors(session)
            if session["track"] == "postgresql"
            and session["postgresql_verification"] is not None
            else set()
        )
        if not state_errors <= blocker_reasons:
            raise PolicyAdminUsabilityEvidenceError("postgresql_blocker_inconsistent")

    for finding in findings:
        origin = sessions_by_id.get(finding["origin_session_id"])
        if origin is None or finding["finding_id"] not in origin["finding_ids"]:
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
            correction["corrected_candidate_frozen_at"], "corrected_candidate_at"
        )
        if corrected_at > generated_at:
            raise PolicyAdminUsabilityEvidenceError("correction_after_generation")
        if (
            correction["corrected_candidate_digest"] != candidate["artifact_digest"]
            or correction["corrected_candidate_source_commit"]
            != candidate["source_commit"]
            or corrected_at != candidate_frozen_at
        ):
            raise PolicyAdminUsabilityEvidenceError(
                "finding_correction_not_in_gated_candidate"
            )
        if correction["automated_regression_source_commit"] != candidate["source_commit"]:
            raise PolicyAdminUsabilityEvidenceError(
                "finding_regression_not_for_gated_candidate"
            )
        if (
            _require_timestamp(origin["started_at"], "origin_started_at") >= corrected_at
            or origin["candidate_artifact_digest"]
            == correction["corrected_candidate_digest"]
        ):
            raise PolicyAdminUsabilityEvidenceError("finding_correction_not_fresh")
        retest = sessions_by_id.get(correction["retest_session_id"])
        if (
            retest is None
            or retest["session_id"] == origin["session_id"]
            or retest["track"] != finding["track"]
            or retest["candidate_artifact_digest"]
            != correction["corrected_candidate_digest"]
            or _require_timestamp(retest["started_at"], "retest_started_at") <= corrected_at
            or not _session_qualifies(retest)
        ):
            raise PolicyAdminUsabilityEvidenceError("finding_retest_invalid")

    current_sessions = [
        session
        for session in sessions
        if session["candidate_artifact_digest"] == candidate["artifact_digest"]
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
            correction["corrected_candidate_frozen_at"], "corrected_candidate_at"
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
    offline_gate_complete = (
        len(offline_sessions) == 5
        and len(successful_offline) == 5
        and len(offline_within_15) >= 4
        and not offline_over_25
        and "offline" not in broad_tracks_not_rerun
        and not any(
            finding["track"] == "offline"
            and finding["blocker_reason"] != "none"
            and finding["status"] == "open"
            for finding in findings
        )
    )
    postgresql_after_offline = True
    if postgresql_sessions:
        postgresql_after_offline = offline_gate_complete and min(
            session_intervals[session["session_id"]][0]
            for session in postgresql_sessions
        ) >= max(
            session_intervals[session["session_id"]][1]
            for session in offline_sessions
        )

    if evidence_kind != "candidate_gate_evidence":
        reasons.append("synthetic_fixture")
    if attestation["distinct_humans_verified_off_repository"] is not True:
        reasons.append("distinct_humans_not_attested")
    if attestation["cohorts_preregistered_before_testing"] is not True:
        reasons.append("cohorts_not_preregistered_before_testing")
    if attestation["all_started_sessions_included"] is not True:
        reasons.append("started_sessions_not_fully_attested")
    if attestation["participant_replacement_absent"] is not True:
        reasons.append("participant_replacement_not_ruled_out")
    if attestation["identity_mapping_not_committed"] is not True:
        reasons.append("identity_mapping_boundary_not_attested")
    if attestation["raw_intake_not_committed"] is not True:
        reasons.append("raw_intake_boundary_not_attested")
    if attestation["candidate_artifact_digest_verified"] is not True:
        reasons.append("candidate_artifact_digest_not_attested")
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
    if not postgresql_after_offline:
        reasons.append("postgresql_started_before_offline_gate_completed")
    if unresolved_blockers:
        reasons.append("blocker_open")
    if broad_tracks_not_rerun:
        reasons.append("broad_workflow_gate_not_fully_rerun")

    ready = not reasons
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "eligible_for_unchanged_promotion" if ready else "not_ready",
        "evidence_kind": evidence_kind,
        "gate_issue": GATE_ISSUE,
        "target_version": candidate["target_version"],
        "candidate_artifact_digest": candidate["artifact_digest"],
        "eligible_for_v1_0_0_promotion": ready,
        "promotion_requires_exact_candidate_digest": True,
        "claim_scope": CLAIM_SCOPE,
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
    except PolicyAdminUsabilityEvidenceError as error:
        print(f"policy-admin usability evidence failed: {error}", file=sys.stderr)
        return 2
    except (UnicodeError, ValueError, RecursionError):
        print("policy-admin usability evidence failed: evidence_invalid_json", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result["evidence_kind"] == "synthetic_test_fixture":
        return 0
    return 0 if result["eligible_for_v1_0_0_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
