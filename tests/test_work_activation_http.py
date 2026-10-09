"""Actual hosted HTTP authority/CSRF boundaries for the guided paid workflow."""
from dataclasses import replace
import json
import time
import unittest
from unittest.mock import patch

from hormuz.work_billing import WorkBilling
from tests._hosted_fixtures import console_credential
from tests import test_hosted_work as hosted_tests


class WorkActivationHTTPTests(unittest.TestCase):
    setUp = hosted_tests.HostedWorkHTTPTests.setUp
    tearDown = hosted_tests.HostedWorkHTTPTests.tearDown
    request = hosted_tests.HostedWorkHTTPTests.request
    webhook = hosted_tests.HostedWorkHTTPTests.webhook

    def prepare(self):
        self.gateway.work_billing = WorkBilling(self.root / "activation.billing.sqlite3", "price_Reviewed", [], self.secret, api_key="synthetic-api-fixture")
        self.gateway.console.sessions.grant(organization_id="customer-a", membership_id=self.member.membership_id, role="member_admin")
        self.console, credential = console_credential(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        self.credential = credential
        self.browser = {"Cookie": "__Host-hormuz_console=" + credential, "Origin": "https://gateway.example.test"}
        self.csrf = self.console.csrf_token(credential)

    def test_customer_cannot_approve_submit_payment_ids_or_bypass_csrf(self):
        self.prepare()
        values = {"model": "openai-primary", "client": "codex", "csrf_token": self.csrf}
        self.assertEqual(self.request("POST", "/v1/work/activation/request", body=values, headers=self.browser)[0], 200)
        self.assertEqual(self.request("POST", "/v1/work/activation/review", body={"action": "approve", "reference": "browser-claim", "csrf_token": self.csrf}, headers=self.browser)[0], 403)
        self.assertEqual(self.request("POST", "/v1/work/billing/checkout", body={"customer_id": "cus_Forged", "csrf_token": self.csrf}, headers=self.browser)[0], 400)
        self.assertEqual(self.request("POST", "/v1/work/activation/request", body={**values, "csrf_token": "wrong"}, headers=self.browser)[0], 403)
        self.assertEqual(self.request("POST", "/v1/work/activation/request", body={**values, "model": "not-allowed"}, headers=self.browser)[0], 403)

    def test_operator_review_checkout_signed_activation_and_own_support_receipt(self):
        self.prepare()
        self.gateway.config = replace(self.gateway.config, ai_work=replace(self.gateway.config.ai_work, administrator_actor_ids=(self.member.membership_id,)))
        self.assertEqual(self.request("POST", "/v1/work/activation/request", body={"model": "openai-primary", "client": "codex", "csrf_token": self.csrf}, headers=self.browser)[0], 200)
        self.assertEqual(self.request("POST", "/v1/work/activation/review", body={"action": "approve", "reference": "qualified-operator-fixture", "csrf_token": self.csrf}, headers=self.browser)[0], 200)
        def response(path, values, **kwargs):
            self.reference = values["client_reference_id"]
            self.assertEqual(kwargs["idempotency"], self.reference)
            return {"id": "cs_live_Fixture", "url": "https://checkout.stripe.com/c/pay/Fixture", "livemode": True,
                "mode": "subscription", "client_reference_id": self.reference}
        with patch.object(self.gateway.work_billing, "_stripe", side_effect=response):
            status, _, raw = self.request("POST", "/v1/work/billing/checkout", body={"csrf_token": self.csrf}, headers=self.browser)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["url"], "https://checkout.stripe.com/c/pay/Fixture")
        checkout = {"id": "cs_live_Fixture", "client_reference_id": self.reference, "mode": "subscription", "status": "complete", "payment_status": "paid", "customer": "cus_Reviewed", "subscription": "sub_Reviewed"}
        self.assertEqual(self.webhook("checkout.session.completed", checkout)[0], 200)
        self.assertFalse(self.gateway.work_billing.entitled("customer-a"))
        now = int(time.time())
        subscription = {"id": "sub_Reviewed", "customer": "cus_Reviewed", "status": "active", "items": {"data": [{"price": {"id": "price_Reviewed"}, "quantity": 1, "current_period_start": now-1, "current_period_end": now+3600}]}}
        invoice = {"customer": "cus_Reviewed", "parent": {"subscription_details": {"subscription": "sub_Reviewed"}}, "status": "paid", "amount_paid": 1000,
            "lines": {"data": [{"price": {"id": "price_Reviewed"}, "period": {"start": now-1, "end": now+3600}}]}}
        self.assertEqual(self.webhook("customer.subscription.updated", subscription, identifier="evt_subscription")[0], 200)
        self.assertFalse(self.gateway.work_billing.entitled("customer-a"))
        self.assertEqual(self.webhook("invoice.paid", invoice, identifier="evt_invoice")[0], 200)
        self.assertTrue(self.gateway.work_billing.entitled("customer-a"))
        self.assertEqual(self.request("GET", "/v1/work/support/receipt", headers=self.token_headers)[0], 200)
        status, _, raw = self.request("GET", "/v1/work/activation", headers={"Cookie": self.browser["Cookie"]})
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)["state"], "active")
        with patch.object(self.gateway.work_billing, "_stripe", return_value={"customer": "cus_Reviewed", "url": "https://billing.stripe.com/p/session/Fixture"}):
            self.assertEqual(self.request("POST", "/v1/work/billing/portal", body={"csrf_token": self.csrf}, headers=self.browser)[0], 200)
        self.assertEqual(self.webhook("customer.subscription.deleted", subscription, identifier="evt_cancellation")[0], 200)
        self.assertFalse(self.gateway.work_billing.entitled("customer-a"))
        self.gateway.work_billing = WorkBilling(self.gateway.work_billing.path, "price_New", [], self.secret, api_key="synthetic-api-fixture")
        status, _, raw = self.request("GET", "/v1/work/state", headers={"Cookie": self.browser["Cookie"]})
        self.assertEqual(status, 200, raw)
        onboarding = json.loads(raw)["onboarding"]
        self.assertFalse(onboarding["can_checkout"])
        self.assertTrue(onboarding["can_open_portal"])
        self.assertEqual(onboarding["next_step"], "manage_subscription")

    def test_operator_application_grant_invalidates_old_native_and_browser_sessions(self):
        self.prepare()
        with self.gateway.session_broker.store._connection() as connection:
            connection.execute("UPDATE onboarding_memberships SET allowed_clients='[]' WHERE id=?", (self.member.membership_id,))
        self.gateway.config = replace(self.gateway.config, ai_work=replace(self.gateway.config.ai_work, administrator_actor_ids=(self.member.membership_id,)))
        self.assertEqual(self.request("POST", "/v1/work/activation/request", body={"model": "openai-primary", "client": "codex", "csrf_token": self.csrf}, headers=self.browser)[0], 200)
        self.assertEqual(self.request("POST", "/v1/work/activation/review", body={"action": "approve", "reference": "qualified-scope-fixture", "csrf_token": self.csrf}, headers=self.browser)[0], 200)
        with self.gateway.session_broker.store._connection() as connection:
            member = self.gateway.session_broker.directory._member(connection, "customer-a", self.member.membership_id)
            self.assertEqual(json.loads(member["allowed_clients"]), ["codex"])
        self.assertEqual(self.request("GET", "/v1/work/state", headers={"Cookie": self.browser["Cookie"]})[0], 401)
        self.assertEqual(self.request("GET", "/v1/work/state", headers=self.token_headers)[0], 401)

    def test_receipt_exports_only_authenticated_owner_jobs(self):
        self.prepare()
        from hormuz.config import Identity
        other = Identity(token_env="", token="", actor_id="other-member", actor_name="", team_id="customer-a-eng", team_name="", organization_id="customer-a")
        other_job = self.gateway.work_runtime.create_work(other, "private/repository")
        status, _, raw = self.request("GET", "/v1/work/support/receipt", headers={"Cookie": self.browser["Cookie"]})
        self.assertEqual(status, 200, raw)
        self.assertNotIn(other_job["work_id"].encode(), raw)
        self.assertNotIn(b"private/repository", raw)

    def test_actual_http_funnel_tracks_connection_and_owned_receipt_only_after_consent(self):
        self.prepare()
        self.assertEqual(self.request("GET", "/v1/work/connect", headers=self.token_headers)[0], 200)
        _, _, raw = self.request("GET", "/v1/work/state", headers=self.token_headers)
        self.assertEqual(json.loads(raw)["funnel"], {"consent": False, "events": {}})
        status, _, raw = self.request("POST", "/v1/work/acquisition", body={"utm_source": "fixture-campaign", "analytics_consent": True}, headers=self.token_headers)
        self.assertEqual(status, 201, raw)
        self.assertEqual(self.request("GET", "/v1/work/connect", headers=self.token_headers)[0], 200)
        status, _, raw = self.request("POST", "/v1/work/jobs", body={"repository": "fixture/repo"}, headers=self.token_headers)
        self.assertEqual(status, 201, raw)
        work_id = json.loads(raw)["work_id"]
        self.assertEqual(self.request("GET", "/v1/work/jobs/" + work_id, headers=self.token_headers)[0], 200)
        _, _, raw = self.request("GET", "/v1/work/state", headers=self.token_headers)
        state = json.loads(raw)
        self.assertTrue(state["funnel"]["consent"])
        # Reading setup records no actual API-response or native qualification.
        self.assertEqual(state["funnel"]["events"]["qualified_connection"], 0)
        self.assertEqual(state["funnel"]["events"]["receipt_opened"], 1)
        self.assertEqual(state["funnel"]["events"]["payment_verified"], 0)
        # Re-opening the same receipt is not another conversion.
        self.request("GET", "/v1/work/jobs/" + work_id, headers=self.token_headers)
        _, _, raw = self.request("GET", "/v1/work/state", headers=self.token_headers)
        self.assertEqual(json.loads(raw)["funnel"]["events"]["receipt_opened"], 1)
