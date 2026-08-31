"""Local-only console fixtures. All identities, keys, claims and calls are synthetic."""

from __future__ import annotations

import html
import re
import secrets
from dataclasses import replace
from urllib.parse import urlencode, urlsplit

from tests._session_fixtures import SessionHTTPTestCase


def activate_member(store, directory, *, subject="admin-subject", email="admin@example.test", name="Ada Admin",
                    organization="customer-a", team="customer-a-eng", invitation=None):
    if invitation is None:
        invitation = directory.invite(organization_id=organization, team_id=team, email=email,
                                      name=name, allowed_clients=("codex", "claude-code"))
    with store._connection() as connection:
        issuer = directory._organization(connection, organization)["issuer"]
    secret, state, cookie = (secrets.token_urlsafe(32) for _ in range(3))
    enrollment = store.create_enrollment(issuer=issuer, client_name="codex", enrollment_secret=secret, organization_id=organization)
    store.begin_authorization(enrollment_id=enrollment.enrollment_id, state=state, browser_cookie=cookie,
                              nonce=secrets.token_urlsafe(32), pkce_verifier=secrets.token_urlsafe(64))
    directory.attach_invitation(enrollment_id=enrollment.enrollment_id, state=state, browser_cookie=cookie, code=invitation.code)
    flow = store.consume_callback(state=state, browser_cookie=cookie)
    directory.authorize_enrollment(flow=flow, claims={"iss": issuer, "sub": subject, "email": email, "email_verified": True})
    return invitation, store.redeem_enrollment(enrollment_id=enrollment.enrollment_id, enrollment_secret=secret)


class ConsoleHTTPTestCase(SessionHTTPTestCase):
    def configure_gateway(self, config):
        issuer = config.oidc_issuers[self.idp.origin]
        return replace(config, session_broker=replace(config.session_broker, onboarding_enabled=True, console_enabled=True),
                       oidc_issuers={self.idp.origin: replace(issuer, login=replace(issuer.login, scopes=("openid", "email")))})

    def setUp(self):
        super().setUp()
        self.directory = self.gateway.session_broker.directory
        self.sessions = self.gateway.console.sessions
        self.store = self.gateway.session_broker.store
        for organization, team in (("customer-a", "customer-a-eng"), ("customer-b", "customer-b-eng")):
            self.directory.create_organization(organization_id=organization, name=organization.title(), issuer=self.idp.origin)
            self.directory.create_team(organization_id=organization, team_id=team, name="Engineering")
        self.admin, self.native = activate_member(self.store, self.directory)
        self.member, self.member_native = activate_member(self.store, self.directory, subject="member-subject", email="member@example.test", name="Mina Member")
        self.sessions.grant(organization_id="customer-a", membership_id=self.admin.membership_id, role="member_admin")
        self.idp.subject = "admin-subject"

    def begin_console(self, *, organization="customer-a"):
        status, headers, page = self.request("POST", "/v1/admin/auth/start", urlencode({"organization_id": organization}),
                                              {"Origin": self.gateway_url, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200, page)
        parsed = urlsplit(html.unescape(re.search(r'<a class="button" href="([^"]+)"', page)[1]))
        status, _, values = self.request("GET", parsed.path + "?" + parsed.query, origin=self.idp.origin)
        self.assertEqual(status, 200, values)
        return values, headers["Set-Cookie"].split(";", 1)[0]

    def console_callback(self, values, cookie):
        return self.request("POST", "/v1/admin/auth/callback", urlencode(values),
                            {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})

    def login_console(self):
        values, flow_cookie = self.begin_console()
        status, headers, body = self.console_callback(values, flow_cookie)
        self.assertEqual(status, 303, body)
        self.assertEqual(headers["Location"], "/console")
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, identity = self.request("GET", "/v1/admin/me", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, identity)
        self.csrf = identity["csrf_token"]
        return identity

    def remove_member(self, *, member=None, version=1, csrf=None, headers=None):
        return self.request("POST", "/v1/admin/members/disable",
                            {"membership_id": member or self.member.membership_id, "expected_version": version,
                             "csrf_token": self.csrf if csrf is None else csrf},
                            {"Origin": self.gateway_url, "Cookie": self.cookie, **(headers or {})})
