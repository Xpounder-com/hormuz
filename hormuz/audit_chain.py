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
    AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_CHAIN_VERSION,
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    ContractValidationError,
    validate_audit_chain_checkpoint,
    validate_audit_chain_entry,
    validate_audit_event,
    validate_custody_control_event,
    validate_custody_deletion_event,
    validate_custody_envelope_attestation,
    validate_custody_execution_attempt,
    validate_custody_execution_event,
    validate_custody_lifecycle_event,
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


@dataclass(frozen=True)
class AuditChainSource:
    """One finite, metadata-only v2 source identity.

    Version 1 entries have no source wrapper and remain limited to current
    gateway audit events. Version 2 adds this identity for the reviewed finite
    custody and finance-attempt source union without opening the chain to
    arbitrary JSON records.
    """

    schema_id: str
    schema_version: int
    event_id: str


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
    entry_schema_version: int = AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION,
    source: AuditChainSource | None = None,
) -> dict[str, object]:
    """Return one strict entry whose digest binds its complete source event.

    The default deliberately remains the established v1 encoding so existing
    usage/security writers retain their durable format. Reviewed finite-source
    writers opt into the strict v2 union through ``source``.
    """

    _validate_chain_position(chain_version=chain_version, chain_epoch=chain_epoch, sequence=sequence)
    _validate_digest(previous_digest, allow_none=True, code="audit_chain_predecessor_invalid")
    if entry_schema_version == AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION:
        if source is not None:
            raise AuditChainError("audit_chain_source_schema_unsupported")
        normalized_event = _normalized_v1_audit_event(event)
        organization_id = normalized_event["organization_id"]
        assert isinstance(organization_id, str)
        digest = _entry_digest_v1(
            organization_id=organization_id,
            chain_version=chain_version,
            chain_epoch=chain_epoch,
            sequence=sequence,
            previous_digest=previous_digest,
            event=normalized_event,
        )
        entry: dict[str, object] = {
            "schema_id": AUDIT_CHAIN_ENTRY_SCHEMA_ID,
            "schema_version": AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION,
            "organization_id": organization_id,
            "chain_version": chain_version,
            "chain_epoch": chain_epoch,
            "sequence": sequence,
            "previous_digest": previous_digest,
            "event_digest": digest,
            "event": normalized_event,
        }
    elif entry_schema_version == AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
        if source is None:
            raise AuditChainError("audit_chain_source_schema_unsupported")
        normalized_event = _normalized_v2_source_event(event, source=source)
        organization_id = normalized_event["organization_id"]
        assert isinstance(organization_id, str)
        digest = _entry_digest_v2(
            organization_id=organization_id,
            chain_version=chain_version,
            chain_epoch=chain_epoch,
            sequence=sequence,
            previous_digest=previous_digest,
            source=source,
            event=normalized_event,
        )
        entry = {
            "schema_id": AUDIT_CHAIN_ENTRY_SCHEMA_ID,
            "schema_version": AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
            "organization_id": organization_id,
            "chain_version": chain_version,
            "chain_epoch": chain_epoch,
            "sequence": sequence,
            "previous_digest": previous_digest,
            "event_digest": digest,
            "source_schema_id": source.schema_id,
            "source_schema_version": source.schema_version,
            "source_event_id": source.event_id,
            "event": normalized_event,
        }
    else:
        raise AuditChainError("audit_chain_entry_schema_unsupported")
    _validate_entry(entry)
    return entry


def build_custody_audit_chain_entry(
    event: Mapping[str, Any],
    *,
    source_schema_id: str,
    source_schema_version: int,
    source_event_id: str,
    chain_version: int,
    chain_epoch: int,
    sequence: int,
    previous_digest: str | None,
) -> dict[str, object]:
    """Build a v2 entry from the finite custody-evidence source union."""

    return build_audit_chain_entry(
        event,
        chain_version=chain_version,
        chain_epoch=chain_epoch,
        sequence=sequence,
        previous_digest=previous_digest,
        entry_schema_version=AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
        source=AuditChainSource(
            schema_id=source_schema_id,
            schema_version=source_schema_version,
            event_id=source_event_id,
        ),
    )


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
    schema_version = entry.get("schema_version")
    if schema_version == AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION:
        normalized_event = _normalized_v1_audit_event(event)
        if normalized_event.get("organization_id") != expected_organization_id:
            raise AuditChainError("audit_chain_tenant_mismatch")
        if source_event is None:
            raise AuditChainError("audit_chain_source_event_missing")
        normalized_source = _normalized_v1_audit_event(source_event)
        if canonical_json_bytes(normalized_source) != canonical_json_bytes(normalized_event):
            raise AuditChainError("audit_chain_source_event_mismatch")
        actual = _entry_digest_v1(
            organization_id=expected_organization_id,
            chain_version=expected_chain_version,
            chain_epoch=expected_chain_epoch,
            sequence=expected_sequence,
            previous_digest=expected_previous_digest,
            event=normalized_event,
        )
    elif schema_version == AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
        source = audit_chain_entry_source(entry)
        normalized_event = _normalized_v2_source_event(event, source=source)
        if normalized_event.get("organization_id") != expected_organization_id:
            raise AuditChainError("audit_chain_tenant_mismatch")
        if source_event is None:
            raise AuditChainError("audit_chain_source_event_missing")
        normalized_source = _normalized_v2_source_event(source_event, source=source)
        if canonical_json_bytes(normalized_source) != canonical_json_bytes(normalized_event):
            raise AuditChainError("audit_chain_source_event_mismatch")
        actual = _entry_digest_v2(
            organization_id=expected_organization_id,
            chain_version=expected_chain_version,
            chain_epoch=expected_chain_epoch,
            sequence=expected_sequence,
            previous_digest=expected_previous_digest,
            source=source,
            event=normalized_event,
        )
    else:  # Defensive after strict validator so callers receive one stable code.
        raise AuditChainError("audit_chain_entry_schema_unsupported")
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


