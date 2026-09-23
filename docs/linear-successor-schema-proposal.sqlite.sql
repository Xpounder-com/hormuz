-- REVIEW ONLY: proposed v1.3 SQLite-14 Linear successor shape.
-- This file is not a bundled migration and must not be applied to a runtime
-- database. Transition tests execute it only through an explicitly patched,
-- test-only migration slot after the real SQLite-13 baseline is present.

CREATE TABLE portfolio_linear_source_binding_versions (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
    version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
    binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) = 36),
    previous_version INTEGER,
    binding_state TEXT NOT NULL CHECK (binding_state IN ('active','revoked')),
    source_workspace_id TEXT NOT NULL CHECK (length(source_workspace_id) = 36),
    source_webhook_id TEXT NOT NULL CHECK (length(source_webhook_id) = 36),
    source_team_ids_json TEXT NOT NULL CHECK (length(source_team_ids_json) BETWEEN 2 AND 65536),
    typed_enrollment_json TEXT NOT NULL CHECK (length(typed_enrollment_json) BETWEEN 2 AND 1048576),
    credential_version TEXT NOT NULL CHECK (length(credential_version) BETWEEN 1 AND 128),
    fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
    registered_by TEXT NOT NULL CHECK (length(registered_by) BETWEEN 1 AND 128),
    registered_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
    PRIMARY KEY (organization_id, connector_id, version),
    UNIQUE (organization_id, binding_event_id),
    UNIQUE (organization_id, connector_id, request_digest),
    CHECK (
        (version=1 AND previous_version IS NULL AND binding_state='active')
        OR (version>1 AND previous_version=version-1)
    )
);

CREATE TABLE gateway_linear_delivery_receipts (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
    receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 36),
    binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
    source_delivery_id TEXT NOT NULL CHECK (length(source_delivery_id) BETWEEN 1 AND 128),
    credential_version TEXT NOT NULL CHECK (length(credential_version) BETWEEN 1 AND 128),
    body_fingerprint TEXT NOT NULL CHECK (length(body_fingerprint) = 64),
    fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
    source_fact_fingerprint TEXT NOT NULL CHECK (length(source_fact_fingerprint) = 64),
    source_fact_key_version INTEGER NOT NULL CHECK (source_fact_key_version BETWEEN 1 AND 2147483647),
    received_at TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    response_digest TEXT NOT NULL CHECK (length(response_digest) = 64),
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, connector_id, receipt_id),
    UNIQUE (organization_id, connector_id, source_delivery_id),
    UNIQUE (organization_id, connector_id, fingerprint_key_version, body_fingerprint),
    UNIQUE (organization_id, connector_id, source_fact_key_version, source_fact_fingerprint),
    FOREIGN KEY (organization_id, connector_id, binding_version)
        REFERENCES portfolio_linear_source_binding_versions
            (organization_id, connector_id, version)
);

CREATE TABLE portfolio_linear_context_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
    context_event_id TEXT NOT NULL CHECK (length(context_event_id) = 36),
    receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 36),
    object_kind TEXT NOT NULL CHECK (object_kind IN ('initiative','project','cycle','issue')),
    object_id TEXT NOT NULL CHECK (length(object_id) = 36),
    lifecycle TEXT NOT NULL CHECK (lifecycle IN ('created','updated','archived','restored','deleted')),
    normalized_state TEXT NOT NULL CHECK (normalized_state IN ('not_started','in_progress','completed','canceled','unknown')),
    relationship_coverage TEXT NOT NULL CHECK (relationship_coverage IN ('complete','partial','unknown','not_applicable')),
    revision_kind TEXT NOT NULL CHECK (revision_kind IN ('source_updated_at_v1','source_revision_counter_v1','unknown')),
    revision_value TEXT CHECK (revision_value IS NULL OR length(revision_value) BETWEEN 1 AND 64),
    ordering_state TEXT NOT NULL CHECK (ordering_state IN ('current','late','incomparable','unknown')),
    scope_state TEXT NOT NULL CHECK (scope_state IN ('matched','unmatched','excluded')),
    event_at TEXT,
    observed_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
    commit_sequence INTEGER NOT NULL CHECK (commit_sequence >= 1),
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
    PRIMARY KEY (organization_id, connector_id, context_event_id),
    UNIQUE (organization_id, commit_sequence),
    FOREIGN KEY (organization_id, connector_id, receipt_id)
        REFERENCES gateway_linear_delivery_receipts
            (organization_id, connector_id, receipt_id)
);

CREATE TABLE portfolio_linear_context_retention_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
    retention_event_id TEXT NOT NULL CHECK (length(retention_event_id) = 36),
    target_context_event_id TEXT NOT NULL CHECK (length(target_context_event_id) = 36),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    reason_code TEXT NOT NULL CHECK (reason_code = 'tombstoned'),
    observed_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, connector_id, retention_event_id),
    FOREIGN KEY (organization_id, connector_id, target_context_event_id)
        REFERENCES portfolio_linear_context_events
            (organization_id, connector_id, context_event_id)
);

CREATE INDEX portfolio_linear_source_binding_current
    ON portfolio_linear_source_binding_versions (organization_id, connector_id, version DESC);
CREATE INDEX gateway_linear_delivery_receipt_time
    ON gateway_linear_delivery_receipts (organization_id, connector_id, committed_at, receipt_id);
CREATE INDEX portfolio_linear_context_object_time
    ON portfolio_linear_context_events (organization_id, connector_id, object_kind, object_id, commit_sequence DESC);
CREATE INDEX portfolio_linear_context_retention_target
    ON portfolio_linear_context_retention_events (organization_id, connector_id, target_context_event_id, ingested_at);

CREATE TRIGGER portfolio_linear_source_binding_versions_no_update
BEFORE UPDATE ON portfolio_linear_source_binding_versions
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER portfolio_linear_source_binding_versions_no_delete
BEFORE DELETE ON portfolio_linear_source_binding_versions
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER gateway_linear_delivery_receipts_no_update
BEFORE UPDATE ON gateway_linear_delivery_receipts
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER gateway_linear_delivery_receipts_no_delete
BEFORE DELETE ON gateway_linear_delivery_receipts
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER portfolio_linear_context_events_no_update
BEFORE UPDATE ON portfolio_linear_context_events
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER portfolio_linear_context_events_no_delete
BEFORE DELETE ON portfolio_linear_context_events
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER portfolio_linear_context_retention_events_no_update
BEFORE UPDATE ON portfolio_linear_context_retention_events
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
CREATE TRIGGER portfolio_linear_context_retention_events_no_delete
BEFORE DELETE ON portfolio_linear_context_retention_events
BEGIN SELECT RAISE(ABORT, 'linear_metadata_append_only'); END;
