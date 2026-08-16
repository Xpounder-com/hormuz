from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hormuz.content_free import (
    CONTENT_FREE_SCHEMA_VERSION,
    CONTENT_FREE_TABLE_COLUMNS,
)
from hormuz.context_store import ContextStoreError, SQLiteContextRepository
from hormuz.session_store import SQLiteSessionStore, SessionStoreError
from hormuz.store import SecurityStoreError, UsageStore


def _session_store(path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(
        path,
        master_key=b"s" * 32,
        access_ttl_seconds=600,
        absolute_ttl_seconds=43_200,
        enrollment_ttl_seconds=300,
    )


class ContentFreeSchemaTests(unittest.TestCase):
    def test_fresh_stores_match_the_versioned_content_free_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stores = {
                "usage": (root / "usage.sqlite3", UsageStore),
                "session": (root / "session.sqlite3", _session_store),
                "context": (root / "context.sqlite3", SQLiteContextRepository),
            }
            for store_kind, (path, constructor) in stores.items():
                with self.subTest(store_kind=store_kind):
                    constructor(path)
                    with sqlite3.connect(path) as connection:
                        for table, expected_columns in CONTENT_FREE_TABLE_COLUMNS[
                            store_kind
                        ].items():
                            observed_columns = {
                                str(row[1])
                                for row in connection.execute(
                                    f"PRAGMA table_info({table})"
                                ).fetchall()
                            }
                            self.assertEqual(observed_columns, set(expected_columns))

            self.assertEqual(
                CONTENT_FREE_SCHEMA_VERSION,
                "hormuz.content-free-schema.v1",
            )

    def test_usage_store_refuses_unreviewed_telemetry_columns_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            UsageStore(path)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "ALTER TABLE gateway_usage_events ADD COLUMN prompt TEXT"
                )

            with self.assertRaisesRegex(
                SecurityStoreError,
                "^content_free_schema_incompatible$",
            ):
                UsageStore(path)

    def test_session_store_refuses_unreviewed_audit_columns_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.sqlite3"
            _session_store(path)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "ALTER TABLE session_security_events ADD COLUMN raw_query TEXT"
                )

            with self.assertRaisesRegex(
                SessionStoreError,
                "^content_free_schema_incompatible$",
            ):
                _session_store(path)

    def test_context_store_refuses_content_in_audit_schema_on_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            SQLiteContextRepository(path)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "ALTER TABLE context_access_events ADD COLUMN source_content TEXT"
                )

            with self.assertRaisesRegex(
                ContextStoreError,
                "^content_free_schema_incompatible$",
            ):
                SQLiteContextRepository(path)

    def test_content_bearing_and_auth_state_tables_are_outside_this_contract(self) -> None:
        manifested_tables = {
            table
            for store_tables in CONTENT_FREE_TABLE_COLUMNS.values()
            for table in store_tables
        }
        self.assertTrue(
            {
                "context_records",
                "context_lifecycle_snapshots",
                "session_enrollments",
                "human_sessions",
                "consumed_refresh_credentials",
            }.isdisjoint(manifested_tables)
        )


if __name__ == "__main__":
    unittest.main()
