-- SQLite 18 / PostgreSQL 23 metadata-only portfolio role-view reads.

CREATE TABLE {schema}.portfolio_role_view_audit_events (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    event_id TEXT NOT NULL CHECK (length(event_id) = 36),
    sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    reader_role TEXT NOT NULL CHECK (reader_role IN ('finance_viewer','platform_viewer','team_lead')),
    query_class TEXT NOT NULL CHECK (query_class IN ('finance_budget','platform_scorecard','team_budget','team_scorecard')),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization','team')),
    scope_id TEXT CHECK (scope_id IS NULL OR length(scope_id) BETWEEN 1 AND 128),
    filter_digest TEXT NOT NULL CHECK (length(filter_digest) = 64),
    snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
    companion_snapshot_sequence BIGINT NOT NULL CHECK (companion_snapshot_sequence >= 0),
    result_count INTEGER NOT NULL CHECK (result_count BETWEEN 0 AND 100),
    partial_count INTEGER NOT NULL CHECK (partial_count BETWEEN 0 AND result_count),
    occurred_at TEXT NOT NULL,
    PRIMARY KEY (organization_id, event_id),
    UNIQUE (organization_id, sequence),
    CHECK ((scope_kind = 'team' AND reader_role = 'team_lead' AND scope_id IS NOT NULL)
        OR (scope_kind = 'organization' AND reader_role <> 'team_lead' AND scope_id IS NULL)),
    CHECK (length(occurred_at) = 27 AND substr(occurred_at, 20, 1) = '.' AND substr(occurred_at, 27, 1) = 'Z')
);

CREATE TABLE {schema}.portfolio_role_view_cursors (
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    cursor_id TEXT NOT NULL CHECK (length(cursor_id) = 64),
    actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
    authority_digest TEXT NOT NULL CHECK (length(authority_digest) = 64),
    reader_role TEXT NOT NULL CHECK (reader_role IN ('finance_viewer','platform_viewer','team_lead')),
    query_class TEXT NOT NULL CHECK (query_class IN ('finance_budget','platform_scorecard','team_budget','team_scorecard')),
    scope_id TEXT CHECK (scope_id IS NULL OR length(scope_id) BETWEEN 1 AND 128),
    schema_id TEXT NOT NULL CHECK (schema_id = 'hormuz.portfolio-role-view-page'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    as_of TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    snapshot_sequence BIGINT NOT NULL CHECK (snapshot_sequence >= 0),
    companion_snapshot_sequence BIGINT NOT NULL CHECK (companion_snapshot_sequence >= 0),
    page_limit INTEGER NOT NULL CHECK (page_limit BETWEEN 1 AND 100),
    after_at TEXT NOT NULL,
    after_id TEXT NOT NULL CHECK (length(after_id) BETWEEN 1 AND 128),
    filters_json TEXT NOT NULL CHECK (length(filters_json) BETWEEN 2 AND 4096),
    PRIMARY KEY (organization_id, cursor_id),
    CHECK ((reader_role = 'team_lead' AND scope_id IS NOT NULL)
        OR (reader_role <> 'team_lead' AND scope_id IS NULL)),
    CHECK (length(as_of) = 27 AND substr(as_of, 20, 1) = '.' AND substr(as_of, 27, 1) = 'Z'),
    CHECK (length(expires_at) = 27 AND substr(expires_at, 20, 1) = '.' AND substr(expires_at, 27, 1) = 'Z'),
    CHECK (length(after_at) = 27 AND substr(after_at, 20, 1) = '.' AND substr(after_at, 27, 1) = 'Z'),
    CHECK (as_of < expires_at)
);

CREATE INDEX portfolio_role_view_audit_window
    ON {schema}.portfolio_role_view_audit_events (
        organization_id, occurred_at, event_id
    );
CREATE INDEX portfolio_role_view_cursor_expiry
    ON {schema}.portfolio_role_view_cursors (
        organization_id, expires_at, cursor_id
    );

ALTER TABLE {schema}.portfolio_role_view_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_role_view_audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_role_view_audit_events_tenant
    ON {schema}.portfolio_role_view_audit_events
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_role_view_audit_events_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_role_view_audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_role_view_audit_events FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_role_view_audit_events TO {runtime_role};

ALTER TABLE {schema}.portfolio_role_view_cursors ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.portfolio_role_view_cursors FORCE ROW LEVEL SECURITY;
CREATE POLICY portfolio_role_view_cursors_tenant
    ON {schema}.portfolio_role_view_cursors
    USING (organization_id=current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id=current_setting('hormuz.organization_id', true));
CREATE TRIGGER portfolio_role_view_cursors_immutable
    BEFORE UPDATE OR DELETE OR TRUNCATE ON {schema}.portfolio_role_view_cursors
    FOR EACH STATEMENT EXECUTE FUNCTION {schema}.portfolio_reject_mutation();
REVOKE ALL ON {schema}.portfolio_role_view_cursors FROM PUBLIC;
GRANT SELECT, INSERT ON {schema}.portfolio_role_view_cursors TO {runtime_role};
