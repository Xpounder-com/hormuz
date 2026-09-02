from __future__ import annotations

import http.client
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hormuz._hosted_config import HostedError
from hormuz._hosted_provider import load_provider_profile
from hormuz._hosted_server import (
    PROVIDER_MAX_CONNECTIONS,
    PROVIDER_MAX_INFERENCE_CONNECTIONS,
    ProviderPilotGatewayServer,
)
from hormuz._hosted_state import initialize
from hormuz.hosted import main
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import directory_setup, provider_profile


class _ProviderResponse:
    def __init__(self, status: int, body: dict | None = None):
        self.status = status
        self.headers = {"Content-Type": "application/json", "x-request-id": f"req-{status}"}
        self._body = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        self.closed = False

    def getcode(self):
        return self.status

    def read(self, _size=-1):
        value, self._body = self._body, b""
        return value

    def close(self):
        self.closed = True


@unittest.skipUnless(os.name == "posix", "The hosted runtime uses POSIX file permissions")
class HostedProviderConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config, self.staging, self.settings, self.document = provider_profile(self.root)
        self.path = Path(self.settings["HORMUZ_PROVIDER_CONFIG"])

    def load(self, value=None, settings=None):
        self.path.write_text(json.dumps(self.document if value is None else value))
        self.path.chmod(0o600)
        return load_provider_profile(self.staging.source_path, self.path, settings or self.settings)

    def test_profile_is_bound_to_login_state_and_fixed_provider_envelope(self):
        self.assertEqual(set(self.config.upstreams), {"openai", "anthropic"})
        self.assertEqual(set(self.config.model_routes), {
            "openai-primary", "openai-secondary", "anthropic-primary", "anthropic-secondary",
        })
        self.assertEqual(self.config.session_broker, self.staging.session_broker)
        self.assertEqual(self.config.oidc_issuers, self.staging.oidc_issuers)
        self.assertEqual(self.config.max_request_bytes, 2 * 1024 * 1024)

    def test_state_provider_route_policy_and_limit_expansions_fail_closed(self):
        mutations = []

        value = json.loads(json.dumps(self.document))
        value["listen"]["host"] = "0.0.0.0"
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["authentication"]["session_broker"]["public_base_url"] = "https://other.example.test"
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["identities"] = [{
            "token_env": "STATIC_TOKEN", "actor_id": "static", "actor_name": "Static",
            "team_id": "static", "team_name": "Static", "organization_id": "customer-a",
        }]
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["upstreams"]["openai"]["base_url"] = "https://proxy.example.test"
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["upstreams"]["openai"]["allow_background"] = True
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["model_routes"]["openai-primary"].pop("failover_alias")
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["model_routes"]["openai-primary"]["upstream_model"] = "replace-with-approved-openai-primary"
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["policies"]["organization"].pop("per_actor_monthly_budget_usd")
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["egress_controls"]["secrets"]["mode"] = "off"
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["max_request_bytes"] = 2 * 1024 * 1024 + 1
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["upstream_timeout_seconds"] = 601
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["usage_storage"] = {"backend": "sqlite", "postgres_dsn_env": "UNUSED_POSTGRES_DSN"}
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["policy_control"] = {"mode": "local", "postgres_control_role": "unused_policy_role"}
        mutations.append(value)
        value = json.loads(json.dumps(self.document))
        value["portfolio_control"] = {"enabled": False}
        mutations.append(value)

        for candidate in mutations:
            with self.subTest(keys=sorted(candidate)), self.assertRaises(HostedError):
                self.load(candidate)

    def test_every_provider_route_requires_explicit_cache_rates(self):
        for alias in self.document["model_routes"]:
            for field in ("cache_read_cost_per_million", "cache_write_cost_per_million"):
                candidate = json.loads(json.dumps(self.document))
                candidate["model_routes"][alias].pop(field)
                with self.subTest(alias=alias, field=field), self.assertRaisesRegex(
                    HostedError, "routes_invalid"
                ):
                    self.load(candidate)

    def test_provider_credentials_are_present_printable_and_distinct(self):
        changes = (
            {"HORMUZ_OPENAI_PROVIDER_KEY": ""},
            {"HORMUZ_OPENAI_PROVIDER_KEY": "short"},
            {"HORMUZ_OPENAI_PROVIDER_KEY": "bad-provider-key\nvalue"},
            {"HORMUZ_OPENAI_PROVIDER_KEY": self.settings["HORMUZ_ANTHROPIC_PROVIDER_KEY"]},
        )
        for change in changes:
            with self.subTest(change=tuple(change)), self.assertRaises(HostedError):
                self.load(settings={**self.settings, **change})

    def test_provider_configuration_must_be_regular_private_and_single_linked(self):
        self.path.chmod(0o666)
        with self.assertRaisesRegex(HostedError, "file_unsafe"):
            load_provider_profile(self.staging.source_path, self.path, self.settings)
        self.path.chmod(0o600)
        linked = self.root / "linked.json"
        os.link(self.path, linked)
        with self.assertRaisesRegex(HostedError, "file_unsafe"):
            load_provider_profile(self.staging.source_path, self.path, self.settings)

    def test_provider_check_validates_state_without_enabling_inference(self):
        initialize(self.staging)
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-check",
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        event = json.loads(output.getvalue())
        self.assertTrue(event["provider_configuration_valid"])
        self.assertFalse(event["inference_enabled"])


