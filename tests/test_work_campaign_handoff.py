"""Actual anonymous campaign→existing sign-in→explicit consent, without tracking."""
import time
import unittest

from hormuz.work_runtime import WorkRuntimeError
from hormuz.work_workflow_http import HANDOFF_COOKIE, _decode_handoff, _encode_handoff
from tests import test_work_activation_http as activation_http
from tests import test_hosted_work as hosted_tests


class WorkCampaignHandoffHTTPTests(unittest.TestCase):
    setUp = hosted_tests.HostedWorkHTTPTests.setUp
    tearDown = hosted_tests.HostedWorkHTTPTests.tearDown
    request = hosted_tests.HostedWorkHTTPTests.request
    prepare = activation_http.WorkActivationHTTPTests.prepare

    def count_sources(self):
        with self.gateway.work_runtime._transaction() as connection:
            return connection.execute("SELECT COUNT(*) FROM ai_work_acquisition").fetchone()[0], connection.execute("SELECT COUNT(*) FROM ai_work_funnel").fetchone()[0]

    def start(self):
        status, headers, body = self.request("GET", "/work/acquisition?utm_source=fixture-ad&utm_campaign=work-launch")
        self.assertEqual(status, 200, body)
        self.assertIn(b'href="/console"', body)
        cookie = headers["Set-Cookie"]
        for flag in ("Path=/work", "Secure", "HttpOnly", "SameSite=Lax", "Max-Age=600"):
            self.assertIn(flag, cookie)
        self.assertEqual((0, 0), self.count_sources())
        return cookie.split(";", 1)[0]

    def test_anonymous_login_handoff_decline_progress_and_explicit_csrf_consent(self):
        handoff = self.start()
        self.prepare()  # Existing verified login fixture; no new OAuth authority.
        cookie = self.browser["Cookie"] + "; " + handoff
        status, headers, body = self.request("GET", "/work", headers={"Cookie": cookie})
        self.assertEqual(status, 200, body)
        self.assertIn(b'value="fixture-ad"', body)
        self.assertIn(b'Continue without source', body)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual((0, 0), self.count_sources())
        # A browser obeys deletion on the consent-page response. Declining then
        # opens the dashboard rather than repeating this acquisition form.
        status, _, dashboard = self.request("GET", "/work", headers={"Cookie": self.browser["Cookie"]})
        self.assertEqual(status, 200, dashboard)
        self.assertNotIn(b'Continue without source', dashboard)
        values = {"utm_source": "fixture-ad", "utm_campaign": "work-launch", "analytics_consent": True, "csrf_token": self.csrf}
        self.assertEqual(self.request("POST", "/v1/work/acquisition", body={**values, "csrf_token": "invalid"}, headers=self.browser)[0], 403)
        self.assertEqual((0, 0), self.count_sources())
        self.assertEqual(self.request("POST", "/v1/work/acquisition", body=values, headers=self.browser)[0], 201)
        self.assertEqual((1, 0), self.count_sources())

    def test_tampered_expired_and_duplicate_cookie_are_cleared_without_tracking(self):
        handoff = self.start()
        self.prepare()
        tampered = handoff[:-1] + ("0" if handoff[-1] != "0" else "1")
        for value in (tampered, handoff + "; " + handoff):
            with self.subTest(value="duplicate" if ";" in value else "tampered"):
                status, headers, _ = self.request("GET", "/work", headers={"Cookie": self.browser["Cookie"] + "; " + value})
                self.assertEqual(status, 400)
                self.assertIn("Max-Age=0", headers["Set-Cookie"])
        expired = HANDOFF_COOKIE + "=" + _encode_handoff({"utm_source": "fixture-ad"}, "gateway.example.test", self.gateway.config.session_broker.master_key, int(time.time()) - 601)
        status, headers, _ = self.request("GET", "/work", headers={"Cookie": self.browser["Cookie"] + "; " + expired})
        self.assertEqual(status, 400)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual((0, 0), self.count_sources())

    def test_invalid_campaign_before_auth_and_bearer_cookie_mixture_remain_rejected(self):
        for query in ("utm_source=one&utm_source=two", "utm_source=one&work_id=private-work", "utm_source=has%20spaces", "utm_source=" + "x"*65):
            status, headers, _ = self.request("GET", "/work/acquisition?" + query)
            self.assertEqual(status, 400, query)
            self.assertNotIn("Set-Cookie", headers)
        handoff = self.start()
        self.assertEqual(self.request("GET", "/work", headers={**self.token_headers, "Cookie": handoff})[0], 403)
        self.assertEqual((0, 0), self.count_sources())

    def test_signed_cookie_is_bound_to_exact_originating_host(self):
        key = self.gateway.config.session_broker.master_key
        token = _encode_handoff({"utm_source": "fixture-ad"}, "gateway.example.test", key, 100)
        self.assertEqual({"utm_source": "fixture-ad"}, _decode_handoff(token, "gateway.example.test", key, 101))
        with self.assertRaisesRegex(WorkRuntimeError, "work_handoff_invalid"):
            _decode_handoff(token, "other.example.test", key, 101)

    def test_direct_authenticated_campaign_form_also_clears_prior_handoff(self):
        handoff = self.start()
        self.prepare()
        status, headers, body = self.request("GET", "/work/acquisition?utm_source=direct-source", headers={"Cookie": self.browser["Cookie"] + "; " + handoff})
        self.assertEqual(status, 200, body)
        self.assertIn(b'value="direct-source"', body)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual((0, 0), self.count_sources())
