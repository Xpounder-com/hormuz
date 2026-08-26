#!/usr/bin/env python3
"""Build and validate Hormuz's content-free disaster-recovery evidence.

The disposable shell rehearsal owns infrastructure orchestration.  This module
owns the claim: it accepts one closed observation schema, derives achieved RPO
and both recovery clocks, and refuses incomplete or overly broad evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_ID = "hormuz.disaster-recovery-reference-proof"
SCHEMA_VERSION = 1
PROFILE = "kubernetes_enterprise_reference"
PLATFORM = "linux/amd64"
CLASSIFICATION = "reference_rehearsal_acceptance_criteria"
RPO_LIMIT_SECONDS = 300
INTERNAL_RTO_LIMIT_SECONDS = 3_600

HORMUZ_IMAGE = (
    "ghcr.io/xpounder-com/hormuz@"
    "sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67"
)
POSTGRES_IMAGE = (
    "ghcr.io/cloudnative-pg/postgresql:16.15-202608240846-minimal-trixie@"
    "sha256:e1ca593856017f1780dbdae8175add3ddd8f8d721348a3b6e8a01df67a9ece8a"
)
OPENBAO_IMAGE = (
    "openbao/openbao@"
    "sha256:d0424c95859f7b4c1e308abf57c4cd72b9cba835bb946eb397172b799fba9477"
)
CNPG_VERSION = "1.30.0"
KIND_VERSION = "v0.32.0"
KUBERNETES_VERSION = "v1.36.1"
HELM_VERSION = "v3.21.4"
CILIUM_VERSION = "1.20.1"

STATE_CLASSES = (
    "runtime_configuration_generations",
    "runtime_secret_and_credential_envelopes",
    "identity_source_and_event_time_bindings",
    "schema_migration_ledger",
    "usage_cost_and_security_evidence",
    "budget_reservations",
    "request_attempt_ledger",
    "policy_authority_versions_and_activation",
    "audit_chain_history_and_checkpoint_metadata",
    "custody_authority_intents_approvals_and_events",
    "custody_execution_and_lifecycle_history",
    "custody_runtime_projection_and_coordination",
    "immutable_audit_anchor_objects",
)

ADMISSION_CHECKS = (
    "audit_chain_epoch_and_checkpoint_continuity",
    "backup_manifest_and_wal_complete",
    "budget_and_uncertain_reservations_preserved",
    "configuration_generation_fingerprint_matches",
    "custody_authority_lifecycle_and_retention_preserved",
    "custody_key_canary_decrypts",
    "custody_projection_and_coordination_ready",
    "event_time_identity_bindings_preserved",
    "external_anchor_matches_latest_checkpoint",
    "migration_ledger_current_and_contiguous",
    "policy_authority_and_active_version_preserved",
    "request_attempt_history_preserved_without_replay",
    "runtime_secret_generation_fingerprint_matches",
    "tenant_isolation_enforced",
    "usage_and_security_evidence_preserved",
)

FAILURE_PATHS = (
    "missing_wal_archive",
    "corrupted_backup",
    "unavailable_custody_key",
    "stale_checkpoint",
    "partial_restore",
    "failed_coordination",
    "cross_tenant_access",
)

TIMESTAMP_FIELDS = (
    "failure_injection_at",
    "incident_detected_at",
    "incident_declared_at",
    "authorized_recovery_execution_started_at",
    "restore_started_at",
    "recovered_database_ready_at",
    "admission_passed_at",
    "required_failure_paths_passed_at",
    "recovered_environment_ready_for_promotion_at",
    "traffic_promoted_at",
    "first_successful_governed_request_after_promotion_at",
)

PHASE_INTERVALS = (
    ("detection", "failure_injection_at", "incident_detected_at"),
    ("incident_declaration", "incident_detected_at", "incident_declared_at"),
    (
        "recovery_authorization",
        "incident_declared_at",
        "authorized_recovery_execution_started_at",
    ),
    (
        "recovery_environment_preparation",
        "authorized_recovery_execution_started_at",
        "restore_started_at",
    ),
    ("restore_and_wal_replay", "restore_started_at", "recovered_database_ready_at"),
    ("admission_validation", "recovered_database_ready_at", "admission_passed_at"),
    (
        "required_failure_path_validation",
        "admission_passed_at",
        "required_failure_paths_passed_at",
    ),
    (
        "application_startup",
        "required_failure_paths_passed_at",
        "recovered_environment_ready_for_promotion_at",
    ),
    (
        "traffic_promotion",
        "recovered_environment_ready_for_promotion_at",
        "traffic_promoted_at",
    ),
    (
        "first_governed_request",
        "traffic_promoted_at",
        "first_successful_governed_request_after_promotion_at",
    ),
)

LIMITATIONS = (
    "account_free_disposable_kind_environment_only",
    "compose_profile_excluded",
    "exact_pinned_combination_only",
    "no_availability_zone_or_regional_failure_claim",
    "no_broad_kubernetes_cni_or_postgresql_distribution_certification",
    "no_customer_infrastructure_certification",
    "no_customer_sla",
    "no_tenant_export_or_deletion_claim",
    "openbao_is_a_customer_controlled_custody_canary_reference_only",
    "reference_backup_policy_is_documented_not_live_retention_or_encryption_certification",
)

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
_FORBIDDEN = (
    re.compile(r"/Users/"),
    re.compile(r"/home/runner/"),
    re.compile(r"/private/tmp/"),
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"\bBearer\s+", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
)

_DATABASE_STATE_CLASSES = STATE_CLASSES[2:-1]
_SNAPSHOT_SCHEMA_ID = "hormuz.disaster-recovery-state-snapshot"
_CHECKPOINT_SCHEMA_ID = "hormuz.audit-chain-checkpoint"


class DisasterRecoveryProofError(RuntimeError):
    """A stable failure for an incomplete or unsafe recovery claim."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write-evidence")
    write.add_argument("--observations", required=True, type=Path)
    write.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("--evidence", required=True, type=Path)
    admit = commands.add_parser("admit")
    admit.add_argument("--input", required=True, type=Path)
    admit.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "write-evidence":
            evidence = build_evidence(load_json(args.observations))
            write_exclusive(args.output, evidence)
            print("wrote content-free disaster-recovery reference evidence")
        elif args.command == "validate":
            validate_evidence(load_json(args.evidence))
            print("verified disaster-recovery reference: verdict=verified")
        else:
            admission = build_admission(load_json(args.input))
            write_json_exclusive(args.output, admission)
            print("recovered environment passed disaster-recovery admission")
    except (
        DisasterRecoveryProofError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        print(f"Disaster-recovery reference proof failed: {error}", file=sys.stderr)
        return 1
    return 0


def build_admission(value: Mapping[str, Any]) -> dict[str, Any]:
    """Compare source, recovered, and external state before Helm promotion."""

    _exact_keys(
        value,
        {
            "source_snapshot",
            "recovered_snapshot",
            "current_checkpoint",
            "configuration",
            "secret_envelope",
            "custody_key_canary_verified",
        },
        "admission_input",
    )
    source = _validate_snapshot(value["source_snapshot"], "source_snapshot")
    recovered = _validate_snapshot(value["recovered_snapshot"], "recovered_snapshot")
    if source["organization_id"] != recovered["organization_id"]:
        raise DisasterRecoveryProofError("recovery_tenant_mismatch")
    if source["manifest_fingerprint"] != recovered["manifest_fingerprint"]:
        raise DisasterRecoveryProofError("recovery_state_manifest_mismatch")
    if source["state_classes"] != recovered["state_classes"]:
        raise DisasterRecoveryProofError("recovery_state_class_mismatch")
    if source["admission_facts"] != recovered["admission_facts"]:
        raise DisasterRecoveryProofError("recovery_admission_facts_mismatch")
    if value["custody_key_canary_verified"] is not True:
        raise DisasterRecoveryProofError("recovery_custody_key_unavailable")

    checkpoint = _validate_checkpoint(value["current_checkpoint"])
    facts = recovered["admission_facts"]
    if (
        checkpoint["organization_id"] != recovered["organization_id"]
        or checkpoint["chain_epoch"] != facts["audit_chain_epoch"]
        or checkpoint["sequence"] != facts["current_checkpoint_sequence"]
        or checkpoint["sequence"] <= facts["stale_checkpoint_sequence"]
    ):
        raise DisasterRecoveryProofError("recovery_checkpoint_not_latest")
    if facts["unresolved_coordination_barriers"] != 0:
        raise DisasterRecoveryProofError("recovery_coordination_incomplete")
    if facts["tenant_isolation_rows"] != 0:
        raise DisasterRecoveryProofError("recovery_tenant_isolation_failed")

    configuration = _validate_external_generation(value["configuration"], "configuration")
    secret = _validate_external_generation(value["secret_envelope"], "secret_envelope")
    checkpoint_digest = "sha256:" + hashlib.sha256(
        json.dumps(checkpoint, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    state_classes: dict[str, dict[str, object]] = {
        "runtime_configuration_generations": configuration,
        "runtime_secret_and_credential_envelopes": secret,
    }
    for identifier in _DATABASE_STATE_CLASSES:
        item = recovered["state_classes"][identifier]
        state_classes[identifier] = {
            "source_fingerprint": item["fingerprint"],
            "recovered_fingerprint": item["fingerprint"],
            "latest_recovered_committed_marker_at": item[
                "latest_committed_marker_at"
            ],
        }
    state_classes["immutable_audit_anchor_objects"] = {
        "source_fingerprint": checkpoint_digest,
        "recovered_fingerprint": checkpoint_digest,
        "latest_recovered_committed_marker_at": checkpoint["created_at"],
    }
    if tuple(state_classes) != STATE_CLASSES:
        raise DisasterRecoveryProofError("recovery_state_coverage_order_invalid")
    output = {
        "schema_id": "hormuz.disaster-recovery-admission",
        "schema_version": 1,
        "organization_id": recovered["organization_id"],
        "source_state_manifest_sha256": source["manifest_fingerprint"],
        "recovered_state_manifest_sha256": recovered["manifest_fingerprint"],
        "state_classes": state_classes,
        "checks": list(ADMISSION_CHECKS),
        "admitted": True,
    }
    _validate_admission_output(output)
    return output


def build_evidence(observations: Mapping[str, Any]) -> dict[str, Any]:
    validate_observations(observations)
    timestamps = {name: _timestamp(observations["timestamps"][name], name) for name in TIMESTAMP_FIELDS}
    failure_at = timestamps["failure_injection_at"]
    backup_completed_at = _timestamp(
        observations["backup"]["base_backup_completed_at"], "base_backup_completed_at"
    )
    if backup_completed_at >= failure_at:
        raise DisasterRecoveryProofError("backup_not_created_before_failure")
    classes: list[dict[str, Any]] = []
    for identifier in STATE_CLASSES:
        source = observations["state_classes"][identifier]
        marker = _timestamp(source["latest_recovered_committed_marker_at"], identifier)
        gap = (failure_at - marker).total_seconds()
        if gap < 0 or gap > RPO_LIMIT_SECONDS:
            raise DisasterRecoveryProofError(f"rpo_{identifier}_exceeded")
        classes.append(
            {
                "id": identifier,
                "source_fingerprint": source["source_fingerprint"],
                "recovered_fingerprint": source["recovered_fingerprint"],
                "latest_recovered_committed_marker_at": source[
                    "latest_recovered_committed_marker_at"
                ],
                "gap_seconds": round(gap, 3),
                "fingerprint_matches": True,
            }
        )

    internal_rto_ms = _duration_ms(
        timestamps["authorized_recovery_execution_started_at"],
        timestamps["recovered_environment_ready_for_promotion_at"],
        "internal_rto",
    )
    if internal_rto_ms > INTERNAL_RTO_LIMIT_SECONDS * 1_000:
        raise DisasterRecoveryProofError("internal_rto_exceeded")
    end_to_end_ms = _duration_ms(
        timestamps["failure_injection_at"],
        timestamps["first_successful_governed_request_after_promotion_at"],
        "end_to_end_recovery",
    )
    maximum_rpo = max(item["gap_seconds"] for item in classes)

    evidence: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "verdict": "verified",
        "classification": CLASSIFICATION,
        "profile": PROFILE,
        "platform": PLATFORM,
        "source_commit": observations["source_commit"],
        "product_boundary": {
            "application_contract": "signed_oci_digest_with_generic_postgresql_dsn",
            "cloudnativepg_role": "source_ha_verification_infrastructure_only",
            "helm_chart_installs_postgresql": False,
            "customer_controls_backup_restore_keys_and_promotion": True,
        },
        "versions": {
            "hormuz_image": HORMUZ_IMAGE,
            "postgresql_image": POSTGRES_IMAGE,
            "openbao_image": OPENBAO_IMAGE,
            "cloudnativepg": CNPG_VERSION,
            "kind": KIND_VERSION,
            "kubernetes": KUBERNETES_VERSION,
            "helm": HELM_VERSION,
            "cilium": CILIUM_VERSION,
            "docker_engine": observations["docker_engine"],
            "helm_chart_sha256": observations["helm_chart_sha256"],
        },
        "objectives": {
            "rpo_limit_seconds": RPO_LIMIT_SECONDS,
            "achieved_maximum_rpo_seconds": maximum_rpo,
            "internal_rto_limit_seconds": INTERNAL_RTO_LIMIT_SECONDS,
            "achieved_internal_rto_ms": internal_rto_ms,
            "complete_end_to_end_recovery_ms": end_to_end_ms,
            "customer_sla": False,
        },
        "timestamps": dict(observations["timestamps"]),
        "phase_durations_ms": dict(observations["phase_durations_ms"]),
        "backup": dict(observations["backup"]),
        "retention_and_authority": dict(observations["retention_and_authority"]),
        "state_coverage": classes,
        "admission": {
            "isolated_before_promotion": True,
            "checks": list(ADMISSION_CHECKS),
            **dict(observations["admission"]),
        },
        "failure_paths": dict(observations["failure_paths"]),
        "promotion": dict(observations["promotion"]),
        "limitations": list(LIMITATIONS),
    }
    validate_evidence(evidence)
    return evidence


def validate_observations(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "source_commit",
            "docker_engine",
            "helm_chart_sha256",
            "timestamps",
            "phase_durations_ms",
            "backup",
            "retention_and_authority",
            "state_classes",
            "admission",
            "failure_paths",
            "promotion",
        },
        "observations",
    )
    _commit(value["source_commit"])
    _safe_text(value["docker_engine"], "docker_engine")
    _digest(value["helm_chart_sha256"], "helm_chart_sha256", prefix=False)
    timestamps = _validate_timestamps(value["timestamps"])
    _validate_phase_durations(value["phase_durations_ms"], timestamps)
    _validate_backup(value["backup"])
    _validate_retention(value["retention_and_authority"])
    _validate_state_classes(value["state_classes"])
    _validate_admission(value["admission"])
    _validate_failure_paths(value["failure_paths"])
    _validate_promotion(value["promotion"])


def validate_evidence(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "generated_at",
            "verdict",
            "classification",
            "profile",
            "platform",
            "source_commit",
            "product_boundary",
            "versions",
            "objectives",
            "timestamps",
            "phase_durations_ms",
            "backup",
            "retention_and_authority",
            "state_coverage",
            "admission",
            "failure_paths",
            "promotion",
            "limitations",
        },
        "evidence",
    )
    if (
        value["schema_id"],
        value["schema_version"],
        value["verdict"],
        value["classification"],
        value["profile"],
        value["platform"],
    ) != (
        SCHEMA_ID,
        SCHEMA_VERSION,
        "verified",
        CLASSIFICATION,
        PROFILE,
        PLATFORM,
    ):
        raise DisasterRecoveryProofError("evidence_claim_invalid")
    generated_at = _timestamp(value["generated_at"], "generated_at")
    _commit(value["source_commit"])
    if value["product_boundary"] != {
        "application_contract": "signed_oci_digest_with_generic_postgresql_dsn",
        "cloudnativepg_role": "source_ha_verification_infrastructure_only",
        "helm_chart_installs_postgresql": False,
        "customer_controls_backup_restore_keys_and_promotion": True,
    }:
        raise DisasterRecoveryProofError("product_boundary_invalid")
    versions = _mapping(value["versions"], "versions")
    expected_versions = {
        "hormuz_image": HORMUZ_IMAGE,
        "postgresql_image": POSTGRES_IMAGE,
        "openbao_image": OPENBAO_IMAGE,
        "cloudnativepg": CNPG_VERSION,
        "kind": KIND_VERSION,
        "kubernetes": KUBERNETES_VERSION,
        "helm": HELM_VERSION,
        "cilium": CILIUM_VERSION,
    }
    if any(versions.get(key) != expected for key, expected in expected_versions.items()):
        raise DisasterRecoveryProofError("versions_invalid")
    _safe_text(versions.get("docker_engine"), "docker_engine")
    _digest(versions.get("helm_chart_sha256"), "helm_chart_sha256", prefix=False)
    if set(versions) != {*expected_versions, "docker_engine", "helm_chart_sha256"}:
        raise DisasterRecoveryProofError("versions_invalid")

    timestamps = _validate_timestamps(value["timestamps"])
    if generated_at < timestamps["first_successful_governed_request_after_promotion_at"]:
        raise DisasterRecoveryProofError("generated_at_precedes_rehearsal_completion")
    _validate_phase_durations(value["phase_durations_ms"], timestamps)
    _validate_backup(value["backup"])
    if _timestamp(value["backup"]["base_backup_completed_at"], "base_backup_completed_at") >= timestamps[
        "failure_injection_at"
    ]:
        raise DisasterRecoveryProofError("backup_not_created_before_failure")
    _validate_retention(value["retention_and_authority"])
    _validate_failure_paths(value["failure_paths"])
    _validate_promotion(value["promotion"])

    objectives = _mapping(value["objectives"], "objectives")
    _exact_keys(
        objectives,
        {
            "rpo_limit_seconds",
            "achieved_maximum_rpo_seconds",
            "internal_rto_limit_seconds",
            "achieved_internal_rto_ms",
            "complete_end_to_end_recovery_ms",
            "customer_sla",
        },
        "objectives",
    )
    if (
        objectives["rpo_limit_seconds"] != RPO_LIMIT_SECONDS
        or objectives["internal_rto_limit_seconds"] != INTERNAL_RTO_LIMIT_SECONDS
        or objectives["customer_sla"] is not False
    ):
        raise DisasterRecoveryProofError("objectives_invalid")
    achieved_rpo = _nonnegative_number(
        objectives["achieved_maximum_rpo_seconds"], "achieved_maximum_rpo_seconds"
    )
    internal_rto = _positive_int(objectives["achieved_internal_rto_ms"], "achieved_internal_rto_ms")
    end_to_end = _positive_int(
        objectives["complete_end_to_end_recovery_ms"],
        "complete_end_to_end_recovery_ms",
    )
    expected_internal = _duration_ms(
        timestamps["authorized_recovery_execution_started_at"],
        timestamps["recovered_environment_ready_for_promotion_at"],
        "internal_rto",
    )
    expected_end_to_end = _duration_ms(
        timestamps["failure_injection_at"],
        timestamps["first_successful_governed_request_after_promotion_at"],
        "end_to_end_recovery",
    )
    if (
        achieved_rpo > RPO_LIMIT_SECONDS
        or internal_rto > INTERNAL_RTO_LIMIT_SECONDS * 1_000
        or internal_rto != expected_internal
        or end_to_end != expected_end_to_end
    ):
        raise DisasterRecoveryProofError("objectives_exceeded_or_inconsistent")

    coverage = value["state_coverage"]
    if not isinstance(coverage, list) or len(coverage) != len(STATE_CLASSES):
        raise DisasterRecoveryProofError("state_coverage_invalid")
    observed_maximum = 0.0
    for expected_id, item in zip(STATE_CLASSES, coverage, strict=True):
        _exact_keys(
            item,
            {
                "id",
                "source_fingerprint",
                "recovered_fingerprint",
                "latest_recovered_committed_marker_at",
                "gap_seconds",
                "fingerprint_matches",
            },
            "state_coverage_entry",
        )
        if item["id"] != expected_id or item["fingerprint_matches"] is not True:
            raise DisasterRecoveryProofError("state_coverage_invalid")
        _digest(item["source_fingerprint"], "source_fingerprint")
        _digest(item["recovered_fingerprint"], "recovered_fingerprint")
        if item["source_fingerprint"] != item["recovered_fingerprint"]:
            raise DisasterRecoveryProofError("state_fingerprint_mismatch")
        marker = _timestamp(item["latest_recovered_committed_marker_at"], expected_id)
        expected_gap = round((timestamps["failure_injection_at"] - marker).total_seconds(), 3)
        gap = _nonnegative_number(item["gap_seconds"], "gap_seconds")
        if gap != expected_gap or gap > RPO_LIMIT_SECONDS:
            raise DisasterRecoveryProofError(f"rpo_{expected_id}_invalid")
        observed_maximum = max(observed_maximum, gap)
    if achieved_rpo != observed_maximum:
        raise DisasterRecoveryProofError("rpo_maximum_inconsistent")

    admission = _mapping(value["admission"], "admission")
    if admission.get("isolated_before_promotion") is not True:
        raise DisasterRecoveryProofError("admission_isolation_invalid")
    if admission.get("checks") != list(ADMISSION_CHECKS):
        raise DisasterRecoveryProofError("admission_checks_invalid")
    _validate_admission(
        {key: item for key, item in admission.items() if key not in {"isolated_before_promotion", "checks"}}
    )
    if value["limitations"] != list(LIMITATIONS):
        raise DisasterRecoveryProofError("limitations_invalid")
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if any(pattern.search(serialized) for pattern in _FORBIDDEN):
        raise DisasterRecoveryProofError("evidence_contains_forbidden_content")


def _validate_timestamps(value: Any) -> dict[str, datetime]:
    _exact_keys(value, set(TIMESTAMP_FIELDS), "timestamps")
    parsed = {name: _timestamp(value[name], name) for name in TIMESTAMP_FIELDS}
    ordered = [parsed[name] for name in TIMESTAMP_FIELDS]
    if ordered != sorted(ordered):
        raise DisasterRecoveryProofError("timestamps_not_ordered")
    return parsed


def _validate_phase_durations(
    value: Any,
    timestamps: Mapping[str, datetime],
) -> None:
    expected = {name for name, _, _ in PHASE_INTERVALS}
    _exact_keys(value, expected, "phase_durations_ms")
    for name, start, end in PHASE_INTERVALS:
        duration = _positive_int(value[name], name)
        if duration != _duration_ms(timestamps[start], timestamps[end], name):
            raise DisasterRecoveryProofError(f"phase_duration_{name}_invalid")


def _validate_backup(value: Any) -> None:
    _exact_keys(
        value,
        {
            "method",
            "base_backup_sha256",
            "backup_manifest_sha256",
            "wal_archive_sha256",
            "wal_segment_count",
            "base_backup_completed_at",
            "pg_verifybackup_passed",
            "backup_completed_before_failure",
            "named_restore_point_reached",
        },
        "backup",
    )
    if value["method"] != "physical_base_backup_plus_continuous_wal":
        raise DisasterRecoveryProofError("backup_method_invalid")
    for key in ("base_backup_sha256", "backup_manifest_sha256", "wal_archive_sha256"):
        _digest(value[key], key)
    _positive_int(value["wal_segment_count"], "wal_segment_count")
    _timestamp(value["base_backup_completed_at"], "base_backup_completed_at")
    for key in (
        "pg_verifybackup_passed",
        "backup_completed_before_failure",
        "named_restore_point_reached",
    ):
        if value[key] is not True:
            raise DisasterRecoveryProofError("backup_validation_failed")


