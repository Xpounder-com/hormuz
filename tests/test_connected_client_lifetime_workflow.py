"""Persistent setup exercised only through the approved coding-client workflow."""
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import tempfile
import tarfile
import socket
import http.client
from urllib.parse import urlsplit
import selectors
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
import zlib

from tests import test_execution_destination_workflow as destination_workflow
from hormuz.contracts import ContractValidationError, validate_contract

provider = destination_workflow.provider


CODEX = r'''#!PYTHON
import http.client, json, os, pathlib, subprocess, sys, tomllib
from urllib.parse import urlsplit
if "--version" in sys.argv:
    if "DESTINATION_CLOUD_KEY" in os.environ or "DESTINATION_WORKFLOW_TOKEN" in os.environ:
        sys.exit(6)
    version_file = pathlib.Path(__file__).with_name("client-version.txt")
    version = version_file.read_text().strip() if version_file.exists() else "0.147.0"
    print("codex-cli " + version)
    sys.exit(0)
path = pathlib.Path(os.environ["CODEX_HOME"]) / "config.toml"
text = path.read_text() if path.exists() else ""
config = tomllib.loads(text)
if "copy-provider-example" in sys.argv:
    block = text[text.rindex("\n[model_providers.hormuz_"):]
    path.write_text("developer_instructions = " + "'" * 3 + block + "'" * 3 + "\n" + text)
    config = tomllib.loads(path.read_text())
if "edit-provider" in sys.argv:
    path.write_text(text + 'request_max_retries = 1\n')
    config = tomllib.loads(path.read_text())
if "edit-model" in sys.argv:
    path.write_text(text.replace('model = "local-code"', 'model = "user-model"'))
    config = tomllib.loads(path.read_text())
if "trust-project" in sys.argv:
    path.write_text(text + '\n[projects."/synthetic/project"]\ntrust_level = "trusted"\n')
    config = tomllib.loads(path.read_text())
view = {"model": config.get("model", "default-model"),
        "provider": config.get("model_provider", "openai"),
        "preference": config.get("personality"),
        "instructions": config.get("developer_instructions"),
        "original_comment": "# personal preference" in text,
        "providers": sorted(config.get("model_providers", {})),
        "base_url": config.get("model_providers", {}).get(config.get("model_provider"), {}).get("base_url"),
        "provider_preferences": {key: value.get("request_max_retries") for key,value in config.get("model_providers", {}).items()}}
if any(option in sys.argv for option in ("inspect", "edit-provider", "edit-model", "copy-provider-example")):
    print(json.dumps(view)); sys.exit(0)
provider = config["model_providers"][view["provider"]]
if "auth" in provider:
    auth = provider["auth"]
    credential = subprocess.run([auth["command"], *auth.get("args", [])],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
        cwd=auth.get("cwd"), timeout=auth.get("timeout_ms", 5000) / 1000).stdout.strip()
    if not credential: sys.exit(7)
else:
    credential = os.environ[provider["env_key"]]
view["credential_in_configuration"] = credential in text
origin = urlsplit(provider["base_url"])
connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=6)
if "idle" in sys.argv:
    connection.connect()
    connection.send(b"GET /v1/router/state HTTP/1.1\r\n")
    print(json.dumps({"connected": True}), flush=True)
    try:
        closed = connection.sock.recv(1) == b""
    except OSError:
        closed = False
    finally:
        connection.close()
    print(json.dumps({"closed": closed}), flush=True)
    sys.exit(0)
try:
    control = "work-state" in sys.argv
    connection.request("GET" if control else "POST", origin.path.rstrip("/") + ("/work/state" if control else "/responses"),
        None if control else json.dumps({"model": "local-code" if "request-local-model" in sys.argv else view["model"],
                    "input": "Consider this fixture." if "unknown-task" in sys.argv else "Summarize this fixture.",
                    "max_output_tokens": 8}),
        {"Content-Type": "application/json", "Authorization": "Bearer " + credential,
         **({"X-Hormuz-Routing-Mode": "local_only"} if "local-only" in sys.argv else {})})
    response = connection.getresponse()
    view.update(status=response.status, body=json.loads(response.read()),
                headers={k.lower():v for k,v in response.getheaders()})
    print(json.dumps(view))
finally:
    connection.close()
'''

LLAMA = r'''#!PYTHON
import http.server,json,os,pathlib,signal,sys,time,subprocess
if any("TOKEN" in k or "CLOUD_KEY" in k for k in os.environ): sys.exit(9)
if pathlib.Path(__file__).with_name("fail-start").exists(): sys.exit(3)
args=sys.argv[1:]
port=int(args[args.index("--port")+1])
model=args[args.index("--alias")+1]
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        if pathlib.Path(__file__).with_name("lose-owner-on-health").exists():
            parent=int(subprocess.check_output(["ps","-o","ppid=","-p",str(os.getppid())],text=True,timeout=1).strip())
            command=subprocess.check_output(["ps","-o","command=","-p",str(parent)],text=True,timeout=1)
            if "hormuz.connected_client_runtime" not in command or str(pathlib.Path(__file__).parent.parent) not in command:
                raise RuntimeError("Not the fixture-owned ancestor")
            os.kill(parent,signal.SIGKILL)
        data=b'{"status":"ok"}'
        self.send_response(200);self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        data=json.dumps({"id":"managed-fixture","object":"response","status":"completed","model":model,
            "output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":
                "LOCAL" if body.get("instructions", "").startswith("You assess coding task fit.") else "MANAGED_LOCAL"}]}],
            "usage":{"input_tokens":4,"output_tokens":2,"total_tokens":6}}).encode()
        self.send_response(200);self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
server=http.server.HTTPServer(("127.0.0.1",port),Handler)
marker="I srv  llama_server: listening on http://127.0.0.1:"+str(port)+"\n"
if pathlib.Path(__file__).with_name("split-output").exists():
    os.write(2,marker[:20].encode());time.sleep(0.4)
    os.write(2,marker[20:].encode().ljust(4096,b" "))
else:
    print(marker,file=sys.stderr,flush=True)
server.timeout=0.2
stopping=False
def stop(*_):
    global stopping
    if pathlib.Path(__file__).with_name("slow-stop").exists(): time.sleep(1)
    stopping=True
signal.signal(signal.SIGTERM,stop)
try:
    while not stopping: server.handle_request()
finally:
    server.server_close()
    pathlib.Path(__file__).with_name("stopped").write_text("stopped")
'''


class ConnectedClientLifetimeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir(mode=0o700)
        self.executable = self.root / "codex-fixture"
        self.executable.write_text(CODEX.replace("#!PYTHON", "#!" + sys.executable, 1))
        self.executable.chmod(0o700)
        self.environment = {**os.environ, "CODEX_HOME": str(self.home),
                            "DESTINATION_WORKFLOW_TOKEN": "fixture-coding-client-credential",
                            "DESTINATION_CLOUD_KEY": "fixture-cloud-destination-credential"}
        self.path = self.root / "gateway.json"

    def command(self, verb, *options, check=True):
        result = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(self.path),
            "client", verb, "codex", "--client-home", str(self.home),
            *(["--executable", str(self.executable)] if verb in {"connect", "setup"} else []), *options],
            env=self.environment, capture_output=True, text=True, timeout=18)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def client(self, *options, cwd=None):
        result = subprocess.run([str(self.executable), *options], env=self.environment,
                                cwd=cwd, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def managed_configuration(self, cloud):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        engine = self.root / "engine"
        engine.mkdir()
        executable = engine / "llama-server"
        executable.write_text(LLAMA.replace("#!PYTHON", "#!" + sys.executable, 1))
        executable.chmod(0o700)
        archive = self.root / "runtime.tar.gz"
        (engine / "libfixture.1.dylib").write_bytes(b"synthetic runtime dependency")
        (engine / "libfixture.dylib").symlink_to("libfixture.1.dylib")
        with tarfile.open(archive, "w:gz") as bundle:
            for path in engine.iterdir():
                bundle.add(path, arcname=path.name)
        model = self.root / "fixture.gguf"
        model.write_bytes(b"GGUF synthetic external runtime fixture")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        value = destination_workflow.ExecutionDestinationWorkflowTests.configuration(self, f"http://127.0.0.1:{port}", cloud)
        value["model_routes"]["local-code"].update(model_revision="sha256:" + digest, capabilities=["responses", "text"])
        value["route_admission"] = {"mode": "local_only", "local_routes": {"local-code": {
            "qualification_id": "synthetic-managed-v1", "model_revision": "sha256:" + digest,
            "max_request_bytes": 4096, "max_output_tokens": 8, "task_types": ["summarization"]}}}
        frozen = json.loads((Path(__file__).parent / "fixtures/managed-llama-profile-v1.json").read_text())
        value["managed_llama"] = {**frozen, "executable": str(executable),
            "runtime_archive": str(archive), "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "model_file": str(model), "model_sha256": digest, "startup_timeout_seconds": 3}
        self.path.write_text(json.dumps(value))
        (self.home / "config.toml").write_text('personality = "none"\nmodel = "original-model"\n')
        return value, port, engine

    def local_setup_options(self, value):
        managed = value["managed_llama"]
        profile = self.root / "qualification.json"
        frozen = json.loads((Path(__file__).parent / "fixtures/local-setup-profile-v1.json").read_text())
        profile.write_text(json.dumps({**frozen,
            "system": platform.system(), "machine": platform.machine(),
            "runtime_sha256": managed["runtime_sha256"], "model_sha256": managed["model_sha256"],
        }))
        self.path.unlink()
        return ("--qualification", str(profile), "--runtime-archive", managed["runtime_archive"],
                "--llama-executable", managed["executable"], "--model-file", managed["model_file"],
                "--credential-env", "DESTINATION_WORKFLOW_TOKEN")

    def test_local_setup_denies_missing_model_before_changing_settings_or_starting_engine(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, port, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            model = Path(value["managed_llama"]["model_file"])
            model.unlink()
            for case in ("missing", "corrupt"):
                with self.subTest(case=case):
                    if case == "corrupt":
                        model.write_bytes(b"PRIVATE_CORRUPT_MODEL_SENTINEL")
                    result = self.command("setup", *options, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_runtime_invalid")
                    self.assertNotIn("PRIVATE_CORRUPT_MODEL_SENTINEL", result.stderr + result.stdout)
            self.assertFalse(self.path.exists())
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
            self.assertFalse((engine / "stopped").exists())
            self.assertEqual(requests, [])

    def test_local_setup_connects_without_manual_router_token_exports(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)[:-2]
            self.environment.pop("DESTINATION_WORKFLOW_TOKEN", None)
            self.environment.pop("HORMUZ_GATEWAY_TOKEN", None)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            connected = json.loads(self.command("setup", *options).stdout)
            validate_contract(connected)
            self.assertEqual(connected["connection"]["phase"], "connected")
            result = self.client()
            self.assertEqual(result["body"]["output"][0]["content"][0]["text"], "MANAGED_LOCAL")
            self.assertFalse(result["credential_in_configuration"])
            self.assertEqual(json.loads(self.command("status").stdout)["router"], "ready")
            diagnosis = json.loads(self.command("diagnose", "--project", str(self.root)).stdout)
            self.assertEqual(diagnosis["connection"]["phase"], "connected")
            self.assertEqual(diagnosis["configuration_binding"], "matching")
            self.assertEqual(diagnosis["action"], "none")
            self.assertEqual(set(diagnosis["override_diagnosis"]["effective_routing"].values()), {"matches"})
            self.assertEqual(diagnosis["override_diagnosis"]["running_session"], "unobserved")
            stopped = json.loads(self.command("disconnect").stdout)
            self.assertEqual(stopped["local_runtime"], {"ownership": "owned", "state": "stopped"})
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertTrue((engine / "stopped").exists())
            self.assertEqual(requests, [])

    def test_local_setup_enables_shadow_routing_without_manual_configuration_or_tokens(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)[:-2]
            self.environment.pop("DESTINATION_WORKFLOW_TOKEN", None)
            self.environment.pop("HORMUZ_GATEWAY_TOKEN", None)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            preview = json.loads(self.command("setup", *options, "--shadow-router", "--dry-run").stdout)
            self.assertEqual((preview["schema_version"], preview["configuration"]["shadow_router"]), (3, "shadow"))
            self.assertFalse(self.path.exists())
            connected = json.loads(self.command("setup", *options, "--shadow-router").stdout)
            validate_contract(connected)
            validate_contract(json.loads((Path(__file__).parent / "fixtures/local-setup-v3.json").read_text()))
            result = self.client()
            self.assertEqual(result["body"]["output"][0]["content"][0]["text"], "MANAGED_LOCAL")
            work = self.client("work-state")["body"]["works"][0]
            advice = work["attempts"][0]["shadow_routing"]
            self.assertEqual((advice["status"], advice["recommendation"], advice["actual_locality"]),
                             ("classified", "local", "local"))
            repeated = json.loads(self.command("setup", *options, "--shadow-router").stdout)
            self.assertEqual(repeated["connection"]["generation"], connected["connection"]["generation"])
            self.command("disconnect")
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertTrue((engine / "stopped").exists())
            self.assertEqual(requests, [])

    def test_local_credential_preview_leaves_no_persistent_credential_or_connection(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)[:-2]
            self.environment.pop("DESTINATION_WORKFLOW_TOKEN", None)
            self.environment.pop("HORMUZ_GATEWAY_TOKEN", None)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            preview = json.loads(self.command("setup", *options, "--dry-run").stdout)
            validate_contract(preview)
            frozen = json.loads((Path(__file__).parent / "fixtures/local-setup-v2.json").read_text())
            validate_contract(frozen)
            self.assertEqual((preview["schema_id"], preview["schema_version"]),
                             (frozen["schema_id"], frozen["schema_version"]))
            for key in ("operation", "mode", "qualification_id", "authentication", "model"):
                self.assertEqual(preview["configuration"][key], frozen["configuration"][key])
            credential = self.path.with_name(self.path.name + ".state") / "router-credential-v1.json"
            self.assertFalse(credential.exists())
            self.assertFalse(self.path.exists())
            self.assertFalse((engine / "stopped").exists())
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual(requests, [])

    def test_repeated_local_setup_rejects_a_replaced_credential_without_overwriting_it(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)[:-2]
            self.environment.pop("DESTINATION_WORKFLOW_TOKEN", None)
            self.environment.pop("HORMUZ_GATEWAY_TOKEN", None)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            self.command("setup", *options)
            self.assertEqual(self.client()["status"], 200)
            source = Path(json.loads(self.path.read_text())["identities"][0]["token_file"])
            replacement = json.dumps({"schema_id": "hormuz.local-router-credential", "schema_version": 1,
                                      "token": "e" * 64})
            source.write_text(replacement)
            result = self.command("setup", *options, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_authentication_unsupported")
            self.assertEqual(source.read_text(), replacement)
            self.assertEqual(json.loads(self.command("disconnect").stdout)["phase"], "disconnected")
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_local_setup_reuses_its_credential_and_restores_the_auth_provider(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)[:-2]
            self.environment.pop("DESTINATION_WORKFLOW_TOKEN", None)
            self.environment.pop("HORMUZ_GATEWAY_TOKEN", None)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            first = json.loads(self.command("setup", *options).stdout)
            source = Path(json.loads(self.path.read_text())["identities"][0]["token_file"])
            credential_inode = source.stat().st_ino
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(source.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(self.client("trust-project")["status"], 200)
            repeated = json.loads(self.command("setup", *options).stdout)
            self.assertEqual(repeated["connection"]["generation"], first["connection"]["generation"])
            stopped = json.loads(self.command("disconnect").stdout)
            self.assertEqual(stopped["retained_settings"], [])
            self.assertEqual(self.client("inspect")["providers"], [])
            self.assertIn('trust_level = "trusted"', (self.home / "config.toml").read_text())
            reconnected = json.loads(self.command("setup", *options).stdout)
            self.assertNotEqual(reconnected["connection"]["generation"], first["connection"]["generation"])
            self.assertEqual(source.stat().st_ino, credential_inode)
            self.assertEqual(self.client()["status"], 200)
            self.client("edit-model")
            stopped = json.loads(self.command("disconnect").stdout)
            self.assertEqual(stopped["retained_settings"], ["model"])
            self.assertEqual(self.client("inspect")["model"], "user-model")
            self.assertEqual(self.client("inspect")["providers"], [])
            self.assertEqual(requests, [])

    def test_local_authentication_uses_the_installed_helper_from_an_unrelated_project(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)[:-2]
            self.environment.pop("DESTINATION_WORKFLOW_TOKEN", None)
            self.environment.pop("HORMUZ_GATEWAY_TOKEN", None)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            project = self.root / "project"
            project.mkdir()
            marker = project / "shadow-imported"
            (project / "hormuz.py").write_text(
                'from pathlib import Path\nPath("shadow-imported").write_text("unexpected")\nprint("UNTRUSTED_HELPER")\n')
            self.command("setup", *options)
            result = self.client(cwd=project)
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["body"]["output"][0]["content"][0]["text"], "MANAGED_LOCAL")
            self.assertFalse(marker.exists())
            self.assertEqual(json.loads(self.command("disconnect").stdout)["retained_settings"], [])
            self.assertEqual(requests, [])

    def test_local_setup_generates_configuration_and_reuses_assets_through_disconnect(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            preview = json.loads(self.command("setup", *options, "--dry-run").stdout)
            expected = json.loads((Path(__file__).parent / "fixtures/local-setup-v1.json").read_text())
            validate_contract(expected)
            self.assertEqual((preview["schema_id"], preview["schema_version"]), (expected["schema_id"], expected["schema_version"]))
            for key in ("operation", "mode", "qualification_id", "credential_env", "model"):
                self.assertEqual(preview["configuration"][key], expected["configuration"][key])
            self.assertEqual(preview["connection"]["router"], "not_started")
            validate_contract(preview)
            self.assertFalse(self.path.exists())
            self.assertFalse((engine / "stopped").exists())
            self.assertEqual(self.client("inspect")["model"], "original-model")
            connected = json.loads(self.command("setup", *options).stdout)
            validate_contract(connected)
            self.assertEqual(connected["configuration"]["operation"], "create")
            self.assertEqual(connected["connection"]["phase"], "connected")
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            raw = self.path.read_text()
            self.assertNotIn(self.environment["DESTINATION_WORKFLOW_TOKEN"], raw)
            configuration = json.loads(raw)
            self.assertEqual(set(configuration["upstreams"]), {"local-llama"})
            self.assertTrue(Path(configuration["managed_llama"]["model_file"]).samefile(value["managed_llama"]["model_file"]))
            self.assertTrue(Path(configuration["managed_llama"]["runtime_archive"]).samefile(value["managed_llama"]["runtime_archive"]))
            self.assertEqual(self.client()["body"]["output"][0]["content"][0]["text"], "MANAGED_LOCAL")
            repeated = json.loads(self.command("setup", *options).stdout)
            self.assertEqual(repeated["configuration"]["operation"], "reuse")
            self.assertEqual(repeated["connection"]["generation"], connected["connection"]["generation"])
            self.assertEqual(self.path.read_text(), raw)
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "connected")
            disconnected = json.loads(self.command("disconnect").stdout)
            self.assertEqual(disconnected["local_runtime"]["state"], "stopped")
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertTrue((engine / "stopped").exists())
            self.assertTrue(Path(value["managed_llama"]["model_file"]).exists())
            self.assertTrue(Path(value["managed_llama"]["runtime_archive"]).exists())
            self.assertEqual(requests, [])

    def test_local_setup_refuses_a_replaced_state_directory_before_reconnecting(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            self.command("setup", *options)
            self.command("disconnect")
            state = self.path.with_name(self.path.name + ".state")
            state.rename(self.root / "saved-state")
            foreign = self.root / "foreign-state"
            foreign.mkdir(mode=0o700)
            state.symlink_to(foreign, target_is_directory=True)
            result = self.command("setup", *options, check=False)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_directory_unsafe")
            self.assertEqual(list(foreign.iterdir()), [])
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_local_setup_can_reconnect_after_disconnect_without_rewriting_configuration(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            first = json.loads(self.command("setup", *options).stdout)
            raw = self.path.read_text()
            self.assertEqual(self.client()["status"], 200)
            self.command("disconnect")
            second = json.loads(self.command("setup", *options).stdout)
            self.assertNotEqual(second["connection"]["generation"], first["connection"]["generation"])
            self.assertEqual(second["configuration"]["operation"], "reuse")
            self.assertEqual(self.path.read_text(), raw)
            self.assertEqual(self.client()["status"], 200)
            self.assertEqual(json.loads(self.command("disconnect").stdout)["local_runtime"]["state"], "stopped")
            self.assertEqual(requests, [])

    def test_local_setup_rejects_malformed_qualification_without_leaking_its_contents(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            profile = Path(options[1])
            profile.write_text('["PRIVATE_PROFILE_SENTINEL"]')
            result = self.command("setup", *options, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_router_configuration_unsupported")
            self.assertNotIn("PRIVATE_PROFILE_SENTINEL", result.stderr + result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(self.path.exists())
            self.assertFalse((engine / "stopped").exists())
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_local_setup_preserves_an_edited_configuration_and_the_active_connection(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            connected = json.loads(self.command("setup", *options).stdout)["connection"]
            changed = json.loads(self.path.read_text())
            changed["policies"]["organization"]["max_output_tokens"] = 7
            text = json.dumps(changed, sort_keys=True, indent=2) + "\n"
            self.path.write_text(text)
            result = self.command("setup", *options, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_setup_changed")
            self.assertEqual(self.path.read_text(), text)
            self.assertEqual(json.loads(self.command("status").stdout)["generation"], connected["generation"])
            self.assertEqual(self.client()["status"], 200)
            self.assertEqual(json.loads(self.command("disconnect").stdout)["configuration"], "not_owned")
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_local_setup_recovers_a_failed_start_using_its_existing_configuration(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            marker = engine / "fail-start"
            marker.write_text("synthetic startup failure")
            failed = self.command("setup", *options, check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "connected_client_runtime_start_failed")
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            raw = self.path.read_text()
            marker.unlink()
            connected = json.loads(self.command("setup", *options).stdout)
            self.assertEqual(connected["configuration"]["operation"], "reuse")
            self.assertEqual(self.path.read_text(), raw)
            self.assertEqual(self.client()["status"], 200)
            self.assertEqual(json.loads(self.command("disconnect").stdout)["local_runtime"]["state"], "stopped")
            self.assertEqual(requests, [])

    def test_local_setup_preserves_an_unrelated_malformed_configuration_without_traceback(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            text = json.dumps({"listen": {"port": 8765}, "upstreams": {"local-llama": {
                "base_url": 123, "PRIVATE_CONFIG_SENTINEL": "unrelated private setting"}}})
            self.path.write_text(text)
            self.path.chmod(0o600)
            result = self.command("setup", *options, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_setup_changed")
            self.assertNotIn("PRIVATE_CONFIG_SENTINEL", result.stderr + result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(self.path.read_text(), text)
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_local_setup_blocks_unsupported_profile_and_client_rows_before_publication(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            profile_path = Path(options[1])
            original = json.loads(profile_path.read_text())
            for change, code in (
                ({"schema_version": 3}, "router_configuration_unsupported"),
                ({"runtime_sha256": "a" * 63}, "router_configuration_unsupported"),
                ({"system": "unsupported-platform"}, "platform_unsupported"),
                ({"client_version": "0.0.0"}, "version_unsupported"),
                ({"capabilities": ["responses", "text", "unqualified"]}, "router_configuration_unsupported"),
                ({"max_output_tokens": 513}, "router_configuration_unsupported"),
                ({"extra": "PRIVATE_PROFILE_SENTINEL"}, "router_configuration_unsupported"),
            ):
                with self.subTest(change=change):
                    profile_path.write_text(json.dumps({**original, **change}))
                    result = self.command("setup", *options, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_" + code)
                    self.assertNotIn("PRIVATE_PROFILE_SENTINEL", result.stderr + result.stdout)
                    self.assertFalse(self.path.exists())
            self.assertFalse((engine / "stopped").exists())
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_local_setup_blocks_aliases_of_client_settings_and_private_lifecycle_state(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            (self.home / "config.toml").unlink()
            (self.home / "sub").mkdir()
            (self.root / "home-alias").symlink_to(self.home, target_is_directory=True)
            for target in (self.home / "sub/../config.toml", self.root / "home-alias/config.toml",
                           self.home / "sub/../.hormuz-router/connection.json"):
                with self.subTest(target=target):
                    self.path = target
                    result = self.command("setup", *options, check=False)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_file_unsafe")
                    self.assertFalse((self.home / "config.toml").exists())
                    self.assertFalse((self.home / ".hormuz-router/connection.json").exists())
            self.assertFalse((engine / "stopped").exists())
            self.assertEqual(requests, [])

    def test_local_setup_recovers_its_interrupted_publication_and_preserves_foreign_links(self):
        with provider("CLOUD_MUST_STAY_UNUSED") as (cloud, requests):
            value, _, _ = self.managed_configuration(cloud)
            options = self.local_setup_options(value)
            self.addCleanup(lambda: self.command("disconnect", check=False))
            self.command("setup", *options)
            self.command("disconnect")
            raw = self.path.read_text()
            # External filesystem fixture for the documented v1 publication window.
            identity = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:16]
            pending = self.path.parent / (".hormuz-setup-v1-" + identity + "-interrupted")
            os.link(self.path, pending)
            result = json.loads(self.command("setup", *options).stdout)
            self.assertEqual(result["connection"]["phase"], "connected")
            self.assertEqual(result["configuration"]["operation"], "reuse")
            self.assertFalse(pending.exists())
            self.assertEqual(self.path.read_text(), raw)
            self.assertEqual(self.client()["status"], 200)
            self.command("disconnect")
            foreign = self.path.parent / "unrelated-copy.json"
            os.link(self.path, foreign)
            failed = self.command("setup", *options, check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(json.loads(failed.stderr)["error"]["code"], "connected_client_file_unsafe")
            self.assertTrue(foreign.exists())
            self.assertEqual(foreign.read_text(), raw)
            self.assertEqual(self.client("inspect")["model"], "original-model")
            self.assertEqual(requests, [])

    def test_connect_starts_verified_local_engine_and_disconnect_stops_only_its_engine(self):
        with provider("EXTERNAL_ENGINE") as (external, external_requests):
            value, port, engine = self.managed_configuration(external)
            try:
                preview = json.loads(self.command("connect", "--dry-run").stdout)
                expected_preview = json.loads((Path(__file__).parent / "fixtures/connected-client-preview-v2.json").read_text())
                self.assertEqual(preview["local_runtime"], expected_preview["local_runtime"])
                self.assertEqual(preview["schema_version"], 2)
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                connected = json.loads(self.command("connect").stdout)
                repeated = json.loads(self.command("connect").stdout)
                self.assertEqual(repeated["generation"], connected["generation"])
                result = self.client("local-only")
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]), (200, "MANAGED_LOCAL"))
                expected_status = json.loads((Path(__file__).parent / "fixtures/connected-client-status-v2.json").read_text())
                self.assertEqual(connected["local_runtime"], expected_status["local_runtime"])
                self.assertEqual(connected["schema_version"], expected_status["schema_version"])
                validate_contract(connected)
                status = json.loads(self.command("status").stdout)
                self.assertEqual(status["local_runtime"], connected["local_runtime"])
                disconnected = json.loads(self.command("disconnect").stdout)
                self.assertEqual(disconnected["local_runtime"], {"ownership": "owned", "state": "stopped"})
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertTrue((engine / "stopped").exists())
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
                origin = urlsplit(external)
                connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=2)
                try:
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read(4096)
                finally:
                    connection.close()
                self.assertEqual(external_requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_invalid_runtime_archive_fails_without_activation_or_private_traceback(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            archive = Path(value["managed_llama"]["runtime_archive"])
            archive.write_bytes(b"not a gzip archive")
            value["managed_llama"]["runtime_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.path.write_text(json.dumps(value))
            try:
                failure = self.command("connect", check=False)
                self.assertEqual(failure.returncode, 2, failure.stderr)
                self.assertEqual(json.loads(failure.stderr)["error"]["code"], "connected_client_runtime_invalid")
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_owned_runtime_becomes_ready_when_native_output_is_fragmented(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            _, _, engine = self.managed_configuration(external)
            (engine / "split-output").touch()
            try:
                connected = json.loads(self.command("connect").stdout)
                self.assertEqual(connected["local_runtime"]["state"], "ready")
                result = self.client("local-only")
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]), (200, "MANAGED_LOCAL"))
                self.assertEqual(json.loads(self.command("disconnect").stdout)["local_runtime"]["state"], "stopped")
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_oversized_archive_member_cannot_activate_an_engine(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            member = tarfile.TarInfo("llama-server")
            member.size = 1024**3 + 1
            archive = Path(value["managed_llama"]["runtime_archive"])
            # A header only; routine tests never expand a gigabyte payload.
            archive.write_bytes(gzip.compress(member.tobuf(format=tarfile.USTAR_FORMAT)))
            value["managed_llama"]["runtime_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.path.write_text(json.dumps(value))
            try:
                failure = self.command("connect", check=False)
                self.assertEqual((failure.returncode, json.loads(failure.stderr)["error"]["code"]),
                                 (2, "connected_client_runtime_invalid"))
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_truncated_compressed_archive_is_denied_without_private_traceback(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            archive = Path(value["managed_llama"]["runtime_archive"])
            archive.write_bytes(gzip.compress(b"synthetic truncated archive")[:10])
            value["managed_llama"]["runtime_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.path.write_text(json.dumps(value))
            try:
                failure = self.command("connect", check=False)
                self.assertEqual(failure.returncode, 2, failure.stderr)
                self.assertEqual(json.loads(failure.stderr)["error"]["code"], "connected_client_runtime_invalid")
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_corrupt_deflate_archive_is_denied_without_private_traceback(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            archive = Path(value["managed_llama"]["runtime_archive"])
            member = tarfile.TarInfo("llama-server")
            member.size = 131072
            compressor = zlib.compressobj(wbits=31)
            compressed = compressor.compress(member.tobuf(format=tarfile.USTAR_FORMAT) + b"x" * 65536)
            # Header parsing succeeds; corruption occurs during member reading.
            archive.write_bytes(compressed + compressor.flush(zlib.Z_SYNC_FLUSH) + b"\x07" + b"\0" * 8)
            value["managed_llama"]["runtime_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.path.write_text(json.dumps(value))
            try:
                failure = self.command("connect", check=False)
                self.assertEqual(failure.returncode, 2, failure.stderr)
                self.assertEqual(json.loads(failure.stderr)["error"]["code"], "connected_client_runtime_invalid")
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_managed_engine_cannot_activate_with_an_unrelated_configured_cloud_baseline(self):
        with provider("CLOUD_DESTINATION") as (cloud, requests):
            value, _, engine = self.managed_configuration(cloud)
            value["model_routes"] = {"cloud-code": {"protocol": "openai", "upstream_model": "cloud-model"}, **value["model_routes"]}
            value["policies"]["organization"]["allowed_models"] = ["cloud-code", "local-code"]
            value["route_admission"]["mode"] = "configured"
            self.path.write_text(json.dumps(value))
            try:
                failure = self.command("connect", check=False)
                self.assertEqual(failure.returncode, 2, failure.stdout)
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_managed_setup_refuses_an_occupied_endpoint_and_preserves_its_external_engine(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            value["upstreams"]["local-llama"]["base_url"] = external
            self.path.write_text(json.dumps(value))
            try:
                failure = self.command("connect", check=False)
                self.assertEqual((failure.returncode, json.loads(failure.stderr)["error"]["code"]),
                                 (2, "connected_client_runtime_occupied"))
                self.command("disconnect")
                connection = http.client.HTTPConnection(urlsplit(external).hostname, urlsplit(external).port, timeout=2)
                try:
                    connection.request("GET", "/health")
                    response = connection.getresponse()
                    self.assertEqual((response.status, json.loads(response.read(4096))), (200, {"status": "ok"}))
                finally:
                    connection.close()
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_interrupted_managed_startup_waits_for_owned_engine_cleanup_before_reporting_stopped(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            _, port, engine = self.managed_configuration(external)
            (engine / "lose-owner-on-health").touch()
            (engine / "slow-stop").touch()
            try:
                failure = self.command("connect", check=False)
                self.assertEqual(failure.returncode, 2, failure.stdout)
                status = json.loads(self.command("status").stdout)
                self.assertEqual((status["phase"], status["local_runtime"]["state"]), ("disconnected", "stopped"))
                self.assertTrue((engine / "stopped").exists())
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)
                deadline = time.monotonic() + 4
                while not (engine / "stopped").exists() and time.monotonic() < deadline:
                    time.sleep(0.1)

    def test_failed_owned_engine_startup_restores_settings_without_cloud_dispatch(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            _, port, engine = self.managed_configuration(external)
            (engine / "fail-start").touch()
            try:
                failure = self.command("connect", check=False)
                self.assertEqual((failure.returncode, json.loads(failure.stderr)["error"]["code"]),
                                 (2, "connected_client_runtime_start_failed"))
                status = json.loads(self.command("status").stdout)
                self.assertEqual((status["phase"], status["local_runtime"]["state"]), ("disconnected", "stopped"))
                self.assertEqual(self.client("inspect")["model"], "original-model")
                with socket.socket() as probe:
                    self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_changed_runtime_dependency_cannot_activate_an_engine(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            _, _, engine = self.managed_configuration(external)
            (engine / "libfixture.1.dylib").write_bytes(b"modified dependency")
            try:
                failure = self.command("connect", check=False)
                self.assertEqual((failure.returncode, json.loads(failure.stderr)["error"]["code"]),
                                 (2, "connected_client_runtime_invalid"))
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    def test_cyclic_runtime_link_is_denied_without_private_traceback(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            link = engine / "libfixture.dylib"
            link.unlink()
            link.symlink_to("libfixture.dylib")
            archive = Path(value["managed_llama"]["runtime_archive"])
            with tarfile.open(archive, "w:gz") as bundle:
                for path in engine.iterdir():
                    bundle.add(path, arcname=path.name)
            value["managed_llama"]["runtime_sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.path.write_text(json.dumps(value))
            original_settings = (self.home / "config.toml").read_text()
            try:
                failure = self.command("connect", check=False)
                self.assertEqual(failure.returncode, 2, failure.stderr)
                self.assertEqual(json.loads(failure.stderr)["error"]["code"], "connected_client_runtime_invalid")
                self.assertEqual((self.home / "config.toml").read_text(), original_settings)
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
                self.assertFalse((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                self.command("disconnect", check=False)

    @contextmanager
    def configured(self, *, hold_first=None):
        with provider("LOCAL_DESTINATION", hold_first=hold_first) as (local, local_requests), \
             provider("ORIGINAL_DESTINATION") as (cloud, cloud_requests):
            value = destination_workflow.ExecutionDestinationWorkflowTests.configuration(self, local, cloud)
            value["upstream_timeout_seconds"] = 2
            self.path.write_text(json.dumps(value))
            (self.home / "config.toml").write_text(
                '# personal preference\npersonality = "none"\nmodel = "original-model"\n'
                'model_provider = "original"\n\n[model_providers.original]\n'
                'name = "Original"\nwire_api = "responses"\n'
                f'base_url = "{cloud}/v1"\nenv_key = "DESTINATION_CLOUD_KEY"\n')
            try:
                yield local_requests, cloud_requests
            finally:
                self.command("disconnect", check=False)

    def test_setup_routes_client_and_disconnect_restores_original_configuration(self):
        with self.configured() as (local_requests, cloud_requests):
            with ThreadPoolExecutor(max_workers=2) as callers:
                connections = list(callers.map(lambda _: self.command("connect"), range(2)))
            connected = connections[0]
            self.assertEqual(json.loads(connections[1].stdout)["generation"], json.loads(connected.stdout)["generation"])
            result = self.client()
            self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"],
                              result["preference"], result["original_comment"]),
                             (200, "LOCAL_DESTINATION", "none", True))
            self.assertEqual(result["headers"]["x-hormuz-destination-locality"], "local")
            status = json.loads(self.command("status").stdout)
            self.assertEqual((status["schema_version"], status["phase"], status["router"]),
                             (1, "connected", "ready"))
            self.assertEqual(status["generation"], json.loads(connected.stdout)["generation"])
            self.assertEqual(json.loads(self.command("disconnect").stdout)["phase"], "disconnected")
            restored = self.client()
            self.assertEqual((restored["model"], restored["provider"], restored["preference"],
                              restored["providers"], restored["original_comment"],
                              restored["body"]["output"][0]["content"][0]["text"]),
                             ("original-model", "original", "none", ["original"], True, "ORIGINAL_DESTINATION"))
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 1))

    def test_persistent_connection_composes_with_auto_and_request_local_only(self):
        with self.configured() as (local_requests, cloud_requests):
            value = json.loads(self.path.read_text())
            local_route = {**value["model_routes"]["local-code"],
                           "model_revision": "fixture-weights-v1", "capabilities": ["responses", "text"]}
            value["model_routes"] = {"cloud-code": {"protocol": "openai", "upstream_model": "cloud-model"},
                                     "local-code": local_route}
            value["policies"]["organization"]["allowed_models"] = ["cloud-code", "local-code"]
            value["route_admission"] = {"mode": "auto", "local_routes": {"local-code": {
                "qualification_id": "fixture-q1", "model_revision": "fixture-weights-v1",
                "max_request_bytes": 4096, "max_output_tokens": 8, "task_types": ["summarization"]}}}
            self.path.write_text(json.dumps(value))
            connected = json.loads(self.command("connect").stdout)
            validate_contract(connected)
            selected = self.client()
            self.assertEqual((selected["status"], selected["model"],
                selected["body"]["output"][0]["content"][0]["text"],
                selected["headers"].get("x-hormuz-destination"),
                selected["headers"].get("x-hormuz-routing-mode"),
                selected["headers"].get("x-hormuz-routing-reason")),
                (200, "cloud-code", "LOCAL_DESTINATION", "local-llama", "auto", "qualified_local"))
            blocked = self.client("unknown-task", "local-only")
            self.assertEqual((blocked["status"], blocked["body"]["error"]["code"]),
                             (503, "hormuz_route_local_route_unavailable"))
            self.assertEqual((len(local_requests), cloud_requests), (1, []))
            disconnected = json.loads(self.command("disconnect").stdout)
            validate_contract(disconnected)
            restored = self.client()
            self.assertEqual((disconnected["phase"], disconnected["router"], restored["provider"],
                restored["body"]["output"][0]["content"][0]["text"]),
                ("disconnected", "stopped", "original", "ORIGINAL_DESTINATION"))
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 1))

    def test_lifecycle_receipts_are_discoverable_strict_versioned_contracts(self):
        with self.configured():
            manifest = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                env=self.environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(manifest.returncode, 0, manifest.stderr)
            schemas = {(item["schema_id"], item["schema_version"]) for item in json.loads(manifest.stdout)["schemas"]}
            self.assertIn(("hormuz.connected-client-status", 1), schemas)
            before = json.loads(self.command("status").stdout)
            connected = json.loads(self.command("connect").stdout)
            self.assertEqual(self.client()["status"], 200)
            current = json.loads(self.command("status").stdout)
            disconnected = json.loads(self.command("disconnect").stdout)
            for receipt in (before, connected, current, disconnected):
                self.assertEqual((receipt["schema_id"], receipt["schema_version"]),
                                 ("hormuz.connected-client-status", 1))
                validate_contract(receipt)
                for malformed in ({**receipt, "schema_version": 2}, {**receipt, "unexpected": "field"},
                                  {**receipt, "router": "invented"},
                                  {**receipt, "router": "stopped" if receipt["router"] != "stopped" else "ready"}):
                    with self.assertRaises(ContractValidationError):
                        validate_contract(malformed)

    def test_oversized_model_preview_fails_without_activation_and_can_be_repaired(self):
        with self.configured() as (local_requests, cloud_requests):
            value = json.loads(self.path.read_text())
            oversized = "x" * 2049
            invalid = {**value, "model_routes": {oversized: value["model_routes"]["local-code"]},
                       "policies": {"organization": {**value["policies"]["organization"],
                                                     "allowed_models": [oversized]}}}
            self.path.write_text(json.dumps(invalid))
            rejected = self.command("connect", "--dry-run", check=False)
            self.assertEqual(rejected.returncode, 2, rejected.stderr)
            self.assertEqual(rejected.stdout, "")
            error = json.loads(rejected.stderr)
            self.assertEqual(error, {"schema_id": "hormuz.connected-client-error", "schema_version": 1,
                "error": {"code": "connected_client_configuration_shape_unsupported",
                          "action": "inspect_status_and_configuration"}})
            validate_contract(error)
            before = json.loads(self.command("status").stdout)
            self.assertEqual((before["phase"], before["router"], self.client("inspect")["provider"]),
                             ("disconnected", "stopped", "original"))
            self.assertEqual((local_requests, cloud_requests), ([], []))
            self.path.write_text(json.dumps(value))
            connected = json.loads(self.command("connect").stdout)
            result = self.client()
            self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]),
                             (200, "LOCAL_DESTINATION"))
            current = json.loads(self.command("status").stdout)
            self.assertEqual((current["phase"], current["router"], current["generation"]),
                             ("connected", "ready", connected["generation"]))
            disconnected = json.loads(self.command("disconnect").stdout)
            self.assertEqual((disconnected["phase"], disconnected["router"],
                              self.client("inspect")["provider"]),
                             ("disconnected", "stopped", "original"))
            self.assertEqual((len(local_requests), cloud_requests), (1, []))

    def test_preview_is_a_discoverable_contract_without_activating_routing(self):
        with self.configured():
            manifest = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                env=self.environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(manifest.returncode, 0, manifest.stderr)
            self.assertIn(("hormuz.connected-client-preview", 1),
                {(item["schema_id"], item["schema_version"]) for item in json.loads(manifest.stdout)["schemas"]})
            preview = json.loads(self.command("connect", "--dry-run").stdout)
            frozen = json.loads((Path(__file__).parent / "fixtures/connected-client-preview-v1.json").read_text())
            self.assertEqual({**preview, "settings": {**preview["settings"],
                "model_provider": "hormuz_11111111111111111111111111111111"}}, frozen)
            self.assertEqual(self.client()["provider"], "original")
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            active = json.loads(self.command("connect", "--dry-run").stdout)
            self.command("disconnect")
            for receipt in (preview, active):
                self.assertEqual((receipt["schema_id"], receipt["schema_version"]),
                                 ("hormuz.connected-client-preview", 1))
                validate_contract(receipt)
                for malformed in ({**receipt, "schema_version": 2}, {**receipt, "unexpected": "field"},
                                  {**receipt, "settings": {**receipt["settings"], "web_search": "enabled"}}):
                    with self.assertRaises(ContractValidationError):
                        validate_contract(malformed)

    def test_setup_error_is_a_discoverable_content_free_contract(self):
        with self.configured():
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            (self.root / "client-version.txt").write_text("0.0.0")
            failed = self.command("connect", check=False)
            self.assertEqual(failed.returncode, 2)
            receipt = json.loads(failed.stderr)
            self.assertEqual(receipt, json.loads((Path(__file__).parent / "fixtures/connected-client-error-v1.json").read_text()))
            self.command("disconnect")
            self.assertEqual(self.client()["provider"], "original")
            self.assertEqual((receipt.get("schema_id"), receipt["schema_version"], receipt["error"]),
                ("hormuz.connected-client-error", 1, {"code": "connected_client_version_unsupported",
                "action": "use_pinned_client_or_disconnect"}))
            validate_contract(receipt)
            for malformed in ({**receipt, "schema_version": 2}, {**receipt, "unexpected": "field"},
                              {**receipt, "error": {**receipt["error"], "code": "connected_client_invented"}},
                              {**receipt, "error": {**receipt["error"], "action": "invented"}}):
                with self.assertRaises(ContractValidationError):
                    validate_contract(malformed)
            manifest = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                env=self.environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(manifest.returncode, 0, manifest.stderr)
            discovery = json.loads(manifest.stdout)
            self.assertIn(("hormuz.connected-client-error", 1),
                {(item["schema_id"], item["schema_version"]) for item in discovery["schemas"]})
            self.assertIn("connected_client_version_unsupported", discovery["error_codes"])

    def test_setup_recovers_both_named_and_earlier_version_one_journals(self):
        for schema_id, recover_with in (("hormuz.connected-client-journal", "connect"),
                                       (None, "connect"), (None, "disconnect")):
            with self.subTest(schema_id=schema_id, recover_with=recover_with), self.configured():
                # A pre-activation journal is an external interrupted-write fixture.
                # Observe recovery only through the ordinary client and receipts.
                generation = "1" * 32
                origin = "http://127.0.0.1:" + str(json.loads(self.path.read_text())["listen"]["port"])
                record = {"schema_version": 1, "generation": generation, "phase": "prepared",
                    "client_version": "0.147.0",
                    "installed": {"model": "local-code", "model_provider": "hormuz_" + generation,
                                  "web_search": "disabled"},
                    "original": {"model": "original-model", "model_provider": "original", "web_search": None},
                    "provider_block": '\n[model_providers.hormuz_' + generation + ']\nname = "Hormuz"\n'
                        + 'base_url = ' + json.dumps(origin + '/v1') + '\nwire_api = "responses"\n'
                        + 'requires_openai_auth = false\nenv_key = "DESTINATION_WORKFLOW_TOKEN"\n',
                    "gateway_config": str(self.path.resolve()),
                    "gateway_digest": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                    "actor_id": "developer",
                    "control": str(Path("/tmp").resolve() / ("hormuz-connected-" + str(os.getuid())) / (generation + ".sock"))}
                if schema_id is not None:
                    record["schema_id"] = schema_id
                directory = self.home / ".hormuz-router"
                directory.mkdir(mode=0o700, exist_ok=True)
                journal = directory / "connection.json"
                for unsupported in ({**record, "schema_id": "hormuz.unrelated-journal"},
                                    {**record, "schema_version": 77}):
                    journal.write_text(json.dumps(unsupported))
                    journal.chmod(0o600)
                    rejected = self.command("status", check=False)
                    self.assertEqual((rejected.returncode, json.loads(rejected.stderr)["error"]["code"]),
                                     (2, "connected_client_state_invalid"))
                journal.write_text(json.dumps(record))
                journal.chmod(0o600)
                if recover_with == "disconnect":
                    # Simulate the external settings write completing before
                    # the earlier owner was interrupted during activation.
                    config_path = self.home / "config.toml"
                    installed_text = config_path.read_text().replace('model = "original-model"',
                        'model = "local-code"').replace('model_provider = "original"',
                        'model_provider = "hormuz_' + generation + '"')
                    config_path.write_text('web_search = "disabled"\n' + installed_text + record["provider_block"])
                    recovered = json.loads(self.command("disconnect").stdout)
                    self.assertEqual((recovered["phase"], recovered["router"]), ("disconnected", "stopped"))
                    self.assertEqual((self.client("inspect")["model"], self.client("inspect")["provider"]),
                                     ("original-model", "original"))
                connected = json.loads(self.command("connect").stdout)
                self.assertEqual((connected["phase"], connected["router"]), ("connected", "ready"))
                self.assertEqual(self.client()["status"], 200)
                result = json.loads(self.command("disconnect").stdout)
                self.assertEqual((result["phase"], result["router"]), ("disconnected", "stopped"))
                self.assertEqual(self.client()["provider"], "original")

    def test_managed_version_two_journal_requires_identifier_and_recovers_owned_setup(self):
        with provider("EXTERNAL_ENGINE") as (external, requests):
            value, _, engine = self.managed_configuration(external)
            record = json.loads((Path(__file__).parent / "fixtures/connected-client-journal-v2.json").read_text())
            record["gateway_config"] = str(self.path.resolve())
            record["gateway_digest"] = hashlib.sha256(self.path.read_bytes()).hexdigest()
            record["provider_block"] = record["provider_block"].replace("http://127.0.0.1:8787",
                "http://127.0.0.1:" + str(value["listen"]["port"]))
            record["control"] = str(Path("/tmp").resolve() / ("hormuz-connected-" + str(os.getuid()))
                                    / (record["generation"] + ".sock"))
            directory = self.home / ".hormuz-router"
            directory.mkdir(mode=0o700)
            journal = directory / "connection.json"
            original_text = (self.home / "config.toml").read_text()
            staged_invalid = False
            try:
                malformed = ({key: item for key, item in record.items() if key != "schema_id"},
                             {key: item for key, item in record.items() if key != "managed_llama"},
                             {**record, "managed_llama": False}, {**record, "schema_version": 1},
                             {**record, "schema_version": 77}, {**record, "schema_id": "hormuz.unrelated-journal"})
                for unsupported in malformed:
                    staged_invalid = True
                    journal.write_text(json.dumps(unsupported))
                    journal.chmod(0o600)
                    rejected = self.command("status", check=False)
                    self.assertEqual(rejected.returncode, 2, rejected.stdout)
                    self.assertEqual((rejected.returncode, json.loads(rejected.stderr)["error"]["code"]),
                                     (2, "connected_client_state_invalid"))
                    self.assertEqual((self.home / "config.toml").read_text(), original_text)
                    self.assertFalse((engine / "stopped").exists())
                journal.write_text(json.dumps(record))
                staged_invalid = False
                (self.home / "config.toml").write_text('personality = "none"\nmodel = "local-code"\n'
                    + 'model_provider = "' + record["installed"]["model_provider"] + '"\n'
                    + 'web_search = "disabled"\n' + record["provider_block"])
                connected = json.loads(self.command("connect").stdout)
                self.assertNotEqual(connected["generation"], record["generation"])
                self.assertEqual(connected["local_runtime"], {"ownership": "owned", "state": "ready"})
                result = self.client("local-only")
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"]),
                                 (200, "MANAGED_LOCAL"))
                restored = json.loads(self.command("disconnect").stdout)
                self.assertEqual((restored["phase"], restored["local_runtime"]["state"]), ("disconnected", "stopped"))
                self.assertEqual(self.client("inspect")["model"], "original-model")
                self.assertTrue((engine / "stopped").exists())
                self.assertEqual(requests, [])
            finally:
                if staged_invalid:
                    journal.write_text(json.dumps(record))
                self.command("disconnect", check=False)

    def test_disconnect_preserves_provider_preferences_added_after_setup(self):
        with self.configured():
            connection = json.loads(self.command("connect").stdout)
            self.assertEqual(self.client()["status"], 200)
            edited = self.client("edit-provider")
            installed_provider = edited["provider"]
            self.client("edit-model")
            result = json.loads(self.command("disconnect").stdout)
            restored = self.client()
            self.assertEqual((restored["model"], restored["provider"], restored["preference"],
                              restored["provider_preferences"].get(installed_provider)),
                             ("user-model", "original", "none", 1))
            self.assertEqual(set(result["retained_settings"]),
                             {"model", "model_providers." + installed_provider})
            self.assertEqual(result["generation"], connection["generation"])

    def test_disconnect_removes_owned_provider_when_client_appends_project_trust(self):
        with self.configured() as (local_requests, cloud_requests):
            path = self.home / "config.toml"
            original = path.read_text()
            appended = '\n[projects."/synthetic/project"]\ntrust_level = "trusted"\n'
            self.command("connect")
            self.assertEqual(self.client("trust-project")["status"], 200)
            status = json.loads(self.command("status").stdout)
            self.assertEqual((status["phase"], status["configuration"]), ("connected", "installed"))
            disconnected = json.loads(self.command("disconnect").stdout)
            self.assertEqual((disconnected["phase"], disconnected["router"], disconnected["retained_settings"]),
                             ("disconnected", "stopped", []))
            self.assertEqual(path.read_text(), original + appended)
            self.assertEqual(self.client("inspect")["providers"], ["original"])
            self.assertEqual((len(local_requests), cloud_requests), (1, []))

    def test_setup_preserves_multiline_instructions_that_resemble_settings(self):
        with self.configured():
            path = self.home / "config.toml"
            instructions = 'Example:\nmodel = "example-model"\n[model_providers.example]\n'
            path.write_text("developer_instructions = \"\"\"" + instructions + "\"\"\"\n" + path.read_text())
            self.command("connect")
            connected = self.client()
            self.assertEqual((connected["model"], connected["instructions"], connected["status"]),
                             ("local-code", instructions, 200))
            self.command("disconnect")
            restored = self.client()
            self.assertEqual((restored["model"], restored["instructions"], restored["status"]),
                             ("original-model", instructions, 200))

    def test_disconnect_closes_idle_owned_client_connection_before_reporting_stopped(self):
        with self.configured():
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            idle = subprocess.Popen([str(self.executable), "idle"], env=self.environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(idle.stdout, selectors.EVENT_READ)
                    self.assertTrue(selector.select(timeout=3), "fixture client did not connect")
                    self.assertEqual(json.loads(idle.stdout.readline()), {"connected": True})
                result = json.loads(self.command("disconnect").stdout)
                output, error = idle.communicate(timeout=3)
                self.assertEqual((idle.returncode, json.loads(output), result["phase"], result["router"]),
                                 (0, {"closed": True}, "disconnected", "stopped"), error)
                self.assertEqual(self.client()["provider"], "original")
            finally:
                if idle.poll() is None:
                    idle.terminate()
                idle.communicate(timeout=3)

    def test_setup_rejects_managed_configuration_before_changing_client_settings(self):
        with self.configured() as (local_requests, cloud_requests):
            (self.home / "managed_config.toml").write_text('model_provider = "organization"\n')
            result = self.command("connect", check=False)
            self.assertEqual((result.returncode, json.loads(result.stderr)["error"]["code"]),
                             (2, "connected_client_managed_configuration"))
            self.assertEqual(self.client()["provider"], "original")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual((len(local_requests), len(cloud_requests)), (0, 1))

    def test_setup_rejects_remote_usage_storage_without_changing_client_settings(self):
        with self.configured() as (local_requests, cloud_requests):
            value = json.loads(self.path.read_text())
            value["usage_storage"] = {"backend": "postgresql"}
            self.path.write_text(json.dumps(value))
            result = self.command("connect", "--dry-run", check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_setup_unavailable")
            self.assertEqual(self.client("inspect")["provider"], "original")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_setup_rejects_retired_account_configuration_without_changing_client_settings(self):
        with self.configured() as (local_requests, cloud_requests):
            value = json.loads(self.path.read_text())
            value["authentication"] = {"session_broker": {"enabled": False}}
            self.path.write_text(json.dumps(value))
            result = self.command("connect", "--dry-run", check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_setup_unavailable")
            self.assertEqual(self.client("inspect")["provider"], "original")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_setup_rejects_retired_hosted_billing_without_changing_client_settings(self):
        with self.configured() as (local_requests, cloud_requests):
            value = json.loads(self.path.read_text())
            value["ai_work"] = {"enabled": True, "billing_price_id": "price_retired"}
            self.path.write_text(json.dumps(value))
            result = self.command("connect", "--dry-run", check=False)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_setup_unavailable")
            self.assertEqual(self.client("inspect")["provider"], "original")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_repeated_setup_detects_client_version_drift_without_overwriting_settings(self):
        with self.configured():
            first = json.loads(self.command("connect").stdout)
            with ThreadPoolExecutor(max_workers=2) as callers:
                repeats = list(callers.map(lambda _: json.loads(self.command("connect").stdout), range(2)))
            self.assertEqual([repeat["generation"] for repeat in repeats], [first["generation"], first["generation"]])
            self.assertEqual(self.client()["status"], 200)
            (self.root / "client-version.txt").write_text("0.999.0")
            rejected = self.command("connect", check=False)
            self.assertEqual((rejected.returncode, json.loads(rejected.stderr)["error"]["code"]),
                             (2, "connected_client_version_unsupported"))
            self.assertEqual(self.client("inspect")["model"], "local-code")
            self.command("disconnect")
            self.assertEqual(self.client()["provider"], "original")
            self.assertEqual(json.loads(self.command("disconnect").stdout)["phase"], "disconnected")

    def test_setup_preview_explains_changes_without_activating_routing(self):
        with self.configured() as (local_requests, cloud_requests):
            preview = json.loads(self.command("connect", "--dry-run").stdout)
            self.assertEqual((preview["schema_version"], preview["phase"], preview["scope"],
                              preview["settings"]["model"], preview["settings"]["web_search"]),
                             (1, "preview", "codex_user_defaults", "local-code", "disabled"))
            self.assertEqual(set(preview["changes"]), {"model", "model_provider", "web_search", "provider_table"})
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual(self.client()["provider"], "original")
            connected = self.command("connect")
            active_preview = json.loads(self.command("connect", "--dry-run").stdout)
            self.assertEqual((active_preview["phase"], active_preview["changes"]), ("preview", {}))
            self.assertNotIn("fixture-coding-client-credential", connected.stdout)
            self.assertNotIn("fixture-cloud-destination-credential", connected.stdout)
            self.assertEqual(self.client()["status"], 200)
            self.command("disconnect")
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 1))

    def test_rerunning_setup_recovers_interrupted_owner_and_fences_delayed_old_host(self):
        with self.configured() as (local_requests, cloud_requests):
            fault = self.root / "startup-fault"
            fault.mkdir()
            marker, paused, release = (fault / name for name in ("once", "paused", "release"))
            marker.touch()
            (fault / "sitecustomize.py").write_text(
                "import os, pathlib, sys, time\n"
                "if 'hormuz.connected_client_runtime' in sys.orig_argv:\n"
                + f"    marker = pathlib.Path({str(marker)!r})\n"
                + "    if marker.exists():\n        marker.unlink()\n"
                + f"        pathlib.Path({str(paused)!r}).touch()\n"
                + "        deadline = time.monotonic() + 5\n"
                + f"        while not pathlib.Path({str(release)!r}).exists() and time.monotonic() < deadline:\n"
                + "            time.sleep(0.05)\n")
            self.environment["PYTHONPATH"] = os.pathsep.join(
                item for item in (str(fault), self.environment.get("PYTHONPATH")) if item)
            interrupted = subprocess.Popen([sys.executable, "-m", "hormuz", "--config", str(self.path),
                "client", "connect", "codex", "--client-home", str(self.home),
                "--executable", str(self.executable)], env=self.environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 3
                while not paused.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(paused.exists(), "fixture runtime did not pause")
                self.assertEqual(self.client("inspect")["model"], "local-code")
                interrupted.terminate()
                interrupted.communicate(timeout=3)
                preview = self.command("connect", "--dry-run", check=False)
                self.assertEqual(preview.returncode, 2, preview.stdout)
                self.assertEqual(json.loads(preview.stderr)["error"]["code"], "connected_client_recovery_required")
                self.assertEqual(self.client("inspect")["model"], "local-code")
                recovered = json.loads(self.command("connect").stdout)
                self.assertEqual((recovered["phase"], recovered["router"]), ("connected", "ready"))
                release.touch()
                self.assertEqual(self.client()["status"], 200)
                self.command("disconnect")
                self.assertEqual(self.client()["provider"], "original")
                self.assertEqual((len(local_requests), len(cloud_requests)), (1, 1))
            finally:
                release.touch()
                if interrupted.poll() is None:
                    interrupted.terminate()
                interrupted.communicate(timeout=3)

    def test_repeated_setup_rejects_a_different_gateway_configuration(self):
        with self.configured() as (local_requests, cloud_requests):
            first = json.loads(self.command("connect").stdout)
            value = json.loads(self.path.read_text())
            value["model_routes"]["local-code"]["upstream_model"] = "different-model"
            self.path.write_text(json.dumps(value))
            rejected = self.command("connect", check=False)
            self.assertEqual(rejected.returncode, 2, rejected.stdout)
            self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "connected_client_setup_changed")
            self.assertEqual(json.loads(self.command("status").stdout)["generation"], first["generation"])
            self.assertEqual(self.client()["status"], 200)
            self.assertEqual(local_requests[0]["body"]["model"], "fixture-local-model")
            self.command("disconnect")
            self.assertEqual(self.client()["provider"], "original")

    def test_stale_disconnect_receipt_cannot_remove_a_new_connection(self):
        with self.configured():
            first = json.loads(self.command("connect").stdout)
            self.assertEqual(self.client()["status"], 200)
            self.command("disconnect")
            second = json.loads(self.command("connect").stdout)
            stale = self.command("disconnect", "--generation", first["generation"], check=False)
            self.assertEqual(stale.returncode, 2, stale.stdout)
            self.assertEqual(json.loads(stale.stderr)["error"]["code"], "connected_client_generation_mismatch")
            self.assertEqual(json.loads(self.command("status").stdout)["generation"], second["generation"])
            self.assertEqual(self.client()["status"], 200)
            self.command("disconnect", "--generation", second["generation"])
            self.assertEqual(self.client()["provider"], "original")

    def test_disconnect_stops_owned_router_even_when_configuration_needs_manual_repair(self):
        with self.configured():
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            (self.home / "config.toml").write_text('model = [')
            try:
                result = self.command("disconnect", check=False)
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_configuration_invalid")
                status = json.loads(self.command("status").stdout)
                self.assertEqual((status["phase"], status["router"]), ("restore_required", "stopped"))
            finally:
                # An external user repair is an input, never an inspection of the journal.
                (self.home / "config.toml").write_text('personality = "none"\nmodel = "user-repaired"\n')
                self.command("disconnect")
            self.assertEqual(self.client("inspect")["model"], "user-repaired")

    def test_corrupt_or_future_journal_is_rejected_without_touching_client_configuration(self):
        with self.configured() as (local_requests, cloud_requests):
            directory = self.home / ".hormuz-router"
            directory.mkdir(mode=0o700)
            for record in ({"schema_version": 1, "generation": "0" * 32, "phase": "connected"},
                           {"schema_version": 77, "generation": "0" * 32}):
                with self.subTest(version=record["schema_version"]):
                    path = directory / "connection.json"
                    path.write_text(json.dumps(record))
                    path.chmod(0o600)
                    for command in ("connect", "status", "disconnect"):
                        result = self.command(command, check=False)
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_state_invalid")
                    self.assertEqual(self.client()["provider"], "original")
            self.assertEqual((len(local_requests), len(cloud_requests)), (0, 2))

    def test_status_reports_edited_owned_settings_and_repeat_setup_preserves_them(self):
        with self.configured():
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            self.client("edit-model")
            status = json.loads(self.command("status").stdout)
            self.assertEqual((status["phase"], status["configuration"]), ("connected", "changed"))
            rejected = self.command("connect", check=False)
            self.assertEqual(rejected.returncode, 2, rejected.stdout)
            self.assertEqual(json.loads(rejected.stderr)["error"]["code"], "connected_client_configuration_changed")
            self.assertEqual(self.client("inspect")["model"], "user-model")
            self.command("disconnect")
            self.assertEqual(self.client()["model"], "user-model")

    def test_disconnect_removes_the_owned_table_without_editing_a_copied_example(self):
        with self.configured():
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            edited = self.client("copy-provider-example")
            self.command("disconnect")
            restored = self.client()
            self.assertEqual(restored["instructions"], edited["instructions"])
            self.assertEqual((restored["provider"], restored["providers"], restored["status"]),
                             ("original", ["original"], 200))

    def test_disconnect_cancels_in_flight_owned_request_without_stopping_external_providers(self):
        hold = (threading.Event(), threading.Event())
        with self.configured(hold_first=hold) as (local_requests, cloud_requests):
            self.command("connect")
            client = subprocess.Popen([str(self.executable)], env=self.environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertTrue(hold[0].wait(timeout=3), "fixture provider did not receive request")
                result = json.loads(self.command("disconnect").stdout)
                self.assertEqual((result["phase"], result["router"]), ("disconnected", "stopped"))
                client.communicate(timeout=3)
                self.assertNotEqual(client.returncode, 0, "interrupted request cannot report completion")
                self.assertEqual(self.client()["provider"], "original")
                self.assertEqual((len(local_requests), len(cloud_requests)), (1, 1))
            finally:
                hold[1].set()
                if client.poll() is None:
                    client.terminate()
                client.communicate(timeout=3)

    def test_setup_rejects_an_actor_policy_that_does_not_admit_codex(self):
        with self.configured() as (local_requests, cloud_requests):
            value = json.loads(self.path.read_text())
            value["policies"]["organization"]["allowed_clients"] = ["claude-code"]
            self.path.write_text(json.dumps(value))
            result = self.command("connect", check=False)
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_client_not_allowed")
            self.assertEqual(json.loads(self.command("status").stdout)["phase"], "disconnected")
            self.assertEqual(self.client()["provider"], "original")
            self.assertEqual((len(local_requests), len(cloud_requests)), (0, 1))

    def test_disconnect_restores_client_defaults_when_no_configuration_preexisted(self):
        with self.configured() as (local_requests, cloud_requests):
            (self.home / "config.toml").unlink()
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            self.command("disconnect")
            restored = self.client("inspect")
            self.assertEqual((restored["model"], restored["provider"], restored["providers"]),
                             ("default-model", "openai", []))
            expected = json.loads((Path(__file__).parent / "fixtures" / "connected-client-status-v1.json").read_text())
            status = json.loads(self.command("status").stdout)
            self.assertEqual({**status, "generation": "11111111111111111111111111111111"}, expected)
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 0))

    def test_disconnect_stops_owned_router_when_provider_table_has_invalid_shape(self):
        with self.configured():
            self.command("connect")
            self.assertEqual(self.client()["status"], 200)
            (self.home / "config.toml").write_text('model_providers = []\n')
            try:
                result = self.command("disconnect", check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(json.loads(result.stderr)["error"]["code"], "connected_client_configuration_shape_unsupported")
                status = json.loads(self.command("status").stdout)
                self.assertEqual((status["phase"], status["router"]), ("restore_required", "stopped"))
            finally:
                (self.home / "config.toml").write_text('model = "user-repaired"\n')
                self.command("disconnect")
            self.assertEqual(self.client("inspect")["model"], "user-repaired")
