"""Dormant account-registration persistence kernel for the v8 successor.

The caller must authenticate before reading the request, then supply an
already locked tenant transaction, a reauthorization callback and a finite
audit-source append callback. No runtime path imports this module. The schema,
audit-source extension and PostgreSQL grants remain unapproved proposals;
this kernel alone cannot register an account on a deployed gateway.
It does not write attempt sidecars or infer accounts for historical attempts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable, Mapping
from uuid import uuid4

from .audit_chain import AuditChainError, canonical_json_bytes, canonical_json_text
from .finance_account_binding import FinanceAccountCandidate, _identifier
from .finance_account_registration import (
    AccountBindingMatchError,
    AccountBindingRegistrationRequest,
    match_active_account_binding_source,
)
from .finance_collection_repository import (
    FinanceCollectionError,
    SourceBindingVersion,
    _binding_from_row,
)


REGISTRATION_TABLE = "portfolio_finance_account_binding_versions"
_SCHEMA_ID = "hormuz.finance-account-binding-version"
_RECEIPT_ID = "hormuz.finance-account-binding-receipt"
_ROW_COLUMNS = (
    "organization_id", "binding_id", "version", "binding_event_id",
    "previous_version", "binding_state", "reason_code",
    "upstream_reference_id", "upstream_reference_version", "transport_profile",
    "inference_credential_reference_id", "inference_credential_reference_version",
    "source_binding_id", "source_binding_version", "source_binding_digest",
    "provider", "provider_account_fingerprint", "scope_kind",
    "scope_fingerprints_json", "fingerprint_key_version", "content_digest",
    "request_digest", "registered_by", "registered_at", "evidence_json",
)
_CONTENT_COLUMNS = tuple(name for name in _ROW_COLUMNS if name not in {
    "content_digest", "request_digest", "binding_event_id", "registered_by",
    "registered_at", "evidence_json",
})
_INTEGER_COLUMNS = frozenset({
    "version", "upstream_reference_version", "inference_credential_reference_version",
    "source_binding_version", "fingerprint_key_version",
})
_TEXT_COLUMNS = frozenset(_ROW_COLUMNS) - _INTEGER_COLUMNS - {
    "previous_version", "evidence_json",
}


class AccountBindingStorageError(ValueError):
    """Fixed, content-free registration result; database details stay private."""

    def __init__(self, code: str) -> None:
        if code not in {"binding_conflict", "unavailable"}:
            raise ValueError("account_binding_storage_error_invalid")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, repr=False)
class AccountBindingRegistrationReceipt:
    schema_id: str
    schema_version: int
    organization_id: str
    binding_id: str
    version: int
    binding_event_id: str
    content_digest: str
    binding_state: str
    registered_at: str

    def __repr__(self) -> str:
        return "AccountBindingRegistrationReceipt(<metadata>)"


def _canonical(value: object) -> str:
    try:
        return canonical_json_text(value)
    except AuditChainError:
        raise AccountBindingStorageError("unavailable") from None


def _digest(value: object) -> str:
    try:
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except AuditChainError:
        raise AccountBindingStorageError("unavailable") from None


def _receipt_from_row(row: Mapping[str, object]) -> AccountBindingRegistrationReceipt:
    """Reject a corrupted replay/current row instead of returning authority."""

    try:
        if set(row) != set(_ROW_COLUMNS):
            raise ValueError
        if (
            any(type(row[name]) is not int for name in _INTEGER_COLUMNS)
            or any(type(row[name]) is not str for name in _TEXT_COLUMNS)
            or (row["previous_version"] is not None
                and type(row["previous_version"]) is not int)
            or type(row["evidence_json"]) is not str
        ):
            raise ValueError
        event = json.loads(str(row["evidence_json"]))
        if (
            not isinstance(event, dict)
            or set(event) != (set(_ROW_COLUMNS) - {"evidence_json"}) | {
                "schema_id", "schema_version"
            }
            or event.get("schema_id") != _SCHEMA_ID
            or type(event.get("schema_version")) is not int
            or event["schema_version"] != 1
            or _canonical(event) != row["evidence_json"]
            or any(
                type(event[name]) is not type(row[name]) or event[name] != row[name]
                for name in _ROW_COLUMNS if name != "evidence_json"
            )
            or _digest({name: row[name] for name in _CONTENT_COLUMNS}) != row["content_digest"]
        ):
            raise ValueError
        return AccountBindingRegistrationReceipt(
            _RECEIPT_ID, 1, str(row["organization_id"]), str(row["binding_id"]),
            int(row["version"]), str(row["binding_event_id"]),
            str(row["content_digest"]), str(row["binding_state"]),
            str(row["registered_at"]),
        )
    except (AuditChainError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise AccountBindingStorageError("unavailable") from None


def _source_from_row(row: Mapping[str, object] | None) -> SourceBindingVersion:
    if row is None:
        raise AccountBindingStorageError("binding_conflict")
    try:
        return _binding_from_row(row)
    except FinanceCollectionError:
        raise AccountBindingStorageError("unavailable") from None


def append_active_account_registration(
    sql: Any,
    request: AccountBindingRegistrationRequest,
    *,
    selected: FinanceAccountCandidate,
    actor_id: str,
    reauthorize: Callable[[], None],
    append_audit: Callable[[Mapping[str, object]], None],
) -> AccountBindingRegistrationReceipt:
    """Append an active version under the caller's tenant serialization lock.

    This intentionally has no connection acquisition, CLI or HTTP route.
    `sql` must be a transaction-bound PortfolioSQL over the proposed table;
    `append_audit` must append the matching finite v2 source in that same
    transaction. Historical replay is checked before current-version CAS or
    source currency. Any exception must roll back the caller's transaction.
    """

    if (
        not isinstance(request, AccountBindingRegistrationRequest)
        or not isinstance(selected, FinanceAccountCandidate)
        or not _identifier(actor_id)
        or not callable(reauthorize)
        or not callable(append_audit)
        or request.state != "active"
        or selected.binding.organization_id != request.organization_id
    ):
        raise AccountBindingStorageError("binding_conflict")
    reauthorize()
    tenant, binding_id = request.organization_id, request.binding_id
    replay = sql.one(
        f"SELECT * FROM {REGISTRATION_TABLE} "
        "WHERE organization_id=? AND binding_id=? AND request_digest=?",
        (tenant, binding_id, request.request_digest),
    )
    if replay is not None:
        return _receipt_from_row(replay)

    latest_row = sql.one(
        f"SELECT * FROM {REGISTRATION_TABLE} "
        "WHERE organization_id=? AND binding_id=? ORDER BY version DESC LIMIT 1",
        (tenant, binding_id),
    )
    latest = None if latest_row is None else _receipt_from_row(latest_row)
    if (
        (latest is None and request.expected_version is not None)
        or (latest is not None and request.expected_version != latest.version)
    ):
        raise AccountBindingStorageError("binding_conflict")

    exact_row = sql.one(
        "SELECT * FROM portfolio_finance_source_binding_versions "
        "WHERE organization_id=? AND binding_id=? AND version=?",
        (tenant, request.source_binding_id, request.source_binding_version),
    )
    current_row = sql.one(
        "SELECT * FROM portfolio_finance_source_binding_versions "
        "WHERE organization_id=? AND binding_id=? ORDER BY version DESC LIMIT 1",
        (tenant, request.source_binding_id),
    )
    try:
        matched = match_active_account_binding_source(
            request, selected=selected, source=_source_from_row(exact_row),
            current_source=_source_from_row(current_row),
        )
    except AccountBindingMatchError:
        raise AccountBindingStorageError("binding_conflict") from None

    row: dict[str, object] = {
        "organization_id": tenant,
        "binding_id": binding_id,
        "version": matched.version,
        "binding_event_id": str(uuid4()),
        "previous_version": request.expected_version,
        "binding_state": request.state,
        "reason_code": request.reason_code,
        "upstream_reference_id": matched.upstream_reference_id,
        "upstream_reference_version": matched.upstream_reference_version,
        "transport_profile": matched.transport_profile,
        "inference_credential_reference_id": matched.inference_credential_reference_id,
        "inference_credential_reference_version": matched.inference_credential_reference_version,
        "source_binding_id": matched.source_binding_id,
        "source_binding_version": matched.source_binding_version,
        "source_binding_digest": matched.source_binding_digest,
        "provider": matched.provider,
        "provider_account_fingerprint": matched.provider_account_fingerprint,
        "scope_kind": matched.scope_kind,
        "scope_fingerprints_json": _canonical(list(matched.scope_fingerprints)),
        "fingerprint_key_version": matched.fingerprint_key_version,
        "content_digest": "",
        "request_digest": request.request_digest,
        "registered_by": actor_id,
        "registered_at": sql.now(),
    }
    row["content_digest"] = _digest({name: row[name] for name in _CONTENT_COLUMNS})
    event = {"schema_id": _SCHEMA_ID, "schema_version": 1, **row}
    row["evidence_json"] = _canonical(event)
    sql.insert(REGISTRATION_TABLE, row)
    append_audit(dict(event))
    reauthorize()
    return _receipt_from_row(row)
