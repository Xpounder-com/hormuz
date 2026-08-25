#!/usr/bin/env python3
"""Seed and inspect representative tenant state without emitting its contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile

from hormuz.config import GatewayConfig
from hormuz.custody_control import CustodyControlService
from hormuz.custody_execution_repository import CustodyExecutionRequest
from hormuz.custody_executor import CustodyExecutorService, LifecycleCustodyOperationRunner
from hormuz.policy_control import PolicyControlService
from hormuz.postgres import postgres_transaction
from hormuz.postgres_custody_lifecycle_store import PostgresCustodyProjectionStore
from hormuz.postgres_usage_store import PostgresUsageStore


ORGANIZATION = "kubernetes-proof-organization"
CONFIG_PATH = Path("/etc/hormuz-state/state-config.json")
SNAPSHOT_SCHEMA_ID = "hormuz.postgresql-ha-state-snapshot"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "snapshot"))
    args = parser.parse_args()
    _stage("config_load_started")
    config = GatewayConfig.load(CONFIG_PATH)
    _stage("config_load_completed")
    if args.command == "seed":
        result = seed(config)
    else:
        result = snapshot(config)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


def seed(config: GatewayConfig) -> dict[str, object]:
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
        version = policy.stage(
            organization_id=ORGANIZATION,
            credential_env="HORMUZ_TOKEN",
            policy_path=stream.name,
        )
    activation = policy.activate(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
        version_id=version.version_id,
    )

    custody = CustodyControlService(config)
    custody.bootstrap(organization_id=ORGANIZATION, credential_env="HORMUZ_TOKEN")
    assert config.custody_lifecycle is not None
    target = config.custody_lifecycle.assets.asset(
        organization_id=ORGANIZATION,
        asset_type="provider_credential",
        asset_id="anthropic-primary",
        generation=1,
    )
    pending = CustodyExecutionRequest(
        organization_id=ORGANIZATION,
        operation_id="00000000-0000-4000-8000-000000000000",
        operation_type="disable_provider_credential",
        target=target.audit_ref(),
        parameters={},
    )
    operation = custody.authorize_operation(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
        operation_type=pending.operation_type,
        target_sha256=pending.target_sha256,
        parameters_sha256=pending.parameters_sha256,
    )
    custody.approve_operation(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_BOB_TOKEN",
        operation_id=operation.operation_id,
    )
    request = CustodyExecutionRequest(
        organization_id=ORGANIZATION,
        operation_id=operation.operation_id,
        operation_type=pending.operation_type,
        target=pending.target,
        parameters=pending.parameters,
    )
    executor = CustodyExecutorService(
        config,
        runner=LifecycleCustodyOperationRunner(config),
    )
    executor.register_asset_catalog()
    attempt = executor.execute(request=request)
    if attempt.state != "succeeded":
        raise SystemExit("custody_seed_not_succeeded")
    value = snapshot(config)
    value.update(
        {
            "command": "seed",
            "policy_generation": activation.generation,
        }
    )
    return value


def snapshot(config: GatewayConfig) -> dict[str, object]:
    _stage("policy_status_started")
    policy_status = PolicyControlService(config).status(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
    )
    _stage("policy_status_completed")
    _stage("custody_status_started")
    custody_status = CustodyControlService(config).status(
        organization_id=ORGANIZATION,
        credential_env="HORMUZ_TOKEN",
    )
    _stage("custody_status_completed")
    if policy_status.active is None:
        raise SystemExit("policy_pointer_missing")
    if config.custody_lifecycle is None:
        raise SystemExit("custody_lifecycle_missing")
    asset = config.custody_lifecycle.assets.asset(
        organization_id=ORGANIZATION,
        asset_type="provider_credential",
        asset_id="anthropic-primary",
        generation=1,
    )
    _stage("custody_projection_started")
    projection = PostgresCustodyProjectionStore(
        _required("HORMUZ_POSTGRES_DSN"),
        schema="hormuz",
        runtime_role="hormuz_runtime",
    ).load(organization_id=ORGANIZATION)
    _stage("custody_projection_completed")
    restriction = projection.restriction_for(asset)
    if restriction != "provider_credential_disabled":
        raise SystemExit("custody_restriction_missing")

    control_value = {
        "policy_active_version": policy_status.active.version_id,
        "policy_generation": policy_status.active.generation,
        "policy_administrators": _sorted_refs(
            administrator.audit_ref() for administrator in policy_status.administrators
        ),
        "custody_administrators": _sorted_refs(
            administrator.audit_ref() for administrator in custody_status.administrators
        ),
        "custody_projection_version": projection.version,
        "custody_restriction": restriction,
    }
    control_fingerprint = hashlib.sha256(
        json.dumps(control_value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    _stage("tenant_state_started")
    with postgres_transaction(
        _required("HORMUZ_POSTGRES_DSN"),
        schema="hormuz",
        runtime_role="hormuz_runtime",
        organization_id=ORGANIZATION,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS count FROM gateway_usage_events")
            usage_events = int(cursor.fetchone()["count"])
            cursor.execute("SELECT COUNT(*) AS count FROM gateway_secret_events")
            security_events = int(cursor.fetchone()["count"])
            cursor.execute(
                """
                WITH latest AS (
                    SELECT root.attempt_id,
                           (SELECT event.state
                              FROM gateway_request_attempt_events AS event
                             WHERE event.attempt_id = root.attempt_id
                             ORDER BY event.sequence DESC LIMIT 1) AS state
                      FROM gateway_request_attempts AS root
                )
                SELECT COUNT(*) AS attempts,
                       COUNT(*) FILTER (WHERE state = 'pending') AS pending,
                       COUNT(*) FILTER (WHERE state = 'outcome_unknown') AS outcome_unknown
                  FROM latest
                """
            )
            attempts = dict(cursor.fetchone())
            cursor.execute(
                """
                WITH latest AS (
                    SELECT root.attempt_id,
                           (SELECT event.state
                              FROM gateway_request_attempt_events AS event
                             WHERE event.attempt_id = root.attempt_id
                             ORDER BY event.sequence DESC LIMIT 1) AS state
                      FROM gateway_request_attempts AS root
                )
                SELECT COUNT(*) AS count
                  FROM gateway_budget_reservations AS reservation
                  JOIN latest ON latest.attempt_id = reservation.attempt_id
                 WHERE latest.state IN ('pending', 'outcome_unknown')
                   AND reservation.reserved_tokens > 0
                   AND reservation.reserved_cost_microusd > 0
                """
            )
            uncertain_reservations = int(cursor.fetchone()["count"])
    _stage("tenant_state_completed")

    _stage("tenant_isolation_started")
    with postgres_transaction(
        _required("HORMUZ_POSTGRES_DSN"),
        schema="hormuz",
        runtime_role="hormuz_runtime",
        organization_id="kubernetes-proof-isolation-tenant",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT COUNT(*) FROM gateway_usage_events) + "
                "(SELECT COUNT(*) FROM gateway_secret_events) + "
                "(SELECT COUNT(*) FROM gateway_request_attempts) AS count"
            )
            isolation_tenant_rows = int(cursor.fetchone()["count"])
    _stage("tenant_isolation_completed")

    _stage("audit_chain_started")
    auditor = PostgresUsageStore(
        _required("HORMUZ_POSTGRES_DSN"),
        organization_ids=(ORGANIZATION,),
        schema="hormuz",
        runtime_role="hormuz_runtime",
    )
    _stage("audit_store_initialized")
    chain = auditor.verify_audit_chain(organization_id=ORGANIZATION)
    _stage("audit_chain_completed")
    return {
        "schema_id": SNAPSHOT_SCHEMA_ID,
        "schema_version": 1,
        "command": "snapshot",
        "control_fingerprint": control_fingerprint,
        "policy_generation": policy_status.active.generation,
        "policy_administrator_count": len(policy_status.administrators),
        "custody_administrator_count": len(custody_status.administrators),
        "custody_projection_version": projection.version,
        "custody_restriction": restriction,
        "usage_events": usage_events,
        "security_events": security_events,
        "request_attempts": int(attempts["attempts"]),
        "pending_attempts": int(attempts["pending"]),
        "outcome_unknown_attempts": int(attempts["outcome_unknown"]),
        "uncertain_reservations": uncertain_reservations,
        "audit_chain_sequence": chain.sequence,
        "audit_chain_verified": True,
        "isolation_tenant_rows": isolation_tenant_rows,
    }


def _required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit("state_probe_environment_missing")
    return value


def _stage(name: str) -> None:
    """Emit only allowlisted operation names while preserving content-free evidence."""

    print(f"postgres_ha_state_stage={name}", file=sys.stderr, flush=True)


def _sorted_refs(values):
    return sorted(values, key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