def _validate_retention(value: Any) -> None:
    expected = {
        "base_backup_frequency_seconds": 86_400,
        "wal_archive_continuous": True,
        "backup_retention_days": 35,
        "wal_retention_days": 35,
        "encryption_at_rest_required": True,
        "backup_writer_cannot_restore_or_promote": True,
        "runtime_cannot_backup_restore_or_promote": True,
        "restore_requires_authorized_operator": True,
        "monitor_backup_age_wal_lag_and_restore_tests": True,
        "expiry_never_shortens_immutable_audit_retention": True,
    }
    if value != expected:
        raise DisasterRecoveryProofError("retention_and_authority_invalid")


def _validate_state_classes(value: Any) -> None:
    _exact_keys(value, set(STATE_CLASSES), "state_classes")
    for identifier in STATE_CLASSES:
        item = value[identifier]
        _exact_keys(
            item,
            {
                "source_fingerprint",
                "recovered_fingerprint",
                "latest_recovered_committed_marker_at",
            },
            identifier,
        )
        _digest(item["source_fingerprint"], f"{identifier}_source_fingerprint")
        _digest(item["recovered_fingerprint"], f"{identifier}_recovered_fingerprint")
        if item["source_fingerprint"] != item["recovered_fingerprint"]:
            raise DisasterRecoveryProofError(f"{identifier}_fingerprint_mismatch")
        _timestamp(item["latest_recovered_committed_marker_at"], identifier)


