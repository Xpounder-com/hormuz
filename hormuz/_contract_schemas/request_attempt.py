"""Durable provider request-attempt ledger validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_string,
    _validate_identity_values,
    _value_integer,
    _value_string,
    _value_string_list,
)
from .constants import (
    REQUEST_ATTEMPT_EVENT_SCHEMA_ID,
    REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION,
    REQUEST_ATTEMPT_SCHEMA_ID,
    REQUEST_ATTEMPT_SCHEMA_VERSION,
    _REQUEST_ATTEMPT_STATES,
    _REQUEST_ATTEMPT_UNKNOWN_REASONS,
)
from .policy import validate_policy_action


def validate_request_attempt(value: Mapping[str, Any]) -> None:
    """Validate one immutable, metadata-only provider-egress attempt root.

    Attempt roots are deliberately separate from usage audit events. They are
    written before provider egress so a later transport or storage ambiguity
    cannot be silently treated as zero consumption.
    """

    _exact_keys(
        value,
        {
            "evidence_schema_id",
            "evidence_schema_version",
            "attempt_id",
            "created_at",
            "organization_id",
            "actor_id",
            "actor_name",
            "team_id",
            "team_name",
            "identity_type",
            "authentication_source",
            "client",
            "protocol",
            "requested_model",
            "resolved_alias",
            "upstream_model",
            "policy_version",
            "policy_action",
            "redaction_count",
            "redaction_rules",
            "reserved_tokens",
            "reserved_cost_microusd",
        },
    )
    if _value_string(value, "evidence_schema_id") != REQUEST_ATTEMPT_SCHEMA_ID:
        raise ContractValidationError("request attempt schema_id is unsupported")
    if _value_integer(value, "evidence_schema_version", minimum=1) != REQUEST_ATTEMPT_SCHEMA_VERSION:
        raise ContractValidationError("request attempt schema_version is unsupported")
    _value_string(value, "attempt_id")
    _value_string(value, "created_at")
    _validate_identity_values(value)
    _value_string(value, "client")
    _value_string(value, "protocol")
    _value_string(value, "requested_model")
    _nullable_string(value, "resolved_alias")
    _nullable_string(value, "upstream_model")
    _value_string(value, "policy_version")
    validate_policy_action(_value_string(value, "policy_action"))
    _value_integer(value, "redaction_count", minimum=0)
    _value_string_list(value, "redaction_rules")
    _value_integer(value, "reserved_tokens", minimum=0)
    _value_integer(value, "reserved_cost_microusd", minimum=0)


def validate_request_attempt_event(value: Mapping[str, Any]) -> None:
    """Validate one immutable state-transition record for an attempt."""

    _exact_keys(
        value,
        {
            "event_schema_id",
            "event_schema_version",
            "id",
            "attempt_id",
            "organization_id",
            "occurred_at",
            "sequence",
            "state",
            "reason_code",
            "usage_event_id",
        },
    )
    if _value_string(value, "event_schema_id") != REQUEST_ATTEMPT_EVENT_SCHEMA_ID:
        raise ContractValidationError("request attempt event schema_id is unsupported")
    if _value_integer(value, "event_schema_version", minimum=1) != REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION:
        raise ContractValidationError("request attempt event schema_version is unsupported")
    _value_string(value, "id")
    _value_string(value, "attempt_id")
    _value_string(value, "organization_id")
    _value_string(value, "occurred_at")
    sequence = _value_integer(value, "sequence", minimum=1)
    state = _value_string(value, "state")
    if state not in _REQUEST_ATTEMPT_STATES:
        raise ContractValidationError("request attempt state is unsupported")
    reason_code = _nullable_string(value, "reason_code")
    usage_event_id = _nullable_string(value, "usage_event_id")
    if state == "pending":
        if sequence != 1 or reason_code is not None or usage_event_id is not None:
            raise ContractValidationError("pending request attempt event is malformed")
        return
    if state == "outcome_unknown":
        if sequence <= 1 or reason_code not in _REQUEST_ATTEMPT_UNKNOWN_REASONS or usage_event_id is not None:
            raise ContractValidationError("outcome_unknown request attempt event is malformed")
        return
    if sequence <= 1 or reason_code is not None or usage_event_id is None:
        raise ContractValidationError("terminal request attempt event is malformed")
