"""Capture pinned Codex tool declarations against a no-inference local endpoint."""

import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading


REPOSITORY = Path(__file__).resolve().parents[3]
EVIDENCE = REPOSITORY / "docs/campaigns/local-repair-20261016-v5"
OUTPUT = Path(__file__).resolve().parent


def capture(patch_type):
    pins = json.loads((EVIDENCE / "pins.json").read_text())
    client = Path(pins["client"]["path"])
    if hashlib.sha256(client.read_bytes()).hexdigest() != pins["client"]["sha256"]:
        raise RuntimeError("Pinned native client changed")
    row = {
        "slug": "diagnostic-local", "display_name": "diagnostic-local",
        "description": "No-inference diagnostic endpoint",
        "base_instructions": json.loads((EVIDENCE / "request-profile.json").read_text())["instructions"],
        "default_reasoning_level": "none",
        "supported_reasoning_levels": [{"effort": "none", "description": "Disabled"}],
        "shell_type": "unified_exec", "visibility": "list", "supported_in_api": True,
        "priority": 0, "include_skills_usage_instructions": False,
        "include_plugin_usage_instructions": False, "include_apps_usage_instructions": False,
        "supports_reasoning_summary_parameter": False, "default_reasoning_summary": "none",
        "support_verbosity": False, "default_verbosity": None,
        "apply_patch_tool_type": patch_type, "truncation_policy": {"mode": "bytes", "limit": 10000},
        "experimental_supported_tools": [], "input_modalities": ["text"],
    }
    received = []
    gets = []

    class Capture(BaseHTTPRequestHandler):
        timeout = 3

        def log_message(self, *_):
            pass

        def do_GET(self):
            gets.append(self.path)
            payload = json.dumps({"object": "list", "data": [{"id": row["slug"], "object": "model"}], "models": [row]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if len(received) or not 0 < length <= 65536:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(length))
            received.append({"path": self.path, "tools": body.get("tools"),
                             "instructions_sha256": hashlib.sha256(body.get("instructions", "").encode()).hexdigest()})
            payload = b'{"error":{"message":"Intentional no-inference diagnostic stop","type":"diagnostic"}}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with tempfile.TemporaryDirectory(prefix="hormuz-537-native-tools-") as temporary:
        root = Path(temporary)
        home = root / "home"
        client_home = home / ".codex"
        client_home.mkdir(parents=True)
        project = root / "project"
        project.mkdir()
        server = HTTPServer(("127.0.0.1", 0), Capture)
        server.timeout = 0.5
        thread = threading.Thread(target=server.serve_forever, daemon=False)
        thread.start()
        (client_home / "config.toml").write_text(
            'model = "diagnostic-local"\nmodel_provider = "diagnostic"\n'
            '[model_providers.diagnostic]\nname = "Offline tool declaration capture"\n'
            f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
            f'model_catalog_url = "http://127.0.0.1:{server.server_port}/v1/models"\n'
            'wire_api = "responses"\nrequires_openai_auth = false\nenv_key = "DIAGNOSTIC_GATEWAY_TOKEN"\n'
            'request_max_retries = 0\nstream_max_retries = 0\nwebsocket_max_retries = 0\n'
            'supports_websockets = false\nstream_idle_timeout_ms = 5000\n')
        environment = {"HOME": str(home), "CODEX_HOME": str(client_home), "TMPDIR": str(root),
                       "PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "en_US.UTF-8", "OTEL_SDK_DISABLED": "true",
                       "DIAGNOSTIC_GATEWAY_TOKEN": "synthetic-loopback-credential"}
        command = [str(client), "exec", "--skip-git-repo-check", "--ephemeral", "--json", "--sandbox", "workspace-write",
                   "-c", 'approval_policy="never"', "-c", "agents.enabled=false", "--disable", "multi_agent_v2",
                   "-c", 'model_reasoning_summary="none"', "-c", 'model_reasoning_effort="none"',
                   "-c", "analytics.enabled=false", "-c", "sandbox_workspace_write.network_access=false",
                   "-C", str(project), "Fix the Python implementation in solution.py; this diagnostic stops before inference."]
        process = None
        try:
            process = subprocess.Popen(command, cwd=project, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=2)
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
        report = {"catalog_apply_patch_tool_type": patch_type, "native_model_inferences": 0,
                  "get_paths": gets, "captured_requests": received, "native_exit_code": process.returncode,
                  "native_stderr": stderr, "native_events": [json.loads(line) for line in stdout.splitlines() if line]}
        label = "none" if patch_type is None else patch_type
        (OUTPUT / ("native-tools-catalog-auth-" + label + ".json")).write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"patch_type": patch_type, "get_paths": gets, "requests": len(received),
                          "tools": [{"name": tool.get("name"), "type": tool["type"]}
                                    for request in received for tool in request["tools"]]}))
        if len(received) != 1:
            raise RuntimeError("Expected exactly one no-inference capture")


if __name__ == "__main__":
    capture(None)
    capture("function")
