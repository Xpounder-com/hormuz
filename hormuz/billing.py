from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
from typing import Iterable, Mapping, Sequence


MAX_REPORT_PAGES = 1_000
MAX_REPORT_BUCKETS = 50_000
MAX_REPORT_ITEMS = 500_000
MAX_REPORT_PAGE_BYTES = 16 * 1024 * 1024
MAX_REPORT_TOTAL_BYTES = 32 * 1024 * 1024
MAX_AMOUNT_USD = Decimal("1000000000000")
MAX_AMOUNT_DECIMAL_PLACES = 12
MAX_RECONCILIATION_AMOUNT_USD = MAX_AMOUNT_USD * MAX_REPORT_ITEMS
_ALLOCATION_UNIT_USD = Decimal("0.000000000001")
_ALLOCATION_METHOD_VERSION = "provider_total_normalized_v1"
_ALLOCATION_BASIS = (
    "request_time_estimated_cost_with_unattributed_provider_remainder_v1"
)

_AUTHENTICATED_SOURCE_CONTRACTS = {
    "openai": (
        "openai.organization.costs.v1",
        "organization_all_projects_line_items",
    ),
    "anthropic": (
        "anthropic.organization.cost_report.2023-06-01",
        "organization_all_workspaces_descriptions",
    ),
}


class ProviderBillingError(ValueError):
    pass


@dataclass
class _AllocationBucket:
    """One content-free allocation target derived from immutable usage facts."""

    kind: str
    key: tuple[str, ...]
    actor_id: str | None = None
    actor_name: str | None = None
    team_id: str | None = None
    team_name: str | None = None
    reason: str | None = None
    successful_requests: int = 0
    unpriced_successful_requests: int = 0
    allocation_weight_usd: Decimal = Decimal(0)


