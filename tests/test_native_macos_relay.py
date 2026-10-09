"""Mac Rust launch integration, with synthetic credentials and loopback only."""
from __future__ import annotations

import json
from contextlib import closing, ExitStack
from dataclasses import replace
import os
import selectors
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit

from tests.test_client_relay import (
    ACCESS_TOKEN, _CodexToolGateway, _CodexToolGatewayHandler, _Gateway, _GatewayHandler,
    _codex_text_events,
)
from tests.test_gateway import FakeProviderHandler
from hormuz.config import GatewayConfig
from hormuz.server import GatewayRequestHandler
from hormuz.work_client import WorkClient, WorkClientError
from tools.ai_work_proof import ProofGatewayServer, ProofProviderServer, serving
from tools.ai_work_provider_examples import gateway_profile
from tools.provider_example_transport import LoopbackTransport


def _record_native_helper(root, kind):
    return ('import os\n'
        + f'open({str(root)!r} + "/native-helper-" + str(os.getpid()) + ".json", "w").write('
        + 'json.dumps({"pid": os.getpid(), "pgid": os.getpgid(0), "ppid": os.getppid(), '
        + f'"kind": {kind!r}' + '}))\n')


def _group_absent(group):
    # Signal zero observes only; never terminate a reaped/reused numeric ID.
    for _ in range(20):
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


class _WorkTerminalProvider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_POST(self) -> None:  # noqa: N802
        fixture = self.server.fixture
        try:
            fixture.count_http()
            path = urlsplit(self.path).path
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024 * 1024:
                raise ValueError("fixture_body_limit")
            body = json.loads(self.rfile.read(size))
            with fixture.lock:
                if path == "/v1/messages/count_tokens":
                    fixture.token_counts += 1
                    if fixture.token_counts > 2:
                        raise ValueError("fixture_token_count_limit")
                    raw, content_type = b'{"input_tokens":24}', "application/json"
                else:
                    if path not in {"/v1/responses", "/v1/messages"} or body.get("stream") is not True:
                        raise ValueError("fixture_unexpected_endpoint")
                    if len(fixture.provider_calls) >= 3:
                        raise ValueError("fixture_inference_limit")
                    if any(item.get("type") == "function_call_output" for item in body.get("input", [])
                           if isinstance(item, dict)):
                        raise ValueError("fixture_unexpected_tool_action")
                    fixture.provider_calls.append({"path": path, "model": body["model"],
                        "work_headers": self.headers.get_all("X-Hormuz-Work-Id", []),
                        "client_credential_present": any(value in str(self.headers) for value in
                            (ACCESS_TOKEN, "hox_a_" + "B" * 43, "synthetic-direct-key-must-not-reach-client")),
                        "authority_header": any(name.lower().startswith(("x-hormuz-actor", "x-hormuz-organization",
                                                                         "x-hormuz-team", "x-hormuz-role"))
                                                for name in self.headers),
                        "configured_provider_auth": (self.headers.get("Authorization") == "Bearer synthetic-provider-key"
                            if path == "/v1/responses" else self.headers.get("x-api-key") == "synthetic-provider-key")})
                    if path == "/v1/responses":
                        events = [(event["type"], event) for event in _codex_text_events(body["model"])]
                    else:
                        message = {"id": "msg_native_work", "type": "message", "role": "assistant",
                            "model": body["model"], "content": [], "stop_reason": None,
                            "stop_sequence": None, "usage": {"input_tokens": 24, "output_tokens": 0}}
                        events = [("message_start", {"message": message}),
                            ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
                            ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "42"}}),
                            ("content_block_stop", {"index": 0}),
                            ("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None},
                                               "usage": {"output_tokens": 8}}), ("message_stop", {})]
                        events = [(kind, {"type": kind, **value}) for kind, value in events]
                    raw = "".join("event: " + kind + "\ndata: " + json.dumps(value) + "\n\n"
                                  for kind, value in events).encode()
                    content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except Exception:
            fixture.failed = True
            self.close_connection = True
            self.send_error(503, "fixture refused")


class _WorkRecordingHandler(GatewayRequestHandler):
    def log_message(self, *_):
        pass

    def _record(self):
        fixture = self.server.fixture
        fixture.count_http()
        path = urlsplit(self.path).path
        if path in {"/v1/responses", "/v1/messages", "/v1/messages/count_tokens"}:
            fixture.gateway_calls.append({"path": path,
                "work_headers": self.headers.get_all("X-Hormuz-Work-Id", []),
                "owner_auth": self.headers.get("Authorization") == "Bearer " + ACCESS_TOKEN,
                "authority_header": any(name.lower().startswith(("x-hormuz-actor", "x-hormuz-organization",
                                                                 "x-hormuz-team", "x-hormuz-role"))
                                        for name in self.headers)})

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        super().do_POST()


