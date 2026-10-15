"""Schema identifiers and fixed validation vocabularies."""

from __future__ import annotations

MANIFEST_SCHEMA_ID = "hormuz.policy-evidence-manifest"

MANIFEST_SCHEMA_VERSION = 1

RELAY_METADATA_SCHEMA_ID = "hormuz.relay-metadata"

RELAY_METADATA_SCHEMA_VERSION = 1

ROUTE_ADMISSION_SCHEMA_ID = "hormuz.route-admission"

ROUTE_ADMISSION_SCHEMA_VERSION = 2

CONNECTED_CLIENT_STATUS_SCHEMA_ID = "hormuz.connected-client-status"

CONNECTED_CLIENT_STATUS_SCHEMA_VERSION = 1

CONNECTED_CLIENT_PREVIEW_SCHEMA_ID = "hormuz.connected-client-preview"

CONNECTED_CLIENT_PREVIEW_SCHEMA_VERSION = 1

CONNECTED_CLIENT_ERROR_SCHEMA_ID = "hormuz.connected-client-error"

CONNECTED_CLIENT_ERROR_SCHEMA_VERSION = 1

LOCAL_SETUP_SCHEMA_ID = "hormuz.local-setup"
LOCAL_SETUP_PROFILE_SCHEMA_ID = "hormuz.local-setup-profile"

CONNECTED_CLIENT_ERROR_ACTIONS = {
    "connected_client_version_unsupported": "use_pinned_client_or_disconnect",
    "connected_client_recovery_required": "disconnect_then_connect",
    "connected_client_setup_changed": "disconnect_then_connect",
    "connected_client_router_unavailable": "disconnect_then_connect",
    "connected_client_configuration_changed": "inspect_settings_then_disconnect",
    "connected_client_configuration_invalid": "repair_configuration_then_disconnect",
    "connected_client_state_invalid": "preserve_journal_and_restore_manually",
    "connected_client_generation_mismatch": "inspect_current_generation",
    "connected_client_managed_configuration": "use_managed_setup",
    **{("connected_client_" + code): "inspect_status_and_configuration" for code in (
        "authentication_unsupported", "busy", "client_not_allowed", "client_unavailable",
        "configuration_shape_unsupported", "directory_unsafe", "file_unsafe",
        "managed_configuration_unknown", "model_unavailable", "platform_unsupported",
        "restoration_unavailable", "router_configuration_unavailable",
        "router_configuration_unsupported", "router_start_failed", "setup_unavailable",
        "runtime_invalid", "runtime_start_failed", "runtime_occupied",
    )},
}

AUDIT_EVENT_SCHEMA_ID = "hormuz.audit-event"

AUDIT_EVENT_SCHEMA_VERSION = 2

AUDIT_ANCHOR_SCHEMA_ID = "hormuz.audit-anchor"

AUDIT_ANCHOR_SCHEMA_VERSION = 1

AUDIT_CHAIN_VERSION = 1

AUDIT_CHAIN_ENTRY_SCHEMA_ID = "hormuz.commit-audit-chain-entry"

# Version 2 binds a strictly allowlisted custody-evidence source identity in
# addition to the complete canonical metadata-only source record.  Version 1
# remains supported for the existing gateway usage/security chain.
AUDIT_CHAIN_ENTRY_SCHEMA_VERSION = 2

AUDIT_CHAIN_ENTRY_LEGACY_SCHEMA_VERSION = 1

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

POLICY_COMPARISON_SCHEMA_ID = "hormuz.policy-comparison"

POLICY_COMPARISON_SCHEMA_VERSION = 1

POLICY_PREVIEW_SCHEMA_ID = "hormuz.policy-preview"

POLICY_PREVIEW_SCHEMA_VERSION = 1

POLICY_SCENARIO_SUITE_SCHEMA_ID = "hormuz.policy-scenario-suite"

POLICY_SCENARIO_SUITE_SCHEMA_VERSION = 1

POLICY_SCENARIO_MAX_COUNT = 100

POLICY_EVALUATION_SCHEMA_ID = "hormuz.policy-evaluation"

POLICY_EVALUATION_SCHEMA_VERSION = 1

