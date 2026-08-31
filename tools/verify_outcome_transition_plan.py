#!/usr/bin/env python3
"""Verify #218's historical and implementation plans, never feature acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_attribution_transition_plan import AttributionTransitionError, verify_attribution_transition_plan
from tools.verify_registry_transition_plan import RegistryTransitionError, verify_released_baseline


PLAN_CANONICAL_SHA256 = "e040fba24a09d698e025c8887594789a70d6d9e23d51040189f2cf0bc4f7193c"
IMPLEMENTATION_CANONICAL_SHA256 = "5b7357a0bd2730048355cd6d41667fa1fdf3aac35788886ada2f671e75b01d03"
ATTRIBUTION_ARCHIVE_SHA256 = "c838abe03ff4ceba5145da37cfafe1c4b81a2dc64a88ed7c9a503f5fda742d72"
ATTRIBUTION_SOURCE_COMMIT = "ade456b90a3f065ebee5b51893dbf111e815ff05"


class OutcomeTransitionError(ValueError):
    """Fixed content-free checkpoint diagnostics."""


def validate_outcome_transition_plan(value: object, *, schema_version: int = 1) -> None:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise OutcomeTransitionError("outcome_plan_invalid") from None
    expected = {1: PLAN_CANONICAL_SHA256, 2: IMPLEMENTATION_CANONICAL_SHA256}.get(schema_version)
    if hashlib.sha256(payload).hexdigest() != expected:
        raise OutcomeTransitionError("outcome_preflight_contract_changed")


def _read_plan(root: Path, version: int) -> None:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise OutcomeTransitionError("outcome_plan_duplicate_member")
            result[key] = value
        return result

    try:
        with (root / f"docs/outcome-transition-plan-v{version}.json").open("rb") as source:
            payload = source.read(32769)
        if len(payload) > 32768:
            raise OutcomeTransitionError("outcome_plan_too_large")
        validate_outcome_transition_plan(json.loads(payload, object_pairs_hook=unique), schema_version=version)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise OutcomeTransitionError("outcome_plan_unreadable") from None


def verify_outcome_transition_plan(root: Path = ROOT) -> dict[str, object]:
    _read_plan(root, 1)
    try:
        baseline = verify_attribution_transition_plan(root)
    except (AttributionTransitionError, RegistryTransitionError):
        raise OutcomeTransitionError("outcome_baseline_contract_invalid") from None
    return {
        "schema_id": "hormuz.outcome-transition-plan", "schema_version": 1,
        "status": "outcome_preflight_plan_verified", "target_release": "1.1.0",
        "feature_issue": 218, "outcome_route_count": 1, "connector_routes_activated": 0,
        "attribution_source_commit": ATTRIBUTION_SOURCE_COMMIT,
        "attribution_archive_sha256": ATTRIBUTION_ARCHIVE_SHA256,
        "baseline_archive_sha256": baseline["baseline_archive_sha256"],
        "outcome_implemented": False, "final_candidate_accepted": False,
    }


def verify_outcome_implementation_plan(root: Path = ROOT) -> dict[str, object]:
    _read_plan(root, 2)
    baseline = verify_outcome_transition_plan(root)
    return {
        **baseline, "schema_version": 2, "status": "outcome_implementation_plan_verified",
        "outcome_implemented": True, "final_candidate_accepted": False,
        "preflight_main_commit": "9af53c79d1671638a57dba9d758482c7d4f88ef8", "outcome_table_count": 9,
    }


def verify_attribution_archive(path: Path) -> None:
    try:
        with path.open("rb") as source:
            payload = source.read(32 * 1024 * 1024 + 1)
        if (len(payload) > 32 * 1024 * 1024
                or hashlib.sha256(payload).hexdigest() != ATTRIBUTION_ARCHIVE_SHA256):
            raise OutcomeTransitionError("outcome_attribution_archive_invalid")
    except OSError:
        raise OutcomeTransitionError("outcome_attribution_archive_invalid") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--attribution-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        if (args.baseline_archive is None) != (args.baseline_manifest is None):
            raise OutcomeTransitionError("outcome_baseline_pair_required")
        result = verify_outcome_implementation_plan()
        if args.baseline_archive is not None:
            verify_released_baseline(args.baseline_archive, args.baseline_manifest)
        if args.attribution_archive is not None:
            verify_attribution_archive(args.attribution_archive)
        result["released_baseline_archive_verified"] = args.baseline_archive is not None
        result["attribution_baseline_archive_verified"] = args.attribution_archive is not None
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OutcomeTransitionError, RegistryTransitionError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
