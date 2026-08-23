"""Versioned, metadata-only commit-time audit-chain primitives.

This module deliberately has no database or Object Lock dependency.  Storage
adapters use it to construct a per-organization append-only chain in the same
transaction as each durable audit event; custody adapters use it to serialize
the small checkpoint artifact that is later retained externally.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import (
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_ID,
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_CHAIN_VERSION,
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    ContractValidationError,
    validate_audit_chain_checkpoint,
    validate_audit_chain_entry,
    validate_audit_event,
)


_MAX_CHECKPOINT_BYTES = 64 * 1024


class AuditChainError(RuntimeError):
    """A stable, content-free commit-time audit-chain failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AuditChainHead:
    """The current durable chain position for one organization."""

    organization_id: str
    chain_version: int
    chain_epoch: int
    sequence: int
    head_digest: str | None


@dataclass(frozen=True)
class AuditChainAnchorStatus:
    """Content-free anchor freshness state for one organization."""

    organization_id: str
    chain_epoch: int
    sequence: int
    latest_checkpoint_at: datetime | None
    oldest_unanchored_at: datetime | None
    overdue: bool


def canonical_json_bytes(value: object, *, code: str = "audit_chain_malformed") -> bytes:
    """Encode a strict, portable JSON representation for hashing or storage."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise AuditChainError(code) from None


def canonical_json_text(value: object, *, code: str = "audit_chain_malformed") -> str:
    return canonical_json_bytes(value, code=code).decode("utf-8")


def build_audit_chain_entry(
    event: Mapping[str, Any],
    *,
    chain_version: int,
    chain_epoch: int,
    sequence: int,
    previous_digest: str | None,
) -> dict[str, object]:
    """Return one strict entry whose digest binds its complete audit event."""

    normalized_event = _normalized_current_event(event)
    organization_id = normalized_event["organization_id"]
    assert isinstance(organization_id, str)
    _validate_chain_position(chain_version=chain_version, chain_epoch=chain_epoch, sequence=sequence)
    _validate_digest(previous_digest, allow_none=True, code="audit_chain_predecessor_invalid")
    digest = _entry_digest(
        organization_id=organization_id,
        chain_version=chain_version,
        chain_epoch=chain_epoch,
        sequence=sequence,
        previous_digest=previous_digest,
        event=normalized_event,
    )
    entry: dict[str, object] = {
        "schema_id": AUDIT_CHAIN_ENTRY_SCHEMA_ID,
        "schema_version": AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
        "organization_id": organization_id,
        "chain_version": chain_version,
        "chain_epoch": chain_epoch,
        "sequence": sequence,
        "previous_digest": previous_digest,
        "event_digest": digest,
        "event": normalized_event,
    }
    _validate_entry(entry)
    return entry


def verify_audit_chain_entry(
    entry: Mapping[str, Any],
    *,
    expected_organization_id: str,
    expected_chain_version: int,
    expected_chain_epoch: int,
    expected_sequence: int,
    expected_previous_digest: str | None,
    source_event: Mapping[str, Any] | None,
) -> str:
    """Validate one durable entry and its source-event correspondence."""

    _validate_entry(entry)
    if (
        entry.get("organization_id") != expected_organization_id
        or entry.get("chain_version") != expected_chain_version
        or entry.get("chain_epoch") != expected_chain_epoch
        or entry.get("sequence") != expected_sequence
    ):
        raise AuditChainError("audit_chain_sequence_invalid")
    if entry.get("previous_digest") != expected_previous_digest:
        raise AuditChainError("audit_chain_predecessor_invalid")
    event = entry["event"]
    assert isinstance(event, Mapping)
    normalized_event = _normalized_current_event(event)
    if normalized_event.get("organization_id") != expected_organization_id:
        raise AuditChainError("audit_chain_tenant_mismatch")
    if source_event is None:
        raise AuditChainError("audit_chain_source_event_missing")
    normalized_source = _normalized_current_event(source_event)
    if canonical_json_bytes(normalized_source) != canonical_json_bytes(normalized_event):
        raise AuditChainError("audit_chain_source_event_mismatch")
    actual = _entry_digest(
        organization_id=expected_organization_id,
        chain_version=expected_chain_version,
        chain_epoch=expected_chain_epoch,
        sequence=expected_sequence,
        previous_digest=expected_previous_digest,
        event=normalized_event,
    )
    digest = entry.get("event_digest")
    if not isinstance(digest, str) or not hmac.compare_digest(digest, actual):
        raise AuditChainError("audit_chain_digest_invalid")
    return digest


def build_audit_chain_checkpoint(
    head: AuditChainHead,
    *,
    created_at: datetime | None = None,
    checkpoint_id: str | None = None,
) -> dict[str, object]:
    """Build a canonical externally retainable checkpoint for a non-empty head."""

    _validate_chain_position(
        chain_version=head.chain_version,
        chain_epoch=head.chain_epoch,
        sequence=head.sequence,
    )
    if head.sequence < 1 or head.head_digest is None:
        raise AuditChainError("audit_chain_checkpoint_empty")
    _validate_digest(head.head_digest, allow_none=False, code="audit_chain_digest_invalid")
    identifier = checkpoint_id or str(uuid.uuid4())
    _validate_uuid(identifier, code="audit_chain_checkpoint_malformed")
    timestamp = _utc_timestamp(created_at or datetime.now(timezone.utc), code="audit_chain_checkpoint_malformed")
    checkpoint: dict[str, object] = {
        "schema_id": AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID,
        "schema_version": AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": identifier,
        "organization_id": head.organization_id,
        "chain_version": head.chain_version,
        "chain_epoch": head.chain_epoch,
        "sequence": head.sequence,
        "head_digest": head.head_digest,
        "created_at": timestamp,
    }
    _validate_checkpoint(checkpoint)
    return checkpoint


def serialize_audit_chain_checkpoint(checkpoint: Mapping[str, Any]) -> bytes:
    """Strictly validate then canonically serialize an external checkpoint."""

    _validate_checkpoint(checkpoint)
    return canonical_json_bytes(dict(checkpoint), code="audit_chain_checkpoint_malformed")


def parse_audit_chain_checkpoint(value: bytes | str) -> dict[str, object]:
    """Strictly parse and validate one content-free checkpoint artifact."""

    try:
        raw = value.encode("utf-8") if isinstance(value, str) else value
    except UnicodeEncodeError:
        raise AuditChainError("audit_chain_checkpoint_malformed") from None
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_CHECKPOINT_BYTES:
        raise AuditChainError("audit_chain_checkpoint_malformed")
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_members,
            parse_constant=_reject_nonfinite,
        )
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise AuditChainError("audit_chain_checkpoint_malformed") from None
    if not isinstance(parsed, dict):
        raise AuditChainError("audit_chain_checkpoint_malformed")
    _validate_checkpoint(parsed)
    canonical = serialize_audit_chain_checkpoint(parsed)
    if not hmac.compare_digest(raw, canonical):
        raise AuditChainError("audit_chain_checkpoint_noncanonical")
    return parsed


def audit_chain_checkpoint_summary(checkpoint: Mapping[str, Any]) -> tuple[str, str, int, int, str]:
    """Return safe sink metadata after strict checkpoint validation."""

    _validate_checkpoint(checkpoint)
    identifier = checkpoint["checkpoint_id"]
    organization_id = checkpoint["organization_id"]
    epoch = checkpoint["chain_epoch"]
    sequence = checkpoint["sequence"]
    head_digest = checkpoint["head_digest"]
    assert isinstance(identifier, str)
    assert isinstance(organization_id, str)
    assert isinstance(epoch, int)
    assert isinstance(sequence, int)
    assert isinstance(head_digest, str)
    return identifier, organization_id, epoch, sequence, head_digest


def audit_chain_checkpoint_position(checkpoint: Mapping[str, Any]) -> tuple[str, str, int, int, int, str]:
    """Return the complete signed position represented by a checkpoint."""

    _validate_checkpoint(checkpoint)
    identifier, organization_id, epoch, sequence, head_digest = audit_chain_checkpoint_summary(checkpoint)
    chain_version = checkpoint["chain_version"]
    assert isinstance(chain_version, int)
    return identifier, organization_id, chain_version, epoch, sequence, head_digest


def _entry_digest(
    *,
    organization_id: str,
    chain_version: int,
    chain_epoch: int,
    sequence: int,
    previous_digest: str | None,
    event: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "domain": "hormuz.commit-audit-chain-entry.v1",
                "organization_id": organization_id,
                "chain_version": chain_version,
                "chain_epoch": chain_epoch,
                "sequence": sequence,
                "previous_digest": previous_digest,
                "event": dict(event),
            }
        )
    ).hexdigest()


def _normalized_current_event(event: Mapping[str, Any]) -> dict[str, object]:
    try:
        parsed = json.loads(canonical_json_bytes(dict(event)).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError
        validate_audit_event(parsed)
    except (AuditChainError, ContractValidationError, TypeError, ValueError, json.JSONDecodeError):
        raise AuditChainError("audit_chain_event_malformed") from None
    if (
        parsed.get("schema_id") != AUDIT_EVENT_SCHEMA_ID
        or parsed.get("schema_version") != AUDIT_EVENT_SCHEMA_VERSION
    ):
        raise AuditChainError("audit_chain_event_schema_unsupported")
    organization_id = parsed.get("organization_id")
    if not isinstance(organization_id, str) or not organization_id:
        raise AuditChainError("audit_chain_event_malformed")
    return parsed


def _validate_entry(value: Mapping[str, Any]) -> None:
    try:
        validate_audit_chain_entry(value)
    except ContractValidationError:
        raise AuditChainError("audit_chain_entry_malformed") from None


def _validate_checkpoint(value: Mapping[str, Any]) -> None:
    try:
        validate_audit_chain_checkpoint(value)
    except ContractValidationError:
        raise AuditChainError("audit_chain_checkpoint_malformed") from None
    _validate_uuid(value.get("checkpoint_id"), code="audit_chain_checkpoint_malformed")


def _validate_chain_position(*, chain_version: int, chain_epoch: int, sequence: int) -> None:
    if (
        isinstance(chain_version, bool)
        or not isinstance(chain_version, int)
        or chain_version != AUDIT_CHAIN_VERSION
        or isinstance(chain_epoch, bool)
        or not isinstance(chain_epoch, int)
        or chain_epoch < 1
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 0
    ):
        raise AuditChainError("audit_chain_position_invalid")


def _validate_digest(value: object, *, allow_none: bool, code: str) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise AuditChainError(code)


def _validate_uuid(value: object, *, code: str) -> None:
    try:
        parsed = uuid.UUID(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        raise AuditChainError(code) from None
    if str(parsed) != str(value).lower():
        raise AuditChainError(code)


def _utc_timestamp(value: datetime, *, code: str) -> str:
    if value.tzinfo is None:
        raise AuditChainError(code)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicate_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")
