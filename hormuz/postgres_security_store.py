"""Multi-instance PostgreSQL DLP approval and security evidence repository."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid
from typing import Iterator

from .config import Identity
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
    DLPApprovalRequest,
    DLPApprovalResult,
    DLPApprovalStoreError,
    DLPApprovalTotals,
    SecretTotals,
    SecurityStoreError,
    _approval_binding,
    _approval_request_id,
    _sanitize_dlp_findings,
    _sqlite_nonnegative,
)


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json_array(value: object) -> list[object]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise DLPApprovalStoreError("approval_store_corrupt") from None
        if isinstance(decoded, list):
            return decoded
    raise DLPApprovalStoreError("approval_store_corrupt")


class PostgresSecurityStore:
    """Tenant-scoped security state with atomic, one-time approval consumption."""

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
        normalized = tuple(sorted(set(organization_ids)))
        if not normalized:
            raise PostgresStorageError("postgres_tenant_set_empty")
        for organization_id in normalized:
            validate_tenant_id(organization_id)
        self._dsn = dsn
        self.organization_ids = normalized
        self.schema = validate_postgres_identifier(schema, "postgres_schema")
        self.runtime_role = validate_postgres_identifier(runtime_role, "runtime_role")
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

    def _approval_organization(self, organization_id: str) -> str:
        try:
            return self._organization(organization_id)
        except (PostgresStorageError, ValueError):
            raise DLPApprovalStoreError("approval_request_not_found") from None

    @contextmanager
    def _transaction(
        self,
        organization_id: str,
        *,
        principal_id: str,
        client_id: str,
        approval: bool = False,
    ) -> Iterator[object]:
        connection = None
        try:
            connection = _open_connection(self._dsn, self._connect)  # type: ignore[arg-type]
            context = TenantContext(organization_id, principal_id, client_id, 1)
            with tenant_transaction(
                connection,
                context,
                runtime_role=self.runtime_role,
                schema=self.schema,
            ):
                with connection.cursor() as cursor:  # type: ignore[attr-defined]
                    cursor.execute(f"SET LOCAL search_path TO {self._qualified}, pg_catalog")
                yield connection
        except DLPApprovalStoreError:
            raise
        except SecurityStoreError:
            raise
        except Exception:
            code = "approval_store_unavailable" if approval else "security_store_unavailable"
            error_type = DLPApprovalStoreError if approval else SecurityStoreError
            raise error_type(code) from None
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _dict_cursor(connection: object):
        try:
            from psycopg.rows import dict_row  # type: ignore[import-not-found]
        except ImportError:
            raise PostgresStorageError("postgres_driver_unavailable") from None
        return connection.cursor(row_factory=dict_row)  # type: ignore[attr-defined]

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
        if action not in {
            "detected",
            "redacted",
            "denied",
            "approval_required",
            "approved",
        }:
            raise ValueError("Unsupported DLP event action")
        normalized = _sanitize_dlp_findings(findings)
        return self._record_security_event(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            routed_model=routed_model,
            action=action,
            detection_count=sum(int(item["count"]) for item in normalized),
            redaction_count=redaction_count,
            rules=tuple(str(item["rule_id"]) for item in normalized),
            event_type="security.dlp",
            policy_version=policy_version,
            findings=normalized,
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
        organization_id = self._organization(identity.organization_id)
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
        with self._transaction(
            organization_id,
            principal_id=identity.actor_id,
            client_id=client,
        ) as connection:
            with connection.cursor() as cursor:  # type: ignore[attr-defined]
                cursor.execute(
                    "INSERT INTO gateway_secret_events ("
                    "tenant_id, id, occurred_at, actor_id, actor_name, team_id, team_name, "
                    "client, protocol, requested_model, routed_model, action, detection_count, "
                    "redaction_count, rules_json, event_type, policy_version, findings_json"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s::jsonb, %s, %s, %s::jsonb)",
                    (
                        organization_id,
                        event_id,
                        datetime.now(timezone.utc),
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
                        json.dumps(findings, sort_keys=True, separators=(",", ":")),
                    ),
                )
        return event_id

    @staticmethod
    def _expire(cursor: object, organization_id: str, now: datetime) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "UPDATE gateway_dlp_approval_requests SET status = 'expired', updated_at = %s "
            "WHERE tenant_id = %s AND status IN ('pending', 'approved') AND expires_at <= %s",
            (now, organization_id, now),
        )

    @staticmethod
    def _request(row: dict[str, object]) -> DLPApprovalRequest:
        rules = _json_array(row["rules_json"])
        if any(not isinstance(rule, str) for rule in rules):
            raise DLPApprovalStoreError("approval_store_corrupt")
        return DLPApprovalRequest(
            request_id=str(row["id"]),
            created_at=_iso(row["created_at"]),
            updated_at=_iso(row["updated_at"]),
            expires_at=_iso(row["expires_at"]),
            organization_id=str(row["tenant_id"]),
            actor_id=str(row["actor_id"]),
            actor_name=str(row["actor_name"]),
            team_id=str(row["team_id"]),
            team_name=str(row["team_name"]),
            client=str(row["client"]),
            protocol=str(row["protocol"]),
            requested_model=str(row["requested_model"]),
            routed_model=str(row["routed_model"]),
            policy_version=str(row["policy_version"]),
            rules=tuple(rules),  # type: ignore[arg-type]
            detection_count=int(row["detection_count"]),
            status=str(row["status"]),
            approved_by_actor_id=(
                str(row["approved_by_actor_id"])
                if row["approved_by_actor_id"] is not None
                else None
            ),
            approved_by_actor_name=(
                str(row["approved_by_actor_name"])
                if row["approved_by_actor_name"] is not None
                else None
            ),
            approved_at=_iso(row["approved_at"]) if row["approved_at"] is not None else None,
            consumed_at=_iso(row["consumed_at"]) if row["consumed_at"] is not None else None,
        )

    @staticmethod
    def _record_approval_event(
        cursor: object,
        *,
        request: dict[str, object],
        action: str,
        occurred_at: datetime,
        decision_actor_id: str | None = None,
        decision_actor_name: str | None = None,
        actual_model: str | None = None,
    ) -> None:
        if action not in {"requested", "approved", "consumed", "model_mismatch"}:
            raise ValueError("Unsupported DLP approval event action")
        cursor.execute(  # type: ignore[attr-defined]
            "INSERT INTO gateway_dlp_approval_events ("
            "tenant_id, id, occurred_at, request_id, actor_id, actor_name, team_id, team_name, "
            "decision_actor_id, decision_actor_name, client, protocol, requested_model, "
            "routed_model, actual_model, policy_version, rules_json, action"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s::jsonb, %s)",
            (
                request["tenant_id"],
                str(uuid.uuid4()),
                occurred_at,
                request["id"],
                request["actor_id"],
                request["actor_name"],
                request["team_id"],
                request["team_name"],
                decision_actor_id,
                decision_actor_name,
                request["client"],
                request["protocol"],
                request["requested_model"],
                request["routed_model"],
                actual_model,
                request["policy_version"],
                json.dumps(_json_array(request["rules_json"]), separators=(",", ":")),
                action,
            ),
        )

    def authorize_or_request_dlp_approval(
        self,
        *,
        identity: Identity,
        client: str,
        protocol: str,
        requested_model: str,
        routed_model: str,
        policy_version: str,
        payload_fingerprint: str,
        rules: tuple[str, ...],
        detection_count: int,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> DLPApprovalResult:
        organization_id = self._organization(identity.organization_id)
        binding = _approval_binding(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            routed_model=routed_model,
            policy_version=policy_version,
            payload_fingerprint=payload_fingerprint,
            rules=rules,
            detection_count=detection_count,
            ttl_seconds=ttl_seconds,
        )
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        binding_values = (
            organization_id,
            identity.actor_id,
            client,
            protocol,
            requested_model,
            routed_model,
            policy_version,
            payload_fingerprint,
            binding["rules_json"],
        )
        lock_digest = hashlib.sha256(
            json.dumps(binding_values, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self._transaction(
            organization_id,
            principal_id=identity.actor_id,
            client_id=client,
            approval=True,
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("hormuz:dlp-approval:" + lock_digest,),
                )
                self._expire(cursor, organization_id, current)
                cursor.execute(
                    "SELECT * FROM gateway_dlp_approval_requests WHERE tenant_id = %s "
                    "AND actor_id = %s AND client = %s AND protocol = %s "
                    "AND requested_model = %s AND routed_model = %s AND policy_version = %s "
                    "AND payload_fingerprint = %s AND rules_json = %s::jsonb "
                    "AND status = 'approved' AND expires_at > %s "
                    "ORDER BY approved_at, id LIMIT 1 FOR UPDATE",
                    (*binding_values[:-1], binding_values[-1], current),
                )
                approved = cursor.fetchone()
                if approved is not None:
                    cursor.execute(
                        "UPDATE gateway_dlp_approval_requests SET status = 'consumed', "
                        "updated_at = %s, consumed_at = %s WHERE tenant_id = %s AND id = %s "
                        "AND status = 'approved' AND expires_at > %s RETURNING *",
                        (current, current, organization_id, approved["id"], current),
                    )
                    consumed = cursor.fetchone()
                    if consumed is None:
                        raise DLPApprovalStoreError("approval_replay_rejected")
                    self._record_approval_event(
                        cursor,
                        request=consumed,
                        action="consumed",
                        occurred_at=current,
                        decision_actor_id=str(consumed["approved_by_actor_id"]),
                        decision_actor_name=str(consumed["approved_by_actor_name"]),
                    )
                    return DLPApprovalResult(
                        request_id=str(consumed["id"]),
                        status="consumed",
                        expires_at=_iso(consumed["expires_at"]),
                    )
                cursor.execute(
                    "SELECT * FROM gateway_dlp_approval_requests WHERE tenant_id = %s "
                    "AND actor_id = %s AND client = %s AND protocol = %s "
                    "AND requested_model = %s AND routed_model = %s AND policy_version = %s "
                    "AND payload_fingerprint = %s AND rules_json = %s::jsonb "
                    "AND status = 'pending' AND expires_at > %s "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    (*binding_values[:-1], binding_values[-1], current),
                )
                pending = cursor.fetchone()
                if pending is not None:
                    return DLPApprovalResult(
                        request_id=str(pending["id"]),
                        status="pending",
                        expires_at=_iso(pending["expires_at"]),
                    )
                request_id = "apr_" + uuid.uuid4().hex
                expires_at = current + timedelta(seconds=ttl_seconds)
                cursor.execute(
                    "INSERT INTO gateway_dlp_approval_requests ("
                    "tenant_id, id, created_at, updated_at, expires_at, actor_id, actor_name, "
                    "team_id, team_name, client, protocol, requested_model, routed_model, "
                    "policy_version, payload_fingerprint, rules_json, detection_count, status"
                    ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s::jsonb, %s, 'pending') RETURNING *",
                    (
                        organization_id,
                        request_id,
                        current,
                        current,
                        expires_at,
                        identity.actor_id,
                        identity.actor_name,
                        identity.team_id,
                        identity.team_name,
                        client,
                        protocol,
                        requested_model,
                        routed_model,
                        policy_version,
                        payload_fingerprint,
                        binding["rules_json"],
                        detection_count,
                    ),
                )
                created = cursor.fetchone()
                self._record_approval_event(
                    cursor,
                    request=created,
                    action="requested",
                    occurred_at=current,
                )
                return DLPApprovalResult(request_id, "pending", _iso(expires_at))

    def get_dlp_approval_request(
        self,
        request_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> DLPApprovalRequest:
        _approval_request_id(request_id)
        organization = self._approval_organization(organization_id)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        result: DLPApprovalRequest | None = None
        with self._transaction(
            organization,
            principal_id="dlp-approval-reader",
            client_id="hormuz-admin",
            approval=True,
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                self._expire(cursor, organization, current)
                cursor.execute(
                    "SELECT * FROM gateway_dlp_approval_requests "
                    "WHERE tenant_id = %s AND id = %s",
                    (organization, request_id),
                )
                row = cursor.fetchone()
                if row is not None:
                    result = self._request(row)
        if result is None:
            raise DLPApprovalStoreError("approval_request_not_found")
        return result

    def approve_dlp_approval_request(
        self,
        request_id: str,
        *,
        approver: Identity,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> DLPApprovalRequest:
        _approval_request_id(request_id)
        if "dlp_approver" not in approver.capabilities:
            raise DLPApprovalStoreError("approval_capability_required")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 900:
            raise ValueError("DLP approval TTL must be between 1 and 900 seconds")
        organization = self._approval_organization(approver.organization_id)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        expires_at = current + timedelta(seconds=ttl_seconds)
        result: DLPApprovalRequest | None = None
        decision_error: str | None = None
        with self._transaction(
            organization,
            principal_id=approver.actor_id,
            client_id="hormuz-admin",
            approval=True,
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                self._expire(cursor, organization, current)
                cursor.execute(
                    "SELECT * FROM gateway_dlp_approval_requests "
                    "WHERE tenant_id = %s AND id = %s FOR UPDATE",
                    (organization, request_id),
                )
                row = cursor.fetchone()
                if row is None:
                    decision_error = "approval_request_not_found"
                elif row["actor_id"] == approver.actor_id:
                    decision_error = "approval_self_approval_forbidden"
                elif row["status"] == "approved":
                    if row["approved_by_actor_id"] == approver.actor_id:
                        result = self._request(row)
                    else:
                        decision_error = "approval_request_already_decided"
                elif row["status"] != "pending":
                    decision_error = "approval_request_not_approvable"
                else:
                    cursor.execute(
                        "UPDATE gateway_dlp_approval_requests SET status = 'approved', "
                        "updated_at = %s, expires_at = %s, approved_by_actor_id = %s, "
                        "approved_by_actor_name = %s, approved_at = %s "
                        "WHERE tenant_id = %s AND id = %s AND status = 'pending' RETURNING *",
                        (
                            current,
                            expires_at,
                            approver.actor_id,
                            approver.actor_name,
                            current,
                            organization,
                            request_id,
                        ),
                    )
                    approved = cursor.fetchone()
                    if approved is None:
                        decision_error = "approval_request_already_decided"
                    else:
                        self._record_approval_event(
                            cursor,
                            request=approved,
                            action="approved",
                            occurred_at=current,
                            decision_actor_id=approver.actor_id,
                            decision_actor_name=approver.actor_name,
                        )
                        result = self._request(approved)
        if decision_error is not None:
            raise DLPApprovalStoreError(decision_error)
        if result is None:  # pragma: no cover - every locked row takes one branch
            raise DLPApprovalStoreError("approval_store_unavailable")
        return result

    def record_dlp_approval_model_mismatch(
        self,
        request_id: str,
        *,
        organization_id: str,
        actual_model: str,
        now: datetime | None = None,
    ) -> None:
        _approval_request_id(request_id)
        if (
            not isinstance(actual_model, str)
            or not actual_model
            or len(actual_model.encode("utf-8")) > 128
            or any(
                not (character.isalnum() or character in {"-", "_", ".", ":", "/"})
                for character in actual_model
            )
        ):
            raise ValueError("DLP approval actual model must be a bounded single-line string")
        organization = self._approval_organization(organization_id)
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        found = False
        with self._transaction(
            organization,
            principal_id="dlp-model-verifier",
            client_id="hormuz-gateway",
            approval=True,
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    "SELECT * FROM gateway_dlp_approval_requests "
                    "WHERE tenant_id = %s AND id = %s",
                    (organization, request_id),
                )
                request = cursor.fetchone()
                if request is not None:
                    found = True
                    self._record_approval_event(
                        cursor,
                        request=request,
                        action="model_mismatch",
                        occurred_at=current,
                        actual_model=actual_model,
                    )
        if not found:
            raise DLPApprovalStoreError("approval_request_not_found")

    def monthly_secret_totals(
        self,
        *,
        organization_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> SecretTotals:
        organization = self._organization(organization_id)
        start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        clauses = ["tenant_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [organization, start]
        if actor_id is not None:
            clauses.append("actor_id = %s")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = %s")
            parameters.append(team_id)
        with self._transaction(
            organization,
            principal_id="security-reporter",
            client_id="hormuz-status",
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    "SELECT COUNT(*) AS events, COALESCE(SUM(detection_count), 0) AS detections, "
                    "COALESCE(SUM(CASE WHEN event_type = 'security.dlp' THEN 1 ELSE 0 END), 0) AS dlp_events, "
                    "COALESCE(SUM(CASE WHEN event_type = 'security.dlp' THEN detection_count ELSE 0 END), 0) AS dlp_detections, "
                    "COALESCE(SUM(CASE WHEN action = 'detected' THEN 1 ELSE 0 END), 0) AS detected_requests, "
                    "COALESCE(SUM(CASE WHEN action = 'redacted' THEN 1 ELSE 0 END), 0) AS redacted_requests, "
                    "COALESCE(SUM(CASE WHEN action = 'denied' THEN 1 ELSE 0 END), 0) AS denied_requests, "
                    "COALESCE(SUM(CASE WHEN action = 'approval_required' THEN 1 ELSE 0 END), 0) AS approval_required_requests "
                    "FROM gateway_secret_events WHERE " + " AND ".join(clauses),
                    parameters,
                )
                row = cursor.fetchone()
                return SecretTotals(**{key: int(value) for key, value in row.items()})

    def monthly_dlp_approval_totals(
        self,
        *,
        organization_id: str | None = None,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> DLPApprovalTotals:
        organization = self._organization(organization_id)
        start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        clauses = ["tenant_id = %s", "occurred_at >= %s"]
        parameters: list[object] = [organization, start]
        if actor_id is not None:
            clauses.append("actor_id = %s")
            parameters.append(actor_id)
        if team_id is not None:
            clauses.append("team_id = %s")
            parameters.append(team_id)
        with self._transaction(
            organization,
            principal_id="approval-reporter",
            client_id="hormuz-status",
            approval=True,
        ) as connection:
            with self._dict_cursor(connection) as cursor:
                cursor.execute(
                    "SELECT COALESCE(SUM(CASE WHEN action = 'requested' THEN 1 ELSE 0 END), 0) AS requests, "
                    "COALESCE(SUM(CASE WHEN action = 'approved' THEN 1 ELSE 0 END), 0) AS approved, "
                    "COALESCE(SUM(CASE WHEN action = 'consumed' THEN 1 ELSE 0 END), 0) AS consumed, "
                    "COALESCE(SUM(CASE WHEN action = 'model_mismatch' THEN 1 ELSE 0 END), 0) AS model_mismatches "
                    "FROM gateway_dlp_approval_events WHERE " + " AND ".join(clauses),
                    parameters,
                )
                row = cursor.fetchone()
                return DLPApprovalTotals(**{key: int(value) for key, value in row.items()})

    def audit_events(self, *, since: str, kind: str = "all") -> list[dict[str, object]]:
        if kind not in {"all", "usage", "security"}:
            raise ValueError(f"Unsupported audit event kind: {kind}")
        if kind == "usage":
            return []
        events: list[dict[str, object]] = []
        for organization in self.organization_ids:
            with self._transaction(
                organization,
                principal_id="security-auditor",
                client_id="audit-export",
            ) as connection:
                with self._dict_cursor(connection) as cursor:
                    cursor.execute(
                        "SELECT id, occurred_at, tenant_id AS organization_id, actor_id, "
                        "actor_name, team_id, team_name, client, protocol, requested_model, "
                        "routed_model, action, detection_count, redaction_count, rules_json, "
                        "event_type, policy_version, findings_json FROM gateway_secret_events "
                        "WHERE tenant_id = %s AND occurred_at >= %s ORDER BY occurred_at, id",
                        (organization, since),
                    )
                    for row in cursor.fetchall():
                        event = dict(row)
                        event["occurred_at"] = _iso(event["occurred_at"])
                        event["rules"] = event.pop("rules_json")
                        event["findings"] = event.pop("findings_json")
                        event_type = str(event.pop("event_type"))
                        events.append({"schema_version": 1, "event_type": event_type, **event})
                    cursor.execute(
                        "SELECT id, occurred_at, request_id, tenant_id AS organization_id, "
                        "actor_id, actor_name, team_id, team_name, decision_actor_id, "
                        "decision_actor_name, client, protocol, requested_model, routed_model, "
                        "actual_model, policy_version, rules_json, action "
                        "FROM gateway_dlp_approval_events WHERE tenant_id = %s "
                        "AND occurred_at >= %s ORDER BY occurred_at, id",
                        (organization, since),
                    )
                    for row in cursor.fetchall():
                        event = dict(row)
                        event["occurred_at"] = _iso(event["occurred_at"])
                        event["rules"] = event.pop("rules_json")
                        events.append(
                            {
                                "schema_version": 1,
                                "event_type": "security.dlp.approval",
                                **event,
                            }
                        )
        events.sort(key=lambda event: (str(event["occurred_at"]), str(event["id"])))
        return events
