from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import http.client
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest import mock

from hormuz.cli import _doctor, _storage
from hormuz.config import GatewayConfig, ListenConfig, PostgresPoolConfig
from hormuz.postgres import PostgresConnectionPool, PostgresStorageError
from hormuz.server import GatewayRequestHandler, GatewayServer, serve_in_thread
from hormuz.store import ReservationDenied


ROOT = Path(__file__).resolve().parents[1]


class _FakePsycopgError(Exception):
    pass


class _FakePsycopg:
    Error = _FakePsycopgError
    rows = SimpleNamespace(dict_row=object())


class _FakePoolModule:
    class PoolTimeout(Exception):
        pass

    class TooManyRequests(Exception):
        pass

    class PoolClosed(Exception):
        pass

    class ConnectionPool:
        instances: list["_FakePoolModule.ConnectionPool"] = []
        open_error: Exception | None = None
        checkout_error: Exception | None = None

        @staticmethod
        def check_connection(_connection: object) -> None:
            return None

        def __init__(self, _dsn: str, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.open_calls: list[tuple[bool, int]] = []
            self.close_calls: list[int] = []
            self.connection_timeouts: list[int] = []
            self.connection_value = object()
            self.__class__.instances.append(self)

        def open(self, *, wait: bool, timeout: int) -> None:
            self.open_calls.append((wait, timeout))
            if self.__class__.open_error is not None:
                raise self.__class__.open_error

        @contextmanager
        def connection(self, *, timeout: int):
            self.connection_timeouts.append(timeout)
            if self.__class__.checkout_error is not None:
                raise self.__class__.checkout_error
            yield self.connection_value

        def close(self, *, timeout: int) -> None:
            self.close_calls.append(timeout)


class PostgresPoolUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakePoolModule.ConnectionPool.instances = []
        _FakePoolModule.ConnectionPool.open_error = None
        _FakePoolModule.ConnectionPool.checkout_error = None
        self.driver = mock.patch(
            "hormuz.postgres._driver",
            return_value=(_FakePsycopg, object()),
        )
        self.pool_driver = mock.patch("hormuz.postgres._pool_driver", return_value=_FakePoolModule)
        self.driver.start()
        self.pool_driver.start()
        self.addCleanup(self.pool_driver.stop)
        self.addCleanup(self.driver.stop)

    def _pool(self, **overrides: int) -> PostgresConnectionPool:
        settings = PostgresPoolConfig(**overrides)
        return PostgresConnectionPool("postgresql://runtime:never-log@db.example/hormuz", settings=settings)

    def test_pool_opens_with_explicit_bounded_settings_and_closes_idempotently(self) -> None:
        pool = self._pool(
            min_connections=2,
            max_connections=4,
            acquire_timeout_seconds=7,
            max_waiting=9,
            max_lifetime_seconds=1800,
            max_idle_seconds=300,
        )
        fake = _FakePoolModule.ConnectionPool.instances[-1]
        self.assertEqual(
            fake.kwargs,
            {
                "kwargs": {"row_factory": _FakePsycopg.rows.dict_row, "connect_timeout": 7},
                "min_size": 2,
                "max_size": 4,
                "timeout": 7,
                "max_waiting": 9,
                "max_lifetime": 1800,
                "max_idle": 300,
                "reconnect_timeout": 7,
                "check": _FakePoolModule.ConnectionPool.check_connection,
                "name": "hormuz-runtime",
                "num_workers": 1,
                "open": False,
            },
        )
        self.assertEqual(fake.open_calls, [(True, 7)])
        with pool.connection() as connection:
            self.assertIs(connection, fake.connection_value)
        self.assertEqual(fake.connection_timeouts, [7])
        pool.close()
        pool.close()
        self.assertTrue(pool.closed)
        self.assertEqual(fake.close_calls, [7])

    def test_checkout_and_startup_errors_are_stable_and_content_free(self) -> None:
        secret_dsn_fragment = "never-log"
        _FakePoolModule.ConnectionPool.open_error = _FakePoolModule.PoolTimeout(secret_dsn_fragment)
        with self.assertRaises(PostgresStorageError) as raised:
            self._pool()
        self.assertEqual(raised.exception.code, "storage_pool_exhausted")
        self.assertNotIn(secret_dsn_fragment, str(raised.exception))

        _FakePoolModule.ConnectionPool.open_error = None
        pool = self._pool()
        _FakePoolModule.ConnectionPool.checkout_error = _FakePoolModule.TooManyRequests(secret_dsn_fragment)
        with self.assertRaises(PostgresStorageError) as raised:
            with pool.connection():
                self.fail("a saturated pool must not yield a connection")
        self.assertEqual(raised.exception.code, "storage_pool_exhausted")
        self.assertNotIn(secret_dsn_fragment, str(raised.exception))

        _FakePoolModule.ConnectionPool.checkout_error = _FakePoolModule.PoolClosed(secret_dsn_fragment)
        with self.assertRaises(PostgresStorageError) as raised:
            with pool.connection():
                self.fail("a closed pool must not yield a connection")
        self.assertEqual(raised.exception.code, "storage_pool_closed")
        self.assertNotIn(secret_dsn_fragment, str(raised.exception))
        pool.close()

    def test_domain_errors_from_a_pooled_transaction_are_not_reclassified_as_storage_failures(self) -> None:
        pool = self._pool()
        denial = ReservationDenied("organization budget exceeded")

        with self.assertRaises(ReservationDenied) as raised:
            with pool.connection():
                raise denial

        self.assertIs(raised.exception, denial)
        pool.close()

    def test_invalid_programmatic_pool_settings_fail_before_any_driver_call(self) -> None:
        self.driver.stop()
        self.pool_driver.stop()
        with self.assertRaises(PostgresStorageError) as raised:
            PostgresConnectionPool(
                "postgresql://runtime@db.example/hormuz",
                settings=PostgresPoolConfig(min_connections=3, max_connections=2),
            )
        self.assertEqual(raised.exception.code, "postgres_pool_configuration_invalid")


class GatewayPostgresPoolOwnershipTests(unittest.TestCase):
    def test_gateway_shares_its_runtime_pool_and_closes_it_with_the_listener(self) -> None:
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-postgres-pool-token"},
        )
        pool = mock.Mock()
        pool.settings = PostgresPoolConfig()
        store = mock.Mock()
        runtime = mock.Mock()
        engine = mock.Mock(policy_runtime=runtime)
        with (
            mock.patch("hormuz.server.create_postgres_runtime_pool", return_value=pool) as create_pool,
            mock.patch("hormuz.server.create_usage_store", return_value=store) as create_store,
            mock.patch("hormuz.server.PolicyRuntime", return_value=runtime) as create_runtime,
            mock.patch("hormuz.server.PolicyEngine", return_value=engine),
            mock.patch("hormuz.server.resolve_upstream_credentials", return_value={}),
            mock.patch("hormuz.server.ThreadingHTTPServer.__init__", return_value=None),
            mock.patch("hormuz.server.ThreadingHTTPServer.server_close") as base_close,
        ):
            server = GatewayServer(config)
            create_pool.assert_called_once_with(config)
            create_store.assert_called_once_with(config, connection_pool=pool)
            create_runtime.assert_called_once_with(config, connection_pool=pool)
            runtime.verify_active_policies.assert_called_once_with()
            server.server_close()
            base_close.assert_called_once_with()
        pool.close.assert_called_once_with()

    def test_graceful_shutdown_waits_for_active_handler_before_pool_close(self) -> None:
        base_config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-postgres-pool-token"},
        )
        handler_entered = threading.Event()
        handler_release = threading.Event()
        pool = mock.Mock(spec=PostgresConnectionPool)
        pool.settings = PostgresPoolConfig()

        def blocked_forward(_handler: GatewayRequestHandler, **_kwargs: object) -> None:
            handler_entered.set()
            self.assertTrue(handler_release.wait(timeout=5), "test handler was not released")
            _handler.send_response(200)
            _handler.send_header("Content-Length", "0")
            _handler.send_header("Connection", "close")
            _handler.end_headers()

        with tempfile.TemporaryDirectory() as temporary:
            config = replace(
                base_config,
                database_path=Path(temporary) / "usage.sqlite3",
                listen=ListenConfig("127.0.0.1", 0),
            )
            server: GatewayServer | None = None
            serve_thread: threading.Thread | None = None
            close_thread: threading.Thread | None = None
            client_thread: threading.Thread | None = None
            try:
                with (
                    mock.patch("hormuz.server.create_postgres_runtime_pool", return_value=pool),
                    mock.patch.object(GatewayRequestHandler, "_forward", new=blocked_forward),
                ):
                    server = GatewayServer(config)
                    serve_thread = serve_in_thread(server)
                    token = next(iter(config.identities_by_token))

                    def request() -> None:
                        connection = http.client.HTTPConnection(
                            "127.0.0.1",
                            server.server_address[1],
                            timeout=5,
                        )
                        try:
                            connection.request(
                                "POST",
                                "/v1/responses",
                                body=json.dumps({"model": "gpt-5.4-mini", "input": "shutdown test"}),
                                headers={
                                    "Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json",
                                },
                            )
                            connection.getresponse().read()
                        except (OSError, http.client.HTTPException):
                            pass
                        finally:
                            connection.close()

                    client_thread = threading.Thread(target=request)
                    client_thread.start()
                    self.assertTrue(handler_entered.wait(timeout=5))

                    server.shutdown()
                    close_thread = threading.Thread(target=server.server_close)
                    close_thread.start()
                    close_thread.join(timeout=0.2)
                    self.assertTrue(close_thread.is_alive())
                    pool.close.assert_not_called()

                    handler_release.set()
                    close_thread.join(timeout=5)
                    self.assertFalse(close_thread.is_alive())
                    client_thread.join(timeout=5)
                    self.assertFalse(client_thread.is_alive())
                    serve_thread.join(timeout=5)
                    self.assertFalse(serve_thread.is_alive())
            finally:
                handler_release.set()
                if server is not None:
                    server.shutdown()
                if close_thread is not None:
                    close_thread.join(timeout=5)
                elif server is not None:
                    server.server_close()
                if client_thread is not None:
                    client_thread.join(timeout=5)
                if serve_thread is not None:
                    serve_thread.join(timeout=5)

        pool.close.assert_called_once_with()

    def test_postgres_diagnostics_use_and_close_the_runtime_pool(self) -> None:
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-postgres-pool-token"},
        )
        pool = mock.Mock(spec=PostgresConnectionPool)
        store = mock.Mock()
        runtime = mock.Mock()
        with (
            mock.patch("hormuz.cli.create_postgres_runtime_pool", return_value=pool) as create_pool,
            mock.patch("hormuz.cli.create_usage_store", return_value=store) as create_store,
            mock.patch("hormuz.cli.PolicyRuntime", return_value=runtime) as create_runtime,
            mock.patch("hormuz.cli.resolve_upstream_credentials", return_value={"openai": "key", "anthropic": "key"}),
        ):
            self.assertEqual(_doctor(config), 0)
        create_pool.assert_called_once_with(config)
        create_store.assert_called_once_with(config, connection_pool=pool)
        create_runtime.assert_called_once_with(config, connection_pool=pool)
        runtime.verify_active_policies.assert_called_once_with()
        pool.close.assert_called_once_with()

    def test_postgres_storage_verification_uses_and_closes_the_runtime_pool(self) -> None:
        config = GatewayConfig.load(
            ROOT / "config.example.json",
            environ={"HORMUZ_TOKEN": "test-postgres-pool-token"},
        )
        pool = mock.Mock(spec=PostgresConnectionPool)
        with (
            mock.patch("hormuz.cli.create_postgres_runtime_pool", return_value=pool) as create_pool,
            mock.patch("hormuz.cli.create_usage_store") as create_store,
        ):
            self.assertEqual(_storage(config, SimpleNamespace(storage_command="verify")), 0)
        create_pool.assert_called_once_with(config)
        create_store.assert_called_once_with(config, connection_pool=pool)
        pool.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
