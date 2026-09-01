"""Content-free provider reliability evidence and bounded failover rules."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


PROVIDER_ATTEMPT_METRICS_SCHEMA_ID = "hormuz.provider-attempt-metrics"
PROVIDER_ATTEMPT_METRICS_SCHEMA_VERSION = 1
PROVIDER_FAILOVER_SCHEMA_ID = "hormuz.provider-failover"
PROVIDER_FAILOVER_SCHEMA_VERSION = 1

# These statuses are explicit capacity rejections. Transport failures and
# generic 5xx responses remain ambiguous and must never trigger another call.
FAILOVER_STATUS_REASONS = {
    429: "provider_rate_limited",
    529: "provider_overloaded",
}


@dataclass(frozen=True)
class ProviderAttemptMetrics:
    """Monotonic, content-free timings and byte counts for one egress."""

    provider_status: int | None
    response_headers_us: int | None
    first_body_byte_us: int | None
    total_us: int
    provider_bytes_read: int
    downstream_bytes_sent: int


@dataclass(frozen=True)
class ProviderFailoverContext:
    """The terminal attempt and explicit response that authorized one hop."""

    original_attempt_id: str
    trigger_status: int
    reason_code: str


def failover_reason(status: int) -> str | None:
    return FAILOVER_STATUS_REASONS.get(status)


def build_provider_attempt_metrics_event(
    *,
    attempt_id: str,
    organization_id: str,
    recorded_at: datetime,
    metrics: ProviderAttemptMetrics,
) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "event_schema_id": PROVIDER_ATTEMPT_METRICS_SCHEMA_ID,
        "event_schema_version": PROVIDER_ATTEMPT_METRICS_SCHEMA_VERSION,
        "organization_id": organization_id,
        "attempt_id": attempt_id,
        "recorded_at": recorded_at.isoformat(),
        "provider_status": metrics.provider_status,
        "response_headers_us": metrics.response_headers_us,
        "first_body_byte_us": metrics.first_body_byte_us,
        "total_us": metrics.total_us,
        "provider_bytes_read": metrics.provider_bytes_read,
        "downstream_bytes_sent": metrics.downstream_bytes_sent,
    }
    _validate_metrics_event(event)
    return event


def build_provider_failover_event(
    *,
    organization_id: str,
    failover_attempt_id: str,
    recorded_at: datetime,
    context: ProviderFailoverContext,
) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": str(uuid.uuid4()),
        "event_schema_id": PROVIDER_FAILOVER_SCHEMA_ID,
        "event_schema_version": PROVIDER_FAILOVER_SCHEMA_VERSION,
        "organization_id": organization_id,
        "original_attempt_id": context.original_attempt_id,
        "failover_attempt_id": failover_attempt_id,
        "trigger_status": context.trigger_status,
        "reason_code": context.reason_code,
        "recorded_at": recorded_at.isoformat(),
    }
    _validate_failover_event(event)
    return event


def _validate_metrics_event(event: dict[str, object]) -> None:
    _identifier(event["event_id"])
    _identifier(event["organization_id"])
    _identifier(event["attempt_id"])
    status = event["provider_status"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        raise ValueError("provider_attempt_metrics_invalid")
    headers = _optional_counter(event["response_headers_us"])
    first_byte = _optional_counter(event["first_body_byte_us"])
    total = _counter(event["total_us"])
    provider_bytes = _counter(event["provider_bytes_read"])
    downstream_bytes = _counter(event["downstream_bytes_sent"])
    if (
        (headers is not None and headers > total)
        or (first_byte is not None and (first_byte > total or headers is None or first_byte < headers))
        or downstream_bytes > provider_bytes
    ):
        raise ValueError("provider_attempt_metrics_invalid")


def _validate_failover_event(event: dict[str, object]) -> None:
    _identifier(event["event_id"])
    _identifier(event["organization_id"])
    original = _identifier(event["original_attempt_id"])
    failover = _identifier(event["failover_attempt_id"])
    status = event["trigger_status"]
    reason = event["reason_code"]
    if (
        original == failover
        or type(status) is not int
        or FAILOVER_STATUS_REASONS.get(status) != reason
    ):
        raise ValueError("provider_failover_evidence_invalid")


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128 or any(character in value for character in "\x00\r\n"):
        raise ValueError("provider_reliability_evidence_invalid")
    return value


def _counter(value: object) -> int:
    if type(value) is not int or not 0 <= value < 9223372036854775807:
        raise ValueError("provider_attempt_metrics_invalid")
    return value


def _optional_counter(value: object) -> int | None:
    return None if value is None else _counter(value)
