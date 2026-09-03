#!/usr/bin/env python3
"""Verify the #8 provider-collection runtime candidate, never acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_finance_collection_transition_plan import (
    CONTRACT_CANONICAL_SHA256,
    PLAN_CANONICAL_SHA256,
    SOURCE_CONTRACT_CANONICAL_SHA256,
    FinanceCollectionTransitionError,
    validate_finance_collection_contract,
    validate_finance_collection_plan,
)
from tools.verify_finance_native_attempt_transition_plan import (
    FinanceNativeAttemptTransitionError,
    verify_finance_native_attempt_transition_plan,
)
from tools.verify_finance_transition_plan import (
    FinanceTransitionError,
    validate_finance_sources,
)


PLAN_PATH = "docs/finance-transition-plan-v6.json"
SOURCE_CONTRACT_PATH = "docs/finance-source-contract-v1.json"
COLLECTION_CONTRACT_PATH = "docs/finance-collection-contract-v1.json"
PLAN_CANONICAL_SHA256_V6 = "cf618e78058b0842efca2768f3638a0a4ce3954d855996a0224bc8dd955b2c7d"
MAX_JSON_BYTES = 256 * 1024
EXPECTED_ACL = (
    185,
    "46c2bf134047c4720d0d6236dfb9efa62e22e37b70c9b6ef8df4b166c656249a",
)
INJECTED_ACL = (
    186,
    "d06ec615d82a176b107e1131c00e1dceb5f629d9504a7519f64e1eb77a0c7246",
)
REQUIRED_FILES = (
    PLAN_PATH,
    SOURCE_CONTRACT_PATH,
    COLLECTION_CONTRACT_PATH,
    "docs/finance-transition-plan-v5.json",
    "docs/FINANCE_COLLECTION_TRANSITION.md",
    "docs/FINANCE_COLLECTION_RUNTIME.md",
    "hormuz/_finance_collection_schema.py",
    "hormuz/finance_collection.py",
    "hormuz/finance_collection_repository.py",
    "hormuz/commands/finance.py",
    "hormuz/migrations/postgresql/0016_finance_collection.sql",
    "tools/verify_finance_collection_runtime.py",
    "tests/test_finance_collection_runtime.py",
    "tests/test_finance_collection_cli.py",
    "tests/test_sqlite_finance_collection_transition.py",
    "tests/test_postgres_finance_collection_transition.py",
    "tests/test_finance_collection_runtime_plan.py",
)


class FinanceCollectionRuntimeError(ValueError):
    """Fixed, content-free runtime candidate diagnostics."""


def _fail(code: str) -> None:
    raise FinanceCollectionRuntimeError(code)


def _canonical_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("finance_collection_runtime_json_invalid")
    return hashlib.sha256(payload).hexdigest()


def validate_finance_collection_runtime_plan(value: object) -> None:
    if _canonical_digest(value) != PLAN_CANONICAL_SHA256_V6:
        _fail("finance_collection_runtime_contract_changed")


def _read_json(root: Path, relative: str) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("finance_collection_runtime_json_duplicate_member")
            result[key] = value
        return result

    def nonfinite(_value):
        _fail("finance_collection_runtime_json_nonfinite")

    try:
        with (root / relative).open("rb") as source:
            raw = source.read(MAX_JSON_BYTES + 1)
        if not 1 <= len(raw) <= MAX_JSON_BYTES:
            _fail("finance_collection_runtime_file_bounds")
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=nonfinite
        )
    except FinanceCollectionRuntimeError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("finance_collection_runtime_file_unreadable")


def _validate_postgres_runtime_grants(migration: str, tables: object) -> None:
    if not isinstance(tables, list):
        _fail("finance_collection_runtime_source_invalid")
    for statement in migration.split(";"):
        normalized = " ".join(statement.split())
        if not normalized.upper().startswith("GRANT "):
            continue
        if any(
            f"{{schema}}.{table}" in normalized and "{runtime_role}" in normalized
            for table in tables
        ):
            _fail("finance_collection_postgres_grants_not_gated")


def verify_finance_collection_runtime(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            _fail("finance_collection_runtime_source_kit_incomplete")

    plan = _read_json(root, PLAN_PATH)
    preflight = _read_json(root, "docs/finance-transition-plan-v5.json")
    collection_contract = _read_json(root, COLLECTION_CONTRACT_PATH)
    source_contract = _read_json(root, SOURCE_CONTRACT_PATH)
    validate_finance_collection_runtime_plan(plan)
    try:
        validate_finance_collection_plan(preflight)
        validate_finance_collection_contract(collection_contract)
        if _canonical_digest(source_contract) != SOURCE_CONTRACT_CANONICAL_SHA256:
            _fail("finance_collection_source_contract_changed")
        validate_finance_sources(source_contract)
        native = verify_finance_native_attempt_transition_plan(
            root, allow_successor_schema=True
        )
    except FinanceCollectionRuntimeError:
        raise
    except (FinanceCollectionTransitionError, FinanceNativeAttemptTransitionError, FinanceTransitionError):
        _fail("finance_collection_runtime_predecessor_invalid")

    try:
        from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
        from hormuz.finance_collection_repository import (
            POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED,
        )
        from hormuz.postgres import (
            POSTGRES_SCHEMA_VERSION,
            _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
        )

        if (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (12, 16):
            _fail("finance_collection_runtime_schema_version_invalid")
        if POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED is not False:
            _fail("finance_collection_postgres_runtime_gate_changed")
        if _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(15) != EXPECTED_ACL:
            _fail("finance_collection_acl_boundary_changed")
        if _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(16) != EXPECTED_ACL:
            _fail("finance_collection_acl_boundary_changed")
        if not isinstance(plan, dict) or not isinstance(preflight, dict):
            _fail("finance_collection_runtime_contract_changed")
        scope = plan["runtime_scope"]
        gate = plan["postgresql_acl_gate"]
        gates = plan["gates"]
        if (
            plan["schema_id"] != "hormuz.finance-transition-plan"
            or plan["schema_version"] != 6
            or plan["target_release"] != "1.1.0"
            or plan["feature_issue"] != 8
            or plan["gate_issue"] != 214
            or plan["preflight_plan"]
            != {
                "path": "docs/finance-transition-plan-v5.json",
                "canonical_sha256": PLAN_CANONICAL_SHA256,
            }
            or plan["collection_contract"]
            != {
                "path": COLLECTION_CONTRACT_PATH,
                "canonical_sha256": CONTRACT_CANONICAL_SHA256,
            }
            or plan["transitions"]
            != {"sqlite": {"from": 11, "to": 12}, "postgresql": {"from": 15, "to": 16}}
            or len(scope["profiles"]) != 4
            or len(scope["tables"]) != 7
            or scope["sqlite_runtime_candidate"] is not True
            or scope["postgresql_schema_candidate"] is not True
            or scope["complete_snapshot_only"] is not True
            or scope["provider_import_implemented"] is not True
            or scope["reconciliation_implemented"] is not False
            or scope["automatic_allocation"] is not False
            or scope["live_finance_verified"] is not False
            or gate["fingerprint_mode"] != "fixed_literal"
            or (gate["expected_current_count"], gate["expected_current_sha256"]) != EXPECTED_ACL
            or (gate["injected_permission_count"], gate["injected_permission_sha256"]) != INJECTED_ACL
            or gate["reject_code"] != "postgres_bootstrap_acl_boundary_invalid"
            or gate["accepts_multiple_fingerprints"] is not False
            or gate["computes_expected_from_database"] is not False
            or gate["runtime_grants_withheld"] is not True
            or gate["runtime_access_accepted"] is not False
            or gates
            != {
                "collection_preflight_accepted": True,
                "provider_collection_runtime_accepted": False,
                "postgresql_collection_runtime_accepted": False,
                "reconciliation_accepted": False,
                "finance_implemented": False,
                "final_candidate_accepted": False,
                "released": False,
            }
        ):
            _fail("finance_collection_runtime_contract_changed")
        migration = (root / "hormuz/migrations/postgresql/0016_finance_collection.sql").read_text(
            encoding="utf-8"
        )
        _validate_postgres_runtime_grants(migration, scope["tables"])
    except FinanceCollectionRuntimeError:
        raise
    except (ImportError, KeyError, OSError, TypeError, UnicodeError):
        _fail("finance_collection_runtime_source_invalid")

    return {
        "schema_id": "hormuz.finance-transition-plan",
        "schema_version": 6,
        "status": "finance_collection_runtime_candidate_verified",
        "target_release": "1.1.0",
        "feature_issue": 8,
        "gate_issue": 214,
        "current_sqlite_schema_version": 12,
        "current_postgresql_schema_version": 16,
        "collection_profile_count": 4,
        "collection_table_count": 7,
        "collection_preflight_accepted": True,
        "provider_collection_runtime_accepted": False,
        "postgresql_collection_runtime_accepted": False,
        "provider_import_implemented": True,
        "reconciliation_implemented": False,
        "finance_implemented": False,
        "live_finance_verified": False,
        "final_candidate_accepted": False,
        "released": False,
        "postgresql_acl_current": EXPECTED_ACL,
        "postgresql_acl_injected": INJECTED_ACL,
        "native_attempt_runtime_source_verified": bool(
            native["native_request_cost_capture_implemented"]
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_finance_collection_runtime(args.repo_root), sort_keys=True))
        return 0
    except FinanceCollectionRuntimeError as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
