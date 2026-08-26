#!/usr/bin/env python3
"""Build and validate the exact content-free PostgreSQL HA reference proof."""

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
from typing import Any, Iterable, Mapping


SCHEMA_ID = "hormuz.postgresql-ha-reference-proof"
SCHEMA_VERSION = 1
PROFILE = "kubernetes-enterprise-reference"
PLATFORM = "linux/amd64"
CNPG_VERSION = "1.30.0"
CNPG_MANIFEST_URL = (
    "https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/"
    "release-1.30/releases/cnpg-1.30.0.yaml"
)
CNPG_MANIFEST_SHA256 = "f8bede43fe4ee0d478c2355b204a36876b2ae4faac60f2a9452280b293da3b88"
CNPG_MUTABLE_IMAGE = "ghcr.io/cloudnative-pg/cloudnative-pg:1.30.0"
CNPG_OPERATOR_INDEX_SHA256 = "a2701eb97cdd2a34b1fdb2cb51987f544b706e40bec72ae7146cd8580efefebb"
CNPG_OPERATOR_AMD64_IMAGE = (
    "ghcr.io/cloudnative-pg/cloudnative-pg:1.30.0@"
    "sha256:091d306935cfdf646debfe78010d59ebfb572150eb6eb922b0203873c0c68841"
)
POSTGRES_VERSION = "16.15"
POSTGRES_IMAGE = (
    "ghcr.io/cloudnative-pg/postgresql:16.15-202608240846-minimal-trixie@"
    "sha256:e1ca593856017f1780dbdae8175add3ddd8f8d721348a3b6e8a01df67a9ece8a"
)
POSTGRES_AMD64_MANIFEST_SHA256 = "e1ca593856017f1780dbdae8175add3ddd8f8d721348a3b6e8a01df67a9ece8a"
POSTGRES_CATALOG_SHA256 = "39f1b58e39656884a73022ab947e949f857239fca70231c4c0daf5fba8423397"
HORMUZ_IMAGE = (
    "ghcr.io/xpounder-com/hormuz@"
    "sha256:1bbcca3490a7a5b004a880f42e8250acb91ce566a9c59f3263d7b279568efb5a"
)
KIND_VERSION = "v0.32.0"
KUBERNETES_VERSION = "v1.36.1"
HELM_VERSION = "v3.21.4"
CILIUM_VERSION = "1.20.1"
STATE_SCHEMA_ID = "hormuz.postgresql-ha-state-snapshot"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

EVENTS = [
    "environment_verified",
    "operator_installed",
    "postgres_topology_ready",
    "gateway_baseline_ready",
    "durable_state_seeded",
    "ambiguous_attempt_committed",
    "primary_loss_injected",
    "former_primary_container_stopped",
    "all_gateways_failed_closed",
    "positive_outage_provider_egress_unchanged",
    "safe_replica_promoted",
    "lease_and_rw_endpoint_converged",
    "synchronous_durability_restored",
    "durable_state_continuity_verified",
    "gateways_reconnected_without_restart",
    "ambiguous_attempt_preserved",
    "no_provider_replay_verified",
    "former_primary_replaced_and_rejoined",
    "quorum_fixture_ready",
    "primary_and_replica_loss_injected",
    "quorum_promotion_refused",
    "rw_endpoint_withdrawn",
    "all_gateways_failed_closed_without_quorum",
    "negative_outage_provider_egress_unchanged",
    "negative_path_recovered",
    "final_state_continuity_verified",
]

