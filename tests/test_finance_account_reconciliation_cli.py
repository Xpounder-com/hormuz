"""Account-bound, provider-free finance reconciliation through the admin CLI."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz._finance_account_binding_schema import (
    ATTEMPT_BINDING_TABLE,
    QUERY_AUDIT_TABLE,
)
from hormuz.cli import build_parser
from hormuz.commands import finance as finance_commands
from hormuz.finance_account_binding import (
    UnavailableFinance,
    parse_finance_account_bindings,
    parse_finance_identity,
    select_finance_account,
)
from hormuz.finance_account_registration_store import (
    create_finance_account_registration_repository,
)
from hormuz.finance_collection import (
    CollectionQuery,
    FinanceCollectionError,
    normalize_collection_pages,
)
from hormuz.finance_collection_repository import (
    _attempt_lifetime_within_period,
    create_finance_collection_repository,
)
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.store import ReservationScope, UsageStore

if __package__:
    from ._portfolio_fixture import (
        ADMIN as ADMIN_TOKEN,
        VIEWER as VIEWER_TOKEN,
        registry_config,
    )
    from ._sqlite import managed_sqlite_connection
    from .test_finance_attempt_runtime import (
        binding as rate_card,
        complete_estimate,
        complete_observation,
        identity as runtime_identity,
    )
    from .test_finance_collection_runtime import KEY, openai_bucket, openai_cost, openai_page
else:
    from _portfolio_fixture import ADMIN as ADMIN_TOKEN, VIEWER as VIEWER_TOKEN, registry_config
    from _sqlite import managed_sqlite_connection
    from test_finance_attempt_runtime import (
        binding as rate_card,
        complete_estimate,
        complete_observation,
        identity as runtime_identity,
    )
    from test_finance_collection_runtime import KEY, openai_bucket, openai_cost, openai_page


ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
PROFILE = "openai.organization-costs.v1"


class FinanceAccountReconciliationCLITests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        base = registry_config(self.root)
        self.primary_identity = self._finance_identity("primary")
        upstream = replace(
            base.upstreams["openai"],
            base_url="https://api.openai.com/v1",
            finance_identity=self.primary_identity,
        )
        self.config = replace(
            base,
            upstreams={**base.upstreams, "openai": upstream},
            finance_account_bindings=parse_finance_account_bindings(
                [self._binding_config("primary-account", "openai-primary")]
            ),
        )
        self.store = UsageStore(self.config.database_path)
        self.repository = create_finance_collection_repository(self.config)
        self.parser = build_parser()
        start = datetime.now(timezone.utc).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        self.start = start.isoformat().replace("+00:00", "Z")
        self.end = (start + timedelta(days=1)).isoformat().replace("+00:00", "Z")
        self.environment = {"HORMUZ_PORTFOLIO_TOKEN": ADMIN_TOKEN}
        self.primary_source = self._bind_source(
            "source-primary",
            "raw-primary-provider-account",
        )
        self.primary_account = self._register_account(
            config=self.config,
            binding_id="primary-account",
            source=self.primary_source,
            finance_identity=self.primary_identity,
        )

    @staticmethod
    def _finance_identity(name: str):
        return parse_finance_identity({
            "upstream_reference_id": f"openai-{name}",
            "upstream_reference_version": 1,
            "transport_profile": "openai.first-party.v1",
            "inference_credential_reference_id": f"inference-{name}",
            "inference_credential_reference_version": 2,
        })

    @staticmethod
    def _binding_config(binding_id: str, upstream_reference_id: str):
        return {
            "organization_id": "acme",
            "upstream_reference_id": upstream_reference_id,
            "binding_id": binding_id,
            "binding_version": 1,
        }

    def _bind_source(self, binding_id: str, provider_account: str):
        return self.repository.bind_source(
            ADMIN,
            {
                "schema_id": "hormuz.finance-source-binding-request",
                "schema_version": 1,
                "binding_id": binding_id,
                "expected_version": None,
                "provider": "openai",
                "provider_account_reference_id": provider_account,
                "scope": {"kind": "organization", "ids": []},
                "credential_reference_version": 1,
                "fingerprint_key_version": 1,
                "state": "active",
                "reason_code": "created",
            },
            fingerprint_key=KEY,
        )

    def _register_account(self, *, config, binding_id, source, finance_identity):
        payload = json.dumps({
            "schema_id": "hormuz.finance-account-binding-request",
            "schema_version": 1,
            "binding_id": binding_id,
            "expected_version": None,
            "upstream_reference_id": finance_identity.upstream_reference_id,
            "upstream_reference_version": finance_identity.upstream_reference_version,
            "transport_profile": finance_identity.transport_profile,
            "inference_credential_reference_id": (
                finance_identity.inference_credential_reference_id
            ),
            "inference_credential_reference_version": (
                finance_identity.inference_credential_reference_version
            ),
            "source_binding": {
                "binding_id": source.binding_id,
                "version": source.version,
                "content_digest": source.content_digest,
            },
            "state": "active",
            "reason_code": "created",
        }).encode()
        repository = create_finance_account_registration_repository(config)
        repository.register(ADMIN, payload)
        candidate = select_finance_account(
            organization_id="acme",
            protocol="openai",
            base_url="https://api.openai.com/v1",
            identity=finance_identity,
            bindings=config.finance_account_bindings,
        )
        self.assertNotIsInstance(candidate, UnavailableFinance)
        return candidate

    def _seed_cost(self):
        query = CollectionQuery(
            "acme",
            self.primary_source.binding_id,
            self.primary_source.version,
            PROFILE,
            self.start,
            self.end,
            "1d",
            1,
        )
        collection = normalize_collection_pages(
            query,
            (openai_page([openai_bucket(self.start, self.end, [openai_cost()])]),),
            fingerprint_key=KEY,
            fingerprint_key_version=1,
        )
        prepared = self.repository.prepare_collection(
            ADMIN,
            query,
            idempotency_key="primary-cost",
            evidence_origin="customer_file",
        )
        return self.repository.publish_collection(ADMIN, prepared, collection)

    def _seed_attempt(
        self,
        finance_account,
        *,
        historical_without_binding=False,
        created_at=None,
        terminal_at=None,
        finalize=True,
    ):
        def begin_attempt():
            return self.store._begin_request_attempt_with_work_budget(
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
                finance_account=finance_account,
            )

        original_schema_version = self.store.schema_version
        if historical_without_binding:
            self.store.schema_version = 12
        try:
            if created_at is None:
                attempt = begin_attempt()
            else:
                with mock.patch("hormuz.store.datetime", wraps=datetime) as clock:
                    clock.now.return_value = created_at
                    attempt = begin_attempt()
        finally:
            self.store.schema_version = original_schema_version

        if not finalize:
            return attempt

        def finalize_attempt():
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

        if terminal_at is None:
            finalize_attempt()
        else:
            with mock.patch("hormuz.store.datetime", wraps=datetime) as clock:
                clock.now.return_value = terminal_at
                finalize_attempt()
        return attempt

    def invoke(self, *, environment=None, dependencies=None, output=None):
        args = self.parser.parse_args([
            "finance",
            "reconcile",
            "primary-account",
            "1",
            PROFILE,
            self.start,
            self.end,
            "--currency",
            "USD",
        ])
        stdout = io.StringIO() if output is None else output
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = finance_commands.run(
                self.config,
                args,
                dependencies,
                environ=self.environment if environment is None else environment,
            )
        return status, stdout.getvalue(), stderr.getvalue()

    def test_exact_binding_calculates_signed_variance_without_provider_access(self):
        receipt = self._seed_cost()
        self._seed_attempt(self.primary_account)
        dependencies = finance_commands.FinanceCommandDependencies(
            resolve_credentials=mock.Mock(
                side_effect=AssertionError("provider credentials opened")
            ),
            fetch_pages=mock.Mock(side_effect=AssertionError("provider called")),
        )
        status, stdout, stderr = self.invoke(dependencies=dependencies)
        self.assertEqual((status, stderr), (0, ""))
        report = json.loads(stdout)
        self.assertEqual(
            (report["schema_id"], report["reader_role"]),
            ("hormuz.finance-account-reconciliation-report", "portfolio_admin"),
        )
        self.assertEqual(
            report["selected_snapshot_provenance"],
            [{
                "snapshot_id": receipt.snapshot_id,
                "evidence_origin": "customer_file",
                "scope_provenance": "customer_supplied_scope_unverified",
            }],
        )
        self.assertEqual(report["preview"]["gateway_estimate"]["attempt_count"], 1)
        self.assertEqual(
            report["preview"]["gateway_estimate"]["account_binding_state"],
            "matched",
        )
        reconciliation = report["account_reconciliation"]
        self.assertEqual(reconciliation["matching_basis"], "operator_attested_unverified")
        self.assertEqual(reconciliation["matched_terminal_attempt_count"], 1)
        self.assertEqual(reconciliation["matched_finance_attempt_count"], 1)
        self.assertEqual(reconciliation["pending_account_gap_count"], 0)
        self.assertEqual(reconciliation["variance_state"], "comparable_operator_attested")
        self.assertEqual(reconciliation["provider_total"], "1.25")
        self.assertEqual(reconciliation["gateway_estimate_known_subtotal"], "0.000035")
        self.assertEqual(reconciliation["signed_variance"], "1.249965")
        self.assertEqual(reconciliation["absolute_variance"], "1.249965")
        self.assertEqual(
            reconciliation["relative_variance"],
            {"numerator": "1.249965", "denominator": "0.000035"},
        )
        self.assertEqual(reconciliation["bypass_state"], "unknown")
        self.assertFalse(reconciliation["provider_final"])
        self.assertFalse(reconciliation["invoice_final"])
        self.assertNotIn("raw-primary-provider-account", stdout)
        dependencies.resolve_credentials.assert_not_called()
        dependencies.fetch_pages.assert_not_called()
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    f"SELECT COUNT(*) FROM {QUERY_AUDIT_TABLE}"
                ).fetchone()[0],
                1,
            )

    def test_unbound_and_historical_attempts_make_variance_unavailable(self):
        self._seed_cost()
        self._seed_attempt(UnavailableFinance("not_configured"))
        self._seed_attempt(self.primary_account, historical_without_binding=True)
        status, stdout, stderr = self.invoke()
        self.assertEqual((status, stderr), (0, ""))
        report = json.loads(stdout)
        reconciliation = report["account_reconciliation"]
        self.assertEqual(report["preview"]["gateway_estimate"]["attempt_count"], 0)
        self.assertEqual(reconciliation["matched_terminal_attempt_count"], 0)
        self.assertEqual(reconciliation["unbound_terminal_attempt_count"], 1)
        self.assertEqual(reconciliation["unbound_reason_counts"], {"not_configured": 1})
        self.assertEqual(reconciliation["historical_missing_account_binding_count"], 1)
        self.assertEqual(
            reconciliation["variance_state"],
            "account_or_gateway_evidence_incomplete",
        )
        self.assertIsNone(reconciliation["signed_variance"])

    def test_other_account_attempt_is_excluded_without_cross_matching(self):
        self._seed_cost()
        other_source = self._bind_source(
            "source-other",
            "raw-other-provider-account",
        )
        other_identity = self._finance_identity("secondary")
        other_upstream = replace(
            self.config.upstreams["openai"],
            finance_identity=other_identity,
        )
        other_config = replace(
            self.config,
            upstreams={**self.config.upstreams, "openai": other_upstream},
            finance_account_bindings=parse_finance_account_bindings(
                [self._binding_config("other-account", "openai-secondary")]
            ),
        )
        other_account = self._register_account(
            config=other_config,
            binding_id="other-account",
            source=other_source,
            finance_identity=other_identity,
        )
        self._seed_attempt(other_account)
        self._seed_attempt(other_account, finalize=False)
        status, stdout, stderr = self.invoke()
        self.assertEqual((status, stderr), (0, ""))
        report = json.loads(stdout)
        reconciliation = report["account_reconciliation"]
        self.assertEqual(report["preview"]["gateway_estimate"]["attempt_count"], 0)
        self.assertEqual(reconciliation["matched_terminal_attempt_count"], 0)
        self.assertEqual(reconciliation["other_account_attempt_count"], 1)
        self.assertEqual(reconciliation["other_account_pending_attempt_count"], 1)
        self.assertEqual(reconciliation["same_account_other_binding_count"], 0)
        self.assertEqual(reconciliation["variance_state"], "comparable_operator_attested")
        self.assertEqual(reconciliation["signed_variance"], "1.25")

    def test_boundary_crossing_attempt_is_a_gap_outside_base_query_count(self):
        self._seed_cost()
        boundary = datetime.fromisoformat(self.end.replace("Z", "+00:00"))
        self._seed_attempt(
            self.primary_account,
            created_at=boundary - timedelta(seconds=1),
            terminal_at=boundary + timedelta(seconds=1),
        )
        status, stdout, stderr = self.invoke()
        self.assertEqual((status, stderr), (0, ""))
        report = json.loads(stdout)
        reconciliation = report["account_reconciliation"]
        self.assertEqual(reconciliation["matched_terminal_attempt_count"], 0)
        self.assertEqual(reconciliation["period_boundary_crossing_attempt_count"], 1)
        self.assertEqual(
            reconciliation["variance_state"],
            "account_or_gateway_evidence_incomplete",
        )
        self.assertIsNone(reconciliation["signed_variance"])
        with managed_sqlite_connection(self.config.database_path) as connection:
            audit = json.loads(connection.execute(
                f"SELECT evidence_json FROM {QUERY_AUDIT_TABLE}"
            ).fetchone()[0])
        self.assertEqual(audit["terminal_attempt_count"], 0)
        self.assertEqual(audit["terminal_attempts_missing_sidecar_count"], 0)

    def test_pending_account_attempt_blocks_variance_without_becoming_terminal(self):
        self._seed_cost()
        self._seed_attempt(self.primary_account, finalize=False)
        status, stdout, stderr = self.invoke()
        self.assertEqual((status, stderr), (0, ""))
        report = json.loads(stdout)
        reconciliation = report["account_reconciliation"]
        self.assertEqual(report["preview"]["gateway_estimate"]["attempt_count"], 0)
        self.assertEqual(reconciliation["matched_terminal_attempt_count"], 0)
        self.assertEqual(reconciliation["pending_account_gap_count"], 1)
        self.assertEqual(
            reconciliation["variance_state"],
            "account_or_gateway_evidence_incomplete",
        )
        self.assertIsNone(reconciliation["signed_variance"])
        with managed_sqlite_connection(self.config.database_path) as connection:
            audit = json.loads(connection.execute(
                f"SELECT evidence_json FROM {QUERY_AUDIT_TABLE}"
            ).fetchone()[0])
        self.assertEqual(audit["terminal_attempt_count"], 0)
        self.assertEqual(audit["terminal_attempts_missing_sidecar_count"], 0)

    def test_corrupt_attempt_binding_fails_closed_before_output(self):
        self._seed_cost()
        self._seed_attempt(self.primary_account)
        trigger = f"{ATTEMPT_BINDING_TABLE}_no_update"
        with managed_sqlite_connection(self.config.database_path) as connection:
            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger,),
            ).fetchone()[0]
            row = connection.execute(
                f"SELECT request_attempt_id,evidence_json FROM {ATTEMPT_BINDING_TABLE}"
            ).fetchone()
            request_attempt_id, evidence_json = row
            corrupted = json.loads(evidence_json)
            corrupted["binding_digest"] = "f" * 64
            connection.execute(f"DROP TRIGGER {trigger}")
            connection.execute(
                f"UPDATE {ATTEMPT_BINDING_TABLE} SET evidence_json=? "
                "WHERE request_attempt_id=?",
                (
                    json.dumps(corrupted, sort_keys=True, separators=(",", ":")),
                    request_attempt_id,
                ),
            )
            connection.execute(trigger_sql)
        status, stdout, stderr = self.invoke()
        self.assertEqual((status, stdout), (2, ""))
        self.assertEqual(json.loads(stderr), {"error": {"code": "unavailable"}})
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    f"SELECT COUNT(*) FROM {QUERY_AUDIT_TABLE}"
                ).fetchone()[0],
                0,
            )

    def test_query_audit_or_authorization_failure_rolls_back_before_delivery(self):
        self._seed_cost()
        self._seed_attempt(self.primary_account)
        with mock.patch(
            "hormuz.finance_collection_repository._append_audit",
            side_effect=FinanceCollectionError("unavailable"),
        ):
            status, stdout, stderr = self.invoke()
        self.assertEqual((status, stdout), (2, ""))
        self.assertEqual(json.loads(stderr), {"error": {"code": "unavailable"}})
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    f"SELECT COUNT(*) FROM {QUERY_AUDIT_TABLE}"
                ).fetchone()[0],
                0,
            )

        authorize = self.repository._authorize
        calls = 0

        def revoked_before_commit(principal):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise FinanceCollectionError("forbidden")
            authorize(principal)

        dependencies = finance_commands.FinanceCommandDependencies(
            create_repository=mock.Mock(return_value=self.repository),
        )
        with mock.patch.object(
            self.repository,
            "_authorize",
            side_effect=revoked_before_commit,
        ):
            status, stdout, stderr = self.invoke(dependencies=dependencies)
        self.assertEqual(calls, 4)
        self.assertEqual((status, stdout), (2, ""))
        self.assertEqual(json.loads(stderr), {"error": {"code": "forbidden"}})
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    f"SELECT COUNT(*) FROM {QUERY_AUDIT_TABLE}"
                ).fetchone()[0],
                0,
            )

    def test_unauthorized_reader_cannot_open_storage(self):
        dependencies = finance_commands.FinanceCommandDependencies(
            create_repository=mock.Mock(side_effect=AssertionError("database opened")),
        )
        status, stdout, stderr = self.invoke(
            environment={"HORMUZ_PORTFOLIO_TOKEN": VIEWER_TOKEN},
            dependencies=dependencies,
        )
        self.assertEqual((status, stdout), (2, ""))
        self.assertEqual(json.loads(stderr), {"error": {"code": "forbidden"}})
        dependencies.create_repository.assert_not_called()

    def test_attempt_lifetime_must_be_fully_contained_in_selected_period(self):
        start = "2026-09-01T00:00:00Z"
        end = "2026-09-02T00:00:00Z"
        self.assertTrue(_attempt_lifetime_within_period(
            {
                "root_created_at": "2026-09-01T00:00:00+00:00",
                "terminal_occurred_at": "2026-09-01T23:59:59+00:00",
            },
            start_at=start,
            end_at=end,
        ))
        for row in (
            {
                "root_created_at": "2026-08-31T23:59:59+00:00",
                "terminal_occurred_at": "2026-09-01T00:00:01+00:00",
            },
            {
                "root_created_at": "2026-09-01T23:59:59+00:00",
                "terminal_occurred_at": "2026-09-02T00:00:00+00:00",
            },
        ):
            with self.subTest(row=row):
                self.assertFalse(_attempt_lifetime_within_period(
                    row,
                    start_at=start,
                    end_at=end,
                ))


if __name__ == "__main__":
    unittest.main()
