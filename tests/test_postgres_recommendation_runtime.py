"""Restricted PostgreSQL parity for reviewable policy recommendations."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files
import json
from pathlib import Path
import tempfile
import threading
from unittest import mock

from hormuz._recommendation_schema import TABLE_DDL
from hormuz.config import PolicyControlConfig, UsageStorageConfig
from hormuz.policy_document import PolicyDocument
from hormuz.policy_scenarios import PolicyScenarioSuite
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import RECOMMENDATIONS, SCOPES, PortfolioError, canonical
from hormuz.postgres import (
    POSTGRES_SCHEMA_VERSION,
    PostgresStorageError,
    _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION,
    _postgres_acl_boundary,
    _postgres_acl_entries,
    postgres_transaction,
)
from hormuz.store import MonthlyTotals

if __package__:
    from ._portfolio_fixture import ADMIN, OTHER, create_request, registry_config
    from ._postgres_fixture import PostgresTestCase
    from .test_recommendation_runtime import NOW, SCORECARD_FIXTURE, _policy_mapping
else:  # The PostgreSQL boundary audit imports test modules from the tests root.
    from _portfolio_fixture import ADMIN, OTHER, create_request, registry_config
    from _postgres_fixture import PostgresTestCase
    from test_recommendation_runtime import NOW, SCORECARD_FIXTURE, _policy_mapping


class PostgresRecommendationRuntimeTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(POSTGRES_SCHEMA_VERSION, 24)
        self.config = replace(
            registry_config(Path(temporary.name)),
            usage_storage=UsageStorageConfig(
                backend="postgresql",
                postgres_schema=self.schema,
                postgres_runtime_role=self.runtime_role,
            ),
        )
        self.environment = {"HORMUZ_POSTGRES_DSN": self.runtime_dsn}
        self.repositories = create_portfolio_repository(
            self.config, environ=self.environment
        )
        self.service = PortfolioService(self.config, self.repositories)
        self.principal = self.service.authenticate(ADMIN)
        self.scope = self.service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=canonical(create_request()).encode("ascii"),
            idempotency_key="postgres-recommendation-scope",
        )[1]
        self.scorecard_input = json.loads(
            SCORECARD_FIXTURE.read_text(encoding="utf-8")
        )["input"]
        self.scorecard_input.update({
            "organization_id": "acme",
            "work_scope": {
                "work_scope_id": self.scope["work_scope_id"],
                "version": self.scope["version"],
            },
        })
        self.scorecard = self.repositories.scorecards.build(
            self.principal, deepcopy(self.scorecard_input)
        )["scorecard"]
        self.baseline = PolicyDocument.from_mapping(
            _policy_mapping(output_cap=1_000), config=self.config
        )
        self.candidate = PolicyDocument.from_mapping(
            _policy_mapping(output_cap=500), config=self.config
        )
        self.scenarios = PolicyScenarioSuite.from_mapping({
            "schema_id": "hormuz.policy-scenario-suite",
            "schema_version": 1,
            "organization_id": "acme",
            "scenarios": [{
                "id": "synthetic-request",
                "actor_id": "alice",
                "client": "codex",
                "protocol": "openai",
                "requested_model": "synthetic",
                "requested_output_tokens": 750,
            }],
        })
        self.usage = mock.Mock()
        self.usage.monthly_totals.return_value = MonthlyTotals()

    @contextmanager
    def runtime(self, *, policy: PolicyDocument | None = None, now: str = NOW):
        active = self.baseline if policy is None else policy
        with mock.patch.object(
            self.repositories.recommendations,
            "_active_policy",
            return_value={
                "version": active.version_id,
                "digest": active.content_sha256,
            },
        ), mock.patch(
            "hormuz._portfolio_sql.PortfolioSQL.now", return_value=now
        ):
            yield

    def request(
        self,
        recommendation_id: str,
        *,
        expires_at: str = "2026-10-01T00:00:00Z",
        selected_cohort_id: str = "efficient",
    ) -> dict[str, object]:
        return {
            "recommendation_id": recommendation_id,
            "version": 1,
            "supersedes_version": None,
            "scorecard": {
                "id": self.scorecard["scorecard_id"],
                "version": self.scorecard["version"],
            },
            "selected_cohort_id": selected_cohort_id,
            "proposal": {
                "change_type": "output_or_cost_cap_change",
                "candidate_policy_digest": self.candidate.content_sha256,
                "candidate_budget_plan": None,
            },
            "expires_at": expires_at,
        }

    def generate(self, recommendation_id: str, **changes):
        request = self.request(recommendation_id, **changes)
        with self.runtime():
            return self.repositories.recommendations.generate(
                self.principal,
                request,
                baseline_policy=self.baseline,
                candidate_policy=self.candidate,
                usage_store=self.usage,
                preview_request={
                    "actor_id": "alice",
                    "client": "codex",
                    "protocol": "openai",
                    "requested_model": "synthetic",
                    "requested_output_tokens": 750,
                },
                scenario_suite=self.scenarios,
            )

    @staticmethod
    def decision(evaluation: dict[str, object]) -> dict[str, object]:
        return {
            "schema_id": "hormuz.policy-recommendation-decision-request",
            "schema_version": 1,
            "expected_version": evaluation["recommendation"]["version"],
            "decision": "accepted",
            "reason_code": "accepted",
            "pre_apply_evidence": evaluation["pre_apply_evidence"],
        }

    def rows(self, organization: str = "acme") -> dict[str, list[dict[str, object]]]:
        order = {
            "portfolio_policy_recommendation_snapshots": "sequence",
            "portfolio_policy_recommendation_events": "sequence",
            "portfolio_policy_recommendation_read_audit": "sequence",
            "portfolio_policy_recommendation_cursors": "cursor_id",
        }
        with postgres_transaction(
            self.runtime_dsn,
            schema=self.schema,
            runtime_role=self.runtime_role,
            organization_id=organization,
        ) as connection:
            return {
                table: [dict(row) for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {column}"
                ).fetchall()]
                for table, column in order.items()
            }

    def test_restricted_runtime_replay_decision_local_receipt_boundary_and_rls(self) -> None:
        evaluation = self.generate("postgres-recommendation")
        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        before = self.rows()
        self.assertEqual(self.generate("postgres-recommendation"), evaluation)
        self.assertEqual(self.rows(), before)
        with self.runtime():
            page = self.service.dispatch(ADMIN, "GET", RECOMMENDATIONS)[1]
            accepted = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/postgres-recommendation/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="postgres-accept",
            )[1]
            replay = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/postgres-recommendation/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="postgres-accept",
            )[1]
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(replay, accepted)
        self.assertEqual(accepted["state"], "accepted")
        self.assertFalse(accepted["automatic_application"])
        self.assertEqual(
            page["items"][0]["pre_apply_evidence"],
            evaluation["pre_apply_evidence"],
        )

        with self.runtime(policy=self.candidate):
            with self.assertRaises(PortfolioError) as caught:
                self.repositories.recommendations.record_applied(
                    self.principal,
                    "postgres-recommendation",
                    1,
                    {
                        "activation_event_id": "separate-postgres-policy-activation",
                        "activated_policy_digest": self.candidate.content_sha256,
                        "activated_budget_plan": None,
                        "activation_digest": "a" * 64,
                    },
                )
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertEqual(self.rows("beta"), {table: [] for table in TABLE_DDL})

        with self.assertRaises(PostgresStorageError) as caught:
            with postgres_transaction(
                self.runtime_dsn,
                schema=self.schema,
                runtime_role=self.runtime_role,
                organization_id="acme",
            ) as connection:
                connection.execute(
                    "UPDATE portfolio_policy_recommendation_events "
                    "SET public_state=public_state"
                )
        self.assertEqual(caught.exception.code, "storage_access_denied")

    def test_concurrent_conflicting_acceptance_has_one_winner(self) -> None:
        first = self.generate("postgres-race-a")
        second = self.generate("postgres-race-b")
        assert first is not None and second is not None
        barrier = threading.Barrier(2)

        def accept(case):
            recommendation_id, evaluation, key = case
            repositories = create_portfolio_repository(
                self.config, environ=self.environment
            )
            service = PortfolioService(self.config, repositories)
            with mock.patch.object(
                repositories.recommendations,
                "_active_policy",
                return_value={
                    "version": self.baseline.version_id,
                    "digest": self.baseline.content_sha256,
                },
            ), mock.patch(
                "hormuz._portfolio_sql.PortfolioSQL.now", return_value=NOW
            ):
                barrier.wait(timeout=10)
                try:
                    return service.dispatch(
                        ADMIN,
                        "POST",
                        RECOMMENDATIONS + f"/{recommendation_id}/decisions",
                        body=canonical(self.decision(evaluation)).encode("ascii"),
                        idempotency_key=key,
                    )[0]
                except PortfolioError as error:
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, (
                ("postgres-race-a", first, "postgres-race-a-accept"),
                ("postgres-race-b", second, "postgres-race-b-accept"),
            )))
        self.assertCountEqual(results, [200, "version_conflict"])
        events = self.rows()["portfolio_policy_recommendation_events"]
        self.assertEqual(sum(row["event_type"] == "accepted" for row in events), 1)
        self.assertEqual(sum(row["event_type"] == "superseded" for row in events), 1)

    def test_managed_policy_application_uses_authoritative_activation_receipt(self) -> None:
        evaluation = self.generate("postgres-managed-application")
        assert evaluation is not None
        with self.runtime():
            self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/postgres-managed-application/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="postgres-managed-accept",
            )

        actor_key = "static:" + "1" * 64
        event_id = "11111111-1111-4111-8111-111111111111"
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self.sql.SQL("SET LOCAL search_path TO {}").format(
                        self.sql.Identifier(self.schema)
                    )
                )
                cursor.execute(
                    "INSERT INTO policy_tenants "
                    "(organization_id,initialized_at,initialized_by_kind,"
                    "initialized_by_identity_key) VALUES (%s,%s,%s,%s)",
                    ("acme", NOW, "static", actor_key),
                )
                cursor.execute(
                    "INSERT INTO policy_versions "
                    "(organization_id,version_id,content_sha256,document_json,"
                    "change_summary,created_at,author_kind,author_identity_key) "
                    "VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)",
                    (
                        "acme",
                        self.candidate.version_id,
                        self.candidate.content_sha256,
                        json.dumps(self.candidate.to_mapping()),
                        "{}",
                        NOW,
                        "static",
                        actor_key,
                    ),
                )
                cursor.execute(
                    "INSERT INTO policy_active_versions "
                    "(organization_id,version_id,generation,activated_at,"
                    "activated_by_kind,activated_by_identity_key) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        "acme",
                        self.candidate.version_id,
                        1,
                        NOW,
                        "static",
                        actor_key,
                    ),
                )
                cursor.execute(
                    "INSERT INTO policy_control_events "
                    "(event_id,event_schema_id,event_schema_version,organization_id,"
                    "occurred_at,event_type,actor_kind,actor_identity_key,"
                    "version_id,generation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        event_id,
                        "hormuz.policy-control-event",
                        1,
                        "acme",
                        NOW,
                        "policy_activated",
                        "static",
                        actor_key,
                        self.candidate.version_id,
                        1,
                    ),
                )

        managed_config = replace(
            self.config,
            policy_control=PolicyControlConfig(mode="postgresql"),
        )
        managed = create_portfolio_repository(
            managed_config, environ=self.environment
        ).recommendations
        with mock.patch(
            "hormuz._portfolio_sql.PortfolioSQL.now", return_value=NOW
        ):
            evidence = managed.application_evidence(
                self.principal,
                "postgres-managed-application",
                1,
            )
            self.assertEqual(evidence["activation_event_id"], event_id)
            tampered = dict(evidence)
            tampered["activation_digest"] = "0" * 64
            with self.assertRaises(PortfolioError) as caught:
                managed.record_applied(
                    self.principal,
                    "postgres-managed-application",
                    1,
                    tampered,
                )
            self.assertEqual(caught.exception.code, "version_conflict")
            applied = managed.record_applied(
                self.principal,
                "postgres-managed-application",
                1,
                evidence,
            )
        self.assertEqual(applied["state"], "accepted")

    def test_drift_expiry_and_weak_evidence_fail_closed(self) -> None:
        drift = self.generate("postgres-policy-drift")
        assert drift is not None
        with self.runtime(policy=self.candidate):
            with self.assertRaises(PortfolioError) as caught:
                self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/postgres-policy-drift/decisions",
                    body=canonical(self.decision(drift)).encode("ascii"),
                    idempotency_key="postgres-policy-drift-decision",
                )
        self.assertEqual(caught.exception.code, "version_conflict")

        expiring = self.generate(
            "postgres-expiring", expires_at="2026-09-25T00:00:00Z"
        )
        assert expiring is not None
        with self.runtime(now="2026-09-26T00:00:00.000000Z"):
            with self.assertRaises(PortfolioError) as caught:
                self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/postgres-expiring/decisions",
                    body=canonical(self.decision(expiring)).encode("ascii"),
                    idempotency_key="postgres-expired-decision",
                )
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertIsNone(self.generate(
            "postgres-guardrail-suppressed", selected_cohort_id="guarded"
        ))

        with self.runtime():
            self.assertEqual(
                self.service.dispatch(OTHER, "GET", RECOMMENDATIONS)[1]["items"],
                [],
            )

    def test_post_acceptance_scorecard_drift_invalidates_application(self) -> None:
        evaluation = self.generate("postgres-post-acceptance-drift")
        assert evaluation is not None
        with self.runtime():
            self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS
                + "/postgres-post-acceptance-drift/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="postgres-accept-before-scorecard-drift",
            )
        successor = deepcopy(self.scorecard_input)
        successor.update({
            "version": 2,
            "supersedes_version": 1,
            "evaluated_at": "2026-09-22T00:00:00Z",
            "generated_at": "2026-09-23T00:00:00Z",
            "expires_at": "2026-10-20T00:00:00Z",
            "review_after": "2026-10-25T00:00:00Z",
        })
        self.repositories.scorecards.build(self.principal, successor)
        with self.runtime(policy=self.candidate):
            with self.assertRaises(PortfolioError) as caught:
                self.repositories.recommendations.record_applied(
                    self.principal,
                    "postgres-post-acceptance-drift",
                    1,
                    {
                        "activation_event_id": "postgres-policy-after-drift",
                        "activated_policy_digest": self.candidate.content_sha256,
                        "activated_budget_plan": None,
                        "activation_digest": "d" * 64,
                    },
                )
            invalidated = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS + "/postgres-post-acceptance-drift",
            )[1]
        self.assertEqual(caught.exception.code, "version_conflict")
        self.assertEqual(
            (invalidated["state"], invalidated["reason_code"]),
            ("invalidated", "scorecard_drift"),
        )

    def test_schema24_shape_acl_and_immutable_migration(self) -> None:
        with self.psycopg.connect(self.owner_dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_tables WHERE schemaname=%s",
                    (self.schema,),
                )
                self.assertEqual(cursor.fetchone()[0], 92)
                entries = _postgres_acl_entries(
                    cursor,
                    schema=self.schema,
                    migration_login=connection.info.user,
                )
                public_entries = {
                    entry for entry in entries if entry[4] == "PUBLIC"
                }
                boundary = _postgres_acl_boundary(
                    entries - public_entries,
                    schema=self.schema,
                    migration_login=connection.info.user,
                    role_names=(
                        self.runtime_role,
                        self.policy_control_role,
                        self.custody_control_role,
                        self.custody_executor_role,
                    ),
                )
                cursor.execute(
                    "SELECT has_table_privilege(%s,%s,'SELECT'),"
                    "has_function_privilege(%s,%s,'EXECUTE')",
                    (
                        self.runtime_role,
                        f"{self.schema}.policy_control_events",
                        self.runtime_role,
                        (
                            f"{self.schema}."
                            "portfolio_policy_recommendation_active_policy_receipt()"
                        ),
                    ),
                )
                policy_receipt_privileges = cursor.fetchone()
        self.assertEqual(len(public_entries), 20)
        self.assertEqual(
            {(entry[0], entry[4], entry[5]) for entry in public_entries},
            {("routine", "PUBLIC", "EXECUTE")},
        )
        self.assertEqual(
            boundary, _POSTGRES_EXPECTED_ACL_BOUNDARY_BY_VERSION[24]
        )
        self.assertEqual(policy_receipt_privileges, (False, True))
        migration = files("hormuz.migrations.postgresql").joinpath(
            "0024_policy_recommendations.sql"
        ).read_text(encoding="utf-8")
        for table in TABLE_DDL:
            self.assertIn(f"CREATE TABLE {{schema}}.{table}", migration)
            self.assertIn(f"ALTER TABLE {{schema}}.{table} FORCE ROW LEVEL SECURITY", migration)
            self.assertIn(f"CREATE TRIGGER {table}_immutable", migration)
            self.assertIn(
                f"GRANT SELECT, INSERT ON {{schema}}.{table} TO {{runtime_role}};",
                migration,
            )
        self.assertIn(
            "CREATE FUNCTION {schema}."
            "portfolio_policy_recommendation_active_policy_receipt()",
            migration,
        )
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION {schema}."
            "portfolio_policy_recommendation_active_policy_receipt()",
            migration,
        )
        self.assertNotIn(
            "GRANT SELECT ON {schema}.policy_control_events TO {runtime_role}",
            migration,
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
