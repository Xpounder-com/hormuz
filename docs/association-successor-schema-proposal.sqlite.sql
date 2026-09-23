-- REVIEW ONLY: proposed v1.3 SQLite-16 run-to-outcome association shape.
-- This file is not a bundled migration and must not be applied to a runtime
-- database. Transition tests execute it only through an explicitly patched,
-- test-only migration slot after the real SQLite-15 baseline is present.

CREATE TABLE portfolio_association_audit_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    event_id TEXT NOT NULL CHECK (length(event_id) = 36),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    actor_id TEXT,
    operation TEXT NOT NULL CHECK (operation IN ('link','correct','tombstone','evaluate','list_associations')),
    entity_id TEXT,
    reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','corrected','tombstoned','associated','unmatched','ambiguous','excluded','observed')),
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, event_id),
    UNIQUE (organization_id, sequence)
);

CREATE TABLE portfolio_run_work_link_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    link_event_id TEXT NOT NULL CHECK (length(link_event_id) = 36),
    request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) BETWEEN 1 AND 128),
    attribution_event_id TEXT NOT NULL CHECK (length(attribution_event_id) BETWEEN 1 AND 128),
    connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
    source_event_id TEXT NOT NULL CHECK (length(source_event_id) BETWEEN 1 AND 256),
    external_object_id TEXT NOT NULL CHECK (length(external_object_id) BETWEEN 1 AND 256),
    source_revision TEXT CHECK (source_revision IS NULL OR length(source_revision) BETWEEN 1 AND 256),
    work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
    work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
    binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) BETWEEN 1 AND 128),
    state TEXT NOT NULL CHECK (state IN ('active','tombstoned')),
    evidence_level TEXT NOT NULL CHECK (evidence_level = 'associated'),
    supersedes_link_event_id TEXT,
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','corrected','tombstoned')),
    event_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    PRIMARY KEY (organization_id, link_event_id),
    UNIQUE (organization_id, request_attempt_id, connector_id, source_event_id, supersedes_link_event_id),
    FOREIGN KEY (organization_id, request_attempt_id)
        REFERENCES gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, attribution_event_id)
        REFERENCES portfolio_attribution_events (organization_id, attribution_event_id),
    FOREIGN KEY (organization_id, connector_id, source_event_id)
        REFERENCES portfolio_outcome_events (organization_id, connector_id, source_event_id),
    FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
        REFERENCES portfolio_work_scope_versions (organization_id, work_scope_id, version),
    FOREIGN KEY (organization_id, binding_event_id)
        REFERENCES portfolio_binding_events (organization_id, binding_event_id),
    FOREIGN KEY (organization_id, supersedes_link_event_id)
        REFERENCES portfolio_run_work_link_events (organization_id, link_event_id),
    FOREIGN KEY (organization_id, sequence)
        REFERENCES portfolio_association_audit_events (organization_id, sequence),
    CHECK (
        (state='active' AND reason_code IN ('explicit_link','corrected'))
        OR (state='tombstoned' AND reason_code='tombstoned')
    )
);

CREATE TABLE portfolio_run_work_link_idempotency (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    request_mac TEXT NOT NULL CHECK (length(request_mac) = 64),
    key_version INTEGER NOT NULL CHECK (key_version BETWEEN 1 AND 2147483647),
    link_event_id TEXT NOT NULL CHECK (length(link_event_id) = 36),
    PRIMARY KEY (organization_id, actor_id, idempotency_key),
    FOREIGN KEY (organization_id, link_event_id)
        REFERENCES portfolio_run_work_link_events (organization_id, link_event_id)
);

