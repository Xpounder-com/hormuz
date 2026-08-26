#!/usr/bin/env python3
"""Seed and fingerprint every PostgreSQL-backed #105 recovery state class.

The output contains counts, timestamps, and SHA-256 fingerprints only.  It is
safe to retain as rehearsal input but is deliberately not the final public
evidence artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping
from uuid import UUID

from hormuz.audit_chain import (
    build_audit_chain_checkpoint,
    serialize_audit_chain_checkpoint,
)
from hormuz.config import GatewayConfig
from hormuz.custody_control import CustodyControlService
from hormuz.custody_execution_repository import CustodyExecutionRequest
from hormuz.custody_executor import CustodyExecutorService, LifecycleCustodyOperationRunner
from hormuz.policy_control import PolicyControlService
from hormuz.postgres import POSTGRES_SCHEMA_VERSION, postgres_transaction
from hormuz.postgres_custody_lifecycle_store import PostgresCustodyProjectionStore
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import ReservationScope


ORGANIZATION = "kubernetes-proof-organization"
ISOLATION_ORGANIZATION = "kubernetes-proof-isolation-tenant"
CONFIG_PATH = Path("/etc/hormuz-recovery/state-config.json")
SNAPSHOT_SCHEMA_ID = "hormuz.disaster-recovery-state-snapshot"
SNAPSHOT_SCHEMA_VERSION = 1
STALE_CHECKPOINT_ID = "00000000-0000-4000-8000-000000000101"
CURRENT_CHECKPOINT_ID = "00000000-0000-4000-8000-000000000102"

CLASS_TABLES: Mapping[str, tuple[tuple[str, str], ...]] = {
    "identity_source_and_event_time_bindings": (
        ("runtime", "gateway_usage_events"),
        ("runtime", "gateway_secret_events"),
        ("runtime", "gateway_request_attempts"),
    ),
    "schema_migration_ledger": (("runtime", "hormuz_schema_migrations"),),
    "usage_cost_and_security_evidence": (
        ("runtime", "gateway_usage_events"),
        ("runtime", "gateway_secret_events"),
    ),
    "budget_reservations": (("runtime", "gateway_budget_reservations"),),
    "request_attempt_ledger": (
        ("runtime", "gateway_request_attempts"),
        ("runtime", "gateway_request_attempt_events"),
    ),
    "policy_authority_versions_and_activation": (
        ("policy", "policy_tenants"),
        ("policy", "policy_administrators"),
        ("runtime", "policy_versions"),
        ("runtime", "policy_active_versions"),
        ("policy", "policy_control_events"),
    ),
    "audit_chain_history_and_checkpoint_metadata": (
        ("runtime", "gateway_audit_chain_epochs"),
        ("runtime", "gateway_audit_chain_heads"),
        ("runtime", "gateway_audit_chain_entries"),
        ("runtime", "gateway_audit_chain_checkpoints"),
    ),
    "custody_authority_intents_approvals_and_events": (
        ("custody_control", "custody_tenants"),
        ("custody_control", "custody_administrators"),
        ("custody_control", "custody_operation_intents"),
        ("custody_control", "custody_operation_approvals"),
        ("custody_control", "custody_control_events"),
        ("custody_control", "custody_deletion_events"),
    ),
    "custody_execution_and_lifecycle_history": (
        ("custody_executor", "custody_execution_attempts"),
        ("custody_executor", "custody_execution_events"),
        ("custody_executor", "custody_lifecycle_asset_identities"),
        ("custody_executor", "custody_lifecycle_chain_heads"),
        ("custody_executor", "custody_lifecycle_events"),
        ("custody_executor", "custody_envelope_attestations"),
    ),
    "custody_runtime_projection_and_coordination": (
        ("runtime", "custody_runtime_projection_heads"),
        ("runtime", "custody_runtime_projection_restrictions"),
        ("runtime", "custody_runtime_replicas"),
        ("runtime", "custody_runtime_projection_barriers"),
        ("runtime", "custody_runtime_projection_acks"),
    ),
}

# RPO uses only timestamps created when durable state is committed.  Expiry,
# lease, retention, and recovery-target timestamps are intentionally excluded:
# they can be in the future and are not evidence of a recovered write.
COMMIT_TIMESTAMP_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "gateway_usage_events": ("occurred_at",),
    "gateway_secret_events": ("occurred_at",),
    "gateway_request_attempts": ("created_at",),
    "hormuz_schema_migrations": ("applied_at",),
    "gateway_budget_reservations": ("created_at",),
    "gateway_request_attempt_events": ("occurred_at",),
    "policy_tenants": ("initialized_at",),
    "policy_administrators": ("created_at", "revoked_at"),
    "policy_versions": ("created_at",),
    "policy_active_versions": ("activated_at",),
    "policy_control_events": ("occurred_at",),
    "gateway_audit_chain_epochs": ("created_at",),
    "gateway_audit_chain_heads": (),
    "gateway_audit_chain_entries": ("appended_at",),
    "gateway_audit_chain_checkpoints": ("anchored_at",),
    "custody_tenants": ("initialized_at",),
    "custody_administrators": ("created_at", "revoked_at"),
    "custody_operation_intents": ("created_at", "authorized_at"),
    "custody_operation_approvals": ("approved_at",),
    "custody_control_events": ("occurred_at",),
    "custody_deletion_events": ("occurred_at",),
    "custody_execution_attempts": ("claimed_at",),
    "custody_execution_events": ("occurred_at",),
    "custody_lifecycle_asset_identities": ("registered_at",),
    "custody_lifecycle_chain_heads": ("committed_at",),
    "custody_lifecycle_events": ("occurred_at",),
    "custody_envelope_attestations": ("occurred_at",),
    "custody_runtime_projection_heads": ("committed_at",),
    "custody_runtime_projection_restrictions": ("committed_at",),
    "custody_runtime_replicas": ("registered_at", "heartbeat_at", "retired_at"),
    "custody_runtime_projection_barriers": (
        "prepared_at",
        "activated_at",
        "resolved_at",
    ),
    "custody_runtime_projection_acks": ("acknowledged_at",),
}

ROLE_ENV = {
    "runtime": ("HORMUZ_POSTGRES_DSN", "hormuz_runtime"),
    "policy": ("HORMUZ_POLICY_CONTROL_DSN", "hormuz_policy_control"),
    "custody_control": ("HORMUZ_CUSTODY_CONTROL_DSN", "hormuz_custody_control"),
    "custody_executor": ("HORMUZ_CUSTODY_EXECUTOR_DSN", "hormuz_custody_executor"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "snapshot"))
    args = parser.parse_args()
    config = GatewayConfig.load(CONFIG_PATH)
    result = seed(config) if args.command == "seed" else snapshot(config)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def seed(config: GatewayConfig) -> dict[str, object]:
    """Create representative metadata through the governed service boundaries."""

    _stage("policy_seed_started")
    identity = config.identities_by_actor["ha-proof-alice"]
    policy = PolicyControlService(config)
    policy.bootstrap(organization_id=ORGANIZATION, credential_env="HORMUZ_TOKEN")
    document = {
        "schema_id": "hormuz.policy-document",
        "schema_version": 1,
        "organization_id": ORGANIZATION,
        "policies": {
            "organization": {
                "allowed_clients": ["codex"],
                "allowed_models": ["gpt-ha-proof", "claude-ha-proof"],
                "max_output_tokens": 64,
                "monthly_budget_usd": 10,
                "per_actor_monthly_budget_usd": 10,
            },
            "teams": {},
            "actors": {},
        },
        "egress_controls": {
            "openai": {"allow_response_storage": False, "allow_background": False},
            "secrets": {"mode": "redact"},
        },
    }
    with NamedTemporaryFile("w", encoding="utf-8", dir="/tmp", suffix=".json") as stream:
        json.dump(document, stream, separators=(",", ":"))
        stream.flush()
        staged = policy.stage(
            organization_id=ORGANIZATION,
            credential_env="HORMUZ_TOKEN",
            policy_path=stream.name,
        )
    active = policy.activate(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
        version_id=staged.version_id,
    )
    _stage("policy_seed_completed")

    _stage("usage_seed_started")
    store = PostgresUsageStore(
        _required("HORMUZ_POSTGRES_DSN"),
        organization_ids=(ORGANIZATION,),
        schema="hormuz",
        runtime_role="hormuz_runtime",
    )
    store.record(
        identity=identity,
        client="codex",
        protocol="openai",
        requested_model="gpt-ha-proof",
        resolved_alias="gpt-ha-proof",
        upstream_model="gpt-ha-proof",
        provider_reported_model="gpt-ha-proof",
        policy_version=active.version_id,
        policy_action="allowed",
        status="succeeded",
        input_tokens=100,
        output_tokens=20,
        cost_microusd=300,
    )
    store.record_secret_event(
        identity=identity,
        client="codex",
        protocol="openai",
        requested_model="gpt-ha-proof",
        policy_version=active.version_id,
        action="redacted",
        detection_count=1,
        rules=("aws_access_key",),
    )
    reservation = store.reserve_budget(
        identity=identity,
        scopes=(
            ReservationScope(
                name="organization",
                token_limit=100_000,
                cost_limit_microusd=100_000,
            ),
        ),
        reserved_tokens=50,
        reserved_cost_microusd=100,
        ttl_seconds=7_200,
    )
    if reservation is None:
        raise SystemExit("recovery_reservation_missing")
    attempt = store.begin_request_attempt(
        identity=identity,
        client="codex",
        protocol="openai",
        requested_model="gpt-ha-proof",
        resolved_alias="gpt-ha-proof",
        upstream_model="gpt-ha-proof",
        policy_version=active.version_id,
        policy_action="allowed",
        redaction_count=0,
        redaction_rules=(),
        scopes=(
            ReservationScope(
                name="organization",
                token_limit=100_000,
                cost_limit_microusd=100_000,
            ),
        ),
        reserved_tokens=25,
        reserved_cost_microusd=75,
        ttl_seconds=7_200,
    )
    if not store.mark_request_attempt_outcome_unknown(
        attempt=attempt,
        organization_id=ORGANIZATION,
        reason_code="provider_transport_ambiguous",
    ):
        raise SystemExit("recovery_ambiguous_attempt_missing")
    _stage("usage_seed_completed")

    _stage("custody_seed_started")
    custody = CustodyControlService(config)
    custody.bootstrap(organization_id=ORGANIZATION, credential_env="HORMUZ_TOKEN")
    if config.custody_lifecycle is None:
        raise SystemExit("custody_lifecycle_missing")
    target = config.custody_lifecycle.assets.asset(
        organization_id=ORGANIZATION,
        asset_type="provider_credential",
        asset_id="anthropic-primary",
        generation=1,
    )
    proposed = CustodyExecutionRequest(
        organization_id=ORGANIZATION,
        operation_id="00000000-0000-4000-8000-000000000000",
        operation_type="disable_provider_credential",
        target=target.audit_ref(),
        parameters={},
    )
    operation = custody.authorize_operation(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
        operation_type=proposed.operation_type,
        target_sha256=proposed.target_sha256,
        parameters_sha256=proposed.parameters_sha256,
    )
    custody.approve_operation(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_BOB_TOKEN",
        operation_id=operation.operation_id,
    )
    request = CustodyExecutionRequest(
        organization_id=ORGANIZATION,
        operation_id=operation.operation_id,
        operation_type=proposed.operation_type,
        target=proposed.target,
        parameters=proposed.parameters,
    )
    executor = CustodyExecutorService(
        config,
        runner=LifecycleCustodyOperationRunner(config),
    )
    executor.register_asset_catalog()
    execution = executor.execute(request=request)
    if execution.state != "succeeded":
        raise SystemExit("custody_seed_not_succeeded")
    _stage("custody_seed_completed")

    stale = _anchor_checkpoint(store, STALE_CHECKPOINT_ID)
    store.record_secret_event(
        identity=identity,
        client="codex",
        protocol="anthropic",
        requested_model="claude-ha-proof",
        policy_version=active.version_id,
        action="redacted",
        detection_count=1,
        rules=("generic_api_key",),
    )
    current = _anchor_checkpoint(store, CURRENT_CHECKPOINT_ID)
    store.verify_audit_chain(organization_id=ORGANIZATION, checkpoint=current)
    _stage("checkpoints_seed_completed")
    return {
        "schema_id": "hormuz.disaster-recovery-seed",
        "schema_version": 1,
        "command": "seed",
        "stale_checkpoint": stale,
        "current_checkpoint": current,
        "snapshot": snapshot(config),
    }


def snapshot(config: GatewayConfig) -> dict[str, object]:
    """Return exact class fingerprints and admission facts without mutations."""

    _stage("snapshot_started")
    policy = PolicyControlService(config).status(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
    )
    custody = CustodyControlService(config).status(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
    )
    if policy.active is None or config.custody_lifecycle is None:
        raise SystemExit("recovery_authority_missing")
    projection_store = PostgresCustodyProjectionStore(
        _required("HORMUZ_POSTGRES_DSN"),
        schema="hormuz",
        runtime_role="hormuz_runtime",
    )
    projection = projection_store.load(organization_id=ORGANIZATION)
    asset = config.custody_lifecycle.assets.asset(
        organization_id=ORGANIZATION,
        asset_type="provider_credential",
        asset_id="anthropic-primary",
        generation=1,
    )
    restriction = projection.restriction_for(asset)
    if restriction != "provider_credential_disabled":
        raise SystemExit("recovery_custody_restriction_missing")

    classes: dict[str, object] = {}
    for identifier, tables in CLASS_TABLES.items():
        material: dict[str, list[dict[str, object]]] = {}
        timestamps: list[datetime] = []
        for role, table in tables:
            rows = _rows(role=role, table=table, organization_id=ORGANIZATION)
            material[table] = rows
            timestamps.extend(_commit_timestamps(table, rows))
        if not timestamps:
            raise SystemExit(f"recovery_state_marker_missing:{identifier}")
        classes[identifier] = {
            "fingerprint": _digest(material),
            "latest_committed_marker_at": max(timestamps).astimezone(timezone.utc).isoformat(),
            "record_count": sum(len(rows) for rows in material.values()),
        }

    runtime_rows = _rows(
        role="runtime",
        table="gateway_request_attempt_events",
        organization_id=ORGANIZATION,
    )
    unknown = sum(1 for row in runtime_rows if row.get("state") == "outcome_unknown")
    reservations = _rows(
        role="runtime",
        table="gateway_budget_reservations",
        organization_id=ORGANIZATION,
    )
    uncertain = sum(1 for row in reservations if row.get("attempt_id") is not None)
    migrations = _rows(
        role="runtime",
        table="hormuz_schema_migrations",
        organization_id=ORGANIZATION,
    )
    versions = sorted(int(row["version"]) for row in migrations)
    if versions != list(range(1, POSTGRES_SCHEMA_VERSION + 1)):
        raise SystemExit("recovery_migration_ledger_invalid")

    usage = _rows(role="runtime", table="gateway_usage_events", organization_id=ORGANIZATION)
    if not usage or any(
        row.get("organization_id") != ORGANIZATION
        or row.get("actor_id") != "ha-proof-alice"
        or row.get("team_id") != "ha-proof-team"
        for row in usage
    ):
        raise SystemExit("recovery_event_time_identity_invalid")

    checkpoints = _rows(
        role="runtime",
        table="gateway_audit_chain_checkpoints",
        organization_id=ORGANIZATION,
    )
    if len(checkpoints) != 2:
        raise SystemExit("recovery_checkpoint_history_invalid")
    current_checkpoint = max(checkpoints, key=lambda row: int(row["sequence"]))
    stale_checkpoint = min(checkpoints, key=lambda row: int(row["sequence"]))
    if int(current_checkpoint["sequence"]) <= int(stale_checkpoint["sequence"]):
        raise SystemExit("recovery_checkpoint_order_invalid")

    barriers = _rows(
        role="runtime",
        table="custody_runtime_projection_barriers",
        organization_id=ORGANIZATION,
    )
    unresolved_barriers = sum(
        1
        for row in barriers
        if row.get("activated_at") is None and row.get("resolved_at") is None
    )
    tenant_isolation_rows = 0
    for role, table in _all_tables():
        if table == "hormuz_schema_migrations":
            continue
        tenant_isolation_rows += len(
            _rows(role=role, table=table, organization_id=ISOLATION_ORGANIZATION)
        )

    chain = PostgresUsageStore(
        _required("HORMUZ_POSTGRES_DSN"),
        organization_ids=(ORGANIZATION,),
        schema="hormuz",
        runtime_role="hormuz_runtime",
    ).verify_audit_chain(organization_id=ORGANIZATION)
    custody_tenant = _rows(
        role="custody_control",
        table="custody_tenants",
        organization_id=ORGANIZATION,
    )
    if (
        len(custody_tenant) != 1
        or int(custody_tenant[0]["retention_days"]) != 365
        or custody_tenant[0]["retention_legal_hold"] is not False
    ):
        raise SystemExit("recovery_custody_retention_invalid")

    manifest_material = {
        identifier: {
            "fingerprint": item["fingerprint"],
            "record_count": item["record_count"],
        }
        for identifier, item in classes.items()
    }
    result = {
        "schema_id": SNAPSHOT_SCHEMA_ID,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "command": "snapshot",
        "organization_id": ORGANIZATION,
        "manifest_fingerprint": _digest(manifest_material),
        "state_classes": classes,
        "admission_facts": {
            "policy_active_version": policy.active.version_id,
            "policy_generation": policy.active.generation,
            "policy_administrator_count": len(policy.administrators),
            "custody_administrator_count": len(custody.administrators),
            "custody_retention_days": int(custody_tenant[0]["retention_days"]),
            "custody_legal_hold": custody_tenant[0]["retention_legal_hold"],
            "custody_projection_version": projection.version,
            "custody_restriction": restriction,
            "unresolved_coordination_barriers": unresolved_barriers,
            "outcome_unknown_attempts": unknown,
            "uncertain_reservations": uncertain,
            "audit_chain_epoch": chain.chain_epoch,
            "audit_chain_sequence": chain.sequence,
            "current_checkpoint_sequence": int(current_checkpoint["sequence"]),
            "stale_checkpoint_sequence": int(stale_checkpoint["sequence"]),
            "tenant_isolation_rows": tenant_isolation_rows,
            "migration_version": POSTGRES_SCHEMA_VERSION,
        },
    }
    _validate_snapshot(result)
    _stage("snapshot_completed")
    return result


def _anchor_checkpoint(store: PostgresUsageStore, checkpoint_id: str) -> dict[str, object]:
    with postgres_transaction(
        _required("HORMUZ_POSTGRES_DSN"),
        schema="hormuz",
        runtime_role="hormuz_runtime",
        organization_id=ORGANIZATION,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT clock_timestamp() AS now")
            created_at = cursor.fetchone()["now"]
    head = store.verify_audit_chain(organization_id=ORGANIZATION)
    checkpoint = build_audit_chain_checkpoint(
        head,
        created_at=created_at,
        checkpoint_id=checkpoint_id,
    )
    artifact = serialize_audit_chain_checkpoint(checkpoint)
    store.record_audit_chain_checkpoint(
        checkpoint=checkpoint,
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        anchor_backend="disposable_protected_reference",
        object_version=checkpoint_id,
        anchored_at=created_at,
    )
    return checkpoint


def _rows(*, role: str, table: str, organization_id: str) -> list[dict[str, object]]:
    if role not in ROLE_ENV or table not in {item for _, item in _all_tables()}:
        raise SystemExit("recovery_state_query_invalid")
    dsn_env, database_role = ROLE_ENV[role]
    with postgres_transaction(
        _required(dsn_env),
        schema="hormuz",
        runtime_role=database_role,
        organization_id=organization_id,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {table}")
            normalized = [_normalize(dict(row)) for row in cursor.fetchall()]
    return sorted(
        normalized,
        key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")),
    )


def _all_tables() -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for tables in CLASS_TABLES.values():
        for value in tables:
            if value not in values:
                values.append(value)
    return tuple(values)


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _commit_timestamps(
    table: str,
    rows: list[dict[str, object]],
) -> tuple[datetime, ...]:
    if table not in COMMIT_TIMESTAMP_COLUMNS:
        raise SystemExit("recovery_commit_timestamp_table_unknown")
    timestamps: list[datetime] = []
    for row in rows:
        for column in COMMIT_TIMESTAMP_COLUMNS[table]:
            if column not in row:
                raise SystemExit("recovery_commit_timestamp_column_missing")
            value = row[column]
            if value is None:
                continue
            if not isinstance(value, str):
                raise SystemExit("recovery_commit_timestamp_invalid")
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                raise SystemExit("recovery_commit_timestamp_invalid") from None
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise SystemExit("recovery_commit_timestamp_invalid")
            timestamps.append(parsed)
    return tuple(timestamps)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _validate_snapshot(value: Mapping[str, object]) -> None:
    if value.get("schema_id") != SNAPSHOT_SCHEMA_ID or value.get("schema_version") != 1:
        raise SystemExit("recovery_snapshot_schema_invalid")
    facts = value.get("admission_facts")
    if not isinstance(facts, Mapping):
        raise SystemExit("recovery_snapshot_schema_invalid")
    if (
        facts.get("policy_generation") != 1
        or facts.get("policy_administrator_count") != 1
        or facts.get("custody_administrator_count") != 2
        or facts.get("custody_projection_version") != 1
        or facts.get("custody_restriction") != "provider_credential_disabled"
        or facts.get("unresolved_coordination_barriers") != 0
        or int(facts.get("outcome_unknown_attempts", 0)) < 1
        or int(facts.get("uncertain_reservations", 0)) < 1
        or facts.get("tenant_isolation_rows") != 0
        or facts.get("migration_version") != POSTGRES_SCHEMA_VERSION
    ):
        raise SystemExit("recovery_snapshot_admission_invalid")


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit("recovery_state_environment_missing")
    return value


def _stage(name: str) -> None:
    print(f"disaster_recovery_state_stage={name}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
