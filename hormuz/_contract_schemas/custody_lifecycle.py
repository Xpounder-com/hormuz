"""Validator for hash-linked, metadata-only custody lifecycle evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from .common import ContractValidationError, _exact_keys, _nullable_string, _sha256_digest, _value_integer, _value_string
from .constants import (
    CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID,
    CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION,
    CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID,
    CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION,
    _CUSTODY_LIFECYCLE_OPERATION_TYPES,
    _CUSTODY_RECOVERY_RESOLUTION_CODES,
)


def validate_custody_lifecycle_event(value: Mapping[str, Any]) -> None:
    """Validate one immutable lifecycle event before it enters durable storage."""

    _exact_keys(
        value,
        {
            "lifecycle_schema_id",
            "lifecycle_schema_version",
            "organization_id",
            "lifecycle_event_id",
            "execution_id",
            "operation_id",
            "occurred_at",
            "operation_type",
            "target_sha256",
            "parameters_sha256",
            "asset_type",
            "asset_id",
            "asset_generation",
            "asset_binding_fingerprint",
            "replacement_asset_type",
            "replacement_asset_id",
            "replacement_asset_generation",
            "replacement_asset_binding_fingerprint",
            "recovery_execution_id",
            "recovery_resolution_code",
            "chain_version",
            "sequence",
            "previous_digest",
            "event_digest",
        },
    )
    if _value_string(value, "lifecycle_schema_id") != CUSTODY_LIFECYCLE_EVENT_SCHEMA_ID:
        raise ContractValidationError("custody lifecycle schema_id is unsupported")
    if _value_integer(value, "lifecycle_schema_version", minimum=1) != CUSTODY_LIFECYCLE_EVENT_SCHEMA_VERSION:
        raise ContractValidationError("custody lifecycle schema_version is unsupported")
    _value_string(value, "organization_id")
    _uuid(_value_string(value, "lifecycle_event_id"), "lifecycle_event_id")
    _uuid(_value_string(value, "execution_id"), "execution_id")
    _uuid(_value_string(value, "operation_id"), "operation_id")
    _value_string(value, "occurred_at")
    _sha256_digest(_value_string(value, "target_sha256"), "target_sha256")
    _sha256_digest(_value_string(value, "parameters_sha256"), "parameters_sha256")
    operation_type = _value_string(value, "operation_type")
    if operation_type not in _CUSTODY_LIFECYCLE_OPERATION_TYPES:
        raise ContractValidationError("custody lifecycle operation_type is unsupported")
    if _value_integer(value, "chain_version", minimum=1) != 1:
        raise ContractValidationError("custody lifecycle chain_version is unsupported")
    _value_integer(value, "sequence", minimum=1)
    previous_digest = _nullable_string(value, "previous_digest")
    if previous_digest is not None:
        _sha256_digest(previous_digest, "previous_digest")
    _sha256_digest(_value_string(value, "event_digest"), "event_digest")

    asset_type = _nullable_string(value, "asset_type")
    asset_id = _nullable_string(value, "asset_id")
    asset_generation = value.get("asset_generation")
    asset_fingerprint = _nullable_string(value, "asset_binding_fingerprint")
    replacement_type = _nullable_string(value, "replacement_asset_type")
    replacement_id = _nullable_string(value, "replacement_asset_id")
    replacement_generation = value.get("replacement_asset_generation")
    replacement_fingerprint = _nullable_string(value, "replacement_asset_binding_fingerprint")
    recovery_execution_id = _nullable_string(value, "recovery_execution_id")
    recovery_code = _nullable_string(value, "recovery_resolution_code")

    if operation_type == "resolve_recovery":
        if any(
            item is not None
            for item in (
                asset_type,
                asset_id,
                asset_generation,
                asset_fingerprint,
                replacement_type,
                replacement_id,
                replacement_generation,
                replacement_fingerprint,
            )
        ):
            raise ContractValidationError("custody recovery lifecycle assets are invalid")
        _uuid(recovery_execution_id, "recovery_execution_id")
        if recovery_code not in _CUSTODY_RECOVERY_RESOLUTION_CODES:
            raise ContractValidationError("custody recovery lifecycle resolution is invalid")
        return

    _asset(asset_type, asset_id, asset_generation, asset_fingerprint, field="asset")
    if recovery_execution_id is not None or recovery_code is not None:
        raise ContractValidationError("custody lifecycle recovery fields are invalid")
    if operation_type == "retire_key_reference":
        if replacement_type != "key_reference":
            raise ContractValidationError("custody lifecycle replacement type is invalid")
        _asset(replacement_type, replacement_id, replacement_generation, replacement_fingerprint, field="replacement_asset")
        return
    if any(item is not None for item in (replacement_type, replacement_id, replacement_generation, replacement_fingerprint)):
        raise ContractValidationError("custody lifecycle replacement fields are invalid")
    expected_type = "provider_credential" if operation_type == "disable_provider_credential" else "envelope"
    if asset_type != expected_type:
        raise ContractValidationError("custody lifecycle asset type is invalid")


def validate_custody_envelope_attestation(value: Mapping[str, Any]) -> None:
    """Validate one active-core rewrap or restore-proof record.

    The record contains only immutable asset identities and fingerprints; no
    envelope ciphertext, source path, KMS key reference, or plaintext is part
    of this evidence contract.
    """

    _exact_keys(
        value,
        {
            "attestation_schema_id",
            "attestation_schema_version",
            "organization_id",
            "execution_id",
            "attestation_kind",
            "envelope_asset_id",
            "envelope_generation",
            "envelope_binding_fingerprint",
            "source_key_asset_id",
            "source_key_generation",
            "source_key_binding_fingerprint",
            "destination_key_asset_id",
            "destination_key_generation",
            "destination_key_binding_fingerprint",
            "occurred_at",
        },
    )
    if _value_string(value, "attestation_schema_id") != CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_ID:
        raise ContractValidationError("custody envelope attestation schema_id is unsupported")
    if (
        _value_integer(value, "attestation_schema_version", minimum=1)
        != CUSTODY_ENVELOPE_ATTESTATION_SCHEMA_VERSION
    ):
        raise ContractValidationError("custody envelope attestation schema_version is unsupported")
    _value_string(value, "organization_id")
    _uuid(_value_string(value, "execution_id"), "execution_id")
    kind = _value_string(value, "attestation_kind")
    _value_string(value, "occurred_at")
    _asset("envelope", _value_string(value, "envelope_asset_id"), value.get("envelope_generation"), value.get("envelope_binding_fingerprint"), field="envelope")
    _asset(
        "key_reference",
        _value_string(value, "destination_key_asset_id"),
        value.get("destination_key_generation"),
        value.get("destination_key_binding_fingerprint"),
        field="destination_key",
    )
    source_id = _nullable_string(value, "source_key_asset_id")
    source_generation = value.get("source_key_generation")
    source_fingerprint = _nullable_string(value, "source_key_binding_fingerprint")
    if kind == "rewrapped":
        _asset("key_reference", source_id, source_generation, source_fingerprint, field="source_key")
        return
    if kind == "restore_verified":
        if source_id is not None or source_generation is not None or source_fingerprint is not None:
            raise ContractValidationError("custody restore attestation source key is invalid")
        return
    raise ContractValidationError("custody envelope attestation kind is invalid")


def _asset(asset_type: object, asset_id: object, generation: object, fingerprint: object, *, field: str) -> None:
    if asset_type not in {"provider_credential", "envelope", "key_reference"}:
        raise ContractValidationError(f"custody lifecycle {field}_type is invalid")
    _value_string({"value": asset_id}, "value")
    _value_integer({"value": generation}, "value", minimum=1)
    _sha256_digest(_value_string({"value": fingerprint}, "value"), f"{field}_binding_fingerprint")


def _uuid(value: object, field: str) -> None:
    try:
        UUID(str(value))
    except (ValueError, TypeError, AttributeError) as error:
        raise ContractValidationError(f"{field} is invalid") from error
