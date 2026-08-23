"""Backend-neutral persistence contracts and durable-state normalization.

This module owns the values and state transitions shared by Hormuz storage
adapters.  It deliberately contains no SQLite or PostgreSQL imports, SQL,
transaction management, connection pooling, tenant context, or RLS behavior.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from .audit_chain import AuditChainAnchorStatus, AuditChainHead
from .config import Identity
from .contracts import (
    REQUEST_ATTEMPT_EVENT_SCHEMA_ID,
    REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION,
    REQUEST_ATTEMPT_SCHEMA_ID,
    REQUEST_ATTEMPT_SCHEMA_VERSION,
    validate_request_attempt,
    validate_request_attempt_event,
)


class PersistenceRow(Protocol):
    """The keyed result surface shared by sqlite3.Row and dict rows."""

    def __getitem__(self, name: str) -> object: ...


class StorageErrorFactory(Protocol):
    def __call__(self, code: str) -> RuntimeError: ...


@dataclass(frozen=True)
class MonthlyTotals:
    requests: int = 0
    denied_requests: int = 0
    rate_limited_requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
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
    redacted_requests: int = 0
    denied_requests: int = 0


@dataclass(frozen=True)
class ReservationScope:
    name: str
    actor_id: str | None = None
    team_id: str | None = None
    token_limit: int | None = None
    cost_limit_microusd: int | None = None


@dataclass(frozen=True)
class RequestAttempt:
    """One immutable gateway-generated provider-egress attempt identifier."""

    attempt_id: str
    reservation_id: str


@dataclass(frozen=True)
class RequestAttemptState:
    """The latest immutable event position for a request attempt."""

    sequence: int
    state: str


@dataclass(frozen=True)
class RequestAttemptResult:
    """Backend-neutral fields needed to materialize terminal usage evidence."""

    identity: Identity
    client: str
    protocol: str
    requested_model: str
    resolved_alias: str | None
    upstream_model: str | None
    policy_version: str
    policy_action: str
    redaction_count: int
    redaction_rules: tuple[str, ...]


class ReservationDenied(RuntimeError):
    pass


class RequestAttemptStateError(RuntimeError):
    """Raised when an immutable attempt cannot make the requested transition."""


class UsageRepository(Protocol):
    """The narrow metadata-only storage contract used by policy and gateway code."""

    def verify_ready(self) -> None: ...

    def record(self, **kwargs: object) -> str: ...

    def record_secret_event(self, **kwargs: object) -> str: ...

    def reserve_budget(self, **kwargs: object) -> str | None: ...

    def begin_request_attempt(self, **kwargs: object) -> RequestAttempt: ...

    def finalize_request_attempt(self, **kwargs: object) -> None: ...

    def mark_request_attempt_outcome_unknown(self, **kwargs: object) -> bool: ...

    def sweep_stale_request_attempts(self, **kwargs: object) -> int: ...

    def release_budget_reservation(self, reservation_id: str | None, **kwargs: object) -> None: ...

    def refresh_budget_reservation(self, reservation_id: str | None, **kwargs: object) -> None: ...

    def active_budget_reservations(self, **kwargs: object) -> int: ...

    def monthly_totals(self, **kwargs: object) -> MonthlyTotals: ...

    def monthly_secret_totals(self, **kwargs: object) -> SecretTotals: ...

    def summary_rows(self, **kwargs: object) -> list[dict[str, object]]: ...

    def report_rows(self, **kwargs: object) -> list[dict[str, object]]: ...

    def audit_events(self, **kwargs: object) -> list[dict[str, object]]: ...

    def audit_chain_head(self, **kwargs: object) -> AuditChainHead: ...

    def audit_chain_anchor_status(self, **kwargs: object) -> AuditChainAnchorStatus: ...

    def record_audit_chain_checkpoint(self, **kwargs: object) -> None: ...

    def begin_audit_chain_epoch(self, **kwargs: object) -> AuditChainHead: ...

    def verify_audit_chain(self, **kwargs: object) -> AuditChainHead: ...


def build_request_attempt_root(
    *,
    attempt_id: str,
    created_at: datetime,
    identity: Identity,
    organization_id: str,
    client: str,
    protocol: str,
    requested_model: str,
    resolved_alias: str | None,
    upstream_model: str | None,
    policy_version: str,
    policy_action: str,
    redaction_count: int,
    redaction_rules: tuple[str, ...],
    reserved_tokens: int,
    reserved_cost_microusd: int,
) -> dict[str, object]:
    """Construct and validate the canonical content-free attempt root."""

    root: dict[str, object] = {
        "evidence_schema_id": REQUEST_ATTEMPT_SCHEMA_ID,
        "evidence_schema_version": REQUEST_ATTEMPT_SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "created_at": created_at.isoformat(),
        "organization_id": organization_id,
        "actor_id": identity.actor_id,
        "actor_name": identity.actor_name,
        "team_id": identity.team_id,
        "team_name": identity.team_name,
        "identity_type": identity.identity_type,
        "authentication_source": identity.authentication_source,
        "client": client,
        "protocol": protocol,
        "requested_model": requested_model,
        "resolved_alias": resolved_alias,
        "upstream_model": upstream_model,
        "policy_version": policy_version,
        "policy_action": policy_action,
        "redaction_count": max(0, redaction_count),
        "redaction_rules": sorted(set(redaction_rules)),
        "reserved_tokens": max(0, reserved_tokens),
        "reserved_cost_microusd": max(0, reserved_cost_microusd),
    }
    validate_request_attempt(root)
    return root


def build_request_attempt_event(
    *,
    attempt_id: str,
    organization_id: str,
    occurred_at: datetime,
    sequence: int,
    state: str,
    reason_code: str | None,
    usage_event_id: str | None,
    event_id: str | None = None,
) -> dict[str, object]:
    """Construct and validate one canonical immutable attempt event."""

    event: dict[str, object] = {
        "event_schema_id": REQUEST_ATTEMPT_EVENT_SCHEMA_ID,
        "event_schema_version": REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION,
        "id": event_id or str(uuid.uuid4()),
        "attempt_id": attempt_id,
        "organization_id": organization_id,
        "occurred_at": occurred_at.isoformat(),
        "sequence": sequence,
        "state": state,
        "reason_code": reason_code,
        "usage_event_id": usage_event_id,
    }
    validate_request_attempt_event(event)
    return event


def require_terminal_request_attempt_state(state: str) -> None:
    if state not in {"succeeded", "failed", "rate_limited"}:
        raise RequestAttemptStateError("request_attempt_terminal_state_unsupported")


def require_pending_request_attempt_state(state: str) -> None:
    if state != "pending":
        raise RequestAttemptStateError("request_attempt_not_pending")


def should_mark_request_attempt_unknown(state: str) -> bool:
    if state == "outcome_unknown":
        return False
    require_pending_request_attempt_state(state)
    return True


def normalize_request_attempt_state(row: PersistenceRow) -> RequestAttemptState:
    return RequestAttemptState(sequence=int(row["sequence"]), state=str(row["state"]))


def normalize_request_attempt_result(
    row: PersistenceRow,
    *,
    error_factory: StorageErrorFactory,
) -> RequestAttemptResult:
    """Normalize the common result shape without owning a backend cursor."""

    return RequestAttemptResult(
        identity=Identity(
            token_env="REQUEST_ATTEMPT_LEDGER",
            token="",
            actor_id=str(row["actor_id"]),
            actor_name=str(row["actor_name"]),
            team_id=str(row["team_id"]),
            team_name=str(row["team_name"]),
            organization_id=str(row["organization_id"]),
            identity_type=str(row["identity_type"]),
            authentication_source=str(row["authentication_source"]),
        ),
        client=str(row["client"]),
        protocol=str(row["protocol"]),
        requested_model=str(row["requested_model"]),
        resolved_alias=optional_string(row, "resolved_alias", error_factory=error_factory),
        upstream_model=optional_string(row, "upstream_model", error_factory=error_factory),
        policy_version=str(row["policy_version"]),
        policy_action=str(row["policy_action"]),
        redaction_count=int(row["redaction_count"]),
        redaction_rules=tuple(json_string_list(row["redaction_rules"], error_factory=error_factory)),
    )


def normalize_audit_chain_head(
    row: PersistenceRow,
    *,
    error_factory: StorageErrorFactory,
) -> AuditChainHead:
    head_digest = row["head_digest"]
    if head_digest is not None and not isinstance(head_digest, str):
        raise error_factory("audit_chain_head_malformed")
    return AuditChainHead(
        organization_id=str(row["organization_id"]),
        chain_version=int(row["chain_version"]),
        chain_epoch=int(row["chain_epoch"]),
        sequence=int(row["sequence"]),
        head_digest=head_digest,
    )


def optional_string(
    row: PersistenceRow,
    name: str,
    *,
    error_factory: StorageErrorFactory,
) -> str | None:
    value = row[name]
    if value is None:
        return None
    if not isinstance(value, str):
        raise error_factory("request_attempt_evidence_malformed")
    return value


def json_string_list(value: object, *, error_factory: StorageErrorFactory) -> list[str]:
    if not isinstance(value, str):
        raise error_factory("request_attempt_evidence_malformed")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise error_factory("request_attempt_evidence_malformed") from None
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise error_factory("request_attempt_evidence_malformed")
    return decoded


def stored_utc_timestamp(
    value: object,
    *,
    code: str,
    error_factory: StorageErrorFactory,
    accept_datetime: bool,
) -> datetime:
    if accept_datetime and isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise error_factory(code) from None
    else:
        raise error_factory(code)
    if parsed.tzinfo is None:
        raise error_factory(code)
    return parsed.astimezone(timezone.utc)


def validate_anchor_age(value: int | None, *, error_factory: StorageErrorFactory) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise error_factory("audit_chain_anchor_age_invalid")


def is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
