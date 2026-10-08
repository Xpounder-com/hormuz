"""Real HTTP admission and signed-payment boundaries, without paid egress."""
import hashlib
import hmac
import http.client
import json
import time
import unittest
from unittest.mock import patch

from hormuz.postgres import PostgresStorageError
from hormuz.work_billing import STRIPE_VERSION, WorkBilling
from tests import test_work_gateway as fixtures


class WorkTransportTests(unittest.TestCase):
    setUp = fixtures.WorkGatewayTests.setUp
    start = fixtures.WorkGatewayTests.start
    tearDown = fixtures.WorkGatewayTests.tearDown
    job = fixtures.WorkGatewayTests.job
    post = fixtures.WorkGatewayTests.post

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, json.loads(data)

    def test_unbounded_chat_options_cannot_dispatch_or_spend(self):
        work = self.job()
        options = ({"n": 128}, {"n": True}, {"web_search_options": {}},
            {"modalities": ["text", "audio"]}, {"audio": {}},
            {"messages": [{"role": "user", "content": [{"type": "image_url",
                "image_url": {"url": "https://example.test/image.png"}}]}]})
        for fields in options:
            with self.subTest(fields=fields):
                status, _, body = self.post(work, **fields)
                self.assertEqual(422, status, body)
                self.assertIn(b"request_cost_unbounded", body)
        self.assertEqual([], fixtures.WorkProvider.requests)
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(0, state["costs"]["consumed_microusd"])
        self.assertEqual(200, self.post(work, n=1)[0])

    def test_signed_billing_post_dispatches_without_bearer_and_get_cannot_mutate(self):
        now = int(time.time())
        secret = "whsec_transport_synthetic_secret"
        self.gateway.work_billing = WorkBilling(self.root / "transport-billing.sqlite3", "price_Approved",
            [(self.identity.organization_id, "cus_Approved", "sub_Approved")], secret)
        event = {"id": "evt_transport", "type": "customer.subscription.updated", "created": now,
            "api_version": STRIPE_VERSION, "livemode": True, "data": {"object": {
                "id": "sub_Approved", "customer": "cus_Approved", "status": "active",
                "items": {"data": [{"price": {"id": "price_Approved"}, "quantity": 1,
                    "current_period_start": now, "current_period_end": now + 3600}]}}}}
        body = json.dumps(event).encode()
        signature = hmac.new(secret.encode(), str(now).encode() + b"." + body, hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json", "Stripe-Signature": f"t={now},v1={signature}"}
        self.assertEqual(405, self.request("GET", "/v1/work/billing/webhook", body, headers)[0])
        status, result = self.request("POST", "/v1/work/billing/webhook", body, headers)
        self.assertEqual(200, status, result)
        self.assertEqual("applied", result["status"])
        self.assertEqual("duplicate", self.request("POST", "/v1/work/billing/webhook", body, headers)[1]["status"])
        self.assertEqual(400, self.request("POST", "/v1/work/billing/webhook", b"{}",
            {"Content-Type": "application/json", "Stripe-Signature": f"t={now},v1={'0'*64}"})[0])

    def test_readiness_checks_work_and_billing_without_recreating_missing_stores(self):
        self.assertEqual(200, self.request("GET", "/ready")[0])
        path = self.gateway.work_runtime.path
        moved = path.with_suffix(".moved")
        path.rename(moved)
        try:
            self.assertEqual(503, self.request("GET", "/ready")[0])
            self.assertFalse(path.exists())
        finally:
            moved.rename(path)
        self.gateway.work_billing = WorkBilling(self.root / "readiness-billing.sqlite3", "price_Approved", [], "")
        self.assertEqual(200, self.request("GET", "/ready")[0])
        billing_path = self.gateway.work_billing.path
        billing_path.rename(billing_path.with_suffix(".moved"))
        self.assertEqual(503, self.request("GET", "/ready")[0])
        self.assertFalse(billing_path.exists())

    def test_model_catalog_authenticates_filters_aliases_and_handles_storage_failure(self):
        self.assertEqual(401, self.request("GET", "/v1/models")[0])
        headers = {"Authorization": "Bearer " + fixtures.GATEWAY_TOKEN}
        status, result = self.request("GET", "/v1/models", headers=headers)
        self.assertEqual(200, status)
        self.assertEqual("list", result["object"])
        self.assertEqual({"engineering-fast", "engineering-deep"}, {item["id"] for item in result["data"]})
        self.assertNotIn("gpt-test", json.dumps(result))
        claude_headers = {"Authorization": "Bearer " + fixtures.CLAUDE_ONLY_TOKEN}
        self.assertEqual([], self.request("GET", "/v1/models", headers=claude_headers)[1]["data"])
        with patch.object(self.gateway.policy_engine.policy_runtime, "snapshot_for", side_effect=PostgresStorageError("unavailable")):
            self.assertEqual(503, self.request("GET", "/v1/models", headers=headers)[0])

    def test_chat_requires_existing_explicit_openai_client_authorization(self):
        work = self.job()
        self.assertEqual(403, self.post(work, token=fixtures.CLAUDE_ONLY_TOKEN)[0])
        self.assertEqual([], fixtures.WorkProvider.requests)
