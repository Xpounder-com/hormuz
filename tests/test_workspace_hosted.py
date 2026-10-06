import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from hormuz._hosted_config import HostedError, load_profile, load_workspace_profile
from hormuz._hosted_server import WorkspaceGatewayServer
from hormuz._hosted_state import initialize, check_initialized, migrate_sessions, snapshot, restore, at_directory, sessions
from tests._hosted_fixtures import profile
from tests import test_hosted_http as hosted_http


class WorkspaceProfileTests(unittest.TestCase):
    def test_profile_is_explicit_and_provider_free(self):
        with tempfile.TemporaryDirectory() as directory:
            config, secrets, document = profile(Path(directory).resolve())
            document["schema"] = "hormuz.hosted-workspaces/v1"
            config.source_path.write_text(json.dumps(document))
            with self.assertRaises(HostedError):
                load_profile(config.source_path, secrets)
            config = load_workspace_profile(config.source_path, secrets)
            self.assertTrue(config.session_broker.workspace_enabled)
            self.assertEqual(config.upstreams, {})
            self.assertEqual(config.model_routes, {})

    def test_offline_migration_keeps_a_v4_snapshot_before_upgrading(self):
        import sqlite3
        from contextlib import closing
        from hormuz._workspace_schema import TABLE_DDL
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config, _, _ = profile(root)
            initialize(config)
            with closing(sqlite3.connect(config.session_broker.database_path)) as connection, connection:
                for table in TABLE_DDL:
                    connection.execute("DROP TABLE " + table)
                connection.execute("PRAGMA user_version = 4")
            with self.assertRaisesRegex(HostedError, "hosted_state_schema_mismatch"):
                check_initialized(config)
            result = migrate_sessions(config, root / "before-workspaces")
            self.assertEqual(result["source_session_schema_version"], 4)
            self.assertEqual(result["target_session_schema_version"], 5)
            with closing(sqlite3.connect(root / "before-workspaces/sessions.sqlite3")) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            check_initialized(config)

    def test_snapshot_restore_closes_workspace_sessions_domains_and_signup_flows(self):
        from hormuz.auth import Authenticator
        from hormuz.session import SessionBroker
        from hormuz.workspace_store import WorkspaceStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config, _, _ = profile(root)
            issuer = next(iter(config.oidc_issuers))
            config = replace(config, session_broker=replace(config.session_broker, workspace_enabled=True, workspace_signup_issuer=issuer))
            initialize(config)
            store = WorkspaceStore(SessionBroker(config, Authenticator(config), sessions(config)))
            flow, state, cookie = store.begin_login()
            flow = store.consume_callback(state, cookie)
            store.complete_login(flow, {"iss": issuer, "sub": "synthetic-owner", "email": "owner@example.com", "email_verified": True})
            store.begin_login()
            snapshot(config, root / "snapshot")
            result = restore(at_directory(config, root / "restored"), root / "snapshot")
            for field in ("active_workspaces", "active_workspace_domains", "unrevoked_workspace_sessions", "active_workspace_login_flows", "unconsumed_workspace_handoffs"):
                self.assertEqual(result[field], 0)


class WorkspaceHostedHTTPTests(unittest.TestCase):
    # Inherit fixture and raw framing helpers without inheriting staging-only
    # acceptance cases, whose route contract intentionally stays separate.
    setUp = hosted_http.HostedHTTPTests.setUp
    tearDown = hosted_http.HostedHTTPTests.tearDown
    stop = hosted_http.HostedHTTPTests.stop
    request = hosted_http.HostedHTTPTests.request
    raw = hosted_http.HostedHTTPTests.raw

    def start(self):
        issuer = next(iter(self.config.oidc_issuers))
        self.config = replace(self.config, session_broker=replace(self.config.session_broker, workspace_enabled=True, workspace_signup_issuer=issuer))
        self.gateway = WorkspaceGatewayServer(replace(self.config, listen=replace(self.config.listen, port=0)))
        import threading
        self.thread = threading.Thread(target=self.gateway.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def test_workspace_routes_and_ingress_boundary(self):
        self.assertEqual(self.request("GET", "/workspace")[0], 200)
        self.assertEqual(self.request("GET", "/workspace", headers={"X-Hormuz-Ingress-Credential": "forged"})[0], 401)
        self.assertEqual(self.request("GET", "/workspace", headers={"Host": "unknown.customer.com"})[0], 400)
        self.assertEqual(self.request("POST", "/v1/responses", body={})[0], 503)

    def test_private_hop_host_and_provider_boundaries(self):
        status, _, body = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["schema_id"], "hormuz.hosted-workspaces")
        self.assertEqual(json.loads(body)["status"], "workspace")
        self.assertEqual(self.gateway.upstream_credentials, {})

    def test_render_health_host_survives_domain_lease_expiry_without_dashboard_access(self):
        from tests.test_workspace_http import FakeDomains
        service = self.gateway.workspace
        service.domains.provider = FakeDomains(service.domains)
        flow, state, browser = service.sessions.begin_login()
        flow = service.sessions.consume_callback(state, browser)
        account = service.sessions.complete_login(flow, {"iss": next(iter(self.config.oidc_issuers)), "sub": "health-fixture", "email": "health@example.com", "email_verified": True})
        domain = service.domains.claim(account["credential"], service.sessions.origin, "ai.customer.com")
        headers = {"Host": domain["hostname"]}
        self.assertEqual(self.request("GET", "/health", headers=headers)[0], 400)
        service.domains.check(account["credential"], service.sessions.origin, domain["id"])
        self.assertEqual(self.request("GET", "/health", headers=headers)[0], 200)
        with service.sessions.store._connection() as connection, connection:
            connection.execute("UPDATE workspace_domains SET verified_until = '2000-01-01T00:00:00Z' WHERE id = ?", (domain["id"],))
        self.assertEqual(self.request("GET", "/ready", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/workspace", headers=headers)[0], 400)
        self.assertEqual(self.request("GET", "/health", headers={"Host": "unknown.customer.com"})[0], 400)

    def test_console_and_generation_routes_remain_closed(self):
        for path in ("/console", "/v1/admin/me", "/v1/auth/enrollments", "/v1/messages"):
            self.assertEqual(self.request("GET", path)[0], 503)
