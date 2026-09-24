-- SQLite 16 / PostgreSQL 21 run-to-outcome association runtime.

CREATE TABLE {schema}.portfolio_association_audit_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        actor_id TEXT,
        operation TEXT NOT NULL CHECK (operation IN ('link','correct','tombstone','evaluate','list_links','list_associations','read_metrics')),
        entity_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','corrected','tombstoned','associated','unmatched','ambiguous','excluded','observed')),
        occurred_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    );

CREATE TABLE {schema}.portfolio_run_work_link_events (
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
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, link_event_id),
        UNIQUE (organization_id, request_attempt_id, connector_id, source_event_id, supersedes_link_event_id),
        FOREIGN KEY (organization_id, request_attempt_id)
            REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, attribution_event_id)
            REFERENCES {schema}.portfolio_attribution_events (organization_id, attribution_event_id),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {schema}.portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, binding_event_id)
            REFERENCES {schema}.portfolio_binding_events (organization_id, binding_event_id),
        FOREIGN KEY (organization_id, supersedes_link_event_id)
            REFERENCES {schema}.portfolio_run_work_link_events (organization_id, link_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_association_audit_events (organization_id, sequence),
        CHECK (
            (state='active' AND reason_code IN ('explicit_link','corrected'))
            OR (state='tombstoned' AND reason_code='tombstoned')
        )
    );

CREATE TABLE {schema}.portfolio_run_work_link_idempotency (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
        request_mac TEXT NOT NULL CHECK (length(request_mac) = 64),
        key_version INTEGER NOT NULL CHECK (key_version BETWEEN 1 AND 2147483647),
        link_event_id TEXT NOT NULL CHECK (length(link_event_id) = 36),
        PRIMARY KEY (organization_id, actor_id, idempotency_key),
        FOREIGN KEY (organization_id, link_event_id)
            REFERENCES {schema}.portfolio_run_work_link_events (organization_id, link_event_id)
    );

CREATE TABLE {schema}.portfolio_run_outcome_association_events (
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
        rule_digest TEXT NOT NULL CHECK (length(rule_digest) = 64),
        window_id TEXT NOT NULL CHECK (length(window_id) = 64),
        window_start_at TEXT NOT NULL,
        window_end_at TEXT NOT NULL,
        evaluation_as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        candidate_count INTEGER NOT NULL CHECK (candidate_count BETWEEN 0 AND 2147483647),
        state TEXT NOT NULL CHECK (state IN ('unmatched','ambiguous','associated','excluded')),
        evidence_level TEXT NOT NULL CHECK (evidence_level IN ('descriptive','associated')),
        link_event_id TEXT,
        supersedes_event_id TEXT,
        reason_code TEXT NOT NULL CHECK (reason_code IN ('explicit_link','missing_evidence','multiple_eligible_links','source_conflict','source_excluded','source_superseded','link_tombstoned','unsupported')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        sequence BIGINT NOT NULL CHECK (sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, association_event_id),
        UNIQUE (organization_id, sequence),
        FOREIGN KEY (organization_id, connector_id, source_event_id)
            REFERENCES {schema}.portfolio_outcome_events (organization_id, connector_id, source_event_id),
        FOREIGN KEY (organization_id, request_attempt_id)
            REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {schema}.portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, link_event_id)
            REFERENCES {schema}.portfolio_run_work_link_events (organization_id, link_event_id),
        FOREIGN KEY (organization_id, supersedes_event_id)
            REFERENCES {schema}.portfolio_run_outcome_association_events (organization_id, association_event_id),
        FOREIGN KEY (organization_id, sequence)
            REFERENCES {schema}.portfolio_association_audit_events (organization_id, sequence),
        CHECK ((work_scope_id IS NULL AND work_scope_version IS NULL) OR (work_scope_id IS NOT NULL AND work_scope_version IS NOT NULL)),
        CHECK (window_start_at < window_end_at),
        CHECK ((state='associated' AND evidence_level='associated' AND request_attempt_id IS NOT NULL AND work_scope_id IS NOT NULL AND link_event_id IS NOT NULL AND candidate_count=1) OR (state<>'associated' AND evidence_level='descriptive'))
    );

CREATE TABLE {schema}.portfolio_run_outcome_association_cursors (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
        as_of TEXT NOT NULL,
        snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
        after_at TEXT NOT NULL,
        after_id TEXT NOT NULL CHECK (length(after_id) = 36),
        filters_digest TEXT NOT NULL CHECK (length(filters_digest) = 64),
        PRIMARY KEY (organization_id, cursor_id)
    );

