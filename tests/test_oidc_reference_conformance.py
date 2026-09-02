from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "oidc_reference_conformance.py"
_SPEC = importlib.util.spec_from_file_location("oidc_reference_conformance", TOOL_PATH)
assert _SPEC is not None and _SPEC.loader is not None
oidc_reference = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = oidc_reference
_SPEC.loader.exec_module(oidc_reference)


class _ReferenceIssuerHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/.well-known/openid-configuration":
            value = {
                "issuer": self.server.issuer,  # type: ignore[attr-defined]
                "jwks_uri": f"{self.server.issuer}/jwks",  # type: ignore[attr-defined]
            }
        elif self.path == "/jwks":
            value = {"keys": [self.server.jwk]}  # type: ignore[attr-defined]
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


class OIDCReferenceConformanceToolTests(unittest.TestCase):
    def test_authorization_url_uses_pkce_s256_without_client_secret(self) -> None:
        metadata = oidc_reference.ProviderMetadata(
            issuer="https://identity.example",
            authorization_endpoint="https://identity.example/authorize",
            token_endpoint="https://identity.example/token",
            jwks_uri="https://identity.example/jwks",
            id_token_algorithms=("RS256",),
            response_modes=("query", "form_post"),
        )
        verifier = "A" * 64

        value = oidc_reference._authorization_url(
            metadata=metadata,
            client_id="public-client-id",
            redirect_uri="http://127.0.0.1:8765/callback",
            verifier=verifier,
            state="state-value",
            nonce="nonce-value",
        )

        self.assertEqual(urlsplit(value).scheme, "https")
        query = parse_qs(urlsplit(value).query)
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["response_mode"], ["form_post"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["code_challenge"], [oidc_reference._pkce_challenge(verifier)])
        self.assertEqual(query["client_id"], ["public-client-id"])
        self.assertNotIn("client_secret", query)

    def test_loopback_callback_rejects_public_hosts_and_malformed_ports(self) -> None:
        accepted = oidc_reference._loopback_redirect("http://127.0.0.1:8765/callback")
        self.assertEqual(accepted.hostname, "127.0.0.1")
        self.assertEqual(accepted.port, 8765)
        for value in (
            "https://127.0.0.1:8765/callback",
            "http://localhost:8765/callback",
            "http://example.com:8765/callback",
            "http://127.0.0.1:invalid/callback",
            "http://127.0.0.1:0/callback",
            "http://127.0.0.1:8765/callback?code=wrong",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(oidc_reference.ConformanceError, "unsafe_redirect_uri"):
                    oidc_reference._loopback_redirect(value)

    def test_discovery_requires_form_post(self) -> None:
        document = {
            "issuer": "https://identity.example",
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/token",
            "jwks_uri": "https://identity.example/jwks",
            "code_challenge_methods_supported": ["S256"],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "response_modes_supported": ["query"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
        with mock.patch.object(oidc_reference, "_fetch_json", return_value=document):
            with self.assertRaisesRegex(oidc_reference.ConformanceError, "form_post_unsupported"):
                oidc_reference._discover("https://identity.example")

    def test_oidc_metadata_and_token_exchange_reject_redirects(self) -> None:
        request = urllib.request.Request("https://identity.example/endpoint")
        handler = oidc_reference._RejectRedirectHandler()
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                method = getattr(handler, f"http_error_{status}")
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    method(request, None, status, "redirect", Message())
                self.addCleanup(raised.exception.close)
                self.assertEqual(raised.exception.code, status)

        redirect_error = urllib.error.HTTPError(
            "https://identity.example/endpoint",
            307,
            "redirect",
            Message(),
            None,
        )
        self.addCleanup(redirect_error.close)
        with mock.patch.object(oidc_reference._NO_REDIRECT_OPENER, "open", side_effect=redirect_error):
            with self.assertRaisesRegex(oidc_reference.ConformanceError, "oidc_metadata_unavailable"):
                oidc_reference._fetch_json("https://identity.example/metadata", "oidc_metadata_unavailable")
            with self.assertRaisesRegex(oidc_reference.ConformanceError, "token_exchange_failed"):
                oidc_reference._exchange_code(
                    metadata=oidc_reference.ProviderMetadata(
                        issuer="https://identity.example",
                        authorization_endpoint="https://identity.example/authorize",
                        token_endpoint="https://identity.example/token",
                        jwks_uri="https://identity.example/jwks",
                        id_token_algorithms=("RS256",),
                        response_modes=("form_post",),
                    ),
                    client_id="public-client-id",
                    redirect_uri="http://127.0.0.1:8765/callback",
                    verifier="A" * 64,
                    code="authorization-code",
                )

    def test_form_post_callback_accepts_expected_state(self) -> None:
        callback = oidc_reference._CallbackServer(
            host="127.0.0.1",
            port=0,
            path="/callback",
            state="expected-state",
        )
        thread = threading.Thread(target=callback.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", callback.server_address[1], timeout=5)
            connection.request(
                "POST",
                "/callback",
                body="code=authorization-code&state=expected-state",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(callback.callback_event.wait(timeout=2))
            self.assertEqual(callback.callback_result, oidc_reference.CallbackResult(code="authorization-code"))
        finally:
            callback.shutdown()
            callback.server_close()
            thread.join(timeout=5)

    def test_form_post_callback_rejects_wrong_state_without_retaining_authorization_code(self) -> None:
        callback = oidc_reference._CallbackServer(
            host="127.0.0.1",
            port=0,
            path="/callback",
            state="expected-state",
        )
        thread = threading.Thread(target=callback.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", callback.server_address[1], timeout=5)
            connection.request(
                "POST",
                "/callback",
                body="code=authorization-code&state=wrong-state",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertTrue(callback.callback_event.wait(timeout=2))
            self.assertEqual(callback.callback_result, oidc_reference.CallbackResult(failure_code="callback_state_mismatch"))
        finally:
            callback.shutdown()
            callback.server_close()
            thread.join(timeout=5)

    def test_query_callback_fails_closed_without_parsing_authorization_code(self) -> None:
        callback = oidc_reference._CallbackServer(
            host="127.0.0.1",
            port=0,
            path="/callback",
            state="expected-state",
        )
        thread = threading.Thread(target=callback.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", callback.server_address[1], timeout=5)
            connection.request("GET", "/callback?code=authorization-code&state=expected-state")
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 405)
            self.assertTrue(callback.callback_event.wait(timeout=2))
            self.assertEqual(
                callback.callback_result,
                oidc_reference.CallbackResult(failure_code="callback_response_mode_invalid"),
            )
        finally:
            callback.shutdown()
            callback.server_close()
            thread.join(timeout=5)

    def test_multi_audience_id_token_requires_matching_authorized_party(self) -> None:
        with self.assertRaisesRegex(oidc_reference.ConformanceError, "id_token_authorized_party_mismatch"):
            oidc_reference._validate_authorized_party(
                {"aud": ["public-client-id", "another-client"]},
                "public-client-id",
            )
        oidc_reference._validate_authorized_party(
            {"aud": ["public-client-id", "another-client"], "azp": "public-client-id"},
            "public-client-id",
        )

    def test_tamper_changes_signature_prefix(self) -> None:
        token = "header.payload.signature"

        tampered = oidc_reference._tamper_jwt(token)

        self.assertNotEqual(tampered, token)
        self.assertEqual(tampered.split(".")[:2], token.split(".")[:2])
        self.assertNotEqual(tampered.split(".")[2][0], token.split(".")[2][0])

    def test_invalid_input_reports_only_a_stable_failure_code(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = oidc_reference.main(
                [
                    "--issuer",
                    "not-an-issuer",
                    "--client-id",
                    "client-id",
                    "--audience",
                    "api://hormuz",
                ]
            )

        self.assertEqual(result, 1)
        self.assertEqual(stderr.getvalue(), "external_oidc_resource_server_conformance=failed code=invalid_issuer\n")

    def test_gateway_proof_uses_real_hormuz_verifier_and_rejects_tampering(self) -> None:
        issuer_server = ThreadingHTTPServer(("127.0.0.1", 0), _ReferenceIssuerHandler)
        issuer = f"http://127.0.0.1:{issuer_server.server_address[1]}"
        issuer_server.issuer = issuer  # type: ignore[attr-defined]
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        jwk.update({"kid": "reference-key", "alg": "RS256", "use": "sig"})
        issuer_server.jwk = jwk  # type: ignore[attr-defined]
        issuer_thread = threading.Thread(target=issuer_server.serve_forever, daemon=True)
        issuer_thread.start()
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": issuer,
                "sub": "reference-subject",
                "aud": "api://reference",
                "iat": now,
                "exp": now + 300,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "reference-key"},
        )
        original_config = oidc_reference._gateway_config_value

        def loopback_config(**kwargs: object) -> dict[str, object]:
            value = original_config(**kwargs)
            issuer_value = value["authentication"]["oidc"]["issuers"][0]  # type: ignore[index]
            issuer_value["allow_insecure_http"] = True  # type: ignore[index]
            return value

        try:
            with mock.patch.object(oidc_reference, "_gateway_config_value", side_effect=loopback_config):
                oidc_reference._verify_with_hormuz(
                    issuer=issuer,
                    audience="api://reference",
                    subject="reference-subject",
                    access_token=token,
                    expected_identity={
                        "actor_id": "reference-user",
                        "team_id": "platform",
                        "organization_id": "xpounder",
                    },
                    display_identity={"actor_name": "Reference User", "team_name": "Platform"},
                )
        finally:
            issuer_server.shutdown()
            issuer_server.server_close()
            issuer_thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
