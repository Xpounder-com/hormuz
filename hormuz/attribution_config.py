"""Restart-bound operator authority for optional work attribution.

Bindings name existing identities/clients and exact authorized use-case
versions. They grant no inference, policy, or administrator capability.
Multiple explicitly configured defaults remain ambiguous, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .portfolio_wire import PortfolioError, validate

if TYPE_CHECKING:
    from .config import Identity


@dataclass(frozen=True)
class WorkScopeRef:
    work_scope_id: str
    version: int


@dataclass(frozen=True)
class AttributionBinding:
    organization_id: str
    actor_id: str
    client: str
    allowed_work_scopes: tuple[WorkScopeRef, ...]
    default_work_scopes: tuple[WorkScopeRef, ...]
    require_scope: bool


@dataclass(frozen=True)
class AttributionConfig:
    bindings: tuple[AttributionBinding, ...]


def build_attribution_config(value: object, identities: tuple[Identity, ...]) -> AttributionConfig | None:
    from .config import ConfigError

    def fail():
        raise ConfigError("attribution_configuration_invalid")

    def exact(item, fields):
        if not isinstance(item, dict) or set(item) != set(fields):
            fail()

    def references(items):
        if not isinstance(items, list) or len(items) > 128:
            fail()
        result = []
        for item in items:
            exact(item, {"work_scope_id", "version"})
            try:
                validate(item["work_scope_id"], "opaque_id")
            except PortfolioError:
                fail()
            if type(item["version"]) is not int or not 1 <= item["version"] <= 2147483647:
                fail()
            result.append(WorkScopeRef(item["work_scope_id"], item["version"]))
        if len(set(result)) != len(result):
            fail()
        return tuple(result)

    if value is None:
        return None
    exact(value, {"schema_id", "schema_version", "bindings"})
    if value["schema_id"] != "hormuz.attribution-control" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail()
    if not isinstance(value["bindings"], list) or len(value["bindings"]) > 1000:
        fail()
    result, seen = [], set()
    for item in value["bindings"]:
        exact(item, {"organization_id", "actor_id", "client", "allowed_work_scopes", "default_work_scopes", "require_scope"})
        for field in ("organization_id", "actor_id"):
            try:
                validate(item[field], "opaque_id")
            except PortfolioError:
                fail()
        if not isinstance(item["client"], str) or item["client"] not in {"codex", "claude-code"} or type(item["require_scope"]) is not bool:
            fail()
        key = item["organization_id"], item["actor_id"], item["client"]
        matches = [identity for identity in identities if (identity.organization_id, identity.actor_id) == key[:2]]
        if key in seen or not matches or any(identity.allowed_clients and item["client"] not in identity.allowed_clients for identity in matches):
            fail()
        allowed, defaults = references(item["allowed_work_scopes"]), references(item["default_work_scopes"])
        if not set(defaults).issubset(allowed):
            fail()
        result.append(AttributionBinding(*key, allowed, defaults, item["require_scope"]))
        seen.add(key)
    return AttributionConfig(tuple(result))
