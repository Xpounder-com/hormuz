#!/usr/bin/env python3
"""Validate exact-digest evidence for Hormuz's five-run v1 sandbox checkpoint.

This contract records deterministic internal execution only. It deliberately
has no participant, identity, PostgreSQL, feedback, policy-content, request-
content, credential, log, screenshot, hostname, or local-path fields.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


SCHEMA_ID = "hormuz.v1-internal-repeatability-evidence"
SCHEMA_VERSION = 1
RESULT_SCHEMA_ID = "hormuz.v1-internal-repeatability-result"
GATE_ISSUE = "https://github.com/Xpounder-com/hormuz/issues/173"
TARGET_VERSION = "v1.0.0"
PACKAGE_VERSION = "1.0.0"
CLAIM_SCOPE = "internal_offline_policy_workflow_repeatability"
EVIDENCE_KINDS = {"candidate_gate_evidence", "synthetic_test_fixture"}
EXPECTED_RUN_COUNT = 5
WORKFLOW_ID = "standard-output-cap-offline-v1"
STAGES = (
    "create_from_template",
    "modify_policy",
    "validate_policy",
    "compare_policy",
    "run_saved_scenarios",
    "evaluate_policy",
)
EXPECTED_EXIT_CODES = {
    "create_from_template": 0,
    "modify_policy": 0,
    "validate_policy": 0,
    "compare_policy": 1,
    "run_saved_scenarios": 0,
    "evaluate_policy": 1,
}
STAGE_STATUSES = {"completed", "failed", "not_attempted"}

BASELINE_ASSET_SHA256 = (
    "sha256:fbd93fa23264617604d80732dfb8821089a58d0cfa2c0af939f08dbe471cc826"
)
CANDIDATE_ASSET_SHA256 = (
    "sha256:9c5d162f4979c2497237517618cb7ce2a7bf9893d3d0eadec09a5e47293cd3d8"
)
SCENARIO_SUITE_ASSET_SHA256 = (
    "sha256:e62e9c6bc1df830c2049f59e1ab0804f0bcd5a50d1debb8dc1d6ac2fc6476f91"
)
BASELINE_CONTENT_SHA256 = (
    "9c0003887b437aa19de4274e8cb04a46ad402cbbfe06b02a16164c012500ea8a"
)
CANDIDATE_CONTENT_SHA256 = (
    "a160954974f6d63e5863dc787cd2f9343d924bdbd261f0b1906ca882c3bbf212"
)
SCENARIO_SUITE_CONTENT_SHA256 = (
    "792b02e08c5b3920898e85f9e828dc1cbc647cc8356e737546a35803f0e15ea9"
)

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "gate_issue",
    "generated_at",
    "candidate",
    "execution_attestation",
    "task",
    "runs",
}
_CANDIDATE_FIELDS = {
    "target_version",
    "artifact_kind",
    "artifact_digest",
    "source_commit",
    "frozen_at",
}
_ATTESTATION_FIELDS = {
    "automation_only",
    "human_participant_count",
    "postgresql_session_count",
    "all_invocation_runs_included",
    "one_archive_used_for_all_runs",
    "provider_credentials_unset",
    "policy_admin_credentials_unset",
    "no_external_usability_claim",
    "no_postgresql_state_claim",
}
_TASK_FIELDS = {
    "workflow_id",
    "repetition_count",
    "baseline_asset_sha256",
    "candidate_asset_sha256",
    "scenario_suite_asset_sha256",
    "baseline_content_sha256",
    "candidate_content_sha256",
    "scenario_suite_content_sha256",
    "change_path",
    "before",
    "after",
    "scenario_id",
    "usage_basis",
}
_RUN_FIELDS = {
    "run_id",
    "run_index",
    "candidate_artifact_digest",
    "started_at",
    "finished_at",
    "duration_seconds",
    "isolation",
    "stages",
    "outcome",
    "observed",
}
_ISOLATION_FIELDS = {
    "fresh_virtual_environment",
    "fresh_working_directory",
    "fresh_sqlite_database",
    "candidate_source_loaded_from_archive",
    "network_guard_enabled",
    "provider_credentials_unset",
    "policy_admin_credentials_unset",
    "setup_current_usage_zero",
}
_STAGE_FIELDS = {"name", "status", "exit_code"}
_OBSERVED_FIELDS = {
    "source_package_version",
    "baseline_version_id",
    "candidate_version_id",
    "suite_id",
    "comparison_exit_code",
    "comparison_change_count",
    "evaluation_exit_code",
    "evaluation_scenario_count",
    "evaluation_changed_count",
    "baseline_allowed",
    "candidate_allowed",
    "baseline_max_output_tokens",
    "candidate_max_output_tokens",
    "current_usage_zero",
}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_RUN_ID_RE = re.compile(rf"v1ir:{_UUID}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TIMESTAMP_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{6})?Z\Z"
)
_MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_MAX_RUN_SECONDS = 3600.0


class V1InternalRepeatabilityEvidenceError(ValueError):
    """A fail-closed internal-repeatability evidence violation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise V1InternalRepeatabilityEvidenceError("duplicate_json_member")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise V1InternalRepeatabilityEvidenceError("json_number_not_finite")


