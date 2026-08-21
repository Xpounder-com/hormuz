from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock
from contextlib import nullcontext
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from hormuz.config import GatewayConfig, Identity, ListenConfig
from hormuz.auth import AuthenticationError, Authenticator
from hormuz.cli import _doctor, main as cli_main
from hormuz.credential_store import CredentialStoreError, SecureCredentialStore
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.session_client import access_token, login, logout
from hormuz.store import SecurityStoreError


class FakeLoginIdPHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/.well-known/openid-configuration":
            value = {
                "issuer": self.server.issuer,  # type: ignore[attr-defined]
                "jwks_uri": self.server.issuer + "/jwks",  # type: ignore[attr-defined]
                "authorization_endpoint": self.server.issuer + "/authorize",  # type: ignore[attr-defined]
                "token_endpoint": self.server.issuer + "/token",  # type: ignore[attr-defined]
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            }
            for key in self.server.discovery_omissions:  # type: ignore[attr-defined]
                value.pop(key, None)
            value.update(self.server.discovery_overrides)  # type: ignore[attr-defined]
            self._json(200, value)
            return
        if parsed.path == "/jwks":
            self._json(200, {"keys": [self.server.public_jwk]})  # type: ignore[attr-defined]
            return
        if parsed.path != "/authorize":
            self.send_response(404)
            self.end_headers()
            return
        values = _single_values(parsed.query)
        required = {
            "response_type",
            "client_id",
            "redirect_uri",
            "scope",
            "state",
            "nonce",
            "code_challenge",
            "code_challenge_method",
        }
        if values is None or set(values) != required:
            self.send_response(400)
            self.end_headers()
            return
        if (
            values["response_type"] != "code"
            or values["client_id"] != "hormuz-login-client"
            or values["code_challenge_method"] != "S256"
            or "openid" not in values["scope"].split()
        ):
            self.send_response(400)
            self.end_headers()
            return
        code = "code_" + str(self.server.code_counter) + "_" + "c" * 24  # type: ignore[attr-defined]
        self.server.code_counter += 1  # type: ignore[attr-defined]
        self.server.codes[code] = values  # type: ignore[attr-defined]
        location = values["redirect_uri"] + "?" + urllib.parse.urlencode(
            {"code": code, "state": values["state"]}
        )
        if self.server.authorization_response_issuer is not None:  # type: ignore[attr-defined]
            location += "&" + urllib.parse.urlencode(
                {"iss": self.server.authorization_response_issuer}  # type: ignore[attr-defined]
            )
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/token":
            self.send_response(404)
            self.end_headers()
            return
        if self.server.token_unavailable:  # type: ignore[attr-defined]
            self._json(503, {"error": "temporarily_unavailable"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        fields = urllib.parse.parse_qs(self.rfile.read(length).decode("ascii"))
        values = {key: items[0] for key, items in fields.items() if len(items) == 1}
        authorization = self.headers.get("Authorization", "")
        expected_basic = "Basic " + base64.b64encode(
            b"hormuz-login-client:super-secret-client-value"
        ).decode("ascii")
        code = values.get("code", "")
        authorization_request = self.server.codes.pop(code, None)  # type: ignore[attr-defined]
        verifier = values.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        if (
            authorization != expected_basic
            or authorization_request is None
            or values.get("grant_type") != "authorization_code"
            or values.get("client_id") != "hormuz-login-client"
            or values.get("redirect_uri") != authorization_request["redirect_uri"]
            or challenge != authorization_request["code_challenge"]
        ):
            self._json(400, {"error": "invalid_grant"})
            return
        now = int(time.time())
        nonce = authorization_request["nonce"]
        if self.server.bad_nonce:  # type: ignore[attr-defined]
            nonce = "wrong_" + nonce
        claims = {
                "iss": self.server.issuer,  # type: ignore[attr-defined]
                "sub": "oidc-alice",
                "aud": "hormuz-login-client",
                "iat": now,
                "exp": now + 300,
                "nonce": nonce,
            }
        if self.server.multiple_audiences_without_azp:  # type: ignore[attr-defined]
            claims["aud"] = ["hormuz-login-client", "another-client"]
        id_token = jwt.encode(
            claims,
            self.server.private_key,  # type: ignore[attr-defined]
            algorithm="RS256",
            headers={"kid": "login-key"},
        )
        self._json(
            200,
            {
                "access_token": "provider-token-that-hormuz-must-not-retain",
                "token_type": "Bearer",
                "id_token": id_token,
            },
        )

    def _json(self, status: int, value: object) -> None:
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class SessionBrokerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        public_jwk.update({"kid": "login-key", "alg": "RS256", "use": "sig"})
        self.idp = ThreadingHTTPServer(("127.0.0.1", 0), FakeLoginIdPHandler)
        self.idp.issuer = f"http://127.0.0.1:{self.idp.server_address[1]}"  # type: ignore[attr-defined]
        self.idp.private_key = private_key  # type: ignore[attr-defined]
        self.idp.public_jwk = public_jwk  # type: ignore[attr-defined]
        self.idp.codes = {}  # type: ignore[attr-defined]
        self.idp.code_counter = 1  # type: ignore[attr-defined]
        self.idp.bad_nonce = False  # type: ignore[attr-defined]
        self.idp.token_unavailable = False  # type: ignore[attr-defined]
        self.idp.authorization_response_issuer = None  # type: ignore[attr-defined]
        self.idp.multiple_audiences_without_azp = False  # type: ignore[attr-defined]
        self.idp.discovery_omissions = set()  # type: ignore[attr-defined]
        self.idp.discovery_overrides = {}  # type: ignore[attr-defined]
        self.idp_thread = threading.Thread(target=self.idp.serve_forever, daemon=True)
        self.idp_thread.start()

        gateway_port = _unused_port()
        self.gateway_url = f"http://127.0.0.1:{gateway_port}"
        config_path = self.root / "hormuz.json"
        config_path.write_text(json.dumps(self._config(gateway_port)), encoding="utf-8")
        master_key = base64.urlsafe_b64encode(b"m" * 32).rstrip(b"=").decode("ascii")
        self.config = GatewayConfig.load(
            config_path,
            environ={
                "HORMUZ_SESSION_MASTER_KEY": master_key,
                "HORMUZ_OIDC_CLIENT_SECRET": "super-secret-client-value",
                "HORMUZ_ADMIN_TOKEN": "admin-token-" + "a" * 32,
                "HORMUZ_EMPLOYEE_TOKEN": "employee-token-" + "e" * 32,
                "HORMUZ_FINANCE_TOKEN": "finance-token-" + "f" * 32,
                "HORMUZ_MANAGER_TOKEN": "manager-token-" + "m" * 32,
                "HORMUZ_POLICY_ADMIN_TOKEN": "policy-admin-token-" + "p" * 32,
            },
        )
        self.gateway = GatewayServer(self.config)
        self.gateway_thread = serve_in_thread(self.gateway)

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.gateway_thread.join(timeout=5)
        self.idp.shutdown()
        self.idp.server_close()
        self.idp_thread.join(timeout=5)
        self.temporary.cleanup()

    def _config(self, gateway_port: int) -> dict[str, object]:
        return {
            "listen": {"host": "127.0.0.1", "port": gateway_port},
            "database": "./usage.sqlite3",
            "context_database": "./context.sqlite3",
            "upstreams": {
                "openai": {"base_url": "https://api.openai.invalid", "api_key_env": "OPENAI_API_KEY"},
                "anthropic": {"base_url": "https://api.anthropic.invalid", "api_key_env": "ANTHROPIC_API_KEY"},
            },
            "authentication": {
                "session_broker": {
                    "enabled": True,
                    "database": "./sessions.sqlite3",
                    "public_base_url": self.gateway_url,
                    "master_key_env": "HORMUZ_SESSION_MASTER_KEY",
                    "allow_insecure_http": True,
                    "access_ttl_seconds": 600,
                    "absolute_ttl_seconds": 43200,
                    "enrollment_ttl_seconds": 300,
                },
                "oidc": {
                    "issuers": [
                        {
                            "issuer": self.idp.issuer,  # type: ignore[attr-defined]
                            "audiences": ["hormuz-api"],
                            "allow_insecure_http": True,
                            "login": {
                                "client_id": "hormuz-login-client",
                                "client_secret_env": "HORMUZ_OIDC_CLIENT_SECRET",
                                "scopes": ["openid"],
                            },
                            "subjects": [
                                {
                                    "subject": "oidc-alice",
                                    "actor_id": "alice",
                                    "actor_name": "Alice",
                                    "team_id": "engineering",
                                    "team_name": "Engineering",
                                    "organization_id": "xpounder",
                                    "clearance": "confidential",
                                    "allowed_clients": ["codex", "claude-code"],
                                }
                            ],
                        }
                    ]
                },
            },
            "identities": [
                {
                    "token_env": "HORMUZ_ADMIN_TOKEN",
                    "actor_id": "security-admin",
                    "actor_name": "Security Admin",
                    "team_id": "security",
                    "team_name": "Security",
                    "organization_id": "xpounder",
                    "clearance": "restricted",
                    "allowed_clients": ["codex", "claude-code"],
                    "capabilities": ["session_admin", "usage_viewer"],
                },
                {
                    "token_env": "HORMUZ_EMPLOYEE_TOKEN",
                    "actor_id": "employee",
                    "actor_name": "Employee",
                    "team_id": "engineering",
                    "team_name": "Engineering",
                    "organization_id": "xpounder",
                    "clearance": "internal",
                    "allowed_clients": ["codex", "claude-code"],
                    "capabilities": ["usage_self_viewer"],
                },
                {
                    "token_env": "HORMUZ_MANAGER_TOKEN",
                    "actor_id": "engineering-manager",
                    "actor_name": "Engineering Manager",
                    "team_id": "engineering",
                    "team_name": "Engineering",
                    "organization_id": "xpounder",
                    "clearance": "internal",
                    "allowed_clients": ["codex", "claude-code"],
                    "capabilities": ["usage_team_viewer"],
                },
                {
                    "token_env": "HORMUZ_FINANCE_TOKEN",
                    "actor_id": "finance-analyst",
                    "actor_name": "Finance Analyst",
                    "team_id": "finance",
                    "team_name": "Finance",
                    "organization_id": "xpounder",
                    "clearance": "internal",
                    "allowed_clients": ["codex", "claude-code"],
                    "capabilities": ["usage_finance_viewer"],
                },
                {
                    "token_env": "HORMUZ_POLICY_ADMIN_TOKEN",
                    "actor_id": "policy-admin",
                    "actor_name": "Policy Admin",
                    "team_id": "security",
                    "team_name": "Security",
                    "organization_id": "xpounder",
                    "clearance": "restricted",
                    "allowed_clients": ["codex", "claude-code"],
                    "capabilities": ["policy_admin"],
                },
            ],
            "model_routes": {
                "gpt-test": {"protocol": "openai", "upstream_model": "gpt-test"},
                "claude-test": {"protocol": "anthropic", "upstream_model": "claude-test"},
            },
            "policies": {
                "organization": {
                    "allowed_clients": ["codex", "claude-code"],
                    "allowed_models": ["gpt-test", "claude-test"],
                    "max_output_tokens": 100,
                },
                "teams": {},
                "actors": {},
            },
        }

    def _post(self, path: str, value: object) -> tuple[int, dict[str, object]]:
        body = json.dumps(value).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request(
            "POST",
            path,
            body=body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        parsed = json.loads(response.read())
        connection.close()
        return response.status, parsed

    def _admin_request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        value: object | None = None,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(value).encode("utf-8") if value is not None else None
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if body is not None:
            headers.update(
                {"Content-Type": "application/json", "Content-Length": str(len(body))}
            )
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.config.listen.port, timeout=5
        )
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        parsed = json.loads(response.read())
        connection.close()
        return response.status, parsed

    def _login(self, *, client: str = "codex") -> dict[str, object]:
        enrollment_secret = "local_enrollment_" + "s" * 32
        status, enrollment = self._post(
            "/v1/auth/enrollments",
            {"client": client, "enrollment_secret": enrollment_secret},
        )
        self.assertEqual(status, 201)
        self._complete_browser_flow(str(enrollment["login_url"]))

        status, pair = self._post(
            f"/v1/auth/enrollments/{enrollment['enrollment_id']}/redeem",
            {"enrollment_secret": enrollment_secret},
        )
        self.assertEqual(status, 200)
        return pair

    def _complete_browser_flow(self, login_url: str) -> bool:
        callback_url, cookie = self._prepare_browser_callback(login_url)
        callback = urllib.parse.urlsplit(callback_url)
        gateway_connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        gateway_connection.request(
            "GET",
            callback.path + "?" + callback.query,
            headers={"Cookie": cookie},
        )
        callback_response = gateway_connection.getresponse()
        callback_body = callback_response.read()
        gateway_connection.close()
        self.assertEqual(callback_response.status, 200, callback_body)
        self.assertNotIn(b"hox_", callback_body)
        return True

    def _prepare_browser_callback(self, login_url: str) -> tuple[str, str]:
        parsed_login = urllib.parse.urlsplit(login_url)
        login_path = parsed_login.path + "?" + parsed_login.query

        gateway_connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        gateway_connection.request("GET", login_path)
        begin_response = gateway_connection.getresponse()
        begin_response.read()
        authorization_url = begin_response.getheader("Location")
        cookie = begin_response.getheader("Set-Cookie").split(";", 1)[0]
        gateway_connection.close()
        self.assertEqual(begin_response.status, 302)

        idp_url = urllib.parse.urlsplit(authorization_url)
        idp_connection = http.client.HTTPConnection("127.0.0.1", self.idp.server_address[1], timeout=5)
        idp_connection.request("GET", idp_url.path + "?" + idp_url.query)
        idp_response = idp_connection.getresponse()
        idp_response.read()
        callback_url = idp_response.getheader("Location")
        idp_connection.close()
        self.assertEqual(idp_response.status, 302)
        return callback_url, cookie

    def test_browser_pkce_login_refresh_replay_and_client_binding(self) -> None:
        first = self._login(client="codex")
        access = str(first["access_token"])
        refresh = str(first["refresh_token"])
        self.assertTrue(access.startswith("hox_a_"))
        self.assertTrue(refresh.startswith("hox_r_"))
        session_database = (self.root / "sessions.sqlite3").read_bytes()
        for forbidden in (
            access,
            refresh,
            "provider-token-that-hormuz-must-not-retain",
            "super-secret-client-value",
        ):
            self.assertNotIn(forbidden.encode("utf-8"), session_database)

        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request(
            "GET",
            "/v1/gateway/whoami",
            headers={"Authorization": "Bearer " + access},
        )
        response = connection.getresponse()
        whoami = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(whoami["actor_id"], "alice")
        self.assertEqual(whoami["allowed_clients"], ["codex"])
        self.assertEqual(whoami["authentication_source"], f"session:{self.idp.issuer}")  # type: ignore[attr-defined]

        status, second = self._post("/v1/auth/refresh", {"refresh_token": refresh})
        self.assertEqual(status, 200)
        self.assertNotEqual(second["access_token"], access)
        status, replay = self._post("/v1/auth/refresh", {"refresh_token": refresh})
        self.assertEqual(status, 401)
        self.assertEqual(replay["error"]["code"], "invalid_session")  # type: ignore[index]

        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request(
            "GET",
            "/v1/gateway/whoami",
            headers={"Authorization": "Bearer " + str(second["access_token"])},
        )
        revoked_response = connection.getresponse()
        revoked_response.read()
        connection.close()
        self.assertEqual(revoked_response.status, 401)

        claude_bound = self._login(client="claude-code")
        body = json.dumps({"model": "gpt-test", "input": "hello"}).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request(
            "POST",
            "/v1/responses",
            body=body,
            headers={
                "Authorization": "Bearer " + str(claude_bound["access_token"]),
                "Content-Type": "application/json",
            },
        )
        client_response = connection.getresponse()
        client_response.read()
        connection.close()
        self.assertEqual(client_response.status, 403)

    def test_doctor_preflight_rejects_unsupported_login_capabilities(self) -> None:
        cases = (
            ("response_types_supported", ["id_token"], "oidc_authorization_code_unsupported"),
            ("grant_types_supported", ["implicit"], "oidc_authorization_code_unsupported"),
            ("code_challenge_methods_supported", ["plain"], "oidc_pkce_s256_unsupported"),
            (
                "id_token_signing_alg_values_supported",
                ["ES256"],
                "oidc_id_token_signing_unsupported",
            ),
            (
                "token_endpoint_auth_methods_supported",
                ["client_secret_post"],
                "oidc_token_endpoint_auth_unsupported",
            ),
        )
        for key, value, expected_code in cases:
            with self.subTest(key=key):
                self.idp.discovery_overrides = {key: value}  # type: ignore[attr-defined]
                authenticator = Authenticator(self.config)
                with self.assertRaises(AuthenticationError) as raised:
                    authenticator.validate_metadata()
                self.assertEqual(raised.exception.code, expected_code)
        self.idp.discovery_overrides = {}  # type: ignore[attr-defined]
        self.idp.discovery_omissions = {"token_endpoint_auth_methods_supported"}  # type: ignore[attr-defined]
        self.assertEqual(len(Authenticator(self.config).validate_metadata()), 1)
        output = io.StringIO()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "OPENAI_API_KEY": "synthetic-openai-provider-key",
                    "ANTHROPIC_API_KEY": "synthetic-anthropic-provider-key",
                },
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(_doctor(self.config), 0)
        self.assertIn("OIDC browser-login preflight: 1 issuer(s)", output.getvalue())

    def test_bad_nonce_fails_without_issuing_hormuz_credentials(self) -> None:
        self.idp.bad_nonce = True  # type: ignore[attr-defined]
        enrollment_secret = "local_enrollment_" + "s" * 32
        status, enrollment = self._post(
            "/v1/auth/enrollments",
            {"client": "codex", "enrollment_secret": enrollment_secret},
        )
        self.assertEqual(status, 201)
        login_url = urllib.parse.urlsplit(str(enrollment["login_url"]))
        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request("GET", login_url.path + "?" + login_url.query)
        begin = connection.getresponse()
        begin.read()
        authorization_url = urllib.parse.urlsplit(begin.getheader("Location"))
        cookie = begin.getheader("Set-Cookie").split(";", 1)[0]
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.idp.server_address[1], timeout=5)
        connection.request("GET", authorization_url.path + "?" + authorization_url.query)
        authorized = connection.getresponse()
        authorized.read()
        callback_url = urllib.parse.urlsplit(authorized.getheader("Location"))
        connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request(
            "GET",
            callback_url.path + "?" + callback_url.query,
            headers={"Cookie": cookie},
        )
        callback = connection.getresponse()
        callback.read()
        connection.close()
        self.assertEqual(callback.status, 400)
        status, _value = self._post(
            f"/v1/auth/enrollments/{enrollment['enrollment_id']}/redeem",
            {"enrollment_secret": enrollment_secret},
        )
        self.assertEqual(status, 409)

    def test_issuer_mixup_and_token_endpoint_outage_fail_closed(self) -> None:
        cases = ("issuer_mixup", "token_outage", "missing_azp")
        for case in cases:
            with self.subTest(case=case):
                self.idp.authorization_response_issuer = (  # type: ignore[attr-defined]
                    "https://attacker.invalid" if case == "issuer_mixup" else None
                )
                self.idp.token_unavailable = case == "token_outage"  # type: ignore[attr-defined]
                self.idp.multiple_audiences_without_azp = case == "missing_azp"  # type: ignore[attr-defined]
                enrollment_secret = "failed_enrollment_" + case + "_" + "s" * 32
                status, enrollment = self._post(
                    "/v1/auth/enrollments",
                    {"client": "codex", "enrollment_secret": enrollment_secret},
                )
                self.assertEqual(status, 201)
                callback_url, cookie = self._prepare_browser_callback(str(enrollment["login_url"]))
                callback = urllib.parse.urlsplit(callback_url)
                connection = http.client.HTTPConnection(
                    "127.0.0.1",
                    self.config.listen.port,
                    timeout=5,
                )
                connection.request(
                    "GET",
                    callback.path + "?" + callback.query,
                    headers={"Cookie": cookie},
                )
                response = connection.getresponse()
                response_body = response.read()
                connection.close()
                self.assertEqual(response.status, 400, response_body)
                self.assertNotIn(b"hox_", response_body)
                status, _value = self._post(
                    f"/v1/auth/enrollments/{enrollment['enrollment_id']}/redeem",
                    {"enrollment_secret": enrollment_secret},
                )
                self.assertEqual(status, 409)
                self.idp.authorization_response_issuer = None  # type: ignore[attr-defined]
                self.idp.token_unavailable = False  # type: ignore[attr-defined]
                self.idp.multiple_audiences_without_azp = False  # type: ignore[attr-defined]

    def test_subject_cannot_enroll_for_an_unauthorized_client(self) -> None:
        identity_key = (self.idp.issuer, "oidc-alice")  # type: ignore[attr-defined]
        original = self.config.identities_by_subject[identity_key]
        self.config.identities_by_subject[identity_key] = replace(
            original,
            allowed_clients=("codex",),
        )
        try:
            enrollment_secret = "disallowed_client_" + "s" * 32
            status, enrollment = self._post(
                "/v1/auth/enrollments",
                {"client": "claude-code", "enrollment_secret": enrollment_secret},
            )
            self.assertEqual(status, 201)
            callback_url, cookie = self._prepare_browser_callback(str(enrollment["login_url"]))
            callback = urllib.parse.urlsplit(callback_url)
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                self.config.listen.port,
                timeout=5,
            )
            connection.request(
                "GET",
                callback.path + "?" + callback.query,
                headers={"Cookie": cookie},
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 400)
            status, _value = self._post(
                f"/v1/auth/enrollments/{enrollment['enrollment_id']}/redeem",
                {"enrollment_secret": enrollment_secret},
            )
            self.assertEqual(status, 409)
        finally:
            self.config.identities_by_subject[identity_key] = original

    def test_session_is_revoked_when_identity_binding_changes(self) -> None:
        pair = self._login(client="codex")
        identity_key = (self.idp.issuer, "oidc-alice")  # type: ignore[attr-defined]
        original = self.config.identities_by_subject[identity_key]
        self.config.identities_by_subject[identity_key] = replace(
            original,
            team_id="platform",
            team_name="Platform",
        )
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.config.listen.port, timeout=5
            )
            connection.request(
                "GET",
                "/v1/gateway/whoami",
                headers={"Authorization": "Bearer " + str(pair["access_token"])},
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 401)
        finally:
            self.config.identities_by_subject[identity_key] = original

        with sqlite3.connect(self.root / "sessions.sqlite3") as connection:
            row = connection.execute(
                """
                SELECT revoked_at FROM human_sessions
                WHERE actor_id = 'alice' ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])

    def test_directory_lifecycle_revokes_the_affected_actor_session_family(self) -> None:
        pair = self._login(client="codex")
        broker = self.gateway.session_broker
        self.assertIsNotNone(broker)
        assert broker is not None
        administrator = replace(
            self.config.identities_by_token["admin-token-" + "a" * 32],
            capabilities=("identity_admin", "session_admin"),
        )
        self.assertEqual(
            broker.revoke_for_directory(
                administrator=administrator,
                actor_ids=("alice", "alice"),
            ),
            1,
        )

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.config.listen.port, timeout=5
        )
        connection.request(
            "GET",
            "/v1/gateway/whoami",
            headers={"Authorization": "Bearer " + str(pair["access_token"])},
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 401)

    def test_cli_login_refresh_and_logout_use_secure_store_profile(self) -> None:
        backend = _MemoryKeyring()
        store = SecureCredentialStore(backend, trust_injected_backend=True)
        access_expiry = login(
            gateway=self.gateway_url,
            profile="codex-work",
            client="codex",
            issuer=None,
            no_open=False,
            allow_insecure_http=True,
            wait_seconds=30,
            store=store,
            browser_open=self._complete_browser_flow,
        )
        self.assertIn("+00:00", access_expiry)
        saved = store.get("codex-work")
        self.assertIsNotNone(saved)
        first_access = access_token(
            gateway=self.gateway_url,
            profile="codex-work",
            allow_insecure_http=True,
            store=store,
            lock_factory=lambda _profile: nullcontext(),
        )
        self.assertEqual(first_access, saved.access_token)  # type: ignore[union-attr]

        store.set(
            "codex-work",
            replace(
                saved,  # type: ignore[arg-type]
                access_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
        )
        refreshed_access = access_token(
            gateway=self.gateway_url,
            profile="codex-work",
            allow_insecure_http=True,
            store=store,
            lock_factory=lambda _profile: nullcontext(),
        )
        self.assertNotEqual(refreshed_access, first_access)
        self.assertTrue(
            logout(
                gateway=self.gateway_url,
                profile="codex-work",
                allow_insecure_http=True,
                store=store,
                lock_factory=lambda _profile: nullcontext(),
            )
        )
        self.assertIsNone(store.get("codex-work"))

        connection = http.client.HTTPConnection("127.0.0.1", self.config.listen.port, timeout=5)
        connection.request(
            "GET",
            "/v1/gateway/whoami",
            headers={"Authorization": "Bearer " + refreshed_access},
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 401)

    def test_cli_revokes_redeemed_session_when_secure_store_write_fails(self) -> None:
        store = SecureCredentialStore(_FailingSetKeyring(), trust_injected_backend=True)
        with self.assertRaisesRegex(CredentialStoreError, "secure_store_unavailable"):
            login(
                gateway=self.gateway_url,
                profile="failed-save",
                client="codex",
                issuer=None,
                no_open=False,
                allow_insecure_http=True,
                wait_seconds=30,
                store=store,
                browser_open=self._complete_browser_flow,
            )
        with sqlite3.connect(self.root / "sessions.sqlite3") as connection:
            row = connection.execute(
                "SELECT revoked_at FROM human_sessions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNotNone(row[0])

    def test_session_admin_lists_and_revokes_only_with_explicit_capability(self) -> None:
        pair = self._login(client="codex")
        second_pair = self._login(client="claude-code")
        admin_token = "admin-token-" + "a" * 32
        employee_token = "employee-token-" + "e" * 32

        status, forbidden = self._admin_request(
            "GET",
            "/v1/admin/sessions",
            token=employee_token,
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"]["code"], "session_admin_capability_required")

        status, listing = self._admin_request(
            "GET",
            "/v1/admin/sessions?actor_id=alice&limit=1",
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(listing["schema_version"], 1)
        self.assertIsInstance(listing["next_cursor"], str)
        self.assertEqual(len(listing["sessions"]), 1)
        session = listing["sessions"][0]
        self.assertEqual(session["actor_id"], "alice")
        self.assertEqual(session["organization_id"], "xpounder")
        self.assertNotIn(str(pair["access_token"]), repr(listing))
        self.assertNotIn(str(pair["refresh_token"]), repr(listing))
        self.assertNotIn(str(second_pair["access_token"]), repr(listing))

        status, final_page = self._admin_request(
            "GET",
            "/v1/admin/sessions?actor_id=alice&limit=1&cursor="
            + urllib.parse.quote(str(listing["next_cursor"]), safe=""),
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertIsNone(final_page["next_cursor"])
        self.assertEqual(len(final_page["sessions"]), 1)
        self.assertNotEqual(
            session["session_id"],
            final_page["sessions"][0]["session_id"],
        )

        status, invalid_cursor = self._admin_request(
            "GET",
            "/v1/admin/sessions?cursor=not-a-valid-cursor",
            token=admin_token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            invalid_cursor["error"]["code"],
            "invalid_session_list_request",
        )

        status, revoked = self._admin_request(
            "POST",
            "/v1/admin/session-revocations",
            token=admin_token,
            value={
                "scope": "actor",
                "target": "alice",
                "reason_code": "access_change",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(revoked["schema_version"], 1)
        self.assertEqual(revoked["scope"], "actor")
        self.assertEqual(revoked["revoked_sessions"], 2)

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.config.listen.port, timeout=5
        )
        connection.request(
            "GET",
            "/v1/gateway/whoami",
            headers={"Authorization": "Bearer " + str(pair["access_token"])},
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 401)

        status, invalid = self._admin_request(
            "POST",
            "/v1/admin/session-revocations",
            token=admin_token,
            value={"scope": "organization", "target": "not-allowed", "reason_code": "administrative"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid["error"]["code"], "invalid_session_revocation")

        status, forbidden_events = self._admin_request(
            "GET",
            "/v1/admin/session-events",
            token=employee_token,
        )
        self.assertEqual(status, 403)
        self.assertEqual(
            forbidden_events["error"]["code"],
            "session_admin_capability_required",
        )

        status, events = self._admin_request(
            "GET",
            "/v1/admin/session-events?event_type=admin_revocation&limit=1",
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(events["schema_version"], 1)
        self.assertEqual(len(events["events"]), 1)
        self.assertIsInstance(events["next_cursor"], str)
        event = events["events"][0]
        self.assertEqual(event["organization_id"], "xpounder")
        self.assertEqual(event["target_actor_id"], "alice")
        self.assertEqual(event["decision_actor_id"], "security-admin")
        self.assertEqual(event["reason_code"], "access_change")
        self.assertNotIn(str(pair["access_token"]), repr(events))
        self.assertNotIn(str(second_pair["refresh_token"]), repr(events))

        status, final_events = self._admin_request(
            "GET",
            "/v1/admin/session-events?event_type=admin_revocation&limit=1&cursor="
            + urllib.parse.quote(str(events["next_cursor"]), safe=""),
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertIsNone(final_events["next_cursor"])
        self.assertEqual(len(final_events["events"]), 1)
        self.assertNotEqual(event["event_id"], final_events["events"][0]["event_id"])

        status, future_events = self._admin_request(
            "GET",
            "/v1/admin/session-events?since=2999-01-01T00%3A00%3A00Z",
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(future_events["events"], [])
        self.assertIsNone(future_events["next_cursor"])

        status, invalid_event = self._admin_request(
            "GET",
            "/v1/admin/session-events?event_type=provider_request",
            token=admin_token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            invalid_event["error"]["code"],
            "invalid_session_event_request",
        )

        for invalid_timestamp in (
            "2026-08-15T12%3A00%3A00",
            "0001-01-01T00%3A00%3A00%2B14%3A00",
        ):
            status, invalid_since = self._admin_request(
                "GET",
                "/v1/admin/session-events?since=" + invalid_timestamp,
                token=admin_token,
            )
            self.assertEqual(status, 400)
            self.assertEqual(
                invalid_since["error"]["code"],
                "invalid_session_event_request",
            )

    def test_usage_admin_is_tenant_scoped_paginated_and_audited(self) -> None:
        employee = self.config.identities_by_actor["employee"]
        admin = self.config.identities_by_actor["security-admin"]
        outsider = Identity(
            token_env="OUTSIDER_TOKEN",
            token="outsider-token-" + "o" * 32,
            actor_id=employee.actor_id,
            actor_name="Other Employee",
            team_id=employee.team_id,
            team_name=employee.team_name,
            organization_id="other-company",
        )
        for identity, model, cost in (
            (employee, "gpt-test", 1_000),
            (admin, "claude-test", 2_000),
            (outsider, "gpt-test", 9_000),
        ):
            self.gateway.store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model=model,
                resolved_alias=model,
                upstream_model=model,
                policy_action="allowed",
                status="succeeded",
                input_tokens=10,
                output_tokens=2,
                billable_tokens=12,
                cost_microusd=cost,
                cost_basis="estimated",
                gateway_latency_milliseconds=20 if identity is employee else 40,
                policy_latency_milliseconds=2,
                provider_latency_milliseconds=15,
            )

        employee_token = "employee-token-" + "e" * 32
        status, self_report = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=person",
            token=employee_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            self_report["schema_version"],
            4,
        )
        self.assertEqual(self_report["access"], {"scope": "self"})
        self.assertEqual(
            self_report["filters"],
            {"actor_id": "employee", "team_id": None},
        )
        self.assertEqual([row["scope_id"] for row in self_report["rows"]], ["employee"])
        self.assertNotIn("Security Admin", repr(self_report))
        self.assertNotIn("Other Employee", repr(self_report))

        status, forbidden = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=organization",
            token="policy-admin-token-" + "p" * 32,
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"]["code"], "usage_viewer_capability_required")

        admin_token = "admin-token-" + "a" * 32
        status, first = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=person&limit=1",
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(first["organization_id"], "xpounder")
        self.assertEqual(first["coverage"]["scope"], "gateway_captured_requests_only")
        self.assertEqual(len(first["rows"]), 1)
        self.assertIsInstance(first["next_cursor"], str)
        self.assertNotIn("Other Employee", repr(first))
        self.assertNotIn("9000", repr(first))
        self.assertEqual(first["schema_version"], 2)
        self.assertNotIn("latency", first["rows"][0])

        status, second = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=person&limit=1&cursor="
            + urllib.parse.quote(str(first["next_cursor"]), safe=""),
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(second["rows"]), 1)
        self.assertIsNone(second["next_cursor"])
        self.assertNotEqual(
            first["rows"][0]["scope_id"],
            second["rows"][0]["scope_id"],
        )

        status, self_usage = self._admin_request(
            "GET",
            "/v1/gateway/usage",
            token=employee_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self_usage["requests"], 1)
        self.assertEqual(self_usage["cost_usd"], 0.001)

        status, invalid = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=provider&cursor="
            + urllib.parse.quote(str(first["next_cursor"]), safe=""),
            token=admin_token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid["error"]["code"], "invalid_usage_report_request")

        status, latency_first = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=person&limit=1&include=latency",
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(latency_first["schema_version"], 3)
        self.assertEqual(
            latency_first["coverage"]["latency_scope"],
            "accounted_gateway_requests_only",
        )
        self.assertIn("latency", latency_first["rows"][0])
        self.assertIsInstance(latency_first["next_cursor"], str)

        status, cursor_mismatch = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=person&limit=1&cursor="
            + urllib.parse.quote(str(latency_first["next_cursor"]), safe=""),
            token=admin_token,
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            cursor_mismatch["error"]["code"],
            "invalid_usage_report_request",
        )

        status, latency_second = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=person&limit=1&include=latency&cursor="
            + urllib.parse.quote(str(latency_first["next_cursor"]), safe=""),
            token=admin_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(latency_second["schema_version"], 3)
        self.assertIsNone(latency_second["next_cursor"])

        audit = self.gateway.store.audit_events(
            since="2000-01-01T00:00:00+00:00",
            kind="security",
        )
        reads = [
            event
            for event in audit
            if event["event_type"] == "security.admin.usage_read"
        ]
        self.assertEqual(len(reads), 5)
        self.assertTrue(
            all(event["organization_id"] == "xpounder" for event in reads)
        )
        self.assertEqual(
            {event["decision_actor_id"] for event in reads},
            {"employee", "security-admin"},
        )

    def test_usage_report_scopes_apply_server_side_aggregate_boundaries(self) -> None:
        employee = self.config.identities_by_actor["employee"]
        administrator = self.config.identities_by_actor["security-admin"]
        colleague = Identity(
            token_env="COLLEAGUE_TOKEN",
            token="colleague-token-" + "c" * 32,
            actor_id="colleague",
            actor_name="Colleague",
            team_id="engineering",
            team_name="Engineering",
            organization_id="xpounder",
        )
        for identity, model, cost in (
            (employee, "gpt-test", 1_000),
            (colleague, "gpt-test", 500),
            (administrator, "claude-test", 2_000),
        ):
            self.gateway.store.record(
                identity=identity,
                client="codex",
                protocol="openai",
                requested_model=model,
                resolved_alias=model,
                upstream_model=model,
                policy_action="allowed",
                status="succeeded",
                input_tokens=10,
                output_tokens=2,
                billable_tokens=12,
                cost_microusd=cost,
                cost_basis="estimated",
                gateway_latency_milliseconds=20,
                policy_latency_milliseconds=2,
                provider_latency_milliseconds=15,
            )

        manager_token = "manager-token-" + "m" * 32
        for forbidden_path in (
            "/v1/admin/usage?group_by=person",
            "/v1/admin/usage?group_by=model&actor_id=employee",
            "/v1/admin/usage?group_by=model&team_id=security",
        ):
            status, response = self._admin_request(
                "GET",
                forbidden_path,
                token=manager_token,
            )
            self.assertEqual(status, 403)
            self.assertEqual(
                response["error"]["code"],
                "usage_report_scope_forbidden",
            )

        status, team_model_report = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=model",
            token=manager_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(team_model_report["schema_version"], 4)
        self.assertEqual(team_model_report["access"], {"scope": "team"})
        self.assertEqual(
            team_model_report["filters"],
            {"actor_id": None, "team_id": "engineering"},
        )
        self.assertEqual(
            [(row["scope_id"], row["cost_microusd"]) for row in team_model_report["rows"]],
            [("gpt-test", 1_500)],
        )

        finance_token = "finance-token-" + "f" * 32
        for forbidden_path in (
            "/v1/admin/usage?group_by=person",
            "/v1/admin/usage?group_by=team",
            "/v1/admin/usage?group_by=model&team_id=engineering",
        ):
            status, response = self._admin_request(
                "GET",
                forbidden_path,
                token=finance_token,
            )
            self.assertEqual(status, 403)
            self.assertEqual(
                response["error"]["code"],
                "usage_report_scope_forbidden",
            )

        status, finance_model_report = self._admin_request(
            "GET",
            "/v1/admin/usage?group_by=model",
            token=finance_token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(finance_model_report["schema_version"], 4)
        self.assertEqual(finance_model_report["access"], {"scope": "finance"})
        self.assertEqual(
            finance_model_report["filters"],
            {"actor_id": None, "team_id": None},
        )
        self.assertEqual(
            [(row["scope_id"], row["cost_microusd"]) for row in finance_model_report["rows"]],
            [("claude-test", 2_000), ("gpt-test", 1_500)],
        )

    def test_usage_coverage_is_scoped_and_never_claims_an_organization_total(self) -> None:
        employee = self.config.identities_by_actor["employee"]
        administrator = self.config.identities_by_actor["security-admin"]
        ci_workload = Identity(
            token_env="",
            token="",
            actor_id="ci-build",
            actor_name="CI build",
            team_id="engineering",
            team_name="Engineering",
            organization_id="xpounder",
            identity_type="ci",
        )
        for identity, client in (
            (employee, "codex"),
            (ci_workload, "claude-code"),
            (administrator, "codex"),
        ):
            self.gateway.store.record(
                identity=identity,
                client=client,
                protocol="openai",
                requested_model="gpt-test",
                resolved_alias="gpt-test",
                upstream_model="gpt-test",
                policy_action="allowed",
                status="succeeded",
            )

        status, self_coverage = self._admin_request(
            "GET",
            "/v1/admin/usage/coverage",
            token="employee-token-" + "e" * 32,
        )
        self.assertEqual(status, 200)
        self.assertEqual(self_coverage["access"], {"scope": "self"})
        self.assertEqual(self_coverage["coverage"]["accounted_gateway_requests"], 1)
        self.assertEqual(
            self_coverage["coverage"]["identity_type_requests"],
            {"human": 1, "service_account": 0, "ci": 0, "connector": 0},
        )

        status, team_coverage = self._admin_request(
            "GET",
            "/v1/admin/usage/coverage",
            token="manager-token-" + "m" * 32,
        )
        self.assertEqual(status, 200)
        self.assertEqual(team_coverage["access"], {"scope": "team"})
        self.assertEqual(team_coverage["coverage"]["accounted_gateway_requests"], 2)
        self.assertEqual(
            team_coverage["coverage"]["identity_type_requests"],
            {"human": 1, "service_account": 0, "ci": 1, "connector": 0},
        )
        self.assertNotIn("Security Admin", repr(team_coverage))

        status, finance_coverage = self._admin_request(
            "GET",
            "/v1/admin/usage/coverage",
            token="finance-token-" + "f" * 32,
        )
        self.assertEqual(status, 200)
        self.assertEqual(finance_coverage["access"], {"scope": "finance"})
        coverage = finance_coverage["coverage"]
        self.assertEqual(coverage["accounted_gateway_requests"], 3)
        self.assertEqual(coverage["identity_bound_gateway_requests"], 3)
        self.assertEqual(coverage["unattributed_accounted_gateway_requests"], 0)
        self.assertFalse(coverage["outside_gateway_traffic_observable"])
        self.assertFalse(coverage["organization_total"])
        self.assertEqual(
            coverage["provider_invoice_reconciliation"],
            "separate_billing_reconciliation_required",
        )
        self.assertNotIn("Employee", repr(finance_coverage))

        status, forbidden = self._admin_request(
            "GET",
            "/v1/admin/usage/coverage",
            token="policy-admin-token-" + "p" * 32,
        )
        self.assertEqual(status, 403)
        self.assertEqual(forbidden["error"]["code"], "usage_viewer_capability_required")

        status, invalid = self._admin_request(
            "GET",
            "/v1/admin/usage/coverage?unexpected=value",
            token="admin-token-" + "a" * 32,
        )
        self.assertEqual(status, 400)
        self.assertEqual(invalid["error"]["code"], "invalid_usage_coverage_request")

    def test_usage_admin_cli_uses_the_authenticated_gateway_contract(self) -> None:
        identity = self.config.identities_by_actor["employee"]
        self.gateway.store.record(
            identity=identity,
            client="claude-code",
            protocol="anthropic",
            requested_model="claude-test",
            resolved_alias="claude-test",
            upstream_model="claude-test",
            policy_action="allowed",
            status="succeeded",
            input_tokens=20,
            output_tokens=4,
            billable_tokens=24,
            cost_microusd=1_500,
            cost_basis="estimated",
            gateway_latency_milliseconds=18,
            policy_latency_milliseconds=2,
            provider_latency_milliseconds=14,
        )
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_ADMIN_TOKEN": "admin-token-" + "a" * 32},
        ), redirect_stdout(output):
            result = cli_main(
                [
                    "usage",
                    "report",
                    "--gateway",
                    self.gateway_url,
                    "--credential-env",
                    "HORMUZ_ADMIN_TOKEN",
                    "--group-by",
                    "team",
                    "--include-latency",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["organization_id"], "xpounder")
        self.assertEqual(report["rows"][0]["scope_id"], "engineering")
        self.assertEqual(report["rows"][0]["billable_tokens"], 24)
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["rows"][0]["latency"]["gateway"]["count"], 1)

    def test_usage_coverage_cli_uses_the_authenticated_gateway_contract(self) -> None:
        identity = self.config.identities_by_actor["employee"]
        self.gateway.store.record(
            identity=identity,
            client="codex",
            protocol="openai",
            requested_model="gpt-test",
            resolved_alias="gpt-test",
            upstream_model="gpt-test",
            policy_action="allowed",
            status="succeeded",
        )
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_ADMIN_TOKEN": "admin-token-" + "a" * 32},
        ), redirect_stdout(output):
            result = cli_main(
                [
                    "usage",
                    "coverage",
                    "--gateway",
                    self.gateway_url,
                    "--credential-env",
                    "HORMUZ_ADMIN_TOKEN",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        coverage = json.loads(output.getvalue())
        self.assertEqual(coverage["schema_version"], 1)
        self.assertEqual(coverage["access"], {"scope": "organization"})
        self.assertEqual(coverage["coverage"]["accounted_gateway_requests"], 1)
        self.assertFalse(coverage["coverage"]["organization_total"])

    def test_usage_admin_cli_accepts_a_constrained_gateway_contract(self) -> None:
        identity = self.config.identities_by_actor["employee"]
        self.gateway.store.record(
            identity=identity,
            client="claude-code",
            protocol="anthropic",
            requested_model="claude-test",
            resolved_alias="claude-test",
            upstream_model="claude-test",
            policy_action="allowed",
            status="succeeded",
            input_tokens=20,
            output_tokens=4,
            billable_tokens=24,
            cost_microusd=1_500,
            cost_basis="estimated",
            gateway_latency_milliseconds=18,
            policy_latency_milliseconds=2,
            provider_latency_milliseconds=14,
        )
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_MANAGER_TOKEN": "manager-token-" + "m" * 32},
        ), redirect_stdout(output):
            result = cli_main(
                [
                    "usage",
                    "report",
                    "--gateway",
                    self.gateway_url,
                    "--credential-env",
                    "HORMUZ_MANAGER_TOKEN",
                    "--group-by",
                    "model",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["schema_version"], 4)
        self.assertEqual(report["access"], {"scope": "team"})
        self.assertEqual(
            report["filters"],
            {"actor_id": None, "team_id": "engineering"},
        )
        self.assertEqual(report["rows"][0]["scope_id"], "claude-test")
        self.assertEqual(report["rows"][0]["billable_tokens"], 24)

    def test_usage_admin_returns_no_rows_when_read_audit_cannot_commit(self) -> None:
        admin_token = "admin-token-" + "a" * 32
        with mock.patch.object(
            self.gateway.store,
            "record_admin_usage_read",
            side_effect=SecurityStoreError("usage_admin_audit_unavailable"),
        ):
            status, response = self._admin_request(
                "GET",
                "/v1/admin/usage?group_by=organization",
                token=admin_token,
            )
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "usage_admin_unavailable")
        self.assertNotIn("rows", response)

        with mock.patch.object(
            self.gateway.store,
            "record_admin_usage_read",
            side_effect=SecurityStoreError("usage_admin_audit_unavailable"),
        ):
            status, response = self._admin_request(
                "GET",
                "/v1/admin/usage/coverage",
                token=admin_token,
            )
        self.assertEqual(status, 503)
        self.assertEqual(response["error"]["code"], "usage_admin_unavailable")
        self.assertNotIn("coverage", response)

    def test_session_admin_cli_uses_the_authenticated_gateway_contract(self) -> None:
        pair = self._login(client="claude-code")
        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_ADMIN_TOKEN": "admin-token-" + "a" * 32},
        ), redirect_stdout(output):
            result = cli_main(
                [
                    "sessions",
                    "list",
                    "--gateway",
                    self.gateway_url,
                    "--credential-env",
                    "HORMUZ_ADMIN_TOKEN",
                    "--actor",
                    "alice",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        listing = json.loads(output.getvalue())
        self.assertEqual(listing["sessions"][0]["client_name"], "claude-code")
        self.assertNotIn(str(pair["access_token"]), output.getvalue())

        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_ADMIN_TOKEN": "admin-token-" + "a" * 32},
        ), redirect_stdout(output):
            result = cli_main(
                [
                    "sessions",
                    "revoke",
                    "--gateway",
                    self.gateway_url,
                    "--credential-env",
                    "HORMUZ_ADMIN_TOKEN",
                    "--scope",
                    "session",
                    "--target",
                    listing["sessions"][0]["session_id"],
                    "--reason",
                    "security_incident",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["revoked_sessions"], 1)

        output = io.StringIO()
        with mock.patch.dict(
            os.environ,
            {"HORMUZ_ADMIN_TOKEN": "admin-token-" + "a" * 32},
        ), redirect_stdout(output):
            result = cli_main(
                [
                    "sessions",
                    "events",
                    "--gateway",
                    self.gateway_url,
                    "--credential-env",
                    "HORMUZ_ADMIN_TOKEN",
                    "--event-type",
                    "admin_revocation",
                    "--allow-insecure-http",
                ]
            )
        self.assertEqual(result, 0)
        events = json.loads(output.getvalue())
        self.assertEqual(events["events"][0]["reason_code"], "security_incident")
        self.assertNotIn(str(pair["refresh_token"]), output.getvalue())


def _single_values(query: str) -> dict[str, str] | None:
    values = urllib.parse.parse_qs(query, keep_blank_values=True)
    if any(len(items) != 1 for items in values.values()):
        return None
    return {key: items[0] for key, items in values.items()}


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class _FailingSetKeyring(_MemoryKeyring):
    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError("simulated keychain refusal")


if __name__ == "__main__":
    unittest.main()
