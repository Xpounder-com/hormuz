#!/usr/bin/env python3
"""Fail-closed validation for the accepted Hormuz v1.1 portfolio contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hormuz._contract_schemas.manifest import validate_contract_manifest
from hormuz.contracts import contract_manifest


SCHEMA_ID = "hormuz.portfolio-intelligence-contract"
SCHEMA_VERSION = 1
CONTRACT_PATH = Path("docs/portfolio-intelligence-contract-v1.json")
DECISION_PATH = Path(
    "docs/decisions/0010-v1.1-portfolio-intelligence-contract.md"
)
BASELINE_MANIFEST_PATH = Path(
    "tests/fixtures/portfolio_intelligence/v1.0.0-contract-manifest.json"
)
DECISION_RECORD = (
    "https://github.com/Xpounder-com/hormuz/issues/212#issuecomment-5466284033"
)
BASELINE_MANIFEST_SHA256 = (
    "6e7f264de74f8f998842b49001a61b3640a05927d74bb31e4eec5ff8ab89504f"
)
MAX_CONTRACT_BYTES = 256 * 1024
MAX_BASELINE_BYTES = 256 * 1024

TOP_LEVEL_FIELDS = {
    "schema_id",
    "schema_version",
    "status",
    "target_release",
    "decision_record",
    "baseline",
    "compatibility",
    "work_scope_hierarchy",
    "entities",
    "identity_and_lifecycle",
    "temporal_and_lifecycle",
    "attribution",
    "budget_policy",
    "evidence",
    "kpis",
    "authorization",
    "api",
    "recommendations",
    "content_boundary",
    "child_gates",
    "conditional_gates",
    "nonclaims",
    "documentation",
}
ENTITY_SCHEMAS = {
    "work_scope_version": "hormuz.work-scope-version",
    "external_work_binding_event": "hormuz.external-work-binding-event",
    "governed_run_attribution_event": "hormuz.governed-run-attribution-event",
    "work_budget_plan": "hormuz.work-budget-plan",
    "work_outcome_event": "hormuz.work-outcome-event",
    "run_outcome_association_event": "hormuz.run-outcome-association-event",
    "model_scorecard": "hormuz.model-scorecard",
    "policy_recommendation": "hormuz.policy-recommendation",
}
ENTITY_MUTABILITY = {
    "append_only_versions",
    "append_only_supersession",
    "immutable_version_and_active_pointer",
    "immutable_snapshot",
    "immutable_with_append_only_status",
}
ENTITY_CONTENT_BOUNDARIES = {
    "bounded_customer_admin_metadata",
    "opaque_source_identifiers_only",
    "metadata_only_evidence",
    "financial_and_policy_metadata",
    "opaque_source_metadata_only",
    "aggregate_metadata_only_evidence",
    "aggregate_decision_metadata",
}
IDENTITY_AND_LIFECYCLE = {
    "organization_id_source": "existing_authenticated_server_resolved_tenant_identity",
    "organization_lifecycle": "existing_tenant_authority_not_mutated_by_portfolio_plane",
    "canonical_identifiers": {
        "organization": ["organization_id"],
        "work_scope_version": ["organization_id", "work_scope_id", "version"],
        "external_work_object": [
            "organization_id",
            "connector_id",
            "external_object_id",
        ],
        "governed_run": ["organization_id", "request_attempt_id"],
        "attribution_event": ["organization_id", "attribution_event_id"],
        "outcome_event": ["organization_id", "connector_id", "source_event_id"],
        "association_event": ["organization_id", "association_event_id"],
        "budget_plan": ["organization_id", "budget_plan_id", "version"],
        "scorecard": ["organization_id", "scorecard_id", "version"],
        "recommendation": ["organization_id", "recommendation_id", "version"],
    },
    "lifecycle_states": {
        "work_scope": ["active", "archived", "tombstoned"],
        "external_work_binding": ["active", "superseded", "tombstoned"],
        "governed_run": [
            "pending",
            "succeeded",
            "failed",
            "rate_limited",
            "outcome_unknown",
        ],
        "attribution": ["active", "superseded", "voided"],
        "budget_plan": ["active", "superseded", "expired", "tombstoned"],
        "outcome": ["observed", "superseded", "tombstoned"],
        "association": [
            "unmatched",
            "ambiguous",
            "associated",
            "excluded",
            "superseded",
        ],
        "scorecard": ["eligible", "inconclusive", "expired", "superseded"],
        "recommendation": [
            "pending",
            "accepted",
            "rejected",
            "expired",
            "invalidated",
            "superseded",
        ],
    },
    "stable_id_reassignment": "forbidden",
    "cross_organization_reference": "forbidden",
}
TEMPORAL_AND_LIFECYCLE = {
    "event_time": "source_or_domain_occurrence_time_preserved_as_observed",
    "observation_time": "connector_observation_time_preserved_separately",
    "ingestion_time": "database_commit_time_authoritative_for_receipt_and_retention",
    "source_time_authority": "never_authorizes_scope_or_overrides_ingestion_time",
    "current_state_order": (
        "entity_version_or_source_revision_then_ingestion_time_then_opaque_event_id"
    ),
    "late_event_behavior": (
        "retain_and_recompute_deterministically_without_rewriting_prior_events"
    ),
    "correction": "append_superseding_event_with_actor_reason_and_prior_identity",
    "idempotency": "same_identity_same_canonical_request_same_result",
    "idempotency_conflict": "fail_closed",
    "deletion": "append_tombstone_without_linked_fact_erasure",
    "retention": "customer_controlled_self_hosted_policy_no_universal_erasure_claim",
}
EXISTING_ROUTES = [
    "GET /health",
    "GET /ready",
    "GET /v1/gateway/usage",
    "GET /v1/gateway/whoami",
    "POST /v1/messages",
    "POST /v1/messages/count_tokens",
    "POST /v1/responses",
    "POST /v1/responses/compact",
]
ROUTE_CONTRACTS = {
    "GET /v1/admin/portfolio/associations": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.run-outcome-association-page",
    },
    "GET /v1/admin/portfolio/attributions": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.governed-run-attribution-page",
    },
    "GET /v1/admin/portfolio/budget-plans": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.work-budget-plan-page",
    },
    "GET /v1/admin/portfolio/budget-plans/{budget_plan_id}": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.work-budget-plan",
    },
    "GET /v1/admin/portfolio/outcomes": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.work-outcome-page",
    },
    "GET /v1/admin/portfolio/recommendations": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.policy-recommendation-page",
    },
    "GET /v1/admin/portfolio/recommendations/{recommendation_id}": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.policy-recommendation",
    },
    "GET /v1/admin/portfolio/scorecards": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.model-scorecard-page",
    },
    "GET /v1/admin/portfolio/scorecards/{scorecard_id}": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.model-scorecard",
    },
    "GET /v1/admin/portfolio/work-bindings": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.external-work-binding-page",
    },
    "GET /v1/admin/portfolio/work-scopes": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.work-scope-page",
    },
    "GET /v1/admin/portfolio/work-scopes/{work_scope_id}": {
        "request": "hormuz.portfolio-query",
        "response": "hormuz.work-scope-version",
    },
    "POST /v1/admin/portfolio/attributions": {
        "request": "hormuz.governed-run-attribution-request",
        "response": "hormuz.governed-run-attribution-event",
    },
    "POST /v1/admin/portfolio/budget-plans": {
        "request": "hormuz.work-budget-plan-request",
        "response": "hormuz.work-budget-plan",
    },
    "POST /v1/admin/portfolio/budget-plans/{budget_plan_id}/activations": {
        "request": "hormuz.work-budget-plan-activation-request",
        "response": "hormuz.work-budget-plan",
    },
    "POST /v1/admin/portfolio/recommendations/{recommendation_id}/decisions": {
        "request": "hormuz.policy-recommendation-decision-request",
        "response": "hormuz.policy-recommendation",
    },
    "POST /v1/admin/portfolio/work-bindings": {
        "request": "hormuz.external-work-binding-request",
        "response": "hormuz.external-work-binding-event",
    },
    "POST /v1/admin/portfolio/work-scopes": {
        "request": "hormuz.work-scope-create-request",
        "response": "hormuz.work-scope-version",
    },
    "POST /v1/admin/portfolio/work-scopes/{work_scope_id}/versions": {
        "request": "hormuz.work-scope-version-request",
        "response": "hormuz.work-scope-version",
    },
    "POST /v1/connectors/github/events": {
        "request": "github.webhook-provider-owned",
        "response": "hormuz.connector-ingest-receipt",
    },
    "POST /v1/connectors/linear/events": {
        "request": "linear.webhook-provider-owned",
        "response": "hormuz.connector-ingest-receipt",
    },
}
NEW_ROUTES = list(ROUTE_CONTRACTS)
API_REQUEST_SCHEMAS = [
    "hormuz.portfolio-query",
    "hormuz.work-scope-create-request",
    "hormuz.work-scope-version-request",
    "hormuz.external-work-binding-request",
    "hormuz.governed-run-attribution-request",
    "hormuz.work-budget-plan-request",
    "hormuz.work-budget-plan-activation-request",
    "hormuz.policy-recommendation-decision-request",
]
API_RESPONSE_SCHEMAS = [
    "hormuz.work-scope-page",
    "hormuz.work-scope-version",
    "hormuz.external-work-binding-page",
    "hormuz.external-work-binding-event",
    "hormuz.governed-run-attribution-page",
    "hormuz.governed-run-attribution-event",
    "hormuz.work-budget-plan-page",
    "hormuz.work-budget-plan",
    "hormuz.work-outcome-page",
    "hormuz.run-outcome-association-page",
    "hormuz.model-scorecard-page",
    "hormuz.model-scorecard",
    "hormuz.policy-recommendation-page",
    "hormuz.policy-recommendation",
    "hormuz.connector-ingest-receipt",
    "hormuz.portfolio-error",
]
PRIMARY_KPIS = [
    "use_case_attributed_spend_coverage",
    "quality_qualified_cost_per_accepted_work_item",
    "optimization_lift_vs_declared_baseline",
]
AUTHORIZATION_ROLES = [
    "portfolio_admin",
    "finance_viewer",
    "platform_viewer",
    "team_lead",
]
CHILD_GATES = [8, 213, 214, 215, 216, 217, 218, 219, 220, 221, 222, 223, 224, 225]
CONDITIONAL_GATES = {
    "browser_session_ux": 13,
    "content_inspection_or_persistence": 10,
    "ha_or_production_sla": 11,
    "independent_security_certification": 9,
    "broad_multi_profile_upgrade": 107,
}
REQUIRED_NONCLAIMS = {
    "automatic_policy_changes",
    "causal_productivity_from_observational_data",
    "content_governance_or_ticket_content_storage",
    "employee_performance_measurement",
    "enterprise_ha_sla_or_security_certification",
    "final_per_request_invoice_cost_from_provider_aggregate",
    "universal_model_ranking",
    "universal_roi",
}
REQUIRED_EXCLUSIONS = {
    "attachments",
    "comments_or_review_text",
    "credentials_or_secret_values",
    "external_workspace_repository_or_branch_names",
    "filenames_or_source_paths",
    "matched_detector_values",
    "patches_or_source_content",
    "prompts_or_responses",
    "raw_connector_payloads",
    "ticket_project_or_initiative_titles_or_bodies",
}
REQUIRED_DOCUMENTATION = [
    "docs/ARCHITECTURE.md",
    "docs/CONTRACTS.md",
    "docs/DURABLE_DATA.md",
    "docs/PORTFOLIO_INTELLIGENCE.md",
    "docs/ROADMAP.md",
    "docs/VERIFICATION.md",
    str(DECISION_PATH),
]
REQUIRED_DECISION_PHRASES = (
    "v1.1.0",
    "separate append-only plane",
    "at most one active primary use-case attribution",
    "Denies win",
    "associated",
    "controlled",
    "Pareto-efficient set",
    "does not publish a universal model rank",
    "never automatically changes",
    "prompts, responses, patches",
    "compatibility adapter or a future major release",
)


class PortfolioIntelligenceContractError(ValueError):
    """The accepted portfolio contract is incomplete, unsafe, or drifting."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PortfolioIntelligenceContractError("duplicate_json_member")
        value[key] = item
    return value


