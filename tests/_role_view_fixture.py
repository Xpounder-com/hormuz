"""Synthetic identities and evidence for portfolio role-view tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

from hormuz.config import Identity
from hormuz.portfolio_config import PortfolioRoleBinding
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import SCOPES, canonical

from ._budget_fixture import ADMIN, activation_request, plan_request
from ._portfolio_fixture import ADMIN as ADMIN_TOKEN, create_request, registry_config


FINANCE_TOKEN = "synthetic-role-view-finance-token"
PLATFORM_TOKEN = "synthetic-role-view-platform-token"
TEAM_TOKEN = "synthetic-role-view-team-token"
SALES_TOKEN = "synthetic-role-view-sales-token"
SCORECARD_FIXTURE = (
    Path(__file__).parent / "fixtures" / "scorecard" / "runtime-v1.json"
)


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
        plan_request(scope, amount=amount),
    )
    repositories.budgets.activate_plan(
        ADMIN,
        plan["budget_plan_id"],
        activation_request(plan["version"]),
    )
    return plan


def create_scorecard(repositories, scope, *, scorecard_id: str):
    value = deepcopy(json.loads(SCORECARD_FIXTURE.read_text(encoding="utf-8"))["input"])
    value["organization_id"] = "acme"
    value["scorecard_id"] = scorecard_id
    value["work_scope"] = {
        "work_scope_id": scope["work_scope_id"],
        "version": scope["version"],
    }
    return repositories.scorecards.build(ADMIN, value)
