"""Persistence configuration construction ownership."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._config_values import _object, _string
from .config import ConfigError, UsageStorageConfig


@dataclass(frozen=True)
class PersistenceConstruction:
    database_path: Path
    usage_storage: UsageStorageConfig


def build_persistence_domain(
    raw: dict[str, Any],
    *,
    source_path: Path,
) -> PersistenceConstruction:
    database_value = _string(raw.get("database", "./hormuz.sqlite3"), "database")
    database_path = Path(database_value).expanduser()
    if not database_path.is_absolute():
        database_path = (source_path.parent / database_path).resolve()

    usage_storage_raw = _object(raw.get("usage_storage", {}), "usage_storage")
    if usage_storage_raw.get("backend", "sqlite") != "sqlite" or set(usage_storage_raw) - {"backend"}:
        raise ConfigError("router_scope_retired_configuration")
    return PersistenceConstruction(database_path, UsageStorageConfig())
