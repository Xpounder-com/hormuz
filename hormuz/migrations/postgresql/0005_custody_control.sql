-- Tenant-scoped custody authority and content-free operation approvals. The
-- gateway runtime and policy-control roles receive no privileges on this
-- surface. Customer KMS permissions remain outside PostgreSQL entirely.

CREATE TABLE IF NOT EXISTS {schema}.custody_tenants (
    organization_id TEXT PRIMARY KEY,
    initialized_at TIMESTAMPTZ NOT NULL,
    initialized_by_kind TEXT NOT NULL,
    initialized_by_identity_key TEXT NOT NULL,
    CHECK (initialized_by_kind IN ('static', 'oidc'))
);

CREATE TABLE IF NOT EXISTS {schema}.custody_administrators (
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
    FOREIGN KEY (organization_id) REFERENCES {schema}.custody_tenants (organization_id),
    CHECK (authentication_kind IN ('static', 'oidc')),
    CHECK (
        (authentication_kind = 'static' AND actor_id IS NOT NULL AND issuer IS NULL AND subject IS NULL)
        OR (authentication_kind = 'oidc' AND actor_id IS NULL AND issuer IS NOT NULL AND subject IS NOT NULL)
    ),
    CHECK (created_by_kind IN ('static', 'oidc')),
    CHECK (revoked_by_kind IS NULL OR revoked_by_kind IN ('static', 'oidc')),
    CHECK (
        (active = TRUE AND revoked_at IS NULL AND revoked_by_kind IS NULL AND revoked_by_identity_key IS NULL)
        OR (
            active = FALSE AND revoked_at IS NOT NULL AND revoked_by_kind IS NOT NULL
            AND revoked_by_identity_key IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_custody_administrators_active
    ON {schema}.custody_administrators (organization_id, active, identity_key);

CREATE TABLE IF NOT EXISTS {schema}.custody_operation_intents (
    organization_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    intent_schema_id TEXT NOT NULL,
    intent_schema_version INTEGER NOT NULL,
    operation_type TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    parameters_sha256 TEXT NOT NULL,
    protected_input_ref_sha256 TEXT,
    state TEXT NOT NULL,
    required_approvals INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    authorized_at TIMESTAMPTZ,
    requested_by_kind TEXT NOT NULL,
    requested_by_identity_key TEXT NOT NULL,
    PRIMARY KEY (organization_id, operation_id),
    FOREIGN KEY (organization_id) REFERENCES {schema}.custody_tenants (organization_id),
    CHECK (intent_schema_id = 'hormuz.custody-operation-intent'),
    CHECK (intent_schema_version = 1),
    CHECK (operation_type IN (
        'seal_envelope',
        'rewrap_envelope',
        'verify_restore',
        'retire_envelope',
        'disable_provider_credential',
        'retire_key_reference',
        'resolve_recovery'
    )),
    CHECK (risk_level IN ('routine', 'destructive')),
    CHECK (target_kind IN ('envelope', 'provider_credential', 'key_reference', 'restore', 'recovery')),
    CHECK (target_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (parameters_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (protected_input_ref_sha256 IS NULL OR protected_input_ref_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (state IN ('pending', 'authorized')),
    CHECK (expires_at > created_at),
    CHECK (requested_by_kind IN ('static', 'oidc')),
    CHECK (
        (risk_level = 'routine' AND required_approvals = 1)
        OR (risk_level = 'destructive' AND required_approvals = 2)
    ),
    CHECK (
        (operation_type IN ('seal_envelope', 'rewrap_envelope', 'verify_restore') AND risk_level = 'routine')
        OR (
            operation_type IN (
                'retire_envelope', 'disable_provider_credential', 'retire_key_reference', 'resolve_recovery'
            )
            AND risk_level = 'destructive'
        )
    ),
    CHECK (
        (operation_type IN ('seal_envelope', 'rewrap_envelope', 'retire_envelope') AND target_kind = 'envelope')
        OR (operation_type = 'verify_restore' AND target_kind = 'restore')
        OR (operation_type = 'disable_provider_credential' AND target_kind = 'provider_credential')
        OR (operation_type = 'retire_key_reference' AND target_kind = 'key_reference')
        OR (operation_type = 'resolve_recovery' AND target_kind = 'recovery')
    ),
    CHECK (
        (operation_type = 'seal_envelope' AND protected_input_ref_sha256 IS NOT NULL)
        OR (operation_type <> 'seal_envelope' AND protected_input_ref_sha256 IS NULL)
    ),
    CHECK (
        (state = 'pending' AND authorized_at IS NULL)
        OR (state = 'authorized' AND authorized_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_custody_operation_intents_history
    ON {schema}.custody_operation_intents (organization_id, created_at DESC, operation_id);

CREATE TABLE IF NOT EXISTS {schema}.custody_operation_approvals (
    organization_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    approval_schema_id TEXT NOT NULL,
    approval_schema_version INTEGER NOT NULL,
    approver_kind TEXT NOT NULL,
    approver_identity_key TEXT NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, operation_id, approver_identity_key),
    FOREIGN KEY (organization_id, operation_id)
        REFERENCES {schema}.custody_operation_intents (organization_id, operation_id),
    CHECK (approval_schema_id = 'hormuz.custody-operation-approval'),
    CHECK (approval_schema_version = 1),
    CHECK (approver_kind IN ('static', 'oidc'))
);

CREATE TABLE IF NOT EXISTS {schema}.custody_control_events (
    event_id TEXT PRIMARY KEY,
    event_schema_id TEXT NOT NULL,
    event_schema_version INTEGER NOT NULL,
    organization_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_type TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    actor_identity_key TEXT NOT NULL,
    target_identity_key TEXT,
    operation_id TEXT,
    operation_type TEXT,
    risk_level TEXT,
    target_kind TEXT,
    target_sha256 TEXT,
    parameters_sha256 TEXT,
    protected_input_ref_sha256 TEXT,
    required_approvals INTEGER,
    approval_count INTEGER,
    expires_at TIMESTAMPTZ,
    FOREIGN KEY (organization_id) REFERENCES {schema}.custody_tenants (organization_id),
    CHECK (event_schema_id = 'hormuz.custody-control-event'),
    CHECK (event_schema_version = 1),
    CHECK (event_type IN (
        'bootstrap_initialized',
        'administrator_granted',
        'administrator_revoked',
        'operation_requested',
        'operation_approved',
        'operation_authorized'
    )),
    CHECK (actor_kind IN ('static', 'oidc')),
    CHECK (target_sha256 IS NULL OR target_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (parameters_sha256 IS NULL OR parameters_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (protected_input_ref_sha256 IS NULL OR protected_input_ref_sha256 ~ '^[0-9a-f]{{64}}$'),
    CHECK (required_approvals IS NULL OR required_approvals IN (1, 2)),
    CHECK (approval_count IS NULL OR approval_count BETWEEN 0 AND 2)
);

CREATE INDEX IF NOT EXISTS idx_custody_control_events_history
    ON {schema}.custody_control_events (organization_id, occurred_at DESC, event_id);

ALTER TABLE {schema}.custody_tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_tenants FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_administrators ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_administrators FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_operation_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_operation_intents FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_operation_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_operation_approvals FORCE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_control_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE {schema}.custody_control_events FORCE ROW LEVEL SECURITY;

CREATE POLICY custody_tenants_organization_isolation
    ON {schema}.custody_tenants
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_administrators_organization_isolation
    ON {schema}.custody_administrators
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_operation_intents_organization_isolation
    ON {schema}.custody_operation_intents
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_operation_approvals_organization_isolation
    ON {schema}.custody_operation_approvals
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));
CREATE POLICY custody_control_events_organization_isolation
    ON {schema}.custody_control_events
    USING (organization_id = current_setting('hormuz.organization_id', true))
    WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));

GRANT USAGE ON SCHEMA {schema} TO {custody_control_role};
GRANT SELECT ON {schema}.hormuz_schema_migrations TO {custody_control_role};
GRANT SELECT, INSERT ON {schema}.custody_tenants TO {custody_control_role};
GRANT SELECT, INSERT ON {schema}.custody_administrators TO {custody_control_role};
GRANT UPDATE (active, revoked_at, revoked_by_kind, revoked_by_identity_key)
    ON {schema}.custody_administrators TO {custody_control_role};
GRANT SELECT, INSERT ON {schema}.custody_operation_intents TO {custody_control_role};
GRANT UPDATE (state, authorized_at) ON {schema}.custody_operation_intents TO {custody_control_role};
GRANT SELECT, INSERT ON {schema}.custody_operation_approvals TO {custody_control_role};
GRANT SELECT, INSERT ON {schema}.custody_control_events TO {custody_control_role};
