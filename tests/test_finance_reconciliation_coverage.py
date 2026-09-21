"""Synthetic coverage evidence; no provider access or account assertion."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import unittest

from hormuz.finance_attempts import (
    ConfiguredRateCardBinding,
    ConfiguredRouteEstimate,
    absent_native_observation,
    build_finance_attempt_event,
    unavailable_estimate,
    unknown_native_observation,
    validate_finance_attempt_event,
)
from hormuz.finance_collection_repository import AsOfCollectionView, SelectedCollectionSnapshot
from hormuz.finance_reconciliation_coverage import (
    FinanceCoveragePreviewError,
    build_finance_coverage_preview,
)
from hormuz.usage import ResponseUsageParser


START = "2026-09-01T00:00:00Z"
MIDDLE = "2026-09-02T00:00:00Z"
END = "2026-09-03T00:00:00Z"
PROFILE = "openai.organization-costs.v1"


def snapshot(suffix: str = "1", sequence: int = 1) -> SelectedCollectionSnapshot:
    return SelectedCollectionSnapshot(f"snapshot-{suffix}", suffix * 64, sequence)


def coverage(start: str, end: str, selected: SelectedCollectionSnapshot, count: int) -> dict:
    return {
        "bucket_start_at": start,
        "bucket_end_at": end,
        "coverage_state": "observed" if count else "no_observation",
        "observation_count": count,
        "snapshot_id": selected.snapshot_id,
        "commit_sequence": selected.commit_sequence,
    }


def cost(
    amount: str, selected: SelectedCollectionSnapshot, *, suffix: str,
    start: str = START, end: str = MIDDLE, classification: str = "unclassified",
) -> dict:
    return {
        "bucket_start_at": start,
        "bucket_end_at": end,
        "snapshot_id": selected.snapshot_id,
        "observation_digest": suffix * 64,
        "canonical_amount": amount,
        "currency": "USD",
        "cost_basis": "provider_reported_aggregate",
        "provider_final": False,
        "invoice_final": False,
        "free_text_classification": classification,
    }


def view(
    *, selected: SelectedCollectionSnapshot | None = None,
    buckets: tuple[dict, ...] | None = None,
    observations: tuple[dict, ...] | None = None,
) -> AsOfCollectionView:
    chosen = selected or snapshot()
    return AsOfCollectionView(
        organization_id="tenant-a", binding_id="source-a", binding_version=2,
        collection_profile=PROFILE, as_of_commit_sequence=chosen.commit_sequence,
        selected_snapshots=(chosen,),
        coverage=buckets if buckets is not None else (
            coverage(START, MIDDLE, chosen, 2),
            coverage(MIDDLE, END, chosen, 0),
        ),
        observations=observations if observations is not None else (
            cost("1.5", chosen, suffix="a"),
            cost("-0.25", chosen, suffix="b"),
        ),
    )


def native_observation():
    parser = ResponseUsageParser("openai", is_event_stream=False)
    parser.feed(json.dumps({
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 2, "cache_write_tokens": 1},
            "output_tokens": 4,
            "total_tokens": 14,
        },
    }).encode())
    return parser.finish_with_finance().finance


def event(
    suffix: str, *, amount: str | None = "0.75", version: int = 1,
    currency: str = "USD", state: str = "succeeded",
    organization_id: str = "tenant-a", protocol: str = "openai",
    occurred_at: str = "2026-09-01T12:00:00+00:00",
) -> dict:
    binding = ConfiguredRateCardBinding("route-a", version, str(version) * 64, currency)
    if state == "outcome_unknown":
        observation = unknown_native_observation(protocol, None, "provider_transport_ambiguous")
        estimate = unavailable_estimate(binding, "attempt_outcome_unknown")
    elif amount is None:
        observation = absent_native_observation(protocol)
        estimate = unavailable_estimate(binding, "missing_native_usage")
    else:
        observation = native_observation()
        estimate = ConfiguredRouteEstimate(
            availability="available", amount=amount,
            amount_microusd=int(Decimal(amount) * 1_000_000),
            currency=currency, cost_basis="configured_rate_card_estimate",
            reason_code="estimated", rate_card_id=binding.rate_card_id,
            rate_card_version=binding.rate_card_version,
            rate_card_digest=binding.rate_card_digest,
        )
    return build_finance_attempt_event(
        protocol=protocol, organization_id=organization_id,
        request_attempt_id=f"attempt-{suffix}",
        terminal_attempt_event_id=f"{suffix * 8}-{suffix * 4}-4{suffix * 3}-8{suffix * 3}-{suffix * 12}",
        usage_event_id=None if state == "outcome_unknown" else f"{suffix * 8}-{suffix * 4}-4{suffix * 3}-9{suffix * 3}-{suffix * 12}",
        terminal_state=state, occurred_at=occurred_at,
        observation=observation, estimate=estimate, binding=binding,
        evidence_event_id=f"{suffix * 8}-{suffix * 4}-4{suffix * 3}-a{suffix * 3}-{suffix * 12}",
    )


def preview(provider_view=None, events=None, **overrides):
    return build_finance_coverage_preview(
        provider_view=view() if provider_view is None else provider_view,
        gateway_attempt_events=(event("1"), event("2", amount=None, state="rate_limited"))
        if events is None else events,
        start_at=overrides.get("start_at", START),
        end_at=overrides.get("end_at", END),
        currency=overrides.get("currency", "USD"),
    )


class FinanceReconciliationCoverageTests(unittest.TestCase):
    def test_distinct_aggregate_and_estimate_subtotals_never_become_variance_or_invoice(self):
        report = preview()
        self.assertEqual(report.provider_cost.known_subtotal, "1.25")
        self.assertEqual(report.gateway_estimate.known_subtotal, "0.75")
        self.assertEqual(
            (report.provider_cost.cost_basis, report.gateway_estimate.cost_basis),
            ("provider_reported_aggregate", "configured_rate_card_estimate"),
        )
        self.assertEqual((report.provider_cost.provider_final, report.provider_cost.invoice_final), (False, False))
        self.assertIsNone(report.signed_variance)
        self.assertEqual(report.variance_state, "account_and_period_not_comparable")
        self.assertEqual((report.bypass_state, report.attribution_state), ("unknown", "not_evaluated"))
        self.assertEqual((report.provider_cost.observed_bucket_count, report.provider_cost.empty_bucket_count,
                          report.provider_cost.missing_bucket_count), (1, 1, 0))
        self.assertEqual(report.provider_cost.numeric_selection_state, "empty_or_missing_buckets")
        self.assertEqual((report.provider_cost.negative_row_count,
                          report.provider_cost.unclassified_negative_row_count), (1, 1))
        self.assertEqual((report.gateway_estimate.attempt_count,
                          report.gateway_estimate.priced_attempt_count,
                          report.gateway_estimate.unpriced_attempt_count,
                          report.gateway_estimate.rate_limited_attempt_count), (2, 1, 1, 1))
        self.assertEqual(report.gateway_estimate.account_binding_state, "unavailable")
        self.assertEqual(report.gateway_estimate.supplied_attempt_pricing, "incomplete")

    def test_empty_and_missing_buckets_are_not_numeric_zero(self):
        selected = snapshot()
        empty = view(buckets=(coverage(START, MIDDLE, selected, 0),), observations=())
        report = preview(empty, ())
        self.assertIsNone(report.provider_cost.known_subtotal)
        self.assertIsNone(report.gateway_estimate.known_subtotal)
        self.assertEqual((report.provider_cost.empty_bucket_count,
                          report.provider_cost.missing_bucket_count), (1, 1))
        self.assertEqual(report.gateway_estimate.supplied_attempt_pricing, "no_attempts")
        self.assertIsNone(report.signed_variance)

    def test_complete_selected_bucket_grid_still_does_not_prove_account_match(self):
        selected = snapshot()
        provider_view = view(
            buckets=(coverage(START, MIDDLE, selected, 1),
                     coverage(MIDDLE, END, selected, 1)),
            observations=(cost("0", selected, suffix="a"),
                          cost("2", selected, suffix="b", start=MIDDLE, end=END)),
        )
        report = preview(provider_view, (event("1"),))
        self.assertEqual(report.provider_cost.known_subtotal, "2")
        self.assertEqual(report.provider_cost.numeric_selection_state, "all_selected_buckets_observed")
        self.assertEqual(report.gateway_estimate.supplied_attempt_pricing,
                         "all_supplied_attempts_priced")
        self.assertIsNone(report.signed_variance)

    def test_pinned_snapshot_and_original_price_versions_replay_without_repricing(self):
        first = preview(events=(event("1", version=1),))
        newer = snapshot("2", 2)
        second = preview(
            view(selected=newer, buckets=(coverage(START, MIDDLE, newer, 1),),
                 observations=(cost("0.5", newer, suffix="c"),)),
            (event("1", version=1), event("2", version=2, amount="0.25")),
        )
        self.assertEqual((first.as_of_commit_sequence, first.provider_cost.known_subtotal), (1, "1.25"))
        self.assertEqual((second.as_of_commit_sequence, second.provider_cost.known_subtotal), (2, "0.5"))
        self.assertEqual(first.gateway_estimate.rate_card_identities, (("route-a", 1, "1" * 64),))
        self.assertEqual(second.gateway_estimate.rate_card_identities,
                         (("route-a", 1, "1" * 64), ("route-a", 2, "2" * 64)))
        self.assertEqual(first.gateway_estimate.known_subtotal, "0.75")

    def test_failed_and_unknown_outcome_attempts_remain_in_denominator(self):
        report = preview(events=(
            event("1", amount=None, state="failed"),
            event("2", amount=None, state="outcome_unknown"),
        ))
        self.assertEqual(report.gateway_estimate.attempt_count, 2)
        self.assertEqual(report.gateway_estimate.unpriced_attempt_count, 2)
        self.assertEqual((report.gateway_estimate.failed_attempt_count,
                          report.gateway_estimate.unknown_outcome_attempt_count), (1, 1))
        self.assertIsNone(report.gateway_estimate.known_subtotal)

    def test_different_currency_is_excluded_from_subtotal_without_conversion(self):
        report = preview(events=(
            event("1"), event("2", currency="EUR", amount="2", version=2),
            event("3", currency="EUR", amount=None, version=3),
        ))
        self.assertEqual(report.gateway_estimate.known_subtotal, "0.75")
        self.assertEqual(report.gateway_estimate.currency_mismatch_attempt_count, 2)
        self.assertEqual(report.gateway_estimate.priced_attempt_count, 1)
        self.assertEqual(report.gateway_estimate.unpriced_attempt_count, 1)

    def test_same_immutable_rate_card_identity_cannot_change_currency(self):
        with self.assertRaises(FinanceCoveragePreviewError):
            preview(events=(event("1", currency="USD"), event("2", currency="EUR")))

    def test_same_rate_card_version_with_conflicting_digest_fails_closed(self):
        first = event("1")
        second = {**event("2"), "configured_rate_card_digest": "f" * 64}
        validate_finance_attempt_event(second)
        with self.assertRaises(FinanceCoveragePreviewError):
            preview(events=(first, second))

    def test_reused_terminal_or_usage_event_identity_fails_closed(self):
        first = event("1")
        for field in ("terminal_attempt_event_id", "usage_event_id"):
            second = {**event("2"), field: first[field]}
            validate_finance_attempt_event(second)
            with self.subTest(field=field), self.assertRaises(FinanceCoveragePreviewError):
                preview(events=(first, second))

    def test_cross_tenant_provider_and_window_events_fail_closed(self):
        cases = (
            (event("1", organization_id="tenant-b"),),
            (event("1", amount=None, protocol="anthropic"),),
            (event("1", occurred_at="2026-09-03T00:00:00+00:00"),),
            (event("1"), event("1")),
        )
        for events in cases:
            with self.subTest(events=len(events)), self.assertRaises(FinanceCoveragePreviewError):
                preview(events=events)

    def test_stale_duplicate_and_mismatched_provider_rows_fail_closed(self):
        selected = snapshot()
        base = view()
        cases = (
            replace(base, observations=base.observations + (base.observations[0],)),
            replace(base, observations=(
                {**base.observations[0], "snapshot_id": "stale-snapshot"}, base.observations[1],
            )),
            replace(base, observations=(
                {**base.observations[0], "currency": "EUR"}, base.observations[1],
            )),
            replace(base, observations=(
                {**base.observations[0], "provider_final": True}, base.observations[1],
            )),
            replace(base, coverage=(coverage(START, MIDDLE, selected, 1),
                                    coverage(MIDDLE, END, selected, 0))),
        )
        for candidate in cases:
            with self.subTest(candidate=candidate.observations[0]), self.assertRaises(FinanceCoveragePreviewError):
                preview(provider_view=candidate)

    def test_invalid_time_profile_and_boolean_sequence_are_rejected(self):
        base = view()
        cases = (
            (replace(base, collection_profile="openai.organization-usage-completions.v1"), START, END),
            (replace(base, as_of_commit_sequence=True), START, END),
            (base, "2026-09-01T12:00:00Z", END),
            (base, START, "2026-10-03T00:00:00Z"),
        )
        for candidate, start, end in cases:
            with self.subTest(start=start, end=end), self.assertRaises(FinanceCoveragePreviewError):
                preview(provider_view=candidate, start_at=start, end_at=end)


if __name__ == "__main__":
    unittest.main()
