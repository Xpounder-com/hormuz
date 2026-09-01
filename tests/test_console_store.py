from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from hormuz.console_store import ConsoleStore
from hormuz.onboarding import TeamDirectory
from hormuz.session_store import SessionStoreError
from tests import test_onboarding_store as onboarding_fixture
from tests._console_fixtures import activate_member


class ConsoleStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = onboarding_fixture.OnboardingStoreTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.store, self.directory = self.fixture.store, self.fixture.directory
        self.console = ConsoleStore(self.store, self.directory)
        self.clock = self.fixture.clock
        self.admin, self.native = activate_member(self.store, self.directory)
        self.member, self.member_native = activate_member(self.store, self.directory, subject="member-subject", email="member@example.test")
        self.grant()

    def grant(self, role="member_admin"):
        return self.console.grant(organization_id="customer-a", membership_id=self.admin.membership_id, role=role)

    def begin(self):
        state, cookie, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
        flow = self.console.begin_login(organization_id="customer-a", state=state, browser_cookie=cookie, nonce=nonce, pkce_verifier=verifier)
        return flow, state, cookie

    def login(self):
        _, state, cookie = self.begin()
        flow = self.console.consume_callback(state=state, browser_cookie=cookie)
        return self.console.complete_login(flow, {"iss": self.fixture.issuer, "sub": "admin-subject"})

    def test_grants_require_active_verified_members_and_cannot_cross_organization(self):
        invitation = self.fixture.invite(email="pending@example.test")
        for organization, member, role, code in (
            ("customer-a", invitation.membership_id, "member_admin", "admin_member_unavailable"),
            ("other-org", self.admin.membership_id, "member_admin", "onboarding_membership_unavailable"),
            ("customer-a", self.admin.membership_id, "root", "admin_invalid_role"),
        ):
            with self.subTest(role=role, code=code), self.assertRaisesRegex(SessionStoreError, code):
                self.console.grant(organization_id=organization, membership_id=member, role=role)
        self.assertFalse(self.grant()["changed"])
        page = self.console.list_grants(organization_id="customer-a", limit=1)
        self.assertEqual(page["items"][0]["authorization_version"], 1)
        self.assertIsNone(page["next_cursor"])
        for limit in (0, 101, True):
            with self.assertRaises(SessionStoreError):
                self.console.list_grants(organization_id="customer-a", limit=limit)

    def test_flow_requires_bound_cookie_single_consumption_and_verified_subject(self):
        _, state, cookie = self.begin()
        with self.assertRaisesRegex(SessionStoreError, "admin_login_invalid"):
            self.console.consume_callback(state=state, browser_cookie="wrong-" + "x" * 43)
        flow = self.console.consume_callback(state=state, browser_cookie=cookie)
        with self.assertRaisesRegex(SessionStoreError, "admin_login_invalid"):
            self.console.consume_callback(state=state, browser_cookie=cookie)
        for claims in ({"iss": "https://wrong.invalid", "sub": "admin-subject"},
                       {"iss": self.fixture.issuer, "sub": "member-subject", "email": "admin@example.test", "role": "member_admin"}):
            with self.assertRaises(SessionStoreError):
                self.console.complete_login(flow, claims)
        credential = self.console.complete_login(flow, {"iss": self.fixture.issuer, "sub": "admin-subject"})
        self.assertEqual(self.console.authenticate(credential).membership_id, self.admin.membership_id)
        with self.assertRaisesRegex(SessionStoreError, "admin_login_invalid"):
            self.console.complete_login(flow, {"iss": self.fixture.issuer, "sub": "admin-subject"})

    def test_session_flow_and_csrf_material_never_persist_as_plaintext(self):
        flow, state, cookie = self.begin()
        with self.store._connection() as connection:
            self.assertIsNotNone(connection.execute("SELECT encrypted_flow FROM console_login_flows").fetchone()[0])
        self.console.consume_callback(state=state, browser_cookie=cookie)
        credential = self.console.complete_login(flow, {"iss": self.fixture.issuer, "sub": "admin-subject"})
        csrf = self.console.csrf_token(credential)
        for path in self.fixture.root.glob("sessions.sqlite3*"):
            raw = path.read_bytes()
            for secret in (flow.nonce, flow.pkce_verifier, cookie, state, credential, csrf):
                self.assertNotIn(secret.encode(), raw, "console material retained as plaintext")
        with self.store._connection() as connection:
            self.assertEqual(tuple(connection.execute("SELECT state_hash, browser_cookie_hash, encrypted_flow FROM console_login_flows").fetchone()), (None, None, None))
        self.assertNotIn(flow.nonce, repr(flow))
        self.console.require_csrf(credential, csrf)
        for value in ("", "é", self.console.csrf_token("other-session")):
            with self.assertRaisesRegex(SessionStoreError, "admin_csrf_rejected"):
                self.console.require_csrf(credential, value)

    def test_console_cookie_flow_and_csrf_values_are_redacted_before_egress(self):
        from hormuz.config import SecretControls
        from hormuz.redaction import REPLACEMENT, SecretRedactor
        redactor = SecretRedactor(SecretControls())
        for prefix in ("hox_c_", "hox_cf_", "hox_cs_"):
            result = redactor.inspect({"input": prefix + "x" * 43})
            self.assertEqual(result.value["input"], REPLACEMENT)
            self.assertEqual(result.rules, ("hormuz_console_credential",))

    def test_native_credentials_cannot_authenticate_console_and_console_cannot_call_gateway(self):
        credential = self.login()
        for value in (self.native.access_token, self.native.refresh_token):
            with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
                self.console.authenticate(value)
        with self.assertRaises(SessionStoreError):
            self.store.authenticate_access(credential)
        self.console.logout(credential)
        self.assertEqual(self.store.authenticate_access(self.native.access_token).membership_id, self.admin.membership_id)
        with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
            self.console.authenticate(credential)

    def test_idle_absolute_and_flow_expiry_are_enforced(self):
        credential = self.login()
        self.clock.advance(600)
        with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
            self.console.authenticate(credential)
        credential = self.login()
        for _ in range(6):
            self.clock.advance(540)
            self.console.authenticate(credential)
        self.clock.advance(360)
        with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
            self.console.authenticate(credential)
        _, state, cookie = self.begin()
        self.clock.advance(300)
        with self.assertRaisesRegex(SessionStoreError, "admin_login_invalid"):
            self.console.consume_callback(state=state, browser_cookie=cookie)

    def test_new_login_and_role_change_revoke_console_but_not_native_sessions(self):
        first = self.login()
        second = self.login()
        with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
            self.console.authenticate(first)
        self.console.authenticate(second)
        self.grant("report_viewer")
        with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
            self.console.authenticate(second)
        viewer = self.login()
        self.assertEqual(self.console.authenticate(viewer).role, "report_viewer")
        with self.assertRaisesRegex(SessionStoreError, "admin_access_denied"):
            self.console.disable_member(viewer, membership_id=self.member.membership_id, expected_version=1)
        self.store.authenticate_access(self.native.access_token)

    def test_operator_revocation_cancels_pending_and_exchanging_flows(self):
        credential = self.login()
        _, state, cookie = self.begin()
        flow = self.console.consume_callback(state=state, browser_cookie=cookie)
        _, pending_state, pending_cookie = self.begin()
        self.assertTrue(self.console.revoke(organization_id="customer-a", membership_id=self.admin.membership_id)["changed"])
        self.grant()
        with self.assertRaisesRegex(SessionStoreError, "admin_login_invalid"):
            self.console.complete_login(flow, {"iss": self.fixture.issuer, "sub": "admin-subject"})
        with self.assertRaisesRegex(SessionStoreError, "admin_login_invalid"):
            self.console.consume_callback(state=pending_state, browser_cookie=pending_cookie)
        with self.assertRaisesRegex(SessionStoreError, "admin_session_required"):
            self.console.authenticate(credential)

    def test_member_removal_revokes_both_session_types_and_never_regrants_on_reinvite(self):
        credential = self.login()
        self.directory.disable_member(organization_id="customer-a", membership_id=self.admin.membership_id)
        for value, action in ((credential, self.console.authenticate), (self.native.access_token, self.store.authenticate_access)):
            with self.assertRaises(SessionStoreError):
                action(value)
        invitation = self.directory.reinvite(organization_id="customer-a", membership_id=self.admin.membership_id)
        activate_member(self.store, self.directory, invitation=invitation)
        with self.assertRaisesRegex(SessionStoreError, "admin_access_denied"):
            self.login()
        self.assertEqual(self.console.list_grants(organization_id="customer-a")["items"][0]["status"], "revoked")
        self.grant()
        self.console.authenticate(self.login())

    def test_member_removal_checks_scope_version_self_and_verified_event_actor(self):
        credential = self.login()
        for member, version, reason in ((self.admin.membership_id, 1, "admin_self_removal_refused"),
                                        (self.member.membership_id, 2, "admin_member_changed"),
                                        ("mem_unavailable", 1, "onboarding_membership_unavailable")):
            with self.assertRaisesRegex(SessionStoreError, reason):
                self.console.disable_member(credential, membership_id=member, expected_version=version)
        result = self.console.disable_member(credential, membership_id=self.member.membership_id, expected_version=1)
        self.assertTrue(result["changed"])
        self.assertFalse(self.console.disable_member(credential, membership_id=self.member.membership_id, expected_version=1)["changed"])
        with self.store._connection() as connection:
            actors = connection.execute("SELECT decision_actor_id, decision_scope FROM session_security_events WHERE event_type = 'membership_disabled'").fetchall()
        self.assertEqual([tuple(row) for row in actors], [(self.admin.membership_id, "organization")])
        invitation = self.directory.reinvite(organization_id="customer-a", membership_id=self.member.membership_id)
        activate_member(self.store, self.directory, invitation=invitation, subject="member-subject", email="member@example.test")
        with self.assertRaisesRegex(SessionStoreError, "admin_member_changed"):
            self.console.disable_member(credential, membership_id=self.member.membership_id, expected_version=1)

    def test_event_write_failure_rolls_back_member_grant_and_session_revocation(self):
        credential = self.login()
        self.console.grant(organization_id="customer-a", membership_id=self.member.membership_id, role="report_viewer")
        with mock.patch.object(self.directory, "_event", side_effect=sqlite3.OperationalError("private fixture failure")):
            with self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
                self.console.disable_member(credential, membership_id=self.member.membership_id, expected_version=1)
        self.assertEqual(self.directory.list_records("memberships", organization_id="customer-a")["items"][0]["status"], "active")
        self.assertTrue(all(row["status"] == "active" for row in self.console.list_grants(organization_id="customer-a")["items"]))
        self.store.authenticate_access(self.member_native.access_token)

    def test_concurrent_login_has_only_one_surviving_console_session(self):
        flows = []
        for _ in range(2):
            _, state, cookie = self.begin()
            flows.append(self.console.consume_callback(state=state, browser_cookie=cookie))
        barrier = threading.Barrier(2)
        def finish(flow):
            store = self.fixture.open_store()
            console = ConsoleStore(store, TeamDirectory(self.fixture.config, store))
            barrier.wait(timeout=5)
            return console.complete_login(flow, {"iss": self.fixture.issuer, "sub": "admin-subject"})
        with ThreadPoolExecutor(max_workers=2) as workers:
            credentials = list(workers.map(finish, flows))
        accepted = 0
        for value in credentials:
            try:
                self.console.authenticate(value)
                accepted += 1
            except SessionStoreError:
                pass
        self.assertEqual(accepted, 1)


if __name__ == "__main__":
    unittest.main()
