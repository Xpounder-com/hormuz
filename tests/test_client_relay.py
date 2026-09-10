from __future__ import annotations

import http.client
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from hormuz.client_relay import (
    ClientRelayError,
    LocalRelayServer,
    RelayOptimizer,
    SavedClientProfile,
    _client_command,
    run_client,
    supported_client_executable,
)
from hormuz.compaction import MAX_REQUEST_BYTES
from hormuz.compaction_enforcement import (
    CONTEXT_FORMAT_HEADER,
    CONTEXT_FORMAT_VERSION,
    CONTEXT_FORMATS_HEADER,
)
from hormuz.compaction_formats import restore_text
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
        rg = bin_directory / "rg"
        rg.write_text(
            "#!/bin/sh\n"
            "test \"$1\" = --files || exit 2\n"
            "exec /usr/bin/find \"$2\" -type f\n",
            encoding="utf-8",
        )
        rg.chmod(0o700)
        codex = bin_directory / "codex"
        codex.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            f"real = {real_codex!r}\n"
            "if sys.argv[1:] == ['--version']:\n"
            "    os.execv(real, [real, '--version'])\n"
            f"root = {str(root)!r}\n"
            "arguments = [real, 'exec', '--ignore-user-config', '--skip-git-repo-check', "
            "'--ephemeral', '--sandbox', 'read-only', '-C', root, *sys.argv[1:], "
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
            shape = {
                "payload_keys": sorted(payload),
                "input_types": [
                    item.get("type") if isinstance(item, dict) else type(item).__name__
                    for item in payload.get("input", [])
                ],
                "has_previous_response_id": "previous_response_id" in payload,
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
