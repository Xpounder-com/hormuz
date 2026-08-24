-- Durable routine-custody execution attempts.  A distinct executor role can
-- consume only metadata-only authorization facts and write its own immutable
-- attempt/event ledger.  It cannot alter custody authority, customer key
-- policy, retained objects, or application usage evidence.

CREATE TABLE IF NOT EXISTS {schema}.custody_execution_attempts (
    organization_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    execution_schema_id TEXT NOT NULL,
    execution_schema_version INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    parameters_sha256 TEXT NOT NULL,
    protected_input_ref_sha256 TEXT,
    claimed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, execution_id),
    UNIQUE (organization_id, operation_id),
    FOREIGN KEY (organization_id, operation_id)
        REFERENCES {schema}.custody_operation_intents (organization_id, operation_id),
    CHECK (execution_schema_id = 'hormuz.custody-execution-attempt'),
    CHECK (execution_schema_version = 1),
    CHECK (operation_type IN ('seal_envelope', 'rewrap_envelope', 'verify_restore')),
    CHECK (
        (operation_type IN ('seal_envelope', 'rewrap_envelope') AND target_kind = 'envelope')
        OR (operation_type = 'verify_restore' AND target_kind = 'restore')
    ),
    CHECK (target_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (parameters_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (
        (operation_type = 'seal_envelope' AND protected_input_ref_sha256 ~ '^[0-9a-f]{{64}}$')
        OR (operation_type <> 'seal_envelope' AND protected_input_ref_sha256 IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_custody_execution_attempts_history
    ON {schema}.custody_execution_attempts (organization_id, claimed_at DESC, execution_id);

CREATE TABLE IF NOT EXISTS {schema}.custody_execution_events (
    organization_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_schema_id TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL,
    reason_code TEXT,
    PRIMARY KEY (organization_id, execution_id, sequence),
    FOREIGN KEY (organization_id, execution_id)
        REFERENCES {schema}.custody_execution_attempts (organization_id, execution_id),
    CHECK (event_schema_id = 'hormuz.custody-execution-event'),
    CHECK (event_schema_version = 1),
    CHECK (
        (sequence = 1 AND state = 'pending' AND reason_code IS NULL)
        OR (sequence = 2 AND state = 'succeeded' AND reason_code IS NULL)
        OR (sequence = 2 AND state = 'failed' AND reason_code = 'execution_failed')
        OR (
            sequence = 2 AND state = 'outcome_unknown'
            AND reason_code IN ('external_result_ambiguous', 'stale_pending')
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_custody_execution_events_history
    ON {schema}.custody_execution_events (organization_id, occurred_at DESC, execution_id, sequence);

-- The executor service performs the human-authorization check before it
-- begins a side effect. Keep the same exact-match requirement at the write
-- boundary so its database credential cannot manufacture a mismatched ledger
-- claim through a raw INSERT. This is deliberately an authorization *check*,
-- not a grant of custody-control authority to the executor role.
CREATE OR REPLACE FUNCTION {schema}.enforce_custody_execution_claim()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
BEGIN
    PERFORM 1
    FROM {schema}.custody_operation_intents AS intent
    JOIN {schema}.custody_administrators AS requester
      ON requester.organization_id = intent.organization_id
     AND requester.identity_key = intent.requested_by_identity_key
    WHERE intent.organization_id = NEW.organization_id
      AND intent.operation_id = NEW.operation_id
      AND intent.state = 'authorized'
      AND intent.risk_level = 'routine'
      AND intent.expires_at > clock_timestamp()
      AND requester.active = TRUE
      AND intent.operation_type = NEW.operation_type
      AND intent.target_kind = NEW.target_kind
      AND intent.target_sha256 = NEW.target_sha256
      AND intent.parameters_sha256 = NEW.parameters_sha256
      AND intent.protected_input_ref_sha256 IS NOT DISTINCT FROM NEW.protected_input_ref_sha256
    FOR SHARE OF intent, requester;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'custody execution claim is not an exact active routine authorization';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION {schema}.enforce_custody_execution_event()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_operation_id TEXT;
BEGIN
    SELECT operation_id INTO expected_operation_id
    FROM {schema}.custody_execution_attempts
    WHERE organization_id = NEW.organization_id
      AND execution_id = NEW.execution_id;
    IF NOT FOUND OR expected_operation_id <> NEW.operation_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'custody execution event does not match its attempt';
    END IF;
    IF NEW.sequence = 2 THEN
        PERFORM 1
        FROM {schema}.custody_execution_events
        WHERE organization_id = NEW.organization_id
          AND execution_id = NEW.execution_id
          AND sequence = 1
          AND state = 'pending';
        IF NOT FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = '23514',
                MESSAGE = 'custody execution terminal event requires a pending event';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS custody_execution_claim_exact_authorization
    ON {schema}.custody_execution_attempts;
CREATE TRIGGER custody_execution_claim_exact_authorization
    BEFORE INSERT ON {schema}.custody_execution_attempts
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_execution_claim();

DROP TRIGGER IF EXISTS custody_execution_event_history
    ON {schema}.custody_execution_events;
CREATE TRIGGER custody_execution_event_history
    BEFORE INSERT ON {schema}.custody_execution_events
    FOR EACH ROW EXECUTE FUNCTION {schema}.enforce_custody_execution_event();

ALTER TABLE {schema}.custody_execution_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_execution_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_execution_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_execution_events FORCE ROW LEVEL SECURITY;

CREATE POLICY custody_execution_attempts_organization_isolation
    ON {schema}.custody_execution_attempts
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_execution_events_organization_isolation
    ON {schema}.custody_execution_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

-- The executor reads only authorization metadata. It cannot alter authority
-- rows, approval history, policy control, gateway evidence, or any customer
-- IAM/KMS configuration.
GRANT USAGE ON SCHEMA {schema} TO {custody_executor_role};
GRANT SELECT ON {schema}.hormuz_schema_migrations TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_tenants TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_administrators TO {custody_executor_role};
GRANT SELECT ON {schema}.custody_operation_intents TO {custody_executor_role};
GRANT SELECT, INSERT ON {schema}.custody_execution_attempts TO {custody_executor_role};
GRANT SELECT, INSERT ON {schema}.custody_execution_events TO {custody_executor_role};

-- Custody administrators see the executor's metadata through their existing
-- status surface. The control role cannot rewrite machine attempts/events.
GRANT SELECT ON {schema}.custody_execution_attempts TO {custody_control_role};
GRANT SELECT ON {schema}.custody_execution_events TO {custody_control_role};
