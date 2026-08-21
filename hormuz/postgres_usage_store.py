"""PostgreSQL accounting repository with transaction-local tenant isolation.

This store owns gateway usage, provider cost evidence, usage-read audit events,
and budget reservations. Security/DLP approval state has its own PostgreSQL
repository so each invariant remains explicit.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import uuid
from typing import Iterator

from .audit_access import AuditAccessError, authorize_audit_read
from .billing import ProviderCostReport, ProviderCostSource
from .config import Identity, is_model_identifier
from .postgres import (
    DEFAULT_POSTGRES_RUNTIME_ROLE,
    DEFAULT_POSTGRES_SCHEMA,
    PostgresStorageError,
    TenantContext,
    _open_connection,
    tenant_transaction,
    validate_postgres_identifier,
    validate_tenant_id,
)
from .store import (
    ContextLineage,
    MonthlyTotals,
    ProviderCostImportResult,
    ReservationDenied,
    ReservationScope,
    SecurityStoreError,
    _bounded_billing_identifier,
    _decimal_text,
    _extract_latency_histograms,
    _extract_usage_outcomes,
    _latency_select_sql,
    _outcome_select_sql,
    _optional_sha256,
    _sqlite_nonnegative,
    _validated_context_lineage,
    _validated_governance_policy_version,
    _validated_identity_type,
    _validated_optional_latency,
    _validate_provider_cost_report_storage,
    _validate_provider_cost_source,
)
from .usage import (
    sanitize_provider_model_id,
    sanitize_provider_request_id,
    sanitize_provider_usage,
)
from .usage_access import UsageReportAccessError, authorize_usage_report
from .usage_reporting import IDENTITY_TYPES


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class PostgresUsageStore:
    """Tenant-scoped accounting store for a verified PostgreSQL runtime role."""

    backend = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        organization_ids: tuple[str, ...],
        schema: str = DEFAULT_POSTGRES_SCHEMA,
        runtime_role: str = DEFAULT_POSTGRES_RUNTIME_ROLE,
        connect: object | None = None,
    ):
        if not isinstance(dsn, str) or not dsn:
            raise PostgresStorageError("postgres_dsn_unavailable")
        self._dsn = dsn
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
        normalized = tuple(sorted(set(organization_ids)))
        if not normalized:
            raise PostgresStorageError("postgres_tenant_set_empty")
        for organization_id in normalized:
            validate_tenant_id(organization_id)
        self.organization_ids = normalized
        self._connect = connect
        self._qualified = '"' + self.schema + '"'

    def _organization(self, organization_id: str | None) -> str:
        if organization_id is None:
            if len(self.organization_ids) != 1:
                raise ValueError("organization_id is required for multi-tenant PostgreSQL access")
            return self.organization_ids[0]
        validate_tenant_id(organization_id)
        if organization_id not in self.organization_ids:
            raise PostgresStorageError("tenant_not_configured")
        return organization_id

    @contextmanager
    def _transaction(
        self,
        organization_id: str,
        *,
        principal_id: str = "hormuz-system",
        client_id: str = "hormuz-cli",
    ) -> Iterator[object]:
        connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
        try:
            context = TenantContext(
                tenant_id=organization_id,
                principal_id=principal_id,
                client_id=client_id,
                authorization_version=1,
            )
            with tenant_transaction(
                connection,
                context,
                runtime_role=self.runtime_role,
                schema=self.schema,
            ):
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SET LOCAL search_path TO {self._qualified}, pg_catalog"
                    )
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _dict_cursor(connection: object):
        try:
            from psycopg.rows import dict_row  # type: ignore[import-not-found]
        except ImportError:
            raise PostgresStorageError("postgres_driver_unavailable") from None
        return connection.cursor(row_factory=dict_row)  # type: ignore[attr-defined]

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
        context_lineage: ContextLineage | None = None,
        gateway_latency_milliseconds: int | None = None,
        policy_latency_milliseconds: int | None = None,
        provider_latency_milliseconds: int | None = None,
        governance_policy_version: str = "bootstrap-legacy-v1",
    ) -> str:
        organization_id = self._organization(identity.organization_id)
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
        normalized_actual_model = sanitize_provider_model_id(actual_model)
        if actual_model is not None and normalized_actual_model is None:
            raise ValueError("Usage actual model must be a safe bounded model identifier")
        normalized_provider_request_id = sanitize_provider_request_id(provider_request_id)
        if provider_request_id is not None and normalized_provider_request_id is None:
            raise ValueError("Usage provider request ID must be a safe bounded identifier")
        normalized_governance_policy_version = _validated_governance_policy_version(
            governance_policy_version
        )
        normalized_identity_type = _validated_identity_type(identity.identity_type)
        input_count = _sqlite_nonnegative(input_tokens)
        output_count = _sqlite_nonnegative(output_tokens)
        cache_read_count = _sqlite_nonnegative(cache_read_tokens)
        cache_write_count = _sqlite_nonnegative(cache_write_tokens)
        reasoning_count = _sqlite_nonnegative(reasoning_tokens)
        normalized_billable = (
            _sqlite_nonnegative(billable_tokens)
            if isinstance(billable_tokens, int) and not isinstance(billable_tokens, bool)
            else _sqlite_nonnegative(
                input_count
                + output_count
                + (cache_read_count + cache_write_count if protocol == "anthropic" else 0)
            )
        )
        lineage = _validated_context_lineage(context_lineage or ContextLineage())
        event_id = str(uuid.uuid4())
        values = (
            organization_id,
            event_id,
            datetime.now(timezone.utc),
            identity.actor_id,
            identity.actor_name,
            normalized_identity_type,
            identity.team_id,
            identity.team_name,
            client,
            protocol,
            requested_model,
            resolved_alias,
            upstream_model,
            normalized_actual_model,
            policy_action,
            status,
            input_count,
            output_count,
            cache_read_count,
            cache_write_count,
            reasoning_count,
            normalized_billable,
            _sqlite_nonnegative(cost_microusd),
            cost_basis,
            currency,
            rate_card_version,
            json.dumps(normalized_provider_usage, sort_keys=True, separators=(",", ":")),
            normalized_provider_request_id,
            _sqlite_nonnegative(redaction_count),
            json.dumps(sorted(set(redaction_rules)), separators=(",", ":")),
            lineage.mode,
            lineage.outcome,
            lineage.reason,
            lineage.pack_id,
            json.dumps(list(lineage.record_ids), separators=(",", ":")),
            lineage.policy_version,
            lineage.retrieval_version,
            lineage.render_version,
            lineage.repository_revision,
            lineage.estimated_tokens,
            lineage.assembly_milliseconds,
            lineage.reuse_status,
            _validated_optional_latency(gateway_latency_milliseconds),
            _validated_optional_latency(policy_latency_milliseconds),
            _validated_optional_latency(provider_latency_milliseconds),
            normalized_governance_policy_version,
        )
        with self._transaction(
            organization_id,
            principal_id=identity.actor_id,
            client_id=client,
        ) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    """
                    INSERT INTO gateway_usage_events (
                      tenant_id, id, occurred_at, actor_id, actor_name, identity_type, team_id, team_name,
                      client, protocol, requested_model, resolved_alias, upstream_model,
                      actual_model, policy_action, status, input_tokens, output_tokens,
                      cache_read_tokens, cache_write_tokens, reasoning_tokens, billable_tokens,
                      cost_microusd, cost_basis, currency, rate_card_version,
                      provider_usage_json, provider_request_id, redaction_count, redaction_rules,
                      context_injection_mode, context_injection_outcome,
                      context_injection_reason, context_pack_id, context_record_ids_json,
                      context_policy_version, context_retrieval_version, context_render_version,
                      context_repository_revision, context_estimated_tokens,
                      context_assembly_milliseconds, context_reuse_status,
                      gateway_latency_milliseconds, policy_latency_milliseconds,
                      provider_latency_milliseconds, governance_policy_version
                    ) VALUES (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s,
                      %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s
                    )
                    """,
                    values,
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
        aliases = {scope.model_alias for scope in constrained if scope.model_alias is not None}
        if len(aliases) > 1:
            raise ValueError("Budget reservation scopes must use one model alias")
        model_alias = next(iter(aliases), None)
        if model_alias is not None and not is_model_identifier(model_alias):
            raise ValueError("Budget reservation model alias must be a safe identifier")
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        reservation_id = str(uuid.uuid4())
        denial: str | None = None
        with self._transaction(
            organization_id,
            principal_id=identity.actor_id,
            client_id="budget-reservation",
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"hormuz:budget:{organization_id}:{month_start.date().isoformat()}",),
                )
                cursor.execute(
                    "DELETE FROM gateway_budget_reservations WHERE tenant_id = %s AND expires_at <= %s",
                    (organization_id, now),
                )
                for scope in constrained:
                    usage_clauses = ["tenant_id = %s", "occurred_at >= %s"]
                    reservation_clauses = ["tenant_id = %s", "expires_at > %s"]
                    usage_parameters: list[object] = [organization_id, month_start]
                    reservation_parameters: list[object] = [organization_id, now]
                    for column, value in (
                        ("actor_id", scope.actor_id),
                        ("team_id", scope.team_id),
                        ("resolved_alias", scope.model_alias),
                    ):
                        if value is not None:
                            usage_clauses.append(f"{column} = %s")
                            usage_parameters.append(value)
                    for column, value in (
                        ("actor_id", scope.actor_id),
                        ("team_id", scope.team_id),
                        ("model_alias", scope.model_alias),
                    ):
                        if value is not None:
                            reservation_clauses.append(f"{column} = %s")
                            reservation_parameters.append(value)
                    cursor.execute(
                        f"""
                        SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                               COALESCE(SUM(cost_microusd), 0) AS cost_microusd
                        FROM gateway_usage_events WHERE {' AND '.join(usage_clauses)}
                        """,
                        usage_parameters,
                    )
                    usage = cursor.fetchone()
                    cursor.execute(
                        f"""
                        SELECT COALESCE(SUM(reserved_tokens), 0) AS tokens,
                               COALESCE(SUM(reserved_cost_microusd), 0) AS cost_microusd
                        FROM gateway_budget_reservations
                        WHERE {' AND '.join(reservation_clauses)}
                        """,
                        reservation_parameters,
                    )
                    reserved = cursor.fetchone()
                    projected_tokens = int(usage["tokens"]) + int(reserved["tokens"]) + max(0, reserved_tokens)
                    projected_cost = int(usage["cost_microusd"]) + int(reserved["cost_microusd"]) + max(0, reserved_cost_microusd)
                    if scope.token_limit is not None and projected_tokens > scope.token_limit:
                        denial = f"The {scope.name} monthly token limit would be exceeded by this request."
                        break
                    if scope.cost_limit_microusd is not None and projected_cost > scope.cost_limit_microusd:
                        denial = f"The {scope.name} monthly AI budget would be exceeded by this request."
                        break
                if denial is None:
                    cursor.execute(
                        """
                        INSERT INTO gateway_budget_reservations (
                          tenant_id, id, created_at, expires_at, actor_id, team_id,
                          model_alias, reserved_tokens, reserved_cost_microusd
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            organization_id,
                            reservation_id,
                            now,
                            now + timedelta(seconds=max(1, ttl_seconds)),
                            identity.actor_id,
                            identity.team_id,
                            model_alias,
                            max(0, reserved_tokens),
                            max(0, reserved_cost_microusd),
                        ),
                    )
        if denial is not None:
            raise ReservationDenied(denial)
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
        with self._transaction(organization, client_id="budget-reservation") as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "DELETE FROM gateway_budget_reservations WHERE tenant_id = %s AND id = %s",
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
        with self._transaction(organization, client_id="budget-reservation") as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    """
                    UPDATE gateway_budget_reservations SET expires_at = %s
                    WHERE tenant_id = %s AND id = %s
                    """,
                    (
                        datetime.now(timezone.utc) + timedelta(seconds=max(1, ttl_seconds)),
                        organization,
                        reservation_id,
                    ),
                )

    def active_budget_reservations(self, *, organization_id: str | None = None) -> int:
        organizations = (
            (self._organization(organization_id),)
            if organization_id is not None or len(self.organization_ids) == 1
            else self.organization_ids
        )
        count = 0
        for organization in organizations:
            with self._transaction(organization, client_id="budget-reservation") as connection:
                with self._dict_cursor(connection) as cursor:
                    cursor.execute(
                        """
                        SELECT COUNT(*) AS count FROM gateway_budget_reservations
                        WHERE tenant_id = %s AND expires_at > %s
                        """,
                        (organization, datetime.now(timezone.utc)),
                    )
                    count += int(cursor.fetchone()["count"])
        return count

    def monthly_totals(
        self,
        *,
        organization_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
        model_alias: str | None = None,
    ) -> MonthlyTotals:
        organization = self._organization(organization_id)
        if model_alias is not None and not is_model_identifier(model_alias):
            raise ValueError("Usage model alias must be a safe identifier")
        clauses = ["tenant_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [
            organization,
            datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        ]
        for column, value in (
            ("actor_id", actor_id),
            ("team_id", team_id),
            ("resolved_alias", model_alias),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                parameters.append(value)
        with self._transaction(organization) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    f"""
                    SELECT COUNT(*) AS requests,
                      COALESCE(SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END), 0) AS denied_requests,
                      COALESCE(SUM(input_tokens), 0) AS input_tokens,
                      COALESCE(SUM(output_tokens), 0) AS output_tokens,
                      COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                      COALESCE(SUM(cache_write_tokens), 0) AS cache_write_tokens,
                      COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                      COALESCE(SUM(billable_tokens), 0) AS billable_tokens,
                      COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                      COALESCE(SUM(redaction_count), 0) AS redaction_count
                    FROM gateway_usage_events WHERE {' AND '.join(clauses)}
                    """,
                    parameters,
                )
                row = cursor.fetchone()
        return MonthlyTotals(**{key: int(value) for key, value in row.items()})

    def summary_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for organization in self.organization_ids:
            with self._transaction(organization) as connection:
                with self._dict_cursor(connection) as cursor:
                    cursor.execute(
                        """
                        SELECT actor_id, actor_name, team_id, team_name, client, protocol,
                          COUNT(*) AS requests,
                          COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens,
                          COALESCE(SUM(billable_tokens), 0) AS billable_tokens,
                          COALESCE(SUM(cost_microusd), 0) AS cost_microusd,
                          COALESCE(SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END), 0) AS denied,
                          COALESCE(SUM(redaction_count), 0) AS redactions,
                          COALESCE(SUM(CASE WHEN context_injection_outcome = 'injected' THEN 1 ELSE 0 END), 0) AS context_injected_requests,
                          COALESCE(SUM(context_estimated_tokens), 0) AS context_estimated_tokens,
                          COUNT(DISTINCT context_pack_id) AS context_packs_used
                        FROM gateway_usage_events
                        WHERE tenant_id = %s AND occurred_at >= %s
                        GROUP BY actor_id, actor_name, team_id, team_name, client, protocol
                        """,
                        (
                            organization,
                            datetime.now(timezone.utc).replace(
                                day=1,
                                hour=0,
                                minute=0,
                                second=0,
                                microsecond=0,
                            ),
                        ),
                    )
                    rows.extend(dict(row) for row in cursor.fetchall())
        return sorted(
            rows,
            key=lambda row: (
                -int(row["cost_microusd"]),
                -int(row["tokens"]),
                str(row["actor_id"]),
                str(row["client"]),
                str(row["protocol"]),
            ),
        )

    def report_rows(
        self,
        *,
        group_by: str,
        organization_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        include_latency: bool = False,
        include_outcomes: bool = False,
    ) -> list[dict[str, object]]:
        organization = self._organization(organization_id)
        dimensions: dict[str, tuple[list[str], list[str]]] = {
            "organization": (["tenant_id AS scope_id", "tenant_id AS scope_name"], ["tenant_id"]),
            "team": (["team_id AS scope_id", "team_name AS scope_name"], ["team_id", "team_name"]),
            "person": (["actor_id AS scope_id", "actor_name AS scope_name", "team_id", "team_name"], ["actor_id", "actor_name", "team_id", "team_name"]),
            "model": (["COALESCE(actual_model, upstream_model, resolved_alias, requested_model) AS scope_id", "COALESCE(actual_model, upstream_model, resolved_alias, requested_model) AS scope_name", "protocol"], ["COALESCE(actual_model, upstream_model, resolved_alias, requested_model)", "protocol"]),
            "requested_model": (["requested_model AS scope_id", "requested_model AS scope_name", "protocol"], ["requested_model", "protocol"]),
            "actual_model": (["COALESCE(actual_model, 'not_reported') AS scope_id", "COALESCE(actual_model, 'not_reported') AS scope_name", "protocol", "CASE WHEN actual_model IS NULL THEN 0 ELSE 1 END AS actual_model_reported"], ["COALESCE(actual_model, 'not_reported')", "protocol", "CASE WHEN actual_model IS NULL THEN 0 ELSE 1 END"]),
            "policy": (["policy_action AS scope_id", "policy_action AS scope_name"], ["policy_action"]),
            "status": (["status AS scope_id", "status AS scope_name"], ["status"]),
            "client": (["client AS scope_id", "client AS scope_name", "client"], ["client"]),
            "provider": (["protocol AS scope_id", "protocol AS scope_name", "protocol"], ["protocol"]),
        }
        if group_by not in dimensions:
            raise ValueError(f"Unsupported usage report dimension: {group_by}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("Usage report offset must be a non-negative integer")
        if not isinstance(include_latency, bool):
            raise ValueError("Usage report latency selection must be boolean")
        if not isinstance(include_outcomes, bool):
            raise ValueError("Usage report outcome selection must be boolean")
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 101):
            raise ValueError("Usage report limit must be an integer from 1 to 101")
        select_dimensions, group_dimensions = dimensions[group_by]
        clauses = ["tenant_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [
            organization,
            start or datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        ]
        if end is not None:
            clauses.append("occurred_at < %s")
            parameters.append(end)
        for column, value in (("actor_id", actor_id), ("team_id", team_id)):
            if value is not None:
                clauses.append(f"{column} = %s")
                parameters.append(value)
        grouping = ", ".join(group_dimensions)
        tie_breakers = ", " + ", ".join(group_dimensions)
        page = ""
        if limit is not None:
            page = "LIMIT %s OFFSET %s"
            parameters.extend((limit, offset))
        latency_select = _latency_select_sql() if include_latency else ""
        outcome_select = _outcome_select_sql() if include_outcomes else ""
        query = f"""
          SELECT {', '.join(select_dimensions)},
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
            COALESCE(SUM(CASE WHEN cost_basis LIKE 'estimated%%' THEN cost_microusd ELSE 0 END), 0) AS estimated_cost_microusd,
            COALESCE(SUM(CASE WHEN cost_basis = 'not_available' THEN 1 ELSE 0 END), 0) AS unpriced_requests,
            string_agg(DISTINCT cost_basis, ',') AS cost_bases_csv,
            string_agg(DISTINCT currency, ',') AS currencies_csv,
            string_agg(DISTINCT rate_card_version, ',') AS rate_card_versions_csv,
            COUNT(DISTINCT actor_id) AS active_actors,
            COALESCE(SUM(redaction_count), 0) AS redactions,
            COALESCE(SUM(CASE WHEN context_injection_outcome = 'injected' THEN 1 ELSE 0 END), 0) AS context_injected_requests,
            COALESCE(SUM(CASE WHEN context_injection_mode = 'required' AND context_injection_outcome = 'denied' THEN 1 ELSE 0 END), 0) AS context_required_denials,
            COALESCE(SUM(context_estimated_tokens), 0) AS context_estimated_tokens,
            COUNT(DISTINCT context_pack_id) AS context_packs_used
            {latency_select}
            {outcome_select}
          FROM gateway_usage_events
          WHERE {' AND '.join(clauses)}
          GROUP BY {grouping}
          ORDER BY cost_microusd DESC, total_tokens DESC, scope_name ASC, scope_id ASC
            {tie_breakers}
          {page}
        """
        with self._transaction(organization) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(query, parameters)
                rows = cursor.fetchall()
        result: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            for field in ("cost_bases", "currencies", "rate_card_versions"):
                raw = item.pop(field + "_csv")
                item[field] = sorted(set(str(raw).split(","))) if raw else []
            if group_by == "actual_model":
                reported = item.get("actual_model_reported")
                if reported not in {0, 1}:
                    raise ValueError("Actual-model report contains an invalid coverage flag")
                item["actual_model_reported"] = bool(reported)
            if include_latency:
                item["latency"] = _extract_latency_histograms(item)
            if include_outcomes:
                item["outcomes"] = _extract_usage_outcomes(item)
            result.append(item)
        return result

    def coverage_summary(
        self,
        *,
        organization_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, object]:
        """Return the observable gateway coverage for one tenant-bound scope."""

        organization = self._organization(organization_id)
        clauses = ["tenant_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [
            organization,
            start
            or datetime.now(timezone.utc).replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            ),
        ]
        if end is not None:
            clauses.append("occurred_at < %s")
            parameters.append(end)
        for column, value in (("actor_id", actor_id), ("team_id", team_id)):
            if value is not None:
                clauses.append(f"{column} = %s")
                parameters.append(value)
        where = " AND ".join(clauses)
        with self._transaction(organization) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    f"""
                    SELECT
                      COUNT(*) AS accounted_gateway_requests,
                      COALESCE(SUM(CASE WHEN actor_id <> '' AND team_id <> ''
                        THEN 1 ELSE 0 END), 0) AS identity_bound_gateway_requests,
                      COUNT(DISTINCT CASE WHEN actor_id <> '' AND team_id <> ''
                        THEN actor_id END) AS active_identities,
                      COUNT(DISTINCT CASE WHEN actor_id <> '' AND team_id <> ''
                        THEN team_id END) AS active_teams
                    FROM gateway_usage_events
                    WHERE {where}
                    """,
                    parameters,
                )
                totals = cursor.fetchone()
                cursor.execute(
                    f"""
                    SELECT identity_type, COUNT(*) AS requests
                    FROM gateway_usage_events
                    WHERE {where}
                    GROUP BY identity_type
                    ORDER BY identity_type
                    """,
                    parameters,
                )
                identity_rows = cursor.fetchall()
                cursor.execute(
                    f"""
                    SELECT client, COUNT(*) AS requests
                    FROM gateway_usage_events
                    WHERE {where}
                    GROUP BY client
                    ORDER BY client
                    """,
                    parameters,
                )
                client_rows = cursor.fetchall()
        identity_type_requests = {identity_type: 0 for identity_type in IDENTITY_TYPES}
        for row in identity_rows:
            identity_type = str(row["identity_type"])
            if identity_type not in identity_type_requests:
                raise ValueError("Usage coverage contains an unsupported identity type")
            identity_type_requests[identity_type] = int(row["requests"])
        accounted = int(totals["accounted_gateway_requests"])
        identity_bound = int(totals["identity_bound_gateway_requests"])
        return {
            "accounted_gateway_requests": accounted,
            "identity_bound_gateway_requests": identity_bound,
            "unattributed_accounted_gateway_requests": accounted - identity_bound,
            "active_identities": int(totals["active_identities"]),
            "active_teams": int(totals["active_teams"]),
            "identity_type_requests": identity_type_requests,
            "observed_gateway_clients": [
                {"client": str(row["client"]), "requests": int(row["requests"])}
                for row in client_rows
            ],
        }

    def record_admin_usage_read(
        self,
        *,
        administrator: Identity,
        access_scope: str,
        group_by: str,
        actor_filter: str | None,
        team_filter: str | None,
        window_start: str,
        window_end: str,
        result_count: int,
    ) -> str:
        organization = self._organization(administrator.organization_id)
        try:
            access = authorize_usage_report(
                administrator,
                group_by=group_by,
                actor_id=actor_filter,
                team_id=team_filter,
            )
        except UsageReportAccessError as error:
            raise SecurityStoreError(error.code) from error
        if (
            access.scope != access_scope
            or access.actor_id != actor_filter
            or access.team_id != team_filter
        ):
            raise SecurityStoreError("usage_admin_audit_scope_mismatch")
        if isinstance(result_count, bool) or not isinstance(result_count, int) or result_count < 0:
            raise SecurityStoreError("invalid_usage_report_request")
        event_id = str(uuid.uuid4())
        try:
            with self._transaction(
                organization,
                principal_id=administrator.actor_id,
                client_id="usage-report",
            ) as connection:
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        """
                        INSERT INTO gateway_admin_access_events (
                          tenant_id, id, occurred_at, decision_actor_id,
                          decision_actor_name, action, group_by, actor_filter_sha256,
                          team_filter_sha256, window_start, window_end, result_count
                        ) VALUES (%s, %s, %s, %s, %s, 'usage.report.read', %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            organization,
                            event_id,
                            datetime.now(timezone.utc),
                            administrator.actor_id,
                            administrator.actor_name,
                            group_by,
                            _optional_sha256(actor_filter),
                            _optional_sha256(team_filter),
                            window_start,
                            window_end,
                            result_count,
                        ),
                    )
        except PostgresStorageError as error:
            raise SecurityStoreError("usage_admin_audit_unavailable") from error
        return event_id

    def record_admin_audit_read(
        self,
        *,
        administrator: Identity,
        kind: str,
        window_start: str,
        window_end: str,
        result_count: int,
    ) -> str:
        organization = self._organization(administrator.organization_id)
        try:
            authorize_audit_read(administrator)
        except AuditAccessError as error:
            raise SecurityStoreError(error.code) from error
        if kind not in {"all", "usage", "security"}:
            raise SecurityStoreError("invalid_audit_event_request")
        if isinstance(result_count, bool) or not isinstance(result_count, int) or result_count < 0:
            raise SecurityStoreError("invalid_audit_event_request")
        event_id = str(uuid.uuid4())
        try:
            with self._transaction(
                organization,
                principal_id=administrator.actor_id,
                client_id="audit-events",
            ) as connection:
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(
                        """
                        INSERT INTO gateway_admin_access_events (
                          tenant_id, id, occurred_at, decision_actor_id,
                          decision_actor_name, action, group_by, actor_filter_sha256,
                          team_filter_sha256, window_start, window_end, result_count
                        ) VALUES (%s, %s, %s, %s, %s, 'audit.events.read', %s, NULL, NULL, %s, %s, %s)
                        """,
                        (
                            organization,
                            event_id,
                            datetime.now(timezone.utc),
                            administrator.actor_id,
                            administrator.actor_name,
                            kind,
                            window_start,
                            window_end,
                            result_count,
                        ),
                    )
        except PostgresStorageError as error:
            raise SecurityStoreError("audit_admin_audit_unavailable") from error
        return event_id

    def import_provider_cost_report(
        self,
        *,
        organization_id: str,
        report: ProviderCostReport,
        source: ProviderCostSource | None = None,
    ) -> ProviderCostImportResult:
        organization = self._organization(
            _bounded_billing_identifier(organization_id, label="organization ID", maximum=256)
        )
        _validate_provider_cost_report_storage(report)
        source = source or ProviderCostSource.offline()
        _validate_provider_cost_source(report=report, source=source)
        imported_at = datetime.now(timezone.utc)
        created = False
        source_created = False
        with self._transaction(organization, client_id="billing-import") as connection:
            with self._dict_cursor(connection) as cursor:
                import_id = "pci_" + uuid.uuid4().hex
                cursor.execute(
                    """
                    INSERT INTO gateway_provider_cost_imports (
                      tenant_id, id, imported_at, provider, source_sha256,
                      report_start, report_end, page_count, bucket_count, item_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, provider, source_sha256) DO NOTHING
                    RETURNING id
                    """,
                    (
                        organization, import_id, imported_at, report.provider,
                        report.source_sha256, report.report_start, report.report_end,
                        report.page_count, report.bucket_count, len(report.items),
                    ),
                )
                inserted = cursor.fetchone()
                if inserted is None:
                    cursor.execute(
                        """
                        SELECT id FROM gateway_provider_cost_imports
                        WHERE tenant_id = %s AND provider = %s AND source_sha256 = %s
                        """,
                        (organization, report.provider, report.source_sha256),
                    )
                    import_id = str(cursor.fetchone()["id"])
                else:
                    created = True
                    for ordinal, item in enumerate(report.items):
                        cursor.execute(
                            """
                            INSERT INTO gateway_provider_cost_items (
                              tenant_id, id, import_id, item_ordinal, bucket_start,
                              bucket_end, amount_usd, currency, provider_scope_kind,
                              provider_scope_id, line_item, cost_type, model, service_tier,
                              token_type, context_window, inference_geo
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                organization, "pciitem_" + uuid.uuid4().hex, import_id,
                                ordinal, item.bucket_start, item.bucket_end, item.amount_usd,
                                item.currency, item.provider_scope_kind, item.provider_scope_id,
                                item.line_item, item.cost_type, item.model, item.service_tier,
                                item.token_type, item.context_window, item.inference_geo,
                            ),
                        )
                canonical = json.dumps(
                    {
                        "import_id": import_id,
                        "source_kind": source.kind,
                        "api_contract": source.api_contract,
                        "query_start": source.query_start,
                        "query_end": source.query_end,
                        "query_scope": source.query_scope,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                source_id = "pcisrc_" + hashlib.sha256(canonical).hexdigest()[:32]
                cursor.execute(
                    """
                    INSERT INTO gateway_provider_cost_sources (
                      tenant_id, id, import_id, observed_at, source_kind,
                      api_contract, query_start, query_end, query_scope
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, id) DO NOTHING RETURNING id
                    """,
                    (
                        organization, source_id, import_id, imported_at, source.kind,
                        source.api_contract, source.query_start, source.query_end,
                        source.query_scope,
                    ),
                )
                source_created = cursor.fetchone() is not None
                cursor.execute(
                    """
                    SELECT id, tenant_id AS organization_id, provider, source_sha256,
                      report_start, report_end, page_count, bucket_count, item_count
                    FROM gateway_provider_cost_imports
                    WHERE tenant_id = %s AND id = %s
                    """,
                    (organization, import_id),
                )
                row = cursor.fetchone()
        return ProviderCostImportResult(
            import_id=str(row["id"]),
            created=created,
            organization_id=str(row["organization_id"]),
            provider=str(row["provider"]),
            source_sha256=str(row["source_sha256"]),
            report_start=_iso(row["report_start"]),
            report_end=_iso(row["report_end"]),
            page_count=int(row["page_count"]),
            bucket_count=int(row["bucket_count"]),
            item_count=int(row["item_count"]),
            source_kind=source.kind,
            source_evidence_created=source_created,
            provider_report_completeness=(
                "authenticated_query_pagination_complete"
                if source.kind == "authenticated_api"
                else "not_verifiable_from_response"
            ),
            api_contract=source.api_contract,
            query_start=source.query_start,
            query_end=source.query_end,
            query_scope=source.query_scope,
        )

    def reconcile_provider_costs(
        self,
        *,
        organization_id: str,
        provider: str,
        import_id: str | None = None,
    ) -> dict[str, object]:
        organization = self._organization(
            _bounded_billing_identifier(organization_id, label="organization ID", maximum=256)
        )
        if provider not in {"openai", "anthropic"}:
            raise ValueError("Provider must be openai or anthropic")
        if import_id is not None:
            import_id = _bounded_billing_identifier(import_id, label="provider cost import ID", maximum=64)
            if not import_id.startswith("pci_"):
                raise ValueError("Provider cost import ID is invalid")
        imported = source_row = gateway = None
        cost_rows: list[dict[str, object]] = []
        with self._transaction(organization, client_id="billing-reconcile") as connection:
            with self._dict_cursor(connection) as cursor:
                parameters: list[object] = [organization, provider]
                clause = ""
                if import_id is not None:
                    clause = "AND id = %s"
                    parameters.append(import_id)
                try:
                    cursor.execute(
                        f"""
                        SELECT id, imported_at, tenant_id AS organization_id, provider,
                          source_sha256, report_start, report_end, page_count, bucket_count, item_count
                        FROM gateway_provider_cost_imports
                        WHERE tenant_id = %s AND provider = %s {clause}
                        ORDER BY imported_at DESC, id DESC LIMIT 1
                        """,
                        parameters,
                    )
                    imported = cursor.fetchone()
                except Exception:
                    raise PostgresStorageError("provider_cost_import_read_failed") from None
                if imported is not None:
                    try:
                        cursor.execute(
                            """
                            SELECT source_kind, api_contract, query_start, query_end, query_scope
                            FROM gateway_provider_cost_sources
                            WHERE tenant_id = %s AND import_id = %s
                            ORDER BY CASE source_kind WHEN 'authenticated_api' THEN 0 ELSE 1 END,
                              observed_at DESC, id DESC LIMIT 1
                            """,
                            (organization, imported["id"]),
                        )
                        source_row = cursor.fetchone()
                    except Exception:
                        raise PostgresStorageError("provider_cost_source_read_failed") from None
                    try:
                        cursor.execute(
                            """
                            SELECT amount_usd, provider_scope_kind, provider_scope_id,
                              line_item, cost_type
                            FROM gateway_provider_cost_items
                            WHERE tenant_id = %s AND import_id = %s ORDER BY item_ordinal
                            """,
                            (organization, imported["id"]),
                        )
                        cost_rows = cursor.fetchall()
                    except Exception:
                        raise PostgresStorageError("provider_cost_items_read_failed") from None
                    try:
                        cursor.execute(
                            """
                            SELECT COUNT(*) AS requests,
                              COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS succeeded,
                              COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                              COALESCE(SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END), 0) AS denied,
                              COALESCE(SUM(CASE WHEN cost_basis LIKE 'estimated%%' THEN cost_microusd ELSE 0 END), 0) AS estimated_cost_microusd,
                              COALESCE(SUM(CASE WHEN cost_basis = 'not_available' THEN 1 ELSE 0 END), 0) AS unpriced_requests,
                              COUNT(DISTINCT actor_id) AS active_actors,
                              COUNT(DISTINCT team_id) AS active_teams
                            FROM gateway_usage_events
                            WHERE tenant_id = %s AND protocol = %s
                              AND occurred_at >= %s AND occurred_at < %s
                            """,
                            (organization, provider, imported["report_start"], imported["report_end"]),
                        )
                        gateway = cursor.fetchone()
                    except Exception:
                        raise PostgresStorageError("provider_cost_usage_read_failed") from None
        if imported is None:
            raise ValueError("Provider cost import not found in this organization and provider scope")
        if source_row is None:
            raise ValueError("Provider billing source evidence is missing")
        if len(cost_rows) != int(imported["item_count"]):
            raise ValueError("Provider billing store item count does not match its import")
        try:
            provider_amounts = [Decimal(str(row["amount_usd"])) for row in cost_rows]
            if any(not amount.is_finite() for amount in provider_amounts):
                raise InvalidOperation
        except InvalidOperation as error:
            raise ValueError("Provider billing store contains an invalid amount") from error
        provider_cost = sum(provider_amounts, Decimal(0))
        estimated_cost = Decimal(int(gateway["estimated_cost_microusd"])) / Decimal(1_000_000)
        variance = provider_cost - estimated_cost
        authenticated = source_row["source_kind"] == "authenticated_api"
        unscoped = sum(1 for row in cost_rows if row["provider_scope_kind"] == "unscoped")
        return {
            "schema_version": 1,
            "organization_id": organization,
            "provider": provider,
            "import_id": str(imported["id"]),
            "imported_at": _iso(imported["imported_at"]),
            "source_sha256": str(imported["source_sha256"]),
            "report_start": _iso(imported["report_start"]),
            "report_end": _iso(imported["report_end"]),
            "page_count": int(imported["page_count"]),
            "bucket_count": int(imported["bucket_count"]),
            "provider_items": len(cost_rows),
            "scoped_provider_items": len(cost_rows) - unscoped,
            "unscoped_provider_items": unscoped,
            "negative_provider_items": sum(1 for amount in provider_amounts if amount < 0),
            "zero_provider_items": sum(1 for amount in provider_amounts if amount == 0),
            "unclassified_provider_items": sum(1 for row in cost_rows if row["cost_type"] is None),
            "provider_cost_basis": "provider_reported",
            "provider_cost_usd": _decimal_text(provider_cost),
            "gateway_cost_basis": "request_time_estimated",
            "gateway_estimated_cost_usd": _decimal_text(estimated_cost),
            "variance_usd": _decimal_text(variance),
            "possible_unobserved_or_adjusted_cost_usd": _decimal_text(max(variance, Decimal(0))),
            "gateway_requests": int(gateway["requests"]),
            "gateway_succeeded": int(gateway["succeeded"]),
            "gateway_failed": int(gateway["failed"]),
            "gateway_denied": int(gateway["denied"]),
            "gateway_unpriced_requests": int(gateway["unpriced_requests"]),
            "active_actors": int(gateway["active_actors"]),
            "active_teams": int(gateway["active_teams"]),
            "legacy_unattributed_gateway_requests": 0,
            "gateway_scope_status": "organization_scoped_gateway_window",
            "provider_report_completeness": "authenticated_query_pagination_complete" if authenticated else "not_verifiable_from_response",
            "coverage_status": "partial_authenticated_provider_endpoint_scope" if authenticated else "partial_unverified_provider_scope",
            "provider_scope_attribution": "provider_admin_credential_bound_query" if authenticated else "operator_bound_to_organization",
            "provider_source_kind": str(source_row["source_kind"]),
            "provider_api_contract": source_row["api_contract"],
            "query_start": _iso(source_row["query_start"]) if source_row["query_start"] is not None else None,
            "query_end": _iso(source_row["query_end"]) if source_row["query_end"] is not None else None,
            "query_scope": source_row["query_scope"],
            "person_cost_basis": "estimated",
            "request_final_cost_available": False,
            "variance_proves_gateway_bypass": False,
            "credits_discounts_adjustments_treatment": "signed_provider_amounts_included_without_reclassification",
            "rounding_treatment": "exact_provider_decimal_and_gateway_micro_usd",
            "cache_batch_line_item_treatment": "provider_dimensions_preserved_without_repricing",
            "failed_rate_limited_treatment": "gateway_status_counts_reported_separately",
            "raw_payload_retained": False,
            "credential_retained": False,
        }

    def audit_events(
        self,
        *,
        since: str,
        kind: str = "all",
        organization_id: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, object]]:
        if kind not in {"all", "usage", "security"}:
            raise ValueError(f"Unsupported audit event kind: {kind}")
        organizations = (
            (self._organization(organization_id),)
            if organization_id is not None
            else self.organization_ids
        )
        until_clause = " AND occurred_at <= %s" if until is not None else ""
        events: list[dict[str, object]] = []
        for organization in organizations:
            parameters: tuple[object, ...] = (organization, since)
            if until is not None:
                parameters += (until,)
            with self._transaction(organization, client_id="audit-export") as connection:
                with self._dict_cursor(connection) as cursor:
                    if kind in {"all", "usage"}:
                        cursor.execute(
                            """
                            SELECT id, occurred_at, tenant_id AS organization_id, actor_id,
                              actor_name, identity_type, team_id, team_name, client, protocol, requested_model,
                              resolved_alias, upstream_model, actual_model, policy_action, status,
                              input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                              reasoning_tokens, billable_tokens, cost_microusd, cost_basis,
                              currency, rate_card_version, provider_usage_json,
                              provider_request_id, redaction_count, redaction_rules,
                              context_injection_mode, context_injection_outcome,
                              context_injection_reason, context_pack_id, context_record_ids_json,
                              context_policy_version, context_retrieval_version,
                              context_render_version, context_repository_revision,
                              context_estimated_tokens, context_assembly_milliseconds,
                              context_reuse_status, governance_policy_version
                            FROM gateway_usage_events
                            WHERE tenant_id = %s AND occurred_at >= %s{until_clause}
                            ORDER BY occurred_at, id
                            """.format(until_clause=until_clause),
                            parameters,
                        )
                        for row in cursor.fetchall():
                            event = dict(row)
                            event["occurred_at"] = _iso(event["occurred_at"])
                            event["provider_usage"] = event.pop("provider_usage_json")
                            event["context_record_ids"] = event.pop("context_record_ids_json")
                            events.append({"schema_version": 4, "event_type": "usage", **event})
                    if kind in {"all", "security"}:
                        cursor.execute(
                            """
                            SELECT id, occurred_at, tenant_id AS organization_id,
                              decision_actor_id, decision_actor_name, action, group_by,
                              actor_filter_sha256, team_filter_sha256, window_start,
                              window_end, result_count
                            FROM gateway_admin_access_events
                            WHERE tenant_id = %s AND occurred_at >= %s{until_clause}
                            ORDER BY occurred_at, id
                            """.format(until_clause=until_clause),
                            parameters,
                        )
                        for row in cursor.fetchall():
                            event = dict(row)
                            for field in ("occurred_at", "window_start", "window_end"):
                                event[field] = _iso(event[field])
                            events.append(
                                {
                                    "schema_version": 1,
                                    "event_type": (
                                        "security.admin.audit_read"
                                        if event["action"] == "audit.events.read"
                                        else "security.admin.usage_read"
                                    ),
                                    **event,
                                }
                            )
        events.sort(key=lambda event: (str(event["occurred_at"]), str(event["id"])))
        return events
