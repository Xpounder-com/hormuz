"""Opt-in, operator-owned registry authority; no role is inferred from policy.

Configuration is a trusted self-hosted control boundary, not an API. Connector
IDs are operator-registered allowlists, not a claim of live provider validation.
The later connector adapters must verify signed source context independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
import re

from .portfolio_wire import PortfolioError, canonical, validate

if TYPE_CHECKING:
    from .config import Identity


ROLES = frozenset({"portfolio_admin", "finance_viewer", "platform_viewer", "team_lead"})


@dataclass(frozen=True)
class PortfolioRoleBinding:
    organization_id: str
    actor_id: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioConnectorBinding:
    organization_id: str
    connector_id: str
    provider: str
    installation_id: str | None
    workspace_id: str | None
    external_object_ids: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioConfig:
    role_bindings: tuple[PortfolioRoleBinding, ...]
    connectors: tuple[PortfolioConnectorBinding, ...]


@dataclass(frozen=True)
class PortfolioPrincipal:
    organization_id: str
    actor_id: str
    roles: tuple[str, ...]

    @property
    def cursor_authority(self) -> str:
        return canonical([self.organization_id, self.actor_id, self.roles])


def authorize(config: PortfolioConfig | None, identity: Identity) -> PortfolioPrincipal:
    if config is not None:
        for binding in config.role_bindings:
            if (identity.organization_id, identity.actor_id) == (binding.organization_id, binding.actor_id):
                if "portfolio_admin" in binding.roles:
                    return PortfolioPrincipal(binding.organization_id, binding.actor_id, binding.roles)
    # Aggregate viewers and team leads gain no raw registry capability in #215.
    raise PortfolioError("forbidden")


def build_portfolio_config(value: object, identities: tuple[Identity, ...]) -> PortfolioConfig | None:
    from .config import ConfigError

    def fail():
        raise ConfigError("portfolio_configuration_invalid")

    def exact(item, fields):
        if not isinstance(item, dict) or set(item) != set(fields):
            fail()

    def bounded_list(item, maximum):
        if not isinstance(item, list) or len(item) > maximum:
            fail()

    def opaque(item):
        try:
            validate(item, "opaque_id")
        except PortfolioError:
            fail()

    if value is None:
        return None
    exact(value, {"schema_id", "schema_version", "role_bindings", "connectors"})
    if value["schema_id"] != "hormuz.portfolio-control" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail()
    bounded_list(value["role_bindings"], 1000)
    bounded_list(value["connectors"], 1000)
    known = {(identity.organization_id, identity.actor_id) for identity in identities}
    organizations = {identity.organization_id for identity in identities}
    roles, connectors, seen_roles, seen_connectors = [], [], set(), set()
    for item in value["role_bindings"]:
        exact(item, {"organization_id", "actor_id", "roles"})
        opaque(item["organization_id"])
        opaque(item["actor_id"])
        key = (item["organization_id"], item["actor_id"])
        bounded_list(item["roles"], 4)
        if not item["roles"] or any(not isinstance(role, str) or role not in ROLES for role in item["roles"]):
            fail()
        if key not in known or key in seen_roles or len(set(item["roles"])) != len(item["roles"]):
            fail()
        seen_roles.add(key)
        roles.append(PortfolioRoleBinding(*key, tuple(sorted(item["roles"]))))
    for item in value["connectors"]:
        exact(item, {"organization_id", "connector_id", "provider", "installation_id", "workspace_id", "external_object_ids"})
        opaque(item["organization_id"])
        opaque(item["connector_id"])
        key = (item["organization_id"], item["connector_id"])
        if item["organization_id"] not in organizations or key in seen_connectors:
            fail()
        bounded_list(item["external_object_ids"], 1000)
        # Registry bindings cover GitHub repository IDs or Linear project IDs.
        # Provider display names/URLs/node titles cannot pass as those IDs.
        if item["provider"] == "github":
            pattern = r"[1-9][0-9]{0,19}"
            authority_id = item["installation_id"]
            if item["workspace_id"] is not None:
                fail()
        elif item["provider"] == "linear":
            pattern = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
            authority_id = item["workspace_id"]
            if item["installation_id"] is not None:
                fail()
        else:
            fail()
        for source_id in [authority_id, *item["external_object_ids"]]:
            if not isinstance(source_id, str) or not re.fullmatch(pattern, source_id):
                fail()
        if len(set(item["external_object_ids"])) != len(item["external_object_ids"]):
            fail()
        seen_connectors.add(key)
        connectors.append(PortfolioConnectorBinding(
            *key, item["provider"], item["installation_id"], item["workspace_id"], tuple(sorted(item["external_object_ids"])),
        ))
    return PortfolioConfig(tuple(roles), tuple(connectors))
