"""Durable, content-free custody-executor attempt contracts.

This module is deliberately separate from :mod:`hormuz.custody_repository`.
Custody control records human authority; the executor consumes one approved
governed intent through a distinct machine credential. Neither the execution
request nor the durable attempt ledger contains plaintext secret material.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol
from uuid import UUID

from .custody_repository import CUSTODY_OPERATIONS, CUSTODY_OPERATION_TARGET_KINDS, CUSTODY_ROUTINE_OPERATIONS
from .custody_lifecycle import CustodyEnvelopeAttestation, CustodyLifecycleEffect


CUSTODY_EXECUTION_STATES = frozenset({"pending", "succeeded", "failed", "outcome_unknown"})
CUSTODY_EXECUTION_UNKNOWN_REASONS = frozenset({"external_result_ambiguous", "stale_pending"})
CUSTODY_EXECUTION_FAILURE_REASONS = frozenset({"execution_failed"})
_EXECUTION_TARGET_KINDS = CUSTODY_OPERATION_TARGET_KINDS

_MAX_EXECUTION_DESCRIPTOR_BYTES = 64 * 1024
_MAX_EXECUTION_DESCRIPTOR_DEPTH = 16
_MAX_PROTECTED_INPUT_REFERENCE_BYTES = 4096


class CustodyExecutionError(RuntimeError):
    """Stable, content-free governed-executor failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CustodyExecutionRequest:
    """One non-persistent machine request matched to a governed intent.

    ``target`` and ``parameters`` are canonicalized only to bind this request
    to its prior human authorization.  They are never written to the custody
    control ledger, executor events, logs, CLI arguments, or status output.
    A secret can enter only through ``protected_input_reference`` and only for
    initial envelope sealing; the executor resolves that reference after its
    pending attempt has committed.
    """

    organization_id: str
    operation_id: str
    operation_type: str
    target: Mapping[str, Any]
    parameters: Mapping[str, Any]
    protected_input_reference: str | None = None

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        _operation_id(self.operation_id)
        if self.operation_type not in CUSTODY_OPERATIONS:
            raise ValueError("Custody execution operation_type is invalid")
        _canonical_descriptor(self.target, "target")
        _canonical_descriptor(self.parameters, "parameters")
        if self.operation_type == "seal_envelope":
            _protected_input_reference(self.protected_input_reference)
        elif self.protected_input_reference is not None:
            raise ValueError("Only seal_envelope accepts a protected input reference")

    @property
    def target_sha256(self) -> str:
        return _sha256(_canonical_descriptor(self.target, "target"))

    @property
    def parameters_sha256(self) -> str:
        return _sha256(_canonical_descriptor(self.parameters, "parameters"))

    @property
    def protected_input_ref_sha256(self) -> str | None:
        if self.protected_input_reference is None:
            return None
        return _sha256(self.protected_input_reference.encode("utf-8"))


@dataclass(frozen=True)
class CustodyExecutionEvent:
    """One immutable metadata-only state transition for an execution."""

    organization_id: str
    execution_id: str
    operation_id: str
    occurred_at: datetime
    sequence: int
    state: str
    reason_code: str | None

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        _operation_id(self.execution_id)
        _operation_id(self.operation_id)
        if self.occurred_at.tzinfo is None:
            raise ValueError("Custody execution event timestamp must be timezone-aware")
        if self.sequence not in {1, 2}:
            raise ValueError("Custody execution event sequence is invalid")
        if self.state not in CUSTODY_EXECUTION_STATES:
            raise ValueError("Custody execution event state is invalid")
        if self.sequence == 1:
            if self.state != "pending" or self.reason_code is not None:
                raise ValueError("Pending custody execution event is invalid")
            return
        if self.state == "pending":
            raise ValueError("Terminal custody execution event is invalid")
        if self.state == "succeeded":
            if self.reason_code is not None:
                raise ValueError("Successful custody execution event is invalid")
            return
        if self.state == "failed":
            if self.reason_code not in CUSTODY_EXECUTION_FAILURE_REASONS:
                raise ValueError("Failed custody execution event is invalid")
            return
        if self.reason_code not in CUSTODY_EXECUTION_UNKNOWN_REASONS:
            raise ValueError("Unknown custody execution event is invalid")

    def contract_record(self) -> dict[str, object]:
        from .contracts import CUSTODY_EXECUTION_EVENT_SCHEMA_ID, CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION

        return {
            "event_schema_id": CUSTODY_EXECUTION_EVENT_SCHEMA_ID,
            "event_schema_version": CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION,
            "organization_id": self.organization_id,
            "execution_id": self.execution_id,
            "operation_id": self.operation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "sequence": self.sequence,
            "state": self.state,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True)
class CustodyExecutionResult:
    """Non-secret execution result applied atomically with terminal evidence."""

    lifecycle_effect: CustodyLifecycleEffect | None = None
    envelope_attestation: CustodyEnvelopeAttestation | None = None

    def __post_init__(self) -> None:
        if self.lifecycle_effect is not None and self.envelope_attestation is not None:
            raise ValueError("Custody execution result cannot mix lifecycle and routine evidence")


