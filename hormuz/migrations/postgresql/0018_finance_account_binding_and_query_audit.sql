-- Finance account registration, pre-egress account capture, and privileged
-- report-read audit. All rows are metadata-only, tenant scoped, and append-only.

CREATE TABLE {schema}.portfolio_finance_account_binding_versions (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
    version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
    binding_event_id TEXT NOT NULL CHECK (length(binding_event_id) = 36),
    previous_version INTEGER,
    binding_state TEXT NOT NULL CHECK (binding_state IN ('active','revoked')),
    reason_code TEXT NOT NULL CHECK (reason_code IN ('created','replaced','revoked')),
    upstream_reference_id TEXT NOT NULL CHECK (length(upstream_reference_id) BETWEEN 1 AND 128),
    upstream_reference_version INTEGER NOT NULL CHECK (upstream_reference_version BETWEEN 1 AND 2147483647),
    transport_profile TEXT NOT NULL CHECK (transport_profile IN ('openai.first-party.v1','anthropic.first-party.v1')),
    inference_credential_reference_id TEXT NOT NULL CHECK (length(inference_credential_reference_id) BETWEEN 1 AND 128),
    inference_credential_reference_version INTEGER NOT NULL CHECK (inference_credential_reference_version BETWEEN 1 AND 2147483647),
    source_binding_id TEXT NOT NULL CHECK (length(source_binding_id) BETWEEN 1 AND 128),
    source_binding_version INTEGER NOT NULL CHECK (source_binding_version BETWEEN 1 AND 2147483647),
    source_binding_digest TEXT NOT NULL CHECK (length(source_binding_digest) = 64),
    provider TEXT NOT NULL CHECK (provider IN ('openai','anthropic')),
    provider_account_fingerprint TEXT NOT NULL CHECK (length(provider_account_fingerprint) = 64),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization','projects','workspaces')),
    scope_fingerprints_json TEXT NOT NULL CHECK (length(scope_fingerprints_json) BETWEEN 2 AND 65536),
    fingerprint_key_version INTEGER NOT NULL CHECK (fingerprint_key_version BETWEEN 1 AND 2147483647),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    request_digest TEXT NOT NULL CHECK (length(request_digest) = 64),
    registered_by TEXT NOT NULL CHECK (length(registered_by) BETWEEN 1 AND 128),
    registered_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, binding_id, version),
    UNIQUE (organization_id, binding_event_id),
    UNIQUE (organization_id, binding_id, request_digest),
    FOREIGN KEY (organization_id, source_binding_id, source_binding_version)
        REFERENCES {schema}.portfolio_finance_source_binding_versions
            (organization_id, binding_id, version),
    CHECK (
        (version=1 AND previous_version IS NULL AND reason_code='created')
        OR (
            version>1 AND previous_version=version-1 AND (
                (binding_state='active' AND reason_code='replaced')
                OR (binding_state='revoked' AND reason_code='revoked')
            )
        )
    ),
    CHECK (
        (provider='openai' AND transport_profile='openai.first-party.v1')
        OR (provider='anthropic' AND transport_profile='anthropic.first-party.v1')
    )
);

