"""Provider-free desktop enrollment through the real HTTP and OIDC paths."""

from __future__ import annotations

from dataclasses import replace

from tests._session_fixtures import SessionHTTPTestCase


class DesktopEnrollmentTests(SessionHTTPTestCase):
    def desktop_enroll(self, client="codex"):
        secret = "desktop-enrollment-secret-" + "s" * 40
        status, _, enrollment = self.request("POST", "/v1/auth/desktop/enrollments", {
            "client": client, "enrollment_secret": secret,
        })
        self.assertEqual(status, 201, enrollment)
        return enrollment, secret

    def test_first_sign_in_profile_restore_and_replay(self):
        enrollment, secret = self.desktop_enroll()
        status, _, pending = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertEqual(status, 409, pending)
        values, cookie = self.begin_browser(enrollment)
        status, _, _ = self.callback(values, cookie)
        self.assertEqual(status, 200)
        status, _, reply = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertEqual(status, 200, reply)
        profile = reply["desktop_profile"]
        self.assertEqual(profile["schema_id"], "hormuz.desktop-profile")
        self.assertEqual(profile["gateway_origin"], self.gateway_url)
        self.assertEqual(profile["organization_id"], "org-a")
        self.assertEqual(profile["model_alias"], "safe-openai")
        self.assertEqual(profile["allowed_clients"], ["codex"])
        status, _, restored = self.request("GET", "/v1/auth/desktop/profile", headers={
            "Authorization": "Bearer " + reply["access_token"],
        })
        self.assertEqual((status, restored), (200, profile))
        status, _, response = self.request("POST", "/v1/responses", {
            "model": profile["model_alias"], "input": "local fixture only",
            "max_output_tokens": 16, "stream": False,
        }, {"Authorization": "Bearer " + reply["access_token"]})
        self.assertEqual(status, 200, response)
        status, _, usage = self.request("GET", "/v1/gateway/usage", headers={
            "Authorization": "Bearer " + reply["access_token"],
        })
        self.assertEqual(status, 200, usage)
        self.assertEqual(usage["requests"], 1)
        status, _, _ = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertNotEqual(status, 200)

    def test_identity_resolves_organization_after_browser_login(self):
        self.idp.subject = "bob-subject"
        enrollment, secret = self.desktop_enroll()
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        status, _, reply = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["desktop_profile"]["organization_id"], "org-b")

    def test_disallowed_client_does_not_create_usable_session(self):
        self.idp.subject = "bob-subject"
        enrollment, secret = self.desktop_enroll(client="claude-code")
        values, cookie = self.begin_browser(enrollment)
        self.assertNotEqual(self.callback(values, cookie)[0], 200)
        status, _, _ = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertNotEqual(status, 200)

    def test_profile_requires_session_bearer(self):
        self.assertEqual(self.request("GET", "/v1/auth/desktop/profile")[0], 401)

    def test_missing_default_revokes_new_session_before_responding(self):
        enrollment, secret = self.desktop_enroll()
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        self.config = replace(self.config, session_broker=replace(
            self.config.session_broker, desktop_defaults={"org-b": {"codex": "safe-openai"}},
        ))
        self.gateway.config = self.config
        self.gateway.session_broker.config = self.config
        status, _, response = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertEqual(status, 400, response)
        self.assertNotIn("access_token", response)
