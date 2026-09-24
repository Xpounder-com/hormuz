#!/usr/bin/env python3
"""Verify the assigned association successor plan without installing it."""

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


PLAN_PATH = "docs/association-transition-plan-v1.json"
PLAN_SHA256 = "36dc6f8b2b0b355e3a940b6e791732fab285ea515caf805d502bbd130c59f4d5"
BASE_MAIN_COMMIT = "8a2da39f03af3dd8ed2160b2b95286d829fdb40d"
BASE_MAIN_CI = "https://github.com/Xpounder-com/hormuz/actions/runs/35940004002"
SQLITE_PROPOSAL = "docs/association-successor-schema-proposal.sqlite.sql"
POSTGRES_PROPOSAL = "docs/association-successor-schema-acl-proposal.sql"
REQUIRED_FILES = (
    PLAN_PATH,
    "docs/ASSOCIATION_LINKAGE_PREFLIGHT.md",
    "docs/ASSOCIATION_TRANSITION.md",
    SQLITE_PROPOSAL,
    POSTGRES_PROPOSAL,
    "tools/verify_association_transition_plan.py",
    "tests/test_association_transition_plan.py",
    "tests/test_association_transition_preflight.py",
)
PROPOSAL_TABLES = (
    "portfolio_association_audit_events",
    "portfolio_run_work_link_events",
    "portfolio_run_work_link_idempotency",
    "portfolio_run_outcome_association_events",
    "portfolio_run_outcome_association_cursors",
)
TRANSITION_CASES = {
    "missing_successor_refusal",
    "ddl_failure_rollback_retry",
    "old_binary_partial_newer_refusal",
    "quiesced_old_pair_restore",
    "post_checkpoint_forward_recovery",
    "explicit_link_idempotency_and_CAS",
    "deterministic_association_replay",
    "late_conflict_retry_reopen_revert",
    "coverage_cost_and_content_scan",
}
EXPECTED_GATES = {
    "association_design_preflight_accepted": True,
    "schema_versions_assigned": True,
    "transition_harness_implemented": True,
    "published_predecessor_source_wheel_matrix_passed": True,
    "successor_acl_measured": True,
    "exact_storage_wire_and_audit_contract_frozen": False,
    "explicit_link_runtime_proven": False,
    "deterministic_association_runtime_proven": False,
    "coverage_and_cost_vectors_proven": False,
    "metadata_content_scans_proven": False,
    "runtime_implemented": False,
    "live_connectors_authorized": False,
    "live_multi_source_evidence_verified": False,
    "final_candidate_accepted": False,
    "released": False,
}
EXPECTED_ACL = {
    "entry_count": 252,
    "sha256": "a0296c1b3bdad58acfc2bd89c4c020fa6a0af75fba7ef895a798fb3ec4fcd3dc",
}


