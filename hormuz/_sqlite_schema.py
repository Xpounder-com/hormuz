"""SQLite schema creation, compatibility inspection, and migrations.

Runtime operations remain in :mod:`hormuz.store`.  This module owns only the
SQLite durable-shape lifecycle and deliberately exposes no application query
or transaction boundary.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol


SQLITE_SCHEMA_VERSION = 7


class StorageErrorFactory(Protocol):
    def __call__(self, code: str) -> RuntimeError: ...


MigrationApplier = Callable[[sqlite3.Connection, int], None]


_MIGRATION_LEDGER_STATEMENT = """
CREATE TABLE IF NOT EXISTS hormuz_schema_migrations (
    version INTEGER PRIMARY KEY,
    state TEXT NOT NULL,
    applied_at TEXT
)
"""


_CORE_BOOTSTRAP_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS gateway_usage_events (
        id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        evidence_schema_id TEXT NOT NULL DEFAULT 'hormuz.audit-event',
        evidence_schema_version INTEGER NOT NULL DEFAULT 2,
        organization_id TEXT NOT NULL DEFAULT 'organization',
        actor_id TEXT NOT NULL,
        actor_name TEXT NOT NULL,
        team_id TEXT NOT NULL,
        team_name TEXT NOT NULL,
        identity_type TEXT NOT NULL DEFAULT 'human',
        authentication_source TEXT NOT NULL DEFAULT 'static',
        client TEXT NOT NULL,
        protocol TEXT NOT NULL,
        requested_model TEXT NOT NULL,
        resolved_alias TEXT,
        upstream_model TEXT,
        provider_reported_model TEXT,
        policy_version TEXT NOT NULL DEFAULT 'legacy-unversioned',
        policy_action TEXT NOT NULL,
        status TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        reasoning_tokens INTEGER NOT NULL DEFAULT 0,
        cost_microusd INTEGER NOT NULL DEFAULT 0,
        cost_basis TEXT NOT NULL DEFAULT 'configured_rate_card_estimate',
        allocation_basis TEXT NOT NULL DEFAULT 'direct_gateway_request',
        coverage TEXT NOT NULL DEFAULT 'gateway_captured_requests_only',
        provider_request_id TEXT,
        redaction_count INTEGER NOT NULL DEFAULT 0,
        redaction_rules TEXT NOT NULL DEFAULT '[]'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_usage_occurred_at
        ON gateway_usage_events(occurred_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_usage_actor_month
        ON gateway_usage_events(actor_id, occurred_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_usage_team_month
        ON gateway_usage_events(team_id, occurred_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_secret_events (
        id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        evidence_schema_id TEXT NOT NULL DEFAULT 'hormuz.audit-event',
        evidence_schema_version INTEGER NOT NULL DEFAULT 2,
        organization_id TEXT NOT NULL DEFAULT 'organization',
        actor_id TEXT NOT NULL,
        actor_name TEXT NOT NULL,
        team_id TEXT NOT NULL,
        team_name TEXT NOT NULL,
        identity_type TEXT NOT NULL DEFAULT 'human',
        authentication_source TEXT NOT NULL DEFAULT 'static',
        client TEXT NOT NULL,
        protocol TEXT NOT NULL,
        requested_model TEXT NOT NULL,
        policy_version TEXT NOT NULL DEFAULT 'legacy-unversioned',
        coverage TEXT NOT NULL DEFAULT 'gateway_captured_requests_only',
        action TEXT NOT NULL,
        detection_count INTEGER NOT NULL,
        rules TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_secret_occurred_at
        ON gateway_secret_events(occurred_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_secret_actor_month
        ON gateway_secret_events(actor_id, occurred_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_secret_team_month
        ON gateway_secret_events(team_id, occurred_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_budget_reservations (
        id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        organization_id TEXT NOT NULL DEFAULT 'organization',
        actor_id TEXT NOT NULL,
        team_id TEXT NOT NULL,
        reserved_tokens INTEGER NOT NULL,
        reserved_cost_microusd INTEGER NOT NULL,
        attempt_id TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_reservation_expires_at
        ON gateway_budget_reservations(expires_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_reservation_actor
        ON gateway_budget_reservations(actor_id, expires_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_reservation_team
        ON gateway_budget_reservations(team_id, expires_at)
    """,
)


