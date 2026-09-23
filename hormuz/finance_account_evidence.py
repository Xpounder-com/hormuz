"""Strict metadata-only evidence contracts for finance account association.

These validators are deliberately independent from the audit-chain module so
the finite v2 source union can import them without a cycle.  They accept only
closed version-1 objects and expose the exact source identity that may enter a
commit-time audit chain.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Mapping
from uuid import UUID


ACCOUNT_BINDING_SCHEMA_ID = "hormuz.finance-account-binding-version"
ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID = "hormuz.finance-attempt-account-binding"
QUERY_AUDIT_SCHEMA_ID = "hormuz.finance-query-audit-event"
FINANCE_ACCOUNT_SOURCE_SCHEMA_IDS = frozenset({
    ACCOUNT_BINDING_SCHEMA_ID,
    ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID,
    QUERY_AUDIT_SCHEMA_ID,
})

ACCOUNT_BINDING_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "binding_id", "version",
    "binding_event_id", "previous_version", "binding_state", "reason_code",
    "upstream_reference_id", "upstream_reference_version", "transport_profile",
    "inference_credential_reference_id", "inference_credential_reference_version",
    "source_binding_id", "source_binding_version", "source_binding_digest",
    "provider", "provider_account_fingerprint", "scope_kind",
    "scope_fingerprints_json", "fingerprint_key_version", "content_digest",
    "request_digest", "registered_by", "registered_at",
})

ATTEMPT_ACCOUNT_BINDING_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "request_attempt_id",
    "event_id", "captured_at", "state", "reason_code", "binding_id",
    "binding_version", "binding_digest", "upstream_reference_id",
    "upstream_reference_version", "transport_profile",
    "inference_credential_reference_id", "inference_credential_reference_version",
    "source_binding_id", "source_binding_version", "source_binding_digest",
    "provider", "provider_account_fingerprint", "scope_kind",
    "scope_fingerprints_json", "fingerprint_key_version",
})

QUERY_AUDIT_FIELDS = frozenset({
    "schema_id", "schema_version", "organization_id", "query_event_id",
    "actor_id", "query_class", "binding_id", "binding_version",
    "collection_profile", "query_start_at", "query_end_at",
    "as_of_commit_sequence", "currency", "selected_snapshot_count",
    "coverage_bucket_count", "provider_observation_count",
    "terminal_attempt_count", "terminal_attempts_missing_sidecar_count",
    "occurred_at",
})

UNBOUND_REASONS = frozenset({
    "not_configured", "binding_missing", "binding_invalid", "binding_revoked",
    "binding_version_stale", "binding_ambiguous", "source_binding_missing",
    "source_binding_revoked", "source_binding_version_stale", "tenant_mismatch",
    "upstream_reference_mismatch", "inference_reference_mismatch",
    "unsupported_transport", "fingerprint_key_version_mismatch",
})

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_TRANSPORTS = {
    "openai": "openai.first-party.v1",
    "anthropic": "anthropic.first-party.v1",
}
_SCOPES = frozenset({"organization", "projects", "workspaces"})
_REGISTRATION_REASONS = frozenset({"created", "replaced", "revoked"})


def canonical_evidence_text(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ValueError("finance_account_evidence_invalid") from None


def _exact(value: Mapping[str, object], fields: frozenset[str]) -> None:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ValueError("finance_account_evidence_invalid")


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _identity_text(value: object) -> bool:
    """Preserve the established tenant and actor string boundary."""

    return isinstance(value, str) and 1 <= len(value) <= 128


def _version(value: object, *, allow_zero: bool = False) -> bool:
    minimum = 0 if allow_zero else 1
    return type(value) is int and minimum <= value <= 2_147_483_647


def _nonnegative_int64(value: object) -> bool:
    return type(value) is int and 0 <= value <= 9_223_372_036_854_775_807


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value and value == value.lower()
    except (AttributeError, TypeError, ValueError):
        return False


def _sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def _scope_fingerprints(value: object) -> bool:
    if not isinstance(value, str) or not 2 <= len(value.encode("utf-8")) <= 65536:
        return False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(parsed, list)
        and all(_sha256(item) for item in parsed)
        and parsed == sorted(set(parsed))
        and canonical_evidence_text(parsed) == value
    )


def validate_finance_account_binding_event(value: Mapping[str, object]) -> None:
    _exact(value, ACCOUNT_BINDING_FIELDS)
    identifiers = (
        "binding_id", "upstream_reference_id", "transport_profile",
        "inference_credential_reference_id", "source_binding_id",
    )
    versions = (
        "version", "upstream_reference_version", "inference_credential_reference_version",
        "source_binding_version", "fingerprint_key_version",
    )
    if (
        value.get("schema_id") != ACCOUNT_BINDING_SCHEMA_ID
        or value.get("schema_version") != 1
        or not _identity_text(value.get("organization_id"))
        or not _identity_text(value.get("registered_by"))
        or not all(_identifier(value.get(field)) for field in identifiers)
        or not all(_version(value.get(field)) for field in versions)
        or not _uuid(value.get("binding_event_id"))
        or value.get("binding_state") not in {"active", "revoked"}
        or value.get("reason_code") not in _REGISTRATION_REASONS
        or value.get("provider") not in _TRANSPORTS
        or value.get("transport_profile") != _TRANSPORTS.get(value.get("provider"))
        or value.get("scope_kind") not in _SCOPES
        or not _scope_fingerprints(value.get("scope_fingerprints_json"))
        or not all(_sha256(value.get(field)) for field in (
            "source_binding_digest", "provider_account_fingerprint", "content_digest",
            "request_digest",
        ))
        or not _utc_timestamp(value.get("registered_at"))
    ):
        raise ValueError("finance_account_evidence_invalid")
    version = value["version"]
    previous = value.get("previous_version")
    if (
        (version == 1 and previous is not None)
        or (version > 1 and previous != version - 1)
        or (version == 1 and value.get("reason_code") != "created")
        or (version > 1 and value.get("binding_state") == "active" and value.get("reason_code") != "replaced")
        or (value.get("binding_state") == "revoked" and value.get("reason_code") != "revoked")
    ):
        raise ValueError("finance_account_evidence_invalid")
    content_fields = {
        key: value[key]
        for key in ACCOUNT_BINDING_FIELDS
        if key not in {
            "schema_id", "schema_version", "content_digest", "request_digest",
            "binding_event_id", "registered_by", "registered_at",
        }
    }
    digest = hashlib.sha256(canonical_evidence_text(content_fields).encode("utf-8")).hexdigest()
    if digest != value.get("content_digest"):
        raise ValueError("finance_account_evidence_invalid")


def validate_finance_attempt_account_binding_event(value: Mapping[str, object]) -> None:
    _exact(value, ATTEMPT_ACCOUNT_BINDING_FIELDS)
    if (
        value.get("schema_id") != ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID
        or value.get("schema_version") != 1
        or not _identity_text(value.get("organization_id"))
        or not _uuid(value.get("request_attempt_id"))
        or not _uuid(value.get("event_id"))
        or not _utc_timestamp(value.get("captured_at"))
        or value.get("state") not in {"bound", "unbound"}
    ):
        raise ValueError("finance_account_evidence_invalid")
    coordinates = (
        "binding_id", "binding_version", "binding_digest", "upstream_reference_id",
        "upstream_reference_version", "transport_profile",
        "inference_credential_reference_id", "inference_credential_reference_version",
        "source_binding_id", "source_binding_version", "source_binding_digest", "provider",
        "provider_account_fingerprint", "scope_kind", "scope_fingerprints_json",
        "fingerprint_key_version",
    )
    if value["state"] == "unbound":
        if value.get("reason_code") not in UNBOUND_REASONS or any(
            value.get(field) is not None for field in coordinates
        ):
            raise ValueError("finance_account_evidence_invalid")
        return
    if value.get("reason_code") is not None:
        raise ValueError("finance_account_evidence_invalid")
    identifiers = (
        "binding_id", "upstream_reference_id", "transport_profile",
        "inference_credential_reference_id", "source_binding_id",
    )
    versions = (
        "binding_version", "upstream_reference_version",
        "inference_credential_reference_version", "source_binding_version",
        "fingerprint_key_version",
    )
    if (
        not all(_identifier(value.get(field)) for field in identifiers)
        or not all(_version(value.get(field)) for field in versions)
        or not all(_sha256(value.get(field)) for field in (
            "binding_digest", "source_binding_digest", "provider_account_fingerprint",
        ))
        or value.get("provider") not in _TRANSPORTS
        or value.get("transport_profile") != _TRANSPORTS.get(value.get("provider"))
        or value.get("scope_kind") not in _SCOPES
        or not _scope_fingerprints(value.get("scope_fingerprints_json"))
    ):
        raise ValueError("finance_account_evidence_invalid")


def validate_finance_query_audit_event(value: Mapping[str, object]) -> None:
    _exact(value, QUERY_AUDIT_FIELDS)
    counts = (
        "selected_snapshot_count", "coverage_bucket_count", "provider_observation_count",
        "terminal_attempt_count", "terminal_attempts_missing_sidecar_count",
    )
    if (
        value.get("schema_id") != QUERY_AUDIT_SCHEMA_ID
        or value.get("schema_version") != 1
        or not _identity_text(value.get("organization_id"))
        or not _identity_text(value.get("actor_id"))
        or not all(_identifier(value.get(field)) for field in (
            "binding_id", "collection_profile",
        ))
        or not _uuid(value.get("query_event_id"))
        or value.get("query_class") != "finance_coverage_report_v1"
        or not _version(value.get("binding_version"))
        or not _nonnegative_int64(value.get("as_of_commit_sequence"))
        or not all(_nonnegative_int64(value.get(field)) for field in counts)
        or value.get("terminal_attempts_missing_sidecar_count", 0)
        > value.get("terminal_attempt_count", -1)
        or not isinstance(value.get("currency"), str)
        or _CURRENCY.fullmatch(str(value.get("currency"))) is None
        or not _utc_timestamp(value.get("query_start_at"))
        or not _utc_timestamp(value.get("query_end_at"))
        or not _utc_timestamp(value.get("occurred_at"))
    ):
        raise ValueError("finance_account_evidence_invalid")
    if datetime.fromisoformat(str(value["query_end_at"]).replace("Z", "+00:00")) <= datetime.fromisoformat(
        str(value["query_start_at"]).replace("Z", "+00:00")
    ):
        raise ValueError("finance_account_evidence_invalid")


def finance_account_source_identity(schema_id: str, event: Mapping[str, object]) -> str:
    if schema_id == ACCOUNT_BINDING_SCHEMA_ID:
        validate_finance_account_binding_event(event)
        identity = event.get("binding_event_id")
    elif schema_id == ATTEMPT_ACCOUNT_BINDING_SCHEMA_ID:
        validate_finance_attempt_account_binding_event(event)
        identity = event.get("event_id")
    elif schema_id == QUERY_AUDIT_SCHEMA_ID:
        validate_finance_query_audit_event(event)
        identity = event.get("query_event_id")
    else:
        raise ValueError("finance_account_evidence_invalid")
    assert isinstance(identity, str)
    return identity
