import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest import mock

from hormuz import session_store
from hormuz._session_schema import validate_session_schema
from hormuz.session_store import SQLiteSessionStore, SessionStoreError


class WorkspaceMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "sessions.sqlite3"
        fixture = json.loads((Path(__file__).parent / "fixtures/session-store-v4.json").read_text())
        self.assertEqual(fixture["source_commit"], "d93e05b26b9542232089b8104eb6881ffa665607")
        with closing(sqlite3.connect(self.path)) as connection, connection:
            for statement in fixture["statements"]:
                connection.execute(statement)
            connection.execute("PRAGMA user_version = 4")

    def open(self):
        return SQLiteSessionStore(self.path, master_key=b"m" * 32, audience="https://gateway.example", access_ttl_seconds=600, absolute_ttl_seconds=43200, enrollment_ttl_seconds=300)

    def test_frozen_v4_migrates_atomically_and_old_binary_refuses(self):
        self.open()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 5)
            self.assertTrue(validate_session_schema(connection))
        with mock.patch.object(session_store, "SESSION_STORE_SCHEMA_VERSION", 4):
            with self.assertRaisesRegex(SessionStoreError, "session_store_schema_newer_than_binary"):
                self.open()

    def test_failure_rolls_back_and_restart_can_finish(self):
        with mock.patch.object(session_store, "WORKSPACE_INDEX_DDL", ("CREATE INDEX invalid syntax",)):
            with self.assertRaisesRegex(SessionStoreError, "session_store_unavailable"):
                self.open()
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertTrue(validate_session_schema(connection, version=4))
        self.open()

    def test_concurrent_openers_observe_one_complete_schema(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda _: self.open(), range(8)))
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertTrue(validate_session_schema(connection))

    def test_unexpected_objects_refused_before_migration(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE request_content (prompt TEXT)")
        with self.assertRaisesRegex(SessionStoreError, "session_store_schema_incompatible"):
            self.open()
