"""Restricted PostgreSQL parity for portfolio role-scoped aggregate reads."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile

from hormuz.config import UsageStorageConfig
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import ROLE_VIEWS
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    postgres_transaction,
)

from ._postgres_fixture import PostgresTestCase
from ._role_view_fixture import (
    FINANCE_TOKEN,
    PLATFORM_TOKEN,
    SALES_TOKEN,
    TEAM_TOKEN,
    create_budget,
    create_scorecard,
    create_scope,
    role_view_config,
)


class PostgresPortfolioRoleViewRuntimeTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 23)
        self.config = replace(
            role_view_config(Path(temporary.name)),
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.repositories = create_portfolio_repository(
            self.config,
            environ={"HORMUZ_POSTGRES_DSN": self.runtime_dsn},
        )
        self.service = PortfolioService(self.config, self.repositories)
        self.engineering = create_scope(
            self.service,
            "postgres-role-view-engineering",
            owner_team_id="engineering",
        )
        self.sales = create_scope(
            self.service,
            "postgres-role-view-sales",
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
            scorecard_id="postgres-engineering-scorecard",
        )
        create_scorecard(
            self.repositories,
            self.sales,
            scorecard_id="postgres-sales-scorecard",
        )

    def call(self, token, view, resource, query=""):
        return self.service.dispatch(
            token,
            "GET",
            ROLE_VIEWS + f"/{view}/{resource}",
            query=query,
        )[1]

    def test_restricted_runtime_matches_sqlite_scope_and_pagination_semantics(self):
        finance = self.call(FINANCE_TOKEN, "finance", "budgets", "limit=1")
        self.assertEqual(finance["result_count"], 1)
        self.assertTrue(finance["has_more"])
        continuation = self.call(
            FINANCE_TOKEN,
            "finance",
            "budgets",
            "cursor=" + finance["next_cursor"],
        )
        self.assertEqual(continuation["result_count"], 1)
        self.assertEqual(continuation["as_of"], finance["as_of"])
        self.assertEqual(continuation["snapshot"], finance["snapshot"])

        platform = self.call(PLATFORM_TOKEN, "platform", "scorecards")
        engineering = self.call(TEAM_TOKEN, "team", "scorecards")
        sales = self.call(SALES_TOKEN, "team", "budgets")
        self.assertEqual(platform["result_count"], 2)
        self.assertEqual(engineering["result_count"], 1)
        self.assertEqual(
            engineering["items"][0]["work_scope"]["work_scope_id"],
            self.engineering["work_scope_id"],
        )
        self.assertEqual(sales["result_count"], 1)

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            audit = connection.execute(
                "SELECT reader_role,result_count,partial_count "
                "FROM portfolio_role_view_audit_events ORDER BY sequence"
            ).fetchall()
            cursors = connection.execute(
                "SELECT page_limit FROM portfolio_role_view_cursors"
            ).fetchall()
        self.assertGreaterEqual(len(audit), 5)
        self.assertTrue(all(row["partial_count"] <= row["result_count"] for row in audit))
        self.assertIn({"page_limit": 1}, cursors)

    def test_rls_and_append_only_privileges_protect_read_metadata(self):
        self.call(FINANCE_TOKEN, "finance", "budgets", "limit=1")
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM portfolio_role_view_audit_events"
                ).fetchone()["count"],
                0,
            )
        with self.assertRaises(PostgresStorageError) as caught:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="beta",
            ) as connection:
                connection.execute(
                    "UPDATE portfolio_role_view_audit_events SET result_count=0"
                )
        self.assertEqual(caught.exception.code, "storage_access_denied")
        with self.psycopg.connect(self.runtime_dsn) as connection:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    self.sql.SQL(
                        "TRUNCATE TABLE {}.portfolio_role_view_cursors"
                    ).format(self.sql.Identifier(self.schema))
                )


if __name__ == "__main__":
    import unittest

    unittest.main()
