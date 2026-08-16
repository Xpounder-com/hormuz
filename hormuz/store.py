from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Identity
from .usage import sanitize_provider_usage


@dataclass(frozen=True)
class MonthlyTotals:
    requests: int = 0
    denied_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    billable_tokens: int = 0
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
    dlp_events: int = 0
    dlp_detections: int = 0
    detected_requests: int = 0
    redacted_requests: int = 0
    denied_requests: int = 0
    approval_required_requests: int = 0


@dataclass(frozen=True)
class ReservationScope:
    name: str
    actor_id: str | None = None
    team_id: str | None = None
    token_limit: int | None = None
    cost_limit_microusd: int | None = None


class ReservationDenied(RuntimeError):
    pass


class SecurityStoreError(RuntimeError):
    pass


class UsageStore:
    def __init__(self, path: Path):
        self.path = path
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
                CREATE TABLE IF NOT EXISTS gateway_usage_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    resolved_alias TEXT,
                    upstream_model TEXT,
                    actual_model TEXT,
                    policy_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    billable_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_microusd INTEGER NOT NULL DEFAULT 0,
                    cost_basis TEXT NOT NULL DEFAULT 'not_available',
                    currency TEXT NOT NULL DEFAULT 'USD',
                    rate_card_version TEXT NOT NULL DEFAULT 'unversioned',
                    provider_usage_json TEXT NOT NULL DEFAULT '{}',
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
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    requested_model TEXT NOT NULL,
                    routed_model TEXT,
                    action TEXT NOT NULL,
                    detection_count INTEGER NOT NULL,
                    redaction_count INTEGER NOT NULL DEFAULT 0,
                    rules TEXT NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'security.secret',
                    policy_version TEXT NOT NULL DEFAULT 'legacy-secret-v1',
                    findings_json TEXT NOT NULL DEFAULT '[]'
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
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(gateway_usage_events)").fetchall()
            }
            if "redaction_count" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN redaction_count INTEGER NOT NULL DEFAULT 0"
                )
            if "redaction_rules" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN redaction_rules TEXT NOT NULL DEFAULT '[]'"
                )
            if "actual_model" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN actual_model TEXT"
                )
            if "billable_tokens" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN billable_tokens INTEGER NOT NULL DEFAULT 0"
                )
                connection.execute(
                    """
                    UPDATE gateway_usage_events
                    SET billable_tokens = input_tokens + output_tokens
                        + CASE WHEN protocol = 'anthropic'
                            THEN cache_read_tokens + cache_write_tokens ELSE 0 END
                    """
                )
            if "cost_basis" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN cost_basis TEXT NOT NULL DEFAULT 'not_available'"
                )
                connection.execute(
                    """
                    UPDATE gateway_usage_events
                    SET cost_basis = CASE WHEN cost_microusd > 0
                        THEN 'estimated_legacy' ELSE 'not_available' END
                    """
                )
            if "currency" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN currency TEXT NOT NULL DEFAULT 'USD'"
                )
            if "rate_card_version" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN rate_card_version TEXT NOT NULL DEFAULT 'unversioned'"
                )
            if "provider_usage_json" not in columns:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN provider_usage_json TEXT NOT NULL DEFAULT '{}'"
                )
            security_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(gateway_secret_events)").fetchall()
            }
            security_migrations = {
                "routed_model": "ALTER TABLE gateway_secret_events ADD COLUMN routed_model TEXT",
                "redaction_count": (
                    "ALTER TABLE gateway_secret_events ADD COLUMN redaction_count "
                    "INTEGER NOT NULL DEFAULT 0"
                ),
                "event_type": (
                    "ALTER TABLE gateway_secret_events ADD COLUMN event_type "
                    "TEXT NOT NULL DEFAULT 'security.secret'"
                ),
                "policy_version": (
                    "ALTER TABLE gateway_secret_events ADD COLUMN policy_version "
                    "TEXT NOT NULL DEFAULT 'legacy-secret-v1'"
                ),
                "findings_json": (
                    "ALTER TABLE gateway_secret_events ADD COLUMN findings_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                ),
            }
            for column, statement in security_migrations.items():
                if column not in security_columns:
                    connection.execute(statement)

    def record(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_action: str,
        status: str,
        actual_model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        billable_tokens: int | None = None,
        cost_microusd: int = 0,
        cost_basis: str = "not_available",
        currency: str = "USD",
        rate_card_version: str = "unversioned",
        provider_usage: dict[str, object] | None = None,
        provider_request_id: str | None = None,
        redaction_count: int = 0,
        redaction_rules: tuple[str, ...] = (),
    ) -> str:
        if cost_basis not in {"estimated", "estimated_legacy", "not_available", "not_applicable"}:
            raise ValueError("Unsupported usage cost basis")
        if currency != "USD":
            raise ValueError("Usage currency must be USD while costs use micro-USD storage")
        if (
            not isinstance(rate_card_version, str)
            or not rate_card_version.strip()
            or len(rate_card_version.encode("utf-8")) > 128
            or any(character in rate_card_version for character in ("\n", "\r", "\x00"))
        ):
            raise ValueError("Usage rate-card version must be a bounded single-line string")
        normalized_provider_usage = sanitize_provider_usage(protocol, provider_usage or {})
        if provider_usage and not normalized_provider_usage:
            raise ValueError("Usage provider metadata contains no supported fields")
        if actual_model is not None and (
            not isinstance(actual_model, str)
            or not actual_model
            or len(actual_model.encode("utf-8")) > 256
            or not all(character.isprintable() for character in actual_model)
        ):
            raise ValueError("Usage actual model must be a bounded printable string")
        normalized_input_tokens = _sqlite_nonnegative(input_tokens)
        normalized_output_tokens = _sqlite_nonnegative(output_tokens)
        normalized_cache_read_tokens = _sqlite_nonnegative(cache_read_tokens)
        normalized_cache_write_tokens = _sqlite_nonnegative(cache_write_tokens)
        normalized_reasoning_tokens = _sqlite_nonnegative(reasoning_tokens)
        normalized_billable_tokens = (
            _sqlite_nonnegative(billable_tokens)
            if isinstance(billable_tokens, int) and not isinstance(billable_tokens, bool)
            else _sqlite_nonnegative(
                normalized_input_tokens
                + normalized_output_tokens
                + (
                    normalized_cache_read_tokens + normalized_cache_write_tokens
                    if protocol == "anthropic"
                    else 0
                )
            )
        )
        event_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO gateway_usage_events (
                    id, occurred_at, actor_id, actor_name, team_id, team_name, client, protocol,
                    requested_model, resolved_alias, upstream_model, actual_model,
                    policy_action, status,
                    input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                    reasoning_tokens, billable_tokens, cost_microusd, cost_basis, currency,
                    rate_card_version, provider_usage_json, provider_request_id,
                    redaction_count, redaction_rules
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    datetime.now(timezone.utc).isoformat(),
                    identity.actor_id,
                    identity.actor_name,
                    identity.team_id,
                    identity.team_name,
                    client,
                    protocol,
                    requested_model,
                    resolved_alias,
                    upstream_model,
                    actual_model,
                    policy_action,
                    status,
                    normalized_input_tokens,
                    normalized_output_tokens,
                    normalized_cache_read_tokens,
                    normalized_cache_write_tokens,
                    normalized_reasoning_tokens,
                    normalized_billable_tokens,
                    _sqlite_nonnegative(cost_microusd),
                    cost_basis,
                    currency,
                    rate_card_version,
                    json.dumps(normalized_provider_usage, sort_keys=True, separators=(",", ":")),
                    provider_request_id,
                    _sqlite_nonnegative(redaction_count),
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
        action: str,
        detection_count: int,
        rules: tuple[str, ...],
    ) -> str:
        if action not in {"redacted", "denied"}:
            raise ValueError("Secret event action must be redacted or denied")
        return self._record_security_event(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            routed_model=None,
            action=action,
            detection_count=detection_count,
            redaction_count=detection_count,
            rules=rules,
            event_type="security.secret",
            policy_version="legacy-secret-v1",
            findings=(),
        )

    def record_dlp_event(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        routed_model: str,
        action: str,
        redaction_count: int,
        policy_version: str,
        findings: tuple[dict[str, object], ...],
    ) -> str:
        if action not in {"detected", "redacted", "denied", "approval_required"}:
            raise ValueError("Unsupported DLP event action")
        normalized_findings = _sanitize_dlp_findings(findings)
        return self._record_security_event(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            routed_model=routed_model,
            action=action,
            detection_count=sum(int(finding["count"]) for finding in normalized_findings),
            redaction_count=redaction_count,
            rules=tuple(str(finding["rule_id"]) for finding in normalized_findings),
            event_type="security.dlp",
            policy_version=policy_version,
            findings=normalized_findings,
        )

    def _record_security_event(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        routed_model: str | None,
        action: str,
        detection_count: int,
        redaction_count: int,
        rules: tuple[str, ...],
        event_type: str,
        policy_version: str,
        findings: tuple[dict[str, object], ...],
    ) -> str:
        if event_type not in {"security.secret", "security.dlp"}:
            raise ValueError("Unsupported security event type")
        if (
            not isinstance(policy_version, str)
            or not policy_version
            or len(policy_version.encode("utf-8")) > 128
            or any(character in policy_version for character in ("\n", "\r", "\x00"))
        ):
            raise ValueError("Security policy version must be a bounded single-line string")
        event_id = str(uuid.uuid4())
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO gateway_secret_events (
                        id, occurred_at, actor_id, actor_name, team_id, team_name,
                        client, protocol, requested_model, routed_model, action,
                        detection_count, redaction_count, rules, event_type, policy_version,
                        findings_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        datetime.now(timezone.utc).isoformat(),
                        identity.actor_id,
                        identity.actor_name,
                        identity.team_id,
                        identity.team_name,
                        client,
                        protocol,
                        requested_model,
                        routed_model,
                        action,
                        _sqlite_nonnegative(detection_count),
                        _sqlite_nonnegative(redaction_count),
                        json.dumps(sorted(set(rules)), separators=(",", ":")),
                        event_type,
                        policy_version,
                        json.dumps(findings, separators=(",", ":"), sort_keys=True),
                    ),
                )
        except sqlite3.Error as error:
            raise SecurityStoreError("security_store_unavailable") from error
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
        reservation_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM gateway_budget_reservations WHERE expires_at <= ?",
                (now_value,),
            )
            for scope in constrained:
                usage_clauses = ["occurred_at >= ?"]
                reservation_clauses = ["expires_at > ?"]
                usage_parameters: list[object] = [month_start]
                reservation_parameters: list[object] = [now_value]
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
                    id, created_at, expires_at, actor_id, team_id,
                    reserved_tokens, reserved_cost_microusd
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    now_value,
                    (now + timedelta(seconds=max(1, ttl_seconds))).isoformat(),
                    identity.actor_id,
                    identity.team_id,
                    max(0, reserved_tokens),
                    max(0, reserved_cost_microusd),
                ),
            )
        return reservation_id

    def release_budget_reservation(self, reservation_id: str | None) -> None:
        if reservation_id is None:
            return
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM gateway_budget_reservations WHERE id = ?",
                (reservation_id,),
            )

    def refresh_budget_reservation(self, reservation_id: str | None, *, ttl_seconds: int) -> None:
        if reservation_id is None:
            return
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds))).isoformat()
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE gateway_budget_reservations SET expires_at = ? WHERE id = ?",
                (expires_at, reservation_id),
            )

    def active_budget_reservations(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM gateway_budget_reservations WHERE expires_at > ?",
                (now,),
            ).fetchone()
        return int(row["count"])

    def monthly_totals(self, *, actor_id: str | None = None, team_id: str | None = None) -> MonthlyTotals:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
        if actor_id is not None:
            clauses.append("actor_id = ?")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = ?")
            parameters.append(team_id)
        query = f"""
            SELECT
                COUNT(*) AS requests,
                SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied_requests,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(billable_tokens), 0) AS billable_tokens,
                COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                COALESCE(SUM(redaction_count), 0) AS redaction_count
            FROM gateway_usage_events
            WHERE {' AND '.join(clauses)}
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return MonthlyTotals(**dict(row))

    def summary_rows(self) -> list[dict[str, object]]:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT actor_id, actor_name, team_id, team_name, client, protocol,
                       COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                       COALESCE(SUM(billable_tokens), 0) AS billable_tokens,
                       COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                       SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied,
                       COALESCE(SUM(redaction_count), 0) AS redactions
                FROM gateway_usage_events
                WHERE occurred_at >= ?
                GROUP BY actor_id, actor_name, team_id, team_name, client, protocol
                ORDER BY cost_microusd DESC, tokens DESC
                """,
                (start,),
            ).fetchall()
        return [dict(row) for row in rows]

    def report_rows(
        self,
        *,
        group_by: str,
        actor_id: str | None = None,
        team_id: str | None = None,
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
                    "COALESCE(actual_model, upstream_model, resolved_alias, requested_model) AS scope_id",
                    "COALESCE(actual_model, upstream_model, resolved_alias, requested_model) AS scope_name",
                    "protocol",
                ],
                [
                    "COALESCE(actual_model, upstream_model, resolved_alias, requested_model)",
                    "protocol",
                ],
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
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                COALESCE(SUM(input_tokens + output_tokens), 0) AS total_tokens,
                COALESCE(SUM(billable_tokens), 0) AS billable_tokens,
                COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                COALESCE(SUM(CASE WHEN cost_basis LIKE 'estimated%' THEN cost_microusd ELSE 0 END), 0)
                    AS estimated_cost_microusd,
                COALESCE(SUM(CASE WHEN cost_basis = 'not_available' THEN 1 ELSE 0 END), 0)
                    AS unpriced_requests,
                GROUP_CONCAT(DISTINCT cost_basis) AS cost_bases_csv,
                GROUP_CONCAT(DISTINCT currency) AS currencies_csv,
                GROUP_CONCAT(DISTINCT rate_card_version) AS rate_card_versions_csv,
                COUNT(DISTINCT actor_id) AS active_actors,
                COALESCE(SUM(redaction_count), 0) AS redactions
            FROM gateway_usage_events
            WHERE {' AND '.join(clauses)}
            {grouping}
            ORDER BY cost_microusd DESC, total_tokens DESC, scope_name ASC
        """
        with self._lock, self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            item["cost_bases"] = _sorted_csv(item.pop("cost_bases_csv"))
            item["currencies"] = _sorted_csv(item.pop("currencies_csv"))
            item["rate_card_versions"] = _sorted_csv(item.pop("rate_card_versions_csv"))
            result.append(item)
        return result

    def monthly_secret_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> SecretTotals:
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start]
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
                COALESCE(SUM(CASE WHEN event_type = 'security.dlp' THEN 1 ELSE 0 END), 0)
                    AS dlp_events,
                COALESCE(SUM(CASE WHEN event_type = 'security.dlp' THEN detection_count ELSE 0 END), 0)
                    AS dlp_detections,
                COALESCE(SUM(CASE WHEN action = 'detected' THEN 1 ELSE 0 END), 0) AS detected_requests,
                COALESCE(SUM(CASE WHEN action = 'redacted' THEN 1 ELSE 0 END), 0) AS redacted_requests,
                COALESCE(SUM(CASE WHEN action = 'denied' THEN 1 ELSE 0 END), 0) AS denied_requests,
                COALESCE(SUM(CASE WHEN action = 'approval_required' THEN 1 ELSE 0 END), 0)
                    AS approval_required_requests
            FROM gateway_secret_events
            WHERE {' AND '.join(clauses)}
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return SecretTotals(**dict(row))

    def audit_events(self, *, since: str, kind: str = "all") -> list[dict[str, object]]:
        if kind not in {"all", "usage", "security"}:
            raise ValueError(f"Unsupported audit event kind: {kind}")
        events: list[dict[str, object]] = []
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN")
            if kind in {"all", "usage"}:
                rows = connection.execute(
                    """
                    SELECT
                        id, occurred_at, actor_id, actor_name, team_id, team_name,
                        client, protocol, requested_model, resolved_alias, upstream_model,
                        actual_model, policy_action, status, input_tokens, output_tokens,
                        cache_read_tokens, cache_write_tokens, reasoning_tokens,
                        billable_tokens, cost_microusd, cost_basis, currency,
                        rate_card_version, provider_usage_json, provider_request_id,
                        redaction_count, redaction_rules
                    FROM gateway_usage_events
                    WHERE occurred_at >= ?
                    ORDER BY occurred_at, id
                    """,
                    (since,),
                ).fetchall()
                for row in rows:
                    event = dict(row)
                    event["redaction_rules"] = json.loads(str(event["redaction_rules"]))
                    event["provider_usage"] = json.loads(str(event.pop("provider_usage_json")))
                    events.append(
                        {
                            "schema_version": 1,
                            "event_type": "usage",
                            **event,
                        }
                    )
            if kind in {"all", "security"}:
                rows = connection.execute(
                    """
                    SELECT
                        id, occurred_at, actor_id, actor_name, team_id, team_name,
                        client, protocol, requested_model, routed_model, action,
                        detection_count, redaction_count, rules, event_type,
                        policy_version, findings_json
                    FROM gateway_secret_events
                    WHERE occurred_at >= ?
                    ORDER BY occurred_at, id
                    """,
                    (since,),
                ).fetchall()
                for row in rows:
                    event = dict(row)
                    event["rules"] = json.loads(str(event["rules"]))
                    event["findings"] = json.loads(str(event.pop("findings_json")))
                    event_type = str(event.pop("event_type"))
                    events.append(
                        {
                            "schema_version": 1,
                            "event_type": event_type,
                            **event,
                        }
                    )
        events.sort(key=lambda event: (str(event["occurred_at"]), str(event["id"])))
        return events


def _sorted_csv(value: object) -> list[str]:
    if not isinstance(value, str) or not value:
        return []
    return sorted(set(value.split(",")))


def _sqlite_nonnegative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return min(max(value, 0), 2**63 - 1)


def _sanitize_dlp_findings(
    findings: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    normalized: list[dict[str, object]] = []
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "rule_id",
            "category",
            "confidence",
            "action",
            "count",
        }:
            raise ValueError("DLP findings must use the metadata-only finding schema")
        rule_id = finding["rule_id"]
        category = finding["category"]
        confidence = finding["confidence"]
        action = finding["action"]
        count = finding["count"]
        for label, value in (("rule_id", rule_id), ("category", category)):
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 64
                or not all(character.isprintable() for character in value)
            ):
                raise ValueError(f"DLP finding {label} must be a bounded printable string")
        if confidence not in {"low", "medium", "high"}:
            raise ValueError("DLP finding confidence is invalid")
        if action not in {"detect", "redact", "deny", "require_approval"}:
            raise ValueError("DLP finding action is invalid")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 2**63 - 1:
            raise ValueError("DLP finding count must be a positive integer")
        normalized.append(
            {
                "rule_id": rule_id,
                "category": category,
                "confidence": confidence,
                "action": action,
                "count": count,
            }
        )
    if not normalized:
        raise ValueError("DLP events require at least one finding")
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                str(item["rule_id"]),
                str(item["category"]),
                str(item["confidence"]),
                str(item["action"]),
            ),
        )
    )
