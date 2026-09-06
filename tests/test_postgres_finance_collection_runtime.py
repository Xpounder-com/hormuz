"""Provider-free finance collection proofs using the restricted PostgreSQL role."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.config import UsageStorageConfig
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.finance_collection import FinanceCollectionError
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz._finance_collection_schema import TABLE_DDL
from hormuz.cli import build_parser
from hormuz.commands import finance as finance_commands

if __package__:
    from ._postgres_fixture import PostgresTestCase
    from ._portfolio_fixture import registry_config
    from . import test_finance_collection_runtime as collection
else:
    from _postgres_fixture import PostgresTestCase
    from _portfolio_fixture import registry_config
    import test_finance_collection_runtime as collection


@unittest.skipUnless(os.environ.get("HORMUZ_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL")
class PostgresFinanceCollectionRuntimeTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = replace(
            registry_config(Path(temporary.name)),
            usage_storage=UsageStorageConfig(
                backend="postgresql", postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.repository = self.restart()

    def restart(self):
        return create_finance_collection_repository(
            self.config, environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        )

    binding_request = collection.FinanceCollectionSQLiteRepositoryTests.binding_request
    bind = collection.FinanceCollectionSQLiteRepositoryTests.bind
    test_binding_request_rejects_boolean_schema_version = collection.FinanceCollectionSQLiteRepositoryTests.test_binding_request_rejects_boolean_schema_version
    test_refresh_selection_uses_newest_exact_coverage_and_empty_suppresses_stale = collection.FinanceCollectionSQLiteRepositoryTests.test_refresh_selection_uses_newest_exact_coverage_and_empty_suppresses_stale
    test_current_cost_observations_expose_boolean_finality = collection.FinanceCollectionSQLiteRepositoryTests.test_current_cost_observations_expose_boolean_finality
    test_binding_and_role_revocation_races_prevent_publication = collection.FinanceCollectionSQLiteRepositoryTests.test_binding_and_role_revocation_races_prevent_publication

    def transaction(self, organization="acme"):
        return postgres_transaction(
            self.runtime_dsn, schema=self.schema,
            runtime_role=self.runtime_role, organization_id=organization,
        )

    def count(self, table):
        self.assertIn(table, TABLE_DDL)
        with self.transaction() as connection:
            return connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]

    def prepare(self, key="stable"):
        value = collection.query("openai.organization-usage-completions.v1")
        prepared = self.repository.prepare_collection(
            collection.ADMIN, value, idempotency_key=key, evidence_origin="customer_file",
        )
        return value, prepared

    def test_restricted_runtime_binding_is_idempotent(self):
        first = self.bind()
        self.assertEqual(first, self.bind())
        self.repository = self.restart()
        self.assertEqual(first, self.bind())

    def test_all_four_provider_profiles_persist_typed_private_observations(self):
        for provider, profile, page in (
            ("openai", "openai.organization-usage-completions.v1", collection.openai_page([collection.openai_bucket(collection.START, collection.MIDDLE, [collection.openai_usage()])])),
            ("openai", "openai.organization-costs.v1", collection.openai_page([collection.openai_bucket(collection.START, collection.MIDDLE, [collection.openai_cost()])])),
            ("anthropic", "anthropic.organization-usage-messages.v1", collection.anthropic_page([collection.anthropic_bucket(collection.START, collection.MIDDLE, [collection.anthropic_usage()])])),
            ("anthropic", "anthropic.organization-costs.v1", collection.anthropic_page([collection.anthropic_bucket(collection.START, collection.MIDDLE, [collection.anthropic_cost()])])),
        ):
            with self.subTest(profile=profile):
                self.bind(binding_id=provider, provider=provider)
                value = collection.query(profile, binding_id=provider)
                normalized = collection.normalize_collection_pages(value, (page,), fingerprint_key=collection.KEY, fingerprint_key_version=1)
                prepared = self.repository.prepare_collection(collection.ADMIN, value, idempotency_key=profile, evidence_origin="customer_file")
                self.repository.publish_collection(collection.ADMIN, prepared, normalized)
                view = self.restart().current_observations(collection.ADMIN, binding_id=provider, binding_version=1,
                    collection_profile=profile, start_at=collection.START, end_at=collection.MIDDLE)
                self.assertEqual(len(view.observations), 1)
                self.assertEqual(view.coverage[0]["coverage_state"], "observed")
                for raw in ("project-sensitive", "workspace-sensitive", "key-sensitive", "person-sensitive", "service-account-sensitive", "sensitive-openai-line-item"):
                    self.assertNotIn(raw, repr(view))
    def test_restart_replay_receipt_booleans_and_audit_chain(self):
        self.bind()
        value, prepared = self.prepare()
        receipt = self.repository.publish_collection(collection.ADMIN, prepared, collection.normalized_usage(value))
        self.repository = self.restart()
        _, replay = self.prepare()
        self.assertEqual(replay.state, "succeeded")
        self.assertEqual(self.repository.receipt_for_prepared(collection.ADMIN, replay), receipt)
        view = self.repository.current_observations(
            collection.ADMIN, binding_id=value.binding_id, binding_version=1,
            collection_profile=value.collection_profile, start_at=collection.START, end_at=collection.MIDDLE,
        )
        self.assertEqual(len(view.coverage), 1)
        self.assertEqual(len(view.observations), 1)
        self.assertIs(view.observations[0]["batch"], False)
        self.assertIs(view.observations[0]["provider_final"], False)
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme").sequence, 3)
        self.assertEqual(self.count("portfolio_finance_snapshots"), 1)

    def test_concurrent_publication_converges_on_one_receipt(self):
        self.bind()
        value = collection.query("openai.organization-usage-completions.v1")
        normalized = collection.normalized_usage(value)
        _, prepared = self.prepare("concurrent")
        barrier = threading.Barrier(2)

        def publish(_):
            repository = self.restart()
            barrier.wait(timeout=10)
            return repository.publish_collection(collection.ADMIN, prepared, normalized)

        with ThreadPoolExecutor(max_workers=2) as executor:
            receipts = list(executor.map(publish, range(2)))
        self.assertEqual(receipts[0], receipts[1])
        for table in ("portfolio_finance_collection_attempts", "portfolio_finance_collection_events", "portfolio_finance_snapshots"):
            self.assertEqual(self.count(table), 1)
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme").sequence, 3)

    def test_concurrent_prepare_allows_one_pending_root_not_duplicate_io(self):
        self.bind()
        barrier = threading.Barrier(2)
        value = collection.query("openai.organization-usage-completions.v1")

        def prepare(_):
            repository = self.restart()
            barrier.wait(timeout=10)
            try:
                return repository.prepare_collection(collection.ADMIN, value, idempotency_key="one-root", evidence_origin="customer_file").state
            except FinanceCollectionError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            states = list(executor.map(prepare, range(2)))
        self.assertCountEqual(states, ["pending", "attempt_pending"])
        self.assertEqual(self.count("portfolio_finance_collection_attempts"), 1)

    def test_cli_import_commits_before_io_and_completed_replay_reads_no_file_key_or_provider(self):
        self.bind()
        page = json.loads(collection.openai_page([
            collection.openai_bucket(collection.START, collection.MIDDLE, [collection.openai_usage()]),
        ]))
        bundle = json.dumps({
            "schema_id": "hormuz.finance-collection-file-bundle", "schema_version": 1,
            "collection_profile": "openai.organization-usage-completions.v1",
            "query_start_at": collection.START, "query_end_at": collection.MIDDLE,
            "bucket_width": "1d", "requested_page_size": 1, "pages": [page],
        }).encode()
        args = build_parser().parse_args([
            "finance", "import", "/unused/synthetic-bundle.json", "provider-account", "1",
            "openai.organization-usage-completions.v1", collection.START, collection.MIDDLE,
            "--page-size", "1", "--idempotency-key", "cli-stable", "--fingerprint-key-version", "1",
        ])
        environment = {
            "HORMUZ_PORTFOLIO_TOKEN": "synthetic-registry-admin-token",
            "HORMUZ_FINANCE_FINGERPRINT_KEY": collection.KEY.decode(),
            "HORMUZ_POSTGRES_DSN": self.runtime_dsn,
        }
        dependencies = finance_commands.FinanceCommandDependencies(
            fetch_pages=mock.Mock(side_effect=AssertionError("import provider I/O")),
            resolve_credentials=mock.Mock(side_effect=AssertionError("import provider credential access")),
        )

        def read_after_commit(path, maximum):
            self.assertEqual(self.count("portfolio_finance_collection_attempts"), 1)
            self.assertEqual(self.count("portfolio_finance_collection_events"), 0)
            return bundle

        def invoke():
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                status = finance_commands.run(self.config, args, dependencies, environ=environment)
            self.assertEqual((status, err.getvalue()), (0, ""))
            return json.loads(out.getvalue())

        with mock.patch.object(finance_commands, "_read_bounded", side_effect=read_after_commit) as read:
            first = invoke()
            read.assert_called_once()
        with mock.patch.object(finance_commands, "_read_bounded", side_effect=AssertionError("replay file I/O")), mock.patch.object(
            finance_commands, "_fingerprint_key", side_effect=AssertionError("replay key access")):
            self.assertEqual(invoke(), first)
        dependencies.fetch_pages.assert_not_called()
        dependencies.resolve_credentials.assert_not_called()

    def test_authorization_precedes_connection_and_revocation_rolls_back(self):
        viewer = PortfolioPrincipal("acme", "finance", ("finance_viewer",))
        with mock.patch("hormuz.finance_collection_repository.portfolio_transaction", side_effect=AssertionError("must not connect")):
            with self.assertRaisesRegex(FinanceCollectionError, "forbidden"):
                self.repository.bind_source(viewer, self.binding_request(), fingerprint_key=collection.KEY)
        self.bind()
        value, prepared = self.prepare()
        authorize = self.repository._authorize
        calls = 0

        def revoked_before_commit(principal):
            nonlocal calls
            calls += 1
            # Public method, transaction entry, acquired lock, then before commit.
            if calls == 4:
                raise FinanceCollectionError("forbidden")
            authorize(principal)

        with mock.patch.object(self.repository, "_authorize", side_effect=revoked_before_commit):
            with self.assertRaisesRegex(FinanceCollectionError, "forbidden"):
                self.repository.publish_collection(collection.ADMIN, prepared, collection.normalized_usage(value))
        self.assertEqual(calls, 4)
        self.assertEqual(self.count("portfolio_finance_snapshots"), 0)
        self.assertEqual(self.count("portfolio_finance_collection_events"), 0)
        self.assertEqual(self.store.verify_audit_chain(organization_id="acme").sequence, 1)

    def test_tenant_rls_and_append_only_privileges(self):
        self.bind()
        value, prepared = self.prepare()
        self.repository.publish_collection(collection.ADMIN, prepared, collection.normalized_usage(value))
        with self.transaction("beta") as connection:
            for table in TABLE_DDL:
                self.assertEqual(connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"], 0)
        for table in TABLE_DDL:
            for privilege in ("UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                with self.subTest(table=table, privilege=privilege), self.transaction() as connection:
                    self.assertFalse(connection.execute(
                        "SELECT has_table_privilege(current_user, %s, %s) AS allowed",
                        (f'"{self.schema}".{table}', privilege),
                    ).fetchone()["allowed"])
            for statement in (f"DELETE FROM {table}", f"TRUNCATE {table}", f"UPDATE {table} SET organization_id='beta'"):
                with self.subTest(statement=statement), self.assertRaisesRegex(PostgresStorageError, "storage_access_denied"):
                    with self.transaction() as connection:
                        connection.execute(statement)
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM portfolio_finance_source_binding_versions").fetchone()
        with self.assertRaisesRegex(PostgresStorageError, "storage_access_denied"):
            with self.transaction("beta") as connection:
                columns = ",".join(row)
                values = ",".join("%s" for _ in row)
                connection.execute(f"INSERT INTO portfolio_finance_source_binding_versions ({columns}) VALUES ({values})", tuple(row.values()))

    def test_failure_events_keep_null_receipts_and_replay_is_terminal(self):
        self.bind()
        for key in ("failed-one", "failed-two"):
            _, prepared = self.prepare(key)
            self.repository.fail_collection(collection.ADMIN, prepared, reason_code="normalization_failed")
            with self.assertRaisesRegex(FinanceCollectionError, "attempt_terminal"):
                self.prepare(key)
        self.assertEqual(self.count("portfolio_finance_collection_events"), 2)
        self.assertEqual(self.count("portfolio_finance_snapshots"), 0)

    def test_schema16_is_refused_without_mutation(self):
        with self.psycopg.connect(self.owner_dsn) as connection:
            connection.execute(self.sql.SQL("DELETE FROM {}.hormuz_schema_migrations WHERE version=17").format(self.sql.Identifier(self.schema)))
        try:
            with self.assertRaisesRegex(FinanceCollectionError, "unavailable"):
                self.bind()
            self.assertEqual(self.count("portfolio_finance_source_binding_versions"), 0)
        finally:
            with self.psycopg.connect(self.owner_dsn) as connection:
                connection.execute(self.sql.SQL("INSERT INTO {}.hormuz_schema_migrations VALUES (17, 'applied', CURRENT_TIMESTAMP)").format(self.sql.Identifier(self.schema)))