CREATE INDEX portfolio_run_work_link_attempt ON {schema}.portfolio_run_work_link_events (organization_id, request_attempt_id, sequence);
CREATE INDEX portfolio_run_work_link_outcome ON {schema}.portfolio_run_work_link_events (organization_id, connector_id, source_event_id, sequence);
CREATE UNIQUE INDEX portfolio_run_work_link_root ON {schema}.portfolio_run_work_link_events (organization_id, request_attempt_id, connector_id, source_event_id) WHERE supersedes_link_event_id IS NULL;
CREATE UNIQUE INDEX portfolio_run_work_link_lineage ON {schema}.portfolio_run_work_link_events (organization_id, supersedes_link_event_id) WHERE supersedes_link_event_id IS NOT NULL;
CREATE INDEX portfolio_run_outcome_association_window ON {schema}.portfolio_run_outcome_association_events (organization_id, event_at, association_event_id, sequence);
CREATE INDEX portfolio_run_outcome_association_scope ON {schema}.portfolio_run_outcome_association_events (organization_id, work_scope_id, work_scope_version, sequence);
CREATE UNIQUE INDEX portfolio_run_outcome_association_root ON {schema}.portfolio_run_outcome_association_events (organization_id, connector_id, source_event_id, rule_id, rule_version, window_id) WHERE supersedes_event_id IS NULL;
CREATE UNIQUE INDEX portfolio_run_outcome_association_lineage ON {schema}.portfolio_run_outcome_association_events (organization_id, supersedes_event_id) WHERE supersedes_event_id IS NOT NULL;

ALTER TABLE {schema}.portfolio_association_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_association_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_association_audit_events_tenant ON {schema}.portfolio_association_audit_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_association_audit_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_association_audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_association_audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_association_audit_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_run_work_link_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_run_work_link_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_run_work_link_events_tenant ON {schema}.portfolio_run_work_link_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_run_work_link_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_run_work_link_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_run_work_link_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_run_work_link_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_run_work_link_idempotency ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_run_work_link_idempotency FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_run_work_link_idempotency_tenant ON {schema}.portfolio_run_work_link_idempotency
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_run_work_link_idempotency_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_run_work_link_idempotency
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_run_work_link_idempotency FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_run_work_link_idempotency TO {runtime_role};

ALTER TABLE {schema}.portfolio_run_outcome_association_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_run_outcome_association_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_run_outcome_association_events_tenant ON {schema}.portfolio_run_outcome_association_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_run_outcome_association_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_run_outcome_association_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_run_outcome_association_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_run_outcome_association_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_run_outcome_association_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_run_outcome_association_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_run_outcome_association_cursors_tenant ON {schema}.portfolio_run_outcome_association_cursors
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_run_outcome_association_cursors_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_run_outcome_association_cursors
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_run_outcome_association_cursors FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_run_outcome_association_cursors TO {runtime_role};

ALTER TABLE {schema}.gateway_audit_chain_entries
    DROP CONSTRAINT gateway_audit_chain_entries_source_identity_check;
ALTER TABLE {schema}.gateway_audit_chain_entries
    ADD CONSTRAINT gateway_audit_chain_entries_source_identity_check CHECK (
        (
            entry_schema_version=1 AND source_schema_id IS NULL
            AND source_schema_version IS NULL AND source_event_id IS NULL
        ) OR (
            entry_schema_version=2 AND source_event_id IS NOT NULL AND (
                (source_schema_id='hormuz.custody-control-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.custody-execution-attempt' AND source_schema_version=2)
                OR (source_schema_id='hormuz.custody-execution-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.custody-lifecycle-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.custody-envelope-attestation' AND source_schema_version=1)
                OR (source_schema_id='hormuz.custody-deletion-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-attempt-evidence' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-source-binding-version' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-collection-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-snapshot' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-account-binding-version' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-attempt-account-binding' AND source_schema_version=1)
                OR (source_schema_id='hormuz.finance-query-audit-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.linear-source-binding-version' AND source_schema_version=1)
                OR (source_schema_id='hormuz.linear-delivery-receipt' AND source_schema_version=1)
                OR (source_schema_id='hormuz.linear-context-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.linear-context-retention' AND source_schema_version=1)
                OR (source_schema_id='hormuz.linear-snapshot-receipt' AND source_schema_version=1)
                OR (source_schema_id='hormuz.run-work-link-event' AND source_schema_version=1)
                OR (source_schema_id='hormuz.run-outcome-association-event' AND source_schema_version=1)
            )
        )
    );

