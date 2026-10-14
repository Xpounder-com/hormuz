"""Persistent setup exercised only through the approved coding-client workflow."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests import test_execution_destination_workflow as destination_workflow

provider = destination_workflow.provider


CODEX = r'''#!PYTHON
import http.client, json, os, pathlib, sys, tomllib
from urllib.parse import urlsplit
if "--version" in sys.argv:
    print("codex-cli 0.147.0")
    sys.exit(0)
path = pathlib.Path(os.environ["CODEX_HOME"]) / "config.toml"
text = path.read_text() if path.exists() else ""
config = tomllib.loads(text)
view = {"model": config.get("model", "default-model"),
        "provider": config.get("model_provider", "openai"),
        "preference": config.get("personality"),
        "original_comment": "# personal preference" in text,
        "providers": sorted(config.get("model_providers", {}))}
if "inspect" in sys.argv:
    print(json.dumps(view)); sys.exit(0)
provider = config["model_providers"][view["provider"]]
origin = urlsplit(provider["base_url"])
connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=6)
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
    def configured(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
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
            connected = self.command("connect")
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