def _validate_admission(value: Any) -> None:
    expected = {
        "source_state_manifest_sha256",
        "recovered_state_manifest_sha256",
        "gateway_replicas_ready",
        "readiness_withheld_until_validation",
        "provider_requests_before_promotion",
    }
    _exact_keys(value, expected, "admission")
    _digest(value["source_state_manifest_sha256"], "source_state_manifest_sha256")
    _digest(value["recovered_state_manifest_sha256"], "recovered_state_manifest_sha256")
    if value["source_state_manifest_sha256"] != value["recovered_state_manifest_sha256"]:
        raise DisasterRecoveryProofError("state_manifest_mismatch")
    if value["gateway_replicas_ready"] != 2 or value["readiness_withheld_until_validation"] is not True:
        raise DisasterRecoveryProofError("admission_readiness_invalid")
    if value["provider_requests_before_promotion"] != 0:
        raise DisasterRecoveryProofError("pre_promotion_provider_egress_detected")


def _validate_failure_paths(value: Any) -> None:
    _exact_keys(value, set(FAILURE_PATHS), "failure_paths")
    expected = {
        "failure_observed": True,
        "admission_denied": True,
        "promotion_blocked": True,
        "provider_request_delta": 0,
    }
    for name in FAILURE_PATHS:
        if value[name] != expected:
            raise DisasterRecoveryProofError(f"failure_path_{name}_invalid")


