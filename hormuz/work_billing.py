"""Signed payment evidence gates approved workspaces; never checkout redirects.

Operator-reviewed config binds an organization to one Stripe customer and
subscription. The two independent facts needed for paid access are an active
subscription at the configured price and a paid invoice covering this instant.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import hmac
import json
import os
import re
import sqlite3
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .work_runtime import WorkRuntimeError

STRIPE_VERSION = "2026-09-30.endive"


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _price(item):
    value = item.get("price", {})
    if isinstance(value, dict):
        value = value.get("id")
    return value or _object(_object(item.get("pricing")).get("price_details")).get("price")


def _object(value):
    return value if isinstance(value, dict) else {}


class WorkBilling:
    def __init__(self, path, price_id, bindings, webhook_secret, *, api_key="", clock=time.time):
        self.path, self.price_id, self.clock = Path(path), price_id, clock
        self._secret, self._api_key = webhook_secret, api_key
        self.bindings = {row[0]: (row[1], row[2]) for row in bindings}
        if self.path.is_symlink():
            raise WorkRuntimeError("billing_storage_unsafe", 503)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise WorkRuntimeError("billing_storage_unsafe", 503)
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS work_billing_events(id TEXT PRIMARY KEY,digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS work_entitlements(
                  organization_id TEXT PRIMARY KEY,customer TEXT NOT NULL,subscription TEXT NOT NULL,price_id TEXT NOT NULL,
                  subscription_state TEXT NOT NULL DEFAULT 'unverified',subscription_at INTEGER NOT NULL DEFAULT 0,
                  subscription_until INTEGER NOT NULL DEFAULT 0,subscription_from INTEGER NOT NULL DEFAULT 0,
                  paid_at INTEGER NOT NULL DEFAULT 0,paid_until INTEGER NOT NULL DEFAULT 0,paid_from INTEGER NOT NULL DEFAULT 0);
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(work_entitlements)")}
            for name, declaration in (("price_id", "TEXT NOT NULL DEFAULT ''"), ("subscription_from", "INTEGER NOT NULL DEFAULT 0"), ("paid_from", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE work_entitlements ADD COLUMN {name} {declaration}")
            for organization, (customer, subscription) in self.bindings.items():
                match = "customer=excluded.customer AND subscription=excluded.subscription AND price_id=excluded.price_id"
                resets = ",".join(f"{field}=CASE WHEN {match} THEN {field} ELSE {fallback} END"
                    for field, fallback in (("subscription_state", "'unverified'"), ("paid_until", "0"), ("paid_from", "0"), ("paid_at", "0"),
                        ("subscription_at", "0"), ("subscription_from", "0"), ("subscription_until", "0")))
                connection.execute("INSERT INTO work_entitlements(organization_id,customer,subscription,price_id) VALUES(?,?,?,?) "
                    "ON CONFLICT(organization_id) DO UPDATE SET customer=excluded.customer,subscription=excluded.subscription,price_id=excluded.price_id," + resets,
                    (organization, customer, subscription, self.price_id))

    @contextmanager
    def _connect(self):
        # Initialization creates the private file explicitly. A missing store
        # after startup must not be silently replaced with an empty database.
        connection = sqlite3.connect(self.path.absolute().as_uri() + "?mode=rw", uri=True, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def verify_ready(self):
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise WorkRuntimeError("billing_storage_unsafe", 503)
        connection = sqlite3.connect(self.path.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            connection.execute("SELECT id,digest FROM work_billing_events LIMIT 0")
            connection.execute("SELECT organization_id,customer,subscription,price_id,subscription_state,"
                "subscription_at,subscription_until,subscription_from,paid_at,paid_until,paid_from FROM work_entitlements LIMIT 0")
        finally:
            connection.close()

    @property
    def webhook_configured(self):
        return isinstance(self._secret, str) and self._secret.startswith("whsec_") and len(self._secret) >= 16

    def protected_values(self):
        return [(name, value) for name, value in (("work_billing_webhook_secret", self._secret), ("work_billing_api_key", self._api_key))
            if isinstance(value, str) and len(value) >= 8]

    def status(self, organization):
        value = {"status": "payment_unverified", "provider_fees": "separate",
            "paid_activation": "requires_signed_subscription_and_paid_invoice", "portal_available": False}
        if organization not in self.bindings:
            return {**value, "status": "qualification_required"}
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM work_entitlements WHERE organization_id=?", (organization,)).fetchone()
        if row is None:
            return value
        if (row["customer"], row["subscription"]) != self.bindings[organization] or row["price_id"] != self.price_id:
            return {**value, "status": "configuration_changed", "entitled": False}
        now = self.clock()
        active = row["price_id"] == self.price_id and row["subscription_state"] == "active" and max(row["subscription_from"], row["paid_from"]) <= now < min(row["subscription_until"], row["paid_until"])
        state = "active" if active else "payment_unverified" if row["subscription_state"] == "active" and row["paid_until"] == 0 else "expired" if row["subscription_state"] == "active" else row["subscription_state"]
        return {**value, "status": state, "entitled": active,
            "paid_until": row["paid_until"], "portal_available": bool(self._api_key)}

    def entitled(self, organization):
        return self.status(organization).get("entitled", False)

    def receive(self, body, signature):
        if not self.webhook_configured:
            raise WorkRuntimeError("billing_webhook_not_configured", 503)
        if not isinstance(body, bytes) or not body or len(body) > 1_048_576 or not isinstance(signature, str) or len(signature) > 2048:
            raise WorkRuntimeError("billing_signature_invalid", 400)
        pieces = signature.split(",")
        timestamps = [piece[2:] for piece in pieces if piece.startswith("t=")]
        signatures = [piece[3:] for piece in pieces if piece.startswith("v1=")]
        if len(timestamps) != 1 or not timestamps[0].isdigit() or len(timestamps[0]) > 12 or abs(self.clock() - int(timestamps[0])) > 300:
            raise WorkRuntimeError("billing_signature_invalid", 400)
        expected = hmac.new(self._secret.encode(), timestamps[0].encode() + b"." + body, hashlib.sha256).hexdigest()
        if not any(re.fullmatch(r"[a-f0-9]{64}", value) and hmac.compare_digest(expected, value) for value in signatures):
            raise WorkRuntimeError("billing_signature_invalid", 400)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate")
                result[key] = value
            return result
        try:
            event = json.loads(body, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            identifier, kind, created = event["id"], event["type"], event["created"]
            item = event["data"]["object"]
            if not isinstance(identifier, str) or not re.fullmatch(r"evt_[A-Za-z0-9]{1,128}", identifier) or not isinstance(kind, str) or len(kind) > 256 or not _integer(created) or created > self.clock() + 300 or event.get("livemode") is not True or event.get("api_version") != STRIPE_VERSION or not isinstance(item, dict):
                raise ValueError()
        except (ValueError, TypeError, KeyError, RecursionError):
            raise WorkRuntimeError("billing_event_invalid", 400) from None
        digest = hashlib.sha256(body).hexdigest()
        customer = item.get("customer")
        subscription = item.get("id") if kind.startswith("customer.subscription.") else _object(_object(item.get("parent")).get("subscription_details")).get("subscription")
        organization = next((org for org, pair in self.bindings.items() if pair == (customer, subscription)), None)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT digest FROM work_billing_events WHERE id=?", (identifier,)).fetchone()
            if previous:
                if previous[0] != digest:
                    raise WorkRuntimeError("billing_event_conflict", 409)
                return {"status": "duplicate"}
            # Durable replay history has no lifetime admission quota: retained
            # events must never prevent an authenticated cancellation. Each
            # stored record remains bounded; no raw payment body is retained.
            applied = False
            if organization and kind in {"customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"}:
                items = _object(item.get("items")).get("data", [])
                accepted = isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) and _price(items[0]) == self.price_id and type(items[0].get("quantity", 1)) is int and items[0].get("quantity", 1) == 1
                end = items[0].get("current_period_end", item.get("current_period_end")) if accepted else 0
                start = items[0].get("current_period_start", item.get("current_period_start")) if accepted else 0
                state = item.get("status") if accepted and _integer(start) and _integer(end) and start < end else "price_unverified"
                if kind.endswith("deleted"):
                    state = "canceled"
                if state not in {"active", "canceled", "past_due", "unpaid", "incomplete", "incomplete_expired", "paused", "trialing"}:
                    state = "unverified"
                end, start = end if _integer(end) else 0, start if _integer(start) else 0
                applied = connection.execute("UPDATE work_entitlements SET subscription_state=?,subscription_at=?,subscription_until=?,subscription_from=? "
                    "WHERE organization_id=? AND customer=? AND subscription=? AND price_id=? AND (subscription_state!='canceled' OR ?!='active') AND (subscription_at<? OR (subscription_at=? AND (?!='active' OR (subscription_state='active' AND (?<subscription_until OR ?>subscription_from)))))",
                    (state, created, end, start, organization, customer, subscription, self.price_id, state, created, created, state, end, start)).rowcount > 0
            elif organization and kind in {"invoice.paid", "invoice.payment_failed", "invoice.voided"}:
                lines = _object(item.get("lines")).get("data", [])
                accepted = isinstance(lines, list) and len(lines) == 1 and isinstance(lines[0], dict) and _price(lines[0]) == self.price_id
                end = _object(lines[0].get("period")).get("end") if accepted else None
                start = _object(lines[0].get("period")).get("start") if accepted else None
                paid = kind == "invoice.paid" and item.get("status") == "paid" and _integer(item.get("amount_paid")) and item["amount_paid"] > 0
                if accepted and _integer(start) and _integer(end) and start < end:
                    applied = connection.execute("UPDATE work_entitlements SET paid_at=?,paid_until=?,paid_from=? WHERE organization_id=? AND customer=? AND subscription=? AND price_id=? AND (paid_at<? OR (paid_at=? AND ?=0))",
                        (created, end if paid else 0, start if paid else 0, organization, customer, subscription, self.price_id, created, created, int(paid))).rowcount > 0
            connection.execute("INSERT INTO work_billing_events VALUES(?,?)", (identifier, digest))
        return {"status": "applied" if applied else "ignored"}

    def portal(self, organization, return_url):
        if organization not in self.bindings or not self._api_key:
            raise WorkRuntimeError("billing_portal_unavailable", 503)
        if self.status(organization).get("status") == "configuration_changed":
            raise WorkRuntimeError("billing_configuration_changed", 503)
        origin = urlsplit(return_url)
        if origin.scheme != "https" or not origin.hostname or origin.username or origin.password or origin.query or origin.fragment or origin.path != "/work":
            raise WorkRuntimeError("billing_return_url_invalid", 400)
        request = urllib.request.Request("https://api.stripe.com/v1/billing_portal/sessions",
            data=urlencode({"customer": self.bindings[organization][0], "return_url": return_url}).encode(),
            headers={"Authorization": "Bearer " + self._api_key, "Stripe-Version": STRIPE_VERSION, "Content-Type": "application/x-www-form-urlencoded"})
        from .work_client import _NoRedirect
        try:
            with urllib.request.build_opener(_NoRedirect).open(request, timeout=10) as response:
                body = response.read(65_537)
                if len(body) > 65_536:
                    raise ValueError()
                value = json.loads(body)
                url = value.get("url", "")
                parsed = urlsplit(url)
                if value.get("customer") != self.bindings[organization][0] or parsed.scheme != "https" or parsed.netloc != "billing.stripe.com" or not parsed.path.startswith("/p/") or any(character in url for character in "\r\n\x00"):
                    raise ValueError()
                return url
        except (ValueError, TypeError, OSError, urllib.error.URLError):
            raise WorkRuntimeError("billing_portal_unavailable", 503) from None


def handle_webhook(handler):
    handler.close_connection = True
    try:
        billing = getattr(handler.server, "work_billing", None)
        if billing is None:
            raise WorkRuntimeError("billing_not_enabled", 404)
        if handler.path != "/v1/work/billing/webhook" or handler.headers.get_content_type() != "application/json" or any(len(handler.headers.get_all(name, [])) != 1 for name in ("Stripe-Signature", "Content-Length", "Content-Type")) or handler.headers.get_all("Transfer-Encoding", []):
            raise WorkRuntimeError("billing_request_invalid", 400)
        size = handler.headers["Content-Length"]
        if not size.isdigit() or not 0 < int(size) <= 1_048_576:
            raise WorkRuntimeError("billing_request_invalid", 400)
        body = handler.rfile.read(int(size))
        if len(body) != int(size):
            raise WorkRuntimeError("billing_request_invalid", 400)
        handler._send_json(200, billing.receive(body, handler.headers["Stripe-Signature"]))
    except WorkRuntimeError as error:
        handler._send_json(error.status, {"error": {"code": "hormuz_ai_work_" + error.reason}})
    except (sqlite3.Error, OSError):
        handler._send_json(503, {"error": {"code": "hormuz_ai_work_billing_unavailable"}})
