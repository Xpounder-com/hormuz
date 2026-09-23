#!/usr/bin/env python3
"""Verify the fixed provider-free Linear reconciliation candidate."""

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


PLAN_PATH = "docs/linear-reconciliation-plan-v1.json"
CI_PATH = ".github/workflows/ci.yml"
PLAN_SHA256 = "e781775f4fee5fd48b9d500d76ac729b962a55ed670a65ee4f3da8bf00e9c451"
PREDECESSOR_PATH = "docs/linear-runtime-plan-v1.json"
PREDECESSOR_FILE_SHA256 = (
    "0440419441a88b9405bf35c986edf91c02ba80fba99203df63d309e2fb9603e2"
)
PREDECESSOR_CANONICAL_SHA256 = (
    "ad8f6448ea791a1890d67a6a8f1dc76684bbf94c1e86df7f329e9700dc59fbfe"
)
PREDECESSOR_SOURCE_COMMIT = "5ccadb542f96238b654b22c9a8f34b96673fad71"
EXPECTED_ACL = (
    222,
    "cd86c395bea316873e11e175563cbf31563212c45ca6cb6b6fb066f2f8f1e64d",
)
TABLES = (
    "gateway_linear_snapshot_receipts",
    "portfolio_linear_snapshot_context_events",
    "portfolio_linear_snapshot_context_retention_events",
)
AUDIT_SOURCES = (
    "hormuz.linear-context-event",
    "hormuz.linear-snapshot-receipt",
)
CUMULATIVE_TRANSITION_FILES = (
    "tests/test_finance_account_binding_transition_preflight.py",
    "tests/test_postgres_budget_transition.py",
    "tests/test_postgres_finance_transition.py",
    "tests/test_postgres_outcome_transition.py",
    "tests/test_sqlite_attribution_transition.py",
    "tests/test_sqlite_finance_native_attempt_transition.py",
    "tests/test_sqlite_finance_transition.py",
    "tests/test_sqlite_outcome_transition.py",
    "tests/test_sqlite_registry_transition.py",
)
EXPECTED_GATES = {
    "runtime_predecessor_verified": True,
    "reconciliation_implemented": True,
    "sqlite_runtime_tests_implemented": True,
    "postgresql_runtime_tests_implemented": True,
    "provider_free_source_verified": True,
    "provider_free_wheel_verified": False,
    "exact_main_ci_verified": False,
    "live_workspace_authorized": False,
    "live_reconciliation_verified": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_NONCLAIMS = {
    "authorized_live_workspace_or_provider_export",
    "complete_live_snapshot_or_backfill",
    "provider_API_token_or_polling_worker",
    "exact_main_CI",
    "provider_free_wheel",
    "final_candidate_acceptance",
    "v1.3.0_release",
}
REQUIRED_FILES = (
    CI_PATH,
    PLAN_PATH,
    PREDECESSOR_PATH,
    "docs/linear-transition-plan-v1.json",
    "docs/LINEAR_RECONCILIATION.md",
    "hormuz/migrations/postgresql/0020_linear_snapshot.sql",
    "tools/verify_linear_reconciliation_plan.py",
    "tests/test_linear_reconciliation_plan.py",
    "tests/test_linear_snapshot_runtime.py",
    "tests/test_postgres_linear_snapshot_runtime.py",
)


class LinearReconciliationPlanError(ValueError):
    """A fixed, content-free reconciliation-plan refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise LinearReconciliationPlanError(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("linear_reconciliation_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 1024 * 1024:
            _fail("linear_reconciliation_plan_invalid")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("linear_reconciliation_plan_invalid"),
        )
    except LinearReconciliationPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("linear_reconciliation_plan_invalid")


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
        _fail("linear_reconciliation_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def _validate_migration(root: Path, plan: dict) -> None:
    relative = "hormuz/migrations/postgresql/0020_linear_snapshot.sql"
    try:
        migration = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("linear_reconciliation_source_kit_incomplete")
    for table in TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("linear_reconciliation_migration_invalid")
        required = (
            f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
        )
        if any(item not in migration for item in required):
            _fail("linear_reconciliation_migration_invalid")
    required = (
        "CREATE FUNCTION {schema}.enforce_linear_snapshot_page_set()",
        "SECURITY DEFINER",
        "SET search_path = pg_catalog",
        "pg_advisory_xact_lock(hashtextextended(",
        "'portfolio:' || TG_TABLE_SCHEMA || ':' || NEW.organization_id",
        "linear_snapshot_page_set_conflict",
        "existing.binding_version IS DISTINCT FROM NEW.binding_version",
        "existing.captured_at IS DISTINCT FROM NEW.captured_at",
        "CREATE OR REPLACE FUNCTION {schema}.enforce_linear_context_cross_capture()",
        "CREATE TRIGGER gateway_linear_snapshot_page_set_consistent",
        "hormuz.linear-snapshot-receipt",
        "portfolio_linear_snapshot_context_events",
    )
    forbidden = (
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT TRUNCATE",
        "WITH GRANT OPTION",
    )
    if any(item not in migration for item in required) or any(
        item in migration.upper() for item in forbidden
    ):
        _fail("linear_reconciliation_migration_invalid")
    expected = plan.get("source_sha256", {}).get(relative)
    if expected != hashlib.sha256(migration.encode("utf-8")).hexdigest():
        _fail("linear_reconciliation_source_changed")


def _validate_ci(root: Path) -> None:
    try:
        workflow = (root / CI_PATH).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("linear_reconciliation_source_kit_incomplete")
    required = (
        "-p test_postgres_linear_connector_runtime.py",
        "-p test_postgres_linear_snapshot_runtime.py",
        "-p test_postgres_test_boundaries.py",
    )
    if any(workflow.count(item) != 1 for item in required):
        _fail("linear_reconciliation_ci_invalid")


def _validate_predecessor(root: Path) -> None:
    try:
        payload = (root / PREDECESSOR_PATH).read_bytes()
    except OSError:
        _fail("linear_reconciliation_source_kit_incomplete")
    predecessor = _read_json(root / PREDECESSOR_PATH)
    if (
        hashlib.sha256(payload).hexdigest() != PREDECESSOR_FILE_SHA256
        or canonical_digest(predecessor) != PREDECESSOR_CANONICAL_SHA256
    ):
        _fail("linear_reconciliation_predecessor_changed")
    try:
        transition = predecessor["predecessor"]
        transition_path = transition["path"]
        transition_payload = (root / transition_path).read_bytes()
        transition_value = _read_json(root / transition_path)
    except (KeyError, TypeError, OSError):
        _fail("linear_reconciliation_predecessor_changed")
    if (
        hashlib.sha256(transition_payload).hexdigest() != transition["file_sha256"]
        or canonical_digest(transition_value) != transition["canonical_sha256"]
    ):
        _fail("linear_reconciliation_predecessor_changed")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        _fail("linear_reconciliation_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        _fail("linear_reconciliation_plan_changed")
    if not isinstance(plan, dict) or set(plan) != {
        "schema_id",
        "schema_version",
        "stage",
        "target_release",
        "feature_issue",
        "gate_issue",
        "predecessor",
        "transitions",
        "postgresql_acl",
        "runtime",
        "storage",
        "completion",
        "source_sha256",
        "gates",
        "nonclaims",
    }:
        _fail("linear_reconciliation_plan_invalid")
    if (
        plan["schema_id"] != "hormuz.linear-reconciliation-plan"
        or plan["schema_version"] != 1
        or plan["stage"] != "provider_free_reconciliation_candidate"
        or plan["target_release"] != "1.3.0"
        or plan["feature_issue"] != 220
        or plan["gate_issue"] != 214
        or plan["predecessor"] != {
            "path": PREDECESSOR_PATH,
            "file_sha256": PREDECESSOR_FILE_SHA256,
            "canonical_sha256": PREDECESSOR_CANONICAL_SHA256,
            "source_commit": PREDECESSOR_SOURCE_COMMIT,
        }
        or plan["transitions"] != {
            "sqlite": {"from": 14, "to": 15},
            "postgresql": {"from": 19, "to": 20},
        }
        or plan["postgresql_acl"] != {
            "entry_count": EXPECTED_ACL[0],
            "sha256": EXPECTED_ACL[1],
            "mode": "fixed_literal_two_clean_managed_role_measurements",
        }
    ):
        _fail("linear_reconciliation_plan_invalid")
    if plan["runtime"] != {
        "route": "POST /v1/connectors/linear/snapshots",
        "success_status": 200,
        "maximum_request_bytes": 1048576,
        "maximum_internal_elapsed_ms": 4000,
        "ingest_slots": 4,
        "maximum_items_per_page": 100,
        "maximum_pages_per_snapshot": 100,
        "signature": "HMAC-SHA256_timestamp_dot_exact_raw_body_before_parse",
        "freshness_window_ms": 300000,
        "authority": "server_enrolled_workspace_team_typed_entity",
        "dedicated_snapshot_secret": True,
        "exact_committed_page_replay_before_stale_denial": True,
        "automatic_provider_work_replay": False,
        "provider_api_token": False,
        "outcomes_emitted": False,
    }:
        _fail("linear_reconciliation_plan_invalid")
    if plan["storage"] != {
        "tables": list(TABLES),
        "append_only": True,
        "tenant_keyed": True,
        "separate_from_webhook_provenance": True,
        "shared_context_commit_sequence": True,
        "shared_semantic_deduplication": True,
        "snapshot_page_set_consistent_in_storage": True,
        "audit_chain_sources": list(AUDIT_SOURCES),
        "raw_payload_storage": False,
        "plain_payload_hash_storage": False,
        "free_text_storage": False,
        "credential_storage": False,
    }:
        _fail("linear_reconciliation_plan_invalid")
    if plan["completion"] != {
        "requires_all_declared_page_numbers": True,
        "derived_from_durable_receipts": True,
        "individual_http_200_proves_complete_snapshot": False,
        "live_provider_export_verified": False,
    }:
        _fail("linear_reconciliation_plan_invalid")
    if plan["gates"] != EXPECTED_GATES:
        _fail("linear_reconciliation_gate_overclaim")
    if set(plan["nonclaims"]) != EXPECTED_NONCLAIMS or len(plan["nonclaims"]) != len(
        EXPECTED_NONCLAIMS
    ):
        _fail("linear_reconciliation_plan_invalid")

    _validate_predecessor(root)
    sources = plan["source_sha256"]
    if (
        not isinstance(sources, dict)
        or not sources
        or not set(CUMULATIVE_TRANSITION_FILES).issubset(sources)
    ):
        _fail("linear_reconciliation_plan_invalid")
    for relative, expected in sources.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            _fail("linear_reconciliation_plan_invalid")
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("linear_reconciliation_source_kit_incomplete")
        if actual != expected:
            _fail("linear_reconciliation_source_changed")

    _validate_ci(root)

    if (
        (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (15, 20)
        or _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(20) != EXPECTED_ACL
    ):
        _fail("linear_reconciliation_schema_boundary_changed")
    _validate_migration(root, plan)
    return {
        "status": "linear_reconciliation_candidate_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
        "postgresql_acl": list(EXPECTED_ACL),
        "table_count": len(TABLES),
        "audit_source_count": len(AUDIT_SOURCES),
        "reconciliation_implemented": True,
        "live_workspace_authorized": False,
        "live_reconciliation_verified": False,
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
    except LinearReconciliationPlanError as error:
        print(error.code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
