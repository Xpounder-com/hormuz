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
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hormuz._hosted_config import HostedError
from hormuz._hosted_provider import (
    PROVIDER_CHILD_ENV_NAMES,
    PROVIDER_FAILOVER_REHEARSAL_ENV,
    load_provider_profile,
)
from hormuz._hosted_server import (
    PROVIDER_MAX_CONNECTIONS,
    PROVIDER_MAX_INFERENCE_CONNECTIONS,
    ProviderPilotGatewayServer,
)
from hormuz._hosted_state import initialize
from hormuz.config import UsageStorageConfig
from hormuz.hosted import main
from hormuz.onboarding import TeamDirectory
from hormuz.postgres import PostgresStorageError
from hormuz.session_store import SQLiteSessionStore
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import console_credential, directory_setup, provider_profile


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
        self.assertEqual(self.config.usage_storage.backend, "postgresql")
        self.assertEqual(self.config.usage_storage.postgres_pool.max_connections, 4)
        self.assertEqual(self.config.usage_storage.postgres_pool.max_waiting, 8)

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
        value["usage_storage"]["postgres_pool"]["max_connections"] = 8
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
            {"HORMUZ_POSTGRES_DSN": ""},
            {"HORMUZ_POSTGRES_DSN": "sqlite:///not-postgres"},
            {"HORMUZ_POSTGRES_DSN": self.settings["HORMUZ_OPENAI_PROVIDER_KEY"]},
            {PROVIDER_FAILOVER_REHEARSAL_ENV: "short"},
            {
                PROVIDER_FAILOVER_REHEARSAL_ENV: self.settings[
                    "HORMUZ_OPENAI_PROVIDER_KEY"
                ]
            },
        )
        for change in changes:
            with self.subTest(change=tuple(change)), self.assertRaises(HostedError):
                self.load(settings={**self.settings, **change})

    def test_provider_mode_rejects_the_operator_migration_credential(self):
        with self.assertRaisesRegex(
            HostedError,
            "hosted_provider_migration_credential_forbidden",
        ):
            self.load(
                settings={
                    **self.settings,
                    "HORMUZ_POSTGRES_MIGRATION_DSN": (
                        "postgresql://migration:synthetic@db.example.test/hormuz"
                    ),
                }
            )

    def test_render_provider_process_is_bound_to_main_small_compute_and_exact_source(self):
        hosted_document = json.loads(self.staging.source_path.read_text())
        render_state = Path("/var/lib/hormuz/private/state").resolve()
        hosted_document.update({
            "public_origin": "https://hormuz-test.onrender.com",
            "oidc_issuer": "https://hormuz-test.okta.com/oauth2/default",
            "state_directory": str(render_state),
            "trusted_parent_path": str(Path("/var/lib/hormuz").resolve()),
        })
        self.staging.source_path.write_text(json.dumps(hosted_document))
        self.staging.source_path.chmod(0o600)
        render_document = json.loads(json.dumps(self.document))
        render_document["database"] = str(render_state / "usage.sqlite3")
        render_document["authentication"]["session_broker"].update({
            "public_base_url": hosted_document["public_origin"],
            "database": str(render_state / "sessions.sqlite3"),
            "trusted_parent_path": str(Path("/var/lib/hormuz").resolve()),
        })
        render_document["authentication"]["oidc"]["issuers"][0]["issuer"] = hosted_document[
            "oidc_issuer"
        ]
        render = {
            **self.settings,
            "RENDER": "true",
            "RENDER_CPU_COUNT": "0.50",
            "RENDER_EXTERNAL_HOSTNAME": "hormuz-test.onrender.com",
            "RENDER_EXTERNAL_URL": "https://hormuz-test.onrender.com",
            "RENDER_GIT_BRANCH": "main",
            "RENDER_GIT_COMMIT": "a" * 40,
            "RENDER_GIT_REPO_SLUG": "Xpounder-com/hormuz",
            "RENDER_INSTANCE_ID": "synthetic-instance-a",
            "RENDER_SERVICE_ID": "srv-" + "a" * 20,
            "RENDER_SERVICE_TYPE": "web",
            "RENDER_WEB_CONCURRENCY": "1",
        }
        self.assertEqual(
            self.load(render_document, settings=render).usage_storage.postgres_pool.max_connections,
            4,
        )
        self.assertEqual(
            self.load(
                render_document,
                settings={**render, "RENDER_CPU_COUNT": "0.5"},
            ).usage_storage.postgres_pool.max_connections,
            4,
        )
        for name, value in (
            ("RENDER_CPU_COUNT", "1"),
            ("RENDER_CPU_COUNT", "0.500"),
            ("RENDER_EXTERNAL_HOSTNAME", "wrong.example.test"),
            ("RENDER_EXTERNAL_URL", "http://hormuz-test.onrender.com"),
            ("RENDER_GIT_BRANCH", "feature"),
            ("RENDER_GIT_COMMIT", "not-a-commit"),
            ("RENDER_GIT_REPO_SLUG", "someone/fork"),
            ("RENDER_INSTANCE_ID", "bad instance"),
            ("RENDER_SERVICE_ID", "not-a-service"),
            ("RENDER_SERVICE_TYPE", "worker"),
            ("RENDER_WEB_CONCURRENCY", "2"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(
                HostedError,
                "deployment_metadata_invalid",
            ):
                self.load(render_document, settings={**render, name: value})

        unsafe_hosted = json.loads(json.dumps(hosted_document))
        unsafe_hosted["oidc_issuer"] = "https://identity.example.test/oauth2/default"
        self.staging.source_path.write_text(json.dumps(unsafe_hosted))
        self.staging.source_path.chmod(0o600)
        unsafe_runtime = json.loads(json.dumps(render_document))
        unsafe_runtime["authentication"]["oidc"]["issuers"][0]["issuer"] = unsafe_hosted[
            "oidc_issuer"
        ]
        with self.assertRaisesRegex(HostedError, "render_runtime_invalid"):
            self.load(unsafe_runtime, settings=render)

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
        session_settings = self.config.session_broker
        assert session_settings.database_path is not None
        session_store = SQLiteSessionStore(
            session_settings.database_path,
            master_key=session_settings.master_key,
            audience=session_settings.public_base_url,
            access_ttl_seconds=session_settings.access_ttl_seconds,
            absolute_ttl_seconds=session_settings.absolute_ttl_seconds,
            enrollment_ttl_seconds=session_settings.enrollment_ttl_seconds,
        )
        TeamDirectory(self.config, session_store).create_organization(
            organization_id="customer-a",
            name="Customer A",
            issuer=next(iter(self.config.oidc_issuers)),
        )
        output, error = io.StringIO(), io.StringIO()
        pool = Mock()
        store = Mock()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch("hormuz.store_router.create_postgres_runtime_pool", return_value=pool) as create_pool,
            patch("hormuz.store_router.create_usage_store", return_value=store) as create_store,
            patch("hormuz.postgres.verify_postgres_deployment_runtime") as verify_runtime,
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
        self.assertTrue(event["postgresql_runtime_verified"])
        self.assertEqual(event["postgresql_pool_max_connections"], 4)
        self.assertFalse(event["inference_enabled"])
        runtime_settings = {
            **self.settings,
            "RENDER": "",
            "RENDER_CPU_COUNT": "",
            "RENDER_EXTERNAL_HOSTNAME": "",
            "RENDER_EXTERNAL_URL": "",
            "RENDER_GIT_BRANCH": "",
            "RENDER_GIT_COMMIT": "",
            "RENDER_GIT_REPO_SLUG": "",
            "RENDER_INSTANCE_ID": "",
            "RENDER_SERVICE_ID": "",
            "RENDER_SERVICE_TYPE": "",
            "RENDER_WEB_CONCURRENCY": "",
            "HORMUZ_POSTGRES_MIGRATION_DSN": "",
        }
        create_pool.assert_called_once_with(self.config, environ=runtime_settings)
        create_store.assert_called_once_with(
            self.config,
            environ=runtime_settings,
            connection_pool=pool,
            organization_ids=("customer-a",),
        )
        verify_runtime.assert_called_once_with(
            self.settings["HORMUZ_POSTGRES_DSN"],
            schema="hormuz",
            runtime_role="hormuz_runtime",
            policy_control_role="hormuz_policy_control",
            custody_control_role="hormuz_custody_control",
            custody_executor_role="hormuz_custody_executor",
            connection_pool=pool,
            require_restricted_migration_login=True,
        )
        store.verify_ready.assert_called_once_with()
        pool.close.assert_called_once_with()

    def test_provider_backend_preserves_only_reviewed_secrets_and_deployment_metadata(self):
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch("hormuz.hosted.backend") as run_backend,
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-backend",
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        run_backend.assert_called_once()
        self.assertIs(run_backend.call_args.kwargs["provider"], True)
        child = run_backend.call_args.kwargs["environ"]
        self.assertEqual(set(child), set(PROVIDER_CHILD_ENV_NAMES))
        self.assertEqual(child["RENDER"], "")
        self.assertEqual(child["HORMUZ_OPENAI_PROVIDER_KEY"], self.settings["HORMUZ_OPENAI_PROVIDER_KEY"])

    def test_provider_server_rejects_runtime_identity_before_binding(self):
        initialize(self.staging)
        with (
            patch(
                "hormuz._hosted_server.verify_postgres_deployment_runtime",
                side_effect=PostgresStorageError(
                    "postgres_runtime_identity_invalid"
                ),
            ) as verify_runtime,
            patch("hormuz._hosted_server.GatewayServer.__init__") as initialize_server,
            self.assertRaisesRegex(
                PostgresStorageError,
                "postgres_runtime_identity_invalid",
            ),
        ):
            ProviderPilotGatewayServer(self.config, environ=self.settings)
        verify_runtime.assert_called_once_with(
            self.settings["HORMUZ_POSTGRES_DSN"],
            schema="hormuz",
            runtime_role="hormuz_runtime",
            policy_control_role="hormuz_policy_control",
            custody_control_role="hormuz_custody_control",
            custody_executor_role="hormuz_custody_executor",
            require_restricted_migration_login=True,
        )
        initialize_server.assert_not_called()

    def test_provider_check_refuses_an_empty_managed_tenant_allowlist(self):
        initialize(self.staging)
        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch("hormuz.store_router.create_postgres_runtime_pool") as create_pool,
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-check",
            ])
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            json.loads(error.getvalue())["code"],
            "hosted_provider_organization_required",
        )
        create_pool.assert_not_called()

    def test_provider_migration_is_explicit_maintenance_only_and_uses_operator_dsn(self):
        migrated = SimpleNamespace(version=14, complete=True)
        output, error = io.StringIO(), io.StringIO()
        settings = {
            **self.settings,
            "HORMUZ_HOSTED_MODE": "maintenance",
            "HORMUZ_POSTGRES_MIGRATION_DSN": (
                "postgresql://migration:synthetic@db.example.test/hormuz"
            ),
        }
        with (
            patch.dict(os.environ, settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch("hormuz.postgres.migrate_postgres", return_value=migrated) as migrate,
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-migrate",
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        event = json.loads(output.getvalue())
        self.assertEqual(event["postgresql_schema_version"], 14)
        self.assertTrue(event["postgresql_schema_complete"])
        self.assertFalse(event["inference_enabled"])
        migrate.assert_called_once_with(
            settings["HORMUZ_POSTGRES_MIGRATION_DSN"],
            schema="hormuz",
            runtime_role="hormuz_runtime",
            policy_control_role="hormuz_policy_control",
            custody_control_role="hormuz_custody_control",
            custody_executor_role="hormuz_custody_executor",
            require_restricted_migration_login=True,
        )

        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch("hormuz.postgres.migrate_postgres") as migrate,
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-migrate",
            ])
        self.assertEqual(status, 1)
        self.assertIn("hosted_provider_migration_requires_maintenance", error.getvalue())
        migrate.assert_not_called()

    def test_provider_postgres_bootstrap_is_maintenance_only_and_separates_credentials(self):
        bootstrapped = SimpleNamespace(
            schema_version=14,
            schema_complete=True,
            restricted_roles=4,
            runtime_login_restricted=True,
            runtime_membership_verified=True,
        )
        output, error = io.StringIO(), io.StringIO()
        settings = {
            **self.settings,
            "HORMUZ_HOSTED_MODE": "maintenance",
            "HORMUZ_POSTGRES_MIGRATION_DSN": (
                "postgresql://migration:synthetic@db.example.test/hormuz"
            ),
        }
        with (
            patch.dict(os.environ, settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch(
                "hormuz.postgres.bootstrap_postgres_deployment",
                return_value=bootstrapped,
            ) as bootstrap,
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-bootstrap-postgres",
            ])
        self.assertEqual((status, error.getvalue()), (0, ""))
        event = json.loads(output.getvalue())
        self.assertEqual(event["postgresql_schema_version"], 14)
        self.assertEqual(event["postgresql_restricted_roles"], 4)
        self.assertTrue(event["postgresql_runtime_login_restricted"])
        self.assertTrue(event["postgresql_runtime_membership_verified"])
        self.assertFalse(event["inference_enabled"])
        bootstrap.assert_called_once_with(
            settings["HORMUZ_POSTGRES_MIGRATION_DSN"],
            settings["HORMUZ_POSTGRES_DSN"],
            schema="hormuz",
            runtime_role="hormuz_runtime",
            policy_control_role="hormuz_policy_control",
            custody_control_role="hormuz_custody_control",
            custody_executor_role="hormuz_custody_executor",
            require_restricted_migration_login=True,
        )

        output, error = io.StringIO(), io.StringIO()
        with (
            patch.dict(os.environ, self.settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch("hormuz.postgres.bootstrap_postgres_deployment") as bootstrap,
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-bootstrap-postgres",
            ])
        self.assertEqual(status, 1)
        self.assertIn("hosted_provider_bootstrap_requires_maintenance", error.getvalue())
        bootstrap.assert_not_called()

    def test_provider_postgres_bootstrap_reports_only_stable_storage_code(self):
        output, error = io.StringIO(), io.StringIO()
        settings = {
            **self.settings,
            "HORMUZ_HOSTED_MODE": "maintenance",
            "HORMUZ_POSTGRES_MIGRATION_DSN": (
                "postgresql://migration:synthetic@db.example.test/hormuz"
            ),
        }
        with (
            patch.dict(os.environ, settings, clear=True),
            patch("hormuz.hosted.logging.disable"),
            patch(
                "hormuz.postgres.bootstrap_postgres_deployment",
                side_effect=PostgresStorageError("storage_access_denied"),
            ),
            redirect_stdout(output),
            redirect_stderr(error),
        ):
            status = main([
                "--config", str(self.staging.source_path),
                "--provider-config", str(self.path),
                "provider-bootstrap-postgres",
            ])
        self.assertEqual(status, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            json.loads(error.getvalue()),
            {"event": "hosted_operation_failed", "code": "storage_access_denied"},
        )


