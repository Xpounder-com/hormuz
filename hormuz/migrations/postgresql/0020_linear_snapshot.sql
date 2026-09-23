-- Authenticated Linear reconciliation snapshots and separate provenance.
CREATE TABLE {schema}.gateway_linear_snapshot_receipts (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        receipt_id TEXT NOT NULL CHECK (length(receipt_id) = 36),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        reconciliation_id TEXT NOT NULL CHECK (length(reconciliation_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        page_id TEXT NOT NULL CHECK (length(page_id) = 36),
        page_number INTEGER NOT NULL CHECK (page_number BETWEEN 1 AND 100),
        page_count INTEGER NOT NULL CHECK (page_count BETWEEN 1 AND 100),
        credential_version TEXT NOT NULL CHECK (length(credential_version) BETWEEN 1 AND 128),
        body_fingerprint TEXT NOT NULL CHECK (length(body_fingerprint) = 64),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        received_at TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        committed_at TEXT NOT NULL,
        accepted_context_count INTEGER NOT NULL CHECK (accepted_context_count BETWEEN 0 AND 100),
        response_digest TEXT NOT NULL CHECK (length(response_digest) = 64),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
        PRIMARY KEY (organization_id, connector_id, receipt_id),
        UNIQUE (organization_id, connector_id, page_id),
        UNIQUE (organization_id, connector_id, snapshot_id, page_number),
        UNIQUE (organization_id, connector_id, fingerprint_key_version, body_fingerprint),
        FOREIGN KEY (organization_id, connector_id, binding_version)
            REFERENCES {schema}.portfolio_linear_source_binding_versions
                (organization_id, connector_id, version),
        CHECK (page_number <= page_count)
    );
CREATE TABLE {schema}.portfolio_linear_snapshot_context_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        connector_id TEXT NOT NULL CHECK (length(connector_id) BETWEEN 1 AND 128),
        context_event_id TEXT NOT NULL CHECK (length(context_event_id) = 36),
        snapshot_receipt_id TEXT NOT NULL CHECK (length(snapshot_receipt_id) = 36),
        object_kind TEXT NOT NULL CHECK (object_kind IN ('initiative','project','cycle','issue')),
        object_id TEXT NOT NULL CHECK (length(object_id) = 36),
        lifecycle TEXT NOT NULL CHECK (lifecycle IN ('updated','archived')),
        normalized_state TEXT NOT NULL CHECK (normalized_state IN ('not_started','in_progress','completed','canceled','unknown')),
        relationship_coverage TEXT NOT NULL CHECK (relationship_coverage IN ('complete','not_applicable')),
        revision_kind TEXT NOT NULL CHECK (revision_kind = 'source_updated_at_v1'),
        revision_value TEXT NOT NULL CHECK (length(revision_value) BETWEEN 1 AND 64),
        ordering_state TEXT NOT NULL CHECK (ordering_state IN ('current','late','incomparable')),
        scope_state TEXT NOT NULL CHECK (scope_state IN ('matched','unmatched','excluded')),
        event_at TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        ingested_at TEXT NOT NULL,
        source_fact_fingerprint TEXT NOT NULL CHECK (length(source_fact_fingerprint) = 64),
        source_fact_key_version INTEGER NOT NULL CHECK (source_fact_key_version BETWEEN 1 AND 2147483647),
        provenance_digest TEXT NOT NULL CHECK (length(provenance_digest) = 64),
        commit_sequence BIGINT NOT NULL CHECK (commit_sequence >= 1),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
        PRIMARY KEY (organization_id, connector_id, context_event_id),
        UNIQUE (organization_id, commit_sequence),
        UNIQUE (organization_id, connector_id, source_fact_key_version, source_fact_fingerprint),
        FOREIGN KEY (organization_id, connector_id, snapshot_receipt_id)
            REFERENCES {schema}.gateway_linear_snapshot_receipts
                (organization_id, connector_id, receipt_id)
    );
CREATE INDEX gateway_linear_snapshot_receipt_time ON {schema}.gateway_linear_snapshot_receipts (organization_id, connector_id, committed_at, receipt_id);
CREATE INDEX portfolio_linear_snapshot_context_object_time ON {schema}.portfolio_linear_snapshot_context_events (organization_id, connector_id, object_kind, object_id, commit_sequence DESC);
ALTER TABLE {schema}.gateway_linear_snapshot_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_linear_snapshot_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY gateway_linear_snapshot_receipts_tenant ON {schema}.gateway_linear_snapshot_receipts
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER gateway_linear_snapshot_receipts_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_linear_snapshot_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.gateway_linear_snapshot_receipts FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.gateway_linear_snapshot_receipts TO {runtime_role};

CREATE FUNCTION {schema}.enforce_linear_snapshot_page_set()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'portfolio:' || TG_TABLE_SCHEMA || ':' || NEW.organization_id,
        0
    ));
    PERFORM pg_advisory_xact_lock(hashtextextended(
        NEW.organization_id || ':' || NEW.connector_id || ':' || NEW.snapshot_id,
        0
    ));
    IF EXISTS (
        SELECT 1 FROM {schema}.gateway_linear_snapshot_receipts existing
        WHERE existing.organization_id=NEW.organization_id
          AND existing.connector_id=NEW.connector_id
          AND existing.snapshot_id=NEW.snapshot_id
          AND (
              existing.binding_version IS DISTINCT FROM NEW.binding_version
              OR existing.reconciliation_id IS DISTINCT FROM NEW.reconciliation_id
              OR existing.page_count IS DISTINCT FROM NEW.page_count
              OR existing.captured_at IS DISTINCT FROM NEW.captured_at
          )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='linear_snapshot_page_set_conflict';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION {schema}.enforce_linear_snapshot_page_set() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.enforce_linear_snapshot_page_set() TO {runtime_role};
CREATE TRIGGER gateway_linear_snapshot_page_set_consistent
    BEFORE INSERT ON {schema}.gateway_linear_snapshot_receipts
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_linear_snapshot_page_set();

ALTER TABLE {schema}.portfolio_linear_snapshot_context_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_linear_snapshot_context_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_linear_snapshot_context_events_tenant ON {schema}.portfolio_linear_snapshot_context_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_linear_snapshot_context_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_linear_snapshot_context_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_linear_snapshot_context_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_linear_snapshot_context_events TO {runtime_role};

CREATE OR REPLACE FUNCTION {schema}.enforce_linear_context_cross_capture()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(
        'portfolio:' || TG_TABLE_SCHEMA || ':' || NEW.organization_id,
        0
    ));
    IF EXISTS (
        SELECT 1 FROM {schema}.portfolio_linear_context_events other
        WHERE other.organization_id=NEW.organization_id
          AND (
              other.context_event_id=NEW.context_event_id
              OR other.commit_sequence=NEW.commit_sequence
          )
    ) OR EXISTS (
        SELECT 1 FROM {schema}.portfolio_linear_snapshot_context_events other
        WHERE other.organization_id=NEW.organization_id
          AND (
              other.context_event_id=NEW.context_event_id
              OR other.commit_sequence=NEW.commit_sequence
          )
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23505', MESSAGE='linear_context_cross_capture_conflict';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION {schema}.enforce_linear_context_cross_capture() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.enforce_linear_context_cross_capture() TO {runtime_role};
CREATE TRIGGER portfolio_linear_context_webhook_cross_capture
    BEFORE INSERT ON {schema}.portfolio_linear_context_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_linear_context_cross_capture();
CREATE TRIGGER portfolio_linear_context_snapshot_cross_capture
    BEFORE INSERT ON {schema}.portfolio_linear_snapshot_context_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_linear_context_cross_capture();

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
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_linear_context_retention_events
        WHERE organization_id=p_organization_id AND retention_event_id=p_source_event_id;
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
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_linear_context_retention_events
        WHERE organization_id=NEW.organization_id AND retention_event_id=NEW.source_event_id;
    END IF;
    IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='audit source evidence mismatch';
    END IF;
    RETURN NEW;
END;
$$;
