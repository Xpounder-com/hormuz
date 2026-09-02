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
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .audit_chain import (
    AuditChainAnchorStatus,
    AuditChainError,
    AuditChainHead,
    AuditChainSource,
    audit_chain_checkpoint_position,
)
from .config import Identity
from .finance_attempts import (
    ConfiguredRateCardBinding,
    ConfiguredRouteEstimate,
    NativeUsageObservation,
)
from .provider_reliability import ProviderAttemptMetrics, ProviderFailoverContext
from .contracts import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
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
class ProviderReliabilityTotals:
    """Content-free reliability evidence scoped to one actor and tenant."""

    attempt_count: int = 0
    provider_attempt_record_count: int = 0
    latency_header_sample_count: int = 0
    latency_first_body_byte_sample_count: int = 0
    latency_total_sample_count: int = 0
    failover_link_record_count: int = 0
    outcome_unknown_count: int = 0
    cancellation_outcome_unknown_count: int = 0

    @property
    def live_provider_request_count(self) -> int:
        return max(0, self.attempt_count - self.failover_link_record_count)


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
    attribution_event_id: str | None = None


@dataclass(frozen=True)
class WorkBudgetContext:
    """Trusted server-resolved work scope for one atomic governed attempt.

    Syntax is revalidated by each adapter and registry state is rechecked in
    the reservation transaction.  This value never grants tenant authority.
    """

    work_scope_id: str | None
    work_scope_version: int | None
    confidence: str
    reason_code: str
    reserved_output_tokens: int
    output_tokens_bounded: bool
    input_tokens_bounded: bool
    policy_version: str
    policy_digest: str
    rate_card_id: str
    rate_card_version: int
    rate_card_digest: str
    rate_card_currency: str


class WorkBudgetRequestRepository(Protocol):
    """Explicit internal capability for atomic v1.1 request admission.

    This is composed beside the frozen v1 :class:`UsageRepository` contract.
    It does not add a public operation to either usage adapter.
    """

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
        work_budget: WorkBudgetContext,
    ) -> RequestAttempt: ...


class ProviderReliabilityRepository(Protocol):
    """Internal capability for atomic provider-attempt reliability evidence.

    This is composed beside the frozen v1 :class:`UsageRepository` contract.
    It owns only the begin/finalize transitions that add failover or timing
    evidence to the same transaction as the request-attempt ledger.
    """

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
        work_budget: WorkBudgetContext | None,
        provider_failover: ProviderFailoverContext | None,
        configured_rate_card: ConfiguredRateCardBinding | None = None,
    ) -> RequestAttempt: ...

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
        provider_metrics: ProviderAttemptMetrics,
        finance_observation: NativeUsageObservation | None = None,
        configured_estimate: ConfiguredRouteEstimate | None = None,
    ) -> None: ...

    def mark_request_attempt_outcome_unknown(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        reason_code: str,
        provider_metrics: ProviderAttemptMetrics,
        finance_observation: NativeUsageObservation | None = None,
    ) -> bool: ...

    def totals(
        self,
        *,
        actor_id: str,
        organization_id: str,
    ) -> ProviderReliabilityTotals: ...


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


@dataclass(frozen=True)
class AuditChainCheckpointInput:
    """One strictly parsed external checkpoint position."""

    chain_version: int
    chain_epoch: int
    sequence: int
    head_digest: str


@dataclass(frozen=True)
class AuditChainEpochInput:
    """Backend-neutral epoch row loaded inside a backend-owned snapshot."""

    chain_version: int
    chain_epoch: int
    reason_code: object
    predecessor_chain_epoch: object
    predecessor_sequence: object
    predecessor_head_digest: object


@dataclass(frozen=True)
class AuditChainEntryInput:
    """Canonical entry mapping plus its finite source identity."""

    chain_epoch: int
    event_id: str
    source: AuditChainSource
    entry: Mapping[str, object]


@dataclass(frozen=True)
class AuditChainSourceEventInput:
    """One loaded metadata-only event bound to a finite source identity."""

    source: AuditChainSource
    event: Mapping[str, Any]


@dataclass(frozen=True)
class AuditChainVerificationInputs:
    """Immutable values consumed by either backend's current verifier.

    Backends still own snapshot acquisition, SQL, source retrieval, tenant
    context, and error translation.  This value only removes their row-shape
    differences so parity can be proven before verifier extraction.
    """

    organization_id: str
    head: AuditChainHead
    epochs: tuple[AuditChainEpochInput, ...]
    entries: tuple[AuditChainEntryInput, ...]
    source_events: tuple[AuditChainSourceEventInput, ...]
    checkpoint: AuditChainCheckpointInput | None


