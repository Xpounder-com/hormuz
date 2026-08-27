"""Resolve the configured metadata-only usage repository."""

from __future__ import annotations

import os
from typing import Mapping

from .config import GatewayConfig
from .postgres import PostgresConnectionPool, PostgresStorageError
from .postgres_usage_store import PostgresUsageStore
from .store import UsageRepository, UsageStore


def create_usage_store(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
) -> UsageRepository:
    """Return the configured store, never placing a PostgreSQL DSN in config."""

    storage = config.usage_storage
    audit_chain_maximum_anchor_age_seconds = (
        config.audit_chain.maximum_anchor_age_seconds if config.audit_chain is not None else None
    )
    if storage.backend == "sqlite":
        return UsageStore(
            config.database_path,
            audit_chain_maximum_anchor_age_seconds=audit_chain_maximum_anchor_age_seconds,
            audit_chain_organization_ids=config.organization_ids,
            read_only=read_only,
        )
    if storage.backend != "postgresql":  # Configuration parsing prevents this path.
        raise PostgresStorageError("storage_backend_unsupported")
    environment = os.environ if environ is None else environ
    dsn = environment.get(storage.postgres_dsn_env, "")
    if not dsn:
        raise PostgresStorageError("postgres_dsn_unavailable")
    return PostgresUsageStore(
        dsn,
        organization_ids=config.organization_ids,
        schema=storage.postgres_schema,
        runtime_role=storage.postgres_runtime_role,
        connection_pool=connection_pool,
        audit_chain_maximum_anchor_age_seconds=audit_chain_maximum_anchor_age_seconds,
    )


def create_postgres_runtime_pool(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> PostgresConnectionPool | None:
    """Build the long-running gateway pool only for the runtime credential.

    Operators use a separate direct migration credential, and policy-control
    commands retain their independently authenticated service boundary.
    """

    storage = config.usage_storage
    if storage.backend == "sqlite":
        return None
    if storage.backend != "postgresql":  # Configuration parsing prevents this path.
        raise PostgresStorageError("storage_backend_unsupported")
    environment = os.environ if environ is None else environ
    dsn = environment.get(storage.postgres_dsn_env, "")
    if not dsn:
        raise PostgresStorageError("postgres_dsn_unavailable")
    return PostgresConnectionPool(dsn, settings=storage.postgres_pool)


def postgres_migration_dsn(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return only the separately configured operator migration DSN."""

    if config.usage_storage.backend != "postgresql":
        raise PostgresStorageError("storage_backend_unsupported")
    environment = os.environ if environ is None else environ
    dsn = environment.get(config.usage_storage.postgres_migration_dsn_env, "")
    if not dsn:
        raise PostgresStorageError("postgres_migration_dsn_unavailable")
    return dsn
