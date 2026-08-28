"""Ingress construction and credential-resolution ownership."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, replace
from typing import Any

from ._config_values import _environment_name, _integer, _object, _string, _string_tuple
from .config import ConfigError, GatewayConfig, IngressConfig, ListenConfig, MAX_TRUSTED_PROXY_CIDRS


@dataclass(frozen=True)
class IngressConstruction:
    listen: ListenConfig
    ingress: IngressConfig


def build_ingress_domain(raw: dict[str, Any]) -> IngressConstruction:
    listen_raw = _object(raw.get("listen", {}), "listen")
    host = _string(listen_raw.get("host", "127.0.0.1"), "listen.host")
    port = _integer(listen_raw.get("port", 8787), "listen.port", minimum=1, maximum=65535)
    return IngressConstruction(
        listen=ListenConfig(host=host, port=port),
        ingress=_ingress_config(raw.get("ingress", {}), listen_host=host),
    )


def resolve_ingress_credential(ingress: IngressConfig, env: dict[str, str]) -> IngressConfig:
    """Resolve the proxy credential only after every semantic check succeeds."""

    if ingress.mode == "local":
        return ingress
    assert ingress.credential_env is not None
    credential = env.get(ingress.credential_env, "")
    if not credential:
        raise ConfigError(f"Required ingress credential environment variable is not set: {ingress.credential_env}")
    if len(credential) < 16:
        raise ConfigError(f"Ingress credential from {ingress.credential_env} must be at least 16 characters")
    return replace(ingress, credential=credential)


def validate_dedicated_ingress_credential_env(config: GatewayConfig) -> None:
    """Keep the proxy-hop secret separate from every other configured secret."""

    ingress = config.ingress
    if ingress.mode == "local":
        return
    assert ingress.credential_env is not None

    credential_envs = {
        identity.token_env
        for identity in config.identities_by_token.values()
        if identity.token_env
    }
    credential_envs.update(
        upstream.api_key_env
        for upstream in config.upstreams.values()
        if upstream.api_key_env is not None
    )
    credential_envs.update(config.secret_controls.custom_secret_envs)
    if config.usage_storage.backend == "postgresql":
        credential_envs.update(
            {
                config.usage_storage.postgres_dsn_env,
                config.usage_storage.postgres_migration_dsn_env,
            }
        )
    if config.policy_control.mode == "postgresql":
        credential_envs.add(config.policy_control.postgres_control_dsn_env)
        if config.policy_control.break_glass.enabled:
            credential_envs.add(config.policy_control.break_glass.token_env)
    if config.custody_control.mode == "postgresql":
        credential_envs.add(config.custody_control.postgres_control_dsn_env)
        credential_envs.add(config.custody_executor.postgres_executor_dsn_env)

    if ingress.credential_env in credential_envs:
        raise ConfigError("ingress.credential_env must name a credential distinct from all other Hormuz secrets")


def _ingress_config(value: Any, *, listen_host: str) -> IngressConfig:
    """Parse the private proxy hop without resolving its credential yet."""

    item = _object(value, "ingress")
    mode = _string(item.get("mode", "local"), "ingress.mode")
    if mode not in {"local", "external_tls_proxy"}:
        raise ConfigError("ingress.mode must be local or external_tls_proxy")

    if mode == "local":
        if "trusted_proxy_cidrs" in item or "credential_env" in item:
            raise ConfigError("ingress.trusted_proxy_cidrs and ingress.credential_env require external_tls_proxy mode")
        if not _is_loopback_listener(listen_host):
            raise ConfigError("a non-loopback listen.host requires ingress.mode external_tls_proxy")
        return IngressConfig()

    trusted_proxy_cidrs = _string_tuple(
        item.get("trusted_proxy_cidrs", []),
        "ingress.trusted_proxy_cidrs",
    )
    if not trusted_proxy_cidrs:
        raise ConfigError("ingress.trusted_proxy_cidrs must contain at least one network")
    if len(trusted_proxy_cidrs) > MAX_TRUSTED_PROXY_CIDRS:
        raise ConfigError(f"ingress.trusted_proxy_cidrs must contain at most {MAX_TRUSTED_PROXY_CIDRS} networks")

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for index, cidr in enumerate(trusted_proxy_cidrs):
        try:
            networks.append(ipaddress.ip_network(cidr, strict=True))
        except ValueError:
            raise ConfigError(f"ingress.trusted_proxy_cidrs[{index}] must be a canonical CIDR") from None
    if len(networks) != len(set(networks)):
        raise ConfigError("ingress.trusted_proxy_cidrs cannot contain duplicate networks")
    ipv4_networks = [network for network in networks if network.version == 4]
    ipv6_networks = [network for network in networks if network.version == 6]
    collapsed_networks = (
        *tuple(ipaddress.collapse_addresses(ipv4_networks)),
        *tuple(ipaddress.collapse_addresses(ipv6_networks)),
    )
    if any(network.prefixlen == 0 for network in collapsed_networks):
        raise ConfigError("ingress.trusted_proxy_cidrs must not admit every address")

    return IngressConfig(
        mode=mode,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
        trusted_proxy_networks=collapsed_networks,
        credential_env=_environment_name(item.get("credential_env"), "ingress.credential_env"),
    )


def _is_loopback_listener(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
