from __future__ import annotations

import http.client
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from hormuz import client_relay as relay_module
from hormuz.client_relay import (
    ClientRelayError,
    LocalRelayServer,
    RelayOptimizer,
    SavedClientProfile,
    _client_command,
    load_saved_profile,
    run_client,
    supported_client_executable,
)
from hormuz.compaction import MAX_REQUEST_BYTES, optimize_request
from hormuz.compaction_enforcement import (
    CONTEXT_FORMAT_HEADER,
    CONTEXT_FORMAT_VERSION,
    CONTEXT_FORMATS_HEADER,
)
from hormuz.compaction_formats import restore_text
from hormuz.compaction_protocols import derive_selections
from hormuz.compaction_runtime import ContextPreferenceStore


COUNTERS = {"cl100k_base": len, "o200k_base": len}
ACCESS_TOKEN = "hox_a_" + "A" * 43
LOCAL_TOKEN = "hox_l_" + "B" * 43


class _Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.requests: list[tuple[bytes, dict[str, str]]] = []
        super().__init__(("127.0.0.1", 0), _GatewayHandler)


class _GatewayHandler(BaseHTTPRequestHandler):
    server: _Gateway
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"status":"ok"}'
        self.send_response(HTTPStatus.OK)
        self.send_header(CONTEXT_FORMATS_HEADER, CONTEXT_FORMAT_VERSION)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.requests.append((body, dict(self.headers.items())))
        response = b'{"ok":true}'
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response[:4])
        self.wfile.flush()
        self.wfile.write(response[4:])

    def log_message(self, format: str, *args: object) -> None:
        return


class _CodexToolGateway(_Gateway):
    def __init__(self) -> None:
        self.requests = []
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), _CodexToolGatewayHandler)


class _CodexToolGatewayHandler(_GatewayHandler):
    server: _CodexToolGateway

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = self.rfile.read(length)
        self.server.requests.append((body, dict(self.headers.items())))
        payload = json.loads(body)
        inputs = payload.get("input", [])
        completed_tool_output = any(
            isinstance(item, dict) and item.get("type") == "function_call_output"
            for item in inputs
        )
        events = (
            _codex_text_events(payload.get("model", "approved"))
            if completed_tool_output
            else _codex_tool_call_events(payload.get("model", "approved"))
        )
        response = "".join(
            f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
            for event in events
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("x-request-id", "req_context_client_conformance")
        self.end_headers()
        self.wfile.write(response)


def _codex_tool_call_events(model: str) -> list[dict[str, object]]:
    arguments = json.dumps({"cmd": "rg --files generated"}, separators=(",", ":"))
    in_progress_item = {
        "id": "fc_context_probe",
        "type": "function_call",
        "status": "in_progress",
        "arguments": "",
        "call_id": "call_context_probe",
        "name": "exec_command",
    }
    completed_item = {**in_progress_item, "status": "completed", "arguments": arguments}
    base = {
        "id": "resp_context_tool",
        "object": "response",
        "status": "in_progress",
        "model": model,
        "output": [],
        "usage": None,
    }
    return [
        {"type": "response.created", "response": base, "sequence_number": 0},
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": in_progress_item,
            "sequence_number": 1,
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "fc_context_probe",
            "output_index": 0,
            "delta": arguments,
            "sequence_number": 2,
        },
        {
            "type": "response.function_call_arguments.done",
            "item_id": "fc_context_probe",
            "output_index": 0,
            "arguments": arguments,
            "sequence_number": 3,
        },
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": completed_item,
            "sequence_number": 4,
        },
        {
            "type": "response.completed",
            "response": {
                **base,
                "status": "completed",
                "output": [completed_item],
                "usage": _codex_usage(),
            },
            "sequence_number": 5,
        },
    ]


