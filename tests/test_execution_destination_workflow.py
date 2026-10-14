"""Destination behavior at connect -> client request -> result -> disconnect.

Only providers and the coding-client executable are disposable stand-ins. The
configuration CLI, router, governance, transport and temporary storage are real.
"""

from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import sqlite3
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from hormuz.config import GatewayConfig
from hormuz.server import GatewayServer, serve_in_thread


CLIENT = r'''
import http.client, json, os, sys, tomllib
from urllib.parse import urlsplit
value = json.load(sys.stdin)
config = tomllib.loads(value["configuration"])
provider = config["model_providers"][config["model_provider"]]
origin = urlsplit(provider["base_url"])
connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=5)
try:
    connection.request(value.get("method", "POST"), origin.path.rstrip("/") + value.get("path", "/responses"),
        None if value.get("method") == "GET" else json.dumps(value["request"] if value.get("control") else
                   {"model": config["model"], "input": "Reply with the fixture answer.",
                    "max_output_tokens": 8, **value.get("request", {})}),
        {"Content-Type": "application/json",
         "Authorization": "Bearer " + os.environ[provider["env_key"]],
         **provider.get("http_headers", {}), **value.get("headers", {})})
    response = connection.getresponse()
    print(json.dumps({"status": response.status,
                      "headers": dict((k.lower(), v) for k, v in response.getheaders()),
                      "body": json.loads(response.read())}))
finally:
    connection.close()
'''


