from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import PortfolioError, SCOPES, canonical, validate
from hormuz.store import UsageStore

from ._portfolio_fixture import ADMIN, create_request, registry_config
from ._sqlite import managed_sqlite_connection


FIXTURE = Path(__file__).parent / "fixtures" / "scorecard" / "runtime-v1.json"


class SQLiteScorecardRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        self.assertEqual(UsageStore.schema_version, 17)
        UsageStore(self.config.database_path)
        self.repositories = create_portfolio_repository(self.config)
        self.service = PortfolioService(self.config, self.repositories)
        self.scope = self.service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=canonical(create_request()).encode("ascii"),
            idempotency_key="scorecard-scope",
        )[1]
        self.principal = self.service.authenticate(ADMIN)
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["input"]

    def request(self, **changes):
        value = deepcopy(self.fixture)
        value.update({
            "organization_id": "acme",
            "work_scope": {
                "work_scope_id": self.scope["work_scope_id"],
                "version": self.scope["version"],
            },
            **changes,
        })
        return value

    def error(self, code, operation):
        with self.assertRaises(PortfolioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def rows(self):
        with managed_sqlite_connection(self.config.database_path) as connection:
            connection.row_factory = sqlite3.Row
            return {
                table: [dict(row) for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY sequence"
                ).fetchall()]
                for table in (
                    "portfolio_scorecard_audit_events",
                    "portfolio_model_scorecard_snapshots",
                )
            }

    def test_exact_snapshot_replay_lineage_and_append_only_storage(self):
        value = self.request()
        result = self.repositories.scorecards.build(self.principal, value)
        validate(result["scorecard"], "hormuz.model-scorecard")
        self.assertEqual(result["scorecard"]["state"], "eligible")
        first_rows = self.rows()
        self.assertEqual(len(first_rows["portfolio_scorecard_audit_events"]), 1)
        self.assertEqual(len(first_rows["portfolio_model_scorecard_snapshots"]), 1)
        stored = first_rows["portfolio_model_scorecard_snapshots"][0]
        self.assertEqual(stored["input_json"], canonical(value))
        self.assertEqual(stored["evaluation_json"], canonical(result))
        self.assertEqual(stored["input_digest"], result["lineage"]["input_digest"])
        self.assertEqual(self.repositories.scorecards.build(self.principal, value), result)
        self.assertEqual(self.rows(), first_rows)

        with managed_sqlite_connection(self.config.database_path) as connection:
            for statement in (
                "UPDATE portfolio_model_scorecard_snapshots SET state='inconclusive'",
                "DELETE FROM portfolio_scorecard_audit_events",
            ):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError,
                    "scorecard_snapshot_append_only",
                ):
                    connection.execute(statement)

    def test_version_lineage_conflict_scope_and_authorization_fail_closed(self):
        first = self.request()
        self.repositories.scorecards.build(self.principal, first)
        conflict = self.request(decision_owner_id="different-owner")
        self.error(
            "version_conflict",
            lambda: self.repositories.scorecards.build(self.principal, conflict),
        )

        skipped = self.request(version=3, supersedes_version=2)
        self.error(
            "version_conflict",
            lambda: self.repositories.scorecards.build(self.principal, skipped),
        )
        second = self.request(
            version=2,
            supersedes_version=1,
            generated_at="2026-09-11T00:00:00Z",
            expires_at="2026-10-11T00:00:00Z",
            review_after="2026-10-16T00:00:00Z",
        )
        result = self.repositories.scorecards.build(self.principal, second)
        self.assertEqual((result["scorecard"]["version"], result["scorecard"]["supersedes_version"]), (2, 1))

        missing = self.request()
        missing["scorecard_id"] = "missing-scope-scorecard"
        missing["work_scope"] = {"work_scope_id": "missing-use-case", "version": 1}
        self.error(
            "not_found",
            lambda: self.repositories.scorecards.build(self.principal, missing),
        )
        foreign = self.request(organization_id="beta", scorecard_id="foreign-scorecard")
        self.error(
            "not_found",
            lambda: self.repositories.scorecards.build(self.principal, foreign),
        )

        viewer = PortfolioPrincipal("acme", "finance", ("finance_viewer",))
        with mock.patch.object(
            self.repositories.scorecards,
            "_transaction",
            side_effect=AssertionError("unauthorized storage access"),
        ):
            self.error(
                "forbidden",
                lambda: self.repositories.scorecards.build(viewer, {"prompt": "forbidden"}),
            )

    def test_concurrent_exact_builds_create_one_snapshot(self):
        value = self.request(scorecard_id="concurrent-scorecard")

        def build(_):
            repository = create_portfolio_repository(self.config).scorecards
            return repository.build(self.principal, deepcopy(value))

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(build, range(4)))
        self.assertTrue(all(result == results[0] for result in results))
        rows = self.rows()
        self.assertEqual(len(rows["portfolio_model_scorecard_snapshots"]), 1)
        self.assertEqual(len(rows["portfolio_scorecard_audit_events"]), 1)

    def test_semantic_replay_is_independent_of_input_list_order(self):
        value = self.request(scorecard_id="order-independent-scorecard")
        expected = self.repositories.scorecards.build(self.principal, value)
        reordered = deepcopy(value)
        reordered["cohorts"].reverse()
        for cohort in reordered["cohorts"]:
            cohort["connector_ids"].reverse()
            cohort["strata"].reverse()
            for stratum in cohort["strata"]:
                stratum["work_items"].reverse()
                for work_item in stratum["work_items"]:
                    work_item["attempts"].reverse()
                    for attempt in work_item["attempts"]:
                        attempt["cost_components"].reverse()
        self.assertNotEqual(canonical(value), canonical(reordered))
        self.assertEqual(
            self.repositories.scorecards.build(self.principal, reordered), expected
        )
        rows = self.rows()
        self.assertEqual(len(rows["portfolio_model_scorecard_snapshots"]), 1)
        self.assertEqual(len(rows["portfolio_scorecard_audit_events"]), 1)

    def test_fractional_timestamps_are_normalized_before_storage_order_checks(self):
        value = self.request(scorecard_id="fractional-time-scorecard")
        value["window"]["end_at"] = "2026-09-08T12:00:00Z"
        value["evaluated_at"] = "2026-09-08T12:00:00.1Z"
        self.repositories.scorecards.build(self.principal, value)
        stored = self.rows()["portfolio_model_scorecard_snapshots"][0]
        self.assertEqual(stored["window_end_at"], "2026-09-08T12:00:00.000000Z")
        self.assertEqual(stored["evaluated_at"], "2026-09-08T12:00:00.100000Z")
        self.assertLess(stored["window_end_at"], stored["evaluated_at"])


if __name__ == "__main__":
    unittest.main()
