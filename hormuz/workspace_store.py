"""Transactional signup and origin-bound customer browser authority."""

from __future__ import annotations

import hmac
import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from .console_store import CONSOLE_ABSOLUTE_TTL, CONSOLE_IDLE_TTL
from .onboarding import normalize_email
from .session_store import SessionStoreError, _isoformat, _parse_time, _require_secret


class WorkspaceError(SessionStoreError):
    """Fixed, content-free workspace failure code."""


@dataclass(frozen=True)
class WorkspaceFlow:
    id: str
    issuer: str
    workspace_id: str | None
    handoff_id: str | None
    nonce: str = field(repr=False)
    verifier: str = field(repr=False)


class WorkspaceStore:
    def __init__(self, broker):
        self.broker = broker
        self.store = broker.store

    @property
    def origin(self):
        return self.broker.config.session_broker.public_base_url

    def begin_login(self, *, workspace_id=None, handoff_id=None):
        settings = self.broker.config.session_broker
        issuer = settings.workspace_signup_issuer
        state, cookie, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        flow_id = "wfl_" + secrets.token_urlsafe(24)
        now = self.store._now()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE workspace_login_flows SET status = 'failed', state_hash = NULL, browser_cookie_hash = NULL, encrypted_flow = NULL WHERE expires_at <= ? AND status IN ('pending', 'exchanging')", (_isoformat(now),))
            if connection.execute("SELECT COUNT(*) FROM workspace_login_flows WHERE status IN ('pending', 'exchanging')").fetchone()[0] >= 1000:
                raise WorkspaceError("workspace_login_capacity")
            if workspace_id:
                workspace = self._workspace(connection, workspace_id)
                issuer = connection.execute("SELECT issuer FROM onboarding_organizations WHERE id = ?", (workspace["organization_id"],)).fetchone()[0]
            if handoff_id:
                handoff = connection.execute("SELECT * FROM workspace_handoffs WHERE id = ? AND workspace_id = ? AND consumed_at IS NULL AND secret_hash IS NULL", (handoff_id, workspace_id)).fetchone()
                if handoff is None or _parse_time(handoff["expires_at"]) <= now:
                    raise WorkspaceError("workspace_login_invalid")
                self._domain(connection, handoff["domain_id"], version=handoff["domain_version"])
            encrypted = self.store._encrypt(json.dumps({"nonce": nonce, "verifier": verifier}).encode(), associated_data=("workspace-flow\x00" + flow_id).encode())
            connection.execute("INSERT INTO workspace_login_flows VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)", (flow_id, issuer, workspace_id, handoff_id, self.store._digest("workspace-state", state), self.store._digest("workspace-browser", cookie), encrypted, _isoformat(now), _isoformat(now + timedelta(seconds=settings.enrollment_ttl_seconds))))
        return WorkspaceFlow(flow_id, issuer, workspace_id, handoff_id, nonce, verifier), state, cookie

    def consume_callback(self, state, cookie):
        for value in (state, cookie):
            _require_secret(value, "workspace_login_invalid")
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM workspace_login_flows WHERE state_hash = ? AND status = 'pending'", (self.store._digest("workspace-state", state),)).fetchone()
            if row is None or _parse_time(row["expires_at"]) <= self.store._now() or not hmac.compare_digest(bytes(row["browser_cookie_hash"]), self.store._digest("workspace-browser", cookie)):
                raise WorkspaceError("workspace_login_invalid")
            plain = self.store._decrypt(bytes(row["encrypted_flow"]), associated_data=("workspace-flow\x00" + row["id"]).encode())
            values = json.loads(plain)
            connection.execute("UPDATE workspace_login_flows SET status = 'exchanging', state_hash = NULL, browser_cookie_hash = NULL, encrypted_flow = NULL WHERE id = ?", (row["id"],))
            return WorkspaceFlow(row["id"], row["issuer"], row["workspace_id"], row["handoff_id"], values["nonce"], values["verifier"])

    def fail_login(self, flow_id):
        with self.store._connection() as connection:
            connection.execute("UPDATE workspace_login_flows SET status = 'failed', state_hash = NULL, browser_cookie_hash = NULL, encrypted_flow = NULL WHERE id = ? AND status IN ('pending', 'exchanging')", (flow_id,))

    def complete_login(self, flow, claims):
        """Called only after signature, audience, issuer and nonce verification."""
        if claims.get("iss") != flow.issuer or not isinstance(claims.get("sub"), str) or not 1 <= len(claims["sub"]) <= 1024:
            raise WorkspaceError("workspace_login_invalid")
        if claims.get("email_verified") is not True:
            raise WorkspaceError("workspace_email_required")
        email = normalize_email(claims.get("email"))
        now = self.store._now()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute("SELECT * FROM workspace_login_flows WHERE id = ? AND status = 'exchanging'", (flow.id,)).fetchone()
            if pending is None or _parse_time(pending["expires_at"]) <= now or (pending["issuer"], pending["workspace_id"], pending["handoff_id"]) != (flow.issuer, flow.workspace_id, flow.handoff_id):
                raise WorkspaceError("workspace_login_invalid")
            workspace = self._workspace(connection, flow.workspace_id) if flow.workspace_id else self._provision(connection, flow.issuer, claims["sub"], email)
            authority = self._authority(connection, workspace, flow.issuer, claims["sub"])
            if flow.handoff_id:
                handoff = connection.execute("SELECT * FROM workspace_handoffs WHERE id = ? AND workspace_id = ? AND consumed_at IS NULL AND secret_hash IS NULL", (flow.handoff_id, workspace["id"])).fetchone()
                if handoff is None or _parse_time(handoff["expires_at"]) <= now:
                    raise WorkspaceError("workspace_login_invalid")
                domain = self._domain(connection, handoff["domain_id"], version=handoff["domain_version"])
                token = secrets.token_urlsafe(32)
                connection.execute("UPDATE workspace_handoffs SET secret_hash = ?, membership_id = ?, membership_version = ?, grant_id = ?, grant_version = ?, expires_at = ? WHERE id = ?", (self.store._digest("workspace-handoff", token), authority["membership_id"], authority["membership_version"], authority["grant_id"], authority["grant_version"], _isoformat(now + timedelta(seconds=60)), handoff["id"]))
                result = {"handoff_token": token, "handoff_origin": "https://" + domain["hostname"], "slug": workspace["slug"]}
            else:
                credential = self._new_session(connection, workspace, authority, self.origin)
                result = {"credential": credential, "slug": workspace["slug"]}
            connection.execute("UPDATE workspace_login_flows SET status = 'completed' WHERE id = ?", (flow.id,))
            return result

    def _provision(self, connection, issuer, subject, email):
        # Account/workspace identity survives changing the public dashboard
        # origin. Browser credentials retain the broker's origin binding.
        account = hmac.new(self.broker.config.session_broker.master_key, b"hormuz/workspace/account/v1\x00" + json.dumps([issuer, subject], separators=(",", ":")).encode(), hashlib.sha256).digest()
        existing = connection.execute("SELECT workspace_id FROM workspace_accounts WHERE account_hash = ?", (account,)).fetchone()
        if existing:
            return self._workspace(connection, existing["workspace_id"])
        if issuer != self.broker.config.session_broker.workspace_signup_issuer:
            raise WorkspaceError("workspace_access_denied")
        now = _isoformat(self.store._now())
        workspace_id, organization_id, team_id, membership_id, grant_id = (prefix + secrets.token_urlsafe(24) for prefix in ("wsp_", "worg_", "wteam_", "wmem_", "wgr_"))
        # Names and URLs do not disclose an account's email or OIDC subject.
        slug = "workspace-" + secrets.token_hex(8)
        connection.execute("INSERT INTO onboarding_organizations VALUES (?, 'My workspace', ?, ?)", (organization_id, issuer, now))
        connection.execute("INSERT INTO onboarding_teams VALUES (?, ?, 'Everyone', ?)", (team_id, organization_id, now))
        email_hash = self.broker.directory._email_hash(organization_id, email)
        connection.execute("INSERT INTO onboarding_memberships (id, organization_id, team_id, issuer, subject, name, email_hash, allowed_clients, clearance, status, authorization_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'Workspace owner', ?, '[]', 'public', 'active', 1, ?, ?)", (membership_id, organization_id, team_id, issuer, subject, email_hash, now, now))
        connection.execute("INSERT INTO console_grants VALUES (?, ?, ?, 'member_admin', 'active', 1, ?, ?)", (grant_id, organization_id, membership_id, now, now))
        connection.execute("INSERT INTO workspaces VALUES (?, ?, ?, ?, 'active', ?)", (workspace_id, organization_id, slug, membership_id, now))
        connection.execute("INSERT INTO workspace_accounts VALUES (?, ?, ?)", (account, workspace_id, membership_id))
        self.broker.directory._event(connection, organization_id, "workspace_created", membership_id=membership_id, decision_actor=membership_id)
        return self._workspace(connection, workspace_id)

    def _workspace(self, connection, workspace_id):
        row = connection.execute("SELECT * FROM workspaces WHERE id = ? AND status = 'active'", (workspace_id,)).fetchone()
        if row is None:
            raise WorkspaceError("workspace_access_denied")
        return row

    def _authority(self, connection, workspace, issuer, subject):
        row = connection.execute("SELECT m.id AS membership_id, m.authorization_version AS membership_version, g.id AS grant_id, g.authorization_version AS grant_version, g.role FROM onboarding_memberships m JOIN console_grants g ON g.organization_id = m.organization_id AND g.membership_id = m.id WHERE m.organization_id = ? AND m.issuer = ? AND m.subject = ? AND m.status = 'active' AND g.status = 'active'", (workspace["organization_id"], issuer, subject)).fetchone()
        if row is None:
            raise WorkspaceError("workspace_access_denied")
        return row

    def _new_session(self, connection, workspace, authority, origin, domain=None):
        now = self.store._now()
        credential = "hox_w_" + secrets.token_urlsafe(32)
        connection.execute("INSERT INTO workspace_sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)", ("wse_" + secrets.token_urlsafe(24), workspace["id"], authority["membership_id"], authority["membership_version"], authority["grant_id"], authority["grant_version"], self.store._digest("workspace-session", credential), origin, domain["id"] if domain else None, domain["version"] if domain else None, _isoformat(now), _isoformat(now), _isoformat(now + CONSOLE_ABSOLUTE_TTL)))
        return credential

    def _current(self, connection, credential, origin):
        if not isinstance(credential, str) or not re.fullmatch(r"hox_w_[A-Za-z0-9_-]{43}", credential):
            raise WorkspaceError("workspace_session_required")
        row = connection.execute("SELECT s.*, w.slug, w.organization_id, w.owner_membership_id, w.status AS workspace_status, m.status AS member_status, m.authorization_version AS current_member_version, g.status AS grant_status, g.authorization_version AS current_grant_version, g.role, o.name AS workspace_name, o.issuer FROM workspace_sessions s JOIN workspaces w ON w.id = s.workspace_id JOIN onboarding_memberships m ON m.id = s.membership_id AND m.organization_id = w.organization_id JOIN console_grants g ON g.id = s.grant_id AND g.membership_id = m.id AND g.organization_id = w.organization_id JOIN onboarding_organizations o ON o.id = w.organization_id WHERE s.credential_hash = ?", (self.store._digest("workspace-session", credential),)).fetchone()
        now = self.store._now()
        if (row is None or row["origin"] != origin or row["revoked_at"] or row["workspace_status"] != "active" or row["member_status"] != "active" or row["grant_status"] != "active" or row["membership_version"] != row["current_member_version"] or row["grant_version"] != row["current_grant_version"] or _parse_time(row["expires_at"]) <= now or _parse_time(row["last_seen_at"]) + CONSOLE_IDLE_TTL <= now or row["issuer"] not in self.broker.config.oidc_issuers or self.broker.config.oidc_issuers[row["issuer"]].login is None):
            raise WorkspaceError("workspace_session_required")
        if row["domain_id"]:
            self._domain(connection, row["domain_id"], version=row["domain_version"])
        elif origin != self.origin:
            raise WorkspaceError("workspace_session_required")
        connection.execute("UPDATE workspace_sessions SET last_seen_at = ? WHERE id = ?", (_isoformat(now), row["id"]))
        return dict(row)

    def authenticate(self, credential, origin):
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._current(connection, credential, origin)

    def csrf(self, credential, origin):
        self.authenticate(credential, origin)
        return self.store._digest("workspace-csrf", credential).hex()

    def require_csrf(self, credential, origin, token):
        if not isinstance(token, str) or not hmac.compare_digest(self.csrf(credential, origin), token):
            raise WorkspaceError("workspace_csrf_rejected")

    def logout(self, credential, origin):
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._current(connection, credential, origin)
            # Sign out across this account's canonical and custom addresses.
            connection.execute("UPDATE workspace_sessions SET revoked_at = ? WHERE membership_id = ? AND revoked_at IS NULL", (_isoformat(self.store._now()), current["membership_id"]))
            connection.execute("UPDATE workspace_handoffs SET consumed_at = ? WHERE membership_id = ? AND consumed_at IS NULL", (_isoformat(self.store._now()), current["membership_id"]))

    def _domain(self, connection, domain_id, *, version=None):
        row = connection.execute("SELECT * FROM workspace_domains WHERE id = ? AND status = 'active'", (domain_id,)).fetchone()
        if row is None or not row["verified_until"] or _parse_time(row["verified_until"]) <= self.store._now() or version is not None and row["version"] != version:
            raise WorkspaceError("workspace_host_rejected")
        self._workspace(connection, row["workspace_id"])
        return row

    def begin_handoff(self, hostname):
        cookie = secrets.token_urlsafe(32)
        handoff_id = "whf_" + secrets.token_urlsafe(24)
        now = self.store._now()
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute("SELECT id FROM workspace_domains WHERE hostname = ?", (hostname,)).fetchone()
            if candidate is None:
                raise WorkspaceError("workspace_host_rejected")
            domain = self._domain(connection, candidate["id"])
            # Repeated custom-host starts cannot allocate unbounded live handoffs.
            if connection.execute("SELECT COUNT(*) FROM workspace_handoffs WHERE consumed_at IS NULL AND expires_at > ?", (_isoformat(now),)).fetchone()[0] >= 1000:
                raise WorkspaceError("workspace_login_capacity")
            connection.execute("INSERT INTO workspace_handoffs (id, workspace_id, domain_id, domain_version, browser_cookie_hash, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (handoff_id, domain["workspace_id"], domain["id"], domain["version"], self.store._digest("workspace-handoff-browser", cookie), _isoformat(now), _isoformat(now + timedelta(minutes=5))))
        return handoff_id, cookie, domain["workspace_id"]

    def consume_handoff(self, token, cookie, origin):
        for value in (token, cookie):
            _require_secret(value, "workspace_login_invalid")
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM workspace_handoffs WHERE secret_hash = ? AND consumed_at IS NULL", (self.store._digest("workspace-handoff", token),)).fetchone()
            if row is None or _parse_time(row["expires_at"]) <= self.store._now() or not hmac.compare_digest(bytes(row["browser_cookie_hash"]), self.store._digest("workspace-handoff-browser", cookie)):
                raise WorkspaceError("workspace_login_invalid")
            domain = self._domain(connection, row["domain_id"], version=row["domain_version"])
            if origin != "https://" + domain["hostname"]:
                raise WorkspaceError("workspace_host_rejected")
            workspace = self._workspace(connection, row["workspace_id"])
            member = connection.execute("SELECT issuer, subject FROM onboarding_memberships WHERE id = ?", (row["membership_id"],)).fetchone()
            if member is None:
                raise WorkspaceError("workspace_access_denied")
            authority = self._authority(connection, workspace, member["issuer"], member["subject"])
            if any(row[key] != authority[key] for key in ("membership_id", "membership_version", "grant_id", "grant_version")):
                raise WorkspaceError("workspace_access_denied")
            credential = self._new_session(connection, workspace, authority, origin, domain)
            connection.execute("UPDATE workspace_handoffs SET consumed_at = ?, secret_hash = NULL WHERE id = ?", (_isoformat(self.store._now()), row["id"]))
            return credential, workspace["slug"]
