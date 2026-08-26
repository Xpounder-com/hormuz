#!/usr/bin/env python3
"""Validate post-publication onboarding evidence without retaining tester content.

The aggregate records only allowlisted environment and completion metadata.
Names, handles, email addresses, feedback text, prompts, responses, credentials,
customer data, local paths, and identity mappings have no fields in this
schema. Synthetic fixtures are useful for contract tests but can never satisfy
the external onboarding-validation milestone. The schema-v1 `quiet-alpha`,
`release_gate`, and `broad_promotion` names remain compatibility identifiers;
they gate validated-onboarding and beyond-alpha claims, not the bounded initial
tester-recruitment announcement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


SCHEMA_ID = "hormuz.quiet-alpha-evidence"
SCHEMA_VERSION = 1
PACKAGE_VERSION = "0.1.3"
PROGRAM = f"hormuz-v{PACKAGE_VERSION}-quiet-alpha"
PUBLICATION_STATUS = "content_free"
RELEASE_SOURCE_COMMIT = "6b3c4b94ff0691668d624a18ba2e63cc9ab5f9ae"

EVIDENCE_KINDS = {"quiet_alpha_release_evidence", "synthetic_test_fixture"}
PERSONAS = {"developer", "security", "platform", "engineering_admin"}
INSTALLATION_METHODS = {"source_checkout", "signed_oci_digest"}
OS_FAMILIES = {"linux", "macos", "windows", "wsl"}
ARCHITECTURES = {"x86_64", "arm64"}
STATUSES = {"passed", "failed", "not_attempted"}
ASSISTANCE_LEVELS = {
    "none",
    "public_repository_material_only",
    "maintainer_or_private_help",
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
    "provider_path",
    "security",
    "other",
}
PROVIDER_PATHS = {
    "not_attempted",
    "openai_codex_succeeded",
    "anthropic_claude_succeeded",
    "both_succeeded",
    "failed",
}
FINDING_CATEGORIES = {
    "installation",
    "security",
    "documentation",
    "compatibility",
    "other",
}
FINDING_REFERENCE_TYPES = {"public_issue", "private_security_advisory"}
FINDING_STATUSES = {"open", "resolved"}

_ROOT_FIELDS = {
    "schema_id",
    "schema_version",
    "evidence_kind",
    "program",
    "publication_status",
    "generated_at",
    "operator_attestation",
    "sessions",
    "findings",
}
_OPERATOR_FIELDS = {
    "distinct_humans_verified_off_repository",
    "identity_mapping_not_committed",
    "reports_triaged_to_content_free_references",
    "broad_promotion_not_started",
}
_SESSION_FIELDS = {
    "session_id",
    "participant_id",
    "session_date",
    "persona",
    "source_commit",
    "package_version",
    "installation_method",
    "environment",
    "consent_content_free_recording",
    "installation_status",
    "demo_status",
    "assistance",
    "time_to_install_seconds",
    "time_to_demo_seconds",
    "failure_code",
    "optional_provider_path",
    "returning_session",
    "content_free_attestations",
}
_ENVIRONMENT_FIELDS = {
    "os_family",
    "os_major_version",
    "architecture",
    "python_minor",
    "docker_used",
}
_CONTENT_FREE_FIELDS = {
    "prompt_or_response_absent",
    "credential_or_token_absent",
    "customer_or_company_data_absent",
    "person_identity_absent",
    "local_path_absent",
    "free_text_absent",
}
_FINDING_FIELDS = {
    "finding_id",
    "category",
    "blocker",
    "reference_type",
    "reference",
    "status",
    "resolution_commit",
    "retest_session_id",
}

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
_SESSION_ID_RE = re.compile(rf"qas:{_UUID}\Z")
_PARTICIPANT_ID_RE = re.compile(rf"qa:{_UUID}\Z")
_FINDING_ID_RE = re.compile(rf"qaf:{_UUID}\Z")
_PRIVATE_ADVISORY_RE = re.compile(rf"private-advisory:{_UUID}\Z")
_ISSUE_RE = re.compile(
    r"https://github\.com/Xpounder-com/hormuz/issues/[1-9][0-9]*\Z"
)
_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
_OS_MAJOR_RE = re.compile(r"[0-9]{1,3}\Z")
_PYTHON_MINOR_RE = re.compile(r"3\.(?:11|12|13|14)\Z")
_MAX_EVIDENCE_BYTES = 1024 * 1024


class QuietAlphaEvidenceError(ValueError):
    """A fail-closed quiet-alpha evidence contract violation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise QuietAlphaEvidenceError("duplicate_json_member")
        value[key] = item
    return value


