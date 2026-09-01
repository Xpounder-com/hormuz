"""Provider-free HTTP identity/model fixtures; never contact an external service."""

from __future__ import annotations

import base64
import hashlib
import html
import http.client
import json
import re
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from hormuz.config import GatewayConfig
from hormuz.server import GatewayServer, serve_in_thread


CLIENT_SECRET = "local-fixture-client-secret-only"
PROVIDER_KEY = "local-fixture-provider-key-only"


class LocalIdentityProvider(BaseHTTPRequestHandler):
    def do_GET(self):
        state = self.server
        parsed = urlsplit(self.path)
        if parsed.path == "/.well-known/openid-configuration":
            document = {
                "issuer": state.origin, "jwks_uri": state.origin + "/jwks",
                "authorization_endpoint": state.origin + "/authorize", "token_endpoint": state.origin + "/token",
                "userinfo_endpoint": state.origin + "/userinfo",
                "response_types_supported": ["code"], "response_modes_supported": ["form_post"],
                "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"],
                "id_token_signing_alg_values_supported": ["RS256"], "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            }
            document.update(state.metadata_overrides)
            self.reply(200, document)
        elif parsed.path == "/jwks":
            self.reply(200, {"keys": [state.jwk]})
        elif parsed.path == "/userinfo":
            state.userinfo_requests += 1
            authorization = self.headers.get("Authorization", "")
            token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
            subject = state.valid_access_tokens.get(token)
            if subject is None:
                self.reply(401, {"error": "invalid_token"})
                return
            if state.userinfo_unavailable:
                self.reply(503, {"error": "unavailable"})
                return
            claims = {"sub": subject, **state.claims_overrides}
            if state.userinfo_claims_overrides is not None:
                claims.update(state.userinfo_claims_overrides)
            self.reply(200, claims)
        elif parsed.path == "/authorize":
            params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            if params.get("response_mode") != "form_post" or params.get("code_challenge_method") != "S256":
                self.reply(400, {})
                return
            code = "fixture-authorization-code-" + str(len(state.codes))
            state.codes[code] = params
            # Simulated browser reads these fields and submits a form POST.
            self.reply(200, {"code": code, "state": params["state"], "iss": state.origin})
        else:
            self.reply(404, {})

    def do_POST(self):
        state = self.server
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path in {"/v1/responses", "/v1/messages"}:
            state.model_requests += 1
            self.reply(200, {"id": "local-response", "object": "response", "model": "fixture-model", "output": [], "usage": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}})
            return
        if self.path != "/token":
            self.reply(404, {})
            return
        if state.token_unavailable:
            self.reply(503, {"error": "unavailable"})
            return
        params = {key: values[0] for key, values in parse_qs(body.decode()).items()}
        authorization = self.headers.get("Authorization")
        # Strict providers reject body credentials combined with HTTP Basic,
        # including a duplicated client_id with no body client_secret.
        if authorization is not None and {"client_id", "client_secret"}.intersection(params):
            self.reply(401, {"error": "invalid_request"})
            return
        flow = state.codes.pop(params.get("code", ""), None)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(params.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
        expected_auth = "Basic " + base64.b64encode(("fixture-login:" + CLIENT_SECRET).encode()).decode()
        methods = state.metadata_overrides.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
        authenticated = (
            "client_secret_basic" in methods and authorization == expected_auth
            or "client_secret_post" in methods and authorization is None
            and params.get("client_id") == "fixture-login" and params.get("client_secret") == CLIENT_SECRET
        )
        if flow is None or not authenticated or challenge != flow["code_challenge"] or params.get("redirect_uri") != flow["redirect_uri"] or params.get("grant_type") != "authorization_code":
            self.reply(400, {"error": "invalid_grant"})
            return
        now = int(time.time())
        claims = {"iss": state.origin, "sub": state.subject, "aud": "fixture-login", "iat": now, "exp": now + 300, "nonce": flow["nonce"]}
        claims.update(state.claims_overrides)
        for key in state.omit_claims:
            claims.pop(key, None)
        token = jwt.encode(claims, state.private_key, algorithm="RS256", headers={"kid": "fixture-key"})
        state.last_id_token = token
        access_token = "idp-token-must-not-be-stored"
        state.valid_access_tokens[access_token] = state.subject
        self.reply(200, {"id_token": token, "access_token": access_token})

    def reply(self, status, value):
        encoded = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


def session_config(root: Path, issuer: str, gateway: str) -> dict:
    return {
        "listen": {"host": "127.0.0.1", "port": 8787}, "database": str(root / "usage.sqlite3"),
        "upstreams": {protocol: {"base_url": issuer, "api_key_env": "TEST_PROVIDER_KEY"} for protocol in ("openai", "anthropic")},
        "authentication": {
            "session_broker": {"enabled": True, "public_base_url": gateway, "database": str(root / "sessions.sqlite3"), "allow_insecure_http": True},
            "oidc": {"issuers": [{
                "issuer": issuer, "audiences": ["hormuz-api"], "allow_insecure_http": True,
                "login": {"client_id": "fixture-login", "client_secret_env": "TEST_OIDC_SECRET"},
                "subjects": [
                    {"subject": "alice-subject", "actor_id": "alice", "actor_name": "Alice", "team_id": "engineering", "team_name": "Engineering", "organization_id": "org-a", "allowed_clients": ["codex", "claude-code"]},
                    {"subject": "bob-subject", "actor_id": "bob", "actor_name": "Bob", "team_id": "engineering-b", "team_name": "Engineering B", "organization_id": "org-b", "allowed_clients": ["codex"]},
                ],
            }]},
        },
        "model_routes": {"safe-openai": {"protocol": "openai", "upstream_model": "fixture-model"}, "safe-claude": {"protocol": "anthropic", "upstream_model": "fixture-model"}},
        "policies": {"organization": {"allowed_clients": ["codex", "claude-code"], "allowed_models": ["safe-openai", "safe-claude"], "max_output_tokens": 32}},
    }


def fixture_environment() -> dict[str, str]:
    return {"HORMUZ_SESSION_MASTER_KEY": base64.b64encode(b"m" * 32).decode(), "TEST_OIDC_SECRET": CLIENT_SECRET, "TEST_PROVIDER_KEY": PROVIDER_KEY}


class SessionHTTPTestCase(unittest.TestCase):
    def configure_gateway(self, config):
        return config

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.idp = ThreadingHTTPServer(("127.0.0.1", 0), LocalIdentityProvider)
        self.idp.origin = f"http://127.0.0.1:{self.idp.server_port}"
        self.idp.private_key = key
        self.idp.jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
        self.idp.jwk.update({"kid": "fixture-key", "alg": "RS256", "use": "sig"})
        self.idp.codes, self.idp.metadata_overrides, self.idp.claims_overrides = {}, {}, {}
        self.idp.omit_claims = set()
        self.idp.userinfo_claims_overrides = None
        self.idp.userinfo_unavailable = False
        self.idp.userinfo_requests = 0
        self.idp.valid_access_tokens = {}
        self.idp.subject = "alice-subject"
        self.idp.token_unavailable = False
        self.idp.model_requests = 0
        self.idp.last_id_token = ""
        self.idp_thread = threading.Thread(target=self.idp.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.idp_thread.start()
        self.addCleanup(self._close, self.idp, self.idp_thread)
        self.config_path = self.root / "hormuz.json"
        self.config_path.write_text(json.dumps(session_config(self.root, self.idp.origin, "http://127.0.0.1:8787")))
        self.config = GatewayConfig.load(self.config_path, environ=fixture_environment())
        self.config = self.configure_gateway(self.config)
        from dataclasses import replace
        self.config = replace(self.config, listen=replace(self.config.listen, port=0))
        self.gateway = GatewayServer(self.config, environ=fixture_environment())
        # Select an ephemeral listener without a port-reservation race.
        self.gateway_url = f"http://127.0.0.1:{self.gateway.server_port}"
        self.config = replace(self.config, session_broker=replace(self.config.session_broker, public_base_url=self.gateway_url))
        self.gateway.config = self.config
        self.gateway.session_broker.config = self.config
        self.gateway.session_broker.callback_url = self.gateway_url + "/v1/auth/callback"
        self.gateway_thread = serve_in_thread(self.gateway)
        self.addCleanup(self._close, self.gateway, self.gateway_thread)

    @staticmethod
    def _close(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    def request(self, method, path, value=None, headers=None, *, origin=None):
        headers = dict(headers or {})
        body = value
        if isinstance(value, dict):
            body = json.dumps(value)
            headers.setdefault("Content-Type", "application/json")
        parsed = urlsplit(origin or self.gateway_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=10)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            raw = response.read().decode()
            result = json.loads(raw) if response.getheader("Content-Type", "").startswith("application/json") else raw
            return response.status, dict(response.getheaders()), result
        finally:
            connection.close()

    def enroll(self, *, organization="org-a", client="codex"):
        secret = "enrollment-secret-" + "s" * 40
        status, headers, response = self.request("POST", "/v1/auth/enrollments", {"organization_id": organization, "client": client, "enrollment_secret": secret})
        self.assertEqual(status, 201, response)
        self.assertEqual(headers["Cache-Control"], "no-store")
        return response, secret

    def begin_browser(self, enrollment):
        url = urlsplit(enrollment["login_url"])
        status, headers, page = self.request("GET", url.path + "?" + url.query)
        self.assertEqual(status, 200, page)
        self.assertIn("Continue only if", page)
        authorization_url = html.unescape(re.search(r'href="([^"]+)"', page)[1])
        parsed = urlsplit(authorization_url)
        status, _, values = self.request("GET", parsed.path + "?" + parsed.query, origin=self.idp.origin)
        self.assertEqual(status, 200, values)
        return values, headers["Set-Cookie"].split(";", 1)[0]

    def callback(self, values, cookie):
        return self.request("POST", "/v1/auth/callback", urlencode(values), {"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie})

    def browser_login(self, *, organization="org-a", client="codex"):
        enrollment, secret = self.enroll(organization=organization, client=client)
        values, cookie = self.begin_browser(enrollment)
        status, _, response = self.callback(values, cookie)
        self.assertEqual(status, 200, response)
        status, _, pair = self.request("POST", "/v1/auth/enrollments/" + enrollment["enrollment_id"] + "/redeem", {"enrollment_secret": secret})
        self.assertEqual(status, 200, pair)
        return pair
