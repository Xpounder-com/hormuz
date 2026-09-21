"""Durable, provider-free finance report reads through the administrator CLI."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.cli import build_parser
from hormuz.commands import finance as finance_commands
from hormuz.finance_collection import CollectionQuery, normalize_collection_pages
from hormuz.finance_collection_repository import create_finance_collection_repository
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.store import UsageStore

if __package__:
    from ._sqlite import managed_sqlite_connection
    from ._portfolio_fixture import ADMIN as ADMIN_TOKEN, OTHER as OTHER_TOKEN, VIEWER as VIEWER_TOKEN, registry_config
    from .test_finance_attempt_runtime import begin, complete_estimate, complete_observation
    from .test_finance_collection_runtime import KEY, openai_bucket, openai_cost, openai_page
else:
    from _sqlite import managed_sqlite_connection
    from _portfolio_fixture import ADMIN as ADMIN_TOKEN, OTHER as OTHER_TOKEN, VIEWER as VIEWER_TOKEN, registry_config
    from test_finance_attempt_runtime import begin, complete_estimate, complete_observation
    from test_finance_collection_runtime import KEY, openai_bucket, openai_cost, openai_page


ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
PROFILE = "openai.organization-costs.v1"


class FinanceCoverageReportCLITests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        self.store = UsageStore(self.config.database_path)
        self.repository = create_finance_collection_repository(self.config)
        self.parser = build_parser()
        start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self.start = (start - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        self.middle = start.isoformat().replace("+00:00", "Z")
        self.end = (start + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        self.environment = {"HORMUZ_PORTFOLIO_TOKEN": ADMIN_TOKEN}

    def invoke(self, *extra: str, environment=None, dependencies=None):
        args = self.parser.parse_args([
            "finance", "report", "source-a", "1", PROFILE, self.start, self.end,
            "--currency", "USD", *extra,
        ])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = finance_commands.run(
                self.config, args, dependencies,
                environ=self.environment if environment is None else environment,
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def seed_cost(self, *, idempotency_key: str, records: list[dict]):
        if idempotency_key == "first":
            self.repository.bind_source(
                ADMIN,
                {
                    "schema_id": "hormuz.finance-source-binding-request",
                    "schema_version": 1,
                    "binding_id": "source-a",
                    "expected_version": None,
                    "provider": "openai",
                    "provider_account_reference_id": "sensitive-provider-account",
                    "scope": {"kind": "organization", "ids": []},
                    "credential_reference_version": 1,
                    "fingerprint_key_version": 1,
                    "state": "active",
                    "reason_code": "created",
                },
                fingerprint_key=KEY,
            )
        query = CollectionQuery(
            "acme", "source-a", 1, PROFILE, self.start, self.middle, "1d", 1,
        )
        collection = normalize_collection_pages(
            query,
            (openai_page([openai_bucket(self.start, self.middle, records)]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        prepared = self.repository.prepare_collection(
            ADMIN, query, idempotency_key=idempotency_key,
            evidence_origin="customer_file",
        )
        return self.repository.publish_collection(ADMIN, prepared, collection)

    def seed_attempt(self, *, status="succeeded"):
        attempt = begin(self.store)
        self.store._finalize_request_attempt_with_provider_metrics(
            attempt=attempt,
            organization_id="acme",
            status=status,
            input_tokens=10 if status == "succeeded" else 0,
            output_tokens=4 if status == "succeeded" else 0,
            cache_read_tokens=2 if status == "succeeded" else 0,
            cost_microusd=35 if status == "succeeded" else 0,
            provider_metrics=None,
            finance_observation=complete_observation() if status == "succeeded" else None,
            configured_estimate=complete_estimate() if status == "succeeded" else None,
        )

    def test_report_reads_durable_subtotals_without_provider_or_secret_access(self):
        receipt = self.seed_cost(idempotency_key="first", records=[openai_cost()])
        self.seed_attempt()
        self.seed_attempt(status="failed")
        with managed_sqlite_connection(self.config.database_path) as connection:
            before = (
                connection.execute("SELECT count(*) FROM gateway_audit_chain_entries").fetchone()[0],
                connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0],
                connection.execute("SELECT count(*) FROM gateway_finance_attempt_evidence").fetchone()[0],
            )
        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=mock.Mock(side_effect=AssertionError("provider credentials opened")),
            fetch_pages=mock.Mock(side_effect=AssertionError("provider called")),
        )
        status, stdout, stderr = self.invoke(dependencies=dependencies)
        self.assertEqual((status, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual((report["schema_id"], report["reader_role"]),
                         ("hormuz.finance-coverage-report", "portfolio_admin"))
        self.assertEqual(report["terminal_attempts_missing_sidecar_count"], 0)
        self.assertEqual(report["selected_snapshot_provenance"], [{
            "snapshot_id": receipt.snapshot_id,
            "evidence_origin": "customer_file",
            "scope_provenance": "customer_supplied_scope_unverified",
        }])
        preview = report["preview"]
        self.assertEqual(preview["as_of_commit_sequence"], receipt.commit_sequence)
        self.assertEqual(preview["provider_cost"]["known_subtotal"], "1.25")
        self.assertEqual(preview["gateway_estimate"]["known_subtotal"], "0.000035")
        self.assertEqual(preview["gateway_estimate"]["attempt_count"], 2)
        self.assertEqual(preview["gateway_estimate"]["failed_attempt_count"], 1)
        self.assertEqual(preview["gateway_estimate"]["unpriced_attempt_count"], 1)
        self.assertEqual(preview["gateway_estimate"]["rate_card_identities"][0][0],
                         "gateway-route-test")
        self.assertIsNone(preview["signed_variance"])
        self.assertEqual(preview["variance_state"], "account_and_period_not_comparable")
        self.assertNotIn("sensitive-provider-account", stdout)
        self.assertNotIn("native_payload_json", stdout)
        dependencies.resolve_credentials.assert_not_called()
        dependencies.fetch_pages.assert_not_called()
        with managed_sqlite_connection(self.config.database_path) as connection:
            after = (
                connection.execute("SELECT count(*) FROM gateway_audit_chain_entries").fetchone()[0],
                connection.execute("SELECT count(*) FROM portfolio_finance_snapshots").fetchone()[0],
                connection.execute("SELECT count(*) FROM gateway_finance_attempt_evidence").fetchone()[0],
            )
        self.assertEqual(after, before)

    def test_explicit_cutoff_replays_prior_cost_after_empty_refresh(self):
        first = self.seed_cost(idempotency_key="first", records=[openai_cost()])
        second = self.seed_cost(idempotency_key="empty-refresh", records=[])
        self.assertGreater(second.commit_sequence, first.commit_sequence)
        old = json.loads(self.invoke("--as-of-commit-sequence", str(first.commit_sequence))[1])
        latest = json.loads(self.invoke()[1])
        self.assertEqual(old["preview"]["provider_cost"]["known_subtotal"], "1.25")
        self.assertIsNone(latest["preview"]["provider_cost"]["known_subtotal"])
        self.assertEqual(latest["preview"]["provider_cost"]["empty_bucket_count"], 1)
        self.assertEqual(old["preview"]["as_of_commit_sequence"], first.commit_sequence)
        self.assertEqual(latest["preview"]["as_of_commit_sequence"], second.commit_sequence)

    def test_collection_cutoff_does_not_claim_a_gateway_attempt_cutoff(self):
        first = self.seed_cost(idempotency_key="first", records=[openai_cost()])
        option = ("--as-of-commit-sequence", str(first.commit_sequence))
        before = json.loads(self.invoke(*option)[1])["preview"]
        self.seed_attempt(status="failed")
        after = json.loads(self.invoke(*option)[1])["preview"]
        self.assertEqual(before["as_of_commit_sequence"], after["as_of_commit_sequence"])
        self.assertEqual(before["provider_cost"], after["provider_cost"])
        self.assertEqual(before["gateway_estimate"]["attempt_count"], 0)
        self.assertEqual(after["gateway_estimate"]["attempt_count"], 1)

    def test_denied_token_or_viewer_cannot_open_storage(self):
        dependencies = finance_commands.FinanceCommandDependencies(
            create_repository=mock.Mock(side_effect=AssertionError("database opened")),
        )
        for token, code in (("bad", "unauthenticated"), (VIEWER_TOKEN, "forbidden")):
            with self.subTest(code=code):
                status, stdout, stderr = self.invoke(
                    environment={"HORMUZ_PORTFOLIO_TOKEN": token},
                    dependencies=dependencies,
                )
                self.assertEqual(status, 2)
                self.assertEqual(stdout, "")
                self.assertEqual(json.loads(stderr), {"error": {"code": code}})
        dependencies.create_repository.assert_not_called()

    def test_other_tenant_admin_cannot_read_cost_or_attempts(self):
        self.seed_cost(idempotency_key="first", records=[openai_cost()])
        self.seed_attempt()
        status, stdout, stderr = self.invoke(
            environment={"HORMUZ_PORTFOLIO_TOKEN": OTHER_TOKEN},
        )
        self.assertEqual((status, stderr), (0, ""))
        preview = json.loads(stdout)["preview"]
        self.assertEqual(preview["organization_id"], "beta")
        self.assertIsNone(preview["provider_cost"]["known_subtotal"])
        self.assertIsNone(preview["gateway_estimate"]["known_subtotal"])
        self.assertEqual(preview["gateway_estimate"]["attempt_count"], 0)

    def test_invalid_window_and_cutoff_fail_without_provider_access(self):
        for option in (("--as-of-commit-sequence", "-1"),):
            status, stdout, stderr = self.invoke(*option)
            self.assertEqual((status, stdout), (2, ""))
            self.assertEqual(json.loads(stderr), {"error": {"code": "invalid_request"}})
        args = self.parser.parse_args([
            "finance", "report", "source-a", "1", PROFILE,
            self.start.replace("00:00:00Z", "12:00:00Z"), self.end,
            "--currency", "USD",
        ])
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(finance_commands.run(self.config, args, environ=self.environment), 2)
        args = self.parser.parse_args([
            "finance", "report", "source-a", "1", PROFILE,
            self.start, self.end, "--currency", "usd",
        ])
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(finance_commands.run(self.config, args, environ=self.environment), 2)
        self.assertEqual(json.loads(stderr.getvalue()), {"error": {"code": "invalid_request"}})


if __name__ == "__main__":
    unittest.main()
