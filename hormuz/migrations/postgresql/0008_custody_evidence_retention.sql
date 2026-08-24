-- Versioned custody evidence: explicit tenant retention, strict v2 audit-chain
-- sources, and deferred atomicity checks.  Existing v1 audit-chain entries and
-- pre-v8 custody history remain untouched; every new custody source record
-- must carry canonical metadata-only evidence plus an immutable deadline.

ALTER TABLE {schema}.custody_tenants
    ADD COLUMN IF NOT EXISTS retention_days INTEGER,
    ADD COLUMN IF NOT EXISTS retention_legal_hold BOOLEAN;

ALTER TABLE {schema}.custody_tenants
    DROP CONSTRAINT IF EXISTS custody_tenants_retention_configuration_check;
ALTER TABLE {schema}.custody_tenants
    ADD CONSTRAINT custody_tenants_retention_configuration_check
    CHECK (
        (retention_days IS NULL AND retention_legal_hold IS NULL)
        OR (
            retention_days BETWEEN 1 AND 36500
            AND retention_legal_hold IS NOT NULL
        )
    );

ALTER TABLE {schema}.gateway_audit_chain_entries
    ADD COLUMN IF NOT EXISTS source_schema_id TEXT,
    ADD COLUMN IF NOT EXISTS source_schema_version INTEGER,
    ADD COLUMN IF NOT EXISTS source_event_id TEXT;

ALTER TABLE {schema}.gateway_audit_chain_entries
    DROP CONSTRAINT IF EXISTS gateway_audit_chain_entries_entry_schema_version_check;
ALTER TABLE {schema}.gateway_audit_chain_entries
    ADD CONSTRAINT gateway_audit_chain_entries_entry_schema_version_check
    CHECK (entry_schema_version IN (1, 2));
ALTER TABLE {schema}.gateway_audit_chain_entries
    DROP CONSTRAINT IF EXISTS gateway_audit_chain_entries_source_identity_check;
ALTER TABLE {schema}.gateway_audit_chain_entries
    ADD CONSTRAINT gateway_audit_chain_entries_source_identity_check
    CHECK (
        (
            entry_schema_version = 1
            AND source_schema_id IS NULL
            AND source_schema_version IS NULL
            AND source_event_id IS NULL
        )
        OR (
            entry_schema_version = 2
            AND (
                (source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2)
                OR (source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1)
                OR (source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)
            )
            AND source_event_id IS NOT NULL
        )
    );
CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_audit_chain_entries_source_identity
    ON {schema}.gateway_audit_chain_entries (
        organization_id, source_schema_id, source_schema_version, source_event_id
    )
    WHERE entry_schema_version = 2;

ALTER TABLE {schema}.custody_control_events
    ADD COLUMN IF NOT EXISTS evidence_json TEXT,
    ADD COLUMN IF NOT EXISTS retain_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN;
ALTER TABLE {schema}.custody_execution_attempts
    ADD COLUMN IF NOT EXISTS evidence_json TEXT,
    ADD COLUMN IF NOT EXISTS retain_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN;
ALTER TABLE {schema}.custody_execution_events
    ADD COLUMN IF NOT EXISTS evidence_json TEXT,
    ADD COLUMN IF NOT EXISTS retain_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN;
ALTER TABLE {schema}.custody_lifecycle_events
    ADD COLUMN IF NOT EXISTS evidence_json TEXT,
    ADD COLUMN IF NOT EXISTS retain_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN;
ALTER TABLE {schema}.custody_envelope_attestations
    ADD COLUMN IF NOT EXISTS evidence_json TEXT,
    ADD COLUMN IF NOT EXISTS retain_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS legal_hold BOOLEAN;

