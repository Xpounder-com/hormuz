"""Static and generic-OIDC identity configuration construction ownership."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._config_values import (
    _boolean,
    _environment_name,
    _integer,
    _object,
    _string,
    _string_tuple,
    _url,
)
from .config import BootstrapAdministrator, ConfigError, Identity, OIDCIssuerConfig


@dataclass(frozen=True)
class IdentityConstruction:
    static_identities: tuple[Identity, ...]
    oidc_issuers: dict[str, OIDCIssuerConfig]
    identities_by_subject: dict[tuple[str, str], Identity]


def build_identity_domain(raw: dict[str, Any]) -> IdentityConstruction:
    identities_raw = raw.get("identities", [])
    if not isinstance(identities_raw, list):
        raise ConfigError("identities must be an array")
    static_identities: list[Identity] = []
    for index, value in enumerate(identities_raw):
        item = _object(value, f"identities[{index}]")
        prefix = f"identities[{index}]"
        if ("token_env" in item) == ("token_file" in item):
            raise ConfigError("identity requires exactly one token source")
        token_env = _environment_name(item["token_env"], f"{prefix}.token_env") if "token_env" in item else ""
        token_file = Path(_string(item["token_file"], f"{prefix}.token_file")) if "token_file" in item else None
        if token_file is not None and not token_file.is_absolute():
            raise ConfigError("identity credential file must be absolute")
        identity = Identity(
            token_env=token_env,
            token_file=token_file,
            token="",
            actor_id=_string(item.get("actor_id"), f"{prefix}.actor_id"),
            actor_name=_string(item.get("actor_name"), f"{prefix}.actor_name"),
            team_id=_string(item.get("team_id"), f"{prefix}.team_id"),
            team_name=_string(item.get("team_name"), f"{prefix}.team_name"),
            allowed_clients=_string_tuple(item.get("allowed_clients", []), f"{prefix}.allowed_clients"),
            organization_id=_string(item.get("organization_id", "organization"), f"{prefix}.organization_id"),
            clearance=_classification(item.get("clearance", "internal"), f"{prefix}.clearance"),
            identity_type=_identity_type(item.get("identity_type", "human"), f"{prefix}.identity_type"),
        )
        static_identities.append(identity)

    if not static_identities:
        raise ConfigError("At least one static router identity is required")
    _validate_identity_consistency(tuple(static_identities))
    return IdentityConstruction(tuple(static_identities), {}, {})


def resolve_static_identity_tokens(
    identities: tuple[Identity, ...],
    env: dict[str, str],
) -> dict[str, Identity]:
    """Resolve static tokens after every non-secret config invariant is valid."""

    resolved: dict[str, Identity] = {}
    for identity in identities:
        if identity.token_file is not None:
            from .local_credentials import LocalCredentialError, read_local_credential
            try:
                token = read_local_credential(identity.token_file)
            except LocalCredentialError:
                raise ConfigError("local_credential_unavailable") from None
        else:
            token = env.get(identity.token_env, "")
        if not token:
            raise ConfigError(f"Required identity token environment variable is not set: {identity.token_env}")
        if len(token) < 16:
            raise ConfigError(f"Identity token from {identity.token_env} must be at least 16 characters")
        if token in resolved:
            raise ConfigError(f"Identity tokens must be unique; duplicate value from {identity.token_env}")
        resolved[token] = replace(identity, token=token)
    return resolved


def _classification(value: Any, path: str) -> str:
    result = _string(value, path)
    if result not in {"public", "internal", "confidential", "restricted"}:
        raise ConfigError(f"{path} must be public, internal, confidential, or restricted")
    return result


def _identity_type(value: Any, path: str) -> str:
    result = _string(value, path)
    if result not in {"human", "service_account", "ci", "connector"}:
        raise ConfigError(f"{path} must be human, service_account, ci, or connector")
    return result


def _validate_identity_consistency(identities: tuple[Identity, ...]) -> None:
    by_actor: dict[str, Identity] = {}
    for identity in identities:
        existing = by_actor.get(identity.actor_id)
        if existing is None:
            by_actor[identity.actor_id] = identity
            continue
        fields = (
            "actor_name",
            "team_id",
            "team_name",
            "allowed_clients",
            "organization_id",
            "clearance",
            "identity_type",
        )
        if any(getattr(existing, name) != getattr(identity, name) for name in fields):
            raise ConfigError(
                f"Identity metadata for actor {identity.actor_id} must match across authentication sources"
            )