CREATE OR REPLACE FUNCTION {schema}.custody_audit_chain_source_event_json(
    p_organization_id TEXT,
    p_source_schema_id TEXT,
    p_source_schema_version INTEGER,
    p_source_event_id TEXT
)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_event_json TEXT;
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='custody audit source tenant context is invalid';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM {schema}.gateway_audit_chain_entries
        WHERE organization_id=p_organization_id
          AND entry_schema_version=2
          AND source_schema_id=p_source_schema_id
          AND source_schema_version=p_source_schema_version
          AND source_event_id=p_source_event_id
    ) THEN
        RETURN NULL;
    END IF;
    IF p_source_schema_id='hormuz.custody-control-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.custody_control_events
        WHERE organization_id=p_organization_id AND event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.custody-execution-attempt' AND p_source_schema_version=2 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.custody_execution_attempts
        WHERE organization_id=p_organization_id AND execution_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.custody-execution-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.custody_execution_events
        WHERE organization_id=p_organization_id
          AND execution_id || ':' || sequence::TEXT=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.custody-lifecycle-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.custody_lifecycle_events
        WHERE organization_id=p_organization_id AND lifecycle_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.custody-envelope-attestation' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.custody_envelope_attestations
        WHERE organization_id=p_organization_id
          AND execution_id || ':' || attestation_kind=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.custody-deletion-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.custody_deletion_events
        WHERE organization_id=p_organization_id AND deletion_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-attempt-evidence' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.gateway_finance_attempt_evidence
        WHERE organization_id=p_organization_id AND evidence_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-source-binding-version' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_finance_source_binding_versions
        WHERE organization_id=p_organization_id AND binding_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-collection-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_finance_collection_events
        WHERE organization_id=p_organization_id AND event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-snapshot' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_finance_snapshots
        WHERE organization_id=p_organization_id AND snapshot_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-account-binding-version' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_finance_account_binding_versions
        WHERE organization_id=p_organization_id AND binding_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-attempt-account-binding' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.gateway_finance_attempt_account_bindings
        WHERE organization_id=p_organization_id AND event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.finance-query-audit-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_finance_query_audit_events
        WHERE organization_id=p_organization_id AND query_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.linear-source-binding-version' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_linear_source_binding_versions
        WHERE organization_id=p_organization_id AND binding_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.linear-delivery-receipt' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.gateway_linear_delivery_receipts
        WHERE organization_id=p_organization_id AND receipt_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.linear-snapshot-receipt' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.gateway_linear_snapshot_receipts
        WHERE organization_id=p_organization_id AND receipt_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.linear-context-event' AND p_source_schema_version=1 THEN
        SELECT source.evidence_json INTO v_event_json FROM (
            SELECT evidence_json FROM {schema}.portfolio_linear_context_events
            WHERE organization_id=p_organization_id AND context_event_id=p_source_event_id
            UNION ALL
            SELECT evidence_json FROM {schema}.portfolio_linear_snapshot_context_events
            WHERE organization_id=p_organization_id AND context_event_id=p_source_event_id
        ) source;
    ELSIF p_source_schema_id='hormuz.linear-context-retention' AND p_source_schema_version=1 THEN
        SELECT source.evidence_json INTO v_event_json FROM (
            SELECT evidence_json FROM {schema}.portfolio_linear_context_retention_events
            WHERE organization_id=p_organization_id AND retention_event_id=p_source_event_id
            UNION ALL
            SELECT evidence_json FROM {schema}.portfolio_linear_snapshot_context_retention_events
            WHERE organization_id=p_organization_id AND retention_event_id=p_source_event_id
        ) source;
    ELSIF p_source_schema_id='hormuz.run-work-link-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_run_work_link_events
        WHERE organization_id=p_organization_id AND link_event_id=p_source_event_id;
    ELSIF p_source_schema_id='hormuz.run-outcome-association-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_run_outcome_association_events
        WHERE organization_id=p_organization_id AND association_event_id=p_source_event_id;
    ELSE
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='custody audit source schema is unsupported';
    END IF;
    RETURN v_event_json;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_audit_chain_entry_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_source_json TEXT;