class _NativeWorkFixture:
    """Two owned bounded listeners; real runtime/SQLite, synthetic terminal replies."""
    def __init__(self, root):
        self.lock = threading.Lock()
        self.http_requests = self.token_counts = 0
        self.failed = False
        self.provider_calls, self.gateway_calls = [], []
        self.expected_work = {}
        self.initial_threads = set(threading.enumerate())
        self.stack = ExitStack()
        try:
            self.provider = ProofProviderServer(("127.0.0.1", 0), _WorkTerminalProvider)
            self.provider.fixture = self
            self.stack.enter_context(serving(self.provider))
            routes = {alias: {"protocol": protocol, "upstream_model": "synthetic-" + protocol,
                      "input_cost_per_million": 3, "output_cost_per_million": 3}
                      for alias, protocol in (("approved", "openai"), ("claude-sonnet-5", "anthropic"))}
            upstreams = {protocol: {"base_url": f"http://127.0.0.1:{self.provider.server_port}",
                                   "api_key_env": "NATIVE_WORK_PROVIDER"}
                         for protocol in ("openai", "anthropic")}
            profile = gateway_profile(routes, upstreams, live=True)
            profile["upstream_timeout_seconds"] = 2
            profile["max_request_bytes"] = 1024 * 1024
            profile["policies"]["organization"]["fallback_models"] = {
                "openai": "approved", "anthropic": "claude-sonnet-5"}
            profile["identities"] = [{"token_env": variable, "actor_id": actor,
                "actor_name": "Synthetic actor", "team_id": "fixture", "team_name": "Fixture",
                "organization_id": "org-a", "allowed_clients": ["codex", "claude-code"]}
                for variable, actor in (("NATIVE_WORK_OWNER", "native-owner"), ("NATIVE_WORK_OTHER", "native-other"))]
            profile["ai_work"]["administrator_actor_ids"] = ["native-owner"]
            path = root / "work-gateway.json"
            path.write_text(json.dumps(profile))
            environment = {"NATIVE_WORK_OWNER": ACCESS_TOKEN, "NATIVE_WORK_OTHER": "hox_a_" + "B" * 43,
                           "NATIVE_WORK_PROVIDER": "synthetic-provider-key"}
            self.config = GatewayConfig.load(path, environ=environment)
            self.config = replace(self.config, listen=replace(self.config.listen, port=0))
            self.gateway = ProofGatewayServer(self.config, environ=environment)
            self.gateway.fixture = self
            self.gateway.RequestHandlerClass = _WorkRecordingHandler
            self.stack.enter_context(serving(self.gateway))
            self.transport = self.stack.enter_context(LoopbackTransport(self.gateway.server_port))
            self.client = WorkClient(self.transport.endpoint, ACCESS_TOKEN, allow_loopback_http=True, timeout=5)
            self.client._opener = self.transport
            self.other = WorkClient(self.transport.endpoint, environment["NATIVE_WORK_OTHER"],
                                    allow_loopback_http=True, timeout=5)
            self.other._opener = self.transport
            self.client.set_plan("workspace", "org-a", budget_microusd=1_000_000,
                                 objective="cost", exploration_enabled=False)
        except BaseException:
            self.stack.close()
            raise

    def count_http(self):
        with self.lock:
            self.http_requests += 1
            if self.http_requests > 40:
                self.failed = True
                raise ValueError("fixture_http_limit")

    def job(self, label, *, model="approved"):
        work_id = self.client.create_job("qualification/native-work", title=label,
            task_type="terminal-reply", context_revision="native-work-v1")["work_id"]
        self.client.set_plan("job", work_id, budget_microusd=1_000_000, objective="cost")
        self.expected_work[work_id] = (model, "openai" if model == "approved" else "anthropic",
                                      45 if model == "approved" else 96)
        return work_id

    def assert_job(self, test, work_id):
        state = self.client.job(work_id).state()
        test.assertEqual((state["state"], state["observations"], state["outcome_evidence"]),
                         ("active", [], "unknown"))
        test.assertIsNone(state["completed_at"])
        test.assertEqual((state["organization_id"], state["actor_id"], state["repository"]),
                         ("org-a", "native-owner", "qualification/native-work"))
        test.assertEqual(len(state["attempts"]), 1)
        attempt = state["attempts"][0]
        test.assertEqual((attempt["state"], attempt["response_succeeded"]),
                         ("succeeded", True))
        model, protocol, cost = self.expected_work[work_id]
        test.assertEqual((attempt["model"], attempt["protocol"], attempt["cost_microusd"]),
                         (model, protocol, cost))
        test.assertGreaterEqual(attempt["reserved_microusd"], cost)
        test.assertLessEqual(attempt["reserved_microusd"], 1_000_000)

    def assert_refused_ownership(self, test, work_id):
        before = len(self.provider_calls)
        for client, target in ((self.other, work_id), (self.client, "unknown-native-work")):
            with test.assertRaises(WorkClientError) as failure:
                client.job(target).state()
            test.assertEqual((failure.exception.status, failure.exception.reason), (404, "work_not_found"))
            token = client._credential
            status, _, raw = self.transport.request("POST", "/v1/responses",
                json.dumps({"model": "approved", "input": "synthetic", "max_output_tokens": 32, "stream": True}),
                {"Authorization": "Bearer " + token, "Content-Type": "application/json", "X-Hormuz-Work-Id": target},
                timeout=5)
            test.assertEqual((status, json.loads(raw)["error"]["code"]), (404, "hormuz_ai_work_work_not_found"))
        test.assertEqual(len(self.provider_calls), before)

    def close(self):
        self.stack.close()

    def assert_closed_ledger(self, test, work_ids):
        test.assertEqual((self.provider.fileno(), self.gateway.fileno()), (-1, -1))
        test.assertFalse(set(threading.enumerate()) - self.initial_threads)
        test.assertFalse(self.failed)
        test.assertEqual(len(self.provider_calls), 3)
        test.assertTrue(all(row["work_headers"] == [] and not row["client_credential_present"]
                            and not row["authority_header"] and row["configured_provider_auth"]
                            for row in self.provider_calls))
        test.assertEqual([(row["path"], row["model"]) for row in self.provider_calls],
            [("/v1/responses", "synthetic-openai"), ("/v1/responses", "synthetic-openai"),
             ("/v1/messages", "synthetic-anthropic")])
        with closing(sqlite3.connect(self.config.ai_work.database_path.absolute().as_uri() + "?mode=ro", uri=True)) as ledger:
            ledger.row_factory = sqlite3.Row
            jobs = ledger.execute("SELECT * FROM ai_work_jobs").fetchall()
            attempts = ledger.execute("SELECT * FROM ai_work_attempts").fetchall()
            test.assertEqual({row["work_id"] for row in jobs}, set(work_ids))
            test.assertEqual(len(jobs), 3)
            test.assertTrue(all(row["state"] == "active" and row["completed_at"] is None for row in jobs))
            test.assertTrue(all((row["organization_id"], row["actor_id"], row["repository"]) ==
                               ("org-a", "native-owner", "qualification/native-work") for row in jobs))
            test.assertEqual(len(attempts), 3)
            test.assertEqual({row["work_id"] for row in attempts}, set(work_ids))
            for row in attempts:
                test.assertEqual((row["organization_id"], row["actor_id"], row["state"], row["response_succeeded"]),
                                 ("org-a", "native-owner", "succeeded", 1))
                model, protocol, cost = self.expected_work[row["work_id"]]
                # Configured $3/million: Responses usage 10+5 =>45 microUSD;
                # Messages usage 24+8 =>96. These are not provider invoices.
                test.assertEqual((row["model"], row["protocol"], row["cost_microusd"]),
                                 (model, protocol, cost))
                test.assertGreaterEqual(row["reserved_microusd"], cost)
                test.assertLessEqual(row["reserved_microusd"], 1_000_000)
                test.assertEqual(row["reservation_exceeded"], 0)
                test.assertIsNone(row["confirmed_cost_microusd"])
                test.assertIsNone(row["cost_confirmation_source"])
                test.assertIsNone(row["cost_confirmation_reference"])
                test.assertIsNone(row["cache_source"])
                test.assertGreaterEqual(row["latency_ms"], 0)
                test.assertGreaterEqual(row["gateway_wall_ms"], 0)
                test.assertGreaterEqual(row["gateway_overhead_ms"], 0)
            plans = ledger.execute("SELECT scope_type,scope_id,budget_microusd FROM ai_work_plans").fetchall()
            test.assertEqual({(row["scope_type"], row["scope_id"], row["budget_microusd"]) for row in plans},
                             {("workspace", "org-a", 1_000_000), *(("job", work, 1_000_000) for work in work_ids)})
            test.assertEqual(ledger.execute("SELECT COUNT(*) FROM ai_work_observations").fetchone()[0], 0)
            test.assertEqual(ledger.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            test.assertIsNone(ledger.execute("PRAGMA foreign_key_check").fetchone())


class _WorkHeaderRecordingMixin:
    def do_POST(self) -> None:  # noqa: N802
        # Preserve duplicate header fields; the inherited fixture's dictionary
        # capture alone would hide two identical job headers.
        self.server.work_header_values.append(self.headers.get_all("X-Hormuz-Work-Id", []))
        super().do_POST()


class _NativeCodexGatewayHandler(_WorkHeaderRecordingMixin, _CodexToolGatewayHandler):
    pass


class _NativeCodexGateway(_CodexToolGateway):
    def __init__(self) -> None:
        super().__init__()
        self.RequestHandlerClass = _NativeCodexGatewayHandler
        self.work_header_values = []


class _ClaudeGatewayHandler(_WorkHeaderRecordingMixin, FakeProviderHandler):
    @property
    def requests(self):
        return self.server.requests

    def do_GET(self) -> None:  # noqa: N802
        _GatewayHandler.do_GET(self)


class _ClaudeGateway(_Gateway):
    def __init__(self) -> None:
        from http.server import ThreadingHTTPServer

        self.requests = []
        self.work_header_values = []
        ThreadingHTTPServer.__init__(self, ("127.0.0.1", 0), _ClaudeGatewayHandler)


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("HORMUZ_NATIVE_RELAY_BINARY"),
                     "Requires an explicitly selected built Mac relay")
class NativeMacRelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="hormuz-native-relay-", dir="/private/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = "12345678-1234-1234-1234-123456789abc"
        if self._testMethodName == "test_official_clients_settle_real_work_ledger":
            self.work_fixture = _NativeWorkFixture(self.root)
            self.addCleanup(self.work_fixture.close)
            self.gateway = self.work_fixture.gateway
        elif self._testMethodName == "test_official_codex_on_and_off":
            self.gateway = _NativeCodexGateway()
        elif self._testMethodName == "test_official_claude_streams_through_native_relay":
            self.gateway = _ClaudeGateway()
        else:
            self.gateway = _Gateway()
        if self._testMethodName != "test_official_clients_settle_real_work_ledger":
            self.thread = threading.Thread(target=self.gateway.serve_forever, daemon=True)
            self.thread.start()
            self.addCleanup(self.gateway.server_close)
            self.addCleanup(self.thread.join, 3)
            self.addCleanup(self.gateway.shutdown)
        self.origin = f"http://127.0.0.1:{self.gateway.server_address[1]}"
        profile = {"id": self.key, "gateway": self.origin, "organization": "org-a",
                   "client": "codex", "model": "approved", "allowLoopbackHTTP": True, "setup": "custom"}
        (self.root / "profile.json").write_text(json.dumps(profile))
        (self.root / "profile.json").chmod(0o600)
        self.owner = socket.socket(socket.AF_UNIX)
        self.owner.bind(str(self.root / "lease"))
        self.owner.listen(4)
        self.owner.settimeout(10)
        self.addCleanup(self.owner.close)
        self.broker = self.root / "broker"
        helper_record = (_record_native_helper(self.root, "broker")
                         if self._testMethodName == "test_official_clients_settle_real_work_ledger" else "")
        self._script(self.broker, f"#!{sys.executable}\n" + f"""
import json, sys
{helper_record}
assert sys.argv[-1] == "--expected-profile-stdin"
assert json.load(sys.stdin) == json.load(open({str(self.root / 'profile.json')!r}))
print({ACCESS_TOKEN!r})
""")
        self.optimizer = Path(os.environ["HORMUZ_NATIVE_OPTIMIZER_HELPER"])
        if self._testMethodName == "test_official_clients_settle_real_work_ledger":
            packaged_optimizer = self.optimizer
            self.optimizer = self.root / "optimizer"
            self._script(self.optimizer, f"#!{sys.executable}\nimport json, sys\n"
                + _record_native_helper(self.root, "optimizer")
                + f"os.execv({str(packaged_optimizer)!r}, [{str(packaged_optimizer)!r}, *sys.argv[1:]])\n")
        self.client = self.root / "codex"

    def _script(self, path: Path, source: str) -> None:
        path.write_text(source)
        path.chmod(0o700)

    def _fake_client(self, body: bytes, *, hold: bool = False, bad_auth: bool = False) -> None:
        self._script(self.client, f"#!{sys.executable}\n" + f"""
import http.client, os, sys, tomllib
from urllib.parse import urlsplit
if sys.argv[1:] == ["--version"]:
    print("codex 0.147.0")
    raise SystemExit(0)
assert "OPENAI_API_KEY" not in os.environ
assert "ANTHROPIC_AUTH_TOKEN" not in os.environ
settings = tomllib.loads("\\n".join(sys.argv[2::2]))
origin = settings["model_providers"]["hormuz_context_relay"]["base_url"]
endpoint = urlsplit(origin)
connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=10)
token = "wrong" if {bad_auth!r} else os.environ["HORMUZ_LOCAL_RELAY_TOKEN"]
connection.request("POST", "/v1/responses", body={body!r},
                   headers={{"Authorization": "Bearer " + token, "Content-Type": "application/json"}})
response = connection.getresponse()
assert response.status == {401 if bad_auth else 200}
response.read()
connection.close()
open({str(self.root / 'address')!r}, "w").write(endpoint.netloc)
if {hold!r}:
    sys.stdin.buffer.read()
""")

    def _start(self, *, work_id: str | None = None) -> tuple[subprocess.Popen, socket.socket]:
        process = self._launch(work_id=work_id)
        lease, _ = self.owner.accept()
        self.addCleanup(lease.close)
        return process, lease

    def _launch(self, *, work_id: str | None = None, terminal_only: bool = False) -> subprocess.Popen:
        environment = {name: value for name, value in os.environ.items() if name in {
            "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SSL_CERT_FILE", "SSL_CERT_DIR",
            "HORMUZ_CONTEXT_TOKENIZER_CACHE",
        }}
        environment["PATH"] = str(self.root) + ":" + environment.get("PATH", "/usr/bin:/bin")
        environment["OPENAI_API_KEY"] = "synthetic-direct-key-must-not-reach-client"
        command = [os.environ["HORMUZ_NATIVE_RELAY_BINARY"], "--profile", self.key,
                   "--state-directory", str(self.root), "--credential-helper", str(self.broker),
                   "--optimizer-helper", str(self.optimizer), "--owner-socket", str(self.root / "lease")]
        if work_id is not None:
            command.extend(["--work-id", work_id])
        if terminal_only:
            environment["HOME"] = str(self.root / "home")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            # The native relay owns a dynamic loopback listener and the fixed
            # Unix lease. The official child adds an exact-port sandbox below.
            policy = ('(version 1) (allow default) (deny network*) '
                '(allow network-bind network-inbound (local ip "localhost:*")) '
                '(allow network-outbound (remote ip "localhost:*")) '
                '(allow network-outbound (literal ' + json.dumps(str(self.root / "lease")) + '))')
            command = ["/usr/bin/sandbox-exec", "-p", policy, *command]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment, cwd=self.root,
                                   start_new_session=terminal_only)
        if terminal_only:
            self.addCleanup(self._terminal_stop, process, self.root)
        else:
            self.addCleanup(self._stop, process)
        return process

    def _official_client(self, name: str, arguments: list[str], *, bare_claude: bool = False,
                         terminal_only: bool = False, omit_work_header: bool = False) -> None:
        real = str(Path(os.environ["HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY"]) / name)
        (self.root / "codex-config").mkdir(mode=0o700, exist_ok=True)
        (self.root / "claude-config").mkdir(mode=0o700, exist_ok=True)
        (self.root / "home").mkdir(mode=0o700, exist_ok=True)
        self._script(self.root / name, f"#!{sys.executable}\n" + f"""
import os, sys
real = {real!r}
os.environ["DISABLE_AUTOUPDATER"] = "1"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["DISABLE_ERROR_REPORTING"] = "1"
os.environ["CLAUDE_CONFIG_DIR"] = {str(self.root / 'claude-config')!r}
os.environ["HOME"] = {str(self.root / 'home')!r}
os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
os.environ["CLAUDE_CODE_MAX_RETRIES"] = "0"
os.environ["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "32"
os.environ["MAX_THINKING_TOKENS"] = "0"
if not {bare_claude!r}:
    # The official client's supported configuration root applies only to this
    # child, not to the native broker, launcher, or user's installed setup.
    os.environ["CODEX_HOME"] = {str(self.root / 'codex-config')!r}
if sys.argv[1:] == ["--version"]:
    # The native guard sees the genuine pinned client's output; no fake version.
    if {terminal_only!r}:
        os.execv("/usr/bin/sandbox-exec", ["sandbox-exec", "-p",
            "(version 1) (allow default) (deny network*)", real, "--version"])
    os.execv(real, [real, "--version"])
if {bare_claude!r}:
    # Bare mode explicitly disables OAuth/Keychain reads and expects API-key
    # auth. Use only the invocation's synthetic local relay credential.
    os.environ["ANTHROPIC_API_KEY"] = os.environ.pop("ANTHROPIC_AUTH_TOKEN")
command = [real, *{arguments!r}, *sys.argv[1:]]
if {terminal_only!r}:
    import json, tomllib
    from urllib.parse import urlsplit
    if {name!r} == "codex":
        settings = tomllib.loads("\\n".join(sys.argv[2::2]))
        origin = settings["model_providers"]["hormuz_context_relay"]["base_url"]
        command.extend(["-c", "model_providers.hormuz_context_relay.request_max_retries=0",
                        "-c", "model_providers.hormuz_context_relay.stream_max_retries=0"])
        if {omit_work_header!r}:
            # Exercise the compiled relay's selected-job binding independently
            # of the official client's optional correlation header.
            command.extend(["-c", "model_providers.hormuz_context_relay.http_headers={{}}"])
    else:
        origin = os.environ["ANTHROPIC_BASE_URL"]
    endpoint = urlsplit(origin)
    assert endpoint.scheme == "http" and endpoint.hostname == "127.0.0.1" and endpoint.port
    open({str(self.root / 'terminal-relay-address.json')!r}, "w").write(json.dumps([endpoint.hostname, endpoint.port]))
    policy = ('(version 1) (allow default) (deny network*) '
              '(allow network-outbound (remote ip "localhost:' + str(endpoint.port) + '"))')
    os.execv("/usr/bin/sandbox-exec", ["sandbox-exec", "-p", policy, *command])
os.execv(real, command)
""")

    def _preference(self, enabled: bool) -> None:
        preference = self.root / f"context-optimization-{self.key}.json"
        preference.write_text(json.dumps({"enabled": enabled, "schema_version": 1}, separators=(",", ":")))
        preference.chmod(0o600)

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    @staticmethod
    def _drain_terminal(process) -> None:
        # macOS CPython has no os.waitid. EOF observes native stdio without
        # reaping; keep the Popen leader owned until group signals are complete.
        selector = selectors.DefaultSelector()
        discarded = 0
        deadline = time.monotonic() + 35
        try:
            for stream in (process.stdout, process.stderr):
                if not stream.closed:
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ)
            while selector.get_map() and time.monotonic() < deadline:
                if discarded > 1024 * 1024:
                    # Keep the native helper grace period even after excessive
                    # output, without consuming unbounded bytes or memory.
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
                    continue
                for key, _ in selector.select(timeout=0.1):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        discarded += len(data)
        finally:
            selector.close()

    @staticmethod
    def _terminal_stop(process: subprocess.Popen, root) -> None:
        if getattr(process, "_hormuz_terminal_wait_called", False):
            return
        try:
            # Lease EOF lets native cleanup cancel separately owned helpers.
            # No poll/wait/communicate has reaped this leader, so its identity
            # stays pinned through cleanup even if native already exited.
            try:
                NativeMacRelayTests._drain_terminal(process)
            finally:
                # A read/selector failure must still close this owned process
                # group while the unreaped launcher identity remains pinned.
                for signum in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, signum)
                    except ProcessLookupError:
                        pass
                    if signum == signal.SIGTERM:
                        time.sleep(0.1)
                process._hormuz_terminal_wait_called = True
                process.wait(timeout=3)
            if not _group_absent(process.pid):
                raise AssertionError("owned native group closure unavailable")
            records = list(root.glob("native-helper-*.json"))
            if len(records) > 24:
                raise AssertionError("bounded helper group inventory exceeded")
            for path in records:
                row = json.loads(path.read_text())
                # Only this launch's records are relevant. Historical helper
                # IDs are observational even when their parent is ours.
                if row["ppid"] == process.pid and (row["pid"] != row["pgid"] or not _group_absent(row["pgid"])):
                    raise AssertionError("native helper group closure unavailable")
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def _run_terminal(self, work_id, *, deadline):
        # Reserve 46 seconds for the 35-second native helper fallback, owned
        # group termination/reap, historical observations and endpoint probe.
        deadline = min(deadline - 46, time.monotonic() + 134)
        self.assertLess(time.monotonic(), deadline, "bounded native case start")
        process = self._launch(work_id=work_id, terminal_only=True)
        lease = None
        output = bytearray()
        selector = selectors.DefaultSelector()
        try:
            lease, _ = self.owner.accept()
            process.stdin.close()
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                self.assertLess(time.monotonic(), deadline, "bounded native case deadline")
                self.assertFalse(self.work_fixture.failed, "provider fixture refused a request")
                for key, _ in selector.select(timeout=0.1):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(data)
                        self.assertLessEqual(len(output), 1024 * 1024, "bounded native output")
        finally:
            if lease is not None:
                lease.close()
            selector.close()
            self._terminal_stop(process, self.root)
        self.assertEqual(process.returncode, 0, "native terminal invocation failed")
        host, port = json.loads((self.root / "terminal-relay-address.json").read_text())
        # This one bounded closure probe is to this invocation's actual endpoint.
        with self.assertRaises(OSError):
            socket.create_connection((host, port), timeout=1)
        return output

    def test_off_is_exact_and_listener_ends_with_client(self) -> None:
        body = b'{ "model": "approved", "input": [] }\n'
        self._fake_client(body)
        process, _ = self._start()
        output, diagnostic = process.communicate(timeout=20)
        self.assertEqual((process.returncode, output, diagnostic), (0, b"", b""))
        self.assertEqual([request[0] for request in self.gateway.requests], [body])
        address = (self.root / "address").read_text().split(":")
        with self.assertRaises(OSError):
            socket.create_connection((address[0], int(address[1])), timeout=1)

    def test_bad_local_auth_never_reaches_gateway(self) -> None:
        self._fake_client(b"{}", bad_auth=True)
        process, _ = self._start()
        process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(self.gateway.requests, [])

    def test_invalid_broker_output_fails_before_client_launch(self) -> None:
        self._script(self.broker, "#!/bin/sh\nprintf 'not-a-credential\\n'\n")
        self._script(self.client, "#!/bin/sh\ntouch client-was-started\n")
        process, _ = self._start()
        output, diagnostic = process.communicate(timeout=10)
        self.assertEqual((process.returncode, output, diagnostic),
                         (1, b"", b"relay error: The gateway session credential is unavailable.\n"))
        self.assertFalse((self.root / "client-was-started").exists())
        self.assertEqual(self.gateway.requests, [])

    def test_owner_exit_stops_client_and_relay_without_replay(self) -> None:
        import time

        self._fake_client(b"{}", hold=True)
        process, lease = self._start()
        deadline = time.monotonic() + 10
        while not (self.root / "address").exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertIsNone(process.poll())
        lease.close()
        process.communicate(timeout=10)
        self.assertEqual(process.returncode, 130)
        self.assertEqual(len(self.gateway.requests), 1)
        address = (self.root / "address").read_text().split(":")
        with self.assertRaises(OSError):
            socket.create_connection((address[0], int(address[1])), timeout=1)

    def test_owner_exit_interrupts_client_discovery_before_launch(self) -> None:
        import time

        probe_pid = self.root / "probe-pid"
        client_started = self.root / "client-was-started"
        optimizer_started = self.root / "optimizer-was-started"
        self._script(self.client, f"#!{sys.executable}\n" + f"""
import os, sys, time
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    Path({str(probe_pid)!r}).write_text(str(os.getpid()))
    time.sleep(4)
    print("codex 0.147.0")
else:
    Path({str(client_started)!r}).touch()
""")
        self.optimizer = self.root / "optimizer"
        self._script(self.optimizer, f"#!{sys.executable}\n" +
                     f"from pathlib import Path\nPath({str(optimizer_started)!r}).touch()\n")
        self._preference(True)
        process, lease = self._start()
        deadline = time.monotonic() + 10
        while True:
            try:
                pid = int(probe_pid.read_text())
            except (FileNotFoundError, ValueError):
                self.assertLess(time.monotonic(), deadline, "Version probe did not start")
                time.sleep(0.01)
            else:
                break
        cancelled_at = time.monotonic()
        lease.close()
        output, diagnostic = process.communicate(timeout=8)
        self.assertEqual((process.returncode, output, diagnostic), (130, b"", b""))
        self.assertLess(time.monotonic() - cancelled_at, 2,
                        "Owner exit must interrupt discovery, not wait for the version probe")
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse(client_started.exists())
        self.assertFalse(optimizer_started.exists())
        self.assertEqual(self.gateway.requests, [])

    def test_owner_exit_interrupts_startup_credential_helper(self) -> None:
        import time

        helper_pid = self.root / "credential-helper-pid"
        self._script(self.broker, f"#!{sys.executable}\n" + f"""
import os, sys, time
from pathlib import Path
sys.stdin.buffer.read()
Path({str(helper_pid)!r}).write_text(str(os.getpid()))
time.sleep(4)
print({ACCESS_TOKEN!r})
""")
        self._script(self.client, "#!/bin/sh\ntouch client-was-started\n")
        self.optimizer = self.root / "optimizer"
        self._script(self.optimizer, "#!/bin/sh\ntouch optimizer-was-started\n")
        self._preference(True)
        process, lease = self._start()
        deadline = time.monotonic() + 10
        while True:
            try:
                pid = int(helper_pid.read_text())
            except (FileNotFoundError, ValueError):
                self.assertLess(time.monotonic(), deadline, "Credential helper did not start")
                time.sleep(0.01)
            else:
                break
        cancelled_at = time.monotonic()
        lease.close()
        output, diagnostic = process.communicate(timeout=8)
        self.assertEqual((process.returncode, output, diagnostic), (130, b"", b""))
        self.assertLess(time.monotonic() - cancelled_at, 2,
                        "Owner exit must interrupt the startup credential exchange")
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        self.assertFalse((self.root / "client-was-started").exists())
        self.assertFalse((self.root / "optimizer-was-started").exists())
        self.assertEqual(self.gateway.requests, [])

    def test_owner_exit_interrupts_request_credential_helper(self) -> None:
        import time

        first_lookup = self.root / "startup-lookup-complete"
        helper_pid = self.root / "request-credential-helper-pid"
        client_pid = self.root / "client-pid"
        self._script(self.broker, f"#!{sys.executable}\n" + f"""
import os, sys, time
from pathlib import Path
sys.stdin.buffer.read()
first_lookup = Path({str(first_lookup)!r})
if first_lookup.exists():
    Path({str(helper_pid)!r}).write_text(str(os.getpid()))
    time.sleep(4)
else:
    first_lookup.touch()
print({ACCESS_TOKEN!r})
""")
        self._script(self.client, f"#!{sys.executable}\n" + f"""
import http.client, os, sys, tomllib
from pathlib import Path
from urllib.parse import urlsplit
if sys.argv[1:] == ["--version"]:
    print("codex 0.147.0")
    raise SystemExit(0)
settings = tomllib.loads("\\n".join(sys.argv[2::2]))
endpoint = urlsplit(settings["model_providers"]["hormuz_context_relay"]["base_url"])
Path({str(client_pid)!r}).write_text(str(os.getpid()))
Path({str(self.root / 'address')!r}).write_text(endpoint.netloc)
connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=10)
connection.request("POST", "/v1/responses", body=b"{{}}",
                   headers={{"Authorization": "Bearer " + os.environ["HORMUZ_LOCAL_RELAY_TOKEN"],
                             "Content-Type": "application/json"}})
connection.getresponse().read()
""")
        process, lease = self._start()
        deadline = time.monotonic() + 10
        while True:
            try:
                pid = int(helper_pid.read_text())
            except (FileNotFoundError, ValueError):
                self.assertLess(time.monotonic(), deadline, "Request credential helper did not start")
                time.sleep(0.01)
            else:
                break
        cancelled_at = time.monotonic()
        lease.close()
        output, diagnostic = process.communicate(timeout=8)
        self.assertEqual((process.returncode, output, diagnostic), (130, b"", b""))
        self.assertLess(time.monotonic() - cancelled_at, 2,
                        "Owner exit must interrupt request-time custody, not wait for its deadline")
        for owned_pid in (pid, int(client_pid.read_text())):
            with self.assertRaises(ProcessLookupError):
                os.kill(owned_pid, 0)
        address = (self.root / "address").read_text().split(":")
        with self.assertRaises(OSError):
            socket.create_connection((address[0], int(address[1])), timeout=1)
        self.assertEqual(self.gateway.requests, [])

    def test_unsupported_client_version_is_not_reported_as_cancellation(self) -> None:
        self._script(self.client, "#!/bin/sh\n"
                     'if [ "$1" = "--version" ]; then\n'
                     "  printf 'codex 0.146.0\\n'\n"
                     "else\n  touch client-was-started\nfi\n")
        process, _ = self._start()
        output, diagnostic = process.communicate(timeout=10)
        self.assertEqual((process.returncode, output, diagnostic),
                         (1, b"", b"relay error: The installed AI client version is unsupported.\n"))
        self.assertFalse((self.root / "client-was-started").exists())
        self.assertEqual(self.gateway.requests, [])

    def test_on_uses_packaged_bridge_and_preserves_lossless_tool_output(self) -> None:
        from hormuz.compaction_formats import restore_text

        paths = "".join(f"src/generated/file_{index % 20}.py\n" for index in range(160))
        body = json.dumps({"model": "approved", "input": [
            {"type": "function_call", "call_id": "records", "name": "exec_command",
             "arguments": json.dumps({"cmd": "rg --files src/generated"})},
            {"type": "function_call_output", "call_id": "records", "output": paths},
        ]}).encode()
        self._preference(True)
        self._fake_client(body)
        process, _ = self._start()
        process.communicate(timeout=40)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(len(self.gateway.requests), 1)
        changed = self.gateway.requests[0][0]
        self.assertNotEqual(changed, body)
        self.assertEqual(restore_text(json.loads(changed)["input"][1]["output"]), paths)

    def test_off_is_independent_of_optimizer_execution(self) -> None:
        self.optimizer = self.root / "optimizer"
        self._script(self.optimizer, "#!/bin/sh\ntouch optimizer-was-started\nexit 1\n")
        self._fake_client(b'{ "input" : [] }\n')
        process, _ = self._start()
        process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0)
        self.assertFalse((self.root / "optimizer-was-started").exists())
        self.assertEqual(self.gateway.requests[0][0], b'{ "input" : [] }\n')

    def test_on_helper_failure_forwards_original_once(self) -> None:
        self.optimizer = self.root / "optimizer"
        self._script(self.optimizer, "#!/bin/sh\nexit 1\n")
        self._preference(True)
        body = b'{ "input" : [] }\n'
        self._fake_client(body)
        process, _ = self._start()
        process.communicate(timeout=20)
        self.assertEqual(process.returncode, 0)
        self.assertEqual([request[0] for request in self.gateway.requests], [body])

    def test_unsafe_owner_directory_fails_before_broker_or_client(self) -> None:
        self._script(self.broker, "#!/bin/sh\ntouch broker-was-started\n")
        self.root.chmod(0o755)
        self.addCleanup(self.root.chmod, 0o700)
        process = self._launch()
        process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse((self.root / "broker-was-started").exists())
        self.assertEqual(self.gateway.requests, [])

    @unittest.skipUnless(os.environ.get("HORMUZ_NATIVE_CREDENTIAL_HELPER"),
                         "Requires the explicitly selected packaged Mac credential executable")
    def test_packaged_credential_broker_rejects_bad_binding_without_token_output(self) -> None:
        command = [os.environ["HORMUZ_NATIVE_CREDENTIAL_HELPER"], "credential", "--profile", self.key,
                   "--state-directory", str(self.root), "--expected-profile-stdin"]
        changed = json.loads((self.root / "profile.json").read_text())
        changed["model"] = "different-model"
        for body in [b"", b"invalid", b"x" * 65_537, json.dumps(changed).encode()]:
            with self.subTest(bytes=len(body)):
                result = subprocess.run(command, input=body, capture_output=True, timeout=10, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")

    def test_owner_exit_cancels_optimizer_and_same_group_descendant_before_egress(self) -> None:
        import time

        self.optimizer = self.root / "optimizer"
        self._script(self.optimizer, "#!/bin/sh\ntouch optimizer-was-started\n"
                     "(sleep 1; touch descendant-survived) &\nsleep 30\n")
        self._preference(True)
        self._fake_client(b"{}")
        process, lease = self._start()
        deadline = time.monotonic() + 10
        while not (self.root / "optimizer-was-started").exists():
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        lease.close()
        process.communicate(timeout=5)
        self.assertEqual(process.returncode, 130)
        time.sleep(1.1)
        self.assertFalse((self.root / "descendant-survived").exists())
        self.assertEqual(self.gateway.requests, [])

    @unittest.skipUnless(os.environ.get("HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY"),
                         "Requires explicitly selected pinned official client installations")
    def test_official_codex_on_and_off(self) -> None:
        from hormuz.compaction_formats import restore_text

        generated = self.root / "generated" / "structural_context_repetition_for_hormuz"
        generated.mkdir(parents=True)
        for index in range(48):
            (generated / f"file_{index:03}.py").write_text("# synthetic context probe\n")
        # Unrelated marketplace cloning must not race this fixture's cleanup.
        self._official_client("codex", [
            "exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
            "--ephemeral", "--sandbox", "read-only", "-C", str(self.root),
            "-c", "analytics.enabled=false", "-c", "feedback.enabled=false",
            "-c", "features.plugins=false",
            "Call exec_command with exactly `rg --files generated`, then finish.",
        ])
        outputs = {}
        for enabled in (False, True):
            self.gateway.requests.clear()
            self.gateway.work_header_values.clear()
            self._preference(enabled)
            work_id = "work-native-codex-" + ("on" if enabled else "off")
            process, lease = self._start(work_id=work_id)
            output, diagnostic = process.communicate(timeout=60)
            lease.close()
            self.assertEqual(process.returncode, 0, msg=diagnostic.decode(errors="replace"))
            self.assertIn(b"CONTEXT_OK", output + diagnostic)
            self.assertEqual(len(self.gateway.requests), 2)
            self.assertEqual(self.gateway.work_header_values, [[work_id], [work_id]])
            requests = [(json.loads(body), {k.lower(): v for k, v in headers.items()})
                        for body, headers in self.gateway.requests]
            self.assertTrue(all(headers["authorization"] == "Bearer " + ACCESS_TOKEN
                                for _, headers in requests))
            self.assertTrue(all("x-hormuz-actor-id" not in headers
                                and "x-hormuz-organization-id" not in headers
                                for _, headers in requests))
            payload, headers = requests[-1]
            result = next(item["output"] for item in payload["input"]
                          if item.get("type") == "function_call_output")
            if enabled:
                self.assertIn("x-hormuz-context-format", headers)
            else:
                self.assertNotIn("x-hormuz-context-format", headers)
            outputs[enabled] = result
        self.assertNotEqual(outputs[False], outputs[True])
        # Codex assigns fresh chunk IDs/timings in each separate invocation.
        # Compare the actual command output, not that volatile wrapper metadata.
        def paths(value: str) -> list[str]:
            return sorted(line for line in value.splitlines() if line.startswith("generated/"))

        restored = restore_text(outputs[True])
        self.assertEqual(len(paths(restored)), 48)
        self.assertEqual(paths(restored), paths(outputs[False]))

    @unittest.skipUnless(os.environ.get("HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY"),
                         "Requires explicitly selected pinned official client installations")
    def test_official_claude_streams_through_native_relay(self) -> None:
        profile = json.loads((self.root / "profile.json").read_text())
        profile.update(client="claude-code", model="claude-sonnet-5")
        (self.root / "profile.json").write_text(json.dumps(profile))
        self._official_client("claude", [
            "-p", "--bare", "--no-session-persistence", "--tools", "",
            "--setting-sources", "", "Reply with exactly ok and do not call tools.",
        ], bare_claude=True)
        work_id = "work-native-claude-stream"
        process, _ = self._start(work_id=work_id)
        output, diagnostic = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, msg=diagnostic.decode(errors="replace"))
        self.assertIn(b"ok", output.lower())
        messages = [request for request in self.gateway.requests
                    if request["path"].partition("?")[0] == "/v1/messages"]
        self.assertTrue(messages)
        self.assertTrue(any(message["body"].get("stream") for message in messages))
        self.assertTrue(self.gateway.work_header_values)
        self.assertTrue(all(values == [work_id] for values in self.gateway.work_header_values))
        self.assertTrue(all(message["headers"]["authorization"] == "Bearer " + ACCESS_TOKEN
                            for message in messages))
        self.assertTrue(all("x-hormuz-actor-id" not in message["headers"]
                            and "x-hormuz-organization-id" not in message["headers"]
                            for message in messages))

    @unittest.skipUnless(os.environ.get("HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY"),
                         "Requires explicitly selected pinned official client installations")
    def test_official_clients_settle_real_work_ledger(self) -> None:
        """Actual pinned clients/native relay join the real gateway's closed ledger.

        Three sequential terminal streams, no tools/retries/payment/provider calls.
        On here proves successful bound transport, not tool-output compaction.
        """
        deadline, works = time.monotonic() + 570, []
        for name, enabled in (("codex", False), ("codex", True), ("claude", False)):
            # Plain sequencing: a failed native case aborts the remaining calls.
            profile = json.loads((self.root / "profile.json").read_text())
            profile.update(client="codex" if name == "codex" else "claude-code",
                           model="approved" if name == "codex" else "claude-sonnet-5")
            (self.root / "profile.json").write_text(json.dumps(profile))
            self._preference(enabled)
            arguments = (["exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                "--ephemeral", "--sandbox", "read-only", "-C", str(self.root),
                "-c", "analytics.enabled=false", "-c", "feedback.enabled=false",
                "-c", "features.plugins=false", "Reply with exactly CONTEXT_OK. Do not call tools."]
                if name == "codex" else ["-p", "--bare", "--no-session-persistence", "--tools", "",
                "--setting-sources", "", "--max-turns", "1", "Reply with exactly 42. Do not call tools."])
            self._official_client(name, arguments, bare_claude=name == "claude", terminal_only=True,
                                  omit_work_header=name == "codex" and enabled)
            work_id = self.work_fixture.job(name + ("-on" if enabled else "-off"), model=profile["model"])
            works.append(work_id)
            before_provider = len(self.work_fixture.provider_calls)
            before_gateway = len(self.work_fixture.gateway_calls)
            output = self._run_terminal(work_id, deadline=deadline)
            self.assertIn(b"CONTEXT_OK" if name == "codex" else b"42", output)
            self.assertEqual(len(self.work_fixture.provider_calls) - before_provider, 1)
            calls = self.work_fixture.gateway_calls[before_gateway:]
            self.assertTrue(calls)
            self.assertTrue(all(row["work_headers"] == [work_id] and row["owner_auth"]
                                and not row["authority_header"] for row in calls))
            self.work_fixture.assert_job(self, work_id)
        self.work_fixture.assert_refused_ownership(self, works[0])
        self.work_fixture.close()
        self.work_fixture.assert_closed_ledger(self, works)


