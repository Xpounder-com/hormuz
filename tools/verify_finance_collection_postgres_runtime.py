#!/usr/bin/env python3
"""Verify the fixed schema-17 collection candidate; never infer acceptance."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_finance_collection_runtime import (
    FinanceCollectionRuntimeError, _canonical_digest, _read_json,
    verify_finance_collection_runtime,
)

PLAN_PATH = "docs/finance-transition-plan-v7.json"
PLAN_SHA256 = "592cdae35324579c5329cee1f896e1b0c2fb83917e6f6175bb3e5a72c9234d78"
PREDECESSOR_ARCHIVE_SHA256 = "d7e6a0287ed8e527d365a121213eaa28e96a92f5b0e9f0e4caf67034dc3c0ae2"
EXPECTED_ACL = (199, "1fa41892fb1206e7e70b922768ac27a39fce6ed98441a9fb78ce1511e1582906")
INJECTED_ACL = (200, "36722069d266896d947c5d0d96f4e3033f7992244d26a5920d2c5cf7f5470296")
REQUIRED_FILES = (
    PLAN_PATH,
    "docs/FINANCE_COLLECTION_POSTGRES_RUNTIME.md",
    "hormuz/migrations/postgresql/0017_finance_collection_runtime.sql",
    "tools/verify_finance_collection_postgres_runtime.py",
    "tests/_finance_collection_runtime_predecessor_fixture.py",
    "tests/test_postgres_finance_collection_runtime.py",
    "tests/test_postgres_finance_collection_runtime_transition.py",
    "tests/test_finance_collection_postgres_runtime_plan.py",
)


def validate_plan(value):
    if _canonical_digest(value) != PLAN_SHA256:
        raise FinanceCollectionRuntimeError("finance_collection_postgres_plan_changed")


def verify_predecessor_archive(path):
    with Path(path).open("rb") as source:
        payload = source.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != PREDECESSOR_ARCHIVE_SHA256:
        raise FinanceCollectionRuntimeError("finance_collection_postgres_predecessor_changed")


def verify(root=ROOT, *, predecessor_archive=None):
    root = Path(root)
    for path in REQUIRED_FILES:
        if not (root / path).is_file():
            raise FinanceCollectionRuntimeError("finance_collection_postgres_source_kit_incomplete")
    plan = _read_json(root, PLAN_PATH)
    validate_plan(plan)
    # The old v6 manifest and schema-16 migration stay frozen, not rewritten to
    # pretend the newly approved ACL was part of the earlier accepted preflight.
    verify_finance_collection_runtime(root, allow_successor_schema=True)
    from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
    from hormuz.postgres import POSTGRES_SCHEMA_VERSION, _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION
    from hormuz.finance_collection_repository import (
        POSTGRES_FINANCE_COLLECTION_RUNTIME_ENABLED,
        POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED,
    )
    if (
        (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION) != (12, 17)
        or _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION.get(17) != EXPECTED_ACL
        or POSTGRES_FINANCE_COLLECTION_RUNTIME_ENABLED is not True
        or POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED is not False
    ):
        raise FinanceCollectionRuntimeError("finance_collection_postgres_runtime_boundary_changed")
    for path, digest in (
        (plan["migration"]["path"], plan["migration"]["sha256"]),
        ("hormuz/migrations/postgresql/0016_finance_collection.sql", plan["migration"]["predecessor_migration_sha256"]),
    ):
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise FinanceCollectionRuntimeError("finance_collection_postgres_migration_changed")
    if predecessor_archive is not None:
        verify_predecessor_archive(predecessor_archive)
    return {
        "status": "finance_collection_postgres_runtime_candidate_verified",
        "current_sqlite_schema_version": 12,
        "current_postgresql_schema_version": 17,
        "postgresql_acl_current": EXPECTED_ACL,
        "postgresql_acl_injected_rejected": INJECTED_ACL,
        "gates": plan["gates"],
        "predecessor_archive_verified": predecessor_archive is not None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--predecessor-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify(args.repo_root, predecessor_archive=args.predecessor_archive), sort_keys=True))
        return 0
    except (FinanceCollectionRuntimeError, OSError) as error:
        print(str(error) if isinstance(error, FinanceCollectionRuntimeError) else "finance_collection_postgres_source_unreadable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
