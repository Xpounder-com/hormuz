from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from hormuz._outcome_schema import TABLE_DDL
from hormuz.config import UsageStorageConfig
from hormuz.outcome_repository import OutcomeRepository
from hormuz.postgres import postgres_transaction

if __package__:
    from ._outcome_fixture import OutcomeAssertions
    from ._portfolio_fixture import registry_config
    from ._postgres_fixture import PostgresTestCase
else:
    from _outcome_fixture import OutcomeAssertions
    from _portfolio_fixture import registry_config
    from _postgres_fixture import PostgresTestCase


class PostgresOutcomeTests(OutcomeAssertions, PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.config = replace(registry_config(Path("/unused/synthetic-outcomes")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role))
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.setup_outcomes()

    def _rows(self, names):
        with self.psycopg.connect(self.owner_dsn, row_factory=self.psycopg.rows.dict_row) as connection:
            return {table: sorted((dict(row) for row in connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(
                self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall()), key=repr) for table in names}

    def outcome_rows(self):
        return self._rows(TABLE_DDL)

    def legacy_rows(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            names = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (self.schema,)).fetchall()]
        return self._rows([name for name in names if name not in TABLE_DDL])

    def test_postgres_outcome_metadata_replay_and_rotation(self):
        self.check_atomic_metadata_receipt_replay_and_rotation()

    def test_postgres_outcome_ordering_corrections_and_tombstones(self):
        self.check_ordering_uncertainty_and_corrections_never_rewrite_facts()

    def test_postgres_outcome_batch_ordering_and_unsupported(self):
        self.check_batch_ordering_and_unsupported_do_not_replace_authoritative_state()

    def test_postgres_outcome_historical_scope(self):
        self.check_historical_binding_and_missing_source_time()

    def test_postgres_outcome_atomicity_and_audit(self):
        self.check_atomic_failure_and_no_read_before_audit()

    def test_postgres_outcome_failed_delivery(self):
        self.check_failed_delivery_is_bounded_content_free_and_not_success()

    def test_postgres_outcome_repository_rejection_coverage(self):
        self.check_repository_rejections_have_durable_failure_coverage()

    def test_postgres_outcome_corrupt_cursors(self):
        self.check_corrupt_cursors_fail_without_audit_or_partial_pages()

    def test_postgres_outcome_outage(self):
        # An absent local Unix socket, never an external database or credential.
        unavailable = OutcomeRepository(self.config, dsn="host=/unused/hormuz-outcome-outage dbname=synthetic user=synthetic connect_timeout=1")
        self.check_storage_outage_never_accepts_or_retries(unavailable)

    def test_postgres_outcome_connector_ties_and_cursor_authority(self):
        self.check_connector_ties_and_config_or_role_changes()

    def test_postgres_outcome_timeout_rolls_back_without_acknowledgement(self):
        before = self.outcome_rows()
        from hormuz.portfolio_wire import PortfolioError
        with self.assertRaises(PortfolioError) as caught:
            with self.repository._transaction("acme") as sql:
                self.repository._audit(sql, "acme", "read_context", None, "observed", actor="alice")
                sql.execute("SELECT pg_sleep(6)")
        self.assertEqual(caught.exception.code, "unavailable")
        self.assertEqual(self.outcome_rows(), before)

    def test_postgres_outcome_authorized_retention(self):
        self.check_authorized_retention_is_separate_append_only_and_invalidates_cursors()

    def test_postgres_outcome_retention_replay_integrity(self):
        self.check_retention_replay_rejects_corrupt_mac_and_supports_rotation()

    def test_postgres_outcome_authorization(self):
        self.check_authorization_and_tenant_isolation_before_lookup()

    def test_postgres_outcome_concurrency_and_cursors(self):
        self.check_concurrent_replicas_and_frozen_pagination()

    def test_postgres_outcome_forced_rls_privileges_and_bounded_statement(self):
        self.ingest()
        with self.repository._transaction("acme") as sql:
            self.assertEqual(sql.one("SHOW statement_timeout")["statement_timeout"], "5s")
        with self.psycopg.connect(self.runtime_dsn) as connection:
            for table in TABLE_DDL:
                self.assertEqual(connection.execute(self.sql.SQL("SELECT * FROM {}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(table))).fetchall(), [])
        with postgres_transaction(self.runtime_dsn, schema=self.schema, runtime_role=self.runtime_role, organization_id="beta") as connection:
            self.assertEqual(connection.execute("SELECT * FROM portfolio_outcome_events").fetchall(), [])
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute("INSERT INTO portfolio_outcome_audit_events VALUES ('acme','forged',999,NULL,'github-one','ingest',NULL,'observed','2026-08-30T12:00:00Z')")
        for table in TABLE_DDL:
            with self.psycopg.connect(self.runtime_dsn) as connection:
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    connection.execute(self.sql.SQL("DELETE FROM {}.{}").format(self.sql.Identifier(self.schema), self.sql.Identifier(table)))
            with self.psycopg.connect(self.owner_dsn) as connection:
                with self.assertRaisesRegex(self.psycopg.errors.CheckViolation, "portfolio_append_only"):
                    connection.execute(self.sql.SQL("UPDATE {}.{} SET organization_id=organization_id").format(self.sql.Identifier(self.schema), self.sql.Identifier(table)))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("ALTER TABLE {}.portfolio_outcome_events NO FORCE ROW LEVEL SECURITY").format(self.sql.Identifier(self.schema)))
        try:
            self.error("unavailable", self.page)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("ALTER TABLE {}.portfolio_outcome_events FORCE ROW LEVEL SECURITY").format(self.sql.Identifier(self.schema)))
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("GRANT UPDATE ON {}.portfolio_outcome_events TO {}").format(
                self.sql.Identifier(self.schema), self.sql.Identifier(self.runtime_role)))
        try:
            self.error("unavailable", self.page)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("REVOKE UPDATE ON {}.portfolio_outcome_events FROM {}").format(
                    self.sql.Identifier(self.schema), self.sql.Identifier(self.runtime_role)))
