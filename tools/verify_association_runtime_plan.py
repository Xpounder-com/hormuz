#!/usr/bin/env python3
"""Verify the fixed provider-free run-to-outcome association runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
)


PLAN_PATH = "docs/association-runtime-plan-v1.json"
PLAN_SHA256 = "40b1dbd1cdfc1d3a90de3cc404cf3a56124cfe5d821bd5a2d499e83987502d67"
PREDECESSOR_PATH = "docs/association-transition-plan-v1.json"
PREDECESSOR_FILE_SHA256 = (
    "261ccd15e629f7beb58e76a12ad2ed206ed6b8222414e6409beb40e9af6c79f3"
)
PREDECESSOR_CANONICAL_SHA256 = (
    "36dc6f8b2b0b355e3a940b6e791732fab285ea515caf805d502bbd130c59f4d5"
)
BASE_MAIN_COMMIT = "dea44f07b41973c6f6e4ac9b7c71016923b10993"
EXPECTED_ACL = (
    232,
    "038e670f801c9b1a0d6b96829d8cdfbb89a66eaf8114f69cb1a9909b98cd1859",
)
TABLES = (
    "portfolio_association_audit_events",
    "portfolio_run_work_link_events",
    "portfolio_run_work_link_idempotency",
    "portfolio_run_outcome_association_events",
    "portfolio_run_outcome_association_cursors",
)
AUDIT_SOURCES = (
    "hormuz.run-work-link-event",
    "hormuz.run-outcome-association-event",
)
SCHEMA_IDS = (
    "hormuz.run-work-link-request",
    "hormuz.run-work-link-event",
    "hormuz.run-work-link-page",
    "hormuz.run-outcome-association-evaluation-request",
    "hormuz.run-outcome-association-event",
    "hormuz.run-outcome-association-page",
)
SOURCE_PATHS = (
    ".github/workflows/ci.yml",
    "MANIFEST.in",
    "docs/ASSOCIATION_RUNTIME.md",
    "docs/DURABLE_DATA.md",
    "docs/durable-data-v1.json",
    "hormuz/_association_schema.py",
    "hormuz/_contract_schemas/audit.py",
    "hormuz/_finance_account_binding_schema.py",
    "hormuz/_finance_collection_schema.py",
    "hormuz/_portfolio_sql.py",
    "hormuz/_sqlite_schema.py",
    "hormuz/association_evidence.py",
    "hormuz/association_metrics.py",
    "hormuz/association_repository.py",
    "hormuz/audit_chain.py",
    "hormuz/migrations/postgresql/0021_run_outcome_association.sql",
    "hormuz/portfolio-association-wire-v1.json",
    "hormuz/portfolio_repository.py",
    "hormuz/portfolio_wire.py",
    "hormuz/postgres.py",
    "pyproject.toml",
    "tests/_postgres_fixture.py",
    "tests/fixtures/association/runtime-multisource-v1.json",
    "tests/test_association_metrics.py",
    "tests/test_association_runtime.py",
    "tests/test_association_runtime_plan.py",
    "tests/test_association_transition_preflight.py",
    "tests/test_attribution_schema.py",
    "tests/test_budget_schema.py",
    "tests/test_durable_data_inventory.py",
    "tests/test_finance_account_binding_transition_preflight.py",
    "tests/test_finance_collection_postgres_runtime_plan.py",
    "tests/test_finance_collection_runtime_plan.py",
    "tests/test_linear_reconciliation_plan.py",
    "tests/test_linear_runtime_plan.py",
    "tests/test_linear_transition_plan.py",
    "tests/test_linear_transition_preflight.py",
    "tests/test_outcome_schema.py",
    "tests/test_postgres_association_runtime.py",
    "tests/test_postgres_budget.py",
    "tests/test_postgres_finance.py",
    "tests/test_postgres_finance_collection_runtime_transition.py",
    "tests/test_postgres_linear_connector_runtime.py",
    "tests/test_postgres_linear_snapshot_runtime.py",
    "tests/test_postgres_migration_rls.py",
    "tests/test_postgres_test_boundaries.py",
    "tests/test_sqlite_budget_transition.py",
    "tests/test_sqlite_attribution_transition.py",
    "tests/test_sqlite_finance.py",
    "tests/test_sqlite_finance_collection_transition.py",
    "tests/test_sqlite_finance_native_attempt_transition.py",
    "tests/test_sqlite_finance_transition.py",
    "tests/test_sqlite_outcome_transition.py",
    "tests/test_sqlite_registry_transition.py",
    "tests/test_store.py",
    "tools/verify_association_transition_plan.py",
    "tools/verify_core_wheel.py",
    "tools/verify_durable_data_inventory.py",
    "tools/verify_finance_collection_postgres_runtime.py",
    "tools/verify_finance_collection_runtime.py",
    "tools/verify_finance_native_attempt_transition_plan.py",
    "tools/verify_linear_reconciliation_plan.py",
    "tools/verify_linear_runtime_plan.py",
    "tools/verify_linear_transition_plan.py",
)
REQUIRED_FILES = (
    PLAN_PATH,
    PREDECESSOR_PATH,
    "docs/ASSOCIATION_LINKAGE_PREFLIGHT.md",
    "docs/ASSOCIATION_TRANSITION.md",
    "docs/portfolio-extension-contract-v1.json",
    "docs/portfolio-intelligence-wire-v1.json",
    "docs/association-successor-schema-proposal.sqlite.sql",
    "docs/association-successor-schema-acl-proposal.sql",
    "hormuz/portfolio-attribution-wire-v1.json",
    "hormuz/portfolio-outcome-wire-v1.json",
    "tools/verify_association_runtime_plan.py",
    *SOURCE_PATHS,
)
EXPECTED_GATES = {
    "transition_checkpoint_accepted": True,
    "exact_storage_wire_and_audit_contract_frozen": True,
    "explicit_link_runtime_proven": True,
    "deterministic_association_runtime_proven": True,
    "coverage_and_cost_vectors_proven": True,
    "metadata_content_scans_proven": True,
    "sqlite_runtime_tests_implemented": True,
    "postgresql_runtime_tests_implemented": True,
    "provider_free_source_verified": True,
    "provider_free_wheel_verified": True,
    "exact_main_ci_verified": False,
    "live_connectors_authorized": False,
    "live_multi_source_evidence_verified": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_NONCLAIMS = {
    "authorized_live_GitHub_or_Linear_workspace",
    "complete_live_connector_history",
    "causal_model_to_work_outcome_attribution",
    "scorecard_or_recommendation_API",
    "realized_savings_or_business_outcome",
    "exact_main_CI",
    "final_candidate_acceptance",
    "v1.3.0_release",
}


class AssociationRuntimePlanError(ValueError):
    """A fixed, content-free runtime-plan refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise AssociationRuntimePlanError(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("association_runtime_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 1024 * 1024:
            _fail("association_runtime_plan_invalid")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("association_runtime_plan_invalid"),
        )
    except AssociationRuntimePlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("association_runtime_plan_invalid")


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
        _fail("association_runtime_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def _validate_predecessor(root: Path) -> None:
    try:
        payload = (root / PREDECESSOR_PATH).read_bytes()
    except OSError:
        _fail("association_runtime_source_kit_incomplete")
    predecessor = _read_json(root / PREDECESSOR_PATH)
    if (
        hashlib.sha256(payload).hexdigest() != PREDECESSOR_FILE_SHA256
        or canonical_digest(predecessor) != PREDECESSOR_CANONICAL_SHA256
    ):
        _fail("association_runtime_predecessor_changed")
    frozen = predecessor.get("frozen_file_sha256")
    if not isinstance(frozen, dict) or not frozen:
        _fail("association_runtime_predecessor_changed")
    for relative, expected in frozen.items():
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("association_runtime_source_kit_incomplete")
        if actual != expected:
            _fail("association_runtime_predecessor_changed")


def _validate_migration(root: Path, plan: dict) -> None:
    relative = "hormuz/migrations/postgresql/0021_run_outcome_association.sql"
    try:
        migration = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("association_runtime_source_kit_incomplete")
    for table in TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("association_runtime_migration_invalid")
        required = (
            f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
        )
        if any(item not in migration for item in required):
            _fail("association_runtime_migration_invalid")
    required = (
        "CREATE OR REPLACE FUNCTION {schema}.custody_audit_chain_source_event_json(",
        "CREATE OR REPLACE FUNCTION {schema}.enforce_custody_audit_chain_entry_insert()",
        "SECURITY DEFINER",
        "SET search_path = pg_catalog",
        "source_conflict",
        "portfolio_run_work_link_root",
        "portfolio_run_work_link_lineage",
        "portfolio_run_outcome_association_root",
        "portfolio_run_outcome_association_lineage",
    )
    forbidden = ("GRANT UPDATE", "GRANT DELETE", "GRANT TRUNCATE", "WITH GRANT OPTION")
    if any(item not in migration for item in required) or any(
        item in migration.upper() for item in forbidden
    ):
        _fail("association_runtime_migration_invalid")
    for source in AUDIT_SOURCES:
        if migration.count(source) < 3:
            _fail("association_runtime_migration_invalid")
    expected = plan.get("source_sha256", {}).get(relative)
    if expected != hashlib.sha256(migration.encode("utf-8")).hexdigest():
        _fail("association_runtime_source_changed")


def _validate_wire_and_fixture(root: Path) -> None:
    wire = _read_json(root / "hormuz/portfolio-association-wire-v1.json")
    if (
        not isinstance(wire, dict)
        or wire.get("schema_ids") != list(SCHEMA_IDS)
        or not isinstance(wire.get("$defs"), dict)
        or not set((*SCHEMA_IDS, "hormuz.association-query")).issubset(wire["$defs"])
    ):
        _fail("association_runtime_wire_invalid")
    fixture = _read_json(root / "tests/fixtures/association/runtime-multisource-v1.json")
    if (
        not isinstance(fixture, dict)
        or set(fixture) != {"schema_id", "schema_version", "input", "expected"}
        or fixture["schema_id"] != "hormuz.association-metric-reference-fixture"
        or fixture["schema_version"] != 1
    ):
        _fail("association_runtime_fixture_invalid")
    selected = fixture["input"]
    try:
        from hormuz.association_metrics import (
            AssociationMetricError,
            build_association_metric_vector,
        )
    except ImportError:
        _fail("association_runtime_fixture_invalid")
    try:
        observed = build_association_metric_vector(
            context=selected["context"],
            attempts=tuple(selected["attempts"]),
            costs=tuple(selected["costs"]),
            outcomes=tuple(selected["outcomes"]),
            associations=tuple(selected["associations"]),
            deliveries=tuple(selected["deliveries"]),
            complete_connector_ids=tuple(selected["complete_connector_ids"]),
        )
    except (AssociationMetricError, KeyError, TypeError):
        _fail("association_runtime_fixture_invalid")
    if observed != fixture["expected"]:
        _fail("association_runtime_fixture_invalid")
    serialized = json.dumps(observed, sort_keys=True, separators=(",", ":"))
    for forbidden in (
        "title", "description", "body", "comment", "prompt", "actor_id",
        "actor_name", "employee", "credential", "raw_payload",
    ):
        if f'"{forbidden}"' in serialized:
            _fail("association_runtime_content_boundary_invalid")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        _fail("association_runtime_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        _fail("association_runtime_plan_changed")
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
        "accounting",
        "source_sha256",
        "gates",
        "nonclaims",
    }:
        _fail("association_runtime_plan_invalid")
    if (
        plan["schema_id"] != "hormuz.association-runtime-plan"
        or plan["schema_version"] != 1
        or plan["stage"] != "provider_free_runtime_candidate"
        or plan["target_release"] != "1.3.0"
        or plan["feature_issue"] != 221
        or plan["gate_issue"] != 214
        or plan["base_main_commit"] != BASE_MAIN_COMMIT
        or plan["predecessor"] != {
            "path": PREDECESSOR_PATH,
            "file_sha256": PREDECESSOR_FILE_SHA256,
            "canonical_sha256": PREDECESSOR_CANONICAL_SHA256,
        }
        or plan["transitions"] != {
            "sqlite": {"from": 15, "to": 16},
            "postgresql": {"from": 20, "to": 21},
        }
        or plan["postgresql_acl"] != {
            "entry_count": EXPECTED_ACL[0],
            "sha256": EXPECTED_ACL[1],
            "mode": "fixed_literal_two_clean_managed_role_measurements",
        }
    ):
        _fail("association_runtime_plan_invalid")
    if plan["runtime"] != {
        "routes": [
            "POST /v1/admin/portfolio/run-work-links",
            "GET /v1/admin/portfolio/run-work-links",
            "POST /v1/admin/portfolio/associations",
            "GET /v1/admin/portfolio/associations",
        ],
        "authorization": "typed_portfolio_admin_and_configured_connector_before_storage",
        "link_evidence": "exact_attempt_attribution_source_revision_scope_and_historical_binding",
        "rule_id": "explicit-run-work-link-v1",
        "rule_version": 1,
        "association_states": ["unmatched", "ambiguous", "associated", "excluded"],
        "source_authority": "connector_ordered_current_revision_equal_or_incomparable_conflict_is_ambiguous",
        "correction_model": "append_only_compare_and_set_successor",
        "idempotency": "tenant_actor_key_versioned_domain_separated_HMAC",
        "cursor": "opaque_authority_and_filter_bound_snapshot_cursor_expires_after_one_hour",
        "internal_metric_reference": True,
        "public_scorecard_route": False,
        "causal_claim": False,
    }:
        _fail("association_runtime_plan_invalid")
    if plan["storage"] != {
        "tables": list(TABLES),
        "append_only": True,
        "tenant_keyed": True,
        "audit_chain_sources": list(AUDIT_SOURCES),
        "raw_provider_payload_storage": False,
        "work_content_storage": False,
        "credential_storage": False,
    }:
        _fail("association_runtime_plan_invalid")
    if plan["accounting"] != {
        "attempt_grain": "unique_request_attempt",
        "outcome_grain": "unique_external_work_object_at_authoritative_snapshot",
        "cost_bases": [
            "provider_final",
            "configured_rate_card_estimate",
            "allocated_estimate",
            "provider_aggregate",
            "credit_or_discount",
            "not_available",
        ],
        "provider_aggregate_or_credit_assigned_to_attempt": False,
        "undefined_denominator": "unavailable_never_numeric_zero",
        "history_dependent_metrics_require_declared_complete_connector_history": True,
        "frozen_fixture": "tests/fixtures/association/runtime-multisource-v1.json",
        "fixture_connectors": ["github-one", "linear-one"],
    }:
        _fail("association_runtime_plan_invalid")
    if plan["gates"] != EXPECTED_GATES:
        _fail("association_runtime_gate_overclaim")
    if set(plan["nonclaims"]) != EXPECTED_NONCLAIMS or len(plan["nonclaims"]) != len(
        EXPECTED_NONCLAIMS
    ):
        _fail("association_runtime_plan_invalid")
    sources = plan["source_sha256"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_PATHS):
        _fail("association_runtime_plan_invalid")
    for relative, expected in sources.items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            _fail("association_runtime_plan_invalid")
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("association_runtime_source_kit_incomplete")
        if actual != expected:
            _fail("association_runtime_source_changed")
    if (
        (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (16, 21)
        or _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(21) != EXPECTED_ACL
    ):
        _fail("association_runtime_schema_boundary_changed")
    _validate_predecessor(root)
    _validate_migration(root, plan)
    _validate_wire_and_fixture(root)
    return {
        "status": "association_runtime_candidate_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
        "postgresql_acl": list(EXPECTED_ACL),
        "table_count": len(TABLES),
        "audit_source_count": len(AUDIT_SOURCES),
        "runtime_implemented": True,
        "metric_reference_implemented": True,
        "public_scorecard_route": False,
        "live_connectors_authorized": False,
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
    except AssociationRuntimePlanError as error:
        print(error.code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