class ReservationDenied(RuntimeError):
    pass


class RequestAttemptStateError(RuntimeError):
    """Raised when an immutable attempt cannot make the requested transition."""


class UsageRepository(Protocol):
    """The complete v1 ledger contract consumed by gateway, policy, and CLI code.

    SQLite and PostgreSQL retain their own SQL, transactions, locks, and tenant
    checks. Portfolio operations belong to a separate repository composed at
    the factory boundary, not to this protocol or either usage adapter.
    """

    def verify_ready(self) -> None: ...

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
    ) -> str: ...

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
    ) -> str: ...

    def reserve_budget(
        self,
        *,
        identity: Identity,
        scopes: tuple[ReservationScope, ...],
        reserved_tokens: int,
        reserved_cost_microusd: int,
        ttl_seconds: int,
    ) -> str | None: ...

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
    ) -> RequestAttempt: ...

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
    ) -> None: ...

    def mark_request_attempt_outcome_unknown(
        self,
        *,
        attempt: RequestAttempt,
        organization_id: str,
        reason_code: str,
    ) -> bool: ...

    def sweep_stale_request_attempts(self, *, organization_id: str | None = None) -> int: ...

    def release_budget_reservation(
        self, reservation_id: str | None, *, organization_id: str | None = None,
    ) -> None: ...

    def refresh_budget_reservation(
        self, reservation_id: str | None, *, ttl_seconds: int, organization_id: str | None = None,
    ) -> None: ...

    def active_budget_reservations(self, *, organization_id: str | None = None) -> int: ...

    def monthly_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
        starts_at: datetime | None = None,
        ends_before: datetime | None = None,
    ) -> MonthlyTotals: ...

    def monthly_secret_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> SecretTotals: ...

    def summary_rows(self, *, organization_id: str | None = None) -> list[dict[str, object]]: ...

    def report_rows(
        self,
        *,
        group_by: str,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> list[dict[str, object]]: ...

    def audit_events(
        self, *, since: str, kind: str = "all", organization_id: str | None = None,
    ) -> list[dict[str, object]]: ...

    def audit_chain_head(self, *, organization_id: str) -> AuditChainHead: ...

    def audit_chain_anchor_status(
        self,
        *,
        organization_id: str,
        maximum_age_seconds: int | None = None,
        now: datetime | None = None,
    ) -> AuditChainAnchorStatus: ...

    def record_audit_chain_checkpoint(
        self,
        *,
        checkpoint: Mapping[str, object],
        artifact_sha256: str,
        anchor_backend: str,
        object_version: str | None,
        anchored_at: datetime | None = None,
    ) -> None: ...

    def begin_audit_chain_epoch(
        self, *, checkpoint: Mapping[str, object], reason_code: str,
    ) -> AuditChainHead: ...

    def verify_audit_chain(
        self, *, organization_id: str, checkpoint: Mapping[str, object] | None = None,
    ) -> AuditChainHead: ...


def monthly_usage_bounds(
    *,
    starts_at: datetime | None,
    ends_before: datetime | None,
) -> tuple[datetime, datetime | None]:
    """Return one explicit UTC period or the existing current-month default."""

    if starts_at is None and ends_before is None:
        current = datetime.now(timezone.utc)
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0), None
    if (
        not isinstance(starts_at, datetime)
        or not isinstance(ends_before, datetime)
        or starts_at.tzinfo is None
        or ends_before.tzinfo is None
    ):
        raise ValueError("usage period requires timezone-aware bounds")
    normalized_start = starts_at.astimezone(timezone.utc)
    normalized_end = ends_before.astimezone(timezone.utc)
    if normalized_start >= normalized_end:
        raise ValueError("usage period must have a positive duration")
    return normalized_start, normalized_end


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


def normalize_audit_chain_checkpoint_input(
    checkpoint: Mapping[str, object] | None,
    *,
    organization_id: str,
    error_factory: StorageErrorFactory,
) -> AuditChainCheckpointInput | None:
    """Strictly parse one optional checkpoint at the storage boundary."""

    if checkpoint is None:
        return None
    try:
        _, checkpoint_organization, chain_version, chain_epoch, sequence, head_digest = (
            audit_chain_checkpoint_position(checkpoint)
        )
    except AuditChainError as error:
        raise error_factory(error.code) from None
    if checkpoint_organization != organization_id:
        raise error_factory("audit_chain_tenant_mismatch")
    return AuditChainCheckpointInput(
        chain_version=chain_version,
        chain_epoch=chain_epoch,
        sequence=sequence,
        head_digest=head_digest,
    )


