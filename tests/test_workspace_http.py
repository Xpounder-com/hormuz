"""Real local OIDC exchange and synthetic domain provider; no external traffic."""

import html
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from unittest import mock
from urllib.parse import urlencode, urlsplit

from hormuz.workspace_store import WorkspaceError
from hormuz.session_store import _isoformat
from tests._session_fixtures import SessionHTTPTestCase


class FakeDomains:
    target = "fixture.onrender.com"
    ownership = routing = verified = tls = True
    available = True

    def __init__(self, domains):
        self.domains = domains
        self.creates = self.removals = 0

    def owned(self, *args):
        return self.ownership

    def routed(self, *args):
        return self.routing

    def ensure(self, *args):
        self.creates += 1
        if not self.available:
            raise WorkspaceError("workspace_domain_unavailable")
        return "provider-domain-fixture", self.verified

    def tls_probe(self, hostname, domain_id, nonce):
        return self.domains.proof(hostname, domain_id, nonce) if self.tls else None

    def remove(self, *args):
        self.removals += 1
        if not self.available:
            raise WorkspaceError("workspace_domain_unavailable")


class WorkspaceHTTPTests(SessionHTTPTestCase):
    def configure_gateway(self, config):
        issuer_name = next(iter(config.oidc_issuers))
        issuer = config.oidc_issuers[issuer_name]
        return replace(config, session_broker=replace(config.session_broker, onboarding_enabled=True, workspace_enabled=True, workspace_signup_issuer=issuer_name), oidc_issuers={issuer_name: replace(issuer, login=replace(issuer.login, scopes=("openid", "email")))})

    def setUp(self):
        super().setUp()
        self.idp.claims_overrides = {"email": "alice@example.com", "email_verified": True}
        self.service = self.gateway.workspace
        self.sessions = self.service.sessions
        self.fake = FakeDomains(self.service.domains)
        self.service.domains.provider = self.fake

    def post(self, path, values, *, cookie="", host=None, origin=None):
        if path == "/v1/workspaces/auth/start" and not values:
            values = {"intent": "signin"}
        return self.request("POST", path, urlencode(values), {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie, "Origin": origin or ("https://" + host if host else self.gateway_url), **({"Host": host} if host else {})})

    def begin(self, *, path="/v1/workspaces/auth/start"):
        status, headers, page = self.post(path, {})
        self.assertEqual(status, 200, page)
        return self.authorization(page), headers["Set-Cookie"].split(";", 1)[0]

    def authorization(self, page):
        link = html.unescape(re.search(r'<a class="button" href="([^"]+)"', page)[1])
        parsed = urlsplit(link)
        status, _, values = self.request("GET", parsed.path + "?" + parsed.query, origin=self.idp.origin)
        self.assertEqual(status, 200, values)
        return values

    def login(self, subject="alice-subject", email="alice@example.com"):
        self.idp.subject = subject
        self.idp.claims_overrides = {"email": email, "email_verified": True}
        values, flow_cookie = self.begin()
        status, headers, page = self.post("/v1/workspaces/auth/callback", values, cookie=flow_cookie)
        self.assertEqual(status, 303, page)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, identity = self.request("GET", "/v1/workspaces/me", headers={"Cookie": cookie})
        self.assertEqual(status, 200, identity)
        return cookie, identity, headers["Location"]

    def activate_domain(self, cookie, identity, hostname="ai.customer.com"):
        credential = cookie.split("=", 1)[1]
        domain = self.service.domains.claim(credential, self.gateway_url, hostname)
        self.service.domains.check(credential, self.gateway_url, domain["id"])
        self.assertEqual(self.service.domains.list(credential, self.gateway_url)[0]["status"], "active")
        return domain

    def custom_login(self, hostname):
        status, headers, _ = self.post("/v1/workspaces/auth/start", {}, host=hostname)
        self.assertEqual(status, 303)
        handoff_cookie = headers["Set-Cookie"].split(";", 1)[0]
        target = urlsplit(headers["Location"])
        status, headers, page = self.request("GET", target.path + "?" + target.query)
        self.assertEqual(status, 200, page)
        flow_cookie = headers["Set-Cookie"].split(";", 1)[0]
        values = self.authorization(page)
        status, headers, page = self.post("/v1/workspaces/auth/callback", values, cookie=flow_cookie)
        self.assertEqual(status, 200, page)
        self.assertIn("form-action 'self' https://" + hostname, headers["Content-Security-Policy"])
        token = re.search(r'name="token" value="([^"]+)"', page)[1]
        return token, handoff_cookie

    def test_two_accounts_persistent_addresses_and_guessed_path_isolation(self):
        first, a, path = self.login()
        second, b, other = self.login("bob-subject", "bob@example.com")
        self.assertNotEqual(a["workspace_id"], b["workspace_id"])
        self.assertEqual(self.request("GET", path, headers={"Cookie": first})[0], 200)
        self.assertEqual(self.request("GET", other, headers={"Cookie": first})[0], 403)
        self.assertEqual(self.request("GET", path, headers={"Cookie": second})[0], 403)
        _, repeated, repeat_path = self.login()
        self.assertEqual((a["workspace_id"], path), (repeated["workspace_id"], repeat_path))
        with self.sessions.store._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0], 2)
            self.assertEqual({row[0] for row in connection.execute("SELECT allowed_clients FROM onboarding_memberships")}, {"[]"})
        self.assertEqual(self.idp.model_requests, 0)

    def test_callback_replay_and_browser_binding(self):
        values, cookie = self.begin()
        self.assertEqual(self.post("/v1/workspaces/auth/callback", values, cookie="")[0], 400)
        self.assertEqual(self.post("/v1/workspaces/auth/callback", values, cookie=cookie)[0], 303)
        self.assertEqual(self.post("/v1/workspaces/auth/callback", values, cookie=cookie)[0], 400)

    def test_verified_email_and_userinfo_subject_binding(self):
        self.idp.claims_overrides["email_verified"] = False
        values, cookie = self.begin()
        self.assertEqual(self.post("/v1/workspaces/auth/callback", values, cookie=cookie)[0], 403)
        self.idp.claims_overrides["email_verified"] = True
        self.idp.omit_claims = {"email", "email_verified"}
        self.idp.userinfo_claims_overrides = {"sub": "different-subject"}
        values, cookie = self.begin()
        self.assertEqual(self.post("/v1/workspaces/auth/callback", values, cookie=cookie)[0], 400)
        self.idp.userinfo_claims_overrides = None
        self.login()
        self.assertGreater(self.idp.userinfo_requests, 0)

    def test_concurrent_retry_and_transaction_rollback(self):
        claims = {"iss": self.idp.origin, "sub": "concurrent-account", "email": "new@example.com", "email_verified": True}
        def provision(_):
            flow, state, cookie = self.sessions.begin_login()
            flow = self.sessions.consume_callback(state, cookie)
            return self.sessions.complete_login(flow, claims)["slug"]
        with mock.patch.object(self.service.broker.directory, "_event", side_effect=WorkspaceError("workspace_storage_unavailable")):
            with self.assertRaises(WorkspaceError):
                provision(0)
        with self.sessions.store._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM onboarding_organizations").fetchone()[0], 0)
        with ThreadPoolExecutor(max_workers=4) as executor:
            slugs = list(executor.map(provision, range(8)))
        self.assertEqual(len(set(slugs)), 1)
        from hormuz.workspace_store import WorkspaceStore
        reopened = WorkspaceStore(self.service.broker)
        self.assertEqual(reopened.origin, self.gateway_url)
        self.assertEqual(provision(9), slugs[0])

    def test_removed_owner_cannot_recreate_or_sign_in(self):
        cookie, identity, _ = self.login()
        with self.sessions.store._connection() as connection:
            connection.execute("UPDATE onboarding_memberships SET status = 'disabled', authorization_version = authorization_version + 1")
        self.assertEqual(self.request("GET", "/v1/workspaces/me", headers={"Cookie": cookie})[0], 401)
        values, flow_cookie = self.begin()
        self.assertEqual(self.post("/v1/workspaces/auth/callback", values, cookie=flow_cookie)[0], 403)
        with self.sessions.store._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0], 1)

    def test_reopened_database_and_changed_origin_keep_the_same_workspace(self):
        cookie, identity, _ = self.login()
        from hormuz.session_store import SQLiteSessionStore
        from hormuz.session import SessionBroker
        from hormuz.auth import Authenticator
        from hormuz.workspace_store import WorkspaceStore
        new_origin = "https://next-dashboard.example.com"
        config = replace(self.service.broker.config, session_broker=replace(self.service.broker.config.session_broker, public_base_url=new_origin))
        settings = config.session_broker
        reopened = SQLiteSessionStore(settings.database_path, master_key=settings.master_key, audience=new_origin, access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300)
        sessions = WorkspaceStore(SessionBroker(config, Authenticator(config), reopened))
        flow, state, browser = sessions.begin_login()
        flow = sessions.consume_callback(state, browser)
        result = sessions.complete_login(flow, {"iss": self.idp.origin, "sub": "alice-subject", "email": "alice@example.com", "email_verified": True})
        self.assertEqual(result["slug"], identity["slug"])
        self.assertEqual(sessions.authenticate(result["credential"], new_origin)["workspace_id"], identity["workspace_id"])
        with reopened._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0], 1)
        with self.assertRaises(WorkspaceError):
            sessions.authenticate(cookie.split("=", 1)[1], new_origin)

    def test_domain_proof_gates_activation_and_duplicate_claims(self):
        cookie, identity, _ = self.login()
        credential = cookie.split("=", 1)[1]
        domain = self.service.domains.claim(credential, self.gateway_url, "AI.Customer.COM")
        self.fake.ownership = False
        self.service.domains.check(credential, self.gateway_url, domain["id"])
        self.assertEqual(self.fake.creates, 0)
        self.assertEqual(self.request("GET", "/workspace", headers={"Host": "ai.customer.com"})[0], 421)
        self.fake.ownership, self.fake.tls = True, False
        self.service.domains.check(credential, self.gateway_url, domain["id"])
        self.assertEqual(self.service.domains.list(credential, self.gateway_url)[0]["last_error"], "https_pending")
        other, _, _ = self.login("bob-subject", "bob@example.com")
        with self.assertRaisesRegex(WorkspaceError, "workspace_domain_claimed"):
            self.service.domains.claim(other.split("=", 1)[1], self.gateway_url, "ai.customer.com")

    def test_removing_an_unverified_claim_cannot_delete_an_external_provider_domain(self):
        cookie, _, _ = self.login()
        credential = cookie.split("=", 1)[1]
        domain = self.service.domains.claim(credential, self.gateway_url, "ai.customer.com")
        self.service.domains.remove(credential, self.gateway_url, domain["id"])
        self.assertEqual(self.fake.removals, 0)
        self.assertEqual(self.service.domains.list(credential, self.gateway_url), [])

    def test_custom_host_serves_dashboard_and_handoff_is_single_use_origin_bound(self):
        cookie, identity, path = self.login()
        self.activate_domain(cookie, identity)
        token, handoff_cookie = self.custom_login("ai.customer.com")
        self.assertEqual(self.post("/v1/workspaces/auth/handoff", {"token": token}, cookie=handoff_cookie, host="ai.customer.com", origin="https://attacker.com")[0], 403)
        self.assertEqual(self.post("/v1/workspaces/auth/handoff", {"token": token}, cookie="", host="ai.customer.com", origin=self.gateway_url)[0], 400)
        status, headers, _ = self.post("/v1/workspaces/auth/handoff", {"token": token}, cookie=handoff_cookie, host="ai.customer.com", origin=self.gateway_url)
        self.assertEqual(status, 303)
        custom_cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertIn("__Host-hormuz_workspace=", custom_cookie)
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", path, headers={"Host": "ai.customer.com", "Cookie": custom_cookie})[0], 200)
        self.assertEqual(self.request("GET", "/v1/workspaces/me", headers={"Cookie": "hormuz_workspace_local=" + custom_cookie.split("=", 1)[1]})[0], 401)
        self.assertEqual(self.post("/v1/workspaces/auth/handoff", {"token": token}, cookie=handoff_cookie, host="ai.customer.com", origin=self.gateway_url)[0], 400)
        self.assertEqual(self.request("GET", path, headers={"Cookie": cookie})[0], 200)

    def test_domain_removal_repair_reassignment_and_canonical_fallback(self):
        cookie, identity, path = self.login()
        domain = self.activate_domain(cookie, identity)
        token, handoff_cookie = self.custom_login("ai.customer.com")
        self.fake.available = False
        self.service.domains.remove(cookie.split("=", 1)[1], self.gateway_url, domain["id"])
        self.assertEqual(self.request("GET", "/workspace", headers={"Host": "ai.customer.com"})[0], 421)
        self.assertEqual(self.request("GET", path, headers={"Cookie": cookie})[0], 200)
        other, _, _ = self.login("bob-subject", "bob@example.com")
        with self.assertRaises(WorkspaceError):
            self.service.domains.claim(other.split("=", 1)[1], self.gateway_url, "ai.customer.com")
        self.fake.available = True
        self.service.domains._reconcile(domain["id"])
        fresh = self.service.domains.claim(other.split("=", 1)[1], self.gateway_url, "ai.customer.com")
        self.assertNotEqual(fresh["dns_records"][0]["value"], domain["dns_records"][0]["value"])
        self.assertEqual(self.post("/v1/workspaces/auth/handoff", {"token": token}, cookie=handoff_cookie, host="ai.customer.com", origin=self.gateway_url)[0], 421)

    def test_domain_expiry_and_dns_changes_fail_closed(self):
        cookie, identity, _ = self.login()
        domain = self.activate_domain(cookie, identity)
        with self.sessions.store._connection() as connection:
            connection.execute("UPDATE workspace_domains SET verified_until = ?", (_isoformat(self.sessions.store._now() - timedelta(seconds=1)),))
        self.assertEqual(self.request("GET", "/workspace", headers={"Host": "ai.customer.com"})[0], 421)
        self.fake.ownership = False
        self.service.domains.check(cookie.split("=", 1)[1], self.gateway_url, domain["id"])
        self.assertEqual(self.service.domains.list(cookie.split("=", 1)[1], self.gateway_url)[0]["status"], "pending")

    def test_transport_csrf_host_and_claimed_authority(self):
        cookie, identity, path = self.login()
        self.assertEqual(self.post("/v1/workspaces/domains", {"hostname": "ai.customer.com", "csrf_token": "forged"}, cookie=cookie)[0], 403)
        self.assertEqual(self.post("/v1/workspaces/domains", {"hostname": "ai.customer.com", "csrf_token": identity["csrf_token"]}, cookie=cookie, origin="https://attacker.com")[0], 403)
        for headers in ({"Host": "evil.example.com"}, {"X-Forwarded-Host": "evil.example.com"}, {"X-Hormuz-Organization": "other"}, {"Authorization": "Bearer forged"}):
            self.assertIn(self.request("GET", path, headers={"Cookie": cookie, **headers})[0], {400, 421})
        status, _, page = self.post("/v1/workspaces/domains", {"hostname": "ai.customer.com", "csrf_token": identity["csrf_token"]}, cookie=cookie)
        self.assertEqual(status, 200, page)
        self.assertIn("hormuz-verification=", page)
        self.assertEqual(self.post("/v1/workspaces/logout", {"csrf_token": identity["csrf_token"]}, cookie=cookie)[0], 303)
        self.assertEqual(self.request("GET", "/v1/workspaces/me", headers={"Cookie": cookie})[0], 401)

    def test_browser_cookie_cannot_be_used_for_inference(self):
        cookie, _, _ = self.login()
        self.assertEqual(self.request("POST", "/v1/responses", {"model": "safe-openai", "input": "hello"}, headers={"Cookie": cookie})[0], 401)
        self.assertEqual(self.idp.model_requests, 0)
