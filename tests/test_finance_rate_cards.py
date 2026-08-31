from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction
import random
import unittest

from hormuz.finance_values import FinanceValueError
from hormuz.finance_usage import normalize_provider_usage
from hormuz.finance_rate_cards import RateCard, estimate_usage, rate_card_from_mapping

if __package__:
    from ._finance_values_fixture import anthropic_usage, estimate_context, openai_usage, rate_card
else:
    from _finance_values_fixture import anthropic_usage, estimate_context, openai_usage, rate_card


class FinanceRateCardTests(unittest.TestCase):
    def test_two_providers_use_different_cache_rules_without_double_counting(self):
        for provider, source, amount in (("openai", openai_usage(), "0.00315"), ("anthropic", anthropic_usage(), "0.00323")):
            card = rate_card_from_mapping(rate_card(provider))
            estimate = estimate_usage(card, normalize_provider_usage(provider, source), **estimate_context(provider))
            self.assertEqual(estimate.amount, amount)
            self.assertEqual(estimate.currency, "USD")
            self.assertEqual(estimate.cost_basis, "configured_rate_card_estimate")
            self.assertEqual(estimate.rate_card_digest, card.content_digest)
            self.assertFalse(estimate.provider_final)

    def test_new_version_and_mutated_input_do_not_reprice_prior_snapshot(self):
        source = rate_card()
        first = rate_card_from_mapping(source)
        usage = normalize_provider_usage("openai", openai_usage())
        estimate = estimate_usage(first, usage, **estimate_context())
        source["version"] = 2
        source["rates"]["output"] = "10"
        second = rate_card_from_mapping(source)
        self.assertEqual(estimate.amount, "0.00315")
        self.assertEqual(estimate_usage(first, usage, **estimate_context()), estimate)
        self.assertEqual(estimate_usage(second, usage, **estimate_context()).amount, "0.00355")
        self.assertNotEqual(first.content_digest, second.content_digest)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            first.version = 2

    def test_rate_digest_is_canonical_and_pins_effective_identity_and_rules(self):
        first = rate_card_from_mapping(rate_card())
        self.assertEqual(first.content_digest, rate_card_from_mapping(dict(reversed(list(rate_card().items())))).content_digest)
        for field, replacement in (("organization_id", "beta"), ("actual_model", "synthetic-model-v2"),
                                   ("currency", "EUR"), ("version", 2), ("batch", True),
                                   ("effective_to", "2026-10-01T00:00:00Z")):
            with self.subTest(field=field):
                changed = {**rate_card(), field: replacement}
                self.assertNotEqual(first.content_digest, rate_card_from_mapping(changed).content_digest)

    def test_unknown_rate_or_native_partition_is_unavailable_not_zero(self):
        source = rate_card()
        source["rates"]["cache_write"] = None
        card = rate_card_from_mapping(source)
        result = estimate_usage(card, normalize_provider_usage("openai", openai_usage()), **estimate_context())
        self.assertIsNone(result.amount)
        self.assertEqual(result.cost_basis, "not_available")
        self.assertEqual(result.reason_code, "missing_rate")
        row = openai_usage()
        row.pop("input_cache_write_tokens")
        result = estimate_usage(rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", row), **estimate_context())
        self.assertIsNone(result.amount)
        self.assertEqual(result.reason_code, "missing_native_usage")

    def test_tenant_model_time_tier_and_batch_must_match_exactly(self):
        card, usage = rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", openai_usage())
        for change, reason in (({"organization_id": "beta"}, "tenant_mismatch"),
                               ({"actual_model": "different"}, "actual_model_mismatch"),
                               ({"actual_model": None}, "actual_model_unknown"),
                               ({"event_at": "2026-09-01T00:00:00Z"}, "outside_rate_interval"),
                               ({"event_at": "2026-07-31T23:59:59Z"}, "outside_rate_interval"),
                               ({"service_tier": "priority"}, "unsupported_tier"),
                               ({"batch": True}, "batch_mismatch"), ({"batch": None}, "batch_unknown")):
            with self.subTest(change=change):
                result = estimate_usage(card, usage, **{**estimate_context(), **change})
                self.assertIsNone(result.amount)
                self.assertEqual(result.reason_code, reason)

    def test_unavailable_estimate_has_null_amount_and_currency(self):
        result = estimate_usage(rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", openai_usage()),
                                **{**estimate_context(), "actual_model": None})
        self.assertEqual(result.cost_basis, "not_available")
        self.assertIsNone(result.amount)
        self.assertIsNone(result.currency)

    def test_rates_never_apply_an_implicit_batch_discount(self):
        body = rate_card()
        body["batch"] = True
        result = estimate_usage(rate_card_from_mapping(body), normalize_provider_usage("openai", openai_usage()),
                                **{**estimate_context(), "batch": True})
        self.assertEqual(result.amount, "0.00315")

    def test_missing_or_nontext_openai_modality_is_explicitly_unpriced(self):
        for replacement in (None, 1):
            row = openai_usage()
            row["input_audio_tokens"] = replacement
            if replacement is not None:
                row["input_text_tokens"] -= replacement
            result = estimate_usage(rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", row), **estimate_context())
            self.assertIsNone(result.amount)
            self.assertEqual(result.reason_code, "unsupported_or_unknown_modality")

    def test_server_tools_are_not_silently_priced_as_tokens(self):
        for requests in (None, 1):
            row = anthropic_usage()
            row["server_tool_use"]["web_search_requests"] = requests
            result = estimate_usage(rate_card_from_mapping(rate_card("anthropic")), normalize_provider_usage("anthropic", row), **estimate_context("anthropic"))
            self.assertIsNone(result.amount)
            self.assertEqual(result.reason_code, "unsupported_or_unknown_tool_usage")

    def test_exact_arithmetic_does_not_depend_on_global_decimal_context(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            card = rate_card_from_mapping(rate_card())
            result = estimate_usage(card, normalize_provider_usage("openai", openai_usage()), **estimate_context())
        self.assertEqual(result.amount, "0.00315")

    def test_subprecision_cost_is_unavailable_and_never_silently_rounded(self):
        body = rate_card()
        body["rates"] = {key: "0.000000000000000001" for key in body["rates"]}
        result = estimate_usage(rate_card_from_mapping(body), normalize_provider_usage("openai", openai_usage()), **estimate_context())
        self.assertIsNone(result.amount)
        self.assertEqual(result.reason_code, "amount_outside_precision")

    def test_exact_boundary_amount_survives_extra_arithmetic_scale(self):
        body = rate_card("anthropic")
        body["rates"] = {key: "0.000000000000000001" for key in body["rates"]}
        row = anthropic_usage()
        row.update(uncached_input_tokens=1000000, cache_read_input_tokens=0, output_tokens=0)
        row["cache_creation"] = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}
        result = estimate_usage(rate_card_from_mapping(body), normalize_provider_usage("anthropic", row), **estimate_context("anthropic"))
        self.assertEqual(result.amount, "0.000000000000000001")

    def test_fraction_oracle_for_two_hundred_provider_cache_combinations(self):
        generator = random.Random(8)
        for _ in range(100):
            uncached, read, short, long, output = (generator.randrange(100000) for _ in range(5))
            openai = openai_usage()
            openai.update(input_tokens=uncached + read + short + long, input_uncached_tokens=uncached,
                          input_cached_tokens=read, input_cache_write_tokens=short + long,
                          input_text_tokens=uncached, input_cached_text_tokens=read,
                          output_tokens=output, output_text_tokens=output)
            anthropic = anthropic_usage()
            anthropic.update(uncached_input_tokens=uncached, cache_read_input_tokens=read, output_tokens=output,
                             cache_creation={"ephemeral_5m_input_tokens": short, "ephemeral_1h_input_tokens": long})
            for provider, source, expected in (
                ("openai", openai, Fraction(uncached * 4 + read + (short + long) * 4 + output * 16, 2000000)),
                ("anthropic", anthropic, Fraction(uncached * 4 + read + short * 5 + long * 8 + output * 16, 2000000)),
            ):
                result = estimate_usage(rate_card_from_mapping(rate_card(provider)), normalize_provider_usage(provider, source), **estimate_context(provider))
                self.assertEqual(Fraction(Decimal(result.amount)), expected)

    def test_exported_mapping_does_not_alias_immutable_card(self):
        card = rate_card_from_mapping(rate_card())
        digest = card.content_digest
        exported = card.as_mapping()
        exported["rates"]["output"] = "9999"
        self.assertEqual(card.content_digest, digest)
        self.assertEqual(card.as_mapping()["rates"]["output"], "8")

    def test_manually_constructed_noncanonical_or_unbounded_cards_fail(self):
        for value in ('{"rates":{}}', "x" * 8193, "{", "[" * 5000):
            with self.assertRaises(FinanceValueError):
                RateCard(value)

    def test_complete_known_zero_usage_is_a_zero_estimate_not_missing(self):
        row = openai_usage()
        for name, value in tuple(row.items()):
            if type(value) is int:
                row[name] = 0
        result = estimate_usage(rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", row), **estimate_context())
        self.assertEqual(result.amount, "0")
        self.assertEqual(result.cost_basis, "configured_rate_card_estimate")

    def test_missing_total_and_invalid_estimate_context_do_not_succeed(self):
        row = openai_usage()
        row["input_tokens"] = None
        result = estimate_usage(rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", row), **estimate_context())
        self.assertEqual(result.reason_code, "missing_native_usage")
        for change in ({"batch": 0}, {"event_at": "2026-99-99T00:00:00Z"}, {"organization_id": []}, {"actual_model": "bad\nmodel"}):
            with self.assertRaises(FinanceValueError):
                estimate_usage(rate_card_from_mapping(rate_card()), normalize_provider_usage("openai", openai_usage()), **{**estimate_context(), **change})

    def test_closed_rate_contract_rejects_unknown_missing_or_invalid_fields(self):
        variants = [{**rate_card(), "extra": "SYNTHETIC_EXCLUDED"}, {**rate_card(), "version": True},
                    {**rate_card(), "schema_version": True}, {**rate_card(), "batch": 0},
                    {**rate_card(), "effective_to": "2026-08-01T00:00:00Z"},
                    {**rate_card(), "rounding": "half_even"}, {**rate_card(), "pricing_profile": "guessed"}]
        for rate in (-1, "-1", 0.1, "NaN"):
            body = copy.deepcopy(rate_card())
            body["rates"]["output"] = rate
            variants.append(body)
        missing = rate_card()
        missing.pop("currency")
        variants.append(missing)
        for body in variants:
            with self.assertRaises(FinanceValueError):
                rate_card_from_mapping(body)

    def test_invalid_money_in_a_card_uses_the_rate_card_error_boundary(self):
        for rate in ("NaN", "1e2", "0." + "0" * 18 + "1", "9" * 19):
            body = rate_card()
            body["rates"]["output"] = rate
            with self.subTest(rate=rate), self.assertRaises(FinanceValueError) as caught:
                rate_card_from_mapping(body)
            self.assertEqual(str(caught.exception), "finance_invalid_rate_card")
        with self.assertRaises(FinanceValueError) as caught:
            rate_card_from_mapping({**rate_card(), "currency": "invalid"})
        self.assertEqual(str(caught.exception), "finance_invalid_rate_card")


if __name__ == "__main__":
    unittest.main()
