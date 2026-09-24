#!/usr/bin/env python3
"""Verify the fixed provider-free role-scoped portfolio view runtime."""

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
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_wire import RESPONSE_BYTES, ROLE_VIEW_ROUTES, PortfolioError
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
)
from hormuz.role_view_repository import (
    _CURSOR_SECONDS,
    _EXCLUSIONS,
    _MAX_CANDIDATES,
    _MAX_SCOPE_ROWS,
    _OPERATIONS,
    PortfolioRoleViewRepository,
)
from tools import verify_scorecard_runtime_plan as scorecard_verifier


PLAN_PATH = "docs/role-view-runtime-plan-v1.json"
PLAN_SHA256 = "29bbb0e0a371abcfe18e12d4f235ab62993f09dcd4fa8e91e557a15d2321fb17"
PREDECESSOR_PATH = "docs/scorecard-runtime-plan-v1.json"
PREDECESSOR_FILE_SHA256 = (
    "b5ad84161eb9d5f2ac109cc25856a041fca5f905bd0f1d415d112b055e7fafb2"
)
PREDECESSOR_CANONICAL_SHA256 = (
    "72e7ad360f825cc5125488209da3843119eff856bf0fcf2f466e0be255c9af46"
)
BASE_MAIN_COMMIT = "108d952b4426be74f11306d30a4955807e8fe766"
EXPECTED_ACL = (
    240,
    "84fc2219343738b183de58bd640a761ff010eb3163429e58d1b70e23ab445c95",
)
TABLES = (
    "portfolio_role_view_audit_events",
    "portfolio_role_view_cursors",
)
ROUTES = (
    "GET /v1/admin/portfolio/views/finance/budgets",
    "GET /v1/admin/portfolio/views/platform/scorecards",
    "GET /v1/admin/portfolio/views/team/budgets",
    "GET /v1/admin/portfolio/views/team/scorecards",
)
BUDGET_ROUTES = (ROUTES[0], ROUTES[2])
BUDGET_ROUTE_QUERY_FIELDS = {
    route: ["cursor", "end_at", "limit", "start_at", "work_scope_id"]
    for route in BUDGET_ROUTES
}
BUDGET_TRANSPORT = {
    "response_maximum_bytes": RESPONSE_BYTES,
    "runtime_enabled": True,
    "new_http_routes": list(BUDGET_ROUTES),
    "delivery": "role_scoped_http_and_portfolio_view_cli",
}
ROLE_ITEM_PAYLOAD_CONDITIONS = [
    {
        "if": {
            "properties": {"resource": {"const": "budget"}},
            "required": ["resource"],
        },
        "then": {
            "properties": {
                "payload": {
                    "$ref": "work-budget-reports-wire-v2.json#/$defs/hormuz.work-budget-report"
                }
            },
            "required": ["payload"],
        },
    },
    {
        "if": {
            "properties": {"resource": {"const": "scorecard"}},
            "required": ["resource"],
        },
        "then": {
            "properties": {
                "payload": {
                    "$ref": "portfolio-intelligence-wire-v1.json#/$defs/hormuz.model-scorecard"
                }
            },
            "required": ["payload"],
        },
    },
]
ROLE_BINDINGS = {
    "finance_budgets": "finance_viewer",
    "platform_scorecards": "platform_viewer",
    "team_budgets": "team_lead",
    "team_scorecards": "team_lead",
}
SOURCE_PATHS = (
    ".github/workflows/ci.yml",
    "MANIFEST.in",
    "docs/DURABLE_DATA.md",
    "docs/PORTFOLIO_ROLE_VIEWS.md",
    "docs/durable-data-v1.json",
    "docs/portfolio-role-views-wire-v1.json",
    "docs/work-budget-reports-wire-v2.json",
    "hormuz/_portfolio_sql.py",
    "hormuz/_role_view_schema.py",
    "hormuz/_sqlite_schema.py",
    "hormuz/budget_repository.py",
    "hormuz/commands/finance.py",
    "hormuz/commands/portfolio.py",
    "hormuz/migrations/postgresql/0023_portfolio_role_views.sql",
    "hormuz/portfolio-role-views-wire-v1.json",
    "hormuz/portfolio_config.py",
    "hormuz/portfolio_repository.py",
    "hormuz/portfolio_service.py",
    "hormuz/portfolio_wire.py",
    "hormuz/postgres.py",
    "hormuz/role_view_repository.py",
    "hormuz/work-budget-reports-wire-v2.json",
    "pyproject.toml",
    "tests/_postgres_fixture.py",
    "tests/_role_view_fixture.py",
    "tests/fixtures/portfolio_role_views/wire-v1-examples.json",
    "tests/test_association_runtime_plan.py",
    "tests/test_association_transition_plan.py",
    "tests/test_association_transition_preflight.py",
    "tests/test_attribution_schema.py",
    "tests/test_budget_schema.py",
    "tests/test_cli_runtime_ownership.py",
    "tests/test_durable_data_inventory.py",
    "tests/test_finance_account_binding_preflight.py",
    "tests/test_finance_account_binding_transition_preflight.py",
    "tests/test_finance_collection_postgres_runtime_plan.py",
    "tests/test_finance_collection_runtime_plan.py",
    "tests/test_linear_reconciliation_plan.py",
    "tests/test_linear_runtime_plan.py",
    "tests/test_linear_transition_preflight.py",
    "tests/test_outcome_schema.py",
    "tests/test_postgres_budget.py",
    "tests/test_postgres_finance.py",
    "tests/test_postgres_finance_collection_runtime_transition.py",
    "tests/test_postgres_linear_connector_runtime.py",
    "tests/test_postgres_linear_snapshot_runtime.py",
    "tests/test_postgres_role_view_runtime.py",
    "tests/test_postgres_scorecard_runtime.py",
    "tests/test_portfolio_display_examples.py",
    "tests/test_role_view_api_cli.py",
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
    "tools/render_portfolio_display_examples.py",
    "tools/verify_association_runtime_plan.py",
    "tools/verify_association_transition_plan.py",
    "tools/verify_budget_transition_plan.py",
    "tools/verify_core_wheel.py",
    "tools/verify_durable_data_inventory.py",
    "tools/verify_finance_account_binding_preflight.py",
    "tools/verify_finance_collection_postgres_runtime.py",
    "tools/verify_finance_collection_runtime.py",
    "tools/verify_finance_native_attempt_transition_plan.py",
    "tools/verify_linear_reconciliation_plan.py",
    "tools/verify_linear_runtime_plan.py",
    "tools/verify_linear_transition_plan.py",
    "tools/verify_scorecard_runtime_plan.py",
)
REQUIRED_FILES = tuple(dict.fromkeys((
    PLAN_PATH,
    PREDECESSOR_PATH,
    "docs/portfolio-role-views-wire-v1.json",
    "docs/work-budget-reports-wire-v2.json",
    "hormuz/portfolio-role-views-wire-v1.json",
    "hormuz/work-budget-reports-wire-v2.json",
    "tools/verify_role_view_runtime.py",
    *SOURCE_PATHS,
    *scorecard_verifier.REQUIRED_FILES,
)))
EXPECTED_GATES = {
    "scorecard_runtime_predecessor_verified": True,
    "finance_view_implemented": True,
    "platform_view_implemented": True,
    "team_views_implemented": True,
    "stable_api_implemented": True,
    "stable_cli_implemented": True,
    "sqlite_runtime_tests_implemented": True,
    "postgresql_runtime_tests_implemented": True,
    "provider_free_source_verified": True,
    "provider_free_wheel_verified": True,
    "exact_main_ci_verified": False,
    "live_connectors_authorized": False,
    "recommendations_implemented": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_NONCLAIMS = {
    "authorized_live_connector_or_customer_workspace",
    "complete_live_external_history",
    "individual_productivity_or_employee_ranking",
    "causal_savings_or_business_outcome",
    "recommendation_runtime",
    "exact_main_CI",
    "final_candidate_acceptance",
    "v1.3.0_release",
}
FORBIDDEN_INSTANCE_FIELDS = {
    "prompt",
    "response",
    "title",
    "description",
    "body",
    "comment",
    "code",
    "filename",
    "path",
    "credential",
    "raw_payload",
    "employee_id",
    "employee_name",
    "individual_quality_score",
    "person_rank",
}


class RoleViewRuntimePlanError(ValueError):
    """A fixed, content-free role-view runtime refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RoleViewRuntimePlanError(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("role_view_runtime_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 32 * 1024 * 1024:
            _fail("role_view_runtime_plan_invalid")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("role_view_runtime_plan_invalid"),
        )
    except RoleViewRuntimePlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("role_view_runtime_plan_invalid")


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
        _fail("role_view_runtime_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def _contains_forbidden_instance_field(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in FORBIDDEN_INSTANCE_FIELDS
            or _contains_forbidden_instance_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_instance_field(item) for item in value)
    return False


def _declared_property_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            names.update(str(key) for key in properties)
        for item in value.values():
            names.update(_declared_property_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(_declared_property_names(item))
    return names


def _validate_predecessor(root: Path) -> None:
    try:
        payload = (root / PREDECESSOR_PATH).read_bytes()
    except OSError:
        _fail("role_view_runtime_source_kit_incomplete")
    predecessor = _read_json(root / PREDECESSOR_PATH)
    if (
        hashlib.sha256(payload).hexdigest() != PREDECESSOR_FILE_SHA256
        or canonical_digest(predecessor) != PREDECESSOR_CANONICAL_SHA256
        or predecessor.get("feature_issue") != 222
        or predecessor.get("target_release") != "1.3.0"
        or predecessor.get("gates", {}).get(
            "role_scoped_decision_views_implemented"
        ) is not False
    ):
        _fail("role_view_runtime_predecessor_changed")
    try:
        scorecard_verifier._validate_successor_predecessor(root)
    except scorecard_verifier.ScorecardRuntimePlanError as error:
        mapping = {
            "scorecard_runtime_source_kit_incomplete": "role_view_runtime_source_kit_incomplete",
            "scorecard_runtime_source_changed": "role_view_runtime_source_changed",
            "scorecard_runtime_predecessor_changed": "role_view_runtime_predecessor_changed",
        }
        _fail(mapping.get(error.code, "role_view_runtime_predecessor_invalid"))


def _validate_migration(root: Path, plan: dict) -> None:
    relative = "hormuz/migrations/postgresql/0023_portfolio_role_views.sql"
    try:
        migration = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("role_view_runtime_source_kit_incomplete")
    for table in TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("role_view_runtime_migration_invalid")
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
            _fail("role_view_runtime_migration_invalid")
    forbidden = ("GRANT UPDATE", "GRANT DELETE", "GRANT TRUNCATE", "WITH GRANT OPTION")
    if (
        migration.count("portfolio_reject_mutation()") != len(TABLES)
        or any(item in migration.upper() for item in forbidden)
        or plan.get("source_sha256", {}).get(relative)
        != hashlib.sha256(migration.encode("utf-8")).hexdigest()
    ):
        _fail("role_view_runtime_migration_invalid")


def _validate_wire_and_fixtures(root: Path) -> None:
    pairs = (
        (
            "docs/portfolio-role-views-wire-v1.json",
            "hormuz/portfolio-role-views-wire-v1.json",
        ),
        (
            "docs/work-budget-reports-wire-v2.json",
            "hormuz/work-budget-reports-wire-v2.json",
        ),
    )
    try:
        if any(
            (root / documentation).read_bytes() != (root / packaged).read_bytes()
            for documentation, packaged in pairs
        ):
            _fail("role_view_runtime_wire_invalid")
    except OSError:
        _fail("role_view_runtime_source_kit_incomplete")
    wire = _read_json(root / pairs[0][0])
    budget = _read_json(root / pairs[1][0])
    definitions = wire.get("$defs") if isinstance(wire, dict) else None
    item = (
        definitions.get("hormuz.portfolio-role-view-item")
        if isinstance(definitions, dict)
        else None
    )
    if (
        wire.get("x-hormuz-schema-ids")
        != ["hormuz.portfolio-role-view-item", "hormuz.portfolio-role-view-page"]
        or not isinstance(definitions, dict)
        or not {
            "hormuz.portfolio-role-view-item",
            "hormuz.portfolio-role-view-page",
            "provenance",
            "freshness",
        }.issubset(definitions)
        or not isinstance(item, dict)
        or item.get("allOf") != ROLE_ITEM_PAYLOAD_CONDITIONS
        or budget.get("x-hormuz-schema-versions", {}).get(
            "hormuz.work-budget-report"
        ) != 2
        or budget.get("x-hormuz-route-query-fields")
        != BUDGET_ROUTE_QUERY_FIELDS
        or budget.get("x-hormuz-transport") != BUDGET_TRANSPORT
        or FORBIDDEN_INSTANCE_FIELDS.intersection(_declared_property_names(wire))
    ):
        _fail("role_view_runtime_wire_invalid")
    fixture = _read_json(
        root / "tests/fixtures/portfolio_role_views/wire-v1-examples.json"
    )
    if (
        not isinstance(fixture, dict)
        or set(fixture) != {"schema_id", "schema_version", "cases"}
        or fixture["schema_id"] != "hormuz.portfolio-role-view-examples"
        or fixture["schema_version"] != 1
        or not isinstance(fixture["cases"], list)
        or len(fixture["cases"]) != len(ROUTES)
        or _contains_forbidden_instance_field(fixture)
    ):
        _fail("role_view_runtime_fixture_invalid")
    observed_routes = []
    try:
        for case in fixture["cases"]:
            if set(case) != {"name", "route", "value"}:
                _fail("role_view_runtime_fixture_invalid")
            observed_routes.append(case["route"])
            value = case["value"]
            PortfolioRoleViewRepository._public(
                value,
                PortfolioPrincipal(
                    value["organization_id"],
                    "fixture-reader",
                    (value["reader_role"],),
                    value["scope"]["id"]
                    if value["reader_role"] == "team_lead"
                    else None,
                ),
            )
    except (KeyError, TypeError, PortfolioError):
        _fail("role_view_runtime_fixture_invalid")
    if tuple(observed_routes) != ROUTES:
        _fail("role_view_runtime_fixture_invalid")


def _validate_inventory(root: Path) -> None:
    try:
        from tools.verify_durable_data_inventory import validate_durable_data_inventory

        inventory = validate_durable_data_inventory(root)
    except (ImportError, ValueError, OSError):
        _fail("role_view_runtime_inventory_invalid")
    if (
        inventory.get("database_class_count") != 39
        or inventory.get("sqlite_table_count") != 80
        or inventory.get("postgresql_table_count") != 88
    ):
        _fail("role_view_runtime_inventory_invalid")


def _validate_distribution_and_ci(root: Path) -> None:
    try:
        from tools import verify_core_wheel as packaging
    except ImportError:
        _fail("role_view_runtime_distribution_invalid")
    required_wheel = {
        "hormuz/_role_view_schema.py",
        "hormuz/role_view_repository.py",
        "hormuz/portfolio-role-views-wire-v1.json",
        "hormuz/work-budget-reports-wire-v2.json",
        "hormuz/migrations/postgresql/0023_portfolio_role_views.sql",
    }
    required_sdist = required_wheel | {
        "docs/PORTFOLIO_ROLE_VIEWS.md",
        "docs/portfolio-role-views-wire-v1.json",
        PLAN_PATH,
        "tools/verify_role_view_runtime.py",
        "tests/_role_view_fixture.py",
        "tests/fixtures/portfolio_role_views/wire-v1-examples.json",
        "tests/test_role_view_api_cli.py",
        "tests/test_role_view_runtime.py",
        "tests/test_role_view_runtime_plan.py",
        "tests/test_role_view_transition.py",
        "tests/test_postgres_role_view_runtime.py",
    }
    if (
        not required_wheel.issubset(packaging.REQUIRED_ROLE_VIEW_RUNTIME_WHEEL_PATHS)
        or not required_sdist.issubset(packaging.REQUIRED_ROLE_VIEW_RUNTIME_SDIST_PATHS)
    ):
        _fail("role_view_runtime_distribution_invalid")
    try:
        ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("role_view_runtime_source_kit_incomplete")
    for required in (
        "tools/verify_role_view_runtime.py",
        "tests.test_role_view_api_cli",
        "tests.test_role_view_runtime",
        "tests.test_role_view_runtime_plan",
        "tests.test_role_view_transition",
        "test_postgres_role_view_runtime.py",
        "test_role_view_transition.py",
    ):
        if required not in ci:
            _fail("role_view_runtime_ci_invalid")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        _fail("role_view_runtime_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        _fail("role_view_runtime_plan_changed")
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
        _fail("role_view_runtime_plan_invalid")
    if (
        plan["schema_id"] != "hormuz.role-view-runtime-plan"
        or plan["schema_version"] != 1
        or plan["stage"] != "provider_free_role_view_candidate"
        or plan["target_release"] != "1.3.0"
        or plan["feature_issue"] != 223
        or plan["gate_issue"] != 214
        or plan["base_main_commit"] != BASE_MAIN_COMMIT
        or plan["predecessor"] != {
            "path": PREDECESSOR_PATH,
            "file_sha256": PREDECESSOR_FILE_SHA256,
            "canonical_sha256": PREDECESSOR_CANONICAL_SHA256,
        }
        or plan["transitions"] != {
            "sqlite": {"from": 17, "to": 18},
            "postgresql": {"from": 22, "to": 23},
        }
        or plan["postgresql_acl"] != {
            "entry_count": EXPECTED_ACL[0],
            "sha256": EXPECTED_ACL[1],
            "mode": "fixed_literal_two_clean_managed_role_measurements",
        }
    ):
        _fail("role_view_runtime_plan_invalid")
    if plan["runtime"] != {
        "routes": list(ROUTES),
        "role_bindings": ROLE_BINDINGS,
        "authorization": "configured_role_before_parse_or_storage",
        "grouping": "work_scope",
        "team_scope_resolution": "nearest_explicit_owner_in_exact_versioned_ancestry",
        "wire_schema_id": "hormuz.portfolio-role-view-page",
        "wire_schema_version": 1,
        "budget_payload": {"schema_id": "hormuz.work-budget-report", "schema_version": 2},
        "scorecard_payload": {"schema_id": "hormuz.model-scorecard", "schema_version": 1},
        "cli_formats": ["terminal", "json", "csv"],
        "default_limit": 50,
        "maximum_limit": 100,
        "maximum_window_days": 366,
        "maximum_candidate_records": _MAX_CANDIDATES,
        "maximum_team_scope_rows": _MAX_SCOPE_ROWS,
        "database_statement_timeout_ms": 5000,
        "maximum_response_bytes": RESPONSE_BYTES,
        "cursor_ttl_seconds": _CURSOR_SECONDS,
        "cursor_binding": [
            "actor",
            "organization",
            "roles",
            "team",
            "query_class",
            "filters",
            "page_limit",
            "schema",
            "as_of",
            "primary_snapshot",
            "companion_snapshot",
        ],
        "provider_egress": False,
    }:
        _fail("role_view_runtime_plan_invalid")
    if plan["storage"] != {
        "tables": list(TABLES),
        "append_only": True,
        "tenant_keyed": True,
        "metadata_only": True,
        "atomic_audit_before_delivery": True,
        "raw_provider_payload_storage": False,
        "work_content_storage": False,
        "person_scoring_storage": False,
        "credential_storage": False,
        "rollback_requires_pre_migration_backup": True,
    } or plan["exclusions"] != list(_EXCLUSIONS):
        _fail("role_view_runtime_plan_invalid")
    if plan["gates"] != EXPECTED_GATES:
        _fail("role_view_runtime_gate_overclaim")
    if set(plan["nonclaims"]) != EXPECTED_NONCLAIMS or len(plan["nonclaims"]) != len(
        EXPECTED_NONCLAIMS
    ):
        _fail("role_view_runtime_plan_invalid")
    sources = plan["source_sha256"]
    if not isinstance(sources, dict) or set(sources) != set(SOURCE_PATHS):
        _fail("role_view_runtime_plan_invalid")
    for relative, expected in sources.items():
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            _fail("role_view_runtime_plan_invalid")
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("role_view_runtime_source_kit_incomplete")
        if actual != expected:
            _fail("role_view_runtime_source_changed")
    expected_runtime_routes = {
        "GET " + path: operation for path, operation in ROLE_VIEW_ROUTES.items()
    }
    expected_operations = {
        "list_finance_budgets": ("finance_viewer", "finance", "budget"),
        "list_platform_scorecards": ("platform_viewer", "platform", "scorecard"),
        "list_team_budgets": ("team_lead", "team", "budget"),
        "list_team_scorecards": ("team_lead", "team", "scorecard"),
    }
    if (
        tuple(expected_runtime_routes) != ROUTES
        or _OPERATIONS != expected_operations
        or (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (18, 23)
        or _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(23) != EXPECTED_ACL
    ):
        _fail("role_view_runtime_schema_boundary_changed")
    _validate_predecessor(root)
    _validate_migration(root, plan)
    _validate_wire_and_fixtures(root)
    _validate_inventory(root)
    _validate_distribution_and_ci(root)
    return {
        "status": "role_view_runtime_candidate_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
        "postgresql_acl": list(EXPECTED_ACL),
        "table_count": len(TABLES),
        "route_count": len(ROUTES),
        "roles": sorted(set(ROLE_BINDINGS.values())),
        "role_scoped_decision_views_implemented": True,
        "live_connectors_authorized": False,
        "recommendations_implemented": False,
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
    except RoleViewRuntimePlanError as error:
        print(error.code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
