"""Provider-free desktop enrollment through the real HTTP and OIDC paths."""

from __future__ import annotations

from dataclasses import replace
from unittest import mock

from hormuz.policy_document import local_policy_snapshot
from hormuz.postgres import PostgresStorageError
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

    def test_static_identity_still_works_with_onboarding_enabled_and_no_managed_team(self):
        self.config = replace(self.config, session_broker=replace(
            self.config.session_broker, onboarding_enabled=True,
        ))
        self.gateway.config = self.config
        self.gateway.session_broker.config = self.config
        self.idp.subject = "bob-subject"
        enrollment, secret = self.desktop_enroll()
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        status, _, reply = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["desktop_profile"]["organization_id"], "org-b")

    def test_profile_tracks_active_runtime_policy_and_fails_closed_when_unavailable(self):
        enrollment, secret = self.desktop_enroll()
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        status, _, reply = self.request("POST", "/v1/auth/desktop/enrollments/" + enrollment["enrollment_id"] + "/redeem", {
            "enrollment_secret": secret,
        })
        self.assertEqual(status, 200, reply)
        headers = {"Authorization": "Bearer " + reply["access_token"]}
        identity = self.gateway.session_broker.authenticate(reply["access_token"])
        snapshot = local_policy_snapshot(self.config, identity)
        runtime = mock.Mock()
        runtime.snapshot_for.return_value = replace(snapshot, policy_version="new-active-version")
        self.gateway.session_broker.policy_runtime = runtime
        status, _, updated = self.request("GET", "/v1/auth/desktop/profile", headers=headers)
        self.assertEqual(status, 200, updated)
        self.assertNotEqual(updated["profile_version"], reply["desktop_profile"]["profile_version"])
        runtime.snapshot_for.return_value = replace(
            snapshot, effective_policy=replace(snapshot.effective_policy, allowed_models=("safe-claude",)),
        )
        self.assertEqual(self.request("GET", "/v1/auth/desktop/profile", headers=headers)[0], 400)
        runtime.snapshot_for.side_effect = PostgresStorageError("policy_store_unavailable")
        self.assertEqual(self.request("GET", "/v1/auth/desktop/profile", headers=headers)[0], 503)

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
