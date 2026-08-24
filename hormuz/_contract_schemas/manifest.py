"""Strict validator for the public policy/evidence schema manifest."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .common import (
    ContractValidationError,
    _exact_keys,
    _value_integer,
    _value_mapping,
    _value_string,
    _value_string_list,
)
from .constants import (
    AUDIT_CHAIN_ENTRY_SCHEMA_ID,
    AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
    CUSTODY_DELETION_EVENT_SCHEMA_ID,
    CUSTODY_DELETION_EVENT_SCHEMA_VERSION,
    CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID,
    CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID,
    CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_ID,
    MANIFEST_SCHEMA_VERSION,
    PUBLIC_ERROR_CODES,
    _REQUEST_STATUSES,
)


def custody_evidence_schema_entries() -> list[dict[str, object]]:
    """Return the v2 custody additions owned by the schema-manifest family."""

    return [
        _manifest_schema(
            AUDIT_CHAIN_ENTRY_SCHEMA_ID,
            AUDIT_CHAIN_ENTRY_SCHEMA_VERSION,
            "durable-evidence",
            [
                "organization_id",
                "chain_version",
                "chain_epoch",
                "sequence",
                "previous_digest",
                "event_digest",
                "source_schema_id",
                "source_schema_version",
                "source_event_id",
                "strict allowlisted metadata-only custody source event",
            ],
        ),
        _manifest_schema(
            CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID,
            CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION,
            "durable-evidence",
            [
                "organization_id",
                "execution_id",
                "attestation_kind",
                "tenant-qualified immutable asset identities and fingerprints",
                "occurred_at",
            ],
        ),
        _manifest_schema(
            CUSTODY_DELETION_EVENT_SCHEMA_ID,
            CUSTODY_DELETION_EVENT_SCHEMA_VERSION,
            "durable-evidence",
            [
                "organization_id",
                "deletion_event_id",
                "source schema and immutable source event identity",
                "source retention and legal-hold state",
                "decision=deletion_blocked",
                "reason_code",
                "occurred_at",
            ],
        ),
        _manifest_schema(
            CUSTODY_EVIDENCE_EXPORT_SCHEMA_ID,
            CUSTODY_EVIDENCE_EXPORT_SCHEMA_VERSION,
            "cli-output",
            [
                "schema_id",
                "schema_version",
                "organization_id",
                "generated_at",
                "strict metadata-only custody source records with v2 chain positions",
            ],
        ),
    ]


def _manifest_schema(
    schema_id: str,
    schema_version: int,
    delivery: str,
    fields: list[str],
) -> dict[str, object]:
    return {
        "schema_id": schema_id,
        "schema_version": schema_version,
        "delivery": delivery,
        "ownership": "hormuz",
        "legacy": False,
        "fields": fields,
    }


def validate_contract_manifest(value: Mapping[str, Any]) -> None:
    """Strictly validate the versioned manifest emitted by ``contract-manifest``."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "compatibility",
            "schemas",
            "policy_action_semantics",
            "request_status_semantics",
            "error_codes",
            "content_boundary",
        },
    )
    if _value_string(value, "schema_id") != MANIFEST_SCHEMA_ID:
        raise ContractValidationError("unsupported policy/evidence manifest schema_id")
    if _value_integer(value, "schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ContractValidationError("unsupported policy/evidence manifest schema_version")

    compatibility = _value_mapping(value, "compatibility")
    _exact_keys(
        compatibility,
        {
            "current_release_line",
            "addition_rule",
            "breaking_change_rule",
            "legacy_audit_read",
            "provider_protocol_rule",
        },
        path="compatibility",
    )
    for field in compatibility:
        _value_string(compatibility, field, path="compatibility")

    schemas = value.get("schemas")
    if not isinstance(schemas, list) or not schemas:
        raise ContractValidationError("schemas must be a non-empty array")
    identities: set[tuple[str, int]] = set()
    for index, schema in enumerate(schemas):
        if not isinstance(schema, Mapping):
            raise ContractValidationError(f"schemas[{index}] must be an object")
        _exact_keys(
            schema,
            {"schema_id", "schema_version", "delivery", "ownership", "legacy", "fields"},
            path=f"schemas[{index}]",
        )
        schema_id = _value_string(schema, "schema_id", path=f"schemas[{index}]")
        schema_version = _value_integer(schema, "schema_version", minimum=1, path=f"schemas[{index}]")
        identity = (schema_id, schema_version)
        if identity in identities:
            raise ContractValidationError(f"schemas contains duplicate identity: {schema_id} v{schema_version}")
        identities.add(identity)
        if _value_string(schema, "delivery", path=f"schemas[{index}]") not in {
            "response",
            "cli-output",
            "durable-evidence",
            "http-headers",
        }:
            raise ContractValidationError(f"schemas[{index}].delivery is unsupported")
        if _value_string(schema, "ownership", path=f"schemas[{index}]") != "hormuz":
            raise ContractValidationError(f"schemas[{index}].ownership must be hormuz")
        if not isinstance(schema.get("legacy"), bool):
            raise ContractValidationError(f"schemas[{index}].legacy must be a boolean")
        _value_string_list(schema, "fields", path=f"schemas[{index}]")

    policy_action_semantics = _value_mapping(value, "policy_action_semantics")
    if set(policy_action_semantics) != {
        "allowed",
        "fallback",
        "capped",
        "redacted",
        "denied",
        "provider_policy_denied",
        "secret_denied",
        "budget_reservation_denied",
    }:
        raise ContractValidationError("policy_action_semantics has unsupported entries")
    for field in policy_action_semantics:
        _value_string(policy_action_semantics, field, path="policy_action_semantics")

    request_status_semantics = _value_mapping(value, "request_status_semantics")
    if set(request_status_semantics) != _REQUEST_STATUSES:
        raise ContractValidationError("request_status_semantics has unsupported entries")
    for field in request_status_semantics:
        _value_string(request_status_semantics, field, path="request_status_semantics")

    error_codes = value.get("error_codes")
    if not isinstance(error_codes, list) or set(error_codes) != PUBLIC_ERROR_CODES or len(error_codes) != len(PUBLIC_ERROR_CODES):
        raise ContractValidationError("error_codes must enumerate every stable public error code exactly once")
    if error_codes != sorted(error_codes):
        raise ContractValidationError("error_codes must be sorted")
    _value_string(value, "content_boundary")
