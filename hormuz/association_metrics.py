"""Deterministic, metadata-only reference vectors for run/outcome association.

The caller must authorize the tenant before selecting these immutable facts.
This module rejects open-ended input, chooses connector-authoritative object
state, deduplicates attempts and work objects, and never treats an aggregate
provider charge as attempt-grained cost.  It is a source for later scorecards;
it does not publish a scorecard or claim causality.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import re
from typing import Mapping


MAX_FACTS = 10_000
MAX_DECIMAL = Decimal("999999999999999999.999999999")
ITEM_COST_BASES = (
    "provider_final",
    "configured_rate_card_estimate",
    "allocated_estimate",
)
COST_BASES = ITEM_COST_BASES + (
    "provider_aggregate",
    "credit_or_discount",
    "not_available",
)
ASSOCIATION_STATES = ("associated", "unmatched", "ambiguous", "excluded")
REFERENCE_RULE_ID = "association-reference-metrics-v1"
REFERENCE_RULE_VERSION = 1
REFERENCE_RULE_DIGEST = hashlib.sha256(json.dumps({
    "rule_id": REFERENCE_RULE_ID,
    "version": REFERENCE_RULE_VERSION,
    "attempt_grain": "unique_request_attempt",
    "outcome_grain": "connector_external_object_object_type",
    "current_state": "highest_connector_authoritative_lineage_tip",
    "accepted": "associated_current_event_type_and_quality_accepted",
    "rounding": "decimal_scale_9_half_even",
    "aggregate_cost_allocation": False,
}, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_EXTERNAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:=+-]{0,255}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z\Z"
)
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?\Z")

_CONTEXT_FIELDS = frozenset({
    "organization_id", "work_scope_id", "work_scope_version", "start_at",
    "end_at", "evaluated_at", "rule_id", "rule_version", "rule_digest",
})
_ATTEMPT_FIELDS = frozenset({
    "organization_id", "request_attempt_id", "occurred_at", "attribution_state",
    "work_scope_id", "work_scope_version",
})
_COST_FIELDS = frozenset({
    "organization_id", "cost_event_id", "request_attempt_id", "basis", "amount",
    "currency", "provenance_digest", "occurred_at",
})
_OUTCOME_FIELDS = frozenset({
    "organization_id", "connector_id", "source_event_id", "external_object_id",
    "object_type", "source_revision", "ordering_domain", "revision_order",
    "ordering_state", "event_type", "quality_state", "duration_ms", "state",
    "scope_state", "work_scope_id", "work_scope_version",
    "supersedes_source_event_id", "retained", "event_at", "observed_at",
})
_ASSOCIATION_FIELDS = frozenset({
    "organization_id", "association_event_id", "connector_id", "source_event_id",
    "request_attempt_id", "state", "candidate_count", "rule_id", "rule_version",
    "rule_digest", "window_start_at", "window_end_at", "sequence",
})
_DELIVERY_FIELDS = frozenset({
    "organization_id", "connector_id", "delivery_id", "state", "occurred_at",
})


class AssociationMetricError(ValueError):
    """A fixed, content-free refusal for malformed or inconsistent facts."""

    def __init__(self) -> None:
        self.code = "association_metric_evidence_invalid"
        super().__init__(self.code)


def _invalid() -> None:
    raise AssociationMetricError()


def _closed(value: object, fields: frozenset[str]) -> Mapping[str, object]:
    if type(value) is not dict or set(value) != fields:
        _invalid()
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _invalid()
    return value


def _optional_identifier(value: object) -> str | None:
    return None if value is None else _identifier(value)


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        _invalid()
    return value


def _time(value: object) -> datetime:
    if type(value) is not str or _TIME.fullmatch(value) is None:
        _invalid()
    try:
        instant = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _invalid()
    return instant


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _invalid()
    return value


def _external_identifier(value: object) -> str:
    if type(value) is not str or _EXTERNAL_ID.fullmatch(value) is None:
        _invalid()
    return value


def _optional_external_identifier(value: object) -> str | None:
    return None if value is None else _external_identifier(value)


def _amount(value: object, *, signed: bool) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        _invalid()
    try:
        result = Decimal(value)
    except InvalidOperation:
        _invalid()
    if abs(result) > MAX_DECIMAL or (not signed and result < 0):
        _invalid()
    return result


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or abs(value) > MAX_DECIMAL:
        _invalid()
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        text = "0"
    if _DECIMAL.fullmatch(text) is None:
        _invalid()
    return text


def _divide(numerator: Decimal, denominator: Decimal) -> str:
    if denominator <= 0:
        _invalid()
    with localcontext() as context:
        context.prec = 48
        value = (numerator / denominator).quantize(
            Decimal("0.000000001"), rounding=ROUND_HALF_EVEN
        )
    return _decimal_text(value)


def _coverage(numerator: int | None, denominator: int | None) -> dict[str, object]:
    if numerator is None or denominator is None:
        return {
            "numerator": None,
            "denominator": None,
            "ratio": None,
            "reason_code": "missing_evidence",
        }
    if not 0 <= numerator <= denominator:
        _invalid()
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": None if denominator == 0 else _divide(Decimal(numerator), Decimal(denominator)),
        "reason_code": "missing_evidence" if denominator == 0 else "eligible",
    }


def _measure(
    *,
    numerator: Decimal | None,
    denominator: Decimal | None,
    unit: str,
    supported: bool,
) -> dict[str, object]:
    eligible = (
        supported
        and numerator is not None
        and denominator is not None
        and denominator > 0
    )
    return {
        "rule": {
            "rule_id": REFERENCE_RULE_ID,
            "version": REFERENCE_RULE_VERSION,
            "digest": REFERENCE_RULE_DIGEST,
        },
        "value": _divide(numerator, denominator) if eligible else None,
        "numerator": _decimal_text(numerator) if numerator is not None else None,
        "denominator": _decimal_text(denominator) if denominator is not None else None,
        "unit": unit,
        "status": "eligible" if eligible else "inconclusive",
        "reason_code": "eligible" if eligible else "missing_evidence",
    }


def build_association_metric_vector(
    *,
    context: Mapping[str, object],
    attempts: tuple[Mapping[str, object], ...],
    costs: tuple[Mapping[str, object], ...],
    outcomes: tuple[Mapping[str, object], ...],
    associations: tuple[Mapping[str, object], ...],
    deliveries: tuple[Mapping[str, object], ...],
    complete_connector_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    """Build one exact reference vector from already-authorized metadata."""

    if any(type(items) is not tuple or len(items) > MAX_FACTS for items in (
        attempts, costs, outcomes, associations, deliveries,
    )) or type(complete_connector_ids) is not tuple:
        _invalid()
    selected = _closed(context, _CONTEXT_FIELDS)
    organization = _identifier(selected["organization_id"])
    scope_id = _identifier(selected["work_scope_id"])
    scope_version = _version(selected["work_scope_version"])
    start, end, evaluated = (
        _time(selected["start_at"]),
        _time(selected["end_at"]),
        _time(selected["evaluated_at"]),
    )
    if not start < end <= evaluated:
        _invalid()
    rule_id = _identifier(selected["rule_id"])
    rule_version = _version(selected["rule_version"])
    rule_digest = _digest(selected["rule_digest"])
    complete = {_identifier(value) for value in complete_connector_ids}
    if len(complete) != len(complete_connector_ids):
        _invalid()

    all_attempts, eligible_attempts = _attempts(
        attempts, organization, scope_id, scope_version, start, end,
    )
    cost_rows = _costs(costs, organization, all_attempts, eligible_attempts, start, end)
    outcome_rows, cohort = _outcomes(
        outcomes, organization, scope_id, scope_version, start, end, evaluated,
    )
    association_rows = _associations(
        associations, organization, outcome_rows, all_attempts, start, end,
        rule_id, rule_version, rule_digest,
    )
    delivery_rows = _deliveries(deliveries, organization, start, end)

    effective: dict[tuple[str, str, str], dict[str, object]] = {}
    state_counts = dict.fromkeys(ASSOCIATION_STATES, 0)
    candidate_count = 0
    selected_connectors: set[str] = set()
    for object_key, rows in cohort.items():
        selected_connectors.add(object_key[0])
        value = _effective_object(
            rows, outcome_rows, association_rows, eligible_attempts, start, end,
        )
        effective[object_key] = value
        state_counts[value["state"]] += 1
        candidate_count += value["candidate_count"]

    associated_objects = {
        key: value for key, value in effective.items() if value["state"] == "associated"
    }
    accepted_objects = {
        key: value for key, value in associated_objects.items()
        if value["outcome"]["event_type"] == "accepted"
        and value["outcome"]["quality_state"] == "accepted"
    }
    priced_attempts = {
        row["request_attempt_id"] for row in cost_rows
        if row["basis"] in ITEM_COST_BASES and row["request_attempt_id"] in eligible_attempts
    }
    delivery_rows = [row for row in delivery_rows if row["connector_id"] in selected_connectors]
    successful_deliveries = sum(row["state"] != "failed" for row in delivery_rows)
    history_complete = bool(selected_connectors) and selected_connectors <= complete

    cost_components, cost_metrics = _cost_vectors(
        cost_rows, eligible_attempts, len(accepted_objects), history_complete,
    )
    measures = _outcome_measures(
        cohort, association_rows, eligible_attempts, accepted_objects,
        history_complete, start, end,
    )
    measures["cost_per_accepted_work_item"] = cost_metrics

    eligible_outcome_events = sum(
        start <= row["event_at_value"] < end
        and row["scope_state"] == "matched"
        and row["state"] == "observed"
        and row["ordering_state"] == "authoritative"
        and row["event_type"] != "unsupported"
        and not row["retained"]
        for row in outcome_rows.values()
    )
    scoped_outcome_events = sum(
        start <= row["event_at_value"] < end and row["scope_state"] == "matched"
        for row in outcome_rows.values()
    )
    denominators = {
        "eligible_attempts": len(eligible_attempts),
        "priced_attempts": len(priced_attempts),
        "eligible_outcome_events": eligible_outcome_events,
        "unique_work_objects": len(cohort),
        "eligible_association_candidates": candidate_count,
        **state_counts,
        "connector_deliveries": len(delivery_rows),
        "connector_successful_deliveries": successful_deliveries,
    }
    result = {
        "schema_id": "hormuz.association-metric-reference",
        "schema_version": 1,
        "organization_id": organization,
        "work_scope": {"work_scope_id": scope_id, "version": scope_version},
        "window": {"start_at": selected["start_at"], "end_at": selected["end_at"]},
        "evaluated_at": selected["evaluated_at"],
        "association_rule": {
            "rule_id": rule_id,
            "version": rule_version,
            "digest": rule_digest,
        },
        "coverage": {
            "attributed_runs": _coverage(len(eligible_attempts), len(all_attempts)),
            "priced_runs": _coverage(len(priced_attempts), len(eligible_attempts)),
            "linked_work_objects": _coverage(len(associated_objects), len(cohort)),
            "eligible_outcomes": _coverage(eligible_outcome_events, scoped_outcome_events),
            "connector_health": _coverage(successful_deliveries, len(delivery_rows)),
            "excluded_or_ambiguous": _coverage(
                state_counts["excluded"] + state_counts["ambiguous"], len(cohort)
            ),
        },
        "denominators": denominators,
        "cost_components": cost_components,
        "measures": measures,
        "evidence_level": "associated",
        "causal_claim": False,
    }
    _content_boundary(result)
    return result


def _attempts(items, organization, scope_id, scope_version, start, end):
    rows: dict[str, dict[str, object]] = {}
    eligible: set[str] = set()
    for item in items:
        row = dict(_closed(item, _ATTEMPT_FIELDS))
        if row["organization_id"] != organization:
            _invalid()
        attempt = _identifier(row["request_attempt_id"])
        occurred = _time(row["occurred_at"])
        if attempt in rows or row["attribution_state"] not in {"active", "unmatched", "excluded"}:
            _invalid()
        row["occurred_at_value"] = occurred
        if row["work_scope_id"] is not None:
            _identifier(row["work_scope_id"])
            _version(row["work_scope_version"])
        elif row["work_scope_version"] is not None:
            _invalid()
        rows[attempt] = row
        if (
            start <= occurred < end
            and row["attribution_state"] == "active"
            and row["work_scope_id"] == scope_id
            and row["work_scope_version"] == scope_version
        ):
            eligible.add(attempt)
    return rows, eligible


def _costs(items, organization, attempts, eligible_attempts, start, end):
    rows: list[dict[str, object]] = []
    identities: set[str] = set()
    per_attempt_basis: set[tuple[str, str]] = set()
    for item in items:
        row = dict(_closed(item, _COST_FIELDS))
        if row["organization_id"] != organization:
            _invalid()
        identity = _identifier(row["cost_event_id"])
        basis = row["basis"]
        attempt = _optional_identifier(row["request_attempt_id"])
        occurred = _time(row["occurred_at"])
        if identity in identities or basis not in COST_BASES:
            _invalid()
        identities.add(identity)
        _digest(row["provenance_digest"])
        amount, currency = row["amount"], row["currency"]
        if basis == "not_available":
            if amount is not None or currency is not None:
                _invalid()
            parsed = None
        else:
            parsed = _amount(amount, signed=basis == "credit_or_discount")
            if type(currency) is not str or re.fullmatch(r"[A-Z]{3}", currency) is None:
                _invalid()
            if basis == "credit_or_discount" and parsed > 0:
                _invalid()
        if basis in ITEM_COST_BASES:
            if attempt not in attempts or attempt is None:
                _invalid()
            key = (attempt, basis)
            if key in per_attempt_basis:
                _invalid()
            per_attempt_basis.add(key)
        elif basis in {"provider_aggregate", "credit_or_discount"} and attempt is not None:
            _invalid()
        elif attempt is not None and attempt not in attempts:
            _invalid()
        row["amount_value"] = parsed
        row["occurred_at_value"] = occurred
        if start <= occurred < end and (attempt is None or attempt in eligible_attempts):
            rows.append(row)
    return rows


def _outcomes(items, organization, scope_id, scope_version, start, end, evaluated):
    rows: dict[tuple[str, str], dict[str, object]] = {}
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = defaultdict(list)
    for item in items:
        row = dict(_closed(item, _OUTCOME_FIELDS))
        if row["organization_id"] != organization:
            _invalid()
        connector = _identifier(row["connector_id"])
        source = _external_identifier(row["source_event_id"])
        external = _external_identifier(row["external_object_id"])
        if row["object_type"] not in {"issue", "pull_request"}:
            _invalid()
        _optional_external_identifier(row["source_revision"])
        ordering_domain = row["ordering_domain"]
        revision_order = row["revision_order"]
        if ordering_domain is None:
            if revision_order is not None:
                _invalid()
        else:
            _identifier(ordering_domain)
            if type(revision_order) is not int or not 0 <= revision_order <= 9_223_372_036_854_775_807:
                _invalid()
        if row["ordering_state"] not in {"authoritative", "late", "uncertain", "superseded"}:
            _invalid()
        if row["ordering_state"] == "authoritative" and revision_order is None:
            _invalid()
        if row["event_type"] not in {
            "created", "started", "completed", "reopened", "accepted", "reverted",
            "defect_reported", "canceled", "deleted", "unsupported",
        } or row["quality_state"] not in {
            "accepted", "rejected", "reverted", "defect", "unknown", "not_applicable",
        } or row["state"] not in {"observed", "superseded", "tombstoned"}:
            _invalid()
        if row["scope_state"] not in {"matched", "unmatched", "excluded"}:
            _invalid()
        if row["work_scope_id"] is not None:
            _identifier(row["work_scope_id"])
            _version(row["work_scope_version"])
        elif row["work_scope_version"] is not None:
            _invalid()
        prior = _optional_external_identifier(row["supersedes_source_event_id"])
        duration = row["duration_ms"]
        if duration is not None:
            row["duration_value"] = _amount(duration, signed=False)
        else:
            row["duration_value"] = None
        if type(row["retained"]) is not bool:
            _invalid()
        event_at, observed_at = _time(row["event_at"]), _time(row["observed_at"])
        if observed_at > evaluated:
            _invalid()
        key = (connector, source)
        if key in rows:
            _invalid()
        row["event_at_value"], row["observed_at_value"] = event_at, observed_at
        rows[key] = row
        if event_at < evaluated:
            groups[(connector, external, row["object_type"])].append(row)
        if prior == source:
            _invalid()
    for row in rows.values():
        prior = row["supersedes_source_event_id"]
        if prior is not None and (row["connector_id"], prior) not in rows:
            _invalid()
    cohort = {
        key: value for key, value in groups.items()
        if any(
            start <= row["event_at_value"] < end
            and row["scope_state"] == "matched"
            and row["work_scope_id"] == scope_id
            and row["work_scope_version"] == scope_version
            for row in value
        )
    }
    return rows, cohort


def _associations(items, organization, outcomes, attempts, start, end, rule_id, rule_version, rule_digest):
    rows: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    identities: set[str] = set()
    sequences: set[int] = set()
    for item in items:
        row = dict(_closed(item, _ASSOCIATION_FIELDS))
        if row["organization_id"] != organization:
            _invalid()
        identity = _identifier(row["association_event_id"])
        connector, source = _identifier(row["connector_id"]), _external_identifier(row["source_event_id"])
        attempt = _optional_identifier(row["request_attempt_id"])
        if (
            identity in identities
            or (connector, source) not in outcomes
            or row["state"] not in ASSOCIATION_STATES
            or type(row["candidate_count"]) is not int
            or not 0 <= row["candidate_count"] <= MAX_FACTS
            or type(row["sequence"]) is not int
            or not 1 <= row["sequence"] <= 9_223_372_036_854_775_807
            or row["sequence"] in sequences
        ):
            _invalid()
        identities.add(identity)
        sequences.add(row["sequence"])
        if attempt is not None and attempt not in attempts:
            _invalid()
        if (
            row["rule_id"] != rule_id
            or row["rule_version"] != rule_version
            or row["rule_digest"] != rule_digest
            or _time(row["window_start_at"]) != start
            or _time(row["window_end_at"]) != end
        ):
            _invalid()
        if row["state"] == "associated":
            if row["candidate_count"] != 1 or attempt is None:
                _invalid()
        elif attempt is not None:
            _invalid()
        rows[(connector, source)].append(row)
    for values in rows.values():
        values.sort(key=lambda row: row["sequence"])
    return rows


def _deliveries(items, organization, start, end):
    rows, identities = [], set()
    for item in items:
        row = dict(_closed(item, _DELIVERY_FIELDS))
        if row["organization_id"] != organization:
            _invalid()
        connector, delivery = _identifier(row["connector_id"]), _external_identifier(row["delivery_id"])
        occurred = _time(row["occurred_at"])
        key = (connector, delivery)
        if key in identities or row["state"] not in {"accepted", "unsupported", "failed"}:
            _invalid()
        identities.add(key)
        if start <= occurred < end:
            row["occurred_at_value"] = occurred
            rows.append(row)
    return rows


def _effective_object(rows, outcomes, associations, eligible_attempts, start, end):
    successors = {row["supersedes_source_event_id"] for row in rows if row["supersedes_source_event_id"] is not None}
    tips = [row for row in rows if row["source_event_id"] not in successors]
    conflicts = [
        row for row in tips
        if row["ordering_state"] == "uncertain"
        and row["state"] == "observed"
        and row["event_type"] != "unsupported"
        and not row["retained"]
    ]
    authoritative = [
        row for row in tips
        if row["ordering_state"] == "authoritative" and row["event_type"] != "unsupported"
    ]
    if not authoritative:
        authoritative = [
            row for row in rows
            if row["ordering_state"] == "authoritative"
            and row["event_type"] != "unsupported"
        ]
        if not conflicts or not authoritative:
            return {"state": "excluded", "candidate_count": 0, "outcome": max(rows, key=lambda row: row["observed_at_value"])}
    selected = max(
        authoritative,
        key=lambda row: (row["revision_order"], row["observed_at_value"], row["source_event_id"]),
    )
    invalid = (
        selected["state"] != "observed"
        or selected["retained"]
        or selected["scope_state"] != "matched"
        or not start <= selected["event_at_value"] < end
    )
    if invalid:
        return {"state": "excluded", "candidate_count": 0, "outcome": selected}
    history = associations.get((selected["connector_id"], selected["source_event_id"]), ())
    if not history:
        return {
            "state": "ambiguous" if conflicts else "unmatched",
            "candidate_count": 2 if conflicts else 0,
            "outcome": selected,
        }
    decision = history[-1]
    state, candidates = decision["state"], decision["candidate_count"]
    conflicts = [
        row for row in conflicts if row["source_event_id"] != selected["source_event_id"]
    ]
    if state == "associated" and conflicts:
        state, candidates = "ambiguous", max(2, candidates)
    if state == "associated" and decision["request_attempt_id"] not in eligible_attempts:
        _invalid()
    return {
        "state": state,
        "candidate_count": candidates,
        "outcome": selected,
        "request_attempt_id": decision["request_attempt_id"] if state == "associated" else None,
    }


def _cost_vectors(rows, eligible_attempts, accepted_count, history_complete):
    grouped: dict[tuple[str, str | None], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(row["basis"], row["currency"])].append(row)
    components: list[dict[str, object]] = []
    metrics: list[dict[str, object]] = []
    for basis in COST_BASES:
        keys = sorted((key for key in grouped if key[0] == basis), key=lambda key: key[1] or "")
        if not keys:
            keys = [(basis, None)]
        for key in keys:
            values = grouped.get(key, [])
            amount = None if basis == "not_available" or not values else sum(
                (row["amount_value"] for row in values), Decimal(0)
            )
            attempt_ids = {
                row["request_attempt_id"] for row in values if row["request_attempt_id"] is not None
            }
            components.append({
                "basis": basis,
                "currency": key[1],
                "amount": None if amount is None else _decimal_text(amount),
                "attempt_count": len(attempt_ids),
                "evidence_count": len(values),
            })
            if basis in ITEM_COST_BASES and key[1] is not None:
                complete = attempt_ids == eligible_attempts
                metric = _measure(
                    numerator=amount,
                    denominator=Decimal(accepted_count),
                    unit=f"{key[1]}_per_accepted_work_item",
                    supported=history_complete and complete,
                )
                metric["cost_basis"] = basis
                metrics.append(metric)
    return components, metrics


def _outcome_measures(cohort, associations, eligible_attempts, accepted_objects, complete, start, end):
    accepted_count = len(accepted_objects)
    durations = [value["outcome"]["duration_value"] for value in accepted_objects.values()]
    duration_supported = complete and accepted_count > 0 and all(value is not None for value in durations)
    seconds = Decimal(str((end - start).total_seconds()))
    associated_attempts_by_object: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for object_key, rows in cohort.items():
        sources = {(row["connector_id"], row["source_event_id"]) for row in rows}
        for source in sources:
            history = associations.get(source, ())
            if not history:
                continue
            decision = history[-1]
            if (
                decision["state"] == "associated"
                and decision["request_attempt_id"] in eligible_attempts
            ):
                associated_attempts_by_object[object_key].add(
                    decision["request_attempt_id"]
                )
    retries = sum(max(0, len(values) - 1) for values in associated_attempts_by_object.values())
    reopened = {
        key for key, rows in cohort.items() if any(row["event_type"] == "reopened" for row in rows)
    }
    reverted = {
        key for key, rows in cohort.items()
        if any(row["event_type"] == "reverted" or row["quality_state"] == "reverted" for row in rows)
    }
    defects = {
        key for key, rows in cohort.items()
        if any(row["event_type"] == "defect_reported" or row["quality_state"] == "defect" for row in rows)
    }
    rework = {
        *reopened,
        *reverted,
        *defects,
        *(key for key, values in associated_attempts_by_object.items() if len(values) > 1),
    }
    first_pass = sum(
        key not in rework and len(associated_attempts_by_object.get(key, ())) == 1
        for key in accepted_objects
    )
    return {
        "cycle_time": _measure(
            numerator=sum((value for value in durations if value is not None), Decimal(0)),
            denominator=Decimal(accepted_count),
            unit="milliseconds_per_accepted_work_item",
            supported=duration_supported,
        ),
        "throughput": _measure(
            numerator=Decimal(accepted_count) * Decimal(86400),
            denominator=seconds,
            unit="accepted_work_items_per_day",
            supported=complete,
        ),
        "first_pass_success": _measure(
            numerator=Decimal(first_pass),
            denominator=Decimal(accepted_count),
            unit="ratio",
            supported=complete,
        ),
        "retries": _measure(
            numerator=Decimal(retries), denominator=Decimal(1), unit="count", supported=complete,
        ),
        "rework": _measure(
            numerator=Decimal(len(rework)), denominator=Decimal(1), unit="work_item_count", supported=complete,
        ),
        "reversions": _measure(
            numerator=Decimal(len(reverted)), denominator=Decimal(1), unit="work_item_count", supported=complete,
        ),
        "defects": _measure(
            numerator=Decimal(len(defects)), denominator=Decimal(1), unit="work_item_count", supported=complete,
        ),
    }


def _content_boundary(value: Mapping[str, object]) -> None:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    forbidden = (
        '"title"', '"description"', '"body"', '"comment"', '"prompt"',
        '"response"', '"path"', '"actor_id"', '"actor_name"', '"credential"',
        '"employee"', '"raw_payload"',
    )
    if len(payload.encode("ascii")) > 1_048_576 or any(item in payload.lower() for item in forbidden):
        _invalid()