def _read_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    try:
        value = path.read_bytes()
    except OSError as exc:
        raise PortfolioIntelligenceContractError(f"{label}_unreadable") from exc
    if not value or len(value) > maximum:
        raise PortfolioIntelligenceContractError(f"{label}_size_invalid")
    return value


def _read_json(path: Path, *, maximum: int, label: str) -> dict[str, Any]:
    raw = _read_bytes(path, maximum=maximum, label=label)
    try:
        value = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PortfolioIntelligenceContractError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise PortfolioIntelligenceContractError(f"{label}_root_invalid")
    return value


def _require_fields(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise PortfolioIntelligenceContractError(f"{label}_fields_invalid")
    return value


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise PortfolioIntelligenceContractError(f"{label}_invalid")
    return value


def _require_unique_strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise PortfolioIntelligenceContractError(f"{label}_invalid")
    result = [_require_string(item, label) for item in value]
    if len(result) != len(set(result)):
        raise PortfolioIntelligenceContractError(f"{label}_duplicate")
    return result


def _validate_baseline(root: Path, baseline: object) -> dict[str, Any]:
    value = _require_fields(
        baseline,
        {
            "release",
            "manifest_path",
            "manifest_sha256",
            "current_main_matches_release_manifest",
            "legacy_manifest_release_line",
            "legacy_manifest_treatment",
        },
        "baseline",
    )
    expected = {
        "release": "1.0.0",
        "manifest_path": str(BASELINE_MANIFEST_PATH),
        "manifest_sha256": BASELINE_MANIFEST_SHA256,
        "current_main_matches_release_manifest": True,
        "legacy_manifest_release_line": "0.2",
        "legacy_manifest_treatment": "preserve_v1_fixture_and_version_any_correction",
    }
    if value != expected:
        raise PortfolioIntelligenceContractError("baseline_changed")
    path = root / BASELINE_MANIFEST_PATH
    raw = _read_bytes(path, maximum=MAX_BASELINE_BYTES, label="baseline_manifest")
    if hashlib.sha256(raw).hexdigest() != BASELINE_MANIFEST_SHA256:
        raise PortfolioIntelligenceContractError("baseline_manifest_digest_mismatch")
    manifest = _read_json(
        path, maximum=MAX_BASELINE_BYTES, label="baseline_manifest"
    )
    try:
        validate_contract_manifest(manifest)
    except ValueError as exc:
        raise PortfolioIntelligenceContractError(
            "baseline_manifest_contract_invalid"
        ) from exc
    release_line = manifest.get("compatibility", {}).get("current_release_line")
    if release_line != "0.2":
        raise PortfolioIntelligenceContractError(
            "baseline_manifest_legacy_release_line_changed"
        )
    return manifest


def _schema_identity(item: object) -> tuple[str, int]:
    if not isinstance(item, Mapping):
        raise PortfolioIntelligenceContractError("current_manifest_schema_invalid")
    schema_id = item.get("schema_id")
    schema_version = item.get("schema_version")
    if (
        not isinstance(schema_id, str)
        or isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
    ):
        raise PortfolioIntelligenceContractError("current_manifest_schema_invalid")
    return schema_id, schema_version


def _validate_additive_manifest(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> None:
    if current.get("schema_id") != baseline.get("schema_id"):
        raise PortfolioIntelligenceContractError("current_manifest_identity_changed")
    current_version = current.get("schema_version")
    baseline_version = baseline.get("schema_version")
    if (
        isinstance(current_version, bool)
        or not isinstance(current_version, int)
        or not isinstance(baseline_version, int)
        or current_version < baseline_version
    ):
        raise PortfolioIntelligenceContractError("current_manifest_version_regressed")
    baseline_schema_items = baseline.get("schemas")
    current_schema_items = current.get("schemas")
    if not isinstance(baseline_schema_items, list) or not isinstance(
        current_schema_items, list
    ):
        raise PortfolioIntelligenceContractError("current_manifest_schemas_invalid")
    baseline_schemas = {
        _schema_identity(item): item for item in baseline_schema_items
    }
    current_schemas = {
        _schema_identity(item): item for item in current_schema_items
    }
    if len(current_schemas) != len(current_schema_items):
        raise PortfolioIntelligenceContractError("current_manifest_schema_duplicate")
    for identity, expected in baseline_schemas.items():
        if current_schemas.get(identity) != expected:
            raise PortfolioIntelligenceContractError(
                f"v1_schema_changed:{identity[0]}:v{identity[1]}"
            )
    for field in (
        "policy_action_semantics",
        "request_status_semantics",
        "content_boundary",
    ):
        if current.get(field) != baseline.get(field):
            raise PortfolioIntelligenceContractError(f"v1_{field}_changed")
    baseline_errors = _require_unique_strings(
        baseline.get("error_codes"), "baseline_error_codes"
    )
    current_errors = _require_unique_strings(
        current.get("error_codes"), "current_error_codes"
    )
    if not set(baseline_errors).issubset(current_errors):
        raise PortfolioIntelligenceContractError("v1_error_code_removed")


def _validate_compatibility(value: object) -> None:
    compatibility = _require_fields(
        value,
        {
            "release_kind",
            "existing_request_fields",
            "existing_response_fields",
            "existing_authentication",
            "existing_error_codes",
            "existing_ordering",
            "existing_pagination",
            "existing_idempotency",
            "existing_retry_behavior",
            "existing_behavior",
            "existing_schema_entries",
            "new_request_metadata",
            "new_routes",
            "new_schemas",
            "breaking_changes",
            "breaking_change_action",
            "migration_gate",
        },
        "compatibility",
    )
    expected = {
        "release_kind": "backward_compatible_minor",
        "existing_request_fields": "unchanged",
        "existing_response_fields": "unchanged",
        "existing_authentication": "unchanged",
        "existing_error_codes": "unchanged",
        "existing_ordering": "unchanged",
        "existing_pagination": "unchanged",
        "existing_idempotency": "unchanged",
        "existing_retry_behavior": "unchanged",
        "existing_behavior": "unchanged",
        "existing_schema_entries": "byte_equivalent",
        "new_request_metadata": "optional_and_hormuz_owned",
        "new_routes": "additive_under_v1",
        "new_schemas": "new_identity_at_version_1",
        "breaking_changes": [],
        "breaking_change_action": "compatibility_adapter_or_future_major_release",
        "migration_gate": 214,
    }
    if compatibility != expected:
        raise PortfolioIntelligenceContractError("compatibility_contract_changed")


def _validate_hierarchy(value: object) -> None:
    hierarchy = _require_fields(
        value,
        {
            "root",
            "types",
            "parent_rule",
            "cycles",
            "primary_analytics_scope",
            "material_changes",
            "lifecycle_states",
            "display_name",
            "description",
        },
        "work_scope_hierarchy",
    )
    if hierarchy != {
        "root": "organization",
        "types": ["portfolio", "initiative", "use_case"],
        "parent_rule": "single_parent_to_nearest_configured_ancestor",
        "cycles": "forbidden",
        "primary_analytics_scope": "use_case",
        "material_changes": "append_new_version",
        "lifecycle_states": ["active", "archived", "tombstoned"],
        "display_name": "bounded_customer_admin_metadata_not_external_work_content",
        "description": "excluded",
    }:
        raise PortfolioIntelligenceContractError("work_scope_hierarchy_changed")


def _validate_entities(value: object) -> None:
    if not isinstance(value, list) or len(value) != len(ENTITY_SCHEMAS):
        raise PortfolioIntelligenceContractError("entities_invalid")
    seen: set[str] = set()
    identities: set[tuple[str, int]] = set()
    for raw in value:
        entity = _require_fields(
            raw,
            {
                "id",
                "schema_id",
                "schema_version",
                "mutability",
                "tenant_key",
                "content_boundary",
            },
            "entity",
        )
        entity_id = _require_string(entity["id"], "entity_id")
        if entity_id in seen or ENTITY_SCHEMAS.get(entity_id) != entity["schema_id"]:
            raise PortfolioIntelligenceContractError("entity_identity_changed")
        schema_version = entity["schema_version"]
        if isinstance(schema_version, bool) or schema_version != 1:
            raise PortfolioIntelligenceContractError("entity_schema_version_changed")
        identity = (str(entity["schema_id"]), int(schema_version))
        if identity in identities:
            raise PortfolioIntelligenceContractError("entity_schema_duplicate")
        if entity["mutability"] not in ENTITY_MUTABILITY:
            raise PortfolioIntelligenceContractError("entity_mutability_invalid")
        if entity["tenant_key"] != "organization_id":
            raise PortfolioIntelligenceContractError("entity_tenant_key_changed")
        if entity["content_boundary"] not in ENTITY_CONTENT_BOUNDARIES:
            raise PortfolioIntelligenceContractError("entity_content_boundary_invalid")
        seen.add(entity_id)
        identities.add(identity)
    if set(ENTITY_SCHEMAS) != seen:
        raise PortfolioIntelligenceContractError("entity_set_changed")


def _validate_attribution(value: object) -> None:
    attribution = _require_fields(
        value,
        {
            "active_primary_use_cases_per_attempt",
            "sources",
            "precedence",
            "post_run_behavior",
            "confidence_classes",
            "confidence_meaning",
            "coverage_denominators",
            "request_header",
            "provider_forwarding",
            "authorization_time",
            "invalid_behavior",
            "missing_behavior",
            "ambiguous_behavior",
            "correction",
            "historical_usage_mutation",
        },
        "attribution",
    )
    if attribution != {
        "active_primary_use_cases_per_attempt": 1,
        "sources": [
            "authenticated_optional_request_metadata",
            "authorized_post_run_binding",
            "server_side_default_binding",
        ],
        "precedence": [
            "authenticated_optional_request_metadata",
            "server_side_default_binding",
            "unattributed",
        ],
        "post_run_behavior": "authorized_binding_may_append_or_supersede_after_attempt",
        "confidence_classes": [
            "explicit_authorized",
            "server_side_default",
            "authorized_post_run",
            "unattributed",
            "ambiguous",
        ],
        "confidence_meaning": "binding_source_quality_not_causal_probability",
        "coverage_denominators": [
            "eligible_governed_attempts",
            "eligible_governed_spend",
            "eligible_external_outcome_events",
            "eligible_association_candidates",
        ],
        "request_header": "X-Hormuz-Work-Scope",
        "provider_forwarding": "strip_before_provider_egress",
        "authorization_time": "before_budget_reservation_and_provider_egress",
        "invalid_behavior": "fail_closed_before_provider_egress",
        "missing_behavior": "explicit_unattributed_unless_policy_requires_attribution",
        "ambiguous_behavior": "explicit_ambiguous_no_guess",
        "correction": "append_superseding_event",
        "historical_usage_mutation": "forbidden",
    }:
        raise PortfolioIntelligenceContractError("attribution_contract_changed")


def _validate_budget_and_evidence(budget_value: object, evidence_value: object) -> None:
    budget = _require_fields(
        budget_value,
        {
            "applicable_scopes",
            "combination_rule",
            "allowed_model_sets",
            "reservation_time",
            "reservation_consistency",
            "historical_plan_binding",
            "cost_bases",
            "cross_basis_relabeling",
        },
        "budget_policy",
    )
    if budget["applicable_scopes"] != [
        "organization",
        "team",
        "actor",
        "application",
        "portfolio",
        "initiative",
        "use_case",
    ] or budget["combination_rule"] != "all_apply_denies_win_lowest_numeric_ceiling":
        raise PortfolioIntelligenceContractError("budget_precedence_changed")
    if budget["allowed_model_sets"] != "intersection_only_never_widen":
        raise PortfolioIntelligenceContractError("budget_model_set_changed")
    if budget["reservation_time"] != "before_provider_egress" or budget[
        "reservation_consistency"
    ] != "atomic_per_organization_and_period":
        raise PortfolioIntelligenceContractError("budget_reservation_changed")
    if budget["historical_plan_binding"] != "event_time_version" or budget[
        "cross_basis_relabeling"
    ] != "forbidden":
        raise PortfolioIntelligenceContractError("budget_evidence_changed")
    if set(_require_unique_strings(budget["cost_bases"], "cost_bases")) != {
        "provider_final",
        "provider_aggregate",
        "configured_rate_card_estimate",
        "allocated_estimate",
        "credit_or_discount",
        "not_available",
    }:
        raise PortfolioIntelligenceContractError("cost_basis_set_changed")

    evidence = _require_fields(
        evidence_value,
        {
            "levels",
            "connector_default",
            "deterministic_join_default",
            "controlled_upgrade",
            "eligibility_rule",
            "minimum_coverage_rule",
            "minimum_sample_rule",
            "below_threshold_behavior",
            "temporal_proximity_is_causality",
            "unknown_or_ineligible",
        },
        "evidence",
    )
    if evidence != {
        "levels": ["descriptive", "associated", "controlled"],
        "connector_default": "descriptive",
        "deterministic_join_default": "associated",
        "controlled_upgrade": "requires_separately_approved_predeclared_design",
        "eligibility_rule": "versioned_predeclared_per_use_case_metric_rule",
        "minimum_coverage_rule": (
            "explicit_versioned_threshold_required_no_global_default"
        ),
        "minimum_sample_rule": (
            "explicit_versioned_threshold_required_no_global_default"
        ),
        "below_threshold_behavior": "inconclusive",
        "temporal_proximity_is_causality": False,
        "unknown_or_ineligible": "visible_not_dropped",
    }:
        raise PortfolioIntelligenceContractError("evidence_contract_changed")


def _validate_kpis(value: object) -> None:
    kpis = _require_fields(
        value,
        {
            "primary",
            "drivers",
            "guardrails",
            "required_dimensions",
            "model_selection",
            "universal_model_rank",
            "employee_rank",
            "insufficient_evidence",
        },
        "kpis",
    )
    if kpis["primary"] != PRIMARY_KPIS:
        raise PortfolioIntelligenceContractError("primary_kpis_changed")
    for field in ("drivers", "guardrails", "required_dimensions"):
        _require_unique_strings(kpis[field], f"kpis_{field}")
    if (
        kpis["model_selection"] != "pareto_frontier_per_use_case"
        or kpis["universal_model_rank"] != "forbidden"
        or kpis["employee_rank"] != "forbidden"
        or kpis["insufficient_evidence"] != "inconclusive"
    ):
        raise PortfolioIntelligenceContractError("kpi_decision_boundary_changed")


def _validate_authorization_and_api(auth_value: object, api_value: object) -> None:
    authorization = _require_fields(
        auth_value,
        {
            "authentication",
            "tenant_source",
            "body_supplied_tenant_scope",
            "roles",
            "team_lead_scope",
            "self_scope",
            "finance_scope",
            "platform_scope",
            "portfolio_admin_scope",
            "privileged_read_audit",
            "role_grants_provider_access",
        },
        "authorization",
    )
    if authorization["roles"] != AUTHORIZATION_ROLES:
        raise PortfolioIntelligenceContractError("authorization_roles_changed")
    if (
        authorization["authentication"]
        != "existing_static_or_oidc_bearer_boundary"
        or authorization["tenant_source"]
        != "server_resolved_identity_or_connector_binding"
        or authorization["body_supplied_tenant_scope"] != "forbidden"
        or authorization["privileged_read_audit"]
        != "required_before_result_delivery"
        or authorization["self_scope"]
        != "existing_actor_self_usage_only_no_portfolio_outcome_or_peer_join"
        or authorization["role_grants_provider_access"] is not False
    ):
        raise PortfolioIntelligenceContractError("authorization_boundary_changed")

    api = _require_fields(
        api_value,
        {
            "admin_prefix",
            "connector_prefix",
            "existing_routes_unchanged",
            "new_routes",
            "route_contracts",
            "public_schema_version",
            "request_schemas",
            "response_schemas",
            "connector_request_ownership",
            "request_validation",
            "response_shaping",
            "collection_pagination",
            "default_page_size",
            "maximum_page_size",
            "ordering",
            "cursor_binding",
            "mutation_idempotency",
            "connector_idempotency",
            "retry_behavior",
            "error_schema",
            "error_schema_version",
            "provider_route_error_delivery",
        },
        "api",
    )
    if (
        api["existing_routes_unchanged"] != EXISTING_ROUTES
        or api["new_routes"] != NEW_ROUTES
        or api["route_contracts"] != ROUTE_CONTRACTS
    ):
        raise PortfolioIntelligenceContractError("api_route_inventory_changed")
    if (
        api["admin_prefix"] != "/v1/admin/portfolio"
        or api["connector_prefix"] != "/v1/connectors"
        or isinstance(api["public_schema_version"], bool)
        or api["public_schema_version"] != 1
        or api["request_schemas"] != API_REQUEST_SCHEMAS
        or api["response_schemas"] != API_RESPONSE_SCHEMAS
        or api["connector_request_ownership"]
        != "provider_owned_signed_raw_bytes_not_hormuz_json"
        or api["request_validation"] != "strict_unknown_fields_rejected"
        or api["response_shaping"] != "explicit_contract_no_database_rows"
        or api["collection_pagination"] != "opaque_frozen_window_cursor"
        or api["default_page_size"] != 50
        or api["maximum_page_size"] != 100
        or api["ordering"] != "event_or_creation_time_then_opaque_id"
        or api["cursor_binding"]
        != "organization_role_filters_window_order_and_schema"
        or api["mutation_idempotency"] != "required_idempotency_key"
        or api["connector_idempotency"]
        != "verified_source_delivery_identity"
        or api["retry_behavior"]
        != "reads_safe_mutations_replay_only_by_verified_idempotency_identity"
        or api["error_schema"] != "hormuz.portfolio-error"
        or isinstance(api["error_schema_version"], bool)
        or api["error_schema_version"] != 1
        or api["provider_route_error_delivery"]
        != "provider_native_body_plus_versioned_hormuz_header"
    ):
        raise PortfolioIntelligenceContractError("api_behavior_changed")


def _validate_recommendations_and_content(
    recommendation_value: object, content_value: object
) -> None:
    recommendation = _require_fields(
        recommendation_value,
        {
            "allowed_types",
            "source",
            "automatic_application",
            "required_review",
            "pre_apply_checks",
            "drift_behavior",
            "expiry",
        },
        "recommendations",
    )
    _require_unique_strings(recommendation["allowed_types"], "recommendation_types")
    if (
        recommendation["source"] != "eligible_versioned_scorecard_snapshot"
        or recommendation["automatic_application"] is not False
        or recommendation["required_review"]
        != "authorized_policy_administrator"
        or recommendation["pre_apply_checks"]
        != [
            "semantic_compare",
            "request_preview",
            "saved_scenario_evaluation",
            "rollback_plan",
        ]
        or recommendation["drift_behavior"] != "invalidate"
        or recommendation["expiry"] != "required"
    ):
        raise PortfolioIntelligenceContractError("recommendation_boundary_changed")

    content = _require_fields(
        content_value,
        {"allowed", "excluded", "release_evidence", "debug_content"},
        "content_boundary",
    )
    _require_unique_strings(content["allowed"], "content_allowed")
    excluded = set(_require_unique_strings(content["excluded"], "content_excluded"))
    if excluded != REQUIRED_EXCLUSIONS:
        raise PortfolioIntelligenceContractError("content_exclusion_set_changed")
    if (
        content["release_evidence"] != "content_free"
        or content["debug_content"]
        != "outside_contract_and_disabled_by_default"
    ):
        raise PortfolioIntelligenceContractError("content_evidence_boundary_changed")


def _validate_gates_and_documentation(root: Path, contract: Mapping[str, Any]) -> None:
    if contract.get("child_gates") != CHILD_GATES:
        raise PortfolioIntelligenceContractError("child_gate_set_changed")
    if contract.get("conditional_gates") != CONDITIONAL_GATES:
        raise PortfolioIntelligenceContractError("conditional_gate_set_changed")
    nonclaims = _require_unique_strings(contract.get("nonclaims"), "nonclaims")
    if set(nonclaims) != REQUIRED_NONCLAIMS:
        raise PortfolioIntelligenceContractError("nonclaim_set_changed")
    documentation = _require_unique_strings(
        contract.get("documentation"), "documentation"
    )
    if documentation != REQUIRED_DOCUMENTATION:
        raise PortfolioIntelligenceContractError("documentation_set_changed")
    for relative in documentation:
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PortfolioIntelligenceContractError(
                f"documentation_unreadable:{relative}"
            ) from exc
        if not text.strip():
            raise PortfolioIntelligenceContractError(
                f"documentation_empty:{relative}"
            )
    decision = (root / DECISION_PATH).read_text(encoding="utf-8")
    for phrase in REQUIRED_DECISION_PHRASES:
        if phrase not in decision:
            raise PortfolioIntelligenceContractError(
                f"accepted_decision_document_drift:{phrase}"
            )
    if "**Status:** Accepted" not in decision or DECISION_RECORD not in decision:
        raise PortfolioIntelligenceContractError("accepted_decision_record_missing")


def validate_portfolio_intelligence_contract(
    root: Path,
    *,
    current_manifest: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    """Validate the accepted plan and its additive v1 compatibility boundary."""

    root = root.resolve()
    contract = _read_json(
        root / CONTRACT_PATH, maximum=MAX_CONTRACT_BYTES, label="contract"
    )
    _require_fields(contract, TOP_LEVEL_FIELDS, "contract")
    if (
        contract["schema_id"] != SCHEMA_ID
        or isinstance(contract["schema_version"], bool)
        or contract["schema_version"] != SCHEMA_VERSION
    ):
        raise PortfolioIntelligenceContractError("contract_identity_changed")
    if (
        contract["status"] != "accepted"
        or contract["target_release"] != "1.1.0"
        or contract["decision_record"] != DECISION_RECORD
    ):
        raise PortfolioIntelligenceContractError("contract_acceptance_changed")

    baseline = _validate_baseline(root, contract["baseline"])
    current = contract_manifest() if current_manifest is None else dict(current_manifest)
    _validate_additive_manifest(baseline, current)
    _validate_compatibility(contract["compatibility"])
    _validate_hierarchy(contract["work_scope_hierarchy"])
    _validate_entities(contract["entities"])
    if contract["identity_and_lifecycle"] != IDENTITY_AND_LIFECYCLE:
        raise PortfolioIntelligenceContractError("identity_and_lifecycle_changed")
    if contract["temporal_and_lifecycle"] != TEMPORAL_AND_LIFECYCLE:
        raise PortfolioIntelligenceContractError("temporal_and_lifecycle_changed")
    _validate_attribution(contract["attribution"])
    _validate_budget_and_evidence(contract["budget_policy"], contract["evidence"])
    _validate_kpis(contract["kpis"])
    _validate_authorization_and_api(contract["authorization"], contract["api"])
    _validate_recommendations_and_content(
        contract["recommendations"], contract["content_boundary"]
    )
    _validate_gates_and_documentation(root, contract)

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "passed",
        "target_release": "1.1.0",
        "baseline_release": "1.0.0",
        "baseline_manifest_sha256": BASELINE_MANIFEST_SHA256,
        "entity_count": len(ENTITY_SCHEMAS),
        "new_route_count": len(NEW_ROUTES),
        "primary_kpi_count": len(PRIMARY_KPIS),
        "child_gate_count": len(CHILD_GATES),
        "breaking_change_count": 0,
        "automatic_policy_application": False,
        "employee_ranking": False,
        "content_free_release_evidence": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the accepted v1.1 portfolio-intelligence contract"
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        result = validate_portfolio_intelligence_contract(args.root)
    except PortfolioIntelligenceContractError as error:
        print(f"portfolio intelligence contract verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
