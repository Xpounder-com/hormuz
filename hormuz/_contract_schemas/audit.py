"""Audit event, anchor, chain-entry, and checkpoint validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_string,
    _sha256_digest,
    _validate_cost_coverage_values,
    _validate_identity_values,
    _value_integer,
    _value_mapping,
    _value_string,
    _value_string_list,
)
from .constants import (
    AUDIT_ANCHOR_SCHEMA_ID,
    AUDIT_ANCHOR_SCHEMA_VERSION,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_ID,
    AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_CHAIN_VERSION,
    AUDIT_EVENT_SCHEMA_ID,
    AUDIT_EVENT_SCHEMA_VERSION,
    COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY,
)
from .custody import validate_custody_control_event
from .custody_execution import validate_custody_execution_attempt, validate_custody_execution_event
from .custody_lifecycle import validate_custody_envelope_attestation, validate_custody_lifecycle_event
from .policy import validate_policy_action, validate_request_status


def validate_audit_event(value: Mapping[str, Any]) -> None:
    """Strictly validate a historical v1 or current v2 metadata-only event."""

    version = _value_integer(value, "schema_version")
    event_type = _value_string(value, "event_type")
    if version == 1:
        if event_type == "usage":
            _validate_audit_usage_v1(value)
            return
        if event_type == "security.secret":
            _validate_audit_security_v1(value)
            return
    if version == AUDIT_EVENT_SCHEMA_VERSION:
        if value.get("schema_id") != AUDIT_EVENT_SCHEMA_ID:
            raise ContractValidationError("audit event v2 must declare schema_id hormuz.audit-event")
        if event_type == "usage":
            _validate_audit_usage_v2(value)
            return
        if event_type == "security.secret":
            _validate_audit_security_v2(value)
            return
    raise ContractValidationError(f"unsupported Hormuz audit event: {event_type} v{version}")


def validate_audit_anchor(value: Mapping[str, Any]) -> None:
    """Strictly validate the structural contract of an immutable audit snapshot.

    Cryptographic digest and predecessor verification remains in
    ``hormuz.custody`` so a provider-neutral storage reader can use the same
    implementation.  This contract validator ensures only current,
    metadata-only tenant evidence enters that verifier.
    """

    _validate_audit_anchor(value)


def validate_audit_chain_entry(value: Mapping[str, Any]) -> None:
    """Validate one commit-time, per-organization durable chain entry."""

    _validate_audit_chain_entry(value)


def validate_audit_chain_checkpoint(value: Mapping[str, Any]) -> None:
    """Validate the small externally retainable commit-time checkpoint."""

    _validate_audit_chain_checkpoint(value)


def _validate_audit_chain_entry(value: Mapping[str, Any]) -> None:
    schema_version = _value_integer(value, "schema_version", minimum=1)
    if schema_version == AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION:
        _validate_audit_chain_entry_v1(value)
        return
    if schema_version == AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
        _validate_audit_chain_entry_v2(value)
        return
    raise ContractValidationError("unsupported audit chain entry schema_version")


def _validate_audit_chain_entry_v1(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "previous_digest",
            "event_digest",
            "event",
        },
    )
    if _value_string(value, "schema_id") != AUDIT_CHAIN_ENTRY_SCHEMA_ID:
        raise ContractValidationError("unsupported audit chain entry schema_id")
    if _value_integer(value, "schema_version") != AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION:
        raise ContractValidationError("unsupported audit chain entry schema_version")
    organization_id = _value_string(value, "organization_id")
    if _value_integer(value, "chain_version", minimum=1) != AUDIT_CHAIN_VERSION:
        raise ContractValidationError("unsupported audit chain version")
    _value_integer(value, "chain_epoch", minimum=1)
    _value_integer(value, "sequence", minimum=1)
    previous_digest = value.get("previous_digest")
    if previous_digest is not None:
        _sha256_digest(_value_string(value, "previous_digest"), "audit chain previous_digest")
    _sha256_digest(_value_string(value, "event_digest"), "audit chain event_digest")
    event = _value_mapping(value, "event")
    validate_audit_event(event)
    if (
        event.get("schema_id") != AUDIT_EVENT_SCHEMA_ID
        or event.get("schema_version") != AUDIT_EVENT_SCHEMA_VERSION
    ):
        raise ContractValidationError("audit chain requires current audit evidence")
    if event.get("organization_id") != organization_id:
        raise ContractValidationError("audit chain tenant mismatch")


def _validate_audit_chain_entry_v2(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "previous_digest",
            "event_digest",
            "source_schema_id",
            "source_schema_version",
            "source_event_id",
            "event",
        },
    )
    if _value_string(value, "schema_id") != AUDIT_CHAIN_ENTRY_SCHEMA_ID:
        raise ContractValidationError("unsupported audit chain entry schema_id")
    if _value_integer(value, "schema_version") != AUDIT_CHAIN_ENTRY_SCHEMA_VERSION:
        raise ContractValidationError("unsupported audit chain entry schema_version")
    organization_id = _value_string(value, "organization_id")
    if _value_integer(value, "chain_version", minimum=1) != AUDIT_CHAIN_VERSION:
        raise ContractValidationError("unsupported audit chain version")
    _value_integer(value, "chain_epoch", minimum=1)
    _value_integer(value, "sequence", minimum=1)
    previous_digest = value.get("previous_digest")
    if previous_digest is not None:
        _sha256_digest(_value_string(value, "previous_digest"), "audit chain previous_digest")
    _sha256_digest(_value_string(value, "event_digest"), "audit chain event_digest")
    source_schema_id = _value_string(value, "source_schema_id")
    source_schema_version = _value_integer(value, "source_schema_version", minimum=1)
    source_event_id = _value_string(value, "source_event_id")
    event = _value_mapping(value, "event")
    _validate_audit_chain_v2_source(
        event,
        organization_id=organization_id,
        source_schema_id=source_schema_id,
        source_schema_version=source_schema_version,
        source_event_id=source_event_id,
    )


def _validate_audit_chain_v2_source(
    event: Mapping[str, Any],
    *,
    organization_id: str,
    source_schema_id: str,
    source_schema_version: int,
    source_event_id: str,
) -> None:
    """Validate the finite v2 source union before it can be hash-chained.

    This deliberately does not accept a generic object.  Adding a new source
    requires an explicit contract validator and a chain-entry schema review.
    """

    if source_schema_id == "hormuz.custody-control-event" and source_schema_version == 1:
        validate_custody_control_event(event)
        _uuid(source_event_id, "audit chain custody control source_event_id")
        expected_source_id = source_event_id
    elif source_schema_id == "hormuz.custody-execution-attempt" and source_schema_version == 2:
        validate_custody_execution_attempt(event)
        expected_source_id = _value_string(event, "execution_id")
    elif source_schema_id == "hormuz.custody-execution-event" and source_schema_version == 1:
        validate_custody_execution_event(event)
        execution_id = _value_string(event, "execution_id")
        sequence = _value_integer(event, "sequence", minimum=1)
        expected_source_id = f"{execution_id}:{sequence}"
    elif source_schema_id == "hormuz.custody-lifecycle-event" and source_schema_version == 1:
        validate_custody_lifecycle_event(event)
        expected_source_id = _value_string(event, "lifecycle_event_id")
    elif source_schema_id == "hormuz.custody-envelope-attestation" and source_schema_version == 1:
        validate_custody_envelope_attestation(event)
        expected_source_id = f"{_value_string(event, 'execution_id')}:{_value_string(event, 'attestation_kind')}"
    elif source_schema_id == "hormuz.custody-deletion-event" and source_schema_version == 1:
        # Import lazily: custody retention's export validator imports this
        # audit validator, while this finite source branch is only evaluated
        # after both contract modules have loaded.
        from .custody_retention import validate_custody_deletion_event

        validate_custody_deletion_event(event)
        expected_source_id = _value_string(event, "deletion_event_id")
    elif source_schema_id == "hormuz.finance-attempt-evidence" and source_schema_version == 1:
        from ..finance_attempts import validate_finance_attempt_event

        try:
            validate_finance_attempt_event(event)
        except ValueError as error:
            raise ContractValidationError("finance attempt audit source is invalid") from error
        expected_source_id = _value_string(event, "evidence_event_id")
    else:
        raise ContractValidationError("audit chain v2 source schema is unsupported")
    if source_event_id != expected_source_id:
        raise ContractValidationError("audit chain v2 source identity is invalid")
    if event.get("organization_id") != organization_id:
        raise ContractValidationError("audit chain tenant mismatch")


def _uuid(value: str, field: str) -> None:
    try:
        UUID(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise ContractValidationError(f"{field} is invalid") from error


def _validate_audit_chain_checkpoint(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "checkpoint_id",
            "organization_id",
            "chain_version",
            "chain_epoch",
            "sequence",
            "head_digest",
            "created_at",
        },
    )
    if _value_string(value, "schema_id") != AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID:
        raise ContractValidationError("unsupported audit chain checkpoint schema_id")
    if _value_integer(value, "schema_version") != AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION:
        raise ContractValidationError("unsupported audit chain checkpoint schema_version")
    _value_string(value, "checkpoint_id")
    _value_string(value, "organization_id")
    if _value_integer(value, "chain_version", minimum=1) != AUDIT_CHAIN_VERSION:
        raise ContractValidationError("unsupported audit chain version")
    _value_integer(value, "chain_epoch", minimum=1)
    _value_integer(value, "sequence", minimum=1)
    _sha256_digest(_value_string(value, "head_digest"), "audit chain checkpoint head_digest")
    _value_string(value, "created_at")


def _validate_audit_anchor(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "artifact_id",
            "organization_id",
            "created_at",
            "chain_algorithm",
            "event_count",
            "entries",
            "head_digest",
        },
    )
    if _value_string(value, "schema_id") != AUDIT_ANCHOR_SCHEMA_ID:
        raise ContractValidationError("unsupported audit anchor schema_id")
    if _value_integer(value, "schema_version") != AUDIT_ANCHOR_SCHEMA_VERSION:
        raise ContractValidationError("unsupported audit anchor schema_version")
    _value_string(value, "artifact_id")
    organization_id = _value_string(value, "organization_id")
    _value_string(value, "created_at")
    if _value_string(value, "chain_algorithm") != "sha256":
        raise ContractValidationError("unsupported audit anchor chain algorithm")
    event_count = _value_integer(value, "event_count", minimum=1)
    _sha256_digest(_value_string(value, "head_digest"), "audit anchor head_digest")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) != event_count:
        raise ContractValidationError("audit anchor entry count is invalid")
    seen_event_ids: set[str] = set()
    previous_digest: str | None = None
    for sequence, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            raise ContractValidationError("audit anchor entry must be an object")
        _exact_keys(
            entry,
            {"schema_id", "schema_version", "sequence", "previous_digest", "event_digest", "event"},
            path=f"entries[{sequence - 1}]",
        )
        if _value_string(entry, "schema_id", path=f"entries[{sequence - 1}]") != "hormuz.audit-chain-entry":
            raise ContractValidationError("unsupported audit anchor entry schema_id")
        if _value_integer(entry, "schema_version", path=f"entries[{sequence - 1}]") != 1:
            raise ContractValidationError("unsupported audit anchor entry schema_version")
        if _value_integer(entry, "sequence", path=f"entries[{sequence - 1}]") != sequence:
            raise ContractValidationError("audit anchor sequence is invalid")
        if entry.get("previous_digest") != previous_digest:
            raise ContractValidationError("audit anchor predecessor is invalid")
        digest = _value_string(entry, "event_digest", path=f"entries[{sequence - 1}]")
        _sha256_digest(digest, f"entries[{sequence - 1}].event_digest")
        event = _value_mapping(entry, "event", path=f"entries[{sequence - 1}]")
        validate_audit_event(event)
        if event.get("schema_id") != AUDIT_EVENT_SCHEMA_ID or event.get("schema_version") != AUDIT_EVENT_SCHEMA_VERSION:
            raise ContractValidationError("audit anchor requires current audit evidence")
        if event.get("organization_id") != organization_id:
            raise ContractValidationError("audit anchor tenant mismatch")
        event_id = _value_string(event, "id", path=f"entries[{sequence - 1}].event")
        if event_id in seen_event_ids:
            raise ContractValidationError("audit anchor duplicate event")
        seen_event_ids.add(event_id)
        previous_digest = digest


def _validate_audit_usage_v1(value: Mapping[str, Any]) -> None:
    expected = _audit_v1_usage_fields()
    _exact_keys(value, expected)
    _validate_audit_usage_common(value, include_v2=False)


def _validate_audit_security_v1(value: Mapping[str, Any]) -> None:
    expected = _audit_v1_security_fields()
    _exact_keys(value, expected)
    _validate_audit_security_common(value, include_v2=False)


def _validate_audit_usage_v2(value: Mapping[str, Any]) -> None:
    _exact_keys(value, _audit_v2_usage_fields())
    _validate_audit_usage_common(value, include_v2=True)


def _validate_audit_security_v2(value: Mapping[str, Any]) -> None:
    _exact_keys(value, _audit_v2_security_fields())
    _validate_audit_security_common(value, include_v2=True)


def _validate_audit_usage_common(value: Mapping[str, Any], *, include_v2: bool) -> None:
    _value_string(value, "id")
    _value_string(value, "occurred_at")
    _value_string(value, "actor_id")
    _value_string(value, "actor_name")
    _value_string(value, "team_id")
    _value_string(value, "team_name")
    _value_string(value, "client")
    _value_string(value, "protocol")
    _value_string(value, "requested_model")
    _nullable_string(value, "resolved_alias")
    _nullable_string(value, "upstream_model" if not include_v2 else "routed_model")
    validate_policy_action(_value_string(value, "policy_action"))
    validate_request_status(_value_string(value, "status"))
    for field in (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "cost_microusd",
        "redaction_count",
    ):
        _value_integer(value, field, minimum=0)
    _nullable_string(value, "provider_request_id")
    _value_string_list(value, "redaction_rules")
    if include_v2:
        _validate_identity_values(value)
        _value_string(value, "policy_version")
        _nullable_string(value, "provider_reported_model")
        _validate_cost_coverage_values(value)


def _validate_audit_security_common(value: Mapping[str, Any], *, include_v2: bool) -> None:
    _value_string(value, "id")
    _value_string(value, "occurred_at")
    _value_string(value, "actor_id")
    _value_string(value, "actor_name")
    _value_string(value, "team_id")
    _value_string(value, "team_name")
    _value_string(value, "client")
    _value_string(value, "protocol")
    _value_string(value, "requested_model")
    if _value_string(value, "action") not in {"redacted", "denied"}:
        raise ContractValidationError("security event action must be redacted or denied")
    _value_integer(value, "detection_count", minimum=0)
    _value_string_list(value, "rules")
    if include_v2:
        _validate_identity_values(value)
        _value_string(value, "policy_version")
        if _value_string(value, "coverage") != COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY:
            raise ContractValidationError("unsupported coverage value")


def _audit_v1_usage_fields() -> set[str]:
    return {
        "schema_version",
        "event_type",
        "id",
        "occurred_at",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "client",
        "protocol",
        "requested_model",
        "resolved_alias",
        "upstream_model",
        "policy_action",
        "status",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "cost_microusd",
        "provider_request_id",
        "redaction_count",
        "redaction_rules",
    }


def _audit_v1_security_fields() -> set[str]:
    return {
        "schema_version",
        "event_type",
        "id",
        "occurred_at",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "client",
        "protocol",
        "requested_model",
        "action",
        "detection_count",
        "rules",
    }


def _audit_v2_usage_fields() -> set[str]:
    return {
        "schema_id",
        "schema_version",
        "event_type",
        "id",
        "occurred_at",
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
        "routed_model",
        "provider_reported_model",
        "policy_version",
        "policy_action",
        "status",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "cost_microusd",
        "cost_basis",
        "allocation_basis",
        "coverage",
        "provider_request_id",
        "redaction_count",
        "redaction_rules",
    }


def _audit_v2_security_fields() -> set[str]:
    return {
        "schema_id",
        "schema_version",
        "event_type",
        "id",
        "occurred_at",
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
        "policy_version",
        "coverage",
        "action",
        "detection_count",
        "rules",
    }
