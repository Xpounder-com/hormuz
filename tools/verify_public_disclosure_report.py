#!/usr/bin/env python3
"""Validate Hormuz's strict, content-free public-disclosure report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_ID = "hormuz.public-disclosure-report"
SCHEMA_VERSION = 1
EXPECTED_REPOSITORY = "Xpounder-com/hormuz"
VERDICTS = {
    "decision_required",
    "ready_for_public_transition",
    "public_transition_verified",
}
CLASSIFICATIONS = {
    "decision_required",
    "owner_approved_public_disclosure",
    "remediated",
    "removed",
    "rotated",
    "safe",
}
SURFACES = {
    "actions_artifacts",
    "actions_logs",
    "commit_metadata",
    "git_history",
    "github_surfaces",
}
FINDING_IDS = {
    "artifact_credential_url_test_fixtures",
    "artifact_email_dependency_metadata_and_fixtures",
    "artifact_generic_synthetic_identifiers",
    "artifact_private_path_false_positive",
    "collector_authenticated_clone_credential",
    "commit_metadata_owner_email",
    "commit_metadata_platform_emails",
    "github_surface_hash_values",
    "github_surface_ssh_url_email_false_positives",
    "history_credential_url_test_fixtures",
    "history_file_email_synthetic_values",
    "history_generic_documentation_values",
    "history_generic_synthetic_identifiers",
    "history_private_path_false_positive",
}
BLOCKER_STATUSES = {
    "authorization_required",
    "decision_required",
    "execution_required",
}
BLOCKER_ACTIONS = {
    "actions_cache_disposition": "delete_before_publication_or_explicitly_accept_risk",
    "final_candidate_delta_audit": "rescan_final_candidate_and_new_github_surfaces",
    "historical_owner_email_disclosure": "approve_disclosure_or_rewrite_history",
    "repository_visibility_authorization": "explicit_owner_authorization_after_zero_other_blockers",
    "server_unadvertised_object_scope": "accept_explicit_scope_or_publish_sanitized_repository",
}
NONCLAIMS = [
    "automated_scan_alone_proves_history_safe",
    "dependency_license_review_is_legal_opinion",
    "expired_artifact_bytes_were_recovered",
    "opaque_actions_cache_contents_were_scanned",
    "server_unadvertised_unreachable_objects_were_enumerated",
]
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
FORBIDDEN_CONTENT = (
    re.compile(r"/Users/"),
    re.compile(r"/home/runner/"),
    re.compile(r"/private/tmp/"),
    re.compile(r"C:\\Users\\"),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b"),
)


class DisclosureReportError(RuntimeError):
    """Raised when a disclosure report is unsupported, unsafe, or inconsistent."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--require-verdict", choices=sorted(VERDICTS))
    args = parser.parse_args(argv)

    try:
        report = load_report(args.report)
        validate_report(report)
        if args.require_verdict is not None and report["verdict"] != args.require_verdict:
            raise DisclosureReportError("disclosure_report_verdict_mismatch")
    except (DisclosureReportError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"public disclosure report verification failed: {error}", file=sys.stderr)
        return 1

    print(
        "verified content-free public disclosure report: "
        f"revision={report['report_revision']} verdict={report['verdict']} "
        f"findings={len(report['findings'])} blockers={len(report['blockers'])}"
    )
    return 0