class NativeMacRelayFixtureTests(unittest.TestCase):
    def test_terminal_cleanup_retains_leader_and_observes_helpers_only(self) -> None:
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory(prefix="hormuz-native-cleanup-fixture-") as folder:
            root = Path(folder)
            (root / "native-helper-424243.json").write_text(json.dumps({
                "pid": 424243, "pgid": 424243, "ppid": 424242, "kind": "optimizer"}))
            process = Mock(pid=424242, _hormuz_terminal_wait_called=False)
            order = []
            process.wait.side_effect = lambda **_: order.append("reap")
            with patch.object(NativeMacRelayTests, "_drain_terminal", side_effect=lambda _: order.append("drain")), \
                    patch("tests.test_native_macos_relay.os.killpg",
                          side_effect=lambda group, sig: order.append((group, sig))), \
                    patch("tests.test_native_macos_relay._group_absent", side_effect=[True, False]) as observe:
                with self.assertRaisesRegex(AssertionError, "helper group closure"):
                    NativeMacRelayTests._terminal_stop(process, root)
                NativeMacRelayTests._terminal_stop(process, root)
            self.assertEqual(order, ["drain", (424242, signal.SIGTERM), (424242, signal.SIGKILL), "reap"])
            self.assertEqual([call.args[0] for call in observe.call_args_list], [424242, 424243])
            process.wait.assert_called_once()
            process.poll.assert_not_called()
            process.communicate.assert_not_called()
            process.stdout.close.assert_called_once()
            failed_drain = Mock(pid=424244, _hormuz_terminal_wait_called=False)
            with patch.object(NativeMacRelayTests, "_drain_terminal", side_effect=OSError("fixed fixture failure")), \
                    patch("tests.test_native_macos_relay.os.killpg") as terminate:
                with self.assertRaisesRegex(OSError, "fixed fixture failure"):
                    NativeMacRelayTests._terminal_stop(failed_drain, root)
            self.assertEqual([call.args for call in terminate.call_args_list],
                             [(424244, signal.SIGTERM), (424244, signal.SIGKILL)])
            failed_drain.wait.assert_called_once()
            failed_drain.poll.assert_not_called()
            failed_drain.communicate.assert_not_called()
            failed_drain.stdout.close.assert_called_once()

    def test_terminal_wrapper_and_helper_scripts_compile_without_launch(self) -> None:
        from unittest.mock import patch

        with tempfile.TemporaryDirectory(prefix="hormuz-native-wrapper-fixture-") as folder, \
                patch.dict(os.environ, {"HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY": "/synthetic/pinned-clients"}):
            fixture = NativeMacRelayTests("test_official_clients_settle_real_work_ledger")
            fixture.root = Path(folder)
            for name in ("codex", "claude"):
                fixture._official_client(name, ["synthetic"], bare_claude=name == "claude",
                    terminal_only=True, omit_work_header=name == "codex")
                compile((fixture.root / name).read_text(), name, "exec")
            compile("import json\n" + _record_native_helper(fixture.root, "optimizer"), "helper", "exec")

    def test_native_batch_stops_after_first_failed_case(self) -> None:
        from unittest.mock import Mock

        with tempfile.TemporaryDirectory(prefix="hormuz-native-stop-fixture-") as folder:
            fixture = NativeMacRelayTests("test_official_clients_settle_real_work_ledger")
            fixture.root = Path(folder)
            (fixture.root / "profile.json").write_text('{}')
            fixture.work_fixture = Mock(spec=_NativeWorkFixture, provider_calls=[], gateway_calls=[])
            fixture.work_fixture.job.return_value = "work-owned"
            fixture._preference = Mock()
            fixture._official_client = Mock()
            fixture._run_terminal = Mock(side_effect=AssertionError("fixed fixture failure"))
            # Exercise control flow only; no native environment, process or
            # socket. The skip wrapper's genuine native invocation stays gated.
            with self.assertRaisesRegex(AssertionError, "fixed fixture failure"):
                NativeMacRelayTests.test_official_clients_settle_real_work_ledger.__wrapped__(fixture)
            fixture._run_terminal.assert_called_once()
            fixture._official_client.assert_called_once()
            fixture.work_fixture.job.assert_called_once()
            fixture.work_fixture.assert_job.assert_not_called()

    def test_real_work_fixture_settles_streams_and_refuses_unowned_jobs(self) -> None:
        # Qualifies this real runtime/provider fixture locally; it does not
        # substitute direct HTTP for the conditional official/native test above.
        with tempfile.TemporaryDirectory(prefix="hormuz-native-work-fixture-") as folder:
            fixture = _NativeWorkFixture(Path(folder))
            works = []
            try:
                for path, model in (("/v1/responses", "approved"), ("/v1/responses", "approved"),
                                    ("/v1/messages", "claude-sonnet-5")):
                    work_id = fixture.job("local-terminal-fixture", model=model)
                    works.append(work_id)
                    body = {"model": model, "stream": True}
                    body.update({"input": "synthetic", "max_output_tokens": 32} if path == "/v1/responses"
                                else {"messages": [{"role": "user", "content": "synthetic"}], "max_tokens": 32})
                    status, headers, raw = fixture.transport.request("POST", path, json.dumps(body),
                        {"Authorization": "Bearer " + ACCESS_TOKEN, "Content-Type": "application/json",
                         "X-Hormuz-Work-Id": work_id}, timeout=5)
                    self.assertEqual(status, 200)
                    self.assertEqual(headers["x-hormuz-work-id"], work_id)
                    self.assertIn(b"response.completed" if path == "/v1/responses" else b"message_stop", raw)
                    fixture.assert_job(self, work_id)
                fixture.assert_refused_ownership(self, works[0])
            finally:
                fixture.close()
            fixture.assert_closed_ledger(self, works)

    def test_work_header_capture_preserves_absence_and_duplicate_fields(self) -> None:
        from http.client import HTTPMessage
        from types import SimpleNamespace
        from unittest.mock import patch

        for handler_type, inherited in (
            (_NativeCodexGatewayHandler, _CodexToolGatewayHandler),
            (_ClaudeGatewayHandler, FakeProviderHandler),
        ):
            for values in ([], ["work-one"], ["work-one", "work-one"], ["work-one", "work-two"]):
                with self.subTest(handler=handler_type.__name__, values=values):
                    handler = object.__new__(handler_type)
                    handler.headers = HTTPMessage()
                    for value in values:
                        handler.headers["X-Hormuz-Work-Id"] = value
                    handler.server = SimpleNamespace(work_header_values=[])
                    with patch.object(inherited, "do_POST") as exchange:
                        handler.do_POST()
                    exchange.assert_called_once_with()
                    self.assertEqual(handler.server.work_header_values, [values])

    def test_optional_fixture_binding_retains_fixed_custody_and_owner_arguments(self) -> None:
        from unittest.mock import patch

        fixture = NativeMacRelayTests("test_off_is_exact_and_listener_ends_with_client")
        fixture.root = Path("/private/tmp/synthetic-native-fixture")
        fixture.key = "12345678-1234-1234-1234-123456789abc"
        fixture.broker = fixture.root / "broker"
        fixture.optimizer = fixture.root / "optimizer"
        with patch.dict(os.environ, {"HORMUZ_NATIVE_RELAY_BINARY": "/synthetic/native-relay"}, clear=True), \
                patch("tests.test_native_macos_relay.subprocess.Popen") as spawn:
            spawn.return_value.poll.return_value = 0
            try:
                fixture._launch()
                unbound = spawn.call_args.args[0]
                fixture._launch(work_id="work-owned")
                bound = spawn.call_args.args[0]
            finally:
                fixture.doCleanups()
        self.assertNotIn("--work-id", unbound)
        self.assertEqual(bound, [*unbound, "--work-id", "work-owned"])
        self.assertEqual(unbound[-2:], ["--owner-socket", str(fixture.root / "lease")])
        self.assertEqual(unbound[unbound.index("--credential-helper") + 1], str(fixture.broker))
        self.assertEqual(unbound[unbound.index("--optimizer-helper") + 1], str(fixture.optimizer))


if __name__ == "__main__":
    unittest.main()
