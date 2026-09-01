"""Separate, short-lived administrator authority in the managed session database."""

from __future__ import annotations

import base64
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ._console_authority import cancel_console_flows, record_console_event, revoke_member_console_access
from .onboarding import TeamDirectory, _identifier
from .session_store import SQLiteSessionStore, SessionStoreError, _isoformat, _parse_time, _require_secret


CONSOLE_ROLES = ("report_viewer", "member_admin")
CONSOLE_IDLE_TTL = timedelta(minutes=10)
CONSOLE_ABSOLUTE_TTL = timedelta(hours=1)
_CREDENTIAL = re.compile(r"hox_c_[A-Za-z0-9_-]{43}\Z")


class ConsoleError(SessionStoreError):
    """A fixed error code; never contains a submitted identifier or claim."""


@dataclass(frozen=True)
class ConsolePrincipal:
    session_id: str
    organization_id: str
    organization_name: str
    membership_id: str
    name: str
    role: str
    expires_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "organization_id": self.organization_id, "organization_name": self.organization_name,
            "membership_id": self.membership_id, "name": self.name, "role": self.role,
            "expires_at": _isoformat(self.expires_at), "idle_timeout_seconds": 600,
        }


@dataclass(frozen=True)
class ConsoleFlow:
    flow_id: str
    organization_id: str
    issuer: str
    nonce: str = field(repr=False)
    pkce_verifier: str = field(repr=False)


