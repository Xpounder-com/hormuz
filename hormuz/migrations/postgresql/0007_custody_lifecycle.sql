-- Governed destructive custody lifecycle. The executor may append an exact,
-- approved event but cannot edit the derived runtime projection, a historical
-- event, customer KMS policy, or a provider credential outside Hormuz.

ALTER TABLE {schema}.custody_execution_attempts
    DROP CONSTRAINT IF EXISTS custody_execution_attempts_execution_schema_version_check;
ALTER TABLE {schema}.custody_execution_attempts
    DROP CONSTRAINT IF EXISTS custody_execution_attempts_operation_type_check;
ALTER TABLE {schema}.custody_execution_attempts
    DROP CONSTRAINT IF EXISTS custody_execution_attempts_target_kind_check;
-- The v6 composite target-kind check was intentionally unnamed in that
-- migration, so PostgreSQL assigned this stable relation-derived name.
ALTER TABLE {schema}.custody_execution_attempts
    DROP CONSTRAINT IF EXISTS custody_execution_attempts_check;

ALTER TABLE {schema}.custody_execution_attempts
    ADD CONSTRAINT custody_execution_attempts_execution_schema_version_check
    CHECK (execution_schema_version IN (1, 2));
ALTER TABLE {schema}.custody_execution_attempts
    ADD CONSTRAINT custody_execution_attempts_operation_type_check
    CHECK (
        (execution_schema_version = 1 AND operation_type IN ('seal_envelope', 'rewrap_envelope', 'verify_restore'))
        OR (
            execution_schema_version = 2
            AND operation_type IN (
                'seal_envelope', 'rewrap_envelope', 'verify_restore',
                'retire_envelope', 'disable_provider_credential',
                'retire_key_reference', 'resolve_recovery'
            )
        )
    );
ALTER TABLE {schema}.custody_execution_attempts
    ADD CONSTRAINT custody_execution_attempts_target_kind_check
    CHECK (
        (operation_type IN ('seal_envelope', 'rewrap_envelope', 'retire_envelope') AND target_kind = 'envelope')
        OR (operation_type = 'verify_restore' AND target_kind = 'restore')
        OR (operation_type = 'disable_provider_credential' AND target_kind = 'provider_credential')
        OR (operation_type = 'retire_key_reference' AND target_kind = 'key_reference')
        OR (operation_type = 'resolve_recovery' AND target_kind = 'recovery')
    );

-- Version 2 claims are required for destructive operations. The exact intent,
-- requester, and every approval are checked again by the write boundary so a
-- revoked or otherwise inactive approver cannot be raced after authorization.
CREATE OR REPLACE FUNCTION {schema}.enforce_custody_execution_claim()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    active_approval_count INTEGER;
BEGIN
    PERFORM 1
    FROM {schema}.custody_operation_intents AS intent
    JOIN {schema}.custody_administrators AS requester
      ON requester.organization_id = intent.organization_id
     AND requester.identity_key = intent.requested_by_identity_key
    WHERE intent.organization_id = NEW.organization_id
      AND intent.operation_id = NEW.operation_id
      AND intent.state = 'authorized'
      AND intent.expires_at > clock_timestamp()
      AND requester.active = TRUE
      AND intent.operation_type = NEW.operation_type
      AND intent.target_kind = NEW.target_kind
      AND intent.target_sha256 = NEW.target_sha256
      AND intent.parameters_sha256 = NEW.parameters_sha256
      AND intent.protected_input_ref_sha256 IS NOT DISTINCT FROM NEW.protected_input_ref_sha256
      AND (
          (intent.risk_level = 'routine' AND NEW.execution_schema_version IN (1, 2))
          OR (intent.risk_level = 'destructive' AND NEW.execution_schema_version = 2)
      )
    FOR SHARE OF intent, requester;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'custody execution claim is not an exact active authorization';
    END IF;
    IF NEW.operation_type IN (
        'retire_envelope', 'disable_provider_credential', 'retire_key_reference', 'resolve_recovery'
    ) THEN
        SELECT COUNT(*) INTO active_approval_count
        FROM {schema}.custody_operation_approvals AS approval
        JOIN {schema}.custody_administrators AS administrator
          ON administrator.organization_id = approval.organization_id
         AND administrator.identity_key = approval.approver_identity_key
        WHERE approval.organization_id = NEW.organization_id
          AND approval.operation_id = NEW.operation_id
          AND administrator.active = TRUE;
        IF active_approval_count <> 2 THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'custody destructive execution requires two active distinct approvers';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- The executor must recheck that the two destructive approvers are still
