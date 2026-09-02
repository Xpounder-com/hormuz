"""Immutable, content-free finance evidence for one provider attempt.

This module owns the strict provider-native allowlist and configured-route
estimate values.  It performs no I/O and has no provider, database, policy, or
authorization capability.  Storage adapters validate and persist the values
inside their existing attempt transactions.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import hashlib
import json
import re
import uuid
from typing import Any, Mapping

from .finance_values import FinanceValueError, decimal_text, exact_context


FINANCE_ATTEMPT_SCHEMA_ID = "hormuz.finance-attempt-evidence"
FINANCE_ATTEMPT_SCHEMA_VERSION = 1
MAX_INTEGER = 9223372036854775807
MAX_NATIVE_BYTES = 16384
MAX_IDENTIFIER_BYTES = 128

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_TERMINAL_STATES = frozenset({"succeeded", "failed", "rate_limited", "outcome_unknown"})
_OBSERVATION_STATES = frozenset({"complete", "partial", "absent"})
_OBSERVATION_REASONS = frozenset({
    "provider_usage_absent",
    "provider_usage_incomplete",
    "provider_usage_invalid",
    "stale_pending",
    "provider_transport_ambiguous",
    "provider_stream_interrupted",
})
_ESTIMATE_REASONS = frozenset({
    "estimated",
    "missing_native_usage",
    "invalid_native_usage",
    "estimate_outside_precision",
    "attempt_outcome_unknown",
})
_PROFILES = {
    "openai": "openai.responses.usage.v1",
    "anthropic": "anthropic.messages.usage.v1",
}
_PROFILE_PROTOCOLS = {profile: protocol for protocol, profile in _PROFILES.items()}
_REQUIRED = {
    "openai": frozenset({"input_tokens", "output_tokens", "total_tokens"}),
    "anthropic": frozenset({"input_tokens", "output_tokens"}),
}
_INTEGER_PATHS = {
    "openai": frozenset({
        "input_tokens",
        "input_tokens_details.cached_tokens",
        "input_tokens_details.cache_write_tokens",
        "output_tokens",
        "output_tokens_details.reasoning_tokens",
        "total_tokens",
    }),
    "anthropic": frozenset({
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cache_creation.ephemeral_5m_input_tokens",
        "cache_creation.ephemeral_1h_input_tokens",
        "output_tokens",
        "output_tokens_details.thinking_tokens",
        "server_tool_use.web_search_requests",
        "server_tool_use.web_fetch_requests",
    }),
}
_DIMENSION_PATHS = {
    "openai": {"service_tier": frozenset({"default", "flex", "priority", "ultrafast"})},
    "anthropic": {
        "service_tier": frozenset({"standard", "priority", "batch"}),
        "inference_geo": None,
    },
}


def _identifier(value: object, *, uuid_value: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("finance_attempt_evidence_invalid")
    try:
        encoded = value.encode("utf-8")
        value.encode("ascii")
    except UnicodeError:
        raise ValueError("finance_attempt_evidence_invalid") from None
    if len(encoded) > MAX_IDENTIFIER_BYTES:
        raise ValueError("finance_attempt_evidence_invalid")
    if uuid_value:
        try:
            if str(uuid.UUID(value)) != value.lower():
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise ValueError("finance_attempt_evidence_invalid") from None
    elif _ID.fullmatch(value) is None:
        raise ValueError("finance_attempt_evidence_invalid")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise ValueError("finance_attempt_evidence_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("finance_attempt_evidence_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("finance_attempt_evidence_invalid")
    # PostgreSQL normalizes TIMESTAMPTZ text on round trip. Accept only the
    # exact UTC representation emitted by the runtime so the row reconstructed
    # for audit verification remains byte-identical to the chained event.
    if value != parsed.astimezone(timezone.utc).isoformat():
        raise ValueError("finance_attempt_evidence_invalid")
    return value


def _counter(value: object, *, optional: bool = True) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError("finance_attempt_evidence_invalid")
    return value


@dataclass(frozen=True)
class ConfiguredRateCardBinding:
    """The immutable configured-route price identity captured pre-egress."""

    rate_card_id: str
    rate_card_version: int
    rate_card_digest: str
    currency: str

    def __post_init__(self) -> None:
        _identifier(self.rate_card_id)
        if type(self.rate_card_version) is not int or not 1 <= self.rate_card_version <= 2147483647:
            raise ValueError("finance_attempt_binding_invalid")
        if not isinstance(self.rate_card_digest, str) or _DIGEST.fullmatch(self.rate_card_digest) is None:
            raise ValueError("finance_attempt_binding_invalid")
        if not isinstance(self.currency, str) or _CURRENCY.fullmatch(self.currency) is None:
            raise ValueError("finance_attempt_binding_invalid")


def configured_rate_card_binding(value: Mapping[str, object]) -> ConfiguredRateCardBinding:
    """Convert the existing route-snapshot mapping without widening its API."""

    try:
        return ConfiguredRateCardBinding(
            rate_card_id=value["id"],  # type: ignore[arg-type]
            rate_card_version=value["version"],  # type: ignore[arg-type]
            rate_card_digest=value["content_digest"],  # type: ignore[arg-type]
            currency=value["currency"],  # type: ignore[arg-type]
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("finance_attempt_binding_invalid") from None


def binding_from_attempt_row(row: Mapping[str, object]) -> ConfiguredRateCardBinding | None:
    """Load the immutable binding or identify one pre-migration coverage gap."""

    state = row.get("configured_rate_card_state")
    coordinates = (
        row.get("configured_rate_card_id"),
        row.get("configured_rate_card_version"),
        row.get("configured_rate_card_digest"),
        row.get("configured_rate_card_currency"),
    )
    if state in {None, "legacy_unavailable"} and all(value is None for value in coordinates):
        return None
    if state != "configured":
        raise ValueError("finance_attempt_binding_invalid")
    try:
        return ConfiguredRateCardBinding(
            rate_card_id=coordinates[0],  # type: ignore[arg-type]
            rate_card_version=coordinates[1],  # type: ignore[arg-type]
            rate_card_digest=coordinates[2],  # type: ignore[arg-type]
            currency=coordinates[3],  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        raise ValueError("finance_attempt_binding_invalid") from None


def repository_contract_binding() -> ConfiguredRateCardBinding:
    """Bind frozen direct repository calls to an explicit unpriced identity.

    The gateway supplies its actual configured-route snapshot. Direct callers
    of the unchanged v1 repository surface have no rate vector, so this
    deterministic compatibility identity preserves cardinality while the
    terminal estimate remains explicitly unavailable.
    """

    value = {
        "contract": "hormuz.usage-repository-request-attempt.v1",
        "pricing": "unavailable",
        "currency": "USD",
    }
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("finance_attempt_binding_invalid") from None
    return ConfiguredRateCardBinding(
        rate_card_id="usage-repository-v1-unpriced",
        rate_card_version=1,
        rate_card_digest=hashlib.sha256(encoded).hexdigest(),
        currency="USD",
    )


@dataclass(frozen=True)
class NativeUsageObservation:
    provider_schema_id: str
    provider_schema_version: int
    state: str
    reason_code: str | None
    native_payload_json: str | None
    native_payload_digest: str | None
    provider_input_tokens: int | None = None
    provider_output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    cache_write_5m_input_tokens: int | None = None
    cache_write_1h_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    total_tokens: int | None = None
    billable_input_tokens: int | None = None
    billable_output_tokens: int | None = None
    server_tool_request_count: int | None = None
    provider_service_tier: str | None = None
    provider_inference_geo: str | None = None

    def __post_init__(self) -> None:
        validate_native_usage_observation(self)


@dataclass(frozen=True)
class _NormalizedNativeUsage:
    provider_input_tokens: int | None
    provider_output_tokens: int | None
    cache_read_input_tokens: int | None
    cache_write_input_tokens: int | None
    cache_write_5m_input_tokens: int | None
    cache_write_1h_input_tokens: int | None
    reasoning_output_tokens: int | None
    total_tokens: int | None
    billable_input_tokens: int | None
    billable_output_tokens: int | None
    server_tool_request_count: int | None
    provider_service_tier: str | None
    provider_inference_geo: str | None


def _reject_json_constant(_: str) -> None:
    raise ValueError("finance_attempt_evidence_invalid")


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("finance_attempt_evidence_invalid")
        result[key] = item
    return result


def _validated_native_payload_values(
    payload: object,
    protocol: str,
) -> dict[str, int | str]:
    integer_paths = _INTEGER_PATHS[protocol]
    dimension_paths = _DIMENSION_PATHS[protocol]
    leaf_paths = set(integer_paths) | set(dimension_paths)
    container_paths = {
        ".".join(path.split(".")[:depth])
        for path in leaf_paths
        for depth in range(1, len(path.split(".")))
    }
    values: dict[str, int | str] = {}

    def walk(node: object, prefix: str = "") -> None:
        if type(node) is not dict or not node:
            raise ValueError("finance_attempt_evidence_invalid")
        for member, item in node.items():
            path = f"{prefix}.{member}" if prefix else member
            if path in integer_paths:
                if type(item) is not int or not 0 <= item <= MAX_INTEGER:
                    raise ValueError("finance_attempt_evidence_invalid")
                values[path] = item
            elif path in dimension_paths:
                normalized = _identifier(item)
                allowed = dimension_paths[path]
                if allowed is not None and normalized not in allowed:
                    raise ValueError("finance_attempt_evidence_invalid")
                values[path] = normalized
            elif path in container_paths:
                walk(item, path)
            else:
                raise ValueError("finance_attempt_evidence_invalid")

    walk(payload)
    return values


def validate_native_usage_observation(value: NativeUsageObservation) -> None:
    if type(value) is not NativeUsageObservation:
        raise ValueError("finance_attempt_evidence_invalid")
    if value.provider_schema_id not in _PROFILES.values() or value.provider_schema_version != 1:
        raise ValueError("finance_attempt_evidence_invalid")
    if value.state not in _OBSERVATION_STATES:
        raise ValueError("finance_attempt_evidence_invalid")
    counters = (
        value.provider_input_tokens,
        value.provider_output_tokens,
        value.cache_read_input_tokens,
        value.cache_write_input_tokens,
        value.cache_write_5m_input_tokens,
        value.cache_write_1h_input_tokens,
        value.reasoning_output_tokens,
        value.total_tokens,
        value.billable_input_tokens,
        value.billable_output_tokens,
        value.server_tool_request_count,
    )
    for counter in counters:
        _counter(counter)
    for dimension in (value.provider_service_tier, value.provider_inference_geo):
        if dimension is not None:
            _identifier(dimension)
    if value.state == "complete":
        if value.reason_code is not None or value.native_payload_json is None:
            raise ValueError("finance_attempt_evidence_invalid")
    elif value.reason_code not in _OBSERVATION_REASONS:
        raise ValueError("finance_attempt_evidence_invalid")
    if (
        (value.state == "partial" and value.reason_code == "provider_usage_absent")
        or (value.state == "absent" and value.reason_code == "provider_usage_incomplete")
    ):
        raise ValueError("finance_attempt_evidence_invalid")
    if value.state == "absent":
        if (
            value.native_payload_json is not None
            or value.native_payload_digest is not None
            or any(counter is not None for counter in counters)
            or value.provider_service_tier is not None
            or value.provider_inference_geo is not None
        ):
            raise ValueError("finance_attempt_evidence_invalid")
        return
    if not isinstance(value.native_payload_json, str):
        raise ValueError("finance_attempt_evidence_invalid")
    try:
        encoded = value.native_payload_json.encode("utf-8")
        if not 1 <= len(encoded) <= MAX_NATIVE_BYTES:
            raise ValueError
        parsed = json.loads(
            value.native_payload_json,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_object,
        )
        protocol = _PROFILE_PROTOCOLS[value.provider_schema_id]
        payload_values = _validated_native_payload_values(parsed, protocol)
        canonical = json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        normalized, intrinsically_invalid = _normalize_native_values(protocol, payload_values)
    except (KeyError, TypeError, UnicodeError, ValueError, RecursionError):
        raise ValueError("finance_attempt_evidence_invalid") from None
    if (
        canonical != value.native_payload_json
        or not isinstance(value.native_payload_digest, str)
        or _DIGEST.fullmatch(value.native_payload_digest) is None
        or hashlib.sha256(encoded).hexdigest() != value.native_payload_digest
        or any(
            getattr(value, field.name) != getattr(normalized, field.name)
            for field in fields(normalized)
        )
    ):
        raise ValueError("finance_attempt_evidence_invalid")
    required_present = _REQUIRED[protocol].issubset(payload_values)
    if value.state == "complete" and (not required_present or intrinsically_invalid):
        raise ValueError("finance_attempt_evidence_invalid")
    if value.reason_code == "provider_usage_incomplete" and (
        required_present or intrinsically_invalid
    ):
        raise ValueError("finance_attempt_evidence_invalid")


@dataclass(frozen=True)
class ConfiguredRouteEstimate:
    availability: str
    amount: str | None
    amount_microusd: int | None
    currency: str
    cost_basis: str
    reason_code: str
    rate_card_id: str
    rate_card_version: int
    rate_card_digest: str
    provider_final: bool = False

    def __post_init__(self) -> None:
        if self.availability not in {"available", "unavailable"}:
            raise ValueError("finance_attempt_estimate_invalid")
        ConfiguredRateCardBinding(
            self.rate_card_id, self.rate_card_version, self.rate_card_digest, self.currency,
        )
        if self.cost_basis != "configured_rate_card_estimate" or self.reason_code not in _ESTIMATE_REASONS:
            raise ValueError("finance_attempt_estimate_invalid")
        if type(self.provider_final) is not bool or self.provider_final:
            raise ValueError("finance_attempt_estimate_invalid")
        if self.availability == "available":
            if self.reason_code != "estimated" or self.amount is None:
                raise ValueError("finance_attempt_estimate_invalid")
            amount_microusd = _counter(self.amount_microusd, optional=False)
            try:
                normalized = decimal_text(self.amount)
                with exact_context():
                    expected = Decimal(normalized) * Decimal(1_000_000)
            except (FinanceValueError, ArithmeticError, InvalidOperation):
                raise ValueError("finance_attempt_estimate_invalid") from None
            if normalized != self.amount or expected != amount_microusd:
                raise ValueError("finance_attempt_estimate_invalid")
        elif self.amount is not None or self.amount_microusd is not None or self.reason_code == "estimated":
            raise ValueError("finance_attempt_estimate_invalid")


def unavailable_estimate(
    binding: ConfiguredRateCardBinding,
    reason_code: str,
) -> ConfiguredRouteEstimate:
    if reason_code not in _ESTIMATE_REASONS - {"estimated"}:
        raise ValueError("finance_attempt_estimate_invalid")
    return ConfiguredRouteEstimate(
        availability="unavailable",
        amount=None,
        amount_microusd=None,
        currency=binding.currency,
        cost_basis="configured_rate_card_estimate",
        reason_code=reason_code,
        rate_card_id=binding.rate_card_id,
        rate_card_version=binding.rate_card_version,
        rate_card_digest=binding.rate_card_digest,
    )


def estimate_configured_route(
    binding: ConfiguredRateCardBinding,
    observation: NativeUsageObservation,
    *,
    input_cost_per_million: object,
    cache_read_cost_per_million: object,
    cache_write_cost_per_million: object,
    output_cost_per_million: object,
) -> ConfiguredRouteEstimate:
    """Calculate one exact configured estimate or retain explicit absence."""

    try:
        validate_native_usage_observation(observation)
        rates = tuple(
            Decimal(str(value))
            for value in (
                input_cost_per_million,
                cache_read_cost_per_million,
                cache_write_cost_per_million,
                output_cost_per_million,
            )
        )
        if any(not rate.is_finite() or rate < 0 for rate in rates):
            raise InvalidOperation
    except (ValueError, InvalidOperation, ArithmeticError):
        return unavailable_estimate(binding, "estimate_outside_precision")

    values: tuple[int | None, int | None, int | None, int | None]
    if observation.provider_schema_id == _PROFILES["openai"]:
        input_total = observation.provider_input_tokens
        cache_read = observation.cache_read_input_tokens
        cache_write = observation.cache_write_input_tokens
        output = observation.provider_output_tokens
        if any(value is None for value in (input_total, cache_read, cache_write, output)):
            return unavailable_estimate(binding, "missing_native_usage")
        assert input_total is not None and cache_read is not None and cache_write is not None
        if cache_read > input_total or cache_write > input_total or cache_read + cache_write > input_total:
            return unavailable_estimate(binding, "invalid_native_usage")
        values = (input_total - cache_read - cache_write, cache_read, cache_write, output)
    elif observation.provider_schema_id == _PROFILES["anthropic"]:
        values = (
            observation.provider_input_tokens,
            observation.cache_read_input_tokens,
            observation.cache_write_input_tokens,
            observation.provider_output_tokens,
        )
        if any(value is None for value in values):
            return unavailable_estimate(binding, "missing_native_usage")
    else:  # Defensive after observation validation.
        return unavailable_estimate(binding, "invalid_native_usage")

    try:
        with exact_context():
            micro = sum(Decimal(value) * rate for value, rate in zip(values, rates))
            # The valuation itself must remain exact, while the contract
            # explicitly permits one final half-even conversion to whole
            # currency microunits. ``to_integral_value`` performs that single
            # rounding step without turning the context's Rounded/Inexact
            # traps into a false precision failure.
            rounded = micro.to_integral_value(rounding=ROUND_HALF_EVEN)
            if rounded < 0 or rounded > MAX_INTEGER:
                raise InvalidOperation
            amount_microusd = int(rounded)
            amount = decimal_text(rounded / Decimal(1_000_000))
    except (FinanceValueError, InvalidOperation, ArithmeticError, ValueError):
        return unavailable_estimate(binding, "estimate_outside_precision")
    return ConfiguredRouteEstimate(
        availability="available",
        amount=amount,
        amount_microusd=amount_microusd,
        currency=binding.currency,
        cost_basis="configured_rate_card_estimate",
        reason_code="estimated",
        rate_card_id=binding.rate_card_id,
        rate_card_version=binding.rate_card_version,
        rate_card_digest=binding.rate_card_digest,
    )


def absent_native_observation(protocol: str, reason_code: str = "provider_usage_absent") -> NativeUsageObservation:
    try:
        profile = _PROFILES[protocol]
    except KeyError:
        raise ValueError("finance_attempt_evidence_invalid") from None
    return NativeUsageObservation(
        provider_schema_id=profile,
        provider_schema_version=1,
        state="absent",
        reason_code=reason_code,
        native_payload_json=None,
        native_payload_digest=None,
    )


def unknown_native_observation(
    protocol: str,
    observation: NativeUsageObservation | None,
    reason_code: str,
) -> NativeUsageObservation:
    """Retain bounded partial evidence without claiming a known outcome."""

    if reason_code not in {
        "stale_pending", "provider_transport_ambiguous", "provider_stream_interrupted",
    }:
        raise ValueError("finance_attempt_evidence_invalid")
    if observation is None or observation.state == "absent":
        return absent_native_observation(protocol, reason_code)
    return NativeUsageObservation(
        **{
            field.name: (
                "partial" if field.name == "state"
                else reason_code if field.name == "reason_code"
                else getattr(observation, field.name)
            )
            for field in fields(observation)
        }
    )


class NativeUsageAccumulator:
    """One bounded provider profile accumulator shared with the v1 parser."""

    def __init__(self, protocol: str):
        if protocol not in _PROFILES:
            raise ValueError("finance_attempt_evidence_invalid")
        self.protocol = protocol
        self._values: dict[str, int | str] = {}
        self._invalid = False
        self._saw_usage = False

    def note_parse_failure(self) -> None:
        self._invalid = True

    def observe_openai_response(self, response: object) -> None:
        if type(response) is not dict:
            self._invalid = True
            return
        self._observe_usage(response.get("usage"), present="usage" in response)
        self._observe_dimension("service_tier", response.get("service_tier"), "service_tier" in response)

    def observe_anthropic_usage(
        self,
        usage: object,
        *,
        present: bool,
        observe_input: bool,
        observe_output: bool,
        replace_output: bool = False,
    ) -> None:
        if replace_output:
            for path in tuple(self._values):
                if path == "output_tokens" or path.startswith("output_tokens_details.") or path.startswith("server_tool_use."):
                    self._values.pop(path, None)
        if not present:
            if replace_output:
                self._invalid = True
            return
        if type(usage) is not dict:
            self._invalid = True
            return
        self._saw_usage = True
        paths = set(_INTEGER_PATHS["anthropic"])
        if not observe_input:
            paths = {path for path in paths if path == "output_tokens" or path.startswith(("output_tokens_details.", "server_tool_use."))}
        if not observe_output:
            paths = {path for path in paths if not (path == "output_tokens" or path.startswith(("output_tokens_details.", "server_tool_use.")))}
        for path in sorted(paths):
            present_value, value = _path_value(usage, path)
            self._observe_integer(path, value, present_value)
        for path in _DIMENSION_PATHS["anthropic"]:
            present_value, value = _path_value(usage, path)
            self._observe_dimension(path, value, present_value)

    def _observe_usage(self, usage: object, *, present: bool) -> None:
        if not present:
            return
        if type(usage) is not dict:
            self._invalid = True
            return
        self._saw_usage = True
        for path in sorted(_INTEGER_PATHS[self.protocol]):
            present_value, value = _path_value(usage, path)
            self._observe_integer(path, value, present_value)

    def _observe_integer(self, path: str, value: object, present: bool) -> None:
        if not present:
            return
        if type(value) is not int or not 0 <= value <= MAX_INTEGER:
            self._values.pop(path, None)
            self._invalid = True
            return
        self._values[path] = value

    def _observe_dimension(self, path: str, value: object, present: bool) -> None:
        if not present:
            return
        allowed = _DIMENSION_PATHS[self.protocol][path]
        try:
            normalized = _identifier(value)
        except ValueError:
            self._values.pop(path, None)
            self._invalid = True
            return
        if allowed is not None and normalized not in allowed:
            self._values.pop(path, None)
            self._invalid = True
            return
        self._values[path] = normalized

    def finish(self) -> NativeUsageObservation:
        if not self._values:
            reason = "provider_usage_invalid" if self._invalid else "provider_usage_absent"
            return absent_native_observation(self.protocol, reason)

        values = dict(self._values)
        normalized, intrinsically_invalid = _normalize_native_values(self.protocol, values)
        invalid = self._invalid or intrinsically_invalid

        payload = _native_payload(values)
        encoded = payload.encode("utf-8")
        required_present = _REQUIRED[self.protocol].issubset(values)
        complete = required_present and not invalid
        state = "complete" if complete else "partial"
        reason = None if complete else (
            "provider_usage_invalid" if invalid else "provider_usage_incomplete"
        )
        return NativeUsageObservation(
            provider_schema_id=_PROFILES[self.protocol],
            provider_schema_version=1,
            state=state,
            reason_code=reason,
            native_payload_json=payload,
            native_payload_digest=hashlib.sha256(encoded).hexdigest(),
            provider_input_tokens=normalized.provider_input_tokens,
            provider_output_tokens=normalized.provider_output_tokens,
            cache_read_input_tokens=normalized.cache_read_input_tokens,
            cache_write_input_tokens=normalized.cache_write_input_tokens,
            cache_write_5m_input_tokens=normalized.cache_write_5m_input_tokens,
            cache_write_1h_input_tokens=normalized.cache_write_1h_input_tokens,
            reasoning_output_tokens=normalized.reasoning_output_tokens,
            total_tokens=normalized.total_tokens,
            billable_input_tokens=normalized.billable_input_tokens,
            billable_output_tokens=normalized.billable_output_tokens,
            server_tool_request_count=normalized.server_tool_request_count,
            provider_service_tier=normalized.provider_service_tier,
            provider_inference_geo=normalized.provider_inference_geo,
        )


def _path_value(value: Mapping[str, object], path: str) -> tuple[bool, object]:
    current: object = value
    members = path.split(".")
    for index, member in enumerate(members):
        if type(current) is not dict:
            # The provider supplied the path prefix with the wrong shape.
            # Preserve presence so the accumulator marks the observation
            # invalid, but never reinterpret that scalar as every nested leaf.
            return True, None
        if member not in current:
            return False, None
        current = current[member]
        if index < len(members) - 1 and type(current) is not dict:
            return True, None
    return True, current


def _int_value(values: Mapping[str, int | str], name: str) -> int | None:
    value = values.get(name)
    return value if type(value) is int else None


def _string_value(values: Mapping[str, int | str], name: str) -> str | None:
    value = values.get(name)
    return value if type(value) is str else None


def _checked_sum(*values: int | None) -> int | None:
    if any(value is None for value in values):
        return None
    result = sum(value for value in values if value is not None)
    return result if result <= MAX_INTEGER else None


def _normalize_native_values(
    protocol: str,
    values: Mapping[str, int | str],
) -> tuple[_NormalizedNativeUsage, bool]:
    provider_input = _int_value(values, "input_tokens")
    provider_output = _int_value(values, "output_tokens")
    cache_read = _int_value(
        values,
        "input_tokens_details.cached_tokens" if protocol == "openai" else "cache_read_input_tokens",
    )
    cache_write = _int_value(
        values,
        "input_tokens_details.cache_write_tokens" if protocol == "openai" else "cache_creation_input_tokens",
    )
    cache_5m = _int_value(values, "cache_creation.ephemeral_5m_input_tokens")
    cache_1h = _int_value(values, "cache_creation.ephemeral_1h_input_tokens")
    reasoning = _int_value(
        values,
        "output_tokens_details.reasoning_tokens" if protocol == "openai" else "output_tokens_details.thinking_tokens",
    )
    invalid = False
    if protocol == "openai":
        total = _int_value(values, "total_tokens")
        if (
            total is not None
            and provider_input is not None
            and provider_output is not None
            and total != provider_input + provider_output
        ):
            total = None
            invalid = True
        if provider_input is not None and any(
            item is not None and item > provider_input for item in (cache_read, cache_write)
        ):
            cache_read = None if cache_read is not None and cache_read > provider_input else cache_read
            cache_write = None if cache_write is not None and cache_write > provider_input else cache_write
            invalid = True
    elif protocol == "anthropic":
        total = _checked_sum(provider_input, cache_read, cache_write, provider_output)
        if all(item is not None for item in (cache_5m, cache_1h)) and cache_write is not None:
            assert cache_5m is not None and cache_1h is not None
            if cache_5m + cache_1h != cache_write:
                cache_5m = cache_1h = None
                invalid = True
        elif cache_write is not None and any(
            item is not None and item > cache_write for item in (cache_5m, cache_1h)
        ):
            cache_5m = None if cache_5m is not None and cache_5m > cache_write else cache_5m
            cache_1h = None if cache_1h is not None and cache_1h > cache_write else cache_1h
            invalid = True
    else:
        raise ValueError("finance_attempt_evidence_invalid")
    search = _int_value(values, "server_tool_use.web_search_requests")
    fetch = _int_value(values, "server_tool_use.web_fetch_requests")
    server_tools = _checked_sum(search, fetch)
    if search is not None and fetch is not None and server_tools is None:
        invalid = True
    return (
        _NormalizedNativeUsage(
            provider_input_tokens=provider_input,
            provider_output_tokens=provider_output,
            cache_read_input_tokens=cache_read,
            cache_write_input_tokens=cache_write,
            cache_write_5m_input_tokens=cache_5m,
            cache_write_1h_input_tokens=cache_1h,
            reasoning_output_tokens=reasoning,
            total_tokens=total,
            billable_input_tokens=None,
            billable_output_tokens=None,
            server_tool_request_count=server_tools,
            provider_service_tier=_string_value(values, "service_tier"),
            provider_inference_geo=_string_value(values, "inference_geo"),
        ),
        invalid,
    )


def _native_payload(values: Mapping[str, int | str]) -> str:
    root: dict[str, object] = {}
    for path, value in sorted(values.items()):
        target = root
        members = path.split(".")
        for member in members[:-1]:
            child = target.setdefault(member, {})
            if type(child) is not dict:
                raise ValueError("finance_attempt_evidence_invalid")
            target = child
        target[members[-1]] = value
    payload = json.dumps(root, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if not 1 <= len(payload.encode("utf-8")) <= MAX_NATIVE_BYTES:
        raise ValueError("finance_attempt_evidence_invalid")
    return payload


_EVENT_FIELDS = frozenset({
    "schema_id", "schema_version", "evidence_event_id", "organization_id",
    "request_attempt_id", "terminal_attempt_event_id", "usage_event_id",
    "terminal_state", "occurred_at", "provider_schema_id", "provider_schema_version",
    "observation_state", "observation_reason_code", "native_payload_json",
    "native_payload_digest", "provider_input_tokens", "provider_output_tokens",
    "cache_read_input_tokens", "cache_write_input_tokens", "cache_write_5m_input_tokens",
    "cache_write_1h_input_tokens", "reasoning_output_tokens", "total_tokens",
    "billable_input_tokens", "billable_output_tokens", "server_tool_request_count",
    "provider_service_tier", "provider_inference_geo", "configured_estimate_availability",
    "configured_estimate_amount", "configured_estimate_microusd",
    "configured_estimate_currency", "configured_estimate_basis",
    "configured_estimate_reason_code", "configured_rate_card_id",
    "configured_rate_card_version", "configured_rate_card_digest", "provider_final",
})


def build_finance_attempt_event(
    *,
    protocol: str,
    organization_id: str,
    request_attempt_id: str,
    terminal_attempt_event_id: str,
    usage_event_id: str | None,
    terminal_state: str,
    occurred_at: str,
    observation: NativeUsageObservation,
    estimate: ConfiguredRouteEstimate,
    binding: ConfiguredRateCardBinding,
    evidence_event_id: str | None = None,
) -> dict[str, object]:
    if (
        _PROFILES.get(protocol) != observation.provider_schema_id
        or estimate.rate_card_id != binding.rate_card_id
        or estimate.rate_card_version != binding.rate_card_version
        or estimate.rate_card_digest != binding.rate_card_digest
        or estimate.currency != binding.currency
    ):
        raise ValueError("finance_attempt_evidence_invalid")
    event: dict[str, object] = {
        "schema_id": FINANCE_ATTEMPT_SCHEMA_ID,
        "schema_version": FINANCE_ATTEMPT_SCHEMA_VERSION,
        "evidence_event_id": evidence_event_id or str(uuid.uuid4()),
        "organization_id": organization_id,
        "request_attempt_id": request_attempt_id,
        "terminal_attempt_event_id": terminal_attempt_event_id,
        "usage_event_id": usage_event_id,
        "terminal_state": terminal_state,
        "occurred_at": occurred_at,
        "provider_schema_id": observation.provider_schema_id,
        "provider_schema_version": observation.provider_schema_version,
        "observation_state": observation.state,
        "observation_reason_code": observation.reason_code,
        "native_payload_json": observation.native_payload_json,
        "native_payload_digest": observation.native_payload_digest,
        **{
            field.name: getattr(observation, field.name)
            for field in fields(observation)
            if field.name.startswith((
                "provider_input", "provider_output", "cache_", "reasoning_", "total_",
                "billable_", "server_tool_", "provider_service_", "provider_inference_",
            ))
        },
        "configured_estimate_availability": estimate.availability,
        "configured_estimate_amount": estimate.amount,
        "configured_estimate_microusd": estimate.amount_microusd,
        "configured_estimate_currency": estimate.currency,
        "configured_estimate_basis": estimate.cost_basis,
        "configured_estimate_reason_code": estimate.reason_code,
        "configured_rate_card_id": binding.rate_card_id,
        "configured_rate_card_version": binding.rate_card_version,
        "configured_rate_card_digest": binding.rate_card_digest,
        "provider_final": estimate.provider_final,
    }
    validate_finance_attempt_event(event)
    return event


def validate_finance_attempt_event(value: Mapping[str, object]) -> None:
    try:
        if type(value) is not dict or set(value) != _EVENT_FIELDS:
            raise ValueError
        if value["schema_id"] != FINANCE_ATTEMPT_SCHEMA_ID or value["schema_version"] != 1:
            raise ValueError
        _identifier(value["evidence_event_id"], uuid_value=True)
        _identifier(value["organization_id"])
        _identifier(value["request_attempt_id"])
        _identifier(value["terminal_attempt_event_id"], uuid_value=True)
        usage_event_id = value["usage_event_id"]
        if usage_event_id is not None:
            _identifier(usage_event_id, uuid_value=True)
        state = value["terminal_state"]
        if state not in _TERMINAL_STATES:
            raise ValueError
        if (state == "outcome_unknown") != (usage_event_id is None):
            raise ValueError
        _timestamp(value["occurred_at"])
        observation = NativeUsageObservation(
            provider_schema_id=value["provider_schema_id"],  # type: ignore[arg-type]
            provider_schema_version=value["provider_schema_version"],  # type: ignore[arg-type]
            state=value["observation_state"],  # type: ignore[arg-type]
            reason_code=value["observation_reason_code"],  # type: ignore[arg-type]
            native_payload_json=value["native_payload_json"],  # type: ignore[arg-type]
            native_payload_digest=value["native_payload_digest"],  # type: ignore[arg-type]
            provider_input_tokens=value["provider_input_tokens"],  # type: ignore[arg-type]
            provider_output_tokens=value["provider_output_tokens"],  # type: ignore[arg-type]
            cache_read_input_tokens=value["cache_read_input_tokens"],  # type: ignore[arg-type]
            cache_write_input_tokens=value["cache_write_input_tokens"],  # type: ignore[arg-type]
            cache_write_5m_input_tokens=value["cache_write_5m_input_tokens"],  # type: ignore[arg-type]
            cache_write_1h_input_tokens=value["cache_write_1h_input_tokens"],  # type: ignore[arg-type]
            reasoning_output_tokens=value["reasoning_output_tokens"],  # type: ignore[arg-type]
            total_tokens=value["total_tokens"],  # type: ignore[arg-type]
            billable_input_tokens=value["billable_input_tokens"],  # type: ignore[arg-type]
            billable_output_tokens=value["billable_output_tokens"],  # type: ignore[arg-type]
            server_tool_request_count=value["server_tool_request_count"],  # type: ignore[arg-type]
            provider_service_tier=value["provider_service_tier"],  # type: ignore[arg-type]
            provider_inference_geo=value["provider_inference_geo"],  # type: ignore[arg-type]
        )
        binding = ConfiguredRateCardBinding(
            value["configured_rate_card_id"],  # type: ignore[arg-type]
            value["configured_rate_card_version"],  # type: ignore[arg-type]
            value["configured_rate_card_digest"],  # type: ignore[arg-type]
            value["configured_estimate_currency"],  # type: ignore[arg-type]
        )
        estimate = ConfiguredRouteEstimate(
            value["configured_estimate_availability"],  # type: ignore[arg-type]
            value["configured_estimate_amount"],  # type: ignore[arg-type]
            value["configured_estimate_microusd"],  # type: ignore[arg-type]
            value["configured_estimate_currency"],  # type: ignore[arg-type]
            value["configured_estimate_basis"],  # type: ignore[arg-type]
            value["configured_estimate_reason_code"],  # type: ignore[arg-type]
            value["configured_rate_card_id"],  # type: ignore[arg-type]
            value["configured_rate_card_version"],  # type: ignore[arg-type]
            value["configured_rate_card_digest"],  # type: ignore[arg-type]
            value["provider_final"],  # type: ignore[arg-type]
        )
        validate_native_usage_observation(observation)
        if (
            estimate.rate_card_id != binding.rate_card_id
            or estimate.rate_card_version != binding.rate_card_version
            or estimate.rate_card_digest != binding.rate_card_digest
            or type(value["provider_final"]) is not bool
        ):
            raise ValueError
        if state == "outcome_unknown" and observation.state == "complete":
            raise ValueError
        if state == "outcome_unknown":
            if (
                estimate.availability != "unavailable"
                or estimate.reason_code != "attempt_outcome_unknown"
            ):
                raise ValueError
        elif estimate.reason_code == "attempt_outcome_unknown":
            raise ValueError
    except (KeyError, TypeError, ValueError, FinanceValueError, UnicodeError):
        raise ValueError("finance_attempt_evidence_invalid") from None


def finance_attempt_event_from_row(row: Mapping[str, object]) -> dict[str, object]:
    """Rebuild the exact finite audit source from one trusted storage row."""

    event = {
        name: row[
            "event_schema_id" if name == "schema_id"
            else "event_schema_version" if name == "schema_version"
            else name
        ]
        for name in _EVENT_FIELDS
    }
    provider_final = event["provider_final"]
    if type(provider_final) is int and provider_final in {0, 1}:
        event["provider_final"] = bool(provider_final)
    occurred_at = event["occurred_at"]
    if isinstance(occurred_at, datetime):
        event["occurred_at"] = (
            occurred_at.astimezone(timezone.utc).isoformat()
            if occurred_at.tzinfo is not None
            else occurred_at.isoformat()
        )
    validate_finance_attempt_event(event)
    return event


def finance_attempt_storage_row(event: Mapping[str, object]) -> dict[str, object]:
    """Return the table row plus the exact canonical audit-source bytes."""

    validate_finance_attempt_event(event)
    canonical = json.dumps(
        dict(event), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    )
    row = {
        ("event_schema_id" if name == "schema_id" else "event_schema_version" if name == "schema_version" else name): value
        for name, value in event.items()
    }
    row["evidence_json"] = canonical
    return row
