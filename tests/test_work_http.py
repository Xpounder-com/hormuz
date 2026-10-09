"""Real HTTP and OIDC sessions for work ownership, authority, and CSRF."""

from dataclasses import replace
from unittest import mock
import re
from urllib.parse import urlencode
from urllib.parse import quote, urlsplit

from hormuz.config import AIWorkConfig
from hormuz.work_runtime import WorkRuntime
from tests._console_fixtures import ConsoleHTTPTestCase, activate_member
from tests._session_fixtures import SessionHTTPTestCase
from tests import test_workspace_http as workspace_fixture


class WorkHTTPTests(ConsoleHTTPTestCase):
    def configure_gateway(self, config):
        config = super().configure_gateway(config)
        return replace(config, ai_work=AIWorkConfig(enabled=True, database_path=self.root / "work.sqlite3"))

    def setUp(self):
        super().setUp()
        self.gateway.work_runtime = WorkRuntime(self.root / "work.sqlite3", self.config)

    def bearer(self, *, token=None):
        return {"Authorization": "Bearer " + (token or self.native.access_token)}

    def create(self, *, token=None, repository="example/repository"):
        status, _, job = self.request("POST", "/v1/work/jobs", {"repository": repository, "title": "Repair CI", "task_type": "ci-repair", "context_revision": "commit-1"}, self.bearer(token=token))
        self.assertEqual(status, 201, job)
        return job

    def test_bearer_work_ownership_and_tenant_isolation(self):
        job = self.create()
        path = "/v1/work/jobs/" + job["work_id"]
        self.assertEqual(self.request("GET", path, headers=self.bearer())[0], 200)
        self.assertEqual(self.request("GET", path, headers=self.bearer(token=self.member_native.access_token))[0], 404)
        _, outsider = activate_member(self.store, self.directory, subject="outside-subject", email="outside@example.test", organization="customer-b", team="customer-b-eng")
        self.assertEqual(self.request("GET", path, headers=self.bearer(token=outsider.access_token))[0], 404)
        status, _, report = self.request("GET", "/v1/work/state", headers=self.bearer(token=outsider.access_token))
        self.assertEqual(status, 200, report)
        self.assertEqual(report["works"], [])
        self.assertFalse(report["principal"]["can_manage_plans"])
        self.assertEqual(self.request("POST", path + "/actions", {"action": "stop"}, self.bearer(token=outsider.access_token))[0], 404)
        detail_path = "/work/jobs/" + job["work_id"]
        status, _, detail = self.request("GET", detail_path, headers=self.bearer())
        self.assertEqual(status, 200)
        self.assertIn("Job details", detail)
        self.assertEqual(self.request("GET", detail_path, headers=self.bearer(token=outsider.access_token))[0], 404)

    def test_owned_attempt_history_is_bounded_and_paged_in_html_and_api(self):
        job = self.create()
        identity = self.gateway.session_broker.authenticate(self.native.access_token)
        for index in range(52):
            request_id = "x" * 129 if index == 2 else "prefix/path:@id" if index == 1 else "admit-" + str(index)
            self.gateway.work_runtime.reserve(identity, job["work_id"], request_id, "safe-openai", "openai", 1,
                logical_request_id="logical-1", retry_of="admit-0" if index else None)
            self.gateway.work_runtime.settle(identity, request_id, 1, status="failed" if index == 0 else "succeeded")
        path = "/v1/work/jobs/" + job["work_id"] + "/attempts"
        status, _, history = self.request("GET", path, headers=self.bearer())
        self.assertEqual(200, status, history)
        self.assertEqual("owned_job", history["scope"])
        self.assertEqual(50, len(history["attempts"]))
        self.assertEqual("x" * 129, history["next_before"])
        self.assertNotIn("rowid", history)
        status, _, older = self.request("GET", path + "/before/" + history["next_before"], headers=self.bearer())
        self.assertEqual(200, status, older)
        self.assertEqual(["prefix/path:@id", "admit-0"], [row["request_id"] for row in older["attempts"]])
        self.assertIsNone(older["next_before"])
        status, _, after_encoded = self.request("GET", path + "/before/" + quote("prefix/path:@id", safe=""), headers=self.bearer())
        self.assertEqual(200, status)
        self.assertEqual(["admit-0"], [row["request_id"] for row in after_encoded["attempts"]])
        html_path = "/work/jobs/" + job["work_id"] + "/attempts"
        status, _, rendered = self.request("GET", html_path, headers=self.bearer())
        self.assertEqual(200, status)
        self.assertIn(html_path + "/before/" + "x" * 129, rendered)
        self.assertIn("Logical request: logical-1", rendered)
        self.assertIn("Retry of: admit-0", rendered)
        status, _, rendered = self.request("GET", html_path + "/before/" + "x" * 129, headers=self.bearer())
        self.assertEqual(200, status)
        self.assertNotIn("Older attempts", rendered)

    def test_attempt_history_uses_owned_authority_exact_cursor_and_get_only(self):
        job, other = self.create(), self.create()
        identity = self.gateway.session_broker.authenticate(self.native.access_token)
        self.gateway.work_runtime.reserve(identity, other["work_id"], "other-job-request", "safe-openai", "openai", 1)
        path = "/v1/work/jobs/" + job["work_id"] + "/attempts"
        self.assertEqual(404, self.request("GET", path, headers=self.bearer(token=self.member_native.access_token))[0])
        self.assertEqual(404, self.request("GET", path + "/before/other-job-request", headers=self.bearer())[0])
        self.assertEqual(404, self.request("GET", path + "/before/missing", headers=self.bearer())[0])
        self.assertEqual(400, self.request("GET", path + "?limit=1000", headers=self.bearer())[0])
        for invalid in ("x" * 257, "%FF", "prefix%253Fquery", "prefix%3Fquery", "%ZZ"):
            self.assertEqual(400, self.request("GET", path + "/before/" + invalid, headers=self.bearer())[0], invalid)
        self.assertEqual(404, self.request("GET", path + "/before/" + "x" * 769, headers=self.bearer())[0])
        self.assertEqual(404, self.request("POST", path, {}, self.bearer())[0])
        self.assertEqual(404, self.request("GET", path.replace("/v1/work/", "/work/"), headers=self.bearer(token=self.member_native.access_token))[0])

    def test_managed_relay_keeps_job_identity_across_requests(self):
        import http.client
        import json
        import threading
        from hormuz.client_relay import LocalRelayServer, RelayOptimizer
        from hormuz.compaction_runtime import ContextPreferenceStore
        job = self.create()
        local_token = "hox_l_" + "B" * 43
        relay = LocalRelayServer(gateway=self.gateway_url, client="codex", local_credential=local_token, gateway_credential=lambda: self.native.access_token, optimizer=RelayOptimizer(preference_store=ContextPreferenceStore(self.root / "context", "fixture"), client="codex", gateway_compatible=False))
        thread = threading.Thread(target=relay.serve_forever, daemon=True)
        thread.start()
        try:
            for _ in range(2):
                connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
                connection.request("POST", "/v1/responses", body=json.dumps({"model": "safe-openai", "input": "Verify actual work binding", "max_output_tokens": 32}), headers={"Authorization": "Bearer " + local_token, "X-Hormuz-Work-Id": job["work_id"], "Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200, response.read())
                connection.close()
            status, _, captured = self.request("GET", "/v1/work/jobs/" + job["work_id"], headers=self.bearer())
            self.assertEqual(status, 200)
            self.assertEqual(captured["costs"]["attempts"], 2)
            self.assertEqual(captured["state"], "active")
        finally:
            relay.shutdown()
            thread.join(timeout=3)
            relay.server_close()

    def test_browser_session_and_csrf_change_real_state(self):
        self.login_console()
        status, _, page = self.request("GET", "/work", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, page)
        self.assertIn("Set the boundaries", page)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page)[1]
        body = {"repository": "example/repository", "title": "", "task_type": "ci-repair", "context_revision": "", "csrf_token": token}
        status, _, page = self.request("POST", "/v1/work/jobs", urlencode(body), {"Cookie": self.cookie, "Origin": self.gateway_url, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200, page)
        self.assertIn("Job created", page)
        status, _, state = self.request("GET", "/v1/work/state", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, state)
        self.assertEqual(len(state["works"]), 1)
        self.assertEqual(state["works"][0]["actor_id"], self.admin.membership_id)
        self.assertEqual(state["works"][0]["title"], None)
        self.assertEqual(self.request("POST", "/v1/work/jobs", {"repository": "example/bad", "csrf_token": "bad"}, {"Cookie": self.cookie, "Origin": self.gateway_url})[0], 403)
        self.assertEqual(self.request("POST", "/v1/work/jobs", {"repository": "example/bad", "csrf_token": token}, {"Cookie": self.cookie, "Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("POST", "/v1/work/jobs", {"repository": "example/bad", "csrf_token": token}, {"Cookie": self.cookie})[0], 403)
        self.assertEqual(len(self.gateway.work_runtime.list_work(self.gateway.session_broker.authenticate(self.native.access_token))), 1)

    def test_shared_policy_requires_explicit_admin_and_cas(self):
        values = {"scope_type": "workspace", "scope_id": "customer-a", "budget_microusd": 1_000_000, "objective": "speed", "expected_version": 0}
        self.assertEqual(self.request("POST", "/v1/work/policies", values, self.bearer())[0], 403)
        self.login_console()
        headers = {"Cookie": self.cookie, "Origin": self.gateway_url}
        values["csrf_token"] = self.csrf
        status, _, plan = self.request("POST", "/v1/work/policies", values, headers)
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan["objective"], "speed")
        self.assertEqual(plan["budget_microusd"], 1_000_000)
        self.assertEqual(self.request("POST", "/v1/work/policies", values, headers)[0], 409)
        self.assertEqual(self.request("POST", "/v1/work/policies", {**values, "scope_id": "customer-b", "expected_version": 0}, headers)[0], 400)
        self.gateway.config = replace(self.gateway.config, ai_work=replace(self.gateway.config.ai_work, administrator_actor_ids=(self.admin.membership_id,)))
        api_values = {key: value for key, value in values.items() if key != "csrf_token"}
        status, _, plan = self.request("POST", "/v1/work/policies", {**api_values, "expected_version": 1}, self.bearer())
        self.assertEqual(status, 200, plan)
        self.assertEqual(plan["version"], 2)

    def test_owned_job_actions_and_sourced_observations(self):
        job = self.create()
        path = "/v1/work/jobs/" + job["work_id"]
        for action, expected in (("pause", "paused"), ("resume", "active"), ("stop", "canceled")):
            status, _, updated = self.request("POST", path + "/actions", {"action": action}, self.bearer())
            self.assertEqual(status, 200, updated)
            self.assertEqual(updated["state"], expected)
        status, _, updated = self.request("POST", path + "/actions", {"action": "approve_budget", "budget_microusd": 2_000_000, "expected_version": 0}, self.bearer())
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["budget_microusd"], 2_000_000)
        observation = {"status": "completed", "source": "workflow", "reference": "ci/run-12"}
        status, _, updated = self.request("POST", path + "/observations", observation, self.bearer())
        self.assertEqual(status, 200, updated)
        self.assertEqual(updated["state"], "completed")
        self.assertEqual(updated["observations"][0]["source"], "workflow")
        self.assertEqual(self.request("POST", path + "/observations", {**observation, "source": "verified_github_webhook"}, self.bearer())[0], 400)
        self.assertEqual(self.request("POST", path + "/observations", {**observation, "status": "corrected"}, self.bearer())[0], 200)

    def test_confused_auth_duplicate_json_and_privilege_headers_rejected(self):
        self.login_console()
        self.assertEqual(self.request("GET", "/v1/work/state", headers={**self.bearer(), "Cookie": self.cookie})[0], 403)
        self.assertEqual(self.request("GET", "/v1/work/state", headers={**self.bearer(), "Origin": self.gateway_url})[0], 403)
        self.assertEqual(self.request("GET", "/v1/work/state", headers={**self.bearer(), "X-Hormuz-Role": "admin"})[0], 400)
        self.assertEqual(self.request("POST", "/v1/work/jobs", '{"repository":"example/a","repository":"example/b"}', {**self.bearer(), "Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.request("GET", "/v1/work/state?actor_id=other", headers=self.bearer())[0], 400)
        self.assertEqual(self.request("GET", "/v1/work/state", headers={**self.bearer(), "Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("GET", "/v1/work/state")[0], 401)
        self.assertEqual(self.request("GET", "/work")[0], 200)

    def test_browser_revocation_and_read_only_grant(self):
        self.login_console()
        self.sessions.grant(organization_id="customer-a", membership_id=self.admin.membership_id, role="report_viewer")
        self.assertEqual(self.request("GET", "/work", headers={"Cookie": self.cookie})[0], 401)
        self.login_console()
        status, _, state = self.request("GET", "/v1/work/state", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, state)
        self.assertFalse(state["principal"]["can_manage_plans"])
        self.assertEqual(self.request("POST", "/v1/work/jobs", {"repository": "example/repository", "csrf_token": self.csrf}, {"Cookie": self.cookie, "Origin": self.gateway_url})[0], 403)
        self.assertEqual(self.request("POST", "/v1/work/policies", {"scope_type": "repository", "scope_id": "example/repository", "budget_microusd": 0, "objective": "cost", "csrf_token": self.csrf}, {"Cookie": self.cookie, "Origin": self.gateway_url})[0], 403)

    def test_dashboard_escaping_headers_and_live_numbers(self):
        job = self.create()
        identity = self.gateway.session_broker.authenticate(self.native.access_token)
        runtime = self.gateway.work_runtime
        runtime.reserve(identity, job["work_id"], "request-1", "fixture-model", "openai", 2_000_000)
        runtime.settle(identity, "request-1", 1_250_000, latency_ms=1250)
        runtime.reserve(identity, job["work_id"], "request-2", "fixture-model", "openai", 3_000_000)
        runtime.settle(identity, "request-2", status="unknown")
        status, _, escaped = self.request("POST", "/v1/work/jobs", {"repository": "example/repository", "title": "<script>bad</script>"}, self.bearer())
        self.assertEqual(status, 201, escaped)
        self.login_console()
        status, headers, page = self.request("GET", "/work", headers={"Cookie": self.cookie})
        self.assertEqual(status, 200, page)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "strict-origin")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn(job["work_id"], page)
        self.assertNotIn(self.native.access_token, page)
        self.assertNotIn("<script", page)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", page)
        self.assertIn("$1.25", page)
        self.assertIn("$3.00", page)
        self.assertIn("1.25s", page)
        self.assertIn("request-1", page)
        self.assertIn("No completion observation", page)
        self.assertEqual(self.request("GET", "/work.css")[0], 200)

    def test_sdk_lifecycle_uses_authenticated_http(self):
        from hormuz.work_client import WorkClient
        client = WorkClient(self.gateway_url, self.native.access_token, allow_loopback_http=True)
        job = client.job(client.create_job("example/repository", task_type="ci-repair")["work_id"])
        self.assertEqual(job.state()["state"], "active")
        self.assertEqual(job.observe_check(0, reference="ci/run-1")["state"], "active")
        self.assertEqual(job.observe_check(1, reference="ci/run-2")["state"], "active")
        self.assertEqual(job.observe_check(0, reference="ci/run-3", completes_work=True)["state"], "completed")
        self.assertEqual(client.state()["totals"]["completed"], 1)

    def test_billing_portal_requires_authority_origin_and_csrf(self):
        billing = mock.Mock()
        billing.status.return_value = {"status": "active", "provider_fees": "separate", "paid_activation": "verified", "portal_available": True}
        billing.portal.return_value = "https://billing.stripe.com/p/session_fixture"
        self.gateway.work_billing = billing
        self.login_console()
        values = {"csrf_token": self.csrf}
        headers = {"Cookie": self.cookie, "Origin": self.gateway_url}
        self.assertEqual(self.request("POST", "/v1/work/billing/portal", {"csrf_token": "bad"}, headers)[0], 403)
        status, response_headers, _ = self.request("POST", "/v1/work/billing/portal", urlencode(values), {**headers, "Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 303)
        self.assertEqual(response_headers["Location"], billing.portal.return_value)
        billing.portal.assert_called_once_with("customer-a", self.gateway_url + "/work")
        self.assertEqual(self.request("POST", "/v1/work/billing/portal", {}, self.bearer())[0], 403)
        billing.portal.return_value = "https://attacker.example/"
        self.assertEqual(self.request("POST", "/v1/work/billing/portal", values, headers)[0], 503)


class WorkWorkspaceHTTPTests(SessionHTTPTestCase):
    workspace_origin = "https://workspace.example.com"

    def configure_gateway(self, config):
        issuer_name = next(iter(config.oidc_issuers))
        issuer = config.oidc_issuers[issuer_name]
        return replace(config, ai_work=AIWorkConfig(enabled=True, database_path=self.root / "work.sqlite3"), session_broker=replace(config.session_broker, public_base_url=self.workspace_origin, onboarding_enabled=True, console_enabled=True, workspace_enabled=True, workspace_signup_issuer=issuer_name), oidc_issuers={issuer_name: replace(issuer, login=replace(issuer.login, scopes=("openid", "email")))})

    def setUp(self):
        super().setUp()
        self.transport_url, self.gateway_url = self.gateway_url, self.workspace_origin
        self.config = replace(self.config, session_broker=replace(self.config.session_broker, public_base_url=self.gateway_url))
        self.gateway.config = self.config
        self.gateway.session_broker.config = self.config
        self.service = self.gateway.workspace
        self.sessions = self.service.sessions
        self.service.domains.provider = workspace_fixture.FakeDomains(self.service.domains)
        self.gateway.work_runtime = WorkRuntime(self.root / "work.sqlite3", self.config)

    def request(self, method, path, value=None, headers=None, *, origin=None):
        headers = dict(headers or {})
        if origin is None:
            headers.setdefault("Host", urlsplit(self.gateway_url).netloc)
            origin = self.transport_url
        return super().request(method, path, value, headers, origin=origin)

    post = workspace_fixture.WorkspaceHTTPTests.post
    begin = workspace_fixture.WorkspaceHTTPTests.begin
    authorization = workspace_fixture.WorkspaceHTTPTests.authorization
    login = workspace_fixture.WorkspaceHTTPTests.login
    activate_domain = workspace_fixture.WorkspaceHTTPTests.activate_domain
    custom_login = workspace_fixture.WorkspaceHTTPTests.custom_login

    def test_workspace_membership_link_ownership_and_enrollment_boundary(self):
        cookie, identity, workspace_path = self.login()
        status, _, workspace = self.request("GET", workspace_path, headers={"Cookie": cookie})
        self.assertEqual(status, 200, workspace)
        self.assertIn('href="/work"', workspace)
        status, _, state = self.request("GET", "/v1/work/state", headers={"Cookie": cookie})
        self.assertEqual(status, 200, state)
        self.assertFalse(state["connection"]["application_access_enabled"])
        csrf = state["principal"]["csrf_token"]
        status, _, job = self.request("POST", "/v1/work/jobs", {"repository": "customer/repository", "csrf_token": csrf}, {"Cookie": cookie, "Origin": self.gateway_url})
        self.assertEqual(status, 201, job)
        other_cookie, _, _ = self.login("other-subject", "other@example.test")
        self.assertEqual(self.request("GET", "/v1/work/jobs/" + job["work_id"], headers={"Cookie": other_cookie})[0], 404)
        self.sessions.logout(cookie.split("=", 1)[1], self.gateway_url)
        self.assertEqual(self.request("GET", "/v1/work/state", headers={"Cookie": cookie})[0], 401)

    def test_workspace_custom_host_requires_domain_bound_session(self):
        cookie, identity, _ = self.login()
        self.activate_domain(cookie, identity)
        self.assertEqual(self.request("GET", "/work", headers={"Cookie": cookie, "Host": "ai.customer.com"})[0], 401)
        token, handoff_cookie = self.custom_login("ai.customer.com")
        status, headers, _ = self.post("/v1/workspaces/auth/handoff", {"token": token}, cookie=handoff_cookie, host="ai.customer.com", origin=self.gateway_url)
        self.assertEqual(status, 303)
        custom_cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, state = self.request("GET", "/v1/work/state", headers={"Cookie": custom_cookie, "Host": "ai.customer.com"})
        self.assertEqual(status, 200, state)
        self.assertEqual(state["connection"]["endpoint"], "https://ai.customer.com")
        self.assertEqual(self.request("POST", "/v1/work/jobs", {"repository": "customer/repository", "csrf_token": state["principal"]["csrf_token"]}, {"Cookie": custom_cookie, "Host": "ai.customer.com", "Origin": self.gateway_url})[0], 403)
