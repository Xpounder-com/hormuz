"""Hosted AI Work boundary fixtures, without provider or Stripe network calls."""
import hashlib
import hmac
import http.client
import json
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from hormuz._hosted_config import HostedError, SECRET_NAMES
from hormuz._hosted_provider import (
    PROVIDER_CHILD_ENV_NAMES, PROVIDER_WORK_BILLING_ENV_NAMES,
    load_provider_profile,
)
from hormuz._hosted_server import ProviderPilotGatewayServer, _ai_work_route
from hormuz._hosted_state import initialize
from hormuz.config import AIWorkConfig, UsageStorageConfig
from hormuz.hosted import proxy_settings, runtime_settings
from hormuz.work_billing import STRIPE_VERSION
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import console_credential, directory_setup, provider_profile

# Exercise the live-key shape validator without a credential-shaped literal in
# source. This deterministic fixture is never sent to Stripe.
SYNTHETIC_BILLING_API_KEY = "sk_" + "live_" + "A" * 32
from tests.test_hosted_provider import _ProviderResponse


class HostedWorkConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.config, self.staging, self.settings, self.document = provider_profile(self.root)
        self.path = Path(self.settings["HORMUZ_PROVIDER_CONFIG"])
        self.document["ai_work"] = {
            "enabled": True, "database": str(self.config.database_path.parent / "hormuz-work.sqlite3"),
            "require_paid": True, "billing_price_id": "price_Reviewed",
            "billing_portal_configuration_id": "bpc_Reviewed",
            "billing_bindings": [{"organization_id": "customer-a", "customer_id": "cus_Reviewed", "subscription_id": "sub_Reviewed"}],
        }
        self.settings.update({
            "HORMUZ_WORK_BILLING_WEBHOOK_SECRET": "whsec_synthetic_fixture_not_real",
            "HORMUZ_WORK_BILLING_API_KEY": SYNTHETIC_BILLING_API_KEY,
        })

    def load(self):
        self.path.write_text(json.dumps(self.document))
        return load_provider_profile(self.staging.source_path, self.path, self.settings)

    def test_reviewed_paid_config_and_child_secret_boundary(self):
        loaded = self.load()
        self.assertTrue(loaded.ai_work.require_paid)
        self.assertEqual("bpc_Reviewed", loaded.ai_work.billing_portal_configuration_id)
        with patch.dict(os.environ, {**self.settings, "UNRELATED_SECRET": "never-inherit"}, clear=True):
            settings = runtime_settings()
        child = {name: settings[name] for name in PROVIDER_CHILD_ENV_NAMES}
        for name in PROVIDER_WORK_BILLING_ENV_NAMES:
            self.assertEqual(child[name], self.settings[name])
            self.assertNotIn(name, proxy_settings(settings, active=True))
            self.assertNotIn(name, SECRET_NAMES)
        self.assertNotIn("UNRELATED_SECRET", child)

    def test_hosted_work_rejects_wrong_store_and_unreviewed_secret_names(self):
        for field, value in (("database", str(self.root / "other.sqlite3")),
                             ("billing_api_key_env", "UNREVIEWED_KEY"),
                             ("billing_webhook_secret_env", "UNREVIEWED_WEBHOOK")):
            with self.subTest(field=field):
                original = self.document["ai_work"].copy()
                self.document["ai_work"][field] = value
                with self.assertRaises(HostedError):
                    self.load()
                self.document["ai_work"] = original

    def test_missing_testmode_and_inactive_billing_credentials_rejected(self):
        for secret_name, value in (("HORMUZ_WORK_BILLING_WEBHOOK_SECRET", ""),
                                   ("HORMUZ_WORK_BILLING_API_KEY", "sk_test_syntheticfixturekeynotreal")):
            with self.subTest(secret=secret_name):
                original = self.settings[secret_name]
                self.settings[secret_name] = value
                with self.assertRaisesRegex(HostedError, "billing_credential_invalid"):
                    self.load()
                self.settings[secret_name] = original
        self.document.pop("ai_work")
        with self.assertRaisesRegex(HostedError, "inactive_credential_forbidden"):
            self.load()

    def test_only_known_work_path_shapes_admitted_at_private_hop(self):
        for path in ("/work", "/work.css", "/v1/work/billing/webhook", "/v1/work/jobs/work_abc/observations", "/work/jobs/work_abc"):
            self.assertTrue(_ai_work_route(path), path)
        for path in ("/v1/work/admin", "/work/jobs/../admin", "/v1/work/jobs/job/delete", "/v1/work/secrets", "/work-anything"):
            self.assertFalse(_ai_work_route(path), path)
        caddy = (Path(__file__).resolve().parents[1] / "deploy/render/gateway/provider-pilot.Caddyfile").read_text()
        for path in ("/v1/models", "/v1/chat/completions", "/work.css", "/v1/work/billing/webhook"):
            self.assertIn(path, caddy)
        self.assertNotIn("/v1/work/*", caddy)


