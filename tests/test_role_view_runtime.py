from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import PortfolioError, ROLE_VIEWS
from hormuz.portfolio_wire import query_parameters
from hormuz.store import UsageStore

from ._portfolio_fixture import ADMIN as ADMIN_TOKEN
from ._sqlite import managed_sqlite_connection
from ._role_view_fixture import (
    FINANCE_TOKEN,
    PLATFORM_TOKEN,
    SALES_TOKEN,
    TEAM_TOKEN,
    create_budget,
    create_scorecard,
    create_scope,
    reactivate_budget,
    revise_budget,
    role_view_config,
)


class SQLitePortfolioRoleViewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = role_view_config(self.root)
        self.assertEqual(UsageStore.schema_version, 19)
        UsageStore(self.config.database_path)
        self.repositories = create_portfolio_repository(self.config)
        self.service = PortfolioService(self.config, self.repositories)

        self.parent = create_scope(
            self.service,
            "role-view-parent",
            owner_team_id="engineering",
            kind="portfolio",
        )
        self.engineering = create_scope(
            self.service,
            "role-view-engineering",
            owner_team_id=None,
            parent_work_scope_id=self.parent["work_scope_id"],
        )
        self.sales = create_scope(
            self.service,
            "role-view-sales",
            owner_team_id="sales",
        )
        self.engineering_plan = create_budget(
            self.repositories, self.engineering, amount="100",
        )
        self.sales_plan = create_budget(
            self.repositories, self.sales, amount="200",
        )
        create_scorecard(
            self.repositories,
            self.engineering,
            scorecard_id="engineering-scorecard",
        )
        create_scorecard(
            self.repositories,
            self.sales,
            scorecard_id="sales-scorecard",
        )

    def call(self, token, view, resource, query=""):
        return self.service.dispatch(
            token,
            "GET",
            ROLE_VIEWS + f"/{view}/{resource}",
            query=query,
        )[1]

    def error(self, code, operation):
        with self.assertRaises(PortfolioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def rows(self, table):
        with managed_sqlite_connection(self.config.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY sequence"
                if table == "portfolio_role_view_audit_events"
                else f"SELECT * FROM {table} ORDER BY cursor_id"
            ).fetchall()]

    def test_role_scopes_payloads_provenance_and_raw_owner_separation(self):
        finance = self.call(FINANCE_TOKEN, "finance", "budgets")
        self.assertEqual((finance["reader_role"], finance["result_count"]), ("finance_viewer", 2))
        self.assertEqual(
            {item["payload"]["plan"]["id"] for item in finance["items"]},
            {self.engineering_plan["budget_plan_id"], self.sales_plan["budget_plan_id"]},
        )
        for item in finance["items"]:
            self.assertEqual(item["schema_id"], "hormuz.portfolio-role-view-item")
            self.assertEqual(item["payload"]["schema_id"], "hormuz.work-budget-report")
            self.assertIn("forecast", item["payload"])
            self.assertIn("financial_observations", item["payload"])
            self.assertIn("coverage", item["payload"])
            self.assertIn("pending_reservation_amount", item["payload"]["enforcement"])
            self.assertIn("uncertain_reservation_amount", item["payload"]["enforcement"])
            self.assertEqual(item["exclusions"][0:2], ["prompts", "responses"])

        platform = self.call(PLATFORM_TOKEN, "platform", "scorecards")
        self.assertEqual((platform["reader_role"], platform["result_count"]), ("platform_viewer", 2))
        scorecard = platform["items"][0]
        self.assertEqual(scorecard["payload"]["schema_id"], "hormuz.model-scorecard")
        self.assertIn("models", scorecard["provenance"])
        self.assertIn("policies", scorecard["provenance"])
        self.assertIn("connectors", scorecard["provenance"])
        self.assertIn(
            "fallback",
            scorecard["payload"]["cohorts"][0]["drivers"]["fallback_and_denial_rate"],
        )
        self.assertIn("latency", scorecard["payload"]["cohorts"][0]["drivers"])
        self.assertIn("reliability", scorecard["payload"]["cohorts"][0]["guardrails"])

        engineering = self.call(TEAM_TOKEN, "team", "budgets")
        sales = self.call(SALES_TOKEN, "team", "scorecards")
        self.assertEqual(engineering["scope"], {"kind": "team", "id": "engineering"})
        self.assertEqual(engineering["result_count"], 1)
        self.assertEqual(
            engineering["items"][0]["work_scope"]["work_scope_id"],
            self.engineering["work_scope_id"],
        )
        self.assertEqual(sales["result_count"], 1)
        self.assertEqual(sales["items"][0]["team_id"], "sales")

        finance_principal = self.service.authenticate(FINANCE_TOKEN)
        with mock.patch.object(
            self.repositories.registry,
            "_transaction",
            side_effect=AssertionError("aggregate reader reached raw registry"),
        ):
            self.error(
                "forbidden",
                lambda: self.repositories.registry.execute(
                    finance_principal,
                    "list_scopes",
                    path="/v1/admin/portfolio/work-scopes",
                    scope_id=None,
                    query={},
                    body=None,
                    idempotency_key=None,
                ),
            )

    def test_authentication_precedes_query_parsing_and_storage(self):
        with mock.patch.object(
            self.repositories.views,
            "_transaction",
            side_effect=AssertionError("denied role-view storage"),
        ):
            self.error(
                "forbidden",
                lambda: self.service.dispatch(
                    ADMIN_TOKEN,
                    "GET",
                    ROLE_VIEWS + "/finance/budgets",
                    query="unknown=SYNTHETIC_EXCLUDED",
                ),
            )
            self.error(
                "invalid_request",
                lambda: self.service.dispatch(
                    FINANCE_TOKEN,
                    "GET",
                    ROLE_VIEWS + "/finance/budgets",
                    query="unknown=SYNTHETIC_EXCLUDED",
                ),
            )
        forged = PortfolioPrincipal(
            "acme", "finance-reader", ("finance_viewer", "team_lead"), "sales",
        )
        self.error(
            "forbidden",
            lambda: self.repositories.views.authorize_operation(
                forged, "list_finance_budgets",
            ),
        )

    def test_frozen_pagination_binds_limit_filters_authority_and_snapshots(self):
        first = self.call(FINANCE_TOKEN, "finance", "budgets", "limit=1")
        self.assertTrue(first["has_more"])
        late_scope = create_scope(
            self.service,
            "role-view-late",
            owner_team_id="engineering",
        )
        late = create_budget(self.repositories, late_scope, amount="300")

        continuation = self.call(
            FINANCE_TOKEN,
            "finance",
            "budgets",
            "cursor=" + first["next_cursor"],
        )
        self.assertEqual(continuation["result_count"], 1)
        self.assertEqual(continuation["as_of"], first["as_of"])
        self.assertEqual(continuation["snapshot"], first["snapshot"])
        self.assertNotIn(
            late["budget_plan_id"],
            {item["payload"]["plan"]["id"] for item in continuation["items"]},
        )
        replay = self.call(
            FINANCE_TOKEN,
            "finance",
            "budgets",
            "cursor=" + first["next_cursor"] + "&limit=1",
        )
        self.assertEqual(replay["items"], continuation["items"])
        self.error(
            "cursor_invalid",
            lambda: self.call(
                FINANCE_TOKEN,
                "finance",
                "budgets",
                "cursor=" + first["next_cursor"] + "&limit=2",
            ),
        )
        self.error(
            "cursor_invalid",
            lambda: self.call(
                TEAM_TOKEN,
                "team",
                "budgets",
                "cursor=" + first["next_cursor"],
            ),
        )
        cursors = self.rows("portfolio_role_view_cursors")
        original = next(row for row in cursors if row["cursor_id"] == first["next_cursor"])
        self.assertEqual(original["page_limit"], 1)
        self.assertEqual(json.loads(original["filters_json"]), {})

    def test_frozen_budget_cursor_survives_later_reactivation(self):
        sales_current = revise_budget(
            self.repositories,
            self.sales_plan,
            self.sales,
            amount="225",
        )
        engineering_current = revise_budget(
            self.repositories,
            self.engineering_plan,
            self.engineering,
            amount="125",
        )
        first = self.call(FINANCE_TOKEN, "finance", "budgets", "limit=1")
        self.assertTrue(first["has_more"])
        self.assertEqual(
            first["items"][0]["payload"]["plan"]["id"],
            engineering_current["budget_plan_id"],
        )
        reactivate_budget(
            self.repositories,
            self.sales_plan,
            current_version=sales_current["version"],
            generation=2,
        )

        continuation = self.call(
            FINANCE_TOKEN,
            "finance",
            "budgets",
            "cursor=" + first["next_cursor"],
        )
        self.assertEqual(continuation["result_count"], 1)
        self.assertEqual(
            continuation["items"][0]["payload"]["plan"]["id"],
            sales_current["budget_plan_id"],
        )
        self.assertEqual(
            continuation["items"][0]["payload"]["plan"]["version"],
            sales_current["version"],
        )
        self.assertEqual(
            continuation["items"][0]["payload"]["activation_generation"],
            2,
        )

    def test_team_plan_change_redacts_prior_scope_after_team_move(self):
        moved = revise_budget(
            self.repositories,
            self.engineering_plan,
            self.sales,
            amount="150",
        )
        sales = self.call(SALES_TOKEN, "team", "budgets")
        item = next(
            item for item in sales["items"]
            if item["payload"]["plan"]["id"] == moved["budget_plan_id"]
        )
        change = item["payload"]["plan_change"]
        self.assertEqual(
            (change["kind"], change["comparison_status"]),
            ("not_comparable", "missing_evidence"),
        )
        for field in (
            "previous_plan",
            "previous_amount",
            "previous_currency",
            "previous_work_scope",
            "previous_window",
            "amount_delta",
            "percent_delta",
        ):
            self.assertIsNone(change[field])
        self.assertEqual(change["comparison_reasons"], [])
        self.assertNotIn(
            self.engineering["work_scope_id"],
            json.dumps(item, sort_keys=True),
        )

        finance = self.call(FINANCE_TOKEN, "finance", "budgets")
        finance_item = next(
            item for item in finance["items"]
            if item["payload"]["plan"]["id"] == moved["budget_plan_id"]
        )
        self.assertEqual(
            finance_item["payload"]["plan_change"]["previous_work_scope"],
            {
                "work_scope_id": self.engineering["work_scope_id"],
                "version": self.engineering["version"],
            },
        )

    def test_team_scope_is_applied_before_candidate_cap(self):
        with mock.patch("hormuz.role_view_repository._MAX_CANDIDATES", 1):
            budgets = self.call(TEAM_TOKEN, "team", "budgets")
            scorecards = self.call(TEAM_TOKEN, "team", "scorecards")
        self.assertEqual(budgets["result_count"], 1)
        self.assertEqual(scorecards["result_count"], 1)
        self.assertEqual(
            budgets["items"][0]["work_scope"]["work_scope_id"],
            self.engineering["work_scope_id"],
        )
        self.assertEqual(
            scorecards["items"][0]["work_scope"]["work_scope_id"],
            self.engineering["work_scope_id"],
        )

    def test_window_limit_is_role_view_specific(self):
        query = (
            "start_at=2024-01-01T00%3A00%3A00.000000Z&"
            "end_at=2026-01-01T00%3A00%3A00.000000Z"
        )
        parsed = query_parameters(query, "list_scopes")
        self.assertEqual(parsed["start_at"], "2024-01-01T00:00:00.000000Z")
        self.error(
            "invalid_request",
            lambda: query_parameters(query, "list_team_budgets"),
        )

    def test_every_read_is_atomically_audited_and_partial_provenance_is_counted(self):
        page = self.call(FINANCE_TOKEN, "finance", "budgets", "limit=1")
        audit = self.rows("portfolio_role_view_audit_events")[-1]
        self.assertEqual(audit["result_count"], 1)
        self.assertEqual(audit["partial_count"], 1)
        self.assertEqual(audit["reader_role"], "finance_viewer")
        self.assertNotIn("Synthetic", json.dumps(audit))
        self.assertEqual(page["items"][0]["delivery_state"], "partial")
        self.assertEqual(page["items"][0]["provenance"]["state"], "partial")

        before_audit = self.rows("portfolio_role_view_audit_events")
        before_cursors = self.rows("portfolio_role_view_cursors")
        with mock.patch.object(
            type(self.repositories.views),
            "_audit_sequence",
            side_effect=RuntimeError("synthetic audit failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic audit failure"):
                self.call(FINANCE_TOKEN, "finance", "budgets", "limit=1")
        self.assertEqual(
            self.rows("portfolio_role_view_audit_events"), before_audit
        )
        self.assertEqual(self.rows("portfolio_role_view_cursors"), before_cursors)

    def test_concurrent_reads_have_unique_audit_sequences_and_outage_fails_closed(self):
        def read(_):
            service = PortfolioService(
                self.config,
                create_portfolio_repository(self.config),
            )
            return service.dispatch(
                FINANCE_TOKEN,
                "GET",
                ROLE_VIEWS + "/finance/budgets",
                query="limit=1",
            )[0]

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(read, range(4))), [200] * 4)
        rows = self.rows("portfolio_role_view_audit_events")
        sequences = [row["sequence"] for row in rows]
        self.assertEqual(len(sequences), len(set(sequences)))
        with mock.patch.object(
            self.repositories.views,
            "_transaction",
            side_effect=PortfolioError("unavailable"),
        ):
            self.error(
                "unavailable",
                lambda: self.call(FINANCE_TOKEN, "finance", "budgets"),
            )


if __name__ == "__main__":
    unittest.main()
