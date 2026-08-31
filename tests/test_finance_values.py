from __future__ import annotations

from decimal import Decimal, Inexact, Rounded, localcontext
import json
import unittest

from hormuz.finance_values import FinanceValueError, ProviderAmount, decimal_text, decode_provider_json, provider_amount


class FinanceValueTests(unittest.TestCase):
    def test_exact_decimal_strings_have_no_float_round_trip(self):
        for value, expected in (("0.000000000000000001", "0.000000000000000001"),
                                ("999999999999999999.999999999999999999", "999999999999999999.999999999999999999"),
                                ("-12.3000", "-12.3"), ("-0.00", "0"), ("2.000", "2")):
            self.assertEqual(decimal_text(value), expected)

    def test_money_rejects_floats_ambiguous_or_unbounded_values(self):
        invalid = (0.1, True, None, [], "01", "+1", "1e2", " 1", ".2", "1.", "NaN", "Infinity",
                   "1" * 19, "0." + "0" * 18 + "1", Decimal("1e1000000"), Decimal("1e-1000000"))
        for value in invalid:
            with self.subTest(value=type(value).__name__), self.assertRaises(FinanceValueError):
                decimal_text(value)

    def test_source_units_are_distinct_and_signs_preserved(self):
        openai = provider_amount("openai", Decimal("0.12345"), "usd")
        anthropic = provider_amount("anthropic", "0.12345", "USD")
        self.assertEqual((openai.amount, openai.native_amount, openai.native_unit), ("0.12345", "0.12345", "major_currency"))
        self.assertEqual((anthropic.amount, anthropic.native_amount, anthropic.native_unit), ("0.0012345", "0.12345", "fractional_cent"))
        self.assertEqual(provider_amount("anthropic", "-10.25", "usd").amount, "-0.1025")
        self.assertEqual(openai.currency, "USD")

    def test_amount_value_cannot_claim_inconsistent_units_or_currency(self):
        for fields in (("1", "USD", "1", "usd", "fractional_cent"),
                       ("1", "EUR", "1", "usd", "major_currency"),
                       ("1", "USD", "1", "usd", "unknown_unit")):
            with self.assertRaises(FinanceValueError):
                ProviderAmount(*fields)

    def test_ambient_decimal_context_cannot_round_or_break_source_conversion(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            self.assertEqual(provider_amount("anthropic", "12345.6789", "usd").amount, "123.456789")

    def test_non_significant_decimal_scale_does_not_make_exact_money_unavailable(self):
        self.assertEqual(decimal_text(Decimal("0.000000000000000001000000")), "0.000000000000000001")
        self.assertEqual(decimal_text(Decimal("1.000000000000000000000000")), "1")

    def test_no_implicit_fx_or_unsupported_precision(self):
        for arguments in (("anthropic", "1", "eur"), ("openai", Decimal("1"), None),
                          ("openai", 0.1, "usd"), ("anthropic", "0.000000000000000001", "usd"),
                          ("unsupported", "1", "usd"), ("openai", "1", "usd"), ("anthropic", 1, "usd")):
            with self.subTest(arguments=arguments), self.assertRaises(FinanceValueError):
                provider_amount(*arguments)
        self.assertEqual(provider_amount("openai", Decimal("1.25"), "eur").currency, "EUR")

    def test_json_parses_fractional_numbers_as_exact_decimals(self):
        row = decode_provider_json(b'{"amount":0.123456789012345678,"tokens":9}')
        self.assertEqual(row["amount"], Decimal("0.123456789012345678"))
        self.assertIs(type(row["tokens"]), int)

    def test_json_duplicate_nonfinite_invalid_unicode_and_wrong_top_level_fail(self):
        for body in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":"\\ud800"}',
                     b'{"a":1e1000000}', b'[]', b'null', b'{"a":' + b'9' * 129 + b'}', b'\xff'):
            with self.subTest(body=body[:20]), self.assertRaises(FinanceValueError):
                decode_provider_json(body)

    def test_json_resource_bounds_and_quoted_brackets(self):
        self.assertEqual(decode_provider_json(b'{"a":"[[[\\\"{{}"}'), {"a": '[[["{{}'})
        for body in (b" " * (1048576 + 1), b'{"a":' + b'[' * 17 + b'0' + b']' * 17 + b'}',
                     json.dumps({"a": [0] * 65537}).encode()):
            with self.assertRaises(FinanceValueError):
                decode_provider_json(body)

    def test_json_exact_container_and_member_limits_are_accepted(self):
        nested = b'{"a":' + b'[' * 15 + b'0' + b']' * 15 + b'}'
        self.assertIsInstance(decode_provider_json(nested), dict)
        self.assertEqual(len(decode_provider_json(json.dumps({"a": [0] * 65535}).encode())["a"]), 65535)

    def test_errors_are_fixed_content_free_codes(self):
        with self.assertRaises(FinanceValueError) as caught:
            decimal_text("SYNTHETIC_DO_NOT_RETAIN")
        self.assertEqual(str(caught.exception), "finance_invalid_amount")
        self.assertNotIn("SYNTHETIC", repr(caught.exception))


if __name__ == "__main__":
    unittest.main()
