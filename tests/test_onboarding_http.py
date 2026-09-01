from __future__ import annotations

import html
import json
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest import mock

from hormuz.credential_store import SecureCredentialStore
from hormuz.session_client import SessionClientError, access_token, login
from tests._session_fixtures import SessionHTTPTestCase
from tests.test_credential_store import MemoryBackend


class OnboardingHTTPTests(SessionHTTPTestCase):
    def configure_gateway(self, config):
        issuer = config.oidc_issuers[self.idp.origin]
        return replace(config, session_broker=replace(config.session_broker, onboarding_enabled=True),
                       oidc_issuers={self.idp.origin: replace(issuer, login=replace(issuer.login, scopes=("openid", "email")))})

    def setUp(self):
        super().setUp()
        self.directory = self.gateway.session_broker.directory
        self.directory.create_organization(organization_id="customer-a", name="Customer A", issuer=self.idp.origin)
        self.directory.create_team(organization_id="customer-a", team_id="customer-a-eng", name="Engineering")
        self.invitation = self.directory.invite(organization_id="customer-a", team_id="customer-a-eng", email="new@example.test",
                                                name="New Member", allowed_clients=("codex", "claude-code"))
        self.idp.subject = "new-subject"
        self.idp.claims_overrides = {"email": "new@example.test", "email_verified": True}

    def invitation_page(self, enrollment):
        url = urlsplit(enrollment["login_url"])
        status, headers, page = self.request("GET", url.path + "?" + url.query)
        self.assertEqual(status, 200)
        self.assertIn("form-action 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "strict-origin")
        self.assertIn("Path=/v1/auth;", headers["Set-Cookie"])
        self.assertIn('type="password"', page)
        state = html.unescape(re.search(r'name="state" value="([^"]+)"', page)[1])
        enrollment_id = html.unescape(re.search(r'name="enrollment" value="([^"]+)"', page)[1])
        return {"enrollment": enrollment_id, "state": state, "invitation_code": self.invitation.code}, headers["Set-Cookie"].split(";", 1)[0]

    def submit_invitation(self, form, cookie, *, origin=None, body=None, path="/v1/auth/invitations/accept"):
        return self.request("POST", path, urlencode(form) if body is None else body,
                            {"Cookie": cookie, "Origin": self.gateway_url if origin is None else origin,
                             "Content-Type": "application/x-www-form-urlencoded"})

    def accept_in_browser(self, enrollment):
        form, cookie = self.invitation_page(enrollment)
        status, headers, page = self.submit_invitation(form, cookie)
        self.assertEqual(status, 200, page)
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn('rel="noreferrer"', page)
        self.assertFalse(self.invitation.code in page)
        authorization = html.unescape(re.search(r'href="([^"]+)"', page)[1])
        parsed = urlsplit(authorization)
        claims_request = json.loads(parse_qs(parsed.query)["claims"][0])
        self.assertTrue(claims_request["id_token"]["email_verified"]["essential"])
        status, _, values = self.request("GET", parsed.path + "?" + parsed.query, origin=self.idp.origin)
        self.assertEqual(status, 200)
        return values, cookie

    def join_team(self):
        enrollment, secret = self.enroll(organization="customer-a")
        values, cookie = self.accept_in_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        status, _, pair = self.request("POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem", {"enrollment_secret": secret})
        self.assertEqual(status, 200)
        return pair

    def test_new_member_can_use_gateway_and_personal_stats_then_operator_removes_access(self):
        pair = self.join_team()
        self.assertEqual(self.idp.userinfo_requests, 0)
        headers = {"Authorization": "Bearer " + pair["access_token"]}
        status, _, identity = self.request("GET", "/v1/gateway/whoami", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual((identity["organization_id"], identity["actor_id"], identity["team_id"]), ("customer-a", self.invitation.membership_id, "customer-a-eng"))
        request = {"model": "safe-openai", "input": "synthetic local request", "max_output_tokens": 16, "stream": False}
        self.assertEqual(self.request("POST", "/v1/responses", request, headers)[0], 200)
        status, _, stats = self.request("GET", "/v1/gateway/usage", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(stats["requests"], 1)
        self.directory.disable_member(organization_id="customer-a", membership_id=self.invitation.membership_id)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=headers)[0], 401)
        self.assertEqual(self.request("GET", "/v1/gateway/usage", headers=headers)[0], 401)
        self.assertEqual(self.request("POST", "/v1/responses", request, headers)[0], 401)
        self.assertEqual(self.request("POST", "/v1/auth/refresh", {"refresh_token": pair["refresh_token"]})[0], 401)
        self.assertEqual(self.idp.model_requests, 1)
        enrollment, _ = self.enroll(organization="customer-a")
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 400)

    def test_code_flow_gets_omitted_email_scope_claims_from_userinfo(self):
        self.idp.omit_claims = {"email", "email_verified"}
        pair = self.join_team()
        self.assertEqual(self.idp.userinfo_requests, 1)
        status, _, identity = self.request(
            "GET", "/v1/gateway/whoami",
            headers={"Authorization": "Bearer " + pair["access_token"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(identity["actor_id"], self.invitation.membership_id)

    def test_userinfo_outage_fails_closed_without_authorizing_invitation(self):
        self.idp.omit_claims = {"email", "email_verified"}
        self.idp.userinfo_unavailable = True
        enrollment, secret = self.enroll(organization="customer-a")
        values, cookie = self.accept_in_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 503)
        self.assertEqual(self.idp.userinfo_requests, 1)
        self.assertEqual(self.request(
            "POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem",
            {"enrollment_secret": secret},
        )[0], 409)

    def test_userinfo_cannot_change_subject_or_override_an_id_token_claim(self):
        cases = (
            ({"email", "email_verified"}, {"sub": "different-subject"}),
            ({"email_verified"}, {"email": "wrong@example.test", "email_verified": True}),
            ({"email", "email_verified"}, {"email": "new@example.test", "email_verified": False}),
        )
        for omitted, userinfo in cases:
            with self.subTest(omitted=omitted, userinfo=userinfo):
                self.idp.omit_claims = omitted
                self.idp.userinfo_claims_overrides = userinfo
                enrollment, secret = self.enroll(organization="customer-a")
                values, cookie = self.accept_in_browser(enrollment)
                self.assertEqual(self.callback(values, cookie)[0], 400)
                self.assertEqual(self.request(
                    "POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem",
                    {"enrollment_secret": secret},
                )[0], 409)

    def test_unmapped_user_without_invite_cannot_self_enroll_and_legacy_user_still_works(self):
        enrollment, _ = self.enroll(organization="customer-a")
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 400)
        self.idp.subject = "alice-subject"
        pair = self.browser_login()
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair["access_token"]})[0], 200)

    def test_form_rejects_foreign_origin_cookie_state_extra_fields_and_query_secrets(self):
        enrollment, _ = self.enroll(organization="customer-a")
        form, cookie = self.invitation_page(enrollment)
        with self.assertLogs("hormuz.session", level="INFO") as log:
            for origin in ("https://untrusted.invalid", "null", ""):
                self.assertEqual(self.submit_invitation(form, cookie, origin=origin)[0], 400)
            self.assertEqual(self.submit_invitation(form, "hormuz_login_local=" + "x" * 43)[0], 400)
            self.assertEqual(self.submit_invitation(dict(form, state="x" * 43), cookie)[0], 400)
            self.assertEqual(self.submit_invitation(dict(form, team_id="forged", clearance="restricted"), cookie)[0], 400)
            self.assertEqual(self.submit_invitation(form, cookie, body=urlencode(form) + "&state=duplicate")[0], 400)
            self.assertEqual(self.submit_invitation(form, cookie, path="/v1/auth/invitations/accept?invitation_code=" + self.invitation.code)[0], 400)
        self.assertFalse(self.invitation.code in "\n".join(log.output))
        status, _, page = self.submit_invitation(form, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.submit_invitation(form, cookie)[0], 400)
        self.assertFalse(self.invitation.code in page)

    def test_signed_token_still_requires_email_nonce_audience_and_exact_issuer(self):
        for overrides in (
            {"email": "wrong@example.test"}, {"email_verified": "true"}, {"email_verified": False},
            {"aud": "hormuz-api"}, {"nonce": "wrong"}, {"iss": "https://wrong.invalid"},
            {"exp": 1}, {"email": None},
        ):
            with self.subTest(overrides=overrides):
                self.idp.claims_overrides = {"email": "new@example.test", "email_verified": True, **overrides}
                enrollment, secret = self.enroll(organization="customer-a")
                values, cookie = self.accept_in_browser(enrollment)
                self.assertEqual(self.callback(values, cookie)[0], 400)
                self.assertEqual(self.request("POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem", {"enrollment_secret": secret})[0], 409)
        self.assertEqual(self.directory.list_records("memberships", organization_id="customer-a")["items"][0]["status"], "pending")

    def test_disabling_between_callback_and_redemption_denies_credential(self):
        enrollment, secret = self.enroll(organization="customer-a")
        values, cookie = self.accept_in_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        self.directory.disable_member(organization_id="customer-a", membership_id=self.invitation.membership_id)
        self.assertEqual(self.request("POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem", {"enrollment_secret": secret})[0], 409)

    def test_disabling_onboarding_denies_existing_managed_session_and_invite(self):
        pair = self.join_team()
        broker = self.gateway.session_broker
        broker.config = replace(broker.config, session_broker=replace(broker.config.session_broker, onboarding_enabled=False))
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair["access_token"]})[0], 401)
        status, _, _ = self.request("POST", "/v1/auth/enrollments", {"client": "codex", "enrollment_secret": "s" * 43, "organization_id": "customer-a"})
        self.assertEqual(status, 400)

    def test_configured_credential_cannot_bypass_a_managed_organization(self):
        from hormuz.auth import Authenticator
        # Defense against a second process introducing a conflicting mapping
        # after this gateway started. The directory stays authoritative at use.
        configured = next(iter(self.config.identities_by_subject.values()))
        token = "synthetic-direct-configured-token"
        identity = replace(configured, organization_id="customer-a", actor_id=self.invitation.membership_id, token=token)
        self.gateway.authenticator = Authenticator(replace(self.config, identities_by_token={token: identity}))
        headers = {"Authorization": "Bearer " + token}
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=headers)[0], 401)
        self.assertEqual(self.request("GET", "/v1/gateway/usage", headers=headers)[0], 401)
        self.assertEqual(self.idp.model_requests, 0)

    def test_client_helper_protocol_needs_no_changes_for_invitation_login_and_removal(self):
        backend = MemoryBackend()
        secure_store = SecureCredentialStore(backend, trust_injected_backend=True)
        def browser(url):
            values, cookie = self.accept_in_browser({"login_url": url})
            self.assertEqual(self.callback(values, cookie)[0], 200)
            return True
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.root)}):
            login(gateway=self.gateway_url, profile="onboarding-test", client="codex", issuer=None, organization="customer-a",
                  no_open=False, allow_insecure_http=True, wait_seconds=5, store=secure_store, browser_open=browser)
            self.assertIsNotNone(secure_store.get("onboarding-test"))
            token = access_token(gateway=self.gateway_url, profile="onboarding-test", allow_insecure_http=True,
                                 force_refresh=True, store=secure_store)
            self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + token})[0], 200)
            self.directory.disable_member(organization_id="customer-a", membership_id=self.invitation.membership_id)
            with self.assertRaises(SessionClientError):
                access_token(gateway=self.gateway_url, profile="onboarding-test", allow_insecure_http=True,
                             force_refresh=True, store=secure_store)

    def test_invitation_and_idp_credentials_are_excluded_from_logs_and_usage(self):
        self.idp.omit_claims = {"email", "email_verified"}
        with self.assertLogs("hormuz.session", level="INFO") as log:
            self.join_team()
            self.request("GET", "/v1/auth/invitations/accept?code=" + self.invitation.code)
        self.assertEqual(self.idp.userinfo_requests, 1)
        data = "\n".join(log.output).encode()
        for pattern in ("sessions.sqlite3*", "usage.sqlite3*"):
            data += b"".join(path.read_bytes() for path in self.root.glob(pattern))
        for value in (self.invitation.code, self.idp.last_id_token, "idp-token-must-not-be-stored", "new@example.test"):
            self.assertFalse(value.encode() in data, "invitation, email or ID token leaked")
