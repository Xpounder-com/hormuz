"""Payment proof, ownership, ordering and replay; no real Stripe calls."""
import hashlib
import hmac
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from hormuz.work_billing import STRIPE_VERSION, WorkBilling
from hormuz.work_runtime import WorkRuntimeError


class WorkBillingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = 1_791_465_600
        self.secret = "whsec_synthetic_secret_never_real"
        self.billing = WorkBilling(Path(self.directory.name) / "billing.sqlite3", "price_Approved",
            [("company", "cus_Approved", "sub_Approved")], self.secret, clock=lambda: self.now)

    def tearDown(self):
        self.directory.cleanup()

    def test_stripe_http_errors_close_response_without_retry_or_public_error_change(self):
        self.billing._api_key = "synthetic-api-fixture"
        for status in (401, 429, 503):
            with self.subTest(status=status), io.BytesIO(b"synthetic private error body") as body:
                error = HTTPError("https://api.stripe.com/v1/subscriptions/sub_Synthetic", status, "synthetic", {}, body)
                with patch("hormuz.work_billing.urllib.request.build_opener") as build_opener:
                    build_opener.return_value.open.side_effect = error
                    with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable") as raised:
                        self.billing._stripe("/v1/subscriptions/sub_Synthetic", {}, method="GET")
                    self.assertEqual(raised.exception.status, 503)
                    self.assertTrue(body.closed)
                    build_opener.return_value.open.assert_called_once()
                    self.assertEqual(build_opener.return_value.open.call_args.kwargs, {"timeout": 10})

    def test_stripe_http_error_close_failure_preserves_public_error(self):
        self.billing._api_key = "synthetic-api-fixture"
        with io.BytesIO(b"synthetic private error body") as body:
            error = HTTPError("https://api.stripe.com/v1/subscriptions/sub_Synthetic", 403, "synthetic", {}, body)

            def close():
                body.close()
                raise OSError("synthetic close failure")

            with patch.object(error, "close", side_effect=close) as close_error, \
                    patch("hormuz.work_billing.urllib.request.build_opener") as build_opener:
                build_opener.return_value.open.side_effect = error
                with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable") as raised:
                    self.billing._stripe("/v1/subscriptions/sub_Synthetic", {}, method="GET")
                self.assertEqual(raised.exception.status, 503)
                self.assertTrue(body.closed)
                close_error.assert_called_once_with()
                build_opener.return_value.open.assert_called_once()

    def send(self, kind, item, *, identifier="evt_one", created=None, live=True):
        event = {"id": identifier, "type": kind, "created": self.now if created is None else created,
            "api_version": STRIPE_VERSION, "livemode": live, "data": {"object": item}}
        body = json.dumps(event, separators=(",", ":")).encode()
        signature = hmac.new(self.secret.encode(), str(self.now).encode() + b"." + body, hashlib.sha256).hexdigest()
        return self.billing.receive(body, f"t={self.now},v1={signature}")

    def subscription(self, **changes):
        return {"id": "sub_Approved", "customer": "cus_Approved", "status": "active",
            "items": {"data": [{"price": {"id": "price_Approved"}, "quantity": 1, "current_period_start": self.now, "current_period_end": self.now + 3600}]}, **changes}

    def invoice(self, **changes):
        return {"customer": "cus_Approved", "parent": {"subscription_details": {"subscription": "sub_Approved"}},
            "status": "paid", "amount_paid": 4999,
            "lines": {"data": [{"pricing": {"price_details": {"price": "price_Approved"}}, "period": {"start": self.now, "end": self.now + 3600}}]}, **changes}

    def test_active_subscription_alone_and_checkout_redirect_do_not_activate(self):
        self.send("customer.subscription.updated", self.subscription())
        self.assertFalse(self.billing.entitled("company"))
        self.send("checkout.session.completed", {"customer": "cus_Approved", "payment_status": "paid"}, identifier="evt_checkout")
        self.assertFalse(self.billing.entitled("company"))
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        self.assertTrue(self.billing.entitled("company"))
        self.assertFalse(self.billing.entitled("other-company"))

    def test_paid_invoice_first_does_not_activate_until_matching_subscription(self):
        self.send("invoice.paid", self.invoice())
        self.assertFalse(self.billing.entitled("company"))
        self.send("customer.subscription.created", self.subscription(), identifier="evt_subscription")
        self.assertTrue(self.billing.entitled("company"))
        self.now += 3601
        self.assertFalse(self.billing.entitled("company"))

    def test_cancellation_and_old_events_cannot_reactivate(self):
        self.send("customer.subscription.updated", self.subscription(), identifier="evt_subscription")
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        self.now += 2
        self.send("customer.subscription.deleted", self.subscription(), identifier="evt_deleted")
        self.assertFalse(self.billing.entitled("company"))
        self.send("customer.subscription.updated", self.subscription(), identifier="evt_old", created=self.now - 1)
        self.send("invoice.paid", self.invoice(), identifier="evt_lateInvoice")
        self.assertFalse(self.billing.entitled("company"))

    def test_wrong_price_customer_zero_payment_and_testmode_fail_closed(self):
        self.send("customer.subscription.updated", self.subscription())
        for index, invoice in enumerate((self.invoice(customer="cus_Other"), self.invoice(amount_paid=0), self.invoice(status="open"), self.invoice(lines={"data": [{"price": "price_Other", "period": {"end": self.now + 3600}}]}))):
            self.send("invoice.paid", invoice, identifier=f"evt_bad{index}")
            self.assertFalse(self.billing.entitled("company"))
        with self.assertRaises(WorkRuntimeError):
            self.send("invoice.paid", self.invoice(), identifier="evt_test", live=False)

    def test_unsigned_tampered_stale_duplicate_and_conflicting_events(self):
        with self.assertRaises(WorkRuntimeError):
            self.billing.receive(b"{}", f"t={self.now},v1={'0'*64}")
        with self.assertRaises(WorkRuntimeError):
            self.billing.receive(b"{}", f"t={self.now-301},v1={'0'*64}")
        first = self.send("customer.subscription.updated", self.subscription())
        self.assertEqual(first["status"], "applied")
        self.assertEqual(self.send("customer.subscription.updated", self.subscription())["status"], "duplicate")
        with self.assertRaises(WorkRuntimeError):
            self.send("customer.subscription.updated", self.subscription(status="canceled"))

    def test_restart_preserves_proof_but_changed_approved_binding_revokes_it(self):
        self.send("customer.subscription.updated", self.subscription())
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        restarted = WorkBilling(self.billing.path, "price_Approved", [("company", "cus_Approved", "sub_Approved")], self.secret, clock=lambda: self.now)
        self.assertTrue(restarted.entitled("company"))
        changed = WorkBilling(self.billing.path, "price_Approved", [("company", "cus_New", "sub_New")], self.secret, clock=lambda: self.now)
        self.assertFalse(changed.entitled("company"))

    def test_portal_requires_configured_owner_and_https_return(self):
        with self.assertRaises(WorkRuntimeError):
            self.billing.portal("other-company", "https://gateway.example/work")
        self.billing._api_key = "sk_synthetic_never_real"
        for url in ("http://gateway.example/work", "https://attacker@gateway.example/work", "https://gateway.example/work?token=secret"):
            with self.assertRaises(WorkRuntimeError):
                self.billing.portal("company", url)

    def test_portal_default_preserves_existing_session_request(self):
        self.billing._api_key = "synthetic-api-fixture"
        with patch.object(self.billing, "_stripe", return_value={
            "customer": "cus_Approved", "configuration": "bpc_AccountDefault", "url": "https://billing.stripe.com/p/session/Synthetic",
        }) as stripe:
            self.assertEqual("https://billing.stripe.com/p/session/Synthetic", self.billing.portal("company", "https://gateway.example/work"))
        stripe.assert_called_once_with("/v1/billing_portal/sessions", {"customer": "cus_Approved", "return_url": "https://gateway.example/work"})

    def test_reviewed_portal_configuration_preserves_paid_and_cancellation_access(self):
        self.send("customer.subscription.updated", self.subscription(), identifier="evt_subscription")
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        billing = WorkBilling(self.billing.path, "price_Approved", [("company", "cus_Approved", "sub_Approved")], self.secret,
            api_key="synthetic-api-fixture", portal_configuration_id="bpc_Reviewed", clock=lambda: self.now)
        self.assertTrue(billing.entitled("company"))
        expected = {"customer": "cus_Approved", "return_url": "https://gateway.example/work", "configuration": "bpc_Reviewed"}
        response = {"customer": "cus_Approved", "configuration": "bpc_Reviewed", "url": "https://billing.stripe.com/p/session/Synthetic"}
        with patch.object(billing, "_stripe", return_value=response) as stripe:
            self.assertEqual(response["url"], billing.portal("company", expected["return_url"]))
        stripe.assert_called_once_with("/v1/billing_portal/sessions", expected)
        self.assertTrue(billing.entitled("company"))

        self.now += 1
        self.send("customer.subscription.deleted", self.subscription(), identifier="evt_cancel")
        self.assertFalse(billing.entitled("company"))
        with patch.object(billing, "_stripe", return_value=response) as stripe:
            self.assertEqual(response["url"], billing.portal("company", expected["return_url"]))
        stripe.assert_called_once_with("/v1/billing_portal/sessions", expected)
        changed = WorkBilling(billing.path, "price_Changed", [("company", "cus_Approved", "sub_Approved")], self.secret,
            api_key="synthetic-api-fixture", portal_configuration_id="bpc_Reviewed", clock=lambda: self.now)
        self.assertFalse(changed.entitled("company"))
        self.assertTrue(changed.status("company")["portal_available"])
        with patch.object(changed, "_stripe", return_value=response) as stripe:
            self.assertEqual(response["url"], changed.portal("company", expected["return_url"]))
        stripe.assert_called_once_with("/v1/billing_portal/sessions", expected)
        self.assertFalse(changed.entitled("company"))

    def test_configured_portal_rejects_wrong_or_missing_returned_configuration(self):
        billing = WorkBilling(self.billing.path, "price_Approved", [("company", "cus_Approved", "sub_Approved")], self.secret,
            api_key="synthetic-api-fixture", portal_configuration_id="bpc_Reviewed", clock=lambda: self.now)
        for configuration in (None, "bpc_Other", {"id": "bpc_Reviewed"}):
            with self.subTest(configuration=configuration), patch.object(billing, "_stripe", return_value={
                "customer": "cus_Approved", "configuration": configuration, "url": "https://billing.stripe.com/p/session/Synthetic",
            }), self.assertRaisesRegex(WorkRuntimeError, "billing_portal_unavailable"):
                billing.portal("company", "https://gateway.example/work")

    def test_invalid_direct_portal_configuration_creates_no_store(self):
        path = Path(self.directory.name) / "invalid.sqlite3"
        with self.assertRaisesRegex(WorkRuntimeError, "billing_portal_configuration_invalid"):
            WorkBilling(path, "price_Approved", [], self.secret, portal_configuration_id="bpc_Bad-Id")
        self.assertFalse(path.exists())

    def test_non_subscription_paid_invoice_is_ignored(self):
        self.assertEqual(self.send("invoice.paid", self.invoice(parent=None))["status"], "ignored")

    def test_missing_optional_key_cannot_register_empty_redaction_pattern(self):
        self.assertEqual(self.billing.protected_values(), [("work_billing_webhook_secret", self.secret)])

    def test_changed_approved_price_revokes_prior_proof(self):
        self.send("customer.subscription.updated", self.subscription())
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        changed = WorkBilling(self.billing.path, "price_New", [("company", "cus_Approved", "sub_Approved")], self.secret, clock=lambda: self.now)
        self.assertFalse(changed.entitled("company"))

    def test_old_instance_cannot_write_proof_to_new_binding(self):
        changed = WorkBilling(self.billing.path, "price_New", [("company", "cus_New", "sub_New")], self.secret, clock=lambda: self.now)
        self.send("customer.subscription.updated", self.subscription())
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        self.assertFalse(changed.entitled("company"))
        self.assertFalse(self.billing.entitled("company"))
        self.assertEqual(self.billing.status("company")["status"], "configuration_changed")

    def test_future_paid_period_does_not_activate_early(self):
        subscription, invoice = self.subscription(), self.invoice()
        subscription["items"]["data"][0]["current_period_start"] = self.now + 100
        invoice["lines"]["data"][0]["period"]["start"] = self.now + 100
        self.send("customer.subscription.updated", subscription)
        self.send("invoice.paid", invoice, identifier="evt_invoice")
        self.assertFalse(self.billing.entitled("company"))
        self.now += 100
        self.assertTrue(self.billing.entitled("company"))

    def test_same_second_revocation_wins_over_any_grant(self):
        self.send("customer.subscription.updated", self.subscription(), identifier="evt_active")
        self.send("invoice.paid", self.invoice(), identifier="evt_invoice")
        self.send("invoice.payment_failed", self.invoice(status="open"), identifier="evt_failed")
        self.send("invoice.paid", self.invoice(), identifier="evt_reorderedPaid")
        self.assertFalse(self.billing.entitled("company"))
        self.now += 1
        self.send("invoice.paid", self.invoice(), identifier="evt_newPaid")
        self.assertTrue(self.billing.entitled("company"))
        self.send("customer.subscription.deleted", self.subscription(), identifier="evt_deleted")
        narrowed = self.subscription()
        narrowed["items"]["data"][0]["current_period_end"] -= 100
        self.send("customer.subscription.updated", narrowed, identifier="evt_reorderedActive")
        self.assertFalse(self.billing.entitled("company"))
        self.now += 1
        self.send("customer.subscription.updated", self.subscription(), identifier="evt_afterDeleted")
        self.assertFalse(self.billing.entitled("company"))

    def test_retained_payment_history_cannot_block_subscription_revocation(self):
        self.send("customer.subscription.updated", self.subscription(), identifier="evt_active")
        self.send("invoice.paid", self.invoice(), identifier="evt_paid")
        with self.billing._connect() as connection:
            connection.executemany("INSERT INTO work_billing_events VALUES(?,?)",
                ((f"evt_history{index}", "0" * 64) for index in range(100_000)))
        self.now += 1
        result = self.send("customer.subscription.deleted", self.subscription(), identifier="evt_cancel")
        self.assertEqual("applied", result["status"])
        self.assertFalse(self.billing.entitled("company"))
        self.assertEqual("duplicate", self.send("customer.subscription.deleted", self.subscription(), identifier="evt_cancel")["status"])
