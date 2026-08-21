"""Content-free inspection of known provider prompt-cache request controls.

Hormuz deliberately does not create, rewrite, or persist provider cache
directives.  This module only recognizes the documented *field names* that a
client supplied so policy can allow or deny that explicit request before any
provider egress.  It never returns a value, JSON path, prompt fragment, or
cache key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# These are static protocol field names, not customer-provided metadata.  They
# are intentionally narrow: a new provider feature must be reviewed and added
# here before a restrictive policy can claim to govern it.
_KNOWN_EXPLICIT_CACHE_FIELDS = {
    "openai": {
        "prompt_cache_key": "openai.prompt_cache_key",
        "prompt_cache_retention": "openai.prompt_cache_retention",
        "prompt_cache_options": "openai.prompt_cache_options",
    },
    "anthropic": {
        "cache_control": "anthropic.cache_control",
    },
}

# Bound structural inspection even when a client submits an unusually shaped
# but otherwise valid JSON request.  A restrictive policy fails closed if this
# bound is reached; an allow policy continues to preserve native compatibility.
MAX_CACHE_POLICY_INSPECTION_NODES = 100_000


@dataclass(frozen=True)
class ProviderCacheInspection:
    """Known explicit controls found without retaining their values."""

    controls: tuple[str, ...] = ()
    complete: bool = True

    @property
    def requested(self) -> bool:
        return bool(self.controls)


def inspect_explicit_cache_controls(
    protocol: str,
    payload: object,
) -> ProviderCacheInspection:
    """Return a bounded, metadata-only view of known cache-control fields.

    Unknown protocols and provider fields are deliberately not interpreted as
    a cache opt-in.  The caller must document that limitation rather than
    treating an unrecognized future field as governed behavior.
    """

    known_fields = _KNOWN_EXPLICIT_CACHE_FIELDS.get(protocol)
    if known_fields is None:
        return ProviderCacheInspection()

    pending: list[object] = [payload]
    found: set[str] = set()
    inspected = 0
    while pending:
        value = pending.pop()
        inspected += 1
        if inspected > MAX_CACHE_POLICY_INSPECTION_NODES:
            return ProviderCacheInspection(
                controls=tuple(sorted(found)),
                complete=False,
            )
        if isinstance(value, dict):
            for key, child in value.items():
                label = known_fields.get(key) if isinstance(key, str) else None
                if label is not None:
                    found.add(label)
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)
    return ProviderCacheInspection(controls=tuple(sorted(found)))
