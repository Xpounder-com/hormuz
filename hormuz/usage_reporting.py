from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from .config import GatewayConfig


REPORT_DIMENSIONS = {"organization", "team", "person", "model", "client", "provider"}
IDENTITY_TYPES = ("human", "service_account", "ci", "connector")
LATENCY_BUCKETS_MS = (1, 5, 10, 25, 50, 100, 250, 500, 1_000, 10_000, 60_000, 600_000)
BUDGET_PACING_METHODOLOGY = "calendar_pace_estimate"
MODEL_MIX_IDENTITY_BASIS = "actual_model_or_routed_fallback"
MODEL_MIX_REQUEST_BASIS = "all_accounted_gateway_attempts"
_MICROUSD = Decimal(1_000_000)
_SECONDS_PER_CALENDAR_DAY = Decimal(86_400)


def utc_month_bounds(as_of: datetime) -> tuple[datetime, datetime]:
    """Return the UTC calendar-month window containing ``as_of``."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("Budget pacing requires a timezone-aware timestamp")
    as_of = as_of.astimezone(timezone.utc)
    start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def build_budget_pacing(
    *,
    as_of: datetime,
    estimated_spend_microusd: int,
    unpriced_requests: int,
    monthly_budget_usd: float | None,
) -> dict[str, object]:
    """Build an advisory, UTC-calendar-month budget pace from recorded estimates.

    This deliberately has no policy-enforcement side effects.  The estimate is
    based only on Hormuz-recorded, priced gateway events, so missing prices are
    surfaced as a partial projection instead of being treated as zero spend.
    """

    if (
        isinstance(estimated_spend_microusd, bool)
        or not isinstance(estimated_spend_microusd, int)
        or estimated_spend_microusd < 0
    ):
        raise ValueError("Estimated spend must be a non-negative integer")
    if (
        isinstance(unpriced_requests, bool)
        or not isinstance(unpriced_requests, int)
        or unpriced_requests < 0
    ):
        raise ValueError("Unpriced request count must be a non-negative integer")
    budget_decimal: Decimal | None = None
    if monthly_budget_usd is not None:
        if (
            isinstance(monthly_budget_usd, bool)
            or not isinstance(monthly_budget_usd, (int, float))
        ):
            raise ValueError("Monthly budget must be a non-negative number")
        try:
            budget_decimal = Decimal(str(monthly_budget_usd))
        except (InvalidOperation, ValueError) as error:
            raise ValueError("Monthly budget must be a non-negative number") from error
        if not budget_decimal.is_finite() or budget_decimal < 0:
            raise ValueError("Monthly budget must be a non-negative number")

    start, end = utc_month_bounds(as_of)
    as_of = as_of.astimezone(timezone.utc)
    elapsed_seconds = int((as_of - start).total_seconds())
    total_seconds = int((end - start).total_seconds())
    if elapsed_seconds < 0 or elapsed_seconds > total_seconds:
        raise ValueError("Budget pacing timestamp is outside its calendar month")

    partial_projection = unpriced_requests > 0
    result: dict[str, object] = {
        "methodology": BUDGET_PACING_METHODOLOGY,
        "advisory_only": True,
        "policy_enforcement_basis": "actual_usage_plus_active_reservations_only",
        "elapsed_fraction": elapsed_seconds / total_seconds,
        "early_period": as_of.day <= 7,
        "projection_available": elapsed_seconds > 0,
        "month_to_date_estimated_spend_microusd": estimated_spend_microusd,
        "month_to_date_estimated_spend_usd": estimated_spend_microusd / 1_000_000,
        "average_estimated_spend_per_calendar_day_microusd": None,
        "average_estimated_spend_per_calendar_day_usd": None,
        "projected_month_end_estimated_spend_microusd": None,
        "projected_month_end_estimated_spend_usd": None,
        "configured_monthly_budget_usd": monthly_budget_usd,
        "projected_budget_utilization_percent": None,
        "projected_budget_overage_usd": None,
        "unpriced_requests": unpriced_requests,
        "partial_projection": partial_projection,
    }
    if elapsed_seconds == 0:
        return result

    estimated_spend = Decimal(estimated_spend_microusd)
    elapsed = Decimal(elapsed_seconds)
    month_seconds = Decimal(total_seconds)
    average_microusd = _rounded_microusd(
        estimated_spend * _SECONDS_PER_CALENDAR_DAY / elapsed
    )
    projected_microusd = _rounded_microusd(estimated_spend * month_seconds / elapsed)
    result.update(
        {
            "average_estimated_spend_per_calendar_day_microusd": average_microusd,
            "average_estimated_spend_per_calendar_day_usd": average_microusd / 1_000_000,
            "projected_month_end_estimated_spend_microusd": projected_microusd,
            "projected_month_end_estimated_spend_usd": projected_microusd / 1_000_000,
        }
    )
    if budget_decimal is None:
        return result

    budget_microusd = _rounded_microusd(budget_decimal * _MICROUSD)
    result["projected_budget_overage_usd"] = (
        max(0, projected_microusd - budget_microusd) / 1_000_000
    )
    if budget_microusd > 0:
        utilization = Decimal(projected_microusd) / Decimal(budget_microusd) * 100
        result["projected_budget_utilization_percent"] = float(
            utilization.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        )
    return result


def build_model_mix(rows: Iterable[dict[str, object]]) -> dict[str, object]:
    """Build content-free current-window model consumption shares.

    ``report_rows(group_by="model")`` already applies the caller's tenant and
    access scope.  This helper deliberately only turns those rows into model
    shares; it does not infer provider invoices, model quality, or employee
    performance.  A provider-returned actual model is preferred by the store
    aggregation, with the routed model used only when the provider omits it.
    """

    totals = {
        "requests": 0,
        "succeeded": 0,
        "failed": 0,
        "denied": 0,
        "total_tokens": 0,
        "estimated_spend_microusd": 0,
        "unpriced_requests": 0,
    }
    models: list[dict[str, object]] = []
    seen_models: set[tuple[str, str]] = set()
    for row in rows:
        model_id = _model_mix_text(row.get("scope_id"), field="model")
        provider = _model_mix_text(row.get("protocol"), field="provider")
        identity = (provider, model_id)
        if identity in seen_models:
            raise ValueError("Model mix rows must be unique by provider and model")
        seen_models.add(identity)

        values = {
            field: _model_mix_nonnegative_int(row.get(field), field=field)
            for field in (
                "requests",
                "succeeded",
                "failed",
                "denied",
                "total_tokens",
                "estimated_cost_microusd",
                "unpriced_requests",
            )
        }
        if values["succeeded"] + values["failed"] + values["denied"] != values["requests"]:
            raise ValueError("Model mix status counts must equal requests")
        if values["unpriced_requests"] > values["requests"]:
            raise ValueError("Model mix unpriced requests cannot exceed requests")

        model = {
            "model_id": model_id,
            "provider": provider,
            "requests": values["requests"],
            "succeeded": values["succeeded"],
            "failed": values["failed"],
            "denied": values["denied"],
            "total_tokens": values["total_tokens"],
            "estimated_spend_microusd": values["estimated_cost_microusd"],
            "estimated_spend_usd": values["estimated_cost_microusd"] / 1_000_000,
            "unpriced_requests": values["unpriced_requests"],
            "request_share_percent": None,
            "token_share_percent": None,
            "estimated_spend_share_percent": None,
        }
        models.append(model)
        totals["requests"] += values["requests"]
        totals["succeeded"] += values["succeeded"]
        totals["failed"] += values["failed"]
        totals["denied"] += values["denied"]
        totals["total_tokens"] += values["total_tokens"]
        totals["estimated_spend_microusd"] += values["estimated_cost_microusd"]
        totals["unpriced_requests"] += values["unpriced_requests"]

    models.sort(
        key=lambda model: (
            -int(model["estimated_spend_microusd"]),
            -int(model["total_tokens"]),
            str(model["provider"]),
            str(model["model_id"]),
        )
    )
    for model in models:
        model["request_share_percent"] = _model_mix_percentage(
            int(model["requests"]), totals["requests"]
        )
        model["token_share_percent"] = _model_mix_percentage(
            int(model["total_tokens"]), totals["total_tokens"]
        )
        model["estimated_spend_share_percent"] = _model_mix_percentage(
            int(model["estimated_spend_microusd"]), totals["estimated_spend_microusd"]
        )

    return {
        "model_identity_basis": MODEL_MIX_IDENTITY_BASIS,
        "request_basis": MODEL_MIX_REQUEST_BASIS,
        "totals": {
            **totals,
            "estimated_spend_usd": totals["estimated_spend_microusd"] / 1_000_000,
            "partial_estimated_spend": totals["unpriced_requests"] > 0,
        },
        "models": models,
    }


def _model_mix_nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Model mix {field} must be a non-negative integer")
    return value


def _model_mix_text(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 256
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise ValueError(f"Model mix {field} must be a bounded identifier")
    return value


def _model_mix_percentage(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    value = Decimal(numerator) * Decimal(100) / Decimal(denominator)
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def budget_for_pacing_scope(
    config: GatewayConfig,
    *,
    organization_id: str,
    actor_id: str | None,
    team_id: str | None,
) -> float | None:
    """Return only the cap applicable to the reporting scope being paced."""

    if actor_id is not None:
        return budget_for_scope(
            config,
            "person",
            {"scope_id": actor_id},
            actor_filter=actor_id,
            team_filter=team_id,
        )
    if team_id is not None:
        return budget_for_scope(
            config,
            "team",
            {"scope_id": team_id},
            team_filter=team_id,
        )
    return budget_for_scope(config, "organization", {"scope_id": organization_id})


def _rounded_microusd(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


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
