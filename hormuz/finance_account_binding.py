"""Provider-free configuration candidates for prospective account binding.

These immutable values carry only operator-supplied metadata references. A
candidate is not a registered binding, account-ownership proof or permission
to match financial evidence. Registration/source validation and durable
attempt capture remain unimplemented. No credentials, storage or provider
lookups belong in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlsplit

from .audit_chain import AuditChainError, canonical_json_bytes


MAX_FINANCE_ACCOUNT_BINDINGS = 1024
MAX_ACCOUNT_BINDING_REQUEST_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
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
_REGISTRATION_FIELDS = frozenset({
    "schema_id", "schema_version", "binding_id", "expected_version",
    "upstream_reference_id", "upstream_reference_version", "transport_profile",
    "inference_credential_reference_id", "inference_credential_reference_version",
    "source_binding", "state", "reason_code",
})
_SOURCE_BINDING_FIELDS = frozenset({"binding_id", "version", "content_digest"})


class AccountBindingRequestError(ValueError):
    """Fixed, content-free validation failure for dormant registration intake."""

    def __init__(self) -> None:
        self.code = "invalid_request"
        super().__init__(self.code)


@dataclass(frozen=True, repr=False)
class AccountBindingRegistrationRequest:
    """Validated operator metadata, not a registration or authority grant."""

    organization_id: str
    binding_id: str
    expected_version: int | None
    upstream_reference_id: str
    upstream_reference_version: int
    transport_profile: str
    inference_credential_reference_id: str
    inference_credential_reference_version: int
    source_binding_id: str
    source_binding_version: int
    source_binding_digest: str
    state: str
    reason_code: str
    request_digest: str

    def __repr__(self) -> str:
        return "AccountBindingRegistrationRequest(<metadata>)"


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


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AccountBindingRequestError()
        result[key] = value
    return result


def _reject_json_number(_value: str) -> object:
    # The closed request shape has integers only. Reject every floating-point
    # spelling, including non-finite values, before any canonical digest exists.
    raise AccountBindingRequestError()


def parse_account_binding_registration_request(
    payload: bytes, *, organization_id: str,
) -> AccountBindingRegistrationRequest:
    """Normalize a future registration request without enabling registration.

    The caller must supply the already authenticated, server-derived tenant.
    This pure function does not authorize a principal, inspect credentials,
    resolve a source row, write storage, or access a provider.
    """

    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_ACCOUNT_BINDING_REQUEST_BYTES
        or not _identifier(organization_id)
    ):
        raise AccountBindingRequestError()
    try:
        request = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_float=_reject_json_number,
            parse_constant=_reject_json_number,
        )
    except AccountBindingRequestError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise AccountBindingRequestError() from None
    if type(request) is not dict or set(request) != _REGISTRATION_FIELDS:
        raise AccountBindingRequestError()
    source = request["source_binding"]
    if (
        request["schema_id"] != "hormuz.finance-account-binding-request"
        or type(request["schema_version"]) is not int
        or request["schema_version"] != 1
        or not all(_identifier(request[name]) for name in (
            "binding_id", "upstream_reference_id", "inference_credential_reference_id",
        ))
        or not all(_version(request[name]) for name in (
            "upstream_reference_version", "inference_credential_reference_version",
        ))
        or request["transport_profile"] not in (
            "openai.first-party.v1", "anthropic.first-party.v1",
        )
        or type(source) is not dict
        or set(source) != _SOURCE_BINDING_FIELDS
        or not _identifier(source["binding_id"])
        or not _version(source["version"])
        or not isinstance(source["content_digest"], str)
        or _SHA256.fullmatch(source["content_digest"]) is None
    ):
        raise AccountBindingRequestError()
    expected = request["expected_version"]
    if expected is None:
        if (request["state"], request["reason_code"]) != ("active", "created"):
            raise AccountBindingRequestError()
    elif (
        not _version(expected)
        or (request["state"], request["reason_code"])
        not in (("active", "replaced"), ("revoked", "revoked"))
    ):
        raise AccountBindingRequestError()
    try:
        digest = hashlib.sha256(canonical_json_bytes({
            "organization_id": organization_id,
            "request": request,
        })).hexdigest()
    except AuditChainError:
        raise AccountBindingRequestError() from None
    return AccountBindingRegistrationRequest(
        organization_id=organization_id,
        binding_id=request["binding_id"],
        expected_version=expected,
        upstream_reference_id=request["upstream_reference_id"],
        upstream_reference_version=request["upstream_reference_version"],
        transport_profile=request["transport_profile"],
        inference_credential_reference_id=request["inference_credential_reference_id"],
        inference_credential_reference_version=request["inference_credential_reference_version"],
        source_binding_id=source["binding_id"],
        source_binding_version=source["version"],
        source_binding_digest=source["content_digest"],
        state=request["state"],
        reason_code=request["reason_code"],
        request_digest=digest,
    )


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
    successful provider delivery.
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
