"""Gateway health, readiness, identity, and error validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_string,
    _validate_identity_values,
    _value_mapping,
    _value_string,
    _value_string_list,
)


def _validate_health(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "status", "service", "protocols"})
    _value_string(value, "status")
    _value_string(value, "service")
    _value_string_list(value, "protocols")


def _validate_readiness(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "status", "service", "reason"})
    status = _value_string(value, "status")
    _value_string(value, "service")
    reason = _nullable_string(value, "reason")
    if status == "ready" and reason is None:
        return
    if status == "not_ready" and reason in {"dependency_unavailable", "draining"}:
        return
    raise ContractValidationError("readiness status and reason are invalid")


def _validate_identity(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "organization_id",
            "identity_type",
            "allowed_clients",
            "authentication_source",
        },
    )
    _validate_identity_values(value)
    _value_string_list(value, "allowed_clients")


def _validate_error(value: Mapping[str, Any], allowed_codes: frozenset[str]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "error"})
    error = _value_mapping(value, "error")
    _exact_keys(error, {"code", "message"}, path="error")
    code = _value_string(error, "code", path="error")
    if code not in allowed_codes:
        raise ContractValidationError(f"unsupported public error code: {code}")
    _value_string(error, "message", path="error")
