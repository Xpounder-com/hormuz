#!/usr/bin/env python3
"""Verify #8's compatibility/source checkpoint, never live finance acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_attribution_transition_plan import AttributionTransitionError
from tools.verify_outcome_transition_plan import OutcomeTransitionError, verify_outcome_implementation_plan
from tools.verify_registry_transition_plan import RegistryTransitionError, verify_released_baseline


PLAN_CANONICAL_SHA256 = "0036081d73af0a7094506aca159fd0adfafb2b16f88a43ac260f9d8809d64ab1"
SOURCES_CANONICAL_SHA256 = "290def8f2cd7026d4e0f0512db9254906f8592a026ee4beb9cac3623d7a1d9f4"
OUTCOME_SOURCE_COMMIT = "aa648edf64df9f4a0c426ad73a95852f11561099"
OUTCOME_ARCHIVE_SHA256 = "fbbe1178607a38c5f390b96c646f5a93414523b37f553abb7d830d42f30ff056"


class FinanceTransitionError(ValueError):
    """Fixed, content-free checkpoint diagnostics."""


def _validate(value: object, digest: str, code: str) -> None:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise FinanceTransitionError("finance_plan_invalid") from None
    if hashlib.sha256(payload).hexdigest() != digest:
        raise FinanceTransitionError(code)


def validate_finance_plan(value: object) -> None:
    _validate(value, PLAN_CANONICAL_SHA256, "finance_preflight_contract_changed")


def validate_finance_sources(value: object) -> None:
    _validate(value, SOURCES_CANONICAL_SHA256, "finance_source_contract_changed")


def _read(root: Path, filename: str, validate) -> None:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise FinanceTransitionError("finance_plan_duplicate_member")
            result[key] = value
        return result

    try:
        with (root / "docs" / filename).open("rb") as source:
            payload = source.read(65537)
        if len(payload) > 65536:
            raise FinanceTransitionError("finance_plan_too_large")
        validate(json.loads(payload, object_pairs_hook=unique))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise FinanceTransitionError("finance_plan_unreadable") from None


def verify_finance_transition_plan(root: Path = ROOT) -> dict[str, object]:
    _read(root, "finance-transition-plan-v1.json", validate_finance_plan)
    _read(root, "finance-source-contract-v1.json", validate_finance_sources)
    try:
        # This verifier also requires both implemented attribution and registry plans.
        baseline = verify_outcome_implementation_plan(root)
    except (RegistryTransitionError, AttributionTransitionError, OutcomeTransitionError):
        raise FinanceTransitionError("finance_baseline_contract_invalid") from None
    return {
        "schema_id": "hormuz.finance-transition-plan", "schema_version": 1,
        "status": "finance_preflight_plan_verified", "target_release": "1.1.0", "feature_issue": 8,
        "new_http_routes": 0, "provider_count": 2,
        "outcome_source_commit": OUTCOME_SOURCE_COMMIT, "outcome_archive_sha256": OUTCOME_ARCHIVE_SHA256,
        "baseline_archive_sha256": baseline["baseline_archive_sha256"],
        "finance_implemented": False, "live_finance_verified": False, "final_candidate_accepted": False,
    }


def verify_outcome_archive(path: Path) -> None:
    try:
        with path.open("rb") as source:
            payload = source.read(32 * 1024 * 1024 + 1)
        if len(payload) > 32 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != OUTCOME_ARCHIVE_SHA256:
            raise FinanceTransitionError("finance_outcome_archive_invalid")
    except OSError:
        raise FinanceTransitionError("finance_outcome_archive_invalid") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path)
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--outcome-archive", type=Path)
    args = parser.parse_args(argv)
    try:
        if (args.baseline_archive is None) != (args.baseline_manifest is None):
            raise FinanceTransitionError("finance_baseline_pair_required")
        result = verify_finance_transition_plan()
        if args.baseline_archive is not None:
            verify_released_baseline(args.baseline_archive, args.baseline_manifest)
        if args.outcome_archive is not None:
            verify_outcome_archive(args.outcome_archive)
        result["released_baseline_archive_verified"] = args.baseline_archive is not None
        result["outcome_baseline_archive_verified"] = args.outcome_archive is not None
        print(json.dumps(result, sort_keys=True))
        return 0
    except (FinanceTransitionError, RegistryTransitionError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
