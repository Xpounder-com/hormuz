"""Metadata-only workspace authority; no provider keys or request content."""

TABLE_DDL = {
    "workspaces": """CREATE TABLE workspaces (
        id TEXT PRIMARY KEY,
        organization_id TEXT NOT NULL UNIQUE REFERENCES onboarding_organizations(id),
        slug TEXT NOT NULL UNIQUE,
        owner_membership_id TEXT NOT NULL REFERENCES onboarding_memberships(id),
        status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
        created_at TEXT NOT NULL
    )""",
    "workspace_accounts": """CREATE TABLE workspace_accounts (
        account_hash BLOB PRIMARY KEY,
        workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        membership_id TEXT NOT NULL REFERENCES onboarding_memberships(id)
    )""",
    "workspace_login_flows": """CREATE TABLE workspace_login_flows (
        id TEXT PRIMARY KEY, issuer TEXT NOT NULL,
        workspace_id TEXT REFERENCES workspaces(id),
        handoff_id TEXT REFERENCES workspace_handoffs(id),
        state_hash BLOB UNIQUE, browser_cookie_hash BLOB, encrypted_flow BLOB,
        status TEXT NOT NULL CHECK (status IN ('pending', 'exchanging', 'completed', 'failed')),
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL
    )""",
    "workspace_domains": """CREATE TABLE workspace_domains (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        hostname TEXT NOT NULL UNIQUE, challenge TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'removing', 'removed')),
        version INTEGER NOT NULL CHECK (version >= 1), provider_id TEXT, provider_requested_at TEXT,
        check_started_at TEXT, verified_until TEXT, last_error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    "workspace_sessions": """CREATE TABLE workspace_sessions (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        membership_id TEXT NOT NULL REFERENCES onboarding_memberships(id),
        membership_version INTEGER NOT NULL, grant_id TEXT NOT NULL REFERENCES console_grants(id),
        grant_version INTEGER NOT NULL, credential_hash BLOB NOT NULL UNIQUE,
        origin TEXT NOT NULL, domain_id TEXT REFERENCES workspace_domains(id), domain_version INTEGER,
        created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
    )""",
    "workspace_handoffs": """CREATE TABLE workspace_handoffs (
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(id),
        domain_id TEXT NOT NULL REFERENCES workspace_domains(id), domain_version INTEGER NOT NULL,
        browser_cookie_hash BLOB NOT NULL, secret_hash BLOB UNIQUE,
        membership_id TEXT REFERENCES onboarding_memberships(id), membership_version INTEGER,
        grant_id TEXT REFERENCES console_grants(id), grant_version INTEGER,
        created_at TEXT NOT NULL, expires_at TEXT NOT NULL, consumed_at TEXT
    )""",
}

TABLE_COLUMNS = {
    "workspaces": ("id", "organization_id", "slug", "owner_membership_id", "status", "created_at"),
    "workspace_accounts": ("account_hash", "workspace_id", "membership_id"),
    "workspace_login_flows": ("id", "issuer", "workspace_id", "handoff_id", "state_hash", "browser_cookie_hash", "encrypted_flow", "status", "created_at", "expires_at"),
    "workspace_domains": ("id", "workspace_id", "hostname", "challenge", "status", "version", "provider_id", "provider_requested_at", "check_started_at", "verified_until", "last_error", "created_at", "updated_at"),
    "workspace_sessions": ("id", "workspace_id", "membership_id", "membership_version", "grant_id", "grant_version", "credential_hash", "origin", "domain_id", "domain_version", "created_at", "last_seen_at", "expires_at", "revoked_at"),
    "workspace_handoffs": ("id", "workspace_id", "domain_id", "domain_version", "browser_cookie_hash", "secret_hash", "membership_id", "membership_version", "grant_id", "grant_version", "created_at", "expires_at", "consumed_at"),
}

INDEX_DDL = (
    "CREATE INDEX idx_workspace_flows_expiry ON workspace_login_flows(status, expires_at)",
    "CREATE INDEX idx_workspace_sessions_member ON workspace_sessions(membership_id, revoked_at)",
    "CREATE INDEX idx_workspace_domains_scope ON workspace_domains(workspace_id, status)",
    "CREATE INDEX idx_workspace_handoffs_expiry ON workspace_handoffs(expires_at)",
)
