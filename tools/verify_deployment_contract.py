#!/usr/bin/env python3
"""Fail-closed validation for the accepted Hormuz v1 deployment contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


SCHEMA_ID = "hormuz.v1-deployment-contract"
SCHEMA_VERSION = 1
CONTRACT_PATH = Path("docs/deployment-contract-v1.json")
DECISION_PATH = Path(
    "docs/decisions/0009-v1-deployment-profiles-and-recovery-objectives.md"
)
DECISION_RECORD = (
    "https://github.com/Xpounder-com/hormuz/issues/100#issuecomment-5414302641"
)
PROFILE_IDS = ("compose_single_vm", "kubernetes_enterprise_reference")
COMPONENT_IDS = (
    "signed_oci_artifact",
    "public_tls_and_ingress",
    "identity_provider",
    "postgresql",
    "runtime_configuration_and_secrets",
    "provider_accounts_and_egress",
    "custody_and_immutable_storage",
    "deployment_platform_and_traffic_promotion",
)
STATE_IDS = (
    "signed_release_and_profile_manifests",
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
    "oidc_discovery_and_jwks_cache",
    "custody_projection_cache_and_admission_barriers",
    "gateway_process_ephemera",
)
STATE_CLASSES = {
    "durable_external",
    "external_source_with_durable_snapshot",
    "durable_authoritative",
    "durable_derived",
    "cached",
    "ephemeral",
}
CHILD_GATES = {
    101: (("compose_single_vm", "kubernetes_enterprise_reference"), "signed_oci_application_contract"),
    102: (("compose_single_vm",), "single_vm_evaluation_and_pilot_reference"),
    103: (("kubernetes_enterprise_reference",), "coordinated_multi_replica_operation"),
    104: (("kubernetes_enterprise_reference",), "postgresql_ha_failover_and_bounded_recovery"),
    105: (("kubernetes_enterprise_reference",), "measured_reference_disaster_recovery"),
    106: (("compose_single_vm", "kubernetes_enterprise_reference"), "tenant_complete_export_and_deletion"),
    107: (("compose_single_vm", "kubernetes_enterprise_reference"), "release_upgrade_and_rollback"),
    108: (("kubernetes_enterprise_reference",), "vendor_neutral_helm_profile_topology"),
}
REQUIRED_NONCLAIMS = {
    "compose_is_enterprise_ha",
    "reference_targets_are_customer_slas",
    "reference_evidence_certifies_customer_infrastructure",
    "browser_session_brokerage_is_supported",
    "linux_arm64_is_supported",
    "kubernetes_or_helm_is_an_application_dependency",
    "open_release_gates_are_already_proven",
}
REQUIRED_DOCUMENTATION = (
    "docs/decisions/0008-signed-oci-deployment-contract.md",
    str(DECISION_PATH),
    "docs/DEPLOYMENT.md",
    "deploy/compose/README.md",
    "deploy/kubernetes/README.md",
)
MAX_CONTRACT_BYTES = 256 * 1024


class DeploymentContractError(ValueError):
    """A fail-closed v1 deployment-contract validation failure."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise DeploymentContractError("duplicate_json_member")
        value[key] = item
    return value