def _validate_promotion(value: Any) -> None:
    expected_keys = {
        "authorized_operator_promoted",
        "runtime_credential_cannot_promote",
        "first_governed_request_status",
        "provider_requests_after_first_governed_request",
        "automatic_provider_replays",
        "rollback_target_preserved",
    }
    _exact_keys(value, expected_keys, "promotion")
    if (
        value["authorized_operator_promoted"] is not True
        or value["runtime_credential_cannot_promote"] is not True
        or value["first_governed_request_status"] != 200
        or value["provider_requests_after_first_governed_request"] != 1
        or value["automatic_provider_replays"] != 0
        or value["rollback_target_preserved"] is not True
    ):
        raise DisasterRecoveryProofError("promotion_invalid")


def _validate_snapshot(value: Any, field: str) -> Mapping[str, Any]:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "command",
            "organization_id",
            "manifest_fingerprint",
            "state_classes",
            "admission_facts",
        },
        field,
    )
    if (
        value["schema_id"] != _SNAPSHOT_SCHEMA_ID
        or value["schema_version"] != 1
        or value["command"] != "snapshot"
        or value["organization_id"] != "kubernetes-proof-organization"
    ):
        raise DisasterRecoveryProofError(f"{field}_schema_invalid")
    _digest(value["manifest_fingerprint"], f"{field}_manifest_fingerprint")
    classes = value["state_classes"]
    _exact_keys(classes, set(_DATABASE_STATE_CLASSES), f"{field}_state_classes")
    for identifier in _DATABASE_STATE_CLASSES:
        item = classes[identifier]
        _exact_keys(
            item,
            {"fingerprint", "latest_committed_marker_at", "record_count"},
            f"{field}_{identifier}",
        )
        _digest(item["fingerprint"], f"{field}_{identifier}_fingerprint")
        _timestamp(item["latest_committed_marker_at"], identifier)
        _positive_int(item["record_count"], f"{field}_{identifier}_record_count")
    facts = value["admission_facts"]
    expected_fact_keys = {
        "policy_active_version",
        "policy_generation",
        "policy_administrator_count",
        "custody_administrator_count",
        "custody_retention_days",
        "custody_legal_hold",
        "custody_projection_version",
        "custody_restriction",
        "unresolved_coordination_barriers",
        "outcome_unknown_attempts",
        "uncertain_reservations",
        "audit_chain_epoch",
        "audit_chain_sequence",
        "current_checkpoint_sequence",
        "stale_checkpoint_sequence",
        "tenant_isolation_rows",
        "migration_version",
    }
    _exact_keys(facts, expected_fact_keys, f"{field}_admission_facts")
    _safe_text(facts["policy_active_version"], "policy_active_version")
    if (
        facts["policy_generation"] != 1
        or facts["policy_administrator_count"] != 1
        or facts["custody_administrator_count"] != 2
        or facts["custody_retention_days"] != 365
        or facts["custody_legal_hold"] is not False
        or facts["custody_projection_version"] != 1
        or facts["custody_restriction"] != "provider_credential_disabled"
        or facts["unresolved_coordination_barriers"] != 0
        or _positive_int(facts["outcome_unknown_attempts"], "outcome_unknown_attempts") < 1
        or _positive_int(facts["uncertain_reservations"], "uncertain_reservations") < 1
        or _positive_int(facts["audit_chain_epoch"], "audit_chain_epoch") < 1
        or _positive_int(facts["audit_chain_sequence"], "audit_chain_sequence") < 1
        or _positive_int(facts["current_checkpoint_sequence"], "current_checkpoint_sequence")
        <= _positive_int(facts["stale_checkpoint_sequence"], "stale_checkpoint_sequence")
        or facts["tenant_isolation_rows"] != 0
        or _positive_int(facts["migration_version"], "migration_version") < 1
    ):
        raise DisasterRecoveryProofError(f"{field}_admission_invalid")
    return value


