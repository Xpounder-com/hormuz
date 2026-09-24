"""Restricted PostgreSQL parity for immutable model scorecard snapshots."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile

from hormuz.config import UsageStorageConfig
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import SCOPES, canonical, validate
from hormuz.postgres import POSTGRES_SCHEMA_VERSION, postgres_transaction

from ._portfolio_fixture import ADMIN, create_request, registry_config
from ._postgres_fixture import PostgresTestCase


FIXTURE = Path(__file__).parent / "fixtures" / "scorecard" / "runtime-v1.json"


class PostgresScorecardRuntimeTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 22)
        self.config = replace(
            registry_config(self.root),
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.repositories = create_portfolio_repository(
            self.config,
            environ=self.environment,
        )
        self.service = PortfolioService(self.config, self.repositories)
        self.scope = self.service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=canonical(create_request()).encode("ascii"),
            idempotency_key="scorecard-postgres-scope",
        )[1]
        self.principal = self.service.authenticate(ADMIN)
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))["input"]

    def request(self):
        value = deepcopy(self.fixture)
        value["organization_id"] = "acme"
        value["work_scope"] = {
            "work_scope_id": self.scope["work_scope_id"],
            "version": self.scope["version"],
        }
        return value

    def test_restricted_runtime_matches_sqlite_and_is_append_only(self):
        value = self.request()
        result = self.repositories.scorecards.build(self.principal, value)
        validate(result["scorecard"], "hormuz.model-scorecard")
        self.assertEqual(self.repositories.scorecards.build(self.principal, value), result)

        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            snapshots = connection.execute(
                "SELECT input_digest,evaluation_json FROM portfolio_model_scorecard_snapshots"
            ).fetchall()
            audit = connection.execute(
                "SELECT operation,reason_code FROM portfolio_scorecard_audit_events"
            ).fetchall()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["input_digest"], result["lineage"]["input_digest"])
        self.assertEqual(snapshots[0]["evaluation_json"], canonical(result))
        self.assertEqual(audit, [{"operation": "build", "reason_code": "eligible"}])

        with self.psycopg.connect(self.runtime_dsn) as connection:
            with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    self.sql.SQL("DELETE FROM {}.portfolio_model_scorecard_snapshots").format(
                        self.sql.Identifier(self.schema)
                    )
                )
        with self.psycopg.connect(self.owner_dsn) as connection:
            with self.assertRaisesRegex(
                self.psycopg.errors.CheckViolation,
                "portfolio_append_only",
            ):
                connection.execute(
                    self.sql.SQL(
                        "UPDATE {}.portfolio_model_scorecard_snapshots "
                        "SET state=state"
                    ).format(self.sql.Identifier(self.schema))
                )

    def test_row_level_security_hides_other_organization(self):
        self.repositories.scorecards.build(self.principal, self.request())
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="beta",
        ) as connection:
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM portfolio_model_scorecard_snapshots"
            ).fetchone()["count"]
        self.assertEqual(count, 0)
