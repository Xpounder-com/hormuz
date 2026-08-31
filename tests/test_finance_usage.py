from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import unittest

from hormuz.finance_values import FinanceValueError
from hormuz.finance_usage import normalize_provider_usage

if __package__:
    from ._finance_values_fixture import anthropic_usage, openai_usage
else:
    from _finance_values_fixture import anthropic_usage, openai_usage


class FinanceUsageTests(unittest.TestCase):
    def test_openai_input_includes_cache_partitions_once(self):
        usage = normalize_provider_usage("openai", openai_usage())
        self.assertEqual(usage.count("input_tokens"), 1000)
        self.assertEqual(usage.count("uncached_input_tokens"), 600)
        self.assertEqual(usage.count("cache_read_tokens"), 300)
        self.assertEqual(usage.count("cache_write_tokens"), 100)
        self.assertEqual(usage.count("total_tokens"), 1200)
        self.assertEqual(usage.count("request_count"), 2)
        self.assertIsNone(usage.count("reasoning_tokens"))
        self.assertIsNone(usage.count("billable_tokens"))

    def test_anthropic_keeps_cache_lifetimes_and_sums_input_once(self):
        usage = normalize_provider_usage("anthropic", anthropic_usage())
        self.assertEqual(usage.count("input_tokens"), 1000)
        self.assertEqual(usage.count("total_tokens"), 1200)
        self.assertEqual(usage.count("cache_write_tokens"), 100)
        self.assertEqual(usage.count("cache_write_5m_tokens"), 80)
        self.assertEqual(usage.count("cache_write_1h_tokens"), 20)
        self.assertEqual(dict(usage.native_counts)["cache_creation.ephemeral_1h_input_tokens"], 20)
        self.assertIsNone(usage.count("request_count"))
        self.assertIsNone(usage.count("reasoning_tokens"))
        self.assertIsNone(usage.count("billable_tokens"))

    def test_missing_and_null_native_counts_remain_unknown(self):
        for provider, source, native_name, normalized in (("openai", openai_usage(), "input_cache_write_tokens", "cache_write_tokens"),
                                                          ("anthropic", anthropic_usage(), "uncached_input_tokens", "input_tokens")):
            for missing in (False, True):
                row = dict(source)
                if missing:
                    row.pop(native_name)
                else:
                    row[native_name] = None
                usage = normalize_provider_usage(provider, row)
                self.assertIsNone(usage.count(normalized))
                self.assertIsNone(dict(usage.native_counts)[native_name])

    def test_cache_write_missing_is_not_inferred_as_zero(self):
        row = openai_usage()
        row.pop("input_cache_write_tokens")
        row.pop("input_uncached_tokens")
        usage = normalize_provider_usage("openai", row)
        self.assertIsNone(usage.count("uncached_input_tokens"))
        self.assertIsNone(usage.count("cache_write_tokens"))
        self.assertEqual(usage.count("input_tokens"), 1000)

    def test_uncached_count_can_only_be_derived_from_known_partitions(self):
        row = openai_usage()
        row.pop("input_uncached_tokens")
        usage = normalize_provider_usage("openai", row)
        self.assertEqual(usage.count("uncached_input_tokens"), 600)
        self.assertIsNone(dict(usage.native_counts)["input_uncached_tokens"])

    def test_inconsistent_overlapping_counts_fail_instead_of_double_counting(self):
        for change in ({"input_cached_tokens": 1100}, {"input_cache_write_tokens": 800},
                       {"input_uncached_tokens": 999}, {"input_cached_text_tokens": 999}):
            with self.subTest(change=change), self.assertRaises(FinanceValueError):
                normalize_provider_usage("openai", {**openai_usage(), **change})

    def test_counts_are_bounded_nonnegative_integers_not_booleans_or_floats(self):
        for provider, source, name in (("openai", openai_usage(), "input_tokens"),
                                       ("anthropic", anthropic_usage(), "uncached_input_tokens")):
            for value in (-1, True, 1.5, "1", 2**63):
                with self.subTest(provider=provider, value=value), self.assertRaises(FinanceValueError):
                    normalize_provider_usage(provider, {**source, name: value})

    def test_overflow_in_derived_totals_fails(self):
        row = anthropic_usage()
        row["uncached_input_tokens"] = 2**63 - 1
        with self.assertRaises(FinanceValueError):
            normalize_provider_usage("anthropic", row)

    def test_unknown_fields_and_work_text_are_not_retained(self):
        row = {**openai_usage(), "description": "SYNTHETIC_EXCLUDED", "user_id": "SYNTHETIC_EXCLUDED",
               "api_key": "SYNTHETIC_EXCLUDED", "reasoning_tokens": 123}
        usage = normalize_provider_usage("openai", row)
        self.assertNotIn("SYNTHETIC_EXCLUDED", repr(usage))
        self.assertIsNone(usage.count("reasoning_tokens"))

    def test_wrong_provider_or_source_object_fails(self):
        for provider, row in (("unsupported", openai_usage()), ("openai", anthropic_usage()),
                              ("openai", {**openai_usage(), "object": "organization.usage.embeddings.result"}),
                              ("anthropic", {**anthropic_usage(), "cache_creation": []}),
                              ("anthropic", openai_usage()), ("anthropic", {})):
            with self.assertRaises(FinanceValueError):
                normalize_provider_usage(provider, row)

    def test_constructed_usage_cannot_change_normalized_counts_or_use_mutable_fields(self):
        usage = normalize_provider_usage("openai", openai_usage())
        for replacement in (tuple((key, 0) for key, _value in usage.normalized_counts),
                            list(usage.normalized_counts), tuple([key, value] for key, value in usage.normalized_counts)):
            with self.assertRaises(FinanceValueError):
                replace(usage, normalized_counts=replacement)

    def test_null_cache_lifetime_does_not_erase_other_native_counts(self):
        row = anthropic_usage()
        row["cache_creation"]["ephemeral_5m_input_tokens"] = None
        usage = normalize_provider_usage("anthropic", row)
        self.assertIsNone(usage.count("cache_write_tokens"))
        self.assertIsNone(usage.count("input_tokens"))
        self.assertEqual(usage.count("cache_write_1h_tokens"), 20)
        self.assertEqual(usage.count("output_tokens"), 200)

    def test_unrecognized_cache_or_tool_categories_cannot_disappear_from_cost(self):
        for parent, unknown in (("cache_creation", "ephemeral_24h_input_tokens"), ("server_tool_use", "web_fetch_requests")):
            row = anthropic_usage()
            row[parent][unknown] = 50
            with self.subTest(parent=parent), self.assertRaises(FinanceValueError):
                normalize_provider_usage("anthropic", row)

    def test_normalized_facts_are_deeply_immutable_and_do_not_alias_input(self):
        row = anthropic_usage()
        usage = normalize_provider_usage("anthropic", row)
        before = usage.native_counts
        row["cache_creation"]["ephemeral_5m_input_tokens"] = 999
        self.assertEqual(usage.native_counts, before)
        with self.assertRaises((FrozenInstanceError, AttributeError)):
            usage.provider = "openai"


if __name__ == "__main__":
    unittest.main()
