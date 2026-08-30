"""Static-only structural and negative checks for the v1 usage boundary.

Run with strict mypy and warn-unused-ignores. These functions are never run:
their deliberately invalid calls must remain type errors, not widen to Any.
"""

from typing import Protocol

from hormuz._persistence import UsageRepository
from hormuz.config import GatewayConfig
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.store import UsageStore
from hormuz.store_router import RepositoryBundle, RepositoryFactory, create_repository_bundle, create_usage_store


class FuturePortfolio(Protocol):
    """Test-only example: the real portfolio contract belongs to its feature."""

    def verify_ready(self) -> None: ...


def adapters_satisfy_the_usage_contract(
    sqlite: UsageStore,
    postgres: PostgresUsageStore,
) -> tuple[UsageRepository, UsageRepository]:
    return sqlite, postgres


def invalid_calls_remain_rejected(repository: UsageRepository) -> None:
    repository.monthly_totals(organization_id=42)  # type: ignore[arg-type]
    repository.verify_audit_chain(organization_id=42)  # type: ignore[arg-type]
    repository.record(prompt="content is not a ledger field")  # type: ignore[call-arg]


def composition_preserves_each_repository_type(
    config: GatewayConfig,
    portfolio_factory: RepositoryFactory[FuturePortfolio],
) -> RepositoryBundle[FuturePortfolio]:
    usage_factory: RepositoryFactory[UsageRepository] = create_usage_store
    usage: UsageRepository = usage_factory(config)
    usage.verify_ready()
    return create_repository_bundle(config, portfolio_factory=portfolio_factory)


def incomplete_factory(config: GatewayConfig) -> None:
    """Lacks the construction context required by RepositoryFactory."""


def invalid_composition_remains_rejected(
    config: GatewayConfig, bundle: RepositoryBundle[FuturePortfolio],
) -> None:
    create_repository_bundle(config, portfolio_factory=incomplete_factory)  # type: ignore[arg-type]
    bundle.portfolio.monthly_totals()  # type: ignore[attr-defined]
    bundle.portfolio = bundle.portfolio  # type: ignore[misc]
