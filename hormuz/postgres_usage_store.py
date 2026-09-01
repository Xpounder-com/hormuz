"""PostgreSQL implementation of Hormuz's metadata-only usage repository."""

from __future__ import annotations

import json
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .audit_chain import (
    AuditChainAnchorStatus,
    AuditChainError,
    AuditChainHead,
    AuditChainSource,
    audit_chain_checkpoint_summary,
    build_audit_chain_entry,
    canonical_json_text,
)
from ._audit_verifier import verify_audit_chain_inputs
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
from .evidence import EvidenceStorageError, security_audit_event, usage_audit_event
from .budget_runtime import (
    RuntimeBudgetSQL,
    WorkBudgetDenied,
    audit_work_budget_denials,
    enforce_and_bind_work_budget,
    prepare_work_budget,
    record_work_budget_denial,
)
from .postgres import (
    PostgresConnectionPool,
    PostgresStorageError,
    postgres_transaction,
    validate_postgres_identifier,
    verify_postgres_schema,
)
from ._persistence import (
    MonthlyTotals,
    RequestAttempt,
    RequestAttemptState,
    RequestAttemptStateError,
    ReservationDenied,
    ReservationScope,
    WorkBudgetContext,
    SecretTotals,
    AuditChainSourceEventInput,
    AuditChainVerificationInputs,
    build_request_attempt_event,
    build_request_attempt_root,
    is_sha256_digest,
    normalize_audit_chain_checkpoint_input,
    normalize_audit_chain_entry_input,
    normalize_audit_chain_epoch_input,
    normalize_audit_chain_head,
    normalize_audit_chain_source_event_input,
    normalize_request_attempt_result,
    normalize_request_attempt_state,
    monthly_usage_bounds,
    require_pending_request_attempt_state,
    require_terminal_request_attempt_state,
    should_mark_request_attempt_unknown,
    stored_utc_timestamp,
    validate_anchor_age,
)


