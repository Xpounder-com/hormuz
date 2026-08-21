CREATE TABLE IF NOT EXISTS {schema}.gateway_usage_events (
    id TEXT PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    evidence_schema_id TEXT NOT NULL DEFAULT 'hormuz.audit-event',
    evidence_schema_version INTEGER NOT NULL DEFAULT 2,
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
    provider_reported_model TEXT,
    policy_version TEXT NOT NULL,
    policy_action TEXT NOT NULL,
    status TEXT NOT NULL,
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    reasoning_tokens BIGINT NOT NULL DEFAULT 0,
    cost_microusd BIGINT NOT NULL DEFAULT 0,
    cost_basis TEXT NOT NULL,
    allocation_basis TEXT NOT NULL,
    coverage TEXT NOT NULL,
    provider_request_id TEXT,
    redaction_count BIGINT NOT NULL DEFAULT 0,
    redaction_rules TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_gateway_usage_organization_occurred_at
    ON {schema}.gateway_usage_events (organization_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_gateway_usage_organization_actor_month
    ON {schema}.gateway_usage_events (organization_id, actor_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_gateway_usage_organization_team_month
    ON {schema}.gateway_usage_events (organization_id, team_id, occurred_at);

CREATE TABLE IF NOT EXISTS {schema}.gateway_secret_events (
    id TEXT PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL,
    evidence_schema_id TEXT NOT NULL DEFAULT 'hormuz.audit-event',
    evidence_schema_version INTEGER NOT NULL DEFAULT 2,
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
    policy_version TEXT NOT NULL,
    coverage TEXT NOT NULL,
    action TEXT NOT NULL,
    detection_count BIGINT NOT NULL,
    rules TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gateway_secret_organization_occurred_at
    ON {schema}.gateway_secret_events (organization_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_gateway_secret_organization_actor_month
    ON {schema}.gateway_secret_events (organization_id, actor_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_gateway_secret_organization_team_month
    ON {schema}.gateway_secret_events (organization_id, team_id, occurred_at);

CREATE TABLE IF NOT EXISTS {schema}.gateway_budget_reservations (
    id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    organization_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    reserved_tokens BIGINT NOT NULL,
    reserved_cost_microusd BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gateway_reservation_organization_expires_at
    ON {schema}.gateway_budget_reservations (organization_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_gateway_reservation_organization_actor
    ON {schema}.gateway_budget_reservations (organization_id, actor_id, expires_at);
CREATE INDEX IF NOT EXISTS idx_gateway_reservation_organization_team
    ON {schema}.gateway_budget_reservations (organization_id, team_id, expires_at);

ALTER TABLE {schema}.gateway_usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_usage_events FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_secret_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_secret_events FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_budget_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.gateway_budget_reservations FORCE ROW LEVEL SECURITY;

CREATE POLICY gateway_usage_events_organization_isolation
    ON {schema}.gateway_usage_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY gateway_secret_events_organization_isolation
    ON {schema}.gateway_secret_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY gateway_budget_reservations_organization_isolation
    ON {schema}.gateway_budget_reservations
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

GRANT USAGE ON SCHEMA {schema} TO {runtime_role};
GRANT SELECT ON {schema}.hormuz_schema_migrations TO {runtime_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.gateway_usage_events TO {runtime_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.gateway_secret_events TO {runtime_role};
GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.gateway_budget_reservations TO {runtime_role};
