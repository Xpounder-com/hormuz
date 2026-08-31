from __future__ import annotations

import io
import sqlite3
from contextlib import redirect_stdout
from dataclasses import replace
from unittest import mock

from hormuz.attribution_admission import RESULT_HEADER
from hormuz.attribution_config import AttributionBinding, AttributionConfig, WorkScopeRef
from hormuz.auth import AuthenticationError
from hormuz.cli import main
from hormuz.credential_store import SecureCredentialStore, StoredSession
from hormuz.portfolio_config import PortfolioConfig, PortfolioRoleBinding
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_service import PortfolioService
from hormuz.portfolio_wire import ATTRIBUTIONS, SCOPES, canonical
from hormuz.session_client import access_token, login, logout
from hormuz.store import UsageStore
from tests._session_fixtures import CLIENT_SECRET, PROVIDER_KEY, SessionHTTPTestCase
from tests.test_credential_store import MemoryBackend


class SessionPortfolioIntegrationTests(SessionHTTPTestCase):
    ADMIN = "synthetic-session-registry-admin-token"

    def configure_gateway(self, config):
        alice = config.identities_by_subject[(self.idp.origin, "alice-subject")]
        config = replace(
            config,
            identities_by_token={self.ADMIN: replace(
                alice, token=self.ADMIN, token_env="UNUSED_TEST_ADMIN", authentication_source="static",
            )},
            portfolio_control=PortfolioConfig(
                (PortfolioRoleBinding("org-a", "alice", ("portfolio_admin",)),), (),
            ),
        )
        UsageStore(config.database_path)
        service = PortfolioService(config, create_portfolio_repository(config))
        self.scope = service.dispatch(
            self.ADMIN, "POST", SCOPES,
            body=canonical({
                "schema_id": "hormuz.work-scope-create-request", "schema_version": 1,
                "kind": "use_case", "parent_work_scope_id": None,
                "owner_team_id": "engineering", "display_name": "Synthetic session use case",
            }).encode(),
            idempotency_key="session-scope",
        )[1]
        return replace(config, attribution_control=AttributionConfig((AttributionBinding(
            "org-a", "alice", "codex", (WorkScopeRef(self.scope["work_scope_id"], 1),), (), False,
        ),)))

    def test_customer_session_does_not_inherit_registry_role(self):
        # Both extensions start together. Even the same actor's explicit
        # registry role cannot turn an inference session into an admin token.
        pair = self.browser_login()
        customer = {"Authorization": "Bearer " + pair["access_token"]}
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=customer)[0], 200)
        self.assertEqual(self.request("GET", SCOPES, headers=customer)[0], 401)
        self.assertEqual(self.request("GET", ATTRIBUTIONS, headers=customer)[0], 401)
        admin = {"Authorization": "Bearer " + self.ADMIN}
        status, _, registry = self.request("GET", SCOPES, headers=admin)
        self.assertEqual(status, 200, registry)
        self.assertEqual(self.idp.model_requests, 0)

    def test_attribution_uses_bound_session_identity_and_preserves_admin_boundary(self):
        pair = self.browser_login()
        scope_header = f'v1;work_scope_id={self.scope["work_scope_id"]};version=1'
        customer = {
            "Authorization": "Bearer " + pair["access_token"],
            "X-Hormuz-Work-Scope": scope_header,
        }
        request = {"model": "safe-openai", "input": "local fixture only", "max_output_tokens": 16, "stream": False}
        status, headers, response = self.request("POST", "/v1/responses", request, customer)
        self.assertEqual(status, 200, response)
        self.assertEqual(headers[RESULT_HEADER], "v1;status=attributed;reason=bound")
        self.assertEqual(self.idp.model_requests, 1)

        # Neither an ungranted version nor a valid session in another tenant
        # can send work to the provider under Alice's scope.
        denied = dict(customer, **{"X-Hormuz-Work-Scope": scope_header.replace("version=1", "version=2")})
        self.assertEqual(self.request("POST", "/v1/responses", request, denied)[0], 403)
        self.idp.subject = "bob-subject"
        bob = self.browser_login(organization="org-b")
        denied = dict(customer, Authorization="Bearer " + bob["access_token"])
        self.assertEqual(self.request("POST", "/v1/responses", request, denied)[0], 403)
        self.assertEqual(self.idp.model_requests, 1)
        self.assertEqual(self.request("GET", ATTRIBUTIONS, headers=customer)[0], 401)

        status, _, page = self.request("GET", ATTRIBUTIONS, headers={"Authorization": "Bearer " + self.ADMIN})
        self.assertEqual(status, 200, page)
        self.assertEqual(len(page["items"]), 1)
        event = page["items"][0]
        self.assertEqual(event["work_scope"], {"work_scope_id": self.scope["work_scope_id"], "version": 1})
        principal = self.gateway.portfolio_service.authenticate(self.ADMIN)
        facts = self.gateway.attribution_repository.attempt_facts(principal, event["request_attempt_id"])
        self.assertEqual((facts["organization_id"], facts["actor_id"], facts["client"]), ("org-a", "alice", "codex"))