@dataclass(frozen=True)
class BillingReconciliationPolicy:
    """Exact, versioned thresholds for aggregate provider reconciliation."""

    enabled: bool = False
    policy_version: str = "billing-reconciliation-disabled-v1"
    max_absolute_variance_usd: Decimal | None = None
    max_variance_basis_points: int | None = None
    max_unpriced_requests: int | None = None
    max_legacy_unattributed_requests: int | None = None
    max_unscoped_provider_items: int | None = None
    require_authenticated_source: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("Billing reconciliation enabled must be a boolean")
        if (
            not isinstance(self.policy_version, str)
            or not self.policy_version.strip()
            or self.policy_version != self.policy_version.strip()
            or len(self.policy_version.encode("utf-8")) > 128
            or any(character in self.policy_version for character in ("\n", "\r", "\x00"))
        ):
            raise ValueError(
                "Billing reconciliation policy_version must be a bounded single-line string"
            )
        if self.max_absolute_variance_usd is not None:
            value = self.max_absolute_variance_usd
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value < 0
                or value > MAX_RECONCILIATION_AMOUNT_USD
                or value.as_tuple().exponent < -MAX_AMOUNT_DECIMAL_PLACES
            ):
                raise ValueError(
                    "Billing reconciliation max_absolute_variance_usd is invalid"
                )
        for name, value, maximum in (
            ("max_variance_basis_points", self.max_variance_basis_points, 1_000_000_000),
            ("max_unpriced_requests", self.max_unpriced_requests, 1_000_000_000),
            (
                "max_legacy_unattributed_requests",
                self.max_legacy_unattributed_requests,
                1_000_000_000,
            ),
            ("max_unscoped_provider_items", self.max_unscoped_provider_items, 500_000),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ValueError(f"Billing reconciliation {name} is invalid")
        if not isinstance(self.require_authenticated_source, bool):
            raise ValueError(
                "Billing reconciliation require_authenticated_source must be a boolean"
            )
        if self.enabled and not any(
            (
                self.max_absolute_variance_usd is not None,
                self.max_variance_basis_points is not None,
                self.max_unpriced_requests is not None,
                self.max_legacy_unattributed_requests is not None,
                self.max_unscoped_provider_items is not None,
                self.require_authenticated_source,
            )
        ):
            raise ValueError(
                "Enabled billing reconciliation policy must contain at least one rule"
            )

    @property
    def policy_sha256(self) -> str:
        canonical = json.dumps(
            self.to_dict(include_digest=False),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self, *, include_digest: bool = True) -> dict[str, object]:
        value: dict[str, object] = {
            "enabled": self.enabled,
            "policy_version": self.policy_version,
            "max_absolute_variance_usd": (
                None
                if self.max_absolute_variance_usd is None
                else _decimal_text(self.max_absolute_variance_usd)
            ),
            "max_variance_basis_points": self.max_variance_basis_points,
            "max_unpriced_requests": self.max_unpriced_requests,
            "max_legacy_unattributed_requests": self.max_legacy_unattributed_requests,
            "max_unscoped_provider_items": self.max_unscoped_provider_items,
            "require_authenticated_source": self.require_authenticated_source,
        }
        if include_digest:
            value["policy_sha256"] = self.policy_sha256
        return value


@dataclass(frozen=True)
class ProviderCostItem:
    bucket_start: str
    bucket_end: str
    amount_usd: str
    currency: str
    provider_scope_kind: str
    provider_scope_id: str | None
    line_item: str | None = None
    cost_type: str | None = None
    model: str | None = None
    service_tier: str | None = None
    token_type: str | None = None
    context_window: str | None = None
    inference_geo: str | None = None


@dataclass(frozen=True)
class ProviderCostReport:
    provider: str
    source_sha256: str
    page_count: int
    bucket_count: int
    report_start: str
    report_end: str
    items: tuple[ProviderCostItem, ...]


@dataclass(frozen=True)
class ProviderCostSource:
    kind: str
    api_contract: str | None = None
    query_start: str | None = None
    query_end: str | None = None
    query_scope: str | None = None

    @classmethod
    def offline(cls) -> ProviderCostSource:
        return cls(kind="offline_upload")

    @classmethod
    def authenticated(
        cls,
        *,
        provider: str,
        query_start: str,
        query_end: str,
    ) -> ProviderCostSource:
        contract = _AUTHENTICATED_SOURCE_CONTRACTS.get(provider)
        if contract is None:
            raise ProviderBillingError("Provider must be openai or anthropic")
        return cls(
            kind="authenticated_api",
            api_contract=contract[0],
            query_start=_rfc3339(query_start, "Provider query start"),
            query_end=_rfc3339(query_end, "Provider query end"),
            query_scope=contract[1],
        )


def evaluate_reconciliation(
    reconciliation: Mapping[str, object],
    policy: BillingReconciliationPolicy,
) -> dict[str, object]:
    """Apply exact thresholds without converting aggregate cost into chargeback."""

    if (
        not isinstance(reconciliation, Mapping)
        or isinstance(reconciliation.get("schema_version"), bool)
        or reconciliation.get("schema_version") != 1
    ):
        raise ProviderBillingError("Unsupported billing reconciliation schema")
    if not isinstance(policy, BillingReconciliationPolicy):
        raise ProviderBillingError("Billing reconciliation policy is required")
    provider_cost = _reconciliation_decimal(reconciliation, "provider_cost_usd")
    gateway_estimated_cost = _reconciliation_decimal(
        reconciliation,
        "gateway_estimated_cost_usd",
    )
    if gateway_estimated_cost < 0:
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    reported_variance = _reconciliation_decimal(reconciliation, "variance_usd")
    with localcontext() as context:
        context.prec = 80
        variance = provider_cost - gateway_estimated_cost
        absolute_variance = variance.copy_abs()
        provider_cost_denominator = provider_cost.copy_abs()
        relative_numerator = absolute_variance * Decimal(10_000)
        if provider_cost_denominator == 0:
            variance_basis_points = Decimal(0) if absolute_variance == 0 else None
        else:
            variance_basis_points = relative_numerator / provider_cost_denominator
        relative_limit = (
            None
            if policy.max_variance_basis_points is None
            else provider_cost_denominator * policy.max_variance_basis_points
        )
    if reported_variance != variance:
        raise ProviderBillingError("Billing reconciliation facts are inconsistent")
    source_kind = reconciliation.get("provider_source_kind")
    if source_kind not in {"offline_upload", "authenticated_api"}:
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    counts = {
        field: _reconciliation_count(reconciliation, field)
        for field in (
            "gateway_unpriced_requests",
            "legacy_unattributed_gateway_requests",
            "unscoped_provider_items",
        )
    }
    reasons: list[str] = []
    if policy.enabled:
        if (
            policy.require_authenticated_source
            and source_kind != "authenticated_api"
        ):
            reasons.append("provider_source_not_authenticated")
        if (
            policy.max_absolute_variance_usd is not None
            and absolute_variance > policy.max_absolute_variance_usd
        ):
            reasons.append("absolute_variance_exceeded")
        if policy.max_variance_basis_points is not None:
            if variance_basis_points is None:
                reasons.append("variance_basis_unavailable")
            elif relative_limit is not None and relative_numerator > relative_limit:
                reasons.append("relative_variance_exceeded")
        for field, threshold, reason in (
            (
                "gateway_unpriced_requests",
                policy.max_unpriced_requests,
                "unpriced_requests_exceeded",
            ),
            (
                "legacy_unattributed_gateway_requests",
                policy.max_legacy_unattributed_requests,
                "legacy_unattributed_requests_exceeded",
            ),
            (
                "unscoped_provider_items",
                policy.max_unscoped_provider_items,
                "unscoped_provider_items_exceeded",
            ),
        ):
            if threshold is not None and counts[field] > threshold:
                reasons.append(reason)

    result = dict(reconciliation)
    result.update(
        {
            "schema_version": 2,
            "variance_absolute_usd": _decimal_text(absolute_variance),
            "variance_basis_points": (
                None
                if variance_basis_points is None
                else _decimal_text(variance_basis_points)
            ),
            "variance_basis": "absolute_provider_reported_cost",
            "exception_status": (
                "not_evaluated"
                if not policy.enabled
                else "review_required"
                if reasons
                else "clear"
            ),
            "exception_reasons": reasons,
            "reconciliation_policy": policy.to_dict(),
        }
    )
    return result


def build_provider_cost_allocation(
    reconciliation: Mapping[str, object],
    successful_requests: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Allocate one provider-reported organization total without inventing per-request facts.

    The provider amount remains the authoritative organization total.  Immutable
    request-time identity and team snapshots determine *where* a captured
    successful request belongs; immutable request-time estimated cost determines
    its relative allocation weight.  A positive amount that the captured priced
    requests do not explain is deliberately placed in ``unattributed`` instead
    of increasing employee allocations.
    """

    if (
        not isinstance(reconciliation, Mapping)
        or isinstance(reconciliation.get("schema_version"), bool)
        or reconciliation.get("schema_version") != 1
        or isinstance(successful_requests, (str, bytes, bytearray))
        or not isinstance(successful_requests, Sequence)
    ):
        raise ProviderBillingError("Billing allocation facts are invalid")

    provider_cost = _reconciliation_decimal(reconciliation, "provider_cost_usd")
    organization_id = _allocation_required_text(
        reconciliation.get("organization_id"),
        field="organization ID",
    )
    provider = _allocation_required_text(
        reconciliation.get("provider"),
        field="provider",
    )
    if provider not in {"openai", "anthropic"}:
        raise ProviderBillingError("Billing allocation facts are invalid")
    import_id = _allocation_required_text(
        reconciliation.get("import_id"),
        field="provider cost import ID",
    )
    report_start = _allocation_required_text(
        reconciliation.get("report_start"),
        field="report start",
    )
    report_end = _allocation_required_text(
        reconciliation.get("report_end"),
        field="report end",
    )
    gateway_requests = _reconciliation_count(reconciliation, "gateway_requests")
    gateway_succeeded = _reconciliation_count(reconciliation, "gateway_succeeded")
    legacy_unbound = _reconciliation_count(
        reconciliation,
        "legacy_unattributed_gateway_requests",
    )

    people: dict[tuple[str, str, str, str], _AllocationBucket] = {}
    unattributed: dict[str, _AllocationBucket] = {}
    price_weighted_requests = 0
    unpriced_successful_requests = 0
    attributed_successful_requests = 0
    unattributed_successful_requests = 0

    for request in successful_requests:
        if not isinstance(request, Mapping):
            raise ProviderBillingError("Billing allocation request facts are invalid")
        actor_id = _allocation_event_text(request.get("actor_id"))
        actor_name = _allocation_event_text(request.get("actor_name")) or actor_id
        team_id = _allocation_event_text(request.get("team_id"))
        team_name = _allocation_event_text(request.get("team_name")) or team_id
        identity_type = _allocation_event_text(request.get("identity_type"))
        cost_basis = _allocation_event_text(request.get("cost_basis"))
        cost_microusd = _allocation_nonnegative_int(
            request.get("cost_microusd"),
            field="request cost",
        )
        is_price_weighted = cost_basis.startswith("estimated") and cost_microusd > 0
        weight = (
            Decimal(cost_microusd) / Decimal(1_000_000)
            if is_price_weighted
            else Decimal(0)
        )
        is_unpriced = cost_basis == "not_available"
        if is_price_weighted:
            price_weighted_requests += 1
        if is_unpriced:
            unpriced_successful_requests += 1

        if identity_type == "human" and actor_id and team_id:
            key = (team_id, team_name, actor_id, actor_name)
            bucket = people.get(key)
            if bucket is None:
                bucket = _AllocationBucket(
                    kind="person",
                    key=("person", *key),
                    actor_id=actor_id,
                    actor_name=actor_name,
                    team_id=team_id,
                    team_name=team_name,
                )
                people[key] = bucket
            attributed_successful_requests += 1
        else:
            reason = _unattributed_reason(
                identity_type=identity_type,
                actor_id=actor_id,
                team_id=team_id,
            )
            bucket = unattributed.get(reason)
            if bucket is None:
                bucket = _AllocationBucket(
                    kind="unattributed",
                    key=("unattributed", reason),
                    reason=reason,
                )
                unattributed[reason] = bucket
            unattributed_successful_requests += 1
        bucket.successful_requests += 1
        bucket.unpriced_successful_requests += int(is_unpriced)
        bucket.allocation_weight_usd += weight

    priced_request_weight = _sum_allocation_decimals(
        (bucket.allocation_weight_usd for bucket in (*people.values(), *unattributed.values())),
    )
    with localcontext() as context:
        context.prec = 96
        unattributed_provider_remainder = (
            provider_cost - priced_request_weight
            if provider_cost > 0 and provider_cost > priced_request_weight
            else Decimal(0)
        )
    if unattributed_provider_remainder > 0 and priced_request_weight > 0:
        remainder = unattributed.get("provider_amount_not_explained_by_priced_gateway_requests")
        if remainder is None:
            remainder = _AllocationBucket(
                kind="unattributed",
                key=(
                    "unattributed",
                    "provider_amount_not_explained_by_priced_gateway_requests",
                ),
                reason="provider_amount_not_explained_by_priced_gateway_requests",
            )
            unattributed[remainder.reason] = remainder
        remainder.allocation_weight_usd += unattributed_provider_remainder

    all_buckets = [*people.values(), *unattributed.values()]
    total_weight = _sum_allocation_decimals(
        (bucket.allocation_weight_usd for bucket in all_buckets),
    )
    if provider_cost != 0 and total_weight == 0:
        reason = (
            "no_successful_gateway_requests"
            if not successful_requests
            else "no_priceable_successful_requests"
        )
        fallback = unattributed.get(reason)
        if fallback is None:
            fallback = _AllocationBucket(
                kind="unattributed",
                key=("unattributed", reason),
                reason=reason,
            )
            unattributed[reason] = fallback
            all_buckets.append(fallback)
        fallback.allocation_weight_usd += provider_cost.copy_abs()

    allocated = _normalized_allocations(
        provider_cost,
        [(bucket.key, bucket.allocation_weight_usd) for bucket in all_buckets],
    )
    person_rows = [
        {
            "team_id": bucket.team_id,
            "team_name": bucket.team_name,
            "actor_id": bucket.actor_id,
            "actor_name": bucket.actor_name,
            "successful_requests": bucket.successful_requests,
            "unpriced_successful_requests": bucket.unpriced_successful_requests,
            "allocation_weight_usd": _decimal_text(bucket.allocation_weight_usd),
            "allocated_cost_usd": _decimal_text(allocated[bucket.key]),
        }
        for bucket in people.values()
    ]
    person_rows.sort(
        key=lambda row: (
            str(row["team_id"]),
            str(row["team_name"]),
            str(row["actor_id"]),
            str(row["actor_name"]),
        )
    )

    teams: dict[tuple[str, str], dict[str, object]] = {}
    for person in person_rows:
        team_key = (str(person["team_id"]), str(person["team_name"]))
        team = teams.setdefault(
            team_key,
            {
                "team_id": person["team_id"],
                "team_name": person["team_name"],
                "successful_requests": 0,
                "unpriced_successful_requests": 0,
                "allocation_weight_usd": Decimal(0),
                "allocated_cost_usd": Decimal(0),
            },
        )
        team["successful_requests"] = int(team["successful_requests"]) + int(
            person["successful_requests"]
        )
        team["unpriced_successful_requests"] = int(
            team["unpriced_successful_requests"]
        ) + int(person["unpriced_successful_requests"])
        team["allocation_weight_usd"] = _sum_allocation_decimals(
            (
                Decimal(str(team["allocation_weight_usd"])),
                Decimal(str(person["allocation_weight_usd"])),
            )
        )
        team["allocated_cost_usd"] = _sum_allocation_decimals(
            (
                Decimal(str(team["allocated_cost_usd"])),
                Decimal(str(person["allocated_cost_usd"])),
            )
        )
    team_rows = [
        {
            "team_id": team["team_id"],
            "team_name": team["team_name"],
            "successful_requests": team["successful_requests"],
            "unpriced_successful_requests": team["unpriced_successful_requests"],
            "allocation_weight_usd": _decimal_text(
                Decimal(str(team["allocation_weight_usd"]))
            ),
            "allocated_cost_usd": _decimal_text(
                Decimal(str(team["allocated_cost_usd"]))
            ),
        }
        for team in teams.values()
    ]
    team_rows.sort(key=lambda row: (str(row["team_id"]), str(row["team_name"])))

    unattributed_reasons = [
        {
            "reason": bucket.reason,
            "successful_requests": bucket.successful_requests,
            "unpriced_successful_requests": bucket.unpriced_successful_requests,
            "allocation_weight_usd": _decimal_text(bucket.allocation_weight_usd),
            "allocated_cost_usd": _decimal_text(allocated[bucket.key]),
        }
        for bucket in unattributed.values()
    ]
    unattributed_reasons.sort(key=lambda row: str(row["reason"]))

    person_total = _sum_allocation_decimals(
        (Decimal(str(row["allocated_cost_usd"])) for row in person_rows),
    )
    team_total = _sum_allocation_decimals(
        (Decimal(str(row["allocated_cost_usd"])) for row in team_rows),
    )
    unattributed_total = _sum_allocation_decimals(
        (Decimal(str(row["allocated_cost_usd"])) for row in unattributed_reasons),
    )
    person_plus_unattributed = _sum_allocation_decimals(
        (person_total, unattributed_total)
    )
    team_plus_unattributed = _sum_allocation_decimals(
        (team_total, unattributed_total)
    )
    if (
        person_plus_unattributed != provider_cost
        or team_plus_unattributed != provider_cost
    ):
        raise ProviderBillingError("Billing allocation does not preserve provider total")

    result: dict[str, object] = {
        "schema_version": 1,
        "organization_id": organization_id,
        "provider": provider,
        "import_id": import_id,
        "report_start": report_start,
        "report_end": report_end,
        "provider_cost_basis": "provider_reported",
        "provider_cost_usd": _decimal_text(provider_cost),
        "allocation_method_version": _ALLOCATION_METHOD_VERSION,
        "allocation_basis": _ALLOCATION_BASIS,
        "request_final_cost_available": False,
        "unattributed_provider_remainder_usd": _decimal_text(
            unattributed_provider_remainder
        ),
        "coverage": {
            "gateway_captured_requests": gateway_requests,
            "gateway_captured_successful_requests": len(successful_requests),
            "reported_gateway_succeeded_requests": gateway_succeeded,
            "attributed_human_successful_requests": attributed_successful_requests,
            "unattributed_successful_requests": unattributed_successful_requests,
            "price_weighted_successful_requests": price_weighted_requests,
            "zero_weight_successful_requests": len(successful_requests)
            - price_weighted_requests,
            "unpriced_successful_requests": unpriced_successful_requests,
            "excluded_non_successful_gateway_requests": max(
                gateway_requests - gateway_succeeded,
                0,
            ),
            "excluded_legacy_unbound_gateway_requests": legacy_unbound,
            "coverage": "gateway_captured_requests_plus_explicit_unattributed_provider_remainder",
        },
        "teams": team_rows,
        "people": person_rows,
        "unattributed": {
            "successful_requests": unattributed_successful_requests,
            "unpriced_successful_requests": sum(
                int(row["unpriced_successful_requests"])
                for row in unattributed_reasons
            ),
            "allocation_weight_usd": _decimal_text(
                _sum_allocation_decimals(
                    (
                        Decimal(str(row["allocation_weight_usd"]))
                        for row in unattributed_reasons
                    ),
                )
            ),
            "allocated_cost_usd": _decimal_text(unattributed_total),
            "reasons": unattributed_reasons,
        },
        "totals": {
            "provider_organization_cost_usd": _decimal_text(provider_cost),
            "team_allocated_cost_usd": _decimal_text(team_total),
            "person_allocated_cost_usd": _decimal_text(person_total),
            "unattributed_cost_usd": _decimal_text(unattributed_total),
            "team_plus_unattributed_cost_usd": _decimal_text(team_plus_unattributed),
            "person_plus_unattributed_cost_usd": _decimal_text(person_plus_unattributed),
        },
    }
    for field in (
        "imported_at",
        "source_sha256",
        "provider_report_completeness",
        "coverage_status",
        "provider_source_kind",
        "provider_api_contract",
        "query_start",
        "query_end",
        "query_scope",
    ):
        if field in reconciliation:
            result[field] = reconciliation[field]
    return result


def _normalized_allocations(
    provider_cost: Decimal,
    weighted_buckets: Sequence[tuple[tuple[str, ...], Decimal]],
) -> dict[tuple[str, ...], Decimal]:
    """Use largest-remainder rounding so allocated currency totals stay exact."""

    if not weighted_buckets:
        if provider_cost == 0:
            return {}
        raise ProviderBillingError("Billing allocation has no allocation targets")
    if any(weight < 0 or not weight.is_finite() for _, weight in weighted_buckets):
        raise ProviderBillingError("Billing allocation weights are invalid")
    if len({key for key, _ in weighted_buckets}) != len(weighted_buckets):
        raise ProviderBillingError("Billing allocation targets are not unique")
    if provider_cost == 0:
        return {key: Decimal(0) for key, _ in weighted_buckets}

    total_weight = _sum_allocation_decimals(weight for _, weight in weighted_buckets)
    if total_weight <= 0:
        raise ProviderBillingError("Billing allocation has no positive weights")
    with localcontext() as context:
        context.prec = 96
        absolute_cost = provider_cost.copy_abs()
        units_decimal = absolute_cost / _ALLOCATION_UNIT_USD
        units = int(units_decimal.to_integral_value())
        if Decimal(units) != units_decimal:
            raise ProviderBillingError("Billing allocation total exceeds supported precision")
        base_units: dict[tuple[str, ...], int] = {}
        remainders: list[tuple[Decimal, tuple[str, ...]]] = []
        for key, weight in weighted_buckets:
            raw_units = absolute_cost * weight / total_weight / _ALLOCATION_UNIT_USD
            base = int(raw_units.to_integral_value(rounding=ROUND_DOWN))
            base_units[key] = base
            remainders.append((raw_units - Decimal(base), key))
        remaining = units - sum(base_units.values())
        if remaining < 0 or remaining > len(remainders):
            raise ProviderBillingError("Billing allocation rounding is invalid")
        for _, key in sorted(remainders, key=lambda item: (-item[0], item[1]))[:remaining]:
            base_units[key] += 1
        sign = Decimal(-1) if provider_cost < 0 else Decimal(1)
        return {
            key: sign * Decimal(base_units[key]) * _ALLOCATION_UNIT_USD
            for key, _ in weighted_buckets
        }


def _sum_allocation_decimals(values: Iterable[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 96
        return sum(values, Decimal(0))


def _allocation_required_text(value: object, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 512
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise ProviderBillingError(f"Billing allocation {field} is invalid")
    return value


def _allocation_event_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value[:512]


def _allocation_nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderBillingError(f"Billing allocation {field} is invalid")
    return value


def _unattributed_reason(*, identity_type: str, actor_id: str, team_id: str) -> str:
    if identity_type in {"service_account", "ci", "connector"}:
        return identity_type
    if identity_type != "human":
        return "unknown_identity_type"
    if not actor_id and not team_id:
        return "missing_actor_and_team"
    if not actor_id:
        return "missing_actor"
    return "missing_team"


def decode_provider_cost_page(payload: bytes) -> dict[str, object]:
    if not isinstance(payload, bytes):
        raise ProviderBillingError("Billing input page must be bytes")
    if len(payload) > MAX_REPORT_PAGE_BYTES:
        raise ProviderBillingError("Billing input page cannot exceed 16 MiB")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProviderBillingError("Billing input page must be UTF-8 JSON") from error
    try:
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_invalid_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except json.JSONDecodeError as error:
        raise ProviderBillingError(
            f"Billing input page must be valid JSON at line {error.lineno}, column {error.colno}"
        ) from error
    except RecursionError as error:
        raise ProviderBillingError("Billing input page exceeds the supported JSON depth") from error
    if not isinstance(value, dict):
        raise ProviderBillingError("Billing input page must be a JSON object")
    return value


def parse_provider_cost_pages(
    provider: str,
    pages: Sequence[Mapping[str, object]],
    *,
    expected_start: str | None = None,
    expected_end: str | None = None,
) -> ProviderCostReport:
    if provider not in {"openai", "anthropic"}:
        raise ProviderBillingError("Provider must be openai or anthropic")
    if not isinstance(pages, Sequence) or isinstance(pages, (str, bytes)):
        raise ProviderBillingError("Provider cost pages must be a sequence")
    if not 1 <= len(pages) <= MAX_REPORT_PAGES:
        raise ProviderBillingError(
            f"Provider cost report must contain 1 to {MAX_REPORT_PAGES} pages"
        )
    if (expected_start is None) != (expected_end is None):
        raise ProviderBillingError("Provider cost query bounds must be supplied together")
    normalized_expected_start: str | None = None
    normalized_expected_end: str | None = None
    if expected_start is not None and expected_end is not None:
        normalized_expected_start = _rfc3339(expected_start, "Provider query start")
        normalized_expected_end = _rfc3339(expected_end, "Provider query end")
        if normalized_expected_end <= normalized_expected_start:
            raise ProviderBillingError("Provider cost query end must be after its start")

    items: list[ProviderCostItem] = []
    bucket_bounds: list[tuple[str, str]] = []
    page_signatures: set[str] = set()
    bucket_count = 0
    for page_index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            raise ProviderBillingError("Provider cost page must be a JSON object")
        _validate_pagination(page, page_index=page_index, page_count=len(pages))
        data = page.get("data")
        if not isinstance(data, list):
            raise ProviderBillingError("Provider cost page data must be an array")
        if provider == "openai" and page.get("object") != "page":
            raise ProviderBillingError("OpenAI cost report object must be page")
        page_items: list[ProviderCostItem] = []
        page_bounds: list[tuple[str, str]] = []
        for bucket in data:
            bucket_count += 1
            if bucket_count > MAX_REPORT_BUCKETS:
                raise ProviderBillingError("Provider cost report has too many buckets")
            if not isinstance(bucket, Mapping):
                raise ProviderBillingError("Provider cost bucket must be a JSON object")
            if provider == "openai":
                parsed_items, bounds = _parse_openai_bucket(bucket)
            else:
                parsed_items, bounds = _parse_anthropic_bucket(bucket)
            if len(items) + len(page_items) + len(parsed_items) > MAX_REPORT_ITEMS:
                raise ProviderBillingError("Provider cost report has too many items")
            page_items.extend(parsed_items)
            page_bounds.append(bounds)
        page_signature = json.dumps(
            {
                "buckets": page_bounds,
                "items": [asdict(item) for item in page_items],
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        if page_signature in page_signatures:
            raise ProviderBillingError("Provider cost pagination contains a duplicate page")
        page_signatures.add(page_signature)
        items.extend(page_items)
        bucket_bounds.extend(page_bounds)

    if not bucket_bounds and normalized_expected_start is None:
        raise ProviderBillingError("Provider cost report must contain at least one bucket")
    if normalized_expected_start is not None and normalized_expected_end is not None:
        if any(
            start < normalized_expected_start or end > normalized_expected_end
            for start, end in bucket_bounds
        ):
            raise ProviderBillingError("Provider cost bucket is outside the authenticated query window")
        report_start = normalized_expected_start
        report_end = normalized_expected_end
    else:
        report_start = min(start for start, _ in bucket_bounds)
        report_end = max(end for _, end in bucket_bounds)
    canonical_items = sorted(
        (asdict(item) for item in items),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )
    canonical = json.dumps(
        {
            "schema_version": 1,
            "provider": provider,
            "buckets": sorted(
                ({"start": start, "end": end} for start, end in bucket_bounds),
                key=lambda bucket: (bucket["start"], bucket["end"]),
            ),
            "report_start": report_start,
            "report_end": report_end,
            "items": canonical_items,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return ProviderCostReport(
        provider=provider,
        source_sha256=hashlib.sha256(canonical).hexdigest(),
        page_count=len(pages),
        bucket_count=bucket_count,
        report_start=report_start,
        report_end=report_end,
        items=tuple(items),
    )


def _validate_pagination(
    page: Mapping[str, object],
    *,
    page_index: int,
    page_count: int,
) -> None:
    has_more = page.get("has_more")
    next_page = page.get("next_page")
    if not isinstance(has_more, bool):
        raise ProviderBillingError("Provider cost pagination has_more must be boolean")
    is_last = page_index == page_count - 1
    if not is_last:
        if not has_more or not _optional_string(next_page, "next_page", 2_048):
            raise ProviderBillingError("Provider cost pagination is inconsistent")
        return
    if has_more:
        raise ProviderBillingError(
            "Provider cost report is incomplete; supply every page through has_more=false"
        )
    if next_page is not None:
        raise ProviderBillingError("Provider cost pagination is inconsistent")


def _parse_openai_bucket(
    bucket: Mapping[str, object],
) -> tuple[list[ProviderCostItem], tuple[str, str]]:
    if bucket.get("object") != "bucket":
        raise ProviderBillingError("OpenAI cost bucket object must be bucket")
    start = _unix_timestamp(bucket.get("start_time"), "OpenAI bucket start_time")
    end = _unix_timestamp(bucket.get("end_time"), "OpenAI bucket end_time")
    _validate_bucket(start, end)
    results = bucket.get("results")
    if not isinstance(results, list):
        raise ProviderBillingError("OpenAI cost bucket results must be an array")
    parsed: list[ProviderCostItem] = []
    for result in results:
        if not isinstance(result, Mapping):
            raise ProviderBillingError("OpenAI cost result must be a JSON object")
        if result.get("object") != "organization.costs.result":
            raise ProviderBillingError("OpenAI cost result object is unsupported")
        amount = result.get("amount")
        if not isinstance(amount, Mapping):
            raise ProviderBillingError("OpenAI cost amount must be an object")
        value = amount.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
            raise ProviderBillingError("OpenAI cost amount value must be a JSON number")
        currency = _currency(amount.get("currency"))
        project_id = _optional_string(result.get("project_id"), "project_id", 256)
        parsed.append(
            ProviderCostItem(
                bucket_start=start,
                bucket_end=end,
                amount_usd=_amount_usd(Decimal(value)),
                currency=currency,
                provider_scope_kind="project" if project_id is not None else "unscoped",
                provider_scope_id=project_id,
                line_item=_optional_string(result.get("line_item"), "line_item", 512),
            )
        )
    return parsed, (start, end)


def _parse_anthropic_bucket(
    bucket: Mapping[str, object],
) -> tuple[list[ProviderCostItem], tuple[str, str]]:
    start = _rfc3339(bucket.get("starting_at"), "Anthropic bucket starting_at")
    end = _rfc3339(bucket.get("ending_at"), "Anthropic bucket ending_at")
    _validate_bucket(start, end)
    results = bucket.get("results")
    if not isinstance(results, list):
        raise ProviderBillingError("Anthropic cost bucket results must be an array")
    parsed: list[ProviderCostItem] = []
    for result in results:
        if not isinstance(result, Mapping):
            raise ProviderBillingError("Anthropic cost result must be a JSON object")
        raw_amount = result.get("amount")
        if not isinstance(raw_amount, str):
            raise ProviderBillingError("Anthropic cost amount must be a decimal string")
        try:
            amount_usd = Decimal(raw_amount) / Decimal(100)
        except InvalidOperation as error:
            raise ProviderBillingError("Anthropic cost amount is invalid") from error
        currency = _currency(result.get("currency"))
        workspace_id = _optional_string(result.get("workspace_id"), "workspace_id", 256)
        parsed.append(
            ProviderCostItem(
                bucket_start=start,
                bucket_end=end,
                amount_usd=_amount_usd(amount_usd),
                currency=currency,
                provider_scope_kind="workspace" if workspace_id is not None else "unscoped",
                provider_scope_id=workspace_id,
                line_item=_optional_string(result.get("description"), "description", 512),
                cost_type=_optional_string(result.get("cost_type"), "cost_type", 128),
                model=_optional_string(result.get("model"), "model", 256),
                service_tier=_optional_string(result.get("service_tier"), "service_tier", 128),
                token_type=_optional_string(result.get("token_type"), "token_type", 128),
                context_window=_optional_string(result.get("context_window"), "context_window", 128),
                inference_geo=_optional_string(result.get("inference_geo"), "inference_geo", 128),
            )
        )
    return parsed, (start, end)


def _amount_usd(value: Decimal) -> str:
    if not value.is_finite():
        raise ProviderBillingError("Provider cost amount must be finite")
    if value.copy_abs() > MAX_AMOUNT_USD:
        raise ProviderBillingError("Provider cost amount exceeds the supported bound")
    exponent = value.as_tuple().exponent
    if exponent < -MAX_AMOUNT_DECIMAL_PLACES:
        raise ProviderBillingError(
            f"Provider cost amount supports at most {MAX_AMOUNT_DECIMAL_PLACES} decimal places in USD"
        )
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ProviderBillingError("Billing reconciliation value must be finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _reconciliation_decimal(
    reconciliation: Mapping[str, object],
    field: str,
) -> Decimal:
    value = reconciliation.get(field)
    if not isinstance(value, str):
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ProviderBillingError("Billing reconciliation facts are invalid") from error
    if (
        not parsed.is_finite()
        or parsed.copy_abs() > MAX_RECONCILIATION_AMOUNT_USD
        or parsed.as_tuple().exponent < -MAX_AMOUNT_DECIMAL_PLACES
    ):
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    return parsed


def _reconciliation_count(
    reconciliation: Mapping[str, object],
    field: str,
) -> int:
    value = reconciliation.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderBillingError("Billing reconciliation facts are invalid")
    return value


def _currency(value: object) -> str:
    if not isinstance(value, str) or value.upper() != "USD":
        raise ProviderBillingError("Provider cost currency must be USD")
    return "USD"


def _optional_string(value: object, label: str, maximum_bytes: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise ProviderBillingError(f"Provider cost {label} must be a bounded single-line string")
    return value


def _unix_timestamp(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProviderBillingError(f"{label} must be an integer Unix timestamp")
    try:
        parsed = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError) as error:
        raise ProviderBillingError(f"{label} is outside the supported range") from error
    return parsed.isoformat()


def _rfc3339(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ProviderBillingError(f"{label} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderBillingError(f"{label} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderBillingError(f"{label} must include an offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_bucket(start: str, end: str) -> None:
    if end <= start:
        raise ProviderBillingError("Provider cost bucket end must be after its start")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderBillingError("Billing input contains a duplicate JSON object member")
        result[key] = value
    return result


def _invalid_json_constant(_value: str) -> object:
    raise ProviderBillingError("Billing input contains a non-standard JSON numeric constant")
