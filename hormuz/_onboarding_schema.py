"""Literal schema additions for the single-node team directory."""

TABLE_DDL = {
    "onboarding_organizations": """
        CREATE TABLE onboarding_organizations (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            issuer TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "onboarding_teams": """
        CREATE TABLE onboarding_teams (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES onboarding_organizations(id),
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (organization_id, id)
        )
    """,
    "onboarding_memberships": """
        CREATE TABLE onboarding_memberships (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES onboarding_organizations(id),
            team_id TEXT NOT NULL,
            issuer TEXT NOT NULL,
            subject TEXT,
            name TEXT NOT NULL,
            email_hash BLOB NOT NULL,
            allowed_clients TEXT NOT NULL,
            clearance TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'disabled')),
            authorization_version INTEGER NOT NULL CHECK (authorization_version >= 1),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (organization_id, issuer, subject),
            UNIQUE (organization_id, email_hash),
            FOREIGN KEY (organization_id, team_id)
                REFERENCES onboarding_teams(organization_id, id)
        )
    """,
    "onboarding_invitations": """
        CREATE TABLE onboarding_invitations (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL REFERENCES onboarding_organizations(id),
            membership_id TEXT NOT NULL REFERENCES onboarding_memberships(id),
            authorization_version INTEGER NOT NULL,
            secret_hash BLOB UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'revoked')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            completed_at TEXT
        )
    """,
    "onboarding_events": """
        CREATE TABLE onboarding_events (
            id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL,
            team_id TEXT,
            membership_id TEXT,
            invitation_id TEXT,
            event_type TEXT NOT NULL,
            decision_actor TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
    """,
}

INDEX_DDL = (
    "CREATE INDEX idx_onboarding_members_scope ON onboarding_memberships(organization_id, id)",
    "CREATE INDEX idx_onboarding_invites_scope ON onboarding_invitations(organization_id, id)",
    "CREATE INDEX idx_onboarding_events_scope ON onboarding_events(organization_id, id)",
    "CREATE INDEX idx_enrollment_membership ON session_enrollments(organization_id, membership_id)",
    "CREATE INDEX idx_session_membership ON human_sessions(organization_id, membership_id)",
)
