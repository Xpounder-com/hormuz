"""Closed schema for identity/session state, separate from routine usage evidence."""

from __future__ import annotations

import sqlite3


SESSION_V2_TABLE_COLUMNS = {
    "session_enrollments": (
        "id", "secret_hash", "issuer", "client_name", "status", "state_hash",
        "browser_cookie_hash", "encrypted_flow", "subject", "organization_id",
        "actor_id", "team_id", "clearance", "created_at", "expires_at",
        "authorization_started_at", "authorized_at", "redeemed_at",
    ),
    "human_sessions": (
        "id", "issuer", "subject", "client_name", "access_hash", "refresh_hash",
        "access_expires_at", "absolute_expires_at", "generation", "created_at",
        "refreshed_at", "organization_id", "actor_id", "team_id", "clearance", "revoked_at",
    ),
    "consumed_refresh_credentials": ("credential_hash", "session_id", "consumed_at", "expires_at"),
    "session_security_events": (
        "id", "occurred_at", "session_id", "event_type", "organization_id",
        "target_actor_id", "target_team_id", "decision_actor_id", "decision_scope", "reason_code",
    ),
}


SESSION_V3_TABLE_COLUMNS = {
    **SESSION_V2_TABLE_COLUMNS,
    "session_enrollments": SESSION_V2_TABLE_COLUMNS["session_enrollments"] + (
        "invitation_id", "membership_id", "authorization_version",
    ),
    "human_sessions": SESSION_V2_TABLE_COLUMNS["human_sessions"] + (
        "membership_id", "authorization_version",
    ),
    "onboarding_organizations": ("id", "name", "issuer", "created_at"),
    "onboarding_teams": ("id", "organization_id", "name", "created_at"),
    "onboarding_memberships": (
        "id", "organization_id", "team_id", "issuer", "subject", "name", "email_hash",
        "allowed_clients", "clearance", "status", "authorization_version", "created_at", "updated_at",
    ),
    "onboarding_invitations": (
        "id", "organization_id", "membership_id", "authorization_version", "secret_hash",
        "status", "created_at", "expires_at", "completed_at",
    ),
    "onboarding_events": (
        "id", "organization_id", "team_id", "membership_id", "invitation_id",
        "event_type", "decision_actor", "occurred_at",
    ),
}


SESSION_TABLE_COLUMNS = {
    **SESSION_V3_TABLE_COLUMNS,
    "console_grants": (
        "id", "organization_id", "membership_id", "role", "status",
        "authorization_version", "created_at", "updated_at",
    ),
    "console_login_flows": (
        "id", "organization_id", "issuer", "state_hash", "browser_cookie_hash",
        "encrypted_flow", "status", "created_at", "expires_at",
    ),
    "console_sessions": (
        "id", "organization_id", "membership_id", "grant_id", "membership_version",
        "grant_version", "credential_hash", "created_at", "last_seen_at", "expires_at", "revoked_at",
    ),
    "console_events": (
        "id", "organization_id", "event_type", "decision_actor_id",
        "target_membership_id", "grant_id", "session_id", "occurred_at",
    ),
}


def validate_session_schema(connection: sqlite3.Connection, *, version: int = 4) -> bool:
    """Reject unexpected durable fields, tables, views, or triggers at startup."""
    tables = {2: SESSION_V2_TABLE_COLUMNS, 3: SESSION_V3_TABLE_COLUMNS, 4: SESSION_TABLE_COLUMNS}.get(version)
    if tables is None:
        return False
    objects = connection.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if {(str(row[0]), str(row[1])) for row in objects} != {(name, "table") for name in tables}:
        return False
    return all(
        {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")} == set(columns)
        for table, columns in tables.items()
    )
