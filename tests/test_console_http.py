from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from unittest import mock
from urllib.parse import urlencode

from hormuz import console_http
from tests._console_fixtures import ConsoleHTTPTestCase, activate_member


class ConsoleHTTPTests(ConsoleHTTPTestCase):
    def test_report_totals_use_inclusive_utc_window_and_exact_organization_team_scope(self):
        self.login_console()
        identity = self.gateway.session_broker.authenticate(self.native.access_token)
        start_day = self.store._now().date() - timedelta(days=2)
        end_day = start_day + timedelta(days=1)
        start = datetime.combine(start_day, time.min, timezone.utc)
        end = datetime.combine(end_day + timedelta(days=1), time.min, timezone.utc)
        rows = (
            (start - timedelta(microseconds=1), identity, 900, 900, 9_000_000, "succeeded"),
            (start, identity, 11, 7, 50_000, "succeeded"),
            (end - timedelta(microseconds=1), identity, 5, 3, 70_000, "succeeded"),
            (end, identity, 900, 900, 9_000_000, "succeeded"),
            (start, replace(identity, organization_id="customer-b", team_id="customer-b-eng"), 800, 800, 8_000_000, "succeeded"),
            (start, replace(identity, team_id="customer-a-other"), 0, 0, 0, "denied"),
            (start, replace(identity, team_id="customer-a-other"), 0, 0, 0, "rate_limited"),
        )
        for occurred_at, actor, input_tokens, output_tokens, cost, status in rows:
            with mock.patch("hormuz.store.datetime", wraps=datetime) as clock:
                clock.now.return_value = occurred_at
                self.gateway.store.record(identity=actor, client="codex", protocol="openai", requested_model="fixture-model",
                                          resolved_alias="fixture-model", upstream_model="fixture-model", policy_action="allowed" if status == "succeeded" else "denied",
                                          status=status, input_tokens=input_tokens, output_tokens=output_tokens, cost_microusd=cost)
        query = urlencode({"from_date": start_day.isoformat(), "through_date": end_day.isoformat()})
        status, _, report = self.request("GET", "/v1/admin/usage?" + query, headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, report)
        self.assertEqual(report["totals"]["requests"], 4)
        self.assertEqual(report["totals"]["total_tokens"], 26)
        self.assertEqual(report["totals"]["cost_microusd"], 120_000)
        self.assertEqual(report["totals"]["denied_requests"], 1)
        self.assertEqual(report["totals"]["rate_limited_requests"], 1)
        self.assertEqual(report["cost_basis"], "configured_rate_card_estimate")
        status, _, team = self.request("GET", "/v1/admin/usage?" + query + "&team_id=customer-a-eng", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.assertEqual(team["totals"]["requests"], 2)
        self.assertEqual(team["totals"]["denied_requests"], 0)

    def test_date_window_is_closed_bounded_and_has_no_future_end(self):
        self.login_console()
        today = self.store._now().date()
        for values in ({"from_date": today.isoformat()}, {"through_date": today.isoformat()},
                       {"from_date": (today - timedelta(days=31)).isoformat(), "through_date": today.isoformat()},
                       {"from_date": today.isoformat(), "through_date": (today + timedelta(days=1)).isoformat()},
                       {"from_date": today.isoformat(), "through_date": (today - timedelta(days=1)).isoformat()},
                       {"from_date": "2026-02-30", "through_date": "2026-03-01"},
                       {"from_date": "20260801", "through_date": "20260802"}):
            with self.subTest(values=values):
                status, _, result = self.request("GET", "/v1/admin/usage?" + urlencode(values), headers={"Cookie": self.cookie})
                self.assertEqual(status, 400)
                self.assertEqual(result["error"]["code"], "admin_invalid_window")

    def test_separate_browser_login_uses_idp_and_renders_only_current_organization(self):
        identity = self.login_console()
        self.assertEqual((identity["schema_id"], identity["schema_version"]), ("hormuz.admin-identity", 1))
        self.assertEqual(identity["organization_id"], "customer-a")
        self.assertEqual(identity["role"], "member_admin")
        status, headers, page = self.request("GET", "/console", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, page)
        self.assertIn("Your team's AI usage", page)
        self.assertIn("Mina Member", page)
        self.assertNotIn("customer-b", page)
        self.assertIn("No gateway requests", page)
        self.assertIn("form-action 'self'", headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        status, headers, css = self.request("GET", "/console/styles.css")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/css; charset=utf-8")
        self.assertIn("@media(max-width:600px)", css)
        self.assertEqual(self.idp.model_requests, 0)

    def test_console_is_opt_in_and_html_sign_in_accepts_no_claimed_role(self):
        self.assertEqual(self.request("GET", "/console")[0], 200)
        self.assertEqual(self.request("GET", "/v1/admin/me")[0], 401)
        self.assertEqual(self.request("POST", "/v1/admin/auth/start", urlencode({"organization_id": "customer-a", "role": "member_admin"}),
                                      {"Origin": self.gateway_url, "Content-Type": "application/x-www-form-urlencoded"})[0], 400)
        self.gateway.config = replace(self.config, session_broker=replace(self.config.session_broker, console_enabled=False))
        self.assertEqual(self.request("GET", "/console")[0], 404)
        self.assertEqual(self.request("GET", "/v1/admin/me")[0], 404)
        self.assertEqual(self.request("GET", "/console/styles.css")[0], 404)

    def test_bearer_and_console_credentials_do_not_cross_security_boundaries(self):
        self.login_console()
        for path in ("/console", "/v1/admin/me", "/v1/admin/members", "/v1/admin/usage"):
            self.assertEqual(self.request("GET", path, headers={"Authorization": "Bearer " + self.native.access_token, "Cookie": self.cookie})[0], 400)
        credential = self.cookie.split("=", 1)[1]
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + credential})[0], 401)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Cookie": self.cookie})[0], 401)
        self.assertEqual(self.request("POST", "/v1/admin/grants", {}, {"Cookie": self.cookie, "Origin": self.gateway_url})[0], 404)

    def test_viewer_can_report_but_cannot_read_members_or_remove_them(self):
        self.sessions.grant(organization_id="customer-a", membership_id=self.admin.membership_id, role="report_viewer")
        self.login_console()
        headers = {"Cookie": self.cookie}
        self.assertEqual(self.request("GET", "/v1/admin/usage", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/v1/admin/teams", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/v1/admin/members", headers=headers)[0], 403)
        self.assertEqual(self.remove_member()[0], 403)
        status, _, page = self.request("GET", "/console", headers=headers)
        self.assertEqual(status, 200)
        self.assertNotIn("Mina Member", page)
        self.assertNotIn("Confirm removal", page)

    def test_csrf_foreign_origin_and_stale_target_cannot_remove_member(self):
        self.login_console()
        for origin in ("https://foreign.invalid", "null", ""):
            self.assertEqual(self.remove_member(headers={"Origin": origin})[0], 403)
        for token in ("", "forged", "é", self.sessions.csrf_token("different-session")):
            self.assertEqual(self.remove_member(csrf=token)[0], 403)
        self.assertEqual(self.remove_member(version=2)[0], 409)
        self.assertEqual(self.remove_member(member=self.admin.membership_id)[0], 409)
        for version in (True, 1.5, "1e0", "1" * 1000, -1):
            self.assertEqual(self.remove_member(version=version)[0], 400)
        self.assertEqual(self.store.authenticate_access(self.member_native.access_token).membership_id, self.member.membership_id)

    def test_removal_revokes_access_and_rechecks_acting_admin_after_initial_auth(self):
        self.login_console()
        original = self.sessions.disable_member
        def revoked_between_checks(*args, **kwargs):
            self.sessions.revoke(organization_id="customer-a", membership_id=self.admin.membership_id)
            return original(*args, **kwargs)
        with mock.patch.object(self.sessions, "disable_member", side_effect=revoked_between_checks):
            self.assertEqual(self.remove_member()[0], 401)
        self.store.authenticate_access(self.member_native.access_token)
        self.sessions.grant(organization_id="customer-a", membership_id=self.admin.membership_id, role="member_admin")
        self.login_console()
        self.assertEqual(self.remove_member()[0], 200)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + self.member_native.access_token})[0], 401)
        status, _, result = self.remove_member()
        self.assertEqual(status, 200)
        self.assertFalse(result["changed"])

    def test_usage_and_member_selection_are_scoped_even_with_other_valid_identifiers(self):
        other, _ = activate_member(self.store, self.directory, organization="customer-b", team="customer-b-eng",
                                   subject="other-subject", email="other@example.test", name="Other customer")
        self.login_console()
        headers = {"Cookie": self.cookie}
        self.assertEqual(self.request("GET", "/v1/admin/usage?team_id=customer-b-eng", headers=headers)[0], 404)
        self.assertEqual(self.request("GET", "/v1/admin/usage?organization_id=customer-b", headers=headers)[0], 400)
        self.assertEqual(self.request("GET", "/v1/admin/members", headers={**headers, "X-Hormuz-Organization-Id": "customer-b"})[0], 400)
        self.assertEqual(self.remove_member(member=other.membership_id)[0], 404)
        _, _, page = self.request("GET", "/v1/admin/members?limit=1", headers=headers)
        first = page["items"][0]["id"]
        self.assertEqual(len(page["items"]), 1)
        _, _, second = self.request("GET", "/v1/admin/members?" + urlencode({"limit": "1", "after": page["next_cursor"]}), headers=headers)
        self.assertNotEqual(second["items"][0]["id"], first)
        self.assertIsNone(second["next_cursor"])
        self.assertNotIn("Other customer", json.dumps([page, second]))
        for value in ("0", "101", "true", "-1", "1&limit=2"):
            self.assertEqual(self.request("GET", "/v1/admin/members?limit=" + value, headers=headers)[0], 400)

    def test_oidc_validation_and_no_grant_deny_login_without_minting_session(self):
        for overrides in ({"aud": "hormuz-api"}, {"nonce": "wrong"}, {"iss": "https://wrong.invalid"},
                          {"exp": 1}, {"sub": "member-subject"}, {"sub": "unknown", "role": "member_admin"}):
            self.idp.claims_overrides = overrides
            values, cookie = self.begin_console()
            self.assertIn(self.console_callback(values, cookie)[0], (400, 403))
        with self.store._connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM console_sessions").fetchone()[0], 0)
        self.idp.claims_overrides = {"email": "changed@example.test", "email_verified": False}
        self.login_console()  # Stable, verified issuer/subject is the authority.

    def test_callback_state_and_cookie_single_use_and_no_query_codes(self):
        values, cookie = self.begin_console()
        self.assertEqual(self.console_callback(values, "hormuz_console_flow_local=" + "x" * 43)[0], 400)
        self.assertEqual(self.request("POST", "/v1/admin/auth/callback?code=must-not-be-used", urlencode(values),
                                      {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})[0], 400)
        self.assertEqual(self.console_callback(values, cookie)[0], 303)
        self.assertEqual(self.console_callback(values, cookie)[0], 400)
        self.assertEqual(self.idp.model_requests, 0)

    def test_logout_only_revokes_console_and_new_login_invalidates_old_csrf(self):
        self.login_console()
        old_csrf, old_cookie = self.csrf, self.cookie
        self.login_console()
        self.assertEqual(self.remove_member(csrf=old_csrf)[0], 403)
        self.assertEqual(self.request("GET", "/v1/admin/me", headers={"Cookie": old_cookie})[0], 401)
        status, headers, value = self.request("POST", "/v1/admin/logout", {"csrf_token": self.csrf},
                                             {"Cookie": self.cookie, "Origin": self.gateway_url})
        self.assertEqual(status, 200)
        self.assertTrue(value["revoked"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/v1/admin/me", headers={"Cookie": self.cookie})[0], 401)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + self.native.access_token})[0], 200)

    def test_ambiguous_cookie_host_fields_and_cross_origin_reads_fail_closed(self):
        self.login_console()
        for headers in ({"Cookie": self.cookie + "; " + self.cookie}, {"Cookie": "x=" + "z" * 8192},
                        {"Cookie": self.cookie, "Host": "wrong.invalid"}, {"Cookie": self.cookie, "Origin": "https://foreign.invalid"}):
            self.assertIn(self.request("GET", "/v1/admin/me", headers=headers)[0], (400, 403))
        for suffix in ("?from_date=2026-08-01&from_date=2026-08-02", "?token=never-used", "?after=" + "a" * 2050):
            self.assertEqual(self.request("GET", "/v1/admin/usage" + suffix, headers={"Cookie": self.cookie})[0], 400)
        body = json.dumps({"membership_id": self.member.membership_id, "expected_version": 1, "csrf_token": self.csrf})[:-1] + ',"expected_version":1}'
        self.assertEqual(self.request("POST", "/v1/admin/members/disable", body,
                                      {"Cookie": self.cookie, "Origin": self.gateway_url, "Content-Type": "application/json"})[0], 400)

    def test_https_cookie_flags_keep_flow_separate_and_session_host_only(self):
        self.gateway.config = replace(self.config, session_broker=replace(self.config.session_broker, public_base_url="https://gateway.example"))
        handler = mock.Mock(server=self.gateway)
        session = console_http._cookie_header(handler, "session", "hox_c_" + "x" * 43)
        flow = console_http._cookie_header(handler, "flow", "hox_cf_" + "x" * 43)
        for cookie in (session, flow):
            self.assertTrue(cookie.startswith("__Host-hormuz_console"))
            self.assertIn("Path=/; HttpOnly;", cookie)
            self.assertIn("; Secure", cookie)
            self.assertNotIn("Domain=", cookie)
        self.assertIn("SameSite=Lax", session)
        self.assertIn("SameSite=None", flow)

    def test_member_names_are_escaped_and_storage_errors_are_content_free(self):
        with self.store._connection() as connection:
            connection.execute("UPDATE onboarding_memberships SET name = ? WHERE id = ?",
                               ('<script>alert("private")</script>', self.member.membership_id))
        self.login_console()
        status, _, page = self.request("GET", "/console", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        with mock.patch.object(self.gateway.store, "monthly_totals", side_effect=sqlite3.OperationalError("private query and credential")):
            status, _, value = self.request("GET", "/v1/admin/usage", headers={"Cookie": self.cookie})
        self.assertEqual(status, 503)
        self.assertNotIn("private query", json.dumps(value))
        self.assertEqual(value["error"]["code"], "admin_storage_unavailable")

    def test_disabled_member_immediately_loses_console_and_new_login_cannot_restore_grant(self):
        self.login_console()
        self.directory.disable_member(organization_id="customer-a", membership_id=self.admin.membership_id)
        self.assertEqual(self.request("GET", "/v1/admin/me", headers={"Cookie": self.cookie})[0], 401)
        status, headers, page = self.request("GET", "/console", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200)
        self.assertIn("Sign in again", page)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        values, flow_cookie = self.begin_console()
        self.assertEqual(self.console_callback(values, flow_cookie)[0], 403)
