#!/usr/bin/env python3
"""Verify the fixed provider-free evidence-qualified scorecard runtime."""

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
from hormuz.portfolio_wire import PortfolioError, validate
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
)
from hormuz.scorecard_kernel import ScorecardKernelError, build_scorecard_evaluation


PLAN_PATH = "docs/scorecard-runtime-plan-v1.json"
PLAN_SHA256 = "831b0ae21afbaf3b8e8df8165b639ce2e752a3487e4986ac3bf49f8bb19e41dc"
PREDECESSOR_PATH = "docs/association-runtime-plan-v1.json"
PREDECESSOR_FILE_SHA256 = (
    "0d31c8c72777d51489c7ec7547b1727973385b8316ca767937a87e7e61b974e1"
)
PREDECESSOR_CANONICAL_SHA256 = (
    "e35c48b9fd8dbb802c9340af69fa44449af3348d046a69b5a3cdd0baae5d8c6b"
)
BASE_MAIN_COMMIT = "bea79349cc29d9cf83622c47c3f31e82fdc621ae"
EXPECTED_ACL = (
    236,
    "4aef5982da3f81a352f813554de5721580f0ad5f0a93dda529199b545fee5a20",
)
TABLES = (
    "portfolio_scorecard_audit_events",
    "portfolio_model_scorecard_snapshots",
)
COST_BASES = (
    "provider_final",
    "configured_rate_card_estimate",
    "allocated_estimate",
    "provider_aggregate",
    "credit_or_discount",
    "not_available",
)
ITEM_COST_BASES = (
    "provider_final",
    "configured_rate_card_estimate",
    "allocated_estimate",
)
SOURCE_PATHS = (
    ".github/workflows/ci.yml",
    "MANIFEST.in",
    "docs/DURABLE_DATA.md",
    "docs/SCORECARD_RUNTIME.md",
    "docs/durable-data-v1.json",
    "hormuz/_portfolio_sql.py",
    "hormuz/_scorecard_schema.py",
    "hormuz/_sqlite_schema.py",
    "hormuz/migrations/postgresql/0022_model_scorecards.sql",
    "hormuz/portfolio-intelligence-wire-v1.json",
    "hormuz/portfolio_repository.py",
    "hormuz/portfolio_wire.py",
    "hormuz/postgres.py",
    "hormuz/scorecard_evidence_reference.py",
    "hormuz/scorecard_kernel.py",
    "hormuz/scorecard_repository.py",
    "pyproject.toml",
    "tests/_postgres_fixture.py",
    "tests/fixtures/scorecard/runtime-v1.json",
    "tests/test_association_runtime_plan.py",
    "tests/test_durable_data_inventory.py",
    "tests/test_postgres_scorecard_runtime.py",
    "tests/test_scorecard_evidence_reference.py",
    "tests/test_scorecard_kernel.py",
    "tests/test_scorecard_runtime.py",
    "tests/test_scorecard_runtime_plan.py",
    "tools/verify_association_runtime_plan.py",
    "tools/verify_core_wheel.py",
    "tools/verify_durable_data_inventory.py",
)
REQUIRED_FILES = (
    PLAN_PATH,
    PREDECESSOR_PATH,
    "docs/portfolio-intelligence-contract-v1.json",
    "docs/portfolio-intelligence-wire-v1.json",
    "tools/verify_scorecard_runtime_plan.py",
    *SOURCE_PATHS,
)
EXPECTED_GATES = {
    "association_runtime_predecessor_verified": True,
    "frozen_reference_vector_proven": True,
    "metadata_content_boundary_proven": True,
    "sqlite_runtime_tests_implemented": True,
    "postgresql_runtime_tests_implemented": True,
    "provider_free_source_verified": True,
    "provider_free_wheel_verified": True,
    "exact_main_ci_verified": False,
    "live_connectors_authorized": False,
    "role_scoped_decision_views_implemented": False,
    "recommendations_implemented": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_NONCLAIMS = {
    "authorized_live_connector_or_customer_workspace",
    "complete_live_external_history",
    "causal_model_to_outcome_effect",
    "public_scorecard_or_recommendation_route",
    "realized_savings_or_business_outcome",
    "exact_main_CI",
    "final_candidate_acceptance",
    "v1.3.0_release",
}


class ScorecardRuntimePlanError(ValueError):
    """A fixed, content-free scorecard runtime refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise ScorecardRuntimePlanError(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("scorecard_runtime_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 32 * 1024 * 1024:
            _fail("scorecard_runtime_plan_invalid")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("scorecard_runtime_plan_invalid"),
        )
    except ScorecardRuntimePlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("scorecard_runtime_plan_invalid")


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
        _fail("scorecard_runtime_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def _validate_predecessor(root: Path) -> None:
    try:
        payload = (root / PREDECESSOR_PATH).read_bytes()
    except OSError:
        _fail("scorecard_runtime_source_kit_incomplete")
    predecessor = _read_json(root / PREDECESSOR_PATH)
    if (
        hashlib.sha256(payload).hexdigest() != PREDECESSOR_FILE_SHA256
        or canonical_digest(predecessor) != PREDECESSOR_CANONICAL_SHA256
        or predecessor.get("feature_issue") != 221
        or predecessor.get("target_release") != "1.3.0"
        or predecessor.get("runtime", {}).get("public_scorecard_route") is not False
    ):
        _fail("scorecard_runtime_predecessor_changed")


def _validate_migration(root: Path, plan: dict) -> None:
    relative = "hormuz/migrations/postgresql/0022_model_scorecards.sql"
    try:
        migration = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("scorecard_runtime_source_kit_incomplete")
    for table in TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("scorecard_runtime_migration_invalid")
        required = (
            f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
        )
        if any(item not in migration for item in required):
            _fail("scorecard_runtime_migration_invalid")
    forbidden = ("GRANT UPDATE", "GRANT DELETE", "GRANT TRUNCATE", "WITH GRANT OPTION")
    if (
        migration.count("portfolio_reject_mutation()") != len(TABLES)
        or any(item in migration.upper() for item in forbidden)
        or plan.get("source_sha256", {}).get(relative)
        != hashlib.sha256(migration.encode("utf-8")).hexdigest()
    ):
        _fail("scorecard_runtime_migration_invalid")


def _forbidden_key(value: object) -> bool:
    forbidden = {
        "prompt", "response", "title", "description", "comment", "body", "path",
        "employee_id", "employee_name", "actor_id", "actor_name", "credential",
        "raw_payload", "individual_quality_score", "person_rank",
    }
    if isinstance(value, dict):
        return any(key in forbidden or _forbidden_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_forbidden_key(item) for item in value)
    return False


def _validate_wire_fixture_and_inventory(root: Path) -> None:
    wire = _read_json(root / "hormuz/portfolio-intelligence-wire-v1.json")
    definitions = wire.get("$defs") if isinstance(wire, dict) else None
    if (
        not isinstance(definitions, dict)
        or "hormuz.model-scorecard" not in definitions
        or "hormuz.model-scorecard-page" not in definitions
        or "hormuz.model-scorecard" not in wire.get("x-hormuz-schema-ids", [])
    ):
        _fail("scorecard_runtime_wire_invalid")
    fixture = _read_json(root / "tests/fixtures/scorecard/runtime-v1.json")
    if (
        not isinstance(fixture, dict)
        or set(fixture) != {"schema_id", "schema_version", "input", "expected"}
        or fixture["schema_id"] != "hormuz.scorecard-runtime-fixture"
        or fixture["schema_version"] != 1
    ):
        _fail("scorecard_runtime_fixture_invalid")
    try:
        observed = build_scorecard_evaluation(fixture["input"])
        validate(observed["scorecard"], "hormuz.model-scorecard")
    except (KeyError, TypeError, PortfolioError, ScorecardKernelError):
        _fail("scorecard_runtime_fixture_invalid")
    if (
        observed != fixture["expected"]
        or observed["scorecard"].get("evidence_level") != "associated"
        or observed["scorecard"].get("controlled_design") is not None
        or _forbidden_key(fixture)
    ):
        _fail("scorecard_runtime_fixture_invalid")
    try:
        from tools.verify_durable_data_inventory import validate_durable_data_inventory

        inventory = validate_durable_data_inventory(root)
    except (ImportError, ValueError, OSError):
        _fail("scorecard_runtime_inventory_invalid")
    if (
        inventory.get("database_class_count") != 38
        or inventory.get("sqlite_table_count") != 78
        or inventory.get("postgresql_table_count") != 86
    ):
        _fail("scorecard_runtime_inventory_invalid")


def _validate_distribution_and_ci(root: Path) -> None:
    try:
        from tools import verify_core_wheel as packaging
    except ImportError:
        _fail("scorecard_runtime_distribution_invalid")
    required_wheel = {
        "hormuz/_scorecard_schema.py",
        "hormuz/scorecard_evidence_reference.py",
        "hormuz/scorecard_kernel.py",
        "hormuz/scorecard_repository.py",
        "hormuz/portfolio-intelligence-wire-v1.json",
        "hormuz/migrations/postgresql/0022_model_scorecards.sql",
    }
    required_sdist = required_wheel | {
        "docs/SCORECARD_RUNTIME.md",
        PLAN_PATH,
        "tools/verify_scorecard_runtime_plan.py",
        "tests/fixtures/scorecard/runtime-v1.json",
        "tests/test_scorecard_kernel.py",
        "tests/test_scorecard_runtime.py",
        "tests/test_scorecard_runtime_plan.py",
        "tests/test_postgres_scorecard_runtime.py",
    }
    if (
        not required_wheel.issubset(packaging.REQUIRED_SCORECARD_RUNTIME_WHEEL_PATHS)
        or not required_sdist.issubset(packaging.REQUIRED_SCORECARD_RUNTIME_SDIST_PATHS)
    ):
        _fail("scorecard_runtime_distribution_invalid")
    try:
        ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("scorecard_runtime_source_kit_incomplete")
    for required in (
        "tools/verify_scorecard_runtime_plan.py",
        "tests.test_scorecard_kernel",
        "tests.test_scorecard_runtime",
        "tests.test_scorecard_runtime_plan",
        "test_postgres_scorecard_runtime.py",
    ):
        if required not in ci:
            _fail("scorecard_runtime_ci_invalid")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        _fail("scorecard_runtime_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        _fail("scorecard_runtime_plan_changed")
    if not isinstance(plan, dict) or set(plan) != {
        "schema_id", "schema_version", "stage", "target_release", "feature_issue",
        "gate_issue", "base_main_commit", "predecessor", "transitions",
        "postgresql_acl", "runtime", "storage", "kpis", "source_sha256",
        "gates", "nonclaims",
    }:
        _fail("scorecard_runtime_plan_invalid")
    if (
        plan["schema_id"] != "hormuz.scorecard-runtime-plan"
        or plan["schema_version"] != 1
        or plan["stage"] != "provider_free_scorecard_candidate"
        or plan["target_release"] != "1.3.0"
        or plan["feature_issue"] != 222
        or plan["gate_issue"] != 214
        or plan["base_main_commit"] != BASE_MAIN_COMMIT
        or plan["predecessor"] != {
            "path": PREDECESSOR_PATH,
            "file_sha256": PREDECESSOR_FILE_SHA256,
            "canonical_sha256": PREDECESSOR_CANONICAL_SHA256,
        }
        or plan["transitions"] != {
            "sqlite": {"from": 16, "to": 17},
            "postgresql": {"from": 21, "to": 22},
        }
        or plan["postgresql_acl"] != {
            "entry_count": EXPECTED_ACL[0],
            "sha256": EXPECTED_ACL[1],
            "mode": "fixed_literal_two_clean_managed_role_measurements",
        }
    ):
        _fail("scorecard_runtime_plan_invalid")
    if plan["runtime"] != {
        "input_schema_id": "hormuz.scorecard-evaluation-input",
        "output_schema_id": "hormuz.model-scorecard",
        "authorization": "configured_portfolio_admin_before_parse_or_storage",
        "scope": "one_active_use_case_version",
        "evidence_level": "associated",
        "controlled_design": None,
        "public_routes": [],
        "public_scorecard_route": False,
    }:
        _fail("scorecard_runtime_plan_invalid")
    if plan["storage"] != {
        "tables": list(TABLES),
        "append_only": True,
        "tenant_keyed": True,
        "canonical_input_and_evaluation": True,
        "exact_replay_without_duplicate_audit": True,
        "strict_version_lineage": True,
        "raw_provider_payload_storage": False,
        "work_content_storage": False,
        "person_scoring_storage": False,
        "credential_storage": False,
    }:
        _fail("scorecard_runtime_plan_invalid")
    if plan["kpis"] != {
        "primary_readiness": "use_case_attributed_spend_coverage",
        "primary_economics": "quality_qualified_cost_per_accepted_work_item",
        "primary_decision": "optimization_lift_vs_declared_baseline",
        "cost_bases": list(COST_BASES),
        "item_cost_bases": list(ITEM_COST_BASES),
        "requested_routed_actual_models_distinct": True,
        "work_item_cluster_is_independent_sample": True,
        "mandatory_guardrails": True,
        "stratum_failure_cannot_be_pooled_away": True,
        "uncertainty_methods": [
            "exact_source_ratio",
            "work_item_wilson_95",
            "work_item_cluster_jackknife_95",
            "work_item_delete_one_range",
            "independent_cluster_interval_propagation_95",
        ],
        "pareto_axes": ["cost", "quality", "latency", "reliability"],
        "composite_rank": False,
        "causal_claim": False,
        "frozen_fixture": "tests/fixtures/scorecard/runtime-v1.json",
    }:
        _fail("scorecard_runtime_plan_invalid")
    if plan["gates"] != EXPECTED_GATES:
        _fail("scorecard_runtime_gate_overclaim")
    if set(plan["nonclaims"]) != EXPECTED_NONCLAIMS or len(plan["nonclaims"]) != len(
        EXPECTED_NONCLAIMS
    ):
        _fail("scorecard_runtime_plan_invalid")
    sources = plan["source_sha256"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_PATHS):
        _fail("scorecard_runtime_plan_invalid")
    for relative, expected in sources.items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            _fail("scorecard_runtime_plan_invalid")
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("scorecard_runtime_source_kit_incomplete")
        if actual != expected:
            _fail("scorecard_runtime_source_changed")
    if (
        (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (17, 22)
        or _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(22) != EXPECTED_ACL
    ):
        _fail("scorecard_runtime_schema_boundary_changed")
    _validate_predecessor(root)
    _validate_migration(root, plan)
    _validate_wire_fixture_and_inventory(root)
    _validate_distribution_and_ci(root)
    return {
        "status": "scorecard_runtime_candidate_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
        "postgresql_acl": list(EXPECTED_ACL),
        "table_count": len(TABLES),
        "kernel_implemented": True,
        "runtime_implemented": True,
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
    except ScorecardRuntimePlanError as error:
        print(error.code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
