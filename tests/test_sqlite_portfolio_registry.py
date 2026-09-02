from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from hormuz._portfolio_schema import TABLE_DDL
from hormuz.portfolio_repository import RegistryRepository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import PortfolioError
from hormuz.store import UsageStore
from tests._sqlite import managed_sqlite_connection
if __package__:
    from ._portfolio_fixture import RegistryAssertions, registry_config
else:
    from _portfolio_fixture import RegistryAssertions, registry_config


class SQLitePortfolioRegistryTests(RegistryAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.registry_environment = {}
        UsageStore(self.config.database_path)
        self.repository = RegistryRepository(self.config)
        self.service = PortfolioService(self.config, self.repository)

    def registry_rows(self):
        with managed_sqlite_connection(self.config.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
                    for table in TABLE_DDL}

    def test_sqlite_registry_lifecycle_and_hierarchy(self):
        self.check_lifecycle_and_hierarchy()

    def test_sqlite_registry_authorization_before_access(self):
        self.check_authorization_before_access()

    def test_sqlite_registry_idempotency_and_versions(self):
        self.check_idempotency_and_versions()

    def test_sqlite_registry_frozen_pagination(self):
        self.check_frozen_pagination()

    def test_sqlite_registry_authorized_bindings(self):
        self.check_bindings()

    def test_sqlite_registry_concurrent_writers(self):
        self.check_concurrent_writers()

    def test_sqlite_registry_atomic_failure_and_safe_audit(self):
        self.check_atomic_failure_and_safe_audit()

    def test_sqlite_registry_strict_input(self):
        self.check_strict_input()

    def test_sqlite_registry_append_only_and_missing_trigger(self):
        self.create()
        with managed_sqlite_connection(self.config.database_path) as connection:
            for table in ("portfolio_work_scope_versions", "portfolio_audit_events", "portfolio_idempotency"):
                for command in (f"DELETE FROM {table}", f"UPDATE {table} SET organization_id='beta'"):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(command)
            connection.execute("DROP TRIGGER portfolio_work_scope_versions_no_update")
        self.raises_code("unavailable", self.call)
