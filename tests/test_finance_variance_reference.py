from __future__ import annotations

from dataclasses import replace
from decimal import Inexact, Rounded, localcontext
import unittest

from hormuz.finance_variance_reference import (
    BoundGatewayScopeClaim,
    ComparableAccountGrain,
    FinanceVarianceReferenceError,
    GatewayEstimateRow,
    ProviderCostRow,
    calculate_reference_variance,
)


def grain() -> ComparableAccountGrain:
    return ComparableAccountGrain(
        organization_id="tenant-a",
        provider="openai",
        provider_account_fingerprint="a" * 64,
        fingerprint_key_version=1,
        source_binding_id="source-a",
        source_binding_version=2,
        source_binding_digest="b" * 64,
        scope_kind="organization",
        scope_fingerprints=(),
        period_start_at="2026-09-01T00:00:00Z",
        period_end_at="2026-09-02T00:00:00Z",
        currency="USD",
        product="openai.costs",
        collection_profile="openai.costs.v1",
    )


def binding() -> BoundGatewayScopeClaim:
    return BoundGatewayScopeClaim(
        binding_id="registration-a", binding_version=3, binding_digest="c" * 64,
        source_binding_id="source-a", source_binding_version=2,
        source_binding_digest="b" * 64,
    )


def provider(amount: str, *, suffix: str = "d") -> ProviderCostRow:
    return ProviderCostRow("snapshot-a", suffix * 64, amount)


def estimate(amount: str | None, *, suffix: str = "1") -> GatewayEstimateRow:
    return GatewayEstimateRow("attempt-" + suffix, "e" * 64, amount)


def calculate(
    *,
    provider_grain: ComparableAccountGrain | None = None,
    gateway_grain: ComparableAccountGrain | None = None,
    gateway_binding: BoundGatewayScopeClaim | None = None,
    provider_coverage: str = "complete",
    provider_rows: tuple[ProviderCostRow, ...] = (provider("1.25"),),
    gateway_rows: tuple[GatewayEstimateRow, ...] = (estimate("1"),),
):
    return calculate_reference_variance(
        provider_grain=provider_grain or grain(),
        gateway_grain=gateway_grain or grain(),
        gateway_binding=binding() if gateway_binding is None else gateway_binding,
        provider_coverage=provider_coverage,
        provider_rows=provider_rows,
        gateway_rows=gateway_rows,
    )