CREATE TABLE {schema}.gateway_finance_attempt_account_bindings (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) = 36),
    event_id TEXT NOT NULL CHECK (length(event_id) = 36),
    captured_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('bound','unbound')),
    reason_code TEXT CHECK (reason_code IS NULL OR reason_code IN (
        'not_configured','binding_missing','binding_invalid','binding_revoked',
        'binding_version_stale','binding_ambiguous','source_binding_missing',
        'source_binding_revoked','source_binding_version_stale','tenant_mismatch',
        'upstream_reference_mismatch','inference_reference_mismatch',
        'unsupported_transport','fingerprint_key_version_mismatch'
    )),
    binding_id TEXT,
    binding_version INTEGER,
    binding_digest TEXT,
    upstream_reference_id TEXT,
    upstream_reference_version INTEGER,
    transport_profile TEXT,
    inference_credential_reference_id TEXT,
    inference_credential_reference_version INTEGER,
    source_binding_id TEXT,
    source_binding_version INTEGER,
    source_binding_digest TEXT,
    provider TEXT,
    provider_account_fingerprint TEXT,
    scope_kind TEXT,
    scope_fingerprints_json TEXT,
    fingerprint_key_version INTEGER,
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, request_attempt_id),
    UNIQUE (organization_id, event_id),
    FOREIGN KEY (organization_id, request_attempt_id)
        REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, binding_id, binding_version)
        REFERENCES {schema}.portfolio_finance_account_binding_versions
            (organization_id, binding_id, version),
    CHECK (
        (
            state='unbound' AND reason_code IS NOT NULL
            AND binding_id IS NULL AND binding_version IS NULL AND binding_digest IS NULL
            AND upstream_reference_id IS NULL AND upstream_reference_version IS NULL
            AND transport_profile IS NULL AND inference_credential_reference_id IS NULL
            AND inference_credential_reference_version IS NULL AND source_binding_id IS NULL
            AND source_binding_version IS NULL AND source_binding_digest IS NULL
            AND provider IS NULL AND provider_account_fingerprint IS NULL
            AND scope_kind IS NULL AND scope_fingerprints_json IS NULL
            AND fingerprint_key_version IS NULL
        ) OR (
            state='bound' AND reason_code IS NULL
            AND binding_id IS NOT NULL AND length(binding_id) BETWEEN 1 AND 128
            AND binding_version BETWEEN 1 AND 2147483647 AND length(binding_digest)=64
            AND upstream_reference_id IS NOT NULL AND length(upstream_reference_id) BETWEEN 1 AND 128
            AND upstream_reference_version BETWEEN 1 AND 2147483647
            AND transport_profile IN ('openai.first-party.v1','anthropic.first-party.v1')
            AND inference_credential_reference_id IS NOT NULL
            AND length(inference_credential_reference_id) BETWEEN 1 AND 128
            AND inference_credential_reference_version BETWEEN 1 AND 2147483647
            AND source_binding_id IS NOT NULL AND length(source_binding_id) BETWEEN 1 AND 128
            AND source_binding_version BETWEEN 1 AND 2147483647
            AND length(source_binding_digest)=64 AND provider IN ('openai','anthropic')
            AND length(provider_account_fingerprint)=64
            AND scope_kind IN ('organization','projects','workspaces')
            AND length(scope_fingerprints_json) BETWEEN 2 AND 65536
            AND fingerprint_key_version BETWEEN 1 AND 2147483647
            AND (
                (provider='openai' AND transport_profile='openai.first-party.v1')
                OR (provider='anthropic' AND transport_profile='anthropic.first-party.v1')
            )
        )
    )
);

CREATE TABLE {schema}.portfolio_finance_query_audit_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    query_event_id TEXT NOT NULL CHECK (length(query_event_id) = 36),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    query_class TEXT NOT NULL CHECK (query_class='finance_coverage_report_v1'),
    binding_id TEXT NOT NULL CHECK (length(binding_id) BETWEEN 1 AND 128),
    binding_version INTEGER NOT NULL CHECK (binding_version BETWEEN 1 AND 2147483647),
    collection_profile TEXT NOT NULL CHECK (length(collection_profile) BETWEEN 1 AND 128),
    query_start_at TEXT NOT NULL,
    query_end_at TEXT NOT NULL,
    as_of_commit_sequence BIGINT NOT NULL CHECK (as_of_commit_sequence >= 0),
    currency TEXT NOT NULL CHECK (length(currency)=3),
    selected_snapshot_count BIGINT NOT NULL CHECK (selected_snapshot_count >= 0),
    coverage_bucket_count BIGINT NOT NULL CHECK (coverage_bucket_count >= 0),
    provider_observation_count BIGINT NOT NULL CHECK (provider_observation_count >= 0),
    terminal_attempt_count BIGINT NOT NULL CHECK (terminal_attempt_count >= 0),
    terminal_attempts_missing_sidecar_count BIGINT NOT NULL CHECK (
        terminal_attempts_missing_sidecar_count >= 0
        AND terminal_attempts_missing_sidecar_count <= terminal_attempt_count
    ),
    occurred_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL CHECK (length(evidence_json) BETWEEN 2 AND 65536),
    PRIMARY KEY (organization_id, query_event_id),
    FOREIGN KEY (organization_id, binding_id, binding_version)
        REFERENCES {schema}.portfolio_finance_source_binding_versions
            (organization_id, binding_id, version)
);

CREATE INDEX portfolio_finance_account_binding_current ON {schema}.portfolio_finance_account_binding_versions (organization_id, binding_id, version DESC);
CREATE INDEX portfolio_finance_attempt_account_binding_event ON {schema}.gateway_finance_attempt_account_bindings (organization_id, event_id);
CREATE INDEX portfolio_finance_query_audit_time ON {schema}.portfolio_finance_query_audit_events (organization_id, occurred_at, query_event_id);

