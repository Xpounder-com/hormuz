"""Provider upstream and model-route configuration construction ownership."""

from __future__ import annotations

from pathlib import Path
import ipaddress
import re
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
from .finance_account_binding import UnavailableFinance, parse_finance_identity
from .native_codex_catalog import NATIVE_ORIGIN


def build_upstream_domain(
    raw: dict[str, Any],
    *,
    source_path: Path,
    key_custody: KeyCustodyConfig | None,
) -> dict[str, UpstreamConfig]:
    upstreams_raw = _object(raw.get("upstreams"), "upstreams")
    if not upstreams_raw:
        raise ConfigError("upstreams must contain at least one destination")
    if raw.get("custody_lifecycle") is not None and set(upstreams_raw) - {"openai", "anthropic"}:
        raise ConfigError("named destinations are not qualified for custody_lifecycle")
    upstreams: dict[str, UpstreamConfig] = {}
    for name, value in upstreams_raw.items():
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) is None:
            raise ConfigError("upstreams keys must be destination identifiers")
        item = _object(value, f"upstreams.{name}")
        protocol = _string(item.get("protocol", name), f"upstreams.{name}.protocol")
        if protocol not in {"openai", "anthropic"} or name in {"openai", "anthropic"} and protocol != name:
            raise ConfigError(f"upstreams.{name}.protocol must be openai or anthropic")
        unsupported_upstream_fields = set(item).difference(
            {
                "base_url",
                "api_key_env",
                "api_key_envelope",
                "allow_response_storage",
                "allow_background",
                "finance_identity",
                "protocol", "locality", "authentication",
            }
        )
        if unsupported_upstream_fields:
            raise ConfigError(
                f"upstreams.{name} contains unsupported fields: "
                + ", ".join(sorted(str(field) for field in unsupported_upstream_fields))
            )
        base_url = _string(item.get("base_url"), f"upstreams.{name}.base_url").rstrip("/")
        try:
            parsed = urlparse(base_url)
            parsed.port  # Validate explicit port syntax and range before startup.
            if any(ord(char) <= 32 or ord(char) == 127 for char in base_url):
                raise ValueError("invalid origin")
        except ValueError:
            raise ConfigError(f"upstreams.{name}.base_url must be an HTTP(S) origin") from None
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ConfigError(f"upstreams.{name}.base_url must be an HTTP(S) URL")
        locality = _string(item.get("locality", "cloud"), f"upstreams.{name}.locality")
        authentication = _string(item.get("authentication", "api_key"), f"upstreams.{name}.authentication")
        if locality not in {"local", "cloud"} or authentication not in {"none", "api_key", "codex_chatgpt"}:
            raise ConfigError(f"upstreams.{name} has invalid locality or authentication")
        if locality == "local":
            try:
                loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise ConfigError(f"upstreams.{name} local destination requires a literal loopback address")
        if authentication == "none" and locality != "local":
            raise ConfigError(f"upstreams.{name} unauthenticated destination must be local")
        api_key_env_value = item.get("api_key_env")
        api_key_envelope_value = item.get("api_key_envelope")
        if authentication == "codex_chatgpt" and (
                protocol != "openai" or locality != "cloud" or base_url != NATIVE_ORIGIN
                or api_key_env_value is not None or api_key_envelope_value is not None
                or item.get("allow_response_storage", False) is not False
                or item.get("allow_background", False) is not False):
            raise ConfigError(f"upstreams.{name} native Codex authentication requires its fixed cloud backend and no API credential")
        if authentication == "none" and (api_key_env_value is not None or api_key_envelope_value is not None):
            raise ConfigError(f"upstreams.{name} unauthenticated destination cannot configure a credential")
        if authentication == "api_key" and (api_key_env_value is None) == (api_key_envelope_value is None):
            raise ConfigError(
                f"upstreams.{name} must configure exactly one of api_key_env or api_key_envelope"
            )
        api_key_env: str | None = None
        api_key_envelope_path: Path | None = None
        if api_key_env_value is not None:
            api_key_env = _environment_name(api_key_env_value, f"upstreams.{name}.api_key_env")
        elif api_key_envelope_value is not None:
            if key_custody is None:
                raise ConfigError(f"upstreams.{name}.api_key_envelope requires key_custody")
            key_custody.key_reference_for(KEY_PURPOSE_PROVIDER_CREDENTIAL)
            api_key_envelope_path = _configured_path(
                api_key_envelope_value,
                f"upstreams.{name}.api_key_envelope",
                source_path.parent,
            )
        upstreams[name] = UpstreamConfig(
            base_url=base_url,
            api_key_env=api_key_env,
            api_key_envelope_path=api_key_envelope_path,
            allow_response_storage=_boolean(
                item.get("allow_response_storage", False),
                f"upstreams.{name}.allow_response_storage",
            ),
            allow_background=_boolean(
                item.get("allow_background", False),
                f"upstreams.{name}.allow_background",
            ),
            finance_identity=(
                parse_finance_identity(item["finance_identity"])
                if "finance_identity" in item else UnavailableFinance("not_configured")
            ),
            protocol=protocol if "protocol" in item or name not in {"openai", "anthropic"} else None,
            locality=locality, authentication=authentication,
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
                "destination",
                "model_revision", "capabilities",
            }
        )
        if unsupported_route_fields:
            raise ConfigError(
                f"model_routes.{alias} contains unsupported fields: "
                + ", ".join(sorted(str(field) for field in unsupported_route_fields))
            )
        protocol = _string(item.get("protocol"), f"model_routes.{alias}.protocol")
        if protocol not in {"openai", "anthropic"}:
            raise ConfigError(f"model_routes.{alias}.protocol must be openai or anthropic")
        destination = _optional_string(item.get("destination"), f"model_routes.{alias}.destination")
        upstream = upstreams.get(destination or protocol)
        if upstream is None or (upstream.protocol or destination or protocol) != protocol:
            raise ConfigError(f"model_routes.{alias}.destination must reference a matching protocol")
        revision = _optional_string(item.get("model_revision"), f"model_routes.{alias}.model_revision")
        if revision is not None and (len(revision) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in revision)):
            raise ConfigError(f"model_routes.{alias}.model_revision must be bounded printable text")
        capabilities = item.get("capabilities", [])
        if (not isinstance(capabilities, list) or len(capabilities) > 16
                or any(not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value) is None
                       for value in capabilities) or len(set(capabilities)) != len(capabilities)):
            raise ConfigError(f"model_routes.{alias}.capabilities must be distinct bounded identifiers")
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
            destination=destination,
            model_revision=revision, capabilities=tuple(capabilities),
            explicit_price_fields=frozenset(name for name in (
                "input_cost_per_million", "output_cost_per_million",
                "cache_read_cost_per_million", "cache_write_cost_per_million",
            ) if name in item),
        )
    return model_routes