CREATE TABLE portfolio_run_outcome_association_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    association_event_id TEXT NOT NULL CHECK (length(association_event_id) = 36),
    connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
    source_event_id TEXT NOT NULL CHECK (length(source_event_id) BETWEEN 1 AND 256),
    external_object_id TEXT NOT NULL CHECK (length(external_object_id) BETWEEN 1 AND 256),
    source_revision TEXT CHECK (source_revision IS NULL OR length(source_revision) BETWEEN 1 AND 256),
    request_attempt_id TEXT,
    work_scope_id TEXT,
    work_scope_version INTEGER,
    rule_id TEXT NOT NULL CHECK (length(rule_id) BETWEEN 1 AND 128),
    rule_version INTEGER NOT NULL CHECK (rule_version BETWEEN 1 AND 2147483647),
    window_id TEXT NOT NULL CHECK (length(window_id) = 64),
    window_start_at TEXT NOT NULL,
    window_end_at TEXT NOT NULL,
    evaluation_as_of TEXT NOT NULL,
    snapshot_sequence INTEGER NOT NULL CHECK (snapshot_sequence >= 0),
    candidate_count INTEGER NOT NULL CHECK (candidate_count BETWEEN 0 AND 2147483647),
    state TEXT NOT NULL CHECK (state IN ('unmatched','ambiguous','associated','excluded')),
    evidence_level TEXT NOT NULL CHECK (evidence_level IN ('descriptive','associated')),
    link_event_id TEXT,
    supersedes_event_id TEXT,
    reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','missing_evidence','multiple_eligible_links','source_excluded','source_superseded','link_tombstoned','unsupported')),
    event_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    PRIMARY KEY (organization_id, association_event_id),
    UNIQUE (organization_id, sequence),
    FOREIGN KEY (organization_id, connector_id, source_event_id)
        REFERENCES portfolio_outcome_events (organization_id, connector_id, source_event_id),
    FOREIGN KEY (organization_id, request_attempt_id)
        REFERENCES gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
        REFERENCES portfolio_work_scope_versions (organization_id, work_scope_id, version),
    FOREIGN KEY (organization_id, link_event_id)
        REFERENCES portfolio_run_work_link_events (organization_id, link_event_id),
    FOREIGN KEY (organization_id, supersedes_event_id)
        REFERENCES portfolio_run_outcome_association_events (organization_id, association_event_id),
    FOREIGN KEY (organization_id, sequence)
        REFERENCES portfolio_association_audit_events (organization_id, sequence),
    CHECK ((work_scope_id IS NULL AND work_scope_version IS NULL) OR (work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL)),
    CHECK (window_start_at < window_end_at),
    CHECK ((state='associated' AND evidence_level='associated' AND request_attempt_id IS NOT NULL AND work_scope_id IS NOT NULL AND link_event_id IS NOT NULL AND candidate_count=1) OR (state<>'associated' AND evidence_level='descriptive'))
);

CREATE TABLE portfolio_run_outcome_association_cursors (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
    as_of TEXT NOT NULL,
    snapshot_sequence INTEGER NOT NULL CHECK (snapshot_sequence >= 0),
    after_at TEXT NOT NULL,
    after_id TEXT NOT NULL CHECK (length(after_id) = 36),
    filters_digest TEXT NOT NULL CHECK (length(filters_digest) = 64),
    PRIMARY KEY (organization_id, cursor_id)
);

CREATE INDEX portfolio_run_work_link_attempt
    ON portfolio_run_work_link_events (organization_id, request_attempt_id, sequence);
CREATE INDEX portfolio_run_work_link_outcome
    ON portfolio_run_work_link_events (organization_id, connector_id, source_event_id, sequence);
CREATE UNIQUE INDEX portfolio_run_work_link_root
    ON portfolio_run_work_link_events (organization_id, request_attempt_id, connector_id, source_event_id)
    WHERE supersedes_link_event_id IS NULL;
CREATE UNIQUE INDEX portfolio_run_work_link_lineage
    ON portfolio_run_work_link_events (organization_id, supersedes_link_event_id)
    WHERE supersedes_link_event_id IS NOT NULL;
CREATE INDEX portfolio_run_outcome_association_window
    ON portfolio_run_outcome_association_events (organization_id, event_at, association_event_id, sequence);
CREATE INDEX portfolio_run_outcome_association_scope
    ON portfolio_run_outcome_association_events (organization_id, work_scope_id, work_scope_version, sequence);
CREATE UNIQUE INDEX portfolio_run_outcome_association_root
    ON portfolio_run_outcome_association_events (organization_id, connector_id, source_event_id, rule_id, rule_version, window_id)
    WHERE supersedes_event_id IS NULL;
CREATE UNIQUE INDEX portfolio_run_outcome_association_lineage
    ON portfolio_run_outcome_association_events (organization_id, supersedes_event_id)
    WHERE supersedes_event_id IS NOT NULL;

CREATE TRIGGER portfolio_association_audit_events_no_update
BEFORE UPDATE ON portfolio_association_audit_events
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_association_audit_events_no_delete
BEFORE DELETE ON portfolio_association_audit_events
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_work_link_events_no_update
BEFORE UPDATE ON portfolio_run_work_link_events
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_work_link_events_no_delete
BEFORE DELETE ON portfolio_run_work_link_events
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_work_link_idempotency_no_update
BEFORE UPDATE ON portfolio_run_work_link_idempotency
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_work_link_idempotency_no_delete
BEFORE DELETE ON portfolio_run_work_link_idempotency
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_outcome_association_events_no_update
BEFORE UPDATE ON portfolio_run_outcome_association_events
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_outcome_association_events_no_delete
BEFORE DELETE ON portfolio_run_outcome_association_events
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_outcome_association_cursors_no_update
BEFORE UPDATE ON portfolio_run_outcome_association_cursors
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
CREATE TRIGGER portfolio_run_outcome_association_cursors_no_delete
BEFORE DELETE ON portfolio_run_outcome_association_cursors
BEGIN SELECT RAISE(ABORT, 'association_metadata_append_only'); END;
