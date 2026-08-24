"""Stable, content-free contracts for Hormuz policy and evidence surfaces.

The provider response bodies relayed by Hormuz remain provider-owned so that
Codex and Claude Code retain protocol compatibility. Hormuz-owned JSON and
audit evidence use the schema identifiers and validators in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ._contract_schemas.audit import (
    _validate_audit_anchor,
    _validate_audit_chain_checkpoint,
    _validate_audit_chain_entry,
    validate_audit_anchor as _validate_audit_anchor_contract,
    validate_audit_chain_checkpoint as _validate_audit_chain_checkpoint_contract,
    validate_audit_chain_entry as _validate_audit_chain_entry_contract,
    validate_audit_event as _validate_audit_event_contract,
)
from ._contract_schemas.common import (
    ContractValidationError,
    _exact_keys,
    _value_integer,
    _value_mapping,
    _value_string,
    _value_string_list,
)
from ._contract_schemas.custody import (
    _validate_custody_control_status_v1,
    _validate_custody_control_status_v2,
    validate_custody_control_event as _validate_custody_control_event_contract,
)
from ._contract_schemas.custody_execution import (
    validate_custody_execution_attempt as _validate_custody_execution_attempt_contract,
    validate_custody_execution_event as _validate_custody_execution_event_contract,
)
# This facade deliberately re-exports the stable schema vocabulary without
# duplicating its ownership in the public compatibility module.
from ._contract_schemas.constants import *  # noqa: F403
from ._contract_schemas.constants import _CURRENT_SCHEMA_VERSIONS, _REQUEST_STATUSES
from ._contract_schemas.health import (
    _validate_error,
    _validate_health,
    _validate_identity,
    _validate_readiness,
)
from ._contract_schemas.policy import (
    _validate_policy_control_status,
    _validate_policy_decision,
    validate_policy_action as _validate_policy_action_contract,
    validate_policy_control_event as _validate_policy_control_event_contract,
    validate_request_status as _validate_request_status_contract,
)
from ._contract_schemas.request_attempt import (
    validate_request_attempt as _validate_request_attempt_contract,
    validate_request_attempt_event as _validate_request_attempt_event_contract,
)
from ._contract_schemas.usage import _validate_usage_report, _validate_usage_summary

# Preserve the public qualified name for callers that inspect or serialize the
# exception class through the long-standing contracts facade.
ContractValidationError.__module__ = __name__


def contract_header(schema_id: str, schema_version: int) -> str:
    """Return the version marker used for provider-owned relay responses."""

    if not isinstance(schema_id, str) or not schema_id:
        raise ContractValidationError("schema_id must be a non-empty string")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version < 1:
        raise ContractValidationError("schema_version must be a positive integer")
    return f"{schema_id};v={schema_version}"


def relay_contract_header() -> str:
    return contract_header(RELAY_METADATA_SCHEMA_ID, RELAY_METADATA_SCHEMA_VERSION)


def contract_envelope(schema_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Add and validate an explicit version for a Hormuz-owned JSON object."""

    try:
        schema_version = _CURRENT_SCHEMA_VERSIONS[schema_id]
    except KeyError as error:
        raise ContractValidationError(f"unsupported Hormuz schema_id: {schema_id}") from error
    result = dict(value)
    result["schema_id"] = schema_id
    result["schema_version"] = schema_version
    validate_contract(result)
    return result


