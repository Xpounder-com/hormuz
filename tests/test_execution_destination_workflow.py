"""Destination behavior at connect -> client request -> result -> disconnect.

Only providers and the coding-client executable are disposable stand-ins. The
configuration CLI, router, governance, transport and temporary storage are real.
"""

from contextlib import closing, contextmanager
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sqlite3
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from hormuz.config import GatewayConfig
from hormuz.server import GatewayServer, serve_in_thread


CLIENT = r'''
import http.client, json, os, socket, struct, sys, time, tomllib
from urllib.parse import urlsplit
value = json.load(sys.stdin)
config = tomllib.loads(value["configuration"])
provider = config["model_providers"][config["model_provider"]]
origin = urlsplit(provider["base_url"])
connection = http.client.HTTPConnection(origin.hostname, origin.port, timeout=5)
try:
    if value.get("stall_after_headers"):
        connection.connect()
        connection.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    connection.request(value.get("method", "POST"), origin.path.rstrip("/") + value.get("path", "/responses"),
        None if value.get("method") == "GET" else json.dumps(value["request"] if value.get("control") else
                   {"model": config["model"], "input": "Reply with the fixture answer.",
                    "max_output_tokens": 8, **value.get("request", {})}),
        {"Content-Type": "application/json",
         "Authorization": "Bearer " + os.environ[provider["env_key"]],
         **provider.get("http_headers", {}), **value.get("headers", {})})
    if value.get("cancel_before_headers"):
        time.sleep(0.2)
        connection.sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        connection.close()
        print(json.dumps({"status": "cancelled", "body": None, "headers": {}}))
        sys.exit(0)
    response = connection.getresponse()
    if value.get("stall_after_headers"):
        time.sleep(1.8)
        connection.close()
        print(json.dumps({"status": response.status,
                          "headers": dict((k.lower(), v) for k, v in response.getheaders()),
                          "body": None}))
        sys.exit(0)
    data = response.readline() if value.get("cancel_after_event") else response.read()
    print(json.dumps({"status": response.status,
                      "headers": dict((k.lower(), v) for k, v in response.getheaders()),
                      "body": data.decode() if "text/event-stream" in response.getheader("Content-Type", "") else json.loads(data)}))
finally:
    connection.close()
'''


