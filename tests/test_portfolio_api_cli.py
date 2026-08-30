from __future__ import annotations

import contextlib
import copy
from dataclasses import asdict
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hormuz.cli import main
from hormuz.config import ConfigError, GatewayConfig
from hormuz.portfolio_config import build_portfolio_config
from hormuz.portfolio_http import _read_body
from hormuz.portfolio_wire import SCOPES, catalogue, validate
from hormuz.server import GatewayServer, serve_in_thread
if __package__:
    from ._portfolio_fixture import ADMIN, VIEWER, create_request, registry_config
else:
    from _portfolio_fixture import ADMIN, VIEWER, create_request, registry_config


def configuration_document(config):
    identities, environment = [], {"HORMUZ_PORTFOLIO_TOKEN": ADMIN}
    for index, identity in enumerate(config.identities_by_token.values()):
        name = f"SYNTHETIC_REGISTRY_TOKEN_{index}"
        environment[name] = identity.token
        identities.append({"token_env": name, "actor_id": identity.actor_id, "actor_name": "Synthetic",
                           "team_id": "engineering", "team_name": "Synthetic", "organization_id": identity.organization_id,
                           "allowed_clients": []})
    portfolio = {"schema_id": "hormuz.portfolio-control", "schema_version": 1, **asdict(config.portfolio_control)}
    # Exercise the real JSON parser rather than passing tuple-shaped dataclasses.
    portfolio = json.loads(json.dumps(portfolio))
    return {"database": str(config.database_path), "identities": identities, "upstreams": {
        protocol: {"base_url": "http://127.0.0.1:1/v1", "api_key_env": "SYNTHETIC_PROVIDER_KEY"} for protocol in ("openai", "anthropic")},
        "model_routes": {"synthetic": {"protocol": "openai", "upstream_model": "synthetic"}},
        "policies": {"organization": {}}, "portfolio_control": portfolio}, environment


class PortfolioConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.config = registry_config(Path("/unused/registry"))
        self.document, self.environment = configuration_document(self.config)

    def test_role_and_connector_configuration_is_explicit_bounded_and_server_owned(self):
        result = build_portfolio_config(self.document["portfolio_control"], tuple(self.config.identities_by_token.values()))
        self.assertEqual(result, self.config.portfolio_control)
        self.assertIsNone(build_portfolio_config(None, ()))
        mutations = [
            lambda x: x.update(schema_version=True),
            lambda x: x.update(credential="SYNTHETIC_EXCLUDED"),
            lambda x: x["role_bindings"][0].update(actor_id="missing"),
            lambda x: x["role_bindings"][0].update(organization_id="beta"),
            lambda x: x["role_bindings"][0].update(roles=["policy_root"]),
            lambda x: x["role_bindings"].append(x["role_bindings"][0]),
            lambda x: x["connectors"][0].update(installation_id="repo-name"),
            lambda x: x["connectors"][0].update(external_object_ids=["customer/repository"]),
            lambda x: x["connectors"][0].update(workspace_id="name"),
            lambda x: x["connectors"][0].update(provider="unsupported"),
            lambda x: x["connectors"][0].update(external_object_ids=["1"] * 1001),
        ]
        for mutation in mutations:
            value = copy.deepcopy(self.document["portfolio_control"])
            mutation(value)
            with self.assertRaisesRegex(ConfigError, "^portfolio_configuration_invalid$"):
                build_portfolio_config(value, tuple(self.config.identities_by_token.values()))

    def test_linear_workspace_and_project_ids_are_opaque_not_names(self):
        value = self.document["portfolio_control"]
        value["connectors"] = [{"organization_id": "acme", "connector_id": "linear-one", "provider": "linear",
                                "installation_id": None, "workspace_id": "11111111-1111-1111-1111-111111111111",
                                "external_object_ids": ["22222222-2222-2222-2222-222222222222"]}]
        result = build_portfolio_config(value, tuple(self.config.identities_by_token.values()))
        self.assertEqual(result.connectors[0].provider, "linear")

    def test_installed_registry_catalogue_matches_exact_approved_definitions(self):
        root = Path(__file__).resolve().parents[1]
        planned = json.loads((root / "docs/portfolio-intelligence-wire-v1.json").read_text())
        installed = catalogue()
        self.assertEqual(len(installed["schema_ids"]), 9)
        for name, definition in installed["$defs"].items():
            self.assertEqual(definition, planned["$defs"][name], name)
        cases = json.loads((root / "tests/fixtures/portfolio_intelligence/wire-v1-examples.json").read_text())["cases"]
        for case in cases:
            if case["schema_id"] in installed["schema_ids"]:
                validate(case["value"], case["schema_id"])


class PortfolioAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = registry_config(Path(temporary.name))
        self.server = GatewayServer(self.config, environ={"SYNTHETIC_PROVIDER_KEY": "synthetic-provider-test-key"})
        self.thread = serve_in_thread(self.server)
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method="GET", path=SCOPES, body=None, *, token=ADMIN, extra=()):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        try:
            connection.putrequest(method, path)
            if token is not None:
                connection.putheader("Authorization", "Bearer " + token)
            if body is not None:
                connection.putheader("Content-Type", "application/json")
                connection.putheader("Content-Length", str(len(body)))
                connection.putheader("Idempotency-Key", "api-create")
            for name, value in extra:
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def test_registry_api_create_show_replay_and_no_provider_egress(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("unexpected provider call")):
            request = json.dumps(create_request()).encode()
            status, created, headers = self.request("POST", body=request)
            self.assertEqual(status, 201)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("hormuz.work-scope-version", headers["X-Hormuz-Contract"])
            self.assertEqual(self.request("POST", body=request)[:2], (201, created))
            self.assertEqual(self.request(path=SCOPES + "/" + created["work_scope_id"])[:2], (200, created))

    def test_registry_authentication_and_roles_precede_body_and_storage(self):
        with mock.patch.object(self.server.portfolio_service.repository, "execute", side_effect=AssertionError("unauthorized lookup")):
            for token, status in ((None, 401), ("wrong", 401), ("non-ascii-\u00e9", 401), (VIEWER, 403)):
                actual, error, _ = self.request("POST", body=b"not JSON", token=token)
                self.assertEqual(actual, status)
                validate(error, "hormuz.portfolio-error")

    def test_registry_duplicate_headers_and_content_exclusion_fail_closed(self):
        payload = json.dumps(create_request()).encode()
        for extra in ((('Content-Length', str(len(payload))),), (("Idempotency-Key", "other"),),
                      (("Transfer-Encoding", "chunked"),), (("Content-Type", "text/plain"),)):
            status, error, _ = self.request("POST", body=payload, extra=extra)
            self.assertEqual(status, 400)
            validate(error, "hormuz.portfolio-error")
        with self.assertLogs("hormuz", level="DEBUG") as logs:
            status, error, _ = self.request(path=SCOPES + "?title=SYNTHETIC_EXCLUDED_QUERY")
        self.assertEqual(status, 400)
        self.assertNotIn("SYNTHETIC_EXCLUDED_QUERY", json.dumps(error))
        self.assertNotIn("SYNTHETIC_EXCLUDED_QUERY", " ".join(logs.output))
        self.assertEqual(self.request()[1]["items"], [])

    def test_registry_failure_is_safe_and_existing_v1_identity_is_unchanged(self):
        from hormuz.portfolio_wire import PortfolioError
        with mock.patch.object(self.server.portfolio_service.repository, "execute", side_effect=PortfolioError("unavailable")):
            status, error, _ = self.request()
        self.assertEqual(status, 503)
        self.assertTrue(error["retryable"])
        status, identity, _ = self.request(path="/v1/gateway/whoami")
        self.assertEqual(status, 200)
        self.assertEqual(identity["allowed_clients"], [])
        self.assertNotIn("roles", identity)


class PortfolioTransportBoundsTests(unittest.TestCase):
    def test_total_body_deadline_cannot_be_reset_by_slow_bytes(self):
        from hormuz.portfolio_wire import PortfolioError
        handler = mock.Mock()
        handler.rfile.read1.side_effect = [b"a", b"b"]
        with mock.patch("hormuz.portfolio_http.time.monotonic", side_effect=[0, 0, 6, 6, 11]):
            with self.assertRaises(PortfolioError) as raised:
                _read_body(handler, 2)
        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(handler.connection.settimeout.call_args_list, [mock.call(10), mock.call(4)])


class PortfolioCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        document, environment = configuration_document(self.config)
        self.config.source_path.write_text(json.dumps(document))
        self.request_path = self.root / "create.json"
        self.request_path.write_text(json.dumps(create_request()))
        patch = mock.patch.dict(os.environ, environment)
        patch.start()
        self.addCleanup(patch.stop)

    def command(self, *arguments):
        out, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(error):
            status = main(["--config", str(self.config.source_path), "portfolio", *arguments])
        return status, json.loads(out.getvalue() if status == 0 else error.getvalue())

    def test_cli_create_list_show_archive_and_tombstone(self):
        status, scope = self.command("create", str(self.request_path), "--idempotency-key", "cli")
        self.assertEqual(status, 0)
        identifier = scope["work_scope_id"]
        self.assertEqual(self.command("create", str(self.request_path), "--idempotency-key", "cli"), (0, scope))
        self.assertEqual(self.command("show", identifier, "--version", "1"), (0, scope))
        self.assertEqual(len(self.command("list", "--limit", "1")[1]["items"]), 1)
        status, archived = self.command("archive", identifier, "--expected-version", "1", "--idempotency-key", "archive")
        self.assertEqual(status, 0)
        self.assertEqual(archived["state"], "archived")
        self.assertEqual(self.command("archive", identifier, "--expected-version", "1", "--idempotency-key", "archive"), (0, archived))
        status, tombstone = self.command("tombstone", identifier, "--expected-version", "2", "--idempotency-key", "tombstone")
        self.assertEqual(status, 0)
        self.assertIsNone(tombstone["display_name"])

    def test_denied_cli_does_not_create_database_and_errors_are_versioned(self):
        with mock.patch.dict(os.environ, {"HORMUZ_PORTFOLIO_TOKEN": VIEWER}):
            status, error = self.command("list")
        self.assertEqual(status, 2)
        self.assertEqual(error["code"], "forbidden")
        self.assertFalse(self.config.database_path.exists())
