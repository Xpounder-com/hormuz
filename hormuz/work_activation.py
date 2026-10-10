"""Durable server-owned qualification and Checkout references, without redirects granting access."""
from __future__ import annotations

import json
import re
import secrets
import sqlite3

from .work_runtime import WorkRuntimeError

CHECKOUT_LIFETIME_SECONDS = 3600
CHECKOUT_MINIMUM_CREATE_REMAINING_SECONDS = 1830

SCHEMA = """
CREATE TABLE IF NOT EXISTS work_activation(
 organization_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, model TEXT NOT NULL, client TEXT NOT NULL,
 state TEXT NOT NULL, reviewer TEXT, reference TEXT, version INTEGER NOT NULL DEFAULT 1,
 generation TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS work_checkouts(
 reference TEXT PRIMARY KEY, organization_id TEXT NOT NULL, generation TEXT NOT NULL,
 price_id TEXT NOT NULL, session_id TEXT UNIQUE, url TEXT, state TEXT NOT NULL,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, return_url TEXT);
CREATE TABLE IF NOT EXISTS work_payment_facts(
 customer TEXT NOT NULL, subscription TEXT NOT NULL, kind TEXT NOT NULL,
 created_at INTEGER NOT NULL, event_id TEXT NOT NULL, value TEXT NOT NULL,
 PRIMARY KEY(customer,subscription,kind));
CREATE UNIQUE INDEX IF NOT EXISTS work_entitlement_customer ON work_entitlements(customer);
CREATE UNIQUE INDEX IF NOT EXISTS work_entitlement_subscription ON work_entitlements(subscription);
"""


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None