def _validate_checkpoint(value: Any) -> Mapping[str, Any]:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "checkpoint_id",
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "head_digest",
            "created_at",
        },
        "checkpoint",
    )
    if (
        value["schema_id"] != _CHECKPOINT_SCHEMA_ID
        or value["schema_version"] != 1
        or value["organization_id"] != "kubernetes-proof-organization"
    ):
        raise DisasterRecoveryProofError("checkpoint_schema_invalid")
    _safe_text(value["checkpoint_id"], "checkpoint_id")
    _positive_int(value["chain_version"], "chain_version")
    _positive_int(value["chain_epoch"], "chain_epoch")
    _positive_int(value["sequence"], "sequence")
    _digest(value["head_digest"], "head_digest", prefix=False)
    _timestamp(value["created_at"], "checkpoint_created_at")
    return value


def _validate_external_generation(value: Any, field: str) -> dict[str, object]:
    _exact_keys(
        value,
        {
            "source_fingerprint",
            "recovered_fingerprint",
            "latest_recovered_committed_marker_at",
        },
        field,
    )
    _digest(value["source_fingerprint"], f"{field}_source_fingerprint")
    _digest(value["recovered_fingerprint"], f"{field}_recovered_fingerprint")
    if value["source_fingerprint"] != value["recovered_fingerprint"]:
        raise DisasterRecoveryProofError(f"{field}_fingerprint_mismatch")
    _timestamp(value["latest_recovered_committed_marker_at"], field)
    return dict(value)


