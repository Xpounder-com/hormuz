"""Validators for durable, metadata-only custody-executor evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .common import ContractValidationError, _exact_keys, _nullable_string, _sha256_digest, _value_integer, _value_string
from .constants import (
    CUSTODY_EXECUTION_EVENT_SCHEMA_ID,
    CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION,
    CUSTODY_EXECUTION_SCHEMA_ID,
    CUSTODY_EXECUTION_SCHEMA_VERSION,
    _CUSTODY_EXECUTION_FAILURE_REASONS,
    _CUSTODY_EXECUTION_STATES,
    _CUSTODY_EXECUTION_UNKNOWN_REASONS,
    _CUSTODY_LIFECYCLE_OPERATION_TYPES,
)


_TARGET_KINDS = {
    "seal_envelope": "envelope",
    "rewrap_envelope": "envelope",
    "verify_restore": "restore",
    "retire_envelope": "envelope",
    "disable_provider_credential": "provider_credential",
    "retire_key_reference": "key_reference",
    "resolve_recovery": "recovery",
}


def validate_custody_execution_attempt(value: Mapping[str, Any]) -> None:
    """Validate one immutable custody-executor attempt root."""

    _exact_keys(
        value,
        {
            "execution_schema_id",
            "execution_schema_version",
            "organization_id",
            "execution_id",
            "operation_id",
            "operation_type",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "claimed_at",
            "state",
        },
    )
    if _value_string(value, "execution_schema_id") != CUSTODY_EXECUTION_SCHEMA_ID:
        raise ContractValidationError("custody execution schema_id is unsupported")
    schema_version = _value_integer(value, "execution_schema_version", minimum=1)
    if schema_version not in {1, CUSTODY_EXECUTION_SCHEMA_VERSION}:
        raise ContractValidationError("custody execution schema_version is unsupported")
    _value_string(value, "organization_id")
    _uuid(_value_string(value, "execution_id"), "execution_id")
    _uuid(_value_string(value, "operation_id"), "operation_id")
    operation_type = _value_string(value, "operation_type")
    if operation_type not in _TARGET_KINDS:
        raise ContractValidationError("custody execution operation_type is unsupported")
    if schema_version == 1 and operation_type in _CUSTODY_LIFECYCLE_OPERATION_TYPES:
        raise ContractValidationError("custody execution schema_version is unsupported")
    if _value_string(value, "target_kind") != _TARGET_KINDS[operation_type]:
        raise ContractValidationError("custody execution target_kind is invalid")
    _sha256_digest(_value_string(value, "target_sha256"), "target_sha256")
    _sha256_digest(_value_string(value, "parameters_sha256"), "parameters_sha256")
    protected_input_ref_sha256 = _nullable_string(value, "protected_input_ref_sha256")
    if operation_type == "seal_envelope":
        if protected_input_ref_sha256 is None:
            raise ContractValidationError("seal_envelope requires protected_input_ref_sha256")
        _sha256_digest(protected_input_ref_sha256, "protected_input_ref_sha256")
    elif protected_input_ref_sha256 is not None:
        raise ContractValidationError("protected_input_ref_sha256 is only valid for seal_envelope")
    _value_string(value, "claimed_at")
    if _value_string(value, "state") not in _CUSTODY_EXECUTION_STATES:
        raise ContractValidationError("custody execution state is unsupported")


def validate_custody_execution_event(value: Mapping[str, Any]) -> None:
    """Validate one immutable custody-executor state transition."""

    _exact_keys(
        value,
        {
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "execution_id",
            "operation_id",
            "occurred_at",
            "sequence",
            "state",
            "reason_code",
        },
    )
    if _value_string(value, "event_schema_id") != CUSTODY_EXECUTION_EVENT_SCHEMA_ID:
        raise ContractValidationError("custody execution event schema_id is unsupported")
    if _value_integer(value, "event_schema_version", minimum=1) != CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION:
        raise ContractValidationError("custody execution event schema_version is unsupported")
    _value_string(value, "organization_id")
    _uuid(_value_string(value, "execution_id"), "execution_id")
    _uuid(_value_string(value, "operation_id"), "operation_id")
    _value_string(value, "occurred_at")
    sequence = _value_integer(value, "sequence", minimum=1)
    state = _value_string(value, "state")
    if state not in _CUSTODY_EXECUTION_STATES:
        raise ContractValidationError("custody execution event state is unsupported")
    reason_code = _nullable_string(value, "reason_code")
    if sequence == 1:
        if state != "pending" or reason_code is not None:
            raise ContractValidationError("pending custody execution event is malformed")
        return
    if sequence != 2 or state == "pending":
        raise ContractValidationError("terminal custody execution event is malformed")
    if state == "succeeded":
        if reason_code is not None:
            raise ContractValidationError("successful custody execution event is malformed")
        return
    if state == "failed":
        if reason_code not in _CUSTODY_EXECUTION_FAILURE_REASONS:
            raise ContractValidationError("failed custody execution event is malformed")
        return
    if reason_code not in _CUSTODY_EXECUTION_UNKNOWN_REASONS:
        raise ContractValidationError("unknown custody execution event is malformed")


def _uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ContractValidationError(f"{field} is invalid") from error