class AssociationTransitionPlanError(ValueError):
    """A fixed, content-free transition-plan refusal."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise AssociationTransitionPlanError("association_transition_plan_invalid")
        value[key] = item
    return value


def _read_json(path: Path):
    try:
        payload = path.read_bytes()
        if not 2 <= len(payload) <= 1024 * 1024:
            raise AssociationTransitionPlanError("association_transition_plan_invalid")
        return json.loads(payload, object_pairs_hook=_unique_object)
    except AssociationTransitionPlanError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AssociationTransitionPlanError(
            "association_transition_plan_invalid"
        ) from None


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
        raise AssociationTransitionPlanError(
            "association_transition_plan_invalid"
        ) from None
    return hashlib.sha256(payload).hexdigest()


def _table_body(payload: str, table: str, *, postgresql: bool) -> str:
    prefix = r"\{schema\}\." if postgresql else ""
    match = re.search(
        rf"CREATE TABLE {prefix}{re.escape(table)}\s*\((.*?)\n\);",
        payload,
        re.DOTALL,
    )
    if match is None:
        raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    return match.group(1)


def _proposal_boundary(root: Path, relative: str, *, postgresql: bool) -> None:
    try:
        payload = (root / relative).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise AssociationTransitionPlanError(
            "association_transition_source_kit_incomplete"
        ) from None
    if (
        len(payload.encode("utf-8")) > 1024 * 1024
        or not payload.startswith("-- REVIEW ONLY:")
    ):
        raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    bodies = {
        table: _table_body(payload, table, postgresql=postgresql)
        for table in PROPOSAL_TABLES
    }
    for table, body in bodies.items():
        if "organization_id" not in body:
            raise AssociationTransitionPlanError("association_transition_proposal_invalid")
        if postgresql:
            required = (
                f"ALTER TABLE {{schema}}.{table} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY",
                f"REVOKE ALL ON {{schema}}.{table} FROM PUBLIC",
                f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}}",
            )
            if any(item not in payload for item in required):
                raise AssociationTransitionPlanError(
                    "association_transition_proposal_invalid"
                )
        elif any(
            f"CREATE TRIGGER {table}_{suffix}" not in payload
            for suffix in ("no_update", "no_delete")
        ):
            raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    link = bodies["portfolio_run_work_link_events"]
    for field in (
        "request_attempt_id",
        "attribution_event_id",
        "connector_id",
        "source_event_id",
        "external_object_id",
        "source_revision",
        "work_scope_id",
        "work_scope_version",
        "binding_event_id",
        "supersedes_link_event_id",
    ):
        if field not in link:
            raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    decision = bodies["portfolio_run_outcome_association_events"]
    for field in (
        "rule_id",
        "rule_version",
        "window_id",
        "window_start_at",
        "window_end_at",
        "evaluation_as_of",
        "snapshot_sequence",
        "candidate_count",
        "supersedes_event_id",
    ):
        if field not in decision:
            raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    for body in (link, decision):
        for field in ("source_event_id", "external_object_id"):
            if (
                f"{field} TEXT NOT NULL CHECK (length({field}) BETWEEN 1 AND 256)"
                not in body
            ):
                raise AssociationTransitionPlanError(
                    "association_transition_proposal_invalid"
                )
        if (
            "source_revision TEXT CHECK (source_revision IS NULL OR "
            "length(source_revision) BETWEEN 1 AND 256)"
            not in body
        ):
            raise AssociationTransitionPlanError(
                "association_transition_proposal_invalid"
            )
    forbidden_columns = (
        "title",
        "description",
        "body_text",
        "comment_text",
        "prompt",
        "credential",
        "evidence_json",
        "authority_json",
        "filters_json",
        "raw_payload",
    )
    normalized = "\n".join(bodies.values()).lower()
    if any(field in normalized for field in forbidden_columns):
        raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    required_foreign_tables = (
        "gateway_request_attempts",
        "portfolio_attribution_events",
        "portfolio_outcome_events",
        "portfolio_work_scope_versions",
        "portfolio_binding_events",
        "portfolio_association_audit_events",
    )
    if any(table not in payload for table in required_foreign_tables):
        raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    for index in (
        "portfolio_run_work_link_root",
        "portfolio_run_work_link_lineage",
        "portfolio_run_outcome_association_root",
        "portfolio_run_outcome_association_lineage",
    ):
        if f"CREATE UNIQUE INDEX {index}" not in payload:
            raise AssociationTransitionPlanError("association_transition_proposal_invalid")
    forbidden_sql = (
        "GRANT UPDATE",
        "GRANT DELETE",
        "GRANT TRUNCATE",
        "WITH GRANT OPTION",
        "ALTER TABLE HORMUZ_",
        "INSERT INTO HORMUZ_SCHEMA_MIGRATIONS",
    )
    if any(item in payload.upper() for item in forbidden_sql):
        raise AssociationTransitionPlanError("association_transition_proposal_invalid")


def verify(root: Path = ROOT) -> dict[str, object]:
    root = Path(root)
    if any(not (root / relative).is_file() for relative in REQUIRED_FILES):
        raise AssociationTransitionPlanError(
            "association_transition_source_kit_incomplete"
        )
    plan = _read_json(root / PLAN_PATH)
    if canonical_digest(plan) != PLAN_SHA256:
        raise AssociationTransitionPlanError("association_transition_plan_changed")
    if (
        plan.get("schema_id") != "hormuz.association-transition-plan"
        or plan.get("schema_version") != 1
        or plan.get("stage") != "assigned_successor_transition_preflight"
        or plan.get("target_release") != "1.3.0"
        or plan.get("feature_issue") != 221
        or plan.get("gate_issue") != 214
        or plan.get("base_main_commit") != BASE_MAIN_COMMIT
        or plan.get("base_main_ci") != BASE_MAIN_CI
    ):
        raise AssociationTransitionPlanError("association_transition_plan_invalid")
    baseline = plan.get("integrated_baseline", {})
    if (
        baseline.get("commit") != plan["base_main_commit"]
        or baseline.get("sqlite_schema_version") != 15
        or baseline.get("postgresql_schema_version") != 20
        or baseline.get("postgresql_acl_boundary")
        != {
            "entry_count": 222,
            "sha256": "cd86c395bea316873e11e175563cbf31563212c45ca6cb6b6fb066f2f8f1e64d",
        }
    ):
        raise AssociationTransitionPlanError("association_transition_plan_invalid")
    if plan.get("transitions") != {
        "sqlite": {"from": 15, "to": 16},
        "postgresql": {"from": 20, "to": 21},
    }:
        raise AssociationTransitionPlanError(
            "association_transition_schema_assignment_invalid"
        )
    if set(plan.get("required_transition_cases", {})) != TRANSITION_CASES:
        raise AssociationTransitionPlanError("association_transition_plan_invalid")
    if plan.get("gates") != EXPECTED_GATES:
        raise AssociationTransitionPlanError("association_transition_gate_overclaim")
    storage = plan.get("proposed_storage", {})
    if (
        storage.get("tables") != list(PROPOSAL_TABLES)
        or storage.get("raw_payload_storage") is not False
        or storage.get("free_text_storage") is not False
        or storage.get("credentials_or_credential_hashes") is not False
    ):
        raise AssociationTransitionPlanError("association_transition_plan_invalid")
    if (
        plan.get("postgresql_acl", {}).get("successor_complete_acl_boundary")
        != EXPECTED_ACL
    ):
        raise AssociationTransitionPlanError("association_transition_plan_invalid")
    frozen = plan.get("frozen_file_sha256")
    if not isinstance(frozen, dict) or set(frozen) != {
        "docs/ASSOCIATION_LINKAGE_PREFLIGHT.md",
        "docs/portfolio-extension-contract-v1.json",
        "docs/portfolio-intelligence-wire-v1.json",
        "hormuz/portfolio-attribution-wire-v1.json",
        "hormuz/portfolio-outcome-wire-v1.json",
        SQLITE_PROPOSAL,
        POSTGRES_PROPOSAL,
    }:
        raise AssociationTransitionPlanError("association_transition_plan_invalid")
    for relative, expected in frozen.items():
        try:
            actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        except OSError:
            raise AssociationTransitionPlanError(
                "association_transition_source_kit_incomplete"
            ) from None
        if not isinstance(expected, str) or actual != expected:
            raise AssociationTransitionPlanError(
                "association_transition_frozen_file_changed"
            )
    _proposal_boundary(root, SQLITE_PROPOSAL, postgresql=False)
    _proposal_boundary(root, POSTGRES_PROPOSAL, postgresql=True)
    versions = (SQLITE_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION)
    if versions in {(16, 21), (17, 22), (18, 23)}:
        try:
            from tools.verify_association_runtime_plan import (
                AssociationRuntimePlanError,
                verify as verify_runtime,
            )

            successor = verify_runtime(root)
        except AssociationRuntimePlanError as error:
            mapping = {
                "association_runtime_source_kit_incomplete": "association_transition_source_kit_incomplete",
                "association_runtime_source_changed": "association_transition_frozen_file_changed",
                "association_runtime_predecessor_changed": "association_transition_plan_changed",
            }
            raise AssociationTransitionPlanError(
                mapping.get(error.code, "association_transition_successor_invalid")
            ) from None
        return {
            "status": "association_transition_successor_verified",
            "plan_sha256": PLAN_SHA256,
            "sqlite_transition": [15, 16],
            "postgresql_transition": [20, 21],
            "sqlite_schema_version": SQLITE_SCHEMA_VERSION,
            "postgresql_schema_version": POSTGRES_SCHEMA_VERSION,
            "postgresql_acl": successor["postgresql_acl"],
            "proposal_tables": len(PROPOSAL_TABLES),
            "runtime_implemented": successor["runtime_implemented"],
            "live_connectors_authorized": False,
            "gates": successor["gates"],
        }
    if versions != (15, 20):
        raise AssociationTransitionPlanError("association_transition_baseline_changed")
    return {
        "status": "association_transition_plan_verified",
        "plan_sha256": PLAN_SHA256,
        "sqlite_transition": [15, 16],
        "postgresql_transition": [20, 21],
        "proposal_tables": len(PROPOSAL_TABLES),
        "runtime_implemented": False,
        "live_connectors_authorized": False,
        "gates": plan["gates"],
    }


if __name__ == "__main__":
    print(json.dumps(verify(), sort_keys=True))
