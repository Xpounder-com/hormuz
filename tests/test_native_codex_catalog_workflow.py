"""Native catalog behavior through the approved connected-client workflow."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from tests import test_execution_destination_workflow as workflow
from tests import test_connected_client_lifetime_workflow as lifetime_workflow
from hormuz.contracts import validate_contract_manifest


@contextmanager
def native_backend(body, *, status=200, headers=None, drip=False, certificate_host="chatgpt.com", dns_delay=0, workers=None):
    """A real TLS service standing in for the fixed native backend authority."""
    received = []
    stopped = threading.Event()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, certificate_host)])
        now = datetime.now(timezone.utc)
        certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(42)
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(certificate_host)]), critical=False)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=True, data_encipherment=False, key_agreement=False,
                key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
        cert_path, key_path = root / "certificate.pem", root / "key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_path.chmod(0o600)

        class Handler(BaseHTTPRequestHandler):
            timeout = 3
            def log_message(self, *_args):
                pass
            def do_GET(self):
                received.append({"path": self.path, "headers": {key.lower(): value for key, value in self.headers.items()}})
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    if drip:
                        for byte in data:
                            if stopped.wait(0.15):
                                return
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                    else:
                        self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
                    pass

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = socket.getaddrinfo
        original_process = subprocess.Popen
        def resolve(host, port, *args, **kwargs):
            if host == "chatgpt.com":
                if dns_delay:
                    time.sleep(dns_delay)
                return original("127.0.0.1", server.server_port, *args, **kwargs)
            return original(host, port, *args, **kwargs)
        def launch(args, *positional, **options):
            if isinstance(args, (list, tuple)) and Path(str(args[-1])).name == "_native_catalog_worker.py":
                # Supply the same external DNS fixture to a real isolated worker.
                # Its HTTP/TLS implementation still runs unmodified; no router
                # collaborator or provider reply is mocked.
                prelude = ('import runpy,socket,sys,time\n'
                    'original=socket.getaddrinfo\n'
                    'def resolve(host,port,*args,**kwargs):\n'
                    ' if host=="chatgpt.com":\n'
                    '  time.sleep(float(sys.argv[3]))\n'
                    '  return original("127.0.0.1",int(sys.argv[2]),*args,**kwargs)\n'
                    ' return original(host,port,*args,**kwargs)\n'
                    'socket.getaddrinfo=resolve\n'
                    'runpy.run_path(sys.argv[1],run_name="__main__")\n')
                args = [args[0], "-I", "-S", "-c", prelude, args[-1], str(server.server_port), str(dns_delay)]
                process = original_process(args, *positional, **options)
                if workers is not None:
                    workers.append(process.pid)
                return process
            return original_process(args, *positional, **options)
        try:
            # DNS and the trust store are external system boundaries. The real
            # router still connects with TLS to its fixed chatgpt.com authority.
            with patch("socket.getaddrinfo", side_effect=resolve), \
                 patch("subprocess.Popen", side_effect=launch), \
                 patch.dict(os.environ, {"SSL_CERT_FILE": str(cert_path)}):
                yield received
        finally:
            stopped.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class NativeCodexCatalogWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def configuration(self, local, cloud):
        value = self.fixture.configuration(local, cloud)
        value["native_codex_catalog"] = True
        value["upstreams"]["native-codex"] = {"protocol": "openai", "locality": "cloud",
            "authentication": "codex_chatgpt", "base_url": "https://chatgpt.com/backend-api/codex"}
        value["model_routes"]["cloud-code"] = {"protocol": "openai", "upstream_model": "fixture-cloud-model"}
        value["model_routes"]["native-code"] = {"protocol": "openai", "destination": "native-codex",
                                               "upstream_model": "fixture-cloud-model"}
        value["policies"]["organization"]["allowed_models"].extend(["cloud-code", "native-code"])
        return value

    def test_native_catalog_consumes_gateway_auth_and_preserves_native_headers_only_to_its_backend(self):
        visible = {"slug": "fixture-cloud-model", "display_name": "Fixture cloud", "context_window": 32000}
        with workflow.provider("LOCAL_RESULT") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [visible, {"slug": "other-model", "display_name": "Not configured"}]}) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                self.assertEqual(submit()["status"], 200)
                result = submit(method="GET", path="/backend-api/codex/models?client_version=0.147.0",
                    absolute_path=True, headers={"X-Gateway-Key": "fixture-coding-client-credential",
                        "Authorization": "Bearer NATIVE_CHATGPT_SENTINEL", "ChatGPT-Account-ID": "fixture-workspace",
                        "X-Api-Key": "UNRELATED_API_KEY_SENTINEL", "Cookie": "unrelated=COOKIE_SENTINEL"})
                self.assertEqual(result["status"], 200, result)
                self.assertEqual(result["body"], {"models": [visible]})
                self.assertEqual(result["headers"].get("x-hormuz-contract"), "hormuz.native-codex-catalog;v=1")
                self.assertEqual(result["headers"].get("cache-control"), "no-store")
                usage = submit(method="GET", path="/gateway/usage")
                self.assertEqual(usage["body"]["requests"], 1)
            self.assertEqual((len(local_requests), len(cloud_requests), len(native_requests)), (1, 0, 1))
            request = native_requests[0]
            self.assertEqual(request["path"], "/backend-api/codex/models?client_version=0.147.0")
            self.assertEqual(request["headers"]["authorization"], "Bearer NATIVE_CHATGPT_SENTINEL")
            self.assertEqual(request["headers"]["chatgpt-account-id"], "fixture-workspace")
            self.assertEqual(request["headers"]["host"], "chatgpt.com")
            self.assertFalse({"x-gateway-key", "x-api-key", "cookie"} & request["headers"].keys())
            self.assertNotIn("SENTINEL", json.dumps(result))

    def test_native_credentials_cannot_fall_through_to_paid_api_or_local_inference(self):
        with workflow.provider("LOCAL_RESULT") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                headers = {"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                           "X-Gateway-Key": "fixture-coding-client-credential",
                           "X-Api-Key": "fixture-coding-client-credential"}
                for options in ({"request": {"model": "cloud-code"}}, {"request": {"model": "local-code"}},
                                {"method": "GET", "path": "/models"}):
                    with self.subTest(options=options):
                        response = submit(headers=headers, **options)
                        self.assertEqual(response["status"], 403, response)
                        self.assertEqual(response["body"]["error"]["code"], "hormuz_policy_denied")
                        self.assertNotIn("SENTINEL", json.dumps(response))
                        self.assertEqual((local_requests, cloud_requests), ([], []))
                response = submit(headers={"ChatGPT-Account-ID": "fixture-workspace"})
                self.assertEqual(response["status"], 403)
                self.assertEqual((local_requests, cloud_requests), ([], []))
                self.assertEqual(submit()["status"], 200)
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 0))

    def test_known_gateway_and_api_credentials_cannot_be_repurposed_as_native_authorization(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
            value = self.configuration(local, cloud)
            self.fixture.environment["OTHER_GATEWAY_TOKEN"] = "OTHER_GATEWAY_SENTINEL"
            value["identities"].append({**value["identities"][0], "actor_id": "other",
                                         "token_env": "OTHER_GATEWAY_TOKEN"})
            with self.fixture.connected_client(value) as submit:
                for credential in ("fixture-cloud-destination-credential", "OTHER_GATEWAY_SENTINEL",
                                   "fixture-coding-client-credential"):
                    with self.subTest(credential_kind=credential.split("-")[0]):
                        response = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                            headers={"Authorization": "Bearer " + credential,
                                     "X-Gateway-Key": "fixture-coding-client-credential"})
                        self.assertEqual(response["status"], 401, response)
                        self.assertNotIn(credential, json.dumps(response))
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_effective_client_model_and_local_only_permissions_block_before_native_egress(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
            for case in ("local_only", "request_local_only", "identity_client", "actor_client", "team_client",
                         "organization_models", "actor_models", "team_models"):
                with self.subTest(case=case):
                    value = self.configuration(local, cloud)
                    headers = {"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                               "X-Gateway-Key": "fixture-coding-client-credential"}
                    if case == "local_only":
                        value["route_admission"] = {"mode": "local_only", "local_routes": {}}
                    elif case == "request_local_only":
                        headers["X-Hormuz-Routing-Mode"] = "local_only"
                    elif case == "identity_client":
                        value["identities"][0]["allowed_clients"] = ["claude-code"]
                    elif case == "organization_models":
                        value["policies"]["organization"]["allowed_models"] = ["local-code"]
                    else:
                        level, field = case.split("_")
                        value["policies"][level + "s"] = {"developer" if level == "actor" else "local":
                            {"allowed_clients" if field == "client" else "allowed_models":
                             ["claude-code" if field == "client" else "local-code"]}}
                    with self.fixture.connected_client(value) as submit:
                        response = submit(method="GET", path="/backend-api/codex/models", absolute_path=True, headers=headers)
                        self.assertEqual(response["status"], 403, response)
                    self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_malformed_or_ambiguous_native_catalogs_return_content_free_errors(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for body in ({"models": "PRIVATE_UPSTREAM_SENTINEL"}, {"models": [None]},
                             {"models": [{"slug": ["PRIVATE_UPSTREAM_SENTINEL"]}]},
                             {"models": [{"slug": ""}]},
                             {"models": [{"slug": "x" * 257}]},
                             {"models": [{"slug": "fixture-cloud-model", "display_name": "PRIVATE_UPSTREAM_SENTINEL"},
                                         {"slug": "fixture-cloud-model"}]},
                             {"models": [{"slug": "model-" + str(index)} for index in range(257)]},
                             b'{"models": [], "models": [{"slug":"PRIVATE_UPSTREAM_SENTINEL"}]}'):
                    with self.subTest(shape=type(body).__name__), native_backend(body) as native_requests:
                        result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                            headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                                     "X-Gateway-Key": "fixture-coding-client-credential"})
                        self.assertEqual(result["status"], 502, result)
                        self.assertEqual(result["body"]["error"]["code"], "gateway_upstream_error")
                        self.assertNotIn("SENTINEL", json.dumps(result))
                        self.assertEqual(len(native_requests), 1)
                        self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_denial_redirect_and_rate_limit_do_not_retry_or_change_billing(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for status in (401, 403, 307, 429):
                    with self.subTest(status=status), native_backend({"error": "PRIVATE_UPSTREAM_SENTINEL"},
                        status=status, headers={"Retry-After": "37", "Location": "https://chatgpt.com/other-path"}) as native_requests:
                        result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                            headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                                     "X-Gateway-Key": "fixture-coding-client-credential"})
                        self.assertEqual(result["status"], 502 if status == 307 else status)
                        self.assertNotIn("SENTINEL", json.dumps(result))
                        self.assertEqual(len(native_requests), 1)
                        self.assertEqual((local_requests, cloud_requests), ([], []))
                        if status == 429:
                            self.assertEqual(result["headers"].get("retry-after"), "37")

    def test_native_catalog_needs_no_api_key_and_does_not_make_native_inference_ready(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             native_backend({"models": [{"slug": "fixture-native-model"}]}) as native_requests:
            value = self.fixture.configuration(local, "https://api.example.invalid/v1")
            del value["upstreams"]["openai"]
            self.fixture.environment.pop("DESTINATION_CLOUD_KEY")
            value["native_codex_catalog"] = True
            value["upstreams"]["native-codex"] = {"protocol": "openai", "locality": "cloud",
                "authentication": "codex_chatgpt", "base_url": "https://chatgpt.com/backend-api/codex"}
            value["model_routes"]["native-code"] = {"protocol": "openai", "destination": "native-codex",
                                                    "upstream_model": "fixture-native-model"}
            value["policies"]["organization"]["allowed_models"].append("native-code")
            with self.fixture.connected_client(value) as submit:
                result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                    headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                             "X-Gateway-Key": "fixture-coding-client-credential"})
                self.assertEqual((result["status"], result["body"]), (200, {"models": [{"slug": "fixture-native-model"}]}))
                ordinary_models = submit(method="GET", path="/models")
                self.assertEqual([model["id"] for model in ordinary_models["body"]["data"]], ["local-code"])
                blocked = submit(request={"model": "native-code"})
                self.assertEqual(blocked["status"], 403, blocked)
                self.assertEqual((len(native_requests), local_requests), (1, []))
                usage = submit(method="GET", path="/gateway/usage")
                self.assertEqual(usage["body"]["requests"], 0)
                preflight = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(self.fixture.root / "gateway.json"),
                    "client", "cloud-readiness", "codex", "--client-home", str(self.fixture.root / "absent-home"),
                    "--surface", "desktop"], capture_output=True, text=True, timeout=10)
                self.assertEqual(preflight.returncode, 0, preflight.stderr)
                readiness = json.loads(preflight.stdout)
                self.assertEqual(readiness["configuration"]["cloud_routes"], 0)
                self.assertEqual(readiness["paths"][1]["status"], "not_implemented")

    def test_versioned_catalog_fixture_is_discoverable_through_the_public_manifest(self):
        fixture = json.loads((Path(__file__).parent / "fixtures/native-codex-catalog-v1.json").read_text())
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend(fixture["response"]):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                result = submit(method="GET", path=fixture["request"]["path"], absolute_path=True,
                    headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                             "X-Gateway-Key": "fixture-coding-client-credential"})
                self.assertEqual(result["status"], 200)
                self.assertEqual(result["headers"].get("x-hormuz-contract"), fixture["contract"])
                self.assertEqual(result["body"], fixture["response"])
                self.assertEqual((local_requests, cloud_requests), ([], []))
        result = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        validate_contract_manifest(manifest)
        entries = [entry for entry in manifest["schemas"] if entry["schema_id"] == "hormuz.native-codex-catalog"]
        self.assertEqual(entries, [{"schema_id": "hormuz.native-codex-catalog", "schema_version": 1,
            "delivery": "http-headers", "ownership": "hormuz", "legacy": False, "fields": ["X-Hormuz-Contract"]}])

    def test_ambiguous_or_missing_authentication_never_reaches_the_native_backend(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                cases = {
                    "missing_gateway": {"X-Gateway-Key": ""},
                    "wrong_gateway": {"X-Gateway-Key": "wrong-gateway", "X-Api-Key": "fixture-coding-client-credential"},
                    "duplicate_gateway": {"x-gateway-key": "another-gateway"},
                    "duplicate_native": {"authorization": "Bearer SECOND_NATIVE_SENTINEL"},
                    "duplicate_account": {"ChatGPT-Account-ID": "first", "chatgpt-account-id": "second"},
                    "oversized_account": {"ChatGPT-Account-ID": "x" * 257},
                    "wrong_scheme": {"Authorization": "Basic NATIVE_CHATGPT_SENTINEL"},
                    "empty_native": {"Authorization": "Bearer "},
                    "multiple_tokens": {"Authorization": "Bearer first second"},
                    "combined_tokens": {"Authorization": "Bearer first,second"},
                }
                for case, changed in cases.items():
                    with self.subTest(case=case):
                        result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                            headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                                     "X-Gateway-Key": "fixture-coding-client-credential", **changed})
                        self.assertEqual(result["status"], 401, result)
                        self.assertNotIn("SENTINEL", json.dumps(result))
                        self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_catalog_size_and_slow_body_are_bounded_and_the_next_request_can_recover(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for case in ("oversized", "slow_body"):
                    with self.subTest(case=case), native_backend({"models": [{"slug": "fixture-cloud-model",
                        "description": "x" * (1024 * 1024 if case == "oversized" else 8)}]},
                        drip=case == "slow_body") as native_requests:
                        started = time.monotonic()
                        result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                            headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                                     "X-Gateway-Key": "fixture-coding-client-credential"})
                        self.assertLess(time.monotonic() - started, 5)
                        self.assertEqual(result["status"], 502, result)
                        self.assertEqual(len(native_requests), 1)
                with native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
                    result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                        headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                                 "X-Gateway-Key": "fixture-coding-client-credential"})
                    self.assertEqual(result["status"], 200, result)
                    self.assertEqual(len(native_requests), 1)
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_credentials_are_not_sent_when_tls_does_not_match_the_fixed_authority(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]},
                            certificate_host="unrelated.example.invalid") as native_requests:
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                    headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                             "X-Gateway-Key": "fixture-coding-client-credential"})
                self.assertEqual(result["status"], 502, result)
                self.assertNotIn("SENTINEL", json.dumps(result))
                self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_empty_identity_client_list_uses_the_existing_unrestricted_identity_semantics(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
            value = self.configuration(local, cloud)
            value["identities"][0]["allowed_clients"] = []
            with self.fixture.connected_client(value) as submit:
                result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                    headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                             "X-Gateway-Key": "fixture-coding-client-credential"})
                self.assertEqual(result["status"], 200, result)
                self.assertEqual((len(native_requests), local_requests, cloud_requests), (1, [], []))

    def test_native_catalog_is_disabled_by_default_and_rejects_unrecognized_queries_before_egress(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
            headers = {"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                       "X-Gateway-Key": "fixture-coding-client-credential"}
            value = self.configuration(local, cloud)
            del value["native_codex_catalog"]
            with self.fixture.connected_client(value) as submit:
                result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True, headers=headers)
                self.assertEqual(result["status"], 404)
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                for query in ("base_url=https://unrelated.example.invalid", "client_version=unexpected",
                              "client_version=0.147.0&client_version=0.160.0", "token=PRIVATE_QUERY_SENTINEL",
                              "client_version=" + "1" * 129):
                    with self.subTest(query_kind=query.split("=")[0]):
                        result = submit(method="GET", path="/backend-api/codex/models?" + query,
                                        absolute_path=True, headers=headers)
                        self.assertEqual(result["status"], 400, result)
                        self.assertNotIn("SENTINEL", json.dumps(result))
            self.assertEqual((local_requests, cloud_requests, native_requests), ([], [], []))

    def test_read_only_preflight_rejects_unsafe_native_profile_declarations(self):
        path = self.fixture.root / "gateway.json"
        cases = ({"base_url": "http://chatgpt.com/backend-api/codex"},
                 {"base_url": "https://unrelated.example.invalid/backend-api/codex"},
                 {"base_url": "https://chatgpt.com:443/backend-api/codex"},
                 {"base_url": "https://chatgpt.com/backend-api/codex?secret=PRIVATE_QUERY_SENTINEL"},
                 {"protocol": "anthropic"}, {"locality": "local"},
                 {"api_key_env": "DO_NOT_READ_SENTINEL"}, {"api_key_envelope": "DO_NOT_OPEN_SENTINEL"},
                 {"allow_response_storage": True}, {"allow_background": True})
        for changes in (*cases, None):
            with self.subTest(field=next(iter(changes)) if changes else "non_boolean_opt_in"):
                value = self.configuration("http://127.0.0.1:1", "https://api.example.invalid/v1")
                if changes:
                    value["upstreams"]["native-codex"].update(changes)
                else:
                    value["native_codex_catalog"] = "true"
                path.write_text(json.dumps(value))
                before = path.read_bytes(), set(self.fixture.root.iterdir())
                result = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(path), "client",
                    "cloud-readiness", "codex", "--surface", "desktop", "--client-home",
                    str(self.fixture.root / "absent-home")], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads(result.stdout)
                self.assertEqual(receipt["configuration"]["status"], "invalid", receipt)
                self.assertNotIn("SENTINEL", result.stdout + result.stderr)
                self.assertEqual(receipt["effects"], {"settings_changed": False, "credentials_read": False,
                                                    "provider_requests": 0})
                self.assertEqual(before, (path.read_bytes(), set(self.fixture.root.iterdir())))

    def test_ai_work_selection_never_offers_catalog_only_routes_as_inference_candidates(self):
        with workflow.provider("LOCAL_RESULT") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests), \
             native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
            value = self.configuration(local, cloud)
            value["ai_work"] = {"enabled": True, "database": str(self.fixture.root / "work.sqlite3")}
            with self.fixture.connected_client(value) as submit:
                job = submit(control=True, path="/work/jobs", request={
                    "repository": "example/repository", "task_type": "summarization"})
                self.assertEqual(job["status"], 201, job)
                result = submit(headers={"X-Hormuz-Work-Id": job["body"]["work_id"]})
                self.assertEqual(result["status"], 200, result)
                status = submit(method="GET", path="/work/jobs/" + job["body"]["work_id"])
                self.assertEqual(status["status"], 200, status)
                self.assertNotIn("native-code", [candidate["model"]
                    for candidate in status["body"]["routing"]["candidates"]])
                self.assertEqual((len(local_requests), cloud_requests, native_requests), (1, [], []))

    def test_generated_and_persistent_client_defaults_skip_catalog_only_routes(self):
        with workflow.provider("LOCAL_RESULT") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value["model_routes"] = {"native-code": value["model_routes"]["native-code"], **value["model_routes"]}
            with self.fixture.connected_client(value) as submit:
                result = submit()
                self.assertEqual(result["status"], 200, result)
            persistent = lifetime_workflow.ConnectedClientLifetimeWorkflowTests()
            persistent.setUp()
            self.addCleanup(persistent.doCleanups)
            settings = persistent.home / "config.toml"
            original = 'model = "original-model"\n'
            settings.write_text(original)
            persistent.path.write_text(json.dumps(value))
            self.addCleanup(persistent.command, "disconnect", check=False)
            persistent.command("connect")
            result = persistent.client()
            self.assertEqual((result["model"], result["status"]), ("local-code", 200), result)
            persistent.command("disconnect")
            self.assertEqual(settings.read_text(), original)
            self.assertEqual((len(local_requests), cloud_requests), (2, []))

    def test_only_catalog_routes_cannot_generate_or_install_generic_client_settings(self):
        persistent = lifetime_workflow.ConnectedClientLifetimeWorkflowTests()
        persistent.setUp()
        self.addCleanup(persistent.doCleanups)
        settings = persistent.home / "config.toml"
        original = 'model = "original-model"\n'
        settings.write_text(original)
        value = self.configuration("http://127.0.0.1:1", "https://api.example.invalid/v1")
        value["policies"]["organization"]["allowed_models"] = ["native-code"]
        persistent.path.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(persistent.path),
            "client", "config", "codex"], capture_output=True, text=True, timeout=10, env=persistent.environment)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "")
        self.addCleanup(persistent.command, "disconnect", check=False)
        result = persistent.command("connect", "--dry-run", check=False)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(settings.read_text(), original)

    def test_catalog_deadline_includes_stalled_dns_and_capacity_recovers_after_cleanup(self):
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                headers = {"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                           "X-Gateway-Key": "fixture-coding-client-credential"}
                workers = []
                with native_backend({"models": [{"slug": "fixture-cloud-model"}]}, dns_delay=4.4, workers=workers) as native_requests:
                    started = time.monotonic()
                    result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True, headers=headers)
                    self.assertEqual(result["status"], 502, result)
                    self.assertLess(time.monotonic() - started, 4)
                    self.assertEqual(native_requests, [])
                    for pid in workers:
                        with self.assertRaises(ProcessLookupError):
                            os.kill(pid, 0)
                with native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
                    result = submit(method="GET", path="/backend-api/codex/models", absolute_path=True, headers=headers)
                    self.assertEqual(result["status"], 200, result)
                    self.assertEqual(len(native_requests), 1)
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_native_catalog_closes_a_stalled_downstream_client_within_a_bounded_write(self):
        client = r'''
import json, os, socket, sys, time, tomllib
from urllib.parse import urlsplit
config = tomllib.loads(sys.stdin.read())
provider = config["model_providers"][config["model_provider"]]
origin = urlsplit(provider["base_url"])
with socket.create_connection((origin.hostname, origin.port), timeout=5) as wire:
    wire.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    request = ("GET /backend-api/codex/models HTTP/1.1\r\nHost: localhost\r\n"
        "Connection: keep-alive\r\nAuthorization: Bearer NATIVE_CHATGPT_SENTINEL\r\n"
        "X-Gateway-Key: " + os.environ[provider["env_key"]] + "\r\n\r\n")
    wire.sendall(request.encode("ascii"))
    headers = b""
    while b"\r\n\r\n" not in headers:
        chunk = wire.recv(4096)
        if not chunk: raise ValueError("fixture closed before headers")
        headers += chunk
        if len(headers) > 8192: raise ValueError("fixture header limit")
    status = int(headers.split(b" ", 2)[1])
    time.sleep(3.6)
    wire.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
    deadline, received, closed = time.monotonic() + 2, 0, False
    while received <= 8 * 1024 * 1024:
        remaining = deadline - time.monotonic()
        if remaining <= 0: break
        wire.settimeout(remaining)
        try: chunk = wire.recv(65536)
        except (TimeoutError, socket.timeout): break
        if not chunk:
            closed = True
            break
        received += len(chunk)
print(json.dumps({"status": status, "closed": closed}))
'''
        # Under the upstream 1 MiB bound, but JSON escaping makes the downstream
        # body exceed typical Linux send buffers as well as the smaller macOS one.
        large = json.dumps({"models": [{"slug": "fixture-cloud-model", "description": "\x7f" * 950000}]},
                           ensure_ascii=False).encode("utf-8")
        with workflow.provider("LOCAL_UNUSED") as (local, local_requests), \
             workflow.provider("PAID_API_UNUSED") as (cloud, cloud_requests):
            with self.fixture.connected_client(self.configuration(local, cloud)) as submit:
                configuration = subprocess.run([sys.executable, "-m", "hormuz", "--config",
                    str(self.fixture.root / "gateway.json"), "client", "config", "codex"],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(configuration.returncode, 0, configuration.stderr)
                with native_backend(large) as native_requests:
                    result = subprocess.run([sys.executable, "-c", client], input=configuration.stdout,
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(json.loads(result.stdout), {"status": 200, "closed": True})
                    self.assertEqual(len(native_requests), 1)
                with native_backend({"models": [{"slug": "fixture-cloud-model"}]}) as native_requests:
                    recovered = submit(method="GET", path="/backend-api/codex/models", absolute_path=True,
                        headers={"Authorization": "Bearer NATIVE_CHATGPT_SENTINEL",
                                 "X-Gateway-Key": "fixture-coding-client-credential"})
                    self.assertEqual(recovered["status"], 200, recovered)
                    self.assertEqual((len(native_requests), local_requests, cloud_requests), (1, [], []))


if __name__ == "__main__":
    unittest.main()
