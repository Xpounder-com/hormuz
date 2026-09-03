-- Provider aggregate collection evidence.  All durable rows are append-only;
-- raw provider payloads, cursors, credentials, and free-form text are excluded.

CREATE TABLE {schema}.portfolio_finance_source_binding_versions (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) = 36),
        provider TEXT NOT NULL CHECK (provider IN ('openai','anthropic')),
        provider_account_fingerprint TEXT NOT NULL CHECK (length(provider_account_fingerprint) = 64),
        scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization','projects','workspaces')),
        scope_fingerprints_json TEXT NOT NULL CHECK (length(scope_fingerprints_json) BETWEEN 2 AND 65536),
        credential_reference_id TEXT NOT NULL CHECK (length(credential_reference_id) BETWEEN 1 AND 128),
        credential_reference_version INTEGER NOT NULL CHECK (credential_reference_version BETWEEN 1 AND 2147483647),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        binding_state TEXT NOT NULL CHECK (binding_state IN ('active','revoked')),
        previous_version INTEGER,
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        bound_by TEXT NOT NULL CHECK (length(bound_by) BETWEEN 1 AND 128),
        bound_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, binding_id, version),
        UNIQUE (organization_id, binding_event_id),
        UNIQUE (organization_id, binding_id, content_digest),
        CHECK (
            (version = 1 AND previous_version IS NULL)
            OR (version > 1 AND previous_version = version - 1)
        )
    );

CREATE TABLE {schema}.portfolio_finance_collection_attempts (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) = 36),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        provider TEXT NOT NULL CHECK (provider IN ('openai','anthropic')),
        collection_profile TEXT NOT NULL CHECK (length(collection_profile) BETWEEN 1 AND 128),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('usage','cost')),
        query_start_at TEXT NOT NULL,
        query_end_at TEXT NOT NULL,
        bucket_width TEXT NOT NULL CHECK (bucket_width IN ('1m','1h','1d')),
        requested_page_size INTEGER NOT NULL CHECK (requested_page_size BETWEEN 1 AND 1440),
        evidence_origin TEXT NOT NULL CHECK (evidence_origin IN ('authenticated_api','customer_file')),
        idempotency_digest TEXT NOT NULL CHECK (length(idempotency_digest) = 64),
        request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
        credential_reference_id TEXT NOT NULL CHECK (length(credential_reference_id) BETWEEN 1 AND 128),
        credential_reference_version INTEGER NOT NULL CHECK (credential_reference_version BETWEEN 1 AND 2147483647),
        fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
        prepared_by TEXT NOT NULL CHECK (length(prepared_by) BETWEEN 1 AND 128),
        prepared_at TEXT NOT NULL,
        PRIMARY KEY (organization_id, attempt_id),
        UNIQUE (
            organization_id, binding_id, binding_version, collection_profile,
            query_start_at, query_end_at, idempotency_digest
        ),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {schema}.portfolio_finance_source_binding_versions
                (organization_id, binding_id, version)
    );

CREATE TABLE {schema}.portfolio_finance_collection_events (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 36),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) = 36),
        state TEXT NOT NULL CHECK (state IN ('succeeded','failed','abandoned')),
        reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 128),
        receipt_id TEXT,
        snapshot_id TEXT,
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        occurred_at TEXT NOT NULL,
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, attempt_id),
        UNIQUE (organization_id, receipt_id),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {schema}.portfolio_finance_collection_attempts
                (organization_id, attempt_id),
        CHECK (
            (state='succeeded' AND reason_code='completed' AND receipt_id IS NOT NULL AND length(receipt_id)=32 AND snapshot_id IS NOT NULL AND length(snapshot_id)=36)
            OR (state IN ('failed','abandoned') AND receipt_id IS NULL AND snapshot_id IS NULL)
        )
    );

