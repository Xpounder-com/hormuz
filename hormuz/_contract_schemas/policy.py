"""Policy decision, status, and durable control-event validators."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
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
    POLICY_CONTROL_STATUS_SCHEMA_ID,
    POLICY_COMPARISON_SCHEMA_ID,
    POLICY_COMPARISON_SCHEMA_VERSION,
    POLICY_DECISION_SCHEMA_ID,
    POLICY_DOCUMENT_SCHEMA_ID,
    POLICY_DOCUMENT_SCHEMA_VERSION,
    POLICY_HISTORY_SCHEMA_ID,
    POLICY_HISTORY_SCHEMA_VERSION,
    POLICY_HISTORY_MAX_LIMIT,
    POLICY_PREVIEW_SCHEMA_ID,
    POLICY_PREVIEW_SCHEMA_VERSION,
    _POLICY_COMPARISON_CHANGE_TYPES,
    _POLICY_LIFECYCLE_EVENT_TYPES,
    _POLICY_ACTIONS,
    _POLICY_BREAK_GLASS_REASONS,
    _POLICY_CHANGE_FIELDS,
    _POLICY_CONTROL_EVENT_TYPES,
    _POLICY_EGRESS_FIELDS,
    _REQUEST_STATUSES,
)


def policy_schema_entries() -> list[dict[str, object]]:
    """Return manifest entries owned by the policy schema family."""

    return [
        _manifest_schema(
            POLICY_DECISION_SCHEMA_ID,
            1,
            "cli-output",
            [
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
            ],
        ),
        _manifest_schema(
            POLICY_CONTROL_STATUS_SCHEMA_ID,
            1,
            "cli-output",
            ["schema_id", "schema_version", "organization_id", "initialized", "active", "versions", "administrators"],
        ),
        _manifest_schema(
            POLICY_COMPARISON_SCHEMA_ID,
            POLICY_COMPARISON_SCHEMA_VERSION,
            "cli-output",
            ["schema_id", "schema_version", "organization_id", "baseline", "candidate", "identical", "changes"],
        ),
        _manifest_schema(
            POLICY_PREVIEW_SCHEMA_ID,
            POLICY_PREVIEW_SCHEMA_VERSION,
            "cli-output",
            [
                "schema_id",
                "schema_version",
                "organization_id",
                "evaluated_at",
                "usage_period",
                "usage_basis",
                "request",
                "baseline",
                "candidate",
            ],
        ),
        _manifest_schema(
            POLICY_HISTORY_SCHEMA_ID,
            POLICY_HISTORY_SCHEMA_VERSION,
            "cli-output",
            ["schema_id", "schema_version", "organization_id", "limit", "has_more", "events"],
        ),
        _manifest_schema(
            POLICY_DOCUMENT_SCHEMA_ID,
            POLICY_DOCUMENT_SCHEMA_VERSION,
            "durable-evidence",
            ["schema_id", "schema_version", "organization_id", "policies", "egress_controls"],
        ),
        _manifest_schema(
            POLICY_CONTROL_EVENT_SCHEMA_ID,
            POLICY_CONTROL_EVENT_SCHEMA_VERSION,
            "durable-evidence",
            [
                "event_schema_id",
                "event_schema_version",
                "event_type",
                "organization_id",
                "occurred_at",
                "opaque actor identity key",
                "version_id",
                "generation",
                "reason_code",
                "content-free structural metadata",
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
    if _value_string(value, "schema_id") != POLICY_DECISION_SCHEMA_ID:
        raise ContractValidationError("policy decision schema_id is unsupported")
    if _value_integer(value, "schema_version", minimum=1) != 1:
        raise ContractValidationError("policy decision schema_version is unsupported")
    if not isinstance(value.get("allowed"), bool):
        raise ContractValidationError("allowed must be a boolean")
    validate_policy_action(_value_string(value, "action"))
    _value_string(value, "reason")
    _value_string(value, "requested_model")
    _nullable_string(value, "resolved_alias")
    _nullable_string(value, "routed_model")
    _nullable_integer(value, "max_output_tokens", minimum=1)
    _value_string(value, "policy_version")


def _validate_policy_comparison(value: Mapping[str, Any]) -> None:
    """Validate a value-bearing administrator-only semantic comparison."""

    _exact_keys(
        value,
        {"schema_id", "schema_version", "organization_id", "baseline", "candidate", "identical", "changes"},
    )
    _value_string(value, "organization_id")
    baseline = _value_mapping(value, "baseline")
    candidate = _value_mapping(value, "candidate")
    _exact_keys(baseline, {"version_id", "content_sha256"}, path="baseline")
    _exact_keys(candidate, {"version_id", "content_sha256"}, path="candidate")
    _validate_policy_version_identity(baseline, path="baseline")
    _validate_policy_version_identity(candidate, path="candidate")
    identical = value.get("identical")
    if not isinstance(identical, bool):
        raise ContractValidationError("identical must be a boolean")
    changes = value.get("changes")
    if not isinstance(changes, list):
        raise ContractValidationError("changes must be an array")
    paths: list[str] = []
    for index, change in enumerate(changes):
        item_path = f"changes[{index}]"
        if not isinstance(change, Mapping):
            raise ContractValidationError(f"{item_path} must be an object")
        _exact_keys(change, {"path", "change_type", "before", "after"}, path=item_path)
        policy_path = _value_string(change, "path", path=item_path)
        if (
            len(policy_path) > 4096
            or "\x00" in policy_path
            or "\n" in policy_path
            or "\r" in policy_path
            or not policy_path.startswith(("policies.", "policies[", "egress_controls."))
        ):
            raise ContractValidationError(f"{item_path}.path is invalid")
        paths.append(policy_path)
        change_type = _value_string(change, "change_type", path=item_path)
        if change_type not in _POLICY_COMPARISON_CHANGE_TYPES:
            raise ContractValidationError(f"{item_path}.change_type is unsupported")
        before = change.get("before")
        after = change.get("after")
        _validate_policy_comparison_value(before, path=f"{item_path}.before")
        _validate_policy_comparison_value(after, path=f"{item_path}.after")
        if change_type == "added" and (before is not None or after is None):
            raise ContractValidationError(f"{item_path} has invalid added values")
        if change_type == "removed" and (before is None or after is not None):
            raise ContractValidationError(f"{item_path} has invalid removed values")
        if change_type == "changed" and (before is None or after is None or before == after):
            raise ContractValidationError(f"{item_path} has invalid changed values")
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ContractValidationError("changes must use unique sorted paths")
    if identical != (len(changes) == 0):
        raise ContractValidationError("identical must match changes")


def _validate_policy_preview(value: Mapping[str, Any]) -> None:
    """Validate one current-usage request preview against pinned documents."""

    _exact_keys(
        value,
        {
            "schema_id",
            "schema_version",
            "organization_id",
            "evaluated_at",
            "usage_period",
            "usage_basis",
            "request",
            "baseline",
            "candidate",
        },
    )
    _value_string(value, "organization_id")
    evaluated_at = _policy_timestamp(_value_string(value, "evaluated_at"), "evaluated_at")
    if evaluated_at.utcoffset() != timedelta(0):
        raise ContractValidationError("evaluated_at must use UTC")
    if _value_string(value, "usage_basis") != "current":
        raise ContractValidationError("usage_basis is unsupported")
    usage_period = _value_mapping(value, "usage_period")
    _exact_keys(usage_period, {"starts_at", "ends_before"}, path="usage_period")
    starts_at = _policy_timestamp(_value_string(usage_period, "starts_at", path="usage_period"), "usage_period.starts_at")
    ends_before = _policy_timestamp(
        _value_string(usage_period, "ends_before", path="usage_period"),
        "usage_period.ends_before",
    )
    if starts_at.utcoffset() != timedelta(0) or ends_before.utcoffset() != timedelta(0):
        raise ContractValidationError("usage_period must use UTC")
    expected_start = evaluated_at.astimezone(timezone.utc).replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    expected_end = (
        expected_start.replace(year=expected_start.year + 1, month=1)
        if expected_start.month == 12
        else expected_start.replace(month=expected_start.month + 1)
    )
    if starts_at != expected_start or ends_before != expected_end:
        raise ContractValidationError("usage_period must be the evaluated UTC month")

    request = _value_mapping(value, "request")
    _exact_keys(
        request,
        {"actor_id", "client", "protocol", "requested_model", "requested_output_tokens"},
        path="request",
    )
    _value_string(request, "actor_id", path="request")
    if _value_string(request, "client", path="request") not in {"codex", "claude-code"}:
        raise ContractValidationError("request.client is unsupported")
    if _value_string(request, "protocol", path="request") not in {"openai", "anthropic"}:
        raise ContractValidationError("request.protocol is unsupported")
    requested_model = _value_string(request, "requested_model", path="request")
    _nullable_integer(request, "requested_output_tokens", minimum=1, path="request")

    baseline = _value_mapping(value, "baseline")
    candidate = _value_mapping(value, "candidate")
    for name, result in (("baseline", baseline), ("candidate", candidate)):
        _exact_keys(result, {"version_id", "content_sha256", "decision"}, path=name)
        version_id = _validate_policy_version_identity(result, path=name)
        decision = _value_mapping(result, "decision", path=name)
        _validate_policy_decision(decision)
        if _value_string(decision, "policy_version", path=f"{name}.decision") != version_id:
            raise ContractValidationError(f"{name}.decision.policy_version does not match version_id")
        if _value_string(decision, "requested_model", path=f"{name}.decision") != requested_model:
            raise ContractValidationError(f"{name}.decision.requested_model does not match request")


def _validate_policy_version_identity(value: Mapping[str, Any], *, path: str) -> str:
    version_id = _value_string(value, "version_id", path=path)
    content_sha256 = _value_string(value, "content_sha256", path=path)
    _policy_version_identifier(version_id, f"{path}.version_id")
    _sha256_digest(content_sha256, f"{path}.content_sha256")
    if version_id != f"sha256:{content_sha256}":
        raise ContractValidationError(f"{path}.version_id does not match content_sha256")
    return version_id


def _validate_policy_comparison_value(value: object, *, path: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        try:
            normalized = float(value)
        except OverflowError:
            raise ContractValidationError(f"{path} has an invalid number") from None
        if not math.isfinite(normalized) or value < 0:
            raise ContractValidationError(f"{path} has an invalid number")
        return
    if isinstance(value, str):
        if not value or len(value) > 1024 or "\x00" in value or "\n" in value or "\r" in value:
            raise ContractValidationError(f"{path} has an invalid string")
        return
    if isinstance(value, list):
        if (
            len(value) > 10_000
            or any(not isinstance(item, str) for item in value)
            or value != sorted(value)
            or len(value) != len(set(value))
        ):
            raise ContractValidationError(f"{path} has an invalid allowlist")
        for index, item in enumerate(value):
            _validate_policy_comparison_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping) and not value:
        return
    raise ContractValidationError(f"{path} has an unsupported value")


def _policy_timestamp(value: str, path: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ContractValidationError(f"{path} must be an ISO-8601 timestamp") from error
    if result.tzinfo is None:
        raise ContractValidationError(f"{path} must include a timezone")
    return result


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


def _validate_policy_history(value: Mapping[str, Any]) -> None:
    """Validate the bounded metadata-only policy lifecycle timeline."""

    _exact_keys(
        value,
        {"schema_id", "schema_version", "organization_id", "limit", "has_more", "events"},
    )
    _value_string(value, "organization_id")
    limit = _value_integer(value, "limit", minimum=1)
    if limit > POLICY_HISTORY_MAX_LIMIT:
        raise ContractValidationError(f"limit must be at most {POLICY_HISTORY_MAX_LIMIT}")
    if not isinstance(value.get("has_more"), bool):
        raise ContractValidationError("has_more must be a boolean")
    events = value.get("events")
    if not isinstance(events, list):
        raise ContractValidationError("events must be an array")
    if len(events) > limit:
        raise ContractValidationError("events must not exceed limit")
    for index, event in enumerate(events):
        path = f"events[{index}]"
        if not isinstance(event, Mapping):
            raise ContractValidationError(f"{path} must be an object")
        _exact_keys(
            event,
            {
                "event_type",
                "version_id",
                "content_sha256",
                "occurred_at",
                "actor_kind",
                "actor_identity_key",
                "generation",
                "change_summary",
            },
            path=path,
        )
        event_type = _value_string(event, "event_type", path=path)
        if event_type not in _POLICY_LIFECYCLE_EVENT_TYPES:
            raise ContractValidationError(f"{path}.event_type is unsupported")
        version_id = _value_string(event, "version_id", path=path)
        _policy_version_identifier(version_id, f"{path}.version_id")
        content_sha256 = _value_string(event, "content_sha256", path=path)
        _sha256_digest(content_sha256, f"{path}.content_sha256")
        if version_id != f"sha256:{content_sha256}":
            raise ContractValidationError(f"{path}.version_id does not match content_sha256")
        _value_string(event, "occurred_at", path=path)
        _administrator_key_fields(event, prefix="actor_", path=path)
        generation = _nullable_integer(event, "generation", minimum=1, path=path)
        if event_type == "policy_staged" and generation is not None:
            raise ContractValidationError(f"{path}.generation must be null for policy_staged")
        if event_type != "policy_staged" and generation is None:
            raise ContractValidationError(f"{path}.generation is required for activation events")
        _validate_redacted_change_summary(_value_mapping(event, "change_summary", path=path))


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
