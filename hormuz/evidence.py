"""Content-free materialization of durable audit evidence.

Storage adapters retain metadata columns, not a provider response shape. This
module is the one allowlisted conversion from those columns to the public audit
event contract. Keeping it shared prevents SQLite and PostgreSQL from silently
drifting in their historical-evidence behavior.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping

from .contracts import AUDIT_EVENT_SCHEMA_ID, ContractValidationError, validate_audit_event


class EvidenceStorageError(RuntimeError):
    """A stable, content-free durable-evidence failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def usage_audit_event(stored: Mapping[str, object]) -> dict[str, object]:
    """Convert one persisted usage row to its declared audit schema version."""

    version = _schema_version(stored)
    if version == 1:
        event = {
            "schema_version": 1,
            "event_type": "usage",
            **_fields(
                stored,
                (
                    "id",
                    "occurred_at",
                    "actor_id",
                    "actor_name",
                    "team_id",
                    "team_name",
                    "client",
                    "protocol",
                    "requested_model",
                    "resolved_alias",
                    "upstream_model",
                    "policy_action",
                    "status",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                    "cost_microusd",
                    "provider_request_id",
                    "redaction_count",
                ),
            ),
            "redaction_rules": _json_string_list(stored, "redaction_rules"),
        }
    elif version == 2:
        _require_current_schema(stored)
        event = {
            "schema_id": AUDIT_EVENT_SCHEMA_ID,
            "schema_version": 2,
            "event_type": "usage",
            **_fields(
                stored,
                (
                    "id",
                    "occurred_at",
                    "organization_id",
                    "actor_id",
                    "actor_name",
                    "team_id",
                    "team_name",
                    "identity_type",
                    "authentication_source",
                    "client",
                    "protocol",
                    "requested_model",
                    "resolved_alias",
                ),
            ),
            "routed_model": _field(stored, "upstream_model"),
            **_fields(
                stored,
                (
                    "provider_reported_model",
                    "policy_version",
                    "policy_action",
                    "status",
                    "input_tokens",
                    "output_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "reasoning_tokens",
                    "cost_microusd",
                    "cost_basis",
                    "allocation_basis",
                    "coverage",
                    "provider_request_id",
                    "redaction_count",
                ),
            ),
            "redaction_rules": _json_string_list(stored, "redaction_rules"),
        }
    else:
        raise EvidenceStorageError("stored_evidence_schema_unsupported")
    return _validated(event)


def security_audit_event(stored: Mapping[str, object]) -> dict[str, object]:
    """Convert one persisted secret-evidence row to its declared schema."""

    version = _schema_version(stored)
    if version == 1:
        event = {
            "schema_version": 1,
            "event_type": "security.secret",
            **_fields(
                stored,
                (
                    "id",
                    "occurred_at",
                    "actor_id",
                    "actor_name",
                    "team_id",
                    "team_name",
                    "client",
                    "protocol",
                    "requested_model",
                    "action",
                    "detection_count",
                ),
            ),
            "rules": _json_string_list(stored, "rules"),
        }
    elif version == 2:
        _require_current_schema(stored)
        event = {
            "schema_id": AUDIT_EVENT_SCHEMA_ID,
            "schema_version": 2,
            "event_type": "security.secret",
            **_fields(
                stored,
                (
                    "id",
                    "occurred_at",
                    "organization_id",
                    "actor_id",
                    "actor_name",
                    "team_id",
                    "team_name",
                    "identity_type",
                    "authentication_source",
                    "client",
                    "protocol",
                    "requested_model",
                    "policy_version",
                    "coverage",
                    "action",
                    "detection_count",
                ),
            ),
            "rules": _json_string_list(stored, "rules"),
        }
    else:
        raise EvidenceStorageError("stored_evidence_schema_unsupported")
    return _validated(event)


def _schema_version(stored: Mapping[str, object]) -> int:
    value = _field(stored, "evidence_schema_version")
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceStorageError("stored_evidence_schema_unsupported")
    return value


def _require_current_schema(stored: Mapping[str, object]) -> None:
    if _field(stored, "evidence_schema_id") != AUDIT_EVENT_SCHEMA_ID:
        raise EvidenceStorageError("stored_evidence_schema_unsupported")


def _json_string_list(stored: Mapping[str, object], name: str) -> list[str]:
    value = _field(stored, name)
    if not isinstance(value, str):
        raise EvidenceStorageError("stored_evidence_malformed")
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise EvidenceStorageError("stored_evidence_malformed") from None
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise EvidenceStorageError("stored_evidence_malformed")
    return decoded


def _fields(stored: Mapping[str, object], names: tuple[str, ...]) -> dict[str, object]:
    return {name: _field(stored, name) for name in names}


def _field(stored: Mapping[str, object], name: str) -> object:
    try:
        value = stored[name]
    except KeyError:
        raise EvidenceStorageError("stored_evidence_malformed") from None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo is not None else value.isoformat()
    return value


def _validated(event: dict[str, object]) -> dict[str, object]:
    try:
        validate_audit_event(event)
    except ContractValidationError:
        raise EvidenceStorageError("stored_evidence_malformed") from None
    return event