CHECKS = {
    "audit_chain_integrity",
    "bounded_pool_acquisition",
    "bounded_pool_queue",
    "bounded_reconnect_horizon",
    "cloudnativepg_manifest_checksum",
    "content_free_storage_denial",
    "custody_authority_and_restriction_continuity",
    "durable_request_attempt_ledger",
    "exact_digest_pinned_images",
    "failover_quorum_refusal",
    "gateway_fail_closed_readiness",
    "gateway_reconnect_without_restart",
    "generic_postgresql_secret_dsn",
    "isolation_fencing",
    "no_automatic_provider_replay",
    "policy_pointer_continuity",
    "primary_lease_coordination",
    "quorum_loss_provider_egress_denial",
    "rw_endpoint_excludes_stale_primary",
    "safe_primary_failover",
    "synchronous_any_one_required_durability",
    "tenant_isolation_after_reconnect",
    "three_distinct_postgresql_instances",
    "uncertain_consumption_preserved",
    "usage_and_security_evidence_continuity",
}

LIMITATIONS = [
    "account_free_disposable_kind_environment_only",
    "cloudnativepg_1_30_unreachable_primary_node_recovery_not_verified",
    "cloudnativepg_is_verification_infrastructure_not_product_contract",
    "compose_profile_has_no_high_availability_claim",
    "exact_pinned_combination_only",
    "helm_chart_does_not_install_postgresql",
    "no_backup_retention_rpo_rto_or_disaster_recovery_claim",
    "no_broad_kubernetes_or_cni_portability_claim",
    "no_customer_sla",
    "no_managed_postgresql_provider_certification",
    "positive_path_uses_abrupt_primary_pod_removal",
    "single_host_kind_does_not_prove_zone_or_hardware_failure_tolerance",
    "worker_pause_negative_path_simulates_abrupt_unavailability_not_disk_destruction",
]

TIMING_LIMITS_MS = {
    "positive_fail_closed": 60_000,
    "primary_promotion": 600_000,
    "gateway_recovery": 600_000,
    "former_primary_rejoin": 600_000,
    "negative_fail_closed": 60_000,
    "quorum_refusal_observation": 120_000,
    "negative_recovery": 600_000,
    "maximum_storage_denial": 15_000,
}

FORBIDDEN_EVIDENCE = (
    re.compile(r"/Users/"),
    re.compile(r"/home/runner/"),
    re.compile(r"/private/tmp/"),
    re.compile(r"postgres(?:ql)?://", re.IGNORECASE),
    re.compile(r"\bBearer\s+", re.IGNORECASE),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}\b"),
)


