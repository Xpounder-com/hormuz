"""Server-local team administration and browser membership authorization.

The directory shares the session store's transaction and keyed digest boundary so
offboarding cannot race a separate credential database. No method grants remote
administrative authority to a human inference session.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import GatewayConfig, Identity
from ._console_authority import revoke_member_console_access
from .session_store import (
    AuthorizationFlow, SQLiteSessionStore, SessionPrincipal, SessionStoreError,
    _isoformat, _parse_time, _require_binding, _require_secret,
)


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
_INVITE = re.compile(r"hox_i_[A-Za-z0-9_-]{43}\Z")
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}\Z")
_DOMAIN_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
_CLIENTS = frozenset({"codex", "claude-code"})
_CLEARANCES = frozenset({"public", "internal", "confidential", "restricted"})


@dataclass(frozen=True)
class Invitation:
    invitation_id: str
    membership_id: str
    organization_id: str
    expires_at: datetime
    code: str = field(repr=False)


def normalize_email(value: object) -> str:
    """Conservative matching, with no alias guessing or local-part case folding."""
    if not isinstance(value, str) or not value.isascii() or len(value) > 254 or value.count("@") != 1:
        raise SessionStoreError("onboarding_invalid_email")
    local, domain = value.split("@")
    if (
        not _EMAIL_LOCAL.fullmatch(local) or local.startswith(".") or local.endswith(".")
        or ".." in local or len(domain) > 253 or "." not in domain
        or any(not _DOMAIN_LABEL.fullmatch(label) for label in domain.split("."))
    ):
        raise SessionStoreError("onboarding_invalid_email")
    return local + "@" + domain.lower()


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SessionStoreError("onboarding_invalid_identifier")


def _name(value: str) -> None:
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 128
        or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
    ):
        raise SessionStoreError("onboarding_invalid_name")


class TeamDirectory:
    def __init__(self, config: GatewayConfig, store: SQLiteSessionStore):
        self.config = config
        self.store = store
        # Check even when onboarding is disabled: a configuration edit must not
        # reintroduce static/OIDC access to a managed organization's tombstones.
        self._configured = tuple(config.identities_by_token.values()) + tuple(config.identities_by_subject.values())
        with store._connection() as connection:
            organizations = {row[0] for row in connection.execute("SELECT id FROM onboarding_organizations")}
            teams = {row[0] for row in connection.execute("SELECT id FROM onboarding_teams")}
            if any(identity.organization_id in organizations or identity.team_id in teams for identity in self._configured):
                raise SessionStoreError("onboarding_configuration_conflict")

    def _enabled(self) -> None:
        if not self.config.session_broker.enabled or not self.config.session_broker.onboarding_enabled:
            raise SessionStoreError("onboarding_disabled")

    def organizations_for_issuer(self, issuer: str) -> tuple[str, ...]:
        self._enabled()
        with self.store._connection() as connection:
            return tuple(row[0] for row in connection.execute(
                "SELECT id FROM onboarding_organizations WHERE issuer = ? ORDER BY id", (issuer,),
            ))

    def managed_organization_ids(self) -> tuple[str, ...]:
        """Return the server-local tenant allowlist in stable order."""

        self._enabled()
        with self.store._connection() as connection:
            return tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT id FROM onboarding_organizations ORDER BY id"
                )
            )

    def manages_organization(self, organization_id: str | None) -> bool:
        with self.store._connection() as connection:
            return connection.execute(
                "SELECT id FROM onboarding_organizations WHERE id = ?", (organization_id,),
            ).fetchone() is not None

    def create_organization(self, *, organization_id: str, name: str, issuer: str) -> bool:
        self._enabled()
        _identifier(organization_id)
        _name(name)
        configured_issuer = self.config.oidc_issuers.get(issuer)
        if configured_issuer is None or configured_issuer.login is None or "email" not in configured_issuer.login.scopes:
            raise SessionStoreError("onboarding_email_login_issuer_required")
        if any(identity.organization_id == organization_id for identity in self._configured):
            raise SessionStoreError("onboarding_configuration_conflict")
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT name, issuer FROM onboarding_organizations WHERE id = ?", (organization_id,)).fetchone()
            if existing is not None:
                if (existing["name"], existing["issuer"]) != (name, issuer):
                    raise SessionStoreError("onboarding_organization_exists")
                return False
            connection.execute(
                "INSERT INTO onboarding_organizations (id, name, issuer, created_at) VALUES (?, ?, ?, ?)",
                (organization_id, name, issuer, _isoformat(self.store._now())),
            )
            self._event(connection, organization_id, "organization_created")
            return True

    def create_team(self, *, organization_id: str, team_id: str, name: str) -> bool:
        self._enabled()
        _identifier(organization_id)
        _identifier(team_id)
        _name(name)
        if any(identity.team_id == team_id for identity in self._configured):
            raise SessionStoreError("onboarding_configuration_conflict")
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._organization(connection, organization_id)
            existing = connection.execute("SELECT organization_id, name FROM onboarding_teams WHERE id = ?", (team_id,)).fetchone()
            if existing is not None:
                if (existing["organization_id"], existing["name"]) != (organization_id, name):
                    raise SessionStoreError("onboarding_team_exists")
                return False
            connection.execute(
                "INSERT INTO onboarding_teams (id, organization_id, name, created_at) VALUES (?, ?, ?, ?)",
                (team_id, organization_id, name, _isoformat(self.store._now())),
            )
            self._event(connection, organization_id, "team_created", team_id=team_id)
            return True

    def invite(
        self, *, organization_id: str, team_id: str, email: str, name: str,
        allowed_clients: tuple[str, ...], clearance: str = "internal", expires_in: int = 3600,
    ) -> Invitation:
        self._enabled()
        _identifier(organization_id)
        _identifier(team_id)
        _name(name)
        email_hash = self._email_hash(organization_id, email)
        if not allowed_clients or len(allowed_clients) != len(set(allowed_clients)) or set(allowed_clients) - _CLIENTS:
            raise SessionStoreError("onboarding_invalid_clients")
        if clearance not in _CLEARANCES:
            raise SessionStoreError("onboarding_invalid_clearance")
        self._expiry(expires_in)
        now = _isoformat(self.store._now())
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            organization = self._organization(connection, organization_id)
            if connection.execute("SELECT id FROM onboarding_teams WHERE id = ? AND organization_id = ?", (team_id, organization_id)).fetchone() is None:
                raise SessionStoreError("onboarding_team_unavailable")
            if connection.execute("SELECT id FROM onboarding_memberships WHERE organization_id = ? AND email_hash = ?", (organization_id, email_hash)).fetchone() is not None:
                raise SessionStoreError("onboarding_member_exists")
            member_id = "mem_" + secrets.token_urlsafe(24)
            connection.execute(
                """
                INSERT INTO onboarding_memberships (
                    id, organization_id, team_id, issuer, name, email_hash, allowed_clients,
                    clearance, status, authorization_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)
                """,
                (member_id, organization_id, team_id, organization["issuer"], name, email_hash,
                 json.dumps(sorted(allowed_clients)), clearance, now, now),
            )
            return self._issue_invitation(connection, organization_id, member_id, team_id, 1, expires_in)

    def reinvite(self, *, organization_id: str, membership_id: str, expires_in: int = 3600) -> Invitation:
        self._enabled()
        self._expiry(expires_in)
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            member = self._member(connection, organization_id, membership_id)
            if member["status"] != "disabled":
                raise SessionStoreError("onboarding_disabled_member_required")
            version = int(member["authorization_version"]) + 1
            connection.execute(
                "UPDATE onboarding_memberships SET status = 'pending', authorization_version = ?, updated_at = ? WHERE id = ? AND organization_id = ?",
                (version, _isoformat(self.store._now()), membership_id, organization_id),
            )
            return self._issue_invitation(connection, organization_id, membership_id, member["team_id"], version, expires_in)

    def _issue_invitation(self, connection, organization_id, membership_id, team_id, version, expires_in) -> Invitation:
        now = self.store._now()
        invitation = Invitation("inv_" + secrets.token_urlsafe(24), membership_id, organization_id,
                                now + timedelta(seconds=expires_in), "hox_i_" + secrets.token_urlsafe(32))
        connection.execute(
            """
            INSERT INTO onboarding_invitations (
                id, organization_id, membership_id, authorization_version, secret_hash,
                status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (invitation.invitation_id, organization_id, membership_id, version,
             self.store._digest("invitation", invitation.code), _isoformat(now), _isoformat(invitation.expires_at)),
        )
        self._event(connection, organization_id, "invitation_issued", team_id=team_id,
                    membership_id=membership_id, invitation_id=invitation.invitation_id)
        return invitation

    def revoke_invitation(self, *, organization_id: str, invitation_id: str) -> bool:
        self._enabled()
        _identifier(organization_id)
        _identifier(invitation_id)
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            invitation = connection.execute(
                "SELECT membership_id, status, authorization_version FROM onboarding_invitations WHERE id = ? AND organization_id = ?",
                (invitation_id, organization_id),
            ).fetchone()
            if invitation is None:
                raise SessionStoreError("onboarding_invitation_unavailable")
            if invitation["status"] != "pending":
                return False
            member = self._member(connection, organization_id, invitation["membership_id"])
            self._disable(connection, member)
            return True

    def disable_member(self, *, organization_id: str, membership_id: str) -> dict[str, object]:
        self._enabled()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._disable(connection, self._member(connection, organization_id, membership_id))

    def _disable(self, connection: sqlite3.Connection, member: sqlite3.Row, *,
                 decision_actor: str = "server_local_operator", decision_scope: str = "server_local") -> dict[str, object]:
        result = {"changed": False, "sessions_revoked": 0, "enrollments_invalidated": 0, "invitations_revoked": 0}
        if member["status"] == "disabled":
            return result
        now = self.store._now()
        scope = (member["organization_id"], member["id"])
        connection.execute(
            "UPDATE onboarding_memberships SET status = 'disabled', authorization_version = authorization_version + 1, updated_at = ? WHERE organization_id = ? AND id = ?",
            (_isoformat(now), *scope),
        )
        sessions = connection.execute(
            "SELECT id FROM human_sessions WHERE organization_id = ? AND membership_id = ? AND revoked_at IS NULL", scope,
        ).fetchall()
        result["sessions_revoked"] = connection.execute(
            "UPDATE human_sessions SET revoked_at = ? WHERE organization_id = ? AND membership_id = ? AND revoked_at IS NULL", (_isoformat(now), *scope),
        ).rowcount
        for session in sessions:
            self.store._record_event(connection, session_id=session["id"], event_type="membership_disabled", occurred_at=now,
                                     organization_id=scope[0], target_actor_id=scope[1], target_team_id=member["team_id"],
                                     decision_actor_id=decision_actor, decision_scope=decision_scope, reason_code="membership_disabled")
        result["invitations_revoked"] = connection.execute(
            "UPDATE onboarding_invitations SET status = 'revoked', secret_hash = NULL, completed_at = ? WHERE organization_id = ? AND membership_id = ? AND status = 'pending'", (_isoformat(now), *scope),
        ).rowcount
        result["enrollments_invalidated"] = connection.execute(
            """
            UPDATE session_enrollments SET status = 'failed', secret_hash = NULL,
                encrypted_flow = NULL, state_hash = NULL, browser_cookie_hash = NULL
            WHERE organization_id = ? AND membership_id = ?
              AND status IN ('pending', 'authorizing', 'exchanging', 'authorized')
            """, scope,
        ).rowcount
        revoke_member_console_access(connection, now, *scope, actor_id=decision_actor)
        self._event(connection, scope[0], "member_disabled", team_id=member["team_id"], membership_id=scope[1], decision_actor=decision_actor)
        result["changed"] = True
        return result

    def attach_invitation(self, *, enrollment_id: str, state: str, browser_cookie: str, code: str) -> AuthorizationFlow:
        """Bind a browser POST to its existing OAuth flow, without consuming the invite."""
        self._enabled()
        _require_secret(state, "invalid_authorization_state")
        _require_secret(browser_cookie, "invalid_browser_cookie")
        if not isinstance(code, str) or not _INVITE.fullmatch(code):
            raise SessionStoreError("onboarding_invitation_unavailable")
        now = self.store._now()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM session_enrollments WHERE id = ? AND state_hash = ? AND status = 'authorizing'",
                (enrollment_id, self.store._digest("state", state)),
            ).fetchone()
            if row is None or not hmac.compare_digest(bytes(row["browser_cookie_hash"]), self.store._digest("browser", browser_cookie)) or _parse_time(row["expires_at"]) <= now:
                raise SessionStoreError("invalid_callback_state")
            invitation = connection.execute(
                "SELECT * FROM onboarding_invitations WHERE secret_hash = ? AND organization_id = ?",
                (self.store._digest("invitation", code), row["organization_id"]),
            ).fetchone()
            member = self._available_invitation(connection, invitation, now)
            if row["invitation_id"] is not None or member["issuer"] != row["issuer"] or row["client_name"] not in json.loads(member["allowed_clients"]):
                raise SessionStoreError("onboarding_invitation_unavailable")
            connection.execute(
                "UPDATE session_enrollments SET invitation_id = ?, membership_id = ?, authorization_version = ? WHERE id = ?",
                (invitation["id"], member["id"], member["authorization_version"], enrollment_id),
            )
            transient = json.loads(self.store._decrypt(bytes(row["encrypted_flow"]), associated_data=enrollment_id.encode()))
            return AuthorizationFlow(enrollment_id, row["issuer"], row["client_name"], transient["nonce"], transient["pkce_verifier"], row["organization_id"], invitation["id"])

    def authorize_enrollment(self, *, flow: AuthorizationFlow, claims: dict[str, object]) -> None:
        """Claims must already have passed signature, issuer, audience and nonce validation."""
        self._enabled()
        subject = claims.get("sub")
        _require_binding(subject, "invalid_subject")
        now = self.store._now()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM session_enrollments WHERE id = ? AND status = 'exchanging' AND expires_at > ?",
                (flow.enrollment_id, _isoformat(now)),
            ).fetchone()
            if row is None or row["organization_id"] != flow.organization_id or row["issuer"] != flow.issuer or claims.get("iss") != flow.issuer:
                raise SessionStoreError("enrollment_unavailable")
            organization = self._organization(connection, row["organization_id"])
            if organization["issuer"] != flow.issuer:
                raise SessionStoreError("onboarding_membership_unavailable")
            invitation = None
            if row["invitation_id"] is not None:
                invitation = connection.execute(
                    "SELECT * FROM onboarding_invitations WHERE id = ? AND organization_id = ?",
                    (row["invitation_id"], row["organization_id"]),
                ).fetchone()
                member = self._available_invitation(connection, invitation, now)
                if claims.get("email_verified") is not True:
                    raise SessionStoreError("onboarding_verified_email_required")
                email_hash = self._email_hash(row["organization_id"], claims.get("email"))
                if not hmac.compare_digest(bytes(member["email_hash"]), email_hash):
                    raise SessionStoreError("onboarding_recipient_mismatch")
                if member["subject"] is not None and member["subject"] != subject:
                    raise SessionStoreError("onboarding_subject_mismatch")
                collision = connection.execute(
                    "SELECT id FROM onboarding_memberships WHERE organization_id = ? AND issuer = ? AND subject = ? AND id != ?",
                    (row["organization_id"], flow.issuer, subject, member["id"]),
                ).fetchone()
                if collision is not None:
                    raise SessionStoreError("onboarding_subject_already_bound")
            else:
                member = connection.execute(
                    "SELECT * FROM onboarding_memberships WHERE organization_id = ? AND issuer = ? AND subject = ? AND status = 'active'",
                    (row["organization_id"], flow.issuer, subject),
                ).fetchone()
            if member is None or member["issuer"] != flow.issuer or row["client_name"] not in json.loads(member["allowed_clients"]):
                raise SessionStoreError("onboarding_membership_unavailable")
            if invitation is not None:
                connection.execute(
                    "UPDATE onboarding_memberships SET status = 'active', subject = ?, updated_at = ? WHERE id = ? AND organization_id = ?",
                    (subject, _isoformat(now), member["id"], row["organization_id"]),
                )
                connection.execute(
                    "UPDATE onboarding_invitations SET status = 'accepted', secret_hash = NULL, completed_at = ? WHERE id = ? AND organization_id = ?",
                    (_isoformat(now), invitation["id"], row["organization_id"]),
                )
                self._event(connection, row["organization_id"], "invitation_accepted", team_id=member["team_id"],
                            membership_id=member["id"], invitation_id=invitation["id"], decision_actor=member["id"])
            connection.execute(
                """
                UPDATE session_enrollments SET status = 'authorized', subject = ?, actor_id = ?,
                    team_id = ?, clearance = ?, membership_id = ?, authorization_version = ?,
                    authorized_at = ?, encrypted_flow = NULL WHERE id = ?
                """,
                (subject, member["id"], member["team_id"], member["clearance"], member["id"], member["authorization_version"], _isoformat(now), flow.enrollment_id),
            )

    def identity_for_session(self, principal: SessionPrincipal) -> Identity:
        self._enabled()
        with self.store._connection() as connection:
            row = connection.execute(
                """
                SELECT members.*, teams.name AS team_name FROM onboarding_memberships AS members
                JOIN onboarding_teams AS teams ON teams.id = members.team_id AND teams.organization_id = members.organization_id
                WHERE members.id = ? AND members.organization_id = ? AND members.issuer = ? AND members.subject = ?
                  AND members.status = 'active' AND members.authorization_version = ?
                """,
                (principal.membership_id, principal.organization_id, principal.issuer, principal.subject, principal.authorization_version),
            ).fetchone()
            if row is None:
                raise SessionStoreError("session_authorization_removed")
            return Identity(token_env="", token="", actor_id=row["id"], actor_name=row["name"],
                            team_id=row["team_id"], team_name=row["team_name"], allowed_clients=tuple(json.loads(row["allowed_clients"])),
                            organization_id=row["organization_id"], clearance=row["clearance"], authentication_source=f"session:{row['issuer']}")

    def list_records(self, kind: str, *, organization_id: str | None = None, after: str = "", limit: int = 50) -> dict[str, object]:
        """Explicit projections: hashes, subjects, codes and email never leave this API."""
        self._enabled()
        projections = {
            "organizations": "id, name, issuer, created_at",
            "teams": "id, organization_id, name, created_at",
            "memberships": "id, organization_id, team_id, name, allowed_clients, clearance, status, authorization_version, created_at, updated_at",
            "invitations": "id, organization_id, membership_id, status, created_at, expires_at, completed_at",
            "events": "id, organization_id, team_id, membership_id, invitation_id, event_type, decision_actor, occurred_at",
        }
        if kind not in projections or type(limit) is not int or not 1 <= limit <= 100:
            raise SessionStoreError("onboarding_invalid_list")
        if after:
            _identifier(after)
        scoped = kind != "organizations"
        if scoped:
            _identifier(organization_id)
        with self.store._connection() as connection:
            if scoped:
                self._organization(connection, organization_id)
            query = f"SELECT {projections[kind]} FROM onboarding_{kind} WHERE id > ?"
            params = (after, organization_id, limit + 1) if scoped else (after, limit + 1)
            query += " AND organization_id = ?" if scoped else ""
            rows = connection.execute(query + " ORDER BY id LIMIT ?", params).fetchall()
            records = [dict(row) for row in rows[:limit]]
            for record in records:
                if kind == "memberships":
                    record["allowed_clients"] = json.loads(record["allowed_clients"])
                if kind == "invitations" and record["status"] == "pending" and _parse_time(record["expires_at"]) <= self.store._now():
                    record["status"] = "expired"
            return {"items": records, "next_cursor": records[-1]["id"] if len(rows) > limit else None}

    @staticmethod
    def _organization(connection: sqlite3.Connection, organization_id: str) -> sqlite3.Row:
        _identifier(organization_id)
        row = connection.execute("SELECT * FROM onboarding_organizations WHERE id = ?", (organization_id,)).fetchone()
        if row is None:
            raise SessionStoreError("onboarding_organization_unavailable")
        return row

    @staticmethod
    def _member(connection: sqlite3.Connection, organization_id: str, membership_id: str) -> sqlite3.Row:
        _identifier(organization_id)
        _identifier(membership_id)
        row = connection.execute("SELECT * FROM onboarding_memberships WHERE id = ? AND organization_id = ?", (membership_id, organization_id)).fetchone()
        if row is None:
            raise SessionStoreError("onboarding_membership_unavailable")
        return row

    def _available_invitation(self, connection, invitation, now):
        if invitation is None or invitation["status"] != "pending" or _parse_time(invitation["expires_at"]) <= now:
            raise SessionStoreError("onboarding_invitation_unavailable")
        member = self._member(connection, invitation["organization_id"], invitation["membership_id"])
        if member["status"] != "pending" or member["authorization_version"] != invitation["authorization_version"]:
            raise SessionStoreError("onboarding_invitation_unavailable")
        return member

    def _email_hash(self, organization_id: str, email: object) -> bytes:
        return self.store._digest("invitation_email:" + organization_id, normalize_email(email))

    @staticmethod
    def _expiry(seconds: int) -> None:
        if type(seconds) is not int or not 300 <= seconds <= 86400:
            raise SessionStoreError("onboarding_invalid_invitation_lifetime")

    def _event(self, connection, organization_id, event_type, *, team_id=None, membership_id=None, invitation_id=None, decision_actor="server_local_operator") -> None:
        connection.execute(
            "INSERT INTO onboarding_events (id, organization_id, team_id, membership_id, invitation_id, event_type, decision_actor, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("oev_" + secrets.token_urlsafe(24), organization_id, team_id, membership_id, invitation_id, event_type, decision_actor, _isoformat(self.store._now())),
        )
