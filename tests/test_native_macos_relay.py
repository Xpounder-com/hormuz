"""Mac Rust launch integration, with synthetic credentials and loopback only."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from tests.test_client_relay import (
    ACCESS_TOKEN, _CodexToolGateway, _CodexToolGatewayHandler, _Gateway, _GatewayHandler,
)
from tests.test_gateway import FakeProviderHandler


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
        if self._testMethodName == "test_official_codex_on_and_off":
            self.gateway = _NativeCodexGateway()
        elif self._testMethodName == "test_official_claude_streams_through_native_relay":
            self.gateway = _ClaudeGateway()
        else:
            self.gateway = _Gateway()
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
        self._script(self.broker, f"#!{sys.executable}\n" + f"""
import json, sys
assert sys.argv[-1] == "--expected-profile-stdin"
assert json.load(sys.stdin) == json.load(open({str(self.root / 'profile.json')!r}))
print({ACCESS_TOKEN!r})
""")
        self.optimizer = Path(os.environ["HORMUZ_NATIVE_OPTIMIZER_HELPER"])
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

    def _launch(self, *, work_id: str | None = None) -> subprocess.Popen:
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
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=environment, cwd=self.root)
        self.addCleanup(self._stop, process)
        return process

    def _official_client(self, name: str, arguments: list[str], *, bare_claude: bool = False) -> None:
        real = str(Path(os.environ["HORMUZ_NATIVE_OFFICIAL_CLIENT_DIRECTORY"]) / name)
        (self.root / "codex-config").mkdir(mode=0o700, exist_ok=True)
        (self.root / "claude-config").mkdir(mode=0o700, exist_ok=True)
        self._script(self.root / name, f"#!{sys.executable}\n" + f"""
import os, sys
real = {real!r}
if sys.argv[1:] == ["--version"]:
    os.execv(real, [real, "--version"])
os.environ["DISABLE_AUTOUPDATER"] = "1"
os.environ["DISABLE_TELEMETRY"] = "1"
os.environ["DISABLE_ERROR_REPORTING"] = "1"
os.environ["CLAUDE_CONFIG_DIR"] = {str(self.root / 'claude-config')!r}
if not {bare_claude!r}:
    # The official client's supported configuration root applies only to this
    # child, not to the native broker, launcher, or user's installed setup.
    os.environ["CODEX_HOME"] = {str(self.root / 'codex-config')!r}
if {bare_claude!r}:
    # Bare mode explicitly disables OAuth/Keychain reads and expects API-key
    # auth. Use only the invocation's synthetic local relay credential.
    os.environ["ANTHROPIC_API_KEY"] = os.environ.pop("ANTHROPIC_AUTH_TOKEN")
os.execv(real, [real, *{arguments!r}, *sys.argv[1:]])
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


class NativeMacRelayFixtureTests(unittest.TestCase):
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
