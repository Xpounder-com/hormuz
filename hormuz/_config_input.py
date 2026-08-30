"""Bounded, strict configuration input parsing with no secret resolution.

This module owns raw JSON decoding, structural limits, deprecated-context
rejection, and the supported field schema.  Construction of identities,
storage, policy, custody, and runtime configuration lives in
``_config_builder``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .custody import KEY_PURPOSES


MAX_CONFIGURATION_BYTES = 1 * 1024 * 1024
MAX_CONFIGURATION_DEPTH = 64
MAX_CONFIGURATION_NODES = 100_000


_CONFIGURATION_UNAVAILABLE = "configuration_unavailable"
_CONFIGURATION_TOO_LARGE = "configuration_too_large"
_CONFIGURATION_INVALID_ENCODING = "configuration_invalid_encoding"
_CONFIGURATION_INVALID_JSON = "configuration_invalid_json"
_CONFIGURATION_DUPLICATE_MEMBER = "configuration_duplicate_member"
_CONFIGURATION_NONFINITE_NUMBER = "configuration_nonfinite_number"
_CONFIGURATION_STRUCTURE_LIMIT = "configuration_structure_limit"
_CONFIGURATION_SCHEMA_INVALID = "configuration_schema_invalid"
_CONFIGURATION_UNSUPPORTED_FIELDS = "configuration_unsupported_fields"


_DEPRECATED_CONTEXT_CONFIGURATION_KEYS = frozenset(
    {
        "context_cache",
        "context_database",
        "context_injection",
        "context_lifecycle",
        "context_packs",
        "context_retrieval",
        "context_service",
        "context_storage",
    }
)
_DEPRECATED_CONTEXT_CAPABILITIES = frozenset(
    {"context_injector", "context_promoter", "context_retriever"}
)
_CONTEXT_EXPERIMENT_MOVED_MESSAGE = (
    "context_experiment_moved: legacy context configuration is not supported by the core gateway; "
    "migrate it to hormuz-context-experiment"
)


_ROOT_CONFIGURATION_FIELDS = frozenset(
    {
        "listen",
        "ingress",
        "database",
        "upstreams",
        "identities",
        "authentication",
        "model_routes",
        "egress_controls",
        "policies",
        "max_request_bytes",
        "upstream_timeout_seconds",
        "usage_storage",
        "policy_control",
        "custody_control",
        "custody_executor",
        "custody_retention",
        "custody_lifecycle",
        "key_custody",
        "audit_anchor",
        "audit_chain",
        "portfolio_control",
    }
)
_LISTEN_FIELDS = frozenset({"host", "port"})
_INGRESS_FIELDS = frozenset({"mode", "trusted_proxy_cidrs", "credential_env"})
_UPSTREAM_FIELDS = frozenset(
    {"base_url", "api_key_env", "api_key_envelope", "allow_response_storage", "allow_background"}
)
_IDENTITY_FIELDS = frozenset(
    {
        "token_env",
        "actor_id",
        "actor_name",
        "team_id",
        "team_name",
        "allowed_clients",
        "organization_id",
        "clearance",
        "identity_type",
    }
)
_OIDC_ISSUER_FIELDS = frozenset(
    {
        "issuer",
        "audiences",
        "jwks_uri",
        "algorithms",
        "clock_skew_seconds",
        "discovery_cache_seconds",
        "allow_insecure_http",
        "subjects",
    }
)
_OIDC_SUBJECT_FIELDS = _IDENTITY_FIELDS.difference({"token_env"}).union({"subject"})
_MODEL_ROUTE_FIELDS = frozenset(
    {
        "protocol",
        "upstream_model",
        "input_cost_per_million",
        "cache_read_cost_per_million",
        "cache_write_cost_per_million",
        "output_cost_per_million",
    }
)
_EGRESS_CONTROL_FIELDS = frozenset({"secrets"})
_SECRET_CONTROL_FIELDS = frozenset({"mode", "builtins", "custom_secret_envs"})
_POLICIES_FIELDS = frozenset({"organization", "teams", "actors"})
_POLICY_FIELDS = frozenset(
    {
        "allowed_clients",
        "allowed_models",
        "fallback_model",
        "fallback_models",
        "max_output_tokens",
        "monthly_token_limit",
        "monthly_budget_usd",
        "per_actor_monthly_budget_usd",
    }
)
_FALLBACK_MODEL_FIELDS = frozenset({"openai", "anthropic"})
_USAGE_STORAGE_FIELDS = frozenset(
    {
        "backend",
        "postgres_dsn_env",
        "postgres_migration_dsn_env",
        "postgres_schema",
        "postgres_runtime_role",
        "postgres_pool",
    }
)
_POSTGRES_POOL_FIELDS = frozenset(
    {
        "min_connections",
        "max_connections",
        "acquire_timeout_seconds",
        "max_waiting",
        "max_lifetime_seconds",
        "max_idle_seconds",
    }
)
_POLICY_CONTROL_FIELDS = frozenset(
    {
        "mode",
        "bootstrap_administrators",
        "postgres_control_dsn_env",
        "postgres_control_role",
        "break_glass",
    }
)
_CUSTODY_CONTROL_FIELDS = frozenset(
    {
        "mode",
        "bootstrap_administrators",
        "postgres_control_dsn_env",
        "postgres_control_role",
        "authorization_ttl_seconds",
    }
)
_CUSTODY_EXECUTOR_FIELDS = frozenset(
    {
        "postgres_executor_dsn_env",
        "postgres_executor_role",
        "pending_attempt_ttl_seconds",
    }
)
_CUSTODY_RETENTION_FIELDS = frozenset({"retention_days", "legal_hold"})
_CUSTODY_LIFECYCLE_FIELDS = frozenset({"freshness_lease_seconds", "assets"})
_CUSTODY_LIFECYCLE_ASSET_FIELDS = frozenset({"asset_type", "asset_id", "generation", "binding"})
_CUSTODY_LIFECYCLE_BINDING_FIELDS = frozenset(
    {
        "protocol",
        "path",
        "provider_credential_asset_id",
        "provider_credential_generation",
        "key_reference_asset_id",
        "key_reference_generation",
        "purpose",
        "key_reference",
    }
)
_BREAK_GLASS_FIELDS = frozenset({"enabled", "token_env"})
_BOOTSTRAP_ADMINISTRATOR_FIELDS = frozenset({"organization_id", "actor_id", "issuer", "subject"})
_KEY_CUSTODY_FIELDS = frozenset(
    {"backend", "region", "key_references", "endpoint_url", "token_env", "transit_mount"}
)
_AUDIT_ANCHOR_FIELDS = frozenset(
    {
        "backend",
        "region",
        "bucket",
        "prefix",
        "retention_days",
        "legal_hold",
        "endpoint_url",
        "access_key_env",
        "secret_key_env",
    }
)
_AUDIT_CHAIN_FIELDS = frozenset({"maximum_anchor_age_seconds"})


class ConfigurationInputError(ValueError):
    """A fixed, content-free configuration input failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_configuration_input(source_path: Path) -> dict[str, Any]:
    """Read and validate one raw configuration before any secret lookup."""

    raw = _load_configuration_json(source_path)
    _reject_deprecated_context_configuration(raw)
    _validate_configuration_schema(raw)
    return raw


