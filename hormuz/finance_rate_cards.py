"""Immutable operator-configured rate identities and exact token estimates.

This module neither loads current provider prices nor changes the v1 gateway
ledger. Callers must authorize tenant scope before loading any financial data.
Missing dimensions/categories produce an unavailable estimate, never zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import re

from .finance_usage import UsageVector
from .finance_values import FinanceValueError, currency_code, decimal_text, exact_context


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "rate_card_id", "version", "provider", "actual_model",
    "currency", "effective_from", "effective_to", "service_tier", "batch", "pricing_profile", "source_kind",
    "unit", "rounding", "rates",
})
_CATEGORIES = {
    "openai": {"uncached_input": "uncached_input_tokens", "cache_read": "cache_read_tokens",
               "cache_write": "cache_write_tokens", "output": "output_tokens"},
    "anthropic": {"uncached_input": "uncached_input_tokens", "cache_read": "cache_read_tokens",
                  "cache_write_5m": "cache_write_5m_tokens", "cache_write_1h": "cache_write_1h_tokens", "output": "output_tokens"},
}
_PROFILES = {"openai": "openai_text_tokens_v1", "anthropic": "anthropic_messages_tokens_v1"}


def _text(value, pattern=_ID):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise FinanceValueError("finance_invalid_rate_card")
    return value


def _time(value):
    _text(value, _TIME)
    try:
        return datetime.fromisoformat(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except ValueError:
        raise FinanceValueError("finance_invalid_rate_card") from None


def _validated_body(value):
    if type(value) is not dict or set(value) != _FIELDS:
        raise FinanceValueError("finance_invalid_rate_card")
    if (value["schema_id"] != "hormuz.finance-rate-card" or type(value["schema_version"]) is not int
            or value["schema_version"] != 1 or type(value["version"]) is not int or not 1 <= value["version"] <= 2147483647):
        raise FinanceValueError("finance_invalid_rate_card")
    provider = value["provider"]
    if not isinstance(provider, str) or provider not in _CATEGORIES:
        raise FinanceValueError("finance_invalid_rate_card")
    if (value["pricing_profile"] != _PROFILES[provider] or value["source_kind"] != "operator_configured"
            or value["unit"] != "per_million_tokens" or value["rounding"] != "exact_or_unavailable_v1"
            or type(value["batch"]) is not bool):
        raise FinanceValueError("finance_invalid_rate_card")
    for name in ("organization_id", "rate_card_id", "service_tier"):
        _text(value[name])
    _text(value["actual_model"], _MODEL)
    start, end = _time(value["effective_from"]), _time(value["effective_to"])
    if start >= end:
        raise FinanceValueError("finance_invalid_rate_card")
    rates = value["rates"]
    if type(rates) is not dict or set(rates) != set(_CATEGORIES[provider]):
        raise FinanceValueError("finance_invalid_rate_card")
    normalized = {}
    for name, rate in rates.items():
        if rate is not None:
            if not isinstance(rate, str):
                raise FinanceValueError("finance_invalid_rate_card")
            rate = decimal_text(rate)
            if Decimal(rate) < 0:
                raise FinanceValueError("finance_invalid_rate_card")
        normalized[name] = rate
    return {**value, "currency": currency_code(value["currency"]), "effective_from": start,
            "effective_to": end, "rates": normalized}


@dataclass(frozen=True)
class RateCard:
    """Canonical JSON is immutable; exported mappings are independent copies."""

    _canonical: str

    def __post_init__(self) -> None:
        self.verify()

    def as_mapping(self) -> dict:
        return json.loads(self._canonical)

    def verify(self) -> None:
        if type(self._canonical) is not str or len(self._canonical) > 8192:
            raise FinanceValueError("finance_invalid_rate_card")
        try:
            body = _validated_body(self.as_mapping())
        except (ValueError, UnicodeError, RecursionError):
            raise FinanceValueError("finance_invalid_rate_card") from None
        if json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True) != self._canonical:
            raise FinanceValueError("finance_invalid_rate_card")

    @property
    def content_digest(self) -> str:
        return hashlib.sha256(self._canonical.encode()).hexdigest()

    @property
    def version(self) -> int:
        return self.as_mapping()["version"]


def rate_card_from_mapping(value: object) -> RateCard:
    body = _validated_body(value)
    result = RateCard(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return result


@dataclass(frozen=True)
class CostEstimate:
    amount: str | None
    currency: str
    cost_basis: str
    reason_code: str
    rate_card_id: str
    rate_card_version: int
    rate_card_digest: str
    provider_final: bool = field(default=False, init=False)


def estimate_usage(card: RateCard, usage: UsageVector, *, organization_id: str, actual_model: str | None,
                   event_at: str, service_tier: str | None, batch: bool | None) -> CostEstimate:
    if type(card) is not RateCard or type(usage) is not UsageVector:
        raise FinanceValueError("finance_invalid_estimate_context")
    card.verify()
    usage.verify()
    body = card.as_mapping()

    def result(reason, amount=None):
        return CostEstimate(amount, body["currency"], "configured_rate_card_estimate" if amount is not None else "not_available",
                            reason, body["rate_card_id"], body["version"], card.content_digest)

    try:
        _text(organization_id)
        instant = _time(event_at)
        if actual_model is not None:
            _text(actual_model, _MODEL)
        if service_tier is not None:
            _text(service_tier)
    except FinanceValueError:
        raise FinanceValueError("finance_invalid_estimate_context") from None
    if batch is not None and type(batch) is not bool:
        raise FinanceValueError("finance_invalid_estimate_context")
    if body["organization_id"] != organization_id:
        return result("tenant_mismatch")
    if body["provider"] != usage.provider:
        return result("provider_mismatch")
    if actual_model is None:
        return result("actual_model_unknown")
    if body["actual_model"] != actual_model:
        return result("actual_model_mismatch")
    if not body["effective_from"] <= instant < body["effective_to"]:
        return result("outside_rate_interval")
    if service_tier is None or body["service_tier"] != service_tier:
        return result("unsupported_tier")
    if batch is None:
        return result("batch_unknown")
    if body["batch"] != batch:
        return result("batch_mismatch")
    native = dict(usage.native_counts)
    if usage.provider == "openai":
        # This profile has text-token rates only. Unknown or nonzero audio /
        # image categories cannot silently receive text rates.
        if any(native[name] != 0 for name in (
            "input_image_tokens", "input_audio_tokens", "input_cached_image_tokens",
            "input_cached_audio_tokens", "output_image_tokens", "output_audio_tokens",
        )):
            return result("unsupported_or_unknown_modality")
    elif native["server_tool_use.web_search_requests"] != 0:
        return result("unsupported_or_unknown_tool_usage")
    categories = _CATEGORIES[usage.provider]
    if usage.count("input_tokens") is None or any(usage.count(name) is None for name in categories.values()):
        return result("missing_native_usage")
    if any(body["rates"][name] is None for name in categories):
        return result("missing_rate")
    with exact_context():
        amount = sum(Decimal(usage.count(field)) * Decimal(body["rates"][name])
                     for name, field in categories.items()) / Decimal(1000000)
        try:
            rendered = decimal_text(amount)
        except FinanceValueError:
            return result("amount_outside_precision")
    return result("estimated", rendered)
