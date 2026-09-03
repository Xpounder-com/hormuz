"""A closed, provider-free configuration for hosted authentication staging.

This is a separate profile, not an extension to the released gateway JSON.
It deliberately cannot configure static identities, provider keys or routes.
"""

from __future__ import annotations

import ipaddress
import re
import stat
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from ._config_input import _load_configuration_json
from ._config_ingress import resolve_ingress_credential
from ._config_session import resolve_session_credentials, validate_session_references
from .config import (
    GatewayConfig, IngressConfig, ListenConfig, OIDCIssuerConfig, OIDCLoginConfig,
    Policy, SessionBrokerConfig,
)


SECRET_NAMES = ("HORMUZ_INGRESS_CREDENTIAL", "HORMUZ_SESSION_MASTER_KEY", "HORMUZ_OIDC_CLIENT_SECRET")
PROFILE_SCHEMA = "hormuz.hosted-auth-staging/v1"
BACKEND_PORT = 8787


class HostedError(RuntimeError):
    """Only fixed, content-free codes may cross the hosted process boundary."""


def _https_url(value: object, *, origin: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048 or not value.isascii():
        raise HostedError("hosted_https_configuration_required")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https" and parsed.hostname and not parsed.username
            and not parsed.password and not parsed.query and not parsed.fragment
            and not any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in value)
            and re.fullmatch(r"[a-z0-9.-]+(?::[0-9]{1,5})?", parsed.netloc)
            and (parsed.port is None or 1 <= parsed.port <= 65535)
            and not value.endswith("/") and (not origin or not parsed.path)
        )
    except ValueError:
        valid = False
    if not valid:
        raise HostedError("hosted_https_configuration_required")
    return value


def load_profile(path: Path, credentials: dict[str, str]) -> GatewayConfig:
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o022 or details.st_size > 16384:
        raise HostedError("hosted_configuration_file_unsafe")
    raw = _load_configuration_json(path)
    if set(raw) != {
        "schema",
        "public_origin",
        "oidc_issuer",
        "oidc_client_id",
        "state_directory",
        "trusted_parent_path",
    } or raw["schema"] != PROFILE_SCHEMA:
        raise HostedError("hosted_configuration_invalid")
    public_origin = _https_url(raw["public_origin"], origin=True)
    issuer = _https_url(raw["oidc_issuer"])
    client = raw["oidc_client_id"]
    if not isinstance(client, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,256}", client) or client == "hormuz-staging-api":
        raise HostedError("hosted_client_configuration_invalid")
    directory = raw["state_directory"]
    if not isinstance(directory, str) or not directory.startswith("/") or any(ord(c) < 32 for c in directory):
        raise HostedError("hosted_state_path_invalid")
    state = Path(directory)
    if state != state.resolve() or state == state.parent:
        raise HostedError("hosted_state_path_invalid")
    boundary = raw["trusted_parent_path"]
    if not isinstance(boundary, str) or not boundary.startswith("/") or any(ord(c) < 32 for c in boundary):
        raise HostedError("hosted_trusted_parent_path_invalid")
    trusted_parent = Path(boundary)
    if (
        trusted_parent == trusted_parent.parent
        or trusted_parent != trusted_parent.resolve()
        or not state.is_relative_to(trusted_parent)
    ):
        raise HostedError("hosted_trusted_parent_path_invalid")
    ingress = credentials.get("HORMUZ_INGRESS_CREDENTIAL", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", ingress):
        raise HostedError("hosted_ingress_credential_invalid")
    values = [credentials.get(name, "") for name in SECRET_NAMES]
    if len(set(values)) != len(values):
        raise HostedError("hosted_credentials_must_be_distinct")
    config = GatewayConfig(
        source_path=path.absolute(), listen=ListenConfig("127.0.0.1", BACKEND_PORT),
        database_path=state / "usage.sqlite3", upstreams={}, identities_by_token={},
        model_routes={}, organization_policy=Policy(allowed_models=()),
        ingress=resolve_ingress_credential(IngressConfig(
            mode="external_tls_proxy", trusted_proxy_cidrs=("127.0.0.1/32",),
            trusted_proxy_networks=(ipaddress.ip_network("127.0.0.1/32"),),
            credential_env="HORMUZ_INGRESS_CREDENTIAL",
        ), credentials),
        oidc_issuers={issuer: OIDCIssuerConfig(
            issuer=issuer, audiences=("hormuz-staging-api",),
            login=OIDCLoginConfig(client, "HORMUZ_OIDC_CLIENT_SECRET", scopes=("openid", "email")),
        )},
        session_broker=SessionBrokerConfig(
            enabled=True, public_base_url=public_origin, database_path=state / "sessions.sqlite3",
            trusted_parent_path=trusted_parent,
            onboarding_enabled=True, console_enabled=True,
        ),
        max_request_bytes=16384, upstream_timeout_seconds=10,
    )
    validate_session_references(config)
    config.validate_references()
    return resolve_session_credentials(config, credentials)


def at_directory(config: GatewayConfig, directory: Path) -> GatewayConfig:
    """Move a private snapshot without changing origin, issuer or key binding."""
    return replace(config, database_path=directory / "usage.sqlite3",
                   session_broker=replace(config.session_broker, database_path=directory / "sessions.sqlite3"))
