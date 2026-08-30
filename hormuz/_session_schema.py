"""Closed schema for identity/session state, separate from routine usage evidence."""

from __future__ import annotations

import sqlite3


SESSION_TABLE_COLUMNS = {
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


def validate_session_schema(connection: sqlite3.Connection) -> bool:
    """Reject unexpected durable fields, tables, views, or triggers at startup."""
    objects = connection.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if {(str(row[0]), str(row[1])) for row in objects} != {(name, "table") for name in SESSION_TABLE_COLUMNS}:
        return False
    return all(
        {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")} == set(columns)
        for table, columns in SESSION_TABLE_COLUMNS.items()
    )