@contextmanager
def provider(answer, *, status=200, health_status=200, hold_first=None, event_stream=False, cancel_stream=None, slow_stream=False, backpressure_first=None, observe_close=None):
    received = []

    class Handler(BaseHTTPRequestHandler):
        timeout = 5
        def log_message(self, *_args):
            pass

        def do_GET(self):
            data = json.dumps({"status": "ok" if health_status == 200 else "loading model"}).encode()
            self.send_response(health_status if self.path == "/health" else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append({"path": self.path, "body": body,
                             "headers": {k.lower(): v for k, v in self.headers.items()}})
            if hold_first and len(received) == 1:
                hold_first[0].set()
                hold_first[1].wait(timeout=5)
            result = {"id": "fixture-response", "object": "response", "status": "completed",
                      "model": body["model"], "output": [{"type": "message", "role": "assistant",
                      "content": [{"type": "output_text", "text": answer}]}],
                      "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}
            data = json.dumps(result).encode()
            if event_stream:
                tool = {"type": "function_call", "call_id": "fixture-call", "name": "fixture_tool", "arguments": "{}"}
                events = [{"type": "response.output_item.done", "item": tool},
                          {"type": "response.completed", "response": result}]
                data = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
            if backpressure_first and len(received) == 1:
                data = b"data: " + b" " * (16 * 1024 * 1024) + b"\n\n" + data
            self.send_response(status(body) if callable(status) else status)
            self.send_header("Content-Type", "text/event-stream" if event_stream else "application/json")
            if not ((cancel_stream or slow_stream) and len(received) == 1):
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if cancel_stream and len(received) == 1:
                try:
                    self.wfile.write(b"data: {\"type\":\"response.created\"}\n\n")
                    self.wfile.flush()
                    cancel_stream[0].set()
                    cancel_stream[1].wait(timeout=5)
                    self.wfile.write(b"data: " + b" " * 262144 + b"\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    cancel_stream[2].set()
            elif slow_stream and len(received) == 1:
                try:
                    for _ in range(8):
                        self.wfile.write(b"data: {\"type\":\"response.in_progress\"}\n\n")
                        self.wfile.flush()
                        time.sleep(0.2)
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                if backpressure_first and len(received) == 1:
                    backpressure_first.set()
                try:
                    self.wfile.write(data)
                    if observe_close and len(received) == 1:
                        self.connection.settimeout(2)
                        if self.rfile.read(1) == b"":
                            observe_close.set()
                except (BrokenPipeError, ConnectionResetError):
                    if observe_close:
                        observe_close.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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

    def test_local_only_blocks_loading_local_model_without_cloud_fallback(self):
        with provider("LOCAL_DESTINATION", health_status=503) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud)) as submit:
                result = submit(request={"input": "Summarize this fixture."})
                self.assertEqual((result["status"], result["body"].get("error", {}).get("code")),
                                 (503, "hormuz_route_local_route_unavailable"))
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def admission_configuration(self, local, cloud, *, mode="local_only"):
        value = self.configuration(local, cloud)
        value["model_routes"]["local-code"].update(
            model_revision="fixture-weights-v1", capabilities=["responses", "text"])
        value["model_routes"]["cloud-code"] = {"protocol": "openai", "upstream_model": "cloud-model"}
        value["policies"]["organization"]["allowed_models"] = ["cloud-code", "local-code"]
        value["route_admission"] = {"mode": mode, "local_routes": {"local-code": {
            "qualification_id": "fixture-q1", "model_revision": "fixture-weights-v1",
            "max_request_bytes": 4096, "max_output_tokens": 8,
            "task_types": ["summarization"]}}}
        return value

    def test_local_only_admits_local_execution_without_ai_work_accounting(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            with self.connected_client(value) as submit:
                result = submit(request={"model": "cloud-code", "input": "Summarize this short fixture."})
                self.assertEqual((result["status"], result["body"]["output"][0]["content"][0]["text"],
                                  result["headers"].get("x-hormuz-destination"),
                                  result["headers"].get("x-hormuz-routing-mode")),
                                 (200, "LOCAL_DESTINATION", "local-llama", "local_only"))
                self.assertEqual(len(local_requests), 1)
                self.assertEqual(cloud_requests, [])

    def test_local_only_uses_root_health_for_v1_destination_base(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local + "/v1", cloud)
            with self.connected_client(value) as submit:
                result = submit(request={"input": "Summarize this fixture."})
                self.assertEqual(result["status"], 200, result)
                self.assertEqual((result["body"]["output"][0]["content"][0]["text"],
                                  result["headers"].get("x-hormuz-destination")),
                                 ("LOCAL_DESTINATION", "local-llama"))
                self.assertEqual([item["path"] for item in local_requests], ["/v1/responses"])
                self.assertEqual(cloud_requests, [])

    def test_explicit_eligible_local_model_is_preserved_with_and_without_ai_work(self):
        with provider("FIRST_LOCAL_DESTINATION") as (local, local_requests), \
             provider("SECOND_LOCAL_DESTINATION") as (second, second_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            for mode in ("auto", "local_only"):
                for accounting in (False, True):
                    with self.subTest(mode=mode, accounting=accounting):
                        value = self.admission_configuration(local, cloud, mode=mode)
                        value["upstreams"]["local-second"] = {"protocol": "openai", "base_url": second,
                            "locality": "local", "authentication": "none"}
                        value["model_routes"]["second-code"] = {"protocol": "openai", "destination": "local-second",
                            "upstream_model": "fixture-second-model", "model_revision": "fixture-second-v1",
                            "capabilities": ["responses", "text"]}
                        value["policies"]["organization"]["allowed_models"].append("second-code")
                        value["route_admission"]["local_routes"]["second-code"] = {
                            "qualification_id": "fixture-q2", "model_revision": "fixture-second-v1",
                            "max_request_bytes": 4096, "max_output_tokens": 8, "task_types": ["summarization"]}
                        if accounting:
                            value["ai_work"] = {"enabled": True, "minimum_samples": 3,
                                "database": str(self.root / ("work-" + mode + ".sqlite3"))}
                        with self.connected_client(value) as submit:
                            headers = {}
                            if accounting:
                                job = submit(control=True, path="/work/jobs", request={
                                    "repository": "example/repository", "task_type": "summarization"})
                                self.assertEqual(job["status"], 201, job)
                                headers["X-Hormuz-Work-Id"] = job["body"]["work_id"]
                            result = submit(headers=headers, request={
                                "model": "second-code", "input": "Summarize this fixture."})
                            self.assertEqual(result["status"], 200, result)
                            self.assertEqual((result["body"]["output"][0]["content"][0]["text"],
                                              result["headers"].get("x-hormuz-destination"),
                                              result["headers"].get("x-hormuz-routed-model")),
                                             ("SECOND_LOCAL_DESTINATION", "local-second", "fixture-second-model"))
                            if accounting:
                                status = submit(method="GET", path="/work/jobs/" + job["body"]["work_id"])
                                self.assertEqual(status["status"], 200, status)
                                self.assertEqual([candidate["model"] for candidate in status["body"]["routing"]["candidates"]],
                                                 ["second-code"])
            self.assertEqual([item["body"]["model"] for item in second_requests], ["fixture-second-model"] * 4)
            self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_auto_routes_qualified_fresh_work_and_keeps_unknown_work_on_baseline(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud, mode="auto")
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                easy = submit(request={"model": "cloud-code", "input": "Summarize this fixture."})
                unknown = submit(request={"model": "cloud-code", "input": "Consider this fixture."})
                large = submit(request={"model": "cloud-code", "input": "Summarize " + "x" * 5000})
                self.assertEqual((easy["status"], easy["headers"].get("x-hormuz-routing-qualification")),
                                 (200, "fixture-q1"))
                self.assertEqual(easy["body"]["output"][0]["content"][0]["text"], "LOCAL_DESTINATION")
                self.assertEqual(easy["headers"].get("x-hormuz-policy-decision"), "fallback")
                self.assertEqual([item["body"]["output"][0]["content"][0]["text"] for item in (unknown, large)],
                                 ["CLOUD_DESTINATION", "CLOUD_DESTINATION"])
                self.assertEqual((len(local_requests), len(cloud_requests)), (1, 2))

    def test_request_can_tighten_auto_to_local_only_but_cannot_loosen_it(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud, mode="auto")) as submit:
                blocked = submit(headers={"X-Hormuz-Routing-Mode": "local_only"},
                                 request={"model": "cloud-code", "input": "Unknown task."})
                self.assertEqual((blocked["status"], blocked["body"].get("error", {}).get("code")),
                                 (503, "hormuz_route_local_route_unavailable"))
                self.assertEqual((local_requests, cloud_requests), ([], []))
            with self.connected_client(self.admission_configuration(local, cloud)) as submit:
                loosened = submit(headers={"X-Hormuz-Routing-Mode": "auto"},
                                   request={"model": "cloud-code", "input": "Unknown task."})
                self.assertEqual(loosened["status"], 403)
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_auto_does_not_retarget_opaque_continuation_without_retained_affinity(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud, mode="auto")) as submit:
                result = submit(request={"model": "cloud-code", "input": "Summarize this fixture.",
                                         "previous_response_id": "opaque-cloud-response"})
                self.assertEqual((result["status"], result["body"].get("error", {}).get("code")),
                                 (409, "hormuz_route_affinity_required"))
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_auto_does_not_execute_an_unqualified_local_baseline(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud, mode="auto")) as submit:
                result = submit(request={"model": "local-code", "input": "Unknown task."})
                self.assertEqual((result["status"], result["body"].get("error", {}).get("code")),
                                 (503, "hormuz_route_local_route_unavailable"))
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_retained_cloud_affinity_reports_cloud_and_local_only_blocks_it(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud, mode="auto")
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                first = submit(request={"model": "cloud-code", "input": "Unknown task."})
                headers = {"X-Hormuz-Work-Id": first["headers"]["x-hormuz-work-id"]}
                continuation = {"model": "cloud-code", "input": [
                    {"role": "assistant", "content": "Existing cloud answer."},
                    {"role": "user", "content": "Summarize this fixture."}]}
                second = submit(headers=headers, request=continuation)
                self.assertEqual((second["status"], second["headers"].get("x-hormuz-routing-reason"),
                                  second["headers"].get("x-hormuz-routing-qualification")),
                                 (200, "session_affinity", None))
                self.assertEqual(second["body"]["output"][0]["content"][0]["text"], "CLOUD_DESTINATION")
                blocked = submit(headers={**headers, "X-Hormuz-Routing-Mode": "local_only"}, request=continuation)
                self.assertEqual(blocked["status"], 409)
                self.assertEqual((len(local_requests), len(cloud_requests)), (0, 2))

    def test_local_only_blocks_multimodal_and_unknown_provider_features(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud)) as submit:
                result = submit(request={"input": [{"role": "user", "content": [
                    {"type": "input_text", "text": "Summarize this image."},
                    {"type": "input_image", "image_url": "https://example.invalid/private-image"}]}]})
                self.assertEqual(result["status"], 503)
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_count_tokens_helper_obeys_local_only_without_work_accounting(self):
        with provider("LOCAL_TOKEN_COUNT") as (local, local_requests), \
             provider("CLOUD_TOKEN_COUNT") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["upstreams"]["local-counter"] = {"protocol": "anthropic", "base_url": local,
                "locality": "local", "authentication": "none"}
            value["upstreams"]["anthropic"] = {"base_url": cloud, "api_key_env": "DESTINATION_CLOUD_KEY"}
            value["model_routes"].update({"local-counter": {"protocol": "anthropic", "destination": "local-counter",
                "upstream_model": "fixture-counter", "model_revision": "fixture-weights-v1",
                "capabilities": ["count_tokens", "text"]},
                "cloud-counter": {"protocol": "anthropic", "upstream_model": "cloud-counter"}})
            value["route_admission"]["local_routes"]["local-counter"] = dict(value["route_admission"]["local_routes"]["local-code"])
            value["policies"]["organization"]["allowed_models"].extend(["local-counter", "cloud-counter"])
            value["policies"]["organization"]["allowed_clients"].append("claude-code")
            value["identities"][0]["allowed_clients"].append("claude-code")
            value["policies"]["organization"].pop("max_output_tokens")
            with self.connected_client(value) as submit:
                result = submit(control=True, path="/messages/count_tokens", request={"model": "cloud-counter",
                    "messages": [{"role": "user", "content": "Summarize this fixture."}]})
                self.assertEqual((result["status"], result["headers"].get("x-hormuz-destination")), (200, "local-counter"))
                self.assertEqual((len(local_requests), cloud_requests), (1, []))

    def test_router_status_distinguishes_qualification_from_observed_readiness(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud)) as submit:
                before = submit(method="GET", path="/router/state")
                self.assertEqual(before["status"], 200, before)
                self.assertEqual((before["body"]["schema_id"], before["body"]["schema_version"], before["body"]["mode"]),
                                 ("hormuz.route-admission", 1, "local_only"))
                route = before["body"]["routes"][0]
                frozen = json.loads((Path(__file__).parent / "fixtures/execution_destination/router-state-v1.json").read_text())
                normalized = json.loads(json.dumps(before["body"]))
                self.assertRegex(normalized["routes"][0]["generation"], r"^[0-9a-f]{64}$")
                normalized["routes"][0]["generation"] = "LIVE_DESTINATION_GENERATION"
                self.assertEqual(normalized, frozen)
                self.assertEqual((route["qualification_id"], route["readiness"], route["evidence_basis"]),
                                 ("fixture-q1", "unchecked", "operator_declared"))
                executed = submit(request={"input": "Summarize this fixture."})
                after = submit(method="GET", path="/router/state")
                self.assertEqual(after["body"]["routes"][0]["readiness"], "ready")
                self.assertEqual(after["body"]["routes"][0]["generation"],
                                 executed["headers"]["x-hormuz-destination-generation"])
                self.assertEqual(executed["headers"].get("x-hormuz-contract"), "hormuz.relay-metadata;v=3")
                self.assertEqual((len(local_requests), cloud_requests), (1, []))

    def test_local_only_never_retries_through_an_unqualified_capacity_alternate(self):
        with provider("LOCAL_DESTINATION", status=lambda body: 429 if body["model"] == "fixture-local-model" else 200) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["model_routes"]["local-code"]["failover_alias"] = "local-alternate"
            value["model_routes"]["local-alternate"] = {"protocol": "openai", "destination": "local-llama",
                "upstream_model": "unqualified-alternate"}
            value["policies"]["organization"]["allowed_models"].append("local-alternate")
            with self.connected_client(value) as submit:
                result = submit(request={"input": "Summarize this fixture."})
                self.assertEqual(result["status"], 429, result)
                self.assertEqual([item["body"]["model"] for item in local_requests], ["fixture-local-model"])
                self.assertEqual(cloud_requests, [])

    def test_final_admission_bounds_the_body_after_work_preparation(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["route_admission"]["local_routes"]["local-code"]["max_request_bytes"] = 110
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                result = submit(request={"model": "cloud-code", "input": "Summarize fixture."})
                self.assertEqual((result["status"], result["body"].get("error", {}).get("code")),
                                 (503, "hormuz_route_local_route_unavailable"))
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_local_capacity_is_bounded_and_released_after_completion(self):
        entered, release = threading.Event(), threading.Event()
        with provider("LOCAL_DESTINATION", hold_first=(entered, release)) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            with self.connected_client(self.admission_configuration(local, cloud)) as submit:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    first = worker.submit(submit, request={"input": "Summarize first fixture."})
                    try:
                        self.assertTrue(entered.wait(timeout=2))
                        busy = submit(request={"input": "Summarize second fixture."})
                        self.assertEqual((busy["status"], busy["body"].get("error", {}).get("code")),
                                         (429, "hormuz_route_local_capacity_busy"))
                        self.assertEqual(len(local_requests), 1)
                    finally:
                        release.set()
                    self.assertEqual(first.result(timeout=5)["status"], 200)
                again = submit(request={"input": "Summarize after release."})
                self.assertEqual(again["status"], 200)
                self.assertEqual((len(local_requests), cloud_requests), (2, []))

    def test_streamed_tool_continuation_stays_on_the_qualified_local_generation(self):
        with provider("LOCAL_DESTINATION", event_stream=True) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud, mode="auto")
            value["model_routes"]["local-code"]["capabilities"].extend(["streaming", "tools"])
            value["ai_work"] = {"enabled": True, "tool_capable_aliases": ["local-code"],
                                "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                first = submit(request={"model": "cloud-code", "input": "Summarize this fixture.", "stream": True,
                    "tools": [{"type": "function", "name": "fixture_tool", "parameters": {"type": "object"}}]})
                self.assertEqual(first["status"], 200, first)
                self.assertIn('"call_id": "fixture-call"', first["body"])
                second = submit(headers={"X-Hormuz-Work-Id": first["headers"]["x-hormuz-work-id"]},
                    request={"model": "cloud-code", "stream": True, "input": [
                        {"type": "function_call_output", "call_id": "fixture-call", "output": "Fixture tool result"}]})
                self.assertEqual(second["status"], 200, second)
                self.assertEqual(second["headers"].get("x-hormuz-routing-reason"), "session_affinity")
                self.assertEqual(second["headers"]["x-hormuz-destination-generation"], first["headers"]["x-hormuz-destination-generation"])
                self.assertEqual(local_requests[1]["body"]["input"][0]["call_id"], "fixture-call")
                self.assertEqual((len(local_requests), cloud_requests), (2, []))

    def test_new_work_id_cannot_claim_successful_continuation_affinity(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["model_routes"]["local-code"]["capabilities"].append("tools")
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                job = submit(control=True, path="/work/jobs", request={"repository": "example/repository",
                    "task_type": "implementation", "context_revision": "fixture-revision"})
                result = submit(headers={"X-Hormuz-Work-Id": job["body"]["work_id"]}, request={"input": [
                    {"type": "function_call_output", "call_id": "unowned-call", "output": "Unknown previous result."}]})
                self.assertEqual((result["status"], result["body"].get("error", {}).get("code")),
                                 (409, "hormuz_route_affinity_required"))
                self.assertEqual((local_requests, cloud_requests), ([], []))

    def test_function_parameter_schema_does_not_create_false_session_affinity(self):
        with provider("LOCAL_DESTINATION") as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud, mode="auto")
            value["model_routes"]["local-code"]["capabilities"].append("tools")
            with self.connected_client(value) as submit:
                result = submit(request={"model": "cloud-code", "input": "Summarize this fixture.",
                    "tools": [{"type": "function", "name": "fixture_tool", "parameters": {
                        "type": "object", "properties": {"type": {"type": ["string", "null"]}}}}]})
                self.assertEqual(result["status"], 200, result)
                self.assertEqual((len(local_requests), cloud_requests), (1, []))

    def test_periodic_local_stream_bytes_cannot_extend_the_request_deadline(self):
        with provider("LOCAL_DESTINATION", event_stream=True, slow_stream=True) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["upstream_timeout_seconds"] = 1
            value["model_routes"]["local-code"]["capabilities"].append("streaming")
            with self.connected_client(value) as submit:
                result = submit(request={"input": "Summarize this fixture.", "stream": True})
                self.assertEqual(result["status"], 200)
                self.assertNotIn('"response.completed"', result["body"])
                state = submit(method="GET", path="/router/state")
                self.assertEqual(state["body"]["local_capacity"]["active"], 0)
                self.assertEqual(submit(request={"input": "Summarize after deadline."})["status"], 200)
                self.assertEqual((len(local_requests), cloud_requests), (2, []))

    def test_stalled_coding_client_cannot_hold_local_capacity_past_deadline(self):
        delivered = threading.Event()
        with provider("LOCAL_DESTINATION", event_stream=True, backpressure_first=delivered) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["upstream_timeout_seconds"] = 1
            value["model_routes"]["local-code"]["capabilities"].append("streaming")
            with self.connected_client(value) as submit:
                with ThreadPoolExecutor(max_workers=1) as worker:
                    first = worker.submit(submit, stall_after_headers=True,
                        request={"input": "Summarize this fixture.", "stream": True})
                    self.assertTrue(delivered.wait(timeout=2))
                    time.sleep(1.2)
                    state = submit(method="GET", path="/router/state")
                    self.assertEqual(state["body"]["local_capacity"]["active"], 0)
                    first.result(timeout=5)
                self.assertEqual(submit(request={"input": "Summarize after deadline."})["status"], 200)
                self.assertEqual((len(local_requests), cloud_requests), (2, []))

    def test_client_reset_before_headers_closes_the_owned_local_response(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        with provider("LOCAL_DESTINATION", hold_first=(entered, release), observe_close=closed) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3")}
            with self.connected_client(value) as submit:
                job = submit(control=True, path="/work/jobs", request={"repository": "example/repository",
                    "task_type": "implementation", "context_revision": "fixture-revision"})
                try:
                    cancelled = submit(cancel_before_headers=True, headers={"X-Hormuz-Work-Id": job["body"]["work_id"]},
                        request={"input": "Summarize this fixture."})
                    self.assertEqual(cancelled["status"], "cancelled")
                    self.assertTrue(entered.wait(timeout=2))
                finally:
                    release.set()
                self.assertTrue(closed.wait(timeout=2))
                history = submit(method="GET", path="/work/jobs/" + job["body"]["work_id"] + "/attempts")
                self.assertEqual(history["body"]["attempts"][0]["state"], "unknown")
                self.assertEqual((len(local_requests), cloud_requests), (1, []))

    def test_cancelled_local_stream_releases_capacity_and_never_replays_to_cloud(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        with provider("LOCAL_DESTINATION", event_stream=True, cancel_stream=(entered, release, finished)) as (local, local_requests), \
             provider("CLOUD_DESTINATION") as (cloud, cloud_requests):
            value = self.admission_configuration(local, cloud)
            value["model_routes"]["local-code"]["capabilities"].append("streaming")
            with self.connected_client(value) as submit:
                try:
                    cancelled = submit(cancel_after_event=True, request={"input": "Summarize this fixture.", "stream": True})
                    self.assertEqual(cancelled["status"], 200)
                    self.assertTrue(entered.wait(timeout=2))
                    state = submit(method="GET", path="/router/state")
                    self.assertEqual(state["body"].get("local_capacity"), {"limit": 1, "active": 1})
                finally:
                    release.set()
                self.assertTrue(finished.wait(timeout=2))
                # At most three status reads let the existing relay finish its
                # disconnect cleanup; this is a finite workflow, not polling.
                for _ in range(3):
                    state = submit(method="GET", path="/router/state")
                    if state["body"]["local_capacity"]["active"] == 0:
                        break
                self.assertEqual(state["body"]["local_capacity"]["active"], 0)
                again = submit(request={"input": "Summarize after cancellation."})
                self.assertEqual(again["status"], 200)
                self.assertEqual((len(local_requests), cloud_requests), (2, []))

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
                self.assertEqual(versions, {1, 2, 3})
                frozen = json.loads((Path(__file__).parent / "fixtures/execution_destination/relay-v3.json").read_text())
                self.assertEqual(next(entry for entry in entries if entry["schema_id"] == "hormuz.relay-metadata" and entry["schema_version"] == 3), frozen)
                self.assertIn("hormuz_route_local_route_unavailable", json.loads(manifest.stdout)["error_codes"])
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
