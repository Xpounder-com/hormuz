"""Provider-free configuration candidates for prospective account binding.

These immutable values carry only operator-supplied metadata references. A
candidate is not a registered binding, account-ownership proof or permission
to match financial evidence. Registration/source validation and durable
attempt capture remain unimplemented. No credentials, storage or provider
lookups belong in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit


MAX_FINANCE_ACCOUNT_BINDINGS = 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_IDENTITY_FIELDS = frozenset({
    "upstream_reference_id", "upstream_reference_version", "transport_profile",
    "inference_credential_reference_id", "inference_credential_reference_version",
})
_BINDING_FIELDS = frozenset({
    "organization_id", "upstream_reference_id", "binding_id", "binding_version",
})
_TRANSPORTS = {
    "openai": ("openai.first-party.v1", "api.openai.com"),
    "anthropic": ("anthropic.first-party.v1", "api.anthropic.com"),
}


@dataclass(frozen=True)
class UnavailableFinance:
    """A fixed preflight reason; never an inference admission decision."""

    reason: str


@dataclass(frozen=True)
class FinanceIdentity:
    upstream_reference_id: str
    upstream_reference_version: int
    transport_profile: str
    inference_credential_reference_id: str
    inference_credential_reference_version: int


@dataclass(frozen=True)
class ConfiguredFinanceBinding:
    organization_id: str
    upstream_reference_id: str
    binding_id: str
    binding_version: int


@dataclass(frozen=True)
class FinanceAccountBindings:
    """Bounded copied values; invalid keyed rows still participate in ambiguity."""

    entries: tuple[ConfiguredFinanceBinding, ...] = ()
    invalid_keys: tuple[tuple[str, str], ...] = ()
    invalid: bool = False


@dataclass(frozen=True)
class FinanceAccountCandidate:
    """References requiring future tenant-bound registration/source validation."""

    identity: FinanceIdentity
    binding: ConfiguredFinanceBinding


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _version(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


def parse_finance_identity(value: object) -> FinanceIdentity | UnavailableFinance:
    """Parse a present optional object without retaining invalid input values."""

    if (
        not isinstance(value, dict)
        or set(value) != _IDENTITY_FIELDS
        or not all(_identifier(value.get(name)) for name in (
            "upstream_reference_id", "transport_profile", "inference_credential_reference_id",
        ))
        or not all(_version(value.get(name)) for name in (
            "upstream_reference_version", "inference_credential_reference_version",
        ))
    ):
        return UnavailableFinance("binding_invalid")
    return FinanceIdentity(**value)


def parse_finance_account_bindings(value: object) -> FinanceAccountBindings:
    """An unidentifiable malformed row disables selection, never ordinary routing.

    When both selection keys are valid, retain only those keys so an invalid
    tenant/upstream row cannot silently fall back to another registration.
    Unknown raw fields and values are discarded.
    """

    if not isinstance(value, list) or len(value) > MAX_FINANCE_ACCOUNT_BINDINGS:
        return FinanceAccountBindings(invalid=True)
    entries = []
    invalid_keys = []
    for row in value:
        if (
            not isinstance(row, dict)
            or not _identifier(row.get("organization_id"))
            or not _identifier(row.get("upstream_reference_id"))
        ):
            return FinanceAccountBindings(invalid=True)
        if (
            set(row) != _BINDING_FIELDS
            or not _identifier(row.get("binding_id"))
            or not _version(row.get("binding_version"))
        ):
            invalid_keys.append((row["organization_id"], row["upstream_reference_id"]))
        else:
            entries.append(ConfiguredFinanceBinding(**row))
    return FinanceAccountBindings(tuple(entries), tuple(invalid_keys))


def _first_party_transport(protocol: str, base_url: str, profile: str) -> bool:
    expected = _TRANSPORTS.get(protocol)
    if expected is None or profile != expected[0]:
        return False
    # urlsplit strips some control characters. Do not normalize malformed input
    # into first-party identity or allow user-info, query or fragment ambiguity.
    if not isinstance(base_url, str) or any(
        ord(char) <= 32 or ord(char) == 127 or char in "\\?#" for char in base_url
    ):
        return False
    try:
        parsed = urlsplit(base_url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == expected[1]
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


def select_finance_account(
    *,
    organization_id: str,
    protocol: str,
    base_url: str,
    identity: FinanceIdentity | UnavailableFinance,
    bindings: FinanceAccountBindings | None,
) -> FinanceAccountCandidate | UnavailableFinance:
    """Select for the server-authenticated tenant and configured origin/profile.

    No rate card, model, request body, collection credential or provider lookup
    supplies identity. Even a candidate is still unverified: this function
    cannot check registration currency, revocation, scope, source versions or
    the final network destination after redirects.
    """

    if isinstance(identity, UnavailableFinance):
        return identity
    if not isinstance(identity, FinanceIdentity):
        return UnavailableFinance("binding_invalid")
    if not _first_party_transport(protocol, base_url, identity.transport_profile):
        return UnavailableFinance("unsupported_transport")
    if bindings is None:
        return UnavailableFinance("not_configured")
    if not isinstance(bindings, FinanceAccountBindings) or bindings.invalid:
        return UnavailableFinance("binding_invalid")
    key = (organization_id, identity.upstream_reference_id)
    matches = tuple(row for row in bindings.entries
                    if (row.organization_id, row.upstream_reference_id) == key)
    invalid_count = bindings.invalid_keys.count(key)
    if len(matches) + invalid_count > 1:
        return UnavailableFinance("binding_ambiguous")
    if invalid_count:
        return UnavailableFinance("binding_invalid")
    if not matches:
        return UnavailableFinance("binding_missing")
    return FinanceAccountCandidate(identity, matches[0])
