"""Usage summary and grouped-report validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_number,
    _nullable_string,
    _validate_cost_coverage_values,
    _value_integer,
    _value_mapping,
    _value_number,
    _value_string,
)


def _validate_usage_summary(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "month",
            "requests",
            "denied_requests",
            "rate_limited_requests",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "cost_usd",
            "cost_basis",
            "allocation_basis",
            "coverage",
            "redactions",
            "secret_events",
            "secret_detections",
            "secret_denied_requests",
        },
    )
    _value_string(value, "month")
    for field in (
        "requests",
        "denied_requests",
        "rate_limited_requests",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "redactions",
        "secret_events",
        "secret_detections",
        "secret_denied_requests",
    ):
        _value_integer(value, field, minimum=0)
    _value_number(value, "cost_usd", minimum=0)
    _validate_cost_coverage_values(value)


def _validate_usage_report(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "month",
            "group_by",
            "filters",
            "cost_basis",
            "allocation_basis",
            "coverage",
            "rows",
        },
    )
    _value_string(value, "month")
    if _value_string(value, "group_by") not in {"organization", "team", "person", "model", "client", "provider"}:
        raise ContractValidationError("unsupported usage report group_by")
    filters = _value_mapping(value, "filters")
    _exact_keys(filters, {"actor_id", "team_id"}, path="filters")
    _nullable_string(filters, "actor_id", path="filters")
    _nullable_string(filters, "team_id", path="filters")
    _validate_cost_coverage_values(value)
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise ContractValidationError("rows must be an array")
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ContractValidationError(f"rows[{index}] must be an object")
        _validate_usage_report_row(row, path=f"rows[{index}]")


def _validate_usage_report_row(value: Mapping[str, Any], *, path: str) -> None:
    required = {
        "scope_id",
        "scope_name",
        "requests",
        "succeeded",
        "failed",
        "denied",
        "rate_limited",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
        "cost_microusd",
        "cost_usd",
        "budget_usd",
        "budget_remaining_usd",
        "budget_used_percent",
        "active_actors",
        "redactions",
    }
    optional = {"team_id", "team_name", "protocol", "client"}
    _exact_keys(value, required, optional, path=path)
    _value_string(value, "scope_id", path=path)
    _value_string(value, "scope_name", path=path)
    for field in required - {
        "scope_id",
        "scope_name",
        "cost_usd",
        "budget_usd",
        "budget_remaining_usd",
        "budget_used_percent",
    }:
        _value_integer(value, field, minimum=0, path=path)
    _value_number(value, "cost_usd", minimum=0, path=path)
    for field in ("budget_usd", "budget_remaining_usd", "budget_used_percent"):
        _nullable_number(value, field, minimum=0, path=path)
    for field in optional:
        if field in value:
            _nullable_string(value, field, path=path)
