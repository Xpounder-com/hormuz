"""Restricted PostgreSQL parity for deterministic run-to-outcome association."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
from unittest import mock

from hormuz.config import UsageStorageConfig
from hormuz.outcome_ingest import OutcomeIngestor
from hormuz.outcome_wire import OutcomeKeys
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import BINDINGS, SCOPES, canonical
from hormuz.postgres import postgres_transaction

from . import test_association_runtime as association_runtime
from ._attribution_fixture import attributed_config
from ._outcome_fixture import SyntheticOutcomeAdapter
from ._portfolio_fixture import ADMIN, binding_request, create_request, registry_config
from ._postgres_fixture import PostgresTestCase


class PostgresAssociationRuntimeTests(
    association_runtime.SQLiteAssociationRuntimeTests,
    PostgresTestCase,
):
    def setUp(self):
        PostgresTestCase.setUp(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = replace(
            registry_config(self.root),
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.clock_instant = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        self.clock_lock = threading.Lock()
        for target in ("hormuz._portfolio_sql.PortfolioSQL.now", "hormuz.outcome_ingest.observed_time"):
            patcher = mock.patch(target, side_effect=self.clock)
            patcher.start()
            self.addCleanup(patcher.stop)
        initial = create_portfolio_repository(self.config, environ=self.environment)
        service = PortfolioService(self.config, initial)
        self.scope = service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=canonical(create_request()).encode(),
            idempotency_key="association-scope",
        )[1]
        self.binding = service.dispatch(
            ADMIN,
            "POST",
            BINDINGS,
            body=canonical(binding_request(self.scope)).encode(),
            idempotency_key="association-binding",
        )[1]
        self.config = attributed_config(self.config, self.scope)
        self.repositories = create_portfolio_repository(self.config, environ=self.environment)
        self.service = PortfolioService(self.config, self.repositories)
        self.principal = self.service.authenticate(ADMIN)
        self.identity = self.config.identities_by_token[ADMIN]
        self.keys = OutcomeKeys("association-key-v1", {"association-key-v1": b"a" * 32})
        self.ingestor = OutcomeIngestor(
            self.config,
            self.repositories.outcomes,
            "acme",
            "github-one",
            SyntheticOutcomeAdapter(),
            self.keys,
        )

    def assert_audit_and_immutability(self, link, associated):
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id="acme",
        ) as connection:
            rows = connection.execute(
                "SELECT source_schema_id,source_event_id,event_json "
                "FROM gateway_audit_chain_entries "
                "WHERE source_schema_id IN (%s,%s) ORDER BY sequence",
                ("hormuz.run-work-link-event", "hormuz.run-outcome-association-event"),
            ).fetchall()
        self.assertEqual(
            [row["source_schema_id"] for row in rows],
            ["hormuz.run-work-link-event", "hormuz.run-outcome-association-event"],
        )
        self.assertEqual(rows[0]["event_json"], canonical(link))
        self.assertEqual(rows[1]["event_json"], canonical(associated))

        with self.psycopg.connect(self.runtime_dsn) as connection:
            for table in (
                "portfolio_run_work_link_events",
                "portfolio_run_outcome_association_events",
            ):
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    connection.execute(
                        self.sql.SQL("DELETE FROM {}.{}").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        )
                    )
                connection.rollback()

        with self.psycopg.connect(self.owner_dsn) as connection:
            for table in (
                "portfolio_run_work_link_events",
                "portfolio_run_outcome_association_events",
            ):
                with self.assertRaisesRegex(
                    self.psycopg.errors.CheckViolation,
                    "portfolio_append_only",
                ):
                    connection.execute(
                        self.sql.SQL("UPDATE {}.{} SET organization_id=organization_id").format(
                            self.sql.Identifier(self.schema),
                            self.sql.Identifier(table),
                        )
                    )
                connection.rollback()
