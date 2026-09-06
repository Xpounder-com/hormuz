#!/usr/bin/env python3
"""Content-free source/SQLite diagnostic for the schema-12/17 predecessor.

This is a decision-preflight probe, not a reconciliation implementation or a
claim about live credentials, provider accounts, PostgreSQL or a release.
Run against the named predecessor; a later runtime may intentionally differ.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from hormuz.budget_runtime import configured_route_rate_card
from hormuz.finance_collection_repository import (
    FinanceCollectionRepository,
    SourceBindingVersion,
)
from hormuz._contract_schemas.common import ContractValidationError
from hormuz._contract_schemas.request_attempt import validate_request_attempt
from hormuz._persistence import build_request_attempt_root
from hormuz.store import UsageStore
from hormuz.config import Identity


SOURCE_SHA256 = {
    "hormuz/budget_runtime.py": "894b299b137d96fdf5e55b6ca28e5f416b353ea0fa89f361075adcd53e6e5c3c",
    "hormuz/store.py": "04874391bea0d50ff40f5f831de1c923d38ffc29b115d8fba63052487218d200",
    "hormuz/finance_collection_repository.py": "40e75186981deee5ac07766d34944a497405381eb539636b09b697dd2eccb434",
    "hormuz/_finance_attempt_schema.py": "65ccbbfe18c3f19cb13f350f035b6f6a39c791bd69a09034c1a1400e35ca5b23",
    "hormuz/_sqlite_schema.py": "e37a7c6226d4b6ae3bea9aed5abce6f28a09863b8cf00807e45214f8d644700b",
    "hormuz/postgres.py": "269ce23f6d3cbe8878473c05df5eabe4ac93c0b64866bddb5f940768c04fa0d3",
    "hormuz/_contract_schemas/request_attempt.py": "a87b5ff7a66ce8c616ac60e8e65bb0e6bfd94071d6cd2f25bcdd850c1663ef28",
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def probe() -> dict:
    for relative, expected in SOURCE_SHA256.items():
        require(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected,
                "reconciliation_scope_probe_predecessor_changed")
    route_arguments = {
        "alias": "synthetic-route", "protocol": "openai",
        "upstream_model": "synthetic-model", "input_cost_per_million": 2,
        "cache_read_cost_per_million": 1, "cache_write_cost_per_million": 3,
        "output_cost_per_million": 4,
    }
    rate_card = configured_route_rate_card(**route_arguments)
    route_parameters = set(inspect.signature(configured_route_rate_card).parameters)
    # The complete callable input set is price/route only. No real upstream or
    # credential is read; this establishes an absent coordinate, not live use.
    require(route_parameters == set(route_arguments), "configured_price_boundary_changed")

    root = build_request_attempt_root(
        attempt_id="synthetic-attempt", created_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        identity=Identity(
            token_env="UNUSED_SCOPE_PROBE", token="", actor_id="synthetic-actor",
            actor_name="Synthetic", team_id="synthetic-team", team_name="Synthetic",
            organization_id="synthetic-tenant",
        ),
        organization_id="synthetic-tenant", client="synthetic-client", protocol="openai",
        requested_model="synthetic-route", resolved_alias="synthetic-route",
        upstream_model="synthetic-model", policy_version="synthetic-policy",
        policy_action="allowed", redaction_count=0, redaction_rules=(),
        reserved_tokens=10, reserved_cost_microusd=20,
    )
    validate_request_attempt(root)
    try:
        validate_request_attempt({**root, "provider_account_binding_id": "synthetic-binding"})
    except ContractValidationError:
        v1_extension_rejected = True
    else:
        raise AssertionError("frozen_v1_attempt_shape_changed")

    with tempfile.TemporaryDirectory(prefix="hormuz-reconciliation-scope-") as temporary:
        database = Path(temporary) / "synthetic.sqlite3"
        UsageStore(database)
        connection = sqlite3.connect(database)
        try:
            columns = {
                table: {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                for table in ("gateway_request_attempts", "gateway_finance_attempt_evidence")
            }
        finally:
            connection.close()

    account_coordinates = {
        "provider_account_fingerprint", "provider_account_binding_id",
        "source_binding_id", "binding_id", "credential_reference_id",
    }
    require(all(not (names & account_coordinates) for names in columns.values()),
            "attempt_account_columns_changed")
    collection_fields = {field.name for field in fields(SourceBindingVersion)}
    require({"organization_id", "binding_id", "version", "provider_account_fingerprint",
             "credential_reference_id", "credential_reference_version"} <= collection_fields,
            "collection_binding_boundary_changed")
    selection_parameters = set(inspect.signature(FinanceCollectionRepository.current_observations).parameters)
    require(selection_parameters == {
        "self", "principal", "binding_id", "binding_version", "collection_profile", "start_at", "end_at",
    }, "collection_selection_boundary_changed")

    return {
        "status": "decision_preflight_gap_reproduced",
        "baseline_commit": "c877f49da8baf6a837924f33f06494964cd7118b",
        "baseline_binding": "seven_relevant_source_files_sha256_not_full_distribution_proof",
        "scope": "source_and_disposable_sqlite_only",
        "checks": {
            "configured_price_identity_has_no_account_input": True,
            "frozen_v1_attempt_rejects_added_account_field": v1_extension_rejected,
            "attempt_and_finance_tables_lack_account_binding": True,
            "collection_binding_has_distinct_account_coordinates": True,
            "latest_collection_selector_has_no_explicit_as_of_input": True,
        },
        "price_digest_present": bool(rate_card["content_digest"]),
        "provider_io": False,
        "credentials_read": False,
        "reconciliation_implemented": False,
        "preflight_accepted": False,
    }


if __name__ == "__main__":
    print(json.dumps(probe(), sort_keys=True))
