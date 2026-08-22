-- Shared, immutable policy control plane. This migration deliberately keeps
-- the gateway runtime role read-only for policy authority and gives the
-- separately configured control-service role the smallest write surface.

CREATE TABLE IF NOT EXISTS {schema}.policy_tenants (
    organization_id TEXT PRIMARY KEY,
    initialized_at TIMESTAMPTZ NOT NULL,
    initialized_by_kind TEXT NOT NULL,
    initialized_by_identity_key TEXT NOT NULL,
    CHECK (initialized_by_kind IN ('static', 'oidc'))
);

CREATE TABLE IF NOT EXISTS {schema}.policy_administrators (
    organization_id TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    authentication_kind TEXT NOT NULL,
    actor_id TEXT,
    issuer TEXT,
    subject TEXT,
    active BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    created_by_kind TEXT NOT NULL,
    created_by_identity_key TEXT NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoked_by_kind TEXT,
    revoked_by_identity_key TEXT,
    PRIMARY KEY (organization_id, identity_key),
    FOREIGN KEY (organization_id) REFERENCES {schema}.policy_tenants (organization_id),
    CHECK (authentication_kind IN ('static', 'oidc')),
    CHECK (
        (authentication_kind = 'static' AND actor_id IS NOT NULL AND issuer IS NULL AND subject IS NULL)
        OR (authentication_kind = 'oidc' AND actor_id IS NULL AND issuer IS NOT NULL AND subject IS NOT NULL)
    ),
    CHECK (created_by_kind IN ('static', 'oidc', 'break_glass')),
    CHECK (revoked_by_kind IS NULL OR revoked_by_kind IN ('static', 'oidc')),
    CHECK (
        (active = TRUE AND revoked_at IS NULL AND revoked_by_kind IS NULL AND revoked_by_identity_key IS NULL)
        OR (
            active = FALSE AND revoked_at IS NOT NULL AND revoked_by_kind IS NOT NULL
            AND revoked_by_identity_key IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_policy_administrators_active
    ON {schema}.policy_administrators (organization_id, active, identity_key);

CREATE TABLE IF NOT EXISTS {schema}.policy_versions (
    organization_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    document_json JSONB NOT NULL,
    change_summary JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    author_kind TEXT NOT NULL,
    author_identity_key TEXT NOT NULL,
    PRIMARY KEY (organization_id, version_id),
    UNIQUE (organization_id, content_sha256),
    FOREIGN KEY (organization_id) REFERENCES {schema}.policy_tenants (organization_id),
    CHECK (author_kind IN ('static', 'oidc'))
);

CREATE INDEX IF NOT EXISTS idx_policy_versions_created_at
    ON {schema}.policy_versions (organization_id, created_at DESC);

CREATE TABLE IF NOT EXISTS {schema}.policy_active_versions (
    organization_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    generation BIGINT NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL,
    activated_by_kind TEXT NOT NULL,
    activated_by_identity_key TEXT NOT NULL,
    FOREIGN KEY (organization_id, version_id)
        REFERENCES {schema}.policy_versions (organization_id, version_id),
    CHECK (generation > 0),
    CHECK (activated_by_kind IN ('static', 'oidc'))
);

CREATE TABLE IF NOT EXISTS {schema}.policy_control_events (
    event_id TEXT PRIMARY KEY,
    event_schema_id TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL,
    organization_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_identity_key TEXT NOT NULL,
    target_identity_key TEXT,
    version_id TEXT,
    generation BIGINT,
    reason_code TEXT,
    change_summary JSONB,
    CHECK (event_type IN (
        'bootstrap_initialized',
        'administrator_granted',
        'administrator_revoked',
        'policy_staged',
        'policy_activated',
        'policy_rolled_back',
        'break_glass_recovered'
    )),
    CHECK (actor_kind IN ('static', 'oidc', 'break_glass')),
    CHECK (event_schema_id = 'hormuz.policy-control-event'),
    CHECK (event_schema_version = 1),
    CHECK (generation IS NULL OR generation > 0),
    FOREIGN KEY (organization_id) REFERENCES {schema}.policy_tenants (organization_id)
);

CREATE INDEX IF NOT EXISTS idx_policy_control_events_history
    ON {schema}.policy_control_events (organization_id, occurred_at DESC);

ALTER TABLE {schema}.policy_tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_administrators ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_administrators FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_active_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_active_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_control_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.policy_control_events FORCE ROW LEVEL SECURITY;

CREATE POLICY policy_tenants_organization_isolation
    ON {schema}.policy_tenants
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY policy_administrators_organization_isolation
    ON {schema}.policy_administrators
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY policy_versions_organization_isolation
    ON {schema}.policy_versions
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY policy_active_versions_organization_isolation
    ON {schema}.policy_active_versions
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY policy_control_events_organization_isolation
    ON {schema}.policy_control_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

GRANT USAGE ON SCHEMA {schema} TO {runtime_role};
GRANT SELECT ON {schema}.policy_versions TO {runtime_role};
GRANT SELECT ON {schema}.policy_active_versions TO {runtime_role};

GRANT USAGE ON SCHEMA {schema} TO {policy_control_role};
GRANT SELECT ON {schema}.hormuz_schema_migrations TO {policy_control_role};
GRANT SELECT, INSERT ON {schema}.policy_tenants TO {policy_control_role};
GRANT SELECT, INSERT ON {schema}.policy_administrators TO {policy_control_role};
GRANT UPDATE (active, revoked_at, revoked_by_kind, revoked_by_identity_key)
    ON {schema}.policy_administrators TO {policy_control_role};
GRANT SELECT, INSERT ON {schema}.policy_versions TO {policy_control_role};
GRANT SELECT, INSERT ON {schema}.policy_active_versions TO {policy_control_role};
GRANT UPDATE (version_id, generation, activated_at, activated_by_kind, activated_by_identity_key)
    ON {schema}.policy_active_versions TO {policy_control_role};
GRANT SELECT, INSERT ON {schema}.policy_control_events TO {policy_control_role};