def contract_manifest() -> dict[str, object]:
    """Return the machine-readable policy/evidence manifest for this release line."""

    manifest: dict[str, object] = {
        "schema_id": MANIFEST_SCHEMA_ID,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "compatibility": {
            "current_release_line": "0.2",
            "addition_rule": "New optional fields require a new documented schema version before release.",
            "breaking_change_rule": "Removed fields, changed types, changed semantics, or new required fields require a new schema version and migration guidance.",
            "legacy_audit_read": "Audit event version 1 remains fixture-validated for historical exports; Hormuz emits version 2.",
            "provider_protocol_rule": "OpenAI and Anthropic response bodies remain provider-owned. Hormuz metadata is versioned through X-Hormuz-Contract.",
        },
        "schemas": [
            _manifest_schema(
                HEALTH_SCHEMA_ID,
                1,
                "response",
                "hormuz",
                ["schema_id", "schema_version", "status", "service", "protocols"],
            ),
            _manifest_schema(
                READINESS_SCHEMA_ID,
                READINESS_SCHEMA_VERSION,
                "response",
                "hormuz",
                ["schema_id", "schema_version", "status", "service", "reason"],
            ),
            _manifest_schema(
                IDENTITY_SCHEMA_ID,
                1,
                "response",
                "hormuz",
                [
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
                ],
            ),
            _manifest_schema(
                USAGE_SUMMARY_SCHEMA_ID,
                1,
                "response",
                "hormuz",
                [
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
                ],
            ),
            _manifest_schema(
                ERROR_SCHEMA_ID,
                1,
                "response",
                "hormuz",
                ["schema_id", "schema_version", "error"],
                legacy=True,
            ),
            _manifest_schema(
                ERROR_SCHEMA_ID,
                ERROR_SCHEMA_VERSION,
                "response",
                "hormuz",
                ["schema_id", "schema_version", "error"],
            ),
            _manifest_schema(
                POLICY_DECISION_SCHEMA_ID,
                1,
                "cli-output",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "allowed",
                    "action",
                    "reason",
                    "requested_model",
                    "resolved_alias",
                    "routed_model",
                    "max_output_tokens",
                    "policy_version",
                ],
            ),
            _manifest_schema(
                POLICY_CONTROL_STATUS_SCHEMA_ID,
                1,
                "cli-output",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "organization_id",
                    "initialized",
                    "active",
                    "versions",
                    "administrators",
                ],
            ),
            _manifest_schema(
                POLICY_DOCUMENT_SCHEMA_ID,
                POLICY_DOCUMENT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "organization_id",
                    "policies",
                    "egress_controls",
                ],
            ),
            _manifest_schema(
                POLICY_CONTROL_EVENT_SCHEMA_ID,
                POLICY_CONTROL_EVENT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "event_schema_id",
                    "event_schema_version",
                    "event_type",
                    "organization_id",
                    "occurred_at",
                    "opaque actor identity key",
                    "version_id",
                    "generation",
                    "reason_code",
                    "content-free structural metadata",
                ],
            ),
            _manifest_schema(
                CUSTODY_CONTROL_STATUS_SCHEMA_ID,
                1,
                "cli-output",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "organization_id",
                    "initialized",
                    "administrators",
                    "content-free operation approvals",
                ],
                legacy=True,
            ),
            _manifest_schema(
                CUSTODY_CONTROL_STATUS_SCHEMA_ID,
                CUSTODY_CONTROL_STATUS_SCHEMA_VERSION,
                "cli-output",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "organization_id",
                    "initialized",
                    "administrators",
                    "content-free operation approvals",
                    "content-free routine execution attempts",
                ],
            ),
            _manifest_schema(
                CUSTODY_CONTROL_EVENT_SCHEMA_ID,
                CUSTODY_CONTROL_EVENT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "event_schema_id",
                    "event_schema_version",
                    "organization_id",
                    "event_type",
                    "opaque actor identity key",
                    "operation type and risk",
                    "target and parameter digests",
                    "approval counts and expiry",
                ],
            ),
            _manifest_schema(
                CUSTODY_EXECUTION_SCHEMA_ID,
                CUSTODY_EXECUTION_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "execution_id",
                    "operation_id",
                    "routine operation type",
                    "target and parameter digests",
                    "protected input reference digest",
                    "claimed_at",
                    "state",
                ],
            ),
            _manifest_schema(
                CUSTODY_EXECUTION_EVENT_SCHEMA_ID,
                CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "execution_id",
                    "operation_id",
                    "occurred_at",
                    "sequence",
                    "state",
                    "reason_code",
                ],
            ),
            _manifest_schema(
                USAGE_REPORT_SCHEMA_ID,
                1,
                "cli-output",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "month",
                    "group_by",
                    "filters",
                    "cost_basis",
                    "allocation_basis",
                    "coverage",
                    "rows",
                ],
            ),
            _manifest_schema(
                AUDIT_EVENT_SCHEMA_ID,
                1,
                "durable-evidence",
                "hormuz",
                ["schema_version", "event_type", "metadata-only legacy audit fields"],
                legacy=True,
            ),
            _manifest_schema(
                AUDIT_EVENT_SCHEMA_ID,
                AUDIT_EVENT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "event_type",
                    "event-time identity binding",
                    "policy_version",
                    "requested_model",
                    "routed_model",
                    "provider_reported_model",
                    "policy_action",
                    "status",
                    "token and cost bases",
                    "allocation_basis",
                    "coverage",
                ],
            ),
            _manifest_schema(
                REQUEST_ATTEMPT_SCHEMA_ID,
                REQUEST_ATTEMPT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "attempt_id",
                    "created_at",
                    "event-time identity binding",
                    "requested_model",
                    "routed_model",
                    "policy_version",
                    "policy_action",
                    "redaction metadata",
                    "reserved token and estimated cost",
                ],
            ),
            _manifest_schema(
                REQUEST_ATTEMPT_EVENT_SCHEMA_ID,
                REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "id",
                    "attempt_id",
                    "organization_id",
                    "occurred_at",
                    "sequence",
                    "state",
                    "reason_code",
                    "usage_event_id",
                ],
            ),
            _manifest_schema(
                AUDIT_ANCHOR_SCHEMA_ID,
                AUDIT_ANCHOR_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "schema_id",
                    "schema_version",
                    "artifact_id",
                    "organization_id",
                    "created_at",
                    "chain_algorithm",
                    "event_count",
                    "entries",
                    "head_digest",
                ],
            ),
            _manifest_schema(
                AUDIT_CHAIN_ENTRY_SCHEMA_ID,
                AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "organization_id",
                    "chain_version",
                    "chain_epoch",
                    "sequence",
                    "previous_digest",
                    "event_digest",
                    "complete metadata-only audit event",
                ],
            ),
            _manifest_schema(
                AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID,
                AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
                "durable-evidence",
                "hormuz",
                [
                    "checkpoint_id",
                    "organization_id",
                    "chain_version",
                    "chain_epoch",
                    "sequence",
                    "head_digest",
                    "created_at",
                ],
            ),
            _manifest_schema(
                RELAY_METADATA_SCHEMA_ID,
                RELAY_METADATA_SCHEMA_VERSION,
                "http-headers",
                "hormuz",
                [
                    "X-Hormuz-Contract",
                    "X-Hormuz-Policy-Decision",
                    "X-Hormuz-Requested-Model",
                    "X-Hormuz-Routed-Model",
                    "X-Hormuz-Redactions",
                    "X-Hormuz-Error-Code",
                ],
            ),
        ],
        "policy_action_semantics": {
            "allowed": "The requested route was allowed without a route substitution or output cap.",
            "fallback": "The requested model was rerouted to the configured compatible fallback. The wire value remains fallback for compatibility.",
            "capped": "The policy lowered the maximum output-token bound.",
            "redacted": "A supported request shape had protected material replaced before provider serialization.",
            "denied": "Policy denied the request before provider egress.",
            "provider_policy_denied": "Hormuz denied a provider-storage or background-mode policy conflict before provider egress.",
            "secret_denied": "Hormuz denied protected material before provider egress.",
            "budget_reservation_denied": "Actual usage plus the active reservation would exceed a configured limit.",
        },
        "request_status_semantics": {
            "succeeded": "A provider response completed with a 2xx status and the downstream relay remained connected.",
            "failed": "The provider or relay failed without a provider rate-limit response.",
            "denied": "Hormuz denied the request before provider egress.",
            "rate_limited": "The provider returned HTTP 429 after Hormuz allowed and forwarded the request.",
        },
        "error_codes": sorted(PUBLIC_ERROR_CODES),
        "content_boundary": "No schema in this manifest permits prompts, responses, secret values, matched detector values, filenames, or source content.",
    }
    validate_contract_manifest(manifest)
    return manifest