def _validate_admission_output(value: Any) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "source_state_manifest_sha256",
            "recovered_state_manifest_sha256",
            "state_classes",
            "checks",
            "admitted",
        },
        "admission_output",
    )
    if (
        value["schema_id"] != "hormuz.disaster-recovery-admission"
        or value["schema_version"] != 1
        or value["organization_id"] != "kubernetes-proof-organization"
        or value["admitted"] is not True
        or value["checks"] != list(ADMISSION_CHECKS)
    ):
        raise DisasterRecoveryProofError("admission_output_invalid")
    _digest(value["source_state_manifest_sha256"], "source_state_manifest_sha256")
    _digest(value["recovered_state_manifest_sha256"], "recovered_state_manifest_sha256")
    if value["source_state_manifest_sha256"] != value["recovered_state_manifest_sha256"]:
        raise DisasterRecoveryProofError("admission_output_manifest_mismatch")
    _validate_state_classes(value["state_classes"])


def load_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise DisasterRecoveryProofError("duplicate_json_key")
            result[key] = item
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    if not isinstance(value, dict):
        raise DisasterRecoveryProofError("json_object_required")
    return value


def write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    validate_evidence(value)
    write_json_exclusive(path, value)


def write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise DisasterRecoveryProofError("output_permissions_invalid")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DisasterRecoveryProofError(f"{field}_invalid")
    return value