def load_report(path: Path) -> dict[str, Any]:
    """Read a UTF-8 report while rejecting duplicate JSON object keys."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise DisclosureReportError("disclosure_report_duplicate_key")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    if not isinstance(value, dict):
        raise DisclosureReportError("disclosure_report_not_object")
    return value


def validate_report(report: Mapping[str, Any]) -> None:
    """Require one exact metadata-only schema and internally consistent counts."""

    _keys(
        report,
        {
            "audit_boundary",
            "blockers",
            "external_surfaces",
            "findings",
            "licensing",
            "nonclaims",
            "observed_window",
            "publication",
            "report_revision",
            "repository",
            "scanner",
            "schema_id",
            "schema_version",
            "verdict",
        },
        "report",
    )
    if report["schema_id"] != SCHEMA_ID or _count(report["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise DisclosureReportError("disclosure_report_schema_unsupported")
    if _count(report["report_revision"], "report_revision") != 1 or report["repository"] != EXPECTED_REPOSITORY:
        raise DisclosureReportError("disclosure_report_identity_invalid")

    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if any(pattern.search(encoded) for pattern in FORBIDDEN_CONTENT):
        raise DisclosureReportError("disclosure_report_contains_forbidden_content")

    window = _object(report["observed_window"], "observed_window")
    _keys(window, {"ended_at", "started_at"}, "observed_window")
    started = _timestamp(window["started_at"], "observed_window_started_at")
    ended = _timestamp(window["ended_at"], "observed_window_ended_at")
    if started > ended:
        raise DisclosureReportError("disclosure_report_observed_window_invalid")

    boundary = _object(report["audit_boundary"], "audit_boundary")
    _keys(
        boundary,
        {
            "advertised_heads",
            "advertised_pull_refs",
            "advertised_refs",
            "baseline_commit",
            "commits",
            "fresh_mirror_unreachable_objects",
            "merge_commits",
            "non_merge_commits",
            "reachable_objects",
            "server_unadvertised_unreachable_enumerable",
        },
        "audit_boundary",
    )
    if not isinstance(boundary["baseline_commit"], str) or COMMIT_PATTERN.fullmatch(boundary["baseline_commit"]) is None:
        raise DisclosureReportError("disclosure_report_baseline_commit_invalid")
    for key in (
        "advertised_heads",
        "advertised_pull_refs",
        "advertised_refs",
        "commits",
        "fresh_mirror_unreachable_objects",
        "merge_commits",
        "non_merge_commits",
        "reachable_objects",
    ):
        _count(boundary[key], f"audit_boundary_{key}")
    if boundary["advertised_refs"] != boundary["advertised_heads"] + boundary["advertised_pull_refs"]:
        raise DisclosureReportError("disclosure_report_advertised_ref_counts_invalid")
    if boundary["commits"] != boundary["merge_commits"] + boundary["non_merge_commits"]:
        raise DisclosureReportError("disclosure_report_commit_counts_invalid")
    if boundary["fresh_mirror_unreachable_objects"] != 0:
        raise DisclosureReportError("disclosure_report_fresh_mirror_has_unreachable_objects")
    if boundary["server_unadvertised_unreachable_enumerable"] is not False:
        raise DisclosureReportError("disclosure_report_server_scope_claim_invalid")

    surfaces = _object(report["external_surfaces"], "external_surfaces")
    _keys(surfaces, {"actions", "github"}, "external_surfaces")
    actions = _object(surfaces["actions"], "actions")
    _validate_actions(actions)
    _validate_github(_object(surfaces["github"], "github"))
    scan_counts = _validate_scanner(_object(report["scanner"], "scanner"))
    findings = _validate_findings(report["findings"])
    _validate_scan_finding_counts(scan_counts, report["findings"])
    _validate_licensing(_object(report["licensing"], "licensing"))
    publication = _object(report["publication"], "publication")
    _validate_publication(publication)
    blockers = _validate_blockers(report["blockers"])
    _validate_resolution_consistency(actions, findings, publication, blockers)

    if report["nonclaims"] != NONCLAIMS:
        raise DisclosureReportError("disclosure_report_nonclaims_invalid")
    verdict = report["verdict"]
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise DisclosureReportError("disclosure_report_verdict_invalid")
    if blockers and verdict != "decision_required":
        raise DisclosureReportError("disclosure_report_open_blockers_without_decision_verdict")
    if not blockers and verdict == "decision_required":
        raise DisclosureReportError("disclosure_report_decision_verdict_without_blockers")
    if verdict != "decision_required":
        if publication["owner_authorization"] != "approved":
            raise DisclosureReportError("disclosure_report_ready_without_owner_authorization")


def _validate_actions(actions: Mapping[str, Any]) -> None:
    required = {
        "artifact_archives_unsafe_or_encrypted",
        "artifact_bytes_scanned",
        "artifacts_available",
        "artifacts_downloaded",
        "artifacts_expired",
        "artifacts_total",
        "cache_bytes",
        "cache_content_downloadable",
        "caches",
        "run_logs_available",
        "run_logs_scanned",
        "run_logs_without_jobs",
        "workflow_runs",
    }
    _keys(actions, required, "actions")
    for key in required - {"cache_content_downloadable"}:
        _count(actions[key], f"actions_{key}")
    if actions["workflow_runs"] != actions["run_logs_available"] + actions["run_logs_without_jobs"]:
        raise DisclosureReportError("disclosure_report_run_log_counts_invalid")
    if actions["run_logs_available"] != actions["run_logs_scanned"]:
        raise DisclosureReportError("disclosure_report_run_logs_not_fully_scanned")
    if actions["artifacts_total"] != actions["artifacts_available"] + actions["artifacts_expired"]:
        raise DisclosureReportError("disclosure_report_artifact_counts_invalid")
    if actions["artifacts_available"] != actions["artifacts_downloaded"]:
        raise DisclosureReportError("disclosure_report_available_artifacts_not_downloaded")
    if actions["artifact_archives_unsafe_or_encrypted"] != 0:
        raise DisclosureReportError("disclosure_report_unsafe_artifact_archive")
    if actions["cache_content_downloadable"] is not False:
        raise DisclosureReportError("disclosure_report_cache_scope_invalid")


def _validate_github(github: Mapping[str, Any]) -> None:
    required = {
        "actions_secrets",
        "actions_variables",
        "commit_comments",
        "container_packages",
        "dependabot_secrets",
        "environments",
        "issue_comments",
        "issues_and_pull_requests",
        "pull_requests",
        "pull_review_comments",
        "pull_reviews",
        "release_assets",
        "releases",
        "repository_secret_scanning_enabled",
        "repository_secret_scanning_push_protection_enabled",
        "surface_snapshot_files",
    }
    _keys(github, required, "github")
    for key in required - {
        "repository_secret_scanning_enabled",
        "repository_secret_scanning_push_protection_enabled",
    }:
        _count(github[key], f"github_{key}")
    if github["repository_secret_scanning_enabled"] is not False:
        raise DisclosureReportError("disclosure_report_secret_scanning_snapshot_invalid")
    if github["repository_secret_scanning_push_protection_enabled"] is not False:
        raise DisclosureReportError("disclosure_report_push_protection_snapshot_invalid")


def _validate_scanner(scanner: Mapping[str, Any]) -> Mapping[str, Any]:
    _keys(
        scanner,
        {
            "archive_depth",
            "binary_sha256",
            "git_version",
            "name",
            "official_release_checksum_verified",
            "result_counts",
            "version",
        },
        "scanner",
    )
    if scanner["name"] != "gitleaks" or scanner["version"] != "8.30.1":
        raise DisclosureReportError("disclosure_report_scanner_identity_invalid")
    if scanner["git_version"] != "2.50.1" or scanner["archive_depth"] != 5:
        raise DisclosureReportError("disclosure_report_scanner_context_invalid")
    if scanner["official_release_checksum_verified"] is not True:
        raise DisclosureReportError("disclosure_report_scanner_checksum_unverified")
    if not isinstance(scanner["binary_sha256"], str) or SHA256_PATTERN.fullmatch(scanner["binary_sha256"]) is None:
        raise DisclosureReportError("disclosure_report_scanner_hash_invalid")
    counts = _object(scanner["result_counts"], "scanner_result_counts")
    expected = {
        "artifact_email",
        "artifact_generic",
        "artifact_path",
        "current_tree_generic",
        "git_email",
        "git_generic",
        "git_path",
        "github_surface_email",
        "github_surface_generic",
        "github_surface_path",
        "run_log_email",
        "run_log_generic",
        "run_log_path",
    }
    _keys(counts, expected, "scanner_result_counts")
    for key in expected:
        _count(counts[key], f"scanner_result_{key}")
    return counts


def _validate_findings(raw_findings: Any) -> dict[str, str]:
    if not isinstance(raw_findings, list) or not raw_findings:
        raise DisclosureReportError("disclosure_report_findings_invalid")
    seen: set[str] = set()
    classifications: dict[str, str] = {}
    for raw in raw_findings:
        finding = _object(raw, "finding")
        _keys(finding, {"classification", "id", "occurrences", "surface", "unique_values"}, "finding")
        identifier = finding["id"]
        if not isinstance(identifier, str):
            raise DisclosureReportError("disclosure_report_finding_id_invalid")
        if identifier not in FINDING_IDS or identifier in seen:
            raise DisclosureReportError("disclosure_report_finding_id_invalid")
        seen.add(identifier)
        if (
            not isinstance(finding["surface"], str)
            or finding["surface"] not in SURFACES
            or not isinstance(finding["classification"], str)
            or finding["classification"] not in CLASSIFICATIONS
        ):
            raise DisclosureReportError("disclosure_report_finding_classification_invalid")
        classifications[identifier] = finding["classification"]
        if _count(finding["occurrences"], "finding_occurrences") < 1:
            raise DisclosureReportError("disclosure_report_finding_occurrences_invalid")
        unique = finding["unique_values"]
        if unique is not None:
            unique = _count(unique, "finding_unique_values")
            if unique < 1 or unique > finding["occurrences"]:
                raise DisclosureReportError("disclosure_report_finding_unique_values_invalid")
    if seen != FINDING_IDS:
        raise DisclosureReportError("disclosure_report_finding_set_incomplete")
    owner_classification = classifications["commit_metadata_owner_email"]
    if owner_classification not in {
        "decision_required",
        "owner_approved_public_disclosure",
        "removed",
    }:
        raise DisclosureReportError("disclosure_report_owner_email_classification_invalid")
    return classifications


def _validate_scan_finding_counts(scan_counts: Mapping[str, Any], raw_findings: Any) -> None:
    findings = {item["id"]: item["occurrences"] for item in raw_findings}
    expected = {
        "git_generic": findings["history_generic_synthetic_identifiers"]
        + findings["history_generic_documentation_values"],
        "git_path": findings["history_credential_url_test_fixtures"]
        + findings["history_private_path_false_positive"],
        "git_email": findings["history_file_email_synthetic_values"],
        "artifact_generic": findings["artifact_generic_synthetic_identifiers"],
        "artifact_path": findings["artifact_credential_url_test_fixtures"]
        + findings["artifact_private_path_false_positive"],
        "artifact_email": findings["artifact_email_dependency_metadata_and_fixtures"],
        "github_surface_generic": findings["github_surface_hash_values"]
        + findings["collector_authenticated_clone_credential"],
        "github_surface_path": 0,
        "github_surface_email": findings["github_surface_ssh_url_email_false_positives"],
        "run_log_generic": 0,
        "run_log_path": 0,
        "run_log_email": 0,
        "current_tree_generic": 0,
    }
    if dict(scan_counts) != expected:
        raise DisclosureReportError("disclosure_report_scan_finding_counts_invalid")


def _validate_licensing(licensing: Mapping[str, Any]) -> None:
    required = {
        "apache_license_sha256",
        "application",
        "container_sbom_components_reviewed",
        "dependency_licenses_remain_authoritative",
        "experimental_package",
        "known_incompatible_inclusions",
        "legal_opinion_claimed",
        "license_files_missing",
        "package_archives_checked",
        "permissive_dependencies",
        "resolved_python_distributions",
        "unknown_or_changed_licenses",
        "weak_copyleft_dependencies",
    }
    _keys(licensing, required, "licensing")
    if not isinstance(licensing["apache_license_sha256"], str) or SHA256_PATTERN.fullmatch(licensing["apache_license_sha256"]) is None:
        raise DisclosureReportError("disclosure_report_license_hash_invalid")
    for key in required - {
        "apache_license_sha256",
        "dependency_licenses_remain_authoritative",
        "legal_opinion_claimed",
    }:
        _count(licensing[key], f"licensing_{key}")
    category_total = (
        licensing["application"]
        + licensing["experimental_package"]
        + licensing["permissive_dependencies"]
        + licensing["weak_copyleft_dependencies"]
    )
    if category_total != licensing["resolved_python_distributions"]:
        raise DisclosureReportError("disclosure_report_license_counts_invalid")
    if licensing["known_incompatible_inclusions"] != 0:
        raise DisclosureReportError("disclosure_report_known_incompatible_license")
    if licensing["license_files_missing"] != 0 or licensing["unknown_or_changed_licenses"] != 0:
        raise DisclosureReportError("disclosure_report_license_evidence_incomplete")
    if licensing["dependency_licenses_remain_authoritative"] is not True:
        raise DisclosureReportError("disclosure_report_dependency_license_boundary_invalid")
    if licensing["legal_opinion_claimed"] is not False:
        raise DisclosureReportError("disclosure_report_legal_opinion_claim_invalid")


def _validate_publication(publication: Mapping[str, Any]) -> None:
    _keys(
        publication,
        {
            "owner_authorization",
            "historical_owner_email_disposition",
            "actions_cache_disposition",
            "server_unadvertised_object_scope",
            "final_candidate_delta_audit",
            "raw_audit_material_committed",
            "repository_visibility",
            "visibility_changed",
        },
        "publication",
    )
    if not isinstance(publication["repository_visibility"], str) or publication["repository_visibility"] not in {"private", "public"}:
        raise DisclosureReportError("disclosure_report_visibility_invalid")
    if not isinstance(publication["owner_authorization"], str) or publication["owner_authorization"] not in {"approved", "pending"}:
        raise DisclosureReportError("disclosure_report_authorization_invalid")
    if not isinstance(publication["historical_owner_email_disposition"], str) or publication["historical_owner_email_disposition"] not in {
        "owner_approved_public_disclosure",
        "pending",
        "removed",
    }:
        raise DisclosureReportError("disclosure_report_owner_email_disposition_invalid")
    if not isinstance(publication["actions_cache_disposition"], str) or publication["actions_cache_disposition"] not in {
        "deleted",
        "owner_accepted_risk",
        "pending",
    }:
        raise DisclosureReportError("disclosure_report_cache_disposition_invalid")
    if not isinstance(publication["server_unadvertised_object_scope"], str) or publication["server_unadvertised_object_scope"] not in {
        "accepted_explicit_nonclaim",
        "pending",
        "sanitized_repository",
    }:
        raise DisclosureReportError("disclosure_report_server_scope_disposition_invalid")
    if not isinstance(publication["final_candidate_delta_audit"], str) or publication["final_candidate_delta_audit"] not in {"complete", "pending"}:
        raise DisclosureReportError("disclosure_report_delta_audit_disposition_invalid")
    if not isinstance(publication["visibility_changed"], bool):
        raise DisclosureReportError("disclosure_report_visibility_change_invalid")
    if publication["raw_audit_material_committed"] is not False:
        raise DisclosureReportError("disclosure_report_raw_material_committed")


def _validate_blockers(raw_blockers: Any) -> set[str]:
    if not isinstance(raw_blockers, list):
        raise DisclosureReportError("disclosure_report_blockers_invalid")
    seen: set[str] = set()
    for raw in raw_blockers:
        blocker = _object(raw, "blocker")
        _keys(blocker, {"id", "required_action", "status"}, "blocker")
        identifier = blocker["id"]
        if not isinstance(identifier, str) or identifier not in BLOCKER_ACTIONS or identifier in seen:
            raise DisclosureReportError("disclosure_report_blocker_id_invalid")
        seen.add(identifier)
        if (
            not isinstance(blocker["status"], str)
            or blocker["status"] not in BLOCKER_STATUSES
            or blocker["required_action"] != BLOCKER_ACTIONS[identifier]
        ):
            raise DisclosureReportError("disclosure_report_blocker_classification_invalid")
    return seen


def _validate_resolution_consistency(
    actions: Mapping[str, Any],
    findings: Mapping[str, str],
    publication: Mapping[str, Any],
    blockers: set[str],
) -> None:
    expected_blockers = set()
    if publication["historical_owner_email_disposition"] == "pending":
        expected_blockers.add("historical_owner_email_disclosure")
    if publication["actions_cache_disposition"] == "pending":
        expected_blockers.add("actions_cache_disposition")
    if publication["server_unadvertised_object_scope"] == "pending":
        expected_blockers.add("server_unadvertised_object_scope")
    if publication["final_candidate_delta_audit"] == "pending":
        expected_blockers.add("final_candidate_delta_audit")
    if publication["owner_authorization"] == "pending":
        expected_blockers.add("repository_visibility_authorization")
    if blockers != expected_blockers:
        raise DisclosureReportError("disclosure_report_blocker_resolution_mismatch")

    owner_disposition = publication["historical_owner_email_disposition"]
    owner_classification = findings["commit_metadata_owner_email"]
    expected_owner_classification = {
        "owner_approved_public_disclosure": "owner_approved_public_disclosure",
        "pending": "decision_required",
        "removed": "removed",
    }[owner_disposition]
    if owner_classification != expected_owner_classification:
        raise DisclosureReportError("disclosure_report_owner_email_resolution_mismatch")
    cache_disposition = publication["actions_cache_disposition"]
    if cache_disposition == "deleted" and actions["caches"] != 0:
        raise DisclosureReportError("disclosure_report_deleted_caches_still_present")
    if cache_disposition == "owner_accepted_risk" and actions["caches"] == 0:
        raise DisclosureReportError("disclosure_report_cache_risk_without_caches")


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DisclosureReportError(f"disclosure_report_{label}_not_object")
    return value


def _keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise DisclosureReportError(f"disclosure_report_{label}_keys_invalid")


def _count(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DisclosureReportError(f"disclosure_report_{label}_invalid")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DisclosureReportError(f"disclosure_report_{label}_invalid")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise DisclosureReportError(f"disclosure_report_{label}_invalid") from error


if __name__ == "__main__":
    raise SystemExit(main())