@unittest.skipUnless(os.name == "posix", "The hosted runtime uses POSIX file permissions")
class HostedProviderHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        config, self.staging, self.settings, _ = provider_profile(self.root)
        initialize(self.staging)
        self.config = replace(config, listen=replace(config.listen, port=0))
        provider_environment = {
            name: self.settings[name]
            for name in ("HORMUZ_OPENAI_PROVIDER_KEY", "HORMUZ_ANTHROPIC_PROVIDER_KEY")
        }
        self.gateway = ProviderPilotGatewayServer(self.config, environ=provider_environment)
        self.thread = threading.Thread(
            target=self.gateway.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=2)
        fields = {
            "Host": "gateway.example.test",
            "X-Hormuz-Ingress-Credential": self.config.ingress.credential,
        }
        fields.update(headers or {})
        encoded = None if body is None else json.dumps(body).encode()
        if encoded is not None:
            fields["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_health_is_provider_pilot_and_unauthenticated_requests_never_egress(self):
        status, _, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "provider_pilot")
        self.assertEqual(self.gateway._connection_slots._initial_value, PROVIDER_MAX_CONNECTIONS)
        self.assertEqual(self.gateway._provider_slots._initial_value, PROVIDER_MAX_INFERENCE_CONNECTIONS)
        with patch("hormuz.server.urllib.request.urlopen") as provider:
            self.assertEqual(self.request("POST", "/v1/responses", body={"model": "openai-primary"})[0], 401)
        provider.assert_not_called()

    def test_health_keeps_reserved_capacity_and_has_no_readiness_dependency(self):
        held = []
        try:
            for _ in range(PROVIDER_MAX_INFERENCE_CONNECTIONS):
                acquired = self.gateway._connection_slots.acquire(blocking=False)
                self.assertTrue(acquired)
                held.append(acquired)
            self.assertEqual(self.request("GET", "/health")[0], 200)
        finally:
            for _ in held:
                self.gateway._connection_slots.release()

        self.config.database_path.unlink()
        self.assertEqual(self.request("GET", "/ready")[0], 503)
        status, _, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "provider_pilot")

    def test_generation_capacity_fails_closed_without_provider_egress(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        held = []
        try:
            for _ in range(PROVIDER_MAX_INFERENCE_CONNECTIONS):
                acquired = self.gateway._provider_slots.acquire(blocking=False)
                self.assertTrue(acquired)
                held.append(acquired)
            with patch("hormuz.server.urllib.request.urlopen") as provider:
                status, _, body = self.request(
                    "POST",
                    "/v1/responses",
                    body={"model": "openai-primary", "input": "synthetic capacity check"},
                    headers={"Authorization": "Bearer " + pair.access_token},
                )
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "hormuz_provider_capacity_exhausted")
            provider.assert_not_called()
            self.assertEqual(self.request("GET", "/health")[0], 200)
        finally:
            for _ in held:
                self.gateway._provider_slots.release()

    def test_proxy_limits_preserve_liveness_and_backend_timeout_margin(self):
        caddy = (
            Path(__file__).resolve().parents[1]
            / "deploy/render/gateway/provider-pilot.Caddyfile"
        ).read_text()
        self.assertIn("unhealthy_request_count 9", caddy)
        self.assertIn("max_conns_per_host 9", caddy)
        self.assertIn("response_header_timeout 660s", caddy)

    def test_one_capacity_failover_records_two_attempts_and_two_egresses(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        success = _ProviderResponse(200, {
            "id": "resp_synthetic", "object": "response", "status": "completed",
            "model": "openai-secondary-model", "output": [],
            "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        })
        limited = _ProviderResponse(429)
        with patch("hormuz.server.urllib.request.urlopen", side_effect=[limited, success]) as provider:
            status, headers, body = self.request(
                "POST", "/v1/responses",
                body={"model": "openai-primary", "input": "synthetic provider-free request", "max_output_tokens": 8},
                headers={"Authorization": "Bearer " + pair.access_token},
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Hormuz-Failover"], "v1;reason=provider_rate_limited")
        self.assertEqual(json.loads(body)["model"], "openai-secondary-model")
        self.assertEqual(provider.call_count, 2)
        sent_models = [json.loads(call.args[0].data)["model"] for call in provider.call_args_list]
        self.assertEqual(sent_models, ["openai-primary-model", "openai-secondary-model"])
        self.assertTrue(limited.closed)
        self.assertTrue(success.closed)
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM gateway_provider_attempt_metrics"
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT count(*) FROM gateway_provider_failover_events"
            ).fetchone()[0], 1)

    def test_unknown_route_and_oversized_body_stop_at_private_boundary(self):
        self.assertEqual(self.request("POST", "/v1/models", body={})[0], 503)
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=2)
        try:
            connection.putrequest("POST", "/v1/responses", skip_host=True, skip_accept_encoding=True)
            connection.putheader("Host", "gateway.example.test")
            connection.putheader("X-Hormuz-Ingress-Credential", self.config.ingress.credential)
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(2 * 1024 * 1024 + 1))
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 413)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