class PostgresHAProofError(RuntimeError):
    """Raised when the reference proof is incomplete, broad, or non-reproducible."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-operator-manifest")
    prepare.add_argument("--input", required=True, type=Path)
    prepare.add_argument("--output", required=True, type=Path)

    write = subparsers.add_parser("write-evidence")
    write.add_argument("--observations", required=True, type=Path)
    write.add_argument("--output", required=True, type=Path)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--evidence", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare-operator-manifest":
            prepare_operator_manifest(args.input, args.output)
            print(f"prepared digest-pinned CloudNativePG operator: version={CNPG_VERSION}")
        elif args.command == "write-evidence":
            observations = load_json(args.observations)
            evidence = build_evidence(observations)
            validate_evidence(evidence)
            write_exclusive(args.output, evidence)
            print("wrote content-free PostgreSQL HA reference evidence")
        else:
            validate_evidence(load_json(args.evidence))
            print(f"verified PostgreSQL HA reference: version={POSTGRES_VERSION} verdict=verified")
    except (PostgresHAProofError, OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        print(f"PostgreSQL HA reference proof failed: {error}", file=sys.stderr)
        return 1
    return 0


def prepare_operator_manifest(source: Path, output: Path) -> None:
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != CNPG_MANIFEST_SHA256:
        raise PostgresHAProofError("operator_manifest_checksum_invalid")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PostgresHAProofError("operator_manifest_encoding_invalid") from error
    if text.count(CNPG_MUTABLE_IMAGE) != 2:
        raise PostgresHAProofError("operator_manifest_image_shape_invalid")
    pinned = text.replace(CNPG_MUTABLE_IMAGE, CNPG_OPERATOR_AMD64_IMAGE)
    if (
        pinned.count(CNPG_MUTABLE_IMAGE) != 2
        or pinned.count(CNPG_OPERATOR_AMD64_IMAGE) != 2
    ):
        raise PostgresHAProofError("operator_manifest_image_pin_invalid")
    write_bytes_exclusive(output, pinned.encode("utf-8"))


def build_evidence(observations: Mapping[str, Any]) -> dict[str, Any]:
    validate_observations(observations)
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "verdict": "verified",
        "profile": PROFILE,
        "platform": PLATFORM,
        "source_commit": observations["source_commit"],
        "product_boundary": {
            "application_contract": "generic_postgresql_ha_dsn_from_existing_secret",
            "cloudnativepg_role": "verification_infrastructure_only",
            "helm_chart_installs_postgresql": False,
        },
        "versions": {
            "cloudnativepg": CNPG_VERSION,
            "cloudnativepg_manifest_url": CNPG_MANIFEST_URL,
            "cloudnativepg_manifest_sha256": CNPG_MANIFEST_SHA256,
            "cloudnativepg_operator_image": CNPG_OPERATOR_AMD64_IMAGE,
            "cloudnativepg_operator_index_sha256": CNPG_OPERATOR_INDEX_SHA256,
            "postgresql": POSTGRES_VERSION,
            "postgresql_image": POSTGRES_IMAGE,
            "postgresql_linux_amd64_manifest_sha256": POSTGRES_AMD64_MANIFEST_SHA256,
            "postgresql_catalog_sha256": POSTGRES_CATALOG_SHA256,
            "hormuz_image": HORMUZ_IMAGE,
            "kind": KIND_VERSION,
            "kubernetes": KUBERNETES_VERSION,
            "helm": HELM_VERSION,
            "cilium": CILIUM_VERSION,
            "docker_engine": observations["docker_engine"],
            "helm_chart_sha256": observations["helm_chart_sha256"],
        },
        "topology": dict(observations["topology"]),
        "pool_bounds": dict(observations["pool_bounds"]),
        "scenarios": {
            "primary_loss": dict(observations["primary_loss"]),
            "primary_and_replica_loss": dict(observations["quorum_loss"]),
        },
        "state_continuity": _state_summary(observations["state"]),
        "timings_ms": dict(observations["timings_ms"]),
        "events": list(observations["events"]),
        "checks": sorted(CHECKS),
        "limitations": list(LIMITATIONS),
    }


def validate_observations(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "source_commit",
            "docker_engine",
            "helm_chart_sha256",
            "topology",
            "pool_bounds",
            "primary_loss",
            "quorum_loss",
            "state",
            "timings_ms",
            "events",
        },
        "observations",
    )
    _commit(value["source_commit"])
    _safe_text(value["docker_engine"], "docker_engine")
    _sha256(value["helm_chart_sha256"], "helm_chart_sha256")
    _validate_topology(value["topology"])
    _validate_pool_bounds(value["pool_bounds"])
    _validate_primary_loss(value["primary_loss"])
    _validate_quorum_loss(value["quorum_loss"])
    _validate_state(value["state"])
    _validate_timings(value["timings_ms"])
    _validate_events(value["events"])


def validate_evidence(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "generated_at",
            "verdict",
            "profile",
            "platform",
            "source_commit",
            "product_boundary",
            "versions",
            "topology",
            "pool_bounds",
            "scenarios",
            "state_continuity",
            "timings_ms",
            "events",
            "checks",
            "limitations",
        },
        "evidence",
    )
    if (value["schema_id"], value["schema_version"]) != (SCHEMA_ID, SCHEMA_VERSION):
        raise PostgresHAProofError("evidence_schema_invalid")
    _timestamp(value["generated_at"])
    if (value["verdict"], value["profile"], value["platform"]) != (
        "verified",
        PROFILE,
        PLATFORM,
    ):
        raise PostgresHAProofError("evidence_claim_invalid")
    _commit(value["source_commit"])
    if value["product_boundary"] != {
        "application_contract": "generic_postgresql_ha_dsn_from_existing_secret",
        "cloudnativepg_role": "verification_infrastructure_only",
        "helm_chart_installs_postgresql": False,
    }:
        raise PostgresHAProofError("product_boundary_invalid")
    versions = value["versions"]
    if not isinstance(versions, Mapping):
        raise PostgresHAProofError("versions_invalid")
    expected_versions = {
        "cloudnativepg": CNPG_VERSION,
        "cloudnativepg_manifest_url": CNPG_MANIFEST_URL,
        "cloudnativepg_manifest_sha256": CNPG_MANIFEST_SHA256,
        "cloudnativepg_operator_image": CNPG_OPERATOR_AMD64_IMAGE,
        "cloudnativepg_operator_index_sha256": CNPG_OPERATOR_INDEX_SHA256,
        "postgresql": POSTGRES_VERSION,
        "postgresql_image": POSTGRES_IMAGE,
        "postgresql_linux_amd64_manifest_sha256": POSTGRES_AMD64_MANIFEST_SHA256,
        "postgresql_catalog_sha256": POSTGRES_CATALOG_SHA256,
        "hormuz_image": HORMUZ_IMAGE,
        "kind": KIND_VERSION,
        "kubernetes": KUBERNETES_VERSION,
        "helm": HELM_VERSION,
        "cilium": CILIUM_VERSION,
    }
    for key, expected in expected_versions.items():
        if versions.get(key) != expected:
            raise PostgresHAProofError(f"version_{key}_invalid")
    _safe_text(versions.get("docker_engine"), "docker_engine")
    _sha256(versions.get("helm_chart_sha256"), "helm_chart_sha256")
    _validate_topology(value["topology"])
    _validate_pool_bounds(value["pool_bounds"])
    scenarios = value["scenarios"]
    if not isinstance(scenarios, Mapping) or set(scenarios) != {
        "primary_loss",
        "primary_and_replica_loss",
    }:
        raise PostgresHAProofError("scenarios_invalid")
    _validate_primary_loss(scenarios["primary_loss"])
    _validate_quorum_loss(scenarios["primary_and_replica_loss"])
    _validate_state_summary(value["state_continuity"])
    _validate_timings(value["timings_ms"])
    _validate_events(value["events"])
    if value["checks"] != sorted(CHECKS):
        raise PostgresHAProofError("checks_invalid")
    if value["limitations"] != LIMITATIONS:
        raise PostgresHAProofError("limitations_invalid")
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if any(pattern.search(serialized) for pattern in FORBIDDEN_EVIDENCE):
        raise PostgresHAProofError("evidence_contains_forbidden_content")


def _validate_topology(value: Any) -> None:
    expected = {
        "kind_nodes": 6,
        "worker_nodes": 5,
        "postgresql_worker_nodes": 3,
        "gateway_worker_nodes": 2,
        "postgresql_instances": 3,
        "distinct_postgresql_nodes": 3,
        "gateway_replicas": 2,
        "failover_delay_seconds": 30,
        "synchronous_method": "any",
        "synchronous_number": 1,
        "data_durability": "required",
        "failover_quorum": True,
        "isolation_check": True,
        "primary_lease": {
            "lease_duration_seconds": 15,
            "renew_deadline_seconds": 10,
            "retry_period_seconds": 2,
            "released_lease_duration_seconds": 1,
        },
    }
    if value != expected:
        raise PostgresHAProofError("topology_invalid")


def _validate_pool_bounds(value: Any) -> None:
    if value != {
        "minimum_connections_per_replica": 1,
        "maximum_connections_per_replica": 4,
        "acquire_timeout_seconds": 5,
        "maximum_waiting_per_replica": 8,
        "reconnect_horizon_seconds": 15,
    }:
        raise PostgresHAProofError("pool_bounds_invalid")


def _validate_primary_loss(value: Any) -> None:
    expected_keys = {
        "trigger",
        "previous_primary_changed",
        "lease_holder_matches_current_primary",
        "rw_endpoint_matches_current_primary",
        "synchronous_durability_restored",
        "former_primary_rejoined_as_replica",
        "former_primary_container_stopped_before_promotion",
        "gateway_replicas_observed",
        "gateways_not_ready",
        "backpressure_requests",
        "gateway_storage_denials",
        "provider_requests_before_denials",
        "provider_requests_after_denials",
        "provider_requests_after_recovery",
        "gateway_processes_reused",
        "ambiguous_attempts_preserved",
        "uncertain_reservations_preserved",
        "automatic_provider_replays",
    }
    _exact_keys(value, expected_keys, "primary_loss")
    required_true = (
        "previous_primary_changed",
        "lease_holder_matches_current_primary",
        "rw_endpoint_matches_current_primary",
        "synchronous_durability_restored",
        "former_primary_rejoined_as_replica",
        "former_primary_container_stopped_before_promotion",
        "gateway_processes_reused",
    )
    if value["trigger"] != "unexpected_primary_pod_deletion" or not all(
        value[key] is True for key in required_true
    ):
        raise PostgresHAProofError("primary_loss_outcome_invalid")
    if (
        value["gateway_replicas_observed"],
        value["gateways_not_ready"],
        value["backpressure_requests"],
        value["gateway_storage_denials"],
    ) != (
        2,
        2,
        32,
        32,
    ):
        raise PostgresHAProofError("primary_loss_gateway_denial_invalid")
    before = _nonnegative(value["provider_requests_before_denials"], "provider_requests_before_denials")
    after = _nonnegative(value["provider_requests_after_denials"], "provider_requests_after_denials")
    recovered = _positive(value["provider_requests_after_recovery"], "provider_requests_after_recovery")
    if after != before or recovered != after + 1:
        raise PostgresHAProofError("primary_loss_provider_count_invalid")
    if _positive(value["ambiguous_attempts_preserved"], "ambiguous_attempts_preserved") < 1:
        raise PostgresHAProofError("ambiguous_attempt_missing")
    if _positive(value["uncertain_reservations_preserved"], "uncertain_reservations_preserved") < 1:
        raise PostgresHAProofError("uncertain_reservation_missing")
    if value["automatic_provider_replays"] != 0:
        raise PostgresHAProofError("provider_replay_detected")


def _validate_quorum_loss(value: Any) -> None:
    expected_keys = {
        "trigger",
        "unavailable_postgresql_instances",
        "promotion_prevented",
        "failover_quorum_reported_insufficient",
        "rw_ready_addresses",
        "stale_primary_endpoint_absent",
        "gateway_replicas_observed",
        "gateways_not_ready",
        "backpressure_requests",
        "gateway_storage_denials",
        "provider_requests_before_denials",
        "provider_requests_after_denials",
        "gateway_processes_reused_after_recovery",
    }
    _exact_keys(value, expected_keys, "quorum_loss")
    if value["trigger"] != "primary_and_one_replica_worker_pause":
        raise PostgresHAProofError("quorum_loss_trigger_invalid")
    if value["unavailable_postgresql_instances"] != 2:
        raise PostgresHAProofError("quorum_loss_instance_count_invalid")
    for key in (
        "promotion_prevented",
        "failover_quorum_reported_insufficient",
        "stale_primary_endpoint_absent",
        "gateway_processes_reused_after_recovery",
    ):
        if value[key] is not True:
            raise PostgresHAProofError("quorum_loss_outcome_invalid")
    if value["rw_ready_addresses"] != 0:
        raise PostgresHAProofError("quorum_loss_rw_endpoint_invalid")
    if (
        value["gateway_replicas_observed"],
        value["gateways_not_ready"],
        value["backpressure_requests"],
        value["gateway_storage_denials"],
    ) != (
        2,
        2,
        32,
        32,
    ):
        raise PostgresHAProofError("quorum_loss_gateway_denial_invalid")
    before = _nonnegative(value["provider_requests_before_denials"], "provider_requests_before_denials")
    after = _nonnegative(value["provider_requests_after_denials"], "provider_requests_after_denials")
    if after != before:
        raise PostgresHAProofError("quorum_loss_provider_egress_invalid")


def _validate_state(value: Any) -> None:
    _exact_keys(value, {"before", "after_failover", "after_recovery", "after_quorum_recovery"}, "state")
    snapshots = [
        _validate_snapshot(value[name])
        for name in ("before", "after_failover", "after_recovery", "after_quorum_recovery")
    ]
    fingerprints = {snapshot["control_fingerprint"] for snapshot in snapshots}
    if len(fingerprints) != 1:
        raise PostgresHAProofError("control_state_changed_during_failover")
    before, after_failover, after_recovery, after_quorum = snapshots
    for field in ("usage_events", "security_events", "request_attempts", "audit_chain_sequence"):
        values = [snapshot[field] for snapshot in snapshots]
        if values != sorted(values):
            raise PostgresHAProofError(f"state_{field}_regressed")
    for snapshot in snapshots:
        if snapshot["isolation_tenant_rows"] != 0:
            raise PostgresHAProofError("tenant_isolation_invalid")
    if after_failover["pending_attempts"] + after_failover["outcome_unknown_attempts"] < 1:
        raise PostgresHAProofError("ambiguous_attempt_not_preserved")
    if after_recovery["pending_attempts"] + after_recovery["outcome_unknown_attempts"] < 1:
        raise PostgresHAProofError("recovered_ambiguous_attempt_not_preserved")
    if min(after_recovery["uncertain_reservations"], after_quorum["uncertain_reservations"]) < 1:
        raise PostgresHAProofError("uncertain_consumption_not_preserved")
    if after_recovery["usage_events"] <= before["usage_events"]:
        raise PostgresHAProofError("recovery_usage_evidence_missing")


def _validate_snapshot(value: Any) -> Mapping[str, Any]:
    expected_keys = {
        "schema_id",
        "schema_version",
        "command",
        "control_fingerprint",
        "policy_generation",
        "policy_administrator_count",
        "custody_administrator_count",
        "custody_projection_version",
        "custody_restriction",
        "usage_events",
        "security_events",
        "request_attempts",
        "pending_attempts",
        "outcome_unknown_attempts",
        "uncertain_reservations",
        "audit_chain_sequence",
        "audit_chain_verified",
        "isolation_tenant_rows",
    }
    _exact_keys(value, expected_keys, "state_snapshot")
    if (value["schema_id"], value["schema_version"], value["command"]) != (
        STATE_SCHEMA_ID,
        1,
        "snapshot",
    ):
        raise PostgresHAProofError("state_snapshot_schema_invalid")
    _sha256(value["control_fingerprint"], "control_fingerprint")
    if (
        value["policy_generation"] != 1
        or value["policy_administrator_count"] != 1
        or value["custody_administrator_count"] != 2
        or value["custody_projection_version"] != 1
        or value["custody_restriction"] != "provider_credential_disabled"
        or value["audit_chain_verified"] is not True
    ):
        raise PostgresHAProofError("state_control_contract_invalid")
    for field in (
        "usage_events",
        "security_events",
        "request_attempts",
        "pending_attempts",
        "outcome_unknown_attempts",
        "uncertain_reservations",
        "audit_chain_sequence",
        "isolation_tenant_rows",
    ):
        _nonnegative(value[field], field)
    return value


def _state_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    _validate_state(value)
    before = value["before"]
    after = value["after_quorum_recovery"]
    return {
        "control_fingerprint": before["control_fingerprint"],
        "policy_pointer_preserved": True,
        "custody_authority_preserved": True,
        "custody_restriction_preserved": True,
        "budget_and_uncertain_reservation_preserved": True,
        "ambiguous_attempt_preserved": True,
        "usage_evidence_before": before["usage_events"],
        "usage_evidence_after": after["usage_events"],
        "security_evidence_before": before["security_events"],
        "security_evidence_after": after["security_events"],
        "audit_chain_sequence_before": before["audit_chain_sequence"],
        "audit_chain_sequence_after": after["audit_chain_sequence"],
        "audit_chain_verified": True,
        "tenant_isolation_verified": True,
    }


def _validate_state_summary(value: Any) -> None:
    expected_keys = {
        "control_fingerprint",
        "policy_pointer_preserved",
        "custody_authority_preserved",
        "custody_restriction_preserved",
        "budget_and_uncertain_reservation_preserved",
        "ambiguous_attempt_preserved",
        "usage_evidence_before",
        "usage_evidence_after",
        "security_evidence_before",
        "security_evidence_after",
        "audit_chain_sequence_before",
        "audit_chain_sequence_after",
        "audit_chain_verified",
        "tenant_isolation_verified",
    }
    _exact_keys(value, expected_keys, "state_continuity")
    _sha256(value["control_fingerprint"], "control_fingerprint")
    for key in (
        "policy_pointer_preserved",
        "custody_authority_preserved",
        "custody_restriction_preserved",
        "budget_and_uncertain_reservation_preserved",
        "ambiguous_attempt_preserved",
        "audit_chain_verified",
        "tenant_isolation_verified",
    ):
        if value[key] is not True:
            raise PostgresHAProofError("state_continuity_invalid")
    if value["usage_evidence_after"] <= value["usage_evidence_before"]:
        raise PostgresHAProofError("usage_continuity_invalid")
    if value["security_evidence_after"] < value["security_evidence_before"]:
        raise PostgresHAProofError("security_continuity_invalid")
    if value["audit_chain_sequence_after"] < value["audit_chain_sequence_before"]:
        raise PostgresHAProofError("audit_chain_continuity_invalid")


def _validate_timings(value: Any) -> None:
    _exact_keys(value, set(TIMING_LIMITS_MS), "timings_ms")
    for name, limit in TIMING_LIMITS_MS.items():
        observed = _positive(value[name], name)
        if observed > limit:
            raise PostgresHAProofError(f"timing_{name}_exceeded")
    if value["quorum_refusal_observation"] < 30_000:
        raise PostgresHAProofError("timing_quorum_refusal_observation_too_short")


def _validate_events(value: Any) -> None:
    if not isinstance(value, list) or len(value) != len(EVENTS):
        raise PostgresHAProofError("events_invalid")
    expected = [
        {"sequence": sequence, "event": event}
        for sequence, event in enumerate(EVENTS, start=1)
    ]
    if value != expected:
        raise PostgresHAProofError("event_sequence_invalid")


def load_json(path: Path) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise PostgresHAProofError("duplicate_json_key")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=object_pairs)
    if not isinstance(value, dict):
        raise PostgresHAProofError("json_object_required")
    return value


def write_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    write_bytes_exclusive(path, encoded)


def write_bytes_exclusive(path: Path, value: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600 or path.is_symlink():
        raise PostgresHAProofError("output_permissions_invalid")


def _exact_keys(value: Any, expected: set[str], field: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise PostgresHAProofError(f"{field}_fields_invalid")


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise PostgresHAProofError(f"{field}_invalid")
    return value


def _commit(value: Any) -> str:
    if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
        raise PostgresHAProofError("source_commit_invalid")
    return value


def _safe_text(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise PostgresHAProofError(f"{field}_invalid")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PostgresHAProofError(f"{field}_invalid")
    return value


def _positive(value: Any, field: str) -> int:
    observed = _nonnegative(value, field)
    if observed < 1:
        raise PostgresHAProofError(f"{field}_invalid")
    return observed


def _timestamp(value: Any) -> None:
    if not isinstance(value, str):
        raise PostgresHAProofError("generated_at_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise PostgresHAProofError("generated_at_invalid") from error
    if parsed.tzinfo is None:
        raise PostgresHAProofError("generated_at_invalid")


if __name__ == "__main__":
    raise SystemExit(main())
