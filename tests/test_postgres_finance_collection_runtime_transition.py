"""Schema 16-to-17 ACL-only transition, exact predecessor and recovery proofs."""

import os
import json
import unittest
from unittest import mock

import hormuz.postgres as postgres_module
from hormuz.postgres import PostgresStorageError

if __package__:
    from ._postgres_fixture import PostgresTestCase
    from ._finance_collection_transition_fixture import seed_postgres_collection_predecessor
    from ._finance_collection_runtime_predecessor_fixture import finance_collection_runtime_predecessor_call
    from . import test_postgres_finance_collection_transition as preflight
    from . import test_postgres_finance_collection_runtime as runtime_tests
    from . import test_finance_collection_runtime as collection
else:
    from _postgres_fixture import PostgresTestCase
    from _finance_collection_transition_fixture import seed_postgres_collection_predecessor
    from _finance_collection_runtime_predecessor_fixture import finance_collection_runtime_predecessor_call
    import test_postgres_finance_collection_transition as preflight
    import test_postgres_finance_collection_runtime as runtime_tests
    import test_finance_collection_runtime as collection


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL")
class PostgresFinanceCollectionRuntimeTransitionTests(PostgresTestCase):
    # Reuse the established exhaustive snapshot and matched pg_dump/restore
    # machinery without inheriting the earlier transition's test cases.
    migrate = preflight.PostgresFinanceCollectionTransitionTests.migrate
    runtime = preflight.PostgresFinanceCollectionTransitionTests.runtime
    request = preflight.PostgresFinanceCollectionTransitionTests.request
    snapshot = preflight.PostgresFinanceCollectionTransitionTests.snapshot
    backup = preflight.PostgresFinanceCollectionTransitionTests.backup
    restore = preflight.PostgresFinanceCollectionTransitionTests.restore
    restart = runtime_tests.PostgresFinanceCollectionRuntimeTests.restart
    binding_request = collection.FinanceCollectionSQLiteRepositoryTests.binding_request
    bind = collection.FinanceCollectionSQLiteRepositoryTests.bind

    def setUp(self):
        self.assertEqual(postgres_module.POSTGRES_SCHEMA_VERSION, 17)
        self._drop_schema(self.schema)
        self.seeded = seed_postgres_collection_predecessor(
            owner_dsn=self.owner_dsn, runtime_dsn=self.runtime_dsn,
            schema=self.schema, runtime_role=self.runtime_role,
            policy_control_role=self.policy_control_role,
            custody_control_role=self.custody_control_role,
            custody_executor_role=self.custody_executor_role,
        )
        with mock.patch.object(postgres_module, "POSTGRES_SCHEMA_VERSION", 16):
            self.assertEqual(self.migrate().version, 16)
            self.runtime().verify_ready()
        self.before = self.snapshot()

    def assert_predecessor_ready(self, dsn=None):
        self.assertEqual(finance_collection_runtime_predecessor_call(self.request(dsn)),
                         {"status": "ready", "runtime_files_verified": 150})

    def assert_prior_bytes_and_guards_preserved(self):
        after = self.snapshot()
        for table, rows in self.before["rows"].items():
            if table != "hormuz_schema_migrations":
                self.assertEqual(after["rows"][table], rows, table)
        self.assertEqual([row for row in after["rows"]["hormuz_schema_migrations"] if json.loads(row[0])["version"] != 17], self.before["rows"]["hormuz_schema_migrations"])
        for field in ("constraints", "triggers", "functions"):
            self.assertEqual(after[field], self.before[field], field)
        self.assertEqual([row[:4] for row in after["shape"]], [row[:4] for row in self.before["shape"]])

    def test_populated_predecessor_and_acl_only_idempotent_transition(self):
        populated = {table for table, rows in self.before["rows"].items() if rows}
        for table in ("gateway_usage_events", "gateway_audit_chain_entries", "portfolio_work_scope_versions",
                      "gateway_finance_attempt_evidence", "gateway_provider_attempt_metrics"):
            self.assertIn(table, populated)
        self.assertEqual(self.migrate().version, 17)
        self.assert_prior_bytes_and_guards_preserved()
        after = self.snapshot()
        self.assertEqual(self.migrate().version, 17)
        self.assertEqual(self.snapshot(), after)
        self.runtime().verify_ready()

    def test_missing_migration_and_failed_grants_rollback_before_retry(self):
        original = postgres_module._migration_sql
        for failure in ("missing", "after_grants"):
            def migration(version, *args):
                if version == 17 and failure == "missing":
                    raise PostgresStorageError("storage_schema_migration_unavailable")
                sql = original(version, *args)
                return sql + (" SELECT 1 / 0;" if version == 17 else "")
            with self.subTest(failure=failure), mock.patch.object(postgres_module, "_migration_sql", side_effect=migration):
                with self.assertRaises(PostgresStorageError) as caught:
                    self.migrate()
                self.assertEqual(caught.exception.code, "storage_schema_migration_unavailable" if failure == "missing" else "storage_unavailable")
            self.assertEqual(self.snapshot(), self.before)
        self.assertEqual(self.migrate().version, 17)
        self.assert_prior_bytes_and_guards_preserved()

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_RUNTIME_PYTHON"), "requires pinned schema-16 installed predecessor")
    def test_exact_predecessor_refuses_newer_and_partial_without_repair(self):
        self.assert_predecessor_ready()
        self.migrate()
        before = self.snapshot()
        self.assertEqual(finance_collection_runtime_predecessor_call(self.request()),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(self.snapshot(), before)
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("UPDATE {}.hormuz_schema_migrations SET state='applying' WHERE version=17").format(self.sql.Identifier(self.schema)))
        partial = self.snapshot()
        self.assertEqual(finance_collection_runtime_predecessor_call(self.request()),
                         {"status": "refused", "code": "storage_schema_partial_upgrade"})
        for operation in (self.migrate, self.runtime):
            with self.assertRaisesRegex(PostgresStorageError, "storage_schema_partial_upgrade"):
                operation()
        self.assertEqual(self.snapshot(), partial)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER") and os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_RUNTIME_PYTHON"), "requires matched backup tools and pinned installed predecessor")
    def test_stopped_writer_restore_verifies_exact_old_pair_and_retains_new_pair(self):
        self.assert_predecessor_ready()
        checkpoint = self.backup()
        self.migrate()
        retained = self.snapshot()
        owner, runtime = self.restore(checkpoint)
        self.assert_predecessor_ready(runtime)
        self.assertEqual(self.snapshot(owner), self.before)
        self.assertEqual(self.snapshot(), retained)

    @unittest.skipUnless(os.environ.get("HORMUZ_TEST_PG_CONTAINER") and os.environ.get("HORMUZ_TEST_FINANCE_COLLECTION_RUNTIME_PYTHON"), "requires matched backup tools and pinned installed predecessor")
    def test_post_checkpoint_collection_write_requires_retained_forward_recovery(self):
        from dataclasses import replace
        from pathlib import Path
        from hormuz.config import UsageStorageConfig
        if __package__:
            from ._portfolio_fixture import registry_config
        else:
            from _portfolio_fixture import registry_config

        checkpoint = self.backup()
        self.migrate()
        self.config = replace(registry_config(Path("/unused/finance17")), usage_storage=UsageStorageConfig(
            backend="postgresql", postgres_schema=self.schema, postgres_runtime_role=self.runtime_role,
        ))
        self.repository = self.restart()
        self.bind()
        value = collection.query("openai.organization-usage-completions.v1")
        prepared = self.repository.prepare_collection(collection.ADMIN, value, idempotency_key="after-checkpoint", evidence_origin="customer_file")
        self.repository.publish_collection(collection.ADMIN, prepared, collection.normalized_usage(value))
        retained = self.snapshot()
        forward_checkpoint = self.backup()
        old_owner, old_runtime = self.restore(checkpoint)
        self.assert_predecessor_ready(old_runtime)
        self.assertEqual(self.snapshot(old_owner), self.before)
        self.assertNotEqual(self.snapshot(old_owner), retained)
        owner, runtime = self.restore(forward_checkpoint)
        self.assertEqual(self.snapshot(owner), retained)
        self.runtime(runtime).verify_ready()
        self.assertEqual(finance_collection_runtime_predecessor_call(self.request(runtime)),
                         {"status": "refused", "code": "storage_schema_newer_than_binary"})
        self.assertEqual(self.snapshot(), retained)
