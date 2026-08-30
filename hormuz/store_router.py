"""Resolve the v1 usage repository and compose separately owned repositories."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Generic, Mapping, Protocol, TypeVar

from ._persistence import UsageRepository
from .config import GatewayConfig
from .postgres import PostgresConnectionPool, PostgresStorageError
from .postgres_usage_store import PostgresUsageStore
from .store import UsageStore


RepositoryT = TypeVar("RepositoryT")
RepositoryT_co = TypeVar("RepositoryT_co", covariant=True)


class RepositoryFactory(Protocol[RepositoryT_co]):
    """Construct one repository with explicit configuration and a borrowed pool."""

    def __call__(
        self,
        config: GatewayConfig,
        *,
        environ: Mapping[str, str] | None = None,
        connection_pool: PostgresConnectionPool | None = None,
        read_only: bool = False,
    ) -> RepositoryT_co: ...


@dataclass(frozen=True, repr=False)
class RepositoryBundle(Generic[RepositoryT]):
    """Separate typed owners; no cross-repository transaction or lifecycle."""

    usage: UsageRepository
    portfolio: RepositoryT


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


def create_repository_bundle(
    config: GatewayConfig,
    *,
    portfolio_factory: RepositoryFactory[RepositoryT],
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
) -> RepositoryBundle[RepositoryT]:
    """Compose an explicitly supplied owner beside the unchanged v1 usage ledger.

    This helper registers no default portfolio implementation; the feature
    entry point supplies its separately gated factory explicitly. Each
    repository owns its SQL and transactions. A shared pool is caller-owned,
    including when either factory fails. Only the factories acquire their
    repository connections; this helper owns no transaction or pool lifecycle
    and promises no cross-repository atomicity.
    The legacy create_usage_store path does not call this composition helper.
    """

    usage = create_usage_store(
        config, environ=environ, connection_pool=connection_pool, read_only=read_only,
    )
    portfolio = portfolio_factory(
        config, environ=environ, connection_pool=connection_pool, read_only=read_only,
    )
    return RepositoryBundle(usage=usage, portfolio=portfolio)


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
