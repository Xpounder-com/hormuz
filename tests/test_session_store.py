from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hormuz.session_store import SQLiteSessionStore, SessionStoreError


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "sessions.sqlite3"
        self.clock = MutableClock()
        self.store = SQLiteSessionStore(
            self.path,
            master_key=b"m" * 32, audience="https://gateway.example",
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
            clock=self.clock,
        )
        self.enrollment_secret = "enrollment_" + "s" * 32

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _authorized_enrollment(self) -> str:
        enrollment = self.store.create_enrollment(
            issuer="https://issuer.example",
            client_name="codex",
            enrollment_secret=self.enrollment_secret,
        )
        self.store.begin_authorization(
            enrollment_id=enrollment.enrollment_id,
            state="state_" + "a" * 32,
            browser_cookie="browser_" + "b" * 32,
            nonce="nonce_" + "n" * 32,
            pkce_verifier="verifier_" + "v" * 64,
        )
        flow = self.store.consume_callback(
            state="state_" + "a" * 32,
            browser_cookie="browser_" + "b" * 32,
        )
        self.assertEqual(flow.nonce, "nonce_" + "n" * 32)
        self.assertEqual(flow.pkce_verifier, "verifier_" + "v" * 64)
        self.store.authorize_enrollment(
            enrollment_id=enrollment.enrollment_id,
            subject="stable-alice-subject",
            organization_id="xpounder",
            actor_id="alice",
            team_id="engineering",
            clearance="confidential",
        )
        return enrollment.enrollment_id

    def test_enrollment_is_single_use_and_credentials_are_not_stored_raw(self) -> None:
        enrollment_id = self._authorized_enrollment()
        pair = self.store.redeem_enrollment(
            enrollment_id=enrollment_id,
            enrollment_secret=self.enrollment_secret,
        )
        principal = self.store.authenticate_access(pair.access_token)
        self.assertEqual(principal.subject, "stable-alice-subject")
        self.assertEqual(principal.client_name, "codex")
        self.assertEqual(principal.organization_id, "xpounder")
        self.assertEqual(principal.actor_id, "alice")
        self.assertEqual(principal.team_id, "engineering")
        self.assertEqual(principal.clearance, "confidential")
        with self.assertRaisesRegex(SessionStoreError, "enrollment_not_redeemable"):
            self.store.redeem_enrollment(
                enrollment_id=enrollment_id,
                enrollment_secret=self.enrollment_secret,
            )

        database_bytes = self.path.read_bytes()
        for secret in (
            self.enrollment_secret,
            pair.access_token,
            pair.refresh_token,
            "state_" + "a" * 32,
            "browser_" + "b" * 32,
            "verifier_" + "v" * 64,
            "nonce_" + "n" * 32,
        ):
            self.assertNotIn(secret.encode("utf-8"), database_bytes)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX directory ownership and mode check")
    def test_insecure_ancestor_is_refused_before_database_creation(self) -> None:
        shared = Path(self.temporary.name) / "shared"
        private = shared / "private"
        private.mkdir(parents=True, mode=0o700)
        shared.chmod(0o777)
        candidate = private / "new-sessions.sqlite3"
        try:
            with self.assertRaisesRegex(SessionStoreError, "session_store_insecure_parent"):
                SQLiteSessionStore(
                    candidate,
                    master_key=b"m" * 32,
                    audience="https://gateway.example",
                    access_ttl_seconds=600,
                    absolute_ttl_seconds=43_200,
                    enrollment_ttl_seconds=300,
                )
        finally:
            shared.chmod(0o700)
        self.assertFalse(candidate.exists())

    @unittest.skipIf(os.name == "nt", "POSIX directory ownership and mode check")
    def test_parent_permissions_are_revalidated_before_each_database_open(self) -> None:
        parent = self.path.parent
        parent.chmod(0o777)
        try:
            with self.assertRaisesRegex(SessionStoreError, "session_store_insecure_parent"):
                self.store.check_available()
        finally:
            parent.chmod(0o700)

    def test_refresh_rotates_both_credentials_and_replay_revokes_family(self) -> None:
        first = self.store.redeem_enrollment(
            enrollment_id=self._authorized_enrollment(),
            enrollment_secret=self.enrollment_secret,
        )
        second = self.store.refresh(first.refresh_token)
        self.assertNotEqual(first.access_token, second.access_token)
        self.assertNotEqual(first.refresh_token, second.refresh_token)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            self.store.authenticate_access(first.access_token)

        with self.assertRaisesRegex(SessionStoreError, "refresh_replay_detected"):
            self.store.refresh(first.refresh_token)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            self.store.authenticate_access(second.access_token)
        with self.assertRaisesRegex(SessionStoreError, "expired_session_credential"):
            self.store.refresh(second.refresh_token)

        with closing(sqlite3.connect(self.path)) as connection, connection:
            event_types = [row[0] for row in connection.execute(
                "SELECT event_type FROM session_security_events"
            )]
        self.assertIn("refresh_replay", event_types)

    def test_access_and_absolute_expiry_fail_closed(self) -> None:
        first = self.store.redeem_enrollment(
            enrollment_id=self._authorized_enrollment(),
            enrollment_secret=self.enrollment_secret,
        )
        self.clock.advance(601)
        with self.assertRaisesRegex(SessionStoreError, "expired_session_credential"):
            self.store.authenticate_access(first.access_token)

        second = self.store.refresh(first.refresh_token)
        self.clock.advance(43_200)
        with self.assertRaisesRegex(SessionStoreError, "expired_session_credential"):
            self.store.refresh(second.refresh_token)

    def test_bad_state_cookie_expiry_and_logout_fail_closed(self) -> None:
        enrollment = self.store.create_enrollment(
            issuer="https://issuer.example",
            client_name="claude-code",
            enrollment_secret=self.enrollment_secret,
        )
        self.store.begin_authorization(
            enrollment_id=enrollment.enrollment_id,
            state="state_" + "a" * 32,
            browser_cookie="browser_" + "b" * 32,
            nonce="nonce_" + "n" * 32,
            pkce_verifier="verifier_" + "v" * 64,
        )
        with self.assertRaisesRegex(SessionStoreError, "invalid_callback_state"):
            self.store.consume_callback(
                state="state_" + "a" * 32,
                browser_cookie="browser_" + "x" * 32,
            )
        self.store.consume_callback(
            state="state_" + "a" * 32,
            browser_cookie="browser_" + "b" * 32,
        )
        with self.assertRaisesRegex(SessionStoreError, "invalid_callback_state"):
            self.store.consume_callback(
                state="state_" + "a" * 32,
                browser_cookie="browser_" + "b" * 32,
            )
        self.store.fail_enrollment(enrollment_id=enrollment.enrollment_id)

        expired = self.store.create_enrollment(
            issuer="https://issuer.example",
            client_name="claude-code",
            enrollment_secret="expired_enrollment_" + "s" * 32,
        )
        self.store.begin_authorization(
            enrollment_id=expired.enrollment_id,
            state="expired_state_" + "a" * 32,
            browser_cookie="expired_browser_" + "b" * 32,
            nonce="expired_nonce_" + "n" * 32,
            pkce_verifier="expired_verifier_" + "v" * 64,
        )
        self.clock.advance(301)
        with self.assertRaisesRegex(SessionStoreError, "invalid_callback_state"):
            self.store.consume_callback(
                state="expired_state_" + "a" * 32,
                browser_cookie="expired_browser_" + "b" * 32,
            )

        second_store = SQLiteSessionStore(
            Path(self.temporary.name) / "second.sqlite3",
            master_key=b"x" * 32, audience="https://gateway.example",
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
            clock=self.clock,
        )
        second_secret = "second_enrollment_" + "s" * 32
        created = second_store.create_enrollment(
            issuer="https://issuer.example",
            client_name="codex",
            enrollment_secret=second_secret,
        )
        second_store.begin_authorization(
            enrollment_id=created.enrollment_id,
            state="second_state_" + "a" * 32,
            browser_cookie="second_browser_" + "b" * 32,
            nonce="second_nonce_" + "n" * 32,
            pkce_verifier="second_verifier_" + "v" * 64,
        )
        second_store.consume_callback(
            state="second_state_" + "a" * 32,
            browser_cookie="second_browser_" + "b" * 32,
        )
        second_store.authorize_enrollment(
            enrollment_id=created.enrollment_id,
            subject="stable-alice-subject",
            organization_id="xpounder",
            actor_id="alice",
            team_id="engineering",
            clearance="confidential",
        )
        pair = second_store.redeem_enrollment(
            enrollment_id=created.enrollment_id,
            enrollment_secret=second_secret,
        )
        self.assertTrue(second_store.revoke(pair.refresh_token))
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            second_store.authenticate_access(pair.access_token)

    def test_concurrent_refresh_reuse_revokes_the_winning_family(self) -> None:
        pair = self.store.redeem_enrollment(
            enrollment_id=self._authorized_enrollment(),
            enrollment_secret=self.enrollment_secret,
        )
        barrier = threading.Barrier(3)
        successes = []
        failures = []

        def rotate() -> None:
            barrier.wait(timeout=5)
            try:
                successes.append(self.store.refresh(pair.refresh_token))
            except SessionStoreError as error:
                failures.append(error.code)

        threads = [threading.Thread(target=rotate) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(len(successes), 1)
        self.assertEqual(failures, ["refresh_replay_detected"])
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            self.store.authenticate_access(successes[0].access_token)

    def test_rotating_master_key_invalidates_prior_credentials(self) -> None:
        pair = self.store.redeem_enrollment(enrollment_id=self._authorized_enrollment(), enrollment_secret=self.enrollment_secret)
        rotated = SQLiteSessionStore(self.path, master_key=b"r" * 32, audience="https://gateway.example", access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300, clock=self.clock)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            rotated.authenticate_access(pair.access_token)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            rotated.refresh(pair.refresh_token)

    def test_credentials_are_bound_to_gateway_even_if_store_and_key_are_reused(self) -> None:
        pair = self.store.redeem_enrollment(enrollment_id=self._authorized_enrollment(), enrollment_secret=self.enrollment_secret)
        other_gateway = SQLiteSessionStore(self.path, master_key=b"m" * 32, audience="https://another-gateway.example", access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300, clock=self.clock)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            other_gateway.authenticate_access(pair.access_token)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            other_gateway.refresh(pair.refresh_token)

    def test_unexpected_durable_fields_fail_closed(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE session_security_events ADD COLUMN prompt TEXT")
        with self.assertRaisesRegex(SessionStoreError, "session_store_schema_incompatible"):
            SQLiteSessionStore(self.path, master_key=b"m" * 32, audience="https://gateway.example", access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300)

    def test_encrypted_flow_tampering_is_a_stable_failure(self) -> None:
        enrollment = self.store.create_enrollment(issuer="https://issuer.example", client_name="codex", enrollment_secret=self.enrollment_secret)
        self.store.begin_authorization(enrollment_id=enrollment.enrollment_id, state="s" * 43, browser_cookie="c" * 43, nonce="n" * 43, pkce_verifier="p" * 64)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE session_enrollments SET encrypted_flow = ?", (b"x" * 70,))
        with self.assertRaisesRegex(SessionStoreError, "session_store_corrupt_flow"):
            self.store.consume_callback(state="s" * 43, browser_cookie="c" * 43)

    def test_credentials_and_transient_secrets_are_hidden_from_repr(self) -> None:
        pair = self.store.redeem_enrollment(enrollment_id=self._authorized_enrollment(), enrollment_secret=self.enrollment_secret)
        self.assertNotIn(pair.access_token, repr(pair))
        self.assertNotIn(pair.refresh_token, repr(pair))

    def test_redactor_recognizes_session_credentials(self) -> None:
        from hormuz.config import SecretControls
        from hormuz.redaction import REPLACEMENT, SecretRedactor
        pair = self.store.redeem_enrollment(enrollment_id=self._authorized_enrollment(), enrollment_secret=self.enrollment_secret)
        result = SecretRedactor(SecretControls()).inspect({"input": pair.access_token + " " + pair.refresh_token})
        self.assertEqual(result.value["input"], REPLACEMENT + " " + REPLACEMENT)
        self.assertEqual(result.count, 2)




if __name__ == "__main__":
    unittest.main()