CREATE TABLE {schema}.portfolio_finance_snapshots (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        attempt_id TEXT NOT NULL CHECK (length(attempt_id) = 36),
        binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
        binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
        collection_profile TEXT NOT NULL CHECK (length(collection_profile) BETWEEN 1 AND 128),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('usage','cost')),
        query_start_at TEXT NOT NULL,
        query_end_at TEXT NOT NULL,
        evidence_origin TEXT NOT NULL CHECK (evidence_origin IN ('authenticated_api','customer_file')),
        scope_provenance TEXT NOT NULL CHECK (scope_provenance IN ('authenticated_query_scope_unverified','customer_supplied_scope_unverified')),
        parser_version INTEGER NOT NULL CHECK (parser_version = 1),
        page_count INTEGER NOT NULL CHECK (page_count BETWEEN 1 AND 32),
        record_count INTEGER NOT NULL CHECK (record_count BETWEEN 0 AND 4096),
        requested_page_size INTEGER NOT NULL CHECK (requested_page_size BETWEEN 1 AND 1440),
        page_chain_digest TEXT NOT NULL CHECK (length(page_chain_digest) = 64),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        supersedes_snapshot_id TEXT,
        commit_sequence BIGINT NOT NULL CHECK (commit_sequence >= 1),
        published_by TEXT NOT NULL CHECK (length(published_by) BETWEEN 1 AND 128),
        published_at TEXT NOT NULL,
        provider_final INTEGER NOT NULL CHECK (provider_final = 0),
        invoice_final INTEGER NOT NULL CHECK (invoice_final = 0),
        evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
        PRIMARY KEY (organization_id, snapshot_id),
        UNIQUE (organization_id, attempt_id),
        UNIQUE (organization_id, commit_sequence),
        FOREIGN KEY (organization_id, attempt_id)
            REFERENCES {schema}.portfolio_finance_collection_attempts
                (organization_id, attempt_id),
        FOREIGN KEY (organization_id, binding_id, binding_version)
            REFERENCES {schema}.portfolio_finance_source_binding_versions
                (organization_id, binding_id, version),
        FOREIGN KEY (organization_id, supersedes_snapshot_id)
            REFERENCES {schema}.portfolio_finance_snapshots
                (organization_id, snapshot_id)
    );

