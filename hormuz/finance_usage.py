"""Pure first-party aggregate usage normalization, separate from v1 Usage.

Only an allowlist of numeric metadata is retained. Missing/null is unknown,
not zero. Source authentication and complete-page collection belong to the
future adapters; these values alone are neither live evidence nor invoices.
"""

from __future__ import annotations

from dataclasses import dataclass

from .finance_values import FinanceValueError


MAX_COUNT = 9223372036854775807
OPENAI_COUNTS = (
    "input_tokens", "output_tokens", "num_model_requests", "input_cached_tokens",
    "input_cache_write_tokens", "input_uncached_tokens", "input_text_tokens",
    "input_image_tokens", "input_audio_tokens", "input_cached_text_tokens",
    "input_cached_image_tokens", "input_cached_audio_tokens", "output_text_tokens",
    "output_image_tokens", "output_audio_tokens",
)
ANTHROPIC_COUNTS = (
    "uncached_input_tokens", "cache_read_input_tokens", "cache_creation.ephemeral_5m_input_tokens",
    "cache_creation.ephemeral_1h_input_tokens", "output_tokens", "server_tool_use.web_search_requests",
)
NORMALIZED_COUNTS = (
    "input_tokens", "output_tokens", "uncached_input_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_write_5m_tokens", "cache_write_1h_tokens", "reasoning_tokens", "billable_tokens",
    "request_count", "total_tokens",
)


def _count(value):
    if value is not None and (type(value) is not int or not 0 <= value <= MAX_COUNT):
        raise FinanceValueError("finance_invalid_usage")
    return value


def _sum(*values):
    if any(value is None for value in values):
        return None
    return _count(sum(values))


def _bounded_parts(total, parts, *, complete=False):
    known = [value for value in parts if value is not None]
    subtotal = _sum(*known)
    if total is not None and (subtotal > total or (complete and len(known) == len(parts) and subtotal != total)):
        raise FinanceValueError("finance_invalid_usage")


def _normalized(provider, native):
    counts = dict.fromkeys(NORMALIZED_COUNTS)
    if provider == "openai":
        total, read, write, uncached = (native[name] for name in (
            "input_tokens", "input_cached_tokens", "input_cache_write_tokens", "input_uncached_tokens"))
        _bounded_parts(total, (read, write, uncached), complete=True)
        if uncached is None and all(value is not None for value in (total, read, write)):
            uncached = total - read - write
        # Uncached modalities and cached modalities are distinct subcategories;
        # neither is added on top of the already inclusive input total.
        _bounded_parts(uncached, tuple(native[f"input_{kind}_tokens"] for kind in ("text", "image", "audio")), complete=True)
        _bounded_parts(read, tuple(native[f"input_cached_{kind}_tokens"] for kind in ("text", "image", "audio")), complete=True)
        _bounded_parts(native["output_tokens"], tuple(native[f"output_{kind}_tokens"] for kind in ("text", "image", "audio")), complete=True)
        counts.update(input_tokens=total, uncached_input_tokens=uncached, cache_read_tokens=read,
                      cache_write_tokens=write, request_count=native["num_model_requests"])
    elif provider == "anthropic":
        uncached, read, short, long = (native[name] for name in (
            "uncached_input_tokens", "cache_read_input_tokens", "cache_creation.ephemeral_5m_input_tokens",
            "cache_creation.ephemeral_1h_input_tokens"))
        counts.update(input_tokens=_sum(uncached, read, short, long), uncached_input_tokens=uncached,
                      cache_read_tokens=read, cache_write_tokens=_sum(short, long),
                      cache_write_5m_tokens=short, cache_write_1h_tokens=long)
    else:
        raise FinanceValueError("finance_unsupported_provider")
    counts["output_tokens"] = native["output_tokens"]
    counts["total_tokens"] = _sum(counts["input_tokens"], counts["output_tokens"])
    # Neither source profile reports reasoning or billable-token totals. Do
    # not invent them from tokens, request counts, successful calls, or zero.
    return tuple((name, _count(counts[name])) for name in NORMALIZED_COUNTS)


@dataclass(frozen=True)
class UsageVector:
    provider: str
    native_counts: tuple[tuple[str, int | None], ...]
    normalized_counts: tuple[tuple[str, int | None], ...]

    def __post_init__(self) -> None:
        self.verify()

    def verify(self) -> None:
        names = OPENAI_COUNTS if self.provider == "openai" else ANTHROPIC_COUNTS if self.provider == "anthropic" else ()
        if (not names or type(self.native_counts) is not tuple or type(self.normalized_counts) is not tuple
                or len(self.native_counts) != len(names)):
            raise FinanceValueError("finance_invalid_usage")
        for item, name in zip(self.native_counts, names):
            if type(item) is not tuple or len(item) != 2 or item[0] != name:
                raise FinanceValueError("finance_invalid_usage")
            _count(item[1])
        if self.normalized_counts != _normalized(self.provider, dict(self.native_counts)):
            raise FinanceValueError("finance_invalid_usage")
        # Equality alone would accept booleans as ints or mutable inner lists.
        for item in self.normalized_counts:
            if type(item) is not tuple or len(item) != 2:
                raise FinanceValueError("finance_invalid_usage")
            _count(item[1])

    def count(self, name: str) -> int | None:
        if name not in NORMALIZED_COUNTS:
            raise FinanceValueError("finance_invalid_usage")
        return dict(self.normalized_counts)[name]


def normalize_provider_usage(provider: str, row: object) -> UsageVector:
    if type(row) is not dict:
        raise FinanceValueError("finance_invalid_usage")
    if provider == "openai":
        if row.get("object") != "organization.usage.completions.result":
            raise FinanceValueError("finance_invalid_usage")
        native = tuple((name, _count(row.get(name))) for name in OPENAI_COUNTS)
    elif provider == "anthropic":
        if not any(name in row for name in ("uncached_input_tokens", "cache_read_input_tokens", "cache_creation")):
            raise FinanceValueError("finance_invalid_usage")
        values = []
        for name in ANTHROPIC_COUNTS:
            if "." in name:
                parent, child = name.split(".")
                nested = row.get(parent)
                if nested is not None and type(nested) is not dict:
                    raise FinanceValueError("finance_invalid_usage")
                allowed = {field.split(".")[1] for field in ANTHROPIC_COUNTS if field.startswith(parent + ".")}
                if nested is not None and set(nested) - allowed:
                    # An unknown cache lifetime/tool cannot vanish while known
                    # categories are presented as a complete input or cost.
                    raise FinanceValueError("finance_invalid_usage")
                value = nested.get(child) if nested is not None else None
            else:
                value = row.get(name)
            values.append((name, _count(value)))
        native = tuple(values)
    else:
        raise FinanceValueError("finance_unsupported_provider")
    result = UsageVector(provider, native, _normalized(provider, dict(native)))
    return result
