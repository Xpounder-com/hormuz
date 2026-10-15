"""Persistent setup exercised only through the approved coding-client workflow."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile
import selectors
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest

from tests import test_execution_destination_workflow as destination_workflow

provider = destination_workflow.provider


CODEX = r'''#!PYTHON
import http.client, json, os, pathlib, sys, tomllib
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
    connection.request("POST", origin.path.rstrip("/") + "/responses",
        json.dumps({"model": view["model"], "input": "Summarize this fixture.", "max_output_tokens": 8}),
        {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ[provider["env_key"]]})
    response = connection.getresponse()
    view.update(status=response.status, body=json.loads(response.read()),
                headers={k.lower():v for k,v in response.getheaders()})
    print(json.dumps(view))
finally:
    connection.close()
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
            *(["--executable", str(self.executable)] if verb == "connect" else []), *options],
            env=self.environment, capture_output=True, text=True, timeout=18)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def client(self, *options):
        result = subprocess.run([str(self.executable), *options], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

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
            self.environment["PYTHONPATH"] = str(fault)
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
            self.assertEqual({key: status[key] for key in expected}, expected)
            self.assertEqual((len(local_requests), len(cloud_requests)), (1, 0))
