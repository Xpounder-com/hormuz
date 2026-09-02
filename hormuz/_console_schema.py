"""Closed, metadata-only additions for separate administrator browser sessions."""

TABLE_DDL = {
    "console_grants": """
        CREATE TABLE console_grants (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES onboarding_organizations(id),
            membership_id TEXT NOT NULL REFERENCES onboarding_memberships(id),
            role TEXT NOT NULL CHECK (role IN ('report_viewer', 'member_admin')),
            status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
            authorization_version INTEGER NOT NULL CHECK (authorization_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (organization_id, membership_id)
        )
    """,
    "console_login_flows": """
        CREATE TABLE console_login_flows (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES onboarding_organizations(id),
            issuer TEXT NOT NULL,
            state_hash BLOB UNIQUE,
            browser_cookie_hash BLOB,
            encrypted_flow BLOB,
            status TEXT NOT NULL CHECK (status IN ('pending', 'exchanging', 'completed', 'failed')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    """,
    "console_sessions": """
        CREATE TABLE console_sessions (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES onboarding_organizations(id),
            membership_id TEXT NOT NULL REFERENCES onboarding_memberships(id),
            grant_id TEXT NOT NULL REFERENCES console_grants(id),
            membership_version INTEGER NOT NULL,
            grant_version INTEGER NOT NULL,
            credential_hash BLOB NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        )
    """,
    "console_events": """
        CREATE TABLE console_events (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            decision_actor_id TEXT NOT NULL,
            target_membership_id TEXT,
            grant_id TEXT,
            session_id TEXT,
            occurred_at TEXT NOT NULL
        )
    """,
}

INDEX_DDL = (
    "CREATE INDEX idx_console_grants_scope ON console_grants(organization_id, id)",
    "CREATE INDEX idx_console_flows_scope ON console_login_flows(organization_id, status, expires_at)",
    "CREATE INDEX idx_console_sessions_member ON console_sessions(organization_id, membership_id, revoked_at)",
    "CREATE INDEX idx_console_events_scope ON console_events(organization_id, id)",
)
