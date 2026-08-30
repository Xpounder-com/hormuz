from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hormuz._portfolio_schema import TABLE_DDL
from hormuz.config import UsageStorageConfig
from hormuz.portfolio_repository import RegistryRepository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import PortfolioError
from hormuz.postgres import postgres_transaction
if __package__:
    from ._portfolio_fixture import RegistryAssertions, registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _portfolio_fixture import RegistryAssertions, registry_config
    from _postgres_fixture import PostgresTestCase


class PostgresPortfolioRegistryTests(RegistryAssertions, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.config = replace(registry_config(Path("/unused/registry-test")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        self.registry_environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.repository = RegistryRepository(self.config, environ=self.registry_environment)
        self.service = PortfolioService(self.config, self.repository)

    def registry_rows(self):
        with self.psycopg.connect(self.owner_dsn, row_factory=self.psycopg.rows.dict_row) as connection:
            return {table: connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(
                self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall() for table in TABLE_DDL}

    def test_postgres_registry_lifecycle_and_hierarchy(self):
        self.check_lifecycle_and_hierarchy()

    def test_postgres_registry_authorization_before_access(self):
        self.check_authorization_before_access()

    def test_postgres_registry_idempotency_and_versions(self):
        self.check_idempotency_and_versions()

    def test_postgres_registry_frozen_pagination(self):
        self.check_frozen_pagination()

    def test_postgres_registry_authorized_bindings(self):
        self.check_bindings()

    def test_postgres_registry_concurrent_writers(self):
        self.check_concurrent_writers()

    def test_postgres_registry_atomic_failure_and_safe_audit(self):
        self.check_atomic_failure_and_safe_audit()

    def test_postgres_registry_strict_input(self):
        self.check_strict_input()

    def test_postgres_registry_forced_rls_and_append_only(self):
        self.create()
        with self.psycopg.connect(self.runtime_dsn) as connection:
            for table in TABLE_DDL:
                rows = connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(
                    self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall()
                self.assertEqual(rows, [])
        with postgres_transaction(self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role, organization_id="beta") as connection:
            self.assertEqual(connection.execute("SELECT * FROM portfolio_work_scope_versions").fetchall(), [])
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute("INSERT INTO portfolio_cursors VALUES ('acme','forged','alice','[]','list_scopes','2026-01-01T00:00:00Z',0,'2026-01-01T00:00:00Z','id','{}')")
        for table in TABLE_DDL:
            with self.psycopg.connect(self.runtime_dsn) as connection:
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    connection.execute(self.sql.SQL("DELETE FROM {}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(table)))

    def test_postgres_registry_pool_reuse_and_rls_shape_fail_closed(self):
        pool = self._runtime_pool()
        self.repository = RegistryRepository(self.config, environ=self.registry_environment, connection_pool=pool)
        self.service = PortfolioService(self.config, self.repository)
        self.check_authorization_before_access()
        self.assertFalse(pool.closed)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("ALTER TABLE {}.portfolio_work_scope_versions NO FORCE ROW LEVEL SECURITY").format(self.sql.Identifier(self.schema)))
        try:
            self.raises_code("unavailable", self.call)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("ALTER TABLE {}.portfolio_work_scope_versions FORCE ROW LEVEL SECURITY").format(self.sql.Identifier(self.schema)))