_REQUEST_ATTEMPT_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS gateway_request_attempts (
        attempt_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        evidence_schema_id TEXT NOT NULL,
        evidence_schema_version INTEGER NOT NULL,
        organization_id TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        actor_name TEXT NOT NULL,
        team_id TEXT NOT NULL,
        team_name TEXT NOT NULL,
        identity_type TEXT NOT NULL,
        authentication_source TEXT NOT NULL,
        client TEXT NOT NULL,
        protocol TEXT NOT NULL,
        requested_model TEXT NOT NULL,
        resolved_alias TEXT,
        upstream_model TEXT,
        policy_version TEXT NOT NULL,
        policy_action TEXT NOT NULL,
        redaction_count INTEGER NOT NULL,
        redaction_rules TEXT NOT NULL,
        reserved_tokens INTEGER NOT NULL,
        reserved_cost_microusd INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_attempt_organization_created
        ON gateway_request_attempts(organization_id, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_request_attempt_events (
        id TEXT PRIMARY KEY,
        attempt_id TEXT NOT NULL,
        organization_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        event_schema_id TEXT NOT NULL,
        event_schema_version INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        state TEXT NOT NULL,
        reason_code TEXT,
        usage_event_id TEXT,
        UNIQUE(attempt_id, sequence)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_attempt_event_organization_attempt
        ON gateway_request_attempt_events(organization_id, attempt_id, sequence)
    """,
)


_AUDIT_CHAIN_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS gateway_audit_chain_epochs (
        organization_id TEXT NOT NULL,
        chain_version INTEGER NOT NULL,
        chain_epoch INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        predecessor_chain_epoch INTEGER,
        predecessor_sequence INTEGER,
        predecessor_head_digest TEXT,
        PRIMARY KEY (organization_id, chain_epoch),
        CHECK (chain_version = 1),
        CHECK (chain_epoch >= 1),
        CHECK (reason_code IN ('initial_adoption', 'restore', 'migration')),
        CHECK (
            (predecessor_chain_epoch IS NULL AND predecessor_sequence IS NULL AND predecessor_head_digest IS NULL)
            OR (
                predecessor_chain_epoch >= 1
                AND predecessor_sequence >= 1
                AND predecessor_head_digest IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_audit_chain_heads (
        organization_id TEXT PRIMARY KEY,
        chain_version INTEGER NOT NULL,
        chain_epoch INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        head_digest TEXT,
        FOREIGN KEY (organization_id, chain_epoch)
            REFERENCES gateway_audit_chain_epochs (organization_id, chain_epoch),
        CHECK (chain_version = 1),
        CHECK (chain_epoch >= 1),
        CHECK (sequence >= 0),
        CHECK (sequence = 0 OR head_digest IS NOT NULL)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_audit_chain_entries (
        organization_id TEXT NOT NULL,
        chain_version INTEGER NOT NULL,
        chain_epoch INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        entry_schema_id TEXT NOT NULL,
        entry_schema_version INTEGER NOT NULL,
        event_id TEXT NOT NULL,
        previous_digest TEXT,
        event_digest TEXT NOT NULL,
        event_json TEXT NOT NULL,
        appended_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, chain_epoch, sequence),
        UNIQUE (organization_id, event_id),
        FOREIGN KEY (organization_id, chain_epoch)
            REFERENCES gateway_audit_chain_epochs (organization_id, chain_epoch),
        CHECK (chain_version = 1),
        CHECK (chain_epoch >= 1),
        CHECK (sequence >= 1),
        CHECK (entry_schema_id = 'hormuz.commit-audit-chain-entry'),
        CHECK (entry_schema_version = 1)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_audit_chain_entries_event
        ON gateway_audit_chain_entries(organization_id, event_id)
    """,
    """
    CREATE TRIGGER IF NOT EXISTS gateway_audit_chain_epochs_no_update
        BEFORE UPDATE ON gateway_audit_chain_epochs
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_epoch_immutable');
        END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS gateway_audit_chain_epochs_no_delete
        BEFORE DELETE ON gateway_audit_chain_epochs
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_epoch_immutable');
        END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS gateway_audit_chain_entries_no_update
        BEFORE UPDATE ON gateway_audit_chain_entries
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_entry_immutable');
        END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS gateway_audit_chain_entries_no_delete
        BEFORE DELETE ON gateway_audit_chain_entries
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_entry_immutable');
        END
    """,
    """
    CREATE TABLE IF NOT EXISTS gateway_audit_chain_checkpoints (
        checkpoint_id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL,
        chain_version INTEGER NOT NULL,
        chain_epoch INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        head_digest TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL,
        anchor_backend TEXT NOT NULL,
        object_version TEXT,
        anchored_at TEXT NOT NULL,
        FOREIGN KEY (organization_id, chain_epoch)
            REFERENCES gateway_audit_chain_epochs (organization_id, chain_epoch),
        CHECK (chain_version = 1),
        CHECK (chain_epoch >= 1),
        CHECK (sequence >= 1)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_gateway_audit_chain_checkpoint_latest
        ON gateway_audit_chain_checkpoints(organization_id, anchored_at DESC)
    """,
    """
    CREATE TRIGGER IF NOT EXISTS gateway_audit_chain_checkpoints_no_update
        BEFORE UPDATE ON gateway_audit_chain_checkpoints
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_checkpoint_immutable');
        END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS gateway_audit_chain_checkpoints_no_delete
        BEFORE DELETE ON gateway_audit_chain_checkpoints
        BEGIN
            SELECT RAISE(ABORT, 'audit_chain_checkpoint_immutable');
        END
    """,
)


_RESERVATION_ATTEMPT_INDEX_STATEMENT = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_reservation_attempt
    ON gateway_budget_reservations(attempt_id)
    WHERE attempt_id IS NOT NULL
"""


_BOOTSTRAP_SCHEMA_SQL = ";\n".join(
    statement.strip().rstrip(";")
    for statement in (
        *_CORE_BOOTSTRAP_STATEMENTS,
        *_REQUEST_ATTEMPT_SCHEMA_STATEMENTS,
        *_AUDIT_CHAIN_SCHEMA_STATEMENTS,
    )
) + ";\n"


def initialize_sqlite_schema(
    connection: sqlite3.Connection,
    *,
    schema_version: int,
    maximum_supported_schema_version: int,
    apply_migration: MigrationApplier,
    error_factory: StorageErrorFactory,
) -> None:
    connection.execute(_MIGRATION_LEDGER_STATEMENT)
    migrations = _migration_states(connection)
    if not migrations:
        # Never bootstrap later objects when a migration ledger already
        # exists. Supported upgrades remain observable ledgered transitions.
        connection.executescript(_BOOTSTRAP_SCHEMA_SQL)
    _validate_migration_states(
        migrations,
        maximum_supported_schema_version=maximum_supported_schema_version,
        error_factory=error_factory,
    )
    if migrations:
        verify_applied_sqlite_schema_shape(
            connection,
            version=max(migrations),
            error_factory=error_factory,
        )
    for version in range(1, schema_version + 1):
        if version in migrations:
            continue
        if version > maximum_supported_schema_version:
            raise error_factory("storage_schema_newer_than_binary")
        connection.execute(
            "INSERT INTO hormuz_schema_migrations (version, state) VALUES (?, 'applying')",
            (version,),
        )
        apply_migration(connection, version)
        connection.execute(
            "UPDATE hormuz_schema_migrations SET state = 'applied', applied_at = ? WHERE version = ?",
            (datetime.now(timezone.utc).isoformat(), version),
        )


def verify_sqlite_schema_ready(
    connection: sqlite3.Connection,
    *,
    schema_version: int,
    maximum_supported_schema_version: int,
    error_factory: StorageErrorFactory,
) -> None:
    states = _migration_states(connection)
    _validate_migration_states(
        states,
        maximum_supported_schema_version=maximum_supported_schema_version,
        error_factory=error_factory,
    )
    if states:
        verify_applied_sqlite_schema_shape(
            connection,
            version=max(states),
            error_factory=error_factory,
        )
    if any(states.get(version) != "applied" for version in range(1, schema_version + 1)):
        raise error_factory("storage_schema_unavailable")


def verify_applied_sqlite_schema_shape(
    connection: sqlite3.Connection,
    *,
    version: int,
    error_factory: StorageErrorFactory,
) -> None:
    """Reject a migration ledger whose claimed durable objects are absent."""

    if version >= 5:
        from ._portfolio_schema import verify_sqlite_registry
        verify_sqlite_registry(connection, error_factory)
    if version >= 6:
        from ._attribution_schema import verify_sqlite_attribution
        verify_sqlite_attribution(connection, error_factory)
    if version >= 7:
        from ._outcome_schema import verify_sqlite_outcomes
        verify_sqlite_outcomes(connection, error_factory)

    required = {
        "gateway_usage_events": {
            "id",
            "occurred_at",
            "evidence_schema_id",
            "evidence_schema_version",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "policy_version",
            "policy_action",
            "status",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "cost_microusd",
            "cost_basis",
            "allocation_basis",
            "coverage",
            "redaction_count",
            "redaction_rules",
        },
        "gateway_secret_events": {
            "id",
            "occurred_at",
            "evidence_schema_id",
            "evidence_schema_version",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "policy_version",
            "coverage",
            "action",
            "detection_count",
            "rules",
        },
        "gateway_budget_reservations": {
            "id",
            "created_at",
            "expires_at",
            "organization_id",
            "actor_id",
            "team_id",
            "reserved_tokens",
            "reserved_cost_microusd",
        },
    }
    if version >= 3:
        required["gateway_budget_reservations"].add("attempt_id")
        required["gateway_request_attempts"] = {
            "attempt_id",
            "created_at",
            "evidence_schema_id",
            "evidence_schema_version",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "policy_version",
            "policy_action",
            "redaction_count",
            "redaction_rules",
            "reserved_tokens",
            "reserved_cost_microusd",
        }
        required["gateway_request_attempt_events"] = {
            "id",
            "attempt_id",
            "organization_id",
            "occurred_at",
            "event_schema_id",
            "event_schema_version",
            "sequence",
            "state",
            "reason_code",
            "usage_event_id",
        }
    if version >= 4:
        required["gateway_audit_chain_epochs"] = {
            "organization_id",
            "chain_version",
            "chain_epoch",
            "created_at",
            "reason_code",
            "predecessor_chain_epoch",
            "predecessor_sequence",
            "predecessor_head_digest",
        }
        required["gateway_audit_chain_heads"] = {
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "head_digest",
        }
        required["gateway_audit_chain_entries"] = {
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "entry_schema_id",
            "entry_schema_version",
            "event_id",
            "previous_digest",
            "event_digest",
            "event_json",
            "appended_at",
        }
        required["gateway_audit_chain_checkpoints"] = {
            "checkpoint_id",
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "head_digest",
            "artifact_sha256",
            "anchor_backend",
            "object_version",
            "anchored_at",
        }
    for table, columns in required.items():
        observed = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not columns.issubset(observed):
            raise error_factory("storage_schema_partial_upgrade")
    if version >= 4:
        required_triggers = {
            "gateway_audit_chain_epochs_no_update",
            "gateway_audit_chain_epochs_no_delete",
            "gateway_audit_chain_entries_no_update",
            "gateway_audit_chain_entries_no_delete",
            "gateway_audit_chain_checkpoints_no_update",
            "gateway_audit_chain_checkpoints_no_delete",
        }
        observed_triggers = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
        }
        if not required_triggers.issubset(observed_triggers):
            raise error_factory("storage_schema_partial_upgrade")


def apply_sqlite_migration(
    connection: sqlite3.Connection,
    version: int,
    *,
    error_factory: StorageErrorFactory,
) -> None:
    if version == 1:
        _add_missing_columns(
            connection,
            "gateway_usage_events",
            {
                "evidence_schema_id": "TEXT NOT NULL DEFAULT 'hormuz.audit-event'",
                "evidence_schema_version": "INTEGER NOT NULL DEFAULT 2",
                "organization_id": "TEXT NOT NULL DEFAULT 'organization'",
                "identity_type": "TEXT NOT NULL DEFAULT 'human'",
                "authentication_source": "TEXT NOT NULL DEFAULT 'static'",
                "provider_reported_model": "TEXT",
                "policy_version": "TEXT NOT NULL DEFAULT 'legacy-unversioned'",
                "cost_basis": "TEXT NOT NULL DEFAULT 'configured_rate_card_estimate'",
                "allocation_basis": "TEXT NOT NULL DEFAULT 'direct_gateway_request'",
                "coverage": "TEXT NOT NULL DEFAULT 'gateway_captured_requests_only'",
                "redaction_count": "INTEGER NOT NULL DEFAULT 0",
                "redaction_rules": "TEXT NOT NULL DEFAULT '[]'",
            },
        )
        _add_missing_columns(
            connection,
            "gateway_secret_events",
            {
                "evidence_schema_id": "TEXT NOT NULL DEFAULT 'hormuz.audit-event'",
                "evidence_schema_version": "INTEGER NOT NULL DEFAULT 2",
                "organization_id": "TEXT NOT NULL DEFAULT 'organization'",
                "identity_type": "TEXT NOT NULL DEFAULT 'human'",
                "authentication_source": "TEXT NOT NULL DEFAULT 'static'",
                "policy_version": "TEXT NOT NULL DEFAULT 'legacy-unversioned'",
                "coverage": "TEXT NOT NULL DEFAULT 'gateway_captured_requests_only'",
            },
        )
        return
    if version == 2:
        _add_missing_columns(
            connection,
            "gateway_budget_reservations",
            {"organization_id": "TEXT NOT NULL DEFAULT 'organization'"},
        )
        return
    if version == 3:
        _add_missing_columns(
            connection,
            "gateway_budget_reservations",
            {"attempt_id": "TEXT"},
        )
        _execute_statements(
            connection,
            (_RESERVATION_ATTEMPT_INDEX_STATEMENT, *_REQUEST_ATTEMPT_SCHEMA_STATEMENTS),
        )
        return
    if version == 4:
        _execute_statements(connection, _AUDIT_CHAIN_SCHEMA_STATEMENTS)
        return
    if version == 5:
        from ._portfolio_schema import sqlite_statements
        _execute_statements(connection, sqlite_statements())
        return
    if version == 6:
        from ._attribution_schema import sqlite_statements
        _execute_statements(connection, sqlite_statements())
        return
    if version == 7:
        from ._outcome_schema import sqlite_statements
        _execute_statements(connection, sqlite_statements())
        return
    raise error_factory("storage_schema_migration_unsupported")


def _migration_states(connection: sqlite3.Connection) -> dict[int, str]:
    return {
        int(row["version"]): str(row["state"])
        for row in connection.execute(
            "SELECT version, state FROM hormuz_schema_migrations ORDER BY version"
        ).fetchall()
    }


def _validate_migration_states(
    states: dict[int, str],
    *,
    maximum_supported_schema_version: int,
    error_factory: StorageErrorFactory,
) -> None:
    if any(state != "applied" for state in states.values()):
        raise error_factory("storage_schema_partial_upgrade")
    if states and max(states) > maximum_supported_schema_version:
        raise error_factory("storage_schema_newer_than_binary")
    if states and set(states) != set(range(1, max(states) + 1)):
        raise error_factory("storage_schema_partial_upgrade")


def _execute_statements(
    connection: sqlite3.Connection,
    statements: tuple[str, ...],
) -> None:
    for statement in statements:
        connection.execute(statement)


def _add_missing_columns(
    connection: sqlite3.Connection,
    table: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, declaration in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
