"""Console revocation primitives shared with transactional member removal."""

from __future__ import annotations

import secrets

from .session_store import _isoformat


def record_console_event(connection, now, organization_id, event_type, actor_id,
                         *, membership_id=None, grant_id=None, session_id=None) -> None:
    connection.execute(
        "INSERT INTO console_events (id, organization_id, event_type, decision_actor_id, "
        "target_membership_id, grant_id, session_id, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("cev_" + secrets.token_urlsafe(24), organization_id, event_type, actor_id,
         membership_id, grant_id, session_id, _isoformat(now)),
    )


def cancel_console_flows(connection, organization_id) -> None:
    # A pending OIDC login has no verified subject yet. Revoke conservatively.
    connection.execute(
        "UPDATE console_login_flows SET status = 'failed', state_hash = NULL, "
        "browser_cookie_hash = NULL, encrypted_flow = NULL "
        "WHERE organization_id = ? AND status IN ('pending', 'exchanging')",
        (organization_id,),
    )


def revoke_member_console_access(connection, now, organization_id, membership_id,
                                 *, actor_id="server_local_operator") -> bool:
    scope = (organization_id, membership_id)
    changed = connection.execute(
        "UPDATE console_grants SET status = 'revoked', authorization_version = authorization_version + 1, "
        "updated_at = ? WHERE organization_id = ? AND membership_id = ? AND status = 'active'",
        (_isoformat(now), *scope),
    ).rowcount
    connection.execute(
        "UPDATE console_sessions SET revoked_at = ? "
        "WHERE organization_id = ? AND membership_id = ? AND revoked_at IS NULL",
        (_isoformat(now), *scope),
    )
    cancel_console_flows(connection, organization_id)
    if changed:
        record_console_event(connection, now, organization_id, "grant_revoked", actor_id,
                             membership_id=membership_id)
    return bool(changed)
