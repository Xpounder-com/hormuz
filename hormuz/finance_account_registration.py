"""Dormant, provider-free intake for prospective account registration.

The caller must authenticate and authorize the tenant before reading a request
file and passing its bytes here. This module validates metadata only: it does
not register a binding, read credentials, access storage or call providers.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from .audit_chain import AuditChainError, canonical_json_bytes
from .finance_account_binding import _identifier, _version


MAX_ACCOUNT_BINDING_REQUEST_BYTES = 64 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
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
    """Normalize metadata using an already authenticated, server-derived tenant."""

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
