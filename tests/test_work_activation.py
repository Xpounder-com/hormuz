"""Trusted qualification→Checkout association→signed entitlement; no Stripe calls."""
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hormuz.work_billing import WorkBilling, STRIPE_VERSION
from hormuz.work_runtime import WorkRuntimeError


class WorkActivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = 1_791_465_600
        self.secret = "whsec_synthetic_secret_not_real"
        self.billing = WorkBilling(Path(self.temporary.name) / "billing.sqlite3", "price_Approved", [], self.secret,
            api_key="synthetic-api-fixture", clock=lambda: self.now)

    def send(self, kind, item, identifier, *, created=None):
        event = {"id": identifier, "type": kind, "created": self.now if created is None else created,
            "api_version": STRIPE_VERSION, "livemode": True, "data": {"object": item}}
        body = json.dumps(event, separators=(",", ":")).encode()
        signature = hmac.new(self.secret.encode(), str(self.now).encode() + b"." + body, hashlib.sha256).hexdigest()
        return self.billing.receive(body, f"t={self.now},v1={signature}")

    def facts(self):
        subscription = {"id": "sub_Trusted", "customer": "cus_Trusted", "status": "active", "items": {"data": [{"price": {"id": "price_Approved"}, "quantity": 1, "current_period_start": self.now-1, "current_period_end": self.now+3600}]}}
        invoice = {"customer": "cus_Trusted", "parent": {"subscription_details": {"subscription": "sub_Trusted"}}, "status": "paid", "amount_paid": 1000,
            "lines": {"data": [{"price": {"id": "price_Approved"}, "period": {"start": self.now-1, "end": self.now+3600}}]}}
        return subscription, invoice

    def checkout(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture-v1")
        def response(path, values, **kwargs):
            self.assertEqual(path, "/v1/checkout/sessions")
            self.assertEqual(values["line_items[0][price]"], "price_Approved")
            self.assertEqual(values["mode"], "subscription")
            self.assertNotIn("customer", values)
            self.assertEqual(values["client_reference_id"], kwargs["idempotency"])
            self.reference = values["client_reference_id"]
            return {"id": "cs_live_Synthetic", "url": "https://checkout.stripe.com/c/pay/Synthetic", "livemode": True,
                "mode": "subscription", "client_reference_id": self.reference}
        with patch.object(self.billing, "_stripe", side_effect=response) as call:
            value = self.billing.activation.checkout("company", "https://gateway.example/work")
            self.assertEqual(value, self.billing.activation.checkout("company", "https://gateway.example/work"))
        call.assert_called_once()
        return {"id": "cs_live_Synthetic", "client_reference_id": self.reference, "mode": "subscription", "status": "complete",
            "payment_status": "paid", "customer": "cus_Trusted", "subscription": "sub_Trusted"}

    def test_restart_and_out_of_order_facts_activate_only_recorded_checkout(self):
        checkout = self.checkout()
        subscription, invoice = self.facts()
        self.send("invoice.paid", invoice, "evt_invoice")
        self.send("customer.subscription.updated", subscription, "evt_subscription")
        self.assertFalse(self.billing.entitled("company"))
        self.billing = WorkBilling(self.billing.path, "price_Approved", [], self.secret, clock=lambda: self.now)
        self.send("checkout.session.completed", checkout, "evt_checkout")
        self.assertTrue(self.billing.entitled("company"))
        self.assertFalse(self.billing.entitled("other"))
        self.assertEqual("duplicate", self.send("checkout.session.completed", checkout, "evt_checkout")["status"])

    def test_checkout_reference_tampering_and_redirect_do_not_bind_or_activate(self):
        checkout = self.checkout()
        self.send("checkout.session.completed", {**checkout, "client_reference_id": "browser-forged"}, "evt_forged")
        self.assertIsNone(self.billing.binding("company"))
        with self.assertRaises(WorkRuntimeError):
            self.billing.activation.checkout("other", "https://gateway.example/work")
        self.assertFalse(self.billing.entitled("company"))

    def test_signed_subscription_and_invoice_still_both_required_after_checkout(self):
        checkout = self.checkout()
        self.send("checkout.session.completed", checkout, "evt_checkout")
        self.assertFalse(self.billing.entitled("company"))
        subscription, invoice = self.facts()
        self.send("customer.subscription.updated", subscription, "evt_subscription")
        self.assertFalse(self.billing.entitled("company"))
        self.send("invoice.paid", invoice, "evt_invoice")
        self.assertTrue(self.billing.entitled("company"))
        self.now += 1
        self.send("customer.subscription.deleted", subscription, "evt_cancel")
        self.assertFalse(self.billing.entitled("company"))

    def test_reset_blocks_old_checkout_and_old_unseen_payment_proof(self):
        checkout = self.checkout()
        self.send("checkout.session.completed", checkout, "evt_checkout")
        subscription, invoice = self.facts()
        self.send("customer.subscription.updated", subscription, "evt_subscription")
        self.send("invoice.paid", invoice, "evt_invoice")
        self.now += 5
        self.billing.activation.reset("company", "operator", "restore-fixture-v1")
        self.send("customer.subscription.updated", subscription, "evt_oldUnseen", created=self.now-1)
        self.send("invoice.paid", invoice, "evt_oldInvoice", created=self.now-1)
        self.assertFalse(self.billing.entitled("company"))
        self.assertEqual("recovery_required", self.billing.activation.status("company")["state"])

    def test_reset_requalification_reads_current_bound_provider_evidence(self):
        checkout = self.checkout()
        self.send("checkout.session.completed", checkout, "evt_checkout")
        subscription, invoice = self.facts()
        self.send("customer.subscription.updated", subscription, "evt_subscription")
        self.send("invoice.paid", invoice, "evt_invoice")
        self.now += 2
        self.billing.activation.reset("company", "operator", "recovery-fixture-v1")
        self.billing.activation.review("company", "operator", "approve", "requalification-fixture-v1")
        self.now += 1
        subscription = {**subscription, "livemode": True, "latest_invoice": "in_Trusted"}
        invoice = {**invoice, "livemode": True, "id": "in_Trusted"}
        with patch.object(self.billing, "_stripe", side_effect=[subscription, invoice]) as call:
            self.assertTrue(self.billing.reverify("company")["entitled"])
        self.assertEqual(call.call_args_list[0].args[0], "/v1/subscriptions/sub_Trusted")
        self.assertEqual(call.call_args_list[1].args[0], "/v1/invoices/in_Trusted")
        with self.assertRaisesRegex(WorkRuntimeError, "existing_subscription"):
            self.billing.activation.checkout("company", "https://gateway.example/work")

    def test_checkout_requires_recorded_qualification_and_bad_redirect_rejected(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        with self.assertRaises(WorkRuntimeError):
            self.billing.activation.checkout("company", "https://gateway.example/work")
        with self.assertRaises(WorkRuntimeError):
            self.billing._validate_stripe_url("https://checkout.stripe.com.attacker/c/pay/fixture", "checkout.stripe.com", "/c/")

    def test_version_changed_review_cannot_approve_a_different_request(self):
        self.billing.activation.request("company", "owner", "first-model", "codex")
        prior = self.billing.activation.status("company")
        self.billing.activation.request("company", "owner", "second-model", "codex")
        with self.assertRaisesRegex(WorkRuntimeError, "review_conflict"):
            self.billing.activation.review("company", "operator", "approve", "stale-fixture", expected_version=prior["version"])
        self.assertEqual("requested", self.billing.activation.status("company")["state"])

    def test_malformed_checkout_reference_is_ignored_without_binding(self):
        checkout = self.checkout()
        self.assertEqual("ignored", self.send("checkout.session.completed", {**checkout, "client_reference_id": {"arbitrary": "value"}}, "evt_badReference")["status"])
        self.assertIsNone(self.billing.binding("company"))

    def test_legacy_bound_subscription_can_requalify_after_reset_without_new_checkout(self):
        self.billing = WorkBilling(self.billing.path, "price_Approved", [("company", "cus_Trusted", "sub_Trusted")], self.secret,
            api_key="synthetic-api-fixture", clock=lambda: self.now)
        self.billing.activation.reset("company", "operator", "legacy-recovery-fixture")
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "legacy-requalification-fixture")
        self.assertEqual(("cus_Trusted", "sub_Trusted"), self.billing.binding("company"))
        with self.assertRaisesRegex(WorkRuntimeError, "existing_subscription"):
            self.billing.activation.checkout("company", "https://gateway.example/work")
        self.now += 1
        subscription, invoice = self.facts()
        subscription = {**subscription, "livemode": True, "latest_invoice": "in_Trusted"}
        invoice = {**invoice, "livemode": True, "id": "in_Trusted"}
        with patch.object(self.billing, "_stripe", side_effect=[subscription, invoice]):
            self.assertTrue(self.billing.reverify("company")["entitled"])

    def test_changed_approved_price_cannot_create_a_second_subscription(self):
        checkout = self.checkout()
        self.send("checkout.session.completed", checkout, "evt_checkout")
        changed = WorkBilling(self.billing.path, "price_New", [], self.secret, api_key="synthetic-api-fixture", clock=lambda: self.now)
        changed.activation.reset("company", "operator", "price-change-fixture")
        changed.activation.review("company", "operator", "approve", "new-price-fixture")
        with patch.object(changed, "_stripe") as adapter:
            with self.assertRaisesRegex(WorkRuntimeError, "existing_subscription"):
                changed.activation.checkout("company", "https://gateway.example/work")
        adapter.assert_not_called()
        self.assertEqual("configuration_changed", changed.status("company")["status"])
        self.assertFalse(changed.entitled("company"))
        self.assertTrue(changed.status("company")["portal_available"])
        with patch.object(changed, "_stripe", return_value={"customer": "cus_Trusted", "url": "https://billing.stripe.com/p/session/Synthetic"}) as adapter:
            self.assertEqual("https://billing.stripe.com/p/session/Synthetic", changed.portal("company", "https://gateway.example/work"))
        self.assertEqual("cus_Trusted", adapter.call_args.args[1]["customer"])