class SessionBrokerTests(SessionHTTPTestCase):
    def test_login_with_client_secret_post_uses_body_authentication(self):
        issuer = self.config.oidc_issuers[self.idp.origin]
        self.config.oidc_issuers[self.idp.origin] = replace(
            issuer, login=replace(issuer.login, token_endpoint_auth_method="client_secret_post"),
        )
        self.idp.metadata_overrides["token_endpoint_auth_methods_supported"] = ["client_secret_post"]
        pair = self.browser_login()
        status, _, identity = self.request(
            "GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair["access_token"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(identity["actor_id"], "alice")

    def test_login_governed_request_usage_refresh_and_logout(self):
        pair = self.browser_login()
        headers = {"Authorization": "Bearer " + pair["access_token"]}
        status, _, identity = self.request("GET", "/v1/gateway/whoami", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual((identity["actor_id"], identity["organization_id"], identity["allowed_clients"]), ("alice", "org-a", ["codex"]))
        status, _, response = self.request("POST", "/v1/responses", {"model": "safe-openai", "input": "local fixture only", "max_output_tokens": 16, "stream": False}, headers)
        self.assertEqual(status, 200, response)
        self.assertEqual(self.idp.model_requests, 1)
        status, _, usage = self.request("GET", "/v1/gateway/usage", headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual((usage["requests"], usage["input_tokens"], usage["output_tokens"]), (1, 11, 7))
        status, _, second = self.request("POST", "/v1/auth/refresh", {"refresh_token": pair["refresh_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(second["session_expires_at"], pair["session_expires_at"])
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=headers)[0], 401)
        self.assertEqual(self.request("POST", "/v1/auth/logout", {"credential": second["refresh_token"]})[0], 200)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + second["access_token"]})[0], 401)

    def test_tenant_selection_and_client_binding_cannot_be_overridden(self):
        wrong, _ = self.enroll(organization="org-b")
        values, cookie = self.begin_browser(wrong)
        self.assertEqual(self.callback(values, cookie)[0], 400)
        pair = self.browser_login()
        status, _, response = self.request("POST", "/v1/messages", {"model": "safe-claude", "messages": [], "max_tokens": 16}, {"X-Api-Key": pair["access_token"], "X-Hormuz-Client": "codex"})
        self.assertEqual(status, 403, response)
        self.assertEqual(self.idp.model_requests, 0)
        # Usage parameters supplied by a caller cannot change the authenticated scope.
        self.idp.subject = "bob-subject"
        other = self.browser_login(organization="org-b")
        status, _, usage = self.request("GET", "/v1/gateway/usage?organization_id=org-a&actor_id=alice", headers={"Authorization": "Bearer " + other["access_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(usage["requests"], 0)

    def test_session_cannot_be_used_as_a_policy_control_credential(self):
        pair = self.browser_login()
        with self.assertRaises(AuthenticationError):
            self.gateway.authenticator.authenticate_control(pair["access_token"])

    def test_refresh_replay_revokes_current_family_over_http(self):
        first = self.browser_login()
        status, _, second = self.request("POST", "/v1/auth/refresh", {"refresh_token": first["refresh_token"]})
        self.assertEqual(status, 200)
        status, _, error = self.request("POST", "/v1/auth/refresh", {"refresh_token": first["refresh_token"]})
        self.assertEqual(status, 401)
        self.assertEqual(error["error"]["code"], "unauthorized")
        self.assertIn("refresh_replay_detected", error["error"]["message"])
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + second["access_token"]})[0], 401)

    def test_mapping_removal_rejects_refresh_without_issuing_usable_credentials(self):
        pair = self.browser_login()
        del self.config.identities_by_subject[(self.idp.origin, "alice-subject")]
        status, _, error = self.request("POST", "/v1/auth/refresh", {"refresh_token": pair["refresh_token"]})
        self.assertEqual(status, 401, error)
        self.assertNotIn("access_token", error)

    def test_mapping_changes_revoke_instead_of_moving_session_to_other_tenant(self):
        pair = self.browser_login()
        key = (self.idp.origin, "alice-subject")
        self.config.identities_by_subject[key] = replace(self.config.identities_by_subject[key], organization_id="org-b")
        status, _, _ = self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair["access_token"]})
        self.assertEqual(status, 401)

    def test_state_cookie_and_callback_are_single_use(self):
        enrollment, _ = self.enroll()
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, "hormuz_login_local=" + "x" * 43)[0], 400)
        self.assertEqual(self.callback(values, cookie)[0], 200)
        self.assertEqual(self.callback(values, cookie)[0], 400)

    def test_id_token_validation_rejects_bad_claims(self):
        cases = [
            {"nonce": "incorrect-nonce"}, {"iss": "https://wrong-issuer.invalid"},
            {"aud": "hormuz-api"}, {"exp": 1}, {"sub": "unmapped-subject"},
            {"aud": ["fixture-login", "other" ]}, {"azp": "other-client"},
            {"nonce": "non-ascii-é"}, {"iat": 99999999999},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.idp.claims_overrides = overrides
                enrollment, secret = self.enroll()
                values, cookie = self.begin_browser(enrollment)
                self.assertEqual(self.callback(values, cookie)[0], 400)
                self.assertEqual(self.request("POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem", {"enrollment_secret": secret})[0], 409)

    def test_idp_outage_blocks_new_login_but_does_not_extend_existing_session(self):
        pair = self.browser_login()
        self.idp.token_unavailable = True
        enrollment, _ = self.enroll()
        values, cookie = self.begin_browser(enrollment)
        self.assertEqual(self.callback(values, cookie)[0], 503)
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair["access_token"]})[0], 200)
        status, _, updated = self.request("POST", "/v1/auth/refresh", {"refresh_token": pair["refresh_token"]})
        self.assertEqual(status, 200)
        self.assertEqual(updated["session_expires_at"], pair["session_expires_at"])

    def test_discovery_capability_failure_stops_before_browser_redirect(self):
        self.idp.metadata_overrides["code_challenge_methods_supported"] = ["plain"]
        enrollment, _ = self.enroll()
        parsed = enrollment["login_url"].split(self.gateway_url, 1)[1]
        status, _, error = self.request("GET", parsed)
        self.assertEqual(status, 400)
        self.assertEqual(error["error"]["code"], "invalid_request")
        self.assertIn("oidc_pkce_s256_unsupported", error["error"]["message"])

    def test_boundary_rejects_query_credentials_duplicate_fields_and_browser_json(self):
        for path, body, headers in [
            ("/v1/auth/refresh?refresh_token=never-log-this", {"refresh_token": "x" * 43}, {}),
            ("/v1/auth/refresh", '{"refresh_token":"first","refresh_token":"second"}', {"Content-Type": "application/json"}),
            ("/v1/auth/enrollments", {"client": "codex", "enrollment_secret": "s" * 43}, {"Origin": "https://untrusted.invalid"}),
            ("/v1/auth/refresh", {"refresh_token": "x" * 43, "organization_id": "forged"}, {}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path, body, headers)[0], 400)

    def test_untrusted_host_oversized_body_and_capacity_limit_are_bounded(self):
        self.assertEqual(self.request("POST", "/v1/auth/enrollments", {"client": "codex", "enrollment_secret": "s" * 43}, {"Host": "untrusted.invalid"})[0], 400)
        self.assertEqual(self.request("POST", "/v1/auth/refresh", {"refresh_token": "s" * 20000})[0], 400)
        with mock.patch.object(self.gateway.session_request_limit, "allow", return_value=False):
            self.assertEqual(self.request("POST", "/v1/auth/refresh", {"refresh_token": "s" * 43})[0], 429)

    def test_session_secrets_do_not_appear_in_logs_or_usage_database(self):
        with self.assertLogs("hormuz", level="DEBUG") as captured:
            pair = self.browser_login()
            self.request("GET", "/v1/auth/callback?code=never-log-this-code&state=never-log-state")
        output = "\n".join(captured.output)
        session_bytes = b"".join(p.read_bytes() for p in self.root.glob("sessions.sqlite3*"))
        usage_bytes = b"".join(p.read_bytes() for p in self.root.glob("usage.sqlite3*"))
        for secret in (pair["access_token"], pair["refresh_token"], self.idp.last_id_token, CLIENT_SECRET, PROVIDER_KEY, "idp-token-must-not-be-stored", "never-log-this-code", "never-log-state"):
            self.assertNotIn(secret, output)
            self.assertNotIn(secret.encode(), session_bytes)
            self.assertNotIn(secret.encode(), usage_bytes)

    def test_unavailable_session_storage_fails_closed_without_recreation(self):
        pair = self.browser_login()
        path = self.config.session_broker.database_path
        path.rename(path.with_suffix(".offline"))
        status, _, error = self.request("GET", "/v1/gateway/whoami", headers={"Authorization": "Bearer " + pair["access_token"]})
        self.assertEqual(status, 503, error)
        self.assertFalse(path.exists())

    def test_native_helper_login_refresh_and_logout_need_no_server_config(self):
        backend = MemoryBackend()
        store = SecureCredentialStore(backend, trust_injected_backend=True)

        def browser(url):
            enrollment = {"login_url": url}
            values, cookie = self.begin_browser(enrollment)
            self.assertEqual(self.callback(values, cookie)[0], 200)
            return True

        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.root)}):
            login(gateway=self.gateway_url, profile="client-test", client="codex", issuer=None, organization="org-a", no_open=False, allow_insecure_http=True, wait_seconds=5, store=store, browser_open=browser)
            saved = store.get("client-test")
            self.assertIsNotNone(saved)
            self.assertNotIn(saved.refresh_token, repr(saved))
            fresh = access_token(gateway=self.gateway_url, profile="client-test", allow_insecure_http=True, force_refresh=True, store=store)
            self.assertNotEqual(fresh, saved.access_token)
            self.assertTrue(logout(gateway=self.gateway_url, profile="client-test", allow_insecure_http=True, store=store))
            self.assertIsNone(store.get("client-test"))

    def test_secure_store_failure_revokes_newly_issued_session(self):
        pair = self.browser_login()
        backend = MemoryBackend()
        store = SecureCredentialStore(backend, trust_injected_backend=True)
        value = dict(pair, version=1, gateway=self.gateway_url, client="codex")
        session = StoredSession.from_dict(value)
        store.set("client-test", session)
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.root)}), mock.patch.object(backend, "set_password", side_effect=OSError("secure store locked")):
            from hormuz.credential_store import CredentialStoreError
            with self.assertRaises(CredentialStoreError):
                access_token(gateway=self.gateway_url, profile="client-test", allow_insecure_http=True, force_refresh=True, store=store)
        with sqlite3.connect(self.config.session_broker.database_path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM human_sessions WHERE revoked_at IS NULL").fetchone()[0], 0)

    def test_session_client_config_does_not_load_local_server_secrets(self):
        output = io.StringIO()
        with mock.patch("hormuz.cli.GatewayConfig.load", side_effect=AssertionError("server config must not be opened")), redirect_stdout(output):
            result = main(["client", "config", "codex", "--auth-mode", "session", "--url", self.gateway_url, "--allow-insecure-http", "--model", "safe-openai"])
        self.assertEqual(result, 0)
        self.assertIn('["auth", "session",', output.getvalue())
        self.assertNotIn("refresh_token", output.getvalue())
