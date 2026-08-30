from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hormuz._attribution_schema import TABLE_DDL
from hormuz.config import UsageStorageConfig
from hormuz.portfolio_wire import PortfolioError
from hormuz.postgres import postgres_transaction

if __package__:
    from ._attribution_fixture import AttributionAssertions
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _attribution_fixture import AttributionAssertions
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase


class PostgresAttributionTests(AttributionAssertions, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.config = replace(registry_config(Path("/unused/synthetic-attribution")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.setup_attribution()

    def _rows(self, names):
        with self.psycopg.connect(self.owner_dsn, row_factory=self.psycopg.rows.dict_row) as connection:
            return {table: sorted((dict(row) for row in connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(
                self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall()), key=repr) for table in names}

    def attribution_rows(self):
        return self._rows(TABLE_DDL)

    def v1_rows(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s AND tablename LIKE 'gateway_%%'", (self.schema,)).fetchall()
        return self._rows([row[0] for row in tables])

    def test_postgres_attribution_sources_and_immutable_facts(self):
        self.check_attribution_sources_and_immutable_facts()

    def test_postgres_attribution_corrections_voids_and_idempotency(self):
        self.check_append_only_corrections_voids_and_idempotency()

    def test_postgres_attribution_authority_before_lookup_and_join(self):
        self.check_authority_precedes_lookup_and_tenant_join()

    def test_postgres_attribution_scope_race(self):
        self.check_scope_race_fails_and_never_retargets()

    def test_postgres_attribution_concurrency(self):
        self.check_admission_and_correction_concurrency()

    def test_postgres_attribution_atomicity_and_read_audit(self):
        self.check_atomicity_and_audit_before_delivery()

    def test_postgres_attribution_frozen_pagination(self):
        self.check_frozen_pagination_and_role_bound_cursors()

    def test_postgres_attribution_rejection_coverage(self):
        self.check_rejections_are_not_fabricated_attempts_or_work_content()

    def test_postgres_attribution_invalid_requests(self):
        self.check_invalid_requests_cannot_reach_storage()

    def test_postgres_attribution_forced_rls_append_only_and_missing_shape(self):
        self.attempt()
        with self.psycopg.connect(self.runtime_dsn) as connection:
            for table in TABLE_DDL:
                self.assertEqual(connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall(), [])
        with postgres_transaction(self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role, organization_id="beta") as connection:
            self.assertEqual(connection.execute("SELECT * FROM portfolio_attribution_events").fetchall(), [])
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute("INSERT INTO portfolio_attribution_cursors VALUES ('acme','forged','bob','[]','2026-01-01T00:00:00Z',0,'2026-01-01T00:00:00Z','id','{}')")
        for table in TABLE_DDL:
            with self.psycopg.connect(self.runtime_dsn) as connection:
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    connection.execute(self.sql.SQL("DELETE FROM {}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(table)))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("ALTER TABLE {}.portfolio_attribution_events NO FORCE ROW LEVEL SECURITY").format(self.sql.Identifier(self.schema)))
        try:
            self.error("unavailable", self.call)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("ALTER TABLE {}.portfolio_attribution_events FORCE ROW LEVEL SECURITY").format(self.sql.Identifier(self.schema)))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("GRANT UPDATE ON {}.portfolio_attribution_events TO {}").format(
                self.sql.Identifier(self.schema), self.sql.Identifier(self.runtime_role)))
        try:
            self.error("unavailable", self.call)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("REVOKE UPDATE ON {}.portfolio_attribution_events FROM {}").format(
                    self.sql.Identifier(self.schema), self.sql.Identifier(self.runtime_role)))
        with self.psycopg.connect(self.owner_dsn) as connection:
            with self.assertRaisesRegex(self.psycopg.errors.CheckViolation, "portfolio_append_only"):
                connection.execute(self.sql.SQL("TRUNCATE {}.portfolio_attribution_cursors").format(self.sql.Identifier(self.schema)))