def _exact_keys(value: Any, expected: set[str], field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise DisasterRecoveryProofError(f"{field}_fields_invalid")


def _commit(value: Any) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise DisasterRecoveryProofError("source_commit_invalid")
    return value


def _digest(value: Any, field: str, *, prefix: bool = True) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise DisasterRecoveryProofError(f"{field}_invalid")
    if prefix and not value.startswith("sha256:"):
        raise DisasterRecoveryProofError(f"{field}_invalid")
    if not prefix and value.startswith("sha256:"):
        raise DisasterRecoveryProofError(f"{field}_invalid")
    return value


def _safe_text(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise DisasterRecoveryProofError(f"{field}_invalid")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise DisasterRecoveryProofError(f"{field}_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DisasterRecoveryProofError(f"{field}_timestamp_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DisasterRecoveryProofError(f"{field}_timestamp_invalid")
    return parsed.astimezone(UTC)


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DisasterRecoveryProofError(f"{field}_invalid")
    return value


def _positive_int(value: Any, field: str) -> int:
    observed = _nonnegative_int(value, field)
    if observed < 1:
        raise DisasterRecoveryProofError(f"{field}_invalid")
    return observed


def _nonnegative_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise DisasterRecoveryProofError(f"{field}_invalid")
    return float(value)


def _duration_ms(start: datetime, end: datetime, field: str) -> int:
    duration = round((end - start).total_seconds() * 1_000)
    if duration <= 0:
        raise DisasterRecoveryProofError(f"{field}_duration_invalid")
    return duration


if __name__ == "__main__":
    raise SystemExit(main())