POLICY_HISTORY_SCHEMA_ID = "hormuz.policy-history"

POLICY_HISTORY_SCHEMA_VERSION = 1

POLICY_HISTORY_DEFAULT_LIMIT = 20

POLICY_HISTORY_MAX_LIMIT = 100

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

CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID = "hormuz.custody-envelope-attestation"

CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION = 1

CUSTODY_DELETION_EVENT_SCHEMA_ID = "hormuz.custody-deletion-event"

CUSTODY_DELETION_EVENT_SCHEMA_VERSION = 1

CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID = "hormuz.custody-evidence-export"

CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION = 1

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

ROUTE_ADMISSION_ERROR_CODES = frozenset({
    "hormuz_route_invalid_routing_mode", "hormuz_route_affinity_required",
    "hormuz_route_local_route_unavailable", "hormuz_route_local_capacity_busy",
    "hormuz_route_destination_not_admitted", "hormuz_route_policy_changed",
    "hormuz_route_custody_restricted", "hormuz_route_custody_unavailable",
})

PUBLIC_ERROR_CODES = frozenset({*PUBLIC_ERROR_CODES_V2, "hormuz_custody_restricted",
                              *ROUTE_ADMISSION_ERROR_CODES, *CONNECTED_CLIENT_ERROR_ACTIONS})

_CURRENT_SCHEMA_VERSIONS = {
    "hormuz.local-coding-evidence": 1,
    LOCAL_SETUP_SCHEMA_ID: 1,
    LOCAL_SETUP_PROFILE_SCHEMA_ID: 2,
    CONNECTED_CLIENT_STATUS_SCHEMA_ID: CONNECTED_CLIENT_STATUS_SCHEMA_VERSION,
    CONNECTED_CLIENT_PREVIEW_SCHEMA_ID: CONNECTED_CLIENT_PREVIEW_SCHEMA_VERSION,
    CONNECTED_CLIENT_ERROR_SCHEMA_ID: CONNECTED_CLIENT_ERROR_SCHEMA_VERSION,
    ROUTE_ADMISSION_SCHEMA_ID: ROUTE_ADMISSION_SCHEMA_VERSION,
    HEALTH_SCHEMA_ID: 1,
    READINESS_SCHEMA_ID: READINESS_SCHEMA_VERSION,
    IDENTITY_SCHEMA_ID: 1,
    USAGE_SUMMARY_SCHEMA_ID: 1,
    ERROR_SCHEMA_ID: ERROR_SCHEMA_VERSION,
    POLICY_DECISION_SCHEMA_ID: 1,
    POLICY_CONTROL_STATUS_SCHEMA_ID: 1,
    POLICY_COMPARISON_SCHEMA_ID: POLICY_COMPARISON_SCHEMA_VERSION,
    POLICY_PREVIEW_SCHEMA_ID: POLICY_PREVIEW_SCHEMA_VERSION,
    POLICY_SCENARIO_SUITE_SCHEMA_ID: POLICY_SCENARIO_SUITE_SCHEMA_VERSION,
    POLICY_EVALUATION_SCHEMA_ID: POLICY_EVALUATION_SCHEMA_VERSION,
    POLICY_HISTORY_SCHEMA_ID: POLICY_HISTORY_SCHEMA_VERSION,
    CUSTODY_CONTROL_STATUS_SCHEMA_ID: CUSTODY_CONTROL_STATUS_SCHEMA_VERSION,
    USAGE_REPORT_SCHEMA_ID: 1,
    AUDIT_ANCHOR_SCHEMA_ID: AUDIT_ANCHOR_SCHEMA_VERSION,
    AUDIT_CHAIN_ENTRY_SCHEMA_ID: AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    AUDIT_CHAIN_CHECKPOINT_SCHEMA_ID: AUDIT_CHAIN_CHECKPOINT_SCHEMA_VERSION,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID: CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION,
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

_POLICY_LIFECYCLE_EVENT_TYPES = frozenset({"policy_staged", "policy_activated", "policy_rolled_back"})

_POLICY_COMPARISON_CHANGE_TYPES = frozenset({"added", "removed", "changed"})

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
