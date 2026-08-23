#!/usr/bin/env python3
"""Verify Hormuz's disposable PostgreSQL logical backup-and-restore drill.

This tool deliberately has a narrow operator-evidence role.  It seeds only
fixed metadata-only fixtures into isolated databases, checks a restored copy
through Hormuz's restricted repository paths, and writes a content-free
summary.  It never accepts a DSN, row, policy document, credential, or dump
as output evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path

from hormuz.config import GatewayConfig, Identity
from hormuz.policy_control import PolicyControlService
from hormuz.policy_repository import PolicyControlError
from hormuz.policy_runtime import PolicyRuntime
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    migrate_postgres,
    postgres_transaction,
    validate_postgres_identifier,
    verify_postgres_schema,
)
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import ReservationScope

try:
    from tools._verification_runtime import (
        canonical_json_sha256,
        file_sha256,
        is_pinned_image_reference,
        is_sha256_digest,
        write_private_json_evidence,
    )
except ModuleNotFoundError:  # Direct execution resolves helpers beside this script.
    from _verification_runtime import (  # type: ignore[no-redef]
        canonical_json_sha256,
        file_sha256,
        is_pinned_image_reference,
        is_sha256_digest,
        write_private_json_evidence,
    )


STATE_SCHEMA_ID = "hormuz.postgresql-recovery-drill-state"
STATE_SCHEMA_VERSION = 1
SUMMARY_SCHEMA_ID = "hormuz.postgresql-recovery-drill-summary"
SUMMARY_SCHEMA_VERSION = 1
SUMMARY_COVERAGE = "ephemeral_logical_backup_restore_only"
_POSTGRES_VERSION_PATTERN = re.compile(r"\d+\.\d+(?:\.\d+)?\Z")
_REQUIRED_RECORD_COUNT_KEYS = (
    "organizations",
    "usage_events",
    "secret_events",
    "active_budget_reservations",
    "request_attempts",
    "request_attempt_events",
    "policy_administrators",
    "policy_versions",
    "active_policy_versions",
    "policy_control_events",
    "audit_chain_epochs",
    "audit_chain_heads",
    "audit_chain_entries",
    "audit_chain_checkpoints",
)
_EXPECTED_RECORD_COUNTS = {
    "organizations": 2,
    "usage_events": 2,
    "secret_events": 2,
    "active_budget_reservations": 4,
    "request_attempts": 2,
    "request_attempt_events": 4,
    "policy_administrators": 2,
    "policy_versions": 2,
    "active_policy_versions": 2,
    "policy_control_events": 6,
    "audit_chain_epochs": 2,
    "audit_chain_heads": 2,
    "audit_chain_entries": 4,
    "audit_chain_checkpoints": 0,
}
_STATE_CHECK_KEYS = (
    "migration_ledger",
    "tenant_scoped_repository",
    "active_policy_versions",
    "audit_chain_integrity",
    "rls_without_organization_context",
)
_NEGATIVE_CHECK_KEYS = (
    "missing_dump_rejected",
    "corrupt_dump_rejected",
    "partial_recovery_not_promoted",
    "state_fingerprint_matches",
)
_FIXTURE_IDENTITIES = (
    {
        "organization_id": "acme",
        "actor_id": "acme-alice",
        "actor_name": "Acme Recovery Fixture",
        "team_id": "engineering",
        "team_name": "Engineering",
        "token_env": "HORMUZ_RECOVERY_ACME_TOKEN",
        "token": "ephemeral-recovery-acme-token",
    },
    {
        "organization_id": "beta",
        "actor_id": "beta-bob",
        "actor_name": "Beta Recovery Fixture",
        "team_id": "operations",
        "team_name": "Operations",
        "token_env": "HORMUZ_RECOVERY_BETA_TOKEN",
        "token": "ephemeral-recovery-beta-token",
    },
)


class RecoveryDrillError(RuntimeError):
    """Content-free failure from the disposable recovery drill."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    provision = commands.add_parser("provision-roles", help="create disposable restricted database roles")
    provision.add_argument("--operator-dsn", required=True)
    provision.add_argument("--runtime-role", required=True)
    provision.add_argument("--runtime-password", required=True)
    provision.add_argument("--policy-control-role", required=True)
    provision.add_argument("--policy-control-password", required=True)

    seed = commands.add_parser("seed", help="migrate and seed fixed source metadata")
    _add_connection_arguments(seed, include_operator=True)
    seed.add_argument("--state-output", required=True, type=Path)

    verify = commands.add_parser("verify", help="verify a recovered database against source state")
    _add_connection_arguments(verify, include_operator=False)
    verify.add_argument("--expected-state", required=True, type=Path)
    verify.add_argument("--state-output", required=True, type=Path)

    validate_backup = commands.add_parser("validate-backup", help="validate a dump artifact before use")
    validate_backup.add_argument("--dump", required=True, type=Path)

    corrupt = commands.add_parser("make-corrupt-copy", help="create a deliberately truncated test dump")
    corrupt.add_argument("--source", required=True, type=Path)
    corrupt.add_argument("--output", required=True, type=Path)

    mismatch = commands.add_parser("make-mismatched-state", help="create an altered expected-state fixture")
    mismatch.add_argument("--source", required=True, type=Path)
    mismatch.add_argument("--output", required=True, type=Path)

    summary = commands.add_parser("summary", help="write the content-free recovery summary")
    summary.add_argument("--database-image", required=True)
    summary.add_argument("--database-version", required=True)
    summary.add_argument("--dump", required=True, type=Path)
    summary.add_argument("--source-state", required=True, type=Path)
    summary.add_argument("--recovery-state", required=True, type=Path)
    summary.add_argument("--migrate-and-seed-ms", required=True, type=int)
    summary.add_argument("--backup-ms", required=True, type=int)
    summary.add_argument("--restore-ms", required=True, type=int)
    summary.add_argument("--verify-ms", required=True, type=int)
    summary.add_argument("--total-ms", required=True, type=int)
    summary.add_argument("--missing-dump-rejected", action="store_true")
    summary.add_argument("--corrupt-dump-rejected", action="store_true")
    summary.add_argument("--partial-recovery-not-promoted", action="store_true")
    summary.add_argument("--state-fingerprint-matches", action="store_true")
    summary.add_argument("--output", required=True, type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "provision-roles":
            provision_restricted_roles(
                operator_dsn=args.operator_dsn,
                runtime_role=args.runtime_role,
                runtime_password=args.runtime_password,
                policy_control_role=args.policy_control_role,
                policy_control_password=args.policy_control_password,
            )
            print("provisioned disposable restricted PostgreSQL roles")
            return 0
        if args.command == "seed":
            state = seed_source_state(
                operator_dsn=args.operator_dsn,
                runtime_dsn=args.runtime_dsn,
                policy_control_dsn=args.policy_control_dsn,
                schema=args.schema,
                runtime_role=args.runtime_role,
                policy_control_role=args.policy_control_role,
            )
            _write_json(args.state_output, state)
            print("seeded source recovery state")
            return 0
        if args.command == "verify":
            expected = _load_state(args.expected_state)
            state = verify_recovered_state(
                runtime_dsn=args.runtime_dsn,
                policy_control_dsn=args.policy_control_dsn,
                schema=args.schema,
                runtime_role=args.runtime_role,
                policy_control_role=args.policy_control_role,
                expected_state=expected,
            )
            _write_json(args.state_output, state)
            print("verified recovered PostgreSQL state")
            return 0
        if args.command == "validate-backup":
            _backup_metadata(args.dump)
            print("validated PostgreSQL custom-format backup artifact")
            return 0
        if args.command == "make-corrupt-copy":
            make_corrupt_copy(args.source, args.output)
            print("created deliberately corrupt backup copy")
            return 0
        if args.command == "make-mismatched-state":
            make_mismatched_state(_load_state(args.source), args.output)
            print("created altered expected recovery state")
            return 0
        if args.command == "summary":
            negative_checks = {
                "missing_dump_rejected": args.missing_dump_rejected,
                "corrupt_dump_rejected": args.corrupt_dump_rejected,
                "partial_recovery_not_promoted": args.partial_recovery_not_promoted,
                "state_fingerprint_matches": args.state_fingerprint_matches,
            }
            durations = {
                "migrate_and_seed": args.migrate_and_seed_ms,
                "backup": args.backup_ms,
                "restore": args.restore_ms,
                "verify": args.verify_ms,
                "total": args.total_ms,
            }
            result = build_recovery_summary(
                database_image=args.database_image,
                database_version=args.database_version,
                dump_path=args.dump,
                source_state=_load_state(args.source_state),
                recovery_state=_load_state(args.recovery_state),
                negative_checks=negative_checks,
                durations_ms=durations,
            )
            _write_json(args.output, result)
            print("wrote content-free PostgreSQL recovery summary")
            return 0
    except RecoveryDrillError as error:
        print(f"PostgreSQL recovery drill failed: {error}", file=sys.stderr)
        return 1
    except PostgresStorageError as error:
        print(f"PostgreSQL recovery drill failed: {error.code}", file=sys.stderr)
        return 1
    except PolicyControlError as error:
        print(f"PostgreSQL recovery drill failed: {error.code}", file=sys.stderr)
        return 1
    except ValueError:
        print("PostgreSQL recovery drill failed: recovery_fixture_configuration_invalid", file=sys.stderr)
        return 1
    raise AssertionError(f"unsupported recovery drill command: {args.command}")


def _add_connection_arguments(parser: argparse.ArgumentParser, *, include_operator: bool) -> None:
    if include_operator:
        parser.add_argument("--operator-dsn", required=True)
    parser.add_argument("--runtime-dsn", required=True)
    parser.add_argument("--policy-control-dsn", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--runtime-role", required=True)
    parser.add_argument("--policy-control-role", required=True)


def provision_restricted_roles(
    *,
    operator_dsn: str,
    runtime_role: str,
    runtime_password: str,
    policy_control_role: str,
    policy_control_password: str,
) -> None:
    """Create two non-owner roles without altering an existing principal."""

    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    policy_control_role = validate_postgres_identifier(
        policy_control_role,
        "postgres_policy_control_role",
    )
    if runtime_role == policy_control_role:
        raise RecoveryDrillError("recovery_roles_must_be_distinct")
    if not runtime_password or not policy_control_password:
        raise RecoveryDrillError("recovery_role_credential_unavailable")
    try:
        import psycopg
        from psycopg import sql
    except ImportError as error:  # pragma: no cover - package-install gate covers this path
        raise RecoveryDrillError("postgres_driver_unavailable") from error

    try:
        with psycopg.connect(operator_dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                for role, password in (
                    (runtime_role, runtime_password),
                    (policy_control_role, policy_control_password),
                ):
                    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                    if cursor.fetchone() is not None:
                        raise RecoveryDrillError("recovery_role_already_exists")
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN PASSWORD {} "
                            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                        ).format(sql.Identifier(role), sql.Literal(password))
                    )
    except RecoveryDrillError:
        raise
    except psycopg.Error as error:
        raise RecoveryDrillError("recovery_role_provisioning_failed") from error


def seed_source_state(
    *,
    operator_dsn: str,
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
) -> dict[str, object]:
    """Apply normal migrations and seed deterministic, metadata-only fixture state."""

    schema, runtime_role, policy_control_role = _validate_database_identifiers(
        schema=schema,
        runtime_role=runtime_role,
        policy_control_role=policy_control_role,
    )
    migration = migrate_postgres(
        operator_dsn,
        schema=schema,
        runtime_role=runtime_role,
        policy_control_role=policy_control_role,
    )
    if not migration.complete or migration.version != POSTGRES_SCHEMA_VERSION:
        raise RecoveryDrillError("source_migration_not_current")

    with tempfile.TemporaryDirectory(prefix="hormuz-recovery-fixture-") as directory:
        config, environment, identities = _fixture_config(
            Path(directory),
            runtime_dsn=runtime_dsn,
            policy_control_dsn=policy_control_dsn,
            schema=schema,
            runtime_role=runtime_role,
            policy_control_role=policy_control_role,
        )
        store = PostgresUsageStore(
            runtime_dsn,
            organization_ids=config.organization_ids,
            schema=schema,
            runtime_role=runtime_role,
        )
        service = PolicyControlService(config, environ=environment)
        versions: dict[str, str] = {}
        for index, identity in enumerate(identities):
            organization_id = identity.organization_id
            credential_env = identity.token_env
            service.bootstrap(organization_id=organization_id, credential_env=credential_env)
            document_path = Path(directory) / f"{organization_id}-policy.json"
            document_path.write_text(
                json.dumps(_policy_document(identity), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            staged = service.stage(
                organization_id=organization_id,
                credential_env=credential_env,
                policy_path=document_path,
            )
            active = service.activate(
                organization_id=organization_id,
                credential_env=credential_env,
                version_id=staged.version_id,
            )
            versions[organization_id] = active.version_id
            store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
                provider_reported_model="gpt-5.4-mini",
                policy_version=active.version_id,
                policy_action="allowed",
                status="succeeded",
                input_tokens=100 + index,
                output_tokens=20 + index,
                cost_microusd=300 + index,
            )
            store.record_secret_event(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
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
                reserved_tokens=50 + index,
                reserved_cost_microusd=100 + index,
                ttl_seconds=3_600,
            )
            if reservation is None:
                raise RecoveryDrillError("source_budget_reservation_missing")
            attempt = store.begin_request_attempt(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model="gpt-5.4-mini",
                resolved_alias="gpt-5.4-mini",
                upstream_model="gpt-5.4-mini",
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
                reserved_tokens=25 + index,
                reserved_cost_microusd=75 + index,
                ttl_seconds=3_600,
            )
            store.mark_request_attempt_outcome_unknown(
                attempt=attempt,
                organization_id=organization_id,
                reason_code="provider_transport_ambiguous",
            )

        return _capture_state(
            config=config,
            environment=environment,
            identities=identities,
            runtime_dsn=runtime_dsn,
            policy_control_dsn=policy_control_dsn,
            schema=schema,
            runtime_role=runtime_role,
            policy_control_role=policy_control_role,
            expected_versions=versions,
        )


def verify_recovered_state(
    *,
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
    expected_state: Mapping[str, object],
) -> dict[str, object]:
    """Verify a clean recovery database without applying migrations to it."""

    schema, runtime_role, policy_control_role = _validate_database_identifiers(
        schema=schema,
        runtime_role=runtime_role,
        policy_control_role=policy_control_role,
    )
    _validate_state(expected_state)
    with tempfile.TemporaryDirectory(prefix="hormuz-recovery-fixture-") as directory:
        config, environment, identities = _fixture_config(
            Path(directory),
            runtime_dsn=runtime_dsn,
            policy_control_dsn=policy_control_dsn,
            schema=schema,
            runtime_role=runtime_role,
            policy_control_role=policy_control_role,
        )
        state = _capture_state(
            config=config,
            environment=environment,
            identities=identities,
            runtime_dsn=runtime_dsn,
            policy_control_dsn=policy_control_dsn,
            schema=schema,
            runtime_role=runtime_role,
            policy_control_role=policy_control_role,
            expected_versions=None,
        )
    _require_matching_state(expected_state, state)
    return state


def _validate_database_identifiers(
    *,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
) -> tuple[str, str, str]:
    schema = validate_postgres_identifier(schema, "postgres_schema")
    runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
    policy_control_role = validate_postgres_identifier(
        policy_control_role,
        "postgres_policy_control_role",
    )
    if runtime_role == policy_control_role:
        raise RecoveryDrillError("recovery_roles_must_be_distinct")
    return schema, runtime_role, policy_control_role


def _fixture_config(
    directory: Path,
    *,
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
) -> tuple[GatewayConfig, dict[str, str], tuple[Identity, ...]]:
    """Build the same strict managed-policy configuration used on both sides."""

    identities = [
        {
            "token_env": str(item["token_env"]),
            "actor_id": str(item["actor_id"]),
            "actor_name": str(item["actor_name"]),
            "team_id": str(item["team_id"]),
            "team_name": str(item["team_name"]),
            "organization_id": str(item["organization_id"]),
            "identity_type": "human",
            "clearance": "confidential",
            "allowed_clients": ["codex"],
        }
        for item in _FIXTURE_IDENTITIES
    ]
    payload: dict[str, object] = {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "database": "./unused.sqlite3",
        "upstreams": {
            "openai": {
                "base_url": "https://api.openai.com",
                "api_key_env": "HORMUZ_RECOVERY_UNUSED_PROVIDER_KEY",
                "allow_response_storage": False,
                "allow_background": False,
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "api_key_env": "HORMUZ_RECOVERY_UNUSED_PROVIDER_KEY",
            },
        },
        "authentication": {"oidc": {"issuers": []}},
        "identities": identities,
        "model_routes": {
            "gpt-5.4-mini": {
                "protocol": "openai",
                "upstream_model": "gpt-5.4-mini",
                "input_cost_per_million": 0.75,
                "cache_read_cost_per_million": 0.075,
                "cache_write_cost_per_million": 0.75,
                "output_cost_per_million": 4.5,
            }
        },
        "egress_controls": {"secrets": {"mode": "redact", "builtins": True, "custom_secret_envs": []}},
        "usage_storage": {
            "backend": "postgresql",
            "postgres_dsn_env": "HORMUZ_RECOVERY_RUNTIME_DSN",
            "postgres_migration_dsn_env": "HORMUZ_RECOVERY_OPERATOR_DSN",
            "postgres_schema": schema,
            "postgres_runtime_role": runtime_role,
        },
        "policy_control": {
            "mode": "postgresql",
            "postgres_control_dsn_env": "HORMUZ_RECOVERY_POLICY_CONTROL_DSN",
            "postgres_control_role": policy_control_role,
            "bootstrap_administrators": [
                {
                    "organization_id": str(item["organization_id"]),
                    "actor_id": str(item["actor_id"]),
                }
                for item in _FIXTURE_IDENTITIES
            ],
        },
    }
    config_path = directory / "recovery-fixture.json"
    config_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    environment = {
        "HORMUZ_RECOVERY_RUNTIME_DSN": runtime_dsn,
        "HORMUZ_RECOVERY_OPERATOR_DSN": "operator-dsn-is-not-used-after-migration",
        "HORMUZ_RECOVERY_POLICY_CONTROL_DSN": policy_control_dsn,
    }
    environment.update({str(item["token_env"]): str(item["token"]) for item in _FIXTURE_IDENTITIES})
    config = GatewayConfig.load(config_path, environ=environment)
    resolved = tuple(config.identities_by_actor[str(item["actor_id"])] for item in _FIXTURE_IDENTITIES)
    return config, environment, resolved


def _policy_document(identity: Identity) -> dict[str, object]:
    return {
        "schema_id": "hormuz.policy-document",
        "schema_version": 1,
        "organization_id": identity.organization_id,
        "policies": {
            "organization": {
                "allowed_clients": ["codex"],
                "allowed_models": ["gpt-5.4-mini"],
                "max_output_tokens": 1_000,
                "monthly_budget_usd": 100,
                "per_actor_monthly_budget_usd": 50,
            },
            "teams": {
                identity.team_id: {
                    "allowed_models": ["gpt-5.4-mini"],
                    "fallback_models": {"openai": "gpt-5.4-mini"},
                    "max_output_tokens": 800,
                    "monthly_budget_usd": 75,
                }
            },
            "actors": {},
        },
        "egress_controls": {
            "openai": {"allow_response_storage": False, "allow_background": False},
            "secrets": {"mode": "redact"},
        },
    }


def _capture_state(
    *,
    config: GatewayConfig,
    environment: Mapping[str, str],
    identities: tuple[Identity, ...],
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
    expected_versions: Mapping[str, str] | None,
) -> dict[str, object]:
    """Exercise restricted read paths and fingerprint all relevant state in memory."""

    runtime_status = verify_postgres_schema(runtime_dsn, schema=schema, runtime_role=runtime_role)
    control_status = verify_postgres_schema(
        policy_control_dsn,
        schema=schema,
        runtime_role=policy_control_role,
        verify_runtime_schema=False,
    )
    if (
        not runtime_status.complete
        or not control_status.complete
        or runtime_status.version != POSTGRES_SCHEMA_VERSION
        or control_status.version != POSTGRES_SCHEMA_VERSION
    ):
        raise RecoveryDrillError("recovery_migration_ledger_invalid")

    store = PostgresUsageStore(
        runtime_dsn,
        organization_ids=config.organization_ids,
        schema=schema,
        runtime_role=runtime_role,
    )
    store.verify_ready()
    service = PolicyControlService(config, environ=environment)
    runtime = PolicyRuntime(config, environ=environment)

    for identity in identities:
        organization_id = identity.organization_id
        usage_events = store.audit_events(
            since="2000-01-01T00:00:00+00:00",
            kind="usage",
            organization_id=organization_id,
        )
        secret_events = store.audit_events(
            since="2000-01-01T00:00:00+00:00",
            kind="security",
            organization_id=organization_id,
        )
        if (
            len(usage_events) != 1
            or len(secret_events) != 1
            or any(event.get("organization_id") != organization_id for event in (*usage_events, *secret_events))
        ):
            raise RecoveryDrillError("recovery_tenant_repository_check_failed")
        if store.monthly_totals(organization_id=organization_id).requests != 1:
            raise RecoveryDrillError("recovery_tenant_repository_check_failed")
        if store.monthly_secret_totals(organization_id=organization_id).events != 1:
            raise RecoveryDrillError("recovery_tenant_repository_check_failed")
        audit_chain = store.verify_audit_chain(organization_id=organization_id)
        if audit_chain.sequence != 2 or audit_chain.head_digest is None:
            raise RecoveryDrillError("recovery_audit_chain_check_failed")
        if store.active_budget_reservations(organization_id=organization_id) != 2:
            raise RecoveryDrillError("recovery_budget_reservation_check_failed")
        with postgres_transaction(
            runtime_dsn,
            schema=schema,
            runtime_role=runtime_role,
            organization_id=organization_id,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state, reason_code, usage_event_id "
                    "FROM gateway_request_attempt_events ORDER BY attempt_id, sequence"
                )
                attempt_events = [dict(row) for row in cursor.fetchall()]
        if attempt_events != [
            {"state": "pending", "reason_code": None, "usage_event_id": None},
            {
                "state": "outcome_unknown",
                "reason_code": "provider_transport_ambiguous",
                "usage_event_id": None,
            },
        ]:
            raise RecoveryDrillError("recovery_request_attempt_check_failed")

        status = service.status(organization_id=organization_id, credential_env=identity.token_env)
        snapshot = runtime.snapshot_for(identity)
        if (
            not status.initialized
            or status.active is None
            or len(status.versions) != 1
            or len(status.administrators) != 1
            or status.active.version_id != snapshot.policy_version
        ):
            raise RecoveryDrillError("recovery_active_policy_check_failed")
        if expected_versions is not None and status.active.version_id != expected_versions.get(organization_id):
            raise RecoveryDrillError("source_active_policy_check_failed")

    _verify_rls_without_organization_context(
        runtime_dsn=runtime_dsn,
        policy_control_dsn=policy_control_dsn,
        schema=schema,
    )
    records = _collect_restricted_records(
        runtime_dsn=runtime_dsn,
        policy_control_dsn=policy_control_dsn,
        schema=schema,
        runtime_role=runtime_role,
        policy_control_role=policy_control_role,
        organization_ids=config.organization_ids,
    )
    counts = {
        "organizations": len(records["policy_tenants"]),
        "usage_events": len(records["gateway_usage_events"]),
        "secret_events": len(records["gateway_secret_events"]),
        "active_budget_reservations": len(records["gateway_budget_reservations"]),
        "request_attempts": len(records["gateway_request_attempts"]),
        "request_attempt_events": len(records["gateway_request_attempt_events"]),
        "policy_administrators": len(records["policy_administrators"]),
        "policy_versions": len(records["policy_versions"]),
        "active_policy_versions": len(records["policy_active_versions"]),
        "policy_control_events": len(records["policy_control_events"]),
        "audit_chain_epochs": len(records["gateway_audit_chain_epochs"]),
        "audit_chain_heads": len(records["gateway_audit_chain_heads"]),
        "audit_chain_entries": len(records["gateway_audit_chain_entries"]),
        "audit_chain_checkpoints": len(records["gateway_audit_chain_checkpoints"]),
    }
    if counts != _EXPECTED_RECORD_COUNTS:
        raise RecoveryDrillError("recovery_fixture_state_unexpected")

    material = {
        "migration_ledger": records["hormuz_schema_migrations"],
        "records": {key: value for key, value in records.items() if key != "hormuz_schema_migrations"},
    }
    state = {
        "schema_id": STATE_SCHEMA_ID,
        "schema_version": STATE_SCHEMA_VERSION,
        "state_fingerprint": _sha256_json(material),
        "migration_version": POSTGRES_SCHEMA_VERSION,
        "record_counts": counts,
        "checks": {
            "migration_ledger": True,
            "tenant_scoped_repository": True,
            "active_policy_versions": True,
            "audit_chain_integrity": True,
            "rls_without_organization_context": True,
        },
    }
    _validate_state(state)
    return state


def _collect_restricted_records(
    *,
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
    runtime_role: str,
    policy_control_role: str,
    organization_ids: tuple[str, ...],
) -> dict[str, list[dict[str, object]]]:
    """Read every relevant table only through the role that owns its surface."""

    migration_rows = _rows_for_role(
        dsn=runtime_dsn,
        schema=schema,
        role=runtime_role,
        organization_id=organization_ids[0],
        table="hormuz_schema_migrations",
        order_by="version",
    )
    runtime_tables = (
        ("gateway_usage_events", "id"),
        ("gateway_secret_events", "id"),
        ("gateway_budget_reservations", "id"),
        ("gateway_request_attempts", "attempt_id"),
        ("gateway_request_attempt_events", "attempt_id, sequence"),
        ("gateway_audit_chain_epochs", "organization_id, chain_epoch"),
        ("gateway_audit_chain_heads", "organization_id"),
        ("gateway_audit_chain_entries", "organization_id, chain_epoch, sequence"),
        ("gateway_audit_chain_checkpoints", "checkpoint_id"),
        ("policy_versions", "organization_id, version_id"),
        ("policy_active_versions", "organization_id"),
    )
    control_tables = (
        ("policy_tenants", "organization_id"),
        ("policy_administrators", "organization_id, identity_key"),
        ("policy_control_events", "organization_id, event_id"),
    )
    records: dict[str, list[dict[str, object]]] = {"hormuz_schema_migrations": migration_rows}
    for table, order_by in runtime_tables:
        records[table] = _rows_for_organizations(
            dsn=runtime_dsn,
            schema=schema,
            role=runtime_role,
            organization_ids=organization_ids,
            table=table,
            order_by=order_by,
        )
    for table, order_by in control_tables:
        records[table] = _rows_for_organizations(
            dsn=policy_control_dsn,
            schema=schema,
            role=policy_control_role,
            organization_ids=organization_ids,
            table=table,
            order_by=order_by,
        )
    return records


def _rows_for_organizations(
    *,
    dsn: str,
    schema: str,
    role: str,
    organization_ids: tuple[str, ...],
    table: str,
    order_by: str,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for organization_id in organization_ids:
        rows.extend(
            _rows_for_role(
                dsn=dsn,
                schema=schema,
                role=role,
                organization_id=organization_id,
                table=table,
                order_by=order_by,
            )
        )
    return rows


def _rows_for_role(
    *,
    dsn: str,
    schema: str,
    role: str,
    organization_id: str,
    table: str,
    order_by: str,
) -> list[dict[str, object]]:
    # Both identifiers are chosen above from a fixed closed set.  The schema
    # itself is validated before it reaches postgres_transaction.
    with postgres_transaction(
        dsn,
        schema=schema,
        runtime_role=role,
        organization_id=organization_id,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
            return [dict(row) for row in cursor.fetchall()]


def _verify_rls_without_organization_context(
    *,
    runtime_dsn: str,
    policy_control_dsn: str,
    schema: str,
) -> None:
    """Prove restricted logins cannot discover tenant rows without SET LOCAL."""

    try:
        import psycopg
        from psycopg import sql
    except ImportError as error:  # pragma: no cover - package-install gate covers this path
        raise RecoveryDrillError("postgres_driver_unavailable") from error
    required = (
        (runtime_dsn, "gateway_usage_events"),
        (runtime_dsn, "gateway_secret_events"),
        (runtime_dsn, "gateway_budget_reservations"),
        (runtime_dsn, "gateway_request_attempts"),
        (runtime_dsn, "gateway_request_attempt_events"),
        (runtime_dsn, "gateway_audit_chain_epochs"),
        (runtime_dsn, "gateway_audit_chain_heads"),
        (runtime_dsn, "gateway_audit_chain_entries"),
        (runtime_dsn, "gateway_audit_chain_checkpoints"),
        (runtime_dsn, "policy_versions"),
        (runtime_dsn, "policy_active_versions"),
        (policy_control_dsn, "policy_tenants"),
        (policy_control_dsn, "policy_administrators"),
        (policy_control_dsn, "policy_control_events"),
    )
    try:
        for dsn, table in required:
            with psycopg.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("SELECT COUNT(*) FROM {}.{}").format(
                            sql.Identifier(schema),
                            sql.Identifier(table),
                        )
                    )
                    row = cursor.fetchone()
                    if row is None or int(row[0]) != 0:
                        raise RecoveryDrillError("recovery_rls_without_context_failed")
    except RecoveryDrillError:
        raise
    except psycopg.Error as error:
        raise RecoveryDrillError("recovery_rls_without_context_failed") from error


def _load_state(path: Path) -> dict[str, object]:
    value = _load_json(path, "recovery_state")
    _validate_state(value)
    return value


def _validate_state(value: Mapping[str, object]) -> None:
    if set(value) != {
        "schema_id",
        "schema_version",
        "state_fingerprint",
        "migration_version",
        "record_counts",
        "checks",
    }:
        raise RecoveryDrillError("recovery_state_schema_invalid")
    if value.get("schema_id") != STATE_SCHEMA_ID or value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise RecoveryDrillError("recovery_state_schema_invalid")
    _require_digest(value.get("state_fingerprint"), "recovery_state_schema_invalid")
    if value.get("migration_version") != POSTGRES_SCHEMA_VERSION:
        raise RecoveryDrillError("recovery_state_schema_invalid")
    counts = _mapping(value.get("record_counts"), "recovery_state_schema_invalid")
    if set(counts) != set(_REQUIRED_RECORD_COUNT_KEYS):
        raise RecoveryDrillError("recovery_state_schema_invalid")
    if any(not isinstance(counts[key], int) or isinstance(counts[key], bool) or counts[key] < 0 for key in counts):
        raise RecoveryDrillError("recovery_state_schema_invalid")
    if dict(counts) != _EXPECTED_RECORD_COUNTS:
        raise RecoveryDrillError("recovery_fixture_state_unexpected")
    checks = _mapping(value.get("checks"), "recovery_state_schema_invalid")
    if set(checks) != set(_STATE_CHECK_KEYS) or any(checks[key] is not True for key in checks):
        raise RecoveryDrillError("recovery_state_schema_invalid")


def _require_matching_state(expected: Mapping[str, object], recovered: Mapping[str, object]) -> None:
    _validate_state(expected)
    _validate_state(recovered)
    if expected["state_fingerprint"] != recovered["state_fingerprint"]:
        raise RecoveryDrillError("recovery_state_fingerprint_mismatch")
    if expected["record_counts"] != recovered["record_counts"]:
        raise RecoveryDrillError("recovery_state_fingerprint_mismatch")


def assert_mismatch_is_rejected(state: Mapping[str, object]) -> None:
    """Exercise the explicit mismatch branch without accepting altered evidence."""

    _validate_state(state)
    mismatched = dict(state)
    original = str(state["state_fingerprint"])
    mismatched["state_fingerprint"] = "sha256:" + ("0" * 64 if original != "sha256:" + "0" * 64 else "1" * 64)
    try:
        _require_matching_state(state, mismatched)
    except RecoveryDrillError as error:
        if str(error) == "recovery_state_fingerprint_mismatch":
            return
        raise
    raise RecoveryDrillError("recovery_state_mismatch_was_accepted")


def make_mismatched_state(source: Mapping[str, object], output: Path) -> None:
    """Create a valid-shape state with an altered fingerprint for a negative check."""

    _validate_state(source)
    mismatched = dict(source)
    original = str(source["state_fingerprint"])
    mismatched["state_fingerprint"] = "sha256:" + ("0" * 64 if original != "sha256:" + "0" * 64 else "1" * 64)
    _validate_state(mismatched)
    _write_json(output, mismatched)


def _backup_metadata(path: Path) -> dict[str, object]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise RecoveryDrillError("recovery_backup_missing") from error
    if size <= 0:
        raise RecoveryDrillError("recovery_backup_empty")
    try:
        digest = file_sha256(path)
    except OSError as error:
        raise RecoveryDrillError("recovery_backup_unreadable") from error
    return {"format": "pg_dump_custom", "sha256": digest, "bytes": size}


def make_corrupt_copy(source: Path, output: Path) -> None:
    """Create a truncated copy that pg_restore must reject in quarantine."""

    metadata = _backup_metadata(source)
    source_size = int(metadata["bytes"])
    if source_size < 4:
        raise RecoveryDrillError("recovery_backup_too_small_to_corrupt")
    try:
        content = source.read_bytes()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content[: max(1, source_size // 2)])
    except OSError as error:
        raise RecoveryDrillError("recovery_corrupt_copy_unavailable") from error
    if output.stat().st_size >= source_size:
        raise RecoveryDrillError("recovery_corrupt_copy_unavailable")


def build_recovery_summary(
    *,
    database_image: str,
    database_version: str,
    dump_path: Path,
    source_state: Mapping[str, object],
    recovery_state: Mapping[str, object],
    negative_checks: Mapping[str, object],
    durations_ms: Mapping[str, object],
) -> dict[str, object]:
    """Create the only retained artifact after every positive and negative check passes."""

    if not is_pinned_image_reference(database_image, image_name="postgres"):
        raise RecoveryDrillError("recovery_database_image_not_pinned")
    if _POSTGRES_VERSION_PATTERN.fullmatch(database_version) is None:
        raise RecoveryDrillError("recovery_database_version_invalid")
    _require_matching_state(source_state, recovery_state)
    backup = _backup_metadata(dump_path)
    if set(negative_checks) != set(_NEGATIVE_CHECK_KEYS) or any(
        negative_checks[key] is not True for key in negative_checks
    ):
        raise RecoveryDrillError("recovery_negative_check_missing")
    if set(durations_ms) != {"migrate_and_seed", "backup", "restore", "verify", "total"}:
        raise RecoveryDrillError("recovery_duration_invalid")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in durations_ms.values()
    ):
        raise RecoveryDrillError("recovery_duration_invalid")
    if int(durations_ms["total"]) < sum(
        int(durations_ms[key]) for key in ("migrate_and_seed", "backup", "restore", "verify")
    ):
        raise RecoveryDrillError("recovery_duration_invalid")

    summary: dict[str, object] = {
        "schema_id": SUMMARY_SCHEMA_ID,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "coverage": SUMMARY_COVERAGE,
        "verdict": "pass",
        "database": {"image": database_image, "version": database_version},
        "backup": backup,
        "state": {
            "source_fingerprint": source_state["state_fingerprint"],
            "recovery_fingerprint": recovery_state["state_fingerprint"],
            "migration_version": source_state["migration_version"],
            "record_counts": source_state["record_counts"],
        },
        "checks": {
            **source_state["checks"],
            **negative_checks,
        },
        "durations_ms": dict(durations_ms),
    }
    _validate_summary(summary)
    return summary


def _validate_summary(value: Mapping[str, object]) -> None:
    expected_keys = {
        "schema_id",
        "schema_version",
        "coverage",
        "verdict",
        "database",
        "backup",
        "state",
        "checks",
        "durations_ms",
    }
    if set(value) != expected_keys:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    if (
        value.get("schema_id") != SUMMARY_SCHEMA_ID
        or value.get("schema_version") != SUMMARY_SCHEMA_VERSION
        or value.get("coverage") != SUMMARY_COVERAGE
        or value.get("verdict") != "pass"
    ):
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    database = _mapping(value.get("database"), "recovery_summary_schema_invalid")
    if set(database) != {"image", "version"}:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    if not is_pinned_image_reference(database.get("image"), image_name="postgres"):
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    if not isinstance(database.get("version"), str) or _POSTGRES_VERSION_PATTERN.fullmatch(database["version"]) is None:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    backup = _mapping(value.get("backup"), "recovery_summary_schema_invalid")
    if set(backup) != {"format", "sha256", "bytes"} or backup.get("format") != "pg_dump_custom":
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    _require_digest(backup.get("sha256"), "recovery_summary_schema_invalid")
    if not isinstance(backup.get("bytes"), int) or isinstance(backup["bytes"], bool) or backup["bytes"] <= 0:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    state = _mapping(value.get("state"), "recovery_summary_schema_invalid")
    if set(state) != {"source_fingerprint", "recovery_fingerprint", "migration_version", "record_counts"}:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    _require_digest(state.get("source_fingerprint"), "recovery_summary_schema_invalid")
    _require_digest(state.get("recovery_fingerprint"), "recovery_summary_schema_invalid")
    if state["source_fingerprint"] != state["recovery_fingerprint"]:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    if state.get("migration_version") != POSTGRES_SCHEMA_VERSION:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    counts = _mapping(state.get("record_counts"), "recovery_summary_schema_invalid")
    if dict(counts) != _EXPECTED_RECORD_COUNTS:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    checks = _mapping(value.get("checks"), "recovery_summary_schema_invalid")
    if set(checks) != set((*_STATE_CHECK_KEYS, *_NEGATIVE_CHECK_KEYS)) or any(
        checks[key] is not True for key in checks
    ):
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    durations = _mapping(value.get("durations_ms"), "recovery_summary_schema_invalid")
    if set(durations) != {"migrate_and_seed", "backup", "restore", "verify", "total"}:
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    if any(
        not isinstance(duration, int) or isinstance(duration, bool) or duration < 0
        for duration in durations.values()
    ):
        raise RecoveryDrillError("recovery_summary_schema_invalid")
    if int(durations["total"]) < sum(
        int(durations[key]) for key in ("migrate_and_seed", "backup", "restore", "verify")
    ):
        raise RecoveryDrillError("recovery_summary_schema_invalid")


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RecoveryDrillError(f"{label}_unavailable") from error
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RecoveryDrillError(f"{label}_invalid") from error
    return _mapping(value, f"{label}_invalid")


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    try:
        write_private_json_evidence(path, value, indent=2)
    except OSError as error:
        raise RecoveryDrillError("recovery_evidence_write_failed") from error


def _sha256_json(value: object) -> str:
    return canonical_json_sha256(_normalize(value))


def _normalize(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _mapping(value: object, code: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RecoveryDrillError(code)
    return value


def _require_digest(value: object, code: str) -> None:
    if not is_sha256_digest(value):
        raise RecoveryDrillError(code)


if __name__ == "__main__":
    raise SystemExit(main())
