from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
import io
import json
import copy
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from hormuz.billing import (
    BillingReconciliationPolicy,
    ProviderBillingError,
    ProviderCostSource,
    evaluate_reconciliation,
    parse_provider_cost_pages,
)
from hormuz.billing_client import ProviderCostFetchResult
from hormuz.cli import _billing_command, build_parser
from hormuz.config import GatewayConfig, Identity
from hormuz.store import UsageStore


def _utc_day() -> tuple[datetime, datetime]:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _openai_page(
    *,
    amount: Decimal = Decimal("1.25"),
    has_more: bool = False,
    next_page: str | None = None,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    start, end = _utc_day()
    result: dict[str, object] = {
        "object": "organization.costs.result",
        "amount": {"value": amount, "currency": "usd"},
        "line_item": "Responses API",
        "project_id": "proj_alpha",
    }
    result.update(extra or {})
    return {
        "object": "page",
        "data": [
            {
                "object": "bucket",
                "start_time": int(start.timestamp()),
                "end_time": int(end.timestamp()),
                "results": [result],
            }
        ],
        "has_more": has_more,
        "next_page": next_page,
    }


def _anthropic_page(
    *,
    amount_cents: str = "123.78912",
    start: datetime | None = None,
) -> dict[str, object]:
    if start is None:
        start, end = _utc_day()
    else:
        end = start + timedelta(days=1)
    return {
        "data": [
            {
                "starting_at": start.isoformat().replace("+00:00", "Z"),
                "ending_at": end.isoformat().replace("+00:00", "Z"),
                "results": [
                    {
                        "amount": amount_cents,
                        "currency": "USD",
                        "workspace_id": "wrkspc_alpha",
                        "description": "Claude cache read tokens",
                        "cost_type": "tokens",
                        "model": "claude-sonnet-4-6",
                        "service_tier": "standard",
                        "token_type": "cache_read_input_tokens",
                        "context_window": "0-200k",
                        "inference_geo": "global",
                    }
                ],
            }
        ],
        "has_more": False,
        "next_page": None,
    }


class ProviderCostParserTests(unittest.TestCase):
    def test_openai_normalizes_dollars_and_billing_dimensions(self) -> None:
        report = parse_provider_cost_pages("openai", [_openai_page()])

        self.assertEqual(report.provider, "openai")
        self.assertEqual(report.page_count, 1)
        self.assertEqual(len(report.items), 1)
        item = report.items[0]
        self.assertEqual(item.amount_usd, "1.25")
        self.assertEqual(item.currency, "USD")
        self.assertEqual(item.provider_scope_kind, "project")
        self.assertEqual(item.provider_scope_id, "proj_alpha")
        self.assertEqual(item.line_item, "Responses API")
        self.assertRegex(report.source_sha256, r"^[0-9a-f]{64}$")

    def test_anthropic_preserves_fractional_cents_exactly(self) -> None:
        report = parse_provider_cost_pages("anthropic", [_anthropic_page()])

        item = report.items[0]
        self.assertEqual(item.amount_usd, "1.2378912")
        self.assertEqual(item.provider_scope_kind, "workspace")
        self.assertEqual(item.provider_scope_id, "wrkspc_alpha")
        self.assertEqual(item.cost_type, "tokens")
        self.assertEqual(item.token_type, "cache_read_input_tokens")

    def test_incomplete_or_inconsistent_pagination_fails_closed(self) -> None:
        with self.assertRaisesRegex(ProviderBillingError, "incomplete"):
            parse_provider_cost_pages(
                "openai",
                [_openai_page(has_more=True, next_page="cursor-2")],
            )
        with self.assertRaisesRegex(ProviderBillingError, "pagination"):
            parse_provider_cost_pages(
                "openai",
                [
                    _openai_page(has_more=False),
                    _openai_page(has_more=False),
                ],
            )
        with self.assertRaisesRegex(ProviderBillingError, "duplicate page"):
            parse_provider_cost_pages(
                "openai",
                [
                    _openai_page(has_more=True, next_page="cursor-2"),
                    _openai_page(has_more=False),
                ],
            )

    def test_malformed_currency_amount_and_bucket_fail_closed(self) -> None:
        wrong_currency = _openai_page()
        wrong_currency["data"][0]["results"][0]["amount"]["currency"] = "eur"  # type: ignore[index]
        with self.assertRaisesRegex(ProviderBillingError, "USD"):
            parse_provider_cost_pages("openai", [wrong_currency])

        malformed_amount = _anthropic_page(amount_cents="NaN")
        with self.assertRaisesRegex(ProviderBillingError, "amount"):
            parse_provider_cost_pages("anthropic", [malformed_amount])

        bad_bucket = _anthropic_page()
        bad_bucket["data"][0]["ending_at"] = bad_bucket["data"][0]["starting_at"]  # type: ignore[index]
        with self.assertRaisesRegex(ProviderBillingError, "bucket"):
            parse_provider_cost_pages("anthropic", [bad_bucket])

    def test_normalized_source_fingerprint_is_independent_of_page_size(self) -> None:
        first = _openai_page()
        second = _openai_page()
        second_bucket = second["data"][0]  # type: ignore[index]
        second_bucket["start_time"] += 86_400  # type: ignore[operator]
        second_bucket["end_time"] += 86_400  # type: ignore[operator]
        combined = copy.deepcopy(first)
        combined["data"].append(copy.deepcopy(second_bucket))  # type: ignore[union-attr]
        first["has_more"] = True
        first["next_page"] = "cursor-2"

        one_page = parse_provider_cost_pages("openai", [combined])
        two_pages = parse_provider_cost_pages("openai", [first, second])

        self.assertEqual(one_page.source_sha256, two_pages.source_sha256)


def _reconciliation_facts(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "schema_version": 1,
        "provider_cost_usd": "100",
        "gateway_estimated_cost_usd": "95",
        "variance_usd": "5",
        "provider_source_kind": "authenticated_api",
        "gateway_unpriced_requests": 0,
        "legacy_unattributed_gateway_requests": 0,
        "unscoped_provider_items": 0,
    }
    values.update(overrides)
    return values


class BillingReconciliationPolicyTests(unittest.TestCase):
    def test_exact_thresholds_are_versioned_and_boundary_is_clear(self) -> None:
        policy = BillingReconciliationPolicy(
            enabled=True,
            policy_version="finance-v1",
            max_absolute_variance_usd=Decimal("5.00"),
            max_variance_basis_points=500,
            max_unpriced_requests=0,
            max_legacy_unattributed_requests=0,
            max_unscoped_provider_items=0,
            require_authenticated_source=True,
        )

        result = evaluate_reconciliation(_reconciliation_facts(), policy)

        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["variance_absolute_usd"], "5")
        self.assertEqual(result["variance_basis_points"], "500")
        self.assertEqual(result["exception_status"], "clear")
        self.assertEqual(result["exception_reasons"], [])
        self.assertEqual(
            result["reconciliation_policy"]["max_absolute_variance_usd"],
            "5",
        )
        self.assertRegex(
            result["reconciliation_policy"]["policy_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(policy.policy_sha256, replace(policy).policy_sha256)

    def test_every_configured_exception_reason_is_explicit(self) -> None:
        policy = BillingReconciliationPolicy(
            enabled=True,
            policy_version="finance-v2",
            max_absolute_variance_usd=Decimal("10"),
            max_variance_basis_points=500,
            max_unpriced_requests=0,
            max_legacy_unattributed_requests=0,
            max_unscoped_provider_items=0,
            require_authenticated_source=True,
        )

        result = evaluate_reconciliation(
            _reconciliation_facts(
                variance_usd="20",
                gateway_estimated_cost_usd="80",
                provider_source_kind="offline_upload",
                gateway_unpriced_requests=1,
                legacy_unattributed_gateway_requests=2,
                unscoped_provider_items=3,
            ),
            policy,
        )

        self.assertEqual(result["exception_status"], "review_required")
        self.assertEqual(
            result["exception_reasons"],
            [
                "provider_source_not_authenticated",
                "absolute_variance_exceeded",
                "relative_variance_exceeded",
                "unpriced_requests_exceeded",
                "legacy_unattributed_requests_exceeded",
                "unscoped_provider_items_exceeded",
            ],
        )

    def test_zero_provider_basis_fails_closed_for_relative_threshold(self) -> None:
        policy = BillingReconciliationPolicy(
            enabled=True,
            policy_version="finance-v3",
            max_variance_basis_points=500,
        )

        result = evaluate_reconciliation(
            _reconciliation_facts(
                provider_cost_usd="0",
                gateway_estimated_cost_usd="1",
                variance_usd="-1",
            ),
            policy,
        )

        self.assertIsNone(result["variance_basis_points"])
        self.assertEqual(result["exception_reasons"], ["variance_basis_unavailable"])

    def test_disabled_policy_is_observable_and_invalid_facts_fail_closed(self) -> None:
        result = evaluate_reconciliation(
            _reconciliation_facts(),
            BillingReconciliationPolicy(),
        )
        self.assertEqual(result["exception_status"], "not_evaluated")
        self.assertEqual(result["exception_reasons"], [])

        with self.assertRaisesRegex(ProviderBillingError, "facts are invalid"):
            evaluate_reconciliation(
                _reconciliation_facts(gateway_unpriced_requests=-1),
                BillingReconciliationPolicy(
                    enabled=True,
                    max_unpriced_requests=0,
                ),
            )
        with self.assertRaisesRegex(ProviderBillingError, "facts are inconsistent"):
            evaluate_reconciliation(
                _reconciliation_facts(variance_usd="4"),
                BillingReconciliationPolicy(),
            )
        with self.assertRaisesRegex(ProviderBillingError, "facts are invalid"):
            evaluate_reconciliation(
                _reconciliation_facts(provider_cost_usd="1e999"),
                BillingReconciliationPolicy(),
            )
        with self.assertRaisesRegex(ProviderBillingError, "facts are invalid"):
            evaluate_reconciliation(
                _reconciliation_facts(provider_cost_usd="1.0000000000001"),
                BillingReconciliationPolicy(),
            )
        with self.assertRaisesRegex(ValueError, "at least one rule"):
            BillingReconciliationPolicy(enabled=True)


class ProviderCostStoreTests(unittest.TestCase):
    def test_legacy_positive_bucket_constraint_migrates_without_data_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE gateway_provider_cost_imports (
                        id TEXT PRIMARY KEY,
                        imported_at TEXT NOT NULL,
                        organization_id TEXT NOT NULL,
                        provider TEXT NOT NULL CHECK (provider IN ('openai', 'anthropic')),
                        source_sha256 TEXT NOT NULL,
                        report_start TEXT NOT NULL,
                        report_end TEXT NOT NULL,
                        page_count INTEGER NOT NULL CHECK (page_count > 0),
                        bucket_count INTEGER NOT NULL CHECK (bucket_count > 0),
                        item_count INTEGER NOT NULL CHECK (item_count >= 0),
                        UNIQUE (organization_id, provider, source_sha256)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO gateway_provider_cost_imports VALUES (
                        'pci_legacy', '2026-08-01T00:00:00+00:00', 'org-a', 'openai',
                        ?, '2026-08-01T00:00:00+00:00', '2026-08-02T00:00:00+00:00',
                        1, 1, 0
                    )
                    """,
                    ("a" * 64,),
                )

            UsageStore(path)

            with sqlite3.connect(path) as connection:
                sql = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'gateway_provider_cost_imports'"
                ).fetchone()[0]
                self.assertIn("bucket_count >= 0", sql)
                self.assertEqual(
                    connection.execute(
                        "SELECT id, bucket_count FROM gateway_provider_cost_imports"
                    ).fetchone(),
                    ("pci_legacy", 1),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT source_kind FROM gateway_provider_cost_sources WHERE import_id = ?",
                        ("pci_legacy",),
                    ).fetchone(),
                    ("offline_upload",),
                )

    def test_source_evidence_cannot_claim_a_different_authenticated_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            report = parse_provider_cost_pages("openai", [_openai_page()])
            forged = ProviderCostSource(
                kind="authenticated_api",
                api_contract="openai.organization.costs.v1",
                query_start="2026-01-01T00:00:00+00:00",
                query_end="2026-01-02T00:00:00+00:00",
                query_scope="organization_all_projects_line_items",
            )
            with self.assertRaisesRegex(ValueError, "does not match"):
                store.import_provider_cost_report(
                    organization_id="org-a",
                    report=report,
                    source=forged,
                )
            with sqlite3.connect(store.path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM gateway_provider_cost_imports"
                    ).fetchone()[0],
                    0,
                )

    def test_empty_authenticated_report_is_persisted_and_reconciles_as_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            page = {
                "object": "page",
                "data": [],
                "has_more": False,
                "next_page": None,
            }
            report = parse_provider_cost_pages(
                "openai",
                [page],
                expected_start="2026-08-01T00:00:00+00:00",
                expected_end="2026-08-02T00:00:00+00:00",
            )
            source = ProviderCostSource.authenticated(
                provider="openai",
                query_start=report.report_start,
                query_end=report.report_end,
            )

            imported = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
                source=source,
            )
            result = store.reconcile_provider_costs(
                organization_id="org-a",
                provider="openai",
                import_id=imported.import_id,
            )

            self.assertEqual(imported.bucket_count, 0)
            self.assertEqual(result["provider_cost_usd"], "0")
            self.assertEqual(
                result["provider_report_completeness"],
                "authenticated_query_pagination_complete",
            )
            self.assertEqual(
                result["coverage_status"],
                "partial_authenticated_provider_endpoint_scope",
            )
            self.assertEqual(result["provider_source_kind"], "authenticated_api")
            self.assertEqual(result["query_start"], report.report_start)
            self.assertEqual(result["query_end"], report.report_end)
            self.assertFalse(result["credential_retained"])
            UsageStore(store.path)
            with sqlite3.connect(store.path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT source_kind FROM gateway_provider_cost_sources"
                    ).fetchall(),
                    [("authenticated_api",)],
                )

    def test_authenticated_observation_upgrades_provenance_without_duplicate_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            report = parse_provider_cost_pages("anthropic", [_anthropic_page()])
            offline = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
            )
            source = ProviderCostSource.authenticated(
                provider="anthropic",
                query_start=report.report_start,
                query_end=report.report_end,
            )
            fetched = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
                source=source,
            )

            self.assertEqual(fetched.import_id, offline.import_id)
            self.assertFalse(fetched.created)
            self.assertTrue(fetched.source_evidence_created)
            self.assertEqual(fetched.source_kind, "authenticated_api")
            with sqlite3.connect(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM gateway_provider_cost_imports").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM gateway_provider_cost_items").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM gateway_provider_cost_sources").fetchone()[0],
                    2,
                )

    def test_store_revalidates_normalized_input_and_detects_missing_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            report = parse_provider_cost_pages("anthropic", [_anthropic_page()])
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                store.import_provider_cost_report(
                    organization_id="org-a",
                    report=replace(report, source_sha256="0" * 63),
                )

            imported = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "DELETE FROM gateway_provider_cost_items WHERE import_id = ?",
                    (imported.import_id,),
                )
            with self.assertRaisesRegex(ValueError, "item count"):
                store.reconcile_provider_costs(
                    organization_id="org-a",
                    provider="anthropic",
                    import_id=imported.import_id,
                )

    def test_import_is_idempotent_normalized_and_does_not_retain_unknown_raw_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            sentinel = "RAW-PROVIDER-PAYLOAD-MUST-NOT-PERSIST"
            report = parse_provider_cost_pages(
                "openai",
                [_openai_page(extra={"future_provider_field": sentinel})],
            )

            first = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
            )
            duplicate = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
            )

            self.assertTrue(first.created)
            self.assertFalse(duplicate.created)
            self.assertEqual(first.import_id, duplicate.import_id)
            with sqlite3.connect(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM gateway_provider_cost_imports").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM gateway_provider_cost_items").fetchone()[0],
                    1,
                )
            self.assertNotIn(sentinel.encode("utf-8"), path.read_bytes())

    def test_concurrent_identical_imports_converge_on_one_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            stores = (UsageStore(path), UsageStore(path))
            report = parse_provider_cost_pages("anthropic", [_anthropic_page()])
            barrier = threading.Barrier(2)
            outcomes: list[tuple[str, bool]] = []
            outcome_lock = threading.Lock()

            def import_report(store: UsageStore) -> None:
                barrier.wait()
                result = store.import_provider_cost_report(
                    organization_id="org-a",
                    report=report,
                )
                with outcome_lock:
                    outcomes.append((result.import_id, result.created))

            threads = [threading.Thread(target=import_report, args=(store,)) for store in stores]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(len(outcomes), 2)
            self.assertEqual(len({import_id for import_id, _ in outcomes}), 1)
            self.assertEqual(sorted(created for _, created in outcomes), [False, True])

    def test_signed_provider_amounts_include_credits_without_reclassification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = UsageStore(Path(temporary) / "usage.sqlite3")
            page = _openai_page()
            page["data"][0]["results"].append(  # type: ignore[index]
                {
                    "object": "organization.costs.result",
                    "amount": {"value": Decimal("-0.25"), "currency": "usd"},
                    "line_item": "Credit",
                    "project_id": None,
                }
            )
            report = parse_provider_cost_pages("openai", [page])
            imported = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
            )

            result = store.reconcile_provider_costs(
                organization_id="org-a",
                provider="openai",
                import_id=imported.import_id,
            )

            self.assertEqual(result["provider_cost_usd"], "1")
            self.assertEqual(result["negative_provider_items"], 1)
            self.assertEqual(
                result["credits_discounts_adjustments_treatment"],
                "signed_provider_amounts_included_without_reclassification",
            )

    def test_reconciliation_keeps_provider_reported_and_request_estimated_costs_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "usage.sqlite3"
            store = UsageStore(path)
            report = parse_provider_cost_pages("openai", [_openai_page()])
            imported = store.import_provider_cost_report(
                organization_id="org-a",
                report=report,
            )
            for organization_id, cost in (("org-a", 100_000), ("org-b", 900_000)):
                store.record(
                    identity=Identity(
                        token_env="TOKEN",
                        token="test-token",
                        actor_id=f"alice-{organization_id}",
                        actor_name="Alice",
                        team_id="engineering",
                        team_name="Engineering",
                        organization_id=organization_id,
                    ),
                    client="codex",
                    protocol="openai",
                    requested_model="gpt-fast",
                    resolved_alias="gpt-fast",
                    upstream_model="gpt-upstream",
                    policy_action="allowed",
                    status="succeeded",
                    input_tokens=100,
                    output_tokens=20,
                    cost_microusd=cost,
                    cost_basis="estimated",
                    rate_card_version="rates-v1",
                )
            store.record(
                identity=Identity(
                    token_env="TOKEN",
                    token="test-token",
                    actor_id="legacy",
                    actor_name="Legacy",
                    team_id="engineering",
                    team_name="Engineering",
                    organization_id="org-a",
                ),
                client="codex",
                protocol="openai",
                requested_model="gpt-fast",
                resolved_alias="gpt-fast",
                upstream_model="gpt-upstream",
                policy_action="allowed",
                status="succeeded",
                cost_microusd=700_000,
                cost_basis="estimated",
                rate_card_version="legacy",
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE gateway_usage_events SET organization_id = NULL WHERE actor_id = 'legacy'"
                )

            result = store.reconcile_provider_costs(
                organization_id="org-a",
                provider="openai",
                import_id=imported.import_id,
            )

            self.assertEqual(result["provider_cost_basis"], "provider_reported")
            self.assertEqual(result["provider_cost_usd"], "1.25")
            self.assertEqual(result["gateway_estimated_cost_usd"], "0.1")
            self.assertEqual(result["variance_usd"], "1.15")
            self.assertEqual(result["gateway_requests"], 1)
            self.assertEqual(result["legacy_unattributed_gateway_requests"], 1)
            self.assertEqual(result["gateway_scope_status"], "partial_legacy_unattributed_gateway_window")
            self.assertEqual(result["unscoped_provider_items"], 0)
            self.assertEqual(result["person_cost_basis"], "estimated")
            self.assertFalse(result["variance_proves_gateway_bypass"])
            self.assertEqual(result["provider_report_completeness"], "not_verifiable_from_response")
            org_a_audit = [
                event
                for event in store.audit_events(
                    since="2000-01-01T00:00:00+00:00",
                    kind="usage",
                )
                if event["organization_id"] == "org-a"
            ]
            self.assertEqual(len(org_a_audit), 1)

            with self.assertRaisesRegex(ValueError, "not found"):
                store.reconcile_provider_costs(
                    organization_id="org-b",
                    provider="openai",
                    import_id=imported.import_id,
                )


class ProviderCostCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.config = GatewayConfig.load(
            cls.root / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-identity-token"},
        )

    def test_cli_import_and_reconcile_use_configured_organization_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            page = _openai_page()
            page["data"][0]["results"][0]["amount"]["value"] = 1.25  # type: ignore[index]
            input_path = root / "openai-costs.json"
            input_path.write_text(json.dumps(page), encoding="utf-8")
            organization = next(iter(config.identities_by_actor.values())).organization_id
            import_args = build_parser().parse_args(
                [
                    "billing",
                    "import",
                    "--organization",
                    organization,
                    "--provider",
                    "openai",
                    "--input",
                    str(input_path),
                ]
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(_billing_command(config, import_args), 0)
            imported = json.loads(output.getvalue())
            self.assertTrue(imported["created"])
            self.assertFalse(imported["raw_payload_retained"])

            reconcile_args = build_parser().parse_args(
                [
                    "billing",
                    "reconcile",
                    "--organization",
                    organization,
                    "--provider",
                    "openai",
                    "--import-id",
                    imported["import_id"],
                    "--json",
                ]
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(_billing_command(config, reconcile_args), 0)
            reconciled = json.loads(output.getvalue())
            self.assertEqual(reconciled["schema_version"], 2)
            self.assertEqual(reconciled["provider_cost_usd"], "1.25")
            self.assertEqual(reconciled["gateway_estimated_cost_usd"], "0")
            self.assertFalse(reconciled["variance_proves_gateway_bypass"])
            self.assertEqual(reconciled["exception_status"], "review_required")
            self.assertEqual(
                reconciled["exception_reasons"],
                [
                    "provider_source_not_authenticated",
                    "relative_variance_exceeded",
                ],
            )

            fail_args = build_parser().parse_args(
                [
                    "billing",
                    "reconcile",
                    "--organization",
                    organization,
                    "--provider",
                    "openai",
                    "--import-id",
                    imported["import_id"],
                    "--json",
                    "--fail-on-review",
                ]
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_billing_command(config, fail_args), 3)

            disabled_config = replace(
                config,
                billing_reconciliation=BillingReconciliationPolicy(),
            )
            with redirect_stderr(disabled_error := io.StringIO()):
                self.assertEqual(_billing_command(disabled_config, fail_args), 2)
            self.assertEqual(
                disabled_error.getvalue(),
                "billing error: --fail-on-review requires an enabled "
                "reconciliation policy\n",
            )

            clear_config = replace(
                config,
                billing_reconciliation=BillingReconciliationPolicy(
                    enabled=True,
                    policy_version="finance-clear-v1",
                    max_absolute_variance_usd=Decimal("2"),
                ),
            )
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_billing_command(clear_config, fail_args), 0)

            wrong_scope = build_parser().parse_args(
                [
                    "billing",
                    "reconcile",
                    "--organization",
                    "another-organization",
                    "--provider",
                    "openai",
                ]
            )
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(_billing_command(config, wrong_scope), 2)
            self.assertEqual(error.getvalue(), "billing error: organization is not configured\n")

    def test_cli_rejects_duplicate_members_and_nonstandard_numbers_before_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            organization = next(iter(config.identities_by_actor.values())).organization_id
            for filename, payload, message in (
                (
                    "duplicate.json",
                    '{"object":"page","data":[],"data":[],"has_more":false,"next_page":null}',
                    "duplicate JSON object member",
                ),
                (
                    "constant.json",
                    '{"object":"page","data":[],"future":NaN,"has_more":false,"next_page":null}',
                    "non-standard JSON numeric constant",
                ),
            ):
                path = root / filename
                path.write_text(payload, encoding="utf-8")
                args = build_parser().parse_args(
                    [
                        "billing",
                        "import",
                        "--organization",
                        organization,
                        "--provider",
                        "openai",
                        "--input",
                        str(path),
                    ]
                )
                error = io.StringIO()
                with self.subTest(filename=filename), redirect_stderr(error):
                    self.assertEqual(_billing_command(config, args), 2)
                    self.assertIn(message, error.getvalue())

            self.assertFalse(config.database_path.exists())

    def test_cli_fetch_uses_environment_admin_key_without_retaining_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            organization = next(iter(config.identities_by_actor.values())).organization_id
            report = parse_provider_cost_pages(
                "anthropic",
                [_anthropic_page(start=datetime(2026, 8, 1, tzinfo=timezone.utc))],
                expected_start="2026-08-01T00:00:00+00:00",
                expected_end="2026-08-02T00:00:00+00:00",
            )
            fetched = ProviderCostFetchResult(
                report=report,
                source=ProviderCostSource.authenticated(
                    provider="anthropic",
                    query_start=report.report_start,
                    query_end=report.report_end,
                ),
            )
            args = build_parser().parse_args(
                [
                    "billing",
                    "fetch",
                    "--organization",
                    organization,
                    "--provider",
                    "anthropic",
                    "--start",
                    "2026-08-01",
                    "--end",
                    "2026-08-02",
                    "--credential-env",
                    "TEST_ANTHROPIC_ADMIN_KEY",
                ]
            )
            secret = "admin-secret-must-not-persist"
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"TEST_ANTHROPIC_ADMIN_KEY": secret}, clear=False),
                mock.patch("hormuz.cli.ProviderBillingClient") as client_type,
                redirect_stdout(output),
            ):
                client_type.return_value.fetch.return_value = fetched
                self.assertEqual(_billing_command(config, args), 0)

            client_type.assert_called_once_with("anthropic", credential=secret)
            client_type.return_value.fetch.assert_called_once()
            result = json.loads(output.getvalue())
            self.assertEqual(result["source_kind"], "authenticated_api")
            self.assertFalse(result["credential_retained"])
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret.encode("utf-8"), config.database_path.read_bytes())

    def test_cli_fetch_failure_does_not_create_usage_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = replace(self.config, database_path=root / "usage.sqlite3")
            organization = next(iter(config.identities_by_actor.values())).organization_id
            args = build_parser().parse_args(
                [
                    "billing",
                    "fetch",
                    "--organization",
                    organization,
                    "--provider",
                    "openai",
                    "--start",
                    "2026-08-01",
                    "--end",
                    "2026-08-02",
                ]
            )
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(_billing_command(config, args), 1)
            self.assertEqual(
                error.getvalue(),
                "billing fetch failed: credential environment variable is not set: OPENAI_ADMIN_KEY\n",
            )
            self.assertFalse(config.database_path.exists())


if __name__ == "__main__":
    unittest.main()
