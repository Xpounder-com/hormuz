from __future__ import annotations

import base64
import hashlib
import http.client
import json
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.parse
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from hormuz.config import GatewayConfig, ListenConfig
from hormuz.credential_store import CredentialStoreError, SecureCredentialStore
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.session_client import access_token, login, logout


class FakeLoginIdPHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/.well-known/openid-configuration":
            self._json(
                200,
                {
                    "issuer": self.server.issuer,  # type: ignore[attr-defined]
                    "jwks_uri": self.server.issuer + "/jwks",  # type: ignore[attr-defined]
                    "authorization_endpoint": self.server.issuer + "/authorize",  # type: ignore[attr-defined]
                    "token_endpoint": self.server.issuer + "/token",  # type: ignore[attr-defined]
                },
            )
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
