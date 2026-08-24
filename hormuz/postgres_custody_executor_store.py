"""PostgreSQL persistence for isolated governed-custody execution attempts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from uuid import uuid4

from .contracts import ContractValidationError, validate_custody_execution_attempt, validate_custody_execution_event
from .custody_execution_repository import (
    CUSTODY_EXECUTION_FAILURE_REASONS,
    CUSTODY_EXECUTION_STATES,
    CUSTODY_EXECUTION_UNKNOWN_REASONS,
    CustodyExecutionAttempt,
    CustodyExecutionError,
    CustodyExecutionEvent,
    CustodyExecutionResult,
    CustodyExecutionRequest,
    CustodyExecutionStatus,
)
from .custody_lifecycle import CustodyAssetCatalog, CustodyLifecycleError
from .postgres import PostgresConnectionPool, PostgresStorageError, postgres_transaction, verify_postgres_schema
from .postgres_custody_lifecycle_store import (
    append_custody_lifecycle_event,
    prepare_custody_runtime_barrier,
    record_custody_envelope_attestation,
    register_custody_asset_catalog,
)


_STATUS_ATTEMPT_LIMIT = 100


class PostgresCustodyExecutorStore:
    """Attempt ledger owned by the distinct custody-executor role.

    The executor receives only ``SELECT`` access to custody authorization
    facts, and ``SELECT``/``INSERT`` access to this immutable attempt ledger.
    It cannot update or delete an authorization, approval, root, or event.
    """

    def __init__(
        self,
        dsn: str,
        *,
        schema: str,
        custody_executor_role: str,
        pending_attempt_ttl_seconds: int,
        asset_catalog: CustodyAssetCatalog | None = None,
        connection_pool: PostgresConnectionPool | None = None,
    ) -> None:
        if isinstance(pending_attempt_ttl_seconds, bool) or not isinstance(pending_attempt_ttl_seconds, int):
            raise CustodyExecutionError("custody_execution_pending_ttl_invalid")
        if not 60 <= pending_attempt_ttl_seconds <= 24 * 60 * 60:
            raise CustodyExecutionError("custody_execution_pending_ttl_invalid")
        self._dsn = dsn
        self._schema = schema
        self._runtime_role = custody_executor_role
        self._pending_attempt_ttl = timedelta(seconds=pending_attempt_ttl_seconds)
        self._asset_catalog = asset_catalog
        self._connection_pool = connection_pool
        verify_postgres_schema(
            dsn,
            schema=schema,
            runtime_role=custody_executor_role,
            connection_pool=connection_pool,
            verify_runtime_schema=False,
            verify_custody_executor_schema=True,
        )

    def claim(self, *, request: CustodyExecutionRequest) -> CustodyExecutionAttempt:
        """Atomically consume one exact active governed authorization.

        The shared tenant advisory lock serializes this start boundary against
        custody-admin revocation. The root and its pending event commit before
        a caller can make a key-service, secret-store, envelope, or object
        storage call.
        """

        now = datetime.now(timezone.utc)
        with self._transaction(request.organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, request.organization_id)
                if self._asset_catalog is not None:
                    register_custody_asset_catalog(
                        cursor,
                        organization_id=request.organization_id,
                        catalog=self._asset_catalog,
                        registered_at=now,
                    )
                    self._require_write_asset_unrestricted(cursor, request=request)
                    if request.operation_type in {
                        "disable_provider_credential",
                        "retire_envelope",
                        "retire_key_reference",
                    }:
                        cursor.execute(
                            """
                            SELECT 1
                            FROM custody_runtime_projection_barriers
                            WHERE organization_id = %s
                              AND activated_at IS NULL
                              AND resolved_at IS NULL
                            LIMIT 1
                            """,
                            (request.organization_id,),
                        )
                        if cursor.fetchone() is not None:
                            raise CustodyExecutionError("custody_execution_coordination_busy")
                    if (
                        request.operation_type == "resolve_recovery"
                        and request.parameters.get("resolution_code") == "confirmed_applied"
                    ):
                        recovery_execution_id = request.target.get("recovery_execution_id")
                        cursor.execute(
                            """
                            SELECT 1
                            FROM custody_runtime_projection_barriers
                            WHERE organization_id = %s
                              AND execution_id = %s
                              AND activated_at IS NULL
                              AND resolved_at IS NULL
                            LIMIT 1
                            """,
                            (request.organization_id, recovery_execution_id),
                        )
                        if cursor.fetchone() is not None:
                            raise CustodyExecutionError("custody_recovery_resolution_invalid")
                cursor.execute(
                    """
                    SELECT intent.*, administrator.active AS requester_active
                    FROM custody_operation_intents AS intent
                    LEFT JOIN custody_administrators AS administrator
                      ON administrator.organization_id = intent.organization_id
                     AND administrator.identity_key = intent.requested_by_identity_key
                    WHERE intent.organization_id = %s AND intent.operation_id = %s
                    """,
                    (request.organization_id, request.operation_id),
                )
                intent = cursor.fetchone()
                if intent is None:
                    raise CustodyExecutionError("custody_execution_authorization_not_found")
                self._require_exact_authorization(cursor, intent, request=request, now=now)
                cursor.execute(
                    """
                    SELECT execution_id FROM custody_execution_attempts
                    WHERE organization_id = %s AND operation_id = %s
                    """,
                    (request.organization_id, request.operation_id),
                )
                if cursor.fetchone() is not None:
                    raise CustodyExecutionError("custody_execution_already_claimed")
                execution_id = str(uuid4())
                attempt = _attempt_from_intent(
                    intent,
                    execution_id=execution_id,
                    claimed_at=now,
                    events=(
                        CustodyExecutionEvent(
                            organization_id=request.organization_id,
                            execution_id=execution_id,
                            operation_id=request.operation_id,
                            occurred_at=now,
                            sequence=1,
                            state="pending",
                            reason_code=None,
                        ),
                    ),
                )
                self._validate_attempt(attempt)
                cursor.execute(
                    """
                    INSERT INTO custody_execution_attempts (
                        organization_id, execution_id, execution_schema_id, execution_schema_version,
                        operation_id, operation_type, target_kind, target_sha256, parameters_sha256,
                        protected_input_ref_sha256, claimed_at
                    ) VALUES (
                        %s, %s, 'hormuz.custody-execution-attempt', %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        attempt.organization_id,
                        attempt.execution_id,
                        attempt.execution_schema_version,
                        attempt.operation_id,
                        attempt.operation_type,
                        attempt.target_kind,
                        attempt.target_sha256,
                        attempt.parameters_sha256,
                        attempt.protected_input_ref_sha256,
                        attempt.claimed_at,
                    ),
                )
                self._insert_event(cursor, attempt.events[0])
        return attempt

    def register_asset_catalog(self, *, organization_ids: tuple[str, ...]) -> None:
        """Register the configured immutable identities through the machine boundary.

        Registration is configuration genesis, not an administrative state
        change: it has no effect on provider selection or customer KMS state.
        Existing identities can never be rebound because fingerprints must
        match the first registered mapping.
        """

        if self._asset_catalog is None:
            raise CustodyExecutionError("custody_lifecycle_configuration_required")
        if not organization_ids or len(set(organization_ids)) != len(organization_ids):
            raise CustodyExecutionError("custody_execution_organization_scope_invalid")
        now = datetime.now(timezone.utc)
        for organization_id in organization_ids:
            with self._transaction(organization_id) as connection:
                with connection.cursor() as cursor:
                    self._lock_tenant(cursor, organization_id)
                    register_custody_asset_catalog(
                        cursor,
                        organization_id=organization_id,
                        catalog=self._asset_catalog,
                        registered_at=now,
                    )

    def prepare_restriction(
        self,
        *,
        organization_id: str,
        execution_id: str,
        result: CustodyExecutionResult,
    ) -> None:
        """Prepare the exact local barrier before any replica may acknowledge it."""

        effect = result.lifecycle_effect
        if effect is None or effect.asset is None or result.envelope_attestation is not None:
            raise CustodyExecutionError("custody_lifecycle_execution_result_invalid")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_execution(cursor, organization_id, execution_id)
                attempt = self._load_attempt(cursor, organization_id=organization_id, execution_id=execution_id)
                if attempt.state != "pending":
                    raise CustodyExecutionError("custody_execution_already_finalized")
                try:
                    prepare_custody_runtime_barrier(
                        cursor,
                        attempt=attempt,
                        effect=effect,
                        prepared_at=now,
                    )
                except CustodyLifecycleError as error:
                    raise CustodyExecutionError(error.code) from None

    def finalize(
        self,
        *,
        organization_id: str,
        execution_id: str,
        state: str,
        reason_code: str | None = None,
        result: CustodyExecutionResult | None = None,
    ) -> CustodyExecutionAttempt:
        """Append one terminal event exactly once; roots are never rewritten."""

        if state not in {"succeeded", "failed", "outcome_unknown"}:
            raise CustodyExecutionError("custody_execution_terminal_state_invalid")
        if state == "succeeded" and reason_code is not None:
            raise CustodyExecutionError("custody_execution_terminal_reason_invalid")
        if state == "failed" and reason_code not in CUSTODY_EXECUTION_FAILURE_REASONS:
            raise CustodyExecutionError("custody_execution_terminal_reason_invalid")
        if state == "outcome_unknown" and reason_code not in CUSTODY_EXECUTION_UNKNOWN_REASONS:
            raise CustodyExecutionError("custody_execution_terminal_reason_invalid")
        if state != "succeeded" and result is not None:
            raise CustodyExecutionError("custody_execution_terminal_result_invalid")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_execution(cursor, organization_id, execution_id)
                attempt = self._load_attempt(cursor, organization_id=organization_id, execution_id=execution_id)
                if attempt.state != "pending":
                    raise CustodyExecutionError("custody_execution_already_finalized")
                if (
                    state == "succeeded"
                    and result is not None
                    and result.lifecycle_effect is not None
                    and result.lifecycle_effect.asset is not None
                ):
                    self._require_restriction_coordination_ready(
                        cursor,
                        organization_id=organization_id,
                        execution_id=execution_id,
                    )
                event = CustodyExecutionEvent(
                    organization_id=organization_id,
                    execution_id=execution_id,
                    operation_id=attempt.operation_id,
                    occurred_at=now,
                    sequence=2,
                    state=state,
                    reason_code=reason_code,
                )
                self._validate_event(event)
                self._insert_event(cursor, event)
                completed = CustodyExecutionAttempt(
                    organization_id=attempt.organization_id,
                    execution_id=attempt.execution_id,
                    operation_id=attempt.operation_id,
                    operation_type=attempt.operation_type,
                    target_kind=attempt.target_kind,
                    target_sha256=attempt.target_sha256,
                    parameters_sha256=attempt.parameters_sha256,
                    protected_input_ref_sha256=attempt.protected_input_ref_sha256,
                    claimed_at=attempt.claimed_at,
                    events=(*attempt.events, event),
                    execution_schema_version=attempt.execution_schema_version,
                )
                self._finalize_result(cursor, attempt=completed, result=result, occurred_at=now)
                return completed

    def sweep_stale_pending(self, *, organization_ids: tuple[str, ...]) -> int:
        """Mark stale durable pending attempts unknown without replaying work."""

        if not organization_ids or len(set(organization_ids)) != len(organization_ids):
            raise CustodyExecutionError("custody_execution_organization_scope_invalid")
        cutoff = datetime.now(timezone.utc) - self._pending_attempt_ttl
        count = 0
        for organization_id in organization_ids:
            with self._transaction(organization_id) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT attempt.execution_id, attempt.operation_id
                        FROM custody_execution_attempts AS attempt
                        LEFT JOIN custody_execution_events AS terminal
                          ON terminal.organization_id = attempt.organization_id
                         AND terminal.execution_id = attempt.execution_id
                         AND terminal.sequence = 2
                        WHERE attempt.organization_id = %s
                          AND attempt.claimed_at <= %s
                          AND terminal.execution_id IS NULL
                        ORDER BY attempt.claimed_at, attempt.execution_id
                        """,
                        (organization_id, cutoff),
                    )
                    pending = tuple(cursor.fetchall())
                    for row in pending:
                        execution_id = str(row["execution_id"])
                        self._lock_execution(cursor, organization_id, execution_id)
                        try:
                            attempt = self._load_attempt(
                                cursor,
                                organization_id=organization_id,
                                execution_id=execution_id,
                            )
                        except CustodyExecutionError:
                            continue
                        if attempt.state != "pending":
                            continue
                        event = CustodyExecutionEvent(
                            organization_id=organization_id,
                            execution_id=execution_id,
                            operation_id=attempt.operation_id,
                            occurred_at=datetime.now(timezone.utc),
                            sequence=2,
                            state="outcome_unknown",
                            reason_code="stale_pending",
                        )
                        self._validate_event(event)
                        self._insert_event(cursor, event)
                        count += 1
        return count

    def _require_exact_authorization(
        self,
        cursor: Any,
        intent: dict[str, object],
        *,
        request: CustodyExecutionRequest,
        now: datetime,
    ) -> None:
        expires_at = intent.get("expires_at")
        if not isinstance(expires_at, datetime) or now >= expires_at:
            raise CustodyExecutionError("custody_execution_authorization_expired")
        if not bool(intent.get("requester_active")):
            raise CustodyExecutionError("custody_execution_requester_inactive")
        if str(intent.get("state")) != "authorized":
            raise CustodyExecutionError("custody_execution_authorization_not_authorized")
        expected_risk = "routine" if request.operation_type in {"seal_envelope", "rewrap_envelope", "verify_restore"} else "destructive"
        if str(intent.get("risk_level")) != expected_risk or str(intent.get("operation_type")) != request.operation_type:
            raise CustodyExecutionError("custody_execution_authorization_mismatch")
        if (
            str(intent.get("target_sha256")) != request.target_sha256
            or str(intent.get("parameters_sha256")) != request.parameters_sha256
            or _nullable_text(intent.get("protected_input_ref_sha256")) != request.protected_input_ref_sha256
        ):
            raise CustodyExecutionError("custody_execution_authorization_mismatch")
        if expected_risk == "destructive":
            if self._asset_catalog is None:
                raise CustodyExecutionError("custody_lifecycle_configuration_required")
            cursor.execute(
                """
                SELECT custody_execution_has_two_active_approvers(%s, %s) AS authorized
                """,
                (request.organization_id, request.operation_id),
            )
            approval_row = cursor.fetchone()
            if approval_row is None or approval_row.get("authorized") is not True:
                raise CustodyExecutionError("custody_execution_active_approvers_required")

    def _finalize_result(
        self,
        cursor: Any,
        *,
        attempt: CustodyExecutionAttempt,
        result: CustodyExecutionResult | None,
        occurred_at: datetime,
    ) -> None:
        if attempt.state != "succeeded":
            if result is not None:
                raise CustodyExecutionError("custody_execution_terminal_result_invalid")
            return
        is_destructive = attempt.operation_type not in {"seal_envelope", "rewrap_envelope", "verify_restore"}
        if is_destructive:
            if result is None or result.lifecycle_effect is None or result.envelope_attestation is not None:
                raise CustodyExecutionError("custody_lifecycle_execution_result_required")
            if result.lifecycle_effect.operation_type != attempt.operation_type:
                raise CustodyExecutionError("custody_lifecycle_execution_result_invalid")
            try:
                append_custody_lifecycle_event(
                    cursor,
                    attempt=attempt,
                    effect=result.lifecycle_effect,
                    occurred_at=occurred_at,
                )
            except CustodyLifecycleError as error:
                raise CustodyExecutionError(error.code) from None
            return
        if result is not None and result.lifecycle_effect is not None:
            raise CustodyExecutionError("custody_lifecycle_execution_result_invalid")
        if result is not None and result.envelope_attestation is not None:
            try:
                record_custody_envelope_attestation(
                    cursor,
                    attempt=attempt,
                    attestation=result.envelope_attestation,
                    occurred_at=occurred_at,
                )
            except CustodyLifecycleError as error:
                raise CustodyExecutionError(error.code) from None

    def _require_write_asset_unrestricted(self, cursor: Any, *, request: CustodyExecutionRequest) -> None:
        """Pin the configured write key before an operation may call the KMS.

        A later committed retirement may complete while this already-claimed
        routine finishes, matching the request-start pin boundary used by the
        gateway. A new claim after the restriction commits is denied.
        """

        if self._asset_catalog is None or request.operation_type not in {"seal_envelope", "rewrap_envelope"}:
            return
        path = request.target.get("path")
        if not isinstance(path, str):
            raise CustodyExecutionError("custody_lifecycle_write_asset_invalid")
        envelopes = tuple(
            asset
            for asset in self._asset_catalog.assets_for(
                organization_id=request.organization_id,
                asset_type="envelope",
            )
            if asset.binding.get("path") == path
        )
        if len(envelopes) != 1:
            raise CustodyExecutionError("custody_lifecycle_write_asset_invalid")
        linked = envelopes[0].binding.get("key_reference_asset", "")
        try:
            linked_id, linked_generation = linked.rsplit("@", 1)
            generation = int(linked_generation)
            key_asset = self._asset_catalog.asset(
                organization_id=request.organization_id,
                asset_type="key_reference",
                asset_id=linked_id,
                generation=generation,
            )
        except (CustodyLifecycleError, TypeError, ValueError):
            raise CustodyExecutionError("custody_lifecycle_write_asset_invalid") from None
        cursor.execute(
            """
            SELECT restriction_kind
            FROM custody_runtime_projection_restrictions
            WHERE organization_id = %s
              AND asset_type = %s
              AND asset_id = %s
              AND generation = %s
            UNION ALL
            SELECT restriction_kind
            FROM custody_runtime_projection_barriers
            WHERE organization_id = %s
              AND asset_type = %s
              AND asset_id = %s
              AND asset_generation = %s
              AND activated_at IS NULL
              AND resolved_at IS NULL
            LIMIT 1
            """,
            (
                request.organization_id,
                key_asset.asset_type,
                key_asset.asset_id,
                key_asset.generation,
                request.organization_id,
                key_asset.asset_type,
                key_asset.asset_id,
                key_asset.generation,
            ),
        )
        restriction = cursor.fetchone()
        if restriction is not None:
            raise CustodyExecutionError("custody_key_reference_write_retired")

    @staticmethod
    def _require_restriction_coordination_ready(
        cursor: Any,
        *,
        organization_id: str,
        execution_id: str,
    ) -> None:
        """Serialize activation and reject it until every live replica acked."""

        # The lifecycle lock is acquired first everywhere an event may append;
        # the runtime lock then freezes registration, heartbeat, and ack state.
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"hormuz:custody-lifecycle:{organization_id}",),
        )
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"hormuz:custody-runtime:{organization_id}",),
        )
        cursor.execute(
            """
            SELECT barrier_id
            FROM custody_runtime_projection_barriers
            WHERE organization_id = %s
              AND execution_id = %s
              AND activated_at IS NULL
              AND resolved_at IS NULL
            """,
            (organization_id, execution_id),
        )
        barrier = cursor.fetchone()
        if barrier is None:
            raise CustodyExecutionError("custody_execution_coordination_not_prepared")
        cursor.execute(
            """
            SELECT 1
            FROM custody_runtime_replicas AS replica
            WHERE replica.organization_id = %s
              AND replica.retired_at IS NULL
              AND replica.lease_expires_at > clock_timestamp()
              AND NOT EXISTS (
                  SELECT 1
                  FROM custody_runtime_projection_acks AS acknowledgement
                  WHERE acknowledgement.organization_id = replica.organization_id
                    AND acknowledgement.replica_id = replica.replica_id
                    AND acknowledgement.barrier_id = %s
              )
            LIMIT 1
            """,
            (organization_id, barrier["barrier_id"]),
        )
        if cursor.fetchone() is not None:
            raise CustodyExecutionError("custody_execution_coordination_pending")

    def _load_attempt(
        self,
        cursor: Any,
        *,
        organization_id: str,
        execution_id: str,
    ) -> CustodyExecutionAttempt:
        cursor.execute(
            """
            SELECT * FROM custody_execution_attempts
            WHERE organization_id = %s AND execution_id = %s
            """,
            (organization_id, execution_id),
        )
        root = cursor.fetchone()
        if root is None:
            raise CustodyExecutionError("custody_execution_not_found")
        cursor.execute(
            """
            SELECT organization_id, execution_id, operation_id, occurred_at, sequence, state, reason_code
            FROM custody_execution_events
            WHERE organization_id = %s AND execution_id = %s
            ORDER BY sequence
            """,
            (organization_id, execution_id),
        )
        return _attempt_from_rows(root, cursor.fetchall())

    def _insert_event(self, cursor: Any, event: CustodyExecutionEvent) -> None:
        cursor.execute(
            """
            INSERT INTO custody_execution_events (
                organization_id, execution_id, sequence, event_schema_id, event_schema_version,
                operation_id, occurred_at, state, reason_code
            ) VALUES (%s, %s, %s, 'hormuz.custody-execution-event', 1, %s, %s, %s, %s)
            """,
            (
                event.organization_id,
                event.execution_id,
                event.sequence,
                event.operation_id,
                event.occurred_at,
                event.state,
                event.reason_code,
            ),
        )

    def _validate_attempt(self, attempt: CustodyExecutionAttempt) -> None:
        try:
            validate_custody_execution_attempt(attempt.contract_record())
            for event in attempt.events:
                self._validate_event(event)
        except ContractValidationError as error:
            raise CustodyExecutionError("custody_execution_evidence_invalid") from error

    def _validate_event(self, event: CustodyExecutionEvent) -> None:
        try:
            validate_custody_execution_event(event.contract_record())
        except ContractValidationError as error:
            raise CustodyExecutionError("custody_execution_evidence_invalid") from error

    def _lock_tenant(self, cursor: Any, organization_id: str) -> None:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"hormuz:custody:{organization_id}",))

    def _lock_execution(self, cursor: Any, organization_id: str, execution_id: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"hormuz:custody-execution:{organization_id}:{execution_id}",),
        )

    def _transaction(self, organization_id: str) -> Iterator[Any]:
        return postgres_transaction(
            self._dsn,
            schema=self._schema,
            runtime_role=self._runtime_role,
            organization_id=organization_id,
            connection_pool=self._connection_pool,
        )


def load_custody_execution_status(
    cursor: Any,
    *,
    organization_id: str,
    limit: int = _STATUS_ATTEMPT_LIMIT,
) -> CustodyExecutionStatus:
    """Load metadata-only attempt state for the custody-admin status surface."""

    cursor.execute(
        "SELECT COUNT(*) AS count FROM custody_execution_attempts WHERE organization_id = %s",
        (organization_id,),
    )
    attempt_count = int(cursor.fetchone()["count"])
    cursor.execute(
        """
        SELECT * FROM custody_execution_attempts
        WHERE organization_id = %s
        ORDER BY claimed_at DESC, execution_id DESC
        LIMIT %s
        """,
        (organization_id, limit),
    )
    roots = tuple(cursor.fetchall())
    execution_ids = [str(root["execution_id"]) for root in roots]
    events_by_execution: dict[str, list[dict[str, object]]] = defaultdict(list)
    if execution_ids:
        cursor.execute(
            """
            SELECT organization_id, execution_id, operation_id, occurred_at, sequence, state, reason_code
            FROM custody_execution_events
            WHERE organization_id = %s AND execution_id = ANY(%s)
            ORDER BY execution_id, sequence
            """,
            (organization_id, execution_ids),
        )
        for event in cursor.fetchall():
            events_by_execution[str(event["execution_id"])].append(event)
    attempts = tuple(
        _attempt_from_rows(root, events_by_execution[str(root["execution_id"])]) for root in roots
    )
    return CustodyExecutionStatus(
        organization_id=organization_id,
        attempt_count=attempt_count,
        attempts=attempts,
    )


def _attempt_from_intent(
    intent: dict[str, object],
    *,
    execution_id: str,
    claimed_at: datetime,
    events: tuple[CustodyExecutionEvent, ...],
) -> CustodyExecutionAttempt:
    return CustodyExecutionAttempt(
        organization_id=str(intent["organization_id"]),
        execution_id=execution_id,
        operation_id=str(intent["operation_id"]),
        operation_type=str(intent["operation_type"]),
        target_kind=str(intent["target_kind"]),
        target_sha256=str(intent["target_sha256"]),
        parameters_sha256=str(intent["parameters_sha256"]),
        protected_input_ref_sha256=_nullable_text(intent.get("protected_input_ref_sha256")),
        claimed_at=claimed_at,
        events=events,
        execution_schema_version=2,
    )


def _attempt_from_rows(root: dict[str, object], event_rows: list[dict[str, object]]) -> CustodyExecutionAttempt:
    claimed_at = root.get("claimed_at")
    if not isinstance(claimed_at, datetime):
        raise PostgresStorageError("custody_execution_attempt_invalid")
    events = tuple(_event_from_row(event) for event in event_rows)
    try:
        return CustodyExecutionAttempt(
            organization_id=str(root["organization_id"]),
            execution_id=str(root["execution_id"]),
            operation_id=str(root["operation_id"]),
            operation_type=str(root["operation_type"]),
            target_kind=str(root["target_kind"]),
            target_sha256=str(root["target_sha256"]),
            parameters_sha256=str(root["parameters_sha256"]),
            protected_input_ref_sha256=_nullable_text(root.get("protected_input_ref_sha256")),
            claimed_at=claimed_at,
            events=events,
            execution_schema_version=int(root["execution_schema_version"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PostgresStorageError("custody_execution_attempt_invalid") from error


def _event_from_row(row: dict[str, object]) -> CustodyExecutionEvent:
    occurred_at = row.get("occurred_at")
    if not isinstance(occurred_at, datetime):
        raise PostgresStorageError("custody_execution_event_invalid")
    try:
        return CustodyExecutionEvent(
            organization_id=str(row["organization_id"]),
            execution_id=str(row["execution_id"]),
            operation_id=str(row["operation_id"]),
            occurred_at=occurred_at,
            sequence=int(row["sequence"]),
            state=str(row["state"]),
            reason_code=_nullable_text(row.get("reason_code")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise PostgresStorageError("custody_execution_event_invalid") from error


def _nullable_text(value: object) -> str | None:
    return str(value) if value is not None else None
