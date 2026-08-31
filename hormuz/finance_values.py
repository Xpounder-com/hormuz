"""Bounded, exact accounting values. No credentials, storage, network or I/O.

These primitives cannot establish source/tenant authority or live verification.
Provider reports are observations, not automatically finalized invoice facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, DivisionByZero, Inexact, InvalidOperation, Overflow, Rounded, localcontext
import json
import re


PAGE_BYTES = 1048576
JSON_DEPTH = 16
JSON_MEMBERS = 65536
_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,18})?\Z")
_CURRENCY = re.compile(r"[A-Za-z]{3}\Z")
_ERRORS = frozenset({
    "finance_invalid_amount", "finance_invalid_source", "finance_source_limit_exceeded",
    "finance_unsupported_provider", "finance_invalid_usage", "finance_invalid_rate_card",
    "finance_invalid_estimate_context",
})


class FinanceValueError(ValueError):
    """Only fixed codes may cross the caller's diagnostic boundary."""

    def __init__(self, code: str):
        self.code = code if isinstance(code, str) and code in _ERRORS else "finance_invalid_source"
        super().__init__(self.code)


def exact_context():
    # Do not inherit caller precision, exponent limits, rounding or traps.
    return localcontext(Context(
        prec=96, Emin=-9999, Emax=9999,
        traps=[InvalidOperation, DivisionByZero, Overflow, Inexact, Rounded],
    ))


def decimal_text(value: object) -> str:
    """Canonical finite money, at most 18 integer and 18 fractional digits."""
    if isinstance(value, str):
        if not _DECIMAL.fullmatch(value):
            raise FinanceValueError("finance_invalid_amount")
        number = Decimal(value)
    elif type(value) is Decimal:
        number = value
    else:
        raise FinanceValueError("finance_invalid_amount")
    if not number.is_finite():
        raise FinanceValueError("finance_invalid_amount")
    if number.is_zero():
        return "0"
    parts = number.as_tuple()
    if len(parts.digits) > 128:
        raise FinanceValueError("finance_invalid_amount")
    # Decimal.normalize() uses ambient precision and may round. Remove only
    # non-significant zero digits directly, without an arithmetic operation.
    digits, exponent = list(parts.digits), parts.exponent
    while digits[-1] == 0:
        digits.pop()
        exponent += 1
    if exponent < -18 or len(digits) + exponent > 18:
        raise FinanceValueError("finance_invalid_amount")
    result = format(Decimal((parts.sign, tuple(digits), exponent)), "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def currency_code(value: object) -> str:
    if not isinstance(value, str) or not _CURRENCY.fullmatch(value):
        raise FinanceValueError("finance_invalid_amount")
    # Format normalization only. This does not assert conversion, settlement,
    # minor-unit rounding, or that the currency is supported by another source.
    return value.upper()


@dataclass(frozen=True)
class ProviderAmount:
    amount: str
    currency: str
    native_amount: str
    native_currency: str
    native_unit: str

    def __post_init__(self) -> None:
        if (self.amount != decimal_text(self.amount) or self.native_amount != decimal_text(self.native_amount)
                or self.currency != currency_code(self.native_currency)):
            raise FinanceValueError("finance_invalid_amount")
        if self.native_unit == "major_currency":
            expected = self.native_amount
        elif self.native_unit == "fractional_cent" and self.currency == "USD":
            with exact_context():
                expected = decimal_text(Decimal(self.native_amount) / Decimal(100))
        else:
            raise FinanceValueError("finance_invalid_amount")
        if self.amount != expected:
            raise FinanceValueError("finance_invalid_amount")


def provider_amount(provider: str, value: object, currency: object) -> ProviderAmount:
    code = currency_code(currency)
    if provider == "openai":
        if type(value) is not Decimal and type(value) is not int:
            raise FinanceValueError("finance_invalid_amount")
        if type(value) is int and not -(10**18) < value < 10**18:
            raise FinanceValueError("finance_invalid_amount")
        native = decimal_text(Decimal(value))
        amount, unit = native, "major_currency"
    elif provider == "anthropic":
        if code != "USD" or not isinstance(value, str):
            raise FinanceValueError("finance_invalid_amount")
        native = decimal_text(value)
        with exact_context():
            amount = decimal_text(Decimal(native) / Decimal(100))
        unit = "fractional_cent"
    else:
        raise FinanceValueError("finance_unsupported_provider")
    return ProviderAmount(amount, code, native, currency, unit)


def decode_provider_json(data: bytes) -> dict:
    """Bound allocation/recursion, reject ambiguity, and never parse floats."""
    if type(data) is not bytes or not 1 <= len(data) <= PAGE_BYTES:
        raise FinanceValueError("finance_source_limit_exceeded")
    depth, quoted, escaped = 0, False, False
    for character in data:
        if quoted:
            if escaped:
                escaped = False
            elif character == 92:
                escaped = True
            elif character == 34:
                quoted = False
        elif character == 34:
            quoted = True
        elif character in (91, 123):
            depth += 1
            if depth > JSON_DEPTH:
                raise FinanceValueError("finance_source_limit_exceeded")
        elif character in (93, 125):
            depth -= 1

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise FinanceValueError("finance_invalid_source")
            result[key] = value
        return result

    def integer(value):
        if len(value) > 128:
            raise FinanceValueError("finance_source_limit_exceeded")
        return int(value)

    def fractional(value):
        if len(value) > 128:
            raise FinanceValueError("finance_source_limit_exceeded")
        with exact_context():
            number = Decimal(value)
            decimal_text(number)
        return number

    def nonfinite(_value):
        raise FinanceValueError("finance_invalid_source")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_int=integer,
                           parse_float=fractional, parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError, InvalidOperation):
        raise FinanceValueError("finance_invalid_source") from None
    if type(value) is not dict:
        raise FinanceValueError("finance_invalid_source")
    pending, members = [(value, 1)], 0
    while pending:
        item, depth = pending.pop()
        if depth > JSON_DEPTH and isinstance(item, (dict, list)):
            raise FinanceValueError("finance_source_limit_exceeded")
        if isinstance(item, dict):
            members += len(item)
            pending.extend((key, depth + 1) for key in item)
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            members += len(item)
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError:
                raise FinanceValueError("finance_invalid_source") from None
        if members > JSON_MEMBERS:
            raise FinanceValueError("finance_source_limit_exceeded")
    return value
