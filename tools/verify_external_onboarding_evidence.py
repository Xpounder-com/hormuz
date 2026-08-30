#!/usr/bin/env python3
"""Validate content-free evidence for Hormuz v1.0.0 external onboarding.

The contract records only bounded artifact, environment, timing, completion,
and finding metadata. It has no fields for names, handles, email addresses,
prompts, responses, credentials, customer data, local paths, logs, screenshots,
or free-form feedback. Synthetic fixtures exercise the validator but can never
count as people or satisfy the external onboarding milestone in issue #110.
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


SCHEMA_ID = "hormuz.external-onboarding-evidence"
SCHEMA_VERSION = 1
GATE_ISSUE = "https://github.com/Xpounder-com/hormuz/issues/110"
PROGRAM = "hormuz-v1.0.0-external-onboarding"
CLAIM_SCOPE = "independent_installation_and_provider_free_demo_usability"

TARGET_VERSION = "v1.0.0"
PACKAGE_VERSION = "1.0.0"
ARTIFACT_KIND = "source_archive"
ARTIFACT_NAME = "hormuz-1.0.0.tar.gz"
ARTIFACT_DIGEST = (
    "sha256:2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a"
)
ARTIFACT_SIZE_BYTES = 895_460
SOURCE_COMMIT = "2fc0605252e41f731c85cc9146fbff6eb3b34669"
FROZEN_AT = "2026-08-29T15:46:33Z"
CUSTODY_TAG = (
    "candidate-v1.0.0-"
    "2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a"
)
RELEASE_URL = (
    "https://github.com/Xpounder-com/hormuz/releases/tag/"
    "candidate-v1.0.0-"
    "2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a"
)

EVIDENCE_KINDS = {"external_onboarding_evidence", "synthetic_test_fixture"}
PERSONAS = {"developer", "security", "platform", "engineering_admin"}
INSTALLATION_METHODS = {"source_archive"}
OS_FAMILIES = {"linux", "macos", "windows", "wsl"}
ARCHITECTURES = {"x86_64", "arm64"}
STATUSES = {"passed", "failed", "not_attempted"}
ASSISTANCE_LEVELS = {
    "none",
    "shipped_material_only",
    "maintainer_or_private_help",
}
GUIDANCE_SOURCES = {
    "shipped_readme",
    "shipped_documentation",
    "command_help",
    "other_public_material",
    "private_material",
}
QUALIFYING_GUIDANCE_SOURCES = {
    "shipped_readme",
    "shipped_documentation",
    "command_help",
}
FAILURE_CODES = {
    "none",
    "install_dependency",
    "unsupported_platform",
    "command_not_found",
    "demo_policy",
    "demo_network_boundary",
    "demo_evidence",
    "documentation",
    "security",
    "other_bounded",
}
FRICTION_CATEGORIES = {
    "none",
    "installation",
    "command_discovery",
    "documentation",
    "compatibility",
    "demo_policy",
    "demo_network_boundary",
    "demo_evidence",
    "security",
    "other_bounded",
}
BLOCKER_REASONS = {
    "none",
    "published_guidance_failure",
    "misleading_success",
    "demo_network_boundary",
    "content_or_credential_exposure",
}
_REQUIRED_BLOCKERS_BY_FAILURE = {
    "install_dependency": {"published_guidance_failure"},
    "command_not_found": {"published_guidance_failure"},
    "demo_policy": {"published_guidance_failure"},
    "demo_network_boundary": {"demo_network_boundary"},
    "demo_evidence": {"published_guidance_failure", "misleading_success"},
    "documentation": {"published_guidance_failure"},
    "security": {"content_or_credential_exposure"},
}
_REQUIRED_BLOCKERS_BY_CATEGORY = {
    "installation": {"published_guidance_failure"},
    "command_discovery": {"published_guidance_failure"},
    "documentation": {"published_guidance_failure"},
    "demo_policy": {"published_guidance_failure"},
    "demo_network_boundary": {"demo_network_boundary"},
    "demo_evidence": {"published_guidance_failure", "misleading_success"},
    "security": {"content_or_credential_exposure"},
}
REFERENCE_TYPES = {"public_issue", "private_security_advisory"}
FINDING_STATUSES = {"open", "resolved"}

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "gate_issue",
    "program",
    "claim_scope",
    "generated_at",
    "artifact",
    "operator_attestation",
    "sessions",
    "findings",
}
_ARTIFACT_FIELDS = {
    "target_version",
    "package_version",
    "artifact_kind",
    "artifact_name",
    "artifact_digest",
    "artifact_size_bytes",
    "source_commit",
    "frozen_at",
    "custody_tag",
    "release_url",
}
_OPERATOR_FIELDS = {
    "distinct_humans_verified_off_repository",
    "cohort_preregistered_before_testing",
    "all_started_sessions_included",
    "participant_replacement_absent",
    "identity_mapping_not_committed",
    "raw_intake_not_committed",
    "reports_triaged_to_content_free_references",
    "artifact_digest_verified",
    "validated_human_onboarding_not_claimed_before_pass",
}
_SESSION_FIELDS = {
    "session_id",
    "participant_id",
    "artifact_digest",
    "started_at",
    "persona",
    "package_version",
    "installation_method",
    "environment",
    "consent_content_free_recording",
    "workflow_author_or_reviewer_absent",
    "installation_status",
    "demo_status",
    "assistance",
    "guidance_usage",
    "time_to_install_seconds",
    "time_to_demo_seconds",
    "failure_code",
    "demo_verification",
    "returning_session",
    "friction_categories",
    "finding_ids",
    "content_free_attestations",
}
_ENVIRONMENT_FIELDS = {
    "os_family",
    "os_major_version",
    "architecture",
    "python_minor",
}
_GUIDANCE_FIELDS = {"sources", "lookup_count"}
_DEMO_FIELDS = {
    "pass_line_count",
    "external_provider_call_count",
    "loopback_provider_call_count",
}
_CONTENT_FREE_FIELDS = {
    "prompt_or_response_absent",
    "credential_or_token_absent",
    "customer_or_company_data_absent",
    "personal_identity_absent",
    "identity_mapping_absent",
    "local_path_absent",
    "free_text_absent",
}
_FINDING_FIELDS = {
    "finding_id",
    "origin_session_id",
    "category",
    "blocker_reason",
    "reference_type",
    "reference",
    "status",
    "correction",
}
_CORRECTION_FIELDS = {
    "resolution_commit",
    "corrected_source_commit",
    "corrected_artifact_digest",
    "resolution_commit_ancestor_verified",
    "automated_regression_url",
    "automated_regression_source_commit",
    "automated_regression_workflow_path",
    "automated_regression_binding_verified",
    "automated_regression_conclusion",
    "retest_session_id",
    "broad_workflow_change",
}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_SESSION_ID_RE = re.compile(rf"eos:{_UUID}\Z")
_PARTICIPANT_ID_RE = re.compile(rf"eop:{_UUID}\Z")
_FINDING_ID_RE = re.compile(rf"eof:{_UUID}\Z")
_PRIVATE_ADVISORY_RE = re.compile(rf"private-advisory:{_UUID}\Z")
_ISSUE_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/issues/[1-9][0-9]*\Z"
)
_ACTIONS_RUN_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/actions/runs/[1-9][0-9]*\Z"
)
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_OS_MAJOR_RE = re.compile(r"[0-9]{1,3}\Z")
_PYTHON_MINOR_RE = re.compile(r"3\.(?:11|12|13|14)\Z")
_MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_REGRESSION_WORKFLOW_PATH = ".github/workflows/ci.yml"


class ExternalOnboardingEvidenceError(ValueError):
    """A fail-closed external-onboarding evidence contract violation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ExternalOnboardingEvidenceError("duplicate_json_member")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> object:
    raise ExternalOnboardingEvidenceError("non_finite_json_number")


