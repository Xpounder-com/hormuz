"""PostgreSQL implementation of Hormuz's metadata-only usage repository."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

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
from .postgres import (
    PostgresConnectionPool,
    PostgresStorageError,
    postgres_transaction,
    validate_postgres_identifier,
    verify_postgres_schema,
)
from .store import MonthlyTotals, ReservationDenied, ReservationScope, SecretTotals


class PostgresUsageStore:
    """Tenant-scoped durable evidence store using PostgreSQL row security.

    The adapter accepts only configured organization IDs. Every operation binds
    one organization in the transaction before issuing a query, and the schema
    enforces the same key with row-level security.
    """

    backend = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        organization_ids: tuple[str, ...],
        schema: str = "hormuz",
        runtime_role: str = "hormuz_runtime",
        verify_schema: bool = True,
        connection_pool: PostgresConnectionPool | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise PostgresStorageError("postgres_dsn_unavailable")
        normalized_organizations = tuple(sorted(set(organization_ids)))
        if not normalized_organizations or any(not item for item in normalized_organizations):
            raise PostgresStorageError("storage_organization_not_configured")
        self._dsn = dsn
        self.organization_ids = normalized_organizations
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "postgres_runtime_role")
        self._qualified_schema = '"' + self.schema.replace('"', '""') + '"'
        self._connection_pool = connection_pool
        if verify_schema:
            verify_postgres_schema(
                dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                connection_pool=self._connection_pool,
            )

    def _table(self, name: str) -> str:
        return f"{self._qualified_schema}.{name}"

    def _organization(self, organization_id: str | None) -> str:
        if organization_id is None:
            if len(self.organization_ids) != 1:
                raise PostgresStorageError("storage_organization_required")
            return self.organization_ids[0]
        if organization_id not in self.organization_ids:
            raise PostgresStorageError("storage_organization_not_configured")
        return organization_id

    def _transaction(self, organization_id: str):
        return postgres_transaction(
            self._dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id=organization_id,
            connection_pool=self._connection_pool,
        )

    def verify_ready(self) -> None:
        """Prove every configured tenant can use the restricted runtime path.

        The query is deliberately read-only and returns no evidence or tenant
        metadata. It still exercises the configured runtime role, RLS policy,
        transaction-local organization setting, and (when configured) the
        bounded connection pool.
        """

        for organization_id in self.organization_ids:
            with self._transaction(organization_id) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT 1
                        FROM {self._table('gateway_usage_events')}
                        WHERE organization_id = %s
                        LIMIT 1
                        """,
                        (organization_id,),
                    )

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
        organization_id = self._organization(identity.organization_id)
        event_id = str(uuid.uuid4())
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('gateway_usage_events')} (
                        id, occurred_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        resolved_alias, upstream_model, provider_reported_model, policy_version,
                        policy_action, status,
                        input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                        reasoning_tokens, cost_microusd, cost_basis, allocation_basis, coverage,
                        provider_request_id, redaction_count, redaction_rules
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    (
                        event_id,
                        datetime.now(timezone.utc),
                        AUDIT_EVENT_SCHEMA_ID,
                        AUDIT_EVENT_SCHEMA_VERSION,
                        organization_id,
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
        organization_id = self._organization(identity.organization_id)
        event_id = str(uuid.uuid4())
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('gateway_secret_events')} (
                        id, occurred_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        policy_version, coverage, action, detection_count, rules
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        event_id,
                        datetime.now(timezone.utc),
                        AUDIT_EVENT_SCHEMA_ID,
                        AUDIT_EVENT_SCHEMA_VERSION,
                        organization_id,
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
        organization_id = self._organization(identity.organization_id)
        constrained = tuple(
            scope
            for scope in scopes
            if scope.token_limit is not None or scope.cost_limit_microusd is not None
        )
        if not constrained:
            return None
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        reservation_id = str(uuid.uuid4())
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (f"hormuz:budget:{organization_id}:{month_start.date().isoformat()}",),
                )
                cursor.execute(
                    f"DELETE FROM {self._table('gateway_budget_reservations')} WHERE organization_id = %s AND expires_at <= %s",
                    (organization_id, now),
                )
                for scope in constrained:
                    usage_clauses = ["organization_id = %s", "occurred_at >= %s"]
                    reservation_clauses = ["organization_id = %s", "expires_at > %s"]
                    usage_parameters: list[object] = [organization_id, month_start]
                    reservation_parameters: list[object] = [organization_id, now]
                    if scope.actor_id is not None:
                        usage_clauses.append("actor_id = %s")
                        reservation_clauses.append("actor_id = %s")
                        usage_parameters.append(scope.actor_id)
                        reservation_parameters.append(scope.actor_id)
                    if scope.team_id is not None:
                        usage_clauses.append("team_id = %s")
                        reservation_clauses.append("team_id = %s")
                        usage_parameters.append(scope.team_id)
                        reservation_parameters.append(scope.team_id)
                    cursor.execute(
                        f"""
                        SELECT
                            COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                            COALESCE(SUM(cost_microusd), 0) AS cost_microusd
                        FROM {self._table('gateway_usage_events')}
                        WHERE {' AND '.join(usage_clauses)}
                        """,
                        usage_parameters,
                    )
                    usage = cursor.fetchone()
                    cursor.execute(
                        f"""
                        SELECT
                            COALESCE(SUM(reserved_tokens), 0) AS tokens,
                            COALESCE(SUM(reserved_cost_microusd), 0) AS cost_microusd
                        FROM {self._table('gateway_budget_reservations')}
                        WHERE {' AND '.join(reservation_clauses)}
                        """,
                        reservation_parameters,
                    )
                    reserved = cursor.fetchone()
                    projected_tokens = int(usage["tokens"]) + int(reserved["tokens"]) + max(0, reserved_tokens)
                    projected_cost = (
                        int(usage["cost_microusd"])
                        + int(reserved["cost_microusd"])
                        + max(0, reserved_cost_microusd)
                    )
                    if scope.token_limit is not None and projected_tokens > scope.token_limit:
                        raise ReservationDenied(
                            f"The {scope.name} monthly token limit would be exceeded by this request."
                        )
                    if scope.cost_limit_microusd is not None and projected_cost > scope.cost_limit_microusd:
                        raise ReservationDenied(
                            f"The {scope.name} monthly AI budget would be exceeded by this request."
                        )
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('gateway_budget_reservations')} (
                        id, created_at, expires_at, organization_id, actor_id, team_id,
                        reserved_tokens, reserved_cost_microusd
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        reservation_id,
                        now,
                        now + timedelta(seconds=max(1, ttl_seconds)),
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
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"DELETE FROM {self._table('gateway_budget_reservations')} WHERE organization_id = %s AND id = %s",
                    (organization, reservation_id),
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
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    UPDATE {self._table('gateway_budget_reservations')}
                    SET expires_at = %s
                    WHERE organization_id = %s AND id = %s
                    """,
                    (
                        datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds)),
                        organization,
                        reservation_id,
                    ),
                )

    def active_budget_reservations(self, *, organization_id: str | None = None) -> int:
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM {self._table('gateway_budget_reservations')}
                    WHERE organization_id = %s AND expires_at > %s
                    """,
                    (organization, datetime.now(timezone.utc)),
                )
                row = cursor.fetchone()
        return int(row["count"])

    def monthly_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> MonthlyTotals:
        organization = self._organization(organization_id)
        start = _month_start()
        clauses = ["organization_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [organization, start]
        if actor_id is not None:
            clauses.append("actor_id = %s")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = %s")
            parameters.append(team_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
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
                    FROM {self._table('gateway_usage_events')}
                    WHERE {' AND '.join(clauses)}
                    """,
                    parameters,
                )
                row = cursor.fetchone()
        return MonthlyTotals(**dict(row))

    def summary_rows(self, *, organization_id: str | None = None) -> list[dict[str, object]]:
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT actor_id, actor_name, team_id, team_name, client, protocol,
                           COUNT(*) AS requests,
                           COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                           COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                           COALESCE(SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END), 0) AS denied,
                           COALESCE(SUM(redaction_count), 0) AS redactions
                    FROM {self._table('gateway_usage_events')}
                    WHERE organization_id = %s AND occurred_at >= %s
                    GROUP BY actor_id, actor_name, team_id, team_name, client, protocol
                    ORDER BY cost_microusd DESC, tokens DESC
                    """,
                    (organization, _month_start()),
                )
                return [dict(row) for row in cursor.fetchall()]

    def report_rows(
        self,
        *,
        group_by: str,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> list[dict[str, object]]:
        organization = self._organization(organization_id)
        dimensions: dict[str, tuple[list[str], list[str]]] = {
            "organization": (["'organization' AS scope_id", "'Organization' AS scope_name"], []),
            "team": (["team_id AS scope_id", "team_name AS scope_name"], ["team_id", "team_name"]),
            "person": (
                ["actor_id AS scope_id", "actor_name AS scope_name", "team_id", "team_name"],
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
            "client": (["client AS scope_id", "client AS scope_name", "client"], ["client"]),
            "provider": (["protocol AS scope_id", "protocol AS scope_name", "protocol"], ["protocol"]),
        }
        try:
            select_dimensions, group_dimensions = dimensions[group_by]
        except KeyError as error:
            raise ValueError(f"Unsupported usage report dimension: {group_by}") from error
        clauses = ["organization_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [organization, _month_start()]
        if actor_id is not None:
            clauses.append("actor_id = %s")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = %s")
            parameters.append(team_id)
        grouping = f"GROUP BY {', '.join(group_dimensions)}" if group_dimensions else ""
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
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
                    FROM {self._table('gateway_usage_events')}
                    WHERE {' AND '.join(clauses)}
                    {grouping}
                    ORDER BY cost_microusd DESC, total_tokens DESC, scope_name ASC
                    """,
                    parameters,
                )
                return [dict(row) for row in cursor.fetchall()]

    def monthly_secret_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> SecretTotals:
        organization = self._organization(organization_id)
        clauses = ["organization_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [organization, _month_start()]
        if actor_id is not None:
            clauses.append("actor_id = %s")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = %s")
            parameters.append(team_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        COUNT(*) AS events,
                        COALESCE(SUM(detection_count), 0) AS detections,
                        COALESCE(SUM(CASE WHEN action = 'redacted' THEN 1 ELSE 0 END), 0) AS redacted_requests,
                        COALESCE(SUM(CASE WHEN action = 'denied' THEN 1 ELSE 0 END), 0) AS denied_requests
                    FROM {self._table('gateway_secret_events')}
                    WHERE {' AND '.join(clauses)}
                    """,
                    parameters,
                )
                row = cursor.fetchone()
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
        organization = self._organization(organization_id)
        events: list[dict[str, object]] = []
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                if kind in {"all", "usage"}:
                    cursor.execute(
                        f"""
                        SELECT
                            id, occurred_at, evidence_schema_id, evidence_schema_version,
                            organization_id, actor_id, actor_name, team_id, team_name,
                            identity_type, authentication_source, client, protocol, requested_model,
                            resolved_alias, upstream_model, provider_reported_model, policy_version,
                            policy_action, status, input_tokens, output_tokens,
                            cache_read_tokens, cache_write_tokens, reasoning_tokens,
                            cost_microusd, cost_basis, allocation_basis, coverage,
                            provider_request_id, redaction_count, redaction_rules
                        FROM {self._table('gateway_usage_events')}
                        WHERE organization_id = %s AND occurred_at >= %s
                        ORDER BY occurred_at, id
                        """,
                        (organization, since),
                    )
                    events.extend(usage_audit_event(dict(row)) for row in cursor.fetchall())
                if kind in {"all", "security"}:
                    cursor.execute(
                        f"""
                        SELECT
                            id, occurred_at, evidence_schema_id, evidence_schema_version,
                            organization_id, actor_id, actor_name, team_id, team_name,
                            identity_type, authentication_source, client, protocol, requested_model,
                            policy_version, coverage, action, detection_count, rules
                        FROM {self._table('gateway_secret_events')}
                        WHERE organization_id = %s AND occurred_at >= %s
                        ORDER BY occurred_at, id
                        """,
                        (organization, since),
                    )
                    events.extend(security_audit_event(dict(row)) for row in cursor.fetchall())
        events.sort(key=lambda event: (str(event["occurred_at"]), str(event["id"])))
        return events


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
