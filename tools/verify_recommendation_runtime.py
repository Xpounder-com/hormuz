#!/usr/bin/env python3
"""Verify the fixed provider-free policy recommendation runtime."""

from __future__ import annotations

import argparse
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hormuz._recommendation_schema import TABLE_DDL
from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
from hormuz.portfolio_wire import (
    RECOMMENDATIONS,
    RECOMMENDATION_OPERATIONS,
    RESPONSE_BYTES,
    PortfolioError,
    route,
    validate,
)
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
)
from hormuz.recommendation_kernel import (
    MAX_EVALUATION_BYTES,
    _ALLOWED_CHANGE_TYPES,
)
from hormuz.recommendation_repository import _CURSOR_TTL, _MAX_CONFLICTS
from tools import verify_role_view_runtime as role_verifier


PLAN_PATH = "docs/recommendation-runtime-plan-v1.json"
PLAN_SHA256 = "06e1d86dbbbaedabb5576b6d6321a2ec74b4418847ceb1a6fe11f4c25307dab3"
PREDECESSOR_PATH = "docs/role-view-runtime-plan-v1.json"
PREDECESSOR_FILE_SHA256 = (
    "ba13527aa781e88232ca1403468e181c759004628645bb9bc48e660955a1e533"
)
PREDECESSOR_CANONICAL_SHA256 = (
    "1afced7771629acf838391eafbe597e9e79c79aac9e8f27d6ac818b23145d822"
)
BASE_MAIN_COMMIT = "edd5b04a2c84c854190c41a774ab5a9c89a7ca41"
EXPECTED_ACL = (
    249,
    "5a7edf2147cfa67c7c32a3f64ac2f825ae68e74ec323e1b789602dc65155ea2b",
)
TABLES = tuple(TABLE_DDL)
ROUTES = (
    "GET /v1/admin/portfolio/recommendations",
    "GET /v1/admin/portfolio/recommendations/{recommendation_id}",
    "POST /v1/admin/portfolio/recommendations/{recommendation_id}/decisions",
)
CHANGE_TYPES = (
    "budget_plan_change",
    "model_allowlist_change",
    "model_fallback_change",
    "output_or_cost_cap_change",
    "routing_policy_change",
)
PUBLIC_STATES = (
    "pending",
    "accepted",
    "rejected",
    "expired",
    "invalidated",
    "superseded",
)
LIFECYCLE_EVENTS = (
    "generated",
    "accepted",
    "rejected",
    "expired",
    "invalidated",
    "superseded",
    "applied",
)
PRE_APPLY_EVIDENCE = (
    "semantic_compare",
    "request_preview",
    "saved_scenario_evaluation",
    "rollback_plan",
)
DECISION_FIXTURE_PATH = "tests/fixtures/recommendation/decision-v1.json"
SCORECARD_FIXTURE_PATH = "tests/fixtures/scorecard/runtime-v1.json"
SUPPRESSION_COVERAGE_FIELDS = (
    "eligible_governed_attempts",
    "eligible_governed_spend",
    "eligible_external_outcome_events",
    "eligible_association_candidates",
    "pricing",
    "connector",
    "association",
)
SUPPRESSION_GUARDRAILS = (
    "comparability",
    "coverage_and_sample_eligibility",
    "freshness",
    "quality",
)
EXCLUSIONS = (
    "prompts",
    "responses",
    "work_item_titles",
    "work_item_bodies",
    "comments",
    "code",
    "filenames",
    "credentials",
    "employee_rankings",
    "person_comparisons",
    "arbitrary_policy_patches",
)
SOURCE_PATHS = (
    ".github/workflows/ci.yml",
    "MANIFEST.in",
    "README.md",
    "docs/DURABLE_DATA.md",
    "docs/PORTFOLIO_INTELLIGENCE.md",
    "docs/PORTFOLIO_RECOMMENDATIONS.md",
    "docs/ROADMAP.md",
    "docs/durable-data-v1.json",
    "hormuz/_portfolio_sql.py",
    "hormuz/_recommendation_schema.py",
    "hormuz/_sqlite_schema.py",
    "hormuz/migrations/postgresql/0024_policy_recommendations.sql",
    "hormuz/policy_analysis.py",
    "hormuz/portfolio-intelligence-wire-v1.json",
    "hormuz/portfolio_repository.py",
    "hormuz/portfolio_wire.py",
    "hormuz/postgres.py",
    "hormuz/recommendation_kernel.py",
    "hormuz/recommendation_repository.py",
    "tests/_postgres_fixture.py",
    DECISION_FIXTURE_PATH,
    "tests/test_association_transition_preflight.py",
    "tests/test_association_transition_plan.py",
    "tests/test_association_runtime_plan.py",
    "tests/test_attribution_schema.py",
    "tests/test_budget_schema.py",
    "tests/test_durable_data_inventory.py",
    "tests/test_finance_account_binding_transition_preflight.py",
    "tests/test_finance_collection_postgres_runtime_plan.py",
    "tests/test_finance_collection_runtime_plan.py",
    "tests/test_linear_transition_preflight.py",
    "tests/test_linear_runtime_plan.py",
    "tests/test_outcome_schema.py",
    "tests/test_postgres_budget.py",
    "tests/test_postgres_budget_transition.py",
    "tests/test_postgres_finance.py",
    "tests/test_postgres_finance_collection_runtime_transition.py",
    "tests/test_postgres_finance_transition.py",
    "tests/test_postgres_linear_connector_runtime.py",
    "tests/test_postgres_linear_snapshot_runtime.py",
    "tests/test_postgres_migration_rls.py",
    "tests/test_postgres_outcome_transition.py",
    "tests/test_postgres_recommendation_runtime.py",
    "tests/test_postgres_role_view_runtime.py",
    "tests/test_postgres_scorecard_runtime.py",
    "tests/test_postgres_test_boundaries.py",
    "tests/test_policy_analysis.py",
    "tests/test_recommendation_kernel.py",
    "tests/test_recommendation_runtime.py",
    "tests/test_recommendation_runtime_plan.py",
    "tests/test_recommendation_transition.py",
    "tests/test_role_view_runtime.py",
    "tests/test_role_view_runtime_plan.py",
    "tests/test_role_view_transition.py",
    "tests/test_scorecard_runtime.py",
    "tests/test_scorecard_runtime_plan.py",
    "tests/test_scorecard_transition.py",
    "tests/test_sqlite_attribution_transition.py",
    "tests/test_sqlite_budget_transition.py",
    "tests/test_sqlite_finance.py",
    "tests/test_sqlite_finance_collection_transition.py",
    "tests/test_sqlite_finance_native_attempt_transition.py",
    "tests/test_sqlite_finance_transition.py",
    "tests/test_sqlite_outcome_transition.py",
    "tests/test_sqlite_registry_transition.py",
    "tests/test_store.py",
    "tools/verify_association_runtime_plan.py",
    "tools/verify_association_transition_plan.py",
    "tools/verify_core_wheel.py",
    "tools/verify_durable_data_inventory.py",
    "tools/verify_finance_collection_postgres_runtime.py",
    "tools/verify_finance_collection_runtime.py",
    "tools/verify_finance_native_attempt_transition_plan.py",
    "tools/verify_linear_reconciliation_plan.py",
    "tools/verify_linear_runtime_plan.py",
    "tools/verify_linear_transition_plan.py",
    "tools/verify_role_view_runtime.py",
    "tools/verify_scorecard_runtime_plan.py",
)
REQUIRED_FILES = tuple(dict.fromkeys((
    PLAN_PATH,
    PREDECESSOR_PATH,
    "docs/portfolio-intelligence-wire-v1.json",
    "hormuz/portfolio-intelligence-wire-v1.json",
    "tools/verify_recommendation_runtime.py",
    *SOURCE_PATHS,
    *role_verifier.REQUIRED_FILES,
)))
EXPECTED_GATES = {
    "role_view_runtime_predecessor_verified": True,
    "recommendation_runtime_implemented": True,
    "policy_preview_and_scenarios_verified": True,
    "sqlite_runtime_tests_implemented": True,
    "postgresql_runtime_tests_implemented": True,
    "migration_and_rollback_tests_implemented": True,
    "provider_free_source_verified": True,
    "provider_free_wheel_verified": True,
    "exact_main_ci_verified": False,
    "live_external_data_authorized": False,
    "automatic_application_authorized": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_NONCLAIMS = {
    "authorized_live_connector_or_customer_workspace",
    "complete_live_external_history",
    "automatic_policy_or_budget_application",
    "causal_savings_or_business_outcome",
    "individual_productivity_or_employee_ranking",
    "exact_main_CI",
    "final_candidate_acceptance",
    "v1.3.0_release",
}


class RecommendationRuntimePlanError(ValueError):
    """A fixed, content-free recommendation runtime refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RecommendationRuntimePlanError(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("recommendation_runtime_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 32 * 1024 * 1024:
            _fail("recommendation_runtime_plan_invalid")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail(
                "recommendation_runtime_plan_invalid"
            ),
        )
    except RecommendationRuntimePlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("recommendation_runtime_plan_invalid")


def canonical_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("recommendation_runtime_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def _validate_predecessor(root: Path) -> None:
    try:
        payload = (root / PREDECESSOR_PATH).read_bytes()
    except OSError:
        _fail("recommendation_runtime_source_kit_incomplete")
    predecessor = _read_json(root / PREDECESSOR_PATH)
    if (
        hashlib.sha256(payload).hexdigest() != PREDECESSOR_FILE_SHA256
        or canonical_digest(predecessor) != PREDECESSOR_CANONICAL_SHA256
        or predecessor.get("feature_issue") != 223
        or predecessor.get("target_release") != "1.3.0"
        or predecessor.get("gates", {}).get("recommendations_implemented")
        is not False
    ):
        _fail("recommendation_runtime_predecessor_changed")
    try:
        role_verifier._validate_successor_predecessor(root)
    except role_verifier.RoleViewRuntimePlanError as error:
        mapping = {
            "role_view_runtime_source_kit_incomplete": "recommendation_runtime_source_kit_incomplete",
            "role_view_runtime_source_changed": "recommendation_runtime_source_changed",
            "role_view_runtime_predecessor_changed": "recommendation_runtime_predecessor_changed",
        }
        _fail(mapping.get(error.code, "recommendation_runtime_predecessor_invalid"))


def _validate_migration(root: Path, plan: dict) -> None:
    relative = "hormuz/migrations/postgresql/0024_policy_recommendations.sql"
    try:
        migration = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("recommendation_runtime_source_kit_incomplete")
    for table in TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("recommendation_runtime_migration_invalid")
        required = (
            f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
            f"CREATE TRIGGER {table}_immutable",
            f"ON {{schema}}.{table}",
            "BEFORE UPDATE OR DELETE OR TRUNCATE",
            "portfolio_reject_mutation()",
        )
        if any(item not in migration for item in required):
            _fail("recommendation_runtime_migration_invalid")
    forbidden = (
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT TRUNCATE",
        "WITH GRANT OPTION",
    )
    if (
        migration.count("portfolio_reject_mutation()") != len(TABLES)
        or migration.count(
            "CREATE FUNCTION {schema}.portfolio_policy_recommendation_active_policy_receipt()"
        )
        != 1
        or migration.count(
            "REVOKE ALL ON FUNCTION {schema}.portfolio_policy_recommendation_active_policy_receipt()"
        )
        != 1
        or migration.count(
            "GRANT EXECUTE ON FUNCTION {schema}.portfolio_policy_recommendation_active_policy_receipt()"
        )
        != 1
        or "GRANT SELECT ON {schema}.policy_control_events TO {runtime_role}" in migration
        or any(item in migration.upper() for item in forbidden)
        or plan.get("source_sha256", {}).get(relative)
        != hashlib.sha256(migration.encode("utf-8")).hexdigest()
    ):
        _fail("recommendation_runtime_migration_invalid")


def _validate_wire(root: Path) -> None:
    documented = "docs/portfolio-intelligence-wire-v1.json"
    packaged = "hormuz/portfolio-intelligence-wire-v1.json"
    for relative in (documented, packaged):
        wire = _read_json(root / relative)
        definitions = wire.get("$defs") if isinstance(wire, dict) else None
        if not isinstance(definitions, dict):
            _fail("recommendation_runtime_wire_invalid")
        recommendation = definitions.get("hormuz.policy-recommendation")
        decision = definitions.get("hormuz.policy-recommendation-decision-request")
        page = definitions.get("hormuz.policy-recommendation-page")
        proposal = definitions.get("recommendation_proposal")
        evidence = definitions.get("pre_apply_evidence")
        try:
            valid = (
                recommendation["additionalProperties"] is False
                and recommendation["properties"]["automatic_application"][
                    "const"
                ]
                is False
                and tuple(recommendation["properties"]["state"]["enum"])
                == PUBLIC_STATES
                and decision["additionalProperties"] is False
                and decision["properties"]["decision"]["enum"]
                == ["accepted", "rejected"]
                and decision["properties"]["reason_code"]["enum"]
                == ["accepted", "rejected"]
                and page["additionalProperties"] is False
                and page["properties"]["items"]["maxItems"] == 100
                and proposal["additionalProperties"] is False
                and tuple(proposal["properties"]["change_type"]["enum"])
                == CHANGE_TYPES
                and evidence["additionalProperties"] is False
                and tuple(evidence["required"]) == PRE_APPLY_EVIDENCE
                and (
                    relative == documented
                    or recommendation["properties"]["pre_apply_evidence"][
                        "$ref"
                    ]
                    == "#/$defs/pre_apply_evidence"
                )
            )
        except (KeyError, TypeError):
            valid = False
        if not valid:
            _fail("recommendation_runtime_wire_invalid")


def _validate_runtime_contract() -> None:
    expected = {
        "list_recommendations",
        "show_recommendation",
        "decide_recommendation",
    }
    try:
        observed = {
            route("GET", RECOMMENDATIONS)[0],
            route("GET", RECOMMENDATIONS + "/recommendation-id")[0],
            route(
                "POST",
                RECOMMENDATIONS + "/recommendation-id/decisions",
            )[0],
        }
        try:
            route("POST", RECOMMENDATIONS)
        except PortfolioError as error:
            generation_closed = error.code == "not_found"
        else:
            generation_closed = False
    except (PortfolioError, TypeError):
        _fail("recommendation_runtime_route_invalid")
    if (
        observed != expected
        or RECOMMENDATION_OPERATIONS != frozenset(expected)
        or not generation_closed
        or tuple(sorted(_ALLOWED_CHANGE_TYPES)) != tuple(sorted(CHANGE_TYPES))
        or MAX_EVALUATION_BYTES != RESPONSE_BYTES
        or _CURSOR_TTL != timedelta(minutes=5)
        or _MAX_CONFLICTS != 1000
    ):
        _fail("recommendation_runtime_route_invalid")


def _validate_decision_fixture(root: Path) -> None:
    fixture = _read_json(root / DECISION_FIXTURE_PATH)
    try:
        scorecard = fixture["scorecard_fixture"]
        scorecard_path = root / scorecard["path"]
        suppression = fixture["suppression_cases"]
        expected = fixture["expected_evaluation"]
        preview_context = expected["bindings"]["request_preview_context"]
        accepted = fixture["accepted_decision"]
        rejected = fixture["rejected_decision"]
        valid = (
            set(fixture)
            == {
                "schema_id",
                "schema_version",
                "scorecard_fixture",
                "created_at",
                "scorecard_evaluation_digest",
                "baseline_policy",
                "candidate_policy",
                "scenario_suite",
                "preview_request",
                "generation_request",
                "expected_evaluation",
                "accepted_decision",
                "rejected_decision",
                "suppression_cases",
            }
            and fixture["schema_id"]
            == "hormuz.recommendation-decision-fixture"
            and fixture["schema_version"] == 1
            and scorecard["path"] == SCORECARD_FIXTURE_PATH
            and scorecard["sha256"]
            == hashlib.sha256(scorecard_path.read_bytes()).hexdigest()
            and suppression
            == {
                "coverage_fields": list(SUPPRESSION_COVERAGE_FIELDS),
                "guardrails": list(SUPPRESSION_GUARDRAILS),
                "sample_eligibility": True,
            }
            and set(expected)
            == {
                "schema_id",
                "schema_version",
                "recommendation",
                "bindings",
                "verification_summary",
                "explanation",
                "pre_apply_evidence",
            }
            and set(preview_context)
            == {
                "actor",
                "client",
                "protocol",
                "requested_model",
                "requested_output_tokens",
                "usage_basis",
                "usage_period",
                "usage_snapshot_sha256",
            }
            and set(preview_context["actor"])
            == {
                "actor_id",
                "team_id",
                "organization_id",
                "identity_type",
                "clearance",
                "authentication_source",
            }
            and set(preview_context["usage_period"])
            == {"starts_at", "ends_before"}
            and re.fullmatch(
                r"[0-9a-f]{64}", preview_context["usage_snapshot_sha256"]
            )
            is not None
            and expected["recommendation"]["automatic_application"] is False
            and accepted["decision"] == "accepted"
            and accepted["reason_code"] == "accepted"
            and accepted["pre_apply_evidence"] == expected["pre_apply_evidence"]
            and rejected["decision"] == "rejected"
            and rejected["reason_code"] == "rejected"
            and rejected["pre_apply_evidence"] is None
        )
        validate(accepted, "hormuz.policy-recommendation-decision-request")
        validate(rejected, "hormuz.policy-recommendation-decision-request")
    except (KeyError, OSError, TypeError, PortfolioError):
        valid = False
    if not valid:
        _fail("recommendation_runtime_decision_fixture_invalid")


def _validate_inventory(root: Path) -> None:
    try:
        from tools.verify_durable_data_inventory import (
            validate_durable_data_inventory,
        )

        inventory = validate_durable_data_inventory(root)
    except (ImportError, ValueError, OSError):
        _fail("recommendation_runtime_inventory_invalid")
    if (
        inventory.get("database_class_count") != 40
        or inventory.get("sqlite_table_count") != 84
        or inventory.get("postgresql_table_count") != 92
    ):
        _fail("recommendation_runtime_inventory_invalid")


def _validate_distribution_and_ci(root: Path) -> None:
    try:
        from tools import verify_core_wheel as packaging
    except ImportError:
        _fail("recommendation_runtime_distribution_invalid")
    required_wheel = {
        "hormuz/_recommendation_schema.py",
        "hormuz/recommendation_kernel.py",
        "hormuz/recommendation_repository.py",
        "hormuz/portfolio-intelligence-wire-v1.json",
        "hormuz/migrations/postgresql/0024_policy_recommendations.sql",
    }
    required_sdist = required_wheel | {
        "docs/PORTFOLIO_RECOMMENDATIONS.md",
        PLAN_PATH,
        "tools/verify_recommendation_runtime.py",
        "tests/test_recommendation_runtime.py",
        "tests/test_recommendation_kernel.py",
        DECISION_FIXTURE_PATH,
        "tests/test_recommendation_runtime_plan.py",
        "tests/test_recommendation_transition.py",
        "tests/test_postgres_recommendation_runtime.py",
    }
    if (
        not required_wheel.issubset(
            packaging.REQUIRED_RECOMMENDATION_RUNTIME_WHEEL_PATHS
        )
        or not required_sdist.issubset(
            packaging.REQUIRED_RECOMMENDATION_RUNTIME_SDIST_PATHS
        )
    ):
        _fail("recommendation_runtime_distribution_invalid")
    try:
        ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("recommendation_runtime_source_kit_incomplete")
    for required in (
        "tools/verify_recommendation_runtime.py",
        "tests.test_recommendation_runtime",
        "tests.test_recommendation_kernel",
        "tests.test_recommendation_runtime_plan",
        "tests.test_recommendation_transition",
        "test_postgres_recommendation_runtime.py",
        "test_recommendation_transition.py",
    ):
        if required not in ci:
            _fail("recommendation_runtime_ci_invalid")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (19, 24):
        _fail("recommendation_runtime_schema_boundary_changed")
    if _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(24) != EXPECTED_ACL:
        _fail("recommendation_runtime_schema_boundary_changed")
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        _fail("recommendation_runtime_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        _fail("recommendation_runtime_plan_changed")
    if not isinstance(plan, dict) or set(plan) != {
        "schema_id",
        "schema_version",
        "stage",
        "target_release",
        "feature_issue",
        "gate_issue",
        "base_main_commit",
        "predecessor",
        "transitions",
        "postgresql_acl",
        "runtime",
        "storage",
        "exclusions",
        "source_sha256",
        "gates",
        "nonclaims",
    }:
        _fail("recommendation_runtime_plan_invalid")
    if (
        plan["schema_id"] != "hormuz.recommendation-runtime-plan"
        or plan["schema_version"] != 1
        or plan["stage"] != "provider_free_recommendation_candidate"
        or plan["target_release"] != "1.3.0"
        or plan["feature_issue"] != 224
        or plan["gate_issue"] != 214
        or plan["base_main_commit"] != BASE_MAIN_COMMIT
        or plan["predecessor"] != {
            "path": PREDECESSOR_PATH,
            "file_sha256": PREDECESSOR_FILE_SHA256,
            "canonical_sha256": PREDECESSOR_CANONICAL_SHA256,
        }
        or plan["transitions"] != {
            "sqlite": {"from": 18, "to": 19},
            "postgresql": {"from": 23, "to": 24},
        }
        or plan["postgresql_acl"] != {
            "entry_count": EXPECTED_ACL[0],
            "sha256": EXPECTED_ACL[1],
            "mode": "fixed_literal_two_clean_managed_role_measurements",
        }
    ):
        _fail("recommendation_runtime_plan_invalid")
    if plan["runtime"] != {
        "public_routes": list(ROUTES),
        "public_generation_route": False,
        "internal_generation": "typed_provider_free_operation",
        "required_role": "portfolio_admin",
        "authorization": "configured_role_before_parse_or_storage",
        "recommendation_schema_id": "hormuz.policy-recommendation",
        "recommendation_schema_version": 1,
        "decision_schema_id": "hormuz.policy-recommendation-decision-request",
        "decision_schema_version": 1,
        "page_schema_id": "hormuz.policy-recommendation-page",
        "page_schema_version": 1,
        "change_types": list(CHANGE_TYPES),
        "public_states": list(PUBLIC_STATES),
        "lifecycle_events": list(LIFECYCLE_EVENTS),
        "pre_apply_evidence": list(PRE_APPLY_EVIDENCE),
        "public_pre_apply_evidence": True,
        "request_preview_binding": (
            "actor_request_time_period_and_usage_snapshot_digest"
        ),
        "frozen_decision_fixture": DECISION_FIXTURE_PATH,
        "suppression_coverage_fields": list(SUPPRESSION_COVERAGE_FIELDS),
        "suppression_guardrails": list(SUPPRESSION_GUARDRAILS),
        "sample_eligibility_required": True,
        "automatic_application": False,
        "separate_activation_required": True,
        "applied_event_public_state": "accepted",
        "weak_evidence_result": "suppressed_no_recommendation",
        "expired_candidate_budget_result": "suppressed_no_recommendation",
        "policy_conflict_scope": (
            "all_policy_change_types_same_scope_version_and_baseline"
        ),
        "cursor_expiry": "frozen_as_of_materialized_once_on_every_page",
        "managed_policy_race_control": (
            "shared_policy_control_tenant_lock_and_transactional_reread"
        ),
        "application_drift_recheck": (
            "scorecard_and_non_target_policy_budget_bindings"
        ),
        "application_evidence": (
            "server_derived_authoritative_activation_ledger"
        ),
        "local_policy_applied_event": False,
        "database_statement_timeout_ms": 5000,
        "maximum_response_bytes": RESPONSE_BYTES,
        "maximum_evaluation_bytes": MAX_EVALUATION_BYTES,
        "maximum_conflict_candidates": _MAX_CONFLICTS,
        "cursor_ttl_seconds": int(_CURSOR_TTL.total_seconds()),
        "provider_egress": False,
    }:
        _fail("recommendation_runtime_plan_invalid")
    if plan["storage"] != {
        "tables": list(TABLES),
        "append_only": True,
        "tenant_keyed": True,
        "audited_reads": True,
        "immutable_expiring_snapshots": True,
        "drift_sensitive_decisions": True,
        "policy_activation_event_access": (
            "tenant_scoped_security_definer_receipt"
        ),
        "raw_provider_payload_storage": False,
        "work_content_storage": False,
        "person_scoring_storage": False,
        "credential_storage": False,
        "rollback_requires_pre_migration_backup": True,
    } or plan["exclusions"] != list(EXCLUSIONS):
        _fail("recommendation_runtime_plan_invalid")
    if plan["gates"] != EXPECTED_GATES:
        _fail("recommendation_runtime_gate_overclaim")
    if (
        set(plan["nonclaims"]) != EXPECTED_NONCLAIMS
        or len(plan["nonclaims"]) != len(EXPECTED_NONCLAIMS)
    ):
        _fail("recommendation_runtime_plan_invalid")
    sources = plan["source_sha256"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_PATHS):
        _fail("recommendation_runtime_plan_invalid")
    for relative, expected in sources.items():
        if (
            not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            _fail("recommendation_runtime_plan_invalid")
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("recommendation_runtime_source_kit_incomplete")
        if actual != expected:
            _fail("recommendation_runtime_source_changed")
    _validate_predecessor(root)
    _validate_migration(root, plan)
    _validate_wire(root)
    _validate_runtime_contract()
    _validate_decision_fixture(root)
    _validate_inventory(root)
    _validate_distribution_and_ci(root)
    return {
        "status": "recommendation_runtime_candidate_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
        "postgresql_acl": list(EXPECTED_ACL),
        "table_count": len(TABLES),
        "route_count": len(ROUTES),
        "required_role": "portfolio_admin",
        "recommendations_implemented": True,
        "automatic_application": False,
        "live_external_data_authorized": False,
        "released": False,
        "gates": plan["gates"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.repo_root), sort_keys=True))
        return 0
    except RecommendationRuntimePlanError as error:
        print(error.code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
