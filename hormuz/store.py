from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .config import Identity
from .contracts import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    validate_policy_action,
    validate_request_status,
)
from .evidence import security_audit_event, usage_audit_event


@dataclass(frozen=True)
class MonthlyTotals:
    requests: int = 0
    denied_requests: int = 0
    rate_limited_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_microusd: int = 0
    redaction_count: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cost_usd(self) -> float:
        return self.cost_microusd / 1_000_000


@dataclass(frozen=True)
class SecretTotals:
    events: int = 0
    detections: int = 0
    redacted_requests: int = 0
    denied_requests: int = 0


@dataclass(frozen=True)
class ReservationScope:
    name: str
    actor_id: str | None = None
    team_id: str | None = None
    token_limit: int | None = None
    cost_limit_microusd: int | None = None


class ReservationDenied(RuntimeError):
    pass


class StorageSchemaError(RuntimeError):
    """A stable failure for an unsafe or incomplete durable-store transition."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UsageRepository(Protocol):
    """The narrow metadata-only storage contract used by policy and gateway code."""

    def record(self, **kwargs: object) -> str: ...

    def record_secret_event(self, **kwargs: object) -> str: ...

    def reserve_budget(self, **kwargs: object) -> str | None: ...

    def release_budget_reservation(self, reservation_id: str | None, **kwargs: object) -> None: ...

    def refresh_budget_reservation(self, reservation_id: str | None, **kwargs: object) -> None: ...

    def active_budget_reservations(self, **kwargs: object) -> int: ...

    def monthly_totals(self, **kwargs: object) -> MonthlyTotals: ...

    def monthly_secret_totals(self, **kwargs: object) -> SecretTotals: ...

    def summary_rows(self, **kwargs: object) -> list[dict[str, object]]: ...

    def report_rows(self, **kwargs: object) -> list[dict[str, object]]: ...

    def audit_events(self, **kwargs: object) -> list[dict[str, object]]: ...


class UsageStore:
    """SQLite implementation of the metadata-only usage repository."""

    schema_version = 2

    def __init__(self, path: Path, *, maximum_supported_schema_version: int | None = None):
        self.path = path
        self.maximum_supported_schema_version = (
            self.schema_version
            if maximum_supported_schema_version is None
            else maximum_supported_schema_version
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS hormuz_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    state TEXT NOT NULL,
                    applied_at TEXT
                );
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
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_occurred_at
                    ON gateway_usage_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_actor_month
                    ON gateway_usage_events(actor_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_usage_team_month
                    ON gateway_usage_events(team_id, occurred_at);
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
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_secret_occurred_at
                    ON gateway_secret_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_secret_actor_month
                    ON gateway_secret_events(actor_id, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_secret_team_month
                    ON gateway_secret_events(team_id, occurred_at);
                CREATE TABLE IF NOT EXISTS gateway_budget_reservations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL DEFAULT 'organization',
                    actor_id TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    reserved_tokens INTEGER NOT NULL,
                    reserved_cost_microusd INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_reservation_expires_at
                    ON gateway_budget_reservations(expires_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_reservation_actor
                    ON gateway_budget_reservations(actor_id, expires_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_reservation_team
                    ON gateway_budget_reservations(team_id, expires_at);
                """
            )
            migrations = {
                int(row["version"]): str(row["state"])
                for row in connection.execute(
                    "SELECT version, state FROM hormuz_schema_migrations ORDER BY version"
                ).fetchall()
            }
            if any(state != "applied" for state in migrations.values()):
                raise StorageSchemaError("storage_schema_partial_upgrade")
            if migrations and max(migrations) > self.maximum_supported_schema_version:
                raise StorageSchemaError("storage_schema_newer_than_binary")
            for version in range(1, self.schema_version + 1):
                if version in migrations:
                    continue
                if version > self.maximum_supported_schema_version:
                    raise StorageSchemaError("storage_schema_newer_than_binary")
                connection.execute(
                    "INSERT INTO hormuz_schema_migrations (version, state) VALUES (?, 'applying')",
                    (version,),
                )
                self._apply_migration(connection, version)
                connection.execute(
                    "UPDATE hormuz_schema_migrations SET state = 'applied', applied_at = ? WHERE version = ?",
                    (datetime.now(timezone.utc).isoformat(), version),
                )

    @classmethod
    def _apply_migration(cls, connection: sqlite3.Connection, version: int) -> None:
        if version == 1:
            cls._add_missing_columns(
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
            cls._add_missing_columns(
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
            cls._add_missing_columns(
                connection,
                "gateway_budget_reservations",
                {"organization_id": "TEXT NOT NULL DEFAULT 'organization'"},
            )
            return
        raise StorageSchemaError("storage_schema_migration_unsupported")

    @staticmethod
    def _add_missing_columns(
        connection: sqlite3.Connection,
        table: str,
        columns: dict[str, str],
    ) -> None:
        existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def record(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        provider_reported_model: str | None = None,
        policy_version: str = "legacy-unversioned",
        policy_action: str,
        status: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_microusd: int = 0,
        cost_basis: str = COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
        allocation_basis: str = ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
        coverage: str = COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
        provider_request_id: str | None = None,
        redaction_count: int = 0,
        redaction_rules: tuple[str, ...] = (),
    ) -> str:
        validate_policy_action(policy_action)
        validate_request_status(status)
        event_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO gateway_usage_events (
                    id, occurred_at, evidence_schema_id, evidence_schema_version,
                    organization_id, actor_id, actor_name, team_id, team_name,
                    identity_type, authentication_source, client, protocol, requested_model,
                    resolved_alias, upstream_model, provider_reported_model, policy_version,
                    policy_action, status,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, cost_microusd, cost_basis, allocation_basis, coverage,
                    provider_request_id, redaction_count,
                    redaction_rules
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    datetime.now(timezone.utc).isoformat(),
                    AUDIT_EVENT_SCHEMA_ID,
                    AUDIT_EVENT_SCHEMA_VERSION,
                    identity.organization_id,
                    identity.actor_id,
                    identity.actor_name,
                    identity.team_id,
                    identity.team_name,
                    identity.identity_type,
                    identity.authentication_source,
                    client,
                    protocol,
                    requested_model,
                    resolved_alias,
                    upstream_model,
                    provider_reported_model,
                    policy_version,
                    policy_action,
                    status,
                    max(0, input_tokens),
                    max(0, output_tokens),
                    max(0, cache_read_tokens),
                    max(0, cache_write_tokens),
                    max(0, reasoning_tokens),
                    max(0, cost_microusd),
                    cost_basis,
                    allocation_basis,
                    coverage,
                    provider_request_id,
                    max(0, redaction_count),
                    json.dumps(sorted(set(redaction_rules)), separators=(",", ":")),
                ),
            )
        return event_id

    def record_secret_event(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        policy_version: str = "legacy-unversioned",
        coverage: str = COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
        action: str,
        detection_count: int,
        rules: tuple[str, ...],
    ) -> str:
        if action not in {"redacted", "denied"}:
            raise ValueError("Secret event action must be redacted or denied")
        event_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO gateway_secret_events (
                    id, occurred_at, evidence_schema_id, evidence_schema_version,
                    organization_id, actor_id, actor_name, team_id, team_name,
                    identity_type, authentication_source, client, protocol, requested_model,
                    policy_version, coverage, action, detection_count, rules
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    datetime.now(timezone.utc).isoformat(),
                    AUDIT_EVENT_SCHEMA_ID,
                    AUDIT_EVENT_SCHEMA_VERSION,
                    identity.organization_id,
                    identity.actor_id,
                    identity.actor_name,
                    identity.team_id,
                    identity.team_name,
                    identity.identity_type,
                    identity.authentication_source,
                    client,
                    protocol,
                    requested_model,
                    policy_version,
                    coverage,
                    action,
                    max(0, detection_count),
                    json.dumps(sorted(set(rules)), separators=(",", ":")),
                ),
            )
        return event_id

    def reserve_budget(
        self,
        *,
        identity: Identity,
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
    ) -> str | None:
        constrained = tuple(
            scope
            for scope in scopes
            if scope.token_limit is not None or scope.cost_limit_microusd is not None
        )
        if not constrained:
            return None
        now = datetime.now(timezone.utc)
        now_value = now.isoformat()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        organization_id = identity.organization_id
        reservation_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM gateway_budget_reservations WHERE expires_at <= ?",
                (now_value,),
            )
            for scope in constrained:
                usage_clauses = ["organization_id = ?", "occurred_at >= ?"]
                reservation_clauses = ["organization_id = ?", "expires_at > ?"]
                usage_parameters: list[object] = [organization_id, month_start]
                reservation_parameters: list[object] = [organization_id, now_value]
                if scope.actor_id is not None:
                    usage_clauses.append("actor_id = ?")
                    reservation_clauses.append("actor_id = ?")
                    usage_parameters.append(scope.actor_id)
                    reservation_parameters.append(scope.actor_id)
                if scope.team_id is not None:
                    usage_clauses.append("team_id = ?")
                    reservation_clauses.append("team_id = ?")
                    usage_parameters.append(scope.team_id)
                    reservation_parameters.append(scope.team_id)
                usage = connection.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                        COALESCE(SUM(cost_microusd), 0) AS cost_microusd
                    FROM gateway_usage_events
                    WHERE {' AND '.join(usage_clauses)}
                    """,
                    usage_parameters,
                ).fetchone()
                reserved = connection.execute(
                    f"""
                    SELECT
                        COALESCE(SUM(reserved_tokens), 0) AS tokens,
                        COALESCE(SUM(reserved_cost_microusd), 0) AS cost_microusd
                    FROM gateway_budget_reservations
                    WHERE {' AND '.join(reservation_clauses)}
                    """,
                    reservation_parameters,
                ).fetchone()
                projected_tokens = usage["tokens"] + reserved["tokens"] + max(0, reserved_tokens)
                projected_cost = usage["cost_microusd"] + reserved["cost_microusd"] + max(
                    0, reserved_cost_microusd
                )
                if scope.token_limit is not None and projected_tokens > scope.token_limit:
                    raise ReservationDenied(
                        f"The {scope.name} monthly token limit would be exceeded by this request."
                    )
                if scope.cost_limit_microusd is not None and projected_cost > scope.cost_limit_microusd:
                    raise ReservationDenied(
                        f"The {scope.name} monthly AI budget would be exceeded by this request."
                    )
            connection.execute(
                """
                INSERT INTO gateway_budget_reservations (
                    id, created_at, expires_at, organization_id, actor_id, team_id,
                    reserved_tokens, reserved_cost_microusd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    now_value,
                    (now + timedelta(seconds=max(1, ttl_seconds))).isoformat(),
                    organization_id,
                    identity.actor_id,
                    identity.team_id,
                    max(0, reserved_tokens),
                    max(0, reserved_cost_microusd),
                ),
            )
        return reservation_id

    def release_budget_reservation(
        self,
        reservation_id: str | None,
        *,
        organization_id: str | None = None,
    ) -> None:
        if reservation_id is None:
            return
        with self._lock, self._connection() as connection:
            clauses = ["id = ?"]
            parameters: list[object] = [reservation_id]
            if organization_id is not None:
                clauses.append("organization_id = ?")
                parameters.append(organization_id)
            connection.execute(
                f"DELETE FROM gateway_budget_reservations WHERE {' AND '.join(clauses)}",
                parameters,
            )

    def refresh_budget_reservation(
        self,
        reservation_id: str | None,
        *,
        ttl_seconds: int,
        organization_id: str | None = None,
    ) -> None:
        if reservation_id is None:
            return
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds))).isoformat()
        with self._lock, self._connection() as connection:
            clauses = ["id = ?"]
            parameters: list[object] = [expires_at, reservation_id]
            if organization_id is not None:
                clauses.append("organization_id = ?")
                parameters.append(organization_id)
            connection.execute(
                f"UPDATE gateway_budget_reservations SET expires_at = ? WHERE {' AND '.join(clauses)}",
                parameters,
            )

    def active_budget_reservations(self, *, organization_id: str | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            clauses = ["expires_at > ?"]
            parameters: list[object] = [now]
            if organization_id is not None:
                clauses.append("organization_id = ?")
                parameters.append(organization_id)
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM gateway_budget_reservations WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
        return int(row["count"])

    def monthly_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> MonthlyTotals:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
        if organization_id is not None:
            clauses.append("organization_id = ?")
            parameters.append(organization_id)
        if actor_id is not None:
            clauses.append("actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = ?")
            parameters.append(team_id)
        query = f"""
            SELECT
                COUNT(*) AS requests,
                COALESCE(SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END), 0) AS denied_requests,
                COALESCE(SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END), 0) AS rate_limited_requests,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                COALESCE(SUM(redaction_count), 0) AS redaction_count
            FROM gateway_usage_events
            WHERE {' AND '.join(clauses)}
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return MonthlyTotals(**dict(row))

    def summary_rows(self, *, organization_id: str | None = None) -> list[dict[str, object]]:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
        if organization_id is not None:
            clauses.append("organization_id = ?")
            parameters.append(organization_id)
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT actor_id, actor_name, team_id, team_name, client, protocol,
                       COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                       COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                       SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied,
                       COALESCE(SUM(redaction_count), 0) AS redactions
                FROM gateway_usage_events
                WHERE {' AND '.join(clauses)}
                GROUP BY actor_id, actor_name, team_id, team_name, client, protocol
                ORDER BY cost_microusd DESC, tokens DESC
                """,
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def report_rows(
        self,
        *,
        group_by: str,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> list[dict[str, object]]:
        dimensions: dict[str, tuple[list[str], list[str]]] = {
            "organization": (
                ["'organization' AS scope_id", "'Organization' AS scope_name"],
                [],
            ),
            "team": (
                ["team_id AS scope_id", "team_name AS scope_name"],
                ["team_id", "team_name"],
            ),
            "person": (
                [
                    "actor_id AS scope_id",
                    "actor_name AS scope_name",
                    "team_id",
                    "team_name",
                ],
                ["actor_id", "actor_name", "team_id", "team_name"],
            ),
            "model": (
                [
                    "COALESCE(upstream_model, resolved_alias, requested_model) AS scope_id",
                    "COALESCE(upstream_model, resolved_alias, requested_model) AS scope_name",
                    "protocol",
                ],
                ["COALESCE(upstream_model, resolved_alias, requested_model)", "protocol"],
            ),
            "client": (
                ["client AS scope_id", "client AS scope_name", "client"],
                ["client"],
            ),
            "provider": (
                ["protocol AS scope_id", "protocol AS scope_name", "protocol"],
                ["protocol"],
            ),
        }
        try:
            select_dimensions, group_dimensions = dimensions[group_by]
        except KeyError as error:
            raise ValueError(f"Unsupported usage report dimension: {group_by}") from error

        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
        if organization_id is not None:
            clauses.append("organization_id = ?")
            parameters.append(organization_id)
        if actor_id is not None:
            clauses.append("actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = ?")
            parameters.append(team_id)
        grouping = f"GROUP BY {', '.join(group_dimensions)}" if group_dimensions else ""
        query = f"""
            SELECT
                {', '.join(select_dimensions)},
                COUNT(*) AS requests,
                COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS succeeded,
                COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                COALESCE(SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END), 0) AS denied,
                COALESCE(SUM(CASE WHEN status = 'rate_limited' THEN 1 ELSE 0 END), 0) AS rate_limited,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens,
                COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                COUNT(DISTINCT actor_id) AS active_actors,
                COALESCE(SUM(redaction_count), 0) AS redactions
            FROM gateway_usage_events
            WHERE {' AND '.join(clauses)}
            {grouping}
            ORDER BY cost_microusd DESC, total_tokens DESC, scope_name ASC
        """
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def monthly_secret_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> SecretTotals:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
        if organization_id is not None:
            clauses.append("organization_id = ?")
            parameters.append(organization_id)
        if actor_id is not None:
            clauses.append("actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = ?")
            parameters.append(team_id)
        query = f"""
            SELECT
                COUNT(*) AS events,
                COALESCE(SUM(detection_count), 0) AS detections,
                COALESCE(SUM(CASE WHEN action = 'redacted' THEN 1 ELSE 0 END), 0) AS redacted_requests,
                COALESCE(SUM(CASE WHEN action = 'denied' THEN 1 ELSE 0 END), 0) AS denied_requests
            FROM gateway_secret_events
            WHERE {' AND '.join(clauses)}
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return SecretTotals(**dict(row))

    def audit_events(
        self,
        *,
        since: str,
        kind: str = "all",
        organization_id: str | None = None,
    ) -> list[dict[str, object]]:
        if kind not in {"all", "usage", "security"}:
            raise ValueError(f"Unsupported audit event kind: {kind}")
        events: list[dict[str, object]] = []
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN")
            usage_clauses = ["occurred_at >= ?"]
            usage_parameters: list[object] = [since]
            if organization_id is not None:
                usage_clauses.append("organization_id = ?")
                usage_parameters.append(organization_id)
            if kind in {"all", "usage"}:
                rows = connection.execute(
                    f"""
                    SELECT
                        id, occurred_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        resolved_alias, upstream_model, provider_reported_model, policy_version,
                        policy_action, status, input_tokens, output_tokens,
                        cache_read_tokens, cache_write_tokens, reasoning_tokens,
                        cost_microusd, cost_basis, allocation_basis, coverage,
                        provider_request_id, redaction_count,
                        redaction_rules
                    FROM gateway_usage_events
                    WHERE {' AND '.join(usage_clauses)}
                    ORDER BY occurred_at, id
                    """,
                    usage_parameters,
                ).fetchall()
                for row in rows:
                    events.append(usage_audit_event(dict(row)))
            if kind in {"all", "security"}:
                rows = connection.execute(
                    f"""
                    SELECT
                        id, occurred_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        policy_version, coverage, action, detection_count, rules
                    FROM gateway_secret_events
                    WHERE {' AND '.join(usage_clauses)}
                    ORDER BY occurred_at, id
                    """,
                    usage_parameters,
                ).fetchall()
                for row in rows:
                    events.append(security_audit_event(dict(row)))
        events.sort(key=lambda event: (str(event["occurred_at"]), str(event["id"])))
        return events
