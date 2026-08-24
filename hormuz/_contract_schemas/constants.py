"""Schema identifiers and fixed validation vocabularies."""

from __future__ import annotations

MANIFEST_SCHEMA_ID = "hormuz.policy-evidence-manifest"

MANIFEST_SCHEMA_VERSION = 1

RELAY_METADATA_SCHEMA_ID = "hormuz.relay-metadata"

RELAY_METADATA_SCHEMA_VERSION = 1

AUDIT_EVENT_SCHEMA_ID = "hormuz.audit-event"

AUDIT_EVENT_SCHEMA_VERSION = 2

AUDIT_ANCHOR_SCHEMA_ID = "hormuz.audit-anchor"

AUDIT_ANCHOR_SCHEMA_VERSION = 1

AUDIT_CHAIN_VERSION = 1

AUDIT_CHAIN_ENTRY_SCHEMA_ID = "hormuz.commit-audit-chain-entry"

AUDIT_CHAIN_ENTRY_SCHEMA_VERSION = 1

AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID = "hormuz.audit-chain-checkpoint"

AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION = 1

REQUEST_ATTEMPT_SCHEMA_ID = "hormuz.request-attempt"

REQUEST_ATTEMPT_SCHEMA_VERSION = 1

REQUEST_ATTEMPT_EVENT_SCHEMA_ID = "hormuz.request-attempt-event"

REQUEST_ATTEMPT_EVENT_SCHEMA_VERSION = 1

HEALTH_SCHEMA_ID = "hormuz.gateway-health"

READINESS_SCHEMA_ID = "hormuz.gateway-readiness"

READINESS_SCHEMA_VERSION = 1

IDENTITY_SCHEMA_ID = "hormuz.gateway-identity"

USAGE_SUMMARY_SCHEMA_ID = "hormuz.gateway-usage-summary"

ERROR_SCHEMA_ID = "hormuz.gateway-error"

ERROR_SCHEMA_VERSION = 3

POLICY_DECISION_SCHEMA_ID = "hormuz.policy-decision"

POLICY_CONTROL_STATUS_SCHEMA_ID = "hormuz.policy-control-status"

POLICY_DOCUMENT_SCHEMA_ID = "hormuz.policy-document"

POLICY_DOCUMENT_SCHEMA_VERSION = 1

POLICY_CONTROL_EVENT_SCHEMA_ID = "hormuz.policy-control-event"

POLICY_CONTROL_EVENT_SCHEMA_VERSION = 1

CUSTODY_CONTROL_STATUS_SCHEMA_ID = "hormuz.custody-control-status"

CUSTODY_CONTROL_STATUS_SCHEMA_VERSION = 3

CUSTODY_CONTROL_EVENT_SCHEMA_ID = "hormuz.custody-control-event"

CUSTODY_CONTROL_EVENT_SCHEMA_VERSION = 1

CUSTODY_EXECUTION_SCHEMA_ID = "hormuz.custody-execution-attempt"

CUSTODY_EXECUTION_SCHEMA_VERSION = 2

CUSTODY_EXECUTION_EVENT_SCHEMA_ID = "hormuz.custody-execution-event"

CUSTODY_EXECUTION_EVENT_SCHEMA_VERSION = 1

CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID = "hormuz.custody-lifecycle-event"

CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION = 1

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

PUBLIC_ERROR_CODES_V2 = frozenset({*PUBLIC_ERROR_CODES_V1, "hormuz_storage_unavailable"})

PUBLIC_ERROR_CODES = frozenset({*PUBLIC_ERROR_CODES_V2, "hormuz_custody_restricted"})

_CURRENT_SCHEMA_VERSIONS = {
    HEALTH_SCHEMA_ID: 1,
    READINESS_SCHEMA_ID: READINESS_SCHEMA_VERSION,
    IDENTITY_SCHEMA_ID: 1,
    USAGE_SUMMARY_SCHEMA_ID: 1,
    ERROR_SCHEMA_ID: ERROR_SCHEMA_VERSION,
    POLICY_DECISION_SCHEMA_ID: 1,
    POLICY_CONTROL_STATUS_SCHEMA_ID: 1,
    CUSTODY_CONTROL_STATUS_SCHEMA_ID: CUSTODY_CONTROL_STATUS_SCHEMA_VERSION,
    USAGE_REPORT_SCHEMA_ID: 1,
    AUDIT_ANCHOR_SCHEMA_ID: AUDIT_ANCHOR_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_ID: AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID: AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
}

_REQUEST_STATUSES = frozenset({"succeeded", "failed", "denied", "rate_limited"})

_REQUEST_ATTEMPT_STATES = frozenset({"pending", "succeeded", "failed", "rate_limited", "outcome_unknown"})

_REQUEST_ATTEMPT_UNKNOWN_REASONS = frozenset(
    {"provider_transport_ambiguous", "provider_stream_interrupted", "stale_pending"}
)

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

_CUSTODY_OPERATION_TYPES = frozenset(
    {
        "seal_envelope",
        "rewrap_envelope",
        "verify_restore",
        "retire_envelope",
        "disable_provider_credential",
        "retire_key_reference",
        "resolve_recovery",
    }
)

_CUSTODY_CONTROL_EVENT_TYPES = frozenset(
    {
        "bootstrap_initialized",
        "administrator_granted",
        "administrator_revoked",
        "operation_requested",
        "operation_approved",
        "operation_authorized",
    }
)

_CUSTODY_EXECUTION_STATES = frozenset({"pending", "succeeded", "failed", "outcome_unknown"})

_CUSTODY_EXECUTION_UNKNOWN_REASONS = frozenset({"external_result_ambiguous", "stale_pending"})

_CUSTODY_EXECUTION_FAILURE_REASONS = frozenset({"execution_failed"})

_CUSTODY_LIFECYCLE_OPERATION_TYPES = frozenset(
    {
        "retire_envelope",
        "disable_provider_credential",
        "retire_key_reference",
        "resolve_recovery",
    }
)

_CUSTODY_RECOVERY_RESOLUTION_CODES = frozenset(
    {"confirmed_applied", "confirmed_not_applied", "compensating_action_completed"}
)
