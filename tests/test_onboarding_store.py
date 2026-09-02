from __future__ import annotations

import json
import secrets
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from hormuz.config import GatewayConfig, SecretControls
from hormuz.onboarding import TeamDirectory, normalize_email
from hormuz.redaction import REPLACEMENT, SecretRedactor
from hormuz.session_store import SQLiteSessionStore, SessionStoreError
from tests._session_fixtures import fixture_environment, session_config
from tests.test_session_store import MutableClock


class OnboardingStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.issuer = "http://127.0.0.1:9000"
        value = session_config(self.root, self.issuer, "http://127.0.0.1:8787")
        value["authentication"]["session_broker"]["onboarding_enabled"] = True
        value["authentication"]["oidc"]["issuers"][0]["login"]["scopes"] = ["openid", "email"]
        path = self.root / "hormuz.json"
        path.write_text(json.dumps(value))
        self.config = GatewayConfig.load(path, environ=fixture_environment())
        self.clock = MutableClock()
        self.store = self.open_store()
        self.directory = TeamDirectory(self.config, self.store)
        self.directory.create_organization(organization_id="customer-a", name="Customer A", issuer=self.issuer)
        self.directory.create_team(organization_id="customer-a", team_id="customer-a-eng", name="Engineering")

    def open_store(self):
        return SQLiteSessionStore(self.root / "sessions.sqlite3", master_key=b"m" * 32, audience="http://127.0.0.1:8787",
                                  access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300, clock=self.clock)

    def invite(self, **changes):
        args = dict(organization_id="customer-a", team_id="customer-a-eng", email="new@example.test", name="New Member", allowed_clients=("codex", "claude-code"))
        args.update(changes)
        return self.directory.invite(**args)

    def test_managed_organization_ids_are_complete_and_stably_ordered(self):
        self.directory.create_organization(
            organization_id="another-customer",
            name="Another Customer",
            issuer=self.issuer,
        )
        self.assertEqual(
            self.directory.managed_organization_ids(),
            ("another-customer", "customer-a"),
        )

    def enrollment(self, invitation=None, *, organization="customer-a", client="codex"):
        secret = secrets.token_urlsafe(32)
        enrollment = self.store.create_enrollment(issuer=self.issuer, client_name=client, enrollment_secret=secret, organization_id=organization)
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.store.begin_authorization(enrollment_id=enrollment.enrollment_id, state=state, browser_cookie=cookie,
                                       nonce=secrets.token_urlsafe(32), pkce_verifier=secrets.token_urlsafe(64))
        if invitation is not None:
            self.directory.attach_invitation(enrollment_id=enrollment.enrollment_id, state=state, browser_cookie=cookie, code=invitation.code)
        flow = self.store.consume_callback(state=state, browser_cookie=cookie)
        return flow, secret

    def authorize(self, flow, **changes):
        claims = dict(iss=self.issuer, sub="new-stable-subject", email="new@example.test", email_verified=True)
        claims.update(changes)
        self.directory.authorize_enrollment(flow=flow, claims=claims)

    def redeem(self, flow, secret):
        return self.store.redeem_enrollment(enrollment_id=flow.enrollment_id, enrollment_secret=secret)

    def test_invitation_binds_subject_and_keeps_raw_secrets_and_email_out_of_database(self):
        invitation = self.invite()
        flow, secret = self.enrollment(invitation)
        self.authorize(flow)
        pair = self.redeem(flow, secret)
        principal = self.store.authenticate_access(pair.access_token)
        identity = self.directory.identity_for_session(principal)
        self.assertEqual(identity.organization_id, "customer-a")
        self.assertEqual(identity.actor_id, invitation.membership_id)
        self.assertEqual(identity.team_id, "customer-a-eng")
        self.assertEqual(principal.authorization_version, 1)
        for path in self.root.glob("sessions.sqlite3*"):
            raw = path.read_bytes()
            for value in (invitation.code, "new@example.test", secret, pair.access_token, pair.refresh_token, flow.nonce, flow.pkce_verifier):
                self.assertFalse(value.encode() in raw, "secret persisted in session database/sidecar")
        self.assertFalse(invitation.code in repr(invitation))
        result = SecretRedactor(SecretControls()).inspect({"input": invitation.code})
        self.assertEqual(result.value["input"], REPLACEMENT)
        self.assertEqual(result.rules, ("hormuz_invitation_credential",))
        listed = json.dumps(self.directory.list_records("memberships", organization_id="customer-a"))
        self.assertNotIn("email", listed)
        self.assertNotIn("subject", listed)
        self.assertNotIn("hash", listed)

    def test_email_and_issuer_checks_do_not_consume_or_activate_invitation(self):
        invitation = self.invite()
        for claims, expected in (
            ({"email": "someone-else@example.test"}, "onboarding_recipient_mismatch"),
            ({"email": "New@example.test"}, "onboarding_recipient_mismatch"),
            ({"email_verified": False}, "onboarding_verified_email_required"),
            ({"email_verified": "true"}, "onboarding_verified_email_required"),
            ({"email_verified": 1}, "onboarding_verified_email_required"),
            ({"email": None}, "onboarding_invalid_email"),
            ({"iss": "https://unapproved.invalid"}, "enrollment_unavailable"),
        ):
            with self.subTest(claims=claims):
                flow, _ = self.enrollment(invitation)
                with self.assertRaisesRegex(SessionStoreError, expected):
                    self.authorize(flow, **claims)
        flow, secret = self.enrollment(invitation)
        self.authorize(flow, email="new@EXAMPLE.TEST")
        self.store.authenticate_access(self.redeem(flow, secret).access_token)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_invitation_unavailable"):
            self.enrollment(invitation)

    def test_existing_member_uses_stable_subject_without_requiring_old_email(self):
        invitation = self.invite()
        flow, secret = self.enrollment(invitation)
        self.authorize(flow)
        self.redeem(flow, secret)
        fresh, enrollment_secret = self.enrollment()
        self.authorize(fresh, email="changed@example.test", email_verified=False)
        self.store.authenticate_access(self.redeem(fresh, enrollment_secret).access_token)
        unknown, _ = self.enrollment()
        with self.assertRaisesRegex(SessionStoreError, "onboarding_membership_unavailable"):
            self.authorize(unknown, sub="new-owner-of-email")

    def test_wrong_organization_and_client_cannot_attach_invitation(self):
        invitation = self.invite(allowed_clients=("codex",))
        for organization, client in (("org-a", "codex"), ("customer-a", "claude-code")):
            with self.subTest(organization=organization, client=client), self.assertRaisesRegex(SessionStoreError, "onboarding_invitation_unavailable"):
                self.enrollment(invitation, organization=organization, client=client)

    def test_expiry_revocation_and_disabled_member_deny_pending_callback(self):
        invitation = self.invite(expires_in=300)
        self.clock.advance(301)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_invitation_unavailable"):
            self.enrollment(invitation)
        self.assertEqual(self.directory.list_records("invitations", organization_id="customer-a")["items"][0]["status"], "expired")
        self.assertTrue(self.directory.revoke_invitation(organization_id="customer-a", invitation_id=invitation.invitation_id))
        self.assertFalse(self.directory.revoke_invitation(organization_id="customer-a", invitation_id=invitation.invitation_id))
        second = self.directory.reinvite(organization_id="customer-a", membership_id=invitation.membership_id)
        flow, _ = self.enrollment(second)
        self.directory.disable_member(organization_id="customer-a", membership_id=invitation.membership_id)
        with self.assertRaisesRegex(SessionStoreError, "enrollment_unavailable"):
            self.authorize(flow)

    def test_removal_revokes_all_clients_pending_redemption_and_fresh_login(self):
        invitation = self.invite()
        first, secret = self.enrollment(invitation)
        self.authorize(first)
        codex = self.redeem(first, secret)
        second, secret = self.enrollment(client="claude-code")
        self.authorize(second)
        claude = self.redeem(second, secret)
        pending, pending_secret = self.enrollment()
        self.authorize(pending)
        result = self.directory.disable_member(organization_id="customer-a", membership_id=invitation.membership_id)
        self.assertEqual(result, {"changed": True, "sessions_revoked": 2, "enrollments_invalidated": 1, "invitations_revoked": 0})
        for pair in (codex, claude):
            with self.assertRaises(SessionStoreError):
                self.store.authenticate_access(pair.access_token)
            with self.assertRaises(SessionStoreError):
                self.store.refresh(pair.refresh_token)
        with self.assertRaisesRegex(SessionStoreError, "enrollment_not_redeemable"):
            self.redeem(pending, pending_secret)
        fresh, _ = self.enrollment()
        with self.assertRaisesRegex(SessionStoreError, "onboarding_membership_unavailable"):
            self.authorize(fresh)
        self.assertFalse(self.directory.disable_member(organization_id="customer-a", membership_id=invitation.membership_id)["changed"])
        with closing(sqlite3.connect(self.store.path)) as connection, connection:
            events = connection.execute("SELECT decision_actor_id, decision_scope FROM session_security_events WHERE event_type = 'membership_disabled'").fetchall()
        self.assertEqual(events, [("server_local_operator", "server_local"), ("server_local_operator", "server_local")])

    def test_disabling_one_member_preserves_another_members_unbound_returning_login(self):
        first = self.invite(email="first@example.test")
        first_flow, first_secret = self.enrollment(first)
        self.authorize(first_flow, sub="first-subject", email="first@example.test")
        self.redeem(first_flow, first_secret)

        second = self.invite(email="second@example.test")
        second_flow, second_secret = self.enrollment(second)
        self.authorize(second_flow, sub="second-subject", email="second@example.test")
        self.redeem(second_flow, second_secret)

        returning_flow, returning_secret = self.enrollment()
        result = self.directory.disable_member(
            organization_id="customer-a",
            membership_id=first.membership_id,
        )
        self.assertEqual(result["enrollments_invalidated"], 0)

        self.authorize(
            returning_flow,
            sub="second-subject",
            email="changed@example.test",
            email_verified=False,
        )
        principal = self.store.authenticate_access(
            self.redeem(returning_flow, returning_secret).access_token
        )
        self.assertEqual(principal.membership_id, second.membership_id)

    def test_reinvite_never_reassigns_subject_or_resurrects_old_credentials(self):
        invitation = self.invite()
        flow, secret = self.enrollment(invitation)
        self.authorize(flow)
        old_pair = self.redeem(flow, secret)
        pending, pending_secret = self.enrollment()
        self.authorize(pending)
        returning_flow, returning_secret = self.enrollment()
        self.directory.disable_member(organization_id="customer-a", membership_id=invitation.membership_id)
        self.clock.advance(1)
        reissued = self.directory.reinvite(organization_id="customer-a", membership_id=invitation.membership_id)
        wrong, _ = self.enrollment(reissued)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_subject_mismatch"):
            self.authorize(wrong, sub="new-owner-of-email")
        accepted, accepted_secret = self.enrollment(reissued)
        self.authorize(accepted)
        new_pair = self.redeem(accepted, accepted_secret)
        self.assertEqual(self.store.authenticate_access(new_pair.access_token).authorization_version, 3)
        with self.assertRaises(SessionStoreError):
            self.store.authenticate_access(old_pair.access_token)
        with self.assertRaises(SessionStoreError):
            self.store.refresh(old_pair.refresh_token)
        with self.assertRaises(SessionStoreError):
            self.redeem(pending, pending_secret)
        self.authorize(returning_flow)
        returning_pair = self.redeem(returning_flow, returning_secret)
        self.store.authenticate_access(returning_pair.access_token)

    def test_same_subject_cannot_accept_a_second_membership_after_email_change(self):
        first = self.invite()
        flow, _ = self.enrollment(first)
        self.authorize(flow)
        second = self.invite(email="changed@example.test")
        another, _ = self.enrollment(second)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_subject_already_bound"):
            self.authorize(another, email="changed@example.test")

    def test_operator_scope_and_configuration_collisions_fail_closed(self):
        invitation = self.invite()
        with self.assertRaisesRegex(SessionStoreError, "onboarding_membership_unavailable"):
            self.directory.disable_member(organization_id="org-a", membership_id=invitation.membership_id)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_invitation_unavailable"):
            self.directory.revoke_invitation(organization_id="org-a", invitation_id=invitation.invitation_id)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_configuration_conflict"):
            self.directory.create_organization(organization_id="org-a", name="Collision", issuer=self.issuer)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_configuration_conflict"):
            self.directory.create_team(organization_id="customer-a", team_id="engineering", name="Collision")
        configured = next(iter(self.config.identities_by_subject.values()))
        conflicting = replace(configured, organization_id="customer-a")
        with self.assertRaisesRegex(SessionStoreError, "onboarding_configuration_conflict"):
            TeamDirectory(replace(self.config, identities_by_subject={(self.issuer, "collision"): conflicting}), self.store)
        disabled = TeamDirectory(replace(self.config, session_broker=replace(self.config.session_broker, onboarding_enabled=False)), self.store)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_disabled"):
            disabled.list_records("memberships", organization_id="customer-a")

    def test_idempotent_setup_bounded_lists_and_conservative_email_matching(self):
        self.assertFalse(self.directory.create_organization(organization_id="customer-a", name="Customer A", issuer=self.issuer))
        self.assertFalse(self.directory.create_team(organization_id="customer-a", team_id="customer-a-eng", name="Engineering"))
        for index in range(3):
            self.invite(email=f"member-{index}@example.test")
        ids, after = [], ""
        while True:
            page = self.directory.list_records("memberships", organization_id="customer-a", limit=1, after=after)
            ids.extend(item["id"] for item in page["items"])
            after = page["next_cursor"]
            if after is None:
                break
        self.assertEqual(len(set(ids)), 3)
        with self.assertRaisesRegex(SessionStoreError, "onboarding_invalid_list"):
            self.directory.list_records("memberships", organization_id="customer-a", limit=101)
        self.assertEqual(normalize_email("Name+tag@EXAMPLE.test"), "Name+tag@example.test")
        for email in ("x\r\n@example.test", " x@example.test", "x@localhost", "x..y@example.test", "x@-example.test", "é@example.test"):
            with self.subTest(email=email), self.assertRaisesRegex(SessionStoreError, "onboarding_invalid_email"):
                normalize_email(email)

    def test_acceptance_event_failure_rolls_back_membership_and_invite(self):
        invitation = self.invite()
        flow, _ = self.enrollment(invitation)
        with mock.patch.object(self.directory, "_event", side_effect=sqlite3.OperationalError("fixture failure")):
            with self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
                self.authorize(flow)
        member = self.directory.list_records("memberships", organization_id="customer-a")["items"][0]
        self.assertEqual(member["status"], "pending")
        self.assertEqual(self.directory.list_records("invitations", organization_id="customer-a")["items"][0]["status"], "pending")
        self.authorize(flow)

    def test_concurrent_invite_acceptance_has_one_winner_across_store_instances(self):
        invitation = self.invite()
        flows = [self.enrollment(invitation)[0], self.enrollment(invitation)[0]]
        barrier, outcomes = threading.Barrier(2), []
        def accept(flow):
            directory = TeamDirectory(self.config, self.open_store())
            barrier.wait(timeout=5)
            try:
                directory.authorize_enrollment(flow=flow, claims={"iss": self.issuer, "sub": "new-stable-subject", "email": "new@example.test", "email_verified": True})
                outcomes.append("accepted")
            except SessionStoreError as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=accept, args=(flow,)) for flow in flows]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["accepted", "onboarding_invitation_unavailable"])

    def test_remove_racing_refresh_or_redemption_leaves_no_valid_credential(self):
        for operation in ("refresh", "redeem"):
            with self.subTest(operation=operation):
                invitation = self.invite(email=f"{operation}@example.test")
                flow, secret = self.enrollment(invitation)
                self.authorize(flow, email=f"{operation}@example.test", sub=operation)
                first = self.redeem(flow, secret) if operation == "refresh" else None
                barrier, pairs, failures = threading.Barrier(2), [], []
                def credential():
                    store = self.open_store()
                    barrier.wait(timeout=5)
                    try:
                        pair = store.refresh(first.refresh_token) if first else store.redeem_enrollment(enrollment_id=flow.enrollment_id, enrollment_secret=secret)
                        pairs.append(pair)
                    except SessionStoreError as error:
                        failures.append(error.code)
                def remove():
                    directory = TeamDirectory(self.config, self.open_store())
                    barrier.wait(timeout=5)
                    directory.disable_member(organization_id="customer-a", membership_id=invitation.membership_id)
                threads = [threading.Thread(target=credential), threading.Thread(target=remove)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
                self.assertEqual(len(pairs) + len(failures), 1)
                for pair in pairs + ([first] if first else []):
                    with self.assertRaises(SessionStoreError):
                        self.store.authenticate_access(pair.access_token)


if __name__ == "__main__":
    unittest.main()
