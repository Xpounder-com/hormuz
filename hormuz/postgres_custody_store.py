"""PostgreSQL custody authority and content-free operation approvals."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Iterator
from uuid import UUID, uuid4

from .contracts import (
    CUSTODY_CONTROL_EVENT_SCHEMA_ID,
    CUSTODY_CONTROL_EVENT_SCHEMA_VERSION,
    ContractValidationError,
    validate_custody_control_event,
)
from .custody_repository import (
    CustodyAdministrator,
    CustodyApproval,
    CustodyControlError,
    CustodyControlStatus,
    CustodyOperationIntent,
    operation_target_kind,
    required_approvals,
    validate_sha256,
)
from .postgres import PostgresConnectionPool, PostgresStorageError, postgres_transaction, verify_postgres_schema


_STATUS_OPERATION_LIMIT = 100
_MAX_AUTHORIZATION_TTL = timedelta(hours=24)


def custody_identity_key(administrator: CustodyAdministrator) -> str:
    """Return an opaque stable key scoped to the custody authority domain."""

    if administrator.authentication_kind == "static":
        assert administrator.actor_id is not None
        material = f"custody\x00static\x00{administrator.organization_id}\x00{administrator.actor_id}"
    else:
        assert administrator.issuer is not None and administrator.subject is not None
        material = (
            f"custody\x00oidc\x00{administrator.organization_id}\x00"
            f"{administrator.issuer}\x00{administrator.subject}"
        )
    return f"{administrator.authentication_kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


class PostgresCustodyControlStore:
    """Custody-service write path using only the dedicated database role."""

    def __init__(
        self,
        dsn: str,
        *,
        schema: str,
        custody_control_role: str,
        connection_pool: PostgresConnectionPool | None = None,
    ) -> None:
        self._dsn = dsn
        self._schema = schema
        self._runtime_role = custody_control_role
        self._connection_pool = connection_pool
        verify_postgres_schema(
            dsn,
            schema=schema,
            runtime_role=custody_control_role,
            connection_pool=connection_pool,
            verify_runtime_schema=False,
            verify_custody_schema=True,
        )

    def is_initialized(self, *, organization_id: str) -> bool:
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM custody_tenants WHERE organization_id = %s", (organization_id,))
                return cursor.fetchone() is not None

    def bootstrap(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        administrators: tuple[CustodyAdministrator, ...],
    ) -> tuple[CustodyAdministrator, ...]:
        if not administrators:
            raise CustodyControlError("custody_bootstrap_administrators_required")
        _require_organization(caller, organization_id)
        administrator_keys: set[str] = set()
        for administrator in administrators:
            _require_organization(administrator, organization_id)
            identity_key = custody_identity_key(administrator)
            if identity_key in administrator_keys:
                raise CustodyControlError("custody_bootstrap_administrator_duplicate")
            administrator_keys.add(identity_key)
        if custody_identity_key(caller) not in administrator_keys:
            raise CustodyControlError("custody_bootstrap_caller_not_administrator")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                cursor.execute("SELECT 1 FROM custody_tenants WHERE organization_id = %s", (organization_id,))
                if cursor.fetchone() is not None:
                    raise CustodyControlError("custody_bootstrap_already_initialized")
                caller_key = custody_identity_key(caller)
                cursor.execute(
                    """
                    INSERT INTO custody_tenants (
                        organization_id, initialized_at, initialized_by_kind, initialized_by_identity_key
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (organization_id, now, caller.authentication_kind, caller_key),
                )
                for administrator in administrators:
                    self._insert_administrator(
                        cursor,
                        administrator=administrator,
                        created_at=now,
                        created_by=caller,
                    )
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    event_type="bootstrap_initialized",
                    actor=caller,
                )
        return administrators

    def grant_administrator(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        administrator: CustodyAdministrator,
    ) -> CustodyAdministrator:
        _require_organization(administrator, organization_id)
        if administrator.authentication_kind != "oidc":
            raise CustodyControlError("custody_static_administrator_grant_denied")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                identity_key = custody_identity_key(administrator)
                cursor.execute(
                    """
                    SELECT active FROM custody_administrators
                    WHERE organization_id = %s AND identity_key = %s
                    FOR UPDATE
                    """,
                    (organization_id, identity_key),
                )
                existing = cursor.fetchone()
                if existing is not None and bool(existing["active"]):
                    return administrator
                if existing is None:
                    self._insert_administrator(
                        cursor,
                        administrator=administrator,
                        created_at=now,
                        created_by=caller,
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE custody_administrators
                        SET active = TRUE,
                            revoked_at = NULL,
                            revoked_by_kind = NULL,
                            revoked_by_identity_key = NULL
                        WHERE organization_id = %s AND identity_key = %s
                        """,
                        (organization_id, identity_key),
                    )
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    event_type="administrator_granted",
                    actor=caller,
                    target_identity_key=identity_key,
                )
        return administrator

    def revoke_administrator(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        administrator: CustodyAdministrator,
    ) -> None:
        _require_organization(administrator, organization_id)
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                target_key = custody_identity_key(administrator)
                cursor.execute(
                    """
                    SELECT active FROM custody_administrators
                    WHERE organization_id = %s AND identity_key = %s
                    FOR UPDATE
                    """,
                    (organization_id, target_key),
                )
                target = cursor.fetchone()
                if target is None or not bool(target["active"]):
                    raise CustodyControlError("custody_administrator_not_found")
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM custody_administrators WHERE organization_id = %s AND active = TRUE",
                    (organization_id,),
                )
                if int(cursor.fetchone()["count"]) <= 1:
                    raise CustodyControlError("custody_last_administrator_revoke_denied")
                cursor.execute(
                    """
                    UPDATE custody_administrators
                    SET active = FALSE,
                        revoked_at = %s,
                        revoked_by_kind = %s,
                        revoked_by_identity_key = %s
                    WHERE organization_id = %s AND identity_key = %s
                    """,
                    (
                        now,
                        caller.authentication_kind,
                        custody_identity_key(caller),
                        organization_id,
                        target_key,
                    ),
                )
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    event_type="administrator_revoked",
                    actor=caller,
                    target_identity_key=target_key,
                )

    def request_operation(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        operation_type: str,
        target_sha256: str,
        parameters_sha256: str,
        protected_input_ref_sha256: str | None,
        expires_at: datetime,
    ) -> CustodyOperationIntent:
        target_sha256 = validate_sha256(target_sha256, "target_sha256")
        parameters_sha256 = validate_sha256(parameters_sha256, "parameters_sha256")
        if protected_input_ref_sha256 is not None:
            protected_input_ref_sha256 = validate_sha256(
                protected_input_ref_sha256,
                "protected_input_ref_sha256",
            )
        approval_requirement = required_approvals(operation_type)
        target_kind = operation_target_kind(operation_type)
        risk_level = "routine" if approval_requirement == 1 else "destructive"
        if operation_type == "seal_envelope" and protected_input_ref_sha256 is None:
            raise CustodyControlError("custody_protected_input_reference_required")
        if operation_type != "seal_envelope" and protected_input_ref_sha256 is not None:
            raise CustodyControlError("custody_protected_input_reference_not_allowed")
        now = datetime.now(timezone.utc)
        if (
            not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
            or expires_at <= now
            or expires_at > now + _MAX_AUTHORIZATION_TTL
        ):
            raise CustodyControlError("custody_operation_expiry_invalid")
        operation_id = str(uuid4())
        state = "authorized" if approval_requirement == 1 else "pending"
        authorized_at = now if state == "authorized" else None
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                cursor.execute(
                    """
                    INSERT INTO custody_operation_intents (
                        organization_id, operation_id, intent_schema_id, intent_schema_version,
                        operation_type, risk_level, target_kind, target_sha256, parameters_sha256,
                        protected_input_ref_sha256, state, required_approvals, created_at, expires_at,
                        authorized_at, requested_by_kind, requested_by_identity_key
                    ) VALUES (
                        %s, %s, 'hormuz.custody-operation-intent', 1,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        organization_id,
                        operation_id,
                        operation_type,
                        risk_level,
                        target_kind,
                        target_sha256,
                        parameters_sha256,
                        protected_input_ref_sha256,
                        state,
                        approval_requirement,
                        now,
                        expires_at,
                        authorized_at,
                        caller.authentication_kind,
                        custody_identity_key(caller),
                    ),
                )
                self._record_operation_event(
                    cursor,
                    event_type="operation_requested",
                    organization_id=organization_id,
                    actor=caller,
                    operation_id=operation_id,
                    operation_type=operation_type,
                    risk_level=risk_level,
                    target_kind=target_kind,
                    target_sha256=target_sha256,
                    parameters_sha256=parameters_sha256,
                    protected_input_ref_sha256=protected_input_ref_sha256,
                    required_approvals=approval_requirement,
                    approval_count=0,
                    expires_at=expires_at,
                )
                self._insert_approval(
                    cursor,
                    organization_id=organization_id,
                    operation_id=operation_id,
                    approver=caller,
                    approved_at=now,
                )
                self._record_operation_event(
                    cursor,
                    event_type="operation_approved",
                    organization_id=organization_id,
                    actor=caller,
                    operation_id=operation_id,
                    operation_type=operation_type,
                    risk_level=risk_level,
                    target_kind=target_kind,
                    target_sha256=target_sha256,
                    parameters_sha256=parameters_sha256,
                    protected_input_ref_sha256=protected_input_ref_sha256,
                    required_approvals=approval_requirement,
                    approval_count=1,
                    expires_at=expires_at,
                )
                if state == "authorized":
                    self._record_operation_event(
                        cursor,
                        event_type="operation_authorized",
                        organization_id=organization_id,
                        actor=caller,
                        operation_id=operation_id,
                        operation_type=operation_type,
                        risk_level=risk_level,
                        target_kind=target_kind,
                        target_sha256=target_sha256,
                        parameters_sha256=parameters_sha256,
                        protected_input_ref_sha256=protected_input_ref_sha256,
                        required_approvals=approval_requirement,
                        approval_count=1,
                        expires_at=expires_at,
                    )
                operation = self._load_operation(cursor, organization_id=organization_id, operation_id=operation_id)
        return operation

    def approve_operation(
        self,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
        operation_id: str,
    ) -> CustodyOperationIntent:
        _validate_operation_id(operation_id)
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                cursor.execute(
                    """
                    SELECT * FROM custody_operation_intents
                    WHERE organization_id = %s AND operation_id = %s
                    FOR UPDATE
                    """,
                    (organization_id, operation_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise CustodyControlError("custody_operation_not_found")
                if str(row["state"]) == "authorized":
                    raise CustodyControlError("custody_operation_already_authorized")
                expires_at = row["expires_at"]
                if not isinstance(expires_at, datetime) or now >= expires_at:
                    raise CustodyControlError("custody_operation_expired")
                caller_key = custody_identity_key(caller)
                cursor.execute(
                    """
                    SELECT 1 FROM custody_operation_approvals
                    WHERE organization_id = %s AND operation_id = %s AND approver_identity_key = %s
                    """,
                    (organization_id, operation_id, caller_key),
                )
                if cursor.fetchone() is not None:
                    raise CustodyControlError("custody_distinct_approver_required")
                cursor.execute(
                    """
                    SELECT 1
                    FROM custody_operation_approvals AS approval
                    LEFT JOIN custody_administrators AS administrator
                      ON administrator.organization_id = approval.organization_id
                     AND administrator.identity_key = approval.approver_identity_key
                    WHERE approval.organization_id = %s
                      AND approval.operation_id = %s
                      AND (administrator.identity_key IS NULL OR administrator.active = FALSE)
                    LIMIT 1
                    """,
                    (organization_id, operation_id),
                )
                if cursor.fetchone() is not None:
                    # Approval history remains immutable. A destructive intent
                    # whose earlier approver is no longer active cannot be
                    # completed under a weaker authority set; start a new
                    # intent and collect two current approvals instead.
                    raise CustodyControlError("custody_active_approvers_required")
                self._insert_approval(
                    cursor,
                    organization_id=organization_id,
                    operation_id=operation_id,
                    approver=caller,
                    approved_at=now,
                )
                cursor.execute(
                    """
                    SELECT COUNT(*) AS count FROM custody_operation_approvals
                    WHERE organization_id = %s AND operation_id = %s
                    """,
                    (organization_id, operation_id),
                )
                approval_count = int(cursor.fetchone()["count"])
                required = int(row["required_approvals"])
                self._record_operation_event_from_row(
                    cursor,
                    row=row,
                    event_type="operation_approved",
                    actor=caller,
                    approval_count=approval_count,
                )
                if approval_count != required:
                    raise CustodyControlError("custody_operation_approval_incomplete")
                cursor.execute(
                    """
                    UPDATE custody_operation_intents
                    SET state = 'authorized', authorized_at = %s
                    WHERE organization_id = %s AND operation_id = %s AND state = 'pending'
                    """,
                    (now, organization_id, operation_id),
                )
                if cursor.rowcount != 1:
                    raise CustodyControlError("custody_operation_state_conflict")
                self._record_operation_event_from_row(
                    cursor,
                    row=row,
                    event_type="operation_authorized",
                    actor=caller,
                    approval_count=approval_count,
                )
                operation = self._load_operation(cursor, organization_id=organization_id, operation_id=operation_id)
        return operation

    def status(self, *, organization_id: str, caller: CustodyAdministrator) -> CustodyControlStatus:
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                cursor.execute("SELECT 1 FROM custody_tenants WHERE organization_id = %s", (organization_id,))
                initialized = cursor.fetchone() is not None
                cursor.execute(
                    """
                    SELECT organization_id, authentication_kind, actor_id, issuer, subject
                    FROM custody_administrators
                    WHERE organization_id = %s AND active = TRUE
                    ORDER BY authentication_kind, actor_id NULLS LAST, issuer NULLS LAST, subject NULLS LAST
                    """,
                    (organization_id,),
                )
                administrators = tuple(_administrator_from_row(row) for row in cursor.fetchall())
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM custody_operation_intents WHERE organization_id = %s",
                    (organization_id,),
                )
                operation_count = int(cursor.fetchone()["count"])
                cursor.execute(
                    """
                    SELECT * FROM custody_operation_intents
                    WHERE organization_id = %s
                    ORDER BY created_at DESC, operation_id DESC
                    LIMIT %s
                    """,
                    (organization_id, _STATUS_OPERATION_LIMIT),
                )
                rows = cursor.fetchall()
                operation_ids = [str(row["operation_id"]) for row in rows]
                approvals_by_operation: dict[str, list[CustodyApproval]] = defaultdict(list)
                if operation_ids:
                    cursor.execute(
                        """
                        SELECT operation_id, approver_kind, approver_identity_key, approved_at
                        FROM custody_operation_approvals
                        WHERE organization_id = %s AND operation_id = ANY(%s)
                        ORDER BY approved_at, approver_identity_key
                        """,
                        (organization_id, operation_ids),
                    )
                    for approval_row in cursor.fetchall():
                        approval = _approval_from_row(approval_row)
                        approvals_by_operation[str(approval_row["operation_id"])].append(approval)
                operations = tuple(
                    _operation_from_row(row, approvals=tuple(approvals_by_operation[str(row["operation_id"])]))
                    for row in rows
                )
        return CustodyControlStatus(
            organization_id=organization_id,
            initialized=initialized,
            administrators=administrators,
            operation_count=operation_count,
            operations=operations,
        )

    def _load_operation(self, cursor: Any, *, organization_id: str, operation_id: str) -> CustodyOperationIntent:
        cursor.execute(
            "SELECT * FROM custody_operation_intents WHERE organization_id = %s AND operation_id = %s",
            (organization_id, operation_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise CustodyControlError("custody_operation_not_found")
        cursor.execute(
            """
            SELECT approver_kind, approver_identity_key, approved_at
            FROM custody_operation_approvals
            WHERE organization_id = %s AND operation_id = %s
            ORDER BY approved_at, approver_identity_key
            """,
            (organization_id, operation_id),
        )
        approvals = tuple(_approval_from_row(approval) for approval in cursor.fetchall())
        return _operation_from_row(row, approvals=approvals)

    def _require_administrator(
        self,
        cursor: Any,
        *,
        organization_id: str,
        caller: CustodyAdministrator,
    ) -> None:
        _require_organization(caller, organization_id)
        cursor.execute("SELECT 1 FROM custody_tenants WHERE organization_id = %s", (organization_id,))
        if cursor.fetchone() is None:
            raise CustodyControlError("custody_bootstrap_required")
        cursor.execute(
            "SELECT COUNT(*) AS count FROM custody_administrators WHERE organization_id = %s AND active = TRUE",
            (organization_id,),
        )
        if int(cursor.fetchone()["count"]) == 0:
            raise CustodyControlError("custody_break_glass_required")
        cursor.execute(
            """
            SELECT 1 FROM custody_administrators
            WHERE organization_id = %s AND identity_key = %s AND active = TRUE
            """,
            (organization_id, custody_identity_key(caller)),
        )
        if cursor.fetchone() is None:
            raise CustodyControlError("custody_administrator_required")

    def _insert_administrator(
        self,
        cursor: Any,
        *,
        administrator: CustodyAdministrator,
        created_at: datetime,
        created_by: CustodyAdministrator,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO custody_administrators (
                organization_id, identity_key, authentication_kind, actor_id, issuer, subject,
                active, created_at, created_by_kind, created_by_identity_key
            ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            """,
            (
                administrator.organization_id,
                custody_identity_key(administrator),
                administrator.authentication_kind,
                administrator.actor_id,
                administrator.issuer,
                administrator.subject,
                created_at,
                created_by.authentication_kind,
                custody_identity_key(created_by),
            ),
        )

    def _insert_approval(
        self,
        cursor: Any,
        *,
        organization_id: str,
        operation_id: str,
        approver: CustodyAdministrator,
        approved_at: datetime,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO custody_operation_approvals (
                organization_id, operation_id, approval_schema_id, approval_schema_version,
                approver_kind, approver_identity_key, approved_at
            ) VALUES (%s, %s, 'hormuz.custody-operation-approval', 1, %s, %s, %s)
            """,
            (
                organization_id,
                operation_id,
                approver.authentication_kind,
                custody_identity_key(approver),
                approved_at,
            ),
        )

    def _record_operation_event_from_row(
        self,
        cursor: Any,
        *,
        row: dict[str, object],
        event_type: str,
        actor: CustodyAdministrator,
        approval_count: int,
    ) -> None:
        self._record_operation_event(
            cursor,
            event_type=event_type,
            organization_id=str(row["organization_id"]),
            actor=actor,
            operation_id=str(row["operation_id"]),
            operation_type=str(row["operation_type"]),
            risk_level=str(row["risk_level"]),
            target_kind=str(row["target_kind"]),
            target_sha256=str(row["target_sha256"]),
            parameters_sha256=str(row["parameters_sha256"]),
            protected_input_ref_sha256=(
                str(row["protected_input_ref_sha256"])
                if row.get("protected_input_ref_sha256") is not None
                else None
            ),
            required_approvals=int(row["required_approvals"]),
            approval_count=approval_count,
            expires_at=row["expires_at"],
        )

    def _record_operation_event(
        self,
        cursor: Any,
        *,
        event_type: str,
        organization_id: str,
        actor: CustodyAdministrator,
        operation_id: str,
        operation_type: str,
        risk_level: str,
        target_kind: str,
        target_sha256: str,
        parameters_sha256: str,
        protected_input_ref_sha256: str | None,
        required_approvals: int,
        approval_count: int,
        expires_at: datetime,
    ) -> None:
        self._record_event(
            cursor,
            organization_id=organization_id,
            event_type=event_type,
            actor=actor,
            operation_id=operation_id,
            operation_type=operation_type,
            risk_level=risk_level,
            target_kind=target_kind,
            target_sha256=target_sha256,
            parameters_sha256=parameters_sha256,
            protected_input_ref_sha256=protected_input_ref_sha256,
            required_approvals=required_approvals,
            approval_count=approval_count,
            expires_at=expires_at,
        )

    def _record_event(
        self,
        cursor: Any,
        *,
        organization_id: str,
        event_type: str,
        actor: CustodyAdministrator,
        target_identity_key: str | None = None,
        operation_id: str | None = None,
        operation_type: str | None = None,
        risk_level: str | None = None,
        target_kind: str | None = None,
        target_sha256: str | None = None,
        parameters_sha256: str | None = None,
        protected_input_ref_sha256: str | None = None,
        required_approvals: int | None = None,
        approval_count: int | None = None,
        expires_at: datetime | None = None,
    ) -> None:
        occurred_at = datetime.now(timezone.utc)
        event = {
            "event_schema_id": CUSTODY_CONTROL_EVENT_SCHEMA_ID,
            "event_schema_version": CUSTODY_CONTROL_EVENT_SCHEMA_VERSION,
            "organization_id": organization_id,
            "occurred_at": occurred_at.isoformat(),
            "event_type": event_type,
            "actor_kind": actor.authentication_kind,
            "actor_identity_key": custody_identity_key(actor),
            "target_identity_key": target_identity_key,
            "operation_id": operation_id,
            "operation_type": operation_type,
            "risk_level": risk_level,
            "target_kind": target_kind,
            "target_sha256": target_sha256,
            "parameters_sha256": parameters_sha256,
            "protected_input_ref_sha256": protected_input_ref_sha256,
            "required_approvals": required_approvals,
            "approval_count": approval_count,
            "expires_at": expires_at.isoformat() if expires_at is not None else None,
        }
        try:
            validate_custody_control_event(event)
        except ContractValidationError as error:
            raise CustodyControlError("custody_control_event_invalid") from error
        cursor.execute(
            """
            INSERT INTO custody_control_events (
                event_id, event_schema_id, event_schema_version, organization_id, occurred_at,
                event_type, actor_kind, actor_identity_key, target_identity_key, operation_id,
                operation_type, risk_level, target_kind, target_sha256, parameters_sha256,
                protected_input_ref_sha256, required_approvals, approval_count, expires_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                str(uuid4()),
                event["event_schema_id"],
                event["event_schema_version"],
                event["organization_id"],
                event["occurred_at"],
                event["event_type"],
                event["actor_kind"],
                event["actor_identity_key"],
                event["target_identity_key"],
                event["operation_id"],
                event["operation_type"],
                event["risk_level"],
                event["target_kind"],
                event["target_sha256"],
                event["parameters_sha256"],
                event["protected_input_ref_sha256"],
                event["required_approvals"],
                event["approval_count"],
                event["expires_at"],
            ),
        )

    def _lock_tenant(self, cursor: Any, organization_id: str) -> None:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"hormuz:custody:{organization_id}",))

    def _transaction(self, organization_id: str) -> Iterator[Any]:
        return postgres_transaction(
            self._dsn,
            schema=self._schema,
            runtime_role=self._runtime_role,
            organization_id=organization_id,
            connection_pool=self._connection_pool,
        )


def _require_organization(administrator: CustodyAdministrator, organization_id: str) -> None:
    if administrator.organization_id != organization_id:
        raise CustodyControlError("custody_organization_mismatch")


def _validate_operation_id(value: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise CustodyControlError("custody_operation_id_invalid") from error


def _administrator_from_row(row: dict[str, object]) -> CustodyAdministrator:
    try:
        return CustodyAdministrator(
            organization_id=str(row["organization_id"]),
            authentication_kind=str(row["authentication_kind"]),
            actor_id=str(row["actor_id"]) if row.get("actor_id") is not None else None,
            issuer=str(row["issuer"]) if row.get("issuer") is not None else None,
            subject=str(row["subject"]) if row.get("subject") is not None else None,
        )
    except ValueError as error:
        raise PostgresStorageError("custody_administrator_invalid") from error


def _approval_from_row(row: dict[str, object]) -> CustodyApproval:
    approved_at = row["approved_at"]
    if not isinstance(approved_at, datetime):
        raise PostgresStorageError("custody_operation_invalid")
    try:
        return CustodyApproval(
            approver_kind=str(row["approver_kind"]),
            approver_identity_key=str(row["approver_identity_key"]),
            approved_at=approved_at,
        )
    except ValueError as error:
        raise PostgresStorageError("custody_operation_invalid") from error


def _operation_from_row(
    row: dict[str, object],
    *,
    approvals: tuple[CustodyApproval, ...],
) -> CustodyOperationIntent:
    created_at = row["created_at"]
    expires_at = row["expires_at"]
    authorized_at = row.get("authorized_at")
    if (
        not isinstance(created_at, datetime)
        or not isinstance(expires_at, datetime)
        or (authorized_at is not None and not isinstance(authorized_at, datetime))
    ):
        raise PostgresStorageError("custody_operation_invalid")
    try:
        return CustodyOperationIntent(
            organization_id=str(row["organization_id"]),
            operation_id=str(row["operation_id"]),
            operation_type=str(row["operation_type"]),
            risk_level=str(row["risk_level"]),
            target_kind=str(row["target_kind"]),
            target_sha256=str(row["target_sha256"]),
            parameters_sha256=str(row["parameters_sha256"]),
            protected_input_ref_sha256=(
                str(row["protected_input_ref_sha256"])
                if row.get("protected_input_ref_sha256") is not None
                else None
            ),
            state=str(row["state"]),
            required_approvals=int(row["required_approvals"]),
            approvals=approvals,
            created_at=created_at,
            expires_at=expires_at,
            authorized_at=authorized_at,
            requested_by_kind=str(row["requested_by_kind"]),
            requested_by_identity_key=str(row["requested_by_identity_key"]),
        )
    except ValueError as error:
        raise PostgresStorageError("custody_operation_invalid") from error
