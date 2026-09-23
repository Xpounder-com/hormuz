"""Offline, source-only evidence qualification for a future model scorecard.

The caller must first authorize the tenant and resolve immutable attribution,
cost, outcome, association, and guardrail evidence. This module does not do
those joins, validate source authority, persist a snapshot, or publish a model
comparison. In particular, a qualified cohort is not a causal result.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import re


MAX_STRATA = 100
MAX_WORK_ITEM_REFERENCES = 10_000
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")

# The excluded quantity is reported but is never treated as successful coverage.
# These are reference-input dimensions; they are not the frozen scorecard wire.
COVERAGE_DIMENSIONS = frozenset({
    "eligible_governed_attempts", "eligible_governed_spend", "pricing",
    "connector", "association", "linked_outcome", "excluded",
})
DECISION_COVERAGE_DIMENSIONS = COVERAGE_DIMENSIONS - {"excluded"}
GUARDRAIL_DIMENSIONS = frozenset({
    "quality", "reversion_or_defect", "reliability", "privacy", "budget",
    "coverage_and_sample_eligibility", "freshness", "comparability",
})
_COST_BASES = frozenset({
    "provider_final", "configured_rate_card_estimate", "allocated_estimate",
    "provider_aggregate", "credit_or_discount", "not_available",
})
_ITEM_COST_BASES = frozenset({
    "provider_final", "configured_rate_card_estimate", "allocated_estimate",
})


class ScorecardEvidenceError(ValueError):
    """A fixed, content-free rejection of malformed reference input."""

    def __init__(self) -> None:
        self.code = "scorecard_evidence_invalid"
        super().__init__(self.code)


def _identifier(value: object) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _version(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


def _decimal(value: object) -> Decimal:
    if type(value) is not str or _DECIMAL.fullmatch(value) is None:
        raise ScorecardEvidenceError()
    return Decimal(value)


def _fraction(value: object) -> Fraction:
    number = _decimal(value)
    if number > 1:
        raise ScorecardEvidenceError()
    return Fraction(number)


def _time(value: object) -> datetime:
    if type(value) is not str or _TIME.fullmatch(value) is None:
        raise ScorecardEvidenceError()
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ScorecardEvidenceError() from None


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    version: int
    digest: str

    def __post_init__(self) -> None:
        if (
            not _identifier(self.rule_id)
            or not _version(self.version)
            or type(self.digest) is not str
            or _DIGEST.fullmatch(self.digest) is None
        ):
            raise ScorecardEvidenceError()


@dataclass(frozen=True)
class VersionRef:
    object_id: str
    version: int

    def __post_init__(self) -> None:
        if not _identifier(self.object_id) or not _version(self.version):
            raise ScorecardEvidenceError()


@dataclass(frozen=True)
class CoverageEvidence:
    dimension: str
    numerator: str | None
    denominator: str | None

    def __post_init__(self) -> None:
        if type(self.dimension) is not str or self.dimension not in COVERAGE_DIMENSIONS:
            raise ScorecardEvidenceError()
        if (self.numerator is None) != (self.denominator is None):
            raise ScorecardEvidenceError()
        if self.numerator is not None:
            numerator, denominator = _decimal(self.numerator), _decimal(self.denominator)
            if numerator > denominator:
                raise ScorecardEvidenceError()

    @property
    def ratio(self) -> Fraction | None:
        if self.denominator is None or Decimal(self.denominator) == 0:
            return None
        return Fraction(Decimal(self.numerator)) / Fraction(Decimal(self.denominator))


@dataclass(frozen=True)
class GuardrailEvidence:
    dimension: str
    state: str
    rule: RuleRef | None

    def __post_init__(self) -> None:
        if (
            type(self.dimension) is not str or self.dimension not in GUARDRAIL_DIMENSIONS
            or type(self.state) is not str
            or self.state not in {"pass", "fail", "inconclusive", "not_applicable"}
            or (self.rule is not None and type(self.rule) is not RuleRef)
        ):
            raise ScorecardEvidenceError()


@dataclass(frozen=True)
class StratumEvidence:
    stratum_id: str
    work_item_ids: tuple[str, ...]
    guardrails: tuple[GuardrailEvidence, ...]

    def __post_init__(self) -> None:
        if (
            not _identifier(self.stratum_id)
            or type(self.work_item_ids) is not tuple
            or len(self.work_item_ids) > MAX_WORK_ITEM_REFERENCES
            or any(not _identifier(item) for item in self.work_item_ids)
            or type(self.guardrails) is not tuple
            or any(type(item) is not GuardrailEvidence for item in self.guardrails)
            or len({item.dimension for item in self.guardrails}) != len(self.guardrails)
        ):
            raise ScorecardEvidenceError()


@dataclass(frozen=True)
class EvidencePolicy:
    rule: RuleRef | None
    declared_at: str | None
    minimum_coverage: str | None
    minimum_sample: int | None
    maximum_staleness_seconds: int | None
    required_strata: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.declared_at is not None:
            _time(self.declared_at)
        if (
            (self.rule is not None and type(self.rule) is not RuleRef)
            or (self.minimum_coverage is not None and not 0 < _fraction(self.minimum_coverage) <= 1)
            or (self.minimum_sample is not None and (
                type(self.minimum_sample) is not int
                or not 1 <= self.minimum_sample <= MAX_WORK_ITEM_REFERENCES
            ))
            or (self.maximum_staleness_seconds is not None and (
                type(self.maximum_staleness_seconds) is not int
                or not 1 <= self.maximum_staleness_seconds <= 31_536_000
            ))
            or type(self.required_strata) is not tuple
            or len(self.required_strata) > MAX_STRATA
            or any(not _identifier(item) for item in self.required_strata)
            or len(set(self.required_strata)) != len(self.required_strata)
        ):
            raise ScorecardEvidenceError()


@dataclass(frozen=True)
class ScorecardContext:
    organization_id: str
    work_scope: VersionRef
    window_start_at: str
    window_end_at: str
    evaluated_at: str

    def __post_init__(self) -> None:
        if (
            not _identifier(self.organization_id)
            or type(self.work_scope) is not VersionRef
            or not _time(self.window_start_at) < _time(self.window_end_at)
        ):
            raise ScorecardEvidenceError()
        _time(self.evaluated_at)


@dataclass(frozen=True)
class CohortEvidence:
    cohort_id: str
    organization_id: str
    work_scope: VersionRef
    actual_provider_id: str | None
    actual_model_id: str | None
    actual_model_version: str | None
    client_id: str
    policy: VersionRef | None
    rate_card: VersionRef | None
    cost_basis: str
    currency: str
    association_rule: RuleRef | None
    last_observed_at: str | None
    coverage: tuple[CoverageEvidence, ...]
    strata: tuple[StratumEvidence, ...]

    def __post_init__(self) -> None:
        if self.last_observed_at is not None:
            _time(self.last_observed_at)
        if (
            not _identifier(self.cohort_id)
            or not _identifier(self.organization_id)
            or type(self.work_scope) is not VersionRef
            or any(value is not None and not _identifier(value) for value in
                   (self.actual_provider_id, self.actual_model_id, self.actual_model_version))
            or not _identifier(self.client_id)
            or (self.policy is not None and type(self.policy) is not VersionRef)
            or (self.rate_card is not None and type(self.rate_card) is not VersionRef)
            or type(self.cost_basis) is not str or self.cost_basis not in _COST_BASES
            or type(self.currency) is not str or _CURRENCY.fullmatch(self.currency) is None
            or (self.association_rule is not None and type(self.association_rule) is not RuleRef)
            or type(self.coverage) is not tuple
            or len(self.coverage) > len(COVERAGE_DIMENSIONS)
            or any(type(item) is not CoverageEvidence for item in self.coverage)
            or len({item.dimension for item in self.coverage}) != len(self.coverage)
            or type(self.strata) is not tuple
            or len(self.strata) > MAX_STRATA
            or any(type(item) is not StratumEvidence for item in self.strata)
            or len({item.stratum_id for item in self.strata}) != len(self.strata)
            or sum(len(item.work_item_ids) for item in self.strata) > MAX_WORK_ITEM_REFERENCES
        ):
            raise ScorecardEvidenceError()


@dataclass(frozen=True)
class EvidenceQualification:
    status: str
    reason_codes: tuple[str, ...]
    distinct_work_item_count: int
    coverage_ratios: tuple[tuple[str, Fraction | None], ...]
    cost_basis: str


def qualify_evidence(
    context: ScorecardContext, policy: EvidencePolicy, cohort: CohortEvidence,
) -> EvidenceQualification:
    """Qualify caller-selected evidence, conservatively and without aggregation.

    Repeated attempt references to one work item count as one sample. Every
    declared stratum must pass every mandatory guardrail; a large stratum
    cannot mask a failed or missing guardrail in a smaller one.
    """
    if (
        type(context) is not ScorecardContext
        or type(policy) is not EvidencePolicy
        or type(cohort) is not CohortEvidence
    ):
        raise ScorecardEvidenceError()
    if cohort.organization_id != context.organization_id or cohort.work_scope != context.work_scope:
        raise ScorecardEvidenceError()

    reasons: set[str] = set()
    start, end, evaluated = (_time(context.window_start_at), _time(context.window_end_at), _time(context.evaluated_at))
    if evaluated < end:
        reasons.add("window_not_closed")
    if policy.rule is None or policy.declared_at is None:
        reasons.add("missing_predeclared_rule")
    elif _time(policy.declared_at) >= start:
        reasons.add("rule_not_predeclared")
    if policy.minimum_coverage is None or policy.minimum_sample is None or policy.maximum_staleness_seconds is None:
        reasons.add("missing_threshold")
    if not policy.required_strata:
        reasons.add("missing_strata_rule")

    if None in (cohort.actual_provider_id, cohort.actual_model_id, cohort.actual_model_version):
        reasons.add("unknown_actual_model")
    if cohort.policy is None:
        reasons.add("unknown_policy")
    if cohort.cost_basis not in _ITEM_COST_BASES:
        reasons.add("ineligible_cost_basis")
    if cohort.cost_basis != "provider_final" and cohort.rate_card is None:
        reasons.add("unknown_rate_card")
    if cohort.association_rule is None:
        reasons.add("unknown_association_rule")
    if cohort.last_observed_at is None:
        reasons.add("unknown_freshness")
    else:
        observed = _time(cohort.last_observed_at)
        if observed > evaluated:
            raise ScorecardEvidenceError()
        if observed < end or (policy.maximum_staleness_seconds is not None and
                                (evaluated - observed).total_seconds() > policy.maximum_staleness_seconds):
            reasons.add("stale_evidence")

    coverages = {item.dimension: item for item in cohort.coverage}
    ratios = tuple((name, coverages[name].ratio if name in coverages else None)
                   for name in sorted(COVERAGE_DIMENSIONS))
    for name, ratio in ratios:
        if ratio is None:
            reasons.add("missing_coverage")
        elif name in DECISION_COVERAGE_DIMENSIONS and policy.minimum_coverage is not None:
            if ratio < _fraction(policy.minimum_coverage):
                reasons.add("coverage_below_minimum")

    strata = {item.stratum_id: item for item in cohort.strata}
    if set(strata) != set(policy.required_strata):
        reasons.add("strata_mismatch")
    work_items: set[str] = set()
    for stratum in cohort.strata:
        unique_here = set(stratum.work_item_ids)
        if not unique_here:
            reasons.add("empty_stratum")
        if work_items.intersection(unique_here):
            raise ScorecardEvidenceError()
        work_items.update(unique_here)
        guards = {guard.dimension: guard for guard in stratum.guardrails}
        if set(guards) != GUARDRAIL_DIMENSIONS:
            reasons.add("missing_guardrail")
        for guard in guards.values():
            if guard.rule is None:
                reasons.add("missing_guardrail_rule")
            if guard.state == "fail":
                reasons.add("guardrail_failed")
            elif guard.state != "pass":
                reasons.add("guardrail_inconclusive")
    if policy.minimum_sample is not None and len(work_items) < policy.minimum_sample:
        reasons.add("sample_below_minimum")

    return EvidenceQualification(
        status="eligible" if not reasons else "inconclusive",
        reason_codes=tuple(sorted(reasons)),
        distinct_work_item_count=len(work_items),
        coverage_ratios=ratios,
        cost_basis=cohort.cost_basis,
    )