class ConsoleStore:
    def __init__(self, store: SQLiteSessionStore, directory: TeamDirectory):
        self.store = store
        self.directory = directory

    def grant(self, *, organization_id: str, membership_id: str, role: str) -> dict[str, object]:
        """Local operator only. No HTTP endpoint calls this method."""
        self.directory._enabled()
        if role not in CONSOLE_ROLES:
            raise ConsoleError("admin_invalid_role")
        now = self.store._now()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            member = self.directory._member(connection, organization_id, membership_id)
            if member["status"] != "active" or not member["subject"]:
                raise ConsoleError("admin_member_unavailable")
            grant = connection.execute(
                "SELECT id, role, status FROM console_grants WHERE organization_id = ? AND membership_id = ?",
                (organization_id, membership_id),
            ).fetchone()
            if grant and grant["status"] == "active" and grant["role"] == role:
                return {"changed": False, "grant_id": grant["id"], "role": role}
            grant_id = grant["id"] if grant else "cgr_" + secrets.token_urlsafe(24)
            if grant:
                connection.execute(
                    "UPDATE console_grants SET role = ?, status = 'active', "
                    "authorization_version = authorization_version + 1, updated_at = ? WHERE id = ?",
                    (role, _isoformat(now), grant_id),
                )
            else:
                connection.execute(
                    "INSERT INTO console_grants (id, organization_id, membership_id, role, status, "
                    "authorization_version, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)",
                    (grant_id, organization_id, membership_id, role, _isoformat(now), _isoformat(now)),
                )
            connection.execute("UPDATE console_sessions SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL",
                               (_isoformat(now), grant_id))
            cancel_console_flows(connection, organization_id)
            record_console_event(connection, now, organization_id, "grant_changed", "server_local_operator",
                                 membership_id=membership_id, grant_id=grant_id)
            return {"changed": True, "grant_id": grant_id, "role": role}

    def revoke(self, *, organization_id: str, membership_id: str) -> dict[str, object]:
        self.directory._enabled()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.directory._member(connection, organization_id, membership_id)
            return {"changed": revoke_member_console_access(connection, self.store._now(), organization_id, membership_id)}

    def list_grants(self, *, organization_id: str, after: str = "", limit: int = 20) -> dict[str, object]:
        self.directory._enabled()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ConsoleError("admin_invalid_request")
        if after:
            _identifier(after)
        with self.store._connection() as connection:
            self.directory._organization(connection, organization_id)
            rows = connection.execute(
                "SELECT id, organization_id, membership_id, role, status, authorization_version, created_at, updated_at "
                "FROM console_grants WHERE organization_id = ? AND id > ? ORDER BY id LIMIT ?",
                (organization_id, after, limit + 1),
            ).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def begin_login(self, *, organization_id: str, state: str, browser_cookie: str,
                    nonce: str, pkce_verifier: str) -> ConsoleFlow:
        self.directory._enabled()
        for value in (state, browser_cookie, nonce, pkce_verifier):
            _require_secret(value, "admin_invalid_request")
        now = self.store._now()
        flow_id = "cfl_" + secrets.token_urlsafe(24)
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            organization = self.directory._organization(connection, organization_id)
            issuer = self.directory.config.oidc_issuers.get(organization["issuer"])
            if issuer is None or issuer.login is None:
                raise ConsoleError("admin_login_unavailable")
            connection.execute(
                "UPDATE console_login_flows SET status = 'failed', state_hash = NULL, "
                "browser_cookie_hash = NULL, encrypted_flow = NULL WHERE expires_at <= ? "
                "AND status IN ('pending', 'exchanging')", (_isoformat(now),),
            )
            if connection.execute("SELECT COUNT(*) FROM console_login_flows WHERE status IN ('pending', 'exchanging')").fetchone()[0] >= 1000:
                raise ConsoleError("admin_login_capacity_reached")
            encrypted = self.store._encrypt(
                json.dumps({"nonce": nonce, "pkce_verifier": pkce_verifier}, separators=(",", ":")).encode(),
                associated_data=("console-flow\x00" + flow_id).encode(),
            )
            connection.execute(
                "INSERT INTO console_login_flows (id, organization_id, issuer, state_hash, browser_cookie_hash, "
                "encrypted_flow, status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (flow_id, organization_id, issuer.issuer, self.store._digest("console-state", state),
                 self.store._digest("console-browser", browser_cookie), encrypted, _isoformat(now),
                 _isoformat(now + timedelta(seconds=self.directory.config.session_broker.enrollment_ttl_seconds))),
            )
        return ConsoleFlow(flow_id, organization_id, issuer.issuer, nonce, pkce_verifier)

    def consume_callback(self, *, state: str, browser_cookie: str) -> ConsoleFlow:
        for value in (state, browser_cookie):
            _require_secret(value, "admin_login_invalid")
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM console_login_flows WHERE state_hash = ? AND status = 'pending'",
                                     (self.store._digest("console-state", state),)).fetchone()
            if (row is None or _parse_time(row["expires_at"]) <= self.store._now()
                    or not hmac.compare_digest(bytes(row["browser_cookie_hash"]), self.store._digest("console-browser", browser_cookie))):
                raise ConsoleError("admin_login_invalid")
            plain = self.store._decrypt(bytes(row["encrypted_flow"]), associated_data=("console-flow\x00" + row["id"]).encode())
            try:
                values = json.loads(plain)
                _require_secret(values["nonce"], "admin_login_invalid")
                _require_secret(values["pkce_verifier"], "admin_login_invalid")
            except (ValueError, KeyError, TypeError):
                raise ConsoleError("admin_login_invalid") from None
            connection.execute(
                "UPDATE console_login_flows SET status = 'exchanging', state_hash = NULL, "
                "browser_cookie_hash = NULL, encrypted_flow = NULL WHERE id = ?", (row["id"],),
            )
            return ConsoleFlow(row["id"], row["organization_id"], row["issuer"], values["nonce"], values["pkce_verifier"])

    def fail_login(self, flow_id: str) -> None:
        with self.store._connection() as connection:
            connection.execute(
                "UPDATE console_login_flows SET status = 'failed', state_hash = NULL, "
                "browser_cookie_hash = NULL, encrypted_flow = NULL WHERE id = ? AND status IN ('pending', 'exchanging')",
                (flow_id,),
            )

    def complete_login(self, flow: ConsoleFlow, claims: dict[str, object]) -> str:
        """Only called with a cryptographically validated ID token by ConsoleService."""
        self.directory._enabled()
        now = self.store._now()
        if claims.get("iss") != flow.issuer or not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ConsoleError("admin_login_invalid")
        credential = "hox_c_" + secrets.token_urlsafe(32)
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT expires_at FROM console_login_flows WHERE id = ? AND organization_id = ? "
                "AND issuer = ? AND status = 'exchanging'", (flow.flow_id, flow.organization_id, flow.issuer),
            ).fetchone()
            if pending is None or _parse_time(pending["expires_at"]) <= now:
                raise ConsoleError("admin_login_invalid")
            authority = connection.execute(
                "SELECT m.id AS membership_id, m.authorization_version AS membership_version, "
                "g.id AS grant_id, g.authorization_version AS grant_version FROM onboarding_memberships m "
                "JOIN console_grants g ON g.membership_id = m.id AND g.organization_id = m.organization_id "
                "WHERE m.organization_id = ? AND m.issuer = ? AND m.subject = ? AND m.status = 'active' AND g.status = 'active'",
                (flow.organization_id, flow.issuer, claims["sub"]),
            ).fetchone()
            if authority is None:
                raise ConsoleError("admin_access_denied")
            connection.execute("UPDATE console_sessions SET revoked_at = ? WHERE grant_id = ? AND revoked_at IS NULL",
                               (_isoformat(now), authority["grant_id"]))
            session_id = "cse_" + secrets.token_urlsafe(24)
            connection.execute(
                "INSERT INTO console_sessions (id, organization_id, membership_id, grant_id, membership_version, "
                "grant_version, credential_hash, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, flow.organization_id, authority["membership_id"], authority["grant_id"],
                 authority["membership_version"], authority["grant_version"], self.store._digest("console-session", credential),
                 _isoformat(now), _isoformat(now), _isoformat(now + CONSOLE_ABSOLUTE_TTL)),
            )
            connection.execute("UPDATE console_login_flows SET status = 'completed' WHERE id = ?", (flow.flow_id,))
            record_console_event(connection, now, flow.organization_id, "session_started", authority["membership_id"],
                                 membership_id=authority["membership_id"], grant_id=authority["grant_id"], session_id=session_id)
        return credential

    def _current(self, connection, credential: str) -> ConsolePrincipal:
        self.directory._enabled()
        if not isinstance(credential, str) or not _CREDENTIAL.fullmatch(credential):
            raise ConsoleError("admin_session_required")
        row = connection.execute(
            "SELECT s.*, m.name, m.issuer, m.status AS member_status, m.authorization_version AS current_member_version, "
            "g.role, g.status AS grant_status, g.authorization_version AS current_grant_version, o.name AS organization_name "
            "FROM console_sessions s JOIN onboarding_memberships m ON m.id = s.membership_id AND m.organization_id = s.organization_id "
            "JOIN console_grants g ON g.id = s.grant_id AND g.membership_id = m.id AND g.organization_id = s.organization_id "
            "JOIN onboarding_organizations o ON o.id = s.organization_id AND o.issuer = m.issuer "
            "WHERE s.credential_hash = ?", (self.store._digest("console-session", credential),),
        ).fetchone()
        now = self.store._now()
        if (row is None or row["revoked_at"] is not None or row["member_status"] != "active"
                or row["grant_status"] != "active" or row["role"] not in CONSOLE_ROLES
                or row["membership_version"] != row["current_member_version"]
                or row["grant_version"] != row["current_grant_version"] or _parse_time(row["expires_at"]) <= now
                or not now - CONSOLE_IDLE_TTL < _parse_time(row["last_seen_at"]) <= now):
            raise ConsoleError("admin_session_required")
        issuer = self.directory.config.oidc_issuers.get(row["issuer"])
        if issuer is None or issuer.login is None:
            raise ConsoleError("admin_session_required")
        connection.execute("UPDATE console_sessions SET last_seen_at = ? WHERE id = ?", (_isoformat(now), row["id"]))
        return ConsolePrincipal(row["id"], row["organization_id"], row["organization_name"],
                                row["membership_id"], row["name"], row["role"], _parse_time(row["expires_at"]))

    def authenticate(self, credential: str) -> ConsolePrincipal:
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._current(connection, credential)

    def csrf_token(self, credential: str) -> str:
        return "hox_cs_" + base64.urlsafe_b64encode(self.store._digest("console-csrf", credential)).rstrip(b"=").decode("ascii")

    def require_csrf(self, credential: str, csrf: str) -> None:
        if not isinstance(csrf, str) or not csrf.isascii() or not hmac.compare_digest(self.csrf_token(credential), csrf):
            raise ConsoleError("admin_csrf_rejected")

    def logout(self, credential: str) -> None:
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            principal = self._current(connection, credential)
            now = self.store._now()
            connection.execute("UPDATE console_sessions SET revoked_at = ? WHERE id = ?", (_isoformat(now), principal.session_id))
            record_console_event(connection, now, principal.organization_id, "session_logged_out", principal.membership_id,
                                 membership_id=principal.membership_id, session_id=principal.session_id)

    def disable_member(self, credential: str, *, membership_id: str, expected_version: int) -> dict[str, object]:
        if type(expected_version) is not int or expected_version < 1:
            raise ConsoleError("admin_invalid_request")
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            principal = self._current(connection, credential)
            if principal.role != "member_admin":
                raise ConsoleError("admin_access_denied")
            if membership_id == principal.membership_id:
                raise ConsoleError("admin_self_removal_refused")
            member = self.directory._member(connection, principal.organization_id, membership_id)
            if member["status"] != "disabled" and member["authorization_version"] != expected_version:
                raise ConsoleError("admin_member_changed")
            return self.directory._disable(connection, member, decision_actor=principal.membership_id, decision_scope="organization")
