#!/usr/bin/env python3
"""Verify the fixed provider-free Linear runtime candidate, never release."""

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


PLAN_PATH = "docs/linear-runtime-plan-v1.json"
PLAN_SHA256 = "ad8f6448ea791a1890d67a6a8f1dc76684bbf94c1e86df7f329e9700dc59fbfe"
PREDECESSOR_PATH = "docs/linear-transition-plan-v1.json"
PREDECESSOR_FILE_SHA256 = "a6fd47259f67ae7b607a205cb6896cae2cb218ce2539829d54260e8bdd5bf0d4"
PREDECESSOR_CANONICAL_SHA256 = "681633994ba4c826e99f826730f92cfad1ac1e4bed6fc730a650caf2ad0c699f"
EXPECTED_ACL = (
    213,
    "337ece4276d5c36f5115f88c28c97818a3c653862a37c53f6a1590eb7c9e2f85",
)
TABLES = (
    "gateway_linear_delivery_receipts",
    "portfolio_linear_context_events",
    "portfolio_linear_context_retention_events",
    "portfolio_linear_source_binding_versions",
)
ENFORCEMENT_TABLES = ("gateway_linear_route_claims",)
AUDIT_SOURCES = (
    "hormuz.linear-context-event",
    "hormuz.linear-context-retention",
    "hormuz.linear-delivery-receipt",
    "hormuz.linear-source-binding-version",
)
EXPECTED_GATES = {
    "transition_checkpoint_accepted": True,
    "runtime_implemented": True,
    "sqlite_runtime_tests_implemented": True,
    "postgresql_runtime_tests_implemented": True,
    "provider_free_source_verified": True,
    "provider_free_wheel_verified": True,
    "exact_main_ci_verified": False,
    "reconciliation_implemented": False,
    "live_workspace_authorized": False,
    "live_delivery_verified": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_NONCLAIMS = {
    "authorized_live_workspace_or_webhook",
    "provider_backfill_or_reconciliation",
    "exact_main_CI",
    "final_candidate_acceptance",
    "v1.3.0_release",
}
REQUIRED_FILES = (
    PLAN_PATH,
    PREDECESSOR_PATH,
    "docs/LINEAR_CONNECTOR_RUNTIME.md",
    "tools/verify_linear_runtime_plan.py",
    "tests/test_linear_runtime_plan.py",
)


class LinearRuntimePlanError(ValueError):
    """A fixed, content-free runtime-plan refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise LinearRuntimePlanError(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("linear_runtime_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 1024 * 1024:
            _fail("linear_runtime_plan_invalid")
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: _fail("linear_runtime_plan_invalid"),
        )
    except LinearRuntimePlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("linear_runtime_plan_invalid")


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
        _fail("linear_runtime_plan_invalid")
    return hashlib.sha256(payload).hexdigest()


def _validate_migration(root: Path, plan: dict) -> None:
    relative = "hormuz/migrations/postgresql/0019_linear_connector.sql"
    try:
        migration = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        _fail("linear_runtime_source_kit_incomplete")
    for table in TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("linear_runtime_migration_invalid")
        required = (
            f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
            f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
            f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
        )
        if any(item not in migration for item in required):
            _fail("linear_runtime_migration_invalid")
    for table in ENFORCEMENT_TABLES:
        if len(re.findall(rf"CREATE TABLE \{{schema\}}\.{table}\s*\(", migration)) != 1:
            _fail("linear_runtime_migration_invalid")
        if (
            f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC" not in migration
            or re.search(rf"GRANT\s+[^;]+\s+ON\s+\{{schema\}}\.{table}\b", migration, re.I)
        ):
            _fail("linear_runtime_migration_invalid")
    for source in AUDIT_SOURCES:
        if migration.count(source) < 3:
            _fail("linear_runtime_migration_invalid")
    required = (
        "CREATE FUNCTION {schema}.enforce_linear_binding_cardinality()",
        "SECURITY DEFINER",
        "SET search_path = pg_catalog",
        "source_workspace_id",
        "source_webhook_id",
        "gateway_linear_route_claims",
        "ON CONFLICT (claim_kind, source_identifier) DO NOTHING",
        "CREATE TRIGGER portfolio_linear_binding_cardinality",
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
        _fail("linear_runtime_migration_invalid")
    expected = plan.get("source_sha256", {}).get(relative)
    if expected != hashlib.sha256(migration.encode("utf-8")).hexdigest():
        _fail("linear_runtime_source_changed")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        _fail("linear_runtime_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        _fail("linear_runtime_plan_changed")
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
        "source_sha256",
        "gates",
        "nonclaims",
    }:
        _fail("linear_runtime_plan_invalid")
    if (
        plan["schema_id"] != "hormuz.linear-runtime-plan"
        or plan["schema_version"] != 1
        or plan["stage"] != "provider_free_runtime_candidate"
        or plan["target_release"] != "1.3.0"
        or plan["feature_issue"] != 220
        or plan["gate_issue"] != 214
        or plan["base_main_commit"]
        != "59cffc0790b45312bb314efda0dd9eafd3d81919"
        or plan["predecessor"]
        != {
            "path": PREDECESSOR_PATH,
            "file_sha256": PREDECESSOR_FILE_SHA256,
            "canonical_sha256": PREDECESSOR_CANONICAL_SHA256,
        }
        or plan["transitions"]
        != {
            "sqlite": {"from": 13, "to": 14},
            "postgresql": {"from": 18, "to": 19},
        }
        or plan["postgresql_acl"]
        != {
            "entry_count": EXPECTED_ACL[0],
            "sha256": EXPECTED_ACL[1],
            "mode": "fixed_literal_clean_managed_role_measurement",
        }
    ):
        _fail("linear_runtime_plan_invalid")
    runtime = plan["runtime"]
    if runtime != {
        "route": "POST /v1/connectors/linear/events",
        "success_status": 200,
        "maximum_request_bytes": 1048576,
        "maximum_internal_elapsed_ms": 4000,
        "transport_slots": 8,
        "ingest_slots": 8,
        "signature": "HMAC-SHA256_exact_raw_body_before_parse",
        "freshness_window_ms": 60000,
        "authority": "server_enrolled_workspace_webhook_team_typed_entity",
        "delivery_header_authority": False,
        "exact_committed_body_replay_before_stale_denial": True,
        "stable_source_fact_replay": True,
        "atomic_commit": "binding_receipt_context_outcome_coverage_audit",
        "automatic_provider_work_replay": False,
    }:
        _fail("linear_runtime_plan_invalid")
    storage = plan["storage"]
    if storage != {
        "tables": list(TABLES),
        "enforcement_tables": list(ENFORCEMENT_TABLES),
        "append_only": True,
        "tenant_keyed": True,
        "workspace_owner_cardinality_in_storage": True,
        "webhook_route_cardinality_in_storage": True,
        "audit_chain_sources": list(AUDIT_SOURCES),
        "raw_payload_storage": False,
        "plain_payload_hash_storage": False,
        "free_text_storage": False,
        "credential_storage": False,
    }:
        _fail("linear_runtime_plan_invalid")
    if plan["gates"] != EXPECTED_GATES:
        _fail("linear_runtime_gate_overclaim")
    if set(plan["nonclaims"]) != EXPECTED_NONCLAIMS or len(plan["nonclaims"]) != len(
        EXPECTED_NONCLAIMS
    ):
        _fail("linear_runtime_plan_invalid")

    try:
        predecessor_bytes = (root / PREDECESSOR_PATH).read_bytes()
    except OSError:
        _fail("linear_runtime_source_kit_incomplete")
    predecessor = _read_json(root / PREDECESSOR_PATH)
    if (
        hashlib.sha256(predecessor_bytes).hexdigest() != PREDECESSOR_FILE_SHA256
        or canonical_digest(predecessor) != PREDECESSOR_CANONICAL_SHA256
    ):
        _fail("linear_runtime_predecessor_changed")

    sources = plan["source_sha256"]
    if not isinstance(sources, dict) or not sources:
        _fail("linear_runtime_plan_invalid")
    for relative, expected in sources.items():
        if (
            not isinstance(relative, str)
            or not isinstance(expected, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected) is None
        ):
            _fail("linear_runtime_plan_invalid")
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            _fail("linear_runtime_source_kit_incomplete")
        if actual != expected:
            _fail("linear_runtime_source_changed")

    if (
        (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (14, 19)
        or _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(19) != EXPECTED_ACL
    ):
        _fail("linear_runtime_schema_boundary_changed")
    _validate_migration(root, plan)
    return {
        "status": "linear_runtime_candidate_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
        "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
        "postgresql_acl": list(EXPECTED_ACL),
        "table_count": len(TABLES),
        "enforcement_table_count": len(ENFORCEMENT_TABLES),
        "audit_source_count": len(AUDIT_SOURCES),
        "runtime_implemented": True,
        "live_workspace_authorized": False,
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
    except LinearRuntimePlanError as error:
        print(error.code)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
