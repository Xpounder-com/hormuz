"""Synthetic metadata fixtures and an isolated, released-v1 subprocess driver.

Only interfaces already present in v1.0.0 may be imported here. The driver is
also executed by the interpreter installed from the digest-pinned release.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from hormuz.config import Identity
from hormuz.store import ReservationScope, StorageSchemaError, UsageStore
from tests._sqlite import managed_sqlite_connection


ARCHIVE_SHA256 = "2c3b16c1742ee76032a33f3714492a8d8515c5291d4d57520441882cd8bc5b5a"
PROBE_TABLE = "registry_transition_test_probe"


def seed_registry_ledger(store) -> None:
    identity = Identity(
        token_env="UNUSED_REGISTRY_TEST_TOKEN", token="synthetic-unused-secret",
        actor_id="alice", actor_name="Alice", team_id="engineering",
        team_name="Engineering", organization_id="acme", identity_type="human",
        authentication_source="oidc",
    )
    store.record(
        identity=identity, client="codex", protocol="openai", requested_model="synthetic-model",
        resolved_alias="synthetic-model", upstream_model="synthetic-provider-model",
        policy_version="synthetic-policy-v1", policy_action="allowed", status="succeeded",
        input_tokens=7, output_tokens=3, cost_microusd=11,
    )
    store.record_secret_event(
        identity=identity, client="codex", protocol="openai", requested_model="synthetic-model",
        policy_version="synthetic-policy-v1", action="redacted", detection_count=1,
        rules=("openai_api_key",),
    )
    attempt = store.begin_request_attempt(
        identity=identity, client="codex", protocol="openai", requested_model="synthetic-model",
        resolved_alias=None, upstream_model="synthetic-provider-model",
        policy_version="synthetic-policy-v1", policy_action="allowed",
        redaction_count=0, redaction_rules=(),
        scopes=(ReservationScope(name="organization", cost_limit_microusd=10000),),
        reserved_tokens=20, reserved_cost_microusd=40, ttl_seconds=60,
    )
    store.mark_request_attempt_outcome_unknown(
        attempt=attempt, organization_id="acme", reason_code="provider_transport_ambiguous",
    )


def ledger_observation(store) -> dict[str, object]:
    store.verify_ready()
    return {
        "unknown_holds": store.active_budget_reservations(organization_id="acme"),
        "audit_sequence": store.verify_audit_chain(organization_id="acme").sequence,
        "usage_events": len(store.audit_events(
            since="2000-01-01T00:00:00+00:00", kind="usage", organization_id="acme",
        )),
    }


def sqlite_snapshot(path: Path) -> dict[str, object]:
    with managed_sqlite_connection(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        objects = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        rows = {}
        for kind, name, _, _ in objects:
            if kind == "table":
                quoted = '"' + name.replace('"', '""') + '"'
                rows[name] = sorted(connection.execute(f"SELECT * FROM {quoted}").fetchall(), key=repr)
        return {"objects": objects, "rows": rows}


def sqlite_backup(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("test_backup_destination_exists")
    with (
        managed_sqlite_connection(f"{source.as_uri()}?mode=ro", uri=True) as reader,
        managed_sqlite_connection(destination) as writer,
    ):
        reader.backup(writer)


def released_v1_call(request: dict[str, object]) -> dict[str, object]:
    result = subprocess.run(
        [os.environ["HORMUZ_TEST_V1_PYTHON"], "-I", str(Path(__file__).resolve())],
        input=json.dumps(request), text=True, capture_output=True, timeout=60,
        cwd=Path(os.environ["HORMUZ_TEST_V1_PYTHON"]).parent,
    )
    if result.returncode != 0:
        # No subprocess stderr, DSN, row contents, or arbitrary exceptions in evidence.
        raise AssertionError("released_v1_driver_failed")
    return json.loads(result.stdout)


def _released_driver() -> None:
    import hormuz
    from hormuz.contracts import contract_manifest
    from hormuz.postgres import PostgresStorageError, migrate_postgres
    from hormuz.postgres_usage_store import PostgresUsageStore

    distribution = importlib.metadata.distribution("hormuz")
    direct_url = json.loads(distribution.read_text("direct_url.json") or "{}")
    if (
        distribution.version != "1.0.0"
        or direct_url.get("archive_info", {}).get("hashes", {}).get("sha256") != ARCHIVE_SHA256
        or not Path(hormuz.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    ):
        raise RuntimeError("released_baseline_install_binding_invalid")
    request = json.load(sys.stdin)
    try:
        if request["backend"] == "sqlite":
            store = UsageStore(Path(request["path"]), read_only=request["mode"] != "seed")
        elif request["backend"] == "postgresql":
            if request["mode"] == "seed":
                migrate_postgres(
                    request["owner_dsn"], schema=request["schema"], runtime_role=request["runtime_role"],
                    policy_control_role=request["policy_control_role"],
                    custody_control_role=request["custody_control_role"],
                    custody_executor_role=request["custody_executor_role"],
                )
            store = PostgresUsageStore(
                request["runtime_dsn"], schema=request["schema"], runtime_role=request["runtime_role"],
                organization_ids=("acme", "beta"),
            )
        else:
            raise ValueError("test_backend_invalid")
        if request["mode"] == "seed":
            seed_registry_ledger(store)
        result = {"status": "ready", **ledger_observation(store), "manifest": contract_manifest()}
    except (StorageSchemaError, PostgresStorageError) as error:
        result = {"status": "refused", "code": error.code}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    _released_driver()
