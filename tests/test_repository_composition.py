from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from hormuz._persistence import UsageRepository
from hormuz.config import GatewayConfig, UsageStorageConfig
from hormuz.postgres import PostgresConnectionPool, PostgresStorageError
from hormuz.store import UsageStore
import hormuz.store_router as router


ROOT = Path(__file__).resolve().parents[1]


class RepositoryCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = GatewayConfig.load(
            ROOT / "config.example.json", environ={"HORMUZ_TOKEN": "synthetic-test-token"},
        )

    def test_registry_sql_facade_keeps_shared_patch_identity(self) -> None:
        from hormuz._portfolio_sql import PortfolioSQL
        from hormuz.portfolio_repository import _SQL

        self.assertIs(_SQL, PortfolioSQL)

    def test_legacy_factory_still_returns_only_the_concrete_usage_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(self.config, database_path=Path(temporary) / "usage.sqlite3")
            with mock.patch.object(router, "create_repository_bundle", side_effect=AssertionError("no composition")):
                usage = router.create_usage_store(config)
            self.assertIs(type(usage), UsageStore)
            usage.verify_ready()

    def test_composes_a_separate_owner_without_extending_the_usage_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(self.config, database_path=Path(temporary) / "usage.sqlite3")
            environ = {"UNUSED_ENV": "synthetic"}
            portfolio = object()
            factory = mock.Mock(return_value=portfolio)
            bundle = router.create_repository_bundle(config, portfolio_factory=factory, environ=environ)
            self.assertIs(type(bundle.usage), UsageStore)
            self.assertIs(bundle.portfolio, portfolio)
            self.assertFalse(hasattr(bundle.usage, "portfolio"))
            factory.assert_called_once_with(config, environ=environ, connection_pool=None, read_only=False)
            with self.assertRaises(AttributeError):
                bundle.portfolio = object()

    def test_work_budget_request_capability_is_explicit_and_builtin_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(self.config, database_path=Path(temporary) / "usage.sqlite3")
            usage = router.create_usage_store(config)
            capability = router.create_work_budget_request_repository(usage)

        self.assertIsInstance(capability, router.WorkBudgetRequestAdapter)
        self.assertNotEqual(capability, usage)
        compatible_v1_only = mock.create_autospec(UsageRepository, instance=True)
        self.assertIsNone(router.create_work_budget_request_repository(compatible_v1_only))

        class CustomUsageStore(UsageStore):
            pass

        custom = object.__new__(CustomUsageStore)
        self.assertIsNone(router.create_work_budget_request_repository(custom))

    def test_read_only_initialization_failure_precedes_the_optional_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(self.config, database_path=Path(temporary) / "missing" / "usage.sqlite3")
            factory = mock.Mock()
            with self.assertRaises(sqlite3.OperationalError):
                router.create_repository_bundle(config, portfolio_factory=factory, read_only=True)
            factory.assert_not_called()
            self.assertFalse(config.database_path.parent.exists())

    def test_read_only_mode_is_forwarded_without_migrating_the_sqlite_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = replace(self.config, database_path=Path(temporary) / "usage.sqlite3")
            UsageStore(config.database_path)
            with sqlite3.connect(config.database_path) as connection:
                before = list(connection.iterdump())
            factory = mock.Mock(return_value=object())
            bundle = router.create_repository_bundle(config, portfolio_factory=factory, read_only=True)
            self.assertTrue(bundle.usage.read_only)
            factory.assert_called_once_with(config, environ=None, connection_pool=None, read_only=True)
            with sqlite3.connect(config.database_path) as connection:
                self.assertEqual(list(connection.iterdump()), before)

    def test_postgres_factories_share_configuration_and_borrow_the_same_pool(self) -> None:
        storage = UsageStorageConfig(backend="postgresql", postgres_schema="custom_schema")
        config = replace(self.config, usage_storage=storage)
        pool = mock.create_autospec(PostgresConnectionPool, instance=True)
        environ = {storage.postgres_dsn_env: "postgresql://synthetic-not-used"}
        factory = mock.Mock(return_value=object())
        with mock.patch.object(router, "PostgresUsageStore") as adapter:
            bundle = router.create_repository_bundle(
                config, portfolio_factory=factory, environ=environ, connection_pool=pool, read_only=True,
            )
            adapter.assert_called_once_with(
                environ[storage.postgres_dsn_env], organization_ids=config.organization_ids,
                schema=storage.postgres_schema, runtime_role=storage.postgres_runtime_role,
                connection_pool=pool, audit_chain_maximum_anchor_age_seconds=None,
            )
            self.assertIs(bundle.usage, adapter.return_value)
        factory.assert_called_once_with(config, environ=environ, connection_pool=pool, read_only=True)
        pool.close.assert_not_called()

    def test_repository_factory_failure_propagates_without_closing_the_borrowed_pool(self) -> None:
        pool = mock.create_autospec(PostgresConnectionPool, instance=True)
        factory = mock.Mock(side_effect=RuntimeError("synthetic_factory_failure"))
        with mock.patch.object(router, "create_usage_store") as usage_factory:
            with self.assertRaisesRegex(RuntimeError, "synthetic_factory_failure"):
                router.create_repository_bundle(self.config, portfolio_factory=factory, connection_pool=pool)
            usage_factory.assert_called_once_with(self.config, environ=None, connection_pool=pool, read_only=False)
        pool.close.assert_not_called()

    def test_existing_configuration_errors_fail_before_the_other_factory_runs(self) -> None:
        cases = (
            (UsageStorageConfig(backend="postgresql"), "postgres_dsn_unavailable"),
            (UsageStorageConfig(backend="unsupported"), "storage_backend_unsupported"),
        )
        for storage, code in cases:
            with self.subTest(code=code):
                factory = mock.Mock()
                with self.assertRaises(PostgresStorageError) as raised:
                    router.create_repository_bundle(
                        replace(self.config, usage_storage=storage), portfolio_factory=factory, environ={},
                    )
                self.assertEqual(raised.exception.code, code)
                factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
