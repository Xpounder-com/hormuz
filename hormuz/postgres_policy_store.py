"""PostgreSQL-backed immutable policy versions and policy-administration state."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from .config import GatewayConfig
from .contracts import (
    POLICY_CONTROL_EVENT_SCHEMA_ID,
    POLICY_CONTROL_EVENT_SCHEMA_VERSION,
    ContractValidationError,
    validate_policy_control_event,
)
from .policy_document import PolicyDocument, PolicyDocumentError, validate_redacted_change_summary
from .policy_repository import (
    PolicyActivation,
    PolicyAdministrator,
    PolicyControlError,
    PolicyControlStatus,
    PolicyVersionRecord,
)
from .postgres import PostgresConnectionPool, PostgresStorageError, postgres_transaction, verify_postgres_schema


def _identity_key(administrator: PolicyAdministrator) -> str:
    """Return an opaque stable key without copying OIDC subjects into events."""

    if administrator.authentication_kind == "static":
        assert administrator.actor_id is not None
        material = f"static\x00{administrator.organization_id}\x00{administrator.actor_id}"
    else:
        assert administrator.issuer is not None and administrator.subject is not None
        material = f"oidc\x00{administrator.organization_id}\x00{administrator.issuer}\x00{administrator.subject}"
    return f"{administrator.authentication_kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


class PostgresPolicyRuntimeStore:
    """The gateway's read-only view of the active tenant policy version."""

    def __init__(
        self,
        dsn: str,
        *,
        config: GatewayConfig,
        schema: str,
        runtime_role: str,
        connection_pool: PostgresConnectionPool | None = None,
    ) -> None:
        self._dsn = dsn
        self._config = config
        self._schema = schema
        self._runtime_role = runtime_role
        self._connection_pool = connection_pool
        verify_postgres_schema(
            dsn,
            schema=schema,
            runtime_role=runtime_role,
            connection_pool=self._connection_pool,
        )

    def active_version(self, *, organization_id: str) -> PolicyVersionRecord:
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        versions.organization_id,
                        versions.version_id,
                        versions.content_sha256,
                        versions.document_json,
                        versions.change_summary,
                        versions.created_at,
                        versions.author_kind,
                        versions.author_identity_key
                    FROM policy_active_versions AS active
                    JOIN policy_versions AS versions
                      ON versions.organization_id = active.organization_id
                     AND versions.version_id = active.version_id
                    WHERE active.organization_id = %s
                    """,
                    (organization_id,),
                )
                row = cursor.fetchone()
        if row is None:
            raise PostgresStorageError("policy_active_version_unavailable")
        return _version_from_row(row, config=self._config)

    def _transaction(self, organization_id: str) -> Iterator[Any]:
        return postgres_transaction(
            self._dsn,
            schema=self._schema,
            runtime_role=self._runtime_role,
            organization_id=organization_id,
            connection_pool=self._connection_pool,
        )


class PostgresPolicyControlStore(PostgresPolicyRuntimeStore):
    """Policy-service write path using the dedicated policy-control database role."""

    def __init__(
        self,
        dsn: str,
        *,
        config: GatewayConfig,
        schema: str,
        policy_control_role: str,
        connection_pool: PostgresConnectionPool | None = None,
    ) -> None:
        self._dsn = dsn
        self._config = config
        self._schema = schema
        self._runtime_role = policy_control_role
        self._connection_pool = connection_pool
        verify_postgres_schema(
            dsn,
            schema=schema,
            runtime_role=policy_control_role,
            connection_pool=self._connection_pool,
        )

    def is_initialized(self, *, organization_id: str) -> bool:
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM policy_tenants WHERE organization_id = %s",
                    (organization_id,),
                )
                return cursor.fetchone() is not None

    def bootstrap(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        administrators: tuple[PolicyAdministrator, ...],
    ) -> tuple[PolicyAdministrator, ...]:
        if not administrators:
            raise PolicyControlError("policy_bootstrap_administrators_required")
        _require_organization(caller, organization_id)
        administrator_keys: set[str] = set()
        for administrator in administrators:
            _require_organization(administrator, organization_id)
            identity_key = _identity_key(administrator)
            if identity_key in administrator_keys:
                raise PolicyControlError("policy_bootstrap_administrator_duplicate")
            administrator_keys.add(identity_key)
        if _identity_key(caller) not in administrator_keys:
            raise PolicyControlError("policy_bootstrap_caller_not_administrator")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                cursor.execute("SELECT 1 FROM policy_tenants WHERE organization_id = %s", (organization_id,))
                if cursor.fetchone() is not None:
                    raise PolicyControlError("policy_bootstrap_already_initialized")
                caller_key = _identity_key(caller)
                cursor.execute(
                    """
                    INSERT INTO policy_tenants (
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
                    change_summary={"summary_version": 1, "bootstrap_administrator_count": len(administrators)},
                )
        return administrators

    def stage(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        document: PolicyDocument,
    ) -> PolicyVersionRecord:
        if document.organization_id != organization_id:
            raise PolicyControlError("policy_document_organization_mismatch")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                cursor.execute(
                    """
                    INSERT INTO policy_versions (
                        organization_id, version_id, content_sha256, document_json, change_summary,
                        created_at, author_kind, author_identity_key
                    ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                    ON CONFLICT (organization_id, content_sha256) DO NOTHING
                    RETURNING organization_id, version_id, content_sha256, document_json, change_summary,
                              created_at, author_kind, author_identity_key
                    """,
                    (
                        organization_id,
                        document.version_id,
                        document.content_sha256,
                        document.canonical_json,
                        json.dumps(document.redacted_change_summary(), sort_keys=True, separators=(",", ":")),
                        now,
                        caller.authentication_kind,
                        _identity_key(caller),
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    cursor.execute(
                        """
                        SELECT organization_id, version_id, content_sha256, document_json, change_summary,
                               created_at, author_kind, author_identity_key
                        FROM policy_versions
                        WHERE organization_id = %s AND content_sha256 = %s
                        """,
                        (organization_id, document.content_sha256),
                    )
                    row = cursor.fetchone()
                    if row is None:  # pragma: no cover - protected by unique constraint
                        raise PostgresStorageError("policy_version_unavailable")
                    return _version_from_row(row, config=self._config)
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    event_type="policy_staged",
                    actor=caller,
                    version_id=document.version_id,
                    change_summary=document.redacted_change_summary(),
                )
        return _version_from_row(row, config=self._config)

    def activate(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        version_id: str,
    ) -> PolicyActivation:
        return self._set_active_version(
            organization_id=organization_id,
            caller=caller,
            version_id=version_id,
            event_type="policy_activated",
            require_previously_active=False,
        )

    def rollback(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        version_id: str,
    ) -> PolicyActivation:
        return self._set_active_version(
            organization_id=organization_id,
            caller=caller,
            version_id=version_id,
            event_type="policy_rolled_back",
            require_previously_active=True,
        )

    def grant_administrator(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        administrator: PolicyAdministrator,
    ) -> PolicyAdministrator:
        _require_organization(administrator, organization_id)
        if administrator.authentication_kind != "oidc":
            raise PolicyControlError("policy_static_administrator_grant_denied")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                identity_key = _identity_key(administrator)
                cursor.execute(
                    """
                    SELECT active FROM policy_administrators
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
                        UPDATE policy_administrators
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
        caller: PolicyAdministrator,
        administrator: PolicyAdministrator,
    ) -> None:
        _require_organization(administrator, organization_id)
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                target_key = _identity_key(administrator)
                cursor.execute(
                    """
                    SELECT active FROM policy_administrators
                    WHERE organization_id = %s AND identity_key = %s
                    FOR UPDATE
                    """,
                    (organization_id, target_key),
                )
                target = cursor.fetchone()
                if target is None or not bool(target["active"]):
                    raise PolicyControlError("policy_administrator_not_found")
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM policy_administrators WHERE organization_id = %s AND active = TRUE",
                    (organization_id,),
                )
                active_count = int(cursor.fetchone()["count"])
                if active_count <= 1:
                    raise PolicyControlError("policy_last_administrator_revoke_denied")
                cursor.execute(
                    """
                    UPDATE policy_administrators
                    SET active = FALSE,
                        revoked_at = %s,
                        revoked_by_kind = %s,
                        revoked_by_identity_key = %s
                    WHERE organization_id = %s AND identity_key = %s
                    """,
                    (now, caller.authentication_kind, _identity_key(caller), organization_id, target_key),
                )
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    event_type="administrator_revoked",
                    actor=caller,
                    target_identity_key=target_key,
                )

    def break_glass_recover(
        self,
        *,
        organization_id: str,
        administrator: PolicyAdministrator,
        reason_code: str,
    ) -> PolicyAdministrator:
        _require_organization(administrator, organization_id)
        if administrator.authentication_kind != "oidc":
            raise PolicyControlError("policy_break_glass_oidc_required")
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                cursor.execute("SELECT 1 FROM policy_tenants WHERE organization_id = %s", (organization_id,))
                if cursor.fetchone() is None:
                    raise PolicyControlError("policy_bootstrap_required")
                cursor.execute(
                    "SELECT COUNT(*) AS count FROM policy_administrators WHERE organization_id = %s AND active = TRUE",
                    (organization_id,),
                )
                if int(cursor.fetchone()["count"]) != 0:
                    raise PolicyControlError("policy_break_glass_not_required")
                identity_key = _identity_key(administrator)
                cursor.execute(
                    "SELECT 1 FROM policy_administrators WHERE organization_id = %s AND identity_key = %s",
                    (organization_id, identity_key),
                )
                if cursor.fetchone() is None:
                    self._insert_administrator(
                        cursor,
                        administrator=administrator,
                        created_at=now,
                        created_by=None,
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE policy_administrators
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
                    event_type="break_glass_recovered",
                    actor=None,
                    target_identity_key=identity_key,
                    reason_code=reason_code,
                )
        return administrator

    def status(self, *, organization_id: str, caller: PolicyAdministrator) -> PolicyControlStatus:
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                cursor.execute("SELECT 1 FROM policy_tenants WHERE organization_id = %s", (organization_id,))
                initialized = cursor.fetchone() is not None
                cursor.execute(
                    """
                    SELECT organization_id, version_id, content_sha256, document_json, change_summary,
                           created_at, author_kind, author_identity_key
                    FROM policy_versions
                    WHERE organization_id = %s
                    ORDER BY created_at DESC, version_id DESC
                    """,
                    (organization_id,),
                )
                versions = tuple(_version_from_row(row, config=self._config) for row in cursor.fetchall())
                cursor.execute(
                    """
                    SELECT organization_id, version_id, generation, activated_at,
                           activated_by_kind, activated_by_identity_key
                    FROM policy_active_versions
                    WHERE organization_id = %s
                    """,
                    (organization_id,),
                )
                active_row = cursor.fetchone()
                active = _activation_from_row(active_row, action="policy_activated") if active_row else None
                cursor.execute(
                    """
                    SELECT organization_id, authentication_kind, actor_id, issuer, subject
                    FROM policy_administrators
                    WHERE organization_id = %s AND active = TRUE
                    ORDER BY authentication_kind, actor_id NULLS LAST, issuer NULLS LAST, subject NULLS LAST
                    """,
                    (organization_id,),
                )
                administrators = tuple(_administrator_from_row(row) for row in cursor.fetchall())
        return PolicyControlStatus(
            organization_id=organization_id,
            initialized=initialized,
            active=active,
            versions=versions,
            administrators=administrators,
        )

    def _set_active_version(
        self,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
        version_id: str,
        event_type: str,
        require_previously_active: bool,
    ) -> PolicyActivation:
        now = datetime.now(timezone.utc)
        with self._transaction(organization_id) as connection:
            with connection.cursor() as cursor:
                self._lock_tenant(cursor, organization_id)
                self._require_administrator(cursor, organization_id=organization_id, caller=caller)
                cursor.execute(
                    "SELECT 1 FROM policy_versions WHERE organization_id = %s AND version_id = %s",
                    (organization_id, version_id),
                )
                if cursor.fetchone() is None:
                    raise PolicyControlError("policy_version_not_found")
                if require_previously_active:
                    cursor.execute(
                        """
                        SELECT 1 FROM policy_control_events
                        WHERE organization_id = %s
                          AND version_id = %s
                          AND event_type IN ('policy_activated', 'policy_rolled_back')
                        """,
                        (organization_id, version_id),
                    )
                    if cursor.fetchone() is None:
                        raise PolicyControlError("policy_rollback_target_not_previously_active")
                cursor.execute(
                    """
                    SELECT organization_id, version_id, generation, activated_at,
                           activated_by_kind, activated_by_identity_key
                    FROM policy_active_versions
                    WHERE organization_id = %s
                    FOR UPDATE
                    """,
                    (organization_id,),
                )
                existing = cursor.fetchone()
                if existing is not None and str(existing["version_id"]) == version_id:
                    return _activation_from_row(existing, action=event_type)
                generation = 1 if existing is None else int(existing["generation"]) + 1
                cursor.execute(
                    """
                    INSERT INTO policy_active_versions (
                        organization_id, version_id, generation, activated_at,
                        activated_by_kind, activated_by_identity_key
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (organization_id) DO UPDATE
                    SET version_id = EXCLUDED.version_id,
                        generation = EXCLUDED.generation,
                        activated_at = EXCLUDED.activated_at,
                        activated_by_kind = EXCLUDED.activated_by_kind,
                        activated_by_identity_key = EXCLUDED.activated_by_identity_key
                    """,
                    (
                        organization_id,
                        version_id,
                        generation,
                        now,
                        caller.authentication_kind,
                        _identity_key(caller),
                    ),
                )
                activation = PolicyActivation(
                    organization_id=organization_id,
                    version_id=version_id,
                    generation=generation,
                    activated_at=now,
                    activated_by_kind=caller.authentication_kind,
                    activated_by_identity_key=_identity_key(caller),
                    action=event_type,
                )
                self._record_event(
                    cursor,
                    organization_id=organization_id,
                    event_type=event_type,
                    actor=caller,
                    version_id=version_id,
                    generation=generation,
                )
        return activation

    def _lock_tenant(self, cursor: Any, organization_id: str) -> None:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"hormuz:policy:{organization_id}",))

    def _require_administrator(
        self,
        cursor: Any,
        *,
        organization_id: str,
        caller: PolicyAdministrator,
    ) -> None:
        _require_organization(caller, organization_id)
        cursor.execute(
            """
            SELECT 1 FROM policy_administrators
            WHERE organization_id = %s AND identity_key = %s AND active = TRUE
            """,
            (organization_id, _identity_key(caller)),
        )
        if cursor.fetchone() is None:
            raise PolicyControlError("policy_administrator_required")

    def _insert_administrator(
        self,
        cursor: Any,
        *,
        administrator: PolicyAdministrator,
        created_at: datetime,
        created_by: PolicyAdministrator | None,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO policy_administrators (
                organization_id, identity_key, authentication_kind, actor_id, issuer, subject,
                active, created_at, created_by_kind, created_by_identity_key
            ) VALUES (%s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s)
            """,
            (
                administrator.organization_id,
                _identity_key(administrator),
                administrator.authentication_kind,
                administrator.actor_id,
                administrator.issuer,
                administrator.subject,
                created_at,
                created_by.authentication_kind if created_by is not None else "break_glass",
                _identity_key(created_by) if created_by is not None else "break_glass",
            ),
        )

    def _record_event(
        self,
        cursor: Any,
        *,
        organization_id: str,
        event_type: str,
        actor: PolicyAdministrator | None,
        target_identity_key: str | None = None,
        version_id: str | None = None,
        generation: int | None = None,
        reason_code: str | None = None,
        change_summary: dict[str, object] | None = None,
    ) -> None:
        event = {
            "event_schema_id": POLICY_CONTROL_EVENT_SCHEMA_ID,
            "event_schema_version": POLICY_CONTROL_EVENT_SCHEMA_VERSION,
            "organization_id": organization_id,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "actor_kind": actor.authentication_kind if actor is not None else "break_glass",
            "actor_identity_key": _identity_key(actor) if actor is not None else "break_glass",
            "target_identity_key": target_identity_key,
            "version_id": version_id,
            "generation": generation,
            "reason_code": reason_code,
            "change_summary": change_summary,
        }
        try:
            validate_policy_control_event(event)
        except ContractValidationError as error:
            raise PolicyControlError("policy_control_event_invalid") from error
        cursor.execute(
            """
            INSERT INTO policy_control_events (
                event_id, event_schema_id, event_schema_version, organization_id, occurred_at, event_type, actor_kind, actor_identity_key,
                target_identity_key, version_id, generation, reason_code, change_summary
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
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
                event["version_id"],
                event["generation"],
                event["reason_code"],
                json.dumps(event["change_summary"], sort_keys=True, separators=(",", ":"))
                if event["change_summary"] is not None
                else None,
            ),
        )


def _require_organization(administrator: PolicyAdministrator, organization_id: str) -> None:
    if administrator.organization_id != organization_id:
        raise PolicyControlError("policy_organization_mismatch")


def _administrator_from_row(row: dict[str, object]) -> PolicyAdministrator:
    try:
        return PolicyAdministrator(
            organization_id=str(row["organization_id"]),
            authentication_kind=str(row["authentication_kind"]),
            actor_id=str(row["actor_id"]) if row.get("actor_id") is not None else None,
            issuer=str(row["issuer"]) if row.get("issuer") is not None else None,
            subject=str(row["subject"]) if row.get("subject") is not None else None,
        )
    except ValueError as error:
        raise PostgresStorageError("policy_document_invalid") from error


def _version_from_row(row: dict[str, object], *, config: GatewayConfig) -> PolicyVersionRecord:
    raw_document = row["document_json"]
    if isinstance(raw_document, str):
        try:
            raw_document = json.loads(raw_document)
        except json.JSONDecodeError as error:
            raise PostgresStorageError("policy_document_invalid") from error
    if not isinstance(raw_document, dict):
        raise PostgresStorageError("policy_document_invalid")
    try:
        document = PolicyDocument.from_mapping(raw_document, config=config)
    except PolicyDocumentError as error:
        raise PostgresStorageError(error.code) from None
    raw_summary = row["change_summary"]
    if isinstance(raw_summary, str):
        try:
            raw_summary = json.loads(raw_summary)
        except json.JSONDecodeError as error:
            raise PostgresStorageError("policy_document_invalid") from error
    if not isinstance(raw_summary, dict):
        raise PostgresStorageError("policy_document_invalid")
    try:
        validate_redacted_change_summary(raw_summary)
    except PolicyDocumentError as error:
        raise PostgresStorageError(error.code) from None
    if str(row["version_id"]) != document.version_id or str(row["content_sha256"]) != document.content_sha256:
        raise PostgresStorageError("policy_document_invalid")
    if str(row["organization_id"]) != document.organization_id:
        raise PostgresStorageError("policy_document_invalid")
    created_at = row["created_at"]
    if not isinstance(created_at, datetime):
        raise PostgresStorageError("policy_document_invalid")
    author_kind = str(row["author_kind"])
    author_identity_key = str(row["author_identity_key"])
    if not _is_opaque_identity_key(author_kind, author_identity_key):
        raise PostgresStorageError("policy_document_invalid")
    return PolicyVersionRecord(
        organization_id=str(row["organization_id"]),
        version_id=str(row["version_id"]),
        content_sha256=str(row["content_sha256"]),
        created_at=created_at,
        author_kind=author_kind,
        author_identity_key=author_identity_key,
        change_summary=raw_summary,
        document=document,
    )


def _activation_from_row(row: dict[str, object], *, action: str) -> PolicyActivation:
    activated_at = row["activated_at"]
    if not isinstance(activated_at, datetime):
        raise PostgresStorageError("policy_document_invalid")
    activated_by_kind = str(row["activated_by_kind"])
    activated_by_identity_key = str(row["activated_by_identity_key"])
    if not _is_opaque_identity_key(activated_by_kind, activated_by_identity_key):
        raise PostgresStorageError("policy_document_invalid")
    generation = int(row["generation"])
    if generation < 1:
        raise PostgresStorageError("policy_document_invalid")
    return PolicyActivation(
        organization_id=str(row["organization_id"]),
        version_id=str(row["version_id"]),
        generation=generation,
        activated_at=activated_at,
        activated_by_kind=activated_by_kind,
        activated_by_identity_key=activated_by_identity_key,
        action=action,
    )


def _is_opaque_identity_key(kind: str, value: str) -> bool:
    prefix = f"{kind}:"
    digest = value.removeprefix(prefix)
    return (
        kind in {"static", "oidc"}
        and value.startswith(prefix)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )
