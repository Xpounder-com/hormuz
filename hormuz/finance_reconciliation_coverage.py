"""Offline finance coverage preview over pinned, content-free evidence.

This module consumes an already authorized as-of cost selection and immutable
gateway finance-attempt events. It neither authenticates a provider account nor
links an attempt to one. Amounts therefore remain separate known subtotals;
variance, bypass, allocation, and invoice finality are unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from typing import Mapping
import re

from .finance_attempts import validate_finance_attempt_event
from .finance_collection import (
    MAX_WINDOW_DAYS, PROFILE_SPECS, FinanceCollectionError, _parse_time, _time_text,
)
from .finance_collection_repository import AsOfCollectionView, SelectedCollectionSnapshot
from .finance_values import FinanceValueError, currency_code, decimal_text, exact_context


MAX_PREVIEW_ROWS = 10_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_COVERAGE_FIELDS = frozenset({
    "bucket_start_at", "bucket_end_at", "coverage_state", "observation_count",
    "snapshot_id", "commit_sequence",
})
_COST_FIELDS = frozenset({
    "bucket_start_at", "bucket_end_at", "snapshot_id", "observation_digest",
    "canonical_amount", "currency", "cost_basis", "provider_final",
    "invoice_final", "free_text_classification",
})


class FinanceCoveragePreviewError(ValueError):
    """Fixed error code; never echo selected rows or attempt data."""

    def __init__(self) -> None:
        super().__init__("finance_coverage_preview_invalid")


def _invalid() -> None:
    raise FinanceCoveragePreviewError()


def _id(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _utc(value: object) -> datetime:
    if not isinstance(value, str):
        _invalid()
    try:
        parsed = _parse_time(value)
    except (FinanceCollectionError, TypeError, ValueError):
        _invalid()
    if _time_text(parsed) != value:
        _invalid()
    return parsed


@dataclass(frozen=True)
class ProviderCostCoverage:
    known_subtotal: str | None
    currency: str
    numeric_selection_state: str
    observed_bucket_count: int
    empty_bucket_count: int
    missing_bucket_count: int
    observation_count: int
    negative_row_count: int
    unclassified_negative_row_count: int
    selected_snapshots: tuple[SelectedCollectionSnapshot, ...]
    observation_keys: tuple[tuple[str, str], ...]
    cost_basis: str = "provider_reported_aggregate"
    provider_final: bool = False
    invoice_final: bool = False


@dataclass(frozen=True)
class GatewayEstimateCoverage:
    known_subtotal: str | None
    currency: str
    supplied_attempt_pricing: str
    attempt_count: int
    priced_attempt_count: int
    unpriced_attempt_count: int
    currency_mismatch_attempt_count: int
    failed_attempt_count: int
    rate_limited_attempt_count: int
    unknown_outcome_attempt_count: int
    attempt_evidence_ids: tuple[str, ...]
    rate_card_identities: tuple[tuple[str, int, str], ...]
    cost_basis: str = "configured_rate_card_estimate"
    account_binding_state: str = "unavailable"


@dataclass(frozen=True)
class FinanceCoveragePreview:
    organization_id: str
    provider: str
    binding_id: str
    binding_version: int
    collection_profile: str
    period_start_at: str
    period_end_at: str
    as_of_commit_sequence: int
    provider_cost: ProviderCostCoverage
    gateway_estimate: GatewayEstimateCoverage
    signed_variance: None = None
    variance_state: str = "account_and_period_not_comparable"
    bypass_state: str = "unknown"
    attribution_state: str = "not_evaluated"
    evidence_basis: str = "offline_unverified_coverage_preview"


def build_finance_coverage_preview(
    *,
    provider_view: AsOfCollectionView,
    gateway_attempt_events: tuple[Mapping[str, object], ...],
    start_at: str,
    end_at: str,
    currency: str,
    account_binding_state: str = "unavailable",
) -> FinanceCoveragePreview:
    """Summarize selected evidence without manufacturing an account match.

    The caller must authorize and select the tenant's data before calling.
    Provider cost is a selected aggregate, and gateway cost is an original
    configured estimate. A shared tenant, provider, and time window still do
    not prove common account scope or provider accounting-period assignment.
    """

    if type(provider_view) is not AsOfCollectionView or type(gateway_attempt_events) is not tuple:
        _invalid()
    if account_binding_state not in {"unavailable", "matched"}:
        _invalid()
    if not isinstance(provider_view.collection_profile, str):
        _invalid()
    spec = PROFILE_SPECS.get(provider_view.collection_profile)
    if spec is None or spec.source_kind != "cost":
        _invalid()
    try:
        code = currency_code(currency)
    except FinanceValueError:
        _invalid()
    if (
        code != currency
        or not _id(provider_view.organization_id)
        or not _id(provider_view.binding_id)
        or type(provider_view.binding_version) is not int
        or not 1 <= provider_view.binding_version <= 2_147_483_647
        or type(provider_view.as_of_commit_sequence) is not int
        or not 0 <= provider_view.as_of_commit_sequence <= 9_223_372_036_854_775_807
        or type(provider_view.selected_snapshots) is not tuple
        or type(provider_view.coverage) is not tuple
        or type(provider_view.observations) is not tuple
        or len(provider_view.selected_snapshots) > MAX_WINDOW_DAYS
        or len(provider_view.coverage) > MAX_WINDOW_DAYS
        or len(provider_view.observations) > MAX_PREVIEW_ROWS
        or len(gateway_attempt_events) > MAX_PREVIEW_ROWS
    ):
        _invalid()
    start, end = _utc(start_at), _utc(end_at)
    day = timedelta(days=1)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    if (
        not timedelta(0) < end - start <= timedelta(days=MAX_WINDOW_DAYS)
        or (start - epoch) % day != timedelta(0)
        or (end - start) % day != timedelta(0)
    ):
        _invalid()
    expected_buckets = tuple(
        (_time_text(start + index * day), _time_text(start + (index + 1) * day))
        for index in range((end - start).days)
    )
    provider_cost = _provider_coverage(provider_view, expected_buckets, code)
    gateway_estimate = _gateway_coverage(
        gateway_attempt_events, provider_view.organization_id, spec.provider,
        start, end, code, account_binding_state,
    )
    return FinanceCoveragePreview(
        organization_id=provider_view.organization_id,
        provider=spec.provider,
        binding_id=provider_view.binding_id,
        binding_version=provider_view.binding_version,
        collection_profile=provider_view.collection_profile,
        period_start_at=start_at,
        period_end_at=end_at,
        as_of_commit_sequence=provider_view.as_of_commit_sequence,
        provider_cost=provider_cost,
        gateway_estimate=gateway_estimate,
    )


def _provider_coverage(
    view: AsOfCollectionView,
    expected_buckets: tuple[tuple[str, str], ...],
    currency: str,
) -> ProviderCostCoverage:
    snapshots: dict[str, SelectedCollectionSnapshot] = {}
    for snapshot in view.selected_snapshots:
        if (
            type(snapshot) is not SelectedCollectionSnapshot
            or not _id(snapshot.snapshot_id)
            or not _digest(snapshot.content_digest)
            or type(snapshot.commit_sequence) is not int
            or not 1 <= snapshot.commit_sequence <= view.as_of_commit_sequence
            or snapshot.snapshot_id in snapshots
        ):
            _invalid()
        snapshots[snapshot.snapshot_id] = snapshot
    coverage: dict[tuple[str, str], Mapping[str, object]] = {}
    expected = set(expected_buckets)
    for row in view.coverage:
        if type(row) is not dict or set(row) != _COVERAGE_FIELDS:
            _invalid()
        if (
            type(row["bucket_start_at"]) is not str
            or type(row["bucket_end_at"]) is not str
            or type(row["coverage_state"]) is not str
            or type(row["snapshot_id"]) is not str
        ):
            _invalid()
        key = (row["bucket_start_at"], row["bucket_end_at"])
        if (
            key not in expected
            or key in coverage
            or row["coverage_state"] not in {"observed", "no_observation"}
            or type(row["observation_count"]) is not int
            or not 0 <= row["observation_count"] <= MAX_PREVIEW_ROWS
            or (row["observation_count"] == 0) != (row["coverage_state"] == "no_observation")
            or row["snapshot_id"] not in snapshots
            or type(row["commit_sequence"]) is not int
            or row["commit_sequence"] != snapshots[row["snapshot_id"]].commit_sequence
        ):
            _invalid()
        coverage[key] = row
    if set(snapshots) != {row["snapshot_id"] for row in coverage.values()}:
        _invalid()
    counts = dict.fromkeys(coverage, 0)
    keys: set[tuple[str, str]] = set()
    digests: set[str] = set()
    negative = 0
    negative_unknown = 0
    try:
        with exact_context():
            total = Decimal(0)
            for row in view.observations:
                if type(row) is not dict or not _COST_FIELDS <= set(row):
                    _invalid()
                if (
                    type(row["bucket_start_at"]) is not str
                    or type(row["bucket_end_at"]) is not str
                    or type(row["snapshot_id"]) is not str
                    or type(row["free_text_classification"]) is not str
                ):
                    _invalid()
                bucket = (row["bucket_start_at"], row["bucket_end_at"])
                selected = coverage.get(bucket)
                if (
                    selected is None
                    or selected["coverage_state"] != "observed"
                    or row["snapshot_id"] != selected["snapshot_id"]
                    or not _digest(row["observation_digest"])
                    or row["currency"] != currency
                    or row["cost_basis"] != "provider_reported_aggregate"
                    or row["provider_final"] is not False
                    or row["invoice_final"] is not False
                    or row["free_text_classification"] not in {
                        "unclassified", "input_tokens", "output_tokens",
                        "cache_read_tokens", "cache_write_tokens",
                    }
                ):
                    _invalid()
                observation_key = (row["snapshot_id"], row["observation_digest"])
                if observation_key in keys or row["observation_digest"] in digests:
                    _invalid()
                keys.add(observation_key)
                digests.add(row["observation_digest"])
                amount = decimal_text(row["canonical_amount"])
                if amount != row["canonical_amount"]:
                    _invalid()
                parsed = Decimal(amount)
                total += parsed
                if parsed < 0:
                    negative += 1
                    if row["free_text_classification"] == "unclassified":
                        negative_unknown += 1
                counts[bucket] += 1
            if any(counts[bucket] != row["observation_count"] for bucket, row in coverage.items()):
                _invalid()
            known_subtotal = decimal_text(total) if view.observations else None
    except (FinanceValueError, DecimalException, TypeError, ValueError):
        _invalid()
    return ProviderCostCoverage(
        known_subtotal=known_subtotal,
        currency=currency,
        numeric_selection_state=(
            "all_selected_buckets_observed"
            if len(coverage) == len(expected_buckets)
            and all(row["coverage_state"] == "observed" for row in coverage.values())
            else "empty_or_missing_buckets"
        ),
        observed_bucket_count=sum(row["coverage_state"] == "observed" for row in coverage.values()),
        empty_bucket_count=sum(row["coverage_state"] == "no_observation" for row in coverage.values()),
        missing_bucket_count=len(expected_buckets) - len(coverage),
        observation_count=len(view.observations),
        negative_row_count=negative,
        unclassified_negative_row_count=negative_unknown,
        selected_snapshots=view.selected_snapshots,
        observation_keys=tuple(sorted(keys)),
    )


def _gateway_coverage(
    events: tuple[Mapping[str, object], ...],
    organization_id: str,
    provider: str,
    start: datetime,
    end: datetime,
    currency: str,
    account_binding_state: str,
) -> GatewayEstimateCoverage:
    expected_profile = {
        "openai": "openai.responses.usage.v1",
        "anthropic": "anthropic.messages.usage.v1",
    }[provider]
    request_ids: set[str] = set()
    evidence_ids: set[str] = set()
    terminal_ids: set[str] = set()
    usage_ids: set[str] = set()
    rate_identities: set[tuple[str, int, str]] = set()
    rate_versions: dict[tuple[str, int], tuple[str, str]] = {}
    priced = unpriced = different_currency = failed = rate_limited = unknown = 0
    try:
        with exact_context():
            total = Decimal(0)
            for event in events:
                validate_finance_attempt_event(event)
                timestamp = datetime.fromisoformat(event["occurred_at"])
                if (
                    event["organization_id"] != organization_id
                    or event["provider_schema_id"] != expected_profile
                    or not start <= timestamp < end
                    or event["request_attempt_id"] in request_ids
                    or event["evidence_event_id"] in evidence_ids
                    or event["terminal_attempt_event_id"] in terminal_ids
                    or (
                        event["usage_event_id"] is not None
                        and event["usage_event_id"] in usage_ids
                    )
                ):
                    _invalid()
                request_ids.add(event["request_attempt_id"])
                evidence_ids.add(event["evidence_event_id"])
                terminal_ids.add(event["terminal_attempt_event_id"])
                if event["usage_event_id"] is not None:
                    usage_ids.add(event["usage_event_id"])
                rate_identity = (
                    event["configured_rate_card_id"],
                    event["configured_rate_card_version"],
                    event["configured_rate_card_digest"],
                )
                rate_currency = event["configured_estimate_currency"]
                rate_version = rate_identity[:2]
                rate_value = (rate_identity[2], rate_currency)
                if rate_version in rate_versions and rate_versions[rate_version] != rate_value:
                    _invalid()
                rate_versions[rate_version] = rate_value
                rate_identities.add(rate_identity)
                state = event["terminal_state"]
                failed += state == "failed"
                rate_limited += state == "rate_limited"
                unknown += state == "outcome_unknown"
                if event["configured_estimate_availability"] == "unavailable":
                    unpriced += 1
                if rate_currency != currency:
                    different_currency += 1
                elif event["configured_estimate_availability"] == "available":
                    priced += 1
                    total += Decimal(event["configured_estimate_amount"])
            subtotal = decimal_text(total) if priced else None
    except (FinanceValueError, DecimalException, TypeError, ValueError):
        _invalid()
    return GatewayEstimateCoverage(
        known_subtotal=subtotal,
        currency=currency,
        supplied_attempt_pricing=(
            "no_attempts" if not events else
            "all_supplied_attempts_priced" if priced == len(events) else
            "incomplete"
        ),
        attempt_count=len(events),
        priced_attempt_count=priced,
        unpriced_attempt_count=unpriced,
        currency_mismatch_attempt_count=different_currency,
        failed_attempt_count=failed,
        rate_limited_attempt_count=rate_limited,
        unknown_outcome_attempt_count=unknown,
        attempt_evidence_ids=tuple(sorted(evidence_ids)),
        rate_card_identities=tuple(sorted(rate_identities)),
        account_binding_state=account_binding_state,
    )