def _read_evidence(path: Path) -> object:
    try:
        before = path.lstat()
    except OSError as error:
        raise V1InternalRepeatabilityEvidenceError("evidence_unavailable") from error
    if not stat.S_ISREG(before.st_mode):
        raise V1InternalRepeatabilityEvidenceError("evidence_not_regular")
    if before.st_size < 1 or before.st_size > _MAX_EVIDENCE_BYTES:
        raise V1InternalRepeatabilityEvidenceError("evidence_size_invalid")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise V1InternalRepeatabilityEvidenceError("evidence_unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size != before.st_size
        ):
            raise V1InternalRepeatabilityEvidenceError("evidence_changed_during_open")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            payload = source.read(_MAX_EVIDENCE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) != before.st_size or len(payload) > _MAX_EVIDENCE_BYTES:
        raise V1InternalRepeatabilityEvidenceError("evidence_size_invalid")
    return json.loads(
        payload.decode("utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=_reject_json_constant,
    )


def _require_fields(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_fields_invalid")
    return value


def _require_int(value: object, minimum: int, maximum: int, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_invalid")
    return value


def _require_number(value: object, minimum: float, maximum: float, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise V1InternalRepeatabilityEvidenceError(f"{label}_invalid")
    number = float(value)
    if not minimum <= number <= maximum:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_invalid")
    return number


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_invalid")
    return value


def _require_timestamp(value: object, label: str) -> datetime:
    timestamp = _require_pattern(value, _TIMESTAMP_RE, label)
    try:
        parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_invalid") from error
    if parsed.tzinfo is None:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_invalid")
    return parsed.astimezone(UTC)


def task_contract() -> dict[str, object]:
    """Return the one bounded workflow that the v1 checkpoint accepts."""

    return {
        "workflow_id": WORKFLOW_ID,
        "repetition_count": EXPECTED_RUN_COUNT,
        "baseline_asset_sha256": BASELINE_ASSET_SHA256,
        "candidate_asset_sha256": CANDIDATE_ASSET_SHA256,
        "scenario_suite_asset_sha256": SCENARIO_SUITE_ASSET_SHA256,
        "baseline_content_sha256": BASELINE_CONTENT_SHA256,
        "candidate_content_sha256": CANDIDATE_CONTENT_SHA256,
        "scenario_suite_content_sha256": SCENARIO_SUITE_CONTENT_SHA256,
        "change_path": "policies.organization.max_output_tokens",
        "before": 16_000,
        "after": 4_000,
        "scenario_id": "output-cap",
        "usage_basis": "current",
    }


def execution_attestation() -> dict[str, object]:
    """Return the non-human, non-PostgreSQL claim boundary."""

    return {
        "automation_only": True,
        "human_participant_count": 0,
        "postgresql_session_count": 0,
        "all_invocation_runs_included": True,
        "one_archive_used_for_all_runs": True,
        "provider_credentials_unset": True,
        "policy_admin_credentials_unset": True,
        "no_external_usability_claim": True,
        "no_postgresql_state_claim": True,
    }


def expected_observation() -> dict[str, object]:
    return {
        "source_package_version": PACKAGE_VERSION,
        "baseline_version_id": f"sha256:{BASELINE_CONTENT_SHA256}",
        "candidate_version_id": f"sha256:{CANDIDATE_CONTENT_SHA256}",
        "suite_id": f"sha256:{SCENARIO_SUITE_CONTENT_SHA256}",
        "comparison_exit_code": 1,
        "comparison_change_count": 1,
        "evaluation_exit_code": 1,
        "evaluation_scenario_count": 1,
        "evaluation_changed_count": 1,
        "baseline_allowed": True,
        "candidate_allowed": True,
        "baseline_max_output_tokens": 16_000,
        "candidate_max_output_tokens": 4_000,
        "current_usage_zero": True,
    }


def _validate_stages(value: object, label: str) -> bool:
    if not isinstance(value, list) or len(value) != len(STAGES):
        raise V1InternalRepeatabilityEvidenceError(f"{label}_stages_invalid")
    statuses: list[str] = []
    for index, (item, expected_name) in enumerate(zip(value, STAGES, strict=True), start=1):
        stage = _require_fields(item, _STAGE_FIELDS, f"{label}_stage_{index}")
        if stage["name"] != expected_name or stage["status"] not in STAGE_STATUSES:
            raise V1InternalRepeatabilityEvidenceError(f"{label}_stage_{index}_invalid")
        status = str(stage["status"])
        exit_code = stage["exit_code"]
        if status == "not_attempted":
            if exit_code is not None:
                raise V1InternalRepeatabilityEvidenceError(
                    f"{label}_stage_{index}_exit_code_invalid"
                )
        else:
            actual = _require_int(exit_code, 0, 255, f"{label}_stage_{index}_exit_code")
            expected = EXPECTED_EXIT_CODES[expected_name]
            if (status == "completed" and actual != expected) or (
                status == "failed" and actual == expected
            ):
                raise V1InternalRepeatabilityEvidenceError(
                    f"{label}_stage_{index}_exit_code_invalid"
                )
        statuses.append(status)

    if all(status == "completed" for status in statuses):
        return True
    first_incomplete = next(index for index, status in enumerate(statuses) if status != "completed")
    if statuses[first_incomplete] != "failed" or any(
        status != "not_attempted" for status in statuses[first_incomplete + 1 :]
    ):
        raise V1InternalRepeatabilityEvidenceError(f"{label}_stage_order_invalid")
    return False


def _validate_run(
    value: object,
    *,
    index: int,
    artifact_digest: str,
    frozen_at: datetime,
    previous_finished_at: datetime | None,
) -> tuple[bool, datetime, str]:
    label = f"run_{index}"
    run = _require_fields(value, _RUN_FIELDS, label)
    run_id = _require_pattern(run["run_id"], _RUN_ID_RE, f"{label}_id")
    if run["run_index"] != index or run["candidate_artifact_digest"] != artifact_digest:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_binding_invalid")

    started_at = _require_timestamp(run["started_at"], f"{label}_started_at")
    finished_at = _require_timestamp(run["finished_at"], f"{label}_finished_at")
    if started_at < frozen_at or finished_at < started_at:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_chronology_invalid")
    if previous_finished_at is not None and started_at < previous_finished_at:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_chronology_invalid")
    duration = _require_number(run["duration_seconds"], 0.000001, _MAX_RUN_SECONDS, f"{label}_duration")
    measured = (finished_at - started_at).total_seconds()
    if abs(duration - measured) > 0.05:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_duration_mismatch")

    isolation = _require_fields(run["isolation"], _ISOLATION_FIELDS, f"{label}_isolation")
    if isolation != {field: True for field in _ISOLATION_FIELDS}:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_isolation_invalid")

    completed = _validate_stages(run["stages"], label)
    expected_outcome = "passed" if completed else "failed"
    if run["outcome"] != expected_outcome:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_outcome_invalid")
    if completed:
        observed = _require_fields(run["observed"], _OBSERVED_FIELDS, f"{label}_observed")
        if observed != expected_observation():
            raise V1InternalRepeatabilityEvidenceError(f"{label}_observation_invalid")
    elif run["observed"] is not None:
        raise V1InternalRepeatabilityEvidenceError(f"{label}_observation_invalid")
    return completed, finished_at, run_id


def validate_evidence(value: object) -> dict[str, object]:
    """Validate the strict contract and return its bounded release decision."""

    root = _require_fields(value, _ROOT_FIELDS, "evidence")
    if root["schema_id"] != SCHEMA_ID or root["schema_version"] != SCHEMA_VERSION:
        raise V1InternalRepeatabilityEvidenceError("evidence_schema_invalid")
    evidence_kind = root["evidence_kind"]
    if evidence_kind not in EVIDENCE_KINDS:
        raise V1InternalRepeatabilityEvidenceError("evidence_kind_invalid")
    if root["gate_issue"] != GATE_ISSUE:
        raise V1InternalRepeatabilityEvidenceError("gate_issue_invalid")

    candidate = _require_fields(root["candidate"], _CANDIDATE_FIELDS, "candidate")
    artifact_digest = _require_pattern(candidate["artifact_digest"], _DIGEST_RE, "candidate_digest")
    _require_pattern(candidate["source_commit"], _REVISION_RE, "candidate_source_commit")
    frozen_at = _require_timestamp(candidate["frozen_at"], "candidate_frozen_at")
    if (
        candidate["target_version"] != TARGET_VERSION
        or candidate["artifact_kind"] != "source_archive"
    ):
        raise V1InternalRepeatabilityEvidenceError("candidate_identity_invalid")

    attestation = _require_fields(
        root["execution_attestation"], _ATTESTATION_FIELDS, "execution_attestation"
    )
    if attestation != execution_attestation():
        raise V1InternalRepeatabilityEvidenceError("execution_attestation_invalid")
    task = _require_fields(root["task"], _TASK_FIELDS, "task")
    if task != task_contract():
        raise V1InternalRepeatabilityEvidenceError("task_contract_invalid")

    runs = root["runs"]
    if not isinstance(runs, list) or len(runs) != EXPECTED_RUN_COUNT:
        raise V1InternalRepeatabilityEvidenceError("run_count_invalid")
    passed = 0
    run_ids: set[str] = set()
    previous_finished_at: datetime | None = None
    for index, run in enumerate(runs, start=1):
        completed, previous_finished_at, run_id = _validate_run(
            run,
            index=index,
            artifact_digest=artifact_digest,
            frozen_at=frozen_at,
            previous_finished_at=previous_finished_at,
        )
        if run_id in run_ids:
            raise V1InternalRepeatabilityEvidenceError("run_id_duplicate")
        run_ids.add(run_id)
        passed += int(completed)

    generated_at = _require_timestamp(root["generated_at"], "generated_at")
    now = datetime.now(UTC)
    if (
        generated_at < frozen_at
        or previous_finished_at is None
        or generated_at < previous_finished_at
        or generated_at > now + _MAX_FUTURE_CLOCK_SKEW
    ):
        raise V1InternalRepeatabilityEvidenceError("generated_at_invalid")

    eligible = evidence_kind == "candidate_gate_evidence" and passed == EXPECTED_RUN_COUNT
    if evidence_kind == "synthetic_test_fixture":
        status = "synthetic_fixture_valid"
    else:
        status = "eligible_for_unchanged_promotion" if eligible else "not_ready"
    return {
        "schema_id": RESULT_SCHEMA_ID,
        "schema_version": 1,
        "status": status,
        "evidence_kind": evidence_kind,
        "gate_issue": GATE_ISSUE,
        "target_version": TARGET_VERSION,
        "candidate_artifact_digest": artifact_digest,
        "run_count": EXPECTED_RUN_COUNT,
        "passed_run_count": passed,
        "eligible_for_v1_0_0_promotion": eligible,
        "promotion_requires_exact_candidate_digest": True,
        "claim_scope": CLAIM_SCOPE,
        "nonclaims": [
            "external_human_usability",
            "postgresql_policy_state",
            "production_enterprise_readiness",
            "market_validation",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument(
        "--allow-synthetic-fixture",
        action="store_true",
        help="Validate the checked-in synthetic fixture without authorizing promotion",
    )
    args = parser.parse_args(argv)
    try:
        value = _read_evidence(args.evidence)
        if (
            isinstance(value, dict)
            and value.get("evidence_kind") == "synthetic_test_fixture"
            and not args.allow_synthetic_fixture
        ):
            raise V1InternalRepeatabilityEvidenceError(
                "synthetic_fixture_requires_explicit_permission"
            )
        result = validate_evidence(value)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        V1InternalRepeatabilityEvidenceError,
        RecursionError,
    ) as error:
        code = str(error) or "evidence_invalid"
        print(f"v1 internal repeatability evidence invalid: {code}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["evidence_kind"] == "synthetic_test_fixture":
        return 0
    return 0 if result["eligible_for_v1_0_0_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
