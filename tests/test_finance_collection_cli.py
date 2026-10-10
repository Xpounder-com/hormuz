"""CLI ordering proofs for authenticated provider finance collection."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.cli import build_parser
from hormuz.commands import finance as finance_commands
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.finance_account_binding import (
    parse_finance_account_bindings,
    parse_finance_identity,
)
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.store import UsageStore

if __package__:
    from ._portfolio_fixture import ADMIN as ADMIN_TOKEN, registry_config
    from ._sqlite import managed_sqlite_connection
    from .test_finance_collection_runtime import (
        ADMIN,
        KEY,
        MIDDLE,
        START,
        openai_bucket,
        openai_cost_metadata_bucket,
        openai_page,
        openai_usage,
    )
else:
    from _portfolio_fixture import ADMIN as ADMIN_TOKEN, registry_config
    from _sqlite import managed_sqlite_connection
    from test_finance_collection_runtime import (
        ADMIN,
        KEY,
        MIDDLE,
        START,
        openai_bucket,
        openai_cost_metadata_bucket,
        openai_page,
        openai_usage,
    )


class FinanceCollectionCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = registry_config(self.root)
        UsageStore(self.config.database_path)
        self.environment = {
            "HORMUZ_PORTFOLIO_TOKEN": ADMIN_TOKEN,
            "HORMUZ_FINANCE_FINGERPRINT_KEY": KEY.decode(),
            "SYNTHETIC_PROVIDER_KEY": "provider-secret-value",
        }
        self.parser = build_parser()

    def invoke(self, argv, *, dependencies=None, environment=None):
        args = self.parser.parse_args(argv)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = finance_commands.run(
                self.config,
                args,
                dependencies,
                environ=self.environment if environment is None else environment,
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def binding_request(self):
        return {
            "schema_id": "hormuz.finance-source-binding-request",
            "schema_version": 1,
            "binding_id": "provider-account",
            "expected_version": None,
            "provider": "openai",
            "provider_account_reference_id": "raw-provider-account",
            "scope": {"kind": "organization", "ids": []},
            "credential_reference_version": 1,
            "fingerprint_key_version": 1,
            "state": "active",
            "reason_code": "created",
        }

    def bind(self):
        return create_finance_collection_repository(self.config).bind_source(
            ADMIN,
            self.binding_request(),
            fingerprint_key=KEY,
        )

    def cost_collect_args(self):
        return ["finance", "collect", "provider-account", "1", "openai.organization-costs.v1",
                START, MIDDLE, "--page-size", "1", "--idempotency-key", "cost-metadata",
                "--fingerprint-key-version", "1"]

    def test_cost_metadata_collect_verifies_bound_account_and_discards_labels(self):
        binding = self.bind()
        fetched = mock.Mock(return_value=(openai_page([openai_cost_metadata_bucket()]),))
        normalized = mock.Mock(wraps=finance_commands.normalize_collection_pages)
        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"},
            fetch_pages=fetched, normalize_pages=normalized,
        )
        status, stdout, stderr = self.invoke(self.cost_collect_args(), dependencies=dependencies)
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("snapshot_id", json.loads(stdout))
        self.assertEqual(normalized.call_args.kwargs["expected_provider_account_fingerprint"],
                         binding.provider_account_fingerprint)
        self.assertEqual(fetched.call_count, 1)
        self.assertEqual(fetched.call_args.args[0].profile.group_by, ("project_id", "line_item", "api_key_id"))
        with managed_sqlite_connection(self.config.database_path) as connection:
            row = connection.execute("SELECT canonical_amount,cost_basis,provider_final,invoice_final "
                                     "FROM portfolio_finance_cost_observations").fetchone()
            self.assertEqual(row, ("1.25", "provider_reported_aggregate", 0, 0))
        database_bytes = self.config.database_path.read_bytes()
        for private in (b"raw-provider-account", b"private-organization-label", b"private-project-label"):
            self.assertNotIn(private, database_bytes)
        repeated = self.invoke(self.cost_collect_args(), dependencies=dependencies)
        self.assertEqual(repeated, (status, stdout, stderr))
        self.assertEqual(fetched.call_count, 1)

    def test_cost_metadata_wrong_account_records_failure_without_snapshot(self):
        self.bind()
        bucket = openai_cost_metadata_bucket()
        bucket["results"][0]["organization_id"] = "wrong-private-provider-account"
        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"},
            fetch_pages=lambda *_args, **_kwargs: (openai_page([bucket]),),
        )
        status, stdout, stderr = self.invoke(self.cost_collect_args(), dependencies=dependencies)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr), {"error": {"code": "provider_response_invalid"}})
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT state,reason_code FROM portfolio_finance_collection_events").fetchone(),
                             ("failed", "normalization_failed"))
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0], 0)

    def test_cost_iso_aliases_collect_to_authoritative_numeric_bounds_and_same_digest(self):
        self.bind()
        cases = ((START[:-1], MIDDLE[:-1]),
                 (START[:-1].replace("T", " "), MIDDLE[:-1].replace("T", " ")),
                 (START[:-1] + ".000000000z", MIDDLE[:-1] + ".000000000+0000"),
                 ("2026-01-01T01:00:00+01:00", "2026-01-01T19:00:00-05:00"),
                 (None, None), ("omitted", "omitted"))
        receipts = []
        for index, (start, end) in enumerate(cases):
            with self.subTest(index=index):
                bucket = openai_cost_metadata_bucket()
                if start == "omitted":
                    del bucket["start_time_iso"], bucket["end_time_iso"]
                else:
                    bucket.update(start_time_iso=start, end_time_iso=end)
                fetched = mock.Mock(return_value=(openai_page([bucket]),))
                dependencies = finance_commands.FinanceCommandDependencies(
                    resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"},
                    fetch_pages=fetched,
                )
                args = self.cost_collect_args()
                args[args.index("--idempotency-key") + 1] = f"alias-{index}"
                outcome = self.invoke(args, dependencies=dependencies)
                self.assertEqual((outcome[0], outcome[2]), (0, ""))
                receipts.append(json.loads(outcome[1]))
                self.assertEqual(self.invoke(args, dependencies=dependencies), outcome)
                fetched.assert_called_once()
        self.assertEqual(len({receipt["content_digest"] for receipt in receipts}), 1)
        with managed_sqlite_connection(self.config.database_path) as connection:
            rows = connection.execute(
                "SELECT bucket_start_at,bucket_end_at,cost_basis,provider_final,invoice_final "
                "FROM portfolio_finance_cost_observations"
            ).fetchall()
            self.assertEqual(rows, [(START, MIDDLE, "provider_reported_aggregate", 0, 0)] * len(cases))
            self.assertEqual(connection.execute(
                "SELECT count(DISTINCT observation_digest) FROM portfolio_finance_cost_observations"
            ).fetchone()[0], 1)
        self.assertNotIn(b"2026-01-01T01:00:00+01:00", self.config.database_path.read_bytes())

    def test_cost_invalid_iso_aliases_fail_before_storage(self):
        self.bind()
        for index, value in enumerate(("2026-01-01T00:00:00.000000001Z", MIDDLE,
                                       "2026-01-01T00:00:00-00:00", "2026-02-30T00:00:00Z", [])):
            with self.subTest(index=index):
                bucket = openai_cost_metadata_bucket()
                bucket["start_time_iso"] = value
                fetched = mock.Mock(return_value=(openai_page([bucket]),))
                dependencies = finance_commands.FinanceCommandDependencies(
                    resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"},
                    fetch_pages=fetched,
                )
                args = self.cost_collect_args()
                args[args.index("--idempotency-key") + 1] = f"invalid-alias-{index}"
                status, stdout, stderr = self.invoke(args, dependencies=dependencies)
                self.assertEqual((status, stdout), (2, ""))
                self.assertEqual(json.loads(stderr), {"error": {"code": "provider_response_invalid"}})
                fetched.assert_called_once()
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_cost_observations").fetchone()[0], 0)

    def test_cost_quantities_collect_exact_units_and_null_unit_to_storage_idempotently(self):
        self.bind()
        units = ("1000_tokens", "duration_seconds", "duration_minutes", "duration_hours", "gibibyte_hours", None)
        quantity = "12345.000000000000123"
        bucket = openai_cost_metadata_bucket(quantity="exact-quantity", quantity_unit=units[0])
        bucket["results"] = [
            {**bucket["results"][0], "quantity_unit": unit} for unit in units
        ]
        page = openai_page([bucket]).replace(b'"exact-quantity"', quantity.encode())
        fetched = mock.Mock(return_value=(page,))
        normalized = []

        def normalize(*args, **kwargs):
            result = finance_commands.normalize_collection_pages(*args, **kwargs)
            normalized.append(result)
            return result

        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"},
            fetch_pages=fetched, normalize_pages=normalize,
        )
        outcome = self.invoke(self.cost_collect_args(), dependencies=dependencies)
        self.assertEqual((outcome[0], outcome[2]), (0, ""))
        receipt = json.loads(outcome[1])
        self.assertEqual(len(normalized), 1)
        collection = normalized[0]
        expected_digests = {item.quantity_unit: item.observation_digest for item in collection.cost_observations}
        self.assertEqual(len(set(expected_digests.values())), len(units))
        with managed_sqlite_connection(self.config.database_path) as connection:
            rows = connection.execute(
                "SELECT native_quantity,quantity_unit,observation_digest,native_amount,currency,"
                "cost_basis,provider_final,invoice_final FROM portfolio_finance_cost_observations"
            ).fetchall()
            self.assertEqual(len(rows), len(units))
            self.assertEqual({row[1] for row in rows}, set(units))
            for row in rows:
                self.assertEqual(row, (quantity, row[1], expected_digests[row[1]], "1.25", "USD",
                                       "provider_reported_aggregate", 0, 0))
            self.assertEqual(connection.execute(
                "SELECT content_digest FROM portfolio_finance_snapshots WHERE snapshot_id=?",
                (receipt["snapshot_id"],),
            ).fetchone()[0], collection.content_digest)
        # Reopen the repository and read its validated typed view, rather than
        # relying only on the SQL insert accepting nullable text columns.
        observed = create_finance_collection_repository(self.config).observations_as_of(
            ADMIN, binding_id="provider-account", binding_version=1,
            collection_profile="openai.organization-costs.v1", start_at=START, end_at=MIDDLE,
        )
        self.assertEqual(len(observed.observations), len(units))
        self.assertEqual(self.invoke(self.cost_collect_args(), dependencies=dependencies), outcome)
        fetched.assert_called_once()
        self.assertEqual(len(normalized), 1)
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_cost_observations").fetchone()[0], len(units))

    def test_cost_quantity_invalid_units_and_types_publish_no_snapshot(self):
        self.bind()
        cases = ((1, "unsupported_unit", "provider_response_invalid"),
                 (1, "DURATION_SECONDS", "provider_response_invalid"),
                 (1, True, "provider_response_invalid"),
                 (1, 17, "provider_response_invalid"),
                 (1, [], "provider_response_invalid"),
                 (1, {}, "provider_response_invalid"),
                 ("1", None, "numeric_domain_invalid"),
                 (True, None, "numeric_domain_invalid"),
                 ({}, "duration_seconds", "numeric_domain_invalid"))
        for index, (quantity, unit, code) in enumerate(cases):
            with self.subTest(index=index):
                page = openai_page([openai_cost_metadata_bucket(quantity=quantity, quantity_unit=unit)])
                fetched = mock.Mock(return_value=(page,))
                dependencies = finance_commands.FinanceCommandDependencies(
                    resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"},
                    fetch_pages=fetched,
                )
                args = self.cost_collect_args()
                args[args.index("--idempotency-key") + 1] = f"invalid-quantity-{index}"
                status, stdout, stderr = self.invoke(args, dependencies=dependencies)
                self.assertEqual((status, stdout), (2, ""))
                self.assertEqual(json.loads(stderr), {"error": {"code": code}})
                fetched.assert_called_once()
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_cost_observations").fetchone()[0], 0)

    def test_cost_metadata_import_uses_the_same_selected_account_context(self):
        binding = self.bind()
        path = self.root / "cost-metadata-bundle.json"
        path.write_text(json.dumps({
            "schema_id": "hormuz.finance-collection-file-bundle", "schema_version": 1,
            "collection_profile": "openai.organization-costs.v1", "query_start_at": START,
            "query_end_at": MIDDLE, "bucket_width": "1d", "requested_page_size": 1,
            "pages": [json.loads(openai_page([openai_cost_metadata_bucket()]))],
        }))
        normalized = mock.Mock(wraps=finance_commands.normalize_collection_file)
        dependencies = finance_commands.FinanceCommandDependencies(normalize_file=normalized)
        status, stdout, stderr = self.invoke(
            ["finance", "import", str(path), "provider-account", "1", "openai.organization-costs.v1", START,
             MIDDLE, "--page-size", "1", "--idempotency-key", "import-metadata",
             "--fingerprint-key-version", "1"], dependencies=dependencies,
        )
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("snapshot_id", json.loads(stdout))
        self.assertEqual(normalized.call_args.kwargs["expected_provider_account_fingerprint"],
                         binding.provider_account_fingerprint)

    def test_cost_metadata_rebinding_during_fetch_prevents_publication(self):
        self.bind()
        def fetch(*_args, **_kwargs):
            request = {**self.binding_request(), "expected_version": 1,
                       "provider_account_reference_id": "new-private-provider-account"}
            create_finance_collection_repository(self.config).bind_source(ADMIN, request, fingerprint_key=KEY)
            return (openai_page([openai_cost_metadata_bucket()]),)
        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=lambda *_args, **_kwargs: {"openai": "synthetic-key"}, fetch_pages=fetch,
        )
        status, stdout, stderr = self.invoke(self.cost_collect_args(), dependencies=dependencies)
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr), {"error": {"code": "binding_inactive"}})
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0], 0)


    def test_unauthorized_source_bind_cannot_open_file_key_or_database(self):
        missing = self.root / "must-not-open.json"
        dependencies = finance_commands.FinanceCommandDependencies(
            create_repository=mock.Mock(side_effect=AssertionError("database opened")),
        )
        status, stdout, stderr = self.invoke(
            ["finance", "source", "bind", str(missing)],
            dependencies=dependencies,
            environment={
                "HORMUZ_PORTFOLIO_TOKEN": "invalid",
                "HORMUZ_FINANCE_FINGERPRINT_KEY": "must-not-read",
            },
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr), {"error": {"code": "unauthenticated"}})
        dependencies.create_repository.assert_not_called()

    def test_source_binding_discards_raw_provider_identifiers(self):
        request = self.root / "binding.json"
        request.write_text(json.dumps(self.binding_request()), encoding="utf-8")
        status, stdout, stderr = self.invoke(
            ["finance", "source", "bind", str(request)]
        )
        self.assertEqual((status, stderr), (0, ""))
        output = json.loads(stdout)
        self.assertEqual(output["binding_id"], "provider-account")
        self.assertNotIn("raw-provider-account", stdout)
        with self.config.database_path.open("rb") as database:
            self.assertNotIn(b"raw-provider-account", database.read())

    def test_account_binding_authorizes_before_file_and_commits_audited_receipt(self):
        source = self.bind()
        upstream = replace(
            self.config.upstreams["openai"],
            base_url="https://api.openai.com/v1",
            finance_identity=parse_finance_identity({
                "upstream_reference_id": "openai-primary",
                "upstream_reference_version": 1,
                "transport_profile": "openai.first-party.v1",
                "inference_credential_reference_id": "inference-primary",
                "inference_credential_reference_version": 2,
            }),
        )
        self.config = replace(
            self.config,
            upstreams={**self.config.upstreams, "openai": upstream},
            finance_account_bindings=parse_finance_account_bindings([{
                "organization_id": "acme",
                "upstream_reference_id": "openai-primary",
                "binding_id": "primary-account",
                "binding_version": 1,
            }]),
        )
        request = self.root / "account-binding.json"
        request.write_text(json.dumps({
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
        }), encoding="utf-8")
        status, stdout, stderr = self.invoke(
            ["finance", "account", "bind", str(request)]
        )
        self.assertEqual((status, stderr), (0, ""))
        receipt = json.loads(stdout)
        self.assertEqual(
            (receipt["schema_id"], receipt["binding_id"], receipt["version"]),
            ("hormuz.finance-account-binding-receipt", "primary-account", 1),
        )
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM gateway_audit_chain_entries "
                    "WHERE source_schema_id='hormuz.finance-account-binding-version'"
                ).fetchone()[0],
                1,
            )

        missing = self.root / "must-not-open-account.json"
        dependencies = finance_commands.FinanceCommandDependencies(
            create_account_repository=mock.Mock(
                side_effect=AssertionError("database opened")
            ),
        )
        status, stdout, stderr = self.invoke(
            ["finance", "account", "bind", str(missing)],
            dependencies=dependencies,
            environment={"HORMUZ_PORTFOLIO_TOKEN": "invalid"},
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(json.loads(stderr), {"error": {"code": "unauthenticated"}})
        dependencies.create_account_repository.assert_not_called()

    def test_import_commits_pending_before_file_and_retry_never_reopens_it(self):
        self.bind()
        bundle = self.root / "bundle.json"
        page = json.loads(
            openai_page([openai_bucket(START, MIDDLE, [openai_usage()])])
        )
        bundle.write_text(
            json.dumps(
                {
                    "schema_id": "hormuz.finance-collection-file-bundle",
                    "schema_version": 1,
                    "collection_profile": "openai.organization-usage-completions.v1",
                    "query_start_at": START,
                    "query_end_at": MIDDLE,
                    "bucket_width": "1d",
                    "requested_page_size": 1,
                    "pages": [page],
                }
            ),
            encoding="utf-8",
        )
        argv = [
            "finance",
            "import",
            str(bundle),
            "provider-account",
            "1",
            "openai.organization-usage-completions.v1",
            START,
            MIDDLE,
            "--page-size",
            "1",
            "--idempotency-key",
            "stable-import",
            "--fingerprint-key-version",
            "1",
        ]
        original_read = finance_commands._read_bounded
        observed_pending = []

        def read_after_prepare(path, maximum):
            with self.config.database_path.open("rb"):
                pass
            with managed_sqlite_connection(self.config.database_path) as connection:
                observed_pending.append(
                    connection.execute(
                        "SELECT count(*) FROM portfolio_finance_collection_attempts"
                    ).fetchone()[0]
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM portfolio_finance_collection_events"
                    ).fetchone()[0],
                    0,
                )
            return original_read(path, maximum)

        with mock.patch.object(finance_commands, "_read_bounded", side_effect=read_after_prepare):
            status, first, stderr = self.invoke(argv)
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(observed_pending, [1])
        bundle.unlink()
        status, retry, stderr = self.invoke(argv)
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(json.loads(first), json.loads(retry))

    def test_collect_resolves_only_selected_credential_after_pending_commit(self):
        self.bind()
        calls = []

        def resolve(config, *, environ, selection_allowed):
            calls.append(
                {
                    provider: selection_allowed(provider)
                    for provider in ("openai", "anthropic")
                }
            )
            return {"openai": environ["SYNTHETIC_PROVIDER_KEY"], "anthropic": ""}

        def fetch(value, *, credential, base_url):
            with managed_sqlite_connection(self.config.database_path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM portfolio_finance_collection_attempts"
                    ).fetchone()[0],
                    1,
                )
            self.assertEqual(credential, "provider-secret-value")
            self.assertEqual(base_url, "https://api.openai.com")
            return (openai_page([openai_bucket(START, MIDDLE, [openai_usage()])]),)

        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=resolve,
            fetch_pages=fetch,
        )
        status, stdout, stderr = self.invoke(
            [
                "finance",
                "collect",
                "provider-account",
                "1",
                "openai.organization-usage-completions.v1",
                START,
                MIDDLE,
                "--page-size",
                "1",
                "--idempotency-key",
                "live-read",
                "--fingerprint-key-version",
                "1",
            ],
            dependencies=dependencies,
        )
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(calls, [{"openai": True, "anthropic": False}])
        self.assertIn("snapshot_id", json.loads(stdout))

    def test_provider_failure_commits_only_content_free_terminal(self):
        self.bind()

        def fail(*_args, **_kwargs):
            from hormuz.finance_collection import FinanceCollectionError

            raise FinanceCollectionError("provider_rate_limited")

        dependencies = finance_commands.FinanceCommandDependencies(fetch_pages=fail)
        status, stdout, stderr = self.invoke(
            [
                "finance",
                "collect",
                "provider-account",
                "1",
                "openai.organization-usage-completions.v1",
                START,
                MIDDLE,
                "--page-size",
                "1",
                "--idempotency-key",
                "failed-live-read",
                "--fingerprint-key-version",
                "1",
            ],
            dependencies=dependencies,
        )
        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(
            json.loads(stderr), {"error": {"code": "provider_rate_limited"}}
        )
        with managed_sqlite_connection(self.config.database_path) as connection:
            event = connection.execute(
                "SELECT state,reason_code,receipt_id,snapshot_id,evidence_json "
                "FROM portfolio_finance_collection_events"
            ).fetchone()
            self.assertEqual(event[:4], ("failed", "provider_rate_limited", None, None))
            self.assertNotIn("provider-secret-value", event[4])
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM portfolio_finance_snapshots"
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main()
