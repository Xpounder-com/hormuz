"""Full hosted composition through bounded loopback; external services mocked."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import io
import http.client
import json
import logging
import os
import secrets
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch

from hormuz._hosted_config import HostedError, at_directory
from hormuz._hosted_state import check_initialized, snapshot
from hormuz._hosted_work import check_work_stores, initialize, load_profile
from hormuz._hosted_work_server import AIWorkGatewayServer, allowed_route
from hormuz.hosted import _backend_ready, main, proxy_settings, runtime_settings, supervise
from hormuz.work_recovery import owned_databases
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import console_credential, directory_setup, provider_profile
from tests import test_hosted_work as prior_work
SYNTHETIC_BILLING_API_KEY = prior_work.SYNTHETIC_BILLING_API_KEY
from tests._portfolio_fixture import ADMIN, create_request, registry_config
from tests.test_portfolio_api_cli import configuration_document
from tests import test_github_connector_runtime as github


def full_profile(root, *, billing=True, portfolio=True):
    _, _, environment, document = provider_profile(root)
    document["usage_storage"] = {"backend": "sqlite"}
    broker = document["authentication"]["session_broker"]
    broker.update(workspace_enabled=True, workspace_signup_issuer=document["authentication"]["oidc"]["issuers"][0]["issuer"],
                  desktop_defaults={"customer-a": {"codex": "openai-primary", "claude-code": "anthropic-primary"}})
    document["ai_work"] = {"enabled": True, "database": str(root / "state" / "hormuz-work.sqlite3")}
    if billing:
        document["ai_work"].update(require_paid=True, billing_price_id="price_Reviewed",
            billing_bindings=[{"organization_id": "customer-a", "customer_id": "cus_Reviewed", "subscription_id": "sub_Reviewed"}])
        environment.update(HORMUZ_WORK_BILLING_WEBHOOK_SECRET="whsec_synthetic_fixture_not_real",
                           HORMUZ_WORK_BILLING_API_KEY=SYNTHETIC_BILLING_API_KEY)
    if portfolio:
        registry, values = configuration_document(registry_config(root))
        document["identities"], document["portfolio_control"] = registry["identities"], registry["portfolio_control"]
        document["outcome_connectors"] = github.runtime_document()
        environment.update(values)
        environment.update(SYNTHETIC_GITHUB_WEBHOOK_SECRET=github.WEBHOOK_SECRET,
                           SYNTHETIC_GITHUB_IDENTITY_KEY=github.IDENTITY_KEY)
    environment["HORMUZ_HOSTED_MODE"] = "ai-work"
    path = root / "ai-work.json"
    path.write_text(json.dumps(document))
    path.chmod(0o600)
    environment["HORMUZ_CONFIG"] = str(path)
    config, selected = load_profile(path, environment)
    return config, selected, environment, document


class AIWorkHostedHTTPTests(unittest.TestCase):
    webhook = prior_work.HostedWorkHTTPTests.webhook

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=2)
        fields = {"Host": "gateway.example.test", "X-Hormuz-Ingress-Credential": self.config.ingress.credential, **(headers or {})}
        encoded = body
        if body is not None and not isinstance(body, (bytes, str)):
            encoded = json.dumps(body, separators=(",", ":")).encode()
            fields["Content-Type"] = "application/json"
        try:
            connection.request(method, path, body=encoded, headers=fields)
            response = connection.getresponse()
            try:
                value = response.read(2 * 1024 * 1024 + 1)
                self.assertLessEqual(len(value), 2 * 1024 * 1024)
                return response.status, dict(response.getheaders()), value
            finally:
                response.close()
        finally:
            connection.close()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config, self.selected, self.settings, _ = full_profile(self.root)
        initialize(self.config, self.selected)
        self.config = replace(self.config, listen=replace(self.config.listen, port=0))
        self.gateway = AIWorkGatewayServer(self.config, environ=self.selected)
        self.thread = threading.Thread(target=self.gateway.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        directory_setup(self.gateway.session_broker.directory, self.config)
        self.member, self.pair = activate_member(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        self.token_headers = {"Authorization": "Bearer " + self.pair.access_token}
        self.secret = self.selected["HORMUZ_WORK_BILLING_WEBHOOK_SECRET"]

    def stop(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def test_workspace_work_health_and_strict_private_hop_share_one_server(self):
        with patch("hormuz.server._open_upstream") as provider:
            status, _, body = self.request("GET", "/health")
            self.assertEqual(status, 200)
            report = json.loads(body)
            self.assertEqual(report["schema_id"], "hormuz.hosted-ai-work")
            self.assertEqual(report["contract"]["storage_backend"], "sqlite")
            self.assertNotIn("tenant_rls", report["contract"])
            self.assertEqual(self.request("GET", "/ready")[0], 200)
            with self.assertRaisesRegex(HostedError, "owner_active"):
                snapshot(self.config, self.root / "live-owner-backup")
            self.assertEqual(self.request("GET", "/workspace")[0], 200)
            status, _, body = self.request("GET", "/work")
            self.assertEqual(status, 200)
            self.assertIn(b'href="/workspace"', body)
            for path in ("/workspace", "/work", "/health", github.GITHUB_EVENTS_PATH):
                self.assertEqual(self.request("GET", path, headers={"X-Hormuz-Ingress-Credential": "wrong"})[0], 401)
            for path in ("/v1/private", "/v1/admin/anything", "/workspace/../console", "/v1/connectors/github/admin"):
                self.assertEqual(self.request("GET", path)[0], 503, path)
        provider.assert_not_called()

    def test_portfolio_idempotency_and_signed_connector_update_existing_work(self):
        headers = {"Authorization": "Bearer " + ADMIN, "Idempotency-Key": "create-full-mode"}
        with patch("hormuz.server._open_upstream") as provider:
            status, _, raw = self.request("POST", "/v1/admin/portfolio/work-scopes", body=create_request(), headers=headers)
            self.assertEqual(status, 201, raw)
            self.assertEqual(self.request("POST", "/v1/admin/portfolio/work-scopes", body=create_request(), headers=headers)[2], raw)
            self.assertEqual(self.request("POST", "/v1/admin/portfolio/work-scopes", body=create_request(), headers={"Authorization": "Bearer " + ADMIN})[0], 400)
            identity = self.config.identities_by_token[ADMIN]
            work = self.gateway.work_runtime.create_work(identity, "fixture/repository", completion_condition="github.pull_request.merged.v1")
            self.gateway.work_workflow.bind(identity, work["work_id"], provider="github", connector_id="github-one",
                container_id="456", object_id="1001", completion_condition="github.pull_request.merged.v1")
            value = github.payload(action="closed")
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            value["pull_request"].update(merged=True, closed_at=now, updated_at=now)
            event_headers = github.signed(github.encoded(value))
            status, _, body = self.request("POST", github.GITHUB_EVENTS_PATH, body=value, headers=event_headers)
            self.assertEqual(status, 202, body)
            self.assertEqual(self.gateway.work_runtime.get_work(identity, work["work_id"])["state"], "completed")
            self.assertEqual(self.request("POST", github.GITHUB_EVENTS_PATH, body=value,
                headers={**event_headers, "X-Hub-Signature-256": "sha256=" + "0" * 64})[0], 401)
        provider.assert_not_called()

    # Real Work/Stripe signed-event/parser/accounting path; only provider bytes
    # are synthetic. The predecessor's identical gate remains independently tested.
    test_paid_chat_retains_work_accounting_and_releases_capacity = prior_work.HostedWorkHTTPTests.test_chat_follows_paid_gate_and_provider_capacity_accounting
    test_history_keeps_auth_ownership_cursor_and_methods = prior_work.HostedWorkHTTPTests.test_history_keeps_auth_ownership_cursor_and_method_checks

    def test_registered_custom_host_reaches_workspace_and_work_but_not_global_routes(self):
        from tests.test_workspace_http import FakeDomains
        sessions = self.gateway.workspace.sessions
        flow, state, cookie = sessions.begin_login()
        flow = sessions.consume_callback(state, cookie)
        account = sessions.complete_login(flow, {"iss": next(iter(self.config.oidc_issuers)), "sub": "full-owner",
            "email": "owner@example.com", "email_verified": True})
        domains = self.gateway.workspace.domains
        domains.provider = FakeDomains(domains)
        domain = domains.claim(account["credential"], sessions.origin, "ai.customer.com")
        domains.check(account["credential"], sessions.origin, domain["id"])
        headers = {"Host": "ai.customer.com"}
        self.assertEqual(self.request("GET", "/workspace", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/work", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/v1/models", headers=headers)[0], 400)
        self.assertEqual(self.request("GET", "/work", headers={"Host": "unregistered.customer.com"})[0], 400)
        self.assertEqual(self.request("GET", "/v1/work/state", headers=headers)[0], 401)

    def test_missing_work_store_drains_without_recreation(self):
        self.config.ai_work.database_path.unlink()
        self.assertEqual(self.request("GET", "/ready")[0], 503)
        self.assertEqual(self.request("GET", "/work", headers=self.token_headers)[0], 503)
        self.assertFalse(self.config.ai_work.database_path.exists())
        self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_member_console_routes_revoke_native_and_browser_sessions_with_guards(self):
        directory = self.gateway.session_broker.directory
        store = self.gateway.session_broker.store
        console = self.gateway.console.sessions
        console.grant(organization_id="customer-a", membership_id=self.member.membership_id, role="member_admin")
        _, credential = console_credential(store, directory)
        headers = {"Cookie": "__Host-hormuz_console=" + credential}
        status, _, raw = self.request("GET", "/v1/admin/me", headers=headers)
        self.assertEqual(status, 200, raw)
        csrf = json.loads(raw)["csrf_token"]
        target, pair = activate_member(store, directory, subject="target-subject", email="target@example.test")
        console.grant(organization_id="customer-a", membership_id=target.membership_id, role="report_viewer")
        state, cookie = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        console.begin_login(organization_id="customer-a", state=state, browser_cookie=cookie,
                            nonce=secrets.token_urlsafe(32), pkce_verifier=secrets.token_urlsafe(64))
        flow = console.consume_callback(state=state, browser_cookie=cookie)
        target_credential = console.complete_login(flow, {"iss": next(iter(self.config.oidc_issuers)), "sub": "target-subject"})
        target_headers = {"Cookie": "__Host-hormuz_console=" + target_credential}
        self.assertEqual(self.request("GET", "/v1/admin/me", headers=target_headers)[0], 200)
        for path in ("/v1/admin/members", "/v1/admin/teams"):
            self.assertEqual(self.request("GET", path, headers=headers)[0], 200)
            self.assertEqual(self.request("GET", path)[0], 401)
        body = {"membership_id": target.membership_id, "expected_version": 1, "csrf_token": csrf}
        self.assertEqual(self.request("POST", "/v1/admin/members/disable", body=body, headers={**headers, "Origin": "https://foreign.invalid"})[0], 403)
        origin = self.config.session_broker.public_base_url
        self.assertEqual(self.request("POST", "/v1/admin/members/disable", body={**body, "csrf_token": "wrong"}, headers={**headers, "Origin": origin})[0], 403)
        self.assertEqual(self.request("POST", "/v1/admin/members/disable", body=body, headers={**target_headers, "Origin": origin})[0], 403)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair.access_token})[0], 200)
        self.assertEqual(self.request("POST", "/v1/admin/members/disable", body=body, headers={**headers, "Origin": origin})[0], 200)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair.access_token})[0], 401)
        self.assertEqual(self.request("GET", "/v1/admin/me", headers=target_headers)[0], 401)
        self.assertEqual(self.request("GET", "/v1/admin/me", headers=headers)[0], 200)

    def test_native_and_desktop_enrollment_callback_and_exact_redemption_paths(self):
        broker = self.gateway.session_broker
        issuer = next(iter(self.config.oidc_issuers))
        metadata = SimpleNamespace(authorization_endpoint=issuer + "/authorize", token_endpoint=issuer + "/token", userinfo_endpoint=issuer + "/userinfo")
        claims = {"iss": issuer, "sub": "admin-subject", "email": "admin@example.test", "email_verified": True}
        with patch.object(broker.authenticator, "login_metadata", return_value=metadata), patch.object(broker.authenticator, "validate_login_claims", return_value=claims), patch("hormuz.session._exchange_code", return_value={"id_token": "synthetic-reviewed-token"}), patch("hormuz.server._open_upstream") as provider:
            for prefix in ("/v1/auth/enrollments", "/v1/auth/desktop/enrollments"):
                with self.subTest(prefix=prefix):
                    secret = secrets.token_urlsafe(32)
                    payload = {"client": "codex", "enrollment_secret": secret}
                    if prefix == "/v1/auth/enrollments":
                        payload["organization_id"] = "customer-a"
                    status, _, raw = self.request("POST", prefix, body=payload)
                    self.assertEqual(status, 201, raw)
                    created = json.loads(raw)
                    path = prefix + "/" + created["enrollment_id"] + "/redeem"
                    self.assertEqual(self.request("POST", path, body={"enrollment_secret": secret})[0], 409)
                    status, headers, raw = self.request("GET", "/v1/auth/login?enrollment=" + created["enrollment_id"])
                    self.assertEqual(status, 302, raw)
                    state = parse_qs(urlsplit(headers["Location"]).query)["state"][0]
                    callback = urlencode({"state": state, "code": "synthetic-code", "iss": issuer})
                    status, _, raw = self.request("POST", "/v1/auth/callback", body=callback, headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": headers["Set-Cookie"].split(";", 1)[0]})
                    self.assertEqual(status, 200, raw)
                    self.assertEqual(self.request("GET", path)[0], 503)
                    self.assertEqual(self.request("POST", path, body={"enrollment_secret": "x" * 43})[0], 409)
                    self.assertEqual(self.request("POST", path, body={"enrollment_secret": secret}, headers={"Origin": issuer})[0], 400)
                    status, _, raw = self.request("POST", path, body={"enrollment_secret": secret})
                    self.assertEqual(status, 200, raw)
                    result = json.loads(raw)
                    self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + result["access_token"]})[0], 200)
                    if "desktop" in prefix:
                        self.assertEqual(result["desktop_profile"]["model_alias"], "openai-primary")
                    self.assertNotEqual(self.request("POST", path, body={"enrollment_secret": secret})[0], 200)
                    for identifier in ("x" * 31, "x" * 33, "../anything", "x" * 31 + "%2F"):
                        self.assertEqual(self.request("POST", prefix + "/" + identifier + "/redeem", body={"enrollment_secret": secret})[0], 503)
        provider.assert_not_called()


class AIWorkHostedLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)

    def test_interrupted_fresh_initialization_is_cleaned_and_retry_publishes_all_stores(self):
        from hormuz import work_gateway
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config, selected, _, _ = full_profile(root)
            create = work_gateway.initialize

            def fail_after_creation(owner, environ):
                create(owner, environ)
                raise OSError("synthetic interruption after Work and billing creation")

            with patch("hormuz.work_gateway.initialize", side_effect=fail_after_creation):
                with self.assertRaisesRegex(OSError, "synthetic interruption"):
                    initialize(config, selected)
            self.assertFalse(config.database_path.parent.exists())
            self.assertEqual(list(root.glob(".state.initialize-*")), [])
            initialize(config, selected)
            check_initialized(config)
            self.assertEqual(len(check_work_stores(config)), 2)
            marker = (config.database_path.parent / "initialized.json").read_bytes()
            with self.assertRaises(FileExistsError):
                initialize(config, selected)
            self.assertEqual(marker, (config.database_path.parent / "initialized.json").read_bytes())

    def test_initialization_preserves_preexisting_partial_and_unexpected_staging_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config, selected, _, _ = full_profile(root)
            config.database_path.parent.mkdir(mode=0o700)
            partial = config.database_path.parent / "owner-data"
            partial.write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                initialize(config, selected)
            self.assertEqual(partial.read_bytes(), b"preserve")
            partial.unlink()
            config.database_path.parent.rmdir()

            def unknown(owner, environ):
                path = owner.config.database_path.parent / "owner-added"
                path.write_bytes(b"unknown-preserve")
                path.chmod(0o600)
                raise OSError("synthetic owner change")

            with patch("hormuz.work_gateway.initialize", side_effect=unknown):
                with self.assertRaisesRegex(OSError, "synthetic owner change"):
                    initialize(config, selected)
            self.assertFalse(config.database_path.parent.exists())
            roots = list(root.glob(".state.initialize-*"))
            self.assertEqual(len(roots), 1)
            self.assertEqual((roots[0] / "state" / "owner-added").read_bytes(), b"unknown-preserve")
    def test_explicit_cli_initializes_checks_and_recovers_all_stores_without_serving(self):
        from hormuz._hosted_backup import export_backup, restore_backup
        from hormuz.work_runtime import WorkRuntime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config, selected, environment, _ = full_profile(root)
            with patch.dict(os.environ, environment, clear=True), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--config", str(config.source_path), "--ai-work-profile", "initialize"]), 0)
                self.assertEqual(main(["--config", str(config.source_path), "ai-work-check"]), 0)
            self.assertEqual(len(check_work_stores(config)), 2)
            identity = config.identities_by_token[ADMIN]
            runtime = WorkRuntime(config.ai_work.database_path, config=config)
            try:
                work = runtime.create_work(identity, "fixture/repo")["work_id"]
                runtime.reserve(identity, work, "unknown-request", "openai-primary", "openai", 500)
            finally:
                runtime.close()
            archive = root / "backup.hzb"
            export_backup(config, archive, b"b" * 32)
            restored = at_directory(config, root / "restored")
            self.assertTrue(restore_backup(restored, archive, b"b" * 32)["recovered_closed"])
            self.assertEqual(len(check_work_stores(restored)), 2)
            runtime = WorkRuntime(restored.ai_work.database_path, config=restored)
            try:
                self.assertEqual(runtime.get_work(identity, work)["costs"]["uncertain_microusd"], 500)
                self.assertEqual(runtime.get_work(identity, work)["state"], "paused")
            finally:
                runtime.close()

    def test_missing_uninitialized_full_stores_are_not_created_by_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            config, selected, _, _ = full_profile(Path(directory).resolve(), billing=False)
            with self.assertRaises(OSError):
                AIWorkGatewayServer(config, environ=selected)
            self.assertFalse(config.database_path.parent.exists())

    def test_new_startup_readiness_is_one_attempt_plus_three_bounded_retries(self):
        process, stopped, opener = Mock(), Mock(), Mock()
        process.poll.return_value = None
        stopped.is_set.return_value = False
        opener.open.side_effect = OSError("synthetic unavailable")
        with patch("hormuz.hosted.build_opener", return_value=opener), patch("hormuz.hosted.random.uniform", side_effect=[0.02, 0.09, 0.05]) as jitter:
            config = Mock()
            config.session_broker.public_base_url = "https://gateway.example.test"
            config.ingress.credential = "synthetic-ingress"
            with self.assertRaisesRegex(HostedError, "backend_not_ready"):
                _backend_ready(process, config, stopped, bounded=True)
        self.assertEqual(opener.open.call_count, 4)
        self.assertEqual([call.args[0] for call in stopped.wait.call_args_list], [0.52, 1.09, 2.05])
        self.assertEqual(jitter.call_count, 3)
        self.assertTrue(all(call.args == (0, 0.1) for call in jitter.call_args_list))
        self.assertTrue(all(call.kwargs["timeout"] == 1 for call in opener.open.call_args_list))

    def test_supervisor_selects_only_declared_child_secrets_and_closes_failed_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            config, selected, environment, _ = full_profile(Path(directory).resolve())
            initialize(config, selected)
            environment["UNRELATED_KEY"] = "never-inherit"
            with patch.dict(os.environ, environment, clear=True):
                settings = runtime_settings()
            child = Mock()
            with patch("hormuz.hosted._spawn", return_value=child) as spawn, patch("hormuz.hosted._backend_ready", side_effect=HostedError("hosted_backend_not_ready")) as ready, patch("hormuz.hosted.stop_child", return_value=True) as stop, patch("hormuz.hosted.signal.signal"), patch("hormuz.hosted.load_provider_profile") as legacy, redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(HostedError, "backend_not_ready"):
                    supervise(settings, config.source_path, Path(settings["HORMUZ_PROVIDER_CONFIG"]), environ=environment)
            spawn.assert_called_once()
            self.assertEqual(spawn.call_args.args[0][-1], "ai-work-backend")
            self.assertEqual(spawn.call_args.args[1], selected)
            self.assertNotIn("UNRELATED_KEY", spawn.call_args.args[1])
            self.assertNotIn("HORMUZ_POSTGRES_DSN", spawn.call_args.args[1])
            self.assertTrue(ready.call_args.kwargs["bounded"])
            self.assertEqual([call.args for call in stop.call_args_list], [(None, 5), (child, 15)])
            legacy.assert_not_called()

    def test_new_proxy_and_container_are_explicit_and_preserve_enrollment_key(self):
        root = Path(__file__).resolve().parents[1]
        proxy = (root / "deploy/render/gateway/ai-work.Caddyfile").read_text()
        self.assertNotIn("header_up -Idempotency-Key", proxy)
        self.assertIn("header_up -X-Idempotency-Key", proxy)
        self.assertNotIn("/v1/admin/*", proxy)
        self.assertIn("/v1/admin/portfolio/*", proxy)
        self.assertIn("/v1/connectors/linear/snapshots", proxy)
        self.assertIn("lb_retries 0", proxy)
        for file in ("Dockerfile", "Dockerfile.dockerignore"):
            self.assertIn("ai-work.Caddyfile", (root / "deploy/render/gateway" / file).read_text())
        manifest = (root / "MANIFEST.in").read_text()
        for name in ("ai-work.Caddyfile", "ai-work-profile.example.json"):
            self.assertIn("include deploy/render/gateway/" + name, manifest)
        for path in ("/v1/admin/members", "/v1/admin/teams", "/v1/admin/members/disable"):
            self.assertTrue(allowed_route("POST" if path.endswith("disable") else "GET", path), path)
            self.assertIn(path, proxy)
        for prefix in ("/v1/auth/enrollments", "/v1/auth/desktop/enrollments"):
            self.assertIn(prefix + "/*/redeem", proxy)
            self.assertTrue(allowed_route("POST", prefix + "/" + "a" * 32 + "/redeem"))
            self.assertFalse(allowed_route("GET", prefix + "/" + "a" * 32 + "/redeem"))
        for path in ("/v1/work/jobs/x/attempts/before/prefix%2Fpath", "/workspace", "/v1/workspaces/domains/check", "/v1/admin/portfolio/work-scopes"):
            self.assertTrue(allowed_route("GET", path), path)
        for path in ("/v1/admin/policy/../../secrets", "/v1/admin/portfolio/nothing", "/v1/private", "/workspace/nothing"):
            self.assertFalse(allowed_route("GET", path), path)