BEGIN
    IF NEW.entry_schema_version=1 THEN
        RETURN NEW;
    END IF;
    IF NEW.entry_schema_version<>2 OR NEW.event_id IS DISTINCT FROM NEW.source_event_id THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='audit chain entry is invalid';
    END IF;
    IF NOT (
        (NEW.source_schema_id='hormuz.custody-control-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.custody-execution-attempt' AND NEW.source_schema_version=2)
        OR (NEW.source_schema_id='hormuz.custody-execution-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.custody-lifecycle-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.custody-envelope-attestation' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.custody-deletion-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-attempt-evidence' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-source-binding-version' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-collection-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-snapshot' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-account-binding-version' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-attempt-account-binding' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.finance-query-audit-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.linear-source-binding-version' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.linear-delivery-receipt' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.linear-snapshot-receipt' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.linear-context-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.linear-context-retention' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.run-work-link-event' AND NEW.source_schema_version=1)
        OR (NEW.source_schema_id='hormuz.run-outcome-association-event' AND NEW.source_schema_version=1)
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='audit source schema is unsupported';
    END IF;
    IF NEW.source_schema_id='hormuz.custody-control-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.custody_control_events
        WHERE organization_id=NEW.organization_id AND event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.custody-execution-attempt' AND NEW.source_schema_version=2 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.custody_execution_attempts
        WHERE organization_id=NEW.organization_id AND execution_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.custody-execution-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.custody_execution_events
        WHERE organization_id=NEW.organization_id
          AND execution_id || ':' || sequence::TEXT=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.custody-lifecycle-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.custody_lifecycle_events
        WHERE organization_id=NEW.organization_id AND lifecycle_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.custody-envelope-attestation' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.custody_envelope_attestations
        WHERE organization_id=NEW.organization_id
          AND execution_id || ':' || attestation_kind=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.custody-deletion-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.custody_deletion_events
        WHERE organization_id=NEW.organization_id AND deletion_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-attempt-evidence' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.gateway_finance_attempt_evidence
        WHERE organization_id=NEW.organization_id AND evidence_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-source-binding-version' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_finance_source_binding_versions
        WHERE organization_id=NEW.organization_id AND binding_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-collection-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_finance_collection_events
        WHERE organization_id=NEW.organization_id AND event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-snapshot' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_finance_snapshots
        WHERE organization_id=NEW.organization_id AND snapshot_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-account-binding-version' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_finance_account_binding_versions
        WHERE organization_id=NEW.organization_id AND binding_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-attempt-account-binding' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.gateway_finance_attempt_account_bindings
        WHERE organization_id=NEW.organization_id AND event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.finance-query-audit-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_finance_query_audit_events
        WHERE organization_id=NEW.organization_id AND query_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.linear-source-binding-version' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_linear_source_binding_versions
        WHERE organization_id=NEW.organization_id AND binding_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.linear-delivery-receipt' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.gateway_linear_delivery_receipts
        WHERE organization_id=NEW.organization_id AND receipt_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.linear-snapshot-receipt' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.gateway_linear_snapshot_receipts
        WHERE organization_id=NEW.organization_id AND receipt_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.linear-context-event' AND NEW.source_schema_version=1 THEN
        SELECT source.evidence_json INTO v_source_json FROM (
            SELECT evidence_json FROM {schema}.portfolio_linear_context_events
            WHERE organization_id=NEW.organization_id AND context_event_id=NEW.source_event_id
            UNION ALL
            SELECT evidence_json FROM {schema}.portfolio_linear_snapshot_context_events
            WHERE organization_id=NEW.organization_id AND context_event_id=NEW.source_event_id
        ) source;
    ELSIF NEW.source_schema_id='hormuz.linear-context-retention' AND NEW.source_schema_version=1 THEN
        SELECT source.evidence_json INTO v_source_json FROM (
            SELECT evidence_json FROM {schema}.portfolio_linear_context_retention_events
            WHERE organization_id=NEW.organization_id AND retention_event_id=NEW.source_event_id
            UNION ALL
            SELECT evidence_json FROM {schema}.portfolio_linear_snapshot_context_retention_events
            WHERE organization_id=NEW.organization_id AND retention_event_id=NEW.source_event_id
        ) source;
    ELSIF NEW.source_schema_id='hormuz.run-work-link-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_run_work_link_events
        WHERE organization_id=NEW.organization_id AND link_event_id=NEW.source_event_id;
    ELSIF NEW.source_schema_id='hormuz.run-outcome-association-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_run_outcome_association_events
        WHERE organization_id=NEW.organization_id AND association_event_id=NEW.source_event_id;
    END IF;
    IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='audit source evidence mismatch';
    END IF;
    RETURN NEW;
END;
$$;