def _require_fields(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise DeploymentContractError(f"{label}_fields_invalid")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DeploymentContractError(f"{label}_invalid")
    return value


def _require_string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise DeploymentContractError(f"{label}_invalid")
    result = [_require_string(item, label) for item in value]
    if len(result) != len(set(result)):
        raise DeploymentContractError(f"{label}_duplicate")
    return result


def _read_contract(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DeploymentContractError("contract_unreadable") from exc
    if size > MAX_CONTRACT_BYTES:
        raise DeploymentContractError("contract_too_large")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentContractError("contract_invalid_json") from exc
    return _require_fields(
        value,
        {
            "schema_id",
            "schema_version",
            "status",
            "decision_record",
            "application_contract",
            "profiles",
            "support_matrix",
            "component_ownership",
            "state_inventory",
            "recovery_objectives",
            "child_gates",
            "nonclaims",
            "documentation",
        },
        "contract",
    )


def _validate_application(value: object) -> None:
    application = _require_fields(
        value,
        {
            "artifact",
            "first_publication_registry",
            "registry_is_product_contract",
            "deployment_tooling_is_application_dependency",
            "initial_supported_platform",
            "arm64_gate",
        },
        "application_contract",
    )
    if application != {
        "artifact": "signed_oci_manifest_digest",
        "first_publication_registry": "ghcr.io/xpounder-com/hormuz",
        "registry_is_product_contract": False,
        "deployment_tooling_is_application_dependency": False,
        "initial_supported_platform": "linux/amd64",
        "arm64_gate": 109,
    }:
        raise DeploymentContractError("application_contract_changed")


def _validate_profiles(value: object) -> None:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise DeploymentContractError("profiles_invalid")
    expected_fields = {
        "id",
        "status",
        "platforms",
        "gateway_replicas",
        "failure_domain",
        "postgresql",
        "availability_claim",
        "required_gates",
        "nonclaims",
    }
    profiles: dict[str, dict[str, Any]] = {}
    for item in value:
        profile = _require_fields(item, expected_fields, "profile")
        profile_id = _require_string(profile["id"], "profile_id")
        if profile_id in profiles:
            raise DeploymentContractError("profile_duplicate")
        _require_string_list(profile["platforms"], "profile_platforms")
        _require_string_list(profile["nonclaims"], "profile_nonclaims")
        if (
            not isinstance(profile["required_gates"], list)
            or any(isinstance(gate, bool) or not isinstance(gate, int) for gate in profile["required_gates"])
        ):
            raise DeploymentContractError("profile_required_gates_invalid")
        profiles[profile_id] = profile
    if tuple(profiles) != PROFILE_IDS:
        raise DeploymentContractError("profile_set_changed")

    compose = profiles["compose_single_vm"]
    if (
        compose["status"] != "verified_evaluation_and_pilot_reference"
        or compose["platforms"] != ["linux/amd64"]
        or compose["gateway_replicas"] != 1
        or compose["failure_domain"] != "single_vm_none"
        or compose["postgresql"]
        != "private_digest_pinned_same_vm_or_customer_operated_external_dsn"
        or compose["availability_claim"] != "none"
        or compose["required_gates"] != [101, 102]
        or "rpo_or_rto" not in compose["nonclaims"]
    ):
        raise DeploymentContractError("compose_profile_changed")

    kubernetes = profiles["kubernetes_enterprise_reference"]
    if (
        kubernetes["status"] != "profile_verified_release_operations_pending"
        or kubernetes["platforms"] != ["linux/amd64"]
        or kubernetes["gateway_replicas"] != 2
        or kubernetes["failure_domain"]
        != "two_distinct_kubernetes_nodes_by_hostname_topology_only"
        or kubernetes["postgresql"] != "customer_operated_external_ha_endpoint"
        or kubernetes["availability_claim"] != "gated_by_issues_103_through_107"
        or kubernetes["required_gates"] != [101, 108, 103, 104, 105, 106, 107]
        or "availability_zone_failure" not in kubernetes["nonclaims"]
        or "universal_customer_recovery_guarantee" not in kubernetes["nonclaims"]
    ):
        raise DeploymentContractError("kubernetes_profile_changed")


def _validate_support_matrix(value: object) -> None:
    matrix = _require_fields(
        value,
        {
            "deployment_platforms",
            "employee_authentication",
            "retry_and_idempotency",
            "provider_protocols",
        },
        "support_matrix",
    )
    expected_platforms = [
        {
            "platform": "linux/amd64",
            "status": "supported",
            "profiles": ["compose_single_vm", "kubernetes_enterprise_reference"],
        },
        {
            "platform": "linux/arm64",
            "status": "unsupported_until_issue_109",
            "profiles": [],
        },
        {
            "platform": "darwin",
            "status": "development_cli_only_no_verified_deployment_profile",
            "profiles": [],
        },
        {
            "platform": "windows",
            "status": "unsupported_deployment_profile",
            "profiles": [],
        },
    ]
    if matrix["deployment_platforms"] != expected_platforms:
        raise DeploymentContractError("platform_support_changed")
    if matrix["employee_authentication"] != {
        "supported": ["bootstrap_token", "generic_oidc_bearer_jwt"],
        "excluded": [
            "browser_session_broker",
            "cookie_session",
            "refresh_token_custody",
            "opaque_token_introspection",
        ],
    }:
        raise DeploymentContractError("authentication_support_changed")
    if matrix["retry_and_idempotency"] != {
        "automatic_provider_replay": False,
        "client_retry": "new_attempt",
        "supported_client_idempotency_key": "none",
    }:
        raise DeploymentContractError("retry_idempotency_support_changed")
    if matrix["provider_protocols"] != ["openai_responses", "anthropic_messages"]:
        raise DeploymentContractError("provider_protocol_support_changed")


def _validate_component_ownership(value: object) -> None:
    if not isinstance(value, list):
        raise DeploymentContractError("component_ownership_invalid")
    ids: list[str] = []
    for item in value:
        component = _require_fields(
            item,
            {"id", "hormuz_responsibility", "customer_responsibility"},
            "component",
        )
        ids.append(_require_string(component["id"], "component_id"))
        _require_string(component["hormuz_responsibility"], "hormuz_responsibility")
        _require_string(component["customer_responsibility"], "customer_responsibility")
    if tuple(ids) != COMPONENT_IDS:
        raise DeploymentContractError("component_ownership_set_changed")


def _validate_state_inventory(value: object) -> None:
    if not isinstance(value, list):
        raise DeploymentContractError("state_inventory_invalid")
    ids: list[str] = []
    for item in value:
        state = _require_fields(
            item,
            {
                "id",
                "class",
                "authoritative_owner",
                "authoritative_store",
                "replica_behavior",
                "recovery_treatment",
                "recovery_gate",
            },
            "state",
        )
        state_id = _require_string(state["id"], "state_id")
        ids.append(state_id)
        if state["class"] not in STATE_CLASSES:
            raise DeploymentContractError("state_class_invalid")
        for field in (
            "authoritative_owner",
            "authoritative_store",
            "replica_behavior",
            "recovery_treatment",
        ):
            _require_string(state[field], f"state_{field}")
        if state["recovery_gate"] not in {103, 105, 107}:
            raise DeploymentContractError("state_recovery_gate_invalid")
        if state["class"] in {"durable_authoritative", "durable_derived"} and not str(
            state["authoritative_store"]
        ).startswith("postgresql"):
            raise DeploymentContractError("postgresql_authority_invalid")
    if tuple(ids) != STATE_IDS:
        raise DeploymentContractError("state_inventory_set_changed")


def _validate_recovery_objectives(value: object) -> dict[str, Any]:
    objectives = _require_fields(
        value,
        {
            "profile",
            "classification",
            "customer_sla",
            "rpo",
            "internal_rto",
            "end_to_end_recovery_time",
            "required_ordered_timestamps",
        },
        "recovery_objectives",
    )
    if (
        objectives["profile"] != "kubernetes_enterprise_reference"
        or objectives["classification"] != "reference_rehearsal_acceptance_criteria"
        or objectives["customer_sla"] is not False
    ):
        raise DeploymentContractError("recovery_scope_changed")

    rpo = _require_fields(
        objectives["rpo"],
        {"maximum_seconds", "failure_event", "recovered_marker", "measurement"},
        "rpo",
    )
    if rpo != {
        "maximum_seconds": 300,
        "failure_event": "failure_injection_at",
        "recovered_marker": "latest_recovered_committed_marker_at",
        "measurement": "maximum gap across every recovery-covered Hormuz durable state class",
    }:
        raise DeploymentContractError("rpo_contract_changed")

    rto = _require_fields(
        objectives["internal_rto"],
        {"maximum_seconds", "start_event", "end_event", "measurement"},
        "internal_rto",
    )
    if rto != {
        "maximum_seconds": 3600,
        "start_event": "authorized_recovery_execution_started_at",
        "end_event": "recovered_environment_ready_for_promotion_at",
        "measurement": "restore plus complete Hormuz admission and state validation with required recovery inputs available",
    }:
        raise DeploymentContractError("internal_rto_contract_changed")

    end_to_end = _require_fields(
        objectives["end_to_end_recovery_time"],
        {"maximum_seconds", "must_publish", "start_event", "end_event", "includes"},
        "end_to_end_recovery_time",
    )
    if (
        end_to_end["maximum_seconds"] is not None
        or end_to_end["must_publish"] is not True
        or end_to_end["start_event"] != "failure_injection_at"
        or end_to_end["end_event"]
        != "first_successful_governed_request_after_promotion_at"
        or end_to_end["includes"]
        != [
            "detection",
            "declaration",
            "authorization",
            "restore",
            "validation",
            "traffic_promotion",
            "first_governed_request",
        ]
    ):
        raise DeploymentContractError("end_to_end_recovery_contract_changed")
    expected_timestamps = [
        "failure_injection_at",
        "incident_detected_at",
        "incident_declared_at",
        "authorized_recovery_execution_started_at",
        "restore_started_at",
        "recovered_environment_ready_for_promotion_at",
        "traffic_promoted_at",
        "first_successful_governed_request_after_promotion_at",
    ]
    if objectives["required_ordered_timestamps"] != expected_timestamps:
        raise DeploymentContractError("recovery_timestamp_contract_changed")
    return objectives


def _validate_child_gates(value: object) -> None:
    if not isinstance(value, list):
        raise DeploymentContractError("child_gates_invalid")
    observed: dict[int, tuple[tuple[str, ...], str]] = {}
    for item in value:
        gate = _require_fields(item, {"issue", "profiles", "claim"}, "child_gate")
        issue = gate["issue"]
        if isinstance(issue, bool) or not isinstance(issue, int) or issue in observed:
            raise DeploymentContractError("child_gate_issue_invalid")
        profiles = tuple(_require_string_list(gate["profiles"], "child_gate_profiles"))
        if any(profile not in PROFILE_IDS for profile in profiles):
            raise DeploymentContractError("child_gate_profile_invalid")
        observed[issue] = (profiles, _require_string(gate["claim"], "child_gate_claim"))
    if observed != CHILD_GATES:
        raise DeploymentContractError("child_gate_mapping_changed")


def _validate_documentation(root: Path, value: object) -> None:
    paths = tuple(_require_string_list(value, "documentation"))
    if paths != REQUIRED_DOCUMENTATION:
        raise DeploymentContractError("documentation_set_changed")
    for relative in paths:
        if not (root / relative).is_file():
            raise DeploymentContractError("documentation_missing")
    decision = (root / DECISION_PATH).read_text(encoding="utf-8")
    for token in (
        "**Status:** Accepted",
        DECISION_RECORD,
        "### Compose single-VM profile",
        "### Kubernetes enterprise-reference profile",
        "no more than **300 seconds**",
        "no more than **3,600 seconds**",
        "Complete end-to-end recovery time",
        "not a customer SLA",
    ):
        if token not in decision:
            raise DeploymentContractError("accepted_decision_document_drift")


def validate_deployment_contract(root: Path) -> dict[str, object]:
    root = root.resolve()
    contract = _read_contract(root / CONTRACT_PATH)
    if (
        contract["schema_id"] != SCHEMA_ID
        or contract["schema_version"] != SCHEMA_VERSION
        or contract["status"] != "accepted"
        or contract["decision_record"] != DECISION_RECORD
    ):
        raise DeploymentContractError("contract_identity_invalid")

    _validate_application(contract["application_contract"])
    _validate_profiles(contract["profiles"])
    _validate_support_matrix(contract["support_matrix"])
    _validate_component_ownership(contract["component_ownership"])
    _validate_state_inventory(contract["state_inventory"])
    recovery = _validate_recovery_objectives(contract["recovery_objectives"])
    _validate_child_gates(contract["child_gates"])
    if set(_require_string_list(contract["nonclaims"], "nonclaims")) != REQUIRED_NONCLAIMS:
        raise DeploymentContractError("nonclaims_changed")
    _validate_documentation(root, contract["documentation"])

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "profile_count": len(PROFILE_IDS),
        "component_owner_count": len(COMPONENT_IDS),
        "state_class_count": len(STATE_IDS),
        "child_gate_count": len(CHILD_GATES),
        "rpo_seconds_max": recovery["rpo"]["maximum_seconds"],
        "internal_rto_seconds_max": recovery["internal_rto"]["maximum_seconds"],
        "end_to_end_time_publication_required": recovery[
            "end_to_end_recovery_time"
        ]["must_publish"],
        "customer_sla": recovery["customer_sla"],
    }


def _write_output(path: Path, result: dict[str, object]) -> None:
    if path.exists():
        raise DeploymentContractError("output_exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_deployment_contract(args.root)
        if args.output is not None:
            _write_output(args.output, result)
        else:
            json.dump(result, sys.stdout, sort_keys=True, separators=(",", ":"))
            sys.stdout.write("\n")
    except (DeploymentContractError, OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