@dataclass(frozen=True)
class CustodyExecutionAttempt:
    """One immutable root plus its append-only execution event history."""

    organization_id: str
    execution_id: str
    operation_id: str
    operation_type: str
    target_kind: str
    target_sha256: str
    parameters_sha256: str
    protected_input_ref_sha256: str | None
    claimed_at: datetime
    events: tuple[CustodyExecutionEvent, ...]
    execution_schema_version: int = 2

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        _operation_id(self.execution_id)
        _operation_id(self.operation_id)
        if self.execution_schema_version not in {1, 2}:
            raise ValueError("Custody execution attempt schema_version is invalid")
        if self.operation_type not in CUSTODY_OPERATIONS:
            raise ValueError("Custody execution attempt operation_type is invalid")
        if self.execution_schema_version == 1 and self.operation_type not in CUSTODY_ROUTINE_OPERATIONS:
            raise ValueError("Custody execution attempt schema_version is invalid")
        if self.target_kind != _EXECUTION_TARGET_KINDS[self.operation_type]:
            raise ValueError("Custody execution attempt target_kind is invalid")
        _hex_digest(self.target_sha256, "target_sha256")
        _hex_digest(self.parameters_sha256, "parameters_sha256")
        if self.operation_type == "seal_envelope":
            _hex_digest(self.protected_input_ref_sha256, "protected_input_ref_sha256")
        elif self.protected_input_ref_sha256 is not None:
            raise ValueError("Only seal_envelope accepts a protected input reference digest")
        if self.claimed_at.tzinfo is None:
            raise ValueError("Custody execution attempt timestamp must be timezone-aware")
        if not self.events:
            raise ValueError("Custody execution attempt events are required")
        expected_sequence = 1
        for event in self.events:
            if (
                event.organization_id != self.organization_id
                or event.execution_id != self.execution_id
                or event.operation_id != self.operation_id
                or event.sequence != expected_sequence
            ):
                raise ValueError("Custody execution attempt event history is invalid")
            expected_sequence += 1
        if self.events[0].state != "pending" or len(self.events) > 2:
            raise ValueError("Custody execution attempt event history is invalid")

    @property
    def state(self) -> str:
        return self.events[-1].state

    def contract_record(self) -> dict[str, object]:
        from .contracts import CUSTODY_EXECUTION_SCHEMA_ID, CUSTODY_EXECUTION_SCHEMA_VERSION

        return {
            "execution_schema_id": CUSTODY_EXECUTION_SCHEMA_ID,
            "execution_schema_version": self.execution_schema_version,
            "organization_id": self.organization_id,
            "execution_id": self.execution_id,
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "target_kind": self.target_kind,
            "target_sha256": self.target_sha256,
            "parameters_sha256": self.parameters_sha256,
            "protected_input_ref_sha256": self.protected_input_ref_sha256,
            "claimed_at": self.claimed_at.isoformat(),
            "state": self.state,
        }


@dataclass(frozen=True)
class CustodyExecutionStatus:
    """Metadata-only executor status returned through custody administration."""

    organization_id: str
    attempt_count: int
    attempts: tuple[CustodyExecutionAttempt, ...]

    def __post_init__(self) -> None:
        _stable_identifier(self.organization_id, "organization_id")
        if isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int) or self.attempt_count < 0:
            raise ValueError("Custody execution attempt count is invalid")
        if self.attempt_count < len(self.attempts):
            raise ValueError("Custody execution attempt count is invalid")
        if any(attempt.organization_id != self.organization_id for attempt in self.attempts):
            raise ValueError("Custody execution status tenant is invalid")


class CustodyExecutionRepository(Protocol):
    """Persistence boundary used only by the isolated machine executor."""

    def claim(self, *, request: CustodyExecutionRequest) -> CustodyExecutionAttempt: ...

    def finalize(
        self,
        *,
        organization_id: str,
        execution_id: str,
        state: str,
        reason_code: str | None = None,
        result: CustodyExecutionResult | None = None,
    ) -> CustodyExecutionAttempt: ...

    def sweep_stale_pending(self, *, organization_ids: tuple[str, ...]) -> int: ...


def canonical_execution_descriptor(value: Mapping[str, Any]) -> bytes:
    """Serialize an in-memory request descriptor deterministically.

    This helper is public so an authorized operator can calculate exactly the
    target and parameter digests before submitting the content-free custody
    intent. It never persists or logs the descriptor.
    """

    return _canonical_descriptor(value, "descriptor")


def execution_descriptor_sha256(value: Mapping[str, Any]) -> str:
    return _sha256(canonical_execution_descriptor(value))


def protected_input_reference_sha256(value: str) -> str:
    return _sha256(_protected_input_reference(value).encode("utf-8"))


def _canonical_descriptor(value: Mapping[str, Any], field: str) -> bytes:
    if not isinstance(value, Mapping):
        raise ValueError(f"Custody execution {field} must be an object")
    normalized = _normalize_json(value, depth=0)
    try:
        encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:  # pragma: no cover - normalization guards this
        raise ValueError(f"Custody execution {field} is invalid") from error
    if not encoded or len(encoded) > _MAX_EXECUTION_DESCRIPTOR_BYTES:
        raise ValueError(f"Custody execution {field} is invalid")
    return encoded


def _normalize_json(value: Any, *, depth: int) -> Any:
    if depth > _MAX_EXECUTION_DESCRIPTOR_DEPTH:
        raise ValueError("Custody execution descriptor is too deep")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and (not value or len(value.encode("utf-8")) > _MAX_EXECUTION_DESCRIPTOR_BYTES):
            raise ValueError("Custody execution descriptor string is invalid")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or key in normalized or "\x00" in key:
                raise ValueError("Custody execution descriptor key is invalid")
            normalized[key] = _normalize_json(item, depth=depth + 1)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json(item, depth=depth + 1) for item in value]
    raise ValueError("Custody execution descriptor is invalid")


def _stable_identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError(f"Custody execution {field} is invalid")
    return value


def _operation_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Custody execution operation_id is invalid")
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Custody execution operation_id is invalid") from error
    return value


def _protected_input_reference(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > _MAX_PROTECTED_INPUT_REFERENCE_BYTES
        or any(character in value for character in "\x00\r\n")
    ):
        raise ValueError("Custody execution protected input reference is invalid")
    return value


def _hex_digest(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"Custody execution {field} is invalid")
    return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
