"""Content-free inspection of known provider prompt-cache request controls.

Hormuz deliberately does not create, rewrite, or persist provider cache
directives.  This module only recognizes the documented *field names* that a
client supplied so policy can allow or deny that explicit request before any
provider egress.  It never returns a value, JSON path, prompt fragment, or
cache key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .config import ProviderCacheCapability, ProviderCachePolicy


# These are static protocol field names, not customer-provided metadata.  They
# are intentionally narrow: a new provider feature must be reviewed and added
# here before a restrictive policy can claim to govern it.
_KNOWN_EXPLICIT_CACHE_FIELDS = {
    "openai": {
        "prompt_cache_key": "openai.prompt_cache_key",
        "prompt_cache_retention": "openai.prompt_cache_retention",
        "prompt_cache_options": "openai.prompt_cache_options",
        "prompt_cache_breakpoint": "openai.prompt_cache_breakpoint",
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
    openai_explicit_without_breakpoints: bool = False

    @property
    def requested(self) -> bool:
        return bool(self.controls)


@dataclass(frozen=True)
class ProviderCacheDecision:
    """A content-free cache-governance result for one provider request."""

    allowed: bool
    reason: str


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
    openai_explicit_option_count = 0
    openai_invalid_option = False
    openai_breakpoint_requested = False
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
                    if label == "openai.prompt_cache_options":
                        if (
                            isinstance(child, dict)
                            and set(child) == {"mode"}
                            and child.get("mode") == "explicit"
                        ):
                            openai_explicit_option_count += 1
                        else:
                            openai_invalid_option = True
                    elif label == "openai.prompt_cache_breakpoint":
                        openai_breakpoint_requested = True
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)
    root_explicit_option = (
        isinstance(payload, dict)
        and isinstance(payload.get("prompt_cache_options"), dict)
        and set(payload["prompt_cache_options"]) == {"mode"}
        and payload["prompt_cache_options"].get("mode") == "explicit"
    )
    return ProviderCacheInspection(
        controls=tuple(sorted(found)),
        openai_explicit_without_breakpoints=(
            protocol == "openai"
            and root_explicit_option
            and openai_explicit_option_count == 1
            and not openai_invalid_option
            and not openai_breakpoint_requested
            and "openai.prompt_cache_key" not in found
            and "openai.prompt_cache_retention" not in found
        ),
    )


def evaluate_provider_cache_request(
    *,
    policy: ProviderCachePolicy,
    capability: ProviderCacheCapability | None,
    protocol: str,
    upstream_model: str,
    operation: str,
    client: str,
    model_alias: str,
    inspection: ProviderCacheInspection,
    today: date | None = None,
) -> ProviderCacheDecision:
    """Evaluate a provider-native cache policy without retaining request data.

    ``disabled`` is an administrator's strict no-provider-cache requirement.
    It permits only a reviewed capability's exact client-supplied opt-out; it
    never injects, strips, or rewrites a provider directive.  Every unknown,
    stale, unsupported, or structurally incomplete case fails before egress.
    """

    if policy.strict_no_cache_required:
        maximum_age_days = policy.capability_max_age_days
        if maximum_age_days is None:  # pragma: no cover - configuration invariant
            return ProviderCacheDecision(False, "strict_policy_invalid")
        current_day = date.today() if today is None else today
        if capability is None:
            return ProviderCacheDecision(False, "capability_unknown")
        if not capability.review_is_current(
            maximum_age_days=maximum_age_days,
            today=current_day,
        ):
            return ProviderCacheDecision(False, "capability_stale")
        if (
            capability.protocol != protocol
            or capability.upstream_model != upstream_model
        ):
            return ProviderCacheDecision(False, "capability_route_mismatch")
        if operation not in capability.operations:
            return ProviderCacheDecision(False, "capability_operation_unsupported")
        if (
            capability.strict_no_cache
            != "openai_explicit_without_breakpoints"
            or protocol != "openai"
        ):
            return ProviderCacheDecision(False, "strict_no_cache_unsupported")
        if not inspection.complete:
            return ProviderCacheDecision(False, "inspection_incomplete")
        if not inspection.openai_explicit_without_breakpoints:
            return ProviderCacheDecision(False, "strict_no_cache_not_verified")
        return ProviderCacheDecision(True, "strict_no_cache_verified")

    restrictive = (
        not policy.explicit_requests_allowed
        or policy.allowed_clients is not None
        or policy.allowed_models is not None
    )
    if inspection.requested and not policy.allows(
        client=client,
        model_alias=model_alias,
    ):
        return ProviderCacheDecision(False, "explicit_control_denied")
    if not inspection.complete and restrictive:
        return ProviderCacheDecision(False, "inspection_incomplete")
    return ProviderCacheDecision(True, "allowed")
