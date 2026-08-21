from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from hormuz.auth import AuthenticationError, Authenticator
from hormuz.config import ConfigError, GatewayConfig, ListenConfig
from hormuz.server import GatewayServer, serve_in_thread


class FakeOIDCHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.server.request_count += 1  # type: ignore[attr-defined]
        if self.path == "/.well-known/openid-configuration":
            value = {
                "issuer": self.server.issuer,  # type: ignore[attr-defined]
                "jwks_uri": f"{self.server.issuer}/jwks",  # type: ignore[attr-defined]
            }
        elif self.path == "/jwks":
            value = {"keys": list(self.server.keys.values())}  # type: ignore[attr-defined]
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(value).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class OIDCAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.issuer_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOIDCHandler)
        issuer_port = self.issuer_server.server_address[1]
        self.issuer = f"http://127.0.0.1:{issuer_port}"
        self.issuer_server.issuer = self.issuer  # type: ignore[attr-defined]
        self.issuer_server.keys = {}  # type: ignore[attr-defined]
        self.issuer_server.request_count = 0  # type: ignore[attr-defined]
        self.issuer_thread = threading.Thread(target=self.issuer_server.serve_forever, daemon=True)
        self.issuer_thread.start()
        self.private_keys: dict[str, rsa.RSAPrivateKey] = {}
        self._add_key("key-1")
        self.config_path = self.root / "hormuz.json"
        self.config_path.write_text(json.dumps(self._config_value()), encoding="utf-8")
        self.config = GatewayConfig.load(self.config_path)

    def tearDown(self) -> None:
        self.issuer_server.shutdown()
        self.issuer_server.server_close()
        self.issuer_thread.join(timeout=5)
        self.temporary.cleanup()

    def _add_key(self, key_id: str) -> None:
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_keys[key_id] = private_key
        public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        public_jwk.update({"kid": key_id, "alg": "RS256", "use": "sig"})
        self.issuer_server.keys[key_id] = public_jwk  # type: ignore[attr-defined]

    def _token(
        self,
        *,
        key_id: str = "key-1",
        subject: str = "oidc-alice",
        audience: str = "hormuz-api",
        expires_at: int | None = None,
    ) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "iss": self.issuer,
                "sub": subject,
                "aud": audience,
                "iat": now,
                "exp": expires_at if expires_at is not None else now + 300,
            },
            self.private_keys[key_id],
            algorithm="RS256",
            headers={"kid": key_id},
        )

    def _config_value(self) -> dict[str, object]:
        return {
            "listen": {"host": "127.0.0.1", "port": 8787},
            "database": "./usage.sqlite3",
            "upstreams": {
                "openai": {"base_url": "https://api.openai.invalid", "api_key_env": "OPENAI_API_KEY"},
                "anthropic": {
                    "base_url": "https://api.anthropic.invalid",
                    "api_key_env": "ANTHROPIC_API_KEY",
                },
            },
            "authentication": {
                "oidc": {
                    "issuers": [
                        {
                            "issuer": self.issuer,
                            "audiences": ["hormuz-api"],
                            "algorithms": ["RS256"],
                            "clock_skew_seconds": 0,
                            "allow_insecure_http": True,
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
                }
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

    @staticmethod
    def _gateway_request(
        gateway: GatewayServer,
        *,
        method: str,
        path: str,
        token: str,
        value: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(value).encode("utf-8") if value is not None else None
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                }
            )
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            gateway.server_address[1],
            timeout=5,
        )
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_valid_token_uses_discovery_and_maps_subject(self) -> None:
        authenticator = Authenticator(self.config)
        identity = authenticator.authenticate(self._token())
        self.assertEqual(identity.actor_id, "alice")
        self.assertEqual(identity.organization_id, "xpounder")
        self.assertEqual(identity.clearance, "confidential")
        self.assertEqual(identity.authentication_source, f"oidc:{self.issuer}")

        authenticator.authenticate(self._token())
        self.assertEqual(self.issuer_server.request_count, 2)  # type: ignore[attr-defined]

    def test_wrong_audience_expired_and_unmapped_subject_fail_closed(self) -> None:
        authenticator = Authenticator(self.config)
        cases = (
            (self._token(audience="another-api"), "jwt_validation_failed"),
            (self._token(expires_at=int(time.time()) - 10), "jwt_validation_failed"),
            (self._token(subject="unknown-person"), "unmapped_subject"),
        )
        for token, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(AuthenticationError) as raised:
                    authenticator.authenticate(token)
                self.assertEqual(raised.exception.code, expected_code)

    def test_unknown_key_triggers_one_rotation_refresh(self) -> None:
        authenticator = Authenticator(self.config)
        authenticator.authenticate(self._token())
        self.assertEqual(self.issuer_server.request_count, 2)  # type: ignore[attr-defined]
        self._add_key("key-2")

        identity = authenticator.authenticate(self._token(key_id="key-2"))

        self.assertEqual(identity.actor_id, "alice")
        self.assertEqual(self.issuer_server.request_count, 4)  # type: ignore[attr-defined]

    def test_repeated_unknown_key_ids_are_refresh_rate_limited(self) -> None:
        authenticator = Authenticator(self.config)
        unknown_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = int(time.time())

        def unknown_token(key_id: str) -> str:
            return jwt.encode(
                {
                    "iss": self.issuer,
                    "sub": "oidc-alice",
                    "aud": "hormuz-api",
                    "iat": now,
                    "exp": now + 300,
                },
                unknown_private_key,
                algorithm="RS256",
                headers={"kid": key_id},
            )

        for key_id in ("unknown-1", "unknown-2"):
            with self.assertRaises(AuthenticationError) as raised:
                authenticator.authenticate(unknown_token(key_id))
            self.assertEqual(raised.exception.code, "unknown_signing_key")
        # First miss fetches discovery + JWKS, then performs one rotation
        # refresh. The second attacker-controlled kid performs no network I/O.
        self.assertEqual(self.issuer_server.request_count, 4)  # type: ignore[attr-defined]

    def test_gateway_whoami_accepts_oidc_and_exposes_no_token(self) -> None:
        gateway = GatewayServer(replace(self.config, listen=ListenConfig("127.0.0.1", 0)))
        thread = serve_in_thread(gateway)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", gateway.server_address[1], timeout=5)
            connection.request("GET", "/v1/gateway/whoami", headers={"Authorization": f"Bearer {self._token()}"})
            response = connection.getresponse()
            value = json.loads(response.read())
            connection.close()
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=5)

        self.assertEqual(response.status, 200)
        self.assertEqual(value["actor_id"], "alice")
        self.assertEqual(value["organization_id"], "xpounder")
        self.assertEqual(value["authentication_source"], f"oidc:{self.issuer}")
        self.assertNotIn("token", value)
        self.assertNotIn("subject", value)

    def test_expired_workload_token_cannot_reach_gateway_or_admin_paths(self) -> None:
        value = self._config_value()
        subject = value["authentication"]["oidc"]["issuers"][0]["subjects"][0]  # type: ignore[index]
        subject.update(
            {
                "subject": "ci:release",
                "actor_id": "ci-release",
                "actor_name": "Release CI",
                "identity_type": "ci",
                "capabilities": ["policy_admin", "dlp_approver"],
            }
        )
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        config = GatewayConfig.load(self.config_path)
        gateway = GatewayServer(replace(config, listen=ListenConfig("127.0.0.1", 0)))
        thread = serve_in_thread(gateway)
        try:
            status, identity = self._gateway_request(
                gateway,
                method="GET",
                path="/v1/gateway/whoami",
                token=self._token(subject="ci:release"),
            )
            self.assertEqual(status, 200)
            self.assertEqual(identity["identity_type"], "ci")

            expired_token = self._token(
                subject="ci:release",
                expires_at=int(time.time()) - 10,
            )
            denied_requests = (
                ("POST", "/v1/responses", {"model": "gpt-test", "input": "blocked"}),
                ("GET", "/v1/admin/policy-active", None),
                (
                    "POST",
                    "/v1/admin/policy-activations",
                    {"version_id": "hpv_v1_" + "0" * 64, "expected_active_version_id": None},
                ),
                (
                    "POST",
                    "/v1/dlp/approval-requests/apr_" + "0" * 32 + "/decisions",
                    {"decision": "approve"},
                ),
            )
            for method, path, request_value in denied_requests:
                with self.subTest(method=method, path=path):
                    status, response = self._gateway_request(
                        gateway,
                        method=method,
                        path=path,
                        token=expired_token,
                        value=request_value,
                    )
                    self.assertEqual(status, 401)
                    self.assertEqual(response["error"]["code"], "unauthorized")
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=5)

    def test_workload_oidc_identity_reaches_lifecycle_connector_only_with_promoter_capability(self) -> None:
        value = self._config_value()
        value["context_database"] = "./context.sqlite3"
        value["context_service"] = {
            "lifecycle": {
                "enabled": True,
                "policy_version": "test-lifecycle-v1",
                "promotion_paths": [
                    {
                        "id": "green",
                        "record_kinds": ["claim"],
                        "required_signals": ["ci_passed"],
                    }
                ],
            }
        }
        subject = value["authentication"]["oidc"]["issuers"][0]["subjects"][0]  # type: ignore[index]
        subject["capabilities"] = ["context_promoter"]
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        config = GatewayConfig.load(self.config_path)
        gateway = GatewayServer(replace(config, listen=ListenConfig("127.0.0.1", 0)))
        thread = serve_in_thread(gateway)
        evidence = json.dumps(
            {
                "schema_version": "hormuz.context-evidence.v1",
                "organization_id": "xpounder",
                "record_id": "not-yet-imported",
                "record_version": 1,
                "signal": "ci_passed",
                "evidence_ref": "ci:private:123",
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                gateway.server_address[1],
                timeout=5,
            )
            connection.request(
                "POST",
                "/v1/context/evidence",
                body=evidence,
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=5)

        self.assertEqual(response.status, 409)
        self.assertEqual(payload["error"]["code"], "context_lifecycle_conflict")
        self.assertEqual(self.issuer_server.request_count, 2)  # type: ignore[attr-defined]

    def test_configuration_rejects_symmetric_or_non_tls_remote_issuer(self) -> None:
        value = self._config_value()
        issuer = value["authentication"]["oidc"]["issuers"][0]  # type: ignore[index]
        issuer["algorithms"] = ["HS256"]
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "asymmetric JWT algorithms"):
            GatewayConfig.load(self.config_path)

        issuer["algorithms"] = ["RS256"]
        issuer["issuer"] = "http://idp.example.com"
        issuer["allow_insecure_http"] = True
        self.config_path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ConfigError, "only loopback HTTP"):
            GatewayConfig.load(self.config_path)

    def test_duplicate_actor_authentication_sources_require_identical_authorization(self) -> None:
        value = self._config_value()
        value["identities"] = [  # type: ignore[index]
            {
                "token_env": "ALICE_BOOTSTRAP_TOKEN",
                "actor_id": "alice",
                "actor_name": "Alice",
                "team_id": "marketing",
                "team_name": "Marketing",
                "organization_id": "xpounder",
                "clearance": "confidential",
                "allowed_clients": ["codex", "claude-code"],
            }
        ]
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaisesRegex(ConfigError, "must match across authentication sources"):
            GatewayConfig.load(
                self.config_path,
                environ={"ALICE_BOOTSTRAP_TOKEN": "long-bootstrap-credential"},
            )


if __name__ == "__main__":
    unittest.main()
