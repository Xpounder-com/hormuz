"""Immutable qualification-scope contracts for the signed Mac pilot."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


FULL_DUAL_PROVIDER = "full_dual_provider"
CODEX_OPENAI = "codex_openai"
DEFAULT_QUALIFICATION_SCOPE = FULL_DUAL_PROVIDER


class MacPilotScopeError(ValueError):
    """A Mac pilot scope selector is malformed, unsupported, or inconsistent."""


@dataclass(frozen=True, slots=True)
class MacPilotScopeContract:
    name: str
    schema_version: int
    claim_scope: str
    gateway_profile: str
    provider_protocols: tuple[str, ...]
    client_names: tuple[str, ...]


_SCOPE_CONTRACTS: dict[str, MacPilotScopeContract] = {
    FULL_DUAL_PROVIDER: MacPilotScopeContract(
        name=FULL_DUAL_PROVIDER,
        schema_version=1,
        claim_scope="signed_macos_controlled_external_pilot_readiness",
        gateway_profile="external_pilot",
        provider_protocols=("anthropic", "openai"),
        client_names=("codex", "claude-code"),
    ),
    CODEX_OPENAI: MacPilotScopeContract(
        name=CODEX_OPENAI,
        schema_version=2,
        claim_scope=(
            "signed_macos_codex_openai_controlled_external_pilot_readiness"
        ),
        gateway_profile="external_pilot_openai",
        provider_protocols=("openai",),
        client_names=("codex",),
    ),
}

SCOPE_CONTRACTS: Mapping[str, MacPilotScopeContract] = MappingProxyType(
    _SCOPE_CONTRACTS
)
QUALIFICATION_SCOPE_CHOICES = tuple(SCOPE_CONTRACTS)


def resolve_requested_scope(
    value: object = DEFAULT_QUALIFICATION_SCOPE,
) -> MacPilotScopeContract:
    """Resolve an explicit request without coercing or guessing its value."""

    if not isinstance(value, str) or value not in SCOPE_CONTRACTS:
        raise MacPilotScopeError("qualification_scope_invalid")
    return SCOPE_CONTRACTS[value]


def resolve_evidence_scope(
    schema_version: object,
    *,
    qualification_scope_present: bool,
    qualification_scope: object = None,
) -> MacPilotScopeContract:
    """Resolve the only supported evidence representation for each schema."""

    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise MacPilotScopeError("schema_version_invalid")
    if schema_version == 1:
        if qualification_scope_present:
            raise MacPilotScopeError("qualification_scope_unexpected")
        return SCOPE_CONTRACTS[FULL_DUAL_PROVIDER]
    if schema_version == 2:
        if not qualification_scope_present:
            raise MacPilotScopeError("qualification_scope_missing")
        if qualification_scope != CODEX_OPENAI:
            raise MacPilotScopeError("qualification_scope_invalid")
        return SCOPE_CONTRACTS[CODEX_OPENAI]
    raise MacPilotScopeError("schema_version_invalid")


def require_matching_scope(
    requested: MacPilotScopeContract,
    evidence: MacPilotScopeContract,
) -> MacPilotScopeContract:
    """Reject mixed-scope evidence before callers authenticate its children."""

    if requested != evidence:
        raise MacPilotScopeError("qualification_scope_mismatch")
    return evidence
