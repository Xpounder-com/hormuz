from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
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
            master_key=b"m" * 32,
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

        events, cursor = self.store.list_security_events(
            organization_id="xpounder",
            limit=10,
            event_type="refresh_replay",
        )
        self.assertIsNone(cursor)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target_actor_id, "alice")
        self.assertEqual(events[0].target_team_id, "engineering")
        self.assertEqual(events[0].organization_id, "xpounder")
        self.assertNotIn(first.refresh_token, repr(events))

        with sqlite3.connect(self.path) as connection:
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
            master_key=b"x" * 32,
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
        events, cursor = second_store.list_security_events(
            organization_id="xpounder",
            limit=10,
            event_type="logout",
        )
        self.assertIsNone(cursor)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].target_actor_id, "alice")
        self.assertEqual(events[0].target_team_id, "engineering")

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

    def test_admin_listing_and_revocation_are_tenant_scoped_and_metadata_only(self) -> None:
        alice = self.store.redeem_enrollment(
            enrollment_id=self._authorized_enrollment(),
            enrollment_secret=self.enrollment_secret,
        )
        second_alice = self.store.redeem_enrollment(
            enrollment_id=self._authorized_enrollment(),
            enrollment_secret=self.enrollment_secret,
        )

        other_secret = "other_enrollment_" + "s" * 32
        enrollment = self.store.create_enrollment(
            issuer="https://issuer.example",
            client_name="claude-code",
            enrollment_secret=other_secret,
        )
        self.store.begin_authorization(
            enrollment_id=enrollment.enrollment_id,
            state="other_state_" + "a" * 32,
            browser_cookie="other_browser_" + "b" * 32,
            nonce="other_nonce_" + "n" * 32,
            pkce_verifier="other_verifier_" + "v" * 64,
        )
        self.store.consume_callback(
            state="other_state_" + "a" * 32,
            browser_cookie="other_browser_" + "b" * 32,
        )
        self.store.authorize_enrollment(
            enrollment_id=enrollment.enrollment_id,
            subject="other-tenant-subject",
            organization_id="other-tenant",
            actor_id="mallory",
            team_id="other-team",
            clearance="internal",
        )
        other = self.store.redeem_enrollment(
            enrollment_id=enrollment.enrollment_id,
            enrollment_secret=other_secret,
        )

        page, cursor = self.store.list_active_sessions(
            organization_id="xpounder",
            limit=1,
        )
        self.assertIsNotNone(cursor)
        self.assertEqual(len(page), 1)
        self.assertEqual(page[0].actor_id, "alice")
        self.assertEqual(page[0].organization_id, "xpounder")
        self.assertNotIn(alice.access_token, repr(page))
        self.assertNotIn(alice.refresh_token, repr(page))
        second_page, final_cursor = self.store.list_active_sessions(
            organization_id="xpounder",
            limit=1,
            cursor=cursor,
        )
        self.assertIsNone(final_cursor)
        self.assertEqual(len(second_page), 1)
        self.assertNotEqual(page[0].session_id, second_page[0].session_id)

        revoked = self.store.revoke_administratively(
            organization_id="xpounder",
            actor_id="alice",
            decision_actor_id="security-admin",
            reason_code="access_change",
        )
        self.assertEqual(revoked, 2)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            self.store.authenticate_access(alice.access_token)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_credential"):
            self.store.authenticate_access(second_alice.access_token)
        self.assertEqual(self.store.authenticate_access(other.access_token).actor_id, "mallory")

        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            event = connection.execute(
                "SELECT * FROM session_security_events WHERE event_type = 'admin_revocation'"
            ).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["organization_id"], "xpounder")
        self.assertEqual(event["target_actor_id"], "alice")
        self.assertEqual(event["decision_actor_id"], "security-admin")
        self.assertEqual(event["reason_code"], "access_change")
        self.assertNotIn(alice.access_token, repr(dict(event)))
        self.assertNotIn(alice.refresh_token, repr(dict(event)))

        events, cursor = self.store.list_security_events(
            organization_id="xpounder",
            limit=1,
            event_type="admin_revocation",
        )
        self.assertIsNotNone(cursor)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].decision_actor_id, "security-admin")
        self.assertEqual(events[0].decision_scope, "actor")
        self.assertEqual(events[0].reason_code, "access_change")
        final_events, final_cursor = self.store.list_security_events(
            organization_id="xpounder",
            limit=1,
            cursor=cursor,
            event_type="admin_revocation",
        )
        self.assertIsNone(final_cursor)
        self.assertEqual(len(final_events), 1)
        self.assertNotEqual(events[0].event_id, final_events[0].event_id)

        other_revoked = self.store.revoke_administratively(
            organization_id="other-tenant",
            actor_id="mallory",
            decision_actor_id="other-admin",
            reason_code="administrative",
        )
        self.assertEqual(other_revoked, 1)
        xpounder_events, _ = self.store.list_security_events(
            organization_id="xpounder",
            limit=10,
        )
        self.assertEqual({item.organization_id for item in xpounder_events}, {"xpounder"})
        self.assertNotIn("mallory", repr(xpounder_events))
        future_events, future_cursor = self.store.list_security_events(
            organization_id="xpounder",
            limit=10,
            since=self.clock() + timedelta(seconds=1),
        )
        self.assertEqual(future_events, ())
        self.assertIsNone(future_cursor)
        with self.assertRaisesRegex(SessionStoreError, "invalid_session_event_since"):
            self.store.list_security_events(
                organization_id="xpounder",
                limit=10,
                since=datetime(2026, 8, 15, 12, 0),
            )

    def test_v1_migration_revokes_sessions_without_tenant_binding(self) -> None:
        now = self.clock().isoformat()
        expiry = (self.clock() + timedelta(hours=1)).isoformat()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO human_sessions (
                    id, issuer, subject, client_name, access_hash, refresh_hash,
                    access_expires_at, absolute_expires_at, generation, created_at,
                    refreshed_at, organization_id, actor_id, team_id, clearance
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-session",
                    "https://issuer.example",
                    "legacy-subject",
                    "codex",
                    b"legacy-access-hash",
                    b"legacy-refresh-hash",
                    expiry,
                    expiry,
                    now,
                    now,
                    "xpounder",
                    "legacy",
                    "engineering",
                    "internal",
                ),
            )
            connection.execute("DROP INDEX idx_human_sessions_admin_scope")
            connection.execute("DROP INDEX idx_session_security_events_admin_scope")
            for table, column in (
                ("session_enrollments", "organization_id"),
                ("session_enrollments", "actor_id"),
                ("session_enrollments", "team_id"),
                ("session_enrollments", "clearance"),
                ("human_sessions", "organization_id"),
                ("human_sessions", "actor_id"),
                ("human_sessions", "team_id"),
                ("human_sessions", "clearance"),
                ("session_security_events", "organization_id"),
                ("session_security_events", "target_actor_id"),
                ("session_security_events", "target_team_id"),
                ("session_security_events", "decision_actor_id"),
                ("session_security_events", "decision_scope"),
                ("session_security_events", "reason_code"),
            ):
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
            connection.execute("PRAGMA user_version = 1")

        migrated = SQLiteSessionStore(
            self.path,
            master_key=b"m" * 32,
            access_ttl_seconds=600,
            absolute_ttl_seconds=43_200,
            enrollment_ttl_seconds=300,
            clock=self.clock,
        )
        with sqlite3.connect(self.path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            columns = {
                row[1]: row[3]
                for row in connection.execute("PRAGMA table_info(human_sessions)")
            }
            revoked_at = connection.execute(
                "SELECT revoked_at FROM human_sessions WHERE id = 'legacy-session'"
            ).fetchone()[0]
            event = connection.execute(
                """
                SELECT event_type FROM session_security_events
                WHERE session_id = 'legacy-session'
                """
            ).fetchone()[0]
        self.assertEqual(version, 2)
        self.assertEqual(columns["organization_id"], 1)
        self.assertEqual(columns["actor_id"], 1)
        self.assertEqual(columns["team_id"], 1)
        self.assertEqual(columns["clearance"], 1)
        self.assertIsNotNone(revoked_at)
        self.assertEqual(event, "migration_identity_binding_required")
        self.assertEqual(
            migrated.list_active_sessions(organization_id="xpounder", limit=10),
            ((), None),
        )


if __name__ == "__main__":
    unittest.main()
