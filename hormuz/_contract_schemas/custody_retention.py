"""Strict custody-retention, deletion-block, and tenant-export contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .audit import validate_audit_chain_entry
from .common import ContractValidationError, _exact_keys, _value_integer, _value_mapping, _value_string
from .constants import (
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    CUSTODY_DELETION_EVENT_SCHEMA_ID,
    CUSTODY_DELETION_EVENT_SCHEMA_VERSION,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION,
)


_DELETION_BLOCK_REASONS = frozenset(
    {
        "retention_active",
        "legal_hold_active",
        "strong_approval_required",
    }
)


def validate_custody_deletion_event(value: Mapping[str, Any]) -> None:
    """Validate a governed deletion refusal; Hormuz never emits a bypass event."""

    _exact_keys(
        value,
        {
            "deletion_schema_id",
            "deletion_schema_version",
            "organization_id",
            "deletion_event_id",
            "occurred_at",
            "source_schema_id",
            "source_schema_version",
            "source_event_id",
            "source_retain_until",
            "source_legal_hold",
            "decision",
            "reason_code",
        },
    )
    if _value_string(value, "deletion_schema_id") != CUSTODY_DELETION_EVENT_SCHEMA_ID:
        raise ContractValidationError("custody deletion event schema_id is unsupported")
    if (
        _value_integer(value, "deletion_schema_version", minimum=1)
        != CUSTODY_DELETION_EVENT_SCHEMA_VERSION
    ):
        raise ContractValidationError("custody deletion event schema_version is unsupported")
    _value_string(value, "organization_id")
    _uuid(_value_string(value, "deletion_event_id"), "deletion_event_id")
    _value_string(value, "occurred_at")
    source_schema_id = _value_string(value, "source_schema_id")
    source_schema_version = _value_integer(value, "source_schema_version", minimum=1)
    _value_string(value, "source_event_id")
    _value_string(value, "source_retain_until")
    if not isinstance(value.get("source_legal_hold"), bool):
        raise ContractValidationError("custody deletion source_legal_hold must be a boolean")
    if value.get("decision") != "deletion_blocked":
        raise ContractValidationError("custody deletion decision is unsupported")
    if _value_string(value, "reason_code") not in _DELETION_BLOCK_REASONS:
        raise ContractValidationError("custody deletion reason_code is unsupported")
    if (source_schema_id, source_schema_version) not in {
        ("hormuz.custody-control-event", 1),
        ("hormuz.custody-execution-attempt", 2),
        ("hormuz.custody-execution-event", 1),
        ("hormuz.custody-lifecycle-event", 1),
        ("hormuz.custody-envelope-attestation", 1),
        ("hormuz.custody-deletion-event", 1),
    }:
        raise ContractValidationError("custody deletion source schema is unsupported")


def validate_custody_evidence_export(value: Mapping[str, Any]) -> None:
    """Validate a complete, tenant-scoped metadata-only custody export."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "generated_at",
            "records",
        },
    )
    if _value_string(value, "schema_id") != CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID:
        raise ContractValidationError("custody evidence export schema_id is unsupported")
    if (
        _value_integer(value, "schema_version", minimum=1)
        != CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION
    ):
        raise ContractValidationError("custody evidence export schema_version is unsupported")
    organization_id = _value_string(value, "organization_id")
    _value_string(value, "generated_at")
    records = value.get("records")
    if not isinstance(records, list):
        raise ContractValidationError("custody evidence export records must be an array")
    previous_position = (0, 0)
    source_ids: set[tuple[str, int, str]] = set()
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ContractValidationError(f"records[{index}] must be an object")
        _exact_keys(record, {"entry", "retain_until", "legal_hold"}, path=f"records[{index}]")
        entry = _value_mapping(record, "entry", path=f"records[{index}]")
        validate_audit_chain_entry(entry)
        if entry.get("schema_version") != AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
            raise ContractValidationError("custody evidence export must contain v2 chain entries")
        if entry.get("organization_id") != organization_id:
            raise ContractValidationError("custody evidence export tenant mismatch")
        _value_string(record, "retain_until", path=f"records[{index}]")
        if not isinstance(record.get("legal_hold"), bool):
            raise ContractValidationError(f"records[{index}].legal_hold must be a boolean")
        epoch = _value_integer(entry, "chain_epoch", minimum=1, path=f"records[{index}].entry")
        sequence = _value_integer(entry, "sequence", minimum=1, path=f"records[{index}].entry")
        position = (epoch, sequence)
        if position <= previous_position:
            raise ContractValidationError("custody evidence export records are not ordered")
        previous_position = position
        source_id = (
            _value_string(entry, "source_schema_id", path=f"records[{index}].entry"),
            _value_integer(entry, "source_schema_version", minimum=1, path=f"records[{index}].entry"),
            _value_string(entry, "source_event_id", path=f"records[{index}].entry"),
        )
        if source_id in source_ids:
            raise ContractValidationError("custody evidence export source identity is duplicated")
        source_ids.add(source_id)


def _uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ContractValidationError(f"{field} is invalid") from error
