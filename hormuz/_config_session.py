"""Validate browser-login configuration before resolving any secret values."""

from __future__ import annotations

import base64
import binascii
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ._config_values import _boolean, _environment_name, _integer, _object, _string, _string_tuple
from .config import ConfigError, GatewayConfig, OIDCLoginConfig, SessionBrokerConfig


def build_oidc_login(value: object, *, prefix: str) -> OIDCLoginConfig | None:
    if value is None:
        return None
    item = _object(value, prefix)
    scopes = _string_tuple(item.get("scopes", ["openid"]), f"{prefix}.scopes")
    if "openid" not in scopes or "offline_access" in scopes or any(" " in scope for scope in scopes):
        raise ConfigError("login scopes must include openid and must not request offline_access")
    method = _string(item.get("token_endpoint_auth_method", "client_secret_basic"), f"{prefix}.token_endpoint_auth_method")
    if method not in {"client_secret_basic", "client_secret_post"}:
        raise ConfigError("login requires client_secret_basic or client_secret_post")
    return OIDCLoginConfig(
        client_id=_string(item.get("client_id"), f"{prefix}.client_id"),
        client_secret_env=_environment_name(item.get("client_secret_env"), f"{prefix}.client_secret_env"),
        scopes=scopes,
        token_endpoint_auth_method=method,
    )


def build_session_broker(raw: dict[str, Any], *, source_path: Path) -> SessionBrokerConfig:
    item = raw.get("authentication", {}).get("session_broker", {})
    enabled = _boolean(item.get("enabled", False), "authentication.session_broker.enabled")
    if not enabled:
        if set(item) - {"enabled"}:
            raise ConfigError("disabled session broker must not contain active settings")
        return SessionBrokerConfig()
    prefix = "authentication.session_broker"
    public_url = _string(item.get("public_base_url"), f"{prefix}.public_base_url").rstrip("/")
    insecure = _boolean(item.get("allow_insecure_http", False), f"{prefix}.allow_insecure_http")
    try:
        parsed = urlsplit(public_url)
        parsed.port
        safe = (
            parsed.hostname and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment and not parsed.path
            and not any(ord(c) < 33 or ord(c) == 127 for c in public_url)
        )
    except ValueError:
        safe = False
    if not safe or not (
        parsed.scheme == "https"
        or insecure and parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ConfigError("session public_base_url requires HTTPS; explicit HTTP is loopback-only")
    database = Path(_string(item.get("database", "./sessions.sqlite3"), f"{prefix}.database")).expanduser()
    if not database.is_absolute():
        database = source_path.parent / database
    access_ttl = _integer(item.get("access_ttl_seconds", 600), f"{prefix}.access_ttl_seconds", minimum=300, maximum=900)
    return SessionBrokerConfig(
        enabled=True,
        public_base_url=public_url,
        database_path=database.absolute(),
        master_key_env=_environment_name(item.get("master_key_env", "HORMUZ_SESSION_MASTER_KEY"), f"{prefix}.master_key_env"),
        access_ttl_seconds=access_ttl,
        absolute_ttl_seconds=_integer(item.get("absolute_ttl_seconds", 43200), f"{prefix}.absolute_ttl_seconds", minimum=access_ttl, maximum=43200),
        enrollment_ttl_seconds=_integer(item.get("enrollment_ttl_seconds", 300), f"{prefix}.enrollment_ttl_seconds", minimum=60, maximum=600),
        allow_insecure_http=insecure,
        onboarding_enabled=_boolean(item.get("onboarding_enabled", False), f"{prefix}.onboarding_enabled"),
        console_enabled=_boolean(item.get("console_enabled", False), f"{prefix}.console_enabled"),
    )


def validate_session_references(config: GatewayConfig) -> None:
    issuers = [issuer for issuer in config.oidc_issuers.values() if issuer.login is not None]
    broker = config.session_broker
    if not broker.enabled:
        if issuers:
            raise ConfigError("OIDC login requires an enabled session broker")
        return
    if not issuers:
        raise ConfigError("session broker requires at least one OIDC login issuer")
    if broker.console_enabled and not broker.onboarding_enabled:
        raise ConfigError("administrator console requires managed team onboarding")
    if broker.database_path is None or broker.database_path.resolve() == config.database_path.resolve():
        raise ConfigError("session database must be separate from usage storage")
    if broker.allow_insecure_http and config.ingress.mode != "local":
        raise ConfigError("insecure session login is restricted to local ingress")
    for issuer in issuers:
        if issuer.login.client_id in issuer.audiences:
            raise ConfigError("OIDC login client_id must differ from gateway resource audiences")
    secret_envs = {identity.token_env for identity in config.identities_by_token.values()}
    secret_envs.update(upstream.api_key_env for upstream in config.upstreams.values() if upstream.api_key_env)
    if config.ingress.credential_env:
        secret_envs.add(config.ingress.credential_env)
    for issuer in issuers:
        if issuer.login.client_secret_env in secret_envs or issuer.login.client_secret_env == broker.master_key_env:
            raise ConfigError("OIDC login secrets must use dedicated environment variables")
    if broker.master_key_env in secret_envs:
        raise ConfigError("session master key must use a dedicated environment variable")


def resolve_session_credentials(config: GatewayConfig, environ: dict[str, str]) -> GatewayConfig:
    broker = config.session_broker
    if not broker.enabled:
        return config
    encoded_key = environ.get(broker.master_key_env, "")
    try:
        master_key = base64.b64decode(encoded_key, validate=True)
    except (ValueError, binascii.Error):
        raise ConfigError("session master key must be base64-encoded 32 random bytes") from None
    if len(master_key) != 32:
        raise ConfigError("session master key must be base64-encoded 32 random bytes")
    issuers = dict(config.oidc_issuers)
    for name, issuer in issuers.items():
        login = issuer.login
        if login is None:
            continue
        secret = environ.get(login.client_secret_env, "")
        if not 1 <= len(secret.encode("utf-8")) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in secret):
            raise ConfigError("OIDC login client secret is missing or invalid")
        issuers[name] = replace(issuer, login=replace(login, client_secret=secret))
    return replace(config, session_broker=replace(broker, master_key=master_key), oidc_issuers=issuers)
