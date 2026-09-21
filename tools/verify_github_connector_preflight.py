#!/usr/bin/env python3
"""Verify the frozen offline GitHub proposal; never report connector acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = "docs/github-connector-preflight-v1.json"
PLAN_SHA256 = "08c3c7115f70815aa0d29285a06eef7d8e3fc933a93ee9e2ec4113a7a6d19442"
REQUIRED_FILES = (
    PLAN_PATH,
    "docs/GITHUB_CONNECTOR_PREFLIGHT.md",
    "tools/verify_github_connector_preflight.py",
    "tests/test_github_connector_preflight.py",
    "tests/test_github_connector_fixtures.py",
    "tests/fixtures/connectors/github/cases.json",
)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("github_preflight_duplicate_key")
        result[key] = value
    return result


def _read_json(path: Path):
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("github_preflight_input_missing_or_oversized")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("github_preflight_nonfinite")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("github_preflight_invalid_json") from error


def _canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def verify(root: Path = ROOT) -> dict:
    root = Path(root)
    if any(not (root / name).is_file() for name in REQUIRED_FILES):
        raise ValueError("github_preflight_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if _canonical_digest(plan) != PLAN_SHA256:
        raise ValueError("github_preflight_plan_changed")
    if plan["status"] != "proposed_offline_preimplementation_not_accepted" or plan["gates"] != {
        "feature_preflight_accepted": False,
        "runtime_implemented": False,
        "live_integration_verified": False,
        "final_candidate_accepted": False,
        "release_authorized": False,
    } or plan["compatibility"]["connector_owned_migration_number"] is not None:
        raise ValueError("github_preflight_gate_claim_changed")
    for name, digest in plan["frozen_inputs_sha256"].items():
        path = root / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("github_preflight_frozen_input_changed")
    fixture = _read_json(root / "tests/fixtures/connectors/github/cases.json")
    cases = fixture["cases"]
    if fixture["fixture_pack"] != plan["normalization_decisions"]["fixture_pack"] or (
        len(cases) != len(plan["normalization_decisions"]["pending_case_ids"]) or
        {case["case_id"] for case in cases} != set(plan["normalization_decisions"]["pending_case_ids"])
    ):
        raise ValueError("github_preflight_fixture_inventory_changed")
    if any(case["expectation"]["kind"] not in {"mapping_pending", "invalid_projection"} or
           (candidate := case["expectation"].get("candidate_observation")) is not None and
           candidate.get("proposal_only") is not True for case in cases):
        raise ValueError("github_preflight_fixture_promoted_without_review")
    return {
        "status": "github_connector_offline_preflight_proposal_verified",
        "plan_sha256": PLAN_SHA256,
        "fixture_cases_pending": len(cases),
        "feature_preflight_accepted": plan["gates"]["feature_preflight_accepted"],
        "runtime_implemented": plan["gates"]["runtime_implemented"],
        "live_integration_verified": plan["gates"]["live_integration_verified"],
        "final_candidate_accepted": plan["gates"]["final_candidate_accepted"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
