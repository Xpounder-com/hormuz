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
class DLPApprovalResult:
    request_id: str
    status: str
    expires_at: str

    @property
    def authorized(self) -> bool:
        return self.status == "consumed"


@dataclass(frozen=True)
class DLPApprovalRequest:
    request_id: str
    created_at: str
    updated_at: str
    expires_at: str
    organization_id: str
    actor_id: str
    actor_name: str
    team_id: str
    team_name: str
    client: str
    protocol: str
    requested_model: str
    routed_model: str
    policy_version: str
    rules: tuple[str, ...]
    detection_count: int
    status: str
    approved_by_actor_id: str | None = None
    approved_by_actor_name: str | None = None
    approved_at: str | None = None
    consumed_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "organization_id": self.organization_id,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "client": self.client,
            "protocol": self.protocol,
            "requested_model": self.requested_model,
            "routed_model": self.routed_model,
            "policy_version": self.policy_version,
            "rules": list(self.rules),
            "detection_count": self.detection_count,
            "status": self.status,
            "approved_by_actor_id": self.approved_by_actor_id,
            "approved_by_actor_name": self.approved_by_actor_name,
            "approved_at": self.approved_at,
            "consumed_at": self.consumed_at,
        }


@dataclass(frozen=True)
class DLPApprovalTotals:
    requests: int = 0
    approved: int = 0
    consumed: int = 0
    model_mismatches: int = 0


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


