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


SESSION_TABLE_COLUMNS = {
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


def validate_session_schema(connection: sqlite3.Connection, *, version: int = 3) -> bool:
    """Reject unexpected durable fields, tables, views, or triggers at startup."""
    tables = SESSION_V2_TABLE_COLUMNS if version == 2 else SESSION_TABLE_COLUMNS
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
