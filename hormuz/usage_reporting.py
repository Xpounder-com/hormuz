from __future__ import annotations

from typing import Iterable

from .config import GatewayConfig


REPORT_DIMENSIONS = {"organization", "team", "person", "model", "client", "provider"}
LATENCY_BUCKETS_MS = (1, 5, 10, 25, 50, 100, 250, 500, 1_000, 10_000, 60_000, 600_000)


def enrich_usage_rows(
    config: GatewayConfig,
    rows: Iterable[dict[str, object]],
    *,
    group_by: str,
    actor_filter: str | None = None,
    team_filter: str | None = None,
) -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    for row in rows:
        cost_microusd = int(row["cost_microusd"])
        estimated_cost_microusd = int(row["estimated_cost_microusd"])
        cost_usd = cost_microusd / 1_000_000
        estimated_cost_usd = estimated_cost_microusd / 1_000_000
        budget_usd = budget_for_scope(
            config,
            group_by,
            row,
            actor_filter=actor_filter,
            team_filter=team_filter,
        )
        report.append(
            {
                **row,
                "cost_usd": cost_usd,
                "estimated_cost_usd": estimated_cost_usd,
                "budget_usd": budget_usd,
                "budget_remaining_usd": (
                    max(0.0, budget_usd - cost_usd)
                    if budget_usd is not None
                    else None
                ),
                "budget_used_percent": (
                    cost_usd / budget_usd * 100 if budget_usd else None
                ),
            }
        )
    return report


def budget_for_scope(
    config: GatewayConfig,
    group_by: str,
    row: dict[str, object],
    *,
    actor_filter: str | None = None,
    team_filter: str | None = None,
) -> float | None:
    scope_id = str(row["scope_id"])
    if group_by == "organization":
        if actor_filter is not None or team_filter is not None:
            return None
        return config.organization_policy.monthly_budget_usd
    if group_by == "team":
        if actor_filter is not None:
            return None
        policy = config.team_policies.get(scope_id)
        return policy.monthly_budget_usd if policy is not None else None
    if group_by != "person":
        return None

    identity = config.identities_by_actor.get(scope_id)
    if identity is None:
        return None
    caps = [
        policy.per_actor_monthly_budget_usd
        for policy in (
            config.organization_policy,
            config.team_policies.get(identity.team_id),
            config.actor_policies.get(identity.actor_id),
        )
        if policy is not None and policy.per_actor_monthly_budget_usd is not None
    ]
    actor_policy = config.actor_policies.get(identity.actor_id)
    if actor_policy is not None and actor_policy.monthly_budget_usd is not None:
        caps.append(actor_policy.monthly_budget_usd)
    return min(caps) if caps else None
