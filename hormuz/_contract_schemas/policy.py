"""Policy decision, status, and durable control-event validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .common import (
    ContractValidationError,
    _exact_keys,
    _nullable_integer,
    _nullable_string,
    _sha256_digest,
    _value_integer,
    _value_mapping,
    _value_string,
    _value_string_list,
)
from .constants import (
    POLICY_CONTROL_EVENT_SCHEMA_ID,
    POLICY_CONTROL_EVENT_SCHEMA_VERSION,
    _POLICY_ACTIONS,
    _POLICY_BREAK_GLASS_REASONS,
    _POLICY_CHANGE_FIELDS,
    _POLICY_CONTROL_EVENT_TYPES,
    _POLICY_EGRESS_FIELDS,
    _REQUEST_STATUSES,
)


def validate_policy_control_event(value: Mapping[str, Any]) -> None:
    """Validate one metadata-only immutable policy-control evidence row.

    This durable format is separate from response envelopes because policy
    control events are written to PostgreSQL, not relayed to an AI client.
    Its validation is deliberately structural: no policy values, model names,
    request content, or secret material can enter ``change_summary``.
    """

    _exact_keys(
        value,
        {
            "event_schema_id",
            "event_schema_version",
            "organization_id",
            "occurred_at",
            "event_type",
            "actor_kind",
            "actor_identity_key",
            "target_identity_key",
            "version_id",
            "generation",
            "reason_code",
            "change_summary",
        },
    )
    if _value_string(value, "event_schema_id") != POLICY_CONTROL_EVENT_SCHEMA_ID:
        raise ContractValidationError("policy control event schema_id is unsupported")
    if _value_integer(value, "event_schema_version", minimum=1) != POLICY_CONTROL_EVENT_SCHEMA_VERSION:
        raise ContractValidationError("policy control event schema_version is unsupported")
    _value_string(value, "organization_id")
    _value_string(value, "occurred_at")
    event_type = _value_string(value, "event_type")
    if event_type not in _POLICY_CONTROL_EVENT_TYPES:
        raise ContractValidationError("policy control event_type is unsupported")
    actor_kind = _value_string(value, "actor_kind")
    if actor_kind not in {"static", "oidc", "break_glass"}:
        raise ContractValidationError("policy control actor_kind is unsupported")
    _policy_identity_key(value, "actor_identity_key", kind=actor_kind)
    target_identity_key = _nullable_string(value, "target_identity_key")
    version_id = _nullable_string(value, "version_id")
    if version_id is not None:
        _policy_version_identifier(version_id, "version_id")
    generation = value.get("generation")
    if generation is not None:
        _value_integer(value, "generation", minimum=1)
    reason_code = _nullable_string(value, "reason_code")
    change_summary = value.get("change_summary")
    if change_summary is not None and not isinstance(change_summary, Mapping):
        raise ContractValidationError("change_summary must be an object or null")

    if event_type == "bootstrap_initialized":
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _event_requires_none(target_identity_key, version_id, generation, reason_code)
        _validate_bootstrap_change_summary(change_summary)
        return
    if event_type in {"administrator_granted", "administrator_revoked"}:
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _policy_identity_key_value(target_identity_key, "target_identity_key")
        _event_requires_none(version_id, generation, reason_code, change_summary)
        return
    if event_type == "policy_staged":
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _event_requires_none(target_identity_key, generation, reason_code)
        if version_id is None:
            raise ContractValidationError("policy_staged requires version_id")
        if not isinstance(change_summary, Mapping):
            raise ContractValidationError("policy_staged requires change_summary")
        _validate_redacted_change_summary(change_summary)
        return
    if event_type in {"policy_activated", "policy_rolled_back"}:
        _event_requires_actor(actor_kind, {"static", "oidc"})
        _event_requires_none(target_identity_key, reason_code, change_summary)
        if version_id is None or generation is None:
            raise ContractValidationError("policy activation event requires version_id and generation")
        return
    # ``event_type`` was checked above, so this is the narrowly controlled
    # break-glass recovery record.
    _event_requires_actor(actor_kind, {"break_glass"})
    _policy_identity_key_value(target_identity_key, "target_identity_key")
    _event_requires_none(version_id, generation, change_summary)
    if reason_code not in _POLICY_BREAK_GLASS_REASONS:
        raise ContractValidationError("break_glass_recovered requires a supported reason_code")


def validate_policy_action(value: str) -> None:
    if value not in _POLICY_ACTIONS:
        raise ContractValidationError(f"unsupported policy action: {value}")


def validate_request_status(value: str) -> None:
    if value not in _REQUEST_STATUSES:
        raise ContractValidationError(f"unsupported request status: {value}")


def _validate_policy_decision(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "allowed",
            "action",
            "reason",
            "requested_model",
            "resolved_alias",
            "routed_model",
            "max_output_tokens",
            "policy_version",
        },
    )
    if not isinstance(value.get("allowed"), bool):
        raise ContractValidationError("allowed must be a boolean")
    validate_policy_action(_value_string(value, "action"))
    _value_string(value, "reason")
    _value_string(value, "requested_model")
    _nullable_string(value, "resolved_alias")
    _nullable_string(value, "routed_model")
    _nullable_integer(value, "max_output_tokens", minimum=1)
    _value_string(value, "policy_version")


def _validate_policy_control_status(value: Mapping[str, Any]) -> None:
    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "initialized",
            "active",
            "versions",
            "administrators",
        },
    )
    _value_string(value, "organization_id")
    if not isinstance(value.get("initialized"), bool):
        raise ContractValidationError("initialized must be a boolean")
    active = value.get("active")
    if active is not None:
        if not isinstance(active, Mapping):
            raise ContractValidationError("active must be an object or null")
        _exact_keys(
            active,
            {
                "version_id",
                "generation",
                "activated_at",
                "activated_by_kind",
                "activated_by_identity_key",
            },
            path="active",
        )
        _policy_version_identifier(_value_string(active, "version_id", path="active"), "active.version_id")
        _value_integer(active, "generation", minimum=1, path="active")
        _value_string(active, "activated_at", path="active")
        _administrator_key_fields(active, prefix="activated_by_", path="active")

    versions = value.get("versions")
    if not isinstance(versions, list):
        raise ContractValidationError("versions must be an array")
    for index, version in enumerate(versions):
        if not isinstance(version, Mapping):
            raise ContractValidationError(f"versions[{index}] must be an object")
        _exact_keys(
            version,
            {
                "version_id",
                "content_sha256",
                "created_at",
                "author_kind",
                "author_identity_key",
                "change_summary",
            },
            path=f"versions[{index}]",
        )
        _policy_version_identifier(
            _value_string(version, "version_id", path=f"versions[{index}]"),
            f"versions[{index}].version_id",
        )
        _sha256_digest(
            _value_string(version, "content_sha256", path=f"versions[{index}]"),
            f"versions[{index}].content_sha256",
        )
        _value_string(version, "created_at", path=f"versions[{index}]")
        _administrator_key_fields(version, prefix="author_", path=f"versions[{index}]")
        _validate_redacted_change_summary(_value_mapping(version, "change_summary", path=f"versions[{index}]"))

    administrators = value.get("administrators")
    if not isinstance(administrators, list):
        raise ContractValidationError("administrators must be an array")
    for index, administrator in enumerate(administrators):
        if not isinstance(administrator, Mapping):
            raise ContractValidationError(f"administrators[{index}] must be an object")
        _exact_keys(
            administrator,
            {"authentication_kind", "actor_id", "issuer", "subject"},
            path=f"administrators[{index}]",
        )
        kind = _value_string(administrator, "authentication_kind", path=f"administrators[{index}]")
        actor_id = _nullable_string(administrator, "actor_id", path=f"administrators[{index}]")
        issuer = _nullable_string(administrator, "issuer", path=f"administrators[{index}]")
        subject = _nullable_string(administrator, "subject", path=f"administrators[{index}]")
        if kind == "static" and actor_id is not None and issuer is None and subject is None:
            continue
        if kind == "oidc" and actor_id is None and issuer is not None and subject is not None:
            continue
        raise ContractValidationError(f"administrators[{index}] has an invalid stable identity key")


def _administrator_key_fields(value: Mapping[str, Any], *, prefix: str, path: str) -> None:
    kind = _value_string(value, f"{prefix}kind", path=path)
    if kind not in {"static", "oidc"}:
        raise ContractValidationError(f"{path}.{prefix}kind is unsupported")
    _policy_identity_key(value, f"{prefix}identity_key", kind=kind, path=path)


def _validate_redacted_change_summary(value: Mapping[str, Any]) -> None:
    _exact_keys(value, {"summary_version", "scopes", "egress_fields"}, path="change_summary")
    if _value_integer(value, "summary_version", minimum=1, path="change_summary") != 1:
        raise ContractValidationError("change_summary.summary_version is unsupported")
    if tuple(_value_string_list(value, "egress_fields", path="change_summary")) != _POLICY_EGRESS_FIELDS:
        raise ContractValidationError("change_summary.egress_fields is invalid")
    scopes = _value_mapping(value, "scopes", path="change_summary")
    _exact_keys(scopes, {"organization", "teams", "actors"}, path="change_summary.scopes")
    organization = _value_mapping(scopes, "organization", path="change_summary.scopes")
    _exact_keys(organization, {"fields"}, path="change_summary.scopes.organization")
    _validate_policy_change_fields(organization, "fields", path="change_summary.scopes.organization")
    for scope in ("teams", "actors"):
        item = _value_mapping(scopes, scope, path="change_summary.scopes")
        _exact_keys(item, {"count", "fields"}, path=f"change_summary.scopes.{scope}")
        _value_integer(item, "count", minimum=0, path=f"change_summary.scopes.{scope}")
        _validate_policy_change_fields(item, "fields", path=f"change_summary.scopes.{scope}")


def _validate_bootstrap_change_summary(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ContractValidationError("bootstrap_initialized requires change_summary")
    _exact_keys(value, {"summary_version", "bootstrap_administrator_count"}, path="change_summary")
    if _value_integer(value, "summary_version", minimum=1, path="change_summary") != 1:
        raise ContractValidationError("change_summary.summary_version is unsupported")
    _value_integer(value, "bootstrap_administrator_count", minimum=1, path="change_summary")


def _validate_policy_change_fields(value: Mapping[str, Any], field: str, *, path: str) -> None:
    fields = _value_string_list(value, field, path=path)
    if (
        len(fields) != len(set(fields))
        or any(item not in _POLICY_CHANGE_FIELDS for item in fields)
        or fields != sorted(fields)
    ):
        raise ContractValidationError(f"{path}.{field} is invalid")


def _event_requires_actor(kind: str, allowed: set[str]) -> None:
    if kind not in allowed:
        raise ContractValidationError("policy control event actor kind is invalid for event_type")


def _event_requires_none(*values: object) -> None:
    if any(value is not None for value in values):
        raise ContractValidationError("policy control event has fields invalid for event_type")


def _policy_identity_key(
    value: Mapping[str, Any],
    field: str,
    *,
    kind: str,
    path: str | None = None,
) -> None:
    _policy_identity_key_value(_value_string(value, field, path=path), field if path is None else f"{path}.{field}", kind=kind)


def _policy_identity_key_value(value: object, path: str, *, kind: str | None = None) -> None:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{path} must be a non-empty string")
    if kind == "break_glass":
        if value != "break_glass":
            raise ContractValidationError(f"{path} is invalid")
        return
    prefix, separator, digest = value.partition(":")
    if (
        not separator
        or prefix not in {"static", "oidc"}
        or (kind is not None and prefix != kind)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ContractValidationError(f"{path} is invalid")


def _policy_version_identifier(value: str, path: str) -> None:
    prefix = "sha256:"
    digest = value.removeprefix(prefix)
    if not value.startswith(prefix):
        raise ContractValidationError(f"{path} is invalid")
    _sha256_digest(digest, path)
