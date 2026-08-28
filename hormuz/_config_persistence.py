"""Persistence configuration construction ownership."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._config_values import (
    _environment_name,
    _integer,
    _object,
    _postgres_identifier,
    _string,
)
from .config import ConfigError, PostgresPoolConfig, UsageStorageConfig


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
    unsupported_storage_fields = set(usage_storage_raw).difference(
        {
            "backend",
            "postgres_dsn_env",
            "postgres_migration_dsn_env",
            "postgres_schema",
            "postgres_runtime_role",
            "postgres_pool",
        }
    )
    if unsupported_storage_fields:
        raise ConfigError(
            "usage_storage contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported_storage_fields))
        )
    usage_backend = _string(usage_storage_raw.get("backend", "sqlite"), "usage_storage.backend")
    if usage_backend not in {"sqlite", "postgresql"}:
        raise ConfigError("usage_storage.backend must be sqlite or postgresql")
    postgres_dsn_env = _environment_name(
        usage_storage_raw.get("postgres_dsn_env", "HORMUZ_POSTGRES_DSN"),
        "usage_storage.postgres_dsn_env",
    )
    postgres_migration_dsn_env = _environment_name(
        usage_storage_raw.get("postgres_migration_dsn_env", "HORMUZ_POSTGRES_MIGRATION_DSN"),
        "usage_storage.postgres_migration_dsn_env",
    )
    postgres_schema = _postgres_identifier(
        usage_storage_raw.get("postgres_schema", "hormuz"),
        "usage_storage.postgres_schema",
    )
    postgres_runtime_role = _postgres_identifier(
        usage_storage_raw.get("postgres_runtime_role", "hormuz_runtime"),
        "usage_storage.postgres_runtime_role",
    )
    postgres_pool = _postgres_pool_config(usage_storage_raw.get("postgres_pool", {}))
    if usage_backend == "postgresql" and postgres_dsn_env == postgres_migration_dsn_env:
        raise ConfigError(
            "usage_storage.postgres_dsn_env and usage_storage.postgres_migration_dsn_env "
            "must name separate credentials"
        )
    if usage_backend != "postgresql" and "postgres_pool" in usage_storage_raw:
        raise ConfigError("usage_storage.postgres_pool requires usage_storage.backend postgresql")

    return PersistenceConstruction(
        database_path=database_path,
        usage_storage=UsageStorageConfig(
            backend=usage_backend,
            postgres_dsn_env=postgres_dsn_env,
            postgres_migration_dsn_env=postgres_migration_dsn_env,
            postgres_schema=postgres_schema,
            postgres_runtime_role=postgres_runtime_role,
            postgres_pool=postgres_pool,
        ),
    )


def _postgres_pool_config(value: Any) -> PostgresPoolConfig:
    raw = _object(value, "usage_storage.postgres_pool")
    allowed = {
        "min_connections",
        "max_connections",
        "acquire_timeout_seconds",
        "max_waiting",
        "max_lifetime_seconds",
        "max_idle_seconds",
    }
    unsupported = set(raw).difference(allowed)
    if unsupported:
        raise ConfigError(
            "usage_storage.postgres_pool contains unsupported fields: "
            + ", ".join(sorted(str(field) for field in unsupported))
        )
    min_connections = _integer(
        raw.get("min_connections", 1),
        "usage_storage.postgres_pool.min_connections",
        minimum=1,
        maximum=100,
    )
    max_connections = _integer(
        raw.get("max_connections", 8),
        "usage_storage.postgres_pool.max_connections",
        minimum=1,
        maximum=1000,
    )
    if min_connections > max_connections:
        raise ConfigError(
            "usage_storage.postgres_pool.min_connections must not exceed max_connections"
        )
    acquire_timeout_seconds = _integer(
        raw.get("acquire_timeout_seconds", 5),
        "usage_storage.postgres_pool.acquire_timeout_seconds",
        minimum=1,
        maximum=120,
    )
    max_waiting = _integer(
        raw.get("max_waiting", 16),
        "usage_storage.postgres_pool.max_waiting",
        minimum=1,
        maximum=10000,
    )
    max_lifetime_seconds = _integer(
        raw.get("max_lifetime_seconds", 3600),
        "usage_storage.postgres_pool.max_lifetime_seconds",
        minimum=60,
        maximum=7 * 24 * 60 * 60,
    )
    max_idle_seconds = _integer(
        raw.get("max_idle_seconds", 300),
        "usage_storage.postgres_pool.max_idle_seconds",
        minimum=1,
        maximum=max_lifetime_seconds,
    )
    return PostgresPoolConfig(
        min_connections=min_connections,
        max_connections=max_connections,
        acquire_timeout_seconds=acquire_timeout_seconds,
        max_waiting=max_waiting,
        max_lifetime_seconds=max_lifetime_seconds,
        max_idle_seconds=max_idle_seconds,
    )