CREATE TABLE {schema}.portfolio_finance_snapshot_bucket_coverage (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        coverage_id TEXT NOT NULL CHECK (length(coverage_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        bucket_start_at TEXT NOT NULL,
        bucket_end_at TEXT NOT NULL,
        coverage_state TEXT NOT NULL CHECK (coverage_state IN ('observed','no_observation')),
        observation_count INTEGER NOT NULL CHECK (observation_count BETWEEN 0 AND 4096),
        PRIMARY KEY (organization_id, coverage_id),
        UNIQUE (organization_id, snapshot_id, bucket_start_at, bucket_end_at),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {schema}.portfolio_finance_snapshots
                (organization_id, snapshot_id),
        CHECK (
            (coverage_state='no_observation' AND observation_count=0)
            OR (coverage_state='observed' AND observation_count>=1)
        )
    );

CREATE TABLE {schema}.portfolio_finance_usage_observations (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        observation_id TEXT NOT NULL CHECK (length(observation_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        bucket_start_at TEXT NOT NULL,
        bucket_end_at TEXT NOT NULL,
        observation_digest TEXT NOT NULL CHECK (length(observation_digest) = 64),
        provider_project_fingerprint TEXT,
        provider_workspace_fingerprint TEXT,
        api_key_fingerprint TEXT,
        model TEXT,
        batch INTEGER CHECK (batch IS NULL OR batch IN (0,1)),
        service_tier TEXT,
        context_window TEXT,
        inference_geo TEXT,
        input_tokens BIGINT CHECK (input_tokens IS NULL OR input_tokens >= 0),
        output_tokens BIGINT CHECK (output_tokens IS NULL OR output_tokens >= 0),
        num_model_requests BIGINT CHECK (num_model_requests IS NULL OR num_model_requests >= 0),
        input_cached_tokens BIGINT CHECK (input_cached_tokens IS NULL OR input_cached_tokens >= 0),
        input_cache_write_tokens BIGINT CHECK (input_cache_write_tokens IS NULL OR input_cache_write_tokens >= 0),
        input_uncached_tokens BIGINT CHECK (input_uncached_tokens IS NULL OR input_uncached_tokens >= 0),
        input_text_tokens BIGINT CHECK (input_text_tokens IS NULL OR input_text_tokens >= 0),
        input_image_tokens BIGINT CHECK (input_image_tokens IS NULL OR input_image_tokens >= 0),
        input_audio_tokens BIGINT CHECK (input_audio_tokens IS NULL OR input_audio_tokens >= 0),
        input_cached_text_tokens BIGINT CHECK (input_cached_text_tokens IS NULL OR input_cached_text_tokens >= 0),
        input_cached_image_tokens BIGINT CHECK (input_cached_image_tokens IS NULL OR input_cached_image_tokens >= 0),
        input_cached_audio_tokens BIGINT CHECK (input_cached_audio_tokens IS NULL OR input_cached_audio_tokens >= 0),
        output_text_tokens BIGINT CHECK (output_text_tokens IS NULL OR output_text_tokens >= 0),
        output_image_tokens BIGINT CHECK (output_image_tokens IS NULL OR output_image_tokens >= 0),
        output_audio_tokens BIGINT CHECK (output_audio_tokens IS NULL OR output_audio_tokens >= 0),
        uncached_input_tokens BIGINT CHECK (uncached_input_tokens IS NULL OR uncached_input_tokens >= 0),
        cache_read_input_tokens BIGINT CHECK (cache_read_input_tokens IS NULL OR cache_read_input_tokens >= 0),
        cache_creation_5m_input_tokens BIGINT CHECK (cache_creation_5m_input_tokens IS NULL OR cache_creation_5m_input_tokens >= 0),
        cache_creation_1h_input_tokens BIGINT CHECK (cache_creation_1h_input_tokens IS NULL OR cache_creation_1h_input_tokens >= 0),
        server_tool_web_search_requests BIGINT CHECK (server_tool_web_search_requests IS NULL OR server_tool_web_search_requests >= 0),
        usage_basis TEXT NOT NULL CHECK (usage_basis = 'provider_native_aggregate_observation'),
        provider_final INTEGER NOT NULL CHECK (provider_final = 0),
        PRIMARY KEY (organization_id, observation_id),
        UNIQUE (organization_id, snapshot_id, observation_digest),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {schema}.portfolio_finance_snapshots
                (organization_id, snapshot_id)
    );

CREATE TABLE {schema}.portfolio_finance_cost_observations (
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        observation_id TEXT NOT NULL CHECK (length(observation_id) = 36),
        snapshot_id TEXT NOT NULL CHECK (length(snapshot_id) = 36),
        bucket_start_at TEXT NOT NULL,
        bucket_end_at TEXT NOT NULL,
        observation_digest TEXT NOT NULL CHECK (length(observation_digest) = 64),
        provider_project_fingerprint TEXT,
        provider_workspace_fingerprint TEXT,
        api_key_fingerprint TEXT,
        free_text_classification TEXT NOT NULL CHECK (length(free_text_classification) BETWEEN 1 AND 128),
        free_text_fingerprint TEXT,
        model TEXT,
        cost_type TEXT,
        token_type TEXT,
        service_tier TEXT,
        context_window TEXT,
        inference_geo TEXT,
        native_amount TEXT NOT NULL CHECK (length(native_amount) BETWEEN 1 AND 128),
        canonical_amount TEXT NOT NULL CHECK (length(canonical_amount) BETWEEN 1 AND 128),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        native_quantity TEXT,
        quantity_unit TEXT,
        cost_basis TEXT NOT NULL CHECK (cost_basis = 'provider_reported_aggregate'),
        provider_final INTEGER NOT NULL CHECK (provider_final = 0),
        invoice_final INTEGER NOT NULL CHECK (invoice_final = 0),
        PRIMARY KEY (organization_id, observation_id),
        UNIQUE (organization_id, snapshot_id, observation_digest),
        FOREIGN KEY (organization_id, snapshot_id)
            REFERENCES {schema}.portfolio_finance_snapshots
                (organization_id, snapshot_id)
    );

CREATE INDEX portfolio_finance_binding_current ON {schema}.portfolio_finance_source_binding_versions (organization_id, binding_id, version DESC);
CREATE INDEX portfolio_finance_snapshot_current ON {schema}.portfolio_finance_snapshots (organization_id, binding_id, binding_version, collection_profile, commit_sequence DESC);
CREATE INDEX portfolio_finance_coverage_current ON {schema}.portfolio_finance_snapshot_bucket_coverage (organization_id, bucket_start_at, bucket_end_at, snapshot_id);
CREATE INDEX portfolio_finance_usage_bucket ON {schema}.portfolio_finance_usage_observations (organization_id, snapshot_id, bucket_start_at, bucket_end_at);
CREATE INDEX portfolio_finance_cost_bucket ON {schema}.portfolio_finance_cost_observations (organization_id, snapshot_id, bucket_start_at, bucket_end_at);

ALTER TABLE {schema}.portfolio_finance_source_binding_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_source_binding_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_source_binding_versions_tenant ON {schema}.portfolio_finance_source_binding_versions USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_source_binding_versions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_source_binding_versions FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_source_binding_versions FROM PUBLIC;
-- Runtime grants are intentionally withheld until the PostgreSQL collection
-- ACL successor is separately reviewed and accepted.  The current protected
-- boundary remains the literal 185-entry fingerprint; collection runtime is
-- therefore gated while these owner-controlled tables are provisioned.
-- Keep the owner ACL explicit so a dump/restore preserves the same durable
-- shape.  CURRENT_USER is the migration owner and is excluded from the
-- protected non-owner ACL fingerprint.
GRANT ALL ON {schema}.portfolio_finance_source_binding_versions TO CURRENT_USER;

ALTER TABLE {schema}.portfolio_finance_collection_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_collection_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_collection_attempts_tenant ON {schema}.portfolio_finance_collection_attempts USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_collection_attempts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_collection_attempts FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_collection_attempts FROM PUBLIC;
GRANT ALL ON {schema}.portfolio_finance_collection_attempts TO CURRENT_USER;

ALTER TABLE {schema}.portfolio_finance_collection_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_collection_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_collection_events_tenant ON {schema}.portfolio_finance_collection_events USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_collection_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_collection_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_collection_events FROM PUBLIC;
GRANT ALL ON {schema}.portfolio_finance_collection_events TO CURRENT_USER;

ALTER TABLE {schema}.portfolio_finance_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_snapshots_tenant ON {schema}.portfolio_finance_snapshots USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_snapshots_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_snapshots FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_snapshots FROM PUBLIC;
GRANT ALL ON {schema}.portfolio_finance_snapshots TO CURRENT_USER;

ALTER TABLE {schema}.portfolio_finance_snapshot_bucket_coverage ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_snapshot_bucket_coverage FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_snapshot_bucket_coverage_tenant ON {schema}.portfolio_finance_snapshot_bucket_coverage USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_snapshot_bucket_coverage_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_snapshot_bucket_coverage FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_snapshot_bucket_coverage FROM PUBLIC;
GRANT ALL ON {schema}.portfolio_finance_snapshot_bucket_coverage TO CURRENT_USER;

ALTER TABLE {schema}.portfolio_finance_usage_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_usage_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_usage_observations_tenant ON {schema}.portfolio_finance_usage_observations USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_usage_observations_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_usage_observations FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_usage_observations FROM PUBLIC;
GRANT ALL ON {schema}.portfolio_finance_usage_observations TO CURRENT_USER;

ALTER TABLE {schema}.portfolio_finance_cost_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_cost_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_cost_observations_tenant ON {schema}.portfolio_finance_cost_observations USING (organization_id = current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_cost_observations_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_cost_observations FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_cost_observations FROM PUBLIC;
GRANT ALL ON {schema}.portfolio_finance_cost_observations TO CURRENT_USER;

ALTER TABLE {schema}.gateway_audit_chain_entries
    DROP CONSTRAINT gateway_audit_chain_entries_source_identity_check;
ALTER TABLE {schema}.gateway_audit_chain_entries
    ADD CONSTRAINT gateway_audit_chain_entries_source_identity_check CHECK (
        (
            entry_schema_version = 1
            AND source_schema_id IS NULL
            AND source_schema_version IS NULL
            AND source_event_id IS NULL
        )
        OR (
            entry_schema_version = 2
            AND source_event_id IS NOT NULL
            AND (
                (source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2)
                OR (source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-attempt-evidence' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-source-binding-version' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-collection-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.finance-snapshot' AND source_schema_version = 1)
            )
        )
    );

-- Preserve the existing restricted-runtime audit path. Collection tables do
-- not receive runtime-role SELECT grants in this candidate; the established
-- SECURITY DEFINER source reader therefore gets the three new collection
-- source identities alongside the previously accepted finance-attempt one.
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
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody audit source tenant context is invalid';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM {schema}.gateway_audit_chain_entries
        WHERE organization_id = p_organization_id
          AND entry_schema_version = 2
          AND source_schema_id = p_source_schema_id
          AND source_schema_version = p_source_schema_version
          AND source_event_id = p_source_event_id
    ) THEN
        RETURN NULL;
    END IF;
    IF p_source_schema_id = 'hormuz.custody-control-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.custody_control_events
        WHERE organization_id = p_organization_id AND event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-execution-attempt' AND p_source_schema_version = 2 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.custody_execution_attempts
        WHERE organization_id = p_organization_id AND execution_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-execution-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.custody_execution_events
        WHERE organization_id = p_organization_id
          AND execution_id || ':' || sequence::TEXT = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-lifecycle-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.custody_lifecycle_events
        WHERE organization_id = p_organization_id AND lifecycle_event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-envelope-attestation' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.custody_envelope_attestations
        WHERE organization_id = p_organization_id
          AND execution_id || ':' || attestation_kind = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-deletion-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.custody_deletion_events
        WHERE organization_id = p_organization_id AND deletion_event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.finance-attempt-evidence' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.gateway_finance_attempt_evidence
        WHERE organization_id = p_organization_id AND evidence_event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.finance-source-binding-version' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.portfolio_finance_source_binding_versions
        WHERE organization_id = p_organization_id AND binding_event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.finance-collection-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.portfolio_finance_collection_events
        WHERE organization_id = p_organization_id AND event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.finance-snapshot' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_event_json
        FROM {schema}.portfolio_finance_snapshots
        WHERE organization_id = p_organization_id AND snapshot_id = p_source_event_id;
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source schema is unsupported';
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
    IF NEW.entry_schema_version = 1 THEN
        RETURN NEW;
    END IF;
    IF NEW.entry_schema_version <> 2
       OR NEW.event_id IS DISTINCT FROM NEW.source_event_id THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit chain entry is invalid';
    END IF;
    IF NOT (
        (NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2)
        OR (NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.finance-attempt-evidence' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.finance-source-binding-version' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.finance-collection-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.finance-snapshot' AND NEW.source_schema_version = 1)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit source schema is unsupported';
    END IF;
    IF NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_control_events
         WHERE organization_id = NEW.organization_id AND event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_execution_attempts
         WHERE organization_id = NEW.organization_id AND execution_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_execution_events
         WHERE organization_id = NEW.organization_id
           AND execution_id || ':' || sequence::TEXT = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_lifecycle_events
         WHERE organization_id = NEW.organization_id AND lifecycle_event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_envelope_attestations
         WHERE organization_id = NEW.organization_id
           AND execution_id || ':' || attestation_kind = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.custody_deletion_events
         WHERE organization_id = NEW.organization_id AND deletion_event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.finance-attempt-evidence' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.gateway_finance_attempt_evidence
         WHERE organization_id = NEW.organization_id AND evidence_event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.finance-source-binding-version' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.portfolio_finance_source_binding_versions
         WHERE organization_id = NEW.organization_id AND binding_event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.finance-collection-event' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.portfolio_finance_collection_events
         WHERE organization_id = NEW.organization_id AND event_id = NEW.source_event_id;
    ELSIF NEW.source_schema_id = 'hormuz.finance-snapshot' AND NEW.source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
          FROM {schema}.portfolio_finance_snapshots
         WHERE organization_id = NEW.organization_id AND snapshot_id = NEW.source_event_id;
    END IF;
    IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'audit source evidence mismatch';
    END IF;
    RETURN NEW;
END;
$$;
