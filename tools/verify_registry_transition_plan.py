#!/usr/bin/env python3
"""Validate the versioned #215 transition plan, never candidate acceptance.

rollback_disposition is an offline decision table for supplied checkpoint facts.
It neither verifies those facts nor performs migrations, backups, or restores.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = {
    "release": "v1.0.0",
    "source_commit": "2fc0605252e41f731c85cc9146fbff6eb3b34669",
    "archive_name": "hormuz-1.0.0.tar.gz",
    "archive_sha256": "2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a",
    "manifest_sha256": "85774aa45a8b30be88d1cb1a7b543222cc1396523aec31c17de07470b09d56b2",
    "contract_manifest_sha256": "6e7f264de74f8f998842b49001a61b3640a05927d74bb31e4eec5ff8ab89504f",
}
COMPATIBILITY = {
    "existing_v1_behavior": "unchanged",
    "legacy_manifest": "preserve_including_historical_release_line",
    "provider_replay": "never_automatic",
    "existing_rows": "no_rewrite_or_backfill",
    "uncertain_reservations": "retain_until_explicit_reconciliation",
    "migration_execution": "serialized_operator_with_writers_quiesced",
    "registry_wire_sha256": "26ef36b12d475d5b8354e45abfc92c76187ca6ff5aafb4b0ebce6fab439feb80",
}
ROLLBACK = {
    "old_binary_on_new_schema": "storage_schema_newer_than_binary",
    "partial_schema": "storage_schema_partial_upgrade",
    "in_place_downgrade": False,
    "before_writes": "restore_verified_pair_before_resuming_writes",
    "after_writes": "preserve_candidate_and_recover_forward",
    "unknown_write_count": "preserve_candidate_and_recover_forward",
    "restore_destination": "new_isolated_destination",
    "retain_candidate_snapshot": True,
}
REGISTRY_ROUTES = [
    "GET /v1/admin/portfolio/work-bindings",
    "GET /v1/admin/portfolio/work-scopes",
    "GET /v1/admin/portfolio/work-scopes/{work_scope_id}",
    "POST /v1/admin/portfolio/work-bindings",
    "POST /v1/admin/portfolio/work-scopes",
    "POST /v1/admin/portfolio/work-scopes/{work_scope_id}/versions",
]
REQUIRED_CASES = [
    "released_baseline_identity", "v1_rows_and_holds_preserved",
    "unimplemented_registry_migration_red", "transaction_failure_and_retry_probe",
    "partial_state_fails_closed", "released_binary_rejects_newer_state",
    "quiesced_verified_pair_restore", "writes_require_forward_recovery",
    "legacy_contract_preserved", "registry_wire_contract_frozen",
]
SCOPE = {
    "schema_id": "hormuz.registry-transition-plan", "schema_version": 1,
    "stage": "pre_implementation", "target_release": "1.1.0",
    "feature_issue": 215, "gate_issue": 214,
    "registry_implemented": False, "final_candidate_accepted": False,
}
IMPLEMENTATION_SCOPE = {**SCOPE, "schema_version": 2, "stage": "implementation_verification", "registry_implemented": True}
IMPLEMENTATION_CASES = [
    "actual_registry_migration_additive" if name == "unimplemented_registry_migration_red" else
    "registry_transaction_failure_and_retry" if name == "transaction_failure_and_retry_probe" else name
    for name in REQUIRED_CASES
]


class RegistryTransitionError(ValueError):
    """Content-free invalid-plan diagnostic."""


def _same(actual: object, expected: object) -> bool:
    # Exact types matter: True must not satisfy schema version 1 or write count 0.
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same(actual[key], value) for key, value in expected.items()
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def validate_registry_transition_plan(plan: object) -> None:
    implementation = isinstance(plan, dict) and type(plan.get("schema_version")) is int and plan["schema_version"] == 2
    scope = IMPLEMENTATION_SCOPE if implementation else SCOPE
    sections = {
        "baseline": (BASELINE, "baseline_binding_changed"),
        "transitions": ({"sqlite": {"from": 4, "to": 5}, "postgresql": {"from": 8, "to": 9}}, "schema_transition_changed"),
        "compatibility": (COMPATIBILITY, "transition_policy_changed"),
        "rollback": (ROLLBACK, "transition_policy_changed"),
        "registry_routes": (REGISTRY_ROUTES, "registry_routes_changed"),
        "required_cases": (IMPLEMENTATION_CASES if implementation else REQUIRED_CASES, "transition_cases_changed"),
    }
    if not isinstance(plan, dict) or set(plan) != set(SCOPE) | set(sections):
        raise RegistryTransitionError("transition_plan_fields_invalid")
    if any(not _same(plan[key], value) for key, value in scope.items()):
        raise RegistryTransitionError("preflight_scope_changed")
    for key, (expected, code) in sections.items():
        if not _same(plan[key], expected):
            raise RegistryTransitionError(code)


def rollback_disposition(checkpoint: object) -> str:
    flags = {"quiesced", "backup_verified", "candidate_snapshot_retained"}
    if not isinstance(checkpoint, dict) or set(checkpoint) != flags | {"post_checkpoint_writes"}:
        raise RegistryTransitionError("rollback_checkpoint_invalid")
    if any(type(checkpoint[key]) is not bool for key in flags):
        raise RegistryTransitionError("rollback_checkpoint_invalid")
    writes = checkpoint["post_checkpoint_writes"]
    if writes is not None and (type(writes) is not int or writes < 0):
        raise RegistryTransitionError("rollback_checkpoint_invalid")
    if not all(checkpoint[key] for key in flags):
        return "refuse_restore"
    if writes is None or writes > 0:
        return "preserve_candidate_and_recover_forward"
    return "restore_verified_pair_before_resuming_writes"


def verify_registry_transition_plan(root: Path = ROOT) -> dict[str, object]:
    from tools.verify_portfolio_intelligence_contract import (
        PortfolioIntelligenceContractError, validate_portfolio_intelligence_contract,
    )

    def unique_members(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RegistryTransitionError("transition_plan_duplicate_member")
            result[key] = value
        return result

    path = root / "docs/registry-transition-plan-v2.json"
    try:
        with path.open("rb") as source:
            payload = source.read(32769)
        if len(payload) > 32768:
            raise RegistryTransitionError("transition_plan_too_large")
        plan = json.loads(payload, object_pairs_hook=unique_members)
        validate_registry_transition_plan(plan)
        if plan["schema_version"] != 2:
            raise RegistryTransitionError("implementation_plan_required")
        for relative, digest in (
            ("tests/fixtures/portfolio_intelligence/v1.0.0-contract-manifest.json", BASELINE["contract_manifest_sha256"]),
            ("docs/portfolio-intelligence-wire-v1.json", COMPATIBILITY["registry_wire_sha256"]),
        ):
            if hashlib.sha256((root / relative).read_bytes()).hexdigest() != digest:
                raise RegistryTransitionError("transition_contract_binding_changed")
        contract = json.loads((root / "docs/portfolio-intelligence-contract-v1.json").read_text(encoding="utf-8"))
        if not set(REGISTRY_ROUTES).issubset(contract["api"]["route_contracts"]):
            raise RegistryTransitionError("registry_routes_changed")
        validate_portfolio_intelligence_contract(root)
    except PortfolioIntelligenceContractError as error:
        raise RegistryTransitionError("transition_contract_binding_changed") from error
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RegistryTransitionError("transition_plan_unreadable") from error
    return {
        "status": "registry_implementation_plan_verified", "feature_issue": 215,
        "target_release": "1.1.0", "registry_route_count": len(REGISTRY_ROUTES),
        "baseline_archive_sha256": BASELINE["archive_sha256"],
        "registry_implemented": True, "final_candidate_accepted": False,
    }


def verify_released_baseline(archive: Path, manifest: Path) -> None:
    from tools.v1_candidate import V1CandidateError, inspect_archive

    try:
        inspected = inspect_archive(archive)
        with manifest.open("rb") as source:
            manifest_bytes = source.read(256 * 1024 + 1)
        if (
            inspected["digest"] != "sha256:" + BASELINE["archive_sha256"]
            or len(manifest_bytes) > 256 * 1024
            or hashlib.sha256(manifest_bytes).hexdigest() != BASELINE["manifest_sha256"]
        ):
            raise RegistryTransitionError("released_baseline_digest_mismatch")
    except (OSError, V1CandidateError) as error:
        raise RegistryTransitionError("released_baseline_archive_invalid") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    args = parser.parse_args()
    if bool(args.baseline_archive) != bool(args.baseline_manifest):
        parser.error("baseline archive and manifest must be supplied together")
    try:
        summary = verify_registry_transition_plan()
        if args.baseline_archive:
            verify_released_baseline(args.baseline_archive, args.baseline_manifest)
        summary["released_baseline_archive_verified"] = bool(args.baseline_archive)
    except RegistryTransitionError as error:
        print(f"registry transition preflight failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