ALTER TABLE {schema}.portfolio_finance_account_binding_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_account_binding_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_account_binding_versions_tenant ON {schema}.portfolio_finance_account_binding_versions USING (organization_id=current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_account_binding_versions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_account_binding_versions FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_account_binding_versions FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_finance_account_binding_versions TO {runtime_role};

ALTER TABLE {schema}.gateway_finance_attempt_account_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_finance_attempt_account_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY gateway_finance_attempt_account_bindings_tenant ON {schema}.gateway_finance_attempt_account_bindings USING (organization_id=current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER gateway_finance_attempt_account_bindings_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.gateway_finance_attempt_account_bindings FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.gateway_finance_attempt_account_bindings FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.gateway_finance_attempt_account_bindings TO {runtime_role};

ALTER TABLE {schema}.portfolio_finance_query_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_finance_query_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_finance_query_audit_events_tenant ON {schema}.portfolio_finance_query_audit_events USING (organization_id=current_setting('hormuz.organization_id', true)) WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_finance_query_audit_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_finance_query_audit_events FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_finance_query_audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_finance_query_audit_events TO {runtime_role};

CREATE FUNCTION {schema}.enforce_finance_account_binding_source()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM {schema}.portfolio_finance_source_binding_versions source
        WHERE source.organization_id=NEW.organization_id
          AND source.binding_id=NEW.source_binding_id
          AND source.version=NEW.source_binding_version
          AND source.content_digest=NEW.source_binding_digest
          AND source.provider=NEW.provider
          AND source.provider_account_fingerprint=NEW.provider_account_fingerprint
          AND source.scope_kind=NEW.scope_kind
          AND source.scope_fingerprints_json=NEW.scope_fingerprints_json
          AND source.fingerprint_key_version=NEW.fingerprint_key_version
    ) THEN
        RAISE EXCEPTION 'finance_account_binding_source_mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION {schema}.enforce_finance_account_binding_source() FROM PUBLIC;
CREATE TRIGGER portfolio_finance_account_binding_source_consistency
BEFORE INSERT ON {schema}.portfolio_finance_account_binding_versions
FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_finance_account_binding_source();

CREATE FUNCTION {schema}.enforce_finance_attempt_account_binding()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM {schema}.gateway_request_attempts root
        WHERE root.organization_id=NEW.organization_id
          AND root.attempt_id=NEW.request_attempt_id
          AND root.created_at=NEW.captured_at::timestamptz
    ) OR (
        NEW.state='bound' AND NOT EXISTS (
            SELECT 1 FROM {schema}.portfolio_finance_account_binding_versions binding
            WHERE binding.organization_id=NEW.organization_id
              AND binding.binding_id=NEW.binding_id
              AND binding.version=NEW.binding_version
              AND binding.binding_state='active'
              AND binding.content_digest=NEW.binding_digest
              AND binding.upstream_reference_id=NEW.upstream_reference_id
              AND binding.upstream_reference_version=NEW.upstream_reference_version
              AND binding.transport_profile=NEW.transport_profile
              AND binding.inference_credential_reference_id=NEW.inference_credential_reference_id
              AND binding.inference_credential_reference_version=NEW.inference_credential_reference_version
              AND binding.source_binding_id=NEW.source_binding_id
              AND binding.source_binding_version=NEW.source_binding_version
              AND binding.source_binding_digest=NEW.source_binding_digest
              AND binding.provider=NEW.provider
              AND binding.provider_account_fingerprint=NEW.provider_account_fingerprint
              AND binding.scope_kind=NEW.scope_kind
              AND binding.scope_fingerprints_json=NEW.scope_fingerprints_json
              AND binding.fingerprint_key_version=NEW.fingerprint_key_version
        )
    ) THEN
        RAISE EXCEPTION 'finance_attempt_account_binding_mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION {schema}.enforce_finance_attempt_account_binding() FROM PUBLIC;
CREATE TRIGGER gateway_finance_attempt_account_binding_consistency
BEFORE INSERT ON {schema}.gateway_finance_attempt_account_bindings
FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_finance_attempt_account_binding();

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
    END IF;
    IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='audit source evidence mismatch';
    END IF;
    RETURN NEW;
END;
$$;
