"""Shared strict primitives for schema-family validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .constants import (
    ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST,
    COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
    _IDENTITY_TYPES,
)


class ContractValidationError(ValueError):
    """Raised when a public Hormuz contract is malformed or unsupported."""


def _validate_identity_values(value: Mapping[str, Any]) -> None:
    _value_string(value, "organization_id")
    _value_string(value, "actor_id")
    _value_string(value, "actor_name")
    _value_string(value, "team_id")
    _value_string(value, "team_name")
    if _value_string(value, "identity_type") not in _IDENTITY_TYPES:
        raise ContractValidationError("unsupported identity_type")
    _value_string(value, "authentication_source")


def _validate_cost_coverage_values(value: Mapping[str, Any]) -> None:
    if _value_string(value, "cost_basis") != COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE:
        raise ContractValidationError("unsupported cost_basis")
    if _value_string(value, "allocation_basis") != ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST:
        raise ContractValidationError("unsupported allocation_basis")
    if _value_string(value, "coverage") != COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY:
        raise ContractValidationError("unsupported coverage")


def _exact_keys(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str] | None = None,
    *,
    path: str = "value",
) -> None:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{path} must be an object")
    optional = optional or set()
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise ContractValidationError(f"{path} is missing required fields: {', '.join(sorted(missing))}")
    if unknown:
        raise ContractValidationError(f"{path} has unsupported fields: {', '.join(sorted(unknown))}")


def _value_mapping(value: Mapping[str, Any], field: str, *, path: str = "value") -> Mapping[str, Any]:
    result = value.get(field)
    if not isinstance(result, Mapping):
        raise ContractValidationError(f"{path}.{field} must be an object")
    return result


def _value_string(value: Mapping[str, Any], field: str, *, path: str = "value") -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ContractValidationError(f"{path}.{field} must be a non-empty string")
    return result


def _nullable_string(value: Mapping[str, Any], field: str, *, path: str = "value") -> str | None:
    result = value.get(field)
    if result is None:
        return None
    if not isinstance(result, str) or not result:
        raise ContractValidationError(f"{path}.{field} must be a non-empty string or null")
    return result


def _value_string_list(value: Mapping[str, Any], field: str, *, path: str = "value") -> list[str]:
    result = value.get(field)
    if not isinstance(result, list) or any(not isinstance(item, str) or not item for item in result):
        raise ContractValidationError(f"{path}.{field} must be an array of non-empty strings")
    return result


def _value_integer(
    value: Mapping[str, Any],
    field: str,
    *,
    minimum: int | None = None,
    path: str = "value",
) -> int:
    result = value.get(field)
    if isinstance(result, bool) or not isinstance(result, int):
        raise ContractValidationError(f"{path}.{field} must be an integer")
    if minimum is not None and result < minimum:
        raise ContractValidationError(f"{path}.{field} must be at least {minimum}")
    return result


def _nullable_integer(
    value: Mapping[str, Any],
    field: str,
    *,
    minimum: int | None = None,
    path: str = "value",
) -> int | None:
    if value.get(field) is None:
        return None
    return _value_integer(value, field, minimum=minimum, path=path)


def _value_number(
    value: Mapping[str, Any],
    field: str,
    *,
    minimum: float | None = None,
    path: str = "value",
) -> float:
    result = value.get(field)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise ContractValidationError(f"{path}.{field} must be a number")
    numeric = float(result)
    if minimum is not None and numeric < minimum:
        raise ContractValidationError(f"{path}.{field} must be at least {minimum}")
    return numeric


def _nullable_number(
    value: Mapping[str, Any],
    field: str,
    *,
    minimum: float | None = None,
    path: str = "value",
) -> float | None:
    if value.get(field) is None:
        return None
    return _value_number(value, field, minimum=minimum, path=path)


def _sha256_digest(value: str, path: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ContractValidationError(f"{path} is invalid")
