"""Owner-scoped domain lifecycle with fail-closed leases and repairable removal."""

from __future__ import annotations

import hmac
import re
import secrets
import threading
from datetime import timedelta
from urllib.parse import urlsplit

from ._workspace_domain_provider import RenderDomainProvider
from .session_store import _isoformat, _parse_time
from .workspace_store import WorkspaceError


def normalize_hostname(value):
    if not isinstance(value, str) or len(value) > 253 or not value.isascii():
        raise WorkspaceError("workspace_domain_invalid")
    hostname = value.lower()
    labels = hostname.split(".")
    if (len(labels) < 3 or labels[0] == "www" or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or not re.fullmatch(r"[a-z]{2,63}", labels[-1])
            or hostname.endswith((".github.io", ".onrender.com", ".localhost", ".local", ".test", ".invalid", ".example"))):
        raise WorkspaceError("workspace_domain_invalid")
    return hostname


class WorkspaceDomains:
    def __init__(self, sessions, *, provider=None):
        self.sessions = sessions
        self.store = sessions.store
        settings = sessions.broker.config.session_broker
        self.provider = provider or (RenderDomainProvider(settings) if settings.workspace_domain_target else None)
        self._stop = threading.Event()
        self._worker = None

    def _owner(self, connection, credential, origin):
        current = self.sessions._current(connection, credential, origin)
        if current["role"] != "member_admin" or current["membership_id"] != current["owner_membership_id"]:
            raise WorkspaceError("workspace_access_denied")
        return current

    def list(self, credential, origin):
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.sessions._current(connection, credential, origin)
            rows = connection.execute("SELECT * FROM workspace_domains WHERE workspace_id = ? AND status != 'removed' ORDER BY created_at", (current["workspace_id"],)).fetchall()
            return [self._public(row) for row in rows]

    def _public(self, row):
        status = row["status"]
        if status == "active" and _parse_time(row["verified_until"]) <= self.store._now():
            status = "pending"
        return {"id": row["id"], "hostname": row["hostname"], "status": status, "last_error": row["last_error"],
                "url": "https://" + row["hostname"] + "/workspace" if status == "active" else None,
                "dns_records": [{"type": "TXT", "name": "_hormuz." + row["hostname"], "value": "hormuz-verification=" + row["challenge"]},
                                {"type": "CNAME", "name": row["hostname"], "value": self.provider.target if self.provider else ""}]}

    def claim(self, credential, origin, hostname):
        if self.provider is None:
            raise WorkspaceError("workspace_domains_disabled")
        hostname = normalize_hostname(hostname)
        if hostname == urlsplit(self.sessions.origin).hostname:
            raise WorkspaceError("workspace_domain_invalid")
        now = _isoformat(self.store._now())
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._owner(connection, credential, origin)
            existing = connection.execute("SELECT * FROM workspace_domains WHERE hostname = ?", (hostname,)).fetchone()
            if existing and existing["status"] != "removed":
                if existing["workspace_id"] != current["workspace_id"]:
                    raise WorkspaceError("workspace_domain_claimed")
                return self._public(existing)
            if connection.execute("SELECT COUNT(*) FROM workspace_domains WHERE workspace_id = ? AND status != 'removed'", (current["workspace_id"],)).fetchone()[0] >= 2 or connection.execute("SELECT COUNT(*) FROM workspace_domains WHERE status != 'removed'").fetchone()[0] >= 1000:
                raise WorkspaceError("workspace_domain_capacity")
            challenge = secrets.token_urlsafe(32)
            domain_id = existing["id"] if existing else "wdm_" + secrets.token_urlsafe(24)
            if existing:
                connection.execute("UPDATE workspace_domains SET workspace_id = ?, challenge = ?, status = 'pending', version = version + 1, provider_id = NULL, provider_requested_at = NULL, check_started_at = NULL, verified_until = NULL, last_error = NULL, created_at = ?, updated_at = ? WHERE id = ?", (current["workspace_id"], challenge, now, now, domain_id))
            else:
                connection.execute("INSERT INTO workspace_domains VALUES (?, ?, ?, ?, 'pending', 1, NULL, NULL, NULL, NULL, NULL, ?, ?)", (domain_id, current["workspace_id"], hostname, challenge, now, now))
            return self._public(connection.execute("SELECT * FROM workspace_domains WHERE id = ?", (domain_id,)).fetchone())

    def check(self, credential, origin, domain_id):
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._owner(connection, credential, origin)
            row = connection.execute("SELECT * FROM workspace_domains WHERE id = ? AND workspace_id = ? AND status != 'removed'", (domain_id, current["workspace_id"])).fetchone()
            if row is None:
                raise WorkspaceError("workspace_access_denied")
        self._reconcile(domain_id)

    def remove(self, credential, origin, domain_id):
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._owner(connection, credential, origin)
            row = connection.execute("SELECT * FROM workspace_domains WHERE id = ? AND workspace_id = ?", (domain_id, current["workspace_id"])).fetchone()
            if row is None:
                raise WorkspaceError("workspace_access_denied")
            if row["status"] == "removed":
                return
            if row["check_started_at"] and _parse_time(row["check_started_at"]) + timedelta(minutes=2) > self.store._now():
                raise WorkspaceError("workspace_domain_busy")
            connection.execute("UPDATE workspace_domains SET status = 'removing', version = version + 1, verified_until = NULL, last_error = NULL, check_started_at = NULL, updated_at = ? WHERE id = ?", (_isoformat(self.store._now()), domain_id))
            self._revoke(connection, domain_id)
        self._reconcile(domain_id)

    def _revoke(self, connection, domain_id):
        now = _isoformat(self.store._now())
        connection.execute("UPDATE workspace_sessions SET revoked_at = ? WHERE domain_id = ? AND revoked_at IS NULL", (now, domain_id))
        connection.execute("UPDATE workspace_handoffs SET consumed_at = ? WHERE domain_id = ? AND consumed_at IS NULL", (now, domain_id))

    def _reconcile(self, domain_id):
        if self.provider is None:
            raise WorkspaceError("workspace_domains_disabled")
        now = self.store._now()
        lease = _isoformat(now)
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM workspace_domains WHERE id = ? AND status != 'removed'", (domain_id,)).fetchone()
            if row is None:
                return
            if row["check_started_at"] and _parse_time(row["check_started_at"]) + timedelta(minutes=2) > now:
                raise WorkspaceError("workspace_domain_busy")
            connection.execute("UPDATE workspace_domains SET check_started_at = ? WHERE id = ?", (lease, domain_id))
            domain = dict(row)
        error, provider_id, active, removed = None, domain["provider_id"], False, False
        try:
            if domain["status"] == "removing":
                # A create may have succeeded just before a crash, even when the
                # provider id is absent locally. Delete by the durable hostname.
                # An unverified claim grants no authority to delete a provider
                # record. Durable intent is recorded only after DNS ownership.
                if domain["provider_requested_at"]:
                    self.provider.remove(domain["hostname"], domain["provider_id"])
                removed = True
            elif not self.provider.owned(domain["hostname"], domain["challenge"]):
                error = "ownership_pending"
            elif not self.provider.routed(domain["hostname"]):
                error = "routing_pending"
            else:
                # Persist before any external create. A crash can then repair
                # an unknown response without treating a bare claim as authority.
                with self.store._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    current = connection.execute("SELECT version, check_started_at FROM workspace_domains WHERE id = ?", (domain_id,)).fetchone()
                    if current is None or current["version"] != domain["version"] or current["check_started_at"] != lease:
                        return
                    self.sessions._workspace(connection, domain["workspace_id"])
                    connection.execute("UPDATE workspace_domains SET provider_requested_at = COALESCE(provider_requested_at, ?) WHERE id = ?", (_isoformat(self.store._now()), domain_id))
                provider_id, verified = self.provider.ensure(domain["hostname"])
                nonce = secrets.token_urlsafe(32)
                proof = self.provider.tls_probe(domain["hostname"], domain["id"], nonce) if verified else None
                expected = self.proof(domain["hostname"], domain["id"], nonce)
                active = isinstance(proof, str) and hmac.compare_digest(proof, expected)
                error = None if active else "https_pending"
        except (WorkspaceError, OSError, ValueError, TypeError):
            error = "provider_unavailable"
        if self._stop.is_set():
            # Leave the durable lease/intent for the next process to repair.
            return
        with self.store._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM workspace_domains WHERE id = ?", (domain_id,)).fetchone()
            if current is None or current["version"] != domain["version"] or current["check_started_at"] != lease:
                return
            status = "removed" if removed else "removing" if domain["status"] == "removing" else "active" if active else "pending"
            invalidated = domain["status"] == "active" and not active
            connection.execute("UPDATE workspace_domains SET status = ?, provider_id = ?, verified_until = ?, last_error = ?, version = version + ?, check_started_at = NULL, updated_at = ? WHERE id = ?", (status, provider_id, _isoformat(self.store._now() + timedelta(minutes=30)) if active else None, error, int(invalidated), _isoformat(self.store._now()), domain_id))
            if invalidated or removed:
                self._revoke(connection, domain_id)

    def proof(self, hostname, domain_id, nonce):
        return self.store._digest("workspace-domain-probe", "\x00".join((hostname, domain_id, nonce))).hex()

    def probe(self, hostname, domain_id, nonce):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", nonce):
            raise WorkspaceError("workspace_domain_invalid")
        with self.store._connection() as connection:
            row = connection.execute("SELECT * FROM workspace_domains WHERE id = ? AND hostname = ? AND status IN ('pending', 'active')", (domain_id, hostname)).fetchone()
            if row is None or not row["check_started_at"] or _parse_time(row["check_started_at"]) + timedelta(minutes=2) <= self.store._now():
                raise WorkspaceError("workspace_host_rejected")
        return self.proof(hostname, domain_id, nonce)

    def resolve(self, hostname):
        with self.store._connection() as connection:
            row = connection.execute("SELECT id FROM workspace_domains WHERE hostname = ?", (hostname,)).fetchone()
            if row is None:
                raise WorkspaceError("workspace_host_rejected")
            return dict(self.sessions._domain(connection, row["id"]))

    def health_host(self, hostname):
        # Render can select a verified custom hostname for service health
        # checks. Keep liveness independent of its tenant serving lease and
        # eventual provider deletion; this grants no dashboard authority.
        # Bare, unverified claims cannot widen even the health Host allowlist.
        with self.store._connection() as connection:
            return connection.execute("SELECT 1 FROM workspace_domains WHERE hostname = ? AND provider_requested_at IS NOT NULL", (hostname,)).fetchone() is not None

    def reconcile_due(self):
        if self.provider is None:
            return
        now = self.store._now()
        with self.store._connection() as connection:
            rows = connection.execute("SELECT id FROM workspace_domains WHERE status != 'removed' AND updated_at <= ? ORDER BY updated_at LIMIT 10", (_isoformat(now - timedelta(minutes=5)),)).fetchall()
        for row in rows:
            if self._stop.is_set():
                return
            try:
                self._reconcile(row["id"])
            except WorkspaceError:
                pass

    def start(self):
        if self.provider is not None and self._worker is None:
            def run():
                while not self._stop.wait(30):
                    try:
                        self.reconcile_due()
                    except Exception:
                        # No provider response, domain, cookie or traceback in logs.
                        pass
            self._worker = threading.Thread(target=run, name="hormuz-domain-reconciliation", daemon=True)
            self._worker.start()

    def close(self):
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=1)