-- A deletion request is evidence about Hormuz's own custody history, never an
-- execution capability.  It records why deletion was refused and becomes a
-- protected source record itself.  There is deliberately no delete executor
-- or bypass state in this schema.
CREATE TABLE IF NOT EXISTS {schema}.custody_deletion_events (
    organization_id TEXT NOT NULL,
    deletion_event_id TEXT NOT NULL,
    deletion_schema_id TEXT NOT NULL,
    deletion_schema_version INTEGER NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    source_schema_id TEXT NOT NULL,
    source_schema_version INTEGER NOT NULL,
    source_event_id TEXT NOT NULL,
    source_retain_until TIMESTAMPTZ NOT NULL,
    source_legal_hold BOOLEAN NOT NULL,
    decision TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evidence_json TEXT,
    retain_until TIMESTAMPTZ,
    legal_hold BOOLEAN,
    PRIMARY KEY (organization_id, deletion_event_id),
    FOREIGN KEY (organization_id) REFERENCES {schema}.custody_tenants (organization_id),
    CHECK (deletion_schema_id = 'hormuz.custody-deletion-event'),
    CHECK (deletion_schema_version = 1),
    CHECK (
        (source_schema_id = 'hormuz.custody-control-event' AND source_schema_version = 1)
        OR (source_schema_id = 'hormuz.custody-execution-attempt' AND source_schema_version = 2)
        OR (source_schema_id = 'hormuz.custody-execution-event' AND source_schema_version = 1)
        OR (source_schema_id = 'hormuz.custody-lifecycle-event' AND source_schema_version = 1)
        OR (source_schema_id = 'hormuz.custody-envelope-attestation' AND source_schema_version = 1)
        OR (source_schema_id = 'hormuz.custody-deletion-event' AND source_schema_version = 1)
    ),
    CHECK (decision = 'deletion_blocked'),
    CHECK (reason_code IN ('retention_active', 'legal_hold_active', 'strong_approval_required'))
);
CREATE INDEX IF NOT EXISTS idx_custody_deletion_events_history
    ON {schema}.custody_deletion_events (organization_id, occurred_at DESC, deletion_event_id);

-- A custody write uses the database clock and the retention policy persisted at
-- tenant bootstrap.  A process-local config change can therefore neither
-- rewrite nor shorten an already durable deadline.
CREATE OR REPLACE FUNCTION {schema}.enforce_custody_evidence_retention()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    policy_days INTEGER;
    policy_hold BOOLEAN;
    event_time TIMESTAMPTZ;
BEGIN
    IF TG_TABLE_NAME = 'custody_execution_attempts' THEN
        event_time := NEW.claimed_at;
    ELSE
        event_time := NEW.occurred_at;
    END IF;
    IF NEW.evidence_json IS NULL OR NEW.retain_until IS NULL OR NEW.legal_hold IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence retention is required';
    END IF;
    SELECT retention_days, retention_legal_hold
      INTO policy_days, policy_hold
      FROM {schema}.custody_tenants
     WHERE organization_id = NEW.organization_id
     FOR SHARE;
    IF NOT FOUND OR policy_days IS NULL OR policy_hold IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody tenant retention is not initialized';
    END IF;
    IF NEW.retain_until <> event_time + make_interval(days => policy_days)
       OR NEW.legal_hold IS DISTINCT FROM policy_hold THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence retention does not match tenant policy';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.deny_custody_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody evidence is immutable';
END;
$$;

-- The restricted custody service must not be able to turn a known source table
-- into an arbitrary JSON side channel.  These checks bind every persisted
-- evidence JSON object to the row that carries it: exact allowlisted keys,
-- fixed source schema/version, tenant identity, immutable identifiers, and
-- all non-temporal fields must agree. Timestamps are compared as timestamptz
-- values so a canonical UTC spelling can be used by independent clients.
CREATE OR REPLACE FUNCTION {schema}.custody_evidence_exact_keys(
    p_value JSONB,
    p_expected TEXT[]
)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    observed TEXT[];
    expected TEXT[];