def validate_contract_manifest(value: Mapping[str, Any]) -> None:
    """Strictly validate the versioned manifest emitted by ``contract-manifest``."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "compatibility",
            "schemas",
            "policy_action_semantics",
            "request_status_semantics",
            "error_codes",
            "content_boundary",
        },
    )
    if _value_string(value, "schema_id") != MANIFEST_SCHEMA_ID:
        raise ContractValidationError("unsupported policy/evidence manifest schema_id")
    if _value_integer(value, "schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ContractValidationError("unsupported policy/evidence manifest schema_version")

    compatibility = _value_mapping(value, "compatibility")
    _exact_keys(
        compatibility,
        {
            "current_release_line",
            "addition_rule",
            "breaking_change_rule",
            "legacy_audit_read",
            "provider_protocol_rule",
        },
        path="compatibility",
    )
    for field in compatibility:
        _value_string(compatibility, field, path="compatibility")

    schemas = value.get("schemas")
    if not isinstance(schemas, list) or not schemas:
        raise ContractValidationError("schemas must be a non-empty array")
    identities: set[tuple[str, int]] = set()
    for index, schema in enumerate(schemas):
        if not isinstance(schema, Mapping):
            raise ContractValidationError(f"schemas[{index}] must be an object")
        _exact_keys(
            schema,
            {"schema_id", "schema_version", "delivery", "ownership", "legacy", "fields"},
            path=f"schemas[{index}]",
        )
        schema_id = _value_string(schema, "schema_id", path=f"schemas[{index}]")
        schema_version = _value_integer(schema, "schema_version", minimum=1, path=f"schemas[{index}]")
        identity = (schema_id, schema_version)
        if identity in identities:
            raise ContractValidationError(f"schemas contains duplicate identity: {schema_id} v{schema_version}")
        identities.add(identity)
        if _value_string(schema, "delivery", path=f"schemas[{index}]") not in {
            "response",
            "cli-output",
            "durable-evidence",
            "http-headers",
        }:
            raise ContractValidationError(f"schemas[{index}].delivery is unsupported")
        if _value_string(schema, "ownership", path=f"schemas[{index}]") != "hormuz":
            raise ContractValidationError(f"schemas[{index}].ownership must be hormuz")
        if not isinstance(schema.get("legacy"), bool):
            raise ContractValidationError(f"schemas[{index}].legacy must be a boolean")
        _value_string_list(schema, "fields", path=f"schemas[{index}]")

    policy_action_semantics = _value_mapping(value, "policy_action_semantics")
    if set(policy_action_semantics) != {
        "allowed",
        "fallback",
        "capped",
        "redacted",
        "denied",
        "provider_policy_denied",
        "secret_denied",
        "budget_reservation_denied",
    }:
        raise ContractValidationError("policy_action_semantics has unsupported entries")
    for field in policy_action_semantics:
        _value_string(policy_action_semantics, field, path="policy_action_semantics")

    request_status_semantics = _value_mapping(value, "request_status_semantics")
    if set(request_status_semantics) != _REQUEST_STATUSES:
        raise ContractValidationError("request_status_semantics has unsupported entries")
    for field in request_status_semantics:
        _value_string(request_status_semantics, field, path="request_status_semantics")

    error_codes = value.get("error_codes")
    if not isinstance(error_codes, list) or set(error_codes) != PUBLIC_ERROR_CODES or len(error_codes) != len(PUBLIC_ERROR_CODES):
        raise ContractValidationError("error_codes must enumerate every stable public error code exactly once")
    if error_codes != sorted(error_codes):
        raise ContractValidationError("error_codes must be sorted")
    _value_string(value, "content_boundary")


def validate_contract(value: Mapping[str, Any]) -> None:
    """Strictly validate a Hormuz-owned JSON response or CLI output."""

    schema_id = _value_string(value, "schema_id")
    schema_version = _value_integer(value, "schema_version")
    validator = {
        (HEALTH_SCHEMA_ID, 1): _validate_health,
        (READINESS_SCHEMA_ID, READINESS_SCHEMA_VERSION): _validate_readiness,
        (IDENTITY_SCHEMA_ID, 1): _validate_identity,
        (USAGE_SUMMARY_SCHEMA_ID, 1): _validate_usage_summary,
        (ERROR_SCHEMA_ID, 1): lambda item: _validate_error(item, PUBLIC_ERROR_CODES_V1),
        (ERROR_SCHEMA_ID, ERROR_SCHEMA_VERSION): lambda item: _validate_error(item, PUBLIC_ERROR_CODES),
        (POLICY_DECISION_SCHEMA_ID, 1): _validate_policy_decision,
        (POLICY_CONTROL_STATUS_SCHEMA_ID, 1): _validate_policy_control_status,
        (CUSTODY_CONTROL_STATUS_SCHEMA_ID, 1): _validate_custody_control_status_v1,
        (CUSTODY_CONTROL_STATUS_SCHEMA_ID, CUSTODY_CONTROL_STATUS_SCHEMA_VERSION): _validate_custody_control_status_v2,
        (USAGE_REPORT_SCHEMA_ID, 1): _validate_usage_report,
        (AUDIT_ANCHOR_SCHEMA_ID, AUDIT_ANCHOR_SCHEMA_VERSION): _validate_audit_anchor,
        (AUDIT_CHAIN_ENTRY_SCHEMA_ID, AUDIT_CHAIN_ENTRY_SCHEMA_VERSION): _validate_audit_chain_entry,
        (AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID, AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION): _validate_audit_chain_checkpoint,
    }.get((schema_id, schema_version))
    if validator is None:
        raise ContractValidationError(f"unsupported Hormuz contract: {schema_id} v{schema_version}")
    validator(value)


def _manifest_schema(
    schema_id: str,
    schema_version: int,
    delivery: str,
    ownership: str,
    fields: list[str],
    *,
    legacy: bool = False,
) -> dict[str, object]:
    return {
        "schema_id": schema_id,
        "schema_version": schema_version,
        "delivery": delivery,
        "ownership": ownership,
        "legacy": legacy,
        "fields": fields,
    }



def validate_audit_event(value: Mapping[str, Any]) -> None:
    """Validate a historical v1 or current v2 metadata-only event."""

    _validate_audit_event_contract(value)


def validate_audit_anchor(value: Mapping[str, Any]) -> None:
    """Validate the structural contract of an immutable audit snapshot."""

    _validate_audit_anchor_contract(value)


def validate_audit_chain_entry(value: Mapping[str, Any]) -> None:
    """Validate one commit-time, per-organization durable chain entry."""

    _validate_audit_chain_entry_contract(value)


def validate_audit_chain_checkpoint(value: Mapping[str, Any]) -> None:
    """Validate the small externally retainable commit-time checkpoint."""

    _validate_audit_chain_checkpoint_contract(value)


def validate_request_attempt(value: Mapping[str, Any]) -> None:
    """Validate one immutable, metadata-only provider-egress attempt root."""

    _validate_request_attempt_contract(value)


def validate_request_attempt_event(value: Mapping[str, Any]) -> None:
    """Validate one immutable state-transition record for an attempt."""

    _validate_request_attempt_event_contract(value)


def validate_policy_control_event(value: Mapping[str, Any]) -> None:
    """Validate one metadata-only immutable policy-control evidence row."""

    _validate_policy_control_event_contract(value)


def validate_custody_control_event(value: Mapping[str, Any]) -> None:
    """Validate one metadata-only immutable custody-control evidence row."""

    _validate_custody_control_event_contract(value)


def validate_custody_execution_attempt(value: Mapping[str, Any]) -> None:
    """Validate one immutable metadata-only custody-executor attempt root."""

    _validate_custody_execution_attempt_contract(value)


def validate_custody_execution_event(value: Mapping[str, Any]) -> None:
    """Validate one immutable custody-executor state transition."""

    _validate_custody_execution_event_contract(value)


def validate_policy_action(value: str) -> None:
    _validate_policy_action_contract(value)


def validate_request_status(value: str) -> None:
    _validate_request_status_contract(value)
