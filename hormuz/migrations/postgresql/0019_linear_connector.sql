-- Linear metadata runtime, source binding, receipts, context, and retention.

CREATE TABLE {schema}.portfolio_linear_source_binding_versions (
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

CREATE TABLE {schema}.gateway_linear_delivery_receipts (
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
        REFERENCES {schema}.portfolio_linear_source_binding_versions
            (organization_id, connector_id, version)
);

CREATE TABLE {schema}.portfolio_linear_context_events (
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
    commit_sequence BIGINT NOT NULL CHECK (commit_sequence >= 1),
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 1048576),
    PRIMARY KEY (organization_id, connector_id, context_event_id),
    UNIQUE (organization_id, commit_sequence),
    FOREIGN KEY (organization_id, connector_id, receipt_id)
        REFERENCES {schema}.gateway_linear_delivery_receipts
            (organization_id, connector_id, receipt_id)
);

CREATE TABLE {schema}.portfolio_linear_context_retention_events (
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
        REFERENCES {schema}.portfolio_linear_context_events
            (organization_id, connector_id, context_event_id)
);

-- This private table is deliberately outside tenant RLS. The restricted
-- runtime role has no privileges on it; only the SECURITY DEFINER binding
-- trigger can atomically claim a provider workspace or webhook across tenants.
CREATE TABLE {schema}.gateway_linear_route_claims (
    claim_kind TEXT NOT NULL CHECK (claim_kind IN ('workspace','webhook')),
    source_identifier TEXT NOT NULL CHECK (length(source_identifier) = 36),
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    connector_id TEXT CHECK (connector_id IS NULL OR length(connector_id) BETWEEN 1 AND 128),
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (claim_kind, source_identifier),
    CHECK (
        (claim_kind='workspace' AND connector_id IS NULL)
        OR (claim_kind='webhook' AND connector_id IS NOT NULL)
    )
);
CREATE TRIGGER gateway_linear_route_claims_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_linear_route_claims
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.gateway_linear_route_claims FROM PUBLIC;

CREATE INDEX portfolio_linear_source_binding_current
    ON {schema}.portfolio_linear_source_binding_versions (organization_id, connector_id, version DESC);
CREATE INDEX gateway_linear_delivery_receipt_time
    ON {schema}.gateway_linear_delivery_receipts (organization_id, connector_id, committed_at, receipt_id);
CREATE INDEX portfolio_linear_context_object_time
    ON {schema}.portfolio_linear_context_events (organization_id, connector_id, object_kind, object_id, commit_sequence DESC);
CREATE INDEX portfolio_linear_context_retention_target
    ON {schema}.portfolio_linear_context_retention_events (organization_id, connector_id, target_context_event_id, ingested_at);

CREATE FUNCTION {schema}.enforce_linear_binding_cardinality()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
DECLARE
    v_organization_id TEXT;
    v_connector_id TEXT;
BEGIN
    INSERT INTO {schema}.gateway_linear_route_claims (
        claim_kind, source_identifier, organization_id, connector_id, claimed_at
    ) VALUES (
        'workspace', NEW.source_workspace_id, NEW.organization_id, NULL,
        NEW.registered_at
    ) ON CONFLICT (claim_kind, source_identifier) DO NOTHING;
    SELECT organization_id, connector_id
      INTO v_organization_id, v_connector_id
      FROM {schema}.gateway_linear_route_claims
     WHERE claim_kind='workspace'
       AND source_identifier=NEW.source_workspace_id;
    IF v_organization_id IS DISTINCT FROM NEW.organization_id
       OR v_connector_id IS NOT NULL THEN
        RAISE EXCEPTION 'linear_binding_cardinality_conflict'
            USING ERRCODE = '23514';
    END IF;

    v_organization_id := NULL;
    v_connector_id := NULL;
    INSERT INTO {schema}.gateway_linear_route_claims (
        claim_kind, source_identifier, organization_id, connector_id, claimed_at
    ) VALUES (
        'webhook', NEW.source_webhook_id, NEW.organization_id, NEW.connector_id,
        NEW.registered_at
    ) ON CONFLICT (claim_kind, source_identifier) DO NOTHING;
    SELECT organization_id, connector_id
      INTO v_organization_id, v_connector_id
      FROM {schema}.gateway_linear_route_claims
     WHERE claim_kind='webhook'
       AND source_identifier=NEW.source_webhook_id;
    IF v_organization_id IS DISTINCT FROM NEW.organization_id
       OR v_connector_id IS DISTINCT FROM NEW.connector_id THEN
        RAISE EXCEPTION 'linear_binding_cardinality_conflict'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION {schema}.enforce_linear_binding_cardinality() FROM PUBLIC;
CREATE TRIGGER portfolio_linear_binding_cardinality
    BEFORE INSERT ON {schema}.portfolio_linear_source_binding_versions
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_linear_binding_cardinality();

ALTER TABLE {schema}.portfolio_linear_source_binding_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_linear_source_binding_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_linear_source_binding_versions_tenant ON {schema}.portfolio_linear_source_binding_versions
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_linear_source_binding_versions_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_linear_source_binding_versions
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_linear_source_binding_versions FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_linear_source_binding_versions TO {runtime_role};

ALTER TABLE {schema}.gateway_linear_delivery_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_linear_delivery_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY gateway_linear_delivery_receipts_tenant ON {schema}.gateway_linear_delivery_receipts
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER gateway_linear_delivery_receipts_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_linear_delivery_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.gateway_linear_delivery_receipts FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.gateway_linear_delivery_receipts TO {runtime_role};

ALTER TABLE {schema}.portfolio_linear_context_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_linear_context_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_linear_context_events_tenant ON {schema}.portfolio_linear_context_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_linear_context_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_linear_context_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_linear_context_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_linear_context_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_linear_context_retention_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_linear_context_retention_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_linear_context_retention_events_tenant ON {schema}.portfolio_linear_context_retention_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_linear_context_retention_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_linear_context_retention_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_linear_context_retention_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_linear_context_retention_events TO {runtime_role};

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
    ELSIF p_source_schema_id='hormuz.linear-context-event' AND p_source_schema_version=1 THEN
        SELECT evidence_json INTO v_event_json FROM {schema}.portfolio_linear_context_events
        WHERE organization_id=p_organization_id AND context_event_id=p_source_event_id;
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
    ELSIF NEW.source_schema_id='hormuz.linear-context-event' AND NEW.source_schema_version=1 THEN
        SELECT evidence_json INTO v_source_json FROM {schema}.portfolio_linear_context_events
        WHERE organization_id=NEW.organization_id AND context_event_id=NEW.source_event_id;
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
