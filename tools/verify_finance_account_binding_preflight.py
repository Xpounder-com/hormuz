#!/usr/bin/env python3
"""Verify the account-binding plan and artifacts; never infer runtime acceptance."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_finance_collection_runtime import _read_json
from tools.verify_finance_account_binding_contract import verify as verify_scope
from tests._finance_account_binding_predecessor_fixture import (
    ARCHIVE_PREFIX, SOURCE_COMMIT, SOURCE_SHA256, WHEEL_SHA256, RUNTIME_FILE_COUNT,
)

PLAN_PATH = "docs/finance-transition-plan-v8.json"
PLAN_SHA256 = "8ac15c609136da28ea152b4d16d0baba4c82b38b3cdada09546f94f52435f187"
REQUIRED_FILES = (
    PLAN_PATH, "docs/FINANCE_ACCOUNT_BINDING_TRANSITION.md",
    "tools/verify_finance_account_binding_preflight.py",
    "tests/_finance_account_binding_predecessor_fixture.py",
    "tests/test_finance_account_binding_preflight.py",
    "tests/test_finance_account_binding_transition_preflight.py",
)


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_plan(value):
    if canonical_digest(value) != PLAN_SHA256:
        raise ValueError("account_binding_preflight_plan_changed")


def runtime_tree(root):
    return {"hormuz/" + p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def _bound_file(path, expected, code):
    with Path(path).open("rb") as stream:
        payload = stream.read(32 * 1024 * 1024 + 1)
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError(code)
    return payload


def verify_artifacts(source, wheel):
    source_bytes = _bound_file(source, SOURCE_SHA256, "account_binding_predecessor_source_mismatch")
    wheel_bytes = _bound_file(wheel, WHEEL_SHA256, "account_binding_predecessor_wheel_mismatch")
    with tarfile.open(fileobj=io.BytesIO(source_bytes), mode="r:gz") as archive:
        expected = {m.name[len(ARCHIVE_PREFIX):]: archive.extractfile(m).read()
                    for m in archive.getmembers() if m.isfile() and m.name.startswith(ARCHIVE_PREFIX + "hormuz/")}
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
        actual = {name: archive.read(name) for name in archive.namelist()
                  if name.startswith("hormuz/") and not name.endswith("/")}
    if len(expected) != RUNTIME_FILE_COUNT or actual != expected:
        raise ValueError("account_binding_predecessor_runtime_mismatch")
    return {name: hashlib.sha256(payload).hexdigest() for name, payload in actual.items()}


def verify(root=ROOT, *, predecessor_source=None, predecessor_wheel=None):
    root = Path(root)
    if any(not (root / name).is_file() for name in REQUIRED_FILES):
        raise ValueError("account_binding_preflight_source_kit_incomplete")
    verify_scope(root)
    plan = _read_json(root, PLAN_PATH)
    validate_plan(plan)
    expected = plan["predecessor"]
    if (expected["source_commit"], expected["source_sha256"], expected["wheel_sha256"],
        expected["runtime_file_count"]) != (SOURCE_COMMIT, SOURCE_SHA256, WHEEL_SHA256, RUNTIME_FILE_COUNT):
        raise ValueError("account_binding_preflight_driver_binding_changed")
    runtime = runtime_tree(root / "hormuz")
    if len(runtime) != RUNTIME_FILE_COUNT or canonical_digest(runtime) != expected["runtime_tree_sha256"]:
        raise ValueError("account_binding_preflight_runtime_changed")
    for name, digest in plan["frozen_file_sha256"].items():
        if not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            raise ValueError("account_binding_preflight_frozen_history_changed")
    if (predecessor_source is None) != (predecessor_wheel is None):
        raise ValueError("account_binding_preflight_requires_both_artifacts")
    if predecessor_source is not None:
        if verify_artifacts(predecessor_source, predecessor_wheel) != runtime:
            raise ValueError("account_binding_preflight_predecessor_runtime_changed")
    return {
        "status": "account_binding_preflight_plan_verified", "plan_sha256": PLAN_SHA256,
        "runtime_files_verified": len(runtime), "published_artifacts_verified": predecessor_source is not None,
        "proof_scope": "static_plan_and_artifact_binding_transition_execution_required_separately",
        "gates": plan["gates"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor-source", type=Path)
    parser.add_argument("--predecessor-wheel", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(predecessor_source=args.predecessor_source,
                            predecessor_wheel=args.predecessor_wheel), sort_keys=True))
