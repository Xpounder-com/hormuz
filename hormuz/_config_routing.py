"""Provider upstream and model-route configuration construction ownership."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._config_values import (
    _boolean,
    _configured_path,
    _environment_name,
    _number,
    _object,
    _optional_string,
    _string,
)
from .config import ConfigError, KeyCustodyConfig, ModelRoute, UpstreamConfig
from .custody import KEY_PURPOSE_PROVIDER_CREDENTIAL


def build_upstream_domain(
    raw: dict[str, Any],
    *,
    source_path: Path,
    key_custody: KeyCustodyConfig | None,
) -> dict[str, UpstreamConfig]:
    upstreams_raw = _object(raw.get("upstreams"), "upstreams")
    upstreams: dict[str, UpstreamConfig] = {}
    for protocol in ("openai", "anthropic"):
        item = _object(upstreams_raw.get(protocol), f"upstreams.{protocol}")
        unsupported_upstream_fields = set(item).difference(
            {
                "base_url",
                "api_key_env",
                "api_key_envelope",
                "allow_response_storage",
                "allow_background",
            }
        )
        if unsupported_upstream_fields:
            raise ConfigError(
                f"upstreams.{protocol} contains unsupported fields: "
                + ", ".join(sorted(str(field) for field in unsupported_upstream_fields))
            )
        base_url = _string(item.get("base_url"), f"upstreams.{protocol}.base_url").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError(f"upstreams.{protocol}.base_url must be an HTTP(S) URL")
        api_key_env_value = item.get("api_key_env")
        api_key_envelope_value = item.get("api_key_envelope")
        if (api_key_env_value is None) == (api_key_envelope_value is None):
            raise ConfigError(
                f"upstreams.{protocol} must configure exactly one of api_key_env or api_key_envelope"
            )
        api_key_env: str | None = None
        api_key_envelope_path: Path | None = None
        if api_key_env_value is not None:
            api_key_env = _environment_name(api_key_env_value, f"upstreams.{protocol}.api_key_env")
        else:
            if key_custody is None:
                raise ConfigError(f"upstreams.{protocol}.api_key_envelope requires key_custody")
            key_custody.key_reference_for(KEY_PURPOSE_PROVIDER_CREDENTIAL)
            api_key_envelope_path = _configured_path(
                api_key_envelope_value,
                f"upstreams.{protocol}.api_key_envelope",
                source_path.parent,
            )
        upstreams[protocol] = UpstreamConfig(
            base_url=base_url,
            api_key_env=api_key_env,
            api_key_envelope_path=api_key_envelope_path,
            allow_response_storage=_boolean(
                item.get("allow_response_storage", False),
                f"upstreams.{protocol}.allow_response_storage",
            ),
            allow_background=_boolean(
                item.get("allow_background", False),
                f"upstreams.{protocol}.allow_background",
            ),
        )
    return upstreams


def build_model_route_domain(
    raw: dict[str, Any],
    *,
    upstreams: dict[str, UpstreamConfig],
) -> dict[str, ModelRoute]:
    routes_raw = _object(raw.get("model_routes"), "model_routes")
    if not routes_raw:
        raise ConfigError("model_routes must contain at least one route")
    model_routes: dict[str, ModelRoute] = {}
    for alias, value in routes_raw.items():
        if not isinstance(alias, str) or not alias.strip():
            raise ConfigError("model_routes keys must be non-empty strings")
        item = _object(value, f"model_routes.{alias}")
        unsupported_route_fields = set(item).difference(
            {
                "protocol",
                "upstream_model",
                "input_cost_per_million",
                "cache_read_cost_per_million",
                "cache_write_cost_per_million",
                "output_cost_per_million",
                "failover_alias",
            }
        )
        if unsupported_route_fields:
            raise ConfigError(
                f"model_routes.{alias} contains unsupported fields: "
                + ", ".join(sorted(str(field) for field in unsupported_route_fields))
            )
        protocol = _string(item.get("protocol"), f"model_routes.{alias}.protocol")
        if protocol not in upstreams:
            raise ConfigError(f"model_routes.{alias}.protocol must be openai or anthropic")
        model_routes[alias] = ModelRoute(
            alias=alias,
            protocol=protocol,
            upstream_model=_string(item.get("upstream_model"), f"model_routes.{alias}.upstream_model"),
            input_cost_per_million=_number(
                item.get("input_cost_per_million", 0),
                f"model_routes.{alias}.input_cost_per_million",
            ),
            cache_read_cost_per_million=_number(
                item.get("cache_read_cost_per_million", 0),
                f"model_routes.{alias}.cache_read_cost_per_million",
            ),
            cache_write_cost_per_million=_number(
                item.get("cache_write_cost_per_million", 0),
                f"model_routes.{alias}.cache_write_cost_per_million",
            ),
            output_cost_per_million=_number(
                item.get("output_cost_per_million", 0),
                f"model_routes.{alias}.output_cost_per_million",
            ),
            failover_alias=_optional_string(
                item.get("failover_alias"),
                f"model_routes.{alias}.failover_alias",
            ),
        )
    return model_routes
