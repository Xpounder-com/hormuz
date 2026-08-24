"""Custody authority, approval-status, and durable-event validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_integer,
    _nullable_string,
    _sha256_digest,
    _value_integer,
    _value_string,
)
from .constants import (
    CUSTODY_CONTROL_EVENT_SCHEMA_ID,
    CUSTODY_CONTROL_EVENT_SCHEMA_VERSION,
    _CUSTODY_CONTROL_EVENT_TYPES,
    _CUSTODY_OPERATION_TYPES,
)
from .custody_execution import validate_custody_execution_attempt, validate_custody_execution_event


_TARGET_KINDS = {
    "seal_envelope": "envelope",
    "rewrap_envelope": "envelope",
    "verify_restore": "restore",
    "retire_envelope": "envelope",
    "disable_provider_credential": "provider_credential",
    "retire_key_reference": "key_reference",
    "resolve_recovery": "recovery",
}
_ROUTINE_OPERATIONS = frozenset({"seal_envelope", "rewrap_envelope", "verify_restore"})


def validate_custody_control_event(value: Mapping[str, Any]) -> None:
    """Validate one strict, metadata-only custody-control evidence row."""

    _exact_keys(
        value,
        {
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "occurred_at",
            "event_type",
            "actor_kind",
            "actor_identity_key",
            "target_identity_key",
            "operation_id",
            "operation_type",
            "risk_level",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "required_approvals",
            "approval_count",
            "expires_at",
        },
    )
    if _value_string(value, "event_schema_id") != CUSTODY_CONTROL_EVENT_SCHEMA_ID:
        raise ContractValidationError("custody control event schema_id is unsupported")
    if _value_integer(value, "event_schema_version", minimum=1) != CUSTODY_CONTROL_EVENT_SCHEMA_VERSION:
        raise ContractValidationError("custody control event schema_version is unsupported")
    _value_string(value, "organization_id")
    _value_string(value, "occurred_at")
    event_type = _value_string(value, "event_type")
    if event_type not in _CUSTODY_CONTROL_EVENT_TYPES:
        raise ContractValidationError("custody control event_type is unsupported")
    actor_kind = _value_string(value, "actor_kind")
    _identity_key(actor_kind, _value_string(value, "actor_identity_key"), "actor_identity_key")
    target_identity_key = _nullable_string(value, "target_identity_key")

    operation_id = _nullable_string(value, "operation_id")
    operation_type = _nullable_string(value, "operation_type")
    risk_level = _nullable_string(value, "risk_level")
    target_kind = _nullable_string(value, "target_kind")
    target_sha256 = _nullable_string(value, "target_sha256")
    parameters_sha256 = _nullable_string(value, "parameters_sha256")
    protected_input_ref_sha256 = _nullable_string(value, "protected_input_ref_sha256")
    required_approvals = _nullable_integer(value, "required_approvals", minimum=1)
    approval_count = _nullable_integer(value, "approval_count", minimum=0)
    expires_at = _nullable_string(value, "expires_at")

    if event_type == "bootstrap_initialized":
        if target_identity_key is not None:
            raise ContractValidationError("custody bootstrap event cannot target an administrator")
        _require_no_operation(
            operation_id,
            operation_type,
            risk_level,
            target_kind,
            target_sha256,
            parameters_sha256,
            protected_input_ref_sha256,
            required_approvals,
            approval_count,
            expires_at,
        )
        return
    if event_type in {"administrator_granted", "administrator_revoked"}:
        if target_identity_key is None:
            raise ContractValidationError("custody administrator event requires target_identity_key")
        _opaque_identity_key(target_identity_key, "target_identity_key")
        _require_no_operation(
            operation_id,
            operation_type,
            risk_level,
            target_kind,
            target_sha256,
            parameters_sha256,
            protected_input_ref_sha256,
            required_approvals,
            approval_count,
            expires_at,
        )
        return
    if target_identity_key is not None:
        raise ContractValidationError("custody operation event cannot target an administrator")
    _validate_operation_fields(
        operation_id=operation_id,
        operation_type=operation_type,
        risk_level=risk_level,
        target_kind=target_kind,
        target_sha256=target_sha256,
        parameters_sha256=parameters_sha256,
        protected_input_ref_sha256=protected_input_ref_sha256,
        required_approvals=required_approvals,
        approval_count=approval_count,
        expires_at=expires_at,
    )
    assert required_approvals is not None and approval_count is not None
    if event_type == "operation_requested" and approval_count != 0:
        raise ContractValidationError("operation_requested approval_count must be zero")
    if event_type == "operation_approved" and not 1 <= approval_count <= required_approvals:
        raise ContractValidationError("operation_approved approval_count is invalid")
    if event_type == "operation_authorized" and approval_count != required_approvals:
        raise ContractValidationError("operation_authorized requires every approval")


def _validate_custody_control_status_v1(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "initialized",
            "administrators",
            "operation_count",
            "operations",
        },
    )
    _value_string(value, "organization_id")
    if not isinstance(value.get("initialized"), bool):
        raise ContractValidationError("initialized must be a boolean")
    administrators = value.get("administrators")
    if not isinstance(administrators, list):
        raise ContractValidationError("administrators must be an array")
    for index, administrator in enumerate(administrators):
        path = f"administrators[{index}]"
        if not isinstance(administrator, Mapping):
            raise ContractValidationError(f"{path} must be an object")
        _validate_administrator(administrator, path=path)
    operation_count = _value_integer(value, "operation_count", minimum=0)
    operations = value.get("operations")
    if not isinstance(operations, list):
        raise ContractValidationError("operations must be an array")
    if operation_count < len(operations):
        raise ContractValidationError("operation_count cannot be smaller than operations")
    for index, operation in enumerate(operations):
        path = f"operations[{index}]"
        if not isinstance(operation, Mapping):
            raise ContractValidationError(f"{path} must be an object")
        _validate_status_operation(operation, path=path)


def _validate_custody_control_status_v2(value: Mapping[str, Any]) -> None:
    _validate_custody_control_status_with_execution(value, allow_lifecycle_execution=False)


def _validate_custody_control_status_v3(value: Mapping[str, Any]) -> None:
    _validate_custody_control_status_with_execution(value, allow_lifecycle_execution=True)


def _validate_custody_control_status_with_execution(
    value: Mapping[str, Any],
    *,
    allow_lifecycle_execution: bool,
) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "initialized",
            "administrators",
            "operation_count",
            "operations",
            "execution_attempt_count",
            "execution_attempts",
        },
    )
    _validate_custody_control_status_v1(
        {
            key: value[key]
            for key in {
                "schema_id",
                "schema_version",
                "organization_id",
                "initialized",
                "administrators",
                "operation_count",
                "operations",
            }
        }
    )
    attempt_count = _value_integer(value, "execution_attempt_count", minimum=0)
    attempts = value.get("execution_attempts")
    if not isinstance(attempts, list):
        raise ContractValidationError("execution_attempts must be an array")
    if attempt_count < len(attempts):
        raise ContractValidationError("execution_attempt_count cannot be smaller than execution_attempts")
    organization_id = _value_string(value, "organization_id")
    for index, attempt in enumerate(attempts):
        path = f"execution_attempts[{index}]"
        if not isinstance(attempt, Mapping):
            raise ContractValidationError(f"{path} must be an object")
        _validate_execution_status_attempt(
            attempt,
            organization_id=organization_id,
            path=path,
            allow_lifecycle_execution=allow_lifecycle_execution,
        )


def _validate_execution_status_attempt(
    value: Mapping[str, Any],
    *,
    organization_id: str,
    path: str,
    allow_lifecycle_execution: bool,
) -> None:
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
            "events",
        },
        path=path,
    )
    attempt_record = {key: value[key] for key in value if key != "events"}
    validate_custody_execution_attempt(attempt_record)
    if not allow_lifecycle_execution and attempt_record.get("execution_schema_version") != 1:
        raise ContractValidationError(f"{path} cannot contain governed lifecycle execution evidence")
    if value.get("organization_id") != organization_id:
        raise ContractValidationError(f"{path}.organization_id is invalid")
    events = value.get("events")
    if not isinstance(events, list) or not events or len(events) > 2:
        raise ContractValidationError(f"{path}.events is invalid")
    expected_sequence = 1
    state: str | None = None
    for event_index, event in enumerate(events):
        event_path = f"{path}.events[{event_index}]"
        if not isinstance(event, Mapping):
            raise ContractValidationError(f"{event_path} must be an object")
        validate_custody_execution_event(event)
        if (
            event.get("organization_id") != organization_id
            or event.get("execution_id") != value.get("execution_id")
            or event.get("operation_id") != value.get("operation_id")
            or event.get("sequence") != expected_sequence
        ):
            raise ContractValidationError(f"{event_path} does not match its execution attempt")
        expected_sequence += 1
        state = str(event.get("state"))
    if value.get("state") != state:
        raise ContractValidationError(f"{path}.state does not match its event history")


def _validate_administrator(value: Mapping[str, Any], *, path: str) -> None:
    _exact_keys(value, {"authentication_kind", "actor_id", "issuer", "subject"}, path=path)
    kind = _value_string(value, "authentication_kind", path=path)
    actor_id = _nullable_string(value, "actor_id", path=path)
    issuer = _nullable_string(value, "issuer", path=path)
    subject = _nullable_string(value, "subject", path=path)
    if kind == "static" and actor_id and issuer is None and subject is None:
        return
    if kind == "oidc" and actor_id is None and issuer and subject:
        return
    raise ContractValidationError(f"{path} identity fields are invalid")


def _validate_status_operation(value: Mapping[str, Any], *, path: str) -> None:
    _exact_keys(
        value,
        {
            "operation_id",
            "operation_type",
            "risk_level",
            "target_kind",
            "target_sha256",
            "parameters_sha256",
            "protected_input_ref_sha256",
            "state",
            "required_approvals",
            "approval_count",
            "created_at",
            "expires_at",
            "authorized_at",
            "requested_by_kind",
            "requested_by_identity_key",
            "approvals",
        },
        path=path,
    )
    operation_id = _value_string(value, "operation_id", path=path)
    operation_type = _value_string(value, "operation_type", path=path)
    risk_level = _value_string(value, "risk_level", path=path)
    target_kind = _value_string(value, "target_kind", path=path)
    target_sha256 = _value_string(value, "target_sha256", path=path)
    parameters_sha256 = _value_string(value, "parameters_sha256", path=path)
    protected_input_ref_sha256 = _nullable_string(value, "protected_input_ref_sha256", path=path)
    required_approvals = _value_integer(value, "required_approvals", minimum=1, path=path)
    approval_count = _value_integer(value, "approval_count", minimum=1, path=path)
    _validate_operation_fields(
        operation_id=operation_id,
        operation_type=operation_type,
        risk_level=risk_level,
        target_kind=target_kind,
        target_sha256=target_sha256,
        parameters_sha256=parameters_sha256,
        protected_input_ref_sha256=protected_input_ref_sha256,
        required_approvals=required_approvals,
        approval_count=approval_count,
        expires_at=_value_string(value, "expires_at", path=path),
    )
    state = _value_string(value, "state", path=path)
    if state not in {"pending", "authorized", "expired"}:
        raise ContractValidationError(f"{path}.state is unsupported")
    _value_string(value, "created_at", path=path)
    authorized_at = _nullable_string(value, "authorized_at", path=path)
    if state == "pending":
        if authorized_at is not None or approval_count >= required_approvals:
            raise ContractValidationError(f"{path} pending authorization state is invalid")
    elif state == "authorized":
        if authorized_at is None or approval_count != required_approvals:
            raise ContractValidationError(f"{path} authorized state is invalid")
    elif (authorized_at is None) != (approval_count < required_approvals):
        raise ContractValidationError(f"{path} expired authorization history is invalid")
    requested_by_kind = _value_string(value, "requested_by_kind", path=path)
    _identity_key(
        requested_by_kind,
        _value_string(value, "requested_by_identity_key", path=path),
        f"{path}.requested_by_identity_key",
    )
    approvals = value.get("approvals")
    if not isinstance(approvals, list) or len(approvals) != approval_count:
        raise ContractValidationError(f"{path}.approvals must match approval_count")
    approvers: set[str] = set()
    for index, approval in enumerate(approvals):
        approval_path = f"{path}.approvals[{index}]"
        if not isinstance(approval, Mapping):
            raise ContractValidationError(f"{approval_path} must be an object")
        _exact_keys(approval, {"approver_kind", "approver_identity_key", "approved_at"}, path=approval_path)
        kind = _value_string(approval, "approver_kind", path=approval_path)
        key = _value_string(approval, "approver_identity_key", path=approval_path)
        _identity_key(kind, key, f"{approval_path}.approver_identity_key")
        if key in approvers:
            raise ContractValidationError(f"{path}.approvals must use distinct administrators")
        approvers.add(key)
        _value_string(approval, "approved_at", path=approval_path)


def _validate_operation_fields(
    *,
    operation_id: str | None,
    operation_type: str | None,
    risk_level: str | None,
    target_kind: str | None,
    target_sha256: str | None,
    parameters_sha256: str | None,
    protected_input_ref_sha256: str | None,
    required_approvals: int | None,
    approval_count: int | None,
    expires_at: str | None,
) -> None:
    if None in {
        operation_id,
        operation_type,
        risk_level,
        target_kind,
        target_sha256,
        parameters_sha256,
        required_approvals,
        approval_count,
        expires_at,
    }:
        raise ContractValidationError("custody operation event fields are required")
    assert operation_id is not None and operation_type is not None
    assert risk_level is not None and target_kind is not None
    assert target_sha256 is not None and parameters_sha256 is not None
    assert required_approvals is not None and approval_count is not None
    _uuid(operation_id, "operation_id")
    if operation_type not in _CUSTODY_OPERATION_TYPES:
        raise ContractValidationError("custody operation_type is unsupported")
    expected_risk = "routine" if operation_type in _ROUTINE_OPERATIONS else "destructive"
    if risk_level != expected_risk:
        raise ContractValidationError("custody risk_level is invalid")
    if target_kind != _TARGET_KINDS[operation_type]:
        raise ContractValidationError("custody target_kind is invalid")
    _sha256_digest(target_sha256, "target_sha256")
    _sha256_digest(parameters_sha256, "parameters_sha256")
    if operation_type == "seal_envelope":
        if protected_input_ref_sha256 is None:
            raise ContractValidationError("seal_envelope requires protected_input_ref_sha256")
        _sha256_digest(protected_input_ref_sha256, "protected_input_ref_sha256")
    elif protected_input_ref_sha256 is not None:
        raise ContractValidationError("protected_input_ref_sha256 is only valid for seal_envelope")
    expected_approvals = 1 if expected_risk == "routine" else 2
    if required_approvals != expected_approvals or approval_count > required_approvals:
        raise ContractValidationError("custody approval requirements are invalid")


def _require_no_operation(*values: object) -> None:
    if any(value is not None for value in values):
        raise ContractValidationError("custody administrator event cannot contain operation fields")


def _identity_key(kind: str, value: str, field: str) -> None:
    if kind not in {"static", "oidc"}:
        raise ContractValidationError(f"{field} authentication kind is unsupported")
    prefix = f"{kind}:"
    if not value.startswith(prefix):
        raise ContractValidationError(f"{field} is invalid")
    _sha256_digest(value.removeprefix(prefix), field)


def _opaque_identity_key(value: str, field: str) -> None:
    if not (value.startswith("static:") or value.startswith("oidc:")):
        raise ContractValidationError(f"{field} is invalid")
    _sha256_digest(value.split(":", 1)[1], field)


def _uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ContractValidationError(f"{field} is invalid") from error
