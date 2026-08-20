from __future__ import annotations

import sqlite3
from types import MappingProxyType


CONTENT_FREE_SCHEMA_VERSION = "hormuz.content-free-schema.v2"


_USAGE_TABLE_COLUMNS = {
    "gateway_usage_events": (
        "id",
        "occurred_at",
        "organization_id",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "client",
        "protocol",
        "requested_model",
        "resolved_alias",
        "upstream_model",
        "actual_model",
        "policy_action",
        "status",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "billable_tokens",
        "cost_microusd",
        "cost_basis",
        "currency",
        "rate_card_version",
        "provider_usage_json",
        "provider_request_id",
        "redaction_count",
        "redaction_rules",
        "context_injection_mode",
        "context_injection_outcome",
        "context_injection_reason",
        "context_pack_id",
        "context_record_ids_json",
        "context_policy_version",
        "context_retrieval_version",
        "context_render_version",
        "context_repository_revision",
        "context_estimated_tokens",
        "context_assembly_milliseconds",
        "context_reuse_status",
        "gateway_latency_milliseconds",
        "policy_latency_milliseconds",
        "provider_latency_milliseconds",
    ),
    "gateway_secret_events": (
        "id",
        "occurred_at",
        "organization_id",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "client",
        "protocol",
        "requested_model",
        "routed_model",
        "action",
        "detection_count",
        "redaction_count",
        "rules",
        "event_type",
        "policy_version",
        "findings_json",
    ),
    "gateway_budget_reservations": (
        "id",
        "created_at",
        "expires_at",
        "organization_id",
        "actor_id",
        "team_id",
        "model_alias",
        "reserved_tokens",
        "reserved_cost_microusd",
    ),
    "gateway_admin_access_events": (
        "id",
        "occurred_at",
        "organization_id",
        "decision_actor_id",
        "decision_actor_name",
        "action",
        "group_by",
        "actor_filter_sha256",
        "team_filter_sha256",
        "window_start",
        "window_end",
        "result_count",
    ),
    "gateway_dlp_approval_requests": (
        "id",
        "created_at",
        "updated_at",
        "expires_at",
        "organization_id",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "client",
        "protocol",
        "requested_model",
        "routed_model",
        "policy_version",
        "payload_fingerprint",
        "rules_json",
        "detection_count",
        "status",
        "approved_by_actor_id",
        "approved_by_actor_name",
        "approved_at",
        "consumed_at",
    ),
    "gateway_dlp_approval_events": (
        "id",
        "occurred_at",
        "request_id",
        "organization_id",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "decision_actor_id",
        "decision_actor_name",
        "client",
        "protocol",
        "requested_model",
        "routed_model",
        "actual_model",
        "policy_version",
        "rules_json",
        "action",
    ),
    "gateway_provider_cost_imports": (
        "id",
        "imported_at",
        "organization_id",
        "provider",
        "source_sha256",
        "report_start",
        "report_end",
        "page_count",
        "bucket_count",
        "item_count",
    ),
    "gateway_provider_cost_items": (
        "id",
        "import_id",
        "item_ordinal",
        "bucket_start",
        "bucket_end",
        "amount_usd",
        "currency",
        "provider_scope_kind",
        "provider_scope_id",
        "line_item",
        "cost_type",
        "model",
        "service_tier",
        "token_type",
        "context_window",
        "inference_geo",
    ),
    "gateway_provider_cost_sources": (
        "id",
        "import_id",
        "observed_at",
        "source_kind",
        "api_contract",
        "query_start",
        "query_end",
        "query_scope",
    ),
}

_SESSION_TABLE_COLUMNS = {
    "session_security_events": (
        "id",
        "occurred_at",
        "session_id",
        "event_type",
        "organization_id",
        "target_actor_id",
        "target_team_id",
        "decision_actor_id",
        "decision_scope",
        "reason_code",
    ),
}

_CONTEXT_TABLE_COLUMNS = {
    "context_mutation_events": (
        "id",
        "occurred_at",
        "organization_id",
        "actor_id",
        "action",
        "prior_record_id",
        "prior_version",
        "new_record_id",
        "new_version",
        "policy_version",
        "kind",
        "classification",
        "visibility",
        "repository_id",
    ),
    "context_access_events": (
        "id",
        "occurred_at",
        "organization_id",
        "team_id",
        "actor_id",
        "action",
        "pack_id",
        "policy_version",
        "repository_id",
        "branch",
        "clearance",
        "include_provisional",
        "selected_records",
        "eligible_records",
        "matched_records",
        "estimated_tokens",
        "lifecycle_outcome",
        "excluded_records",
        "contradiction_groups",
    ),
    "context_lifecycle_events": (
        "id",
        "occurred_at",
        "organization_id",
        "repository_id",
        "branch",
        "action",
        "prior_version",
        "new_version",
        "snapshot_sha256",
        "artifact_count",
        "actor_id",
        "policy_version",
    ),
    "context_evidence_events": (
        "id",
        "organization_id",
        "record_id",
        "record_version",
        "subject_sha256",
        "signal",
        "signal_family",
        "evidence_ref_sha256",
        "observed_at",
        "created_at",
        "actor_id",
        "policy_version",
    ),
    "context_revalidation_events": (
        "id",
        "occurred_at",
        "organization_id",
        "repository_id",
        "branch",
        "job_id",
        "action",
        "status",
        "batch_records",
        "actor_id",
        "policy_version",
    ),
}


CONTENT_FREE_TABLE_COLUMNS = MappingProxyType(
    {
        "usage": MappingProxyType(_USAGE_TABLE_COLUMNS),
        "session": MappingProxyType(_SESSION_TABLE_COLUMNS),
        "context": MappingProxyType(_CONTEXT_TABLE_COLUMNS),
    }
)


class ContentFreeSchemaError(RuntimeError):
    """Fixed-detail failure for routine telemetry schema drift."""

    code = "content_free_schema_incompatible"

    def __init__(self) -> None:
        super().__init__(self.code)


def validate_content_free_schema(
    connection: sqlite3.Connection,
    *,
    store_kind: str,
) -> None:
    """Fail closed when routine observability gains or loses a column."""

    expected_tables = CONTENT_FREE_TABLE_COLUMNS.get(store_kind)
    if expected_tables is None:
        raise ContentFreeSchemaError()
    try:
        for table, expected_columns in expected_tables.items():
            observed_columns = frozenset(
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            )
            if observed_columns != frozenset(expected_columns):
                raise ContentFreeSchemaError()
    except sqlite3.Error as error:
        raise ContentFreeSchemaError() from error
