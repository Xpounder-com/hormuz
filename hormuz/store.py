from __future__ import annotations

import hmac
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .audit_chain import (
    AuditChainAnchorStatus,
    AuditChainError,
    AuditChainHead,
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
from ._persistence import (
    MonthlyTotals,
    RequestAttempt,
    RequestAttemptState,
    RequestAttemptStateError,
    ReservationDenied,
    ReservationScope,
    WorkBudgetContext,
    SecretTotals,
    UsageRepository,
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
from ._sqlite_schema import (
    SQLITE_SCHEMA_VERSION,
    apply_sqlite_migration,
    initialize_sqlite_schema,
    verify_sqlite_schema_ready,
)


class StorageSchemaError(RuntimeError):
    """A stable failure for an unsafe or incomplete durable-store transition."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UsageStore:
    """SQLite implementation of the metadata-only usage repository."""

    schema_version = SQLITE_SCHEMA_VERSION

    def __init__(
        self,
        path: Path,
        *,
        maximum_supported_schema_version: int | None = None,
        audit_chain_maximum_anchor_age_seconds: int | None = None,
        audit_chain_organization_ids: tuple[str, ...] = (),
        read_only: bool = False,
    ):
        self.path = path
        self.read_only = read_only
        self.maximum_supported_schema_version = (
            self.schema_version
            if maximum_supported_schema_version is None
            else maximum_supported_schema_version
        )
        if (
            audit_chain_maximum_anchor_age_seconds is not None
            and (
                isinstance(audit_chain_maximum_anchor_age_seconds, bool)
                or not isinstance(audit_chain_maximum_anchor_age_seconds, int)
                or audit_chain_maximum_anchor_age_seconds < 1
            )
        ):
            raise StorageSchemaError("audit_chain_configuration_invalid")
        self.audit_chain_maximum_anchor_age_seconds = audit_chain_maximum_anchor_age_seconds
        self.audit_chain_organization_ids = tuple(sorted(set(audit_chain_organization_ids)))
        if any(not isinstance(organization_id, str) or not organization_id for organization_id in self.audit_chain_organization_ids):
            raise StorageSchemaError("audit_chain_configuration_invalid")
        self._lock = threading.RLock()
        if self.read_only:
            with self._lock, self._connection() as connection:
                verify_sqlite_schema_ready(
                    connection,
                    schema_version=self.schema_version,
                    maximum_supported_schema_version=self.maximum_supported_schema_version,
                    error_factory=StorageSchemaError,
                )
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            connection = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
        else:
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
            initialize_sqlite_schema(
                connection,
                schema_version=self.schema_version,
                maximum_supported_schema_version=self.maximum_supported_schema_version,
                apply_migration=self._apply_migration,
                error_factory=StorageSchemaError,
            )

    def verify_ready(self) -> None:
        """Perform a read-only local-store readiness check."""

        with self._lock, self._connection() as connection:
            verify_sqlite_schema_ready(
                connection,
                schema_version=self.schema_version,
                maximum_supported_schema_version=self.maximum_supported_schema_version,
                error_factory=StorageSchemaError,
            )
            if self.audit_chain_maximum_anchor_age_seconds is not None:
                now = datetime.now(timezone.utc)
                for organization_id in self.audit_chain_organization_ids:
                    status = self._audit_chain_anchor_status_in_connection(
                        connection,
                        organization_id=organization_id,
                        maximum_age_seconds=self.audit_chain_maximum_anchor_age_seconds,
                        now=now,
                    )
                    if status.overdue:
                        raise StorageSchemaError("audit_chain_anchor_overdue")

    @staticmethod
    def _apply_migration(connection: sqlite3.Connection, version: int) -> None:
        """Compatibility shim for callers that instrument migration progress."""

        apply_sqlite_migration(
            connection,
            version,
            error_factory=StorageSchemaError,
        )

    def _audit_chain_head_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        create: bool = True,
    ) -> AuditChainHead | None:
        """Return the active chain head, lazily creating the initial epoch."""

        if create:
            now = datetime.now(timezone.utc).isoformat()
            connection.execute(
                """
                INSERT OR IGNORE INTO gateway_audit_chain_epochs (
                    organization_id, chain_version, chain_epoch, created_at, reason_code,
                    predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
                ) VALUES (?, 1, 1, ?, 'initial_adoption', NULL, NULL, NULL)
                """,
                (organization_id, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO gateway_audit_chain_heads (
                    organization_id, chain_version, chain_epoch, sequence, head_digest
                ) VALUES (?, 1, 1, 0, NULL)
                """,
                (organization_id,),
            )
        row = connection.execute(
            """
            SELECT organization_id, chain_version, chain_epoch, sequence, head_digest
            FROM gateway_audit_chain_heads
            WHERE organization_id = ?
            """,
            (organization_id,),
        ).fetchone()
        if row is None and not create:
            return None
        if row is None:
            raise StorageSchemaError("audit_chain_head_unavailable")
        return normalize_audit_chain_head(row, error_factory=StorageSchemaError)

    def _append_audit_chain_entry_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        event: Mapping[str, object],
    ) -> AuditChainHead:
        """Atomically append a canonical event and advance its tenant head."""

        organization_id = event.get("organization_id")
        event_id = event.get("id")
        if not isinstance(organization_id, str) or not organization_id or not isinstance(event_id, str) or not event_id:
            raise StorageSchemaError("audit_chain_event_malformed")
        head = self._audit_chain_head_in_connection(connection, organization_id=organization_id)
        if head is None:
            raise StorageSchemaError("audit_chain_head_unavailable")
        try:
            entry = build_audit_chain_entry(
                event,
                chain_version=head.chain_version,
                chain_epoch=head.chain_epoch,
                sequence=head.sequence + 1,
                previous_digest=head.head_digest,
            )
        except AuditChainError as error:
            raise StorageSchemaError(error.code) from None
        event_value = entry["event"]
        if not isinstance(event_value, Mapping):  # Defensive after strict construction above.
            raise StorageSchemaError("audit_chain_entry_malformed")
        cursor = connection.execute(
            """
            INSERT INTO gateway_audit_chain_entries (
                organization_id, chain_version, chain_epoch, sequence,
                entry_schema_id, entry_schema_version, event_id, previous_digest,
                event_digest, event_json, appended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            raise StorageSchemaError("audit_chain_entry_unavailable")
        cursor = connection.execute(
            """
            UPDATE gateway_audit_chain_heads
            SET sequence = ?, head_digest = ?
            WHERE organization_id = ?
              AND chain_version = ?
              AND chain_epoch = ?
              AND sequence = ?
              AND head_digest IS ?
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
            raise StorageSchemaError("audit_chain_head_conflict")
        return AuditChainHead(
            organization_id=organization_id,
            chain_version=head.chain_version,
            chain_epoch=head.chain_epoch,
            sequence=int(entry["sequence"]),
            head_digest=str(entry["event_digest"]),
        )

    @staticmethod
    def _usage_audit_event_in_connection(connection: sqlite3.Connection, event_id: str) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT
                id, occurred_at, evidence_schema_id, evidence_schema_version,
                organization_id, actor_id, actor_name, team_id, team_name,
                identity_type, authentication_source, client, protocol, requested_model,
                resolved_alias, upstream_model, provider_reported_model, policy_version,
                policy_action, status, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, reasoning_tokens,
                cost_microusd, cost_basis, allocation_basis, coverage,
                provider_request_id, redaction_count, redaction_rules
            FROM gateway_usage_events
            WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise StorageSchemaError("audit_chain_source_event_missing")
        try:
            return usage_audit_event(dict(row))
        except EvidenceStorageError as error:
            raise StorageSchemaError(error.code) from None

    @staticmethod
    def _secret_audit_event_in_connection(connection: sqlite3.Connection, event_id: str) -> dict[str, object]:
        row = connection.execute(
            """
            SELECT
                id, occurred_at, evidence_schema_id, evidence_schema_version,
                organization_id, actor_id, actor_name, team_id, team_name,
                identity_type, authentication_source, client, protocol, requested_model,
                policy_version, coverage, action, detection_count, rules
            FROM gateway_secret_events
            WHERE id = ?
            """,
            (event_id,),
        ).fetchone()
        if row is None:
            raise StorageSchemaError("audit_chain_source_event_missing")
        try:
            return security_audit_event(dict(row))
        except EvidenceStorageError as error:
            raise StorageSchemaError(error.code) from None

    def _record_in_connection(
        self,
        connection: sqlite3.Connection,
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
        """Insert one usage event inside an existing SQLite transaction."""

        validate_policy_action(policy_action)
        validate_request_status(status)
        event_id = str(uuid.uuid4())
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
        self._append_audit_chain_entry_in_connection(
            connection,
            event=self._usage_audit_event_in_connection(connection, event_id),
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
        with self._lock, self._connection() as connection:
            # Serialize head advancement across separate local gateway
            # processes.  The event insert, immutable entry, and head update
            # remain one rollback unit.
            connection.execute("BEGIN IMMEDIATE")
            return self._record_in_connection(
                connection,
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
        event_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            self._append_audit_chain_entry_in_connection(
                connection,
                event=self._secret_audit_event_in_connection(connection, event_id),
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
        reservation_id = str(uuid.uuid4())
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._sweep_stale_request_attempts_in_connection(connection, now=now, organization_id=identity.organization_id)
            self._reserve_budget_in_connection(
                connection,
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
        try:
            with self._lock, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                record_work_budget_denial(
                    RuntimeBudgetSQL(connection, postgres=False),
                    organization_id=identity.organization_id,
                    actor_id=identity.actor_id,
                    denial=denial,
                )
        except ReservationDenied:
            raise StorageSchemaError("storage_unavailable") from None

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
        """Durably record a pending attempt and its budget hold before egress.

        The root, its initial immutable event, and the reservation commit in
        one SQLite transaction. A failed reservation check rolls all three
        writes back, so no provider call can observe a partial attempt.
        """

        now = datetime.now(timezone.utc)
        attempt_id = str(uuid.uuid4())
        root = build_request_attempt_root(
            attempt_id=attempt_id,
            created_at=now,
            identity=identity,
            organization_id=identity.organization_id,
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
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._sweep_stale_request_attempts_in_connection(connection, now=now, organization_id=identity.organization_id)
            connection.execute(
                """
                INSERT INTO gateway_request_attempts (
                    attempt_id, created_at, evidence_schema_id, evidence_schema_version,
                    organization_id, actor_id, actor_name, team_id, team_name,
                    identity_type, authentication_source, client, protocol, requested_model,
                    resolved_alias, upstream_model, policy_version, policy_action,
                    redaction_count, redaction_rules, reserved_tokens, reserved_cost_microusd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    root["attempt_id"],
                    root["created_at"],
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
            self._append_request_attempt_event_in_connection(
                connection,
                attempt_id=attempt_id,
                organization_id=identity.organization_id,
                occurred_at=now,
                sequence=1,
                state="pending",
                reason_code=None,
                usage_event_id=None,
            )
            budget_schema_ready = connection.execute(
                "SELECT 1 FROM hormuz_schema_migrations "
                "WHERE version=9 AND state='applied'"
            ).fetchone() is not None
            if work_budget is not None and not budget_schema_ready:
                raise StorageSchemaError("storage_schema_partial_upgrade")
            budget_sql = RuntimeBudgetSQL(connection, postgres=False)
            prepared_budget = (
                prepare_work_budget(
                    budget_sql,
                    organization_id=identity.organization_id,
                    attempt_id=attempt_id,
                    work_budget=work_budget,
                    now=now,
                )
                if budget_schema_ready
                else None
            )
            self._reserve_budget_in_connection(
                connection,
                identity=identity,
                scopes=scopes,
                reserved_tokens=root["reserved_tokens"],
                reserved_cost_microusd=root["reserved_cost_microusd"],
                ttl_seconds=ttl_seconds,
                reservation_id=attempt_id,
                attempt_id=attempt_id,
                now=now,
            )
            if budget_schema_ready:
                enforce_and_bind_work_budget(
                    budget_sql,
                    prepared=prepared_budget,
                    organization_id=identity.organization_id,
                    attempt_id=attempt_id,
                    provider_id=protocol,
                    model_id=upstream_model or resolved_alias or requested_model,
                    model_version=None,
                    reserved_cost_microusd=root["reserved_cost_microusd"],
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
        """Finalize a pending attempt once and atomically materialize usage."""

        require_terminal_request_attempt_state(status)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            root = self._request_attempt_root_in_connection(connection, attempt.attempt_id, organization_id)
            latest = self._latest_request_attempt_state_in_connection(connection, attempt.attempt_id)
            require_pending_request_attempt_state(latest.state)
            result = normalize_request_attempt_result(root, error_factory=StorageSchemaError)
            usage_event_id = self._record_in_connection(
                connection,
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
            self._append_request_attempt_event_in_connection(
                connection,
                attempt_id=attempt.attempt_id,
                organization_id=organization_id,
                occurred_at=datetime.now(timezone.utc),
                sequence=latest.sequence + 1,
                state=status,
                reason_code=None,
                usage_event_id=usage_event_id,
            )
            deleted = connection.execute(
                """
                DELETE FROM gateway_budget_reservations
                WHERE id = ? AND attempt_id = ? AND organization_id = ?
                """,
                (attempt.reservation_id, attempt.attempt_id, organization_id),
            )
            if deleted.rowcount != 1:
                raise StorageSchemaError("request_attempt_reservation_missing")

    def mark_request_attempt_outcome_unknown(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        reason_code: str,
    ) -> bool:
        """Append a conservative unknown-outcome event without releasing cost."""

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._request_attempt_root_in_connection(connection, attempt.attempt_id, organization_id)
            latest = self._latest_request_attempt_state_in_connection(connection, attempt.attempt_id)
            if not should_mark_request_attempt_unknown(latest.state):
                return False
            self._append_request_attempt_event_in_connection(
                connection,
                attempt_id=attempt.attempt_id,
                organization_id=organization_id,
                occurred_at=datetime.now(timezone.utc),
                sequence=latest.sequence + 1,
                state="outcome_unknown",
                reason_code=reason_code,
                usage_event_id=None,
            )
        return True

    def sweep_stale_request_attempts(self, *, organization_id: str | None = None) -> int:
        """Mark expired pending attempts unknown while retaining their holds."""

        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._sweep_stale_request_attempts_in_connection(
                connection,
                now=datetime.now(timezone.utc),
                organization_id=organization_id,
            )

    def _reserve_budget_in_connection(
        self,
        connection: sqlite3.Connection,
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
        """Check budget scopes and insert one hold in the current transaction."""

        constrained = tuple(
            scope
            for scope in scopes
            if scope.token_limit is not None or scope.cost_limit_microusd is not None
        )
        now_value = now.isoformat()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        organization_id = identity.organization_id
        connection.execute(
            "DELETE FROM gateway_budget_reservations WHERE attempt_id IS NULL AND expires_at <= ?",
            (now_value,),
        )
        for scope in constrained:
            usage_clauses = ["organization_id = ?", "occurred_at >= ?"]
            reservation_clauses = ["r.organization_id = ?", self._active_reservation_clause("r")]
            usage_parameters: list[object] = [organization_id, month_start]
            reservation_parameters: list[object] = [organization_id, now_value]
            if scope.actor_id is not None:
                usage_clauses.append("actor_id = ?")
                reservation_clauses.append("r.actor_id = ?")
                usage_parameters.append(scope.actor_id)
                reservation_parameters.append(scope.actor_id)
            if scope.team_id is not None:
                usage_clauses.append("team_id = ?")
                reservation_clauses.append("r.team_id = ?")
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
                    COALESCE(SUM(r.reserved_tokens), 0) AS tokens,
                    COALESCE(SUM(r.reserved_cost_microusd), 0) AS cost_microusd
                FROM gateway_budget_reservations AS r
                WHERE {' AND '.join(reservation_clauses)}
                """,
                reservation_parameters,
            ).fetchone()
            projected_tokens = int(usage["tokens"]) + int(reserved["tokens"]) + max(0, reserved_tokens)
            projected_cost = int(usage["cost_microusd"]) + int(reserved["cost_microusd"]) + max(
                0, reserved_cost_microusd
            )
            if scope.token_limit is not None and projected_tokens > scope.token_limit:
                raise ReservationDenied(f"The {scope.name} monthly token limit would be exceeded by this request.")
            if scope.cost_limit_microusd is not None and projected_cost > scope.cost_limit_microusd:
                raise ReservationDenied(f"The {scope.name} monthly AI budget would be exceeded by this request.")
        connection.execute(
            """
            INSERT INTO gateway_budget_reservations (
                id, created_at, expires_at, organization_id, actor_id, team_id,
                reserved_tokens, reserved_cost_microusd, attempt_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                attempt_id,
            ),
        )

    @staticmethod
    def _active_reservation_clause(alias: str) -> str:
        return f"""
        (
            ({alias}.attempt_id IS NULL AND {alias}.expires_at > ?)
            OR (
                {alias}.attempt_id IS NOT NULL
                AND (
                    SELECT event.state
                    FROM gateway_request_attempt_events AS event
                    WHERE event.attempt_id = {alias}.attempt_id
                    ORDER BY event.sequence DESC
                    LIMIT 1
                ) IN ('pending', 'outcome_unknown')
            )
        )
        """

    def _sweep_stale_request_attempts_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        now: datetime,
        organization_id: str | None,
    ) -> int:
        clauses = ["r.attempt_id IS NOT NULL", "r.expires_at <= ?", self._active_pending_clause("r")]
        parameters: list[object] = [now.isoformat()]
        if organization_id is not None:
            clauses.append("r.organization_id = ?")
            parameters.append(organization_id)
        rows = connection.execute(
            f"""
            SELECT r.attempt_id, r.organization_id
            FROM gateway_budget_reservations AS r
            WHERE {' AND '.join(clauses)}
            ORDER BY r.attempt_id
            """,
            parameters,
        ).fetchall()
        for row in rows:
            latest = self._latest_request_attempt_state_in_connection(connection, str(row["attempt_id"]))
            if latest.state != "pending":
                continue
            self._append_request_attempt_event_in_connection(
                connection,
                attempt_id=str(row["attempt_id"]),
                organization_id=str(row["organization_id"]),
                occurred_at=now,
                sequence=latest.sequence + 1,
                state="outcome_unknown",
                reason_code="stale_pending",
                usage_event_id=None,
            )
        return len(rows)

    @staticmethod
    def _active_pending_clause(alias: str) -> str:
        return f"""
        (
            SELECT event.state
            FROM gateway_request_attempt_events AS event
            WHERE event.attempt_id = {alias}.attempt_id
            ORDER BY event.sequence DESC
            LIMIT 1
        ) = 'pending'
        """

    def _request_attempt_root_in_connection(
        self,
        connection: sqlite3.Connection,
        attempt_id: str,
        organization_id: str,
    ) -> sqlite3.Row:
        root = connection.execute(
            """
            SELECT *
            FROM gateway_request_attempts
            WHERE attempt_id = ? AND organization_id = ?
            """,
            (attempt_id, organization_id),
        ).fetchone()
        if root is None:
            raise RequestAttemptStateError("request_attempt_not_found")
        return root

    @staticmethod
    def _latest_request_attempt_state_in_connection(
        connection: sqlite3.Connection,
        attempt_id: str,
    ) -> RequestAttemptState:
        event = connection.execute(
            """
            SELECT sequence, state
            FROM gateway_request_attempt_events
            WHERE attempt_id = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (attempt_id,),
        ).fetchone()
        if event is None:
            raise StorageSchemaError("request_attempt_event_missing")
        return normalize_request_attempt_state(event)

    @staticmethod
    def _append_request_attempt_event_in_connection(
        connection: sqlite3.Connection,
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
        connection.execute(
            """
            INSERT INTO gateway_request_attempt_events (
                id, attempt_id, organization_id, occurred_at,
                event_schema_id, event_schema_version, sequence, state,
                reason_code, usage_event_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                event["attempt_id"],
                event["organization_id"],
                event["occurred_at"],
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
        with self._lock, self._connection() as connection:
            clauses = ["id = ?", "attempt_id IS NULL"]
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
            clauses = [self._active_reservation_clause("r")]
            parameters: list[object] = [now]
            if organization_id is not None:
                clauses.append("r.organization_id = ?")
                parameters.append(organization_id)
            row = connection.execute(
                f"SELECT COUNT(*) AS count FROM gateway_budget_reservations AS r WHERE {' AND '.join(clauses)}",
                parameters,
            ).fetchone()
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
        start, end = monthly_usage_bounds(starts_at=starts_at, ends_before=ends_before)
        clauses = ["occurred_at >= ?"]
        parameters: list[object] = [start.isoformat()]
        if end is not None:
            clauses.append("occurred_at < ?")
            parameters.append(end.isoformat())
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

    def audit_chain_head(self, *, organization_id: str) -> AuditChainHead:
        """Return the active chain position, creating an initial empty epoch if needed."""

        with self._lock, self._connection() as connection:
            head = self._audit_chain_head_in_connection(connection, organization_id=organization_id)
        if head is None:
            raise StorageSchemaError("audit_chain_head_unavailable")
        return head

    def _audit_chain_anchor_status_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        maximum_age_seconds: int | None,
        now: datetime,
    ) -> AuditChainAnchorStatus:
        head = self._audit_chain_head_in_connection(
            connection,
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
        checkpoint = connection.execute(
            """
            SELECT sequence, anchored_at
            FROM gateway_audit_chain_checkpoints
            WHERE organization_id = ? AND chain_epoch = ?
            ORDER BY sequence DESC, anchored_at DESC
            LIMIT 1
            """,
            (organization_id, head.chain_epoch),
        ).fetchone()
        checkpoint_sequence = 0
        checkpoint_at: datetime | None = None
        if checkpoint is not None:
            checkpoint_sequence = int(checkpoint["sequence"])
            if checkpoint_sequence > head.sequence:
                raise StorageSchemaError("audit_chain_checkpoint_mismatch")
            checkpoint_at = stored_utc_timestamp(
                checkpoint["anchored_at"],
                code="audit_chain_checkpoint_malformed",
                error_factory=StorageSchemaError,
                accept_datetime=False,
            )
        oldest_unanchored_at: datetime | None = None
        if head.sequence > checkpoint_sequence:
            row = connection.execute(
                """
                SELECT appended_at
                FROM gateway_audit_chain_entries
                WHERE organization_id = ? AND chain_epoch = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT 1
                """,
                (organization_id, head.chain_epoch, checkpoint_sequence),
            ).fetchone()
            if row is None:
                raise StorageSchemaError("audit_chain_head_mismatch")
            oldest_unanchored_at = stored_utc_timestamp(
                row["appended_at"],
                code="audit_chain_entry_malformed",
                error_factory=StorageSchemaError,
                accept_datetime=False,
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
        """Return local anchor freshness only; this method never contacts Object Lock."""

        validate_anchor_age(maximum_age_seconds, error_factory=StorageSchemaError)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise StorageSchemaError("audit_chain_anchor_age_invalid")
        with self._lock, self._connection() as connection:
            return self._audit_chain_anchor_status_in_connection(
                connection,
                organization_id=organization_id,
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
        """Persist a successful external checkpoint without putting it on the request path."""

        try:
            checkpoint_id, organization_id, epoch, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
        except AuditChainError as error:
            raise StorageSchemaError(error.code) from None
        chain_version = checkpoint.get("chain_version")
        if (
            not isinstance(chain_version, int)
            or not is_sha256_digest(artifact_sha256)
            or not isinstance(anchor_backend, str)
            or not anchor_backend
            or (object_version is not None and not isinstance(object_version, str))
        ):
            raise StorageSchemaError("audit_chain_checkpoint_malformed")
        timestamp = anchored_at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise StorageSchemaError("audit_chain_checkpoint_malformed")
        anchored = timestamp.astimezone(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            entry = connection.execute(
                """
                SELECT event_digest
                FROM gateway_audit_chain_entries
                WHERE organization_id = ? AND chain_epoch = ? AND sequence = ?
                """,
                (organization_id, epoch, sequence),
            ).fetchone()
            if entry is None or not isinstance(entry["event_digest"], str):
                raise StorageSchemaError("audit_chain_checkpoint_missing")
            if not hmac.compare_digest(str(entry["event_digest"]), head_digest):
                raise StorageSchemaError("audit_chain_checkpoint_mismatch")
            connection.execute(
                """
                INSERT INTO gateway_audit_chain_checkpoints (
                    checkpoint_id, organization_id, chain_version, chain_epoch, sequence,
                    head_digest, artifact_sha256, anchor_backend, object_version, anchored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(checkpoint_id) DO NOTHING
                """,
                (
                    checkpoint_id,
                    organization_id,
                    chain_version,
                    epoch,
                    sequence,
                    head_digest,
                    artifact_sha256,
                    anchor_backend,
                    object_version,
                    anchored,
                ),
            )
            existing = connection.execute(
                """
                SELECT organization_id, chain_version, chain_epoch, sequence, head_digest,
                       artifact_sha256, anchor_backend, object_version
                FROM gateway_audit_chain_checkpoints
                WHERE checkpoint_id = ?
                """,
                (checkpoint_id,),
            ).fetchone()
            if existing is None or (
                existing["organization_id"] != organization_id
                or int(existing["chain_version"]) != chain_version
                or int(existing["chain_epoch"]) != epoch
                or int(existing["sequence"]) != sequence
                or not hmac.compare_digest(str(existing["head_digest"]), head_digest)
                or not hmac.compare_digest(str(existing["artifact_sha256"]), artifact_sha256)
                or existing["anchor_backend"] != anchor_backend
                or existing["object_version"] != object_version
            ):
                raise StorageSchemaError("audit_chain_checkpoint_conflict")

    def begin_audit_chain_epoch(
        self,
        *,
        checkpoint: Mapping[str, object],
        reason_code: str,
    ) -> AuditChainHead:
        """Explicitly begin a post-restore or post-migration epoch from a trusted checkpoint."""

        if reason_code not in {"restore", "migration"}:
            raise StorageSchemaError("audit_chain_epoch_reason_invalid")
        try:
            _, organization_id, predecessor_epoch, predecessor_sequence, predecessor_digest = audit_chain_checkpoint_summary(
                checkpoint
            )
        except AuditChainError as error:
            raise StorageSchemaError(error.code) from None
        chain_version = checkpoint.get("chain_version")
        if not isinstance(chain_version, int):
            raise StorageSchemaError("audit_chain_checkpoint_malformed")
        new_epoch = predecessor_epoch + 1
        with self._lock, self._connection() as connection:
            head = self._audit_chain_head_in_connection(connection, organization_id=organization_id)
            if head is None:
                raise StorageSchemaError("audit_chain_head_unavailable")
            if chain_version != head.chain_version or new_epoch <= head.chain_epoch:
                raise StorageSchemaError("audit_chain_epoch_predecessor_invalid")
            connection.execute(
                """
                INSERT INTO gateway_audit_chain_epochs (
                    organization_id, chain_version, chain_epoch, created_at, reason_code,
                    predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(organization_id, chain_epoch) DO NOTHING
                """,
                (
                    organization_id,
                    chain_version,
                    new_epoch,
                    datetime.now(timezone.utc).isoformat(),
                    reason_code,
                    predecessor_epoch,
                    predecessor_sequence,
                    predecessor_digest,
                ),
            )
            epoch = connection.execute(
                """
                SELECT chain_version, predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
                FROM gateway_audit_chain_epochs
                WHERE organization_id = ? AND chain_epoch = ?
                """,
                (organization_id, new_epoch),
            ).fetchone()
            if epoch is None or (
                int(epoch["chain_version"]) != chain_version
                or int(epoch["predecessor_chain_epoch"]) != predecessor_epoch
                or int(epoch["predecessor_sequence"]) != predecessor_sequence
                or not hmac.compare_digest(str(epoch["predecessor_head_digest"]), predecessor_digest)
            ):
                raise StorageSchemaError("audit_chain_epoch_conflict")
            updated = connection.execute(
                """
                UPDATE gateway_audit_chain_heads
                SET chain_epoch = ?, sequence = 0, head_digest = ?
                WHERE organization_id = ? AND chain_version = ? AND chain_epoch = ?
                  AND sequence = ? AND head_digest IS ?
                """,
                (
                    new_epoch,
                    predecessor_digest,
                    organization_id,
                    head.chain_version,
                    head.chain_epoch,
                    head.sequence,
                    head.head_digest,
                ),
            )
            if updated.rowcount != 1:
                raise StorageSchemaError("audit_chain_head_conflict")
        return AuditChainHead(
            organization_id=organization_id,
            chain_version=chain_version,
            chain_epoch=new_epoch,
            sequence=0,
            head_digest=predecessor_digest,
        )

    def _audit_chain_source_events_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        organization_id: str,
    ) -> tuple[AuditChainSourceEventInput, ...]:
        sources: list[AuditChainSourceEventInput] = []
        source_identities = set()
        usage_rows = connection.execute(
            """
            SELECT
                id, occurred_at, evidence_schema_id, evidence_schema_version,
                organization_id, actor_id, actor_name, team_id, team_name,
                identity_type, authentication_source, client, protocol, requested_model,
                resolved_alias, upstream_model, provider_reported_model, policy_version,
                policy_action, status, input_tokens, output_tokens,
                cache_read_tokens, cache_write_tokens, reasoning_tokens,
                cost_microusd, cost_basis, allocation_basis, coverage,
                provider_request_id, redaction_count, redaction_rules
            FROM gateway_usage_events
            WHERE organization_id = ?
            """,
            (organization_id,),
        ).fetchall()
        secret_rows = connection.execute(
            """
            SELECT
                id, occurred_at, evidence_schema_id, evidence_schema_version,
                organization_id, actor_id, actor_name, team_id, team_name,
                identity_type, authentication_source, client, protocol, requested_model,
                policy_version, coverage, action, detection_count, rules
            FROM gateway_secret_events
            WHERE organization_id = ?
            """,
            (organization_id,),
        ).fetchall()
        for row in usage_rows:
            try:
                event = usage_audit_event(dict(row))
            except EvidenceStorageError as error:
                raise StorageSchemaError(error.code) from None
            source = normalize_audit_chain_source_event_input(
                event,
                error_factory=StorageSchemaError,
            )
            if source.source in source_identities:
                raise StorageSchemaError("audit_chain_source_event_malformed")
            source_identities.add(source.source)
            sources.append(source)
        for row in secret_rows:
            try:
                event = security_audit_event(dict(row))
            except EvidenceStorageError as error:
                raise StorageSchemaError(error.code) from None
            source = normalize_audit_chain_source_event_input(
                event,
                error_factory=StorageSchemaError,
            )
            if source.source in source_identities:
                raise StorageSchemaError("audit_chain_source_event_malformed")
            source_identities.add(source.source)
            sources.append(source)
        return tuple(sources)

    def verify_audit_chain(
        self,
        *,
        organization_id: str,
        checkpoint: Mapping[str, object] | None = None,
    ) -> AuditChainHead:
        """Verify ordered entries, source-event correspondence, and an optional external checkpoint."""

        checkpoint_input = normalize_audit_chain_checkpoint_input(
            checkpoint,
            organization_id=organization_id,
            error_factory=StorageSchemaError,
        )

        with self._lock, self._connection() as connection:
            # Take one SQLite read snapshot. Without an explicit transaction,
            # a long verification can observe a new head and an older entry
            # set (or the reverse) while another gateway instance appends.
            # Verification is off the request path, so this short snapshot is
            # preferable to a false integrity alarm.
            connection.execute("BEGIN")
            head = self._audit_chain_head_in_connection(
                connection,
                organization_id=organization_id,
                create=False,
            )
            if head is None:
                if checkpoint_input is not None:
                    raise StorageSchemaError("audit_chain_checkpoint_mismatch")
                return AuditChainHead(organization_id, 1, 1, 0, None)
            epoch_rows = connection.execute(
                """
                SELECT chain_version, chain_epoch, reason_code, predecessor_chain_epoch,
                       predecessor_sequence, predecessor_head_digest
                FROM gateway_audit_chain_epochs
                WHERE organization_id = ?
                ORDER BY chain_epoch ASC
                """,
                (organization_id,),
            ).fetchall()
            entry_rows = connection.execute(
                """
                SELECT chain_version, chain_epoch, sequence, entry_schema_id,
                       entry_schema_version, event_id, previous_digest, event_digest, event_json
                FROM gateway_audit_chain_entries
                WHERE organization_id = ?
                ORDER BY chain_epoch ASC, sequence ASC
                """,
                (organization_id,),
            ).fetchall()
            source_events = self._audit_chain_source_events_in_connection(
                connection,
                organization_id=organization_id,
            )
        inputs = AuditChainVerificationInputs(
            organization_id=organization_id,
            head=head,
            epochs=tuple(
                normalize_audit_chain_epoch_input(row, error_factory=StorageSchemaError)
                for row in epoch_rows
            ),
            entries=tuple(
                normalize_audit_chain_entry_input(
                    row,
                    organization_id=organization_id,
                    error_factory=StorageSchemaError,
                )
                for row in entry_rows
            ),
            source_events=source_events,
            checkpoint=checkpoint_input,
        )
        try:
            return verify_audit_chain_inputs(inputs)
        except AuditChainError as error:
            raise StorageSchemaError(error.code) from None
