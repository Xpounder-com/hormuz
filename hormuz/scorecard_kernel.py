"""Deterministic, content-free per-use-case model scorecard evaluation.

The caller must authorize the organization and select immutable source facts
before entering this module.  The kernel accepts only closed metadata fields,
counts work items rather than requests as independent quality samples, keeps
cost bases separate, and emits observational evidence.  It never reads prompt,
response, source-work content, or actor/employee dimensions.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import re
from typing import Mapping

from .scorecard_evidence_reference import (
    COVERAGE_DIMENSIONS,
    GUARDRAIL_DIMENSIONS,
    CohortEvidence,
    CoverageEvidence,
    EvidencePolicy,
    GuardrailEvidence,
    RuleRef,
    ScorecardContext,
    ScorecardEvidenceError,
    StratumEvidence,
    VersionRef,
    qualify_evidence,
)


MAX_COHORTS = 100
MAX_WORK_ITEMS = 10_000
MAX_ATTEMPTS = 100_000
MAX_COST_COMPONENTS = 100_000
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?\Z")
_SIGNED_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?\Z")
_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z"
)
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_ITEM_COST_BASES = frozenset({
    "provider_final", "configured_rate_card_estimate", "allocated_estimate",
})
_COST_BASES = _ITEM_COST_BASES | frozenset({
    "provider_aggregate", "credit_or_discount", "not_available",
})
_METRIC_RULES = frozenset({
    "spend_coverage", "cost_per_accepted", "optimization_lift", "aggregation",
})
_ATTEMPT_STATES = frozenset({"succeeded", "failed", "denied"})
_Z_95 = Decimal("1.959963985")


class ScorecardKernelError(ValueError):
    """Stable, content-free rejection for malformed or inconsistent evidence."""

    def __init__(self) -> None:
        self.code = "scorecard_evidence_invalid"
        super().__init__(self.code)


def _invalid() -> None:
    raise ScorecardKernelError()


def _closed(value: object, fields: set[str] | frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != set(fields):
        _invalid()
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _invalid()
    return value


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        _invalid()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _invalid()
    return value


def _time(value: object) -> datetime:
    if type(value) is not str or _TIME.fullmatch(value) is None:
        _invalid()
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _invalid()


def _decimal(value: object, *, signed: bool = False) -> Decimal:
    pattern = _SIGNED_DECIMAL if signed else _DECIMAL
    if type(value) is not str or pattern.fullmatch(value) is None:
        _invalid()
    try:
        result = Decimal(value)
    except InvalidOperation:
        _invalid()
    if signed and value.startswith("-") and result == 0:
        _invalid()
    return result


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        _invalid()
    with localcontext() as context:
        context.prec = 50
        value = value.quantize(Decimal("0.000000001"))
    text = format(value, "f").rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    with localcontext() as context:
        context.prec = 50
        return numerator / denominator


def _fraction_number(value: Decimal | None) -> float | None:
    return None if value is None else float(_decimal_text(value))


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _invalid()


def _rule(value: object, *, nullable: bool = False) -> dict[str, object] | None:
    if value is None and nullable:
        return None
    item = _closed(value, {"rule_id", "version", "digest"})
    return {
        "rule_id": _identifier(item["rule_id"]),
        "version": _version(item["version"]),
        "digest": _digest(item["digest"]),
    }


def _rule_object(value: dict[str, object] | None) -> RuleRef | None:
    if value is None:
        return None
    return RuleRef(value["rule_id"], value["version"], value["digest"])


def _version_ref(value: object, *, nullable: bool = False) -> dict[str, object] | None:
    if value is None and nullable:
        return None
    item = _closed(value, {"id", "version"})
    return {"id": _identifier(item["id"]), "version": _version(item["version"])}


def _model(value: object, *, nullable: bool = False) -> dict[str, object] | None:
    if value is None and nullable:
        return None
    item = _closed(value, {"provider_id", "model_id", "model_version"})
    model_version = item["model_version"]
    if model_version is not None:
        model_version = _identifier(model_version)
    return {
        "provider_id": _identifier(item["provider_id"]),
        "model_id": _identifier(item["model_id"]),
        "model_version": model_version,
    }


def _coverage(value: object) -> tuple[CoverageEvidence, dict[str, object], str]:
    item = _closed(value, {"numerator", "denominator", "provenance_digest"})
    numerator, denominator = item["numerator"], item["denominator"]
    if (numerator is None) != (denominator is None):
        _invalid()
    if numerator is not None:
        first, second = _decimal(numerator), _decimal(denominator)
        if first > second:
            _invalid()
        ratio = _divide(first, second)
    else:
        ratio = None
    result = {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": _fraction_number(ratio),
        "reason_code": "eligible" if ratio is not None else "missing_evidence",
    }
    return CoverageEvidence("pricing", numerator, denominator), result, _digest(item["provenance_digest"])


def _coverage_dimension(name: str, value: object):
    evidence, output, digest = _coverage(value)
    return CoverageEvidence(name, evidence.numerator, evidence.denominator), output, digest


def _aggregate_coverage(items: list[dict[str, object]]) -> dict[str, object]:
    if any(item["numerator"] is None or item["ratio"] is None for item in items):
        return {"numerator": None, "denominator": None, "ratio": None,
                "reason_code": "missing_evidence"}
    numerator = sum((_decimal(item["numerator"]) for item in items), Decimal(0))
    denominator = sum((_decimal(item["denominator"]) for item in items), Decimal(0))
    ratio = _divide(numerator, denominator)
    return {
        "numerator": _decimal_text(numerator),
        "denominator": _decimal_text(denominator),
        "ratio": _fraction_number(ratio),
        "reason_code": "eligible" if ratio is not None else "missing_evidence",
    }


def _metric(rule, *, value=None, numerator=None, denominator=None, unit, status, reason):
    return {
        "rule": rule,
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "unit": unit,
        "status": status,
        "reason_code": reason,
    }


def _inconclusive_uncertainty(metric: str, cohort_id: str, point: str | None = None):
    return {
        "cohort_id": cohort_id,
        "metric": metric,
        "method": "not_available",
        "confidence_level": "0.95",
        "point": point,
        "lower": None,
        "upper": None,
        "cluster_count": None,
        "status": "inconclusive",
        "reason_code": "missing_evidence",
    }


def _exact_uncertainty(metric: str, cohort_id: str, point: str | None):
    if point is None:
        return _inconclusive_uncertainty(metric, cohort_id)
    return {
        "cohort_id": cohort_id,
        "metric": metric,
        "method": "exact_source_ratio",
        "confidence_level": "0.95",
        "point": point,
        "lower": point,
        "upper": point,
        "cluster_count": None,
        "status": "eligible",
        "reason_code": "eligible",
    }


def _wilson(metric: str, cohort_id: str, successes: int, count: int):
    point = None if count == 0 else Decimal(successes) / Decimal(count)
    if count < 3:
        return _inconclusive_uncertainty(
            metric, cohort_id, None if point is None else _decimal_text(point),
        )
    with localcontext() as context:
        context.prec = 50
        n = Decimal(count)
        z2 = _Z_95 * _Z_95
        denominator = Decimal(1) + z2 / n
        center = (point + z2 / (Decimal(2) * n)) / denominator
        margin = _Z_95 * (
            (point * (Decimal(1) - point) / n + z2 / (Decimal(4) * n * n)).sqrt()
        ) / denominator
        lower = max(Decimal(0), center - margin)
        upper = min(Decimal(1), center + margin)
    return {
        "cohort_id": cohort_id,
        "metric": metric,
        "method": "work_item_wilson_95",
        "confidence_level": "0.95",
        "point": _decimal_text(point),
        "lower": _decimal_text(lower),
        "upper": _decimal_text(upper),
        "cluster_count": count,
        "status": "eligible",
        "reason_code": "eligible",
    }


def _jackknife_ratio(cohort_id: str, clusters: list[tuple[Decimal, int]]):
    total_cost = sum((item[0] for item in clusters), Decimal(0))
    total_accepted = sum(item[1] for item in clusters)
    point = _divide(total_cost, Decimal(total_accepted))
    if point is None or len(clusters) < 3:
        return _inconclusive_uncertainty(
            "quality_qualified_cost_per_accepted_work_item",
            cohort_id,
            None if point is None else _decimal_text(point),
        )
    estimates = []
    for cost, accepted in clusters:
        denominator = total_accepted - accepted
        if denominator <= 0:
            return _inconclusive_uncertainty(
                "quality_qualified_cost_per_accepted_work_item", cohort_id,
                _decimal_text(point),
            )
        estimates.append((total_cost - cost) / Decimal(denominator))
    with localcontext() as context:
        context.prec = 50
        count = Decimal(len(estimates))
        center = sum(estimates, Decimal(0)) / count
        variance = (count - Decimal(1)) / count * sum(
            ((item - center) * (item - center) for item in estimates), Decimal(0)
        )
        margin = _Z_95 * variance.sqrt()
        lower = max(Decimal(0), point - margin)
        upper = point + margin
    return {
        "cohort_id": cohort_id,
        "metric": "quality_qualified_cost_per_accepted_work_item",
        "method": "work_item_cluster_jackknife_95",
        "confidence_level": "0.95",
        "point": _decimal_text(point),
        "lower": _decimal_text(lower),
        "upper": _decimal_text(upper),
        "cluster_count": len(clusters),
        "status": "eligible",
        "reason_code": "eligible",
    }


def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    with localcontext() as context:
        context.prec = 50
        index = (Decimal(len(ordered) - 1) * percentile)
        lower = int(index)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = index - Decimal(lower)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _latency_uncertainty(cohort_id: str, clusters: list[list[Decimal]]):
    point = _percentile([value for cluster in clusters for value in cluster], Decimal("0.95"))
    if point is None or len(clusters) < 3 or any(not cluster for cluster in clusters):
        return _inconclusive_uncertainty(
            "latency_p95_ms", cohort_id, None if point is None else _decimal_text(point),
        )
    estimates = []
    for index in range(len(clusters)):
        selected = [
            value for offset, cluster in enumerate(clusters) if offset != index for value in cluster
        ]
        estimate = _percentile(selected, Decimal("0.95"))
        if estimate is None:
            return _inconclusive_uncertainty("latency_p95_ms", cohort_id, _decimal_text(point))
        estimates.append(estimate)
    return {
        "cohort_id": cohort_id,
        "metric": "latency_p95_ms",
        "method": "work_item_delete_one_range",
        "confidence_level": "0.95",
        "point": _decimal_text(point),
        "lower": _decimal_text(min(estimates)),
        "upper": _decimal_text(max(estimates)),
        "cluster_count": len(clusters),
        "status": "eligible",
        "reason_code": "eligible",
    }


def _guardrail_result(strata: list[dict[str, object]], dimension: str):
    values = [item["guardrails"][dimension] for item in strata]
    rules = {_canonical(item["rule"]) for item in values if item["rule"] is not None}
    rule = values[0]["rule"] if len(rules) == 1 and all(item["rule"] is not None for item in values) else None
    states = {item["state"] for item in values}
    if "fail" in states:
        state, reason = "fail", "below_threshold"
    elif states == {"pass"} and rule is not None:
        state, reason = "pass", "eligible"
    else:
        state, reason = "inconclusive", "missing_evidence"
    return {"state": state, "rule": rule, "value": None, "reason_code": reason}


def _aggregate_cost_components(components: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in components:
        key = _canonical({name: item[name] for name in ("basis", "currency", "rate_card")})
        grouped[key].append(item)
    result = []
    for key in sorted(grouped):
        selected = grouped[key]
        first = selected[0]
        amounts = [item["amount"] for item in selected]
        amount = None if any(item is None for item in amounts) else _decimal_text(sum(
            (_decimal(item, signed=True) for item in amounts), Decimal(0)
        ))
        provenance = hashlib.sha256("\n".join(sorted(
            item["provenance_digest"] for item in selected
        )).encode("ascii")).hexdigest()
        result.append({
            "basis": first["basis"], "amount": amount, "currency": first["currency"],
            "rate_card": first["rate_card"], "provenance_digest": provenance,
        })
    return result


def _cost_component(value: object, cohort_rate_card):
    item = _closed(value, {"basis", "amount", "currency", "rate_card", "provenance_digest"})
    basis = item["basis"]
    if type(basis) is not str or basis not in _COST_BASES:
        _invalid()
    amount, currency = item["amount"], item["currency"]
    if basis == "not_available":
        if amount is not None or currency is not None or item["rate_card"] is not None:
            _invalid()
        rate_card = None
    else:
        numeric = _decimal(amount, signed=True)
        if (basis == "credit_or_discount") != (numeric < 0):
            _invalid()
        if type(currency) is not str or _CURRENCY.fullmatch(currency) is None:
            _invalid()
        rate_card = _version_ref(item["rate_card"], nullable=True)
        if basis in {"configured_rate_card_estimate", "allocated_estimate"} and (
            rate_card is None or rate_card != cohort_rate_card
        ):
            _invalid()
        if basis in {"provider_final", "provider_aggregate", "credit_or_discount"} and rate_card is not None:
            _invalid()
    return {
        "basis": basis, "amount": amount, "currency": currency, "rate_card": rate_card,
        "provenance_digest": _digest(item["provenance_digest"]),
    }


def _parse_cohort(value, *, context, policy, required_strata, metric_rules):
    fields = {
        "cohort_id", "actual_model", "client_id", "policy", "rate_card", "cost_basis",
        "currency", "association_rule", "last_observed_at", "connector_ids", "coverage", "strata",
    }
    item = _closed(value, fields)
    if len(_canonical(item).encode("ascii")) > MAX_DOCUMENT_BYTES:
        _invalid()
    cohort_id = _identifier(item["cohort_id"])
    actual_model = _model(item["actual_model"], nullable=True)
    client_id = _identifier(item["client_id"])
    policy_ref = _version_ref(item["policy"], nullable=True)
    rate_card = _version_ref(item["rate_card"], nullable=True)
    cost_basis = item["cost_basis"]
    currency = item["currency"]
    if type(cost_basis) is not str or cost_basis not in _ITEM_COST_BASES:
        _invalid()
    if type(currency) is not str or _CURRENCY.fullmatch(currency) is None:
        _invalid()
    association_rule = _rule(item["association_rule"], nullable=True)
    last_observed = item["last_observed_at"]
    if last_observed is not None:
        _time(last_observed)
    connectors = item["connector_ids"]
    if type(connectors) is not list or not 1 <= len(connectors) <= 100:
        _invalid()
    connectors = tuple(sorted(_identifier(value) for value in connectors))
    if len(set(connectors)) != len(connectors):
        _invalid()

    coverage_value = _closed(item["coverage"], set(COVERAGE_DIMENSIONS))
    coverage_evidence, coverage_output, source_digests = [], {}, []
    for name in sorted(COVERAGE_DIMENSIONS):
        evidence, output, digest = _coverage_dimension(name, coverage_value[name])
        coverage_evidence.append(evidence)
        coverage_output[name] = output
        source_digests.append(digest)

    strata_input = item["strata"]
    if type(strata_input) is not list or not 1 <= len(strata_input) <= 100:
        _invalid()
    parsed_strata, evidence_strata, work_items = [], [], []
    attempt_ids, work_ids = set(), set()
    components, requested, routed, all_actual = [], set(), set(), set()
    attempt_count = 0
    for raw_stratum in strata_input:
        stratum = _closed(raw_stratum, {"stratum_id", "guardrails", "work_items"})
        stratum_id = _identifier(stratum["stratum_id"])
        guard_input = _closed(stratum["guardrails"], set(GUARDRAIL_DIMENSIONS))
        guard_output, guard_evidence = {}, []
        for dimension in sorted(GUARDRAIL_DIMENSIONS):
            guard = _closed(guard_input[dimension], {"state", "rule"})
            state = guard["state"]
            if type(state) is not str or state not in {"pass", "fail", "inconclusive", "not_applicable"}:
                _invalid()
            rule = _rule(guard["rule"], nullable=True)
            guard_output[dimension] = {"state": state, "rule": rule}
            guard_evidence.append(GuardrailEvidence(dimension, state, _rule_object(rule)))
        raw_work = stratum["work_items"]
        if type(raw_work) is not list or not raw_work:
            _invalid()
        stratum_work_ids = []
        for raw_work_item in raw_work:
            work = _closed(raw_work_item, {"work_item_id", "source_digest", "accepted", "attempts"})
            work_id = _identifier(work["work_item_id"])
            if work_id in work_ids:
                _invalid()
            work_ids.add(work_id)
            stratum_work_ids.append(work_id)
            source_digests.append(_digest(work["source_digest"]))
            accepted = work["accepted"]
            if accepted is not None and type(accepted) is not bool:
                _invalid()
            raw_attempts = work["attempts"]
            if type(raw_attempts) is not list or not raw_attempts:
                _invalid()
            parsed_attempts = []
            for raw_attempt in raw_attempts:
                attempt = _closed(raw_attempt, {
                    "attempt_id", "source_digest", "occurred_at", "requested_model_id",
                    "routed_model_id", "actual_model", "status", "retry_ordinal", "fallback",
                    "latency_ms", "input_tokens", "output_tokens", "cached_input_tokens",
                    "reasoning_tokens", "cost_components",
                })
                attempt_id = _identifier(attempt["attempt_id"])
                if attempt_id in attempt_ids:
                    _invalid()
                attempt_ids.add(attempt_id)
                source_digests.append(_digest(attempt["source_digest"]))
                occurred = _time(attempt["occurred_at"])
                if not context["start"] <= occurred < context["end"]:
                    _invalid()
                requested_id = _identifier(attempt["requested_model_id"])
                routed_id = _identifier(attempt["routed_model_id"])
                attempt_actual = _model(attempt["actual_model"], nullable=True)
                if attempt_actual != actual_model:
                    _invalid()
                status = attempt["status"]
                retry = attempt["retry_ordinal"]
                fallback = attempt["fallback"]
                if (
                    type(status) is not str or status not in _ATTEMPT_STATES
                    or type(retry) is not int or not 0 <= retry <= 1000
                    or type(fallback) is not bool
                ):
                    _invalid()
                latency = attempt["latency_ms"]
                if latency is not None:
                    _decimal(latency)
                tokens = {}
                for name in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
                    token = attempt[name]
                    if token is not None and (type(token) is not int or not 0 <= token <= 10**12):
                        _invalid()
                    tokens[name] = token
                raw_components = attempt["cost_components"]
                if type(raw_components) is not list or not 1 <= len(raw_components) <= 100:
                    _invalid()
                parsed_components = [_cost_component(value, rate_card) for value in raw_components]
                if len([value for value in parsed_components if value["basis"] == cost_basis]) != 1:
                    _invalid()
                components.extend(parsed_components)
                source_digests.extend(value["provenance_digest"] for value in parsed_components)
                requested.add(requested_id)
                routed.add(routed_id)
                all_actual.add(_canonical(attempt_actual))
                parsed_attempts.append({
                    "attempt_id": attempt_id, "occurred_at": attempt["occurred_at"],
                    "requested_model_id": requested_id, "routed_model_id": routed_id,
                    "actual_model": attempt_actual, "status": status, "retry_ordinal": retry,
                    "fallback": fallback, "latency_ms": latency, **tokens,
                    "cost_components": parsed_components,
                })
                attempt_count += 1
                if attempt_count > MAX_ATTEMPTS or len(components) > MAX_COST_COMPONENTS:
                    _invalid()
            ordinals = sorted(value["retry_ordinal"] for value in parsed_attempts)
            if ordinals != list(range(len(parsed_attempts))):
                _invalid()
            parsed_attempts.sort(key=lambda value: (value["retry_ordinal"], value["attempt_id"]))
            work_items.append({
                "work_item_id": work_id, "accepted": accepted, "attempts": parsed_attempts,
            })
        evidence_strata.append(StratumEvidence(
            stratum_id, tuple(stratum_work_ids), tuple(guard_evidence),
        ))
        parsed_strata.append({
            "stratum_id": stratum_id, "guardrails": guard_output,
            "work_item_ids": tuple(stratum_work_ids),
        })
    if len(work_items) > MAX_WORK_ITEMS:
        _invalid()
    if {item["stratum_id"] for item in parsed_strata} != set(required_strata):
        # The qualifier also returns inconclusive, but duplicate/malformed strata
        # would make later aggregation ambiguous and are rejected here.
        if len({item["stratum_id"] for item in parsed_strata}) != len(parsed_strata):
            _invalid()

    evidence = CohortEvidence(
        cohort_id=cohort_id,
        organization_id=context["organization_id"],
        work_scope=VersionRef(context["work_scope_id"], context["work_scope_version"]),
        actual_provider_id=None if actual_model is None else actual_model["provider_id"],
        actual_model_id=None if actual_model is None else actual_model["model_id"],
        actual_model_version=None if actual_model is None else actual_model["model_version"],
        client_id=client_id,
        policy=None if policy_ref is None else VersionRef(policy_ref["id"], policy_ref["version"]),
        rate_card=None if rate_card is None else VersionRef(rate_card["id"], rate_card["version"]),
        cost_basis=cost_basis,
        currency=currency,
        association_rule=_rule_object(association_rule),
        last_observed_at=last_observed,
        coverage=tuple(coverage_evidence),
        strata=tuple(evidence_strata),
    )
    try:
        qualification = qualify_evidence(context["evidence_context"], policy, evidence)
    except ScorecardEvidenceError:
        _invalid()

    selected_costs, clusters, latencies = [], [], []
    accepted_count, first_pass, known_outcomes = 0, 0, 0
    token_totals = {name: 0 for name in (
        "input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens",
    )}
    token_complete = dict.fromkeys(token_totals, True)
    fallback_count = denial_count = 0
    for work in work_items:
        accepted = work["accepted"]
        known_outcomes += accepted is not None
        accepted_count += accepted is True
        attempts = work["attempts"]
        first_pass += bool(accepted is True and attempts[0]["status"] == "succeeded")
        cost = Decimal(0)
        cluster_latency = []
        for attempt in attempts:
            chosen = next(value for value in attempt["cost_components"] if value["basis"] == cost_basis)
            if chosen["amount"] is None:
                _invalid()
            numeric = _decimal(chosen["amount"], signed=True)
            if numeric < 0:
                _invalid()
            cost += numeric
            selected_costs.append(numeric)
            fallback_count += attempt["fallback"]
            denial_count += attempt["status"] == "denied"
            if attempt["latency_ms"] is not None:
                cluster_latency.append(_decimal(attempt["latency_ms"]))
            for name in token_totals:
                if attempt[name] is None:
                    token_complete[name] = False
                else:
                    token_totals[name] += attempt[name]
        clusters.append((cost, int(accepted is True)))
        latencies.append(cluster_latency)

    spend = coverage_output["eligible_governed_spend"]
    spend_metric = _metric(
        metric_rules["spend_coverage"],
        value=None if spend["ratio"] is None else _decimal_text(Decimal(str(spend["ratio"]))),
        numerator=spend["numerator"],
        denominator=spend["denominator"], unit="ratio",
        status="eligible" if qualification.status == "eligible" and spend["ratio"] is not None else "inconclusive",
        reason="eligible" if qualification.status == "eligible" and spend["ratio"] is not None else "below_threshold",
    )
    total_cost = sum(selected_costs, Decimal(0))
    cost_point = _divide(total_cost, Decimal(accepted_count))
    cost_uncertainty = _jackknife_ratio(cohort_id, clusters)
    cost_eligible = (
        qualification.status == "eligible" and cost_point is not None
        and cost_uncertainty["status"] == "eligible"
    )
    cost_metric = _metric(
        metric_rules["cost_per_accepted"],
        value=None if cost_point is None else _decimal_text(cost_point),
        numerator=_decimal_text(total_cost), denominator=str(accepted_count),
        unit="currency_per_accepted_item", status="eligible" if cost_eligible else "inconclusive",
        reason="eligible" if cost_eligible else "missing_evidence",
    )

    quality_uncertainty = _wilson(cohort_id=cohort_id, metric="accepted_work_item_rate",
                                  successes=accepted_count, count=known_outcomes)
    reliability_uncertainty = _wilson(cohort_id=cohort_id, metric="first_pass_success",
                                      successes=first_pass, count=known_outcomes)
    latency_uncertainty = _latency_uncertainty(cohort_id, latencies)
    attempts_total = len(attempt_ids)
    retry_count = sum(max(0, len(work["attempts"]) - 1) for work in work_items)
    driver = {
        "actual_model_mix": [{"model": actual_model, "attempt_count": attempts_total}],
        "cache_and_token_mix": {
            name: token_totals[name] if token_complete[name] else None for name in token_totals
        },
        "latency": {
            "p50_ms": None if not any(latencies) else _decimal_text(_percentile(
                [value for cluster in latencies for value in cluster], Decimal("0.5")
            )),
            "p95_ms": latency_uncertainty["point"],
        },
        "retries": retry_count,
        "first_pass_success": {
            "numerator": str(first_pass), "denominator": str(known_outcomes),
            "ratio": None if reliability_uncertainty["point"] is None else float(
                reliability_uncertainty["point"]
            ),
            "reason_code": "eligible" if reliability_uncertainty["point"] is not None else "missing_evidence",
        },
        "fallback_and_denial_rate": {
            "fallback": {
                "numerator": str(fallback_count), "denominator": str(attempts_total),
                "ratio": None if attempts_total == 0 else _fraction_number(
                    Decimal(fallback_count) / Decimal(attempts_total)
                ),
                "reason_code": "eligible" if attempts_total else "missing_evidence",
            },
            "denial": {
                "numerator": str(denial_count), "denominator": str(attempts_total),
                "ratio": None if attempts_total == 0 else _fraction_number(
                    Decimal(denial_count) / Decimal(attempts_total)
                ),
                "reason_code": "eligible" if attempts_total else "missing_evidence",
            },
        },
    }
    guardrails = {name: _guardrail_result(parsed_strata, name) for name in sorted(GUARDRAIL_DIMENSIONS)}
    observed = [ratio for name, ratio in qualification.coverage_ratios if name != "excluded"]
    observed_coverage = None if any(value is None for value in observed) else min(observed)
    eligibility = {
        "rule": {
            "rule_id": policy.rule.rule_id, "version": policy.rule.version, "digest": policy.rule.digest,
        } if policy.rule is not None else None,
        "declared_at": policy.declared_at,
        "minimum_coverage": None if policy.minimum_coverage is None else float(policy.minimum_coverage),
        "minimum_sample": policy.minimum_sample,
        "sample_count": qualification.distinct_work_item_count,
        "observed_coverage": None if observed_coverage is None else _fraction_number(
            Decimal(observed_coverage.numerator) / Decimal(observed_coverage.denominator)
        ),
        "status": qualification.status,
    }
    cost_components = _aggregate_cost_components(components)
    connector_coverage = coverage_output["connector"]
    cohort_output = {
        "cohort_id": cohort_id,
        "actual_model": actual_model,
        "policy": policy_ref,
        "rate_card": rate_card,
        "cost_basis": cost_basis,
        "cost_components": cost_components,
        "connector_coverage": connector_coverage,
        "association_rule": association_rule,
        "eligibility": eligibility,
        "metrics": {
            "use_case_attributed_spend_coverage": spend_metric,
            "quality_qualified_cost_per_accepted_work_item": cost_metric,
            "optimization_lift_vs_declared_baseline": _metric(
                metric_rules["optimization_lift"], unit="relative_lift",
                status="inconclusive", reason="missing_evidence",
            ),
        },
        "drivers": driver,
        "guardrails": guardrails,
    }
    dimensions = {
        "cohort_id": cohort_id, "client_id": client_id,
        "requested_model_ids": sorted(requested), "routed_model_ids": sorted(routed),
        "actual_model": actual_model, "policy": policy_ref, "rate_card": rate_card,
        "cost_basis": cost_basis, "currency": currency,
        "connector_ids": list(connectors), "association_rule": association_rule,
    }
    uncertainties = [
        _exact_uncertainty(
            "use_case_attributed_spend_coverage", cohort_id,
            None if spend["ratio"] is None else _decimal_text(Decimal(str(spend["ratio"]))),
        ),
        cost_uncertainty, quality_uncertainty, reliability_uncertainty, latency_uncertainty,
    ]
    cohort_digest = hashlib.sha256(_canonical({
        "cohort": cohort_output, "dimensions": dimensions,
        "source_digests": sorted(source_digests),
    }).encode("ascii")).hexdigest()
    return {
        "output": cohort_output, "dimensions": dimensions, "coverage": coverage_output,
        "uncertainty": uncertainties, "qualification": qualification,
        "cost_uncertainty": cost_uncertainty, "quality_uncertainty": quality_uncertainty,
        "reliability_uncertainty": reliability_uncertainty,
        "latency_uncertainty": latency_uncertainty,
        "cohort_digest": cohort_digest, "source_digests": source_digests,
        "currency": currency, "strata": tuple(sorted(value["stratum_id"] for value in parsed_strata)),
    }


def _axis_interval(cohort, name):
    selected = {
        "cost": cohort["cost_uncertainty"],
        "quality": cohort["quality_uncertainty"],
        "latency": cohort["latency_uncertainty"],
        "reliability": cohort["reliability_uncertainty"],
    }[name]
    if selected["status"] != "eligible":
        return None
    return (_decimal(selected["lower"]), _decimal(selected["upper"]), _decimal(selected["point"]))


def _dominates(left, right) -> bool:
    left_axes = {name: _axis_interval(left, name) for name in ("cost", "quality", "latency", "reliability")}
    right_axes = {name: _axis_interval(right, name) for name in left_axes}
    if any(value is None for value in (*left_axes.values(), *right_axes.values())):
        return False
    comparisons = [
        left_axes["cost"][1] <= right_axes["cost"][0],
        left_axes["quality"][0] >= right_axes["quality"][1],
        left_axes["latency"][1] <= right_axes["latency"][0],
        left_axes["reliability"][0] >= right_axes["reliability"][1],
    ]
    strict = [
        left_axes["cost"][1] < right_axes["cost"][0],
        left_axes["quality"][0] > right_axes["quality"][1],
        left_axes["latency"][1] < right_axes["latency"][0],
        left_axes["reliability"][0] > right_axes["reliability"][1],
    ]
    return all(comparisons) and any(strict)


def build_scorecard_evaluation(value: Mapping[str, object]) -> dict[str, object]:
    """Build one immutable scorecard evaluation from authorized metadata facts."""

    fields = {
        "schema_id", "schema_version", "organization_id", "scorecard_id", "version",
        "work_scope", "window", "evaluated_at", "generated_at", "expires_at",
        "decision_owner_id", "review_after", "supersedes_version", "baseline_cohort_id",
        "eligibility_policy", "metric_rules", "cohorts",
    }
    item = _closed(value, fields)
    if item["schema_id"] != "hormuz.scorecard-evaluation-input" or item["schema_version"] != 1:
        _invalid()
    organization = _identifier(item["organization_id"])
    scorecard_id = _identifier(item["scorecard_id"])
    version = _version(item["version"])
    work_scope = _closed(item["work_scope"], {"work_scope_id", "version"})
    work_scope_id, work_scope_version = _identifier(work_scope["work_scope_id"]), _version(work_scope["version"])
    window = _closed(item["window"], {"start_at", "end_at"})
    start, end = _time(window["start_at"]), _time(window["end_at"])
    evaluated, generated, expires = _time(item["evaluated_at"]), _time(item["generated_at"]), _time(item["expires_at"])
    review_after = _time(item["review_after"])
    if not start < end <= evaluated <= generated < expires or not generated < review_after:
        _invalid()
    decision_owner = _identifier(item["decision_owner_id"])
    supersedes = item["supersedes_version"]
    if supersedes is not None:
        supersedes = _version(supersedes)
    if (version == 1) != (supersedes is None) or (supersedes is not None and supersedes != version - 1):
        _invalid()
    baseline_id = _identifier(item["baseline_cohort_id"])

    raw_policy = _closed(item["eligibility_policy"], {
        "rule", "declared_at", "minimum_coverage", "minimum_sample",
        "maximum_staleness_seconds", "required_strata",
    })
    policy_rule = _rule(raw_policy["rule"], nullable=True)
    declared = raw_policy["declared_at"]
    if declared is not None:
        _time(declared)
    minimum_coverage = raw_policy["minimum_coverage"]
    if minimum_coverage is not None:
        _decimal(minimum_coverage)
    minimum_sample = raw_policy["minimum_sample"]
    maximum_staleness = raw_policy["maximum_staleness_seconds"]
    required_strata = raw_policy["required_strata"]
    if type(required_strata) is not list:
        _invalid()
    required_strata = tuple(sorted(_identifier(value) for value in required_strata))
    try:
        policy = EvidencePolicy(
            _rule_object(policy_rule), declared, minimum_coverage, minimum_sample,
            maximum_staleness, required_strata,
        )
    except ScorecardEvidenceError:
        _invalid()

    raw_rules = _closed(item["metric_rules"], set(_METRIC_RULES))
    metric_rules = {name: _rule(raw_rules[name]) for name in sorted(_METRIC_RULES)}
    evidence_context = ScorecardContext(
        organization, VersionRef(work_scope_id, work_scope_version),
        window["start_at"], window["end_at"], item["evaluated_at"],
    )
    context = {
        "organization_id": organization, "work_scope_id": work_scope_id,
        "work_scope_version": work_scope_version, "start": start, "end": end,
        "evidence_context": evidence_context,
    }
    raw_cohorts = item["cohorts"]
    if type(raw_cohorts) is not list or not 1 <= len(raw_cohorts) <= MAX_COHORTS:
        _invalid()
    try:
        cohorts = [
            _parse_cohort(value, context=context, policy=policy,
                          required_strata=required_strata, metric_rules=metric_rules)
            for value in raw_cohorts
        ]
    except ScorecardEvidenceError:
        _invalid()
    ids = [value["output"]["cohort_id"] for value in cohorts]
    if len(set(ids)) != len(ids) or baseline_id not in ids:
        _invalid()
    cohorts.sort(key=lambda value: value["output"]["cohort_id"])
    ids = [value["output"]["cohort_id"] for value in cohorts]
    all_sources = [digest for cohort in cohorts for digest in cohort["source_digests"]]
    if len(set(all_sources)) != len(all_sources):
        _invalid()

    baseline = cohorts[ids.index(baseline_id)]
    baseline_cost = baseline["output"]["metrics"]["quality_qualified_cost_per_accepted_work_item"]
    eligible_lifts = 0
    lift_uncertainty = []
    for cohort in cohorts:
        metric = cohort["output"]["metrics"]["quality_qualified_cost_per_accepted_work_item"]
        comparable = (
            cohort["currency"] == baseline["currency"]
            and cohort["strata"] == baseline["strata"]
            and metric["status"] == "eligible"
            and baseline_cost["status"] == "eligible"
            and _decimal(baseline_cost["value"]) > 0
        )
        if comparable:
            baseline_value, current_value = _decimal(baseline_cost["value"]), _decimal(metric["value"])
            lift = (baseline_value - current_value) / baseline_value
            result = _metric(
                metric_rules["optimization_lift"], value=_decimal_text(lift),
                numerator=_decimal_text(baseline_value - current_value), denominator=_decimal_text(baseline_value),
                unit="relative_lift", status="eligible", reason="eligible",
            )
            base_interval = baseline["cost_uncertainty"]
            current_interval = cohort["cost_uncertainty"]
            lower = (_decimal(base_interval["lower"]) - _decimal(current_interval["upper"])) / _decimal(base_interval["upper"])
            upper = (_decimal(base_interval["upper"]) - _decimal(current_interval["lower"])) / _decimal(base_interval["lower"])
            uncertainty = {
                "cohort_id": cohort["output"]["cohort_id"], "metric": "optimization_lift_vs_declared_baseline",
                "method": "independent_cluster_interval_propagation_95", "confidence_level": "0.95",
                "point": _decimal_text(lift), "lower": _decimal_text(lower), "upper": _decimal_text(upper),
                "cluster_count": min(base_interval["cluster_count"], current_interval["cluster_count"]),
                "status": "eligible", "reason_code": "eligible",
            }
            if cohort is not baseline:
                eligible_lifts += 1
        else:
            result = _metric(
                metric_rules["optimization_lift"], unit="relative_lift",
                status="inconclusive", reason="missing_evidence",
            )
            uncertainty = _inconclusive_uncertainty(
                "optimization_lift_vs_declared_baseline", cohort["output"]["cohort_id"],
            )
        cohort["output"]["metrics"]["optimization_lift_vs_declared_baseline"] = result
        lift_uncertainty.append(uncertainty)

    pareto_candidates = [cohort for cohort in cohorts if (
        cohort["qualification"].status == "eligible"
        and all(_axis_interval(cohort, name) is not None for name in ("cost", "quality", "latency", "reliability"))
    )]
    pareto = sorted(
        candidate["output"]["cohort_id"] for candidate in pareto_candidates
        if not any(other is not candidate and _dominates(other, candidate) for other in pareto_candidates)
    )
    state = "eligible" if eligible_lifts and len(pareto_candidates) >= 2 else "inconclusive"
    reason = "eligible" if state == "eligible" else "missing_evidence"
    coverage_by_name = {name: [] for name in COVERAGE_DIMENSIONS}
    for cohort in cohorts:
        for name in COVERAGE_DIMENSIONS:
            coverage_by_name[name].append(cohort["coverage"][name])
    aggregate = {name: _aggregate_coverage(values) for name, values in coverage_by_name.items()}
    public_coverage = {
        "eligible_governed_attempts": aggregate["eligible_governed_attempts"],
        "eligible_governed_spend": aggregate["eligible_governed_spend"],
        "eligible_external_outcome_events": aggregate["linked_outcome"],
        "eligible_association_candidates": aggregate["association"],
        "pricing": aggregate["pricing"], "connector": aggregate["connector"],
        "association": aggregate["association"],
    }
    scorecard = {
        "schema_id": "hormuz.model-scorecard", "schema_version": 1,
        "organization_id": organization, "scorecard_id": scorecard_id, "version": version,
        "work_scope": {"work_scope_id": work_scope_id, "version": work_scope_version},
        "window": {"start_at": window["start_at"], "end_at": window["end_at"]},
        "generated_at": item["generated_at"], "expires_at": item["expires_at"],
        "state": state, "evidence_level": "associated", "controlled_design": None,
        "coverage": public_coverage, "cohorts": [value["output"] for value in cohorts],
        "baseline_cohort_id": baseline_id if baseline_cost["status"] == "eligible" else None,
        "pareto_cohort_ids": pareto, "decision_owner_id": decision_owner,
        "review_after": item["review_after"], "supersedes_version": supersedes,
        "reason_code": reason,
    }
    input_digest = hashlib.sha256(_canonical({
        "schema_id": item["schema_id"], "schema_version": item["schema_version"],
        "organization_id": organization, "scorecard_id": scorecard_id, "version": version,
        "work_scope": scorecard["work_scope"], "window": scorecard["window"],
        "evaluated_at": item["evaluated_at"], "generated_at": item["generated_at"],
        "expires_at": item["expires_at"], "decision_owner_id": decision_owner,
        "review_after": item["review_after"], "supersedes_version": supersedes,
        "baseline_cohort_id": baseline_id,
        "eligibility_policy": {
            "rule": policy_rule, "declared_at": declared,
            "minimum_coverage": minimum_coverage, "minimum_sample": minimum_sample,
            "maximum_staleness_seconds": maximum_staleness,
            "required_strata": list(required_strata),
        },
        "metric_rules": metric_rules,
        "cohort_digests": [entry["cohort_digest"] for entry in cohorts],
    }).encode("ascii")).hexdigest()
    result = {
        "schema_id": "hormuz.scorecard-evaluation", "schema_version": 1,
        "scorecard": scorecard,
        "cohort_dimensions": [value["dimensions"] for value in cohorts],
        "uncertainty": [entry for cohort in cohorts for entry in cohort["uncertainty"]] + lift_uncertainty,
        "lineage": {
            "input_digest": input_digest,
            "aggregation_rule": metric_rules["aggregation"],
            "cohort_digests": [value["cohort_digest"] for value in cohorts],
            "source_digests": sorted(all_sources),
        },
    }
    _canonical(result)
    return result
