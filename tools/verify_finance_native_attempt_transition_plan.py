#!/usr/bin/env python3
"""Verify #8's native-attempt finance runtime candidate, never acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_budget_transition_plan import (
    BudgetTransitionError,
    verify_budget_implementation_plan,
)


PLAN_PATH = "docs/finance-transition-plan-v3.json"
IMPLEMENTATION_PLAN_PATH = "docs/finance-transition-plan-v4.json"
CONTRACT_PATH = "docs/finance-attempt-evidence-contract-v1.json"
PLAN_CANONICAL_SHA256 = "9cf10dc4072aa3827d5c7a561850f57acf4f9a8cb5d9a4f920596604232642a7"
IMPLEMENTATION_PLAN_CANONICAL_SHA256 = "508fc567d91b7c9117014735b7237e5979c2d8fd019b0c927e9605fd0ffc5318"
CONTRACT_CANONICAL_SHA256 = "fdb0026e4efb601b241239c6b53b967f017aa637c131b49cff8af13af50362c9"
PREDECESSOR_SOURCE_COMMIT = "4e3133f19db4c34d7a181848ebc36754bce164ea"
PREDECESSOR_ARCHIVE_SHA256 = "86a29497ac0f4e9a2ba177fba54a3b36179077ce402a1ce0fbe37a95c61920a0"
MAX_JSON_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
REQUIRED_FILES = (
    PLAN_PATH,
    IMPLEMENTATION_PLAN_PATH,
    CONTRACT_PATH,
    "docs/FINANCE_NATIVE_ATTEMPT_TRANSITION.md",
    "docs/FINANCE_NATIVE_ATTEMPT_RUNTIME.md",
    "hormuz/_finance_attempt_schema.py",
    "hormuz/finance_attempts.py",
    "hormuz/migrations/postgresql/0015_finance_attempt_evidence.sql",
    "tools/verify_finance_native_attempt_transition_plan.py",
    "tools/verify_budget_transition_plan.py",
    "tests/_finance_native_predecessor_fixture.py",
    "tests/_postgres_fixture.py",
    "tests/_registry_transition_fixture.py",
    "tests/test_finance_native_attempt_transition_plan.py",
    "tests/test_sqlite_finance_native_attempt_transition.py",
    "tests/test_postgres_finance_native_attempt_transition.py",
    "tests/test_finance_native_attempt_packaging.py",
    "tests/test_finance_attempt_runtime.py",
    "tests/test_postgres_finance_attempt_runtime.py",
)


class FinanceNativeAttemptTransitionError(ValueError):
    """Fixed, content-free native-attempt checkpoint diagnostics."""


def _fail(code: str) -> None:
    raise FinanceNativeAttemptTransitionError(code)


def _canonical_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _fail("finance_native_json_invalid")
    return hashlib.sha256(payload).hexdigest()


def validate_finance_native_attempt_plan(value: object) -> None:
    if _canonical_digest(value) != PLAN_CANONICAL_SHA256:
        _fail("finance_native_preflight_contract_changed")


def validate_finance_attempt_evidence_contract(value: object) -> None:
    if _canonical_digest(value) != CONTRACT_CANONICAL_SHA256:
        _fail("finance_attempt_evidence_contract_changed")


def validate_finance_native_attempt_implementation_plan(value: object) -> None:
    if _canonical_digest(value) != IMPLEMENTATION_PLAN_CANONICAL_SHA256:
        _fail("finance_native_runtime_contract_changed")


def _read_json(root: Path, relative: str) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("finance_native_json_duplicate_member")
            result[key] = value
        return result

    def nonfinite(_value):
        _fail("finance_native_json_nonfinite")

    try:
        with (root / relative).open("rb") as source:
            raw = source.read(MAX_JSON_BYTES + 1)
        if not 1 <= len(raw) <= MAX_JSON_BYTES:
            _fail("finance_native_file_bounds")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=nonfinite,
        )
    except FinanceNativeAttemptTransitionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("finance_native_file_unreadable")


def verify_predecessor_archive(path: Path) -> None:
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_ARCHIVE_BYTES + 1)
        if (
            not payload
            or len(payload) > MAX_ARCHIVE_BYTES
            or hashlib.sha256(payload).hexdigest() != PREDECESSOR_ARCHIVE_SHA256
        ):
            _fail("finance_native_predecessor_archive_invalid")
    except FinanceNativeAttemptTransitionError:
        raise
    except OSError:
        _fail("finance_native_predecessor_archive_invalid")


def verify_finance_native_attempt_transition_plan(
    root: Path = ROOT,
    *,
    allow_successor_schema: bool = False,
) -> dict[str, object]:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            _fail("finance_native_source_kit_incomplete")
    plan = _read_json(root, PLAN_PATH)
    implementation = _read_json(root, IMPLEMENTATION_PLAN_PATH)
    contract = _read_json(root, CONTRACT_PATH)
    validate_finance_native_attempt_plan(plan)
    validate_finance_native_attempt_implementation_plan(implementation)
    validate_finance_attempt_evidence_contract(contract)
    try:
        predecessor = verify_budget_implementation_plan(root)
    except BudgetTransitionError:
        _fail("finance_native_budget_predecessor_invalid")
    try:
        from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
        from hormuz.postgres import POSTGRES_SCHEMA_VERSION

        expected_current = (12, 16) if allow_successor_schema else (11, 15)
        if (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != expected_current:
            _fail("finance_native_runtime_schema_version_invalid")
        if not isinstance(implementation, dict):
            _fail("finance_native_runtime_contract_changed")
        candidate = implementation["candidate"]
        storage = implementation["storage"]
        if (
            candidate["sqlite_schema_version"] != 11
            or candidate["postgresql_schema_version"] != 15
            or candidate["native_request_cost_capture_implemented"] is not True
            or candidate["native_attempt_runtime_accepted"] is not False
            or set(storage["altered_tables"]) != {
                "gateway_request_attempts", "gateway_audit_chain_entries",
            }
        ):
            _fail("finance_native_runtime_contract_changed")
    except FinanceNativeAttemptTransitionError:
        raise
    except (ImportError, KeyError, TypeError):
        _fail("finance_native_runtime_source_invalid")
    return {
        "schema_id": "hormuz.finance-transition-plan",
        "schema_version": 4,
        "status": "finance_native_attempt_runtime_candidate_verified",
        "target_release": "1.1.0",
        "feature_issue": 8,
        "gate_issue": 214,
        "predecessor_source_commit": PREDECESSOR_SOURCE_COMMIT,
        "predecessor_archive_sha256": PREDECESSOR_ARCHIVE_SHA256,
        "predecessor_sqlite_schema_version": 10,
        "predecessor_postgresql_schema_version": 14,
        "planned_sqlite_schema_version": 11,
        "planned_postgresql_schema_version": 15,
        "provider_profile_count": 2,
        "new_table_count": 1,
        "altered_table_count": 2,
        "audit_chain_source_union_expanded": True,
        "request_attempt_price_binding_column_count": 5,
        "post_migration_price_binding_required": True,
        "missing_usage_estimate_is_zero": False,
        "new_http_routes": 0,
        "new_cli_commands": 0,
        "budget_runtime_source_verified": predecessor["budget_implemented"],
        "native_request_cost_capture_implemented": True,
        "native_attempt_preflight_accepted": True,
        "native_attempt_runtime_accepted": False,
        "finance_implemented": False,
        "live_finance_verified": False,
        "final_candidate_accepted": False,
        "released": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor-archive", type=Path)
    parser.add_argument(
        "--allow-successor-schema",
        action="store_true",
        help="also accept the current additive schema successor while retaining the v4 contract checks",
    )
    args = parser.parse_args(argv)
    try:
        if args.predecessor_archive is not None:
            verify_predecessor_archive(args.predecessor_archive)
        result = verify_finance_native_attempt_transition_plan(
            allow_successor_schema=args.allow_successor_schema
        )
        result["predecessor_archive_verified"] = args.predecessor_archive is not None
        print(json.dumps(result, sort_keys=True))
        return 0
    except FinanceNativeAttemptTransitionError as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