class DLPApprovalStoreError(SecurityStoreError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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
                CREATE TABLE IF NOT EXISTS gateway_dlp_approval_requests (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL CHECK (protocol IN ('openai', 'anthropic')),
                    requested_model TEXT NOT NULL,
                    routed_model TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    payload_fingerprint TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    detection_count INTEGER NOT NULL CHECK (detection_count > 0),
                    status TEXT NOT NULL CHECK (
                        status IN ('pending', 'approved', 'consumed', 'expired')
                    ),
                    approved_by_actor_id TEXT,
                    approved_by_actor_name TEXT,
                    approved_at TEXT,
                    consumed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_dlp_approval_binding
                    ON gateway_dlp_approval_requests(
                        organization_id, actor_id, protocol, routed_model,
                        policy_version, payload_fingerprint, status, expires_at
                    );
                CREATE INDEX IF NOT EXISTS idx_gateway_dlp_approval_lookup
                    ON gateway_dlp_approval_requests(organization_id, id);
                CREATE TABLE IF NOT EXISTS gateway_dlp_approval_events (
                    id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    actor_name TEXT NOT NULL,
                    team_id TEXT NOT NULL,
                    team_name TEXT NOT NULL,
                    decision_actor_id TEXT,
                    decision_actor_name TEXT,
                    client TEXT NOT NULL,
                    protocol TEXT NOT NULL CHECK (protocol IN ('openai', 'anthropic')),
                    requested_model TEXT NOT NULL,
                    routed_model TEXT NOT NULL,
                    actual_model TEXT,
                    policy_version TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (
                        action IN ('requested', 'approved', 'consumed', 'model_mismatch')
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_gateway_dlp_approval_event_time
                    ON gateway_dlp_approval_events(occurred_at);
                CREATE INDEX IF NOT EXISTS idx_gateway_dlp_approval_event_org
                    ON gateway_dlp_approval_events(organization_id, occurred_at);
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
            approval_event_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(gateway_dlp_approval_events)"
                ).fetchall()
            }
            if "actual_model" not in approval_event_columns:
                connection.execute(
                    "ALTER TABLE gateway_dlp_approval_events ADD COLUMN actual_model TEXT"
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
        if action not in {
            "detected",
            "redacted",
            "denied",
            "approval_required",
            "approved",
        }:
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
        current_value = current.isoformat()
        pending_expiry = (current + timedelta(seconds=ttl_seconds)).isoformat()
        try:
            with self._lock, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_dlp_approvals(connection, current_value)
                parameters = (
                    identity.organization_id,
                    identity.actor_id,
                    client,
                    protocol,
                    requested_model,
                    routed_model,
                    policy_version,
                    payload_fingerprint,
                    binding["rules_json"],
                )
                approved = connection.execute(
                    """
                    SELECT * FROM gateway_dlp_approval_requests
                    WHERE organization_id = ? AND actor_id = ? AND client = ?
                        AND protocol = ? AND requested_model = ? AND routed_model = ?
                        AND policy_version = ? AND payload_fingerprint = ?
                        AND rules_json = ? AND status = 'approved' AND expires_at > ?
                    ORDER BY approved_at, id
                    LIMIT 1
                    """,
                    (*parameters, current_value),
                ).fetchone()
                if approved is not None:
                    changed = connection.execute(
                        """
                        UPDATE gateway_dlp_approval_requests
                        SET status = 'consumed', updated_at = ?, consumed_at = ?
                        WHERE id = ? AND status = 'approved' AND expires_at > ?
                        """,
                        (current_value, current_value, approved["id"], current_value),
                    ).rowcount
                    if changed != 1:  # pragma: no cover - BEGIN IMMEDIATE serializes this path
                        raise DLPApprovalStoreError("approval_replay_rejected")
                    consumed = connection.execute(
                        "SELECT * FROM gateway_dlp_approval_requests WHERE id = ?",
                        (approved["id"],),
                    ).fetchone()
                    self._record_dlp_approval_event(
                        connection,
                        request=consumed,
                        action="consumed",
                        occurred_at=current_value,
                        decision_actor_id=consumed["approved_by_actor_id"],
                        decision_actor_name=consumed["approved_by_actor_name"],
                    )
                    return DLPApprovalResult(
                        request_id=str(consumed["id"]),
                        status="consumed",
                        expires_at=str(consumed["expires_at"]),
                    )

                pending = connection.execute(
                    """
                    SELECT * FROM gateway_dlp_approval_requests
                    WHERE organization_id = ? AND actor_id = ? AND client = ?
                        AND protocol = ? AND requested_model = ? AND routed_model = ?
                        AND policy_version = ? AND payload_fingerprint = ?
                        AND rules_json = ? AND status = 'pending' AND expires_at > ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (*parameters, current_value),
                ).fetchone()
                if pending is not None:
                    return DLPApprovalResult(
                        request_id=str(pending["id"]),
                        status="pending",
                        expires_at=str(pending["expires_at"]),
                    )

                request_id = "apr_" + uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO gateway_dlp_approval_requests (
                        id, created_at, updated_at, expires_at, organization_id,
                        actor_id, actor_name, team_id, team_name, client, protocol,
                        requested_model, routed_model, policy_version, payload_fingerprint,
                        rules_json, detection_count, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        request_id,
                        current_value,
                        current_value,
                        pending_expiry,
                        identity.organization_id,
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
                created = connection.execute(
                    "SELECT * FROM gateway_dlp_approval_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                self._record_dlp_approval_event(
                    connection,
                    request=created,
                    action="requested",
                    occurred_at=current_value,
                )
                return DLPApprovalResult(
                    request_id=request_id,
                    status="pending",
                    expires_at=pending_expiry,
                )
        except DLPApprovalStoreError:
            raise
        except sqlite3.Error as error:
            raise DLPApprovalStoreError("approval_store_unavailable") from error

    def get_dlp_approval_request(
        self,
        request_id: str,
        *,
        organization_id: str,
        now: datetime | None = None,
    ) -> DLPApprovalRequest:
        _approval_request_id(request_id)
        current_value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        try:
            with self._lock, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_dlp_approvals(connection, current_value)
                row = connection.execute(
                    """
                    SELECT * FROM gateway_dlp_approval_requests
                    WHERE id = ? AND organization_id = ?
                    """,
                    (request_id, organization_id),
                ).fetchone()
        except sqlite3.Error as error:
            raise DLPApprovalStoreError("approval_store_unavailable") from error
        if row is None:
            raise DLPApprovalStoreError("approval_request_not_found")
        return _dlp_approval_request(row)

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
        if not 1 <= ttl_seconds <= 900:
            raise ValueError("DLP approval TTL must be between 1 and 900 seconds")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        current_value = current.isoformat()
        expires_at = (current + timedelta(seconds=ttl_seconds)).isoformat()
        try:
            with self._lock, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._expire_dlp_approvals(connection, current_value)
                row = connection.execute(
                    """
                    SELECT * FROM gateway_dlp_approval_requests
                    WHERE id = ? AND organization_id = ?
                    """,
                    (request_id, approver.organization_id),
                ).fetchone()
                if row is None:
                    raise DLPApprovalStoreError("approval_request_not_found")
                if row["actor_id"] == approver.actor_id:
                    raise DLPApprovalStoreError("approval_self_approval_forbidden")
                if row["status"] == "approved":
                    if row["approved_by_actor_id"] == approver.actor_id:
                        return _dlp_approval_request(row)
                    raise DLPApprovalStoreError("approval_request_already_decided")
                if row["status"] != "pending":
                    raise DLPApprovalStoreError("approval_request_not_approvable")
                connection.execute(
                    """
                    UPDATE gateway_dlp_approval_requests
                    SET status = 'approved', updated_at = ?, expires_at = ?,
                        approved_by_actor_id = ?, approved_by_actor_name = ?, approved_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (
                        current_value,
                        expires_at,
                        approver.actor_id,
                        approver.actor_name,
                        current_value,
                        request_id,
                    ),
                )
                approved = connection.execute(
                    "SELECT * FROM gateway_dlp_approval_requests WHERE id = ?",
                    (request_id,),
                ).fetchone()
                self._record_dlp_approval_event(
                    connection,
                    request=approved,
                    action="approved",
                    occurred_at=current_value,
                    decision_actor_id=approver.actor_id,
                    decision_actor_name=approver.actor_name,
                )
                return _dlp_approval_request(approved)
        except DLPApprovalStoreError:
            raise
        except sqlite3.Error as error:
            raise DLPApprovalStoreError("approval_store_unavailable") from error

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
        current_value = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        try:
            with self._lock, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                request = connection.execute(
                    """
                    SELECT * FROM gateway_dlp_approval_requests
                    WHERE id = ? AND organization_id = ?
                    """,
                    (request_id, organization_id),
                ).fetchone()
                if request is None:
                    raise DLPApprovalStoreError("approval_request_not_found")
                self._record_dlp_approval_event(
                    connection,
                    request=request,
                    action="model_mismatch",
                    occurred_at=current_value,
                    actual_model=actual_model,
                )
        except DLPApprovalStoreError:
            raise
        except sqlite3.Error as error:
            raise DLPApprovalStoreError("approval_store_unavailable") from error

    @staticmethod
    def _expire_dlp_approvals(connection: sqlite3.Connection, current_value: str) -> None:
        connection.execute(
            """
            UPDATE gateway_dlp_approval_requests
            SET status = 'expired', updated_at = ?
            WHERE status IN ('pending', 'approved') AND expires_at <= ?
            """,
            (current_value, current_value),
        )

    @staticmethod
    def _record_dlp_approval_event(
        connection: sqlite3.Connection,
        *,
        request: sqlite3.Row,
        action: str,
        occurred_at: str,
        decision_actor_id: str | None = None,
        decision_actor_name: str | None = None,
        actual_model: str | None = None,
    ) -> None:
        if action not in {"requested", "approved", "consumed", "model_mismatch"}:
            raise ValueError("Unsupported DLP approval event action")
        connection.execute(
            """
            INSERT INTO gateway_dlp_approval_events (
                id, occurred_at, request_id, organization_id, actor_id, actor_name,
                team_id, team_name, decision_actor_id, decision_actor_name, client,
                protocol, requested_model, routed_model, policy_version, rules_json,
                actual_model, action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                occurred_at,
                request["id"],
                request["organization_id"],
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
                request["policy_version"],
                request["rules_json"],
                actual_model,
                action,
            ),
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

    def monthly_dlp_approval_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
    ) -> DLPApprovalTotals:
        start = datetime.now(timezone.utc).replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ).isoformat()
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
                COALESCE(SUM(CASE WHEN action = 'requested' THEN 1 ELSE 0 END), 0)
                    AS requests,
                COALESCE(SUM(CASE WHEN action = 'approved' THEN 1 ELSE 0 END), 0)
                    AS approved,
                COALESCE(SUM(CASE WHEN action = 'consumed' THEN 1 ELSE 0 END), 0)
                    AS consumed,
                COALESCE(SUM(CASE WHEN action = 'model_mismatch' THEN 1 ELSE 0 END), 0)
                    AS model_mismatches
            FROM gateway_dlp_approval_events
            WHERE {' AND '.join(clauses)}
        """
        with self._lock, self._connection() as connection:
            row = connection.execute(query, parameters).fetchone()
        return DLPApprovalTotals(**dict(row))

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
                approval_rows = connection.execute(
                    """
                    SELECT
                        id, occurred_at, request_id, organization_id, actor_id,
                        actor_name, team_id, team_name, decision_actor_id,
                        decision_actor_name, client, protocol, requested_model,
                        routed_model, actual_model, policy_version, rules_json, action
                    FROM gateway_dlp_approval_events
                    WHERE occurred_at >= ?
                    ORDER BY occurred_at, id
                    """,
                    (since,),
                ).fetchall()
                for row in approval_rows:
                    event = dict(row)
                    event["rules"] = json.loads(str(event.pop("rules_json")))
                    events.append(
                        {
                            "schema_version": 1,
                            "event_type": "security.dlp.approval",
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


def _approval_binding(
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
) -> dict[str, str]:
    for label, value, maximum in (
        ("organization_id", identity.organization_id, 256),
        ("actor_id", identity.actor_id, 256),
        ("client", client, 64),
        ("requested_model", requested_model, 256),
        ("routed_model", routed_model, 256),
        ("policy_version", policy_version, 128),
    ):
        if (
            not isinstance(value, str)
            or not value
            or len(value.encode("utf-8")) > maximum
            or any(character in value for character in ("\n", "\r", "\x00"))
        ):
            raise ValueError(f"DLP approval {label} must be a bounded single-line string")
    if protocol not in {"openai", "anthropic"}:
        raise ValueError("DLP approval protocol must be openai or anthropic")
    if (
        not isinstance(payload_fingerprint, str)
        or not payload_fingerprint.startswith("hdf_v1_")
        or len(payload_fingerprint) != 71
        or any(character not in "0123456789abcdef" for character in payload_fingerprint[7:])
    ):
        raise ValueError("DLP approval payload fingerprint is invalid")
    normalized_rules = tuple(sorted(set(rules)))
    if not normalized_rules or len(normalized_rules) > 100:
        raise ValueError("DLP approval rules must contain 1 to 100 identifiers")
    for rule in normalized_rules:
        if (
            not isinstance(rule, str)
            or not rule
            or len(rule.encode("utf-8")) > 64
            or any(not (character.isalnum() or character in {"-", "_", "."}) for character in rule)
        ):
            raise ValueError("DLP approval rule identifiers are invalid")
    if (
        isinstance(detection_count, bool)
        or not isinstance(detection_count, int)
        or not 1 <= detection_count <= 2**63 - 1
    ):
        raise ValueError("DLP approval detection count must be positive")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 1 <= ttl_seconds <= 900:
        raise ValueError("DLP approval TTL must be between 1 and 900 seconds")
    return {"rules_json": json.dumps(normalized_rules, separators=(",", ":"))}


def _approval_request_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("apr_")
        or len(value) != 36
        or any(character not in "0123456789abcdef" for character in value[4:])
    ):
        raise DLPApprovalStoreError("approval_request_not_found")
    return value


def _dlp_approval_request(row: sqlite3.Row) -> DLPApprovalRequest:
    try:
        rules_value = json.loads(str(row["rules_json"]))
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise DLPApprovalStoreError("approval_store_corrupt") from error
    if not isinstance(rules_value, list) or any(not isinstance(rule, str) for rule in rules_value):
        raise DLPApprovalStoreError("approval_store_corrupt")
    return DLPApprovalRequest(
        request_id=str(row["id"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        expires_at=str(row["expires_at"]),
        organization_id=str(row["organization_id"]),
        actor_id=str(row["actor_id"]),
        actor_name=str(row["actor_name"]),
        team_id=str(row["team_id"]),
        team_name=str(row["team_name"]),
        client=str(row["client"]),
        protocol=str(row["protocol"]),
        requested_model=str(row["requested_model"]),
        routed_model=str(row["routed_model"]),
        policy_version=str(row["policy_version"]),
        rules=tuple(rules_value),
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
        approved_at=str(row["approved_at"]) if row["approved_at"] is not None else None,
        consumed_at=str(row["consumed_at"]) if row["consumed_at"] is not None else None,
    )