def normalize_audit_chain_epoch_input(
    row: PersistenceRow,
    *,
    error_factory: StorageErrorFactory,
) -> AuditChainEpochInput:
    """Normalize SQLite and PostgreSQL epoch rows without validating linkage."""

    try:
        chain_version = int(row["chain_version"])
        chain_epoch = int(row["chain_epoch"])
    except (KeyError, TypeError, ValueError):
        raise error_factory("audit_chain_epoch_malformed") from None
    return AuditChainEpochInput(
        chain_version=chain_version,
        chain_epoch=chain_epoch,
        reason_code=row["reason_code"],
        predecessor_chain_epoch=row["predecessor_chain_epoch"],
        predecessor_sequence=row["predecessor_sequence"],
        predecessor_head_digest=row["predecessor_head_digest"],
    )


def normalize_audit_chain_entry_input(
    row: PersistenceRow,
    *,
    organization_id: str,
    error_factory: StorageErrorFactory,
) -> AuditChainEntryInput:
    """Normalize one stored entry and its v1 or v2 source identity."""

    try:
        parsed_event = json.loads(str(row["event_json"]))
        if not isinstance(parsed_event, dict):
            raise ValueError
        schema_version = row["entry_schema_version"]
        entry: dict[str, object] = {
            "schema_id": row["entry_schema_id"],
            "schema_version": schema_version,
            "organization_id": organization_id,
            "chain_version": row["chain_version"],
            "chain_epoch": row["chain_epoch"],
            "sequence": row["sequence"],
            "previous_digest": row["previous_digest"],
            "event_digest": row["event_digest"],
            "event": parsed_event,
        }
        if schema_version == AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION:
            event_id = parsed_event.get("id")
            if not isinstance(event_id, str) or row["event_id"] != event_id:
                raise AuditChainError("audit_chain_entry_malformed")
            source = AuditChainSource(
                schema_id=AUDIT_EVENT_SCHEMA_ID,
                schema_version=AUDIT_EVENT_SCHEMA_VERSION,
                event_id=event_id,
            )
        elif schema_version == AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
            source_schema_id = row["source_schema_id"]
            source_schema_version = row["source_schema_version"]
            source_event_id = row["source_event_id"]
            if (
                not isinstance(source_schema_id, str)
                or isinstance(source_schema_version, bool)
                or not isinstance(source_schema_version, int)
                or not isinstance(source_event_id, str)
                or row["event_id"] != source_event_id
            ):
                raise AuditChainError("audit_chain_entry_malformed")
            event_id = source_event_id
            source = AuditChainSource(
                schema_id=source_schema_id,
                schema_version=source_schema_version,
                event_id=source_event_id,
            )
            entry.update(
                {
                    "source_schema_id": source.schema_id,
                    "source_schema_version": source.schema_version,
                    "source_event_id": source.event_id,
                }
            )
        else:
            raise AuditChainError("audit_chain_entry_schema_unsupported")
        chain_epoch = int(row["chain_epoch"])
    except (AuditChainError, IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        code = error.code if isinstance(error, AuditChainError) else "audit_chain_entry_malformed"
        raise error_factory(code) from None
    return AuditChainEntryInput(
        chain_epoch=chain_epoch,
        event_id=event_id,
        source=source,
        entry=MappingProxyType(entry),
    )


def normalize_audit_chain_source_event_input(
    event: Mapping[str, Any],
    *,
    source: AuditChainSource | None = None,
    error_factory: StorageErrorFactory,
) -> AuditChainSourceEventInput:
    """Bind one loaded source event to the identity used by its entry."""

    if not isinstance(event, Mapping):
        raise error_factory("audit_chain_source_event_malformed")
    if source is None:
        event_id = event.get("id")
        if not isinstance(event_id, str):
            raise error_factory("audit_chain_source_event_malformed")
        source = AuditChainSource(
            schema_id=AUDIT_EVENT_SCHEMA_ID,
            schema_version=AUDIT_EVENT_SCHEMA_VERSION,
            event_id=event_id,
        )
    return AuditChainSourceEventInput(source=source, event=MappingProxyType(dict(event)))


def audit_chain_source_event_map(
    source_events: tuple[AuditChainSourceEventInput, ...],
    *,
    error_factory: StorageErrorFactory,
) -> dict[AuditChainSource, Mapping[str, Any]]:
    """Index an already loaded finite source set and reject ambiguity."""

    indexed: dict[AuditChainSource, Mapping[str, Any]] = {}
    for source_event in source_events:
        if source_event.source in indexed:
            raise error_factory("audit_chain_source_event_malformed")
        indexed[source_event.source] = source_event.event
    return indexed


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