_DATETIME_TYPE = datetime


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
        audit_chain_maximum_anchor_age_seconds: int | None = None,
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
        if (
            audit_chain_maximum_anchor_age_seconds is not None
            and (
                isinstance(audit_chain_maximum_anchor_age_seconds, bool)
                or not isinstance(audit_chain_maximum_anchor_age_seconds, int)
                or audit_chain_maximum_anchor_age_seconds < 1
            )
        ):
            raise PostgresStorageError("audit_chain_configuration_invalid")
        self.audit_chain_maximum_anchor_age_seconds = audit_chain_maximum_anchor_age_seconds
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

    @staticmethod
    def _database_now_in_cursor(cursor: object) -> datetime:
        """Read one PostgreSQL clock value for the current transaction step."""

        cursor.execute("SELECT clock_timestamp() AS now")
        row = cursor.fetchone()
        value = None if row is None else (
            row.get("now") if isinstance(row, dict) else row[0]
        )
        if (
            type(value) is not _DATETIME_TYPE
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise PostgresStorageError("storage_unavailable")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _acquire_budget_month_in_cursor(
        cursor: object,
        *,
        organization_id: str,
        observed_at: datetime,
    ) -> datetime:
        """Lock the monthly reservation key selected by a trusted clock."""

        month_start = observed_at.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0,
        )
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"hormuz:budget:{organization_id}:{month_start.date().isoformat()}",),
        )
        return month_start

    def _acquire_current_budget_month_in_cursor(
        self,
        cursor: object,
        *,
        organization_id: str,
    ) -> tuple[datetime, datetime]:
        """Lock the database's current month before any request-attempt lock."""

        observed = self._database_now_in_cursor(cursor)
        prior_month: datetime | None = None
        for _ in range(2):
            month_start = observed.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if prior_month is not None and month_start <= prior_month:
                raise PostgresStorageError("storage_unavailable")
            self._acquire_budget_month_in_cursor(
                cursor,
                organization_id=organization_id,
                observed_at=observed,
            )
            confirmed = self._database_now_in_cursor(cursor)
            confirmed_month = confirmed.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0,
            )
            if confirmed_month == month_start:
                return month_start, confirmed
            if confirmed_month < month_start:
                raise PostgresStorageError("storage_unavailable")
            prior_month, observed = month_start, confirmed
        raise PostgresStorageError("storage_unavailable")

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
                    for table in (
                        "gateway_audit_chain_epochs",
                        "gateway_audit_chain_entries",
                        "gateway_audit_chain_checkpoints",
                    ):
                        cursor.execute(
                            """
                            SELECT
                                has_table_privilege(current_user, %s, 'UPDATE') AS can_update,
                                has_table_privilege(current_user, %s, 'DELETE') AS can_delete
                            """,
                            (self._table(table), self._table(table)),
                        )
                        privileges = cursor.fetchone()
                        if (
                            privileges is None
                            or bool(privileges["can_update"])
                            or bool(privileges["can_delete"])
                        ):
                            raise PostgresStorageError("audit_chain_runtime_privilege_excess")
                    if self.audit_chain_maximum_anchor_age_seconds is not None:
                        status = self._audit_chain_anchor_status_in_cursor(
                            cursor,
                            organization_id=organization_id,
                            maximum_age_seconds=self.audit_chain_maximum_anchor_age_seconds,
                            now=datetime.now(timezone.utc),
                        )
                        if status.overdue:
                            raise PostgresStorageError("audit_chain_anchor_overdue")

    def _audit_chain_head_in_cursor(
        self,
        cursor: Any,
        *,
        organization_id: str,
        create: bool = True,
        for_update: bool = False,
        for_share: bool = False,
    ) -> AuditChainHead | None:
        """Return one tenant head, lazily creating only an initial empty epoch."""

        if create:
            cursor.execute(
                f"""
                INSERT INTO {self._table('gateway_audit_chain_epochs')} (
                    organization_id, chain_version, chain_epoch, created_at, reason_code,
                    predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
                ) VALUES (%s, 1, 1, CURRENT_TIMESTAMP, 'initial_adoption', NULL, NULL, NULL)
                ON CONFLICT (organization_id, chain_epoch) DO NOTHING
                """,
                (organization_id,),
            )
            cursor.execute(
                f"""
                INSERT INTO {self._table('gateway_audit_chain_heads')} (
                    organization_id, chain_version, chain_epoch, sequence, head_digest
                ) VALUES (%s, 1, 1, 0, NULL)
                ON CONFLICT (organization_id) DO NOTHING
                """,
                (organization_id,),
            )
        if for_update and for_share:
            raise PostgresStorageError("audit_chain_lock_invalid")
        lock = " FOR UPDATE" if for_update else " FOR SHARE" if for_share else ""
        cursor.execute(
            f"""
            SELECT organization_id, chain_version, chain_epoch, sequence, head_digest
            FROM {self._table('gateway_audit_chain_heads')}
            WHERE organization_id = %s{lock}
            """,
            (organization_id,),
        )
        row = cursor.fetchone()
        if row is None:
            if not create:
                return None
            raise PostgresStorageError("audit_chain_head_unavailable")
        return normalize_audit_chain_head(row, error_factory=PostgresStorageError)

    def _append_audit_chain_entry_in_cursor(
        self,
        cursor: Any,
        *,
        event: Mapping[str, object],
    ) -> AuditChainHead:
        """Append one event and head update in the caller's existing transaction."""

        organization_id = event.get("organization_id")
        event_id = event.get("id")
        if not isinstance(organization_id, str) or not organization_id or not isinstance(event_id, str) or not event_id:
            raise PostgresStorageError("audit_chain_event_malformed")
        head = self._audit_chain_head_in_cursor(
            cursor,
            organization_id=organization_id,
            for_update=True,
        )
        if head is None:
            raise PostgresStorageError("audit_chain_head_unavailable")
        try:
            entry = build_audit_chain_entry(
                event,
                chain_version=head.chain_version,
                chain_epoch=head.chain_epoch,
                sequence=head.sequence + 1,
                previous_digest=head.head_digest,
            )
        except AuditChainError as error:
            raise PostgresStorageError(error.code) from None
        event_value = entry["event"]
        if not isinstance(event_value, Mapping):
            raise PostgresStorageError("audit_chain_entry_malformed")
        cursor.execute(
            f"""
            INSERT INTO {self._table('gateway_audit_chain_entries')} (
                organization_id, chain_version, chain_epoch, sequence,
                entry_schema_id, entry_schema_version, event_id, previous_digest,
                event_digest, event_json, appended_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                organization_id,
                entry["chain_version"],
                entry["chain_epoch"],
                entry["sequence"],
                entry["schema_id"],
                entry["schema_version"],
                event_id,
                entry["previous_digest"],
                entry["event_digest"],
                canonical_json_text(dict(event_value)),
            ),
        )
        if cursor.rowcount != 1:
            raise PostgresStorageError("audit_chain_entry_unavailable")
        cursor.execute(
            f"""
            UPDATE {self._table('gateway_audit_chain_heads')}
            SET sequence = %s, head_digest = %s
            WHERE organization_id = %s
              AND chain_version = %s
              AND chain_epoch = %s
              AND sequence = %s
              AND head_digest IS NOT DISTINCT FROM %s
            """,
            (
                entry["sequence"],
                entry["event_digest"],
                organization_id,
                head.chain_version,
                head.chain_epoch,
                head.sequence,
                head.head_digest,
            ),
        )
        if cursor.rowcount != 1:
            raise PostgresStorageError("audit_chain_head_conflict")
        return AuditChainHead(
            organization_id=organization_id,
            chain_version=head.chain_version,
            chain_epoch=head.chain_epoch,
            sequence=int(entry["sequence"]),
            head_digest=str(entry["event_digest"]),
        )

    def _usage_audit_event_in_cursor(self, cursor: Any, event_id: str) -> dict[str, object]:
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
            WHERE id = %s
            """,
            (event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise PostgresStorageError("audit_chain_source_event_missing")
        try:
            return usage_audit_event(dict(row))
        except EvidenceStorageError as error:
            raise PostgresStorageError(error.code) from None

    def _secret_audit_event_in_cursor(self, cursor: Any, event_id: str) -> dict[str, object]:
        cursor.execute(
            f"""
            SELECT
                id, occurred_at, evidence_schema_id, evidence_schema_version,
                organization_id, actor_id, actor_name, team_id, team_name,
                identity_type, authentication_source, client, protocol, requested_model,
                policy_version, coverage, action, detection_count, rules
            FROM {self._table('gateway_secret_events')}
            WHERE id = %s
            """,
            (event_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise PostgresStorageError("audit_chain_source_event_missing")
        try:
            return security_audit_event(dict(row))
        except EvidenceStorageError as error:
            raise PostgresStorageError(error.code) from None

    def _record_in_cursor(
        self,
        cursor: object,
        *,
        occurred_at: datetime | None = None,
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
        """Insert one usage event through the caller's transaction cursor."""

        validate_policy_action(policy_action)
        validate_request_status(status)
        organization_id = self._organization(identity.organization_id)
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc) if occurred_at is None else occurred_at
        if (
            type(timestamp) is not _DATETIME_TYPE
            or timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            raise PostgresStorageError("storage_unavailable")
        timestamp = timestamp.astimezone(timezone.utc)
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
                timestamp,
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
        self._append_audit_chain_entry_in_cursor(
            cursor,
            event=self._usage_audit_event_in_cursor(cursor, event_id),
        )
        return event_id

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
        organization_id = self._organization(identity.organization_id)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                return self._record_in_cursor(
                    cursor,
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=requested_model,
                    resolved_alias=resolved_alias,
                    upstream_model=upstream_model,
                    provider_reported_model=provider_reported_model,
                    policy_version=policy_version,
                    policy_action=policy_action,
                    status=status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cost_microusd=cost_microusd,
                    cost_basis=cost_basis,
                    allocation_basis=allocation_basis,
                    coverage=coverage,
                    provider_request_id=provider_request_id,
                    redaction_count=redaction_count,
                    redaction_rules=redaction_rules,
                )

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
                self._append_audit_chain_entry_in_cursor(
                    cursor,
                    event=self._secret_audit_event_in_cursor(cursor, event_id),
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
        # Preserve the v1 repository clock contract for legacy monthly holds.
        # Work-budget enforcement below uses PostgreSQL's clock instead.
        now = datetime.now(timezone.utc)
        reservation_id = str(uuid.uuid4())
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._acquire_budget_month_in_cursor(
                    cursor,
                    organization_id=organization_id,
                    observed_at=now,
                )
                self._sweep_stale_request_attempts_in_cursor(
                    cursor,
                    now=now,
                    occurred_at=self._database_now_in_cursor(cursor),
                    organization_id=organization_id,
                )
                self._reserve_budget_in_cursor(
                    cursor,
                    identity=identity,
                    scopes=constrained,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_microusd=reserved_cost_microusd,
                    ttl_seconds=ttl_seconds,
                    reservation_id=reservation_id,
                    attempt_id=None,
                    now=now,
                )
        return reservation_id

    def _record_work_budget_denial(self, identity: Identity, denial: WorkBudgetDenied) -> None:
        organization_id = self._organization(identity.organization_id)
        try:
            with self._transaction(organization_id) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"work-budget:{self.schema}:{organization_id}",),
                    )
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (f"portfolio:{self.schema}:{organization_id}",),
                    )
                    record_work_budget_denial(
                        RuntimeBudgetSQL(cursor, postgres=True),
                        organization_id=organization_id,
                        actor_id=identity.actor_id,
                        denial=denial,
                    )
        except ReservationDenied:
            raise PostgresStorageError("storage_unavailable") from None

    def begin_request_attempt(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_version: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
    ) -> RequestAttempt:
        return self._begin_request_attempt_with_work_budget(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            resolved_alias=resolved_alias,
            upstream_model=upstream_model,
            policy_version=policy_version,
            policy_action=policy_action,
            redaction_count=redaction_count,
            redaction_rules=redaction_rules,
            scopes=scopes,
            reserved_tokens=reserved_tokens,
            reserved_cost_microusd=reserved_cost_microusd,
            ttl_seconds=ttl_seconds,
            work_budget=None,
        )

    @audit_work_budget_denials
    def _begin_request_attempt_with_work_budget(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        resolved_alias: str | None,
        upstream_model: str | None,
        policy_version: str,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        work_budget: WorkBudgetContext | None,
    ) -> RequestAttempt:
        """Atomically persist a pending pre-egress attempt and its budget hold."""

        organization_id = self._organization(identity.organization_id)
        attempt_id = str(uuid.uuid4())
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT EXISTS (SELECT 1 FROM "
                    f"{self._table('hormuz_schema_migrations')} "
                    "WHERE version=13 AND state='applied')"
                )
                budget_schema_row = cursor.fetchone()
                budget_schema_ready = bool(
                    next(iter(budget_schema_row.values()))
                    if isinstance(budget_schema_row, dict)
                    else budget_schema_row[0]
                )
                if work_budget is not None and not budget_schema_ready:
                    raise PostgresStorageError("storage_schema_partial_upgrade")
                if work_budget is None:
                    # Legacy callers retain the injectable v1 repository
                    # clock; the lock still precedes every request row lock.
                    now = datetime.now(timezone.utc)
                    locked_month = self._acquire_budget_month_in_cursor(
                        cursor,
                        organization_id=organization_id,
                        observed_at=now,
                    )
                else:
                    locked_month, _locked_at = self._acquire_current_budget_month_in_cursor(
                        cursor, organization_id=organization_id,
                    )
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"work-budget:{self.schema}:{organization_id}",),
                )
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"portfolio:{self.schema}:{organization_id}",),
                )
                if work_budget is not None:
                    now = self._database_now_in_cursor(cursor)
                    if now.replace(
                        day=1, hour=0, minute=0, second=0, microsecond=0,
                    ) != locked_month:
                        # Do not acquire a later monthly lock while holding the
                        # work/portfolio locks; that would invert the canonical
                        # reservation order at a month boundary.
                        raise PostgresStorageError("storage_unavailable")
                root = build_request_attempt_root(
                    attempt_id=attempt_id,
                    created_at=now,
                    identity=identity,
                    organization_id=organization_id,
                    client=client,
                    protocol=protocol,
                    requested_model=requested_model,
                    resolved_alias=resolved_alias,
                    upstream_model=upstream_model,
                    policy_version=policy_version,
                    policy_action=policy_action,
                    redaction_count=redaction_count,
                    redaction_rules=redaction_rules,
                    reserved_tokens=reserved_tokens,
                    reserved_cost_microusd=reserved_cost_microusd,
                )
                self._sweep_stale_request_attempts_in_cursor(
                    cursor,
                    now=now,
                    occurred_at=(
                        now
                        if work_budget is not None
                        else self._database_now_in_cursor(cursor)
                    ),
                    organization_id=organization_id,
                )
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('gateway_request_attempts')} (
                        attempt_id, created_at, evidence_schema_id, evidence_schema_version,
                        organization_id, actor_id, actor_name, team_id, team_name,
                        identity_type, authentication_source, client, protocol, requested_model,
                        resolved_alias, upstream_model, policy_version, policy_action,
                        redaction_count, redaction_rules, reserved_tokens, reserved_cost_microusd
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        root["attempt_id"],
                        now,
                        root["evidence_schema_id"],
                        root["evidence_schema_version"],
                        root["organization_id"],
                        root["actor_id"],
                        root["actor_name"],
                        root["team_id"],
                        root["team_name"],
                        root["identity_type"],
                        root["authentication_source"],
                        root["client"],
                        root["protocol"],
                        root["requested_model"],
                        root["resolved_alias"],
                        root["upstream_model"],
                        root["policy_version"],
                        root["policy_action"],
                        root["redaction_count"],
                        json.dumps(root["redaction_rules"], separators=(",", ":")),
                        root["reserved_tokens"],
                        root["reserved_cost_microusd"],
                    ),
                )
                self._append_request_attempt_event_in_cursor(
                    cursor,
                    attempt_id=attempt_id,
                    organization_id=organization_id,
                    occurred_at=now,
                    sequence=1,
                    state="pending",
                    reason_code=None,
                    usage_event_id=None,
                )
                budget_sql = RuntimeBudgetSQL(cursor, postgres=True)
                prepared_budget = (
                    prepare_work_budget(
                        budget_sql,
                        organization_id=organization_id,
                        attempt_id=attempt_id,
                        work_budget=work_budget,
                        now=now,
                    )
                    if budget_schema_ready
                    else None
                )
                self._reserve_budget_in_cursor(
                    cursor,
                    identity=identity,
                    scopes=scopes,
                    reserved_tokens=int(root["reserved_tokens"]),
                    reserved_cost_microusd=int(root["reserved_cost_microusd"]),
                    ttl_seconds=ttl_seconds,
                    reservation_id=attempt_id,
                    attempt_id=attempt_id,
                    now=now,
                )
                if budget_schema_ready:
                    enforce_and_bind_work_budget(
                        budget_sql,
                        prepared=prepared_budget,
                        organization_id=organization_id,
                        attempt_id=attempt_id,
                        provider_id=protocol,
                        model_id=upstream_model or resolved_alias or requested_model,
                        model_version=None,
                        reserved_cost_microusd=int(root["reserved_cost_microusd"]),
                        now=now,
                        work_budget=work_budget,
                    )
        return RequestAttempt(
            attempt_id=attempt_id,
            reservation_id=attempt_id,
            attribution_event_id=None if prepared_budget is None else prepared_budget.attribution_event_id,
        )

    def finalize_request_attempt(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        status: str,
        provider_reported_model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_microusd: int = 0,
        provider_request_id: str | None = None,
    ) -> None:
        """Finalize a pending attempt once and retain the linked usage evidence."""

        require_terminal_request_attempt_state(status)
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                root = self._request_attempt_root_in_cursor(cursor, attempt.attempt_id, organization, for_update=True)
                latest = self._latest_request_attempt_state_in_cursor(cursor, attempt.attempt_id)
                require_pending_request_attempt_state(latest.state)
                result = normalize_request_attempt_result(root, error_factory=PostgresStorageError)
                terminal_at = self._database_now_in_cursor(cursor)
                usage_event_id = self._record_in_cursor(
                    cursor,
                    occurred_at=terminal_at,
                    identity=result.identity,
                    client=result.client,
                    protocol=result.protocol,
                    requested_model=result.requested_model,
                    resolved_alias=result.resolved_alias,
                    upstream_model=result.upstream_model,
                    provider_reported_model=provider_reported_model,
                    policy_version=result.policy_version,
                    policy_action=result.policy_action,
                    status=status,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    reasoning_tokens=reasoning_tokens,
                    cost_microusd=cost_microusd,
                    provider_request_id=provider_request_id,
                    redaction_count=result.redaction_count,
                    redaction_rules=result.redaction_rules,
                )
                self._append_request_attempt_event_in_cursor(
                    cursor,
                    attempt_id=attempt.attempt_id,
                    organization_id=organization,
                    occurred_at=terminal_at,
                    sequence=latest.sequence + 1,
                    state=status,
                    reason_code=None,
                    usage_event_id=usage_event_id,
                )
                cursor.execute(
                    f"""
                    DELETE FROM {self._table('gateway_budget_reservations')}
                    WHERE id = %s AND attempt_id = %s AND organization_id = %s
                    """,
                    (attempt.reservation_id, attempt.attempt_id, organization),
                )
                if cursor.rowcount != 1:
                    raise PostgresStorageError("request_attempt_reservation_missing")

    def mark_request_attempt_outcome_unknown(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        reason_code: str,
    ) -> bool:
        """Record an ambiguous provider outcome without releasing its hold."""

        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                self._request_attempt_root_in_cursor(cursor, attempt.attempt_id, organization, for_update=True)
                latest = self._latest_request_attempt_state_in_cursor(cursor, attempt.attempt_id)
                if not should_mark_request_attempt_unknown(latest.state):
                    return False
                terminal_at = self._database_now_in_cursor(cursor)
                self._append_request_attempt_event_in_cursor(
                    cursor,
                    attempt_id=attempt.attempt_id,
                    organization_id=organization,
                    occurred_at=terminal_at,
                    sequence=latest.sequence + 1,
                    state="outcome_unknown",
                    reason_code=reason_code,
                    usage_event_id=None,
                )
        return True

    def sweep_stale_request_attempts(self, *, organization_id: str | None = None) -> int:
        """Convert only stale pending attempts to durable unknown outcomes."""

        organizations = (self._organization(organization_id),) if organization_id is not None else self.organization_ids
        # Preserve the v1 injectable clock for deciding which holds are stale.
        # Only the terminal event timestamp moves to PostgreSQL's clock domain.
        now = datetime.now(timezone.utc)
        count = 0
        for organization in organizations:
            with self._transaction(organization) as connection:
                with connection.cursor() as cursor:
                    count += self._sweep_stale_request_attempts_in_cursor(
                        cursor,
                        now=now,
                        occurred_at=self._database_now_in_cursor(cursor),
                        organization_id=organization,
                    )
        return count

    def _reserve_budget_in_cursor(
        self,
        cursor: object,
        *,
        identity: Identity,
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
        reservation_id: str,
        attempt_id: str | None,
        now: datetime,
    ) -> None:
        organization_id = self._organization(identity.organization_id)
        constrained = tuple(
            scope
            for scope in scopes
            if scope.token_limit is not None or scope.cost_limit_microusd is not None
        )
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cursor.execute(
            f"""
            DELETE FROM {self._table('gateway_budget_reservations')}
            WHERE organization_id = %s AND attempt_id IS NULL AND expires_at <= %s
            """,
            (organization_id, now),
        )
        for scope in constrained:
            usage_clauses = ["organization_id = %s", "occurred_at >= %s"]
            reservation_clauses = ["r.organization_id = %s", self._active_reservation_clause("r")]
            usage_parameters: list[object] = [organization_id, month_start]
            reservation_parameters: list[object] = [organization_id, now]
            if scope.actor_id is not None:
                usage_clauses.append("actor_id = %s")
                reservation_clauses.append("r.actor_id = %s")
                usage_parameters.append(scope.actor_id)
                reservation_parameters.append(scope.actor_id)
            if scope.team_id is not None:
                usage_clauses.append("team_id = %s")
                reservation_clauses.append("r.team_id = %s")
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
                    COALESCE(SUM(r.reserved_tokens), 0) AS tokens,
                    COALESCE(SUM(r.reserved_cost_microusd), 0) AS cost_microusd
                FROM {self._table('gateway_budget_reservations')} AS r
                WHERE {' AND '.join(reservation_clauses)}
                """,
                reservation_parameters,
            )
            reserved = cursor.fetchone()
            projected_tokens = int(usage["tokens"]) + int(reserved["tokens"]) + max(0, reserved_tokens)
            projected_cost = int(usage["cost_microusd"]) + int(reserved["cost_microusd"]) + max(
                0, reserved_cost_microusd
            )
            if scope.token_limit is not None and projected_tokens > scope.token_limit:
                raise ReservationDenied(f"The {scope.name} monthly token limit would be exceeded by this request.")
            if scope.cost_limit_microusd is not None and projected_cost > scope.cost_limit_microusd:
                raise ReservationDenied(f"The {scope.name} monthly AI budget would be exceeded by this request.")
        cursor.execute(
            f"""
            INSERT INTO {self._table('gateway_budget_reservations')} (
                id, created_at, expires_at, organization_id, actor_id, team_id,
                reserved_tokens, reserved_cost_microusd, attempt_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                attempt_id,
            ),
        )

    def _active_reservation_clause(self, alias: str) -> str:
        return f"""
        (
            ({alias}.attempt_id IS NULL AND {alias}.expires_at > %s)
            OR (
                {alias}.attempt_id IS NOT NULL
                AND (
                    SELECT event.state
                    FROM {self._table('gateway_request_attempt_events')} AS event
                    WHERE event.attempt_id = {alias}.attempt_id
                    ORDER BY event.sequence DESC
                    LIMIT 1
                ) IN ('pending', 'outcome_unknown')
            )
        )
        """

    def _sweep_stale_request_attempts_in_cursor(
        self,
        cursor: object,
        *,
        now: datetime,
        occurred_at: datetime,
        organization_id: str,
    ) -> int:
        cursor.execute(
            f"""
            SELECT r.attempt_id, r.organization_id
            FROM {self._table('gateway_budget_reservations')} AS r
            WHERE r.organization_id = %s
              AND r.attempt_id IS NOT NULL
              AND r.expires_at <= %s
              AND {self._active_pending_clause('r')}
            ORDER BY r.attempt_id
            """,
            (organization_id, now),
        )
        rows = cursor.fetchall()
        count = 0
        for row in rows:
            attempt_id = str(row["attempt_id"])
            self._request_attempt_root_in_cursor(cursor, attempt_id, organization_id, for_update=True)
            latest = self._latest_request_attempt_state_in_cursor(cursor, attempt_id)
            if latest.state != "pending":
                continue
            self._append_request_attempt_event_in_cursor(
                cursor,
                attempt_id=attempt_id,
                organization_id=organization_id,
                occurred_at=occurred_at,
                sequence=latest.sequence + 1,
                state="outcome_unknown",
                reason_code="stale_pending",
                usage_event_id=None,
            )
            count += 1
        return count

    def _active_pending_clause(self, alias: str) -> str:
        return f"""
        (
            SELECT event.state
            FROM {self._table('gateway_request_attempt_events')} AS event
            WHERE event.attempt_id = {alias}.attempt_id
            ORDER BY event.sequence DESC
            LIMIT 1
        ) = 'pending'
        """

    def _request_attempt_root_in_cursor(
        self,
        cursor: object,
        attempt_id: str,
        organization_id: str,
        *,
        for_update: bool,
    ) -> dict[str, object]:
        if for_update:
            # The runtime role deliberately has no UPDATE privilege over
            # immutable attempt roots. A transaction-scoped advisory lock
            # gives transition writers the same serialization without making
            # the append-only evidence table mutable.
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"hormuz:request-attempt:{organization_id}:{attempt_id}",),
            )
        cursor.execute(
            f"""
            SELECT *
            FROM {self._table('gateway_request_attempts')}
            WHERE attempt_id = %s AND organization_id = %s
            """,
            (attempt_id, organization_id),
        )
        root = cursor.fetchone()
        if root is None:
            raise RequestAttemptStateError("request_attempt_not_found")
        return dict(root)

    def _latest_request_attempt_state_in_cursor(
        self,
        cursor: object,
        attempt_id: str,
    ) -> RequestAttemptState:
        cursor.execute(
            f"""
            SELECT sequence, state
            FROM {self._table('gateway_request_attempt_events')}
            WHERE attempt_id = %s
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (attempt_id,),
        )
        event = cursor.fetchone()
        if event is None:
            raise PostgresStorageError("request_attempt_event_missing")
        return normalize_request_attempt_state(event)

    def _append_request_attempt_event_in_cursor(
        self,
        cursor: object,
        *,
        attempt_id: str,
        organization_id: str,
        occurred_at: datetime,
        sequence: int,
        state: str,
        reason_code: str | None,
        usage_event_id: str | None,
    ) -> str:
        event = build_request_attempt_event(
            attempt_id=attempt_id,
            organization_id=organization_id,
            occurred_at=occurred_at,
            sequence=sequence,
            state=state,
            reason_code=reason_code,
            usage_event_id=usage_event_id,
        )
        cursor.execute(
            f"""
            INSERT INTO {self._table('gateway_request_attempt_events')} (
                id, attempt_id, organization_id, occurred_at,
                event_schema_id, event_schema_version, sequence, state,
                reason_code, usage_event_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event["id"],
                event["attempt_id"],
                event["organization_id"],
                occurred_at,
                event["event_schema_id"],
                event["event_schema_version"],
                event["sequence"],
                event["state"],
                event["reason_code"],
                event["usage_event_id"],
            ),
        )
        return str(event["id"])

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
                    f"""
                    DELETE FROM {self._table('gateway_budget_reservations')}
                    WHERE organization_id = %s AND id = %s AND attempt_id IS NULL
                    """,
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
                    FROM {self._table('gateway_budget_reservations')} AS r
                    WHERE r.organization_id = %s AND {self._active_reservation_clause('r')}
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
        starts_at: datetime | None = None,
        ends_before: datetime | None = None,
    ) -> MonthlyTotals:
        organization = self._organization(organization_id)
        start, end = monthly_usage_bounds(starts_at=starts_at, ends_before=ends_before)
        clauses = ["organization_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [organization, start]
        if end is not None:
            clauses.append("occurred_at < %s")
            parameters.append(end)
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

    def audit_chain_head(self, *, organization_id: str) -> AuditChainHead:
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                head = self._audit_chain_head_in_cursor(cursor, organization_id=organization)
        if head is None:
            raise PostgresStorageError("audit_chain_head_unavailable")
        return head

    def _audit_chain_anchor_status_in_cursor(
        self,
        cursor: Any,
        *,
        organization_id: str,
        maximum_age_seconds: int | None,
        now: datetime,
    ) -> AuditChainAnchorStatus:
        head = self._audit_chain_head_in_cursor(
            cursor,
            organization_id=organization_id,
            create=False,
        )
        if head is None:
            return AuditChainAnchorStatus(
                organization_id=organization_id,
                chain_epoch=1,
                sequence=0,
                latest_checkpoint_at=None,
                oldest_unanchored_at=None,
                overdue=False,
            )
        cursor.execute(
            f"""
            SELECT sequence, anchored_at
            FROM {self._table('gateway_audit_chain_checkpoints')}
            WHERE organization_id = %s AND chain_epoch = %s
            ORDER BY sequence DESC, anchored_at DESC
            LIMIT 1
            """,
            (organization_id, head.chain_epoch),
        )
        checkpoint = cursor.fetchone()
        checkpoint_sequence = 0
        checkpoint_at: datetime | None = None
        if checkpoint is not None:
            checkpoint_sequence = int(checkpoint["sequence"])
            if checkpoint_sequence > head.sequence:
                raise PostgresStorageError("audit_chain_checkpoint_mismatch")
            checkpoint_at = stored_utc_timestamp(
                checkpoint["anchored_at"],
                code="audit_chain_checkpoint_malformed",
                error_factory=PostgresStorageError,
                accept_datetime=True,
            )
        oldest_unanchored_at: datetime | None = None
        if head.sequence > checkpoint_sequence:
            cursor.execute(
                f"""
                SELECT appended_at
                FROM {self._table('gateway_audit_chain_entries')}
                WHERE organization_id = %s AND chain_epoch = %s AND sequence > %s
                ORDER BY sequence ASC
                LIMIT 1
                """,
                (organization_id, head.chain_epoch, checkpoint_sequence),
            )
            row = cursor.fetchone()
            if row is None:
                raise PostgresStorageError("audit_chain_head_mismatch")
            oldest_unanchored_at = stored_utc_timestamp(
                row["appended_at"],
                code="audit_chain_entry_malformed",
                error_factory=PostgresStorageError,
                accept_datetime=True,
            )
        overdue = bool(
            maximum_age_seconds is not None
            and oldest_unanchored_at is not None
            and (now - oldest_unanchored_at).total_seconds() > maximum_age_seconds
        )
        return AuditChainAnchorStatus(
            organization_id=organization_id,
            chain_epoch=head.chain_epoch,
            sequence=head.sequence,
            latest_checkpoint_at=checkpoint_at,
            oldest_unanchored_at=oldest_unanchored_at,
            overdue=overdue,
        )

    def audit_chain_anchor_status(
        self,
        *,
        organization_id: str,
        maximum_age_seconds: int | None = None,
        now: datetime | None = None,
    ) -> AuditChainAnchorStatus:
        validate_anchor_age(maximum_age_seconds, error_factory=PostgresStorageError)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise PostgresStorageError("audit_chain_anchor_age_invalid")
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                return self._audit_chain_anchor_status_in_cursor(
                    cursor,
                    organization_id=organization,
                    maximum_age_seconds=maximum_age_seconds,
                    now=current.astimezone(timezone.utc),
                )

    def record_audit_chain_checkpoint(
        self,
        *,
        checkpoint: Mapping[str, object],
        artifact_sha256: str,
        anchor_backend: str,
        object_version: str | None,
        anchored_at: datetime | None = None,
    ) -> None:
        try:
            checkpoint_id, organization_id, epoch, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
        except AuditChainError as error:
            raise PostgresStorageError(error.code) from None
        chain_version = checkpoint.get("chain_version")
        if (
            not isinstance(chain_version, int)
            or not is_sha256_digest(artifact_sha256)
            or not isinstance(anchor_backend, str)
            or not anchor_backend
            or (object_version is not None and not isinstance(object_version, str))
        ):
            raise PostgresStorageError("audit_chain_checkpoint_malformed")
        timestamp = anchored_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise PostgresStorageError("audit_chain_checkpoint_malformed")
        organization = self._organization(organization_id)
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT event_digest
                    FROM {self._table('gateway_audit_chain_entries')}
                    WHERE organization_id = %s AND chain_epoch = %s AND sequence = %s
                    """,
                    (organization, epoch, sequence),
                )
                entry = cursor.fetchone()
                if entry is None or not isinstance(entry["event_digest"], str):
                    raise PostgresStorageError("audit_chain_checkpoint_missing")
                if not hmac.compare_digest(str(entry["event_digest"]), head_digest):
                    raise PostgresStorageError("audit_chain_checkpoint_mismatch")
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('gateway_audit_chain_checkpoints')} (
                        checkpoint_id, organization_id, chain_version, chain_epoch, sequence,
                        head_digest, artifact_sha256, anchor_backend, object_version, anchored_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (checkpoint_id) DO NOTHING
                    """,
                    (
                        checkpoint_id,
                        organization,
                        chain_version,
                        epoch,
                        sequence,
                        head_digest,
                        artifact_sha256,
                        anchor_backend,
                        object_version,
                        timestamp.astimezone(timezone.utc),
                    ),
                )
                cursor.execute(
                    f"""
                    SELECT organization_id, chain_version, chain_epoch, sequence, head_digest,
                           artifact_sha256, anchor_backend, object_version
                    FROM {self._table('gateway_audit_chain_checkpoints')}
                    WHERE checkpoint_id = %s
                    """,
                    (checkpoint_id,),
                )
                existing = cursor.fetchone()
                if existing is None or (
                    existing["organization_id"] != organization
                    or int(existing["chain_version"]) != chain_version
                    or int(existing["chain_epoch"]) != epoch
                    or int(existing["sequence"]) != sequence
                    or not hmac.compare_digest(str(existing["head_digest"]), head_digest)
                    or not hmac.compare_digest(str(existing["artifact_sha256"]), artifact_sha256)
                    or existing["anchor_backend"] != anchor_backend
                    or existing["object_version"] != object_version
                ):
                    raise PostgresStorageError("audit_chain_checkpoint_conflict")

    def begin_audit_chain_epoch(
        self,
        *,
        checkpoint: Mapping[str, object],
        reason_code: str,
    ) -> AuditChainHead:
        if reason_code not in {"restore", "migration"}:
            raise PostgresStorageError("audit_chain_epoch_reason_invalid")
        try:
            _, organization_id, predecessor_epoch, predecessor_sequence, predecessor_digest = audit_chain_checkpoint_summary(
                checkpoint
            )
        except AuditChainError as error:
            raise PostgresStorageError(error.code) from None
        chain_version = checkpoint.get("chain_version")
        if not isinstance(chain_version, int):
            raise PostgresStorageError("audit_chain_checkpoint_malformed")
        organization = self._organization(organization_id)
        new_epoch = predecessor_epoch + 1
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                head = self._audit_chain_head_in_cursor(
                    cursor,
                    organization_id=organization,
                    for_update=True,
                )
                if head is None:
                    raise PostgresStorageError("audit_chain_head_unavailable")
                if chain_version != head.chain_version or new_epoch <= head.chain_epoch:
                    raise PostgresStorageError("audit_chain_epoch_predecessor_invalid")
                cursor.execute(
                    f"""
                    INSERT INTO {self._table('gateway_audit_chain_epochs')} (
                        organization_id, chain_version, chain_epoch, created_at, reason_code,
                        predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
                    ) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s, %s, %s, %s)
                    ON CONFLICT (organization_id, chain_epoch) DO NOTHING
                    """,
                    (
                        organization,
                        chain_version,
                        new_epoch,
                        reason_code,
                        predecessor_epoch,
                        predecessor_sequence,
                        predecessor_digest,
                    ),
                )
                cursor.execute(
                    f"""
                    SELECT chain_version, predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
                    FROM {self._table('gateway_audit_chain_epochs')}
                    WHERE organization_id = %s AND chain_epoch = %s
                    """,
                    (organization, new_epoch),
                )
                epoch = cursor.fetchone()
                if epoch is None or (
                    int(epoch["chain_version"]) != chain_version
                    or int(epoch["predecessor_chain_epoch"]) != predecessor_epoch
                    or int(epoch["predecessor_sequence"]) != predecessor_sequence
                    or not hmac.compare_digest(str(epoch["predecessor_head_digest"]), predecessor_digest)
                ):
                    raise PostgresStorageError("audit_chain_epoch_conflict")
                cursor.execute(
                    f"""
                    UPDATE {self._table('gateway_audit_chain_heads')}
                    SET chain_epoch = %s, sequence = 0, head_digest = %s
                    WHERE organization_id = %s AND chain_version = %s AND chain_epoch = %s
                      AND sequence = %s AND head_digest IS NOT DISTINCT FROM %s
                    """,
                    (
                        new_epoch,
                        predecessor_digest,
                        organization,
                        head.chain_version,
                        head.chain_epoch,
                        head.sequence,
                        head.head_digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PostgresStorageError("audit_chain_head_conflict")
        return AuditChainHead(
            organization_id=organization,
            chain_version=chain_version,
            chain_epoch=new_epoch,
            sequence=0,
            head_digest=predecessor_digest,
        )

    def _audit_chain_source_events_in_cursor(
        self,
        cursor: Any,
        *,
        organization_id: str,
    ) -> tuple[AuditChainSourceEventInput, ...]:
        sources: list[AuditChainSourceEventInput] = []
        source_identities: set[AuditChainSource] = set()
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
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        usage_rows = cursor.fetchall()
        cursor.execute(
            f"""
            SELECT
                id, occurred_at, evidence_schema_id, evidence_schema_version,
                organization_id, actor_id, actor_name, team_id, team_name,
                identity_type, authentication_source, client, protocol, requested_model,
                policy_version, coverage, action, detection_count, rules
            FROM {self._table('gateway_secret_events')}
            WHERE organization_id = %s
            """,
            (organization_id,),
        )
        secret_rows = cursor.fetchall()
        for row in usage_rows:
            try:
                event = usage_audit_event(dict(row))
            except EvidenceStorageError as error:
                raise PostgresStorageError(error.code) from None
            source = normalize_audit_chain_source_event_input(
                event,
                error_factory=PostgresStorageError,
            )
            if source.source in source_identities:
                raise PostgresStorageError("audit_chain_source_event_malformed")
            source_identities.add(source.source)
            sources.append(source)
        for row in secret_rows:
            try:
                event = security_audit_event(dict(row))
            except EvidenceStorageError as error:
                raise PostgresStorageError(error.code) from None
            source = normalize_audit_chain_source_event_input(
                event,
                error_factory=PostgresStorageError,
            )
            if source.source in source_identities:
                raise PostgresStorageError("audit_chain_source_event_malformed")
            source_identities.add(source.source)
            sources.append(source)
        return tuple(sources)

    @staticmethod
    def _custody_audit_chain_source_events_in_cursor(
        cursor: Any,
        *,
        organization_id: str,
        entries: list[Mapping[str, object]],
    ) -> tuple[AuditChainSourceEventInput, ...]:
        """Load v2 custody sources through the bounded verifier function.

        The ordinary gateway runtime intentionally has no direct read privilege
        on custody control or execution evidence.  PostgreSQL exposes only a
        source row already tied to the selected v2 chain entry, keeping audit
        verification content-free and tenant scoped.
        """

        sources: list[AuditChainSourceEventInput] = []
        source_identities: set[AuditChainSource] = set()
        for entry in entries:
            if entry.get("entry_schema_version") != 2:
                continue
            source_schema_id = entry.get("source_schema_id")
            source_schema_version = entry.get("source_schema_version")
            source_event_id = entry.get("source_event_id")
            if (
                not isinstance(source_schema_id, str)
                or isinstance(source_schema_version, bool)
                or not isinstance(source_schema_version, int)
                or not isinstance(source_event_id, str)
            ):
                raise PostgresStorageError("audit_chain_entry_malformed")
            source = AuditChainSource(
                schema_id=source_schema_id,
                schema_version=source_schema_version,
                event_id=source_event_id,
            )
            if source in source_identities:
                raise PostgresStorageError("audit_chain_entry_malformed")
            cursor.execute(
                """
                SELECT custody_audit_chain_source_event_json(%s, %s, %s, %s) AS event_json
                """,
                (organization_id, source_schema_id, source_schema_version, source_event_id),
            )
            row = cursor.fetchone()
            event_json = row.get("event_json") if row is not None else None
            if not isinstance(event_json, str):
                raise PostgresStorageError("audit_chain_source_event_missing")
            try:
                parsed = json.loads(event_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise PostgresStorageError("audit_chain_source_event_malformed") from None
            if not isinstance(parsed, dict):
                raise PostgresStorageError("audit_chain_source_event_malformed")
            sources.append(
                normalize_audit_chain_source_event_input(
                    parsed,
                    source=source,
                    error_factory=PostgresStorageError,
                )
            )
            source_identities.add(source)
        return tuple(sources)

    def verify_audit_chain(
        self,
        *,
        organization_id: str,
        checkpoint: Mapping[str, object] | None = None,
    ) -> AuditChainHead:
        organization = self._organization(organization_id)
        checkpoint_input = normalize_audit_chain_checkpoint_input(
            checkpoint,
            organization_id=organization,
            error_factory=PostgresStorageError,
        )
        with self._transaction(organization) as connection:
            with connection.cursor() as cursor:
                # Hold a shared lock on the tenant head for this full
                # verification snapshot. Appenders acquire FOR UPDATE on the
                # same row, so the verifier cannot mix an old entry list with
                # a newly advanced head under PostgreSQL's READ COMMITTED
                # default.
                head = self._audit_chain_head_in_cursor(
                    cursor,
                    organization_id=organization,
                    create=False,
                    for_share=True,
                )
                if head is None:
                    if checkpoint_input is not None:
                        raise PostgresStorageError("audit_chain_checkpoint_mismatch")
                    return AuditChainHead(organization, 1, 1, 0, None)
                cursor.execute(
                    f"""
                    SELECT chain_version, chain_epoch, reason_code, predecessor_chain_epoch,
                           predecessor_sequence, predecessor_head_digest
                    FROM {self._table('gateway_audit_chain_epochs')}
                    WHERE organization_id = %s
                    ORDER BY chain_epoch ASC
                    """,
                    (organization,),
                )
                epoch_rows = cursor.fetchall()
                cursor.execute(
                    f"""
                    SELECT chain_version, chain_epoch, sequence, entry_schema_id,
                           entry_schema_version, event_id, previous_digest, event_digest, event_json,
                           source_schema_id, source_schema_version, source_event_id
                    FROM {self._table('gateway_audit_chain_entries')}
                    WHERE organization_id = %s
                    ORDER BY chain_epoch ASC, sequence ASC
                    """,
                    (organization,),
                )
                entry_rows = cursor.fetchall()
                source_events = self._audit_chain_source_events_in_cursor(
                    cursor,
                    organization_id=organization,
                )
                custody_source_events = self._custody_audit_chain_source_events_in_cursor(
                    cursor,
                    organization_id=organization,
                    entries=entry_rows,
                )
        inputs = AuditChainVerificationInputs(
            organization_id=organization,
            head=head,
            epochs=tuple(
                normalize_audit_chain_epoch_input(row, error_factory=PostgresStorageError)
                for row in epoch_rows
            ),
            entries=tuple(
                normalize_audit_chain_entry_input(
                    row,
                    organization_id=organization,
                    error_factory=PostgresStorageError,
                )
                for row in entry_rows
            ),
            source_events=source_events + custody_source_events,
            checkpoint=checkpoint_input,
        )
        try:
            return verify_audit_chain_inputs(inputs)
        except AuditChainError as error:
            raise PostgresStorageError(error.code) from None


def _month_start() -> datetime:
    return datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
