from __future__ import annotations

from dataclasses import replace
import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from uuid import uuid4

from hormuz.config import PostgresPoolConfig
from hormuz.contracts import validate_contract
from hormuz.policy_control import PolicyControlService
from hormuz.postgres import PostgresConnectionPool, PostgresStorageError
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.server import GatewayServer, serve_in_thread
if __package__:
    from ._postgres_fixture import (
        PostgresTestCase,
        _ReplicaPolicyProviderHandler,
        _free_port,
        _runtime_dsn,
    )
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import (
        PostgresTestCase,
        _ReplicaPolicyProviderHandler,
        _free_port,
        _runtime_dsn,
    )


class PostgresRuntimeRecoveryTests(PostgresTestCase):
    def test_failed_replica_fails_closed_while_sibling_and_replacement_remain_usable(self) -> None:
        """A local pool loss is isolated and a fresh gateway instance recovers.

        This is deliberately a deterministic process-level runtime-pool failure
        proof. It does not claim database failover or high availability.
        """

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        active_gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        failed_gateway: GatewayServer | None = None
        failed_gateway_thread: threading.Thread | None = None
        try:
            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            active_policy = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=active_policy.version_id,
            )

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_REPLICA_RECOVERY_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_REPLICA_RECOVERY_ANTHROPIC_KEY",
            )
            environment.update(
                {
                    "TEST_REPLICA_RECOVERY_OPENAI_KEY": "replica-recovery-provider-key",
                    "TEST_REPLICA_RECOVERY_ANTHROPIC_KEY": "replica-recovery-anthropic-key",
                }
            )
            configs = tuple(
                replace(
                    config,
                    listen=replace(config.listen, port=_free_port()),
                    upstreams=dict(upstreams),
                )
                for _ in range(2)
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                active_gateways = [GatewayServer(replica_config) for replica_config in configs]

            self.assertIsNotNone(active_gateways[0].postgres_pool)
            self.assertIsNotNone(active_gateways[1].postgres_pool)
            self.assertIsNot(active_gateways[0].postgres_pool, active_gateways[1].postgres_pool)
            gateway_threads = [serve_in_thread(gateway) for gateway in active_gateways]

            def send_get(gateway: GatewayServer, path: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def send_request(gateway: GatewayServer, *, input_value: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            failed_gateway = active_gateways.pop(0)
            failed_gateway_thread = gateway_threads.pop(0)
            self.assertIsNotNone(failed_gateway.postgres_pool)
            failed_gateway.postgres_pool.close()

            failed_ready_status, failed_ready_body = send_get(failed_gateway, "/ready")
            self.assertEqual(failed_ready_status, 503, failed_ready_body)
            failed_readiness = json.loads(failed_ready_body)
            validate_contract(failed_readiness)
            self.assertEqual(failed_readiness["reason"], "dependency_unavailable")

            secret_input = "replica-local-secret-must-not-leak"
            failed_status, failed_body = send_request(failed_gateway, input_value=secret_input)
            self.assertEqual(failed_status, 503, failed_body)
            failed_response = json.loads(failed_body)
            self.assertEqual(failed_response["error"]["code"], "hormuz_storage_unavailable")
            self.assertNotIn(secret_input, repr(failed_response))
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 0)

            healthy_gateway = active_gateways[0]
            healthy_ready_status, healthy_ready_body = send_get(healthy_gateway, "/ready")
            self.assertEqual(healthy_ready_status, 200, healthy_ready_body)
            healthy_status, healthy_body = send_request(healthy_gateway, input_value="healthy sibling probe")
            self.assertEqual(healthy_status, 200, healthy_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            failed_gateway.shutdown()
            failed_gateway.server_close()
            failed_gateway_thread.join(timeout=10)
            self.assertFalse(failed_gateway_thread.is_alive())
            failed_gateway = None
            failed_gateway_thread = None

            replacement_config = replace(
                config,
                listen=replace(config.listen, port=_free_port()),
                upstreams=dict(upstreams),
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                replacement = GatewayServer(replacement_config)
            self.assertIsNotNone(replacement.postgres_pool)
            self.assertIsNot(replacement.postgres_pool, healthy_gateway.postgres_pool)
            active_gateways.append(replacement)
            gateway_threads.append(serve_in_thread(replacement))

            replacement_ready_status, replacement_ready_body = send_get(replacement, "/ready")
            self.assertEqual(replacement_ready_status, 200, replacement_ready_body)
            replacement_status, replacement_body = send_request(
                replacement,
                input_value="replacement recovery probe",
            )
            self.assertEqual(replacement_status, 200, replacement_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 2)

            totals = healthy_gateway.store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (2, 0))
            events = healthy_gateway.store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(len(events), 2)
            self.assertEqual({event["status"] for event in events}, {"succeeded"})

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            if failed_gateway is not None:
                failed_gateway.shutdown()
                failed_gateway.server_close()
            if failed_gateway_thread is not None:
                failed_gateway_thread.join(timeout=10)
            for gateway in active_gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in active_gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)
    def test_rolling_runtime_login_rotation_keeps_ready_replacement_and_tenant_isolation(self) -> None:
        """A new NOINHERIT login can replace an old runtime login safely.

        The stable restricted ``runtime_role`` remains the authorization role.
        This exercises the real rolling process boundary: start a ready
        replacement using a distinct login member, drain the old process, then
        revoke only the old login. Hormuz deliberately does not hot-reload a
        DSN in a live process.
        """

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        suffix = uuid4().hex[:8]
        old_login = f"hormuz_runtime_old_{suffix}"
        replacement_login = f"hormuz_runtime_new_{suffix}"
        old_dsn = _runtime_dsn(self.owner_dsn, old_login, "hormuz-old-runtime-password")
        replacement_dsn = _runtime_dsn(
            self.owner_dsn,
            replacement_login,
            "hormuz-new-runtime-password",
        )
        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        try:
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    for login, password in (
                        (old_login, "hormuz-old-runtime-password"),
                        (replacement_login, "hormuz-new-runtime-password"),
                    ):
                        cursor.execute(
                            self.sql.SQL(
                                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                                "NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS"
                            ).format(self.sql.Identifier(login), self.sql.Literal(password))
                        )
                        cursor.execute(
                            self.sql.SQL("GRANT {} TO {}").format(
                                self.sql.Identifier(self.runtime_role),
                                self.sql.Identifier(login),
                            )
                        )

            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            active_policy = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=active_policy.version_id,
            )

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_RUNTIME_ROTATION_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_RUNTIME_ROTATION_ANTHROPIC_KEY",
            )
            old_config = replace(
                config,
                listen=replace(config.listen, port=_free_port()),
                upstreams=upstreams,
            )
            replacement_config = replace(
                config,
                listen=replace(config.listen, port=_free_port()),
                upstreams=upstreams,
            )
            common_environment = {
                **environment,
                "TEST_RUNTIME_ROTATION_OPENAI_KEY": "runtime-rotation-openai-key",
                "TEST_RUNTIME_ROTATION_ANTHROPIC_KEY": "runtime-rotation-anthropic-key",
            }
            old_environment = {
                **common_environment,
                "TEST_POSTGRES_RUNTIME_DSN": old_dsn,
            }
            replacement_environment = {
                **common_environment,
                "TEST_POSTGRES_RUNTIME_DSN": replacement_dsn,
            }

            with mock.patch.dict(os.environ, old_environment, clear=False):
                old_gateway = GatewayServer(old_config)
            gateways.append(old_gateway)
            old_thread = serve_in_thread(old_gateway)
            gateway_threads.append(old_thread)

            def send_get(gateway: GatewayServer, path: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def send_request(gateway: GatewayServer, *, input_value: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            old_ready_status, old_ready_body = send_get(old_gateway, "/ready")
            self.assertEqual(old_ready_status, 200, old_ready_body)
            old_status, old_body = send_request(old_gateway, input_value="old runtime login probe")
            self.assertEqual(old_status, 200, old_body)

            # The replacement is built from a separately injected runtime
            # credential. It must become ready before the old login is revoked
            # or customer traffic is moved.
            with mock.patch.dict(os.environ, replacement_environment, clear=False):
                replacement_gateway = GatewayServer(replacement_config)
            gateways.append(replacement_gateway)
            replacement_thread = serve_in_thread(replacement_gateway)
            gateway_threads.append(replacement_thread)

            replacement_ready_status, replacement_ready_body = send_get(replacement_gateway, "/ready")
            self.assertEqual(replacement_ready_status, 200, replacement_ready_body)
            replacement_status, replacement_body = send_request(
                replacement_gateway,
                input_value="replacement runtime login probe",
            )
            self.assertEqual(replacement_status, 200, replacement_body)

            # Draining owns existing work and closes the old pool before the
            # operator disables the superseded login.
            old_gateway.shutdown()
            old_gateway.server_close()
            old_thread.join(timeout=10)
            self.assertFalse(old_thread.is_alive())
            self.assertIsNotNone(old_gateway.postgres_pool)
            self.assertTrue(old_gateway.postgres_pool.closed)

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(self.sql.SQL("ALTER ROLE {} NOLOGIN").format(self.sql.Identifier(old_login)))

            with self.assertRaises(PostgresStorageError):
                PostgresConnectionPool(
                    old_dsn,
                    settings=PostgresPoolConfig(
                        min_connections=1,
                        max_connections=1,
                        acquire_timeout_seconds=1,
                        max_waiting=1,
                        max_lifetime_seconds=1800,
                        max_idle_seconds=120,
                    ),
                )

            # The replacement keeps serving through the same stable runtime
            # authorization role, including transaction-local RLS isolation.
            replacement_ready_status, replacement_ready_body = send_get(replacement_gateway, "/ready")
            self.assertEqual(replacement_ready_status, 200, replacement_ready_body)
            replacement_status, replacement_body = send_request(
                replacement_gateway,
                input_value="post-revocation replacement probe",
            )
            self.assertEqual(replacement_status, 200, replacement_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 3)

            rotated_store = PostgresUsageStore(
                replacement_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(rotated_store.monthly_totals(organization_id="xpounder").requests, 3)
            self.assertEqual(rotated_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            for gateway, thread in zip(gateways, gateway_threads):
                if thread.is_alive():
                    gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)
            with self.psycopg.connect(self.owner_dsn, autocommit=True) as connection:
                with connection.cursor() as cursor:
                    for login in (old_login, replacement_login):
                        cursor.execute(self.sql.SQL("DROP ROLE IF EXISTS {}").format(self.sql.Identifier(login)))
    def test_terminated_idle_backend_connection_is_replaced_before_replica_egress(self) -> None:
        """A replica replaces a stale backend connection without affecting its sibling.

        This is deliberately a bounded connection-churn proof. It does not
        claim PostgreSQL database outage recovery or high availability.
        """

        _ReplicaPolicyProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ReplicaPolicyProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        try:
            config, environment, _issuer = self._managed_config()
            service = PolicyControlService(config, environ=environment)
            service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            active_policy = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=active_policy.version_id,
            )

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_REPLICA_CHURN_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_REPLICA_CHURN_ANTHROPIC_KEY",
            )
            environment.update(
                {
                    "TEST_REPLICA_CHURN_OPENAI_KEY": "replica-churn-provider-key",
                    "TEST_REPLICA_CHURN_ANTHROPIC_KEY": "replica-churn-anthropic-key",
                }
            )
            configs = tuple(
                replace(
                    config,
                    listen=replace(config.listen, port=_free_port()),
                    upstreams=dict(upstreams),
                )
                for _ in range(2)
            )
            with mock.patch.dict(os.environ, environment, clear=False):
                gateways = [GatewayServer(replica_config) for replica_config in configs]

            self.assertIsNotNone(gateways[0].postgres_pool)
            self.assertIsNotNone(gateways[1].postgres_pool)
            self.assertIsNot(gateways[0].postgres_pool, gateways[1].postgres_pool)
            gateway_threads = [serve_in_thread(gateway) for gateway in gateways]

            def send_get(gateway: GatewayServer, path: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            def send_request(gateway: GatewayServer, *, input_value: str) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": input_value}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            churned_gateway, sibling_gateway = gateways
            self.assertIsNotNone(churned_gateway.postgres_pool)
            with churned_gateway.postgres_pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                    stale_backend = cursor.fetchone()
            assert stale_backend is not None
            stale_backend_pid = int(stale_backend["backend_pid"])

            with self.psycopg.connect(self.owner_dsn, autocommit=True) as owner:
                with owner.cursor() as cursor:
                    cursor.execute("SELECT pg_terminate_backend(%s)", (stale_backend_pid,))
                    self.assertTrue(cursor.fetchone()[0])

            churned_ready_status, churned_ready_body = send_get(churned_gateway, "/ready")
            self.assertEqual(churned_ready_status, 200, churned_ready_body)
            with churned_gateway.postgres_pool.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid() AS backend_pid")
                    replacement_backend = cursor.fetchone()
            assert replacement_backend is not None
            self.assertNotEqual(stale_backend_pid, int(replacement_backend["backend_pid"]))

            churned_status, churned_body = send_request(
                churned_gateway,
                input_value="replacement backend gateway probe",
            )
            self.assertEqual(churned_status, 200, churned_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            sibling_ready_status, sibling_ready_body = send_get(sibling_gateway, "/ready")
            self.assertEqual(sibling_ready_status, 200, sibling_ready_body)
            sibling_status, sibling_body = send_request(
                sibling_gateway,
                input_value="independent sibling gateway probe",
            )
            self.assertEqual(sibling_status, 200, sibling_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 2)

            totals = churned_gateway.store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (2, 0))
            events = churned_gateway.store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(len(events), 2)
            self.assertEqual({event["status"] for event in events}, {"succeeded"})

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            for gateway in gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)


if __name__ == "__main__":
    unittest.main()
