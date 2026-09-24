"""Synthetic identities and evidence for portfolio role-view tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from hormuz.config import Identity
from hormuz.portfolio_config import PortfolioPrincipal, PortfolioRoleBinding
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import SCOPES, canonical

from ._portfolio_fixture import ADMIN as ADMIN_TOKEN, create_request, registry_config


FINANCE_TOKEN = "synthetic-role-view-finance-token"
PLATFORM_TOKEN = "synthetic-role-view-platform-token"
TEAM_TOKEN = "synthetic-role-view-team-token"
SALES_TOKEN = "synthetic-role-view-sales-token"
SCORECARD_FIXTURE = (
    Path(__file__).parent / "fixtures" / "scorecard" / "runtime-v1.json"
)
ADMIN = PortfolioPrincipal("acme", "alice", ("portfolio_admin",))


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _plan_request(
    scope,
    *,
    amount: str,
    budget_plan_id: str | None = None,
    expected_version: int | None = None,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "schema_id": "hormuz.work-budget-plan-request",
        "schema_version": 1,
        "budget_plan_id": budget_plan_id,
        "expected_version": expected_version,
        "work_scope": {
            "work_scope_id": scope["work_scope_id"],
            "version": scope["version"],
        },
        "window": {
            "start_at": _timestamp(now - timedelta(days=1)),
            "end_at": _timestamp(now + timedelta(days=1)),
        },
        "currency": "USD",
        "amount": amount,
        "allowed_models": None,
        "output_token_cap": None,
        "per_request_cost_cap": None,
        "reason_code": "created" if budget_plan_id is None else "corrected",
    }


def _activation_request(
    version: int,
    *,
    expected_active_version: int | None = None,
    expected_activation_generation: int = 0,
    reason_code: str = "accepted",
) -> dict[str, object]:
    return {
        "schema_id": "hormuz.work-budget-plan-activation-request",
        "schema_version": 1,
        "version": version,
        "expected_active_version": expected_active_version,
        "expected_activation_generation": expected_activation_generation,
        "reason_code": reason_code,
    }


def role_view_config(root: Path):
    base = registry_config(root)
    identities = dict(base.identities_by_token)
    identities.update({
        FINANCE_TOKEN: Identity(
            token_env="UNUSED_ROLE_VIEW_FINANCE_TOKEN",
            token=FINANCE_TOKEN,
            actor_id="finance-reader",
            actor_name="Synthetic finance reader",
            team_id="finance",
            team_name="Finance",
            organization_id="acme",
            allowed_clients=(),
        ),
        PLATFORM_TOKEN: Identity(
            token_env="UNUSED_ROLE_VIEW_PLATFORM_TOKEN",
            token=PLATFORM_TOKEN,
            actor_id="platform-reader",
            actor_name="Synthetic platform reader",
            team_id="platform",
            team_name="Platform",
            organization_id="acme",
            allowed_clients=(),
        ),
        TEAM_TOKEN: Identity(
            token_env="UNUSED_ROLE_VIEW_TEAM_TOKEN",
            token=TEAM_TOKEN,
            actor_id="engineering-lead",
            actor_name="Synthetic engineering lead",
            team_id="engineering",
            team_name="Engineering",
            organization_id="acme",
            allowed_clients=(),
        ),
        SALES_TOKEN: Identity(
            token_env="UNUSED_ROLE_VIEW_SALES_TOKEN",
            token=SALES_TOKEN,
            actor_id="sales-lead",
            actor_name="Synthetic sales lead",
            team_id="sales",
            team_name="Sales",
            organization_id="acme",
            allowed_clients=(),
        ),
    })
    role_bindings = (
        *base.portfolio_control.role_bindings,
        PortfolioRoleBinding("acme", "finance-reader", ("finance_viewer",)),
        PortfolioRoleBinding("acme", "platform-reader", ("platform_viewer",)),
        PortfolioRoleBinding("acme", "engineering-lead", ("team_lead",)),
        PortfolioRoleBinding("acme", "sales-lead", ("team_lead",)),
    )
    return replace(
        base,
        identities_by_token=identities,
        portfolio_control=replace(
            base.portfolio_control,
            role_bindings=role_bindings,
        ),
    )


def create_scope(
    service: PortfolioService,
    key: str,
    *,
    owner_team_id: str | None,
    kind: str = "use_case",
    parent_work_scope_id: str | None = None,
):
    return service.dispatch(
        ADMIN_TOKEN,
        "POST",
        SCOPES,
        body=canonical(create_request(
            owner_team_id=owner_team_id,
            kind=kind,
            parent_work_scope_id=parent_work_scope_id,
        )).encode("ascii"),
        idempotency_key=key,
    )[1]


def create_budget(repositories, scope, *, amount: str = "100"):
    plan = repositories.budgets.create_plan(
        ADMIN,
        _plan_request(scope, amount=amount),
    )
    repositories.budgets.activate_plan(
        ADMIN,
        plan["budget_plan_id"],
        _activation_request(plan["version"]),
    )
    return plan


def revise_budget(repositories, plan, scope, *, amount: str):
    revised = repositories.budgets.create_plan(
        ADMIN,
        _plan_request(
            scope,
            amount=amount,
            budget_plan_id=plan["budget_plan_id"],
            expected_version=plan["version"],
        ),
    )
    repositories.budgets.activate_plan(
        ADMIN,
        revised["budget_plan_id"],
        _activation_request(
            revised["version"],
            expected_active_version=plan["version"],
            expected_activation_generation=plan["version"],
        ),
    )
    return revised


def reactivate_budget(
    repositories,
    plan,
    *,
    current_version: int,
    generation: int,
):
    repositories.budgets.activate_plan(
        ADMIN,
        plan["budget_plan_id"],
        _activation_request(
            plan["version"],
            expected_active_version=current_version,
            expected_activation_generation=generation,
            reason_code="reactivated",
        ),
    )


def create_scorecard(repositories, scope, *, scorecard_id: str):
    value = deepcopy(json.loads(SCORECARD_FIXTURE.read_text(encoding="utf-8"))["input"])
    value["organization_id"] = "acme"
    value["scorecard_id"] = scorecard_id
    value["work_scope"] = {
        "work_scope_id": scope["work_scope_id"],
        "version": scope["version"],
    }
    return repositories.scorecards.build(ADMIN, value)
