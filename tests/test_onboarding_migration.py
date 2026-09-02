from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest import mock

from hormuz import session_store
from hormuz.session_store import SQLiteSessionStore, SessionStoreError
from tests.test_session_store import MutableClock


class OnboardingMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "sessions.sqlite3"
        self.clock = MutableClock()
        fixture = json.loads((Path(__file__).parent / "fixtures/session-store-v2.json").read_text())
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executescript(fixture["ddl"])
        self.access, self.refresh = "hox_a_" + "a" * 43, "hox_r_" + "r" * 43
        self.enrollment_secret = "legacy-enrollment-" + "s" * 32
        # Frozen v2 key derivation, independent of the upgraded store's helpers.
        key = hmac.new(b"m" * 32, b"hormuz/session/hash/v2\0https://gateway.example", hashlib.sha256).digest()
        def digest(purpose, value):
            return hmac.new(key, (purpose + "\0" + value).encode(), hashlib.sha256).digest()
        now = self.clock().isoformat()
        expiry = (self.clock() + timedelta(seconds=600)).isoformat()
        absolute = (self.clock() + timedelta(seconds=43200)).isoformat()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                """INSERT INTO human_sessions (
                    id, issuer, subject, client_name, access_hash, refresh_hash, access_expires_at,
                    absolute_expires_at, generation, created_at, refreshed_at, organization_id, actor_id, team_id, clearance
                ) VALUES ('legacy-session', 'https://issuer.example', 'legacy-subject', 'codex', ?, ?, ?, ?, 0, ?, ?, 'legacy-org', 'legacy-actor', 'legacy-team', 'internal')""",
                (digest("access", self.access), digest("refresh", self.refresh), expiry, absolute, now, now),
            )
            connection.execute(
                """INSERT INTO session_enrollments (
                    id, secret_hash, issuer, client_name, status, subject, organization_id, actor_id, team_id, clearance, created_at, expires_at, authorized_at
                ) VALUES (?, ?, 'https://issuer.example', 'codex', 'authorized', 'legacy-subject', 'legacy-org', 'legacy-actor', 'legacy-team', 'internal', ?, ?, ?)""",
                ("E" * 32, digest("enrollment", self.enrollment_secret), now, expiry, now),
            )

    def open(self):
        return SQLiteSessionStore(self.path, master_key=b"m" * 32, audience="https://gateway.example",
                                  access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300, clock=self.clock)

    def version(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            return connection.execute("PRAGMA user_version").fetchone()[0]

    def test_real_v2_schema_upgrades_without_rotating_credentials_or_losing_pending_redemption(self):
        store = self.open()
        self.assertEqual(self.version(), session_store.SESSION_STORE_SCHEMA_VERSION)
        self.assertEqual(store.authenticate_access(self.access).actor_id, "legacy-actor")
        pending = store.redeem_enrollment(enrollment_id="E" * 32, enrollment_secret=self.enrollment_secret)
        self.assertIsNone(store.authenticate_access(pending.access_token).membership_id)
        rotated = store.refresh(self.refresh)
        self.assertEqual(store.authenticate_access(rotated.access_token).organization_id, "legacy-org")
        self.assertTrue(store.revoke(rotated.refresh_token))

    def test_unexpected_v2_column_fails_without_partial_upgrade(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE human_sessions ADD COLUMN prompt TEXT")
        with self.assertRaisesRegex(SessionStoreError, "session_store_schema_incompatible"):
            self.open()
        self.assertEqual(self.version(), 2)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'onboarding_%'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM human_sessions").fetchone()[0], 1)

    def test_mid_migration_failure_rolls_back_ddl_and_all_legacy_data_survives(self):
        partial = dict(session_store.ONBOARDING_TABLE_DDL)
        partial["fixture_failure"] = "CREATE TABLE invalid syntax"
        with mock.patch.object(session_store, "ONBOARDING_TABLE_DDL", partial):
            with self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
                self.open()
        self.assertEqual(self.version(), 2)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertNotIn("membership_id", {row[1] for row in connection.execute("PRAGMA table_info(human_sessions)")})
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'onboarding_%'").fetchone()[0], 0)
        self.assertEqual(self.open().authenticate_access(self.access).actor_id, "legacy-actor")

    def test_concurrent_open_serializes_upgrade_and_older_schema_ceiling_refuses(self):
        barrier = threading.Barrier(2)
        def open_together():
            barrier.wait(timeout=5)
            return self.open().authenticate_access(self.access).actor_id
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(open_together) for _ in range(2)]
            self.assertEqual([future.result(timeout=10) for future in futures], ["legacy-actor", "legacy-actor"])
        with mock.patch.object(session_store, "SESSION_STORE_SCHEMA_VERSION", 2):
            with self.assertRaisesRegex(SessionStoreError, "session_store_schema_newer_than_binary"):
                self.open()
        self.assertEqual(self.version(), session_store.SESSION_STORE_SCHEMA_VERSION)

    def test_concurrent_first_open_rechecks_version_before_initializing(self):
        self.path = self.path.with_name("brand-new.sqlite3")
        reached_bootstrap, other_finished = threading.Event(), threading.Event()
        original_connect = sqlite3.connect

        class DelayedConnection(sqlite3.Connection):
            def execute(connection, statement, *args, **kwargs):
                if statement.strip().upper() == "PRAGMA JOURNAL_MODE = WAL" and threading.current_thread().name.startswith("delayed-bootstrap"):
                    reached_bootstrap.set()
                    if not other_finished.wait(timeout=5):
                        raise AssertionError("other bootstrap did not finish")
                return super().execute(statement, *args, **kwargs)

        def connect(*args, **kwargs):
            return original_connect(*args, factory=DelayedConnection, **kwargs)

        with mock.patch.object(session_store.sqlite3, "connect", connect):
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="delayed-bootstrap") as worker:
                delayed = worker.submit(self.open)
                try:
                    self.assertTrue(reached_bootstrap.wait(timeout=5))
                    self.open()
                finally:
                    other_finished.set()
                delayed.result(timeout=10)
        self.assertEqual(self.version(), session_store.SESSION_STORE_SCHEMA_VERSION)
        self.open().check_available()

    def test_failed_first_open_leaves_no_partially_initialized_schema(self):
        self.path = self.path.with_name("failed-first-open.sqlite3")
        partial = dict(session_store.ONBOARDING_TABLE_DDL)
        partial["fixture_failure"] = "CREATE TABLE invalid syntax"
        with mock.patch.object(session_store, "ONBOARDING_TABLE_DDL", partial):
            with self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
                self.open()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchone())
        self.open().check_available()
        self.assertEqual(self.version(), session_store.SESSION_STORE_SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
