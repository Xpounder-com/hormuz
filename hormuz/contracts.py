"""Stable, content-free contracts for Hormuz policy and evidence surfaces.

The provider response bodies relayed by Hormuz remain provider-owned so that
Codex and Claude Code retain protocol compatibility.  Hormuz-owned JSON and
audit evidence use the schema identifiers and validators in this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


MANIFEST_SCHEMA_ID = "hormuz.policy-evidence-manifest"
MANIFEST_SCHEMA_VERSION = 1
RELAY_METADATA_SCHEMA_ID = "hormuz.relay-metadata"
RELAY_METADATA_SCHEMA_VERSION = 1
AUDIT_EVENT_SCHEMA_ID = "hormuz.audit-event"
AUDIT_EVENT_SCHEMA_VERSION = 2
AUDIT_ANCHOR_SCHEMA_ID = "hormuz.audit-anchor"
AUDIT_ANCHOR_SCHEMA_VERSION = 1

HEALTH_SCHEMA_ID = "hormuz.gateway-health"
READINESS_SCHEMA_ID = "hormuz.gateway-readiness"
READINESS_SCHEMA_VERSION = 1
IDENTITY_SCHEMA_ID = "hormuz.gateway-identity"
USAGE_SUMMARY_SCHEMA_ID = "hormuz.gateway-usage-summary"
ERROR_SCHEMA_ID = "hormuz.gateway-error"
ERROR_SCHEMA_VERSION = 2
POLICY_DECISION_SCHEMA_ID = "hormuz.policy-decision"
POLICY_CONTROL_STATUS_SCHEMA_ID = "hormuz.policy-control-status"
POLICY_DOCUMENT_SCHEMA_ID = "hormuz.policy-document"
POLICY_DOCUMENT_SCHEMA_VERSION = 1
POLICY_CONTROL_EVENT_SCHEMA_ID = "hormuz.policy-control-event"
POLICY_CONTROL_EVENT_SCHEMA_VERSION = 1
USAGE_REPORT_SCHEMA_ID = "hormuz.usage-report"

COST_BASIS_CONFIGURED_RATE_CARD_ESTIMATE = "configured_rate_card_estimate"
ALLOCATION_BASIS_DIRECT_GATEWAY_REQUEST = "direct_gateway_request"
COVERAGE_GATEWAY_CAPTURED_REQUESTS_ONLY = "gateway_captured_requests_only"

PUBLIC_ERROR_CODES_V1 = frozenset(
    {
        "not_found",
        "unauthorized",
        "length_required",
        "invalid_content_length",
        "request_too_large",
        "invalid_json",
        "invalid_request",
        "hormuz_policy_denied",
        "hormuz_provider_policy_denied",
        "hormuz_secret_detected",
        "hormuz_budget_denied",
        "gateway_upstream_not_configured",
        "gateway_upstream_error",
    }
)
PUBLIC_ERROR_CODES = frozenset({*PUBLIC_ERROR_CODES_V1, "hormuz_storage_unavailable"})

_CURRENT_SCHEMA_VERSIONS = {
    HEALTH_SCHEMA_ID: 1,
    READINESS_SCHEMA_ID: READINESS_SCHEMA_VERSION,
    IDENTITY_SCHEMA_ID: 1,
    USAGE_SUMMARY_SCHEMA_ID: 1,
    ERROR_SCHEMA_ID: ERROR_SCHEMA_VERSION,
    POLICY_DECISION_SCHEMA_ID: 1,
    POLICY_CONTROL_STATUS_SCHEMA_ID: 1,
    USAGE_REPORT_SCHEMA_ID: 1,
    AUDIT_ANCHOR_SCHEMA_ID: AUDIT_ANCHOR_SCHEMA_VERSION,
}

_REQUEST_STATUSES = frozenset({"succeeded", "failed", "denied", "rate_limited"})
_IDENTITY_TYPES = frozenset({"human", "service_account", "ci", "connector"})
_POLICY_ACTIONS = frozenset(
    {
        "allowed",
        "allowed+redacted",
        "fallback",
        "fallback+redacted",
        "capped",
        "capped+redacted",
        "fallback+capped",
        "fallback+capped+redacted",
        "denied",
        "provider_policy_denied",
        "secret_denied",
        "budget_reservation_denied",
    }
)
_POLICY_CONTROL_EVENT_TYPES = frozenset(
    {
        "bootstrap_initialized",
        "administrator_granted",
        "administrator_revoked",
        "policy_staged",
        "policy_activated",
        "policy_rolled_back",
        "break_glass_recovered",
    }
)
_POLICY_CHANGE_FIELDS = frozenset(
    {
        "allowed_clients",
        "allowed_models",
        "fallback_model",
        "fallback_models",
        "max_output_tokens",
        "monthly_token_limit",
        "monthly_budget_usd",
        "per_actor_monthly_budget_usd",
    }
)
_POLICY_EGRESS_FIELDS = (
    "openai.allow_background",
    "openai.allow_response_storage",
    "secrets.mode",
)
_POLICY_BREAK_GLASS_REASONS = frozenset({"all_administrators_lost", "administrator_store_recovered"})


class ContractValidationError(ValueError):
    """Raised when a public Hormuz contract is malformed or unsupported."""


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
        (USAGE_REPORT_SCHEMA_ID, 1): _validate_usage_report,
        (AUDIT_ANCHOR_SCHEMA_ID, AUDIT_ANCHOR_SCHEMA_VERSION): _validate_audit_anchor,
    }.get((schema_id, schema_version))
    if validator is None:
        raise ContractValidationError(f"unsupported Hormuz contract: {schema_id} v{schema_version}")
    validator(value)


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


def validate_policy_control_event(value: Mapping[str, Any]) -> None:
    """Validate one metadata-only immutable policy-control evidence row.

    This durable format is separate from response envelopes because policy
    control events are written to PostgreSQL, not relayed to an AI client.
    Its validation is deliberately structural: no policy values, model names,
    request content, or secret material can enter ``change_summary``.
    """

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
            "version_id",
            "generation",
            "reason_code",
            "change_summary",
        },
    )
    if _value_string(value, "event_schema_id") != POLICY_CONTROL_EVENT_SCHEMA_ID:
        raise ContractValidationError("policy control event schema_id is unsupported")
    if _value_integer(value, "event_schema_version", minimum=1) != POLICY_CONTROL_EVENT_SCHEMA_VERSION:
        raise ContractValidationError("policy control event schema_version is unsupported")
    _value_string(value, "organization_id")
    _value_string(value, "occurred_at")
    event_type = _value_string(value, "event_type")
    if event_type not in _POLICY_CONTROL_EVENT_TYPES:
        raise ContractValidationError("policy control event_type is unsupported")
    actor_kind = _value_string(value, "actor_kind")
    if actor_kind not in {"static", "oidc", "break_glass"}:
        raise ContractValidationError("policy control actor_kind is unsupported")
    _policy_identity_key(value, "actor_identity_key", kind=actor_kind)
    target_identity_key = _nullable_string(value, "target_identity_key")
    version_id = _nullable_string(value, "version_id")
    if version_id is not None:
        _policy_version_identifier(version_id, "version_id")
    generation = value.get("generation")
    if generation is not None:
        _value_integer(value, "generation", minimum=1)
    reason_code = _nullable_string(value, "reason_code")
    change_summary = value.get("change_summary")
    if change_summary is not None and not isinstance(change_summary, Mapping):
        raise ContractValidationError("change_summary must be an object or null")

    if event_type == "bootstrap_initialized":
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _event_requires_none(target_identity_key, version_id, generation, reason_code)
        _validate_bootstrap_change_summary(change_summary)
        return
    if event_type in {"administrator_granted", "administrator_revoked"}:
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _policy_identity_key_value(target_identity_key, "target_identity_key")
        _event_requires_none(version_id, generation, reason_code, change_summary)
        return
    if event_type == "policy_staged":
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _event_requires_none(target_identity_key, generation, reason_code)
        if version_id is None:
            raise ContractValidationError("policy_staged requires version_id")
        if not isinstance(change_summary, Mapping):
            raise ContractValidationError("policy_staged requires change_summary")
        _validate_redacted_change_summary(change_summary)
        return
    if event_type in {"policy_activated", "policy_rolled_back"}:
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _event_requires_none(target_identity_key, reason_code, change_summary)
        if version_id is None or generation is None:
            raise ContractValidationError("policy activation event requires version_id and generation")
        return
    # ``event_type`` was checked above, so this is the narrowly controlled
    # break-glass recovery record.
    _event_requires_actor(actor_kind, {"break_glass"})
    _policy_identity_key_value(target_identity_key, "target_identity_key")
    _event_requires_none(version_id, generation, change_summary)
    if reason_code not in _POLICY_BREAK_GLASS_REASONS:
        raise ContractValidationError("break_glass_recovered requires a supported reason_code")


def validate_policy_action(value: str) -> None:
    if value not in _POLICY_ACTIONS:
        raise ContractValidationError(f"unsupported policy action: {value}")


def validate_request_status(value: str) -> None:
    if value not in _REQUEST_STATUSES:
        raise ContractValidationError(f"unsupported request status: {value}")


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


def _validate_error(value: Mapping[str, Any], allowed_codes: frozenset[str]) -> None:
    _exact_keys(value, {"schema_id", "schema_version", "error"})
    error = _value_mapping(value, "error")
    _exact_keys(error, {"code", "message"}, path="error")
    code = _value_string(error, "code", path="error")
    if code not in allowed_codes:
        raise ContractValidationError(f"unsupported public error code: {code}")
    _value_string(error, "message", path="error")


def _validate_policy_decision(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
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
        },
    )
    if not isinstance(value.get("allowed"), bool):
        raise ContractValidationError("allowed must be a boolean")
    validate_policy_action(_value_string(value, "action"))
    _value_string(value, "reason")
    _value_string(value, "requested_model")
    _nullable_string(value, "resolved_alias")
    _nullable_string(value, "routed_model")
    _nullable_integer(value, "max_output_tokens", minimum=1)
    _value_string(value, "policy_version")


def _validate_policy_control_status(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "initialized",
            "active",
            "versions",
            "administrators",
        },
    )
    _value_string(value, "organization_id")
    if not isinstance(value.get("initialized"), bool):
        raise ContractValidationError("initialized must be a boolean")
    active = value.get("active")
    if active is not None:
        if not isinstance(active, Mapping):
            raise ContractValidationError("active must be an object or null")
        _exact_keys(
            active,
            {
                "version_id",
                "generation",
                "activated_at",
                "activated_by_kind",
                "activated_by_identity_key",
            },
            path="active",
        )
        _policy_version_identifier(_value_string(active, "version_id", path="active"), "active.version_id")
        _value_integer(active, "generation", minimum=1, path="active")
        _value_string(active, "activated_at", path="active")
        _administrator_key_fields(active, prefix="activated_by_", path="active")

    versions = value.get("versions")
    if not isinstance(versions, list):
        raise ContractValidationError("versions must be an array")
    for index, version in enumerate(versions):
        if not isinstance(version, Mapping):
            raise ContractValidationError(f"versions[{index}] must be an object")
        _exact_keys(
            version,
            {
                "version_id",
                "content_sha256",
                "created_at",
                "author_kind",
                "author_identity_key",
                "change_summary",
            },
            path=f"versions[{index}]",
        )
        _policy_version_identifier(
            _value_string(version, "version_id", path=f"versions[{index}]"),
            f"versions[{index}].version_id",
        )
        _sha256_digest(
            _value_string(version, "content_sha256", path=f"versions[{index}]"),
            f"versions[{index}].content_sha256",
        )
        _value_string(version, "created_at", path=f"versions[{index}]")
        _administrator_key_fields(version, prefix="author_", path=f"versions[{index}]")
        _validate_redacted_change_summary(_value_mapping(version, "change_summary", path=f"versions[{index}]"))

    administrators = value.get("administrators")
    if not isinstance(administrators, list):
        raise ContractValidationError("administrators must be an array")
    for index, administrator in enumerate(administrators):
        if not isinstance(administrator, Mapping):
            raise ContractValidationError(f"administrators[{index}] must be an object")
        _exact_keys(
            administrator,
            {"authentication_kind", "actor_id", "issuer", "subject"},
            path=f"administrators[{index}]",
        )
        kind = _value_string(administrator, "authentication_kind", path=f"administrators[{index}]")
        actor_id = _nullable_string(administrator, "actor_id", path=f"administrators[{index}]")
        issuer = _nullable_string(administrator, "issuer", path=f"administrators[{index}]")
        subject = _nullable_string(administrator, "subject", path=f"administrators[{index}]")
        if kind == "static" and actor_id is not None and issuer is None and subject is None:
            continue
        if kind == "oidc" and actor_id is None and issuer is not None and subject is not None:
            continue
        raise ContractValidationError(f"administrators[{index}] has an invalid stable identity key")


def _administrator_key_fields(value: Mapping[str, Any], *, prefix: str, path: str) -> None:
    kind = _value_string(value, f"{prefix}kind", path=path)
    if kind not in {"static", "oidc"}:
        raise ContractValidationError(f"{path}.{prefix}kind is unsupported")
    _policy_identity_key(value, f"{prefix}identity_key", kind=kind, path=path)


def _validate_redacted_change_summary(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"summary_version", "scopes", "egress_fields"}, path="change_summary")
    if _value_integer(value, "summary_version", minimum=1, path="change_summary") != 1:
        raise ContractValidationError("change_summary.summary_version is unsupported")
    if tuple(_value_string_list(value, "egress_fields", path="change_summary")) != _POLICY_EGRESS_FIELDS:
        raise ContractValidationError("change_summary.egress_fields is invalid")
    scopes = _value_mapping(value, "scopes", path="change_summary")
    _exact_keys(scopes, {"organization", "teams", "actors"}, path="change_summary.scopes")
    organization = _value_mapping(scopes, "organization", path="change_summary.scopes")
    _exact_keys(organization, {"fields"}, path="change_summary.scopes.organization")
    _validate_policy_change_fields(organization, "fields", path="change_summary.scopes.organization")
    for scope in ("teams", "actors"):
        item = _value_mapping(scopes, scope, path="change_summary.scopes")
        _exact_keys(item, {"count", "fields"}, path=f"change_summary.scopes.{scope}")
        _value_integer(item, "count", minimum=0, path=f"change_summary.scopes.{scope}")
        _validate_policy_change_fields(item, "fields", path=f"change_summary.scopes.{scope}")


def _validate_bootstrap_change_summary(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ContractValidationError("bootstrap_initialized requires change_summary")
    _exact_keys(value, {"summary_version", "bootstrap_administrator_count"}, path="change_summary")
    if _value_integer(value, "summary_version", minimum=1, path="change_summary") != 1:
        raise ContractValidationError("change_summary.summary_version is unsupported")
    _value_integer(value, "bootstrap_administrator_count", minimum=1, path="change_summary")


def _validate_policy_change_fields(value: Mapping[str, Any], field: str, *, path: str) -> None:
    fields = _value_string_list(value, field, path=path)
    if (
        len(fields) != len(set(fields))
        or any(item not in _POLICY_CHANGE_FIELDS for item in fields)
        or fields != sorted(fields)
    ):
        raise ContractValidationError(f"{path}.{field} is invalid")


def _event_requires_actor(kind: str, allowed: set[str]) -> None:
    if kind not in allowed:
        raise ContractValidationError("policy control event actor kind is invalid for event_type")


def _event_requires_none(*values: object) -> None:
    if any(value is not None for value in values):
        raise ContractValidationError("policy control event has fields invalid for event_type")


def _policy_identity_key(
    value: Mapping[str, Any],
    field: str,
    *,
    kind: str,
    path: str | None = None,
) -> None:
    _policy_identity_key_value(_value_string(value, field, path=path), field if path is None else f"{path}.{field}", kind=kind)


def _policy_identity_key_value(value: object, path: str, *, kind: str | None = None) -> None:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{path} must be a non-empty string")
    if kind == "break_glass":
        if value != "break_glass":
            raise ContractValidationError(f"{path} is invalid")
        return
    prefix, separator, digest = value.partition(":")
    if (
        not separator
        or prefix not in {"static", "oidc"}
        or (kind is not None and prefix != kind)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ContractValidationError(f"{path} is invalid")


def _policy_version_identifier(value: str, path: str) -> None:
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    if not value.startswith(prefix):
        raise ContractValidationError(f"{path} is invalid")
    _sha256_digest(digest, path)


def _sha256_digest(value: str, path: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ContractValidationError(f"{path} is invalid")


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
