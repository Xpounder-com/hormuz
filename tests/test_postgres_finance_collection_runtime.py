"""Provider-free finance collection proofs using the restricted PostgreSQL role."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.config import UsageStorageConfig
from hormuz.finance_account_binding import (
    parse_finance_account_bindings,
    parse_finance_identity,
    select_finance_account,
)
from hormuz.finance_account_registration_store import (
    create_finance_account_registration_repository,
)
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.finance_collection import CollectionQuery, FinanceCollectionError, normalize_collection_pages
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.postgres import PostgresStorageError, postgres_transaction
from hormuz._finance_collection_schema import TABLE_DDL
from hormuz._finance_account_binding_schema import (
    QUERY_AUDIT_TABLE,
    TABLE_DDL as FINANCE_ACCOUNT_TABLE_DDL,
)
from hormuz.finance_account_evidence import QUERY_AUDIT_SCHEMA_ID
from hormuz.cli import build_parser
from hormuz.commands import finance as finance_commands
from hormuz.store import ReservationScope

if __package__:
    from ._postgres_fixture import PostgresTestCase
    from ._portfolio_fixture import registry_config
    from . import test_finance_collection_runtime as collection
    from .test_finance_attempt_runtime import (
        begin,
        binding as rate_card,
        complete_estimate,
        complete_observation,
        identity as runtime_identity,
    )
else:
    from _postgres_fixture import PostgresTestCase
    from _portfolio_fixture import registry_config
    import test_finance_collection_runtime as collection
    from test_finance_attempt_runtime import (
        begin,
        binding as rate_card,
        complete_estimate,
        complete_observation,
        identity as runtime_identity,
    )


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
    test_as_of_selection_replays_tenant_high_water_after_empty_refresh = collection.FinanceCollectionSQLiteRepositoryTests.test_as_of_selection_replays_tenant_high_water_after_empty_refresh
    test_as_of_selection_rejects_invalid_or_future_cutoffs_and_unauthorized_reads = collection.FinanceCollectionSQLiteRepositoryTests.test_as_of_selection_rejects_invalid_or_future_cutoffs_and_unauthorized_reads
    test_current_cost_observations_expose_boolean_finality = collection.FinanceCollectionSQLiteRepositoryTests.test_current_cost_observations_expose_boolean_finality
    test_binding_and_role_revocation_races_prevent_publication = collection.FinanceCollectionSQLiteRepositoryTests.test_binding_and_role_revocation_races_prevent_publication

    def transaction(self, organization="acme"):
        return postgres_transaction(
            self.runtime_dsn, schema=self.schema,
            runtime_role=self.runtime_role, organization_id=organization,
        )

    def count(self, table):
        self.assertIn(table, {**TABLE_DDL, **FINANCE_ACCOUNT_TABLE_DDL})
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

    def test_admin_report_reads_cost_and_terminal_sidecar_through_restricted_role(self):
        self.bind(binding_id="source-a")
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        start = (midnight - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        middle = midnight.isoformat().replace("+00:00", "Z")
        end = (midnight + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        profile = "openai.organization-costs.v1"
        query = CollectionQuery("acme", "source-a", 1, profile, start, middle, "1d", 1)
        normalized = normalize_collection_pages(
            query,
            (collection.openai_page([
                collection.openai_bucket(start, middle, [collection.openai_cost()]),
            ]),),
            fingerprint_key=collection.KEY,
            fingerprint_key_version=1,
        )
        prepared = self.repository.prepare_collection(
            collection.ADMIN, query, idempotency_key="report-cost", evidence_origin="customer_file",
        )
        receipt = self.repository.publish_collection(collection.ADMIN, prepared, normalized)
        attempt = begin(self.store)
        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=2,
            cost_microusd=35,
            provider_metrics=None,
            finance_observation=complete_observation(),
            configured_estimate=complete_estimate(),
        )
        args = build_parser().parse_args([
            "finance", "report", "source-a", "1", profile, start, end,
            "--currency", "USD", "--as-of-commit-sequence", str(receipt.commit_sequence),
        ])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = finance_commands.run(
                self.config, args,
                environ={
                    "HORMUZ_PORTFOLIO_TOKEN": "synthetic-registry-admin-token",
                    "HORMUZ_POSTGRES_DSN": self.runtime_dsn,
                },
            )
        self.assertEqual((status, stderr.getvalue()), (0, ""))
        report = json.loads(stdout.getvalue())
        self.assertIsInstance(report["query_audit_event_id"], str)
        self.assertEqual(report["preview"]["provider_cost"]["known_subtotal"], "1.25")
        self.assertEqual(report["preview"]["gateway_estimate"]["known_subtotal"], "0.000035")
        self.assertEqual(report["preview"]["gateway_estimate"]["attempt_count"], 1)
        self.assertEqual(report["terminal_attempts_missing_sidecar_count"], 0)
        self.assertEqual(report["selected_snapshot_provenance"], [{
            "snapshot_id": receipt.snapshot_id,
            "evidence_origin": "customer_file",
            "scope_provenance": "customer_supplied_scope_unverified",
        }])
        self.assertIsNone(report["preview"]["signed_variance"])
        self.assertEqual(self.count(QUERY_AUDIT_TABLE), 1)
        with self.transaction() as connection:
            query = connection.execute(
                f"SELECT evidence_json FROM {QUERY_AUDIT_TABLE} "
                "WHERE organization_id=%s AND query_event_id=%s",
                ("acme", report["query_audit_event_id"]),
            ).fetchone()
            chain = connection.execute(
                "SELECT event_json FROM gateway_audit_chain_entries "
                "WHERE organization_id=%s AND source_schema_id=%s "
                "AND source_schema_version=1 AND source_event_id=%s",
                ("acme", QUERY_AUDIT_SCHEMA_ID, report["query_audit_event_id"]),
            ).fetchone()
        self.assertEqual(query["evidence_json"], chain["event_json"])
        event = json.loads(query["evidence_json"])
        self.assertEqual(event["actor_id"], "alice")
        self.assertEqual(event["selected_snapshot_count"], 1)
        self.assertEqual(event["coverage_bucket_count"], 1)
        self.assertEqual(event["provider_observation_count"], 1)
        self.assertEqual(event["terminal_attempt_count"], 1)
        self.assertEqual(event["terminal_attempts_missing_sidecar_count"], 0)

    def test_account_reconciliation_matches_bound_attempt_through_restricted_role(self):
        finance_identity = parse_finance_identity({
            "upstream_reference_id": "openai-primary",
            "upstream_reference_version": 1,
            "transport_profile": "openai.first-party.v1",
            "inference_credential_reference_id": "inference-primary",
            "inference_credential_reference_version": 2,
        })
        upstream = replace(
            self.config.upstreams["openai"],
            base_url="https://api.openai.com/v1",
            finance_identity=finance_identity,
        )
        finance_config = replace(
            self.config,
            upstreams={**self.config.upstreams, "openai": upstream},
            finance_account_bindings=parse_finance_account_bindings([{
                "organization_id": "acme",
                "upstream_reference_id": "openai-primary",
                "binding_id": "primary-account",
                "binding_version": 1,
            }]),
        )
        repository = create_finance_collection_repository(
            finance_config,
            environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        source = repository.bind_source(
            collection.ADMIN,
            {
                "schema_id": "hormuz.finance-source-binding-request",
                "schema_version": 1,
                "binding_id": "source-primary",
                "expected_version": None,
                "provider": "openai",
                "provider_account_reference_id": "raw-provider-account",
                "scope": {"kind": "organization", "ids": []},
                "credential_reference_version": 1,
                "fingerprint_key_version": 1,
                "state": "active",
                "reason_code": "created",
            },
            fingerprint_key=collection.KEY,
        )
        registration = create_finance_account_registration_repository(
            finance_config,
            environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        registration.register(
            collection.ADMIN,
            json.dumps({
                "schema_id": "hormuz.finance-account-binding-request",
                "schema_version": 1,
                "binding_id": "primary-account",
                "expected_version": None,
                "upstream_reference_id": "openai-primary",
                "upstream_reference_version": 1,
                "transport_profile": "openai.first-party.v1",
                "inference_credential_reference_id": "inference-primary",
                "inference_credential_reference_version": 2,
                "source_binding": {
                    "binding_id": source.binding_id,
                    "version": source.version,
                    "content_digest": source.content_digest,
                },
                "state": "active",
                "reason_code": "created",
            }).encode(),
        )
        selected = select_finance_account(
            organization_id="acme",
            protocol="openai",
            base_url="https://api.openai.com/v1",
            identity=finance_identity,
            bindings=finance_config.finance_account_bindings,
        )
        midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        start = midnight.isoformat().replace("+00:00", "Z")
        end = (midnight + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        profile = "openai.organization-costs.v1"
        query = CollectionQuery(
            "acme", source.binding_id, source.version, profile, start, end, "1d", 1,
        )
        normalized = normalize_collection_pages(
            query,
            (collection.openai_page([
                collection.openai_bucket(start, end, [collection.openai_cost()]),
            ]),),
            fingerprint_key=collection.KEY,
            fingerprint_key_version=1,
        )
        prepared = repository.prepare_collection(
            collection.ADMIN,
            query,
            idempotency_key="account-reconciliation-cost",
            evidence_origin="customer_file",
        )
        repository.publish_collection(collection.ADMIN, prepared, normalized)
        attempt = self.store._begin_request_attempt_with_work_budget(
            identity=runtime_identity(),
            client="codex",
            protocol="openai",
            requested_model="smart",
            resolved_alias="smart",
            upstream_model="gpt-test",
            policy_version="policy-1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(ReservationScope(name="organization"),),
            reserved_tokens=100,
            reserved_cost_microusd=500,
            ttl_seconds=60,
            work_budget=None,
            configured_rate_card=rate_card(),
            finance_account=selected,
        )
        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status="succeeded",
            input_tokens=10,
            output_tokens=4,
            cache_read_tokens=2,
            cost_microusd=35,
            provider_metrics=None,
            finance_observation=complete_observation(),
            configured_estimate=complete_estimate(),
        )
        preview, missing, _, _, reconciliation = (
            repository.account_reconciliation_report_evidence(
                collection.ADMIN,
                account_binding_id="primary-account",
                account_binding_version=1,
                collection_profile=profile,
                start_at=start,
                end_at=end,
                currency="USD",
            )
        )
        self.assertEqual((missing, preview.gateway_estimate.attempt_count), (0, 1))
        self.assertEqual(preview.gateway_estimate.account_binding_state, "matched")
        self.assertEqual(reconciliation["matched_terminal_attempt_count"], 1)
        self.assertEqual(reconciliation["signed_variance"], "1.249965")
        self.assertEqual(reconciliation["variance_state"], "comparable_operator_attested")

        self.store._begin_request_attempt_with_work_budget(
            identity=runtime_identity(),
            client="codex",
            protocol="openai",
            requested_model="smart",
            resolved_alias="smart",
            upstream_model="gpt-test",
            policy_version="policy-1",
            policy_action="allowed",
            redaction_count=0,
            redaction_rules=(),
            scopes=(ReservationScope(name="organization"),),
            reserved_tokens=100,
            reserved_cost_microusd=500,
            ttl_seconds=60,
            work_budget=None,
            configured_rate_card=rate_card(),
            finance_account=selected,
        )
        preview, missing, _, _, reconciliation = (
            repository.account_reconciliation_report_evidence(
                collection.ADMIN,
                account_binding_id="primary-account",
                account_binding_version=1,
                collection_profile=profile,
                start_at=start,
                end_at=end,
                currency="USD",
            )
        )
        self.assertEqual((missing, preview.gateway_estimate.attempt_count), (0, 1))
        self.assertEqual(reconciliation["pending_account_gap_count"], 1)
        self.assertIsNone(reconciliation["signed_variance"])
        self.assertEqual(
            reconciliation["variance_state"],
            "account_or_gateway_evidence_incomplete",
        )
        self.assertEqual(self.count(QUERY_AUDIT_TABLE), 2)

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
