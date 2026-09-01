"""Synthetic work-budget behavior shared by SQLite and PostgreSQL tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import runpy
import threading
from unittest import mock

from hormuz._persistence import WorkBudgetContext
from hormuz.budget_runtime import WorkBudgetDenied
from hormuz.budget_repository import BudgetRepositoryError
from hormuz.config import ModelRoute, Policy
from hormuz.policy_scenarios import create_policy_scenario_suite
from hormuz.policy_document import local_policy_content_sha256
from hormuz.portfolio_config import PortfolioPrincipal
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import SCOPES, canonical
from hormuz.store import ReservationDenied, ReservationScope

try:
    from tools._portfolio_wire_contract import validate_wire_payload
except ModuleNotFoundError:
    # Installed-wheel CI runs the source tests under ``python -I``. Keep this
    # source-only contract checker outside the runtime wheel while loading the
    # exact checked-in verifier from the test kit.
    validate_wire_payload = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "tools" / "_portfolio_wire_contract.py")
    )["validate_wire_payload"]

if __package__:
    from ._portfolio_fixture import (
        ADMIN as ADMIN_TOKEN, VIEWER as VIEWER_TOKEN, create_request, version_request,
    )
else:
    from _portfolio_fixture import (
        ADMIN as ADMIN_TOKEN, VIEWER as VIEWER_TOKEN, create_request, version_request,
    )


ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))
VIEWER = PortfolioPrincipal("acme", "finance", ("finance_viewer",))
BUDGET_WIRE = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/work-budget-reports-wire-v2.json").read_bytes()
)
PLAN_WIRE = json.loads(
    (Path(__file__).resolve().parents[1] / "docs/portfolio-intelligence-wire-v1.json").read_bytes()
)
TEST_DIGEST = "a" * 64


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def plan_request(scope, *, amount="1", budget_plan_id=None, expected_version=None,
                 allowed_models=None, output_token_cap=None, per_request_cost_cap=None,
                 reason_code=None, start_at=None, end_at=None, currency="USD"):
    now = datetime.now(timezone.utc)
    return {
        "schema_id": "hormuz.work-budget-plan-request",
        "schema_version": 1,
        "budget_plan_id": budget_plan_id,
        "expected_version": expected_version,
        "work_scope": {"work_scope_id": scope["work_scope_id"], "version": scope["version"]},
        "window": {
            "start_at": start_at or _timestamp(now - timedelta(days=1)),
            "end_at": end_at or _timestamp(now + timedelta(days=1)),
        },
        "currency": currency,
        "amount": amount,
        "allowed_models": allowed_models,
        "output_token_cap": output_token_cap,
        "per_request_cost_cap": per_request_cost_cap,
        "reason_code": reason_code or ("created" if budget_plan_id is None else "corrected"),
    }


def activation_request(version, *, active=None, generation=0, reason_code="accepted"):
    return {
        "schema_id": "hormuz.work-budget-plan-activation-request",
        "schema_version": 1,
        "version": version,
        "expected_active_version": active,
        "expected_activation_generation": generation,
        "reason_code": reason_code,
    }


class BudgetAssertions:
    def setup_budget(self):
        repositories = create_portfolio_repository(
            self.config, environ=self.environment,
        )
        registry = PortfolioService(self.config, repositories)
        self.registry = registry
        self.portfolio_scope = registry.dispatch(
            ADMIN_TOKEN, "POST", SCOPES,
            body=canonical(create_request(kind="portfolio")).encode(),
            idempotency_key="budget-portfolio",
        )[1]
        self.initiative_scope = registry.dispatch(
            ADMIN_TOKEN, "POST", SCOPES,
            body=canonical(create_request(
                kind="initiative", parent_work_scope_id=self.portfolio_scope["work_scope_id"],
            )).encode(),
            idempotency_key="budget-initiative",
        )[1]
        self.scope = registry.dispatch(
            ADMIN_TOKEN, "POST", SCOPES,
            body=canonical(create_request(
                parent_work_scope_id=self.initiative_scope["work_scope_id"],
            )).encode(), idempotency_key="budget-use-case",
        )[1]
        self.identity = self.config.identities_by_token[ADMIN_TOKEN]
        self.repository = repositories.budgets
        self.assertIsNotNone(self.repository)

    def error(self, code, operation):
        with self.assertRaises(BudgetRepositoryError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def create(self, **changes):
        return self.create_for(self.scope, **changes)

    def create_for(self, scope, **changes):
        result = self.repository.create_plan(ADMIN, plan_request(scope, **changes))
        validate_wire_payload(PLAN_WIRE, "hormuz.work-budget-plan", result)
        return result

    def activate(self, plan, **changes):
        result = self.repository.activate_plan(
            ADMIN, plan["budget_plan_id"], activation_request(plan["version"], **changes),
        )
        validate_wire_payload(PLAN_WIRE, "hormuz.work-budget-plan", result)
        return result

    def context(self, *, output_tokens=20):
        return WorkBudgetContext(
            work_scope_id=self.scope["work_scope_id"],
            work_scope_version=self.scope["version"],
            confidence="explicit_authorized",
            reason_code="bound",
            reserved_output_tokens=output_tokens,
            output_tokens_bounded=True,
            policy_version="budget-policy-v1",
            policy_digest=TEST_DIGEST,
            rate_card_id="synthetic-route-rate",
            rate_card_version=1,
            rate_card_digest=TEST_DIGEST,
            rate_card_currency="USD",
        )

    def attempt(self, *, cost_microusd, output_tokens=20,
                output_tokens_bounded=True, store=None, scopes=None,
                requested_model="synthetic", resolved_alias="synthetic",
                upstream_model="synthetic"):
        store = store or self.store
        return store._begin_request_attempt_with_work_budget(
            identity=self.identity, client="codex", protocol="openai",
            requested_model=requested_model, resolved_alias=resolved_alias,
            upstream_model=upstream_model, policy_version="budget-policy-v1",
            policy_action="allowed", redaction_count=0, redaction_rules=(),
            scopes=(ReservationScope(name="organization"),) if scopes is None else scopes,
            reserved_tokens=output_tokens + 10,
            reserved_cost_microusd=cost_microusd, ttl_seconds=60,
            work_budget=replace(
                self.context(output_tokens=output_tokens),
                output_tokens_bounded=output_tokens_bounded,
            ),
        )

    def check_configured_route_identity_accepts_provider_model_names(self):
        plan = self.create(
            amount="10",
            allowed_models=[{
                "provider_id": "openai",
                "model_id": "managed-route",
                "model_version": None,
            }],
        )
        self.activate(plan)
        provider_model = "vendor/" + "model" * 32

        attempt = self.attempt(
            cost_microusd=1,
            requested_model="managed-route",
            resolved_alias="managed-route",
            upstream_model=provider_model,
        )

        binding = next(
            row
            for row in self.budget_rows()["portfolio_work_budget_reservation_bindings"]
            if row["request_attempt_id"] == attempt.attempt_id
        )
        self.assertGreater(len(provider_model), 128)
        self.assertEqual(binding["provider_id"], "openai")
        self.assertEqual(binding["model_id"], "managed-route")
        self.assertIsNone(binding["model_version"])

        managed_config = replace(
            self.config,
            model_routes={
                "managed-route": ModelRoute(
                    "managed-route", "openai", provider_model,
                ),
            },
        )
        suite = create_policy_scenario_suite(
            organization_id="acme",
            scenario_id="provider-native-model-name",
            actor_id="alice",
            client="codex",
            protocol="openai",
            requested_model="managed-route",
            requested_output_tokens=20,
        )
        with mock.patch.object(self.repository, "config", managed_config):
            preview = self.repository.preview_plan(
                ADMIN, plan["budget_plan_id"], plan["version"], suite,
            )
        self.assertNotIn("model_intersection", preview["restriction_reasons"])

    def check_plan_versions_activation_and_management_change(self):
        start, end = "2026-08-01T00:00:00Z", "2027-09-01T00:00:00Z"
        first = self.create(amount="100", start_at=start, end_at=end)
        self.assertEqual((first["version"], first["active_version"], first["activation_generation"]), (1, None, 0))
        self.assertIsNone(first["state"])
        active = self.activate(first)
        self.assertEqual((active["active_version"], active["activation_generation"], active["state"]), (1, 1, "active"))
        initial = self.repository.current_report(ADMIN, first["budget_plan_id"])
        validate_wire_payload(BUDGET_WIRE, "hormuz.work-budget-report", initial)
        self.assertEqual(initial["schema_version"], 2)
        self.assertEqual(initial["plan_change"]["kind"], "established")
        self.assertIsNone(initial["plan_change"]["percent_delta"])
        self.assertEqual(initial["coverage"]["reason_code"], "missing_evidence")
        self.assertEqual(initial["forecast"]["reason_code"], "missing_evidence")

        second = self.create(
            amount="120", budget_plan_id=first["budget_plan_id"], expected_version=1,
            start_at=start, end_at=end,
        )
        self.assertEqual(second["version"], 2)
        active = self.activate(second, active=1, generation=1)
        self.assertEqual((active["active_version"], active["activation_generation"]), (2, 2))
        report = self.repository.current_report(ADMIN, first["budget_plan_id"])
        validate_wire_payload(BUDGET_WIRE, "hormuz.work-budget-report", report)
        change = report["plan_change"]
        self.assertEqual((change["kind"], change["amount_delta"], change["percent_delta"]),
                         ("increased", "20", "20"))
        self.assertEqual(change["previous_plan"]["version"], 1)
        latest_activation = max(
            self.budget_rows()["portfolio_work_budget_activation_events"],
            key=lambda item: item["activation_generation"],
        )
        self.assertEqual(change["changed_at"], latest_activation["committed_at"])
        self.assertEqual(
            latest_activation["policy_digest"],
            local_policy_content_sha256(self.config),
        )

        # Change the comparison basis while retaining a currently enforceable window.
        third = self.create(
            amount="4", budget_plan_id=first["budget_plan_id"], expected_version=2,
            start_at="2026-07-01T00:00:00Z", end_at="2027-08-01T00:00:00Z",
        )
        self.assertEqual(third["supersedes_version"], 2)
        self.activate(third, active=2, generation=2)
        noncomparable = self.repository.current_report(ADMIN, first["budget_plan_id"])["plan_change"]
        self.assertEqual(noncomparable["kind"], "not_comparable")
        self.assertEqual(noncomparable["comparison_reasons"], ["window_changed"])

    def check_compare_and_set_authority_and_strict_values(self):
        first = self.create()
        self.error("version_conflict", lambda: self.create(
            budget_plan_id=first["budget_plan_id"], expected_version=99,
        ))
        self.error("version_conflict", lambda: self.activate(first, active=1, generation=0))
        self.activate(first)
        self.error("version_conflict", lambda: self.activate(first))
        for changes in (
            {"amount": "01"}, {"amount": "-1"}, {"amount": "1e3"},
            {"amount": Decimal("1")}, {"amount": "1.0000000001"},
            {"currency": "usd"},
            {"allowed_models": ["synthetic", "synthetic"]},
            {"output_token_cap": True}, {"per_request_cost_cap": "nan"},
            {"per_request_cost_cap": "0.0000000001"},
        ):
            self.error("invalid_request", lambda changes=changes: self.create(**changes))
        self.error("version_conflict", lambda: self.activate(
            first, generation=2147483648,
        ))
        self.error("invalid_request", lambda: self.activate(
            first, generation=9007199254740992,
        ))
        largest_allowlist = [{
            "provider_id": "p" + "x" * 127,
            "model_id": f"m{index:03d}" + "x" * 124,
            "model_version": "v" + "x" * 127,
        } for index in range(100)]
        largest = self.create(allowed_models=largest_allowlist)
        self.assertEqual(len(largest["allowed_models"]), 100)
        with mock.patch.object(self.repository, "_transaction", side_effect=AssertionError("unauthorized_io")):
            self.error("forbidden", lambda: self.repository.create_plan(VIEWER, plan_request(self.scope)))
            self.error("forbidden", lambda: self.repository.current_report(VIEWER, first["budget_plan_id"]))

    def check_preview_is_read_only_and_scenario_backed(self):
        plan = self.create(allowed_models=[{
            "provider_id": "openai", "model_id": "synthetic", "model_version": None,
        }], output_token_cap=100)
        suite = create_policy_scenario_suite(
            organization_id="acme", scenario_id="allowed", actor_id="alice",
            client="codex", protocol="openai", requested_model="synthetic",
            requested_output_tokens=20,
        )
        before = self.budget_rows()
        preview = self.repository.preview_plan(ADMIN, plan["budget_plan_id"], plan["version"], suite)
        validate_wire_payload(BUDGET_WIRE, "hormuz.work-budget-preview", preview)
        self.assertEqual(preview["result"], "inconclusive")
        self.assertEqual(preview["simulation"]["evaluated_attempts"], 1)
        self.assertEqual(preview["simulation"]["inconclusive_attempts"], 1)
        self.assertEqual(preview["restriction_reasons"], ["missing_evidence"])
        self.assertFalse(preview["activation_permitted"])
        after = self.budget_rows()
        # Preview adds only its mandatory privileged-read audit.
        self.assertEqual(before["portfolio_work_budget_plan_versions"], after["portfolio_work_budget_plan_versions"])
        self.assertEqual(before["portfolio_work_budget_active_plans"], after["portfolio_work_budget_active_plans"])
        restrictive = self.create(
            budget_plan_id=plan["budget_plan_id"], expected_version=1, allowed_models=[],
        )
        preview = self.repository.preview_plan(ADMIN, plan["budget_plan_id"], restrictive["version"], suite)
        self.assertEqual(preview["result"], "would_restrict")
        self.assertIn("model_intersection", preview["restriction_reasons"])

        zero = self.create(amount="0")
        zero_preview = self.repository.preview_plan(
            ADMIN, zero["budget_plan_id"], zero["version"], suite,
        )
        self.assertEqual(zero_preview["result"], "inconclusive")
        self.assertEqual(zero_preview["restriction_reasons"], ["missing_evidence"])

        parent = self.create_for(self.portfolio_scope, allowed_models=[])
        self.activate(parent)
        hierarchy_preview = self.repository.preview_plan(
            ADMIN, plan["budget_plan_id"], plan["version"], suite,
        )
        self.assertEqual(hierarchy_preview["result"], "would_restrict")
        self.assertIn(
            "model_intersection", hierarchy_preview["restriction_reasons"],
        )

        unbounded_suite = create_policy_scenario_suite(
            organization_id="acme", scenario_id="unbounded", actor_id="alice",
            client="codex", protocol="openai", requested_model="synthetic",
            requested_output_tokens=None,
        )
        unbounded_preview = self.repository.preview_plan(
            ADMIN, zero["budget_plan_id"], zero["version"], unbounded_suite,
        )
        self.assertEqual(unbounded_preview["result"], "would_restrict")
        self.assertIn(
            "request_cost_ceiling", unbounded_preview["restriction_reasons"],
        )

        actor_config = replace(
            self.config,
            actor_policies={"finance": Policy(allowed_models=())},
        )
        finance_suite = create_policy_scenario_suite(
            organization_id="acme", scenario_id="finance-restricted",
            actor_id="finance", client="codex", protocol="openai",
            requested_model="synthetic", requested_output_tokens=20,
        )
        with mock.patch.object(self.repository, "config", actor_config):
            actor_preview = self.repository.preview_plan(
                ADMIN, plan["budget_plan_id"], plan["version"], finance_suite,
            )
        self.assertEqual(actor_preview["result"], "would_restrict")
        self.assertIn("policy_drift", actor_preview["restriction_reasons"])

    def check_atomic_budget_binding_reconciliation_and_unknown_holds(self):
        plan = self.create(amount="1")
        self.activate(plan)
        first = self.attempt(cost_microusd=600_000)
        self.assertIsNotNone(first.attribution_event_id)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=500_000)
        rows = self.budget_rows()
        self.assertEqual(len(rows["portfolio_work_budget_reservation_bindings"]), 1)
        self.assertEqual(len(self.attribution_rows()), 1)
        self.store.finalize_request_attempt(
            attempt=first, organization_id="acme", status="succeeded", cost_microusd=400_000,
        )
        second = self.attempt(cost_microusd=500_000)
        self.store.mark_request_attempt_outcome_unknown(
            attempt=second, organization_id="acme", reason_code="provider_transport_ambiguous",
        )
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=200_000)
        report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        enforcement = report["enforcement"]
        self.assertEqual(enforcement["committed_amount"], "0.4")
        self.assertEqual(enforcement["pending_reservation_amount"], "0")
        self.assertEqual(enforcement["uncertain_reservation_amount"], "0.5")
        self.assertEqual(enforcement["remaining_amount"], "0.1")
        self.assertEqual(enforcement["over_cap_attempts"], 2)
        self.assertEqual(report["coverage"]["population_attempts"], 4)
        self.assertEqual(report["coverage"]["included_attempts"], 4)
        self.assertEqual(report["financial_observations"][0]["rate_card"], {
            "id": "synthetic-route-rate", "version": 1, "content_digest": TEST_DIGEST,
        })
        binding = next(
            row for row in self.budget_rows()["portfolio_work_budget_reservation_bindings"]
            if row["request_attempt_id"] == first.attempt_id
        )
        self.assertEqual(binding["attribution_event_id"], first.attribution_event_id)
        self.assertEqual(binding["request_policy_version"], "budget-policy-v1")
        self.assertEqual(binding["rate_card_digest"], TEST_DIGEST)

    def check_model_output_request_caps_and_missing_attribution_fail_closed(self):
        plan = self.create(
            amount="10", allowed_models=[{
                "provider_id": "openai", "model_id": "synthetic", "model_version": None,
            }], output_token_cap=20,
            per_request_cost_cap="0.5",
        )
        self.activate(plan)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=100_000, output_tokens=21)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=500_001, output_tokens=20)
        with self.assertRaises(ReservationDenied):
            self.attempt(
                cost_microusd=0, output_tokens=0,
                output_tokens_bounded=False,
            )
        bad = WorkBudgetContext(
            None, None, "unattributed", "missing_evidence", 20, True,
            "budget-policy-v1", TEST_DIGEST,
            "synthetic-route-rate", 1, TEST_DIGEST, "USD",
        )
        with self.assertRaises(ReservationDenied):
            self.store._begin_request_attempt_with_work_budget(
                identity=self.identity, client="codex", protocol="openai",
                requested_model="synthetic", resolved_alias="synthetic", upstream_model="synthetic",
                policy_version="budget-policy-v1", policy_action="allowed", redaction_count=0,
                redaction_rules=(), scopes=(), reserved_tokens=30, reserved_cost_microusd=100_000,
                ttl_seconds=60, work_budget=bad,
            )
        self.assertEqual(self.budget_rows()["portfolio_work_budget_reservation_bindings"], [])
        self.assertEqual(self.attribution_rows(), [])
        reasons = [
            row["reason_code"] for row in self.budget_rows()["portfolio_work_budget_audit_events"]
            if row["operation"] == "reserve_denied"
        ]
        self.assertEqual(sorted(reasons), [
            "attribution_invalid", "output_token_ceiling",
            "request_cost_ceiling", "request_cost_ceiling",
        ])
        report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        self.assertEqual(report["enforcement"]["over_cap_attempts"], 3)
        self.assertEqual(report["coverage"]["population_attempts"], 4)
        self.assertEqual(report["coverage"]["included_attempts"], 3)
        self.assertEqual(report["coverage"]["unattributed_attempts"], 1)

    def check_concurrent_instances_cannot_overspend(self):
        plan = self.create(amount="1")
        self.activate(plan)
        stores = (self.new_store(), self.new_store())
        barrier = threading.Barrier(2)

        def reserve(store):
            barrier.wait(timeout=10)
            try:
                return self.attempt(cost_microusd=600_000, store=store)
            except ReservationDenied:
                return "denied"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, stores))
        self.assertEqual(sum(result == "denied" for result in results), 1)
        self.assertEqual(len(self.budget_rows()["portfolio_work_budget_reservation_bindings"]), 1)

    def check_hierarchy_deny_wins_and_legacy_ceiling_is_atomic(self):
        portfolio = self.create_for(self.portfolio_scope, amount="2")
        initiative = self.create_for(self.initiative_scope, amount="1.5")
        use_case = self.create(amount="1")
        for plan in (portfolio, initiative, use_case):
            self.activate(plan)
        first = self.attempt(cost_microusd=600_000)
        self.assertIsNotNone(first.attribution_event_id)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=500_000)
        bindings = self.budget_rows()["portfolio_work_budget_reservation_bindings"]
        self.assertEqual(len(bindings), 3)
        self.assertEqual({row["budget_plan_id"] for row in bindings}, {
            portfolio["budget_plan_id"], initiative["budget_plan_id"], use_case["budget_plan_id"],
        })
        self.assertEqual(
            self.repository.current_report(ADMIN, use_case["budget_plan_id"])["enforcement"]["over_cap_attempts"],
            1,
        )
        self.assertEqual(
            self.repository.current_report(ADMIN, portfolio["budget_plan_id"])["enforcement"]["over_cap_attempts"],
            0,
        )

        # A legacy organization ceiling fails in the same transaction: no
        # attempt, attribution, hold, or partial parent/work-plan binding leaks.
        isolated = self.create(amount="5")
        self.activate(isolated)
        before_bindings = self.budget_rows()["portfolio_work_budget_reservation_bindings"]
        before_attribution = self.attribution_rows()
        with self.assertRaises(ReservationDenied):
            self.attempt(
                cost_microusd=600_000,
                scopes=(ReservationScope(name="organization", cost_limit_microusd=500_000),),
            )
        self.assertEqual(self.budget_rows()["portfolio_work_budget_reservation_bindings"], before_bindings)
        self.assertEqual(self.attribution_rows(), before_attribution)

    def check_model_intersection_exact_decimal_and_deny_audit(self):
        parent = self.create_for(self.portfolio_scope, amount="1", allowed_models=[{
            "provider_id": "openai", "model_id": "synthetic", "model_version": None,
        }])
        child = self.create_for(self.initiative_scope, amount="1", allowed_models=[])
        self.activate(parent)
        self.activate(child)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        self.assertEqual(self.budget_rows()["portfolio_work_budget_reservation_bindings"], [])
        child_report = self.repository.current_report(ADMIN, child["budget_plan_id"])
        self.assertEqual(child_report["enforcement"]["over_cap_attempts"], 0)
        self.assertEqual(child_report["coverage"]["population_attempts"], 1)

    def check_replacement_emergency_tightening_rollback_and_schedule_bounds(self):
        now = datetime.now(timezone.utc)
        start, end = _timestamp(now - timedelta(days=1)), _timestamp(now + timedelta(days=1))
        first = self.create(amount="1", start_at=start, end_at=end)
        self.activate(first)
        self.attempt(cost_microusd=800_000)
        tighter = self.create(
            amount="0.5", budget_plan_id=first["budget_plan_id"], expected_version=1,
            start_at=start, end_at=end,
        )
        self.activate(tighter, active=1, generation=1)
        tightened = self.repository.current_report(ADMIN, first["budget_plan_id"])
        self.assertEqual(tightened["enforcement"]["remaining_amount"], "-0.3")
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        rolled_back = self.activate(
            first, active=2, generation=2, reason_code="reactivated",
        )
        self.assertEqual((rolled_back["active_version"], rolled_back["activation_generation"]), (1, 3))
        self.attempt(cost_microusd=100_000)
        report = self.repository.current_report(ADMIN, first["budget_plan_id"])
        self.assertEqual(report["enforcement"]["pending_reservation_amount"], "0.9")
        self.assertEqual(report["enforcement"]["remaining_amount"], "0.1")
        self.error("version_conflict", lambda: self.activate(
            first, active=1, generation=3, reason_code="reactivated",
        ))

        future = self.create(
            amount="2", budget_plan_id=first["budget_plan_id"], expected_version=2,
            start_at=_timestamp(now + timedelta(days=2)), end_at=_timestamp(now + timedelta(days=3)),
        )
        self.error("invalid_request", lambda: self.activate(future, active=1, generation=3))
        expired = self.create(
            amount="2", budget_plan_id=first["budget_plan_id"], expected_version=3,
            start_at=_timestamp(now - timedelta(days=3)), end_at=_timestamp(now - timedelta(days=2)),
        )
        self.error("invalid_request", lambda: self.activate(expired, active=1, generation=3))

    def check_hierarchy_change_never_resets_same_period_spend(self):
        start, end = "2026-08-01T00:00:00Z", "2027-09-01T00:00:00Z"
        first = self.create(amount="1", start_at=start, end_at=end)
        self.activate(first)
        self.attempt(cost_microusd=800_000)
        moved = self.create_for(
            self.initiative_scope,
            amount="1",
            start_at=start,
            end_at=end,
            budget_plan_id=first["budget_plan_id"],
            expected_version=1,
        )
        self.activate(moved, active=1, generation=1)
        report = self.repository.current_report(ADMIN, moved["budget_plan_id"])
        self.assertEqual(report["plan_change"]["comparison_status"], "not_comparable")
        self.assertEqual(report["plan_change"]["comparison_reasons"], ["work_scope_changed"])
        self.assertEqual(report["coverage"]["population_attempts"], 1)
        self.assertEqual(report["enforcement"]["pending_reservation_amount"], "0.8")
        self.assertEqual(report["enforcement"]["remaining_amount"], "0.2")
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=300_000)

    def check_archived_and_ambiguous_attribution_fail_closed(self):
        plan = self.create(amount="1")
        self.activate(plan)
        self.registry.dispatch(
            ADMIN_TOKEN, "POST", SCOPES + "/" + self.scope["work_scope_id"] + "/versions",
            body=canonical(version_request(
                self.scope, state="archived", reason_code="archived",
            )).encode(),
            idempotency_key="budget-archive-use-case",
        )
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        ambiguous = WorkBudgetContext(
            None, None, "ambiguous", "ambiguous", 20, True,
            "budget-policy-v1", TEST_DIGEST,
            "synthetic-route-rate", 1, TEST_DIGEST, "USD",
        )
        with self.assertRaises(ReservationDenied):
            self.store._begin_request_attempt_with_work_budget(
                identity=self.identity, client="codex", protocol="openai",
                requested_model="synthetic", resolved_alias="synthetic", upstream_model="synthetic",
                policy_version="budget-policy-v1", policy_action="allowed", redaction_count=0,
                redaction_rules=(), scopes=(), reserved_tokens=30, reserved_cost_microusd=1,
                ttl_seconds=60, work_budget=ambiguous,
            )
        report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        self.assertEqual(report["coverage"]["population_attempts"], 2)
        self.assertEqual(report["coverage"]["included_attempts"], 0)
        self.assertEqual(report["coverage"]["unattributed_attempts"], 2)

    def check_exact_microusd_boundary(self):
        plan = self.create(amount="0.000001", per_request_cost_cap="0.000001")
        self.activate(plan)
        self.attempt(cost_microusd=1)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        binding = self.budget_rows()["portfolio_work_budget_reservation_bindings"][0]
        self.assertEqual(binding["reserved_amount"], "0.000001")
        report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        self.assertEqual(report["enforcement"]["pending_reservation_amount"], "0.000001")
        self.assertEqual(report["enforcement"]["remaining_amount"], "0")

    def check_unsupported_currency_and_future_report_fail_safely(self):
        previous = self.create(amount="0")
        self.activate(previous)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        plan = self.create(
            amount="1", currency="EUR", budget_plan_id=previous["budget_plan_id"],
            expected_version=1,
        )
        self.activate(plan, active=1, generation=1)
        # A denial from a non-comparable predecessor basis must not leak into
        # the new current row merely because its timestamp overlaps the window.
        report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        self.assertEqual(report["coverage"]["population_attempts"], 0)
        with self.assertRaises(ReservationDenied):
            self.attempt(cost_microusd=1)
        with self.assertRaises(ReservationDenied):
            self.attempt(
                cost_microusd=0, output_tokens=0,
                output_tokens_bounded=False,
            )
        report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        self.assertEqual(report["enforcement"]["reason_code"], "unsupported_currency")
        self.assertEqual(report["coverage"]["population_attempts"], 2)
        self.assertEqual(report["coverage"]["included_attempts"], 0)
        self.assertEqual(report["coverage"]["unsupported_attempts"], 2)
        self.error(
            "invalid_request",
            lambda: self.repository.current_report(
                ADMIN, plan["budget_plan_id"], as_of="2100-01-01T00:00:00Z",
            ),
        )

    def check_missing_terminal_price_never_becomes_zero(self):
        plan = self.create(amount="1")
        self.activate(plan)
        missing_price = [{
            "attempt_state": "succeeded",
            "committed_cost_microusd": None,
            "reserved_amount": "0.5",
            "rate_card_id": "synthetic-route-rate",
            "rate_card_version": 1,
            "rate_card_digest": TEST_DIGEST,
        }]
        with mock.patch.object(
            self.repository, "_attempt_rows", return_value=missing_price,
        ):
            report = self.repository.current_report(ADMIN, plan["budget_plan_id"])
        self.assertIsNone(report["enforcement"]["committed_amount"])
        self.assertIsNone(report["enforcement"]["remaining_amount"])
        self.assertEqual(report["enforcement"]["reason_code"], "missing_evidence")
        self.assertEqual(report["coverage"]["pricing_eligible_attempts"], 1)
        self.assertEqual(report["coverage"]["priced_attempts"], 0)
        self.assertEqual(report["coverage"]["reason_code"], "incomplete_coverage")
        self.assertEqual(report["forecast"]["reason_code"], "missing_evidence")
        observation = report["financial_observations"][0]
        self.assertEqual(observation["basis"], "configured_rate_card_estimate")
        self.assertIsNone(observation["amount"])
        self.assertIsNone(observation["currency"])
        self.assertEqual(observation["reason_code"], "missing_evidence")
        self.assertEqual(observation["rate_card"], {
            "id": "synthetic-route-rate", "version": 1,
            "content_digest": TEST_DIGEST,
        })

    def check_bounded_gateway_actor_is_never_silently_unaudited(self):
        plan = self.create(amount="0")
        self.activate(plan)
        original = self.identity
        self.identity = replace(original, actor_id="user@example.com")
        try:
            with self.assertRaises(ReservationDenied):
                self.attempt(cost_microusd=1)
        finally:
            self.identity = original
        denial = next(
            row for row in self.budget_rows()["portfolio_work_budget_audit_events"]
            if row["operation"] == "reserve_denied"
        )
        self.assertEqual(denial["actor_id"], "user@example.com")

    def check_active_plan_count_is_bounded_at_activation_and_request_time(self):
        first = self.create(amount="10")
        self.activate(first)
        second = self.create(amount="10")
        with mock.patch("hormuz.budget_repository.MAX_ACTIVE_BUDGET_PLANS", 1):
            self.error("unavailable", lambda: self.activate(second))
        self.activate(second)

        with mock.patch("hormuz.budget_runtime.MAX_ACTIVE_BUDGET_PLANS", 1):
            with self.assertRaises(ReservationDenied):
                self.attempt(cost_microusd=1)

        self.assertEqual(self.attribution_rows(), [])
        self.assertEqual(
            self.budget_rows()["portfolio_work_budget_reservation_bindings"],
            [],
        )
        self.assertEqual(
            [
                row["reason_code"]
                for row in self.budget_rows()["portfolio_work_budget_audit_events"]
                if row["operation"] == "reserve_denied"
            ],
            ["attribution_invalid"],
        )

    def check_request_time_accounting_history_is_bounded(self):
        plan = self.create(amount="10")
        self.activate(plan)
        self.attempt(cost_microusd=1)
        self.attempt(cost_microusd=1)

        with mock.patch(
            "hormuz.budget_runtime.MAX_BUDGET_BINDINGS_PER_PLAN_WINDOW", 2,
        ):
            with self.assertRaises(ReservationDenied):
                self.attempt(cost_microusd=1)

        self.assertEqual(
            len(self.budget_rows()["portfolio_work_budget_reservation_bindings"]),
            2,
        )
        self.assertEqual(
            [
                row["reason_code"]
                for row in self.budget_rows()["portfolio_work_budget_audit_events"]
                if row["operation"] == "reserve_denied"
            ],
            ["budget_ceiling"],
        )

    def check_activation_history_is_bounded_for_reads_and_writes(self):
        first = self.create(amount="10")
        self.activate(first)
        second = self.create(
            amount="11", budget_plan_id=first["budget_plan_id"], expected_version=1,
        )
        with mock.patch(
            "hormuz.budget_repository.MAX_BUDGET_ACTIVATIONS_PER_PLAN", 1,
        ):
            self.error(
                "version_conflict",
                lambda: self.activate(second, active=1, generation=1),
            )
        self.activate(second, active=1, generation=1)
        with mock.patch(
            "hormuz.budget_repository.MAX_BUDGET_ACTIVATIONS_PER_PLAN", 1,
        ):
            self.error(
                "unavailable",
                lambda: self.repository.get_plan(ADMIN, first["budget_plan_id"]),
            )

    def check_denial_audit_retains_evaluation_time(self):
        plan = self.create(amount="10")
        self.activate(plan)
        evaluated_at = _timestamp(datetime.now(timezone.utc))
        self.store._record_work_budget_denial(
            self.identity,
            WorkBudgetDenied(
                "synthetic evaluated denial",
                "budget_ceiling",
                ((plan["budget_plan_id"], plan["version"]),),
                evaluated_at=evaluated_at,
            ),
        )
        denial = next(
            row for row in self.budget_rows()["portfolio_work_budget_audit_events"]
            if row["operation"] == "reserve_denied"
        )
        self.assertEqual(denial["occurred_at"], evaluated_at)