def _codex_text_events(model: str) -> list[dict[str, object]]:
    content = {"type": "output_text", "text": "CONTEXT_OK", "annotations": [], "logprobs": []}
    in_progress_item = {
        "id": "msg_context_probe",
        "type": "message",
        "status": "in_progress",
        "role": "assistant",
        "content": [],
    }
    completed_item = {**in_progress_item, "status": "completed", "content": [content]}
    base = {
        "id": "resp_context_done",
        "object": "response",
        "status": "in_progress",
        "model": model,
        "output": [],
        "usage": None,
    }
    return [
        {"type": "response.created", "response": base, "sequence_number": 0},
        {"type": "response.output_item.added", "output_index": 0, "item": in_progress_item, "sequence_number": 1},
        {
            "type": "response.content_part.added",
            "item_id": "msg_context_probe",
            "output_index": 0,
            "content_index": 0,
            "part": {**content, "text": ""},
            "sequence_number": 2,
        },
        {
            "type": "response.output_text.delta",
            "item_id": "msg_context_probe",
            "output_index": 0,
            "content_index": 0,
            "delta": "CONTEXT_OK",
            "logprobs": [],
            "sequence_number": 3,
        },
        {
            "type": "response.output_text.done",
            "item_id": "msg_context_probe",
            "output_index": 0,
            "content_index": 0,
            "text": "CONTEXT_OK",
            "logprobs": [],
            "sequence_number": 4,
        },
        {
            "type": "response.content_part.done",
            "item_id": "msg_context_probe",
            "output_index": 0,
            "content_index": 0,
            "part": content,
            "sequence_number": 5,
        },
        {"type": "response.output_item.done", "output_index": 0, "item": completed_item, "sequence_number": 6},
        {
            "type": "response.completed",
            "response": {
                **base,
                "status": "completed",
                "output": [completed_item],
                "usage": _codex_usage(),
            },
            "sequence_number": 7,
        },
    ]


def _codex_usage() -> dict[str, object]:
    return {
        "input_tokens": 10,
        "output_tokens": 5,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": 15,
    }


class RelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / "state"
        self.state.mkdir(mode=0o700)
        self.store = ContextPreferenceStore(self.state, "profile-a")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _payload(self) -> dict[str, object]:
        paths = "".join(f"src/generated/file_{index % 20}.py\n" for index in range(160))
        return {"model": "approved", "input": [
            {"type": "function_call", "call_id": "records", "name": "exec_command",
             "arguments": json.dumps({"cmd": "rg --files src/generated"})},
            {"type": "function_call_output", "call_id": "records", "output": paths},
        ]}

    def test_loader_accepts_native_setup_and_rejects_invalid_setup_metadata(self) -> None:
        profile_key = str(uuid.uuid4())
        path = self.state / "profile.json"
        profile = {
            "id": profile_key,
            "gateway": "https://gateway.example.test",
            "organization": "org-a",
            "issuer": "https://issuer.example.test",
            "client": "codex",
            "model": "openai-primary",
            "allowLoopbackHTTP": False,
            "setup": "openai-pilot",
        }

        def write(value: dict[str, object]) -> None:
            path.write_text(json.dumps(value), encoding="utf-8")
            path.chmod(0o600)

        write(profile)
        loaded = load_saved_profile(self.state, profile_key)
        self.assertEqual((loaded.client, loaded.model), ("codex", "openai-primary"))

        legacy = dict(profile)
        legacy.pop("setup")
        write(legacy)
        self.assertEqual(load_saved_profile(self.state, profile_key), loaded)

        for setup in (None, "future", True, [], {}):
            write({**profile, "setup": setup})
            with self.subTest(setup=setup), self.assertRaisesRegex(
                ClientRelayError, "profile_invalid"
            ):
                load_saved_profile(self.state, profile_key)

        write({**profile, "client": "claude-code"})
        with self.assertRaisesRegex(ClientRelayError, "profile_invalid"):
            load_saved_profile(self.state, profile_key)

    def test_toggle_is_pinned_per_request_and_off_is_exact(self) -> None:
        optimizer = RelayOptimizer(
            preference_store=self.store, client="codex", gateway_compatible=True, counters=COUNTERS
        )
        original = b'{ "input" : [] }\n'
        changed, headers = optimizer.prepare(original, "/v1/responses")
        self.assertEqual((changed, headers, optimizer.status.code), (original, {}, "off"))

        self.store.save(True)
        body = json.dumps(self._payload()).encode()
        changed, headers = optimizer.prepare(body, "/v1/responses")
        self.assertNotEqual(changed, body)
        self.assertEqual(headers, {CONTEXT_FORMAT_HEADER: CONTEXT_FORMAT_VERSION})
        changed_payload = json.loads(changed)
        original_payload = json.loads(body)
        self.assertEqual(
            restore_text(changed_payload["input"][1]["output"]),
            original_payload["input"][1]["output"],
        )
        self.store.save(False)
        self.assertEqual(optimizer.prepare(original, "/v1/responses"), (original, {}))

    def test_launcher_scrubs_direct_provider_credentials_and_preserves_client_tools(self) -> None:
        codex_profile = SavedClientProfile(
            key="profile-a", gateway="https://gateway.example", client="codex",
            model="approved", allow_insecure_http=False,
        )
        claude_profile = SavedClientProfile(
            key="profile-a", gateway="https://gateway.example", client="claude-code",
            model="approved", allow_insecure_http=False,
        )
        inherited = {
            "OPENAI_API_KEY": "openai-direct",
            "OPENAI_BASE_URL": "https://direct-openai.example",
            "CODEX_API_KEY": "codex-direct",
            "ANTHROPIC_API_KEY": "anthropic-direct",
            "CLAUDE_CODE_OAUTH_TOKEN": "claude-direct",
            "TOOL_INTEGRATION_TOKEN": "client-owned-tool-token",
        }
        with mock.patch.dict(os.environ, inherited, clear=True):
            _, codex_environment = _client_command(
                codex_profile, "http://127.0.0.1:9876", LOCAL_TOKEN,
                executable="/usr/bin/codex",
            )
            _, claude_environment = _client_command(
                claude_profile, "http://127.0.0.1:9876", LOCAL_TOKEN,
                executable="/usr/bin/claude",
            )
        self.assertNotIn("OPENAI_API_KEY", codex_environment)
        self.assertNotIn("OPENAI_BASE_URL", codex_environment)
        self.assertNotIn("CODEX_API_KEY", codex_environment)
        self.assertEqual(codex_environment["HORMUZ_LOCAL_RELAY_TOKEN"], LOCAL_TOKEN)
        self.assertEqual(codex_environment["TOOL_INTEGRATION_TOKEN"], "client-owned-tool-token")
        self.assertEqual(claude_environment["ANTHROPIC_API_KEY"], "")
        self.assertNotEqual(claude_environment["ANTHROPIC_API_KEY"], "anthropic-direct")
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", claude_environment)
        self.assertEqual(claude_environment["ANTHROPIC_AUTH_TOKEN"], LOCAL_TOKEN)
        self.assertEqual(claude_environment["TOOL_INTEGRATION_TOKEN"], "client-owned-tool-token")

    def test_incompatible_gateway_and_invalid_settings_fail_open_to_original(self) -> None:
        self.store.save(True)
        body = json.dumps(self._payload()).encode()
        incompatible = RelayOptimizer(
            preference_store=self.store, client="codex", gateway_compatible=False, counters=COUNTERS
        )
        self.assertEqual(incompatible.prepare(body, "/v1/responses"), (body, {}))
        self.assertEqual(incompatible.status.code, "gateway_incompatible")
        self.store.path.write_text('{"schema_version":1,"enabled":true,"extra":1}')
        self.store.path.chmod(0o600)
        self.assertEqual(incompatible.prepare(body, "/v1/responses"), (body, {}))
        self.assertEqual(incompatible.status.code, "settings_invalid")

    def test_loopback_relay_replaces_auth_preserves_off_bytes_and_streams_response(self) -> None:
        gateway = _Gateway()
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        optimizer = RelayOptimizer(
            preference_store=self.store, client="codex", gateway_compatible=True, counters=COUNTERS
        )
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{gateway.server_port}", client="codex",
            local_credential=LOCAL_TOKEN, gateway_credential=lambda: ACCESS_TOKEN, optimizer=optimizer,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            original = b'{ "model" : "approved", "input" : [] }\n'
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            connection.request("POST", "/v1/responses", body=original, headers={
                "Authorization": "Bearer " + LOCAL_TOKEN, "Content-Type": "application/json"
            })
            response = connection.getresponse()
            self.assertEqual((response.status, response.read()), (200, b'{"ok":true}'))
            connection.close()
            self.assertEqual(gateway.requests[0][0], original)
            headers = {key.lower(): value for key, value in gateway.requests[0][1].items()}
            self.assertEqual(headers["authorization"], "Bearer " + ACCESS_TOKEN)
            self.assertNotIn(CONTEXT_FORMAT_HEADER.lower(), headers)

            denied = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
            denied.request("POST", "/v1/responses", body=b"{}", headers={"Authorization": "Bearer wrong"})
            denied_response = denied.getresponse()
            self.assertEqual(denied_response.status, 401)
            denied_response.read()
            denied.close()
            self.assertEqual(len(gateway.requests), 1)

            duplicated_host = http.client.HTTPConnection(
                "127.0.0.1", relay.server_port, timeout=5
            )
            duplicated_host.putrequest("POST", "/v1/responses", skip_host=True)
            duplicated_host.putheader("Host", f"127.0.0.1:{relay.server_port}")
            duplicated_host.putheader("Host", "attacker.invalid")
            duplicated_host.putheader("Authorization", "Bearer " + LOCAL_TOKEN)
            duplicated_host.putheader("Content-Type", "application/json")
            duplicated_host.putheader("Content-Length", "2")
            duplicated_host.endheaders(b"{}")
            duplicate_response = duplicated_host.getresponse()
            self.assertEqual(duplicate_response.status, 403)
            duplicate_response.read()
            duplicated_host.close()
            self.assertEqual(len(gateway.requests), 1)
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            gateway.shutdown()
            gateway_thread.join(timeout=2)
            gateway.server_close()

    def test_shutdown_interrupts_blocked_upstream_read(self) -> None:
        for response_started in (False, True):
            with self.subTest(response_started=response_started):
                self._assert_shutdown_interrupts_blocked_upstream_read(response_started)

    def _assert_shutdown_interrupts_blocked_upstream_read(self, response_started: bool) -> None:
        gateway = socket.socket()
        gateway.bind(("127.0.0.1", 0))
        gateway.listen(1)
        gateway.settimeout(5)
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{gateway.getsockname()[1]}", client="codex",
            local_credential=LOCAL_TOKEN, gateway_credential=lambda: ACCESS_TOKEN,
            optimizer=RelayOptimizer(preference_store=self.store, client="codex", gateway_compatible=False),
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        client = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
        upstream = None
        try:
            client.request("POST", "/v1/responses", body=b"{}", headers={
                "Authorization": "Bearer " + LOCAL_TOKEN,
            })
            upstream, _ = gateway.accept()
            upstream.settimeout(5)
            request = b""
            while b"\r\n\r\n{}" not in request:
                request += upstream.recv(8192)
            self.assertTrue(request.startswith(b"POST /v1/responses"))
            if response_started:
                # Connection: close makes http.client clear connection.sock
                # after headers, while the handler still reads the response.
                upstream.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\npartial"
                )
                self.assertEqual(client.getresponse().status, 200)
            client.close()
            relay.shutdown()
            relay_thread.join(timeout=2)
            self.assertFalse(relay_thread.is_alive())
            upstream.settimeout(1)
            try:
                self.assertEqual(upstream.recv(1), b"")
            except (ConnectionResetError, ConnectionAbortedError):
                pass
            with relay._upstream_condition:
                self.assertFalse(relay._upstreams)
            self.assertFalse(relay._register_upstream(http.client.HTTPConnection("127.0.0.1", 1)))
        finally:
            client.close()
            if upstream is not None:
                upstream.close()
            if relay_thread.is_alive():
                relay.shutdown()
                relay_thread.join(timeout=2)
            relay.server_close()
            gateway.close()

    def test_shutdown_during_connect_sends_no_late_post(self) -> None:
        gateway = socket.socket()
        gateway.bind(("127.0.0.1", 0))
        gateway.listen(1)
        gateway.settimeout(5)
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{gateway.getsockname()[1]}", client="codex",
            local_credential=LOCAL_TOKEN, gateway_credential=lambda: ACCESS_TOKEN,
            optimizer=RelayOptimizer(preference_store=self.store, client="codex", gateway_compatible=False),
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        about_to_connect = threading.Event()
        release_connect = threading.Event()
        original_factory = relay_module._gateway_connection

        def delayed_connection(origin: str, timeout: float) -> http.client.HTTPConnection:
            connection = original_factory(origin, timeout)
            connect = connection.connect

            def wait_then_connect() -> None:
                about_to_connect.set()
                if not release_connect.wait(5):
                    raise TimeoutError("test did not release the gateway connect")
                connect()

            connection.connect = wait_then_connect
            return connection

        client = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
        upstream = None
        shutdown_thread = None
        try:
            with mock.patch("hormuz.client_relay._gateway_connection", side_effect=delayed_connection):
                client.request("POST", "/v1/responses", body=b"{}", headers={
                    "Authorization": "Bearer " + LOCAL_TOKEN,
                })
                self.assertTrue(about_to_connect.wait(5))
                shutdown_thread = threading.Thread(target=relay.shutdown)
                shutdown_thread.start()
                with relay._upstream_condition:
                    self.assertTrue(relay._upstream_condition.wait_for(lambda: relay._stopping, timeout=5))
                release_connect.set()
                shutdown_thread.join(timeout=5)
                self.assertFalse(shutdown_thread.is_alive())
            upstream, _ = gateway.accept()
            upstream.settimeout(1)
            try:
                self.assertEqual(upstream.recv(1), b"")
            except (ConnectionResetError, ConnectionAbortedError):
                pass
            with relay._upstream_condition:
                self.assertFalse(relay._upstreams)
        finally:
            release_connect.set()
            client.close()
            if upstream is not None:
                upstream.close()
            if shutdown_thread is not None:
                shutdown_thread.join(timeout=6)
            if relay_thread.is_alive():
                relay.shutdown()
                relay_thread.join(timeout=2)
            relay.server_close()
            gateway.close()

    def test_shutdown_interrupts_https_response_read(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=5))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        certificate_path = self.state / "fake-gateway.crt"
        key_path = self.state / "fake-gateway.key"
        certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        key_path.chmod(0o600)
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(str(certificate_path), str(key_path))
        client_context = ssl.create_default_context(cafile=str(certificate_path))
        gateway = socket.socket()
        gateway.bind(("127.0.0.1", 0))
        gateway.listen(1)
        gateway.settimeout(5)
        relay = LocalRelayServer(
            gateway=f"https://127.0.0.1:{gateway.getsockname()[1]}", client="codex",
            local_credential=LOCAL_TOKEN, gateway_credential=lambda: ACCESS_TOKEN,
            optimizer=RelayOptimizer(preference_store=self.store, client="codex", gateway_compatible=False),
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        client = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
        upstream = None
        try:
            with mock.patch.object(relay_module.ssl, "create_default_context", return_value=client_context):
                client.request("POST", "/v1/responses", body=b"{}", headers={
                    "Authorization": "Bearer " + LOCAL_TOKEN,
                })
                raw, _ = gateway.accept()
                upstream = server_context.wrap_socket(raw, server_side=True)
                upstream.settimeout(5)
                request = b""
                while b"\r\n\r\n{}" not in request:
                    request += upstream.recv(8192)
                self.assertTrue(request.startswith(b"POST /v1/responses"))
                upstream.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\npartial"
                )
                self.assertEqual(client.getresponse().status, 200)
                client.close()
                relay.shutdown()
            relay_thread.join(timeout=2)
            self.assertFalse(relay_thread.is_alive())
            upstream.settimeout(1)
            try:
                self.assertEqual(upstream.recv(1), b"")
            except (ssl.SSLError, ConnectionResetError, ConnectionAbortedError):
                pass
            with relay._upstream_condition:
                self.assertFalse(relay._upstreams)
        finally:
            client.close()
            if upstream is not None:
                upstream.close()
            if relay_thread.is_alive():
                relay.shutdown()
                relay_thread.join(timeout=2)
            relay.server_close()
            gateway.close()

    def test_uncertain_gateway_post_is_not_replayed(self) -> None:
        gateway = socket.socket()
        gateway.bind(("127.0.0.1", 0))
        gateway.listen(2)
        gateway.settimeout(5)
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{gateway.getsockname()[1]}", client="codex",
            local_credential=LOCAL_TOKEN, gateway_credential=lambda: ACCESS_TOKEN,
            optimizer=RelayOptimizer(preference_store=self.store, client="codex", gateway_compatible=False),
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        client = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=5)
        try:
            client.request("POST", "/v1/responses", body=b"{}", headers={
                "Authorization": "Bearer " + LOCAL_TOKEN,
            })
            upstream, _ = gateway.accept()
            with upstream:
                upstream.settimeout(5)
                request = b""
                while b"\r\n\r\n{}" not in request:
                    request += upstream.recv(8192)
                self.assertTrue(request.startswith(b"POST /v1/responses"))
                # The gateway may have committed the request before disconnect.
            try:
                response = client.getresponse()
            except (http.client.RemoteDisconnected, ConnectionResetError):
                pass  # The existing relay closes a reset upstream without replay.
            else:
                self.assertEqual(response.status, 502)
                response.read()
            gateway.settimeout(0.25)
            with self.assertRaises(socket.timeout):
                gateway.accept()
        finally:
            client.close()
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            gateway.close()

    def test_oversized_eligible_request_uses_exact_streaming_passthrough(self) -> None:
        gateway = _Gateway()
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        self.store.save(True)
        optimizer = RelayOptimizer(
            preference_store=self.store, client="codex", gateway_compatible=True, counters=COUNTERS
        )
        relay = LocalRelayServer(
            gateway=f"http://127.0.0.1:{gateway.server_port}", client="codex",
            local_credential=LOCAL_TOKEN, gateway_credential=lambda: ACCESS_TOKEN, optimizer=optimizer,
        )
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        relay_thread.start()
        try:
            original = b"x" * (MAX_REQUEST_BYTES + 1)
            connection = http.client.HTTPConnection("127.0.0.1", relay.server_port, timeout=10)
            connection.request("POST", "/v1/responses", body=original, headers={
                "Authorization": "Bearer " + LOCAL_TOKEN, "Content-Type": "application/json"
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.read()
            connection.close()
            self.assertEqual(gateway.requests[0][0], original)
            self.assertEqual(optimizer.status.code, "unsupported_history")
            self.store.save(False)
            optimizer.note_oversized_passthrough()
            self.assertEqual(optimizer.status.code, "off")
        finally:
            relay.shutdown()
            relay_thread.join(timeout=2)
            relay.server_close()
            gateway.shutdown()
            gateway_thread.join(timeout=2)
            gateway.server_close()

    def test_supported_codex_tool_shape_changes_through_launched_client(self) -> None:
        gateway = _Gateway()
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        self.store.save(True)
        bin_directory = Path(self.temporary.name) / "bin"
        bin_directory.mkdir()
        credential = bin_directory / "credential-helper"
        credential.write_text("#!/bin/sh\nprintf '%s\\n' '" + ACCESS_TOKEN + "'\n", encoding="utf-8")
        credential.chmod(0o700)
        codex = bin_directory / "codex"
        # GitHub's Linux runner blocks the nested bubblewrap namespace that Codex's
        # read-only sandbox requires. The outer job already isolates this synthetic,
        # disposable fixture, so bypass only the nested client sandbox here.
        codex.write_text(
            """#!/usr/bin/env python3
import http.client, json, os, re, sys, urllib.parse
if sys.argv[1:] == ['--version']:
    print('codex-cli 0.147.0')
    raise SystemExit(0)
provider = next(value for value in sys.argv if value.startswith('model_providers.hormuz_context_relay='))
origin = re.search(r'base_url=\"([^\"]+)/v1\"', provider).group(1)
url = urllib.parse.urlsplit(origin)
paths = ''.join(f'src/generated/file_{index % 20}.py\\n' for index in range(180))
payload = {'model': 'approved', 'input': [
    {'type': 'function_call', 'call_id': 'actual-rg', 'name': 'exec_command',
     'arguments': json.dumps({'cmd': 'rg --files src/generated'})},
    {'type': 'function_call_output', 'call_id': 'actual-rg', 'output': paths},
]}
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
connection.request('POST', '/v1/responses', body=json.dumps(payload).encode(), headers={
    'Authorization': 'Bearer ' + os.environ['HORMUZ_LOCAL_RELAY_TOKEN'],
    'Content-Type': 'application/json',
})
response = connection.getresponse()
response.read()
raise SystemExit(0 if response.status == 200 else 1)
""",
            encoding="utf-8",
        )
        codex.chmod(0o700)
        profile = SavedClientProfile(
            key="profile-a",
            gateway=f"http://127.0.0.1:{gateway.server_port}",
            client="codex",
            model="approved",
            allow_insecure_http=True,
        )
        old_path = os.environ.get("PATH")
        os.environ["PATH"] = str(bin_directory) + (":" + old_path if old_path else "")
        try:
            self.assertEqual(
                run_client(
                    profile=profile,
                    state_directory=self.state,
                    credential_helper=credential,
                    counters=COUNTERS,
                ),
                0,
            )
            self.assertEqual(len(gateway.requests), 1)
            body, request_headers = gateway.requests[0]
            headers = {key.lower(): value for key, value in request_headers.items()}
            self.assertEqual(headers[CONTEXT_FORMAT_HEADER.lower()], CONTEXT_FORMAT_VERSION)
            payload = json.loads(body)
            compact = payload["input"][1]["output"]
            self.assertIn("hormuz-path-list-v1", compact)
            self.assertEqual(
                restore_text(compact),
                "".join(f"src/generated/file_{index % 20}.py\n" for index in range(180)),
            )
        finally:
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
            gateway.shutdown()
            gateway_thread.join(timeout=2)
            gateway.server_close()

    @unittest.skipUnless(
        os.environ.get("HORMUZ_RUN_CONTEXT_CLIENT_TEST") == "1" and shutil.which("codex"),
        "Set HORMUZ_RUN_CONTEXT_CLIENT_TEST=1 and install Codex CLI 0.147.0",
    )
    def test_official_codex_exec_command_output_is_compacted_through_relay(self) -> None:
        real_codex = shutil.which("codex")
        assert real_codex is not None
        gateway = _CodexToolGateway()
        gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        gateway_thread.start()
        profile_key = "10c577a9-41e7-4d16-aed8-5f29da33ff52"
        preference_store = ContextPreferenceStore(self.state, profile_key)
        preference_store.save(True)
        root = Path(self.temporary.name) / "client-root"
        generated = root / "generated" / "structural_context_repetition_for_hormuz"
        generated.mkdir(parents=True)
        for index in range(48):
            (generated / f"file_{index:03}.py").write_text("# context probe\n", encoding="utf-8")
        bin_directory = Path(self.temporary.name) / "actual-client-bin"
        bin_directory.mkdir()
        credential = bin_directory / "credential-helper"
        credential.write_text("#!/bin/sh\nprintf '%s\\n' '" + ACCESS_TOKEN + "'\n", encoding="utf-8")
        credential.chmod(0o700)
        codex = bin_directory / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"real = {real_codex!r}\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    os.execv(real, [real, '--version'])\n"
            f"root = {str(root)!r}\n"
            "arguments = [real, 'exec', '--ignore-user-config', '--skip-git-repo-check', "
            "'--ephemeral', '--dangerously-bypass-approvals-and-sandbox', '-C', root, *sys.argv[1:], "
            "'Call exec_command with exactly `rg --files generated`, then finish.']\n"
            "os.execv(real, arguments)\n",
            encoding="utf-8",
        )
        codex.chmod(0o700)
        profile = SavedClientProfile(
            key=profile_key,
            gateway=f"http://127.0.0.1:{gateway.server_port}",
            client="codex",
            model="approved",
            allow_insecure_http=True,
        )
        (self.state / "profile.json").write_text(
            json.dumps(
                {
                    "id": profile.key,
                    "gateway": profile.gateway,
                    "organization": "context-client-test",
                    "client": profile.client,
                    "model": profile.model,
                    "allowLoopbackHTTP": True,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        (self.state / "profile.json").chmod(0o600)
        old_path = os.environ.get("PATH")
        os.environ["PATH"] = str(bin_directory) + (":" + old_path if old_path else "")
        try:
            packaged_helper = os.environ.get("HORMUZ_PACKAGED_CONTEXT_HELPER")
            def launch() -> tuple[int, str]:
                if packaged_helper:
                    completed = subprocess.run(
                        [
                            packaged_helper,
                            "context",
                            "run",
                            "--profile",
                            profile.key,
                            "--state-directory",
                            str(self.state),
                            "--credential-helper",
                            str(credential),
                        ],
                        env=os.environ.copy(),
                        capture_output=True,
                        text=True,
                        timeout=45,
                        check=False,
                    )
                    return completed.returncode, (
                        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                    )
                return (
                    run_client(
                        profile=profile,
                        state_directory=self.state,
                        credential_helper=credential,
                        counters=COUNTERS,
                    ),
                    "source helper returned a nonzero status",
                )

            result, failure_detail = launch()
            self.assertEqual(result, 0, msg=failure_detail)
            decoded_requests = [json.loads(body) for body, _headers in gateway.requests]
            output_requests = [
                (payload, headers)
                for payload, (_body, headers) in zip(decoded_requests, gateway.requests, strict=True)
                if any(
                    isinstance(item, dict) and item.get("type") == "function_call_output"
                    for item in payload.get("input", [])
                )
            ]
            self.assertEqual(len(output_requests), 1)
            payload, request_headers = output_requests[0]
            headers = {key.lower(): value for key, value in request_headers.items()}
            diagnostic_selections = derive_selections(payload, "responses", client="codex")
            diagnostic_result = optimize_request(
                payload,
                "responses",
                diagnostic_selections,
                COUNTERS,
                enabled=True,
            )
            diagnostic_outputs = [
                item.get("output")
                for item in payload.get("input", [])
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            ]
            diagnostic_output = diagnostic_outputs[0] if len(diagnostic_outputs) == 1 else None
            shape = {
                "payload_keys": sorted(payload),
                "input_types": [
                    item.get("type") if isinstance(item, dict) else type(item).__name__
                    for item in payload.get("input", [])
                ],
                "call_names": [
                    item.get("name")
                    for item in payload.get("input", [])
                    if isinstance(item, dict) and item.get("type") == "function_call"
                ],
                "has_previous_response_id": "previous_response_id" in payload,
                "selection_formats": [selection.format for selection in diagnostic_selections],
                "optimizer_reason": diagnostic_result.reason,
                "output_bytes": (
                    len(diagnostic_output.encode("utf-8"))
                    if isinstance(diagnostic_output, str)
                    else None
                ),
                "output_lines": (
                    diagnostic_output.count("\n")
                    if isinstance(diagnostic_output, str)
                    else None
                ),
                "has_first_fixture_path": (
                    "generated/structural_context_repetition_for_hormuz/file_000.py"
                    in diagnostic_output
                    if isinstance(diagnostic_output, str)
                    else False
                ),
                "has_last_fixture_path": (
                    "generated/structural_context_repetition_for_hormuz/file_047.py"
                    in diagnostic_output
                    if isinstance(diagnostic_output, str)
                    else False
                ),
                "relay_status": headers.get(CONTEXT_FORMAT_HEADER.lower()),
            }
            self.assertEqual(
                headers.get(CONTEXT_FORMAT_HEADER.lower()),
                CONTEXT_FORMAT_VERSION,
                msg=json.dumps(shape, sort_keys=True),
            )
            output_item = next(
                item
                for item in payload["input"]
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            )
            compact = output_item["output"]
            self.assertIsInstance(compact, str)
            self.assertIn("hormuz-path-list-v1", compact)
            restored = restore_text(compact)
            self.assertIn("Chunk ID:", restored)
            self.assertIn("Process exited with code 0", restored)
            self.assertIn(
                "generated/structural_context_repetition_for_hormuz/file_000.py", restored
            )
            self.assertIn(
                "generated/structural_context_repetition_for_hormuz/file_047.py", restored
            )

            preference_store.save(False)
            gateway.requests.clear()
            result, failure_detail = launch()
            self.assertEqual(result, 0, msg=failure_detail)
            decoded_requests = [json.loads(body) for body, _headers in gateway.requests]
            off_requests = [
                (payload, headers)
                for payload, (_body, headers) in zip(decoded_requests, gateway.requests, strict=True)
                if any(
                    isinstance(item, dict) and item.get("type") == "function_call_output"
                    for item in payload.get("input", [])
                )
            ]
            self.assertEqual(len(off_requests), 1)
            off_payload, off_headers = off_requests[0]
            self.assertNotIn(
                CONTEXT_FORMAT_HEADER.lower(),
                {key.lower(): value for key, value in off_headers.items()},
            )
            off_output = next(
                item["output"]
                for item in off_payload["input"]
                if isinstance(item, dict) and item.get("type") == "function_call_output"
            )
            self.assertNotIn("hormuz-path-list-v1", off_output)
            self.assertIn("Chunk ID:", off_output)
            self.assertIn(
                "generated/structural_context_repetition_for_hormuz/file_047.py", off_output
            )
        finally:
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path
            gateway.shutdown()
            gateway_thread.join(timeout=2)
            gateway.server_close()

    def test_client_version_must_match_the_qualified_release(self) -> None:
        bin_directory = Path(self.temporary.name) / "version-bin"
        bin_directory.mkdir()
        codex = bin_directory / "codex"
        codex.write_text("#!/bin/sh\necho 'codex-cli 9.9.9'\n", encoding="utf-8")
        codex.chmod(0o700)
        old_path = os.environ.get("PATH")
        os.environ["PATH"] = str(bin_directory)
        try:
            with self.assertRaisesRegex(ClientRelayError, "unsupported_client"):
                supported_client_executable("codex")
        finally:
            if old_path is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()
