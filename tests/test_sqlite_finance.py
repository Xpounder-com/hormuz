from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hormuz._finance_schema import TABLE_DDL, sqlite_statements
from hormuz.finance_repository import create_finance_repository
from hormuz.store import StorageSchemaError, UsageStore

if __package__:
    from ._finance_fixture import AUDIT, CARDS, FinanceAssertions
    from ._portfolio_fixture import registry_config
    from ._registry_transition_fixture import sqlite_snapshot
else:
    from _finance_fixture import AUDIT, CARDS, FinanceAssertions
    from _portfolio_fixture import registry_config
    from _registry_transition_fixture import sqlite_snapshot


class SQLiteFinanceTests(FinanceAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.environment = None
        self.store = UsageStore(self.config.database_path)
        self.setup_finance()

    def finance_rows(self):
        with sqlite3.connect(self.config.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY organization_id, sequence")]
                    for table in TABLE_DDL}

    def legacy_rows(self):
        return {name: rows for name, rows in sqlite_snapshot(self.config.database_path)["rows"].items() if name not in TABLE_DDL}

    def test_sqlite_finance_real_schema_and_append_only_guards(self):
        self.assertEqual(self.store.schema_version, 9)
        self.assertEqual(len(sqlite_snapshot(self.config.database_path)["rows"]), 36)
        self.register()
        before = self.finance_rows()
        with sqlite3.connect(self.config.database_path) as connection:
            for table in TABLE_DDL:
                for statement in (f"UPDATE {table} SET organization_id=organization_id", f"DELETE FROM {table}"):
                    with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                        connection.execute(statement)
        self.assertEqual(self.finance_rows(), before)

    def test_sqlite_finance_missing_guard_refuses_without_repair(self):
        with sqlite3.connect(self.config.database_path) as connection:
            connection.execute(f"DROP TRIGGER {CARDS}_no_delete")
        before = sqlite_snapshot(self.config.database_path)
        self.error("unavailable", self.register)
        self.error("unavailable", self.get)
        with self.assertRaisesRegex(StorageSchemaError, "storage_schema_partial_upgrade"):
            UsageStore(self.config.database_path)
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_sqlite_finance_insert_conflicts_cannot_replace_history(self):
        self.register()
        self.get()  # A read audit has no receipt FK, so each audit key is tested independently.
        before = sqlite_snapshot(self.config.database_path)
        selections = (
            (AUDIT, "organization_id, event_id, 3, actor_id, operation, rate_card_id, version, content_digest, occurred_at", "sequence=2"),
            (AUDIT, "organization_id, '00000000000000000000000000000000', sequence, actor_id, operation, rate_card_id, version, content_digest, occurred_at", "sequence=2"),
            (CARDS, "organization_id, rate_card_id, version, card_json, content_digest, '00000000000000000000000000000000', registered_by, registered_at, sequence", "version=1"),
            (CARDS, "organization_id, 'other-card', version, card_json, content_digest, receipt_id, registered_by, registered_at, sequence", "version=1"),
        )
        for recursive_triggers in (0, 1):
            for verb in ("INSERT OR REPLACE", "REPLACE"):
                for table, selection, where in selections:
                    with self.subTest(recursive_triggers=recursive_triggers, verb=verb, table=table, selection=selection):
                        with sqlite3.connect(self.config.database_path) as connection:
                            connection.execute("PRAGMA foreign_keys=ON")
                            connection.execute(f"PRAGMA recursive_triggers={recursive_triggers}")
                            with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                                connection.execute(f"{verb} INTO {table} SELECT {selection} FROM {table} WHERE {where}")
                        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_sqlite_finance_replacement_conflict_rolls_back_whole_insert(self):
        self.register()
        self.get()
        before = sqlite_snapshot(self.config.database_path)
        with sqlite3.connect(self.config.database_path) as connection:
            connection.execute("PRAGMA recursive_triggers=OFF")
            with self.assertRaisesRegex(sqlite3.IntegrityError, "portfolio_append_only"):
                connection.execute(
                    f"INSERT OR REPLACE INTO {AUDIT} "
                    f"SELECT organization_id, '00000000000000000000000000000000', 3, actor_id, operation, "
                    f"rate_card_id, version, content_digest, occurred_at FROM {AUDIT} WHERE sequence=2 UNION ALL "
                    f"SELECT organization_id, event_id, 4, actor_id, operation, "
                    f"rate_card_id, version, content_digest, occurred_at FROM {AUDIT} WHERE sequence=2"
                )
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_sqlite_finance_has_no_hidden_rowid_replacement_key(self):
        self.register()
        with sqlite3.connect(self.config.database_path) as connection:
            for table in TABLE_DDL:
                for alias in ("rowid", "_rowid_", "oid"):
                    with self.subTest(table=table, alias=alias):
                        with self.assertRaisesRegex(sqlite3.OperationalError, "no such column"):
                            connection.execute(f"SELECT {alias} FROM {table}").fetchall()

    def test_sqlite_finance_missing_replacement_guards_refuse_without_repair(self):
        for table in TABLE_DDL:
            with self.subTest(table=table):
                with sqlite3.connect(self.config.database_path) as connection:
                    connection.execute(f"DROP TRIGGER {table}_no_replace")
                before = sqlite_snapshot(self.config.database_path)
                self.error("unavailable", self.register)
                self.error("unavailable", self.get)
                with self.assertRaisesRegex(StorageSchemaError, "storage_schema_partial_upgrade"):
                    UsageStore(self.config.database_path)
                self.assertEqual(sqlite_snapshot(self.config.database_path), before)
                with sqlite3.connect(self.config.database_path) as connection:
                    connection.execute(next(sql for sql in sqlite_statements() if sql.startswith(f"CREATE TRIGGER {table}_no_replace ")))

    def test_sqlite_finance_hidden_rowid_schema_refuses_without_repair(self):
        with sqlite3.connect(self.config.database_path) as connection:
            for table in reversed(TABLE_DDL):
                connection.execute(f"DROP TABLE {table}")
            for statement in sqlite_statements():
                connection.execute(statement.replace(" WITHOUT ROWID", ""))
        before = sqlite_snapshot(self.config.database_path)
        self.error("unavailable", self.register)
        self.error("unavailable", self.get)
        with self.assertRaisesRegex(StorageSchemaError, "storage_schema_partial_upgrade"):
            UsageStore(self.config.database_path)
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_sqlite_finance_corrupted_card_or_receipt_never_replays(self):
        self.register()
        for column, replacement in (("content_digest", "a" * 64), ("card_json", "{}"),
                                    ("receipt_id", "b" * 32), ("registered_by", "forged")):
            with self.subTest(column=column):
                with sqlite3.connect(self.config.database_path) as connection:
                    original = connection.execute(f"SELECT {column} FROM {CARDS}").fetchone()[0]
                    connection.execute(f"DROP TRIGGER {CARDS}_no_update")
                    connection.execute(f"UPDATE {CARDS} SET {column}=?", (replacement,))
                    connection.execute(next(sql for sql in sqlite_statements() if sql.startswith(f"CREATE TRIGGER {CARDS}_no_update ")))
                before = self.finance_rows()
                self.error("unavailable", self.register)
                self.error("unavailable", self.get)
                self.assertEqual(self.finance_rows(), before)
                with sqlite3.connect(self.config.database_path) as connection:
                    connection.execute(f"DROP TRIGGER {CARDS}_no_update")
                    connection.execute(f"UPDATE {CARDS} SET {column}=?", (original,))
                    connection.execute(next(sql for sql in sqlite_statements() if sql.startswith(f"CREATE TRIGGER {CARDS}_no_update ")))

    def test_sqlite_finance_absent_database_is_not_created(self):
        path = self.config.database_path.with_name("absent.sqlite3")
        repository = create_finance_repository(replace(self.config, database_path=path))
        self.error("unavailable", lambda: self.register(repository=repository))
        self.error("unavailable", lambda: self.get(repository=repository))
        self.assertFalse(path.exists())

    def test_sqlite_finance_malformed_audit_maximum_fails_closed_without_writes(self):
        # SQLite's BIGINT affinity and numeric CHECK admit text, blobs and
        # fractional/out-of-range reals. Exercise actual stored values, not mocks.
        for sequence in ("SYNTHETIC_EXCLUDED", b"SYNTHETIC_EXCLUDED", 1.5,
                         float("inf"), float(9223372036854775808), 9223372036854775807):
            with self.subTest(sequence=sequence), tempfile.TemporaryDirectory() as directory:
                config = registry_config(Path(directory))
                UsageStore(config.database_path)
                repository = create_finance_repository(config)
                self.register(repository=repository)
                with sqlite3.connect(config.database_path) as connection:
                    connection.execute(
                        f"INSERT INTO {AUDIT} SELECT organization_id, ?, ?, actor_id, 'read', "
                        f"rate_card_id, version, content_digest, occurred_at FROM {AUDIT} WHERE sequence=1",
                        ("0" * 32, sequence),
                    )
                    maximum = connection.execute(f"SELECT MAX(sequence) FROM {AUDIT}").fetchone()[0]
                    self.assertEqual(maximum, sequence)
                    self.assertIs(type(maximum), type(sequence))
                before = sqlite_snapshot(config.database_path)
                self.error("unavailable", lambda: self.register(repository=repository, version=2))
                self.error("unavailable", lambda: self.get(repository=repository))
                self.assertEqual(sqlite_snapshot(config.database_path), before)
