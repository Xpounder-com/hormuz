from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.policy_document import PolicyDocument
from hormuz.policy_scenarios import PolicyScenarioSuite
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import (
    RECOMMENDATIONS,
    SCOPES,
    PortfolioError,
    canonical,
    validate,
)
from hormuz.store import MonthlyTotals, UsageStore

if __package__:
    from ._portfolio_fixture import ADMIN, OTHER, VIEWER, create_request, registry_config
    from ._role_view_fixture import _activation_request, _plan_request
    from ._sqlite import managed_sqlite_connection
else:  # PostgreSQL ownership discovery imports dependencies from the tests root.
    from _portfolio_fixture import ADMIN, OTHER, VIEWER, create_request, registry_config
    from _role_view_fixture import _activation_request, _plan_request
    from _sqlite import managed_sqlite_connection


SCORECARD_FIXTURE = (
    Path(__file__).parent / "fixtures" / "scorecard" / "runtime-v1.json"
)
NOW = "2026-09-24T12:00:00.000000Z"


def _policy_mapping(*, output_cap: int) -> dict[str, object]:
    return {
        "schema_id": "hormuz.policy-document",
        "schema_version": 1,
        "organization_id": "acme",
        "policies": {
            "organization": {
                "allowed_clients": ["codex"],
                "allowed_models": ["synthetic"],
                "max_output_tokens": output_cap,
            },
            "teams": {},
            "actors": {},
        },
        "egress_controls": {
            "openai": {
                "allow_response_storage": False,
                "allow_background": False,
            },
            "secrets": {"mode": "redact"},
        },
    }


class SQLiteRecommendationRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        self.assertEqual(UsageStore.schema_version, 19)
        UsageStore(self.config.database_path)
        self.repositories = create_portfolio_repository(self.config)
        self.service = PortfolioService(self.config, self.repositories)
        self.principal = self.service.authenticate(ADMIN)
        self.scope = self.service.dispatch(
            ADMIN,
            "POST",
            SCOPES,
            body=canonical(create_request()).encode("ascii"),
            idempotency_key="recommendation-scope",
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
        self.preview = {
            "actor_id": "alice",
            "client": "codex",
            "protocol": "openai",
            "requested_model": "synthetic",
            "requested_output_tokens": 750,
        }

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
        recommendation_id: str = "recommendation-a",
        *,
        version: int = 1,
        supersedes_version: int | None = None,
        scorecard: dict[str, object] | None = None,
        selected_cohort_id: str = "efficient",
        change_type: str = "output_or_cost_cap_change",
        candidate_policy: PolicyDocument | None = None,
        candidate_budget_plan: dict[str, object] | None = None,
        expires_at: str = "2026-10-01T00:00:00Z",
    ) -> dict[str, object]:
        candidate = self.candidate if candidate_policy is None else candidate_policy
        return {
            "recommendation_id": recommendation_id,
            "version": version,
            "supersedes_version": supersedes_version,
            "scorecard": {
                "id": (self.scorecard if scorecard is None else scorecard)[
                    "scorecard_id"
                ],
                "version": (self.scorecard if scorecard is None else scorecard)[
                    "version"
                ],
            },
            "selected_cohort_id": selected_cohort_id,
            "proposal": {
                "change_type": change_type,
                "candidate_policy_digest": (
                    None
                    if change_type == "budget_plan_change"
                    else candidate.content_sha256
                ),
                "candidate_budget_plan": candidate_budget_plan,
            },
            "expires_at": expires_at,
        }

    def generate(
        self,
        value: dict[str, object] | None = None,
        *,
        baseline: PolicyDocument | None = None,
        candidate: PolicyDocument | None = None,
        now: str = NOW,
    ) -> dict[str, object] | None:
        baseline = self.baseline if baseline is None else baseline
        candidate = self.candidate if candidate is None else candidate
        with self.runtime(policy=baseline, now=now):
            return self.repositories.recommendations.generate(
                self.principal,
                self.request(candidate_policy=candidate) if value is None else value,
                baseline_policy=baseline,
                candidate_policy=candidate,
                usage_store=self.usage,
                preview_request=self.preview,
                scenario_suite=self.scenarios,
            )

    def decision(
        self,
        evaluation: dict[str, object],
        *,
        decision: str = "accepted",
    ) -> dict[str, object]:
        return {
            "schema_id": "hormuz.policy-recommendation-decision-request",
            "schema_version": 1,
            "expected_version": evaluation["recommendation"]["version"],
            "decision": decision,
            "reason_code": decision,
            "pre_apply_evidence": (
                evaluation["pre_apply_evidence"] if decision == "accepted" else None
            ),
        }

    def rows(self) -> dict[str, list[dict[str, object]]]:
        with managed_sqlite_connection(self.config.database_path) as connection:
            connection.row_factory = sqlite3.Row
            order = {
                "portfolio_policy_recommendation_snapshots": "sequence",
                "portfolio_policy_recommendation_events": "sequence",
                "portfolio_policy_recommendation_read_audit": "sequence",
                "portfolio_policy_recommendation_cursors": "cursor_id",
            }
            return {
                table: [dict(row) for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {order[table]}"
                ).fetchall()]
                for table in order
            }

    def error(self, code: str, operation) -> None:
        with self.assertRaises(PortfolioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_exact_replay_audited_reads_acceptance_and_local_receipt_boundary(self) -> None:
        evaluation = self.generate()
        self.assertIsNotNone(evaluation)
        assert evaluation is not None
        self.assertEqual(evaluation["schema_id"], "hormuz.recommendation-evaluation")
        validate(evaluation["recommendation"], "hormuz.policy-recommendation")
        first_rows = self.rows()
        self.assertEqual(len(first_rows["portfolio_policy_recommendation_snapshots"]), 1)
        self.assertEqual(len(first_rows["portfolio_policy_recommendation_events"]), 1)
        self.assertEqual(
            self.generate(now="2026-09-24T12:00:01.000000Z"), evaluation
        )
        self.assertEqual(self.rows(), first_rows)

        with self.runtime():
            page = self.service.dispatch(ADMIN, "GET", RECOMMENDATIONS)[1]
            shown = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS + "/recommendation-a",
                query="version=1",
            )[1]
        validate(page, "hormuz.policy-recommendation-page")
        self.assertEqual(page["items"], [shown])
        self.assertFalse(shown["automatic_application"])
        self.assertEqual(shown["state"], "pending")
        self.assertEqual(
            shown["pre_apply_evidence"], evaluation["pre_apply_evidence"]
        )

        value = self.decision(evaluation)
        value["pre_apply_evidence"] = shown["pre_apply_evidence"]
        bad = deepcopy(value)
        bad["pre_apply_evidence"]["semantic_compare"]["digest"] = "0" * 64
        with self.runtime():
            self.error(
                "invalid_request",
                lambda: self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/recommendation-a/decisions",
                    body=canonical(bad).encode("ascii"),
                    idempotency_key="bad-pre-apply-evidence",
                ),
            )
            accepted = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/recommendation-a/decisions",
                body=canonical(value).encode("ascii"),
                idempotency_key="accept-recommendation-a",
            )[1]
            replay = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/recommendation-a/decisions",
                body=canonical(value).encode("ascii"),
                idempotency_key="accept-recommendation-a",
            )[1]
            self.error(
                "idempotency_conflict",
                lambda: self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/recommendation-a/decisions",
                    body=canonical(
                        self.decision(evaluation, decision="rejected")
                    ).encode("ascii"),
                    idempotency_key="accept-recommendation-a",
                ),
            )
        self.assertEqual(replay, accepted)
        self.assertEqual(accepted["state"], "accepted")
        self.assertFalse(accepted["automatic_application"])
        with managed_sqlite_connection(self.config.database_path) as connection:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM portfolio_work_budget_active_plans"
            ).fetchone()[0], 0)

        unsupported_evidence = {
            "activation_event_id": "separate-policy-activation",
            "activated_policy_digest": self.candidate.content_sha256,
            "activated_budget_plan": None,
            "activation_digest": "a" * 64,
        }
        with self.runtime(policy=self.candidate):
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.application_evidence(
                    self.principal,
                    "recommendation-a",
                    1,
                ),
            )
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.record_applied(
                    self.principal,
                    "recommendation-a",
                    1,
                    unsupported_evidence,
                ),
            )
        events = self.rows()["portfolio_policy_recommendation_events"]
        self.assertEqual([row["event_type"] for row in events], [
            "generated", "accepted",
        ])

        with managed_sqlite_connection(self.config.database_path) as connection:
            for statement in (
                "UPDATE portfolio_policy_recommendation_snapshots SET version=version",
                "DELETE FROM portfolio_policy_recommendation_events",
            ):
                with self.assertRaisesRegex(
                    sqlite3.IntegrityError, "recommendation_append_only"
                ):
                    connection.execute(statement)

    def test_authorization_precedes_parse_storage_and_tenant_scope(self) -> None:
        self.generate()
        with mock.patch.object(
            self.repositories.recommendations,
            "_transaction",
            side_effect=AssertionError("unauthorized storage access"),
        ):
            self.error(
                "forbidden",
                lambda: self.service.dispatch(
                    VIEWER,
                    "GET",
                    RECOMMENDATIONS,
                    query="limit=invalid",
                ),
            )
            self.error(
                "forbidden",
                lambda: self.service.dispatch(
                    VIEWER,
                    "POST",
                    RECOMMENDATIONS + "/recommendation-a/decisions",
                    body=b"not-json",
                ),
            )
        with self.runtime():
            self.assertEqual(
                self.service.dispatch(OTHER, "GET", RECOMMENDATIONS)[1]["items"],
                [],
            )
            self.error(
                "not_found",
                lambda: self.service.dispatch(
                    OTHER,
                    "GET",
                    RECOMMENDATIONS + "/recommendation-a",
                ),
            )

    def test_policy_scorecard_and_expiry_drift_are_committed_before_conflict(self) -> None:
        policy_evaluation = self.generate(self.request("policy-drift"))
        assert policy_evaluation is not None
        with self.runtime(policy=self.candidate):
            self.error(
                "version_conflict",
                lambda: self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/policy-drift/decisions",
                    body=canonical(self.decision(policy_evaluation)).encode("ascii"),
                    idempotency_key="policy-drift-decision",
                ),
            )
            invalidated = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS + "/policy-drift"
            )[1]
        self.assertEqual(
            (invalidated["state"], invalidated["reason_code"]),
            ("invalidated", "policy_drift"),
        )

        scorecard_evaluation = self.generate(self.request("scorecard-drift"))
        assert scorecard_evaluation is not None
        expiring = self.generate(self.request(
            "expiring", expires_at="2026-09-25T00:00:00Z"
        ))
        assert expiring is not None
        successor_input = deepcopy(self.scorecard_input)
        successor_input.update({
            "version": 2,
            "supersedes_version": 1,
            "evaluated_at": "2026-09-22T00:00:00Z",
            "generated_at": "2026-09-23T00:00:00Z",
            "expires_at": "2026-10-20T00:00:00Z",
            "review_after": "2026-10-25T00:00:00Z",
        })
        successor = self.repositories.scorecards.build(
            self.principal, successor_input
        )["scorecard"]
        with self.runtime():
            self.error(
                "version_conflict",
                lambda: self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/scorecard-drift/decisions",
                    body=canonical(self.decision(scorecard_evaluation)).encode("ascii"),
                    idempotency_key="scorecard-drift-decision",
                ),
            )
            invalidated = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS + "/scorecard-drift"
            )[1]
        self.assertEqual(invalidated["reason_code"], "scorecard_drift")

        with self.runtime(now="2026-09-26T00:00:00.000000Z"):
            self.error(
                "version_conflict",
                lambda: self.service.dispatch(
                    ADMIN,
                    "POST",
                    RECOMMENDATIONS + "/expiring/decisions",
                    body=canonical(self.decision(expiring)).encode("ascii"),
                    idempotency_key="expired-decision",
                ),
            )
            expired = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS + "/expiring"
            )[1]
        self.assertEqual((expired["state"], expired["reason_code"]), (
            "expired", "expired",
        ))

    def test_policy_change_between_generation_preflight_and_commit_is_rejected(self) -> None:
        baseline = {
            "version": self.baseline.version_id,
            "digest": self.baseline.content_sha256,
        }
        changed = {
            "version": self.candidate.version_id,
            "digest": self.candidate.content_sha256,
        }
        with mock.patch.object(
            self.repositories.recommendations,
            "_active_policy",
            side_effect=(baseline, changed),
        ), mock.patch(
            "hormuz._portfolio_sql.PortfolioSQL.now", return_value=NOW
        ):
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.generate(
                    self.principal,
                    self.request("policy-activation-race"),
                    baseline_policy=self.baseline,
                    candidate_policy=self.candidate,
                    usage_store=self.usage,
                    preview_request=self.preview,
                    scenario_suite=self.scenarios,
                ),
            )
        rows = self.rows()
        self.assertEqual(rows["portfolio_policy_recommendation_snapshots"], [])
        self.assertEqual(rows["portfolio_policy_recommendation_events"], [])

    def test_post_acceptance_budget_drift_invalidates_policy_application(self) -> None:
        evaluation = self.generate(self.request("post-acceptance-budget-drift"))
        assert evaluation is not None
        with self.runtime():
            self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS
                + "/post-acceptance-budget-drift/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="accept-before-budget-drift",
            )
        plan = self.repositories.budgets.create_plan(
            self.principal,
            _plan_request(self.scope, amount="100"),
        )
        self.repositories.budgets.activate_plan(
            self.principal,
            plan["budget_plan_id"],
            _activation_request(plan["version"]),
        )
        with self.runtime(policy=self.candidate):
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.record_applied(
                    self.principal,
                    "post-acceptance-budget-drift",
                    1,
                    {
                        "activation_event_id": "policy-after-budget-drift",
                        "activated_policy_digest": self.candidate.content_sha256,
                        "activated_budget_plan": None,
                        "activation_digest": "d" * 64,
                    },
                ),
            )
            invalidated = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS + "/post-acceptance-budget-drift",
            )[1]
        self.assertEqual(
            (invalidated["state"], invalidated["reason_code"]),
            ("invalidated", "policy_drift"),
        )

    def test_post_acceptance_scorecard_drift_invalidates_policy_application(self) -> None:
        evaluation = self.generate(self.request("post-acceptance-scorecard-drift"))
        assert evaluation is not None
        with self.runtime():
            self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS
                + "/post-acceptance-scorecard-drift/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="accept-before-scorecard-drift",
            )
        successor_input = deepcopy(self.scorecard_input)
        successor_input.update({
            "version": 2,
            "supersedes_version": 1,
            "evaluated_at": "2026-09-22T00:00:00Z",
            "generated_at": "2026-09-23T00:00:00Z",
            "expires_at": "2026-10-20T00:00:00Z",
            "review_after": "2026-10-25T00:00:00Z",
        })
        self.repositories.scorecards.build(self.principal, successor_input)
        with self.runtime(policy=self.candidate):
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.record_applied(
                    self.principal,
                    "post-acceptance-scorecard-drift",
                    1,
                    {
                        "activation_event_id": "policy-after-scorecard-drift",
                        "activated_policy_digest": self.candidate.content_sha256,
                        "activated_budget_plan": None,
                        "activation_digest": "e" * 64,
                    },
                ),
            )
            invalidated = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS + "/post-acceptance-scorecard-drift",
            )[1]
        self.assertEqual(
            (invalidated["state"], invalidated["reason_code"]),
            ("invalidated", "scorecard_drift"),
        )

    def test_conflict_supersession_rejection_and_frozen_pagination(self) -> None:
        first = self.generate(self.request("conflict-a"))
        second = self.generate(self.request("conflict-b"))
        rejected = self.generate(self.request("rejected"))
        assert first is not None and second is not None and rejected is not None

        with self.runtime():
            exact_window = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS,
                query=(
                    "start_at=2026-09-24T12%3A00%3A00Z&"
                    "end_at=2026-09-24T12%3A00%3A01Z"
                ),
            )[1]
            page = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS, query="limit=1"
            )[1]
            self.assertTrue(page["has_more"])
            continuation = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS,
                query="cursor=" + page["next_cursor"],
            )[1]
            self.assertEqual(continuation["as_of"], page["as_of"])
            self.error(
                "cursor_invalid",
                lambda: self.service.dispatch(
                    ADMIN,
                    "GET",
                    RECOMMENDATIONS,
                    query="cursor=" + page["next_cursor"] + "&limit=2",
                ),
            )
            declined = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/rejected/decisions",
                body=canonical(self.decision(rejected, decision="rejected")).encode(
                    "ascii"
                ),
                idempotency_key="reject-recommendation",
            )[1]
            accepted = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/conflict-a/decisions",
                body=canonical(self.decision(first)).encode("ascii"),
                idempotency_key="accept-conflict-a",
            )[1]
            superseded = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS + "/conflict-b"
            )[1]
        self.assertEqual(len(exact_window["items"]), 3)
        self.assertEqual(accepted["state"], "accepted")
        self.assertEqual(superseded["state"], "superseded")
        self.assertEqual(declined["state"], "rejected")

    def test_later_cursor_page_materializes_frozen_expiry_once(self) -> None:
        live = self.generate(
            self.request("page-a-live"),
            now="2026-09-24T09:00:00.000000Z",
        )
        expired = self.generate(
            self.request(
                "page-b-expired", expires_at="2026-09-24T11:00:00Z"
            ),
            now="2026-09-24T10:00:00.000000Z",
        )
        assert live is not None and expired is not None

        with self.runtime(now="2026-09-24T12:00:00.000000Z"):
            first = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS, query="limit=1"
            )[1]
            self.assertEqual(first["items"][0]["recommendation_id"], "page-a-live")
            second = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS,
                query="cursor=" + first["next_cursor"],
            )[1]
            replay = self.service.dispatch(
                ADMIN,
                "GET",
                RECOMMENDATIONS,
                query="cursor=" + first["next_cursor"],
            )[1]
        self.assertEqual(second["as_of"], first["as_of"])
        self.assertEqual(second["items"][0]["state"], "expired")
        self.assertEqual(replay["items"], second["items"])
        self.assertEqual(
            sum(
                row["event_type"] == "expired"
                for row in self.rows()["portfolio_policy_recommendation_events"]
            ),
            1,
        )

    def test_different_policy_change_categories_conflict(self) -> None:
        cap = self.generate(self.request("policy-cap"))
        allowlist_mapping = _policy_mapping(output_cap=1_000)
        allowlist_mapping["policies"]["organization"]["allowed_models"] = []
        allowlist = PolicyDocument.from_mapping(
            allowlist_mapping, config=self.config
        )
        model = self.generate(
            self.request(
                "policy-models",
                change_type="model_allowlist_change",
                candidate_policy=allowlist,
            ),
            candidate=allowlist,
        )
        assert cap is not None and model is not None

        with self.runtime():
            self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/policy-cap/decisions",
                body=canonical(self.decision(cap)).encode("ascii"),
                idempotency_key="accept-policy-cap",
            )
            superseded = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS + "/policy-models"
            )[1]
        self.assertEqual(superseded["state"], "superseded")

    def test_accepted_expiry_releases_conflict_and_blocks_late_application(self) -> None:
        expiring = self.generate(self.request(
            "accepted-expiry", expires_at="2026-09-25T00:00:00Z"
        ))
        assert expiring is not None
        with self.runtime():
            self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/accepted-expiry/decisions",
                body=canonical(self.decision(expiring)).encode("ascii"),
                idempotency_key="accept-expiring",
            )
        replacement = self.generate(self.request("replacement"))
        assert replacement is not None

        late = "2026-09-26T00:00:00.000000Z"
        with self.runtime(now=late):
            expired = self.service.dispatch(
                ADMIN, "GET", RECOMMENDATIONS + "/accepted-expiry"
            )[1]
            accepted = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/replacement/decisions",
                body=canonical(self.decision(replacement)).encode("ascii"),
                idempotency_key="accept-replacement",
            )[1]
        self.assertEqual(expired["state"], "expired")
        self.assertEqual(accepted["state"], "accepted")

        with self.runtime(policy=self.candidate, now=late):
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.record_applied(
                    self.principal,
                    "accepted-expiry",
                    1,
                    {
                        "activation_event_id": "late-policy-activation",
                        "activated_policy_digest": self.candidate.content_sha256,
                        "activated_budget_plan": None,
                        "activation_digest": "c" * 64,
                    },
                ),
            )

    def test_concurrent_conflicting_acceptance_has_one_winner(self) -> None:
        first = self.generate(self.request("race-a"))
        second = self.generate(self.request("race-b"))
        assert first is not None and second is not None
        barrier = threading.Barrier(2)

        def accept(case):
            recommendation_id, evaluation, key = case
            repositories = create_portfolio_repository(self.config)
            service = PortfolioService(self.config, repositories)
            active = {
                "version": self.baseline.version_id,
                "digest": self.baseline.content_sha256,
            }
            with mock.patch.object(
                repositories.recommendations,
                "_active_policy",
                return_value=active,
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

        with mock.patch(
            "hormuz._portfolio_sql.PortfolioSQL.now", return_value=NOW
        ), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, (
                ("race-a", first, "race-a-accept"),
                ("race-b", second, "race-b-accept"),
            )))
        self.assertCountEqual(results, [200, "version_conflict"])
        events = self.rows()["portfolio_policy_recommendation_events"]
        self.assertEqual(
            sum(row["event_type"] == "accepted" for row in events), 1
        )
        self.assertEqual(
            sum(row["event_type"] == "superseded" for row in events), 1
        )

    def test_weak_evidence_is_suppressed_and_change_types_are_strict(self) -> None:
        self.assertIsNone(self.generate(self.request(
            "guardrail-suppressed", selected_cohort_id="guarded"
        )))
        mismatch = self.request(
            "mismatch", change_type="model_allowlist_change"
        )
        self.error("invalid_request", lambda: self.generate(mismatch))

        weak_input = deepcopy(self.scorecard_input)
        weak_input.update({
            "scorecard_id": "weak-scorecard",
            "version": 1,
            "supersedes_version": None,
        })
        for cohort in weak_input["cohorts"]:
            cohort["coverage"]["connector"]["numerator"] = "0"
        weak = self.repositories.scorecards.build(self.principal, weak_input)["scorecard"]
        self.assertEqual(weak["state"], "inconclusive")
        self.assertIsNone(self.generate(self.request(
            "weak-suppressed", scorecard=weak
        )))

    def test_budget_recommendation_requires_separate_activation(self) -> None:
        active = self.repositories.budgets.create_plan(
            self.principal,
            _plan_request(self.scope, amount="100"),
        )
        self.repositories.budgets.activate_plan(
            self.principal,
            active["budget_plan_id"],
            _activation_request(active["version"]),
        )
        candidate = self.repositories.budgets.create_plan(
            self.principal,
            _plan_request(
                self.scope,
                amount="75",
                budget_plan_id=active["budget_plan_id"],
                expected_version=active["version"],
            ),
        )
        value = self.request(
            "budget-change",
            change_type="budget_plan_change",
            candidate_budget_plan={
                "id": candidate["budget_plan_id"],
                "version": candidate["version"],
            },
        )
        evaluation = self.generate(
            value,
            baseline=self.baseline,
            candidate=self.baseline,
        )
        assert evaluation is not None
        with self.runtime():
            accepted = self.service.dispatch(
                ADMIN,
                "POST",
                RECOMMENDATIONS + "/budget-change/decisions",
                body=canonical(self.decision(evaluation)).encode("ascii"),
                idempotency_key="accept-budget-change",
            )[1]
        self.assertEqual(accepted["state"], "accepted")

        with managed_sqlite_connection(self.config.database_path) as connection:
            pointer = connection.execute(
                "SELECT active_version,activation_generation,current_activation_event_id "
                "FROM portfolio_work_budget_active_plans WHERE organization_id='acme' "
                "AND budget_plan_id=?",
                (candidate["budget_plan_id"],),
            ).fetchone()
        self.assertEqual(pointer[0], active["version"])

        self.repositories.budgets.activate_plan(
            self.principal,
            candidate["budget_plan_id"],
            _activation_request(
                candidate["version"],
                expected_active_version=active["version"],
                expected_activation_generation=1,
            ),
        )
        with managed_sqlite_connection(self.config.database_path) as connection:
            activation_event_id = connection.execute(
                "SELECT current_activation_event_id "
                "FROM portfolio_work_budget_active_plans WHERE organization_id='acme' "
                "AND budget_plan_id=?",
                (candidate["budget_plan_id"],),
            ).fetchone()[0]
        with self.runtime():
            applied_evidence = (
                self.repositories.recommendations.application_evidence(
                    self.principal, "budget-change", 1
                )
            )
            self.assertEqual(
                applied_evidence["activation_event_id"], activation_event_id
            )
            wrong_evidence = deepcopy(applied_evidence)
            wrong_evidence["activation_event_id"] = "wrong-budget-activation"
            self.error(
                "version_conflict",
                lambda: self.repositories.recommendations.record_applied(
                    self.principal,
                    "budget-change",
                    1,
                    wrong_evidence,
                ),
            )
            result = self.repositories.recommendations.record_applied(
                self.principal,
                "budget-change",
                1,
                applied_evidence,
            )
        self.assertEqual(result["state"], "accepted")

    def test_expired_candidate_budget_plan_is_suppressed(self) -> None:
        request = _plan_request(self.scope, amount="75")
        request["window"] = {
            "start_at": "2026-09-23T00:00:00.000000Z",
            "end_at": "2026-09-25T00:00:00.000000Z",
        }
        candidate = self.repositories.budgets.create_plan(
            self.principal, request
        )
        reference = {
            "id": candidate["budget_plan_id"],
            "version": candidate["version"],
        }
        replayable = self.request(
            "expiring-budget-change",
            change_type="budget_plan_change",
            candidate_budget_plan=reference,
        )
        evaluation = self.generate(
            replayable,
            baseline=self.baseline,
            candidate=self.baseline,
        )
        assert evaluation is not None
        self.assertEqual(
            self.generate(
                replayable,
                baseline=self.baseline,
                candidate=self.baseline,
                now="2026-09-26T00:00:00.000000Z",
            ),
            evaluation,
        )

        expired = self.request(
            "expired-budget-change",
            change_type="budget_plan_change",
            candidate_budget_plan=reference,
        )
        self.assertIsNone(
            self.generate(
                expired,
                baseline=self.baseline,
                candidate=self.baseline,
                now="2026-09-26T00:00:00.000000Z",
            )
        )


if __name__ == "__main__":
    unittest.main()
