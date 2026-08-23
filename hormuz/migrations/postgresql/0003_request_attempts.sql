-- Immutable, content-free pre-egress request-attempt evidence. The runtime
-- role can append roots/events but cannot rewrite them. Linked reservations
-- retain conservative estimated consumption while their latest event remains
-- pending or outcome_unknown.

ALTER TABLE {schema}.gateway_budget_reservations
    ADD COLUMN IF NOT EXISTS attempt_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_reservation_attempt
    ON {schema}.gateway_budget_reservations (attempt_id)
    WHERE attempt_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_usage_organization_id
    ON {schema}.gateway_usage_events (organization_id, id);

CREATE TABLE IF NOT EXISTS {schema}.gateway_request_attempts (
    attempt_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    evidence_schema_id TEXT NOT NULL CHECK (evidence_schema_id = 'hormuz.request-attempt'),
    evidence_schema_version INTEGER NOT NULL CHECK (evidence_schema_version = 1),
    organization_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_name TEXT NOT NULL,
    team_id TEXT NOT NULL,
    team_name TEXT NOT NULL,
    identity_type TEXT NOT NULL,
    authentication_source TEXT NOT NULL,
    client TEXT NOT NULL,
    protocol TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    resolved_alias TEXT,
    upstream_model TEXT,
    policy_version TEXT NOT NULL,
    policy_action TEXT NOT NULL,
    redaction_count BIGINT NOT NULL CHECK (redaction_count >= 0),
    redaction_rules TEXT NOT NULL,
    reserved_tokens BIGINT NOT NULL CHECK (reserved_tokens >= 0),
    reserved_cost_microusd BIGINT NOT NULL CHECK (reserved_cost_microusd >= 0),
    UNIQUE (organization_id, attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_gateway_attempt_organization_created
    ON {schema}.gateway_request_attempts (organization_id, created_at);

CREATE TABLE IF NOT EXISTS {schema}.gateway_request_attempt_events (
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_schema_id TEXT NOT NULL CHECK (event_schema_id = 'hormuz.request-attempt-event'),
    event_schema_version INTEGER NOT NULL CHECK (event_schema_version = 1),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    state TEXT NOT NULL CHECK (state IN ('pending', 'succeeded', 'failed', 'rate_limited', 'outcome_unknown')),
    reason_code TEXT,
    usage_event_id TEXT,
    UNIQUE (attempt_id, sequence),
    FOREIGN KEY (organization_id, attempt_id)
        REFERENCES {schema}.gateway_request_attempts (organization_id, attempt_id),
    FOREIGN KEY (organization_id, usage_event_id)
        REFERENCES {schema}.gateway_usage_events (organization_id, id),
    CHECK (
        (state = 'pending' AND sequence = 1 AND reason_code IS NULL AND usage_event_id IS NULL)
        OR (
            state IN ('succeeded', 'failed', 'rate_limited')
            AND sequence > 1
            AND reason_code IS NULL
            AND usage_event_id IS NOT NULL
        )
        OR (
            state = 'outcome_unknown'
            AND sequence > 1
            AND reason_code IN ('provider_transport_ambiguous', 'provider_stream_interrupted', 'stale_pending')
            AND usage_event_id IS NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_gateway_attempt_event_organization_attempt
    ON {schema}.gateway_request_attempt_events (organization_id, attempt_id, sequence);

ALTER TABLE {schema}.gateway_request_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_request_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_request_attempt_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_request_attempt_events FORCE ROW LEVEL SECURITY;

CREATE POLICY gateway_request_attempts_organization_isolation
    ON {schema}.gateway_request_attempts
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

CREATE POLICY gateway_request_attempt_events_organization_isolation
    ON {schema}.gateway_request_attempt_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

GRANT SELECT, INSERT ON {schema}.gateway_request_attempts TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.gateway_request_attempt_events TO {runtime_role};
