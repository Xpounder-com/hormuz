#!/usr/bin/env python3
"""Verify #8's provider collection preflight, never runtime acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_finance_native_attempt_transition_plan import (
    FinanceNativeAttemptTransitionError,
    verify_finance_native_attempt_transition_plan,
)
from tools.verify_finance_transition_plan import (
    FinanceTransitionError,
    validate_finance_sources,
)


PLAN_PATH = "docs/finance-transition-plan-v5.json"
CONTRACT_PATH = "docs/finance-collection-contract-v1.json"
SOURCE_CONTRACT_PATH = "docs/finance-source-contract-v1.json"
PLAN_CANONICAL_SHA256 = "ab8b05ac20b26259aa892a98ef2414469d777e52485ae0a75d4e6021251becb6"
CONTRACT_CANONICAL_SHA256 = "c32eeab34af806bd18cbedf77bbf91b0e30b4b369300b7ba01159701ced0760e"
SOURCE_CONTRACT_CANONICAL_SHA256 = "290def8f2cd7026d4e0f0512db9254906f8592a026ee4beb9cac3623d7a1d9f4"
AUDIT_SOURCE_SCHEMAS = {
    "hormuz.finance-source-binding-version": {
        "schema_version": 1,
        "table": "portfolio_finance_source_binding_versions",
        "source_event_id_column": "binding_event_id",
        "evidence_json_column": "evidence_json",
    },
    "hormuz.finance-collection-event": {
        "schema_version": 1,
        "table": "portfolio_finance_collection_events",
        "source_event_id_column": "event_id",
        "evidence_json_column": "evidence_json",
    },
    "hormuz.finance-snapshot": {
        "schema_version": 1,
        "table": "portfolio_finance_snapshots",
        "source_event_id_column": "snapshot_id",
        "evidence_json_column": "evidence_json",
    },
}
PLANNED_TABLES = (
    "portfolio_finance_source_binding_versions",
    "portfolio_finance_collection_attempts",
    "portfolio_finance_collection_events",
    "portfolio_finance_snapshots",
    "portfolio_finance_snapshot_bucket_coverage",
    "portfolio_finance_usage_observations",
    "portfolio_finance_cost_observations",
)
PREDECESSOR_SOURCE_COMMIT = "cf30256760b68b133208b4013bdd31b22639b172"
PREDECESSOR_ARCHIVE_SHA256 = "35cecfb4dbb1b4a972a4f43a30941e91e38c049636bd98cc4869cb145c65d1da"
PREDECESSOR_ARCHIVE_PREFIX = "hormuz-finance-native-runtime-baseline/"
PREDECESSOR_RUNTIME_FILE_COUNT = 145
PREDECESSOR_RUNTIME_TREE_SHA256 = "163b8ebec0a519f2d07b7c2b2b53a169f69eb0abef6122ee91b6194a2df21b2a"
MAX_JSON_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_RUNTIME_FILE_BYTES = 2 * 1024 * 1024
REQUIRED_FILES = (
    PLAN_PATH,
    CONTRACT_PATH,
    SOURCE_CONTRACT_PATH,
    "docs/finance-transition-plan-v4.json",
    "docs/FINANCE_COLLECTION_TRANSITION.md",
    "tools/verify_finance_collection_transition_plan.py",
    "tools/verify_finance_native_attempt_transition_plan.py",
    "tests/_finance_collection_predecessor_fixture.py",
    "tests/_finance_collection_transition_fixture.py",
    "tests/_postgres_fixture.py",
    "tests/_registry_transition_fixture.py",
    "tests/test_finance_collection_transition_plan.py",
    "tests/test_sqlite_finance_collection_transition.py",
    "tests/test_postgres_finance_collection_transition.py",
    "tests/test_finance_collection_packaging.py",
)
FORBIDDEN_RUNTIME_FILES = (
    "hormuz/_finance_collection_schema.py",
    "hormuz/finance_collection.py",
    "hormuz/migrations/postgresql/0016_finance_collection.sql",
)


class FinanceCollectionTransitionError(ValueError):
    """Fixed, content-free provider collection checkpoint diagnostics."""


def _fail(code: str) -> None:
    raise FinanceCollectionTransitionError(code)


def _canonical_digest(value: object) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError):
        _fail("finance_collection_json_invalid")
    return hashlib.sha256(payload).hexdigest()


def validate_finance_collection_plan(value: object) -> None:
    if _canonical_digest(value) != PLAN_CANONICAL_SHA256:
        _fail("finance_collection_preflight_contract_changed")


def validate_finance_collection_contract(value: object) -> None:
    if _canonical_digest(value) != CONTRACT_CANONICAL_SHA256:
        _fail("finance_collection_contract_changed")


def _read_json(root: Path, relative: str) -> object:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("finance_collection_json_duplicate_member")
            result[key] = value
        return result

    def nonfinite(_value):
        _fail("finance_collection_json_nonfinite")

    try:
        with (root / relative).open("rb") as source:
            raw = source.read(MAX_JSON_BYTES + 1)
        if not 1 <= len(raw) <= MAX_JSON_BYTES:
            _fail("finance_collection_file_bounds")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=nonfinite,
        )
    except FinanceCollectionTransitionError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        _fail("finance_collection_file_unreadable")


def verify_predecessor_archive(path: Path) -> None:
    try:
        with path.open("rb") as source:
            payload = source.read(MAX_ARCHIVE_BYTES + 1)
        if (
            not payload
            or len(payload) > MAX_ARCHIVE_BYTES
            or hashlib.sha256(payload).hexdigest() != PREDECESSOR_ARCHIVE_SHA256
        ):
            _fail("finance_collection_predecessor_archive_invalid")
    except FinanceCollectionTransitionError:
        raise
    except OSError:
        _fail("finance_collection_predecessor_archive_invalid")


def _runtime_inventory(root: Path) -> tuple[int, str]:
    package = root / "hormuz"
    try:
        # Every package file is evidence. Suffix filtering would omit runtime
        # assets such as console.css and make the predecessor pin incomplete.
        files = [
            path
            for path in package.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
        ]
        entries = []
        package_root = package.resolve()
        for path in sorted(files):
            if path.is_symlink() or not path.resolve().is_relative_to(package_root):
                _fail("finance_collection_runtime_inventory_invalid")
            payload = path.read_bytes()
            if len(payload) > MAX_RUNTIME_FILE_BYTES:
                _fail("finance_collection_runtime_inventory_invalid")
            entries.append(
                (
                    path.relative_to(package).as_posix(),
                    hashlib.sha256(payload).hexdigest(),
                )
            )
    except (OSError, RuntimeError):
        _fail("finance_collection_runtime_inventory_invalid")
    manifest = json.dumps(
        entries,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return len(entries), hashlib.sha256(manifest).hexdigest()


def verify_finance_collection_transition_plan(
    root: Path = ROOT,
) -> dict[str, object]:
    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            _fail("finance_collection_source_kit_incomplete")
    if any((root / relative).exists() for relative in FORBIDDEN_RUNTIME_FILES):
        _fail("finance_collection_runtime_present_in_preflight")

    plan = _read_json(root, PLAN_PATH)
    contract = _read_json(root, CONTRACT_PATH)
    source_contract = _read_json(root, SOURCE_CONTRACT_PATH)
    validate_finance_collection_plan(plan)
    validate_finance_collection_contract(contract)
    if _canonical_digest(source_contract) != SOURCE_CONTRACT_CANONICAL_SHA256:
        _fail("finance_collection_source_contract_changed")
    try:
        validate_finance_sources(source_contract)
        native = verify_finance_native_attempt_transition_plan(root)
    except (FinanceTransitionError, FinanceNativeAttemptTransitionError):
        _fail("finance_collection_native_predecessor_invalid")

    try:
        from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
        from hormuz.postgres import POSTGRES_SCHEMA_VERSION

        if SQLITE_SCHEMA_VERSION != 11 or POSTGRES_SCHEMA_VERSION != 15:
            _fail("finance_collection_preflight_schema_changed")
        runtime_count, runtime_digest = _runtime_inventory(root)
        if (
            runtime_count != PREDECESSOR_RUNTIME_FILE_COUNT
            or runtime_digest != PREDECESSOR_RUNTIME_TREE_SHA256
        ):
            _fail("finance_collection_runtime_inventory_invalid")
        if not isinstance(plan, dict) or not isinstance(contract, dict):
            _fail("finance_collection_contract_changed")
        predecessor = plan["predecessor"]
        transitions = plan["transitions"]
        dependencies = plan["dependencies"]
        storage = plan["planned_storage"]
        compatibility = plan["compatibility"]
        snapshot = contract["snapshot"]
        bucket_coverage = contract["bucket_coverage"]
        observation = contract["observation"]
        numeric_domains = contract["numeric_domains"]
        money_domain = numeric_domains["money"]
        quantity_domain = numeric_domains["provider_quantity"]
        usage_count_domain = numeric_domains["usage_count"]
        coverage_count_domain = numeric_domains[
            "coverage_observation_count"
        ]
        if (
            predecessor["source_commit"] != PREDECESSOR_SOURCE_COMMIT
            or predecessor["archive_sha256"] != PREDECESSOR_ARCHIVE_SHA256
            or predecessor["archive_prefix"] != PREDECESSOR_ARCHIVE_PREFIX
            or predecessor["verified_runtime_file_count"]
            != PREDECESSOR_RUNTIME_FILE_COUNT
            or predecessor["runtime_tree_sha256"]
            != PREDECESSOR_RUNTIME_TREE_SHA256
            or predecessor["sqlite_schema_version"] != SQLITE_SCHEMA_VERSION
            or predecessor["postgresql_schema_version"] != POSTGRES_SCHEMA_VERSION
            or transitions != {
                "sqlite": {"from": 11, "to": 12},
                "postgresql": {"from": 15, "to": 16},
            }
            or dependencies["collection_contract"]["canonical_sha256"]
            != CONTRACT_CANONICAL_SHA256
            or dependencies["source_contract"]["canonical_sha256"]
            != SOURCE_CONTRACT_CANONICAL_SHA256
            or len(contract["collection_profiles"]) != 4
            or storage["tables"] != list(PLANNED_TABLES)
            or set(storage["altered_tables"]) != {"gateway_audit_chain_entries"}
            or storage["audit_source_schemas"] != AUDIT_SOURCE_SCHEMAS
            or contract["audit_source_schemas"] != AUDIT_SOURCE_SCHEMAS
            or "partial_overlap_never_supersedes" not in snapshot["whole_snapshot_supersession"]
            or "within_one_organization_binding_id_and_version_and_collection_profile"
            not in snapshot["overlap"]
            or "exact_provider_native_bucket_start_and_end" not in snapshot["overlap"]
            or "nonidentical_bucket_intervals" not in snapshot["nonidentical_overlap"]
            or "exact_provider_native_bucket_start_and_end" not in observation["granularity"]
            or "including_organization_binding_id_and_version_collection_profile_and_query_window"
            not in snapshot["content_digest"]
            or "excluding_attempt_identity_page_size_page_boundaries_cursors_and_page_chain_mechanics"
            not in snapshot["content_digest"]
            or "including_requested_page_size_and_returned_page_boundaries_or_counts"
            not in snapshot["page_chain_digest"]
            or "typed_bucket_coverage_observations"
            not in snapshot["content_digest"]
            or "bucket_coverage"
            not in snapshot["selection"]
            or "no_observation_coverage_suppresses"
            not in snapshot["empty_bucket_selection"]
            or "never_zero" not in snapshot["empty_bucket_selection"]
            or bucket_coverage["table"] != PLANNED_TABLES[4]
            or bucket_coverage["states"] != ["observed", "no_observation"]
            or "newest_complete_snapshot_from_coverage_first"
            not in bucket_coverage["selection_authority"]
            or "no_observation_requires_count_zero"
            not in bucket_coverage["empty"]
            or bucket_coverage["numeric_zero_claim"] is not False
            or numeric_domains["validation_order"]
            != "reject_before_canonical_digest_idempotency_comparison_or_persistence"
            or numeric_domains["source_numeric_lexeme_maximum_bytes"] != 128
            or money_domain != {
                "type": "finite_exact_decimal",
                "minimum_exclusive": "-1000000000000000000",
                "maximum_exclusive": "1000000000000000000",
                "maximum_integer_digits": 18,
                "maximum_fractional_digits": 18,
                "maximum_significant_digits": 36,
                "normalized_nonzero_exponent_minimum": -18,
                "normalized_nonzero_exponent_maximum": 17,
                "provider_native_and_canonical_major_value_must_each_fit": True,
                "rounding": "forbidden",
            }
            or quantity_domain != {
                "type": "finite_exact_decimal",
                "minimum_exclusive": "-1000000000000000000",
                "maximum_exclusive": "1000000000000000000",
                "maximum_integer_digits": 18,
                "maximum_fractional_digits": 18,
                "maximum_significant_digits": 36,
                "normalized_nonzero_exponent_minimum": -18,
                "normalized_nonzero_exponent_maximum": 17,
                "provider_native_value_must_fit": True,
                "unit_handling": "retain_allowlisted_quantity_unit_or_null_no_conversion",
                "rounding": "forbidden",
            }
            or usage_count_domain != {
                "type": "integer_not_boolean",
                "minimum": 0,
                "maximum": 9223372036854775807,
                "derived_sum_overflow": "reject",
            }
            or coverage_count_domain != {
                "type": "integer_not_boolean",
                "minimum": 0,
                "maximum": 4096,
            }
            or "all_seven_collection_tables_reject_update_and_delete"
            not in storage["mutation_protection"]
            or "PostgreSQL_rejects_TRUNCATE"
            not in storage["mutation_protection"]
            or "SQLite_rejects_INSERT_OR_REPLACE"
            not in storage["mutation_protection"]
            or compatibility["new_http_routes"]
            or compatibility["preflight_new_cli_commands"]
            or plan["provider_collection_implemented"] is not False
            or plan["provider_import_implemented"] is not False
            or plan["reconciliation_implemented"] is not False
            or plan["finance_implemented"] is not False
        ):
            _fail("finance_collection_contract_changed")
    except FinanceCollectionTransitionError:
        raise
    except (ImportError, KeyError, TypeError):
        _fail("finance_collection_source_invalid")

    return {
        "schema_id": "hormuz.finance-transition-plan",
        "schema_version": 5,
        "status": "finance_collection_preflight_candidate_verified",
        "target_release": "1.1.0",
        "feature_issue": 8,
        "gate_issue": 214,
        "predecessor_source_commit": PREDECESSOR_SOURCE_COMMIT,
        "predecessor_archive_sha256": PREDECESSOR_ARCHIVE_SHA256,
        "predecessor_runtime_file_count": PREDECESSOR_RUNTIME_FILE_COUNT,
        "predecessor_runtime_tree_sha256": PREDECESSOR_RUNTIME_TREE_SHA256,
        "current_sqlite_schema_version": 11,
        "current_postgresql_schema_version": 15,
        "planned_sqlite_schema_version": 12,
        "planned_postgresql_schema_version": 16,
        "collection_profile_count": 4,
        "planned_table_count": len(PLANNED_TABLES),
        "planned_altered_table_count": 1,
        "planned_audit_source_count": 3,
        "native_attempt_runtime_source_verified": bool(
            native["native_request_cost_capture_implemented"]
        ),
        "collection_preflight_accepted": False,
        "provider_collection_implemented": False,
        "provider_import_implemented": False,
        "reconciliation_implemented": False,
        "finance_implemented": False,
        "live_finance_verified": False,
        "final_candidate_accepted": False,
        "released": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_finance_collection_transition_plan()
        if args.predecessor_archive is not None:
            verify_predecessor_archive(args.predecessor_archive)
        result["predecessor_archive_verified"] = (
            args.predecessor_archive is not None
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except FinanceCollectionTransitionError as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