BEGIN
    IF jsonb_typeof(p_value) <> 'object' THEN
        RETURN FALSE;
    END IF;
    SELECT array_agg(key ORDER BY key)
      INTO observed
      FROM jsonb_object_keys(p_value) AS key;
    SELECT array_agg(key ORDER BY key)
      INTO expected
      FROM unnest(p_expected) AS key;
    RETURN observed IS NOT DISTINCT FROM expected;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_evidence_timestamp_matches(
    p_value JSONB,
    p_expected TIMESTAMPTZ
)
RETURNS BOOLEAN
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    parsed TIMESTAMPTZ;
BEGIN
    IF p_expected IS NULL THEN
        RETURN p_value = 'null'::jsonb;
    END IF;
    IF jsonb_typeof(p_value) <> 'string' THEN
        RETURN FALSE;
    END IF;
    BEGIN
        parsed := (p_value #>> '{{}}')::TIMESTAMPTZ;
    EXCEPTION WHEN others THEN
        RETURN FALSE;
    END;
    RETURN parsed = p_expected;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_evidence_contract()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    evidence JSONB;
    expected JSONB;
BEGIN
    BEGIN
        evidence := NEW.evidence_json::JSONB;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
    END;
    IF evidence IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
    END IF;

    IF TG_TABLE_NAME = 'custody_control_events' THEN
        expected := jsonb_build_object(
            'event_schema_id', NEW.event_schema_id,
            'event_schema_version', NEW.event_schema_version,
            'organization_id', NEW.organization_id,
            'event_type', NEW.event_type,
            'actor_kind', NEW.actor_kind,
            'actor_identity_key', NEW.actor_identity_key,
            'target_identity_key', NEW.target_identity_key,
            'operation_id', NEW.operation_id,
            'operation_type', NEW.operation_type,
            'risk_level', NEW.risk_level,
            'target_kind', NEW.target_kind,
            'target_sha256', NEW.target_sha256,
            'parameters_sha256', NEW.parameters_sha256,
            'protected_input_ref_sha256', NEW.protected_input_ref_sha256,
            'required_approvals', NEW.required_approvals,
            'approval_count', NEW.approval_count
        );
        IF NOT {schema}.custody_evidence_exact_keys(
            evidence,
            ARRAY[
                'event_schema_id', 'event_schema_version', 'organization_id',
                'occurred_at', 'event_type', 'actor_kind', 'actor_identity_key',
                'target_identity_key', 'operation_id', 'operation_type', 'risk_level',
                'target_kind', 'target_sha256', 'parameters_sha256',
                'protected_input_ref_sha256', 'required_approvals', 'approval_count',
                'expires_at'
            ]
        ) OR (evidence - ARRAY['occurred_at', 'expires_at']) IS DISTINCT FROM expected
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'occurred_at', NEW.occurred_at)
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'expires_at', NEW.expires_at) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
        END IF;
    ELSIF TG_TABLE_NAME = 'custody_execution_attempts' THEN
        expected := jsonb_build_object(
            'execution_schema_id', NEW.execution_schema_id,
            'execution_schema_version', NEW.execution_schema_version,
            'organization_id', NEW.organization_id,
            'execution_id', NEW.execution_id,
            'operation_id', NEW.operation_id,
            'operation_type', NEW.operation_type,
            'target_kind', NEW.target_kind,
            'target_sha256', NEW.target_sha256,
            'parameters_sha256', NEW.parameters_sha256,
            'protected_input_ref_sha256', NEW.protected_input_ref_sha256,
            'state', 'pending'
        );
        IF NOT {schema}.custody_evidence_exact_keys(
            evidence,
            ARRAY[
                'execution_schema_id', 'execution_schema_version', 'organization_id',
                'execution_id', 'operation_id', 'operation_type', 'target_kind',
                'target_sha256', 'parameters_sha256', 'protected_input_ref_sha256',
                'claimed_at', 'state'
            ]
        ) OR (evidence - ARRAY['claimed_at']) IS DISTINCT FROM expected
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'claimed_at', NEW.claimed_at) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
        END IF;
    ELSIF TG_TABLE_NAME = 'custody_execution_events' THEN
        expected := jsonb_build_object(
            'event_schema_id', NEW.event_schema_id,
            'event_schema_version', NEW.event_schema_version,
            'organization_id', NEW.organization_id,
            'execution_id', NEW.execution_id,
            'operation_id', NEW.operation_id,
            'sequence', NEW.sequence,
            'state', NEW.state,
            'reason_code', NEW.reason_code
        );
        IF NOT {schema}.custody_evidence_exact_keys(
            evidence,
            ARRAY[
                'event_schema_id', 'event_schema_version', 'organization_id',
                'execution_id', 'operation_id', 'occurred_at', 'sequence', 'state',
                'reason_code'
            ]
        ) OR (evidence - ARRAY['occurred_at']) IS DISTINCT FROM expected
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'occurred_at', NEW.occurred_at) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
        END IF;
    ELSIF TG_TABLE_NAME = 'custody_lifecycle_events' THEN
        expected := jsonb_build_object(
            'lifecycle_schema_id', NEW.lifecycle_schema_id,
            'lifecycle_schema_version', NEW.lifecycle_schema_version,
            'organization_id', NEW.organization_id,
            'lifecycle_event_id', NEW.lifecycle_event_id,
            'execution_id', NEW.execution_id,
            'operation_id', NEW.operation_id,
            'operation_type', NEW.operation_type,
            'target_sha256', NEW.target_sha256,
            'parameters_sha256', NEW.parameters_sha256,
            'asset_type', NEW.asset_type,
            'asset_id', NEW.asset_id,
            'asset_generation', NEW.asset_generation,
            'asset_binding_fingerprint', NEW.asset_binding_fingerprint,
            'replacement_asset_type', NEW.replacement_asset_type,
            'replacement_asset_id', NEW.replacement_asset_id,
            'replacement_asset_generation', NEW.replacement_asset_generation,
            'replacement_asset_binding_fingerprint', NEW.replacement_asset_binding_fingerprint,
            'recovery_execution_id', NEW.recovery_execution_id,
            'recovery_resolution_code', NEW.recovery_resolution_code,
            'chain_version', NEW.chain_version,
            'sequence', NEW.sequence,
            'previous_digest', NEW.previous_digest,
            'event_digest', NEW.event_digest
        );
        IF NOT {schema}.custody_evidence_exact_keys(
            evidence,
            ARRAY[
                'lifecycle_schema_id', 'lifecycle_schema_version', 'organization_id',
                'lifecycle_event_id', 'execution_id', 'operation_id', 'occurred_at',
                'operation_type', 'target_sha256', 'parameters_sha256', 'asset_type',
                'asset_id', 'asset_generation', 'asset_binding_fingerprint',
                'replacement_asset_type', 'replacement_asset_id',
                'replacement_asset_generation', 'replacement_asset_binding_fingerprint',
                'recovery_execution_id', 'recovery_resolution_code', 'chain_version',
                'sequence', 'previous_digest', 'event_digest'
            ]
        ) OR (evidence - ARRAY['occurred_at']) IS DISTINCT FROM expected
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'occurred_at', NEW.occurred_at) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
        END IF;
    ELSIF TG_TABLE_NAME = 'custody_envelope_attestations' THEN
        expected := jsonb_build_object(
            'attestation_schema_id', 'hormuz.custody-envelope-attestation',
            'attestation_schema_version', 1,
            'organization_id', NEW.organization_id,
            'execution_id', NEW.execution_id,
            'attestation_kind', NEW.attestation_kind,
            'envelope_asset_id', NEW.envelope_asset_id,
            'envelope_generation', NEW.envelope_generation,
            'envelope_binding_fingerprint', NEW.envelope_binding_fingerprint,
            'source_key_asset_id', NEW.source_key_asset_id,
            'source_key_generation', NEW.source_key_generation,
            'source_key_binding_fingerprint', NEW.source_key_binding_fingerprint,
            'destination_key_asset_id', NEW.destination_key_asset_id,
            'destination_key_generation', NEW.destination_key_generation,
            'destination_key_binding_fingerprint', NEW.destination_key_binding_fingerprint
        );
        IF NOT {schema}.custody_evidence_exact_keys(
            evidence,
            ARRAY[
                'attestation_schema_id', 'attestation_schema_version', 'organization_id',
                'execution_id', 'attestation_kind', 'envelope_asset_id',
                'envelope_generation', 'envelope_binding_fingerprint',
                'source_key_asset_id', 'source_key_generation',
                'source_key_binding_fingerprint', 'destination_key_asset_id',
                'destination_key_generation', 'destination_key_binding_fingerprint',
                'occurred_at'
            ]
        ) OR (evidence - ARRAY['occurred_at']) IS DISTINCT FROM expected
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'occurred_at', NEW.occurred_at) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
        END IF;
    ELSIF TG_TABLE_NAME = 'custody_deletion_events' THEN
        expected := jsonb_build_object(
            'deletion_schema_id', NEW.deletion_schema_id,
            'deletion_schema_version', NEW.deletion_schema_version,
            'organization_id', NEW.organization_id,
            'deletion_event_id', NEW.deletion_event_id,
            'source_schema_id', NEW.source_schema_id,
            'source_schema_version', NEW.source_schema_version,
            'source_event_id', NEW.source_event_id,
            'source_legal_hold', NEW.source_legal_hold,
            'decision', NEW.decision,
            'reason_code', NEW.reason_code
        );
        IF NOT {schema}.custody_evidence_exact_keys(
            evidence,
            ARRAY[
                'deletion_schema_id', 'deletion_schema_version', 'organization_id',
                'deletion_event_id', 'occurred_at', 'source_schema_id',
                'source_schema_version', 'source_event_id', 'source_retain_until',
                'source_legal_hold', 'decision', 'reason_code'
            ]
        ) OR (evidence - ARRAY['occurred_at', 'source_retain_until']) IS DISTINCT FROM expected
           OR NOT {schema}.custody_evidence_timestamp_matches(evidence -> 'occurred_at', NEW.occurred_at)
           OR NOT {schema}.custody_evidence_timestamp_matches(
               evidence -> 'source_retain_until', NEW.source_retain_until
           ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence contract is invalid';
        END IF;
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody evidence source table is unsupported';
    END IF;
    RETURN NEW;
END;
$$;

-- A common tuple position function keeps v2 custody entries serialized with
-- existing v1 usage/security writers.  The row lock is held by the calling
-- transaction until it appends the matching entry below.
CREATE OR REPLACE FUNCTION {schema}.custody_audit_chain_next_position(
    p_organization_id TEXT
)
RETURNS TABLE(chain_version INTEGER, chain_epoch INTEGER, next_sequence BIGINT, previous_digest TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_chain_version INTEGER;
    v_chain_epoch INTEGER;
    v_sequence BIGINT;
    v_digest TEXT;
    v_now TIMESTAMPTZ;
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody audit chain tenant context is invalid';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:audit-chain:' || p_organization_id));
    v_now := clock_timestamp();
    INSERT INTO {schema}.gateway_audit_chain_epochs (
        organization_id, chain_version, chain_epoch, created_at, reason_code,
        predecessor_chain_epoch, predecessor_sequence, predecessor_head_digest
    ) VALUES (p_organization_id, 1, 1, v_now, 'initial_adoption', NULL, NULL, NULL)
    ON CONFLICT DO NOTHING;
    INSERT INTO {schema}.gateway_audit_chain_heads (
        organization_id, chain_version, chain_epoch, sequence, head_digest
    ) VALUES (p_organization_id, 1, 1, 0, NULL)
    ON CONFLICT (organization_id) DO NOTHING;
    SELECT head.chain_version, head.chain_epoch, head.sequence, head.head_digest
      INTO v_chain_version, v_chain_epoch, v_sequence, v_digest
      FROM {schema}.gateway_audit_chain_heads AS head
     WHERE head.organization_id = p_organization_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit chain head is unavailable';
    END IF;
    RETURN QUERY SELECT v_chain_version, v_chain_epoch, v_sequence + 1, v_digest;
