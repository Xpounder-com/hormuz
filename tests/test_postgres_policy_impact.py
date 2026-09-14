"""Real PostgreSQL grants, immutable v2 documents and generation CAS."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hormuz.policy_control import PolicyControlService
from hormuz.policy_impact_control import candidate_document
from hormuz.policy_repository import PolicyAdministrator, PolicyControlError
from hormuz.policy_runtime import PolicyRuntime
from hormuz.contracts import validate_contract
from tests._postgres_fixture import PostgresTestCase


class PostgresPolicyImpactTests(PostgresTestCase):
    def test_managed_directory_two_listener_apply_receipt_and_rollback(self):
        from tests._policy_impact_evaluation_fixture import policy_impact_evaluation
        with policy_impact_evaluation(self) as (case, controller, root, gateway, console, proxy, request, info):
            self.assertNotIn("customer-a", gateway.config.organization_ids)
            self.assertIn("customer-a", gateway.session_broker.directory.managed_organization_ids())
            case.login_console()
            def post(action, values):
                status, _, result = case.request("POST", "/v1/admin/policy/" + action,
                    {"csrf_token": case.csrf, **values}, {"Origin": case.gateway_url, "Cookie": case.cookie})
                self.assertEqual(status, 200, result)
                return result
            preview = post("preview", {"team_id": "customer-a-eng", "model_alias": "safe-openai", "proposed_limit": 16})
            values = {"preview_id": preview["preview_id"], "acknowledged": True}
            post("review", {"preview_id": preview["preview_id"]})
            self.assertTrue(post("apply", values)["candidate_active"])
            self.assertEqual(request(), 200)
            result = console.policy_console.results(case.cookie.split("=", 1)[1], preview["preview_id"])
            self.assertEqual(result["captured_requests"], 1)
            self.assertEqual(result["receipts"][0]["effective_limit"], 16)
            self.assertEqual(result["receipts"][0]["policy_version"], preview["candidate_version"])
            self.assertEqual(post("rollback", values)["active_version"], preview["baseline_version"])
            self.assertEqual(controller.browser_baseline(root)[1], 3)
            self.assertTrue(controller.browser_history(root).events)
            self.assertFalse(hasattr(gateway, "policy_console"))
    def initialized(self):
        config, environment, _ = self._managed_config(include_bob=True)
        service = PolicyControlService(config, environ=environment)
        service.bootstrap(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        first = self._stage(service, environment=environment, document=self._policy_document())
        service.activate(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN", version_id=first.version_id)
        caller = PolicyAdministrator(organization_id="xpounder", authentication_kind="static", actor_id="alice")
        candidate = candidate_document(first.document, config=config, team_id="engineering", model_alias="gpt-5.4-mini", cap=4000)
        return config, environment, service, first, caller, candidate

    def test_apply_runtime_enforcement_and_rollback_preserve_v1_document(self):
        config, env, service, first, caller, candidate = self.initialized()
        runtime = PolicyRuntime(config, environ=env)
        pinned = runtime.snapshot_for(config.identities_by_actor["alice"])
        service.browser_apply(caller, candidate, baseline_version=first.version_id, generation=1)
        current = runtime.snapshot_for(config.identities_by_actor["alice"])
        self.assertEqual(current.model_output_limits, {"gpt-5.4-mini": 4000})
        self.assertEqual(pinned.model_output_limits, {})
        self.assertEqual(service.browser_baseline(caller)[1], 2)
        # Existing versioned status/history serializers validate the v2 nested summary.
        status = service.status(organization_id="xpounder", credential_env="HORMUZ_POLICY_ADMIN_TOKEN")
        self.assertEqual(len(status.versions), 2)
        service.browser_rollback(caller, target_version=first.version_id, active_version=candidate.version_id, generation=2)
        record, generation = service.browser_baseline(caller)
        self.assertEqual((record.version_id, generation), (first.version_id, 3))
        self.assertEqual(record.document.canonical_json, first.document.canonical_json)
        with self.assertRaisesRegex(PolicyControlError, "policy_active_version_mismatch"):
            service.browser_apply(caller, candidate, baseline_version=first.version_id, generation=1)

    def test_racing_applies_have_exactly_one_winner(self):
        config, env, service, first, caller, candidate = self.initialized()
        other = candidate_document(first.document, config=config, team_id="engineering", model_alias="gpt-5.4-mini", cap=2000)
        def apply(document):
            try:
                service.browser_apply(caller, document, baseline_version=first.version_id, generation=1)
                return "applied"
            except PolicyControlError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply, (candidate, other)))
        self.assertCountEqual(results, ["applied", "policy_active_version_mismatch"])
        self.assertEqual(service.browser_baseline(caller)[1], 2)

    def test_runtime_identity_without_root_grant_cannot_read_or_apply(self):
        config, env, service, first, caller, candidate = self.initialized()
        unauthorized = replace(caller, actor_id="bob")
        for action in (lambda: service.browser_baseline(unauthorized),
                       lambda: service.browser_apply(unauthorized, candidate, baseline_version=first.version_id, generation=1)):
            with self.assertRaisesRegex(PolicyControlError, "policy_administrator_required"):
                action()
        self.assertEqual(service.browser_baseline(caller)[1], 1)

    def test_isolated_listener_loads_only_session_credentials_and_has_no_inference_route(self):
        import http.client
        import json
        import threading
        from hormuz._config_builder import build_policy_console_config
        from hormuz.policy_console import PolicyConsoleServer
        from tests._session_fixtures import session_config, fixture_environment
        config, env, _, _, _, _ = self.initialized()
        raw = json.loads(config.source_path.read_text())
        raw["authentication"] = session_config(config.source_path.parent, "http://127.0.0.1:9444", "http://127.0.0.1:8787")["authentication"]
        raw["authentication"]["oidc"]["issuers"][0]["subjects"] = []
        raw["authentication"]["session_broker"].update(onboarding_enabled=True, console_enabled=True, policy_impact_enabled=True)
        config.source_path.write_text(json.dumps(raw))
        browser_env = {k: v for k, v in fixture_environment().items() if k != "TEST_PROVIDER_KEY"}
        browser_env.update({config.usage_storage.postgres_dsn_env: env[config.usage_storage.postgres_dsn_env],
                            config.policy_control.postgres_control_dsn_env: env[config.policy_control.postgres_control_dsn_env]})
        # Static employee tokens, provider keys, migration and break-glass secrets are absent.
        console_config = build_policy_console_config(config.source_path, environ=browser_env)
        self.assertTrue(all(not identity.token for identity in console_config.identities_by_token.values()))
        server = PolicyConsoleServer(console_config, address=("127.0.0.1", 0), environ=browser_env)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        thread.start()
        try:
            for method, path, expected in (("GET", "/console", 200), ("POST", "/v1/responses", 404), ("GET", "/v1/admin/policy/scopes", 401)):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                connection.request(method, path, headers={"Host": "127.0.0.1:8787", "Origin": "http://127.0.0.1:8787"})
                response = connection.getresponse()
                body = response.read()
                self.assertEqual(response.status, expected, body)
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
