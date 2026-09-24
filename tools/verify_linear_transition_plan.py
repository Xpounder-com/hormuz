#!/usr/bin/env python3
"""Verify the assigned Linear successor plan without installing a migration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hormuz._sqlite_schema import SQLITE_SCHEMA_VERSION
from hormuz.postgres import POSTGRES_SCHEMA_VERSION


PLAN_PATH = "docs/linear-transition-plan-v1.json"
PLAN_SHA256 = "681633994ba4c826e99f826730f92cfad1ac1e4bed6fc730a650caf2ad0c699f"
SQLITE_PROPOSAL = "docs/linear-successor-schema-proposal.sqlite.sql"
POSTGRES_PROPOSAL = "docs/linear-successor-schema-acl-proposal.sql"
REQUIRED_FILES = (
    PLAN_PATH,
    "docs/LINEAR_CONNECTOR_TRANSITION.md",
    SQLITE_PROPOSAL,
    POSTGRES_PROPOSAL,
    "tools/verify_linear_transition_plan.py",
    "tests/test_linear_transition_plan.py",
    "tests/test_linear_transition_preflight.py",
)
PROPOSAL_TABLES = (
    "portfolio_linear_source_binding_versions",
    "gateway_linear_delivery_receipts",
    "portfolio_linear_context_events",
    "portfolio_linear_context_retention_events",
)
TRANSITION_CASES = {
    "missing_successor_refusal",
    "ddl_failure_rollback_retry",
    "old_binary_partial_newer_refusal",
    "quiesced_old_pair_restore",
    "post_checkpoint_forward_recovery",
    "receipt_replay_and_concurrent_conflict",
    "durable_ack_outage_and_deadline",
}
EXPECTED_GATES = {
    "schema_versions_assigned": True,
    "transition_harness_implemented": True,
    "published_predecessor_source_wheel_matrix_passed": True,
    "successor_acl_measured": True,
    "exact_storage_and_audit_contract_frozen": False,
    "receipt_concurrency_runtime_proven": False,
    "durable_ack_HTTP_budget_proven": False,
    "connector_preimplementation_checkpoint_accepted": False,
    "runtime_implemented": False,
    "live_workspace_authorized": False,
    "live_delivery_verified": False,
    "final_candidate_accepted": False,
    "released": False,
}


class LinearTransitionPlanError(ValueError):
    """A fixed, content-free transition-plan refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise LinearTransitionPlanError("linear_transition_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 1024 * 1024:
            raise LinearTransitionPlanError("linear_transition_plan_invalid")
        return json.loads(payload, object_pairs_hook=_unique_object)
    except LinearTransitionPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise LinearTransitionPlanError("linear_transition_plan_invalid") from None


def canonical_digest(value) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise LinearTransitionPlanError("linear_transition_plan_invalid") from None
    return hashlib.sha256(payload).hexdigest()


def _proposal_boundary(root: Path, relative: str, *, postgresql: bool) -> None:
    try:
        payload = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise LinearTransitionPlanError("linear_transition_source_kit_incomplete") from None
    if len(payload.encode("utf-8")) > 1024 * 1024 or not payload.startswith("-- REVIEW ONLY:"):
        raise LinearTransitionPlanError("linear_transition_proposal_invalid")
    for table in PROPOSAL_TABLES:
        prefix = "{schema}." if postgresql else ""
        if len(re.findall(rf"CREATE TABLE {re.escape(prefix + table)}\s*\(", payload)) != 1:
            raise LinearTransitionPlanError("linear_transition_proposal_invalid")
        if postgresql:
            required = (
                f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
                f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
                f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
            )
            if any(item not in payload for item in required):
                raise LinearTransitionPlanError("linear_transition_proposal_invalid")
        else:
            if any(
                f"CREATE TRIGGER {table}_{suffix}" not in payload
                for suffix in ("no_update", "no_delete")
            ):
                raise LinearTransitionPlanError("linear_transition_proposal_invalid")
    receipt = re.search(
        r"CREATE TABLE (?:\{schema\}\.)?gateway_linear_delivery_receipts\s*\((.*?)\n\);",
        payload,
        re.DOTALL,
    )
    if receipt is None or any(
        field not in receipt.group(1)
        for field in (
            "body_fingerprint",
            "fingerprint_key_version",
            "source_fact_fingerprint",
            "source_fact_key_version",
        )
    ) or any(
        field in receipt.group(1)
        for field in ("source_workspace_id", "source_webhook_id")
    ):
        raise LinearTransitionPlanError("linear_transition_proposal_invalid")
    forbidden = (
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT TRUNCATE",
        "WITH GRANT OPTION",
        "ALTER TABLE HORMUZ_",
        "INSERT INTO HORMUZ_SCHEMA_MIGRATIONS",
    )
    if any(item in payload.upper() for item in forbidden):
        raise LinearTransitionPlanError("linear_transition_proposal_invalid")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        raise LinearTransitionPlanError("linear_transition_source_kit_incomplete")
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        raise LinearTransitionPlanError("linear_transition_plan_changed")
    if (
        plan.get("schema_id") != "hormuz.linear-transition-plan"
        or plan.get("schema_version") != 1
        or plan.get("stage") != "assigned_successor_transition_preflight"
        or plan.get("target_release") != "1.3.0"
        or plan.get("feature_issue") != 220
        or plan.get("gate_issue") != 214
        or plan.get("base_main_commit") != "72df12391fa33c7a53d45fabf4899c1346139196"
    ):
        raise LinearTransitionPlanError("linear_transition_plan_invalid")
    if plan.get("integrated_baseline", {}).get("commit") != plan["base_main_commit"]:
        raise LinearTransitionPlanError("linear_transition_plan_invalid")
    if plan.get("transitions") != {
        "sqlite": {"from": 13, "to": 14},
        "postgresql": {"from": 18, "to": 19},
    }:
        raise LinearTransitionPlanError("linear_transition_schema_assignment_invalid")
    if set(plan.get("required_transition_cases", {})) != TRANSITION_CASES:
        raise LinearTransitionPlanError("linear_transition_plan_invalid")
    if plan.get("gates") != EXPECTED_GATES:
        raise LinearTransitionPlanError("linear_transition_gate_overclaim")
    frozen = plan.get("frozen_file_sha256")
    if not isinstance(frozen, dict) or set(frozen) != {
        "docs/linear-connector-preflight-v1.json",
        "docs/linear-context-wire-v1.json",
        "docs/portfolio-extension-contract-v1.json",
        "docs/portfolio-intelligence-wire-v1.json",
        "hormuz/portfolio-outcome-wire-v1.json",
        SQLITE_PROPOSAL,
        POSTGRES_PROPOSAL,
    }:
        raise LinearTransitionPlanError("linear_transition_plan_invalid")
    for relative, expected in frozen.items():
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            raise LinearTransitionPlanError("linear_transition_source_kit_incomplete") from None
        if not isinstance(expected, str) or actual != expected:
            raise LinearTransitionPlanError("linear_transition_frozen_file_changed")
    _proposal_boundary(root, SQLITE_PROPOSAL, postgresql=False)
    _proposal_boundary(root, POSTGRES_PROPOSAL, postgresql=True)
    runtime_successor_verified = False
    versions = (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION)
    if versions in {(14, 19), (15, 20), (16, 21), (17, 22)}:
        try:
            from tools.verify_linear_runtime_plan import verify as verify_runtime

            verify_runtime(root)
        except Exception:
            raise LinearTransitionPlanError(
                "linear_transition_baseline_changed"
            ) from None
        runtime_successor_verified = True
    elif versions != (13, 18):
        raise LinearTransitionPlanError("linear_transition_baseline_changed")
    return {
        "status": "linear_transition_plan_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_transition": [13, 14],
        "postgresql_transition": [18, 19],
        "proposal_tables": len(PROPOSAL_TABLES),
        "runtime_implemented": False,
        "runtime_successor_verified": runtime_successor_verified,
        "live_workspace_authorized": False,
        "gates": plan["gates"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