END;
$$;

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
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source schema is unsupported';
    END IF;
    RETURN v_event_json;
END;
$$;

-- Tenant export reads are likewise constrained to the finite custody source
-- union.  The control service cannot query arbitrary source tables or modify
-- chain state; it receives only records that already have a committed v2
-- chain entry.
CREATE OR REPLACE FUNCTION {schema}.custody_audit_chain_export_entries(
    p_organization_id TEXT
)
RETURNS TABLE(
    chain_version INTEGER,
    chain_epoch INTEGER,
    sequence BIGINT,
    entry_schema_id TEXT,
    entry_schema_version INTEGER,
    event_id TEXT,
    previous_digest TEXT,
    event_digest TEXT,
    event_json TEXT,
    source_schema_id TEXT,
    source_schema_version INTEGER,
    source_event_id TEXT
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody audit export tenant context is invalid';
    END IF;
    RETURN QUERY
    SELECT entry.chain_version,
           entry.chain_epoch,
           entry.sequence,
           entry.entry_schema_id,
           entry.entry_schema_version,
           entry.event_id,
           entry.previous_digest,
           entry.event_digest,
           entry.event_json,
           entry.source_schema_id,
           entry.source_schema_version,
           entry.source_event_id
    FROM {schema}.gateway_audit_chain_entries AS entry
    WHERE entry.organization_id = p_organization_id
      AND entry.entry_schema_version = 2
      AND (
          (entry.source_schema_id = 'hormuz.custody-control-event' AND entry.source_schema_version = 1)
          OR (entry.source_schema_id = 'hormuz.custody-execution-attempt' AND entry.source_schema_version = 2)
          OR (entry.source_schema_id = 'hormuz.custody-execution-event' AND entry.source_schema_version = 1)
          OR (entry.source_schema_id = 'hormuz.custody-lifecycle-event' AND entry.source_schema_version = 1)
          OR (entry.source_schema_id = 'hormuz.custody-envelope-attestation' AND entry.source_schema_version = 1)
          OR (entry.source_schema_id = 'hormuz.custody-deletion-event' AND entry.source_schema_version = 1)
      )
    ORDER BY entry.chain_epoch, entry.sequence;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_audit_chain_source_retention(
    p_organization_id TEXT,
    p_source_schema_id TEXT,
    p_source_schema_version INTEGER,
    p_source_event_id TEXT
)
RETURNS TABLE(retain_until TIMESTAMPTZ, legal_hold BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
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
        RETURN;
    END IF;
    IF p_source_schema_id = 'hormuz.custody-control-event' AND p_source_schema_version = 1 THEN
        RETURN QUERY SELECT source.retain_until, source.legal_hold
        FROM {schema}.custody_control_events AS source
        WHERE source.organization_id = p_organization_id AND source.event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-execution-attempt' AND p_source_schema_version = 2 THEN
        RETURN QUERY SELECT source.retain_until, source.legal_hold
        FROM {schema}.custody_execution_attempts AS source
        WHERE source.organization_id = p_organization_id AND source.execution_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-execution-event' AND p_source_schema_version = 1 THEN
        RETURN QUERY SELECT source.retain_until, source.legal_hold
        FROM {schema}.custody_execution_events AS source
        WHERE source.organization_id = p_organization_id
          AND source.execution_id || ':' || source.sequence::TEXT = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-lifecycle-event' AND p_source_schema_version = 1 THEN
        RETURN QUERY SELECT source.retain_until, source.legal_hold
        FROM {schema}.custody_lifecycle_events AS source
        WHERE source.organization_id = p_organization_id AND source.lifecycle_event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-envelope-attestation' AND p_source_schema_version = 1 THEN
        RETURN QUERY SELECT source.retain_until, source.legal_hold
        FROM {schema}.custody_envelope_attestations AS source
        WHERE source.organization_id = p_organization_id
          AND source.execution_id || ':' || source.attestation_kind = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-deletion-event' AND p_source_schema_version = 1 THEN
        RETURN QUERY SELECT source.retain_until, source.legal_hold
        FROM {schema}.custody_deletion_events AS source
        WHERE source.organization_id = p_organization_id AND source.deletion_event_id = p_source_event_id;
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source schema is unsupported';
    END IF;
END;
$$;

-- Version 1 gateway writers retain their established direct append path.  A
-- version 2 entry, however, may only exist when it is the exact canonical
-- evidence of one already-inserted strict custody source.  This prevents the
-- existing runtime entry-insert privilege from becoming an arbitrary JSON
-- side channel merely because v2 shares the organization audit table.
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
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit chain entry is invalid';
    END IF;
    IF NOT (
        (NEW.source_schema_id = 'hormuz.custody-control-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-execution-attempt' AND NEW.source_schema_version = 2)
        OR (NEW.source_schema_id = 'hormuz.custody-execution-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-lifecycle-event' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-envelope-attestation' AND NEW.source_schema_version = 1)
        OR (NEW.source_schema_id = 'hormuz.custody-deletion-event' AND NEW.source_schema_version = 1)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source schema is unsupported';
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
    END IF;
    IF v_source_json IS NULL OR NEW.event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source evidence mismatch';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_audit_chain_append_entry(
    p_organization_id TEXT,
    p_source_schema_id TEXT,
    p_source_schema_version INTEGER,
    p_source_event_id TEXT,
    p_chain_epoch INTEGER,
    p_sequence BIGINT,
    p_previous_digest TEXT,
    p_event_digest TEXT,
    p_event_json TEXT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_chain_version INTEGER;
    v_current_epoch INTEGER;
    v_current_sequence BIGINT;
    v_current_digest TEXT;
    v_source_json TEXT;
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody audit chain tenant context is invalid';
    END IF;
    IF p_source_schema_id NOT IN (
        'hormuz.custody-control-event',
        'hormuz.custody-execution-attempt',
        'hormuz.custody-execution-event',
        'hormuz.custody-lifecycle-event',
        'hormuz.custody-envelope-attestation',
        'hormuz.custody-deletion-event'
    ) OR NOT (
        (p_source_schema_id = 'hormuz.custody-control-event' AND p_source_schema_version = 1)
        OR (p_source_schema_id = 'hormuz.custody-execution-attempt' AND p_source_schema_version = 2)
        OR (p_source_schema_id = 'hormuz.custody-execution-event' AND p_source_schema_version = 1)
        OR (p_source_schema_id = 'hormuz.custody-lifecycle-event' AND p_source_schema_version = 1)
        OR (p_source_schema_id = 'hormuz.custody-envelope-attestation' AND p_source_schema_version = 1)
        OR (p_source_schema_id = 'hormuz.custody-deletion-event' AND p_source_schema_version = 1)
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source schema is unsupported';
    END IF;
    IF p_event_digest !~ '^[0-9a-f]{{64}}$' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit chain digest is invalid';
    END IF;
    IF p_source_schema_id = 'hormuz.custody-control-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
        FROM {schema}.custody_control_events
        WHERE organization_id = p_organization_id AND event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-execution-attempt' AND p_source_schema_version = 2 THEN
        SELECT evidence_json INTO v_source_json
        FROM {schema}.custody_execution_attempts
        WHERE organization_id = p_organization_id AND execution_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-execution-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
        FROM {schema}.custody_execution_events
        WHERE organization_id = p_organization_id
          AND execution_id || ':' || sequence::TEXT = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-lifecycle-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
        FROM {schema}.custody_lifecycle_events
        WHERE organization_id = p_organization_id AND lifecycle_event_id = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-envelope-attestation' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
        FROM {schema}.custody_envelope_attestations
        WHERE organization_id = p_organization_id
          AND execution_id || ':' || attestation_kind = p_source_event_id;
    ELSIF p_source_schema_id = 'hormuz.custody-deletion-event' AND p_source_schema_version = 1 THEN
        SELECT evidence_json INTO v_source_json
        FROM {schema}.custody_deletion_events
        WHERE organization_id = p_organization_id AND deletion_event_id = p_source_event_id;
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source schema is unsupported';
    END IF;
    IF v_source_json IS NULL OR p_event_json IS DISTINCT FROM v_source_json THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source evidence mismatch';
    END IF;
    SELECT chain_version, chain_epoch, sequence, head_digest
      INTO v_chain_version, v_current_epoch, v_current_sequence, v_current_digest
      FROM {schema}.gateway_audit_chain_heads
     WHERE organization_id = p_organization_id
     FOR UPDATE;
    IF NOT FOUND
       OR v_chain_version <> 1
       OR v_current_epoch <> p_chain_epoch
       OR p_sequence <> v_current_sequence + 1
       OR v_current_digest IS DISTINCT FROM p_previous_digest THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit chain position is invalid';
    END IF;
    INSERT INTO {schema}.gateway_audit_chain_entries (
        organization_id, chain_version, chain_epoch, sequence,
        entry_schema_id, entry_schema_version, event_id,
        previous_digest, event_digest, event_json, appended_at,
        source_schema_id, source_schema_version, source_event_id
    ) VALUES (
        p_organization_id, 1, p_chain_epoch, p_sequence,
        'hormuz.commit-audit-chain-entry', 2, p_source_event_id,
        p_previous_digest, p_event_digest, p_event_json, clock_timestamp(),
        p_source_schema_id, p_source_schema_version, p_source_event_id
    );
    UPDATE {schema}.gateway_audit_chain_heads
       SET sequence = p_sequence, head_digest = p_event_digest
     WHERE organization_id = p_organization_id
       AND chain_epoch = p_chain_epoch
       AND sequence = p_sequence - 1
       AND head_digest IS NOT DISTINCT FROM p_previous_digest;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit chain head update conflict';
    END IF;
END;
$$;

CREATE TRIGGER gateway_audit_chain_entries_v2_source_required
    BEFORE INSERT ON {schema}.gateway_audit_chain_entries
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry_insert();

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_audit_chain_entry()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_source_schema_id TEXT;
    v_source_schema_version INTEGER;
    v_source_event_id TEXT;
BEGIN
    IF TG_TABLE_NAME = 'custody_control_events' THEN
        v_source_schema_id := 'hormuz.custody-control-event';
        v_source_schema_version := 1;
        v_source_event_id := NEW.event_id;
    ELSIF TG_TABLE_NAME = 'custody_execution_attempts' THEN
        v_source_schema_id := 'hormuz.custody-execution-attempt';
        v_source_schema_version := 2;
        v_source_event_id := NEW.execution_id;
    ELSIF TG_TABLE_NAME = 'custody_execution_events' THEN
        v_source_schema_id := 'hormuz.custody-execution-event';
        v_source_schema_version := 1;
        v_source_event_id := NEW.execution_id || ':' || NEW.sequence::TEXT;
    ELSIF TG_TABLE_NAME = 'custody_lifecycle_events' THEN
        v_source_schema_id := 'hormuz.custody-lifecycle-event';
        v_source_schema_version := 1;
        v_source_event_id := NEW.lifecycle_event_id;
    ELSIF TG_TABLE_NAME = 'custody_envelope_attestations' THEN
        v_source_schema_id := 'hormuz.custody-envelope-attestation';
        v_source_schema_version := 1;
        v_source_event_id := NEW.execution_id || ':' || NEW.attestation_kind;
    ELSIF TG_TABLE_NAME = 'custody_deletion_events' THEN
        v_source_schema_id := 'hormuz.custody-deletion-event';
        v_source_schema_version := 1;
        v_source_event_id := NEW.deletion_event_id;
    ELSE
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody audit source table is unsupported';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM {schema}.gateway_audit_chain_entries AS entry
        WHERE entry.organization_id = NEW.organization_id
          AND entry.entry_schema_version = 2
          AND entry.source_schema_id = v_source_schema_id
          AND entry.source_schema_version = v_source_schema_version
          AND entry.source_event_id = v_source_event_id
          AND entry.event_json = NEW.evidence_json
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody source entry is required';
    END IF;
    RETURN NULL;
END;
$$;

-- Retention validation runs before a new source record is accepted.  The
-- deferred check runs at commit and rolls back the source, chain head, and
-- any lifecycle projection if the matching v2 entry is absent.
CREATE TRIGGER custody_control_events_contract_required
    BEFORE INSERT ON {schema}.custody_control_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_contract();
CREATE TRIGGER custody_control_events_retention_required
    BEFORE INSERT ON {schema}.custody_control_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_retention();
CREATE CONSTRAINT TRIGGER custody_control_events_chain_required
    AFTER INSERT ON {schema}.custody_control_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry();

CREATE TRIGGER custody_execution_attempts_contract_required
    BEFORE INSERT ON {schema}.custody_execution_attempts
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_contract();
CREATE TRIGGER custody_execution_attempts_retention_required
    BEFORE INSERT ON {schema}.custody_execution_attempts
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_retention();
CREATE CONSTRAINT TRIGGER custody_execution_attempts_chain_required
    AFTER INSERT ON {schema}.custody_execution_attempts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry();

CREATE TRIGGER custody_execution_events_contract_required
    BEFORE INSERT ON {schema}.custody_execution_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_contract();
CREATE TRIGGER custody_execution_events_retention_required
    BEFORE INSERT ON {schema}.custody_execution_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_retention();
CREATE CONSTRAINT TRIGGER custody_execution_events_chain_required
    AFTER INSERT ON {schema}.custody_execution_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry();

CREATE TRIGGER custody_lifecycle_events_contract_required
    BEFORE INSERT ON {schema}.custody_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_contract();
CREATE TRIGGER custody_lifecycle_events_retention_required
    BEFORE INSERT ON {schema}.custody_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_retention();
CREATE CONSTRAINT TRIGGER custody_lifecycle_events_chain_required
    AFTER INSERT ON {schema}.custody_lifecycle_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry();

CREATE TRIGGER custody_envelope_attestations_contract_required
    BEFORE INSERT ON {schema}.custody_envelope_attestations
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_contract();
CREATE TRIGGER custody_envelope_attestations_retention_required
    BEFORE INSERT ON {schema}.custody_envelope_attestations
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_retention();
CREATE CONSTRAINT TRIGGER custody_envelope_attestations_chain_required
    AFTER INSERT ON {schema}.custody_envelope_attestations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry();

CREATE TRIGGER custody_deletion_events_contract_required
    BEFORE INSERT ON {schema}.custody_deletion_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_contract();
CREATE TRIGGER custody_deletion_events_retention_required
    BEFORE INSERT ON {schema}.custody_deletion_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_evidence_retention();
CREATE CONSTRAINT TRIGGER custody_deletion_events_chain_required
    AFTER INSERT ON {schema}.custody_deletion_events
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_audit_chain_entry();

DROP TRIGGER IF EXISTS custody_control_events_immutable ON {schema}.custody_control_events;
CREATE TRIGGER custody_control_events_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_control_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_evidence_mutation();
DROP TRIGGER IF EXISTS custody_execution_attempts_immutable ON {schema}.custody_execution_attempts;
CREATE TRIGGER custody_execution_attempts_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_execution_attempts
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_evidence_mutation();
DROP TRIGGER IF EXISTS custody_execution_events_immutable ON {schema}.custody_execution_events;
CREATE TRIGGER custody_execution_events_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_execution_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_evidence_mutation();
CREATE TRIGGER custody_deletion_events_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_deletion_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_evidence_mutation();

ALTER TABLE {schema}.custody_deletion_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_deletion_events FORCE ROW LEVEL SECURITY;
CREATE POLICY custody_deletion_events_organization_isolation
    ON {schema}.custody_deletion_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

REVOKE ALL ON FUNCTION {schema}.custody_audit_chain_next_position(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_audit_chain_next_position(TEXT)
    TO {custody_control_role}, {custody_executor_role};
REVOKE ALL ON FUNCTION {schema}.custody_audit_chain_append_entry(TEXT, TEXT, INTEGER, TEXT, INTEGER, BIGINT, TEXT, TEXT, TEXT)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_audit_chain_append_entry(TEXT, TEXT, INTEGER, TEXT, INTEGER, BIGINT, TEXT, TEXT, TEXT)
    TO {custody_control_role}, {custody_executor_role};
REVOKE ALL ON FUNCTION {schema}.custody_audit_chain_source_event_json(TEXT, TEXT, INTEGER, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_audit_chain_source_event_json(TEXT, TEXT, INTEGER, TEXT)
    TO {runtime_role}, {custody_control_role}, {custody_executor_role};
REVOKE ALL ON FUNCTION {schema}.custody_audit_chain_export_entries(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_audit_chain_export_entries(TEXT)
    TO {custody_control_role};
REVOKE ALL ON FUNCTION {schema}.custody_audit_chain_source_retention(TEXT, TEXT, INTEGER, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_audit_chain_source_retention(TEXT, TEXT, INTEGER, TEXT)
    TO {custody_control_role};

GRANT SELECT, INSERT ON {schema}.custody_deletion_events TO {custody_control_role};

-- The ordinary runtime can read only the derived projection and the bounded
-- source-verifier function. It has no custody-source DELETE/UPDATE authority,
-- no retention-shortening capability, and no append function.
REVOKE ALL PRIVILEGES ON {schema}.custody_control_events FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.custody_execution_attempts FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.custody_execution_events FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.custody_lifecycle_events FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.custody_envelope_attestations FROM {runtime_role};
REVOKE ALL PRIVILEGES ON {schema}.custody_deletion_events FROM {runtime_role};
