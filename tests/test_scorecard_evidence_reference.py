from __future__ import annotations

from dataclasses import replace
from decimal import Inexact, Rounded, localcontext
from fractions import Fraction
import unittest

from hormuz.scorecard_evidence_reference import (
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


RULE = RuleRef("metric-222", 2, "a" * 64)
GUARD_RULE = RuleRef("guard-222", 3, "b" * 64)


def context() -> ScorecardContext:
    return ScorecardContext(
        "org-a", VersionRef("use-case-a", 3),
        "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z", "2026-09-03T00:00:00Z",
    )


def policy() -> EvidencePolicy:
    return EvidencePolicy(
        RULE, "2026-08-31T23:59:59Z", "0.8", 3, 172800,
        ("stratum-a", "stratum-b"),
    )


def coverage(**overrides: tuple[str | None, str | None]) -> tuple[CoverageEvidence, ...]:
    return tuple(CoverageEvidence(name, *(overrides.get(name, ("9", "10"))))
                 for name in sorted(COVERAGE_DIMENSIONS))


def guards(**overrides: tuple[str, RuleRef | None]) -> tuple[GuardrailEvidence, ...]:
    return tuple(GuardrailEvidence(name, *(overrides.get(name, ("pass", GUARD_RULE))))
                 for name in sorted(GUARDRAIL_DIMENSIONS))


def cohort() -> CohortEvidence:
    return CohortEvidence(
        cohort_id="cohort-a", organization_id="org-a", work_scope=VersionRef("use-case-a", 3),
        actual_provider_id="openai", actual_model_id="model-a", actual_model_version="2026-08-20",
        client_id="application-a", policy=VersionRef("policy-a", 4),
        rate_card=VersionRef("rate-card-a", 2), cost_basis="configured_rate_card_estimate",
        currency="USD", association_rule=RuleRef("association-a", 1, "c" * 64),
        last_observed_at="2026-09-02T12:00:00Z", coverage=coverage(),
        strata=(StratumEvidence("stratum-a", ("work-a", "work-a", "work-b"), guards()),
                StratumEvidence("stratum-b", ("work-c",), guards())),
    )


class ScorecardEvidenceTests(unittest.TestCase):
    def test_exact_reference_vector_counts_work_items_not_retry_attempts(self):
        result = qualify_evidence(context(), policy(), cohort())
        self.assertEqual((result.status, result.reason_codes, result.distinct_work_item_count),
                         ("eligible", (), 3))
        self.assertEqual(dict(result.coverage_ratios), {name: Fraction(9, 10) for name in COVERAGE_DIMENSIONS})
        self.assertEqual(result.cost_basis, "configured_rate_card_estimate")

    def test_decimal_coverage_is_exact_independent_of_process_precision(self):
        chosen = replace(cohort(), coverage=coverage(eligible_governed_spend=("0.81", "0.9")))
        with localcontext() as decimal_context:
            decimal_context.prec = 2
            decimal_context.traps[Inexact] = True
            decimal_context.traps[Rounded] = True
            result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(dict(result.coverage_ratios)["eligible_governed_spend"], Fraction(9, 10))
        self.assertEqual(result.status, "eligible")

    def test_missing_and_zero_denominators_are_inconclusive_not_zero(self):
        for missing in ((None, None), ("0", "0")):
            with self.subTest(missing=missing):
                chosen = replace(cohort(), coverage=coverage(pricing=missing))
                result = qualify_evidence(context(), policy(), chosen)
                self.assertEqual(result.status, "inconclusive")
                self.assertIn("missing_coverage", result.reason_codes)
                self.assertIsNone(dict(result.coverage_ratios)["pricing"])

    def test_any_required_coverage_dimension_below_predeclared_minimum_blocks(self):
        chosen = replace(cohort(), coverage=coverage(linked_outcome=("7", "10")))
        result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(result.status, "inconclusive")
        self.assertEqual(result.reason_codes, ("coverage_below_minimum",))
        self.assertEqual(dict(result.coverage_ratios)["linked_outcome"], Fraction(7, 10))

    def test_excluded_quantity_is_visible_but_not_treated_as_success_coverage(self):
        chosen = replace(cohort(), coverage=coverage(excluded=("1", "10")))
        result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(result.status, "eligible")
        self.assertEqual(dict(result.coverage_ratios)["excluded"], Fraction(1, 10))

    def test_missing_dimension_is_inconclusive_even_when_all_supplied_ratios_pass(self):
        chosen = replace(cohort(), coverage=tuple(item for item in coverage() if item.dimension != "connector"))
        result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(result.status, "inconclusive")
        self.assertIn("missing_coverage", result.reason_codes)

    def test_missing_or_late_rule_or_threshold_cannot_qualify(self):
        for chosen_policy, reason in (
            (replace(policy(), rule=None), "missing_predeclared_rule"),
            (replace(policy(), declared_at=None), "missing_predeclared_rule"),
            (replace(policy(), declared_at=context().window_start_at), "rule_not_predeclared"),
            (replace(policy(), minimum_coverage=None), "missing_threshold"),
            (replace(policy(), minimum_sample=None), "missing_threshold"),
            (replace(policy(), maximum_staleness_seconds=None), "missing_threshold"),
        ):
            with self.subTest(reason=reason, chosen_policy=chosen_policy):
                result = qualify_evidence(context(), chosen_policy, cohort())
                self.assertEqual(result.status, "inconclusive")
                self.assertIn(reason, result.reason_codes)

    def test_repeated_attempts_cannot_satisfy_a_distinct_work_item_sample(self):
        chosen = replace(cohort(), strata=(
            StratumEvidence("stratum-a", ("work-a",) * 100, guards()),
            StratumEvidence("stratum-b", ("work-b",) * 100, guards()),
        ))
        result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(result.distinct_work_item_count, 2)
        self.assertIn("sample_below_minimum", result.reason_codes)

    def test_a_failed_small_stratum_blocks_a_large_stratum_pass(self):
        chosen = replace(cohort(), strata=(
            StratumEvidence("stratum-a", tuple(f"work-{number}" for number in range(500)), guards()),
            StratumEvidence("stratum-b", ("work-small",), guards(quality=("fail", GUARD_RULE))),
        ))
        result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(result.distinct_work_item_count, 501)
        self.assertEqual(result.status, "inconclusive")
        self.assertIn("guardrail_failed", result.reason_codes)

    def test_unknown_or_missing_guardrails_do_not_pass(self):
        for small_stratum, reason in (
            (StratumEvidence("stratum-b", ("work-c",), guards(privacy=("inconclusive", GUARD_RULE))), "guardrail_inconclusive"),
            (StratumEvidence("stratum-b", ("work-c",), guards(budget=("not_applicable", GUARD_RULE))), "guardrail_inconclusive"),
            (StratumEvidence("stratum-b", ("work-c",), guards(quality=("pass", None))), "missing_guardrail_rule"),
            (StratumEvidence("stratum-b", ("work-c",), tuple(item for item in guards() if item.dimension != "reliability")), "missing_guardrail"),
        ):
            with self.subTest(reason=reason):
                chosen = replace(cohort(), strata=(cohort().strata[0], small_stratum))
                result = qualify_evidence(context(), policy(), chosen)
                self.assertEqual(result.status, "inconclusive")
                self.assertIn(reason, result.reason_codes)

    def test_missing_declared_stratum_never_disappears_from_guardrails(self):
        chosen = replace(cohort(), strata=(cohort().strata[0],))
        result = qualify_evidence(context(), policy(), chosen)
        self.assertEqual(result.status, "inconclusive")
        self.assertIn("strata_mismatch", result.reason_codes)

    def test_unknown_actual_version_or_provenance_is_inconclusive(self):
        for changed, reason in (
            ({"actual_model_version": None}, "unknown_actual_model"),
            ({"policy": None}, "unknown_policy"),
            ({"rate_card": None}, "unknown_rate_card"),
            ({"association_rule": None}, "unknown_association_rule"),
            ({"cost_basis": "provider_aggregate"}, "ineligible_cost_basis"),
            ({"cost_basis": "credit_or_discount"}, "ineligible_cost_basis"),
            ({"cost_basis": "not_available"}, "ineligible_cost_basis"),
        ):
            with self.subTest(changed=changed):
                result = qualify_evidence(context(), policy(), replace(cohort(), **changed))
                self.assertEqual(result.status, "inconclusive")
                self.assertIn(reason, result.reason_codes)

    def test_provider_final_keeps_its_basis_and_does_not_require_an_estimate_card(self):
        result = qualify_evidence(context(), policy(), replace(cohort(), cost_basis="provider_final", rate_card=None))
        self.assertEqual((result.status, result.cost_basis), ("eligible", "provider_final"))

    def test_unclosed_window_stale_or_missing_observation_is_inconclusive(self):
        not_closed = replace(context(), evaluated_at="2026-09-01T12:00:00Z")
        self.assertIn("window_not_closed", qualify_evidence(not_closed, policy(),
                      replace(cohort(), last_observed_at="2026-09-01T11:00:00Z")).reason_codes)
        self.assertIn("stale_evidence", qualify_evidence(context(), policy(),
                      replace(cohort(), last_observed_at="2026-08-31T00:00:00Z")).reason_codes)
        self.assertIn("unknown_freshness", qualify_evidence(context(), policy(),
                      replace(cohort(), last_observed_at=None)).reason_codes)

    def test_cross_tenant_cross_scope_or_future_observation_rejected(self):
        for chosen in (
            replace(cohort(), organization_id="org-b"),
            replace(cohort(), work_scope=VersionRef("use-case-a", 4)),
            replace(cohort(), last_observed_at="2026-09-04T00:00:00Z"),
        ):
            with self.subTest(chosen=chosen):
                with self.assertRaisesRegex(ScorecardEvidenceError, "^scorecard_evidence_invalid$"):
                    qualify_evidence(context(), policy(), chosen)

    def test_a_work_item_cannot_be_double_counted_across_strata(self):
        chosen = replace(cohort(), strata=(
            cohort().strata[0], StratumEvidence("stratum-b", ("work-b", "work-c"), guards()),
        ))
        with self.assertRaises(ScorecardEvidenceError):
            qualify_evidence(context(), policy(), chosen)

    def test_malformed_financial_and_count_inputs_are_rejected(self):
        for invalid in ("-1", "01", "1e0", "NaN", "Infinity", "1.0000000000", 0.9, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ScorecardEvidenceError):
                    CoverageEvidence("pricing", invalid, "1")
        for bad in (0, True, 10_001):
            with self.subTest(bad=bad):
                with self.assertRaises(ScorecardEvidenceError):
                    replace(policy(), minimum_sample=bad)
        with self.assertRaises(ScorecardEvidenceError):
            CoverageEvidence("pricing", "2", "1")
        with self.assertRaises(ScorecardEvidenceError):
            replace(policy(), minimum_coverage="0")

    def test_duplicate_guardrail_or_coverage_dimension_is_rejected(self):
        with self.assertRaises(ScorecardEvidenceError):
            replace(cohort(), coverage=coverage() + (CoverageEvidence("pricing", "9", "10"),))
        with self.assertRaises(ScorecardEvidenceError):
            StratumEvidence("stratum-a", (), guards() + (guards()[0],))


if __name__ == "__main__":
    unittest.main()