def _load_configuration_json(source_path: Path) -> dict[str, Any]:
    try:
        with source_path.open("rb") as source:
            encoded = source.read(MAX_CONFIGURATION_BYTES + 1)
    except OSError:
        raise ConfigurationInputError(_CONFIGURATION_UNAVAILABLE) from None
    if len(encoded) > MAX_CONFIGURATION_BYTES:
        raise ConfigurationInputError(_CONFIGURATION_TOO_LARGE)
    try:
        decoded = encoded.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigurationInputError(_CONFIGURATION_INVALID_ENCODING) from None
    try:
        raw = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_nonfinite_json_number,
        )
    except ConfigurationInputError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError):
        raise ConfigurationInputError(_CONFIGURATION_INVALID_JSON) from None
    if not isinstance(raw, dict):
        raise ConfigurationInputError(_CONFIGURATION_SCHEMA_INVALID)
    _validate_configuration_structure(raw)
    return raw


def _reject_duplicate_json_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationInputError(_CONFIGURATION_DUPLICATE_MEMBER)
        result[key] = value
    return result


def _reject_nonfinite_json_number(_value: str) -> None:
    raise ConfigurationInputError(_CONFIGURATION_NONFINITE_NUMBER)


def _validate_configuration_structure(raw: object) -> None:
    pending: list[tuple[object, int]] = [(raw, 1)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > MAX_CONFIGURATION_NODES or depth > MAX_CONFIGURATION_DEPTH:
            raise ConfigurationInputError(_CONFIGURATION_STRUCTURE_LIMIT)
        if isinstance(value, float) and not math.isfinite(value):
            raise ConfigurationInputError(_CONFIGURATION_NONFINITE_NUMBER)
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def _reject_deprecated_context_configuration(raw: dict[str, Any]) -> None:
    pending: list[Any] = [raw]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in _DEPRECATED_CONTEXT_CONFIGURATION_KEYS:
                    raise ConfigurationInputError(_CONTEXT_EXPERIMENT_MOVED_MESSAGE)
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and value in _DEPRECATED_CONTEXT_CAPABILITIES:
            raise ConfigurationInputError(_CONTEXT_EXPERIMENT_MOVED_MESSAGE)


def _validate_configuration_schema(raw: dict[str, Any]) -> None:
    _schema_object(raw, _ROOT_CONFIGURATION_FIELDS)

    _schema_optional_object(raw, "listen", _LISTEN_FIELDS)
    _schema_optional_object(raw, "ingress", _INGRESS_FIELDS)

    upstreams = _schema_required_object(raw, "upstreams", frozenset({"openai", "anthropic"}))
    for value in upstreams.values():
        _schema_object(value, _UPSTREAM_FIELDS)

    for value in _schema_optional_array(raw, "identities"):
        _schema_object(value, _IDENTITY_FIELDS)

    authentication = _schema_optional_object(raw, "authentication", frozenset({"oidc"}))
    if authentication is not None:
        oidc = _schema_optional_object(authentication, "oidc", frozenset({"issuers"}))
        if oidc is not None:
            for issuer in _schema_optional_array(oidc, "issuers"):
                issuer_object = _schema_object(issuer, _OIDC_ISSUER_FIELDS)
                for subject in _schema_optional_array(issuer_object, "subjects"):
                    _schema_object(subject, _OIDC_SUBJECT_FIELDS)

    for value in _schema_required_mapping(raw, "model_routes").values():
        _schema_object(value, _MODEL_ROUTE_FIELDS)

    egress = _schema_optional_object(raw, "egress_controls", _EGRESS_CONTROL_FIELDS)
    if egress is not None:
        _schema_optional_object(egress, "secrets", _SECRET_CONTROL_FIELDS)

    if "policies" in raw:
        policies = _schema_object(raw["policies"], _POLICIES_FIELDS)
        if "organization" in policies:
            _validate_policy_schema(policies["organization"])
        for scope in ("teams", "actors"):
            if scope not in policies:
                continue
            for policy in _schema_mapping(policies[scope]).values():
                _validate_policy_schema(policy)

    usage_storage = _schema_optional_object(raw, "usage_storage", _USAGE_STORAGE_FIELDS)
    if usage_storage is not None:
        _schema_optional_object(usage_storage, "postgres_pool", _POSTGRES_POOL_FIELDS)

    policy_control = _schema_optional_object(raw, "policy_control", _POLICY_CONTROL_FIELDS)
    if policy_control is not None:
        _schema_optional_object(policy_control, "break_glass", _BREAK_GLASS_FIELDS)
        for administrator in _schema_optional_array(policy_control, "bootstrap_administrators"):
            _schema_object(administrator, _BOOTSTRAP_ADMINISTRATOR_FIELDS)

    custody_control = _schema_optional_object(raw, "custody_control", _CUSTODY_CONTROL_FIELDS)
    if custody_control is not None:
        for administrator in _schema_optional_array(custody_control, "bootstrap_administrators"):
            _schema_object(administrator, _BOOTSTRAP_ADMINISTRATOR_FIELDS)

    _schema_optional_object(raw, "custody_executor", _CUSTODY_EXECUTOR_FIELDS)
    _schema_optional_object(raw, "custody_retention", _CUSTODY_RETENTION_FIELDS)

    custody_lifecycle = _schema_optional_object(raw, "custody_lifecycle", _CUSTODY_LIFECYCLE_FIELDS)
    if custody_lifecycle is not None:
        for asset in _schema_optional_array(custody_lifecycle, "assets"):
            asset_object = _schema_object(asset, _CUSTODY_LIFECYCLE_ASSET_FIELDS)
            _schema_required_object(asset_object, "binding", _CUSTODY_LIFECYCLE_BINDING_FIELDS)

    if "key_custody" in raw and raw["key_custody"] is not None:
        key_custody = _schema_object(raw["key_custody"], _KEY_CUSTODY_FIELDS)
        if "key_references" in key_custody:
            _schema_object(key_custody["key_references"], frozenset(KEY_PURPOSES))

    if "audit_anchor" in raw and raw["audit_anchor"] is not None:
        _schema_object(raw["audit_anchor"], _AUDIT_ANCHOR_FIELDS)
    if "audit_chain" in raw and raw["audit_chain"] is not None:
        _schema_object(raw["audit_chain"], _AUDIT_CHAIN_FIELDS)


def _validate_policy_schema(value: object) -> None:
    policy = _schema_object(value, _POLICY_FIELDS)
    if "fallback_models" in policy and policy["fallback_models"] is not None:
        _schema_object(policy["fallback_models"], _FALLBACK_MODEL_FIELDS)


def _schema_required_object(
    parent: dict[str, Any],
    key: str,
    allowed_fields: frozenset[str],
) -> dict[str, Any]:
    return _schema_object(parent.get(key), allowed_fields)


def _schema_optional_object(
    parent: dict[str, Any],
    key: str,
    allowed_fields: frozenset[str],
) -> dict[str, Any] | None:
    if key not in parent:
        return None
    return _schema_object(parent[key], allowed_fields)


def _schema_required_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    return _schema_mapping(parent.get(key))


def _schema_optional_array(parent: dict[str, Any], key: str) -> list[Any]:
    if key not in parent:
        return []
    value = parent[key]
    if not isinstance(value, list):
        raise ConfigurationInputError(_CONFIGURATION_SCHEMA_INVALID)
    return value


def _schema_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationInputError(_CONFIGURATION_SCHEMA_INVALID)
    return value


def _schema_object(value: object, allowed_fields: frozenset[str]) -> dict[str, Any]:
    result = _schema_mapping(value)
    if set(result).difference(allowed_fields):
        raise ConfigurationInputError(_CONFIGURATION_UNSUPPORTED_FIELDS)
    return result