@unittest.skipUnless(os.name == "posix", "The hosted runtime uses POSIX file permissions")
class HostedProviderHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        config, self.staging, self.settings, _ = provider_profile(self.root)
        initialize(self.staging)
        # HTTP behavior is exercised against a disposable local SQLite store;
        # the configuration tests above separately require the live profile's
        # exact PostgreSQL contract.
        self.config = replace(
            config,
            listen=replace(config.listen, port=0),
            usage_storage=UsageStorageConfig(),
        )
        provider_environment = {
            name: self.settings[name]
            for name in (
                "HORMUZ_OPENAI_PROVIDER_KEY",
                "HORMUZ_ANTHROPIC_PROVIDER_KEY",
                PROVIDER_FAILOVER_REHEARSAL_ENV,
            )
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
        self.assertEqual(json.loads(body)["deployment"]["platform"], "local")
        self.assertEqual(json.loads(body)["contract"]["profile"], "local_provider_fixture")
        self.assertFalse(json.loads(body)["contract"]["postgresql_durable"])
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
            snapshot = self.gateway.operational_stats()
            self.assertEqual(snapshot["provider"]["saturated_total"], 1)
            self.assertEqual(snapshot["provider"]["inflight"], 0)
        finally:
            for _ in held:
                self.gateway._provider_slots.release()

    def test_member_admin_can_read_only_content_free_operational_counters(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        invitation, _ = activate_member(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        self.gateway.console.sessions.grant(
            organization_id="customer-a",
            membership_id=invitation.membership_id,
            role="member_admin",
        )
        _, credential = console_credential(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        status, headers, body = self.request(
            "GET",
            "/v1/admin/operations",
            headers={"Cookie": "__Host-hormuz_console=" + credential},
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        value = json.loads(body)
        self.assertEqual(value["schema_id"], "hormuz.provider-operations")
        self.assertEqual(value["content_boundary"], "aggregate_content_free_counters")
        self.assertEqual(value["provider"]["capacity"], PROVIDER_MAX_INFERENCE_CONNECTIONS)
        self.assertEqual(value["provider"]["connection_capacity"], PROVIDER_MAX_CONNECTIONS)
        self.assertFalse(value["postgresql_pool"]["configured"])
        self.assertNotIn("organization", json.dumps(value))

    def test_connection_slot_rejections_are_counted_as_worker_pressure(self):
        held = []
        try:
            for _ in range(PROVIDER_MAX_CONNECTIONS):
                acquired = self.gateway._connection_slots.acquire(blocking=False)
                self.assertTrue(acquired)
                held.append(acquired)
            request = Mock()
            with patch.object(self.gateway, "shutdown_request") as shutdown_request:
                self.gateway.process_request(request, ("127.0.0.1", 1))
            shutdown_request.assert_called_once_with(request)
            snapshot = self.gateway.operational_stats()
            self.assertEqual(snapshot["provider"]["connection_capacity"], PROVIDER_MAX_CONNECTIONS)
            self.assertEqual(snapshot["provider"]["connection_saturated_total"], 1)
        finally:
            for _ in held:
                self.gateway._connection_slots.release()

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

        status, reliability_headers, reliability_body = self.request(
            "GET",
            "/v1/gateway/reliability",
            headers={"Authorization": "Bearer " + pair.access_token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(reliability_headers["Cache-Control"], "no-store")
        summary = json.loads(reliability_body)
        self.assertEqual(summary["scope"], "current_actor")
        self.assertEqual(summary["live_provider_request_count"], 1)
        self.assertEqual(summary["provider_attempt_record_count"], 2)
        self.assertEqual(summary["latency_header_sample_count"], 2)
        self.assertEqual(summary["latency_first_body_byte_sample_count"], 1)
        self.assertEqual(summary["latency_total_sample_count"], 2)
        self.assertEqual(summary["failover_link_record_count"], 1)
        self.assertEqual(summary["outcome_unknown_count"], 0)
        self.assertEqual(summary["provider_capacity"], PROVIDER_MAX_INFERENCE_CONNECTIONS)
        self.assertEqual(summary["deployment"]["platform"], "local")

    def test_authorized_failover_rehearsal_simulates_only_the_first_egress(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        success = _ProviderResponse(200, {
            "id": "resp_rehearsal", "object": "response", "status": "completed",
            "model": "openai-secondary-model", "output": [],
            "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        })
        with patch("hormuz.server.urllib.request.urlopen", return_value=success) as provider:
            status, headers, body = self.request(
                "POST",
                "/v1/responses",
                body={"model": "openai-primary", "input": "synthetic rehearsal", "max_output_tokens": 4},
                headers={
                    "Authorization": "Bearer " + pair.access_token,
                    "X-Hormuz-Failover-Rehearsal": self.settings[
                        PROVIDER_FAILOVER_REHEARSAL_ENV
                    ],
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Hormuz-Failover"], "v1;reason=provider_rate_limited")
        self.assertEqual(headers["X-Hormuz-Failover-Rehearsal"], "v1")
        self.assertEqual(json.loads(body)["model"], "openai-secondary-model")
        provider.assert_called_once()
        self.assertEqual(json.loads(provider.call_args.args[0].data)["model"], "openai-secondary-model")

        with patch("hormuz.server.urllib.request.urlopen") as provider:
            status, _, body = self.request(
                "POST",
                "/v1/responses",
                body={"model": "openai-primary", "input": "synthetic rejected rehearsal"},
                headers={
                    "Authorization": "Bearer " + pair.access_token,
                    "X-Hormuz-Failover-Rehearsal": "wrong-rehearsal-key",
                },
            )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(body)["error"]["code"], "hormuz_failover_rehearsal_rejected")
        provider.assert_not_called()

    def test_authorized_cancellation_rehearsal_closes_upstream_and_never_replays(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        response = _ProviderResponse(200)
        response.headers["Content-Type"] = "text/event-stream"
        response._body = b'data: {"type":"response.output_text.delta","delta":"x"}\n\n'
        with (
            patch("hormuz.server.urllib.request.urlopen", return_value=response) as provider,
            patch(
                "hormuz.server.GatewayRequestHandler._write_downstream_chunk",
                side_effect=BrokenPipeError,
            ),
        ):
            status, headers, body = self.request(
                "POST",
                "/v1/responses",
                body={"model": "openai-primary", "input": "synthetic cancellation", "stream": True},
                headers={
                    "Authorization": "Bearer " + pair.access_token,
                    "X-Hormuz-Cancellation-Rehearsal": self.settings[
                        PROVIDER_FAILOVER_REHEARSAL_ENV
                    ],
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Hormuz-Cancellation-Rehearsal"], "v1")
        self.assertEqual(body, b"")
        provider.assert_called_once()
        self.assertTrue(response.closed)
        summary_status, _, summary_body = self.request(
            "GET",
            "/v1/gateway/reliability",
            headers={"Authorization": "Bearer " + pair.access_token},
        )
        self.assertEqual(summary_status, 200)
        summary = json.loads(summary_body)
        self.assertEqual(summary["live_provider_request_count"], 1)
        self.assertEqual(summary["provider_attempt_record_count"], 1)
        self.assertEqual(summary["cancellation_outcome_unknown_count"], 1)
        self.assertEqual(summary["failover_link_record_count"], 0)

    def test_cancellation_rehearsal_does_not_accept_an_already_completed_stream(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        response = _ProviderResponse(200)
        response.headers["Content-Type"] = "text/event-stream"
        response._body = (
            b'event: response.completed\n'
            b'data: {"type":"response.completed","response":{"model":"openai-primary-model",'
            b'"usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )
        with patch("hormuz.server.urllib.request.urlopen", return_value=response) as provider:
            status, headers, body = self.request(
                "POST",
                "/v1/responses",
                body={"model": "openai-primary", "input": "synthetic completed stream", "stream": True},
                headers={
                    "Authorization": "Bearer " + pair.access_token,
                    "X-Hormuz-Cancellation-Rehearsal": self.settings[
                        PROVIDER_FAILOVER_REHEARSAL_ENV
                    ],
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Hormuz-Cancellation-Rehearsal"], "v1")
        self.assertIn(b"response.completed", body)
        provider.assert_called_once()
        self.assertTrue(response.closed)
        summary_status, _, summary_body = self.request(
            "GET",
            "/v1/gateway/reliability",
            headers={"Authorization": "Bearer " + pair.access_token},
        )
        self.assertEqual(summary_status, 200)
        summary = json.loads(summary_body)
        self.assertEqual(summary["live_provider_request_count"], 1)
        self.assertEqual(summary["provider_attempt_record_count"], 1)
        self.assertEqual(summary["cancellation_outcome_unknown_count"], 0)

    def test_disconnect_on_terminal_chunk_records_completed_provider_work(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        invitation, pair = activate_member(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        response = _ProviderResponse(200)
        response.headers["Content-Type"] = "text/event-stream"
        response._body = (
            b'event: response.completed\n'
            b'data: {"type":"response.completed","response":{"model":"openai-primary-model",'
            b'"usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )
        with (
            patch("hormuz.server.urllib.request.urlopen", return_value=response),
            patch(
                "hormuz.server.GatewayRequestHandler._write_downstream_chunk",
                side_effect=BrokenPipeError,
            ),
        ):
            status, _, body = self.request(
                "POST",
                "/v1/responses",
                body={"model": "openai-primary", "input": "terminal disconnect", "stream": True},
                headers={"Authorization": "Bearer " + pair.access_token},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertTrue(response.closed)
        summary_status, _, summary_body = self.request(
            "GET",
            "/v1/gateway/reliability",
            headers={"Authorization": "Bearer " + pair.access_token},
        )
        self.assertEqual(summary_status, 200)
        summary = json.loads(summary_body)
        self.assertEqual(summary["live_provider_request_count"], 1)
        self.assertEqual(summary["provider_attempt_record_count"], 1)
        self.assertEqual(summary["outcome_unknown_count"], 0)
        self.assertEqual(summary["cancellation_outcome_unknown_count"], 0)
        self.assertEqual(
            self.gateway.store.monthly_totals(actor_id=invitation.membership_id).requests,
            1,
        )

    def test_disconnect_on_failed_terminal_chunk_records_known_failure(self):
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(
            self.gateway.session_broker.store,
            self.gateway.session_broker.directory,
        )
        response = _ProviderResponse(200)
        response.headers["Content-Type"] = "text/event-stream"
        response._body = (
            b'event: response.failed\n'
            b'data: {"type":"response.failed","response":{"status":"failed",'
            b'"model":"openai-primary-model","usage":{"input_tokens":2,'
            b'"output_tokens":1}}}\n\n'
        )
        with (
            patch("hormuz.server.urllib.request.urlopen", return_value=response),
            patch(
                "hormuz.server.GatewayRequestHandler._write_downstream_chunk",
                side_effect=BrokenPipeError,
            ),
        ):
            status, _, body = self.request(
                "POST",
                "/v1/responses",
                body={"model": "openai-primary", "input": "failed terminal disconnect", "stream": True},
                headers={"Authorization": "Bearer " + pair.access_token},
            )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertTrue(response.closed)

        summary_status, _, summary_body = self.request(
            "GET",
            "/v1/gateway/reliability",
            headers={"Authorization": "Bearer " + pair.access_token},
        )
        self.assertEqual(summary_status, 200)
        summary = json.loads(summary_body)
        self.assertEqual(summary["outcome_unknown_count"], 0)
        self.assertEqual(summary["cancellation_outcome_unknown_count"], 0)
        with closing(sqlite3.connect(self.config.database_path)) as connection:
            row = connection.execute(
                "SELECT terminal.state, usage.status, finance.terminal_state "
                "FROM gateway_finance_attempt_evidence AS finance "
                "JOIN gateway_request_attempt_events AS terminal "
                "ON terminal.organization_id=finance.organization_id "
                "AND terminal.id=finance.terminal_attempt_event_id "
                "JOIN gateway_usage_events AS usage "
                "ON usage.organization_id=finance.organization_id "
                "AND usage.id=finance.usage_event_id"
            ).fetchone()
        self.assertEqual(row, ("failed", "failed", "failed"))

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