class WorkActivation:
    def __init__(self, billing):
        self.billing = billing
        with billing._connect() as connection:
            connection.executescript(SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(work_checkouts)")}
            if "return_url" not in columns:
                connection.execute("ALTER TABLE work_checkouts ADD COLUMN return_url TEXT")

    def status(self, organization):
        with self.billing._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM work_activation WHERE organization_id=?", (organization,)).fetchone()
        return {"state": "qualification_required", "version": 0} if row is None else {
            key: row[key] for key in ("state", "model", "client", "reference", "version", "updated_at")}

    def request(self, organization, actor, model, client):
        if not all(_identifier(value) for value in (organization, actor, model, client)) or client not in {"codex", "claude-code"}:
            raise WorkRuntimeError("activation_invalid_request")
        with self.billing._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT state,model,client FROM work_activation WHERE organization_id=?", (organization,)).fetchone()
            if previous and previous[1:] == (model, client) and previous[0] in {"requested", "qualified", "checkout_pending", "payment_unverified"}:
                return self.status(organization)
            outstanding = connection.execute("SELECT 1 FROM work_checkouts WHERE organization_id=? AND (state='creating' OR (state='open' AND expires_at>?)) LIMIT 1",
                (organization, int(self.billing.clock()))).fetchone()
            if outstanding:
                raise WorkRuntimeError("billing_checkout_retry_pending", 409)
            bound = connection.execute("SELECT subscription_state,price_id FROM work_entitlements WHERE organization_id=?", (organization,)).fetchone()
            if bound and (bound[0] != "recovery_required" or bound[1] != self.billing.price_id):
                raise WorkRuntimeError("activation_existing_subscription", 409)
            connection.execute("INSERT INTO work_activation(organization_id,actor_id,model,client,state,generation,updated_at) VALUES(?,?,?,?,'requested',?,?) "
                "ON CONFLICT(organization_id) DO UPDATE SET actor_id=excluded.actor_id,model=excluded.model,client=excluded.client,state='requested',reviewer=NULL,reference=NULL,generation=excluded.generation,updated_at=excluded.updated_at,version=version+1",
                (organization, actor, model, client, secrets.token_urlsafe(24), int(self.billing.clock())))
        return self.status(organization)

    def review(self, organization, reviewer, action, reference, *, expected_version=None):
        if action not in {"approve", "reject"} or not _identifier(reviewer) or not isinstance(reference, str) or not 1 <= len(reference) <= 256 or any(ord(char) < 32 for char in reference):
            raise WorkRuntimeError("activation_invalid_review")
        with self.billing._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state,version FROM work_activation WHERE organization_id=?", (organization,)).fetchone()
            if row is None or row[0] not in {"requested", "qualified", "rejected", "recovery_required"} or expected_version is not None and row[1] != expected_version:
                raise WorkRuntimeError("activation_review_conflict", 409)
            connection.execute("UPDATE work_activation SET state=?,reviewer=?,reference=?,version=version+1,updated_at=? WHERE organization_id=?",
                ("qualified" if action == "approve" else "rejected", reviewer, reference, int(self.billing.clock()), organization))
        return self.status(organization)

    def checkout(self, organization, return_url):
        from urllib.parse import urlsplit
        try:
            parsed = urlsplit(return_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path != "/work" or parsed.query or parsed.fragment:
                raise ValueError()
        except (ValueError, TypeError):
            raise WorkRuntimeError("billing_return_url_invalid") from None
        if self.billing.binding(organization) is not None:
            raise WorkRuntimeError("billing_existing_subscription", 409)
        now = int(self.billing.clock())
        with self.billing._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM work_entitlements WHERE organization_id=?", (organization,)).fetchone():
                raise WorkRuntimeError("billing_existing_subscription", 409)
            row = connection.execute("SELECT state,generation FROM work_activation WHERE organization_id=?", (organization,)).fetchone()
            if row is None or row[0] not in {"qualified", "checkout_pending"}:
                raise WorkRuntimeError("activation_qualification_required", 409)
            outstanding = connection.execute("SELECT reference,session_id,url,state,expires_at,generation,price_id,return_url FROM work_checkouts WHERE organization_id=? AND (state='creating' OR (state='open' AND expires_at>?)) ORDER BY created_at DESC LIMIT 2",
                (organization, now)).fetchall()
            if len(outstanding) > 1 or outstanding and outstanding[0][5:7] != (row[1], self.billing.price_id):
                raise WorkRuntimeError("billing_checkout_retry_pending", 409)
            pending = outstanding[0] if outstanding else None
            if pending:
                reference = pending[0]
                expires_at = pending[4]
                if pending[3] == "open":
                    return {"url": pending[2], "status": "checkout_pending"}
                if pending[7] != return_url:
                    raise WorkRuntimeError("billing_checkout_retry_pending", 409)
                recorded_return_url = pending[7]
            else:
                expires_at = now + CHECKOUT_LIFETIME_SECONDS
                reference = "hormuz_checkout_" + secrets.token_urlsafe(24)
                recorded_return_url = return_url
                connection.execute("INSERT INTO work_checkouts(reference,organization_id,generation,price_id,state,expires_at,created_at,return_url) VALUES(?,?,?,?,'creating',?,?,?)",
                    (reference, organization, row[1], self.billing.price_id, expires_at, now, recorded_return_url))
        # Stripe requires at least 30 minutes after its creation time. Keep the
        # recorded parameters immutable for idempotent retries, with headroom
        # for the bounded HTTP call. An unresolved creation cannot silently get
        # a new reference when its remaining window becomes too short or ends.
        if expires_at - int(self.billing.clock()) < CHECKOUT_MINIMUM_CREATE_REMAINING_SECONDS:
            raise WorkRuntimeError("billing_checkout_retry_pending", 409)
        values = {"mode": "subscription", "line_items[0][price]": self.billing.price_id,
            "line_items[0][quantity]": "1", "success_url": recorded_return_url, "cancel_url": recorded_return_url,
            "client_reference_id": reference, "expires_at": str(expires_at)}
        value = self.billing._stripe("/v1/checkout/sessions", values, idempotency=reference)
        session, url = value.get("id"), value.get("url")
        self.billing._validate_stripe_url(url, "checkout.stripe.com", "/c/")
        if not isinstance(session, str) or re.fullmatch(r"cs_[A-Za-z0-9_]{1,180}", session) is None or value.get("livemode") is not True or value.get("mode") != "subscription" or value.get("client_reference_id") != reference or type(value.get("expires_at")) is not int or value["expires_at"] != expires_at or expires_at <= int(self.billing.clock()):
            raise WorkRuntimeError("billing_checkout_invalid_response", 503)
        with self.billing._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT state,generation FROM work_activation WHERE organization_id=?", (organization,)).fetchone()
            if current != row:
                raise WorkRuntimeError("activation_configuration_changed", 409)
            connection.execute("UPDATE work_checkouts SET session_id=?,url=?,state='open' WHERE reference=? AND state='creating'", (session, url, reference))
            connection.execute("UPDATE work_activation SET state='checkout_pending',updated_at=?,version=version+1 WHERE organization_id=?", (now, organization))
        return {"url": url, "status": "checkout_pending"}

    def bind_checkout(self, connection, item):
        reference, session = item.get("client_reference_id"), item.get("id")
        if not isinstance(reference, str) or not isinstance(session, str):
            return None
        row = connection.execute("SELECT c.organization_id,c.generation,c.price_id,c.state,a.generation,a.state FROM work_checkouts c JOIN work_activation a USING(organization_id) WHERE c.reference=? AND c.session_id=?", (reference, session)).fetchone()
        if row is None or row[1] != row[4] or row[2] != self.billing.price_id or row[3] != "open" or row[5] != "checkout_pending" or item.get("mode") != "subscription" or item.get("status") != "complete" or item.get("payment_status") != "paid":
            return None
        customer, subscription = item.get("customer"), item.get("subscription")
        if not isinstance(customer, str) or re.fullmatch(r"cus_[A-Za-z0-9]{1,128}", customer) is None or not isinstance(subscription, str) or re.fullmatch(r"sub_[A-Za-z0-9]{1,128}", subscription) is None:
            return None
        if connection.execute("SELECT 1 FROM work_entitlements WHERE organization_id=? OR customer=? OR subscription=?", (row[0], customer, subscription)).fetchone():
            return None
        connection.execute("INSERT INTO work_entitlements(organization_id,customer,subscription,price_id) VALUES(?,?,?,?)", (row[0], customer, subscription, self.billing.price_id))
        connection.execute("UPDATE work_checkouts SET state='completed',url=NULL WHERE reference=?", (reference,))
        connection.execute("UPDATE work_activation SET state='payment_unverified',updated_at=?,version=version+1 WHERE organization_id=?", (int(self.billing.clock()), row[0]))
        return row[0], customer, subscription

    def reset(self, organization, reviewer, reference):
        # Operator reset closes admission and every stale checkout; it never
        # deletes subscription/cost history or cancels a Stripe subscription.
        if not _identifier(reviewer) or not isinstance(reference, str) or not 1 <= len(reference) <= 256:
            raise WorkRuntimeError("activation_invalid_review")
        with self.billing._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE work_activation SET state='recovery_required',generation=?,reviewer=?,reference=?,updated_at=?,version=version+1 WHERE organization_id=?", (secrets.token_urlsafe(24), reviewer, reference, int(self.billing.clock()), organization))
            connection.execute("UPDATE work_checkouts SET state='blocked',url=NULL WHERE organization_id=?", (organization,))
            connection.execute("UPDATE work_entitlements SET subscription_state='recovery_required',subscription_at=?,paid_at=?,recovery_after=?,paid_until=0,subscription_until=0 WHERE organization_id=?", (int(self.billing.clock()), int(self.billing.clock()), int(self.billing.clock()), organization))
        return self.status(organization)


def grant_reviewed_application(directory, organization, membership, client, reviewer):
    """Use the existing membership owner/audit boundary for an operator-reviewed grant."""
    from .session_store import _isoformat
    if client not in {"codex", "claude-code"} or not all(_identifier(value) for value in (organization, membership, reviewer)):
        raise WorkRuntimeError("activation_invalid_application")
    directory._enabled()
    with directory.store._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        member = directory._member(connection, organization, membership)
        if member["status"] != "active":
            raise WorkRuntimeError("activation_membership_inactive", 403)
        clients = json.loads(member["allowed_clients"])
        if client in clients:
            return False
        clients.append(client)
        now = _isoformat(directory.store._now())
        connection.execute("UPDATE onboarding_memberships SET allowed_clients=?,authorization_version=authorization_version+1,updated_at=? WHERE id=? AND organization_id=? AND authorization_version=?",
            (json.dumps(sorted(clients)), now, membership, organization, member["authorization_version"]))
        directory._event(connection, organization, "application_access_granted", membership_id=membership, decision_actor=reviewer)
    # Every existing session re-checks membership authorization_version. The
    # customer must sign in/enroll again after this explicit scope expansion.
    return True