def _read_evidence(path: Path) -> object:
    try:
        before = path.lstat()
    except OSError as error:
        raise ExternalOnboardingEvidenceError("evidence_unavailable") from error
    if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_EVIDENCE_BYTES:
        raise ExternalOnboardingEvidenceError("evidence_not_bounded_regular_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            payload = source.read(_MAX_EVIDENCE_BYTES + 1)
        after = path.lstat()
    except OSError as error:
        raise ExternalOnboardingEvidenceError("evidence_unavailable") from error
    finally:
        if "descriptor" in locals() and descriptor >= 0:
            os.close(descriptor)
    if (
        len(payload) > _MAX_EVIDENCE_BYTES
        or not stat.S_ISREG(after.st_mode)
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise ExternalOnboardingEvidenceError("evidence_changed_during_read")
    try:
        return json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ExternalOnboardingEvidenceError("evidence_json_invalid") from error


def _require_fields(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ExternalOnboardingEvidenceError(f"{label}_fields_invalid")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")
    return value


def _require_enum(value: object, choices: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")
    return value


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")
    return value


def _require_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ExternalOnboardingEvidenceError(f"{label}_invalid") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")
    return parsed


def _require_duration(value: object, status: str, label: str) -> int | None:
    if status == "not_attempted":
        if value is not None:
            raise ExternalOnboardingEvidenceError(f"{label}_invalid")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86_400:
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")
    return value


def _validate_artifact(value: object) -> datetime:
    artifact = _require_fields(value, _ARTIFACT_FIELDS, "artifact")
    expected = {
        "target_version": TARGET_VERSION,
        "package_version": PACKAGE_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "artifact_name": ARTIFACT_NAME,
        "artifact_digest": ARTIFACT_DIGEST,
        "artifact_size_bytes": ARTIFACT_SIZE_BYTES,
        "source_commit": SOURCE_COMMIT,
        "frozen_at": FROZEN_AT,
        "custody_tag": CUSTODY_TAG,
        "release_url": RELEASE_URL,
    }
    if artifact != expected:
        raise ExternalOnboardingEvidenceError("artifact_identity_invalid")
    return _require_timestamp(artifact["frozen_at"], "artifact_frozen_at")


def _validate_environment(value: object, label: str) -> None:
    environment = _require_fields(value, _ENVIRONMENT_FIELDS, label)
    _require_enum(environment["os_family"], OS_FAMILIES, f"{label}_os_family")
    _require_pattern(environment["os_major_version"], _OS_MAJOR_RE, f"{label}_os_major")
    _require_enum(environment["architecture"], ARCHITECTURES, f"{label}_architecture")
    _require_pattern(environment["python_minor"], _PYTHON_MINOR_RE, f"{label}_python")


def _validate_guidance(value: object, label: str) -> set[str]:
    guidance = _require_fields(value, _GUIDANCE_FIELDS, label)
    sources = guidance["sources"]
    if (
        not isinstance(sources, list)
        or not sources
        or len(sources) > len(GUIDANCE_SOURCES)
        or any(not isinstance(item, str) or item not in GUIDANCE_SOURCES for item in sources)
        or sources != sorted(set(sources))
    ):
        raise ExternalOnboardingEvidenceError(f"{label}_sources_invalid")
    lookups = guidance["lookup_count"]
    if isinstance(lookups, bool) or not isinstance(lookups, int) or not 0 <= lookups <= 100:
        raise ExternalOnboardingEvidenceError(f"{label}_lookup_count_invalid")
    return set(sources)


def _validate_demo(value: object, status: str, label: str) -> None:
    if status != "passed":
        if value is not None:
            raise ExternalOnboardingEvidenceError(f"{label}_unexpected")
        return
    demo = _require_fields(value, _DEMO_FIELDS, label)
    if demo != {
        "pass_line_count": 6,
        "external_provider_call_count": 0,
        "loopback_provider_call_count": 3,
    }:
        raise ExternalOnboardingEvidenceError(f"{label}_invalid")


def _validate_session(value: object, index: int) -> dict[str, Any]:
    label = f"session_{index}"
    session = dict(_require_fields(value, _SESSION_FIELDS, label))
    _require_pattern(session["session_id"], _SESSION_ID_RE, f"{label}_id")
    _require_pattern(session["participant_id"], _PARTICIPANT_ID_RE, f"{label}_participant")
    _require_pattern(session["artifact_digest"], _SHA256_RE, f"{label}_artifact")
    _require_timestamp(session["started_at"], f"{label}_started_at")
    _require_enum(session["persona"], PERSONAS, f"{label}_persona")
    if not isinstance(session["package_version"], str) or not session["package_version"]:
        raise ExternalOnboardingEvidenceError(f"{label}_package_version_invalid")
    _require_enum(
        session["installation_method"],
        INSTALLATION_METHODS,
        f"{label}_installation_method",
    )
    _validate_environment(session["environment"], f"{label}_environment")
    if session["consent_content_free_recording"] is not True:
        raise ExternalOnboardingEvidenceError(f"{label}_consent_invalid")
    independent = _require_bool(
        session["workflow_author_or_reviewer_absent"],
        f"{label}_workflow_independence",
    )

    installation_status = _require_enum(
        session["installation_status"], STATUSES, f"{label}_installation_status"
    )
    demo_status = _require_enum(session["demo_status"], STATUSES, f"{label}_demo_status")
    assistance = _require_enum(
        session["assistance"], ASSISTANCE_LEVELS, f"{label}_assistance"
    )
    guidance_sources = _validate_guidance(
        session["guidance_usage"], f"{label}_guidance"
    )
    _require_duration(
        session["time_to_install_seconds"], installation_status, f"{label}_install_time"
    )
    _require_duration(session["time_to_demo_seconds"], demo_status, f"{label}_demo_time")
    failure_code = _require_enum(
        session["failure_code"], FAILURE_CODES, f"{label}_failure"
    )
    returning = _require_bool(session["returning_session"], f"{label}_returning")
    if not returning and installation_status == "not_attempted":
        raise ExternalOnboardingEvidenceError(f"{label}_initial_install_required")
    if installation_status != "passed" and demo_status == "passed":
        if not (returning and installation_status == "not_attempted"):
            raise ExternalOnboardingEvidenceError(f"{label}_demo_without_install")
    failed = installation_status == "failed" or demo_status == "failed"
    if failed != (failure_code != "none"):
        raise ExternalOnboardingEvidenceError(f"{label}_failure_inconsistent")
    _validate_demo(session["demo_verification"], demo_status, f"{label}_demo_verification")

    categories = session["friction_categories"]
    finding_ids = session["finding_ids"]
    if (
        not isinstance(categories, list)
        or not categories
        or len(categories) > 10
        or any(item not in FRICTION_CATEGORIES for item in categories)
        or categories != sorted(set(categories))
        or not isinstance(finding_ids, list)
        or len(finding_ids) > 20
        or any(
            not isinstance(item, str) or _FINDING_ID_RE.fullmatch(item) is None
            for item in finding_ids
        )
        or finding_ids != sorted(set(finding_ids))
        or ((categories == ["none"]) != (not finding_ids))
    ):
        raise ExternalOnboardingEvidenceError(f"{label}_findings_invalid")
    if failed and not finding_ids:
        raise ExternalOnboardingEvidenceError(f"{label}_failed_without_finding")

    attestations = _require_fields(
        session["content_free_attestations"],
        _CONTENT_FREE_FIELDS,
        f"{label}_content_free",
    )
    if any(attestations[field] is not True for field in _CONTENT_FREE_FIELDS):
        raise ExternalOnboardingEvidenceError(f"{label}_content_free_invalid")

    session["_guidance_qualifies"] = guidance_sources <= QUALIFYING_GUIDANCE_SOURCES
    session["_assistance_qualifies"] = assistance != "maintainer_or_private_help"
    session["_independence_qualifies"] = independent
    return session


def _validate_correction(value: object, label: str) -> dict[str, Any]:
    correction = dict(
        _require_fields(value, _CORRECTION_FIELDS, f"{label}_correction")
    )
    _require_pattern(correction["resolution_commit"], _REVISION_RE, f"{label}_resolution")
    if correction["corrected_source_commit"] != SOURCE_COMMIT:
        raise ExternalOnboardingEvidenceError(f"{label}_corrected_source_invalid")
    if correction["corrected_artifact_digest"] != ARTIFACT_DIGEST:
        raise ExternalOnboardingEvidenceError(f"{label}_corrected_artifact_invalid")
    if correction["resolution_commit_ancestor_verified"] is not True:
        raise ExternalOnboardingEvidenceError(f"{label}_resolution_ancestry_invalid")
    _require_pattern(
        correction["automated_regression_url"],
        _ACTIONS_RUN_RE,
        f"{label}_regression_url",
    )
    if correction["automated_regression_source_commit"] != SOURCE_COMMIT:
        raise ExternalOnboardingEvidenceError(f"{label}_regression_source_invalid")
    if correction["automated_regression_workflow_path"] != _REGRESSION_WORKFLOW_PATH:
        raise ExternalOnboardingEvidenceError(f"{label}_regression_workflow_invalid")
    if correction["automated_regression_binding_verified"] is not True:
        raise ExternalOnboardingEvidenceError(f"{label}_regression_binding_invalid")
    if correction["automated_regression_conclusion"] != "success":
        raise ExternalOnboardingEvidenceError(f"{label}_regression_invalid")
    _require_pattern(
        correction["retest_session_id"], _SESSION_ID_RE, f"{label}_retest"
    )
    _require_bool(correction["broad_workflow_change"], f"{label}_broad_change")
    return correction


def _validate_finding(value: object, index: int) -> dict[str, Any]:
    label = f"finding_{index}"
    finding = dict(_require_fields(value, _FINDING_FIELDS, label))
    _require_pattern(finding["finding_id"], _FINDING_ID_RE, f"{label}_id")
    _require_pattern(finding["origin_session_id"], _SESSION_ID_RE, f"{label}_origin")
    category = _require_enum(
        finding["category"],
        FRICTION_CATEGORIES - {"none"},
        f"{label}_category",
    )
    blocker = _require_enum(finding["blocker_reason"], BLOCKER_REASONS, f"{label}_blocker")
    if blocker == "content_or_credential_exposure" and category != "security":
        raise ExternalOnboardingEvidenceError(f"{label}_blocker_category_invalid")
    if blocker == "demo_network_boundary" and category != "demo_network_boundary":
        raise ExternalOnboardingEvidenceError(f"{label}_blocker_category_invalid")
    reference_type = _require_enum(
        finding["reference_type"], REFERENCE_TYPES, f"{label}_reference_type"
    )
    if reference_type == "public_issue":
        _require_pattern(finding["reference"], _ISSUE_RE, f"{label}_reference")
    else:
        if category != "security":
            raise ExternalOnboardingEvidenceError(f"{label}_private_reference_invalid")
        _require_pattern(
            finding["reference"], _PRIVATE_ADVISORY_RE, f"{label}_reference"
        )
    status = _require_enum(finding["status"], FINDING_STATUSES, f"{label}_status")
    if status == "open" or blocker == "none":
        if finding["correction"] is not None:
            raise ExternalOnboardingEvidenceError(f"{label}_correction_unexpected")
    else:
        finding["correction"] = _validate_correction(finding["correction"], label)
    return finding


def _session_qualifies(session: dict[str, Any]) -> bool:
    return bool(
        session["artifact_digest"] == ARTIFACT_DIGEST
        and session["package_version"] == PACKAGE_VERSION
        and session["installation_status"] == "passed"
        and session["demo_status"] == "passed"
        and session["_guidance_qualifies"]
        and session["_assistance_qualifies"]
        and session["_independence_qualifies"]
    )


def validate_evidence(value: object) -> dict[str, object]:
    """Validate one strict aggregate and return its computed milestone result."""

    root = _require_fields(value, _ROOT_FIELDS, "root")
    schema_version = root["schema_version"]
    if (
        root["schema_id"] != SCHEMA_ID
        or isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != SCHEMA_VERSION
    ):
        raise ExternalOnboardingEvidenceError("schema_identity_invalid")
    evidence_kind = _require_enum(root["evidence_kind"], EVIDENCE_KINDS, "evidence_kind")
    if (
        root["gate_issue"] != GATE_ISSUE
        or root["program"] != PROGRAM
        or root["claim_scope"] != CLAIM_SCOPE
    ):
        raise ExternalOnboardingEvidenceError("program_identity_invalid")
    generated_at = _require_timestamp(root["generated_at"], "generated_at")
    artifact_frozen_at = _validate_artifact(root["artifact"])
    if artifact_frozen_at > generated_at:
        raise ExternalOnboardingEvidenceError("artifact_frozen_after_generation")
    if (
        evidence_kind != "synthetic_test_fixture"
        and generated_at > datetime.now(timezone.utc) + _MAX_FUTURE_CLOCK_SKEW
    ):
        raise ExternalOnboardingEvidenceError("generation_in_future")

    attestation = _require_fields(
        root["operator_attestation"], _OPERATOR_FIELDS, "operator"
    )
    for field in _OPERATOR_FIELDS:
        _require_bool(attestation[field], f"operator_{field}")

    raw_sessions = root["sessions"]
    if not isinstance(raw_sessions, list) or not 1 <= len(raw_sessions) <= 80:
        raise ExternalOnboardingEvidenceError("sessions_invalid")
    sessions = [_validate_session(item, index) for index, item in enumerate(raw_sessions)]
    session_ids = [session["session_id"] for session in sessions]
    if len(session_ids) != len(set(session_ids)):
        raise ExternalOnboardingEvidenceError("session_ids_duplicated")

    session_times: dict[str, tuple[datetime, datetime]] = {}
    participant_intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    for session in sessions:
        started = _require_timestamp(session["started_at"], "session_started_at")
        install = session["time_to_install_seconds"] or 0
        demo = session["time_to_demo_seconds"] or 0
        finished = started + timedelta(seconds=install + demo)
        if finished > generated_at:
            raise ExternalOnboardingEvidenceError("session_ends_after_generation")
        if session["artifact_digest"] == ARTIFACT_DIGEST and started < artifact_frozen_at:
            raise ExternalOnboardingEvidenceError("session_before_artifact_freeze")
        session_times[session["session_id"]] = (started, finished)
        participant_intervals.setdefault(session["participant_id"], []).append(
            (started, finished)
        )
    for intervals in participant_intervals.values():
        ordered = sorted(intervals)
        for (_, previous_end), (current_start, _) in zip(
            ordered, ordered[1:], strict=False
        ):
            if current_start < previous_end:
                raise ExternalOnboardingEvidenceError("participant_sessions_overlap")

    initial_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    sessions_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    persona_by_participant: dict[str, str] = {}
    for session in sessions:
        participant = session["participant_id"]
        previous_persona = persona_by_participant.setdefault(
            participant, session["persona"]
        )
        if previous_persona != session["persona"]:
            raise ExternalOnboardingEvidenceError("participant_persona_changed")
        key = (session["participant_id"], session["artifact_digest"])
        sessions_by_key.setdefault(key, []).append(session)
        if not session["returning_session"]:
            if key in initial_by_key:
                raise ExternalOnboardingEvidenceError("participant_initial_session_duplicated")
            initial_by_key[key] = session
    if set(initial_by_key) != set(sessions_by_key):
        raise ExternalOnboardingEvidenceError("returning_session_without_initial")
    for key, participant_sessions in sessions_by_key.items():
        initial = initial_by_key[key]
        initial_start = session_times[initial["session_id"]][0]
        for session in participant_sessions:
            if session["returning_session"]:
                return_start = session_times[session["session_id"]][0]
                if return_start.date() <= initial_start.date():
                    raise ExternalOnboardingEvidenceError("returning_session_not_later_date")

    raw_findings = root["findings"]
    if not isinstance(raw_findings, list) or len(raw_findings) > 100:
        raise ExternalOnboardingEvidenceError("findings_invalid")
    findings = [_validate_finding(item, index) for index, item in enumerate(raw_findings)]
    finding_ids = [finding["finding_id"] for finding in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise ExternalOnboardingEvidenceError("finding_ids_duplicated")
    sessions_by_id = {session["session_id"]: session for session in sessions}
    findings_by_id = {finding["finding_id"]: finding for finding in findings}

    for session in sessions:
        linked: list[dict[str, Any]] = []
        for finding_id in session["finding_ids"]:
            finding = findings_by_id.get(finding_id)
            if finding is None or finding["origin_session_id"] != session["session_id"]:
                raise ExternalOnboardingEvidenceError("session_finding_invalid")
            linked.append(finding)
        expected_categories = (
            sorted({finding["category"] for finding in linked}) if linked else ["none"]
        )
        if session["friction_categories"] != expected_categories:
            raise ExternalOnboardingEvidenceError("session_finding_invalid")
        required_blockers = _REQUIRED_BLOCKERS_BY_FAILURE.get(
            session["failure_code"]
        )
        if required_blockers and not any(
            finding["blocker_reason"] in required_blockers for finding in linked
        ):
            raise ExternalOnboardingEvidenceError(
                "session_required_blocker_missing"
            )
        if session["failure_code"] != "none":
            for category in session["friction_categories"]:
                category_blockers = _REQUIRED_BLOCKERS_BY_CATEGORY.get(category)
                if category_blockers and not any(
                    finding["category"] == category
                    and finding["blocker_reason"] in category_blockers
                    for finding in linked
                ):
                    raise ExternalOnboardingEvidenceError(
                        "session_required_blocker_missing"
                    )

    for finding in findings:
        origin = sessions_by_id.get(finding["origin_session_id"])
        if origin is None or finding["finding_id"] not in origin["finding_ids"]:
            raise ExternalOnboardingEvidenceError("finding_origin_invalid")
        correction = finding["correction"]
        if correction is None:
            continue
        if origin["artifact_digest"] == correction["corrected_artifact_digest"]:
            raise ExternalOnboardingEvidenceError("finding_correction_not_fresh")
        if session_times[origin["session_id"]][1] >= artifact_frozen_at:
            raise ExternalOnboardingEvidenceError(
                "finding_origin_not_before_corrected_artifact"
            )
        retest = sessions_by_id.get(correction["retest_session_id"])
        if (
            retest is None
            or retest["artifact_digest"] != correction["corrected_artifact_digest"]
            or session_times[retest["session_id"]][0]
            <= session_times[origin["session_id"]][1]
            or not _session_qualifies(retest)
        ):
            raise ExternalOnboardingEvidenceError("finding_retest_invalid")
        if correction["broad_workflow_change"]:
            affected_participants = {
                participant
                for participant, digest in initial_by_key
                if digest == origin["artifact_digest"]
            }
            corrected_participants = {
                participant
                for (participant, digest), session in initial_by_key.items()
                if digest == correction["corrected_artifact_digest"]
                and _session_qualifies(session)
            }
            if not affected_participants <= corrected_participants:
                raise ExternalOnboardingEvidenceError(
                    "broad_correction_cohort_retest_incomplete"
                )

    current_initials = [
        session
        for (participant, digest), session in initial_by_key.items()
        if digest == ARTIFACT_DIGEST
    ]
    current_participants = {session["participant_id"] for session in current_initials}
    successful_initials = {
        session["participant_id"] for session in current_initials if _session_qualifies(session)
    }
    successful_returns = {
        session["participant_id"]
        for session in sessions
        if session["artifact_digest"] == ARTIFACT_DIGEST
        and session["returning_session"]
        and session["participant_id"] in successful_initials
        and session["package_version"] == PACKAGE_VERSION
        and session["installation_status"] in {"passed", "not_attempted"}
        and session["demo_status"] == "passed"
        and session["_guidance_qualifies"]
        and session["_assistance_qualifies"]
        and session["_independence_qualifies"]
    }
    personas = {session["persona"] for session in current_initials}
    unresolved_blockers = [
        finding
        for finding in findings
        if finding["blocker_reason"] != "none" and finding["status"] == "open"
    ]

    reasons: list[str] = []
    if evidence_kind != "external_onboarding_evidence":
        reasons.append("synthetic_fixture")
    required_true = {
        "distinct_humans_verified_off_repository": "distinct_humans_not_attested",
        "cohort_preregistered_before_testing": "cohort_preregistration_not_attested",
        "all_started_sessions_included": "started_sessions_not_complete",
        "participant_replacement_absent": "participant_replacement_not_ruled_out",
        "identity_mapping_not_committed": "identity_mapping_boundary_not_attested",
        "raw_intake_not_committed": "raw_intake_boundary_not_attested",
        "reports_triaged_to_content_free_references": "report_triage_not_attested",
        "artifact_digest_verified": "artifact_digest_not_attested",
        "validated_human_onboarding_not_claimed_before_pass": "claim_made_before_evidence",
    }
    for field, reason in required_true.items():
        if attestation[field] is not True:
            reasons.append(reason)
    if not 5 <= len(current_participants) <= 10:
        reasons.append("participant_count_outside_target")
    if personas != PERSONAS:
        reasons.append("persona_coverage_incomplete")
    if len(successful_initials) < 5:
        reasons.append("independent_install_demo_count_incomplete")
    if not successful_returns:
        reasons.append("returning_user_session_missing")
    if unresolved_blockers:
        reasons.append("onboarding_blocker_open")

    ready = not reasons
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed_external_onboarding" if ready else "not_ready",
        "evidence_kind": evidence_kind,
        "claim_scope": CLAIM_SCOPE,
        "validated_human_onboarding": ready,
        "artifact_digest": ARTIFACT_DIGEST,
        "participant_count": len(current_participants),
        "session_count": len(sessions),
        "successful_independent_participant_count": len(successful_initials),
        "successful_returning_participant_count": len(successful_returns),
        "persona_coverage": sorted(personas),
        "finding_count": len(findings),
        "unresolved_blocker_count": len(unresolved_blockers),
        "reasons": reasons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate strict, content-free Hormuz external-onboarding evidence."
    )
    parser.add_argument("evidence", type=Path)
    parser.add_argument(
        "--allow-synthetic-fixture",
        action="store_true",
        help="validate a synthetic fixture without treating it as human evidence",
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
            raise ExternalOnboardingEvidenceError(
                "synthetic_fixture_requires_explicit_flag"
            )
    except (OSError, ValueError, ExternalOnboardingEvidenceError) as error:
        code = str(error) or "external_onboarding_evidence_invalid"
        print(f"external onboarding evidence failed: {code}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result["evidence_kind"] == "synthetic_test_fixture":
        return 0
    return 0 if result["validated_human_onboarding"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
