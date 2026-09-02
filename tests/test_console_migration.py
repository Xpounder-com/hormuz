from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from hormuz import session_store
from hormuz.session_store import SQLiteSessionStore, SessionStoreError
from tests import test_onboarding_migration as v2_fixture


class ConsoleMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "sessions.sqlite3"
        fixture = json.loads((Path(__file__).parent / "fixtures/session-store-v3.json").read_text())
        self.assertEqual(fixture["source_commit"], "7638f0a9ce68c82f09200331c925e7c9b62ca47d")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in fixture["statements"]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 3")
            connection.execute("INSERT INTO onboarding_organizations VALUES ('legacy-org', 'Legacy organization', 'https://issuer.example', '2026-08-30T00:00:00+00:00')")

    def open(self):
        return SQLiteSessionStore(self.path, master_key=b"m" * 32, audience="https://gateway.example",
                                  access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300)

    def test_frozen_v3_database_upgrades_and_old_binary_ceiling_refuses_v4(self):
        self.open()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT name FROM onboarding_organizations").fetchone()[0], "Legacy organization")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM console_sessions").fetchone()[0], 0)
        with mock.patch.object(session_store, "SESSION_STORE_SCHEMA_VERSION", 3):
            with self.assertRaisesRegex(SessionStoreError, "session_store_schema_newer_than_binary"):
                self.open()

    def test_console_migration_failure_rolls_back_to_v3_without_partial_tables(self):
        invalid = dict(session_store.CONSOLE_TABLE_DDL, fixture_failure="CREATE TABLE invalid syntax")
        with mock.patch.object(session_store, "CONSOLE_TABLE_DDL", invalid), self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
            self.open()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'console_%'").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM onboarding_organizations").fetchone()[0], 1)
        self.open()

    def test_failure_after_both_migrations_rolls_back_all_the_way_to_v2(self):
        fixture = v2_fixture.OnboardingMigrationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        with mock.patch.object(session_store, "CONSOLE_INDEX_DDL", ("CREATE INDEX invalid syntax",)), self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
            fixture.open()
        self.assertEqual(fixture.version(), 2)
        self.assertEqual(fixture.open().authenticate_access(fixture.access).actor_id, "legacy-actor")

    def test_unexpected_v3_objects_fail_without_modification(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("ALTER TABLE onboarding_memberships ADD COLUMN raw_token TEXT")
        with self.assertRaisesRegex(SessionStoreError, "session_store_schema_incompatible"):
            self.open()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'console_grants'").fetchone())

    def test_concurrent_v3_upgrade_serializes_schema_change(self):
        barrier = threading.Barrier(2)
        def open_together():
            barrier.wait(timeout=5)
            self.open().check_available()
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(open_together) for _ in range(2)]
            for future in futures:
                future.result(timeout=10)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
