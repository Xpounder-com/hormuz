from __future__ import annotations

from dataclasses import replace
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from hormuz.config import GatewayConfig
from hormuz.policy_control import PolicyControlService
from hormuz.postgres_usage_store import PostgresUsageStore
from hormuz.server import GatewayServer, serve_in_thread
if __package__:
    from ._postgres_fixture import (
        ROOT,
        PostgresTestCase,
        _BlockingReplicaBudgetProviderHandler,
        _ReplicaPolicyProviderHandler,
        _free_port,
    )
else:  # Isolated wheel compatibility discovery uses the tests directory as its import root.
    from _postgres_fixture import (
        ROOT,
        PostgresTestCase,
        _BlockingReplicaBudgetProviderHandler,
        _ReplicaPolicyProviderHandler,
        _free_port,
    )


class PostgresMultiInstanceTests(PostgresTestCase):
    def test_two_gateway_instances_share_atomic_organization_budget_reservations(self) -> None:
        """A durable reservation made through one gateway constrains the other.

        The lower-level store test above proves the advisory-lock algorithm.
        This test deliberately exercises the full request path through two
        independently constructed gateway servers and their separate runtime
        pools: authentication, policy evaluation, reservation, provider
        admission, evidence accounting, and reservation release.
        """

        _BlockingReplicaBudgetProviderHandler.reset()
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _BlockingReplicaBudgetProviderHandler)
        provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
        provider_thread.start()

        gateways: list[GatewayServer] = []
        gateway_threads: list[threading.Thread] = []
        first_request_thread: threading.Thread | None = None
        try:
            request_body = {"model": "gpt-5.4-mini", "input": "replica budget probe"}
            max_output_tokens = 1
            reserved_body = {**request_body, "store": False, "max_output_tokens": max_output_tokens}
            reserved_cost_microusd = len(
                json.dumps(reserved_body, separators=(",", ":")).encode("utf-8")
            ) * 1_000_000
            organization_budget_usd = reserved_cost_microusd * 1.5 / 1_000_000
            self.assertGreater(reserved_cost_microusd, 0)
            self.assertLessEqual(reserved_cost_microusd, round(organization_budget_usd * 1_000_000))
            self.assertGreater(reserved_cost_microusd * 2, round(organization_budget_usd * 1_000_000))

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                config_value = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
                config_value["usage_storage"] = {
                    "backend": "postgresql",
                    "postgres_dsn_env": "TEST_POSTGRES_RUNTIME_DSN",
                    "postgres_migration_dsn_env": "TEST_POSTGRES_MIGRATION_DSN",
                    "postgres_schema": self.schema,
                    "postgres_runtime_role": self.runtime_role,
                }
                config_value["upstreams"]["openai"] = {
                    "base_url": f"http://127.0.0.1:{provider.server_port}/v1",
                    "api_key_env": "TEST_REPLICA_OPENAI_KEY",
                    "allow_response_storage": False,
                    "allow_background": False,
                }
                config_value["upstreams"]["anthropic"][
                    "api_key_env"
                ] = "TEST_REPLICA_ANTHROPIC_KEY"
                config_value["model_routes"]["gpt-5.4-mini"] = {
                    "protocol": "openai",
                    "upstream_model": "gpt-5.4-mini",
                    "input_cost_per_million": 1_000_000,
                    "cache_read_cost_per_million": 0,
                    "cache_write_cost_per_million": 0,
                    "output_cost_per_million": 0,
                }
                config_value["policies"]["organization"]["max_output_tokens"] = max_output_tokens
                config_value["policies"]["teams"]["engineering"]["max_output_tokens"] = max_output_tokens
                config_value["policies"]["organization"]["monthly_budget_usd"] = organization_budget_usd

                environment = {
                    "HORMUZ_TOKEN": "replica-budget-employee-token",
                    "TEST_POSTGRES_RUNTIME_DSN": self.runtime_dsn,
                    "TEST_POSTGRES_MIGRATION_DSN": self.owner_dsn,
                    "TEST_REPLICA_OPENAI_KEY": "replica-budget-provider-key",
                    "TEST_REPLICA_ANTHROPIC_KEY": "replica-budget-anthropic-key",
                }
                configs: list[GatewayConfig] = []
                for index in range(2):
                    config_value["listen"]["port"] = _free_port()
                    config_path = root / f"gateway-{index}.json"
                    config_path.write_text(json.dumps(config_value), encoding="utf-8")
                    configs.append(GatewayConfig.load(config_path, environ=environment))
                with mock.patch.dict(os.environ, environment, clear=False):
                    for config in configs:
                        gateways.append(GatewayServer(config))

            for gateway in gateways:
                gateway_threads.append(serve_in_thread(gateway))

            def send_request(gateway: GatewayServer) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps(request_body),
                        headers={
                            "Authorization": "Bearer replica-budget-employee-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            first_outcome: list[tuple[int, bytes] | BaseException] = []

            def send_first_request() -> None:
                try:
                    first_outcome.append(send_request(gateways[0]))
                except BaseException as error:  # pragma: no cover - reported by the assertion below
                    first_outcome.append(error)

            first_request_thread = threading.Thread(target=send_first_request, daemon=True)
            first_request_thread.start()
            self.assertTrue(_BlockingReplicaBudgetProviderHandler.first_request_started.wait(timeout=5))

            denied_status, denied_body = send_request(gateways[1])
            self.assertEqual(denied_status, 403, denied_body)
            self.assertEqual(json.loads(denied_body)["error"]["code"], "hormuz_budget_denied")
            self.assertEqual(_BlockingReplicaBudgetProviderHandler.request_count, 1)

            _BlockingReplicaBudgetProviderHandler.release_first_response.set()
            first_request_thread.join(timeout=10)
            self.assertFalse(first_request_thread.is_alive())
            self.assertEqual(len(first_outcome), 1)
            self.assertNotIsInstance(first_outcome[0], BaseException)
            first_status, first_body = first_outcome[0]
            self.assertEqual(first_status, 200, first_body)
            self.assertEqual(_BlockingReplicaBudgetProviderHandler.request_count, 1)

            totals = gateways[0].store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (2, 1))
            self.assertEqual((totals.input_tokens, totals.output_tokens), (1, 0))
            self.assertEqual(totals.cost_microusd, 1_000_000)
            self.assertEqual(gateways[1].store.active_budget_reservations(organization_id="xpounder"), 0)
            events = gateways[0].store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(
                {(event["status"], event["policy_action"]) for event in events},
                {("succeeded", "allowed"), ("denied", "budget_reservation_denied")},
            )

            shared_store = PostgresUsageStore(
                self.runtime_dsn,
                organization_ids=("xpounder", "beta"),
                schema=self.schema,
                runtime_role=self.runtime_role,
            )
            self.assertEqual(shared_store.monthly_totals(organization_id="beta").requests, 0)
        finally:
            _BlockingReplicaBudgetProviderHandler.release_first_response.set()
            if first_request_thread is not None:
                first_request_thread.join(timeout=10)
            for gateway in gateways[: len(gateway_threads)]:
                gateway.shutdown()
            for gateway in gateways:
                gateway.server_close()
            for thread in gateway_threads:
                thread.join(timeout=10)
            provider.shutdown()
            provider.server_close()
            provider_thread.join(timeout=10)
    def test_two_gateway_instances_converge_on_policy_activation_and_rollback(self) -> None:
        """A committed policy pointer governs both replicas before provider egress."""

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
            permissive = self._stage(
                service,
                environment=environment,
                document=self._policy_document(),
            )
            initial_activation = service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=permissive.version_id,
            )
            self.assertEqual(initial_activation.generation, 1)

            upstreams = dict(config.upstreams)
            upstreams["openai"] = replace(
                upstreams["openai"],
                base_url=f"http://127.0.0.1:{provider.server_port}/v1",
                api_key_env="TEST_REPLICA_POLICY_OPENAI_KEY",
            )
            upstreams["anthropic"] = replace(
                upstreams["anthropic"],
                api_key_env="TEST_REPLICA_POLICY_ANTHROPIC_KEY",
            )
            environment.update(
                {
                    "TEST_REPLICA_POLICY_OPENAI_KEY": "replica-policy-provider-key",
                    "TEST_REPLICA_POLICY_ANTHROPIC_KEY": "replica-policy-anthropic-key",
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

            def send_request(gateway: GatewayServer) -> tuple[int, bytes]:
                connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
                try:
                    connection.request(
                        "POST",
                        "/v1/responses",
                        body=json.dumps({"model": "gpt-5.4-mini", "input": "replica policy probe"}),
                        headers={
                            "Authorization": "Bearer policy-test-alice-token",
                            "Content-Type": "application/json",
                        },
                    )
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            initial_status, initial_body = send_request(gateways[0])
            self.assertEqual(initial_status, 200, initial_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            stricter = self._stage(
                service,
                environment=environment,
                document=self._policy_document(actor_blocked=True),
            )
            strict_activation = service.activate(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=stricter.version_id,
            )
            self.assertEqual(strict_activation.generation, 2)

            denied_status, denied_body = send_request(gateways[1])
            self.assertEqual(denied_status, 403, denied_body)
            self.assertEqual(json.loads(denied_body)["error"]["code"], "hormuz_policy_denied")
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 1)

            rollback = service.rollback(
                organization_id="xpounder",
                credential_env="HORMUZ_POLICY_ADMIN_TOKEN",
                version_id=permissive.version_id,
            )
            self.assertEqual(rollback.action, "policy_rolled_back")
            self.assertEqual(rollback.generation, 3)

            restored_status, restored_body = send_request(gateways[1])
            self.assertEqual(restored_status, 200, restored_body)
            self.assertEqual(_ReplicaPolicyProviderHandler.request_count, 2)

            totals = gateways[0].store.monthly_totals(organization_id="xpounder")
            self.assertEqual((totals.requests, totals.denied_requests), (3, 1))
            events = gateways[0].store.audit_events(
                since="2000-01-01T00:00:00+00:00",
                organization_id="xpounder",
            )
            self.assertEqual(len(events), 3)
            self.assertEqual(
                {(event["status"], event["policy_action"], event["policy_version"]) for event in events},
                {
                    ("succeeded", "allowed", permissive.version_id),
                    ("denied", "denied", stricter.version_id),
                },
            )

            status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
            self.assertEqual(status.active.version_id if status.active else None, permissive.version_id)
            self.assertEqual(status.active.generation if status.active else None, 3)

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
