"""Explicit forecast scenarios from local covered costs, not promised savings."""
from __future__ import annotations

import math


def forecast_view(costs, *, point, days, elapsed_days, remaining_days):
    active = [amount for amount in days if amount is not None]
    uncertainty = []
    if len(active) < 3:
        uncertainty.append("limited_observed_days")
    if costs["pending_microusd"]:
        uncertainty.append("pending_provider_holds")
    if costs["uncertain_microusd"]:
        uncertainty.append("unresolved_provider_cost")
    if costs.get("unconfirmed_attempts", 0):
        uncertainty.append("estimated_provider_prices")
    lower = upper = None
    if len(active) >= 3:
        committed = costs["committed_microusd"]
        lower = committed + math.ceil(min(active) * remaining_days)
        upper = committed + math.ceil(max(active) * remaining_days)
    return {"point_microusd": point, "lower_microusd": lower, "upper_microusd": upper,
            "basis": "observed_daily_run_rate_scenarios_not_confidence_interval",
            "observed_days": len(active), "elapsed_days": elapsed_days,
            "coverage": "gateway_captured_work_requests_only", "uncertainty": uncertainty,
            "held_microusd": costs["pending_microusd"] + costs["uncertain_microusd"],
            "excludes_holds": True}