def _read_evidence(path: Path) -> object:
    if path.stat().st_size > _MAX_EVIDENCE_BYTES:
        raise QuietAlphaEvidenceError("evidence_too_large")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)


def _require_fields(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise QuietAlphaEvidenceError(f"{label}_fields_invalid")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise QuietAlphaEvidenceError(f"{label}_invalid")
    return value


def _require_enum(value: object, choices: set[str], label: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise QuietAlphaEvidenceError(f"{label}_invalid")
    return value


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise QuietAlphaEvidenceError(f"{label}_invalid")
    return value


def _require_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise QuietAlphaEvidenceError(f"{label}_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise QuietAlphaEvidenceError(f"{label}_invalid") from exc
    if parsed.isoformat() != value:
        raise QuietAlphaEvidenceError(f"{label}_invalid")
    return parsed


def _require_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise QuietAlphaEvidenceError("generated_at_invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise QuietAlphaEvidenceError("generated_at_invalid") from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise QuietAlphaEvidenceError("generated_at_invalid")
    return parsed


def _require_duration(value: object, status: str, label: str) -> None:
    if status == "not_attempted":
        if value is not None:
            raise QuietAlphaEvidenceError(f"{label}_invalid")
        return
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 86_400:
        raise QuietAlphaEvidenceError(f"{label}_invalid")


def _validate_environment(value: object, label: str) -> None:
    environment = _require_fields(value, _ENVIRONMENT_FIELDS, label)
    _require_enum(environment["os_family"], OS_FAMILIES, f"{label}_os_family")
    _require_pattern(environment["os_major_version"], _OS_MAJOR_RE, f"{label}_os_major")
    _require_enum(environment["architecture"], ARCHITECTURES, f"{label}_architecture")
    _require_pattern(environment["python_minor"], _PYTHON_MINOR_RE, f"{label}_python_minor")
    _require_bool(environment["docker_used"], f"{label}_docker_used")


def _validate_session(value: object, index: int) -> dict[str, Any]:
    label = f"session_{index}"
    session = _require_fields(value, _SESSION_FIELDS, label)
    _require_pattern(session["session_id"], _SESSION_ID_RE, f"{label}_id")
    _require_pattern(session["participant_id"], _PARTICIPANT_ID_RE, f"{label}_participant")
    _require_date(session["session_date"], f"{label}_date")
    _require_enum(session["persona"], PERSONAS, f"{label}_persona")
    source_commit = _require_pattern(
        session["source_commit"], _REVISION_RE, f"{label}_source_commit"
    )
    if source_commit != RELEASE_SOURCE_COMMIT:
        raise QuietAlphaEvidenceError(f"{label}_source_commit_unpinned")
    if session["package_version"] != PACKAGE_VERSION:
        raise QuietAlphaEvidenceError(f"{label}_package_version_invalid")
    _require_enum(
        session["installation_method"], INSTALLATION_METHODS, f"{label}_installation_method"
    )
    _validate_environment(session["environment"], f"{label}_environment")
    if session["consent_content_free_recording"] is not True:
        raise QuietAlphaEvidenceError(f"{label}_consent_invalid")

    installation_status = _require_enum(
        session["installation_status"], STATUSES, f"{label}_installation_status"
    )
    demo_status = _require_enum(session["demo_status"], STATUSES, f"{label}_demo_status")
    _require_enum(session["assistance"], ASSISTANCE_LEVELS, f"{label}_assistance")
    _require_duration(
        session["time_to_install_seconds"], installation_status, f"{label}_install_duration"
    )
    _require_duration(session["time_to_demo_seconds"], demo_status, f"{label}_demo_duration")
    failure_code = _require_enum(session["failure_code"], FAILURE_CODES, f"{label}_failure")
    provider_path = _require_enum(
        session["optional_provider_path"], PROVIDER_PATHS, f"{label}_provider_path"
    )
    returning = _require_bool(session["returning_session"], f"{label}_returning")
    if not returning and installation_status == "not_attempted":
        raise QuietAlphaEvidenceError(f"{label}_initial_install_required")

    failed = (
        installation_status == "failed"
        or demo_status == "failed"
        or provider_path == "failed"
    )
    if failed != (failure_code != "none"):
        raise QuietAlphaEvidenceError(f"{label}_failure_inconsistent")
    if demo_status != "passed" and provider_path not in {"not_attempted", "failed"}:
        raise QuietAlphaEvidenceError(f"{label}_provider_path_inconsistent")

    attestations = _require_fields(
        session["content_free_attestations"], _CONTENT_FREE_FIELDS, f"{label}_content_free"
    )
    if any(attestations[field] is not True for field in _CONTENT_FREE_FIELDS):
        raise QuietAlphaEvidenceError(f"{label}_content_free_invalid")
    return session


def _validate_finding(value: object, index: int) -> dict[str, Any]:
    label = f"finding_{index}"
    finding = _require_fields(value, _FINDING_FIELDS, label)
    _require_pattern(finding["finding_id"], _FINDING_ID_RE, f"{label}_id")
    category = _require_enum(finding["category"], FINDING_CATEGORIES, f"{label}_category")
    _require_bool(finding["blocker"], f"{label}_blocker")
    reference_type = _require_enum(
        finding["reference_type"], FINDING_REFERENCE_TYPES, f"{label}_reference_type"
    )
    if reference_type == "public_issue":
        _require_pattern(finding["reference"], _ISSUE_RE, f"{label}_reference")
    else:
        if category != "security":
            raise QuietAlphaEvidenceError(f"{label}_private_reference_invalid")
        _require_pattern(
            finding["reference"], _PRIVATE_ADVISORY_RE, f"{label}_reference"
        )

    status = _require_enum(finding["status"], FINDING_STATUSES, f"{label}_status")
    if status == "open":
        if finding["resolution_commit"] is not None or finding["retest_session_id"] is not None:
            raise QuietAlphaEvidenceError(f"{label}_resolution_inconsistent")
    else:
        _require_pattern(
            finding["resolution_commit"], _REVISION_RE, f"{label}_resolution_commit"
        )
        _require_pattern(
            finding["retest_session_id"], _SESSION_ID_RE, f"{label}_retest_session"
        )
    return finding


def validate_evidence(value: object) -> dict[str, object]:
    """Validate one strict aggregate and return its computed gate result."""

    root = _require_fields(value, _ROOT_FIELDS, "root")
    if root["schema_id"] != SCHEMA_ID or root["schema_version"] != SCHEMA_VERSION:
        raise QuietAlphaEvidenceError("schema_identity_invalid")
    evidence_kind = _require_enum(root["evidence_kind"], EVIDENCE_KINDS, "evidence_kind")
    if root["program"] != PROGRAM or root["publication_status"] != PUBLICATION_STATUS:
        raise QuietAlphaEvidenceError("program_identity_invalid")
    generated_at = _require_timestamp(root["generated_at"])

    attestation = _require_fields(root["operator_attestation"], _OPERATOR_FIELDS, "operator")
    for field in _OPERATOR_FIELDS:
        _require_bool(attestation[field], f"operator_{field}")

    raw_sessions = root["sessions"]
    if not isinstance(raw_sessions, list) or not 1 <= len(raw_sessions) <= 40:
        raise QuietAlphaEvidenceError("sessions_invalid")
    sessions = [_validate_session(value, index) for index, value in enumerate(raw_sessions)]
    if any(
        _require_date(session["session_date"], "session_date") > generated_at.date()
        for session in sessions
    ):
        raise QuietAlphaEvidenceError("session_date_after_generation")
    session_ids = [session["session_id"] for session in sessions]
    if len(session_ids) != len(set(session_ids)):
        raise QuietAlphaEvidenceError("session_ids_duplicated")

    initial_by_participant: dict[str, dict[str, Any]] = {}
    sessions_by_participant: dict[str, list[dict[str, Any]]] = {}
    for session in sessions:
        participant_id = session["participant_id"]
        sessions_by_participant.setdefault(participant_id, []).append(session)
        if not session["returning_session"]:
            if participant_id in initial_by_participant:
                raise QuietAlphaEvidenceError("participant_initial_session_duplicated")
            initial_by_participant[participant_id] = session
    if set(initial_by_participant) != set(sessions_by_participant):
        raise QuietAlphaEvidenceError("returning_session_without_initial")
    for participant_id, participant_sessions in sessions_by_participant.items():
        initial = initial_by_participant[participant_id]
        initial_date = _require_date(initial["session_date"], "initial_session_date")
        persona = initial["persona"]
        for session in participant_sessions:
            if session["persona"] != persona:
                raise QuietAlphaEvidenceError("participant_persona_changed")
            if session["returning_session"] and _require_date(
                session["session_date"], "returning_session_date"
            ) <= initial_date:
                raise QuietAlphaEvidenceError("returning_session_not_later")

    raw_findings = root["findings"]
    if not isinstance(raw_findings, list) or len(raw_findings) > 100:
        raise QuietAlphaEvidenceError("findings_invalid")
    findings = [_validate_finding(value, index) for index, value in enumerate(raw_findings)]
    finding_ids = [finding["finding_id"] for finding in findings]
    references = [(finding["reference_type"], finding["reference"]) for finding in findings]
    if len(finding_ids) != len(set(finding_ids)):
        raise QuietAlphaEvidenceError("finding_ids_duplicated")
    if len(references) != len(set(references)):
        raise QuietAlphaEvidenceError("finding_references_duplicated")
    session_by_id = {session["session_id"]: session for session in sessions}
    for finding in findings:
        if finding["status"] != "resolved":
            continue
        retest = session_by_id.get(finding["retest_session_id"])
        if (
            retest is None
            or retest["installation_status"] != "passed"
            or retest["demo_status"] != "passed"
            or retest["assistance"] == "maintainer_or_private_help"
        ):
            raise QuietAlphaEvidenceError("finding_retest_invalid")

    participant_count = len(initial_by_participant)
    personas = {session["persona"] for session in initial_by_participant.values()}
    successful_initial_participants = {
        participant_id
        for participant_id, session in initial_by_participant.items()
        if session["installation_status"] == "passed"
        and session["demo_status"] == "passed"
        and session["assistance"] != "maintainer_or_private_help"
    }
    successful_returning_participants = {
        session["participant_id"]
        for session in sessions
        if session["returning_session"]
        and session["installation_status"] in {"passed", "not_attempted"}
        and session["demo_status"] == "passed"
        and session["assistance"] != "maintainer_or_private_help"
        and session["participant_id"] in successful_initial_participants
    }
    provider_participants = {
        session["participant_id"]
        for session in sessions
        if session["optional_provider_path"]
        in {
            "openai_codex_succeeded",
            "anthropic_claude_succeeded",
            "both_succeeded",
        }
    }
    unresolved_blockers = [
        finding
        for finding in findings
        if finding["blocker"] is True
        and finding["category"] in {"installation", "security"}
        and finding["status"] != "resolved"
    ]

    reasons: list[str] = []
    if evidence_kind != "quiet_alpha_release_evidence":
        reasons.append("synthetic_fixture")
    if attestation["distinct_humans_verified_off_repository"] is not True:
        reasons.append("distinct_humans_not_attested")
    if attestation["identity_mapping_not_committed"] is not True:
        reasons.append("identity_mapping_boundary_not_attested")
    if attestation["reports_triaged_to_content_free_references"] is not True:
        reasons.append("report_triage_not_attested")
    if attestation["broad_promotion_not_started"] is not True:
        reasons.append("broad_promotion_already_started")
    if not 5 <= participant_count <= 10:
        reasons.append("participant_count_outside_target")
    if personas != PERSONAS:
        reasons.append("persona_coverage_incomplete")
    if len(successful_initial_participants) < 5:
        reasons.append("independent_install_demo_count_incomplete")
    if not successful_returning_participants:
        reasons.append("returning_user_session_missing")
    if unresolved_blockers:
        reasons.append("security_or_installation_blocker_open")

    ready = not reasons
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed_release_gate" if ready else "not_ready",
        "evidence_kind": evidence_kind,
        "ready_for_broad_promotion": ready,
        "participant_count": participant_count,
        "session_count": len(sessions),
        "successful_independent_participant_count": len(successful_initial_participants),
        "successful_returning_participant_count": len(successful_returning_participants),
        "persona_coverage": sorted(personas),
        "optional_provider_participant_count": len(provider_participants),
        "finding_count": len(findings),
        "unresolved_security_or_installation_blocker_count": len(unresolved_blockers),
        "reasons": reasons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate strict, content-free Hormuz quiet-alpha evidence."
    )
    parser.add_argument("evidence", type=Path)
    parser.add_argument(
        "--allow-synthetic-fixture",
        action="store_true",
        help="validate a synthetic contract fixture without treating it as release evidence",
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
            raise QuietAlphaEvidenceError("synthetic_fixture_requires_explicit_flag")
    except (OSError, UnicodeError, json.JSONDecodeError, QuietAlphaEvidenceError) as exc:
        print(f"quiet-alpha evidence failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    if result["evidence_kind"] == "synthetic_test_fixture":
        return 0
    return 0 if result["ready_for_broad_promotion"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