def audit_chain_entry_source(entry: Mapping[str, Any]) -> AuditChainSource:
    """Return a strict source identity from one supported v2 entry."""

    _validate_entry(entry)
    if entry.get("schema_version") != AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
        raise AuditChainError("audit_chain_entry_schema_unsupported")
    source_schema_id = entry.get("source_schema_id")
    source_schema_version = entry.get("source_schema_version")
    source_event_id = entry.get("source_event_id")
    if (
        not isinstance(source_schema_id, str)
        or isinstance(source_schema_version, bool)
        or not isinstance(source_schema_version, int)
        or not isinstance(source_event_id, str)
    ):
        raise AuditChainError("audit_chain_source_schema_unsupported")
    return AuditChainSource(
        schema_id=source_schema_id,
        schema_version=source_schema_version,
        event_id=source_event_id,
    )


def _entry_digest_v1(
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


def _entry_digest_v2(
    *,
    organization_id: str,
    chain_version: int,
    chain_epoch: int,
    sequence: int,
    previous_digest: str | None,
    source: AuditChainSource,
    event: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "domain": "hormuz.commit-audit-chain-entry.v2",
                "organization_id": organization_id,
                "chain_version": chain_version,
                "chain_epoch": chain_epoch,
                "sequence": sequence,
                "previous_digest": previous_digest,
                "source_schema_id": source.schema_id,
                "source_schema_version": source.schema_version,
                "source_event_id": source.event_id,
                "event": dict(event),
            }
        )
    ).hexdigest()


def _normalized_v1_audit_event(event: Mapping[str, Any]) -> dict[str, object]:
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


def _normalized_v2_source_event(event: Mapping[str, Any], *, source: AuditChainSource) -> dict[str, object]:
    try:
        parsed = json.loads(canonical_json_bytes(dict(event)).decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError
        if source.schema_id == "hormuz.custody-control-event" and source.schema_version == 1:
            validate_custody_control_event(parsed)
            _validate_uuid(source.event_id, code="audit_chain_event_malformed")
            expected_id = source.event_id
        elif source.schema_id == "hormuz.custody-execution-attempt" and source.schema_version == 2:
            validate_custody_execution_attempt(parsed)
            expected_id = parsed.get("execution_id")
        elif source.schema_id == "hormuz.custody-execution-event" and source.schema_version == 1:
            validate_custody_execution_event(parsed)
            execution_id = parsed.get("execution_id")
            sequence = parsed.get("sequence")
            expected_id = f"{execution_id}:{sequence}"
        elif source.schema_id == "hormuz.custody-lifecycle-event" and source.schema_version == 1:
            validate_custody_lifecycle_event(parsed)
            expected_id = parsed.get("lifecycle_event_id")
        elif source.schema_id == "hormuz.custody-envelope-attestation" and source.schema_version == 1:
            validate_custody_envelope_attestation(parsed)
            expected_id = f"{parsed.get('execution_id')}:{parsed.get('attestation_kind')}"
        elif source.schema_id == "hormuz.custody-deletion-event" and source.schema_version == 1:
            validate_custody_deletion_event(parsed)
            expected_id = parsed.get("deletion_event_id")
        elif source.schema_id == "hormuz.finance-attempt-evidence" and source.schema_version == 1:
            from .finance_attempts import validate_finance_attempt_event

            validate_finance_attempt_event(parsed)
            expected_id = parsed.get("evidence_event_id")
        elif source.schema_version == 1:
            from .finance_collection import (
                FINANCE_COLLECTION_SOURCE_SCHEMA_IDS,
                FinanceCollectionError,
                finance_collection_source_identity,
            )

            if source.schema_id not in FINANCE_COLLECTION_SOURCE_SCHEMA_IDS:
                raise AuditChainError("audit_chain_event_schema_unsupported")
            try:
                expected_id = finance_collection_source_identity(
                    source.schema_id,
                    parsed,
                )
            except FinanceCollectionError:
                raise AuditChainError("audit_chain_event_malformed") from None
        else:
            raise AuditChainError("audit_chain_event_schema_unsupported")
    except (AuditChainError, ContractValidationError, TypeError, ValueError, json.JSONDecodeError):
        raise AuditChainError("audit_chain_event_malformed") from None
    if source.event_id != expected_id:
        raise AuditChainError("audit_chain_event_malformed")
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