@unittest.skipUnless(os.name == "posix", "Hosted state needs POSIX permissions")
class HostedWorkHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        config, self.staging, self.settings, _ = provider_profile(self.root)
        initialize(self.staging)
        self.secret = "whsec_synthetic_fixture_not_real"
        self.config = replace(config, listen=replace(config.listen, port=0), usage_storage=UsageStorageConfig(),
            ai_work=AIWorkConfig(enabled=True, database_path=config.database_path.parent / "hormuz-work.sqlite3",
                billing_price_id="price_Reviewed", require_paid=True,
                billing_bindings=(("customer-a", "cus_Reviewed", "sub_Reviewed"),)))
        environment = {name: self.settings.get(name, "") for name in PROVIDER_CHILD_ENV_NAMES}
        environment["HORMUZ_WORK_BILLING_WEBHOOK_SECRET"] = self.secret
        environment["HORMUZ_WORK_BILLING_API_KEY"] = SYNTHETIC_BILLING_API_KEY
        self.gateway = ProviderPilotGatewayServer(self.config, environ=environment)
        self.thread = threading.Thread(target=self.gateway.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        directory_setup(self.gateway.session_broker.directory, self.config)
        self.member, self.pair = activate_member(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        self.token_headers = {"Authorization": "Bearer " + self.pair.access_token}

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=2)
        fields = {"Host": "gateway.example.test", "X-Hormuz-Ingress-Credential": self.config.ingress.credential}
        fields.update(headers or {})
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        if encoded is not None:
            fields["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def webhook(self, kind, item, *, ingress=None, signature=True, identifier="evt_fixture"):
        now = int(time.time())
        event = {"id": identifier, "type": kind, "created": now, "api_version": STRIPE_VERSION,
                 "livemode": True, "data": {"object": item}}
        raw = json.dumps(event, separators=(",", ":")).encode()
        digest = hmac.new(self.secret.encode(), str(now).encode() + b"." + raw, hashlib.sha256).hexdigest()
        headers = {"Stripe-Signature": f"t={now},v1={digest if signature else '0'*64}"}
        if ingress is not None:
            headers["X-Hormuz-Ingress-Credential"] = ingress
        return self.request("POST", "/v1/work/billing/webhook", body=event, headers=headers)

    def test_anonymous_work_uses_available_console_sign_in(self):
        status, _, body = self.request("GET", "/work")
        self.assertEqual(200, status)
        self.assertIn(b'class="button" href="/console"', body)
        self.assertNotIn(b'href="/workspace"', body)
        console_status, _, _ = self.request("GET", "/console")
        self.assertNotEqual(503, console_status)

    def test_work_routes_authority_and_unknown_routes_stay_closed(self):
        with patch("hormuz.server._open_upstream") as provider:
            self.assertEqual(self.request("GET", "/work")[0], 200)
            self.assertEqual(self.request("GET", "/work.css")[0], 200)
            self.assertEqual(self.request("GET", "/v1/work/state")[0], 401)
            self.assertEqual(self.request("GET", "/v1/work/state", headers=self.token_headers)[0], 200)
            self.assertEqual(self.request("GET", "/v1/models")[0], 401)
            self.assertEqual(self.request("GET", "/v1/models", headers=self.token_headers)[0], 200)
            self.assertEqual(self.request("GET", "/work", headers={"Host": "foreign.example.test"})[0], 400)
            self.assertEqual(self.request("GET", "/work", headers={"X-Hormuz-Ingress-Credential": "forged"})[0], 401)
            self.assertEqual(self.request("GET", "/v1/work/unknown")[0], 503)
            self.assertEqual(self.request("POST", "/v1/chat/completions", body={"model": "openai-primary", "messages": []})[0], 401)
        provider.assert_not_called()

    def test_browser_mutation_retains_origin_csrf_and_native_plan_authority(self):
        self.gateway.console.sessions.grant(organization_id="customer-a", membership_id=self.member.membership_id, role="member_admin")
        console, credential = console_credential(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        cookie = {"Cookie": "__Host-hormuz_console=" + credential}
        self.assertEqual(self.request("GET", "/work", headers=cookie)[0], 200)
        body = {"repository": "fixture/repo", "csrf_token": console.csrf_token(credential)}
        self.assertEqual(self.request("POST", "/v1/work/jobs", body=body, headers=cookie)[0], 403)
        status, _, _ = self.request("POST", "/v1/work/jobs", body=body,
            headers={**cookie, "Origin": "https://gateway.example.test"})
        self.assertEqual(status, 201)
        self.assertEqual(self.request("POST", "/v1/work/policies", body={"scope_type": "workspace", "scope_id": "customer-a", "budget_usd": "5", "objective": "cost"}, headers=self.token_headers)[0], 403)

    def test_live_signed_webhook_requires_ingress_and_both_paid_evidence(self):
        now = int(time.time())
        subscription = {"id": "sub_Reviewed", "customer": "cus_Reviewed", "status": "active", "items": {"data": [
            {"price": {"id": "price_Reviewed"}, "quantity": 1, "current_period_start": now - 1, "current_period_end": now + 3600}]}}
        self.assertEqual(self.webhook("customer.subscription.updated", subscription, ingress="wrong")[0], 401)
        self.assertEqual(self.webhook("customer.subscription.updated", subscription, signature=False)[0], 400)
        self.assertFalse(self.gateway.work_billing.entitled("customer-a"))
        self.assertEqual(self.webhook("customer.subscription.updated", subscription)[0], 200)
        self.assertFalse(self.gateway.work_billing.entitled("customer-a"))
        invoice = {"customer": "cus_Reviewed", "parent": {"subscription_details": {"subscription": "sub_Reviewed"}},
            "status": "paid", "amount_paid": 4999, "lines": {"data": [{"pricing": {"price_details": {"price": "price_Reviewed"}},
            "period": {"start": now - 1, "end": now + 3600}}]}}
        self.assertEqual(self.webhook("invoice.paid", invoice, identifier="evt_invoice")[0], 200)
        self.assertTrue(self.gateway.work_billing.entitled("customer-a"))
        self.assertFalse(self.gateway.work_billing.entitled("customer-b"))

    def test_chat_follows_paid_gate_and_provider_capacity_accounting(self):
        status, _, raw = self.request("POST", "/v1/work/jobs", body={"repository": "fixture/repo"}, headers=self.token_headers)
        self.assertEqual(status, 201)
        work_id = json.loads(raw)["work_id"]
        headers = {**self.token_headers, "X-Hormuz-Work-ID": work_id}
        body = {"model": "openai-primary", "messages": [{"role": "user", "content": "synthetic fixture"}], "max_tokens": 8}
        with patch("hormuz.server._open_upstream") as provider:
            self.assertEqual(self.request("POST", "/v1/chat/completions", body=body, headers=headers)[0], 402)
        provider.assert_not_called()
        self.assertEqual(self.gateway.operational_stats()["provider"]["inflight"], 0)
        now = int(time.time())
        subscription = {"id": "sub_Reviewed", "customer": "cus_Reviewed", "status": "active", "items": {"data": [
            {"price": {"id": "price_Reviewed"}, "quantity": 1, "current_period_start": now - 1, "current_period_end": now + 3600}]}}
        invoice = {"customer": "cus_Reviewed", "parent": {"subscription_details": {"subscription": "sub_Reviewed"}},
            "status": "paid", "amount_paid": 4999, "lines": {"data": [{"pricing": {"price_details": {"price": "price_Reviewed"}},
            "period": {"start": now - 1, "end": now + 3600}}]}}
        self.assertEqual(self.webhook("customer.subscription.updated", subscription)[0], 200)
        self.assertEqual(self.webhook("invoice.paid", invoice, identifier="evt_invoice")[0], 200)
        response = _ProviderResponse(200, {"id": "chatcmpl_fixture", "object": "chat.completion", "created": now,
            "model": "openai-primary-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}})
        with patch("hormuz.server._open_upstream", return_value=response) as provider:
            status, fields, _ = self.request("POST", "/v1/chat/completions", body=body, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(fields["X-Hormuz-Work-ID"], work_id)
        provider.assert_called_once()
        self.assertEqual(self.gateway.operational_stats()["provider"]["inflight"], 0)
        status, _, raw = self.request("GET", "/v1/work/jobs/" + work_id, headers=self.token_headers)
        self.assertEqual(status, 200)
        work = json.loads(raw)
        self.assertEqual(work["attempts"][0]["state"], "succeeded")
        self.assertEqual(work["attempts"][0]["cost_microusd"], 8)
        self.assertEqual(work["outcome_evidence"], "unknown")