class FinanceVarianceReferenceTests(unittest.TestCase):
    def test_positive_negative_and_zero_variance_are_exact_and_unreviewed(self):
        positive = calculate()
        self.assertEqual((positive.provider_total, positive.configured_estimate_known_subtotal), ("1.25", "1"))
        self.assertEqual((positive.signed_variance, positive.absolute_variance), ("0.25", "0.25"))
        self.assertEqual((positive.relative_variance.numerator, positive.relative_variance.denominator), ("0.25", "1"))
        self.assertEqual((positive.supplied_attempt_pricing, positive.review_status), ("complete", "not_evaluated"))
        self.assertEqual(positive.evidence_basis, "operator_attested_unverified_reference")
        self.assertEqual((positive.provider_cost_basis, positive.gateway_cost_basis),
                         ("provider_reported_aggregate", "configured_rate_card_estimate"))
        self.assertFalse(hasattr(positive, "invoice_final"))

        negative = calculate(provider_rows=(provider("0.75"),))
        self.assertEqual((negative.signed_variance, negative.absolute_variance), ("-0.25", "0.25"))
        self.assertEqual((negative.relative_variance.numerator, negative.relative_variance.denominator), ("-0.25", "1"))
        zero = calculate(provider_rows=(provider("1"),))
        self.assertEqual((zero.signed_variance, zero.absolute_variance), ("0", "0"))

    def test_zero_estimate_denominator_is_undefined_even_for_zero_zero(self):
        for provider_amount in ("0", "0.5"):
            with self.subTest(provider_amount=provider_amount):
                result = calculate(provider_rows=(provider(provider_amount),), gateway_rows=(estimate("0"),))
                self.assertIsNone(result.relative_variance)
                self.assertEqual(result.signed_variance, provider_amount)

    def test_incomplete_pricing_keeps_known_subtotal_but_never_passes(self):
        result = calculate(gateway_rows=(estimate("0.75"), estimate(None, suffix="2")))
        self.assertEqual(result.configured_estimate_known_subtotal, "0.75")
        self.assertEqual(result.signed_variance, "0.5")
        self.assertEqual(result.unpriced_attempt_count, 1)
        self.assertEqual((result.supplied_attempt_pricing, result.review_status), ("incomplete", "not_evaluated"))
        self.assertEqual(result.gateway_attempt_ids, ("attempt-1", "attempt-2"))
        self.assertEqual(result.gateway_price_identity_digests, ("e" * 64, "e" * 64))

    def test_signed_provider_adjustment_is_summed_once_without_classifying_it(self):
        result = calculate(
            provider_rows=(provider("10"), provider("-2", suffix="f")),
            gateway_rows=(estimate("9"),),
        )
        self.assertEqual((result.provider_total, result.signed_variance, result.absolute_variance), ("8", "-1", "1"))
        self.assertEqual(result.provider_observation_keys, (("snapshot-a", "d" * 64), ("snapshot-a", "f" * 64)))

    def test_scope_currency_period_and_product_guards_run_before_amount_parsing(self):
        original = grain()
        invalid_amount = (provider("not-a-decimal"),)
        mismatches = (
            replace(original, organization_id="tenant-b"),
            replace(original, provider_account_fingerprint="f" * 64),
            replace(original, fingerprint_key_version=2),
            replace(original, source_binding_digest="f" * 64),
            replace(original, period_end_at="2026-09-03T00:00:00Z"),
            replace(original, period_start_at="2026-09-01T12:00:00Z"),
            replace(original, currency="EUR"),
            replace(original, product="other.costs"),
            replace(original, collection_profile="other.costs.v1"),
            replace(original, scope_kind="projects", scope_fingerprints=("f" * 64,)),
        )
        for mismatched in mismatches:
            with self.subTest(field=mismatched), self.assertRaises(FinanceVarianceReferenceError) as caught:
                calculate(provider_grain=original, gateway_grain=mismatched, provider_rows=invalid_amount)
            self.assertEqual(caught.exception.code, "finance_grain_not_comparable")
            self.assertNotIn("not-a-decimal", repr(caught.exception))

    def test_unbound_or_unverified_source_claim_cannot_be_compared(self):
        for claim in (
            None,
            replace(binding(), source_binding_version=4),
            replace(binding(), source_binding_digest="f" * 64),
        ):
            with self.subTest(claim=claim), self.assertRaises(FinanceVarianceReferenceError) as caught:
                calculate_reference_variance(
                    provider_grain=grain(), gateway_grain=grain(), gateway_binding=claim,
                    provider_coverage="complete", provider_rows=(provider("1"),), gateway_rows=(estimate("1"),),
                )
            self.assertEqual(caught.exception.code, "finance_grain_not_comparable")

    def test_missing_partial_or_empty_provider_coverage_is_not_zero(self):
        for coverage in ("empty", "missing", "partial", "stale"):
            with self.subTest(coverage=coverage), self.assertRaises(FinanceVarianceReferenceError) as caught:
                calculate(provider_coverage=coverage, provider_rows=(provider("not-a-decimal"),))
            self.assertEqual(caught.exception.code, "finance_grain_not_comparable")
        for rows in ((), (provider("1"), provider("1"))):
            with self.assertRaises(FinanceVarianceReferenceError) as caught:
                calculate(provider_rows=rows)
            self.assertEqual(caught.exception.code, "finance_reference_invalid")

    def test_duplicate_attempts_invalid_estimates_and_unbounded_sums_fail_closed(self):
        cases = (
            {"gateway_rows": (estimate("1"), estimate("1"))},
            {"gateway_rows": (estimate("-1"),)},
            {"gateway_rows": (estimate("0.1"), estimate(0.1, suffix="2"))},
            {"gateway_rows": (estimate("01"),)},
            {"provider_rows": (provider("999999999999999999"), provider("1", suffix="f"))},
            {"gateway_rows": (estimate("1"),) * 10_001},
        )
        for arguments in cases:
            with self.subTest(case=next(iter(arguments))), self.assertRaises(FinanceVarianceReferenceError) as caught:
                calculate(**arguments)
            self.assertEqual(caught.exception.code, "finance_reference_invalid")

    def test_relative_variance_is_exact_ratio_without_ambient_rounding(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            result = calculate(provider_rows=(provider("4"),), gateway_rows=(estimate("3"),))
        self.assertEqual((result.relative_variance.numerator, result.relative_variance.denominator), ("1", "3"))
        tiny = calculate(
            provider_rows=(provider("0.000000000000000002"),),
            gateway_rows=(estimate("0.000000000000000001"),),
        )
        self.assertEqual(tiny.signed_variance, "0.000000000000000001")

    def test_grain_rejects_invalid_window_scope_and_boolean_versions(self):
        original = grain()
        for changed in (
            {"period_end_at": original.period_start_at},
            {"period_start_at": "2026-09-01T00:00:00+00:00"},
            {"scope_kind": "projects", "scope_fingerprints": ()},
            {"fingerprint_key_version": True},
            {"currency": "usd"},
            {"provider": []},
            {"scope_kind": []},
        ):
            with self.subTest(changed=changed), self.assertRaises(FinanceVarianceReferenceError) as caught:
                replace(original, **changed)
            self.assertEqual(caught.exception.code, "finance_reference_invalid")


if __name__ == "__main__":
    unittest.main()
