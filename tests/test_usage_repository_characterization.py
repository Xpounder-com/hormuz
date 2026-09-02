from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hormuz.store import UsageStore
from tests._sqlite import managed_sqlite_connection

if __package__:
    from ._usage_repository_contract import exercise_usage_repository, ledger_clock, read_usage_repository
else:
    from _usage_repository_contract import exercise_usage_repository, ledger_clock, read_usage_repository


class UsageRepositoryCharacterizationTests(unittest.TestCase):
    def test_all_v1_operations_preserve_the_sqlite_ledger_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            exercise_usage_repository(self, store)

    def test_read_operations_never_initialize_or_mutate_empty_or_populated_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            writable = UsageStore(path)
            for populated in (False, True):
                with self.subTest(populated=populated):
                    if populated:
                        exercise_usage_repository(self, writable)
                    with managed_sqlite_connection(path) as connection:
                        before = list(connection.iterdump())
                    reader = UsageStore(path, read_only=True)
                    with ledger_clock():
                        read_usage_repository(reader)
                    with self.assertRaises(sqlite3.OperationalError):
                        reader.audit_chain_head(organization_id="acme")
                    with managed_sqlite_connection(path) as connection:
                        self.assertEqual(list(connection.iterdump()), before)


if __name__ == "__main__":
    unittest.main()
