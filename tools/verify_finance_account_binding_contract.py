#!/usr/bin/env python3
"""Verify the owner-approved internal contract; not runtime/transition proof."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_finance_collection_runtime import _canonical_digest, _read_json

CONTRACT_PATH = "docs/finance-account-binding-contract-v1.json"
CONTRACT_SHA256 = "0941c9a8bee1f9653b5de776f5707515b635c437f6e13f3a902c8fbd51c2f271"
REQUIRED_FILES = (
    CONTRACT_PATH,
    "docs/FINANCE_RECONCILIATION_DESIGN.md",
    "docs/evidence/reconciliation_scope_probe.py",
    "tools/verify_finance_account_binding_contract.py",
    "tests/test_finance_account_binding_contract.py",
    "tests/test_finance_account_binding_transition_preflight.py",
)


def validate_contract(value):
    if _canonical_digest(value) != CONTRACT_SHA256:
        raise ValueError("finance_account_binding_contract_changed")


def verify(root=ROOT):
    root = Path(root)
    if any(not (root / name).is_file() for name in REQUIRED_FILES):
        raise ValueError("finance_account_binding_source_kit_incomplete")
    value = _read_json(root, CONTRACT_PATH)
    validate_contract(value)
    return {
        "status": "finance_account_binding_internal_contract_verified",
        "contract_sha256": CONTRACT_SHA256,
        "proof_scope": "frozen_contract_only_not_runtime_or_transition_evidence",
        "gates": value["gates"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