@contextmanager
def provider(answer, *, status=200):
    received = []

    class Handler(BaseHTTPRequestHandler):
        timeout = 5
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"path": self.path, "body": body,
                             "headers": {k.lower(): v for k, v in self.headers.items()}})
            result = {"id": "fixture-response", "object": "response", "status": "completed",
                      "model": body["model"], "output": [{"type": "message", "role": "assistant",
                      "content": [{"type": "output_text", "text": answer}]}],
                      "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class ExecutionDestinationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.environment = {"DESTINATION_WORKFLOW_TOKEN": "fixture-coding-client-credential",
                            "DESTINATION_CLOUD_KEY": "fixture-cloud-destination-credential"}

    def configuration(self, local, cloud):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        return {
            "listen": {"host": "127.0.0.1", "port": port},
            "database": str(self.root / "usage.sqlite3"),
            "upstreams": {
                "openai": {"base_url": cloud, "api_key_env": "DESTINATION_CLOUD_KEY"},
                "local-llama": {"protocol": "openai", "base_url": local,
                                "locality": "local", "authentication": "none"},
            },
            "identities": [{"token_env": "DESTINATION_WORKFLOW_TOKEN", "actor_id": "developer",
                            "actor_name": "Developer", "team_id": "local", "team_name": "Local",
                            "allowed_clients": ["codex"]}],
            "model_routes": {"local-code": {"protocol": "openai", "destination": "local-llama",
                                           "upstream_model": "fixture-local-model"}},
            "policies": {"organization": {"allowed_clients": ["codex"],
                                          "allowed_models": ["local-code"], "max_output_tokens": 8}},
        }

    @contextmanager
    def connected_client(self, value):
        path = self.root / "gateway.json"
        path.write_text(json.dumps(value))
        with patch.dict(os.environ, self.environment):
            gateway = GatewayServer(GatewayConfig.load(path), environ=self.environment)
            thread = serve_in_thread(gateway)
            try:
                setup = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(path),
                                        "client", "config", "codex"],
                                       capture_output=True, text=True, timeout=10)
                self.assertEqual(setup.returncode, 0, setup.stderr)

                def submit(**options):
                    result = subprocess.run([sys.executable, "-c", CLIENT],
                        input=json.dumps({"configuration": setup.stdout, **options}),
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return json.loads(result.stdout)

                yield submit
            finally:
                gateway.shutdown()
                gateway.server_close()
                thread.join(timeout=2)

    def test_connected_client_uses_local_destination_without_cloud_credentials(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.configuration(local, cloud)) as submit:
                result = submit()
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"],
                                  result["headers"].get("x-hormuz-destination"),
                                  result["headers"].get("x-hormuz-destination-locality")),
                                 (200, "LOCAL_DESTINATION", "local-llama", "local"))
                self.assertEqual(local_requests[0]["body"]["model"], "fixture-local-model")
                self.assertNotIn("authorization", local_requests[0]["headers"])
                self.assertNotIn("x-api-key", local_requests[0]["headers"])
                self.assertEqual(cloud_requests, [])

    def test_connect_rejects_capacity_failover_to_another_destination(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value["model_routes"]["local-code"]["failover_alias"] = "cloud-code"
            value["model_routes"]["cloud-code"] = {"protocol": "openai", "upstream_model": "cloud-model"}
            value["policies"]["organization"]["allowed_models"].append("cloud-code")
            path = self.root / "invalid-gateway.json"
            path.write_text(json.dumps(value))
            with patch.dict(os.environ, self.environment):
                setup = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(path),
                                        "client", "config", "codex"],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(setup.returncode, 2)
            self.assertIn("same destination", setup.stderr)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_local_work_and_exact_reuse_keep_the_actual_destination_identity(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests):
            value = self.configuration(local, "http://127.0.0.1:1")
            del value["upstreams"]["openai"]
            del self.environment["DESTINATION_CLOUD_KEY"]
            value["ai_work"] = {"enabled": True, "cache_enabled": True, "minimum_samples": 3,
                                "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                job = submit(control=True, path="/work/jobs", request={"repository": "example/repository",
                    "task_type": "implementation", "context_revision": "fixture-revision"})
                self.assertEqual(job["status"], 201, job)
                options = {"headers": {"X-Hormuz-Work-Id": job["body"]["work_id"]},
                           "request": {"temperature": 0}}
                first = submit(**options)
                repeated = submit(**options)
                self.assertEqual((first["status"], repeated["status"],
                                  repeated["headers"].get("x-hormuz-cache")), (200, 200, "exact-answer-hit"))
                self.assertEqual(repeated["headers"].get("x-hormuz-destination"), "local-llama")
                self.assertEqual(repeated["headers"].get("x-hormuz-destination-generation"),
                                 first["headers"].get("x-hormuz-destination-generation"))
                self.assertEqual(len(local_requests), 1)

    def test_local_destination_status_reports_the_same_model_and_generation_as_execution(self):
        with provider("LOCAL_DESTINATION") as (local, _requests):
            value = self.configuration(local, "http://127.0.0.1:1")
            del value["upstreams"]["openai"]
            value["model_routes"]["local-code"].update(
                model_revision="fixture-weights-v1", capabilities=["responses", "text", "streaming", "tools"])
            value["ai_work"] = {"enabled": True, "minimum_samples": 3,
                                "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                result = submit()
                status = submit(method="GET", path="/work/state")
                self.assertEqual(status["status"], 200, status)
                self.assertEqual(status["body"]["schema_version"], 2)
                self.assertEqual(result["headers"]["x-hormuz-contract"], "hormuz.relay-metadata;v=2")
                route = status["body"]["connection"]["routes"][0]
                self.assertEqual((route["destination"], route["locality"], route["authentication"],
                                  route["upstream_model"], route["model_revision"], route["capabilities"],
                                  route["eligible"], route["credential_configured"]),
                                 ("local-llama", "local", "none", "fixture-local-model", "fixture-weights-v1",
                                  ["responses", "text", "streaming", "tools"], True, False))
                self.assertEqual(route["generation"], result["headers"]["x-hormuz-destination-generation"])
                self.assertEqual(status["body"]["onboarding"]["choices"][0]["model"], "local-code")

    def test_local_destination_does_not_inherit_cloud_storage_permissions(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value["upstreams"]["openai"].update(allow_response_storage=True, allow_background=True)
            with self.connected_client(value) as submit:
                for field in ("store", "background"):
                    with self.subTest(field=field):
                        result = submit(request={field: True})
                        self.assertEqual(result["status"], 403, result)
                        self.assertIn(result["body"]["error"]["code"],
                                      {"hormuz_destination_policy_denied", "hormuz_provider_policy_denied"})
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_reconnect_does_not_send_existing_tool_state_to_a_changed_destination(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("REPLACEMENT_DESTINATION") as (replacement, replacement_requests):
            work_settings = {"enabled": True, "minimum_samples": 3,
                             "database": str(self.root / "work.sqlite3")}
            value = self.configuration(local, "http://127.0.0.1:1")
            value["ai_work"] = work_settings
            with self.connected_client(value) as submit:
                job = submit(control=True, path="/work/jobs", request={"repository": "example/repository",
                    "task_type": "implementation", "context_revision": "fixture-revision"})
                headers = {"X-Hormuz-Work-Id": job["body"]["work_id"]}
                self.assertEqual(submit(headers=headers)["status"], 200)
            reconnected = self.configuration(replacement, "http://127.0.0.1:1")
            reconnected["ai_work"] = work_settings
            with self.connected_client(reconnected) as submit:
                result = submit(headers=headers, request={"input": [
                    {"role": "assistant", "content": "Existing local model state"},
                    {"role": "user", "content": "Continue."}]})
                self.assertEqual(result["status"], 409, result)
                self.assertEqual(result["body"]["error"]["code"], "hormuz_ai_work_session_destination_generation_changed")
                self.assertEqual((len(local_requests), replacement_requests), (1, []))

    def test_unattributed_continuation_also_retains_its_destination_generation(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("REPLACEMENT_DESTINATION") as (replacement, replacement_requests):
            settings = {"enabled": True, "minimum_samples": 3, "database": str(self.root / "work.sqlite3")}
            value = self.configuration(local, "http://127.0.0.1:1")
            value["ai_work"] = settings
            with self.connected_client(value) as submit:
                first = submit()
                headers = {"X-Hormuz-Work-Id": first["headers"]["x-hormuz-work-id"]}
            value = self.configuration(replacement, "http://127.0.0.1:1")
            value["ai_work"] = settings
            with self.connected_client(value) as submit:
                result = submit(headers=headers, request={"input": [
                    {"role": "assistant", "content": "Existing local model state"},
                    {"role": "user", "content": "Continue."}]})
                self.assertEqual(result["status"], 409, result)
                self.assertEqual(replacement_requests, [])

    def test_named_cloud_destination_owns_its_credential_and_storage_permissions(self):
        with provider("NAMED_CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.configuration("http://127.0.0.1:1", cloud)
            value["upstreams"] = {"cloud-peer": {"protocol": "openai", "base_url": cloud,
                "api_key_env": "DESTINATION_CLOUD_KEY", "allow_response_storage": True, "allow_background": True}}
            value["model_routes"] = {"cloud-code": {"protocol": "openai", "destination": "cloud-peer",
                                                   "upstream_model": "fixture-cloud-model"}}
            value["policies"]["organization"]["allowed_models"] = ["cloud-code"]
            with self.connected_client(value) as submit:
                result = submit(request={"store": True, "background": True})
                self.assertEqual(result["status"], 200, result)
                self.assertEqual(result["headers"].get("x-hormuz-destination"), "cloud-peer")
                self.assertEqual(cloud_requests[0]["headers"]["authorization"],
                                 "Bearer fixture-cloud-destination-credential")
                self.assertEqual((cloud_requests[0]["body"]["store"], cloud_requests[0]["body"]["background"]),
                                 (True, True))

    def test_selected_local_destination_disables_implicit_storage_with_cloud_also_allowed(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.configuration(local, cloud)
            value["upstreams"]["openai"]["allow_response_storage"] = True
            value["model_routes"]["cloud-code"] = {"protocol": "openai", "upstream_model": "fixture-cloud-model"}
            value["policies"]["organization"]["allowed_models"].append("cloud-code")
            with self.connected_client(value) as submit:
                self.assertEqual(submit()["status"], 200)
                self.assertIs(local_requests[0]["body"].get("store"), False)
                self.assertEqual(cloud_requests, [])

    def test_failed_fresh_attempt_cannot_rebind_old_continuation_to_a_new_generation(self):
        with provider("LOCAL_DESTINATION") as (local, _local_requests), \
             provider("FAILED_REPLACEMENT", status=503) as (replacement, replacement_requests):
            settings = {"enabled": True, "minimum_samples": 3, "database": str(self.root / "work.sqlite3")}
            value = self.configuration(local, "http://127.0.0.1:1")
            value["ai_work"] = settings
            with self.connected_client(value) as submit:
                first = submit()
                headers = {"X-Hormuz-Work-Id": first["headers"]["x-hormuz-work-id"]}
            value = self.configuration(replacement, "http://127.0.0.1:1")
            value["ai_work"] = settings
            with self.connected_client(value) as submit:
                self.assertEqual(submit(headers=headers)["status"], 503)
                continuation = submit(headers=headers, request={"input": [
                    {"role": "assistant", "content": "Existing local model state"},
                    {"role": "user", "content": "Continue."}]})
                self.assertEqual(continuation["status"], 409, continuation)
                self.assertEqual(len(replacement_requests), 1)

    def test_connected_client_can_discover_the_emitted_destination_contract(self):
        with provider("LOCAL_DESTINATION") as (local, _requests):
            with self.connected_client(self.configuration(local, "http://127.0.0.1:1")) as submit:
                response = submit()
                manifest = subprocess.run([sys.executable, "-m", "hormuz", "contract", "manifest"],
                                          capture_output=True, text=True, timeout=10)
                self.assertEqual(manifest.returncode, 0, manifest.stderr)
                entries = json.loads(manifest.stdout)["schemas"]
                versions = {entry["schema_version"] for entry in entries
                            if entry["schema_id"] == "hormuz.relay-metadata"}
                self.assertEqual(versions, {1, 2})
                self.assertEqual(response["headers"]["x-hormuz-contract"], "hormuz.relay-metadata;v=2")

    def test_legacy_connection_preserves_version_one_metadata(self):
        with provider("LEGACY_DESTINATION") as (origin, _requests):
            value = self.configuration("http://127.0.0.1:1", origin)
            del value["upstreams"]["local-llama"]
            del value["model_routes"]["local-code"]["destination"]
            value["ai_work"] = {"enabled": True, "minimum_samples": 3,
                                "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                response = submit()
                state = submit(method="GET", path="/work/state")
                self.assertEqual(response["headers"]["x-hormuz-contract"], "hormuz.relay-metadata;v=1")
                self.assertNotIn("x-hormuz-destination", response["headers"])
                self.assertEqual(state["body"]["schema_version"], 1)
                self.assertEqual(set(state["body"]["connection"]["routes"][0]),
                                 {"model", "protocol", "credential_configured", "eligible"})
            with self.assertRaises(OSError):
                with socket.create_connection(("127.0.0.1", value["listen"]["port"]), timeout=1):
                    pass

    def test_legacy_work_evidence_survives_destination_upgrade(self):
        fixtures = Path(__file__).parent / "fixtures" / "execution_destination"
        golden = json.loads((fixtures / "legacy-work.json").read_text())
        # Seed the externally supplied legacy file, generated by the pre-change
        # public workflow. Observe migration only through client status/output.
        with closing(sqlite3.connect(self.root / "work.sqlite3")) as connection:
            connection.executescript((fixtures / "legacy-work.sql").read_text())
        with provider("LOCAL_DESTINATION") as (local, requests):
            value = self.configuration(local, "http://127.0.0.1:1")
            value["ai_work"] = {"enabled": True, "cache_enabled": True, "minimum_samples": 3,
                                "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                receipt = submit(method="GET", path="/work/jobs/" + golden["work_id"])
                self.assertEqual(receipt["status"], 200, receipt)
                self.assertEqual(receipt["body"]["costs"], golden["receipt"]["costs"])
                self.assertEqual(receipt["body"]["agent_choices"][0]["model"], "local-code")
                stale = submit(headers={"X-Hormuz-Work-Id": golden["work_id"]}, request={"input": [
                    {"role": "assistant", "content": "Legacy model state"},
                    {"role": "user", "content": "Continue."}]})
                self.assertEqual(stale["status"], 409, stale)
                self.assertEqual(requests, [])
                fresh = submit(control=True, path="/work/jobs", request={"repository": "example/repository",
                    "task_type": "implementation", "context_revision": "new-conversation"})
                response = submit(headers={"X-Hormuz-Work-Id": fresh["body"]["work_id"]})
                self.assertEqual((response["status"], response["body"]["output"][0]["content"][0]["text"]),
                                 (200, "LOCAL_DESTINATION"))

    def test_invalid_destination_origin_is_rejected_during_connect(self):
        value = self.configuration("http://[broken", "http://127.0.0.1:1")
        path = self.root / "invalid-origin.json"
        path.write_text(json.dumps(value))
        with patch.dict(os.environ, self.environment):
            setup = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(path),
                                    "client", "config", "codex"],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(setup.returncode, 2, setup.stderr)
        self.assertIn("configuration error:", setup.stderr)
        self.assertNotIn("Traceback", setup.stderr)

    def test_local_destination_is_not_repointed_by_environment_proxy_settings(self):
        worker = r'''
import json, sys
from tests.test_execution_destination_workflow import ExecutionDestinationWorkflowTests
case = ExecutionDestinationWorkflowTests("runTest")
case.setUp()
try:
    with case.connected_client(case.configuration(sys.argv[1], "http://127.0.0.1:1")) as submit:
        print(json.dumps(submit()))
finally:
    case.doCleanups()
'''
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("PROXY_DESTINATION") as (proxy, proxy_requests):
            environment = {**os.environ, "http_proxy": proxy, "HTTP_PROXY": proxy,
                           "https_proxy": proxy, "HTTPS_PROXY": proxy, "NO_PROXY": "", "no_proxy": ""}
            result = subprocess.run([sys.executable, "-c", worker, local], env=environment,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            self.assertEqual(response["body"]["output"][0]["content"][0]["text"], "LOCAL_DESTINATION")
            self.assertEqual((len(local_requests), proxy_requests), (1, []))


if __name__ == "__main__":
    unittest.main()