-- active immediately before it claims an attempt. It receives only this
-- Boolean result, not a general read grant over approval history.
CREATE OR REPLACE FUNCTION {schema}.custody_execution_has_two_active_approvers(
    p_organization_id TEXT,
    p_operation_id TEXT
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT COUNT(*) = 2
    FROM {schema}.custody_operation_approvals AS approval
    JOIN {schema}.custody_administrators AS administrator
      ON administrator.organization_id = approval.organization_id
     AND administrator.identity_key = approval.approver_identity_key
    WHERE approval.organization_id = p_organization_id
      AND approval.operation_id = p_operation_id
      AND administrator.active = TRUE
$$;

CREATE TABLE IF NOT EXISTS {schema}.custody_lifecycle_asset_identities (
    organization_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    binding_fingerprint TEXT NOT NULL,
    envelope_key_asset_id TEXT,
    envelope_key_generation INTEGER,
    envelope_key_binding_fingerprint TEXT,
    registered_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, asset_type, asset_id, generation),
    CHECK (asset_type IN ('provider_credential', 'envelope', 'key_reference')),
    CHECK (binding_fingerprint ~ '^[0-9a-f]{{64}}$'),
    CHECK (
        (asset_type = 'envelope'
         AND envelope_key_asset_id IS NOT NULL AND envelope_key_generation >= 1
         AND envelope_key_binding_fingerprint ~ '^[0-9a-f]{{64}}$')
        OR
        (asset_type <> 'envelope'
         AND envelope_key_asset_id IS NULL AND envelope_key_generation IS NULL
         AND envelope_key_binding_fingerprint IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS {schema}.custody_lifecycle_chain_heads (
    organization_id TEXT PRIMARY KEY,
    chain_version INTEGER NOT NULL CHECK (chain_version = 1),
    sequence BIGINT NOT NULL CHECK (sequence >= 0),
    head_digest TEXT,
    committed_at TIMESTAMPTZ NOT NULL,
    CHECK ((sequence = 0 AND head_digest IS NULL) OR (sequence >= 1 AND head_digest ~ '^[0-9a-f]{{64}}$'))
);

-- Return and lock the next tenant chain position without granting the executor
-- UPDATE or SELECT ... FOR UPDATE access to the chain head table. The advisory
-- lock also serializes the first event, before a tenant head exists.
CREATE OR REPLACE FUNCTION {schema}.custody_lifecycle_next_chain_head(
    p_organization_id TEXT
)
RETURNS TABLE(next_sequence BIGINT, previous_digest TEXT)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    current_sequence BIGINT;
    current_digest TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:custody-lifecycle:' || p_organization_id));
    SELECT sequence, head_digest INTO current_sequence, current_digest
    FROM {schema}.custody_lifecycle_chain_heads
    WHERE organization_id = p_organization_id
    FOR UPDATE;
    IF FOUND THEN
        RETURN QUERY SELECT current_sequence + 1, current_digest;
    ELSE
        RETURN QUERY SELECT 1::BIGINT, NULL::TEXT;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS {schema}.custody_lifecycle_events (
    organization_id TEXT NOT NULL,
    lifecycle_event_id TEXT NOT NULL,
    lifecycle_schema_id TEXT NOT NULL,
    lifecycle_schema_version INTEGER NOT NULL,
    execution_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    operation_type TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    parameters_sha256 TEXT NOT NULL,
    asset_type TEXT,
    asset_id TEXT,
    asset_generation INTEGER,
    asset_binding_fingerprint TEXT,
    replacement_asset_type TEXT,
    replacement_asset_id TEXT,
    replacement_asset_generation INTEGER,
    replacement_asset_binding_fingerprint TEXT,
    recovery_execution_id TEXT,
    recovery_resolution_code TEXT,
    chain_version INTEGER NOT NULL,
    sequence BIGINT NOT NULL,
    previous_digest TEXT,
    event_digest TEXT NOT NULL,
    PRIMARY KEY (organization_id, lifecycle_event_id),
    UNIQUE (organization_id, execution_id),
    UNIQUE (organization_id, chain_version, sequence),
    FOREIGN KEY (organization_id, execution_id)
        REFERENCES {schema}.custody_execution_attempts (organization_id, execution_id),
    CHECK (lifecycle_schema_id = 'hormuz.custody-lifecycle-event'),
    CHECK (lifecycle_schema_version = 1),
    CHECK (operation_type IN ('retire_envelope', 'disable_provider_credential', 'retire_key_reference', 'resolve_recovery')),
    CHECK (target_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (parameters_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (chain_version = 1 AND sequence >= 1),
    CHECK (previous_digest IS NULL OR previous_digest ~ '^[0-9a-f]{{64}}$'),
    CHECK (event_digest ~ '^[0-9a-f]{{64}}$'),
    CHECK (
        (operation_type = 'resolve_recovery'
         AND asset_type IS NULL AND asset_id IS NULL AND asset_generation IS NULL AND asset_binding_fingerprint IS NULL
         AND replacement_asset_type IS NULL AND replacement_asset_id IS NULL
         AND replacement_asset_generation IS NULL AND replacement_asset_binding_fingerprint IS NULL
         AND recovery_execution_id IS NOT NULL
         AND recovery_resolution_code IN ('confirmed_applied', 'confirmed_not_applied', 'compensating_action_completed'))
        OR
        (operation_type <> 'resolve_recovery'
         AND asset_type IN ('provider_credential', 'envelope', 'key_reference')
         AND asset_id IS NOT NULL AND asset_generation >= 1
         AND asset_binding_fingerprint ~ '^[0-9a-f]{{64}}$'
         AND recovery_execution_id IS NULL AND recovery_resolution_code IS NULL)
    ),
    CHECK (
        (operation_type = 'retire_key_reference'
         AND asset_type = 'key_reference'
         AND replacement_asset_type = 'key_reference'
         AND replacement_asset_id IS NOT NULL AND replacement_asset_generation >= 1
         AND replacement_asset_binding_fingerprint ~ '^[0-9a-f]{{64}}$')
        OR
        (operation_type <> 'retire_key_reference'
         AND replacement_asset_type IS NULL AND replacement_asset_id IS NULL
         AND replacement_asset_generation IS NULL AND replacement_asset_binding_fingerprint IS NULL)
    ),
    CHECK (
        (operation_type = 'disable_provider_credential' AND asset_type = 'provider_credential')
        OR (operation_type = 'retire_envelope' AND asset_type = 'envelope')
        OR (operation_type = 'retire_key_reference' AND asset_type = 'key_reference')
        OR operation_type = 'resolve_recovery'
    )
);

CREATE INDEX IF NOT EXISTS idx_custody_lifecycle_events_history
    ON {schema}.custody_lifecycle_events (organization_id, occurred_at DESC, lifecycle_event_id);

CREATE TABLE IF NOT EXISTS {schema}.custody_runtime_projection_heads (
    organization_id TEXT PRIMARY KEY,
    projection_schema_id TEXT NOT NULL CHECK (projection_schema_id = 'hormuz.custody-runtime-projection'),
    projection_schema_version INTEGER NOT NULL CHECK (projection_schema_version = 1),
    version BIGINT NOT NULL CHECK (version >= 0),
    committed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS {schema}.custody_runtime_projection_restrictions (
    organization_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    binding_fingerprint TEXT NOT NULL,
    restriction_kind TEXT NOT NULL,
    lifecycle_event_id TEXT NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, asset_type, asset_id, generation),
    UNIQUE (organization_id, lifecycle_event_id),
    FOREIGN KEY (organization_id, lifecycle_event_id)
        REFERENCES {schema}.custody_lifecycle_events (organization_id, lifecycle_event_id),
    CHECK (asset_type IN ('provider_credential', 'envelope', 'key_reference')),
    CHECK (binding_fingerprint ~ '^[0-9a-f]{{64}}$'),
    CHECK (restriction_kind IN ('provider_credential_disabled', 'envelope_retired', 'key_reference_write_retired')),
    CHECK (
        (asset_type = 'provider_credential' AND restriction_kind = 'provider_credential_disabled')
        OR (asset_type = 'envelope' AND restriction_kind = 'envelope_retired')
        OR (asset_type = 'key_reference' AND restriction_kind = 'key_reference_write_retired')
    )
);

-- Every gateway process receives a new opaque replica UUID. A successful
-- synchronization grants at most five seconds of local admission authority;
-- the client starts its shorter monotonic lease before making this call, so a
-- response delayed in the network cannot extend its serving window.
CREATE TABLE IF NOT EXISTS {schema}.custody_runtime_replicas (
    organization_id TEXT NOT NULL,
    replica_id UUID NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL,
    heartbeat_at TIMESTAMPTZ NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    observed_projection_version BIGINT NOT NULL CHECK (observed_projection_version >= 0),
    retired_at TIMESTAMPTZ,
    PRIMARY KEY (organization_id, replica_id),
    CHECK (lease_expires_at >= heartbeat_at),
    CHECK (retired_at IS NULL OR retired_at >= registered_at)
);

-- A prepared barrier is internal coordination state, not lifecycle authority.
-- It becomes activated only in the same transaction that appends the exact
-- immutable lifecycle event and updates the derived projection.
CREATE TABLE IF NOT EXISTS {schema}.custody_runtime_projection_barriers (
    organization_id TEXT NOT NULL,
    barrier_id UUID NOT NULL,
    execution_id TEXT NOT NULL,
    proposed_version BIGINT NOT NULL CHECK (proposed_version >= 1),
    operation_type TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    parameters_sha256 TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    asset_generation INTEGER NOT NULL CHECK (asset_generation >= 1),
    asset_binding_fingerprint TEXT NOT NULL,
    restriction_kind TEXT NOT NULL,
    replacement_asset_type TEXT,
    replacement_asset_id TEXT,
    replacement_asset_generation INTEGER,
    replacement_asset_binding_fingerprint TEXT,
    prepared_at TIMESTAMPTZ NOT NULL,
    activated_at TIMESTAMPTZ,
    lifecycle_event_id TEXT,
    resolved_at TIMESTAMPTZ,
    resolution_lifecycle_event_id TEXT,
    PRIMARY KEY (organization_id, barrier_id),
    UNIQUE (organization_id, execution_id),
    UNIQUE (organization_id, proposed_version),
    FOREIGN KEY (organization_id, execution_id)
        REFERENCES {schema}.custody_execution_attempts (organization_id, execution_id),
    FOREIGN KEY (organization_id, lifecycle_event_id)
        REFERENCES {schema}.custody_lifecycle_events (organization_id, lifecycle_event_id),
    FOREIGN KEY (organization_id, resolution_lifecycle_event_id)
        REFERENCES {schema}.custody_lifecycle_events (organization_id, lifecycle_event_id),
    CHECK (operation_type IN ('retire_envelope', 'disable_provider_credential', 'retire_key_reference')),
    CHECK (target_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (parameters_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (asset_type IN ('provider_credential', 'envelope', 'key_reference')),
    CHECK (asset_binding_fingerprint ~ '^[0-9a-f]{{64}}$'),
    CHECK (restriction_kind IN ('provider_credential_disabled', 'envelope_retired', 'key_reference_write_retired')),
    CHECK (
        (operation_type = 'disable_provider_credential'
         AND asset_type = 'provider_credential' AND restriction_kind = 'provider_credential_disabled')
        OR (operation_type = 'retire_envelope'
            AND asset_type = 'envelope' AND restriction_kind = 'envelope_retired')
        OR (operation_type = 'retire_key_reference'
            AND asset_type = 'key_reference' AND restriction_kind = 'key_reference_write_retired')
    ),
    CHECK (
        (operation_type = 'retire_key_reference'
         AND replacement_asset_type = 'key_reference'
         AND replacement_asset_id IS NOT NULL AND replacement_asset_generation >= 1
         AND replacement_asset_binding_fingerprint ~ '^[0-9a-f]{{64}}$')
        OR
        (operation_type <> 'retire_key_reference'
         AND replacement_asset_type IS NULL AND replacement_asset_id IS NULL
         AND replacement_asset_generation IS NULL AND replacement_asset_binding_fingerprint IS NULL)
    ),
    CHECK (
        (activated_at IS NULL AND lifecycle_event_id IS NULL
         AND resolved_at IS NULL AND resolution_lifecycle_event_id IS NULL)
        OR (activated_at IS NOT NULL AND lifecycle_event_id IS NOT NULL
            AND resolved_at IS NULL AND resolution_lifecycle_event_id IS NULL)
        OR (activated_at IS NULL AND lifecycle_event_id IS NULL
            AND resolved_at IS NOT NULL AND resolution_lifecycle_event_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_custody_runtime_one_prepared_barrier
    ON {schema}.custody_runtime_projection_barriers (organization_id)
    WHERE activated_at IS NULL AND resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS {schema}.custody_runtime_projection_acks (
    organization_id TEXT NOT NULL,
    barrier_id UUID NOT NULL,
    replica_id UUID NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, barrier_id, replica_id),
    FOREIGN KEY (organization_id, barrier_id)
        REFERENCES {schema}.custody_runtime_projection_barriers (organization_id, barrier_id),
    FOREIGN KEY (organization_id, replica_id)
        REFERENCES {schema}.custody_runtime_replicas (organization_id, replica_id)
);

CREATE TABLE IF NOT EXISTS {schema}.custody_envelope_attestations (
    organization_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    attestation_kind TEXT NOT NULL,
    envelope_asset_id TEXT NOT NULL,
    envelope_generation INTEGER NOT NULL CHECK (envelope_generation >= 1),
    envelope_binding_fingerprint TEXT NOT NULL,
    source_key_asset_id TEXT,
    source_key_generation INTEGER,
    source_key_binding_fingerprint TEXT,
    destination_key_asset_id TEXT NOT NULL,
    destination_key_generation INTEGER NOT NULL CHECK (destination_key_generation >= 1),
    destination_key_binding_fingerprint TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, execution_id, attestation_kind),
    FOREIGN KEY (organization_id, execution_id)
        REFERENCES {schema}.custody_execution_attempts (organization_id, execution_id),
    CHECK (attestation_kind IN ('rewrapped', 'restore_verified')),
    CHECK (envelope_binding_fingerprint ~ '^[0-9a-f]{{64}}$'),
    CHECK (destination_key_binding_fingerprint ~ '^[0-9a-f]{{64}}$'),
    CHECK (
        (attestation_kind = 'rewrapped'
         AND source_key_asset_id IS NOT NULL AND source_key_generation >= 1
         AND source_key_binding_fingerprint ~ '^[0-9a-f]{{64}}$')
        OR
        (attestation_kind = 'restore_verified'
         AND source_key_asset_id IS NULL AND source_key_generation IS NULL AND source_key_binding_fingerprint IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_custody_envelope_attestations_retirement
    ON {schema}.custody_envelope_attestations (
        organization_id, envelope_asset_id, envelope_generation, attestation_kind, occurred_at
    );

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_lifecycle_asset_identity()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.asset_type <> 'envelope' THEN
        RETURN NEW;
    END IF;
    PERFORM 1
    FROM {schema}.custody_lifecycle_asset_identities AS key_asset
    WHERE key_asset.organization_id = NEW.organization_id
      AND key_asset.asset_type = 'key_reference'
      AND key_asset.asset_id = NEW.envelope_key_asset_id
      AND key_asset.generation = NEW.envelope_key_generation
      AND key_asset.binding_fingerprint = NEW.envelope_key_binding_fingerprint;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody envelope key identity is unknown';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_key_retirement_is_ready(
    p_organization_id TEXT,
    p_asset_id TEXT,
    p_asset_generation INTEGER,
    p_asset_binding_fingerprint TEXT,
    p_replacement_asset_id TEXT,
    p_replacement_asset_generation INTEGER,
    p_replacement_asset_binding_fingerprint TEXT
)
RETURNS BOOLEAN
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM 1
    FROM {schema}.custody_lifecycle_asset_identities AS replacement
    WHERE replacement.organization_id = p_organization_id
      AND replacement.asset_type = 'key_reference'
      AND replacement.asset_id = p_replacement_asset_id
      AND replacement.generation = p_replacement_asset_generation
      AND replacement.binding_fingerprint = p_replacement_asset_binding_fingerprint;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    PERFORM 1
    FROM {schema}.custody_runtime_projection_restrictions AS restriction
    WHERE restriction.organization_id = p_organization_id
      AND restriction.asset_type = 'key_reference'
      AND restriction.asset_id = p_replacement_asset_id
      AND restriction.generation = p_replacement_asset_generation;
    IF FOUND THEN
        RETURN FALSE;
    END IF;
    RETURN NOT EXISTS (
        SELECT 1
        FROM {schema}.custody_lifecycle_asset_identities AS envelope
        LEFT JOIN {schema}.custody_runtime_projection_restrictions AS retired_envelope
          ON retired_envelope.organization_id = envelope.organization_id
         AND retired_envelope.asset_type = envelope.asset_type
         AND retired_envelope.asset_id = envelope.asset_id
         AND retired_envelope.generation = envelope.generation
        WHERE envelope.organization_id = p_organization_id
          AND envelope.asset_type = 'envelope'
          AND envelope.envelope_key_asset_id = p_asset_id
          AND envelope.envelope_key_generation = p_asset_generation
          AND envelope.envelope_key_binding_fingerprint = p_asset_binding_fingerprint
          AND retired_envelope.asset_id IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM {schema}.custody_envelope_attestations AS rewrapped
              JOIN {schema}.custody_envelope_attestations AS restored
                ON restored.organization_id = rewrapped.organization_id
               AND restored.envelope_asset_id = rewrapped.envelope_asset_id
               AND restored.envelope_generation = rewrapped.envelope_generation
               AND restored.attestation_kind = 'restore_verified'
               AND restored.destination_key_asset_id = p_replacement_asset_id
               AND restored.destination_key_generation = p_replacement_asset_generation
               AND restored.destination_key_binding_fingerprint = p_replacement_asset_binding_fingerprint
               AND restored.occurred_at >= rewrapped.occurred_at
              WHERE rewrapped.organization_id = p_organization_id
                AND rewrapped.envelope_asset_id = envelope.asset_id
                AND rewrapped.envelope_generation = envelope.generation
                AND rewrapped.envelope_binding_fingerprint = envelope.binding_fingerprint
                AND rewrapped.attestation_kind = 'rewrapped'
                AND rewrapped.source_key_asset_id = p_asset_id
                AND rewrapped.source_key_generation = p_asset_generation
                AND rewrapped.source_key_binding_fingerprint = p_asset_binding_fingerprint
                AND rewrapped.destination_key_asset_id = p_replacement_asset_id
                AND rewrapped.destination_key_generation = p_replacement_asset_generation
                AND rewrapped.destination_key_binding_fingerprint = p_replacement_asset_binding_fingerprint
          )
    );
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_runtime_projection_barrier()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    current_version BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:custody-runtime:' || NEW.organization_id));
    PERFORM 1
    FROM {schema}.custody_execution_attempts AS attempt
    WHERE attempt.organization_id = NEW.organization_id
      AND attempt.execution_id = NEW.execution_id
      AND attempt.operation_type = NEW.operation_type
      AND attempt.target_sha256 = NEW.target_sha256
      AND attempt.parameters_sha256 = NEW.parameters_sha256
      AND NOT EXISTS (
          SELECT 1
          FROM {schema}.custody_execution_events AS terminal
          WHERE terminal.organization_id = attempt.organization_id
            AND terminal.execution_id = attempt.execution_id
            AND terminal.sequence = 2
      )
    FOR SHARE OF attempt;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody barrier requires an exact pending execution';
    END IF;
    PERFORM 1
    FROM {schema}.custody_lifecycle_asset_identities AS asset
    WHERE asset.organization_id = NEW.organization_id
      AND asset.asset_type = NEW.asset_type
      AND asset.asset_id = NEW.asset_id
      AND asset.generation = NEW.asset_generation
      AND asset.binding_fingerprint = NEW.asset_binding_fingerprint;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody barrier asset identity is unknown';
    END IF;
    PERFORM 1
    FROM {schema}.custody_runtime_projection_restrictions AS restriction
    WHERE restriction.organization_id = NEW.organization_id
      AND restriction.asset_type = NEW.asset_type
      AND restriction.asset_id = NEW.asset_id
      AND restriction.generation = NEW.asset_generation;
    IF FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody barrier asset is already restricted';
    END IF;
    IF NEW.operation_type = 'retire_key_reference' AND NOT {schema}.custody_key_retirement_is_ready(
        NEW.organization_id,
        NEW.asset_id,
        NEW.asset_generation,
        NEW.asset_binding_fingerprint,
        NEW.replacement_asset_id,
        NEW.replacement_asset_generation,
        NEW.replacement_asset_binding_fingerprint
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody barrier key retirement lacks replacement proof';
    END IF;
    SELECT version INTO current_version
    FROM {schema}.custody_runtime_projection_heads
    WHERE organization_id = NEW.organization_id
    FOR UPDATE;
    IF NOT FOUND THEN
        current_version := 0;
    END IF;
    IF NEW.proposed_version <> current_version + 1 THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody barrier projection version is invalid';
    END IF;
    PERFORM 1
    FROM {schema}.custody_runtime_projection_barriers AS barrier
    WHERE barrier.organization_id = NEW.organization_id
      AND barrier.activated_at IS NULL
      AND barrier.resolved_at IS NULL;
    IF FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody barrier coordination is already in progress';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.notify_custody_runtime_projection_change()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM pg_notify('hormuz_custody_projection', 'changed');
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_runtime_sync_replica(
    p_organization_id TEXT,
    p_replica_id UUID,
    p_observed_projection_version BIGINT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    synchronized_at TIMESTAMPTZ;
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody replica tenant context is invalid';
    END IF;
    IF p_observed_projection_version < 0 THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody replica projection version is invalid';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:custody-runtime:' || p_organization_id));
    synchronized_at := clock_timestamp();
    INSERT INTO {schema}.custody_runtime_replicas (
        organization_id, replica_id, registered_at, heartbeat_at,
        lease_expires_at, observed_projection_version, retired_at
    ) VALUES (
        p_organization_id, p_replica_id, synchronized_at, synchronized_at,
        synchronized_at + INTERVAL '5 seconds', p_observed_projection_version, NULL
    )
    ON CONFLICT (organization_id, replica_id) DO UPDATE
    SET heartbeat_at = EXCLUDED.heartbeat_at,
        lease_expires_at = EXCLUDED.lease_expires_at,
        observed_projection_version = EXCLUDED.observed_projection_version
    WHERE {schema}.custody_runtime_replicas.retired_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody replica identity is retired';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_runtime_ack_barrier(
    p_organization_id TEXT,
    p_replica_id UUID,
    p_barrier_id UUID,
    p_observed_projection_version BIGINT
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_acknowledged_at TIMESTAMPTZ;
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody replica tenant context is invalid';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:custody-runtime:' || p_organization_id));
    v_acknowledged_at := clock_timestamp();
    UPDATE {schema}.custody_runtime_replicas
    SET heartbeat_at = v_acknowledged_at,
        lease_expires_at = v_acknowledged_at + INTERVAL '5 seconds',
        observed_projection_version = p_observed_projection_version
    WHERE organization_id = p_organization_id
      AND replica_id = p_replica_id
      AND retired_at IS NULL
      AND lease_expires_at > v_acknowledged_at;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody replica lease is not active';
    END IF;
    PERFORM 1
    FROM {schema}.custody_runtime_projection_barriers AS barrier
    WHERE barrier.organization_id = p_organization_id
      AND barrier.barrier_id = p_barrier_id
      AND barrier.proposed_version = p_observed_projection_version + 1
      AND barrier.activated_at IS NULL
      AND barrier.resolved_at IS NULL;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'custody projection barrier does not match the observed projection';
    END IF;
    INSERT INTO {schema}.custody_runtime_projection_acks (
        organization_id, barrier_id, replica_id, acknowledged_at
    ) VALUES (
        p_organization_id, p_barrier_id, p_replica_id, v_acknowledged_at
    )
    ON CONFLICT (organization_id, barrier_id, replica_id) DO NOTHING;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.custody_runtime_retire_replica(
    p_organization_id TEXT,
    p_replica_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_retired_at TIMESTAMPTZ;
BEGIN
    IF current_setting('hormuz.organization_id', true) IS DISTINCT FROM p_organization_id THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody replica tenant context is invalid';
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:custody-runtime:' || p_organization_id));
    v_retired_at := clock_timestamp();
    UPDATE {schema}.custody_runtime_replicas
    SET heartbeat_at = v_retired_at,
        lease_expires_at = v_retired_at,
        retired_at = v_retired_at
    WHERE organization_id = p_organization_id
      AND replica_id = p_replica_id
      AND retired_at IS NULL;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_lifecycle_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    current_sequence BIGINT;
    current_digest TEXT;
    current_projection_version BIGINT;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('hormuz:custody-runtime:' || NEW.organization_id));
    PERFORM 1
    FROM {schema}.custody_execution_attempts AS attempt
    JOIN {schema}.custody_execution_events AS terminal
      ON terminal.organization_id = attempt.organization_id
     AND terminal.execution_id = attempt.execution_id
     AND terminal.sequence = 2
    WHERE attempt.organization_id = NEW.organization_id
      AND attempt.execution_id = NEW.execution_id
      AND attempt.operation_id = NEW.operation_id
      AND attempt.operation_type = NEW.operation_type
      AND attempt.target_sha256 = NEW.target_sha256
      AND attempt.parameters_sha256 = NEW.parameters_sha256
      AND terminal.state = 'succeeded'
    FOR SHARE OF attempt, terminal;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle event requires a succeeded exact execution';
    END IF;
    IF NEW.operation_type = 'resolve_recovery' THEN
        PERFORM 1
        FROM {schema}.custody_execution_events AS unknown_attempt
        WHERE unknown_attempt.organization_id = NEW.organization_id
          AND unknown_attempt.execution_id = NEW.recovery_execution_id
          AND unknown_attempt.sequence = 2
          AND unknown_attempt.state = 'outcome_unknown';
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody recovery resolution requires an unknown attempt';
        END IF;
        PERFORM 1
        FROM {schema}.custody_runtime_projection_barriers AS barrier
        WHERE barrier.organization_id = NEW.organization_id
          AND barrier.activated_at IS NULL
          AND barrier.resolved_at IS NULL
          AND barrier.execution_id <> NEW.recovery_execution_id;
        IF FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'another custody lifecycle coordination is in progress';
        END IF;
        PERFORM 1
        FROM {schema}.custody_runtime_projection_barriers AS barrier
        WHERE barrier.organization_id = NEW.organization_id
          AND barrier.execution_id = NEW.recovery_execution_id
          AND barrier.activated_at IS NULL
          AND barrier.resolved_at IS NULL;
        IF FOUND AND NEW.recovery_resolution_code = 'confirmed_applied' THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'a prepared Hormuz restriction cannot be resolved as already applied';
        END IF;
    ELSE
        PERFORM 1
        FROM {schema}.custody_lifecycle_asset_identities AS asset
        WHERE asset.organization_id = NEW.organization_id
          AND asset.asset_type = NEW.asset_type
          AND asset.asset_id = NEW.asset_id
          AND asset.generation = NEW.asset_generation
          AND asset.binding_fingerprint = NEW.asset_binding_fingerprint;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle asset identity is unknown';
        END IF;
        SELECT version INTO current_projection_version
        FROM {schema}.custody_runtime_projection_heads
        WHERE organization_id = NEW.organization_id
        FOR UPDATE;
        IF NOT FOUND THEN
            current_projection_version := 0;
        END IF;
        PERFORM 1
        FROM {schema}.custody_runtime_projection_barriers AS barrier
        WHERE barrier.organization_id = NEW.organization_id
          AND barrier.execution_id = NEW.execution_id
          AND barrier.proposed_version = current_projection_version + 1
          AND barrier.operation_type = NEW.operation_type
          AND barrier.target_sha256 = NEW.target_sha256
          AND barrier.parameters_sha256 = NEW.parameters_sha256
          AND barrier.asset_type = NEW.asset_type
          AND barrier.asset_id = NEW.asset_id
          AND barrier.asset_generation = NEW.asset_generation
          AND barrier.asset_binding_fingerprint = NEW.asset_binding_fingerprint
          AND barrier.replacement_asset_type IS NOT DISTINCT FROM NEW.replacement_asset_type
          AND barrier.replacement_asset_id IS NOT DISTINCT FROM NEW.replacement_asset_id
          AND barrier.replacement_asset_generation IS NOT DISTINCT FROM NEW.replacement_asset_generation
          AND barrier.replacement_asset_binding_fingerprint IS NOT DISTINCT FROM NEW.replacement_asset_binding_fingerprint
          AND barrier.activated_at IS NULL
          AND barrier.resolved_at IS NULL
        FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle event requires its exact prepared barrier';
        END IF;
        PERFORM 1
        FROM {schema}.custody_runtime_replicas AS replica
        WHERE replica.organization_id = NEW.organization_id
          AND replica.retired_at IS NULL
          AND replica.lease_expires_at > clock_timestamp()
          AND NOT EXISTS (
              SELECT 1
              FROM {schema}.custody_runtime_projection_acks AS acknowledgement
              JOIN {schema}.custody_runtime_projection_barriers AS barrier
                ON barrier.organization_id = acknowledgement.organization_id
               AND barrier.barrier_id = acknowledgement.barrier_id
              WHERE acknowledgement.organization_id = replica.organization_id
                AND acknowledgement.replica_id = replica.replica_id
                AND barrier.execution_id = NEW.execution_id
                AND barrier.activated_at IS NULL
                AND barrier.resolved_at IS NULL
          );
        IF FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '40001', MESSAGE = 'custody lifecycle coordination acknowledgements are pending';
        END IF;
    END IF;
    IF NEW.operation_type = 'retire_key_reference' AND NOT {schema}.custody_key_retirement_is_ready(
        NEW.organization_id,
        NEW.asset_id,
        NEW.asset_generation,
        NEW.asset_binding_fingerprint,
        NEW.replacement_asset_id,
        NEW.replacement_asset_generation,
        NEW.replacement_asset_binding_fingerprint
    ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle key retirement lacks rewrap and restore evidence';
    END IF;
    SELECT head.sequence, head.head_digest INTO current_sequence, current_digest
    FROM {schema}.custody_lifecycle_chain_heads
    AS head
    WHERE organization_id = NEW.organization_id
    FOR UPDATE;
    IF NOT FOUND THEN
        IF NEW.sequence <> 1 OR NEW.previous_digest IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle chain position is invalid';
        END IF;
        INSERT INTO {schema}.custody_lifecycle_chain_heads (
            organization_id, chain_version, sequence, head_digest, committed_at
        ) VALUES (NEW.organization_id, 1, 0, NULL, NEW.occurred_at);
    ELSIF NEW.sequence <> current_sequence + 1 OR NEW.previous_digest IS DISTINCT FROM current_digest THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle chain predecessor is invalid';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_envelope_attestation()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM 1
    FROM {schema}.custody_execution_attempts AS attempt
    JOIN {schema}.custody_execution_events AS terminal
      ON terminal.organization_id = attempt.organization_id
     AND terminal.execution_id = attempt.execution_id
     AND terminal.sequence = 2
    WHERE attempt.organization_id = NEW.organization_id
      AND attempt.execution_id = NEW.execution_id
      AND terminal.state = 'succeeded'
      AND (
          (NEW.attestation_kind = 'rewrapped' AND attempt.operation_type = 'rewrap_envelope')
          OR (NEW.attestation_kind = 'restore_verified' AND attempt.operation_type = 'verify_restore')
      )
    FOR SHARE OF attempt, terminal;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody envelope attestation requires a succeeded routine execution';
    END IF;
    PERFORM 1
    FROM {schema}.custody_lifecycle_asset_identities AS envelope
    WHERE envelope.organization_id = NEW.organization_id
      AND envelope.asset_type = 'envelope'
      AND envelope.asset_id = NEW.envelope_asset_id
      AND envelope.generation = NEW.envelope_generation
      AND envelope.binding_fingerprint = NEW.envelope_binding_fingerprint;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody envelope attestation identity is unknown';
    END IF;
    PERFORM 1
    FROM {schema}.custody_lifecycle_asset_identities AS destination
    WHERE destination.organization_id = NEW.organization_id
      AND destination.asset_type = 'key_reference'
      AND destination.asset_id = NEW.destination_key_asset_id
      AND destination.generation = NEW.destination_key_generation
      AND destination.binding_fingerprint = NEW.destination_key_binding_fingerprint;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody destination key attestation identity is unknown';
    END IF;
    IF NEW.attestation_kind = 'rewrapped' THEN
        PERFORM 1
        FROM {schema}.custody_lifecycle_asset_identities AS source
        WHERE source.organization_id = NEW.organization_id
          AND source.asset_type = 'key_reference'
          AND source.asset_id = NEW.source_key_asset_id
          AND source.generation = NEW.source_key_generation
          AND source.binding_fingerprint = NEW.source_key_binding_fingerprint;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody source key attestation identity is unknown';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.project_custody_lifecycle_event()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    restriction_kind TEXT;
BEGIN
    UPDATE {schema}.custody_lifecycle_chain_heads
    SET sequence = NEW.sequence,
        head_digest = NEW.event_digest,
        committed_at = NEW.occurred_at
    WHERE organization_id = NEW.organization_id
      AND chain_version = NEW.chain_version
      AND sequence = NEW.sequence - 1
      AND head_digest IS NOT DISTINCT FROM NEW.previous_digest;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody lifecycle chain head update conflict';
    END IF;
    INSERT INTO {schema}.custody_runtime_projection_heads (
        organization_id, projection_schema_id, projection_schema_version, version, committed_at
    ) VALUES (NEW.organization_id, 'hormuz.custody-runtime-projection', 1, 0, NEW.occurred_at)
    ON CONFLICT (organization_id) DO NOTHING;
    IF NEW.operation_type = 'disable_provider_credential' THEN
        restriction_kind := 'provider_credential_disabled';
    ELSIF NEW.operation_type = 'retire_envelope' THEN
        restriction_kind := 'envelope_retired';
    ELSIF NEW.operation_type = 'retire_key_reference' THEN
        restriction_kind := 'key_reference_write_retired';
    END IF;
    IF restriction_kind IS NOT NULL THEN
        INSERT INTO {schema}.custody_runtime_projection_restrictions (
            organization_id, asset_type, asset_id, generation, binding_fingerprint,
            restriction_kind, lifecycle_event_id, committed_at
        ) VALUES (
            NEW.organization_id, NEW.asset_type, NEW.asset_id, NEW.asset_generation,
            NEW.asset_binding_fingerprint, restriction_kind, NEW.lifecycle_event_id, NEW.occurred_at
        );
    END IF;
    UPDATE {schema}.custody_runtime_projection_heads
    SET version = version + 1, committed_at = NEW.occurred_at
    WHERE organization_id = NEW.organization_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody runtime projection update conflict';
    END IF;
    IF restriction_kind IS NOT NULL THEN
        UPDATE {schema}.custody_runtime_projection_barriers
        SET activated_at = NEW.occurred_at,
            lifecycle_event_id = NEW.lifecycle_event_id
        WHERE organization_id = NEW.organization_id
          AND execution_id = NEW.execution_id
          AND activated_at IS NULL
          AND resolved_at IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'custody runtime barrier activation conflict';
        END IF;
    ELSIF NEW.operation_type = 'resolve_recovery'
          AND NEW.recovery_resolution_code IN ('confirmed_not_applied', 'compensating_action_completed') THEN
        UPDATE {schema}.custody_runtime_projection_barriers
        SET resolved_at = NEW.occurred_at,
            resolution_lifecycle_event_id = NEW.lifecycle_event_id
        WHERE organization_id = NEW.organization_id
          AND execution_id = NEW.recovery_execution_id
          AND activated_at IS NULL
          AND resolved_at IS NULL;
    END IF;
    PERFORM pg_notify('hormuz_custody_projection', 'changed');
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.deny_custody_lifecycle_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'custody lifecycle evidence is immutable';
END;
$$;

DROP TRIGGER IF EXISTS custody_lifecycle_event_exact_execution ON {schema}.custody_lifecycle_events;
CREATE TRIGGER custody_lifecycle_event_exact_execution
    BEFORE INSERT ON {schema}.custody_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_lifecycle_event();
DROP TRIGGER IF EXISTS custody_lifecycle_event_projects_runtime ON {schema}.custody_lifecycle_events;
CREATE TRIGGER custody_lifecycle_event_projects_runtime
    AFTER INSERT ON {schema}.custody_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.project_custody_lifecycle_event();
DROP TRIGGER IF EXISTS custody_runtime_projection_barrier_exact_execution
    ON {schema}.custody_runtime_projection_barriers;
CREATE TRIGGER custody_runtime_projection_barrier_exact_execution
    BEFORE INSERT ON {schema}.custody_runtime_projection_barriers
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_runtime_projection_barrier();
DROP TRIGGER IF EXISTS custody_runtime_projection_barrier_notifies_replicas
    ON {schema}.custody_runtime_projection_barriers;
CREATE TRIGGER custody_runtime_projection_barrier_notifies_replicas
    AFTER INSERT ON {schema}.custody_runtime_projection_barriers
    FOR EACH ROW EXECUTE FUNCTION {schema}.notify_custody_runtime_projection_change();
DROP TRIGGER IF EXISTS custody_lifecycle_asset_identity_exact_binding ON {schema}.custody_lifecycle_asset_identities;
CREATE TRIGGER custody_lifecycle_asset_identity_exact_binding
    BEFORE INSERT ON {schema}.custody_lifecycle_asset_identities
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_lifecycle_asset_identity();
DROP TRIGGER IF EXISTS custody_envelope_attestation_exact_execution ON {schema}.custody_envelope_attestations;
CREATE TRIGGER custody_envelope_attestation_exact_execution
    BEFORE INSERT ON {schema}.custody_envelope_attestations
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_envelope_attestation();

DROP TRIGGER IF EXISTS custody_lifecycle_events_immutable ON {schema}.custody_lifecycle_events;
CREATE TRIGGER custody_lifecycle_events_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_lifecycle_mutation();
DROP TRIGGER IF EXISTS custody_lifecycle_asset_identities_immutable ON {schema}.custody_lifecycle_asset_identities;
CREATE TRIGGER custody_lifecycle_asset_identities_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_lifecycle_asset_identities
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_lifecycle_mutation();
DROP TRIGGER IF EXISTS custody_envelope_attestations_immutable ON {schema}.custody_envelope_attestations;
CREATE TRIGGER custody_envelope_attestations_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_envelope_attestations
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_lifecycle_mutation();
DROP TRIGGER IF EXISTS custody_runtime_projection_restrictions_immutable ON {schema}.custody_runtime_projection_restrictions;
CREATE TRIGGER custody_runtime_projection_restrictions_immutable
    BEFORE UPDATE OR DELETE ON {schema}.custody_runtime_projection_restrictions
    FOR EACH ROW EXECUTE FUNCTION {schema}.deny_custody_lifecycle_mutation();

ALTER TABLE {schema}.custody_lifecycle_asset_identities ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_lifecycle_asset_identities FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_lifecycle_chain_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_lifecycle_chain_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_lifecycle_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_lifecycle_events FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_restrictions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_restrictions FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_replicas ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_replicas FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_barriers ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_barriers FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_acks ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_runtime_projection_acks FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_envelope_attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_envelope_attestations FORCE ROW LEVEL SECURITY;

CREATE POLICY custody_lifecycle_asset_identities_organization_isolation
    ON {schema}.custody_lifecycle_asset_identities
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_lifecycle_chain_heads_organization_isolation
    ON {schema}.custody_lifecycle_chain_heads
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_lifecycle_events_organization_isolation
    ON {schema}.custody_lifecycle_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_runtime_projection_heads_organization_isolation
    ON {schema}.custody_runtime_projection_heads
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_runtime_projection_restrictions_organization_isolation
    ON {schema}.custody_runtime_projection_restrictions
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_runtime_replicas_organization_isolation
    ON {schema}.custody_runtime_replicas
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_runtime_projection_barriers_organization_isolation
    ON {schema}.custody_runtime_projection_barriers
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_runtime_projection_acks_organization_isolation
    ON {schema}.custody_runtime_projection_acks
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_envelope_attestations_organization_isolation
    ON {schema}.custody_envelope_attestations
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

-- The ordinary gateway can read a projection but cannot update it, append
-- lifecycle evidence, or alter the immutable custody ledger.
GRANT SELECT ON {schema}.custody_lifecycle_asset_identities TO {runtime_role};
GRANT SELECT ON {schema}.custody_runtime_projection_heads TO {runtime_role};
GRANT SELECT ON {schema}.custody_runtime_projection_restrictions TO {runtime_role};
GRANT SELECT ON {schema}.custody_runtime_projection_barriers TO {runtime_role};
GRANT SELECT ON {schema}.custody_runtime_replicas TO {runtime_role};
GRANT SELECT ON {schema}.custody_runtime_projection_acks TO {runtime_role};
REVOKE ALL ON FUNCTION {schema}.custody_runtime_sync_replica(TEXT, UUID, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_runtime_sync_replica(TEXT, UUID, BIGINT) TO {runtime_role};
REVOKE ALL ON FUNCTION {schema}.custody_runtime_ack_barrier(TEXT, UUID, UUID, BIGINT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_runtime_ack_barrier(TEXT, UUID, UUID, BIGINT) TO {runtime_role};
REVOKE ALL ON FUNCTION {schema}.custody_runtime_retire_replica(TEXT, UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_runtime_retire_replica(TEXT, UUID) TO {runtime_role};

-- The executor can register configured asset identities and append immutable
-- evidence. SECURITY DEFINER triggers own the mutable heads and projection.
GRANT SELECT, INSERT ON {schema}.custody_lifecycle_asset_identities TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_lifecycle_chain_heads TO {custody_executor_role};
GRANT SELECT, INSERT ON {schema}.custody_lifecycle_events TO {custody_executor_role};
GRANT SELECT, INSERT ON {schema}.custody_envelope_attestations TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_runtime_projection_heads TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_runtime_projection_restrictions TO {custody_executor_role};
GRANT SELECT, INSERT ON {schema}.custody_runtime_projection_barriers TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_runtime_replicas TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_runtime_projection_acks TO {custody_executor_role};
REVOKE ALL ON FUNCTION {schema}.custody_execution_has_two_active_approvers(TEXT, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_execution_has_two_active_approvers(TEXT, TEXT) TO {custody_executor_role};
REVOKE ALL ON FUNCTION {schema}.custody_lifecycle_next_chain_head(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {schema}.custody_lifecycle_next_chain_head(TEXT) TO {custody_executor_role};

-- Custody administrators can inspect the content-free history but never edit
-- a projection or use it to resurrect an asset generation.
GRANT SELECT ON {schema}.custody_lifecycle_asset_identities TO {custody_control_role};
GRANT SELECT ON {schema}.custody_lifecycle_chain_heads TO {custody_control_role};
GRANT SELECT ON {schema}.custody_lifecycle_events TO {custody_control_role};
GRANT SELECT ON {schema}.custody_runtime_projection_heads TO {custody_control_role};
GRANT SELECT ON {schema}.custody_runtime_projection_restrictions TO {custody_control_role};
GRANT SELECT ON {schema}.custody_runtime_projection_barriers TO {custody_control_role};
GRANT SELECT ON {schema}.custody_runtime_replicas TO {custody_control_role};
GRANT SELECT ON {schema}.custody_runtime_projection_acks TO {custody_control_role};
GRANT SELECT ON {schema}.custody_envelope_attestations TO {custody_control_role};

REVOKE ALL ON FUNCTION {schema}.custody_key_retirement_is_ready(TEXT, TEXT, INTEGER, TEXT, TEXT, INTEGER, TEXT)
    FROM PUBLIC;
