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

    def checkout_records(self, organization="company", *, billing=None):
        with (billing or self.billing)._connect() as connection:
            return (
                connection.execute("SELECT * FROM work_checkouts WHERE organization_id=? ORDER BY reference", (organization,)).fetchall(),
                connection.execute("SELECT * FROM work_activation WHERE organization_id=?", (organization,)).fetchall(),
            )

    @staticmethod
    def checkout_response(values, session="cs_live_Synthetic"):
        return {"id": session, "url": "https://checkout.stripe.com/c/pay/" + session, "livemode": True,
            "mode": "subscription", "client_reference_id": values["client_reference_id"], "expires_at": int(values["expires_at"])}

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
                "mode": "subscription", "client_reference_id": self.reference, "expires_at": int(values["expires_at"])}
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

    def test_checkout_creation_has_transit_headroom_and_retry_parameters_stay_immutable(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        original_time = self.now
        calls = []

        def response(path, values, **options):
            calls.append((path, dict(values), dict(options)))
            self.now += 2  # Time passes before Stripe processes this request.
            self.assertGreaterEqual(int(values["expires_at"]) - self.now, 1800)
            if len(calls) == 1:
                raise WorkRuntimeError("billing_api_unavailable", 503)
            return {"id": "cs_live_Synthetic", "url": "https://checkout.stripe.com/c/pay/Synthetic", "livemode": True,
                "mode": "subscription", "client_reference_id": values["client_reference_id"], "expires_at": int(values["expires_at"])}

        with patch.object(self.billing, "_stripe", side_effect=response):
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
            self.now += 300
            self.assertEqual("checkout_pending", self.billing.activation.checkout("company", "https://gateway.example/work")["status"])
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(str(original_time + 3600), calls[0][1]["expires_at"])
        with self.billing._connect() as connection:
            row = connection.execute("SELECT state,expires_at,created_at FROM work_checkouts").fetchone()
        self.assertEqual(("open", original_time + 3600, original_time), row)
        self.assertFalse(self.billing.entitled("company"))

    def test_uncertain_checkout_short_or_expired_window_cannot_start_another_session(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        original_time = self.now
        with patch.object(self.billing, "_stripe", side_effect=WorkRuntimeError("billing_api_unavailable", 503)) as stripe:
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
            self.now = original_time + 1770  # Exactly 30 minutes + 30 seconds remain.
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
        self.assertEqual(2, stripe.call_count)
        self.assertEqual(stripe.call_args_list[0], stripe.call_args_list[1])
        with self.billing._connect() as connection:
            before = connection.execute("SELECT * FROM work_checkouts").fetchall()
            activation_before = connection.execute("SELECT * FROM work_activation").fetchall()

        for elapsed in (1771, 3599, 3600, 3601):
            self.now = original_time + elapsed
            with self.subTest(elapsed=elapsed), patch.object(self.billing, "_stripe") as stripe:
                with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending") as error:
                    self.billing.activation.checkout("company", "https://gateway.example/work")
                self.assertEqual(409, error.exception.status)
                stripe.assert_not_called()
            with self.billing._connect() as connection:
                self.assertEqual(before, connection.execute("SELECT * FROM work_checkouts").fetchall())
                self.assertEqual(activation_before, connection.execute("SELECT * FROM work_activation").fetchall())
            self.assertIsNone(self.billing.binding("company"))

        self.billing.activation.reset("company", "operator", "expired-creation-reviewed")
        self.billing.activation.review("company", "operator", "approve", "requalified-fixture")
        def response(path, values, **options):
            self.assertNotEqual(before[0][0], values["client_reference_id"])
            self.assertEqual(values["client_reference_id"], options["idempotency"])
            return {"id": "cs_live_NewSynthetic", "url": "https://checkout.stripe.com/c/pay/NewSynthetic", "livemode": True,
                "mode": "subscription", "client_reference_id": values["client_reference_id"], "expires_at": int(values["expires_at"])}
        with patch.object(self.billing, "_stripe", side_effect=response) as stripe:
            self.billing.activation.checkout("company", "https://gateway.example/work")
        stripe.assert_called_once()
        with self.billing._connect() as connection:
            rows = connection.execute("SELECT state,expires_at FROM work_checkouts ORDER BY created_at").fetchall()
        self.assertEqual([("blocked", original_time + 3600), ("open", self.now + 3600)], rows)
        self.assertFalse(self.billing.entitled("company"))

    def test_checkout_response_must_confirm_exact_unexpired_creation_deadline(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        for deadline in (None, True, "3600", self.now + 3599):
            def response(path, values, **options):
                return {"id": "cs_live_Synthetic", "url": "https://checkout.stripe.com/c/pay/Synthetic", "livemode": True,
                    "mode": "subscription", "client_reference_id": values["client_reference_id"], "expires_at": deadline}
            with self.subTest(deadline=deadline), patch.object(self.billing, "_stripe", side_effect=response):
                with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_invalid_response"):
                    self.billing.activation.checkout("company", "https://gateway.example/work")
        def delayed_response(path, values, **options):
            self.now = int(values["expires_at"])
            return {"id": "cs_live_Synthetic", "url": "https://checkout.stripe.com/c/pay/Synthetic", "livemode": True,
                "mode": "subscription", "client_reference_id": values["client_reference_id"], "expires_at": int(values["expires_at"])}
        with patch.object(self.billing, "_stripe", side_effect=delayed_response):
            with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_invalid_response"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
        with self.billing._connect() as connection:
            row = connection.execute("SELECT state,session_id,url FROM work_checkouts").fetchone()
        self.assertEqual(("creating", None, None), row)
        self.assertIsNone(self.billing.binding("company"))

    def test_uncertain_creation_blocks_model_or_client_rotation_even_after_expiry(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        original_time = self.now
        with patch.object(self.billing, "_stripe", side_effect=WorkRuntimeError("billing_api_unavailable", 503)) as stripe:
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
        stripe.assert_called_once()
        before = self.checkout_records()
        for elapsed in (0, 3601):
            self.now = original_time + elapsed
            for model, client in (("different-model", "codex"), ("approved-model", "claude-code")):
                with self.subTest(elapsed=elapsed, model=model, client=client), patch.object(self.billing, "_stripe") as stripe:
                    with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending"):
                        self.billing.activation.request("company", "owner", model, client)
                    stripe.assert_not_called()
                self.assertEqual(before, self.checkout_records())
            self.assertEqual("qualified", self.billing.activation.request("company", "owner", "approved-model", "codex")["state"])
            self.assertEqual(before, self.checkout_records())

        self.billing.activation.reset("company", "operator", "uncertain-creation-reviewed")
        self.billing.activation.request("company", "owner", "different-model", "claude-code")
        self.billing.activation.review("company", "operator", "approve", "new-connection-qualified")
        with patch.object(self.billing, "_stripe", side_effect=lambda path, values, **options: self.checkout_response(values)) as stripe:
            self.billing.activation.checkout("company", "https://gateway.example/work")
        stripe.assert_called_once()
        rows, _ = self.checkout_records()
        self.assertEqual({"blocked", "open"}, {row[6] for row in rows})
        self.assertEqual(2, len(rows))
        self.assertFalse(self.billing.entitled("company"))

    def test_checkout_organization_barrier_rejects_old_generation_or_price(self):
        for index, (state, expired, mismatch) in enumerate((
            ("creating", False, "generation"), ("creating", True, "generation"),
            ("creating", False, "price"), ("creating", True, "price"),
            ("open", False, "generation"), ("open", False, "price"),
        )):
            organization = f"company-{index}"
            self.billing.activation.request(organization, "owner", "approved-model", "codex")
            self.billing.activation.review(organization, "operator", "approve", "qualified-fixture")
            def response(path, values, **options):
                if state == "creating":
                    raise WorkRuntimeError("billing_api_unavailable", 503)
                return self.checkout_response(values, f"cs_live_Synthetic{index}")
            with patch.object(self.billing, "_stripe", side_effect=response) as first:
                if state == "creating":
                    with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                        self.billing.activation.checkout(organization, "https://gateway.example/work")
                else:
                    self.billing.activation.checkout(organization, "https://gateway.example/work")
            first.assert_called_once()
            if expired:
                self.now += 3601
            billing = self.billing
            if mismatch == "generation":
                with billing._connect() as connection:
                    connection.execute("UPDATE work_activation SET generation='legacy-changed-generation' WHERE organization_id=?", (organization,))
            else:
                billing = WorkBilling(self.billing.path, "price_New", [], self.secret, api_key="synthetic-api-fixture", clock=lambda: self.now)
            before = self.checkout_records(organization, billing=billing)
            with self.subTest(state=state, expired=expired, mismatch=mismatch), patch.object(billing, "_stripe") as stripe:
                with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending"):
                    billing.activation.checkout(organization, "https://gateway.example/work")
                stripe.assert_not_called()
            self.assertEqual(before, self.checkout_records(organization, billing=billing))
            self.assertIsNone(billing.binding(organization))

    def test_open_checkout_blocks_generation_change_but_other_organizations_are_independent(self):
        self.checkout()
        before = self.checkout_records()
        for model, client in (("different-model", "codex"), ("approved-model", "claude-code")):
            with self.subTest(model=model, client=client), patch.object(self.billing, "_stripe") as stripe:
                with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending"):
                    self.billing.activation.request("company", "owner", model, client)
                stripe.assert_not_called()
            self.assertEqual(before, self.checkout_records())
        self.billing.activation.request("other-company", "other-owner", "different-model", "claude-code")
        self.billing.activation.review("other-company", "operator", "approve", "independent-qualification")
        with patch.object(self.billing, "_stripe", side_effect=lambda path, values, **options: self.checkout_response(values, "cs_live_Other")) as stripe:
            self.billing.activation.checkout("other-company", "https://gateway.example/work")
        stripe.assert_called_once()
        self.assertEqual(before, self.checkout_records())
        other_before = self.checkout_records("other-company")
        self.now += 3601
        self.billing.activation.request("company", "owner", "different-model", "claude-code")
        self.billing.activation.review("company", "operator", "approve", "expired-open-reviewed")
        with patch.object(self.billing, "_stripe", side_effect=lambda path, values, **options: self.checkout_response(values, "cs_live_AfterKnownExpiry")) as stripe:
            self.billing.activation.checkout("company", "https://gateway.example/work")
        stripe.assert_called_once()
        self.assertEqual(2, len(self.checkout_records()[0]))
        self.assertEqual(other_before, self.checkout_records("other-company"))

    def test_creating_retry_after_restart_preserves_origin_and_financial_parameters(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        original_url = "https://workspace.example/work"
        with patch.object(self.billing, "_stripe", side_effect=WorkRuntimeError("billing_api_unavailable", 503)) as first:
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", original_url)
        before = self.checkout_records()
        self.assertEqual(original_url, before[0][0][-1])
        restarted = WorkBilling(self.billing.path, "price_Approved", [], self.secret, api_key="synthetic-api-fixture", clock=lambda: self.now)
        with patch.object(restarted, "_stripe") as stripe:
            with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending"):
                restarted.activation.checkout("company", "https://custom.example/work")
            stripe.assert_not_called()
        self.assertEqual(before, self.checkout_records(billing=restarted))
        with patch.object(restarted, "_stripe", side_effect=lambda path, values, **options: self.checkout_response(values)) as retry:
            restarted.activation.checkout("company", original_url)
        retry.assert_called_once()
        self.assertEqual(first.call_args, retry.call_args)
        self.assertFalse(restarted.entitled("company"))

    def test_legacy_checkout_migration_preserves_history_and_blocks_unknown_creating_origin(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        with patch.object(self.billing, "_stripe", side_effect=WorkRuntimeError("billing_api_unavailable", 503)):
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
        with self.billing._connect() as connection:
            connection.execute("ALTER TABLE work_checkouts DROP COLUMN return_url")
            legacy_rows = connection.execute("SELECT * FROM work_checkouts").fetchall()
        for _ in range(2):
            self.billing = WorkBilling(self.billing.path, "price_Approved", [], self.secret, api_key="synthetic-api-fixture", clock=lambda: self.now)
            with self.billing._connect() as connection:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(work_checkouts)")]
                self.assertEqual(1, columns.count("return_url"))
                self.assertEqual([(*row, None) for row in legacy_rows], connection.execute("SELECT * FROM work_checkouts").fetchall())
            before = self.checkout_records()
            with patch.object(self.billing, "_stripe") as stripe:
                with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending"):
                    self.billing.activation.checkout("company", "https://gateway.example/work")
                stripe.assert_not_called()
            self.assertEqual(before, self.checkout_records())

        self.billing.activation.reset("company", "operator", "legacy-creation-reviewed")
        self.billing.activation.review("company", "operator", "approve", "legacy-connection-requalified")
        with patch.object(self.billing, "_stripe", side_effect=lambda path, values, **options: self.checkout_response(values)):
            opened = self.billing.activation.checkout("company", "https://gateway.example/work")
        with self.billing._connect() as connection:
            connection.execute("ALTER TABLE work_checkouts DROP COLUMN return_url")
        self.billing = WorkBilling(self.billing.path, "price_Approved", [], self.secret, api_key="synthetic-api-fixture", clock=lambda: self.now)
        before = self.checkout_records()
        with patch.object(self.billing, "_stripe") as stripe:
            self.assertEqual(opened, self.billing.activation.checkout("company", "https://custom.example/work"))
            stripe.assert_not_called()
        self.assertEqual(before, self.checkout_records())

    def test_multiple_legacy_outstanding_creations_cannot_select_a_new_key(self):
        self.billing.activation.request("company", "owner", "approved-model", "codex")
        self.billing.activation.review("company", "operator", "approve", "qualified-fixture")
        with patch.object(self.billing, "_stripe", side_effect=WorkRuntimeError("billing_api_unavailable", 503)):
            with self.assertRaisesRegex(WorkRuntimeError, "billing_api_unavailable"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
        with self.billing._connect() as connection:
            generation = connection.execute("SELECT generation FROM work_activation WHERE organization_id='company'").fetchone()[0]
            connection.execute("INSERT INTO work_checkouts(reference,organization_id,generation,price_id,state,expires_at,created_at,return_url) VALUES('hormuz_checkout_Legacy','company',?,'price_Approved','creating',?,?,?)",
                (generation, self.now - 1, self.now - 3601, "https://gateway.example/work"))
        before = self.checkout_records()
        with patch.object(self.billing, "_stripe") as stripe:
            with self.assertRaisesRegex(WorkRuntimeError, "billing_checkout_retry_pending"):
                self.billing.activation.checkout("company", "https://gateway.example/work")
            stripe.assert_not_called()
        self.assertEqual(before, self.checkout_records())

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
