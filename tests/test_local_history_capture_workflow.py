"""Local history capture exercised through coding-client and CLI boundaries."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
import threading
import unittest

from tests import test_execution_destination_workflow as destination_workflow


_REQUESTED_MODEL = object()


@contextmanager
def streaming_provider(parts, *, terminal=True, gate=None, protocol="openai", reported_model=_REQUESTED_MODEL,
                       server_tool_usage=None, status=200):
    """One finite synthetic provider; no external requests or background polling."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            if gate is not None:
                gate[0].set()
                gate[1].wait(timeout=5)
            answer = "".join(parts)
            events = [{"type": "response.output_text.delta", "delta": text} for text in parts]
            if terminal:
                events.append({"type": "response.completed", "response": {
                    "id": "history-fixture", "object": "response", "status": "completed",
                    "model": body["model"] if reported_model is _REQUESTED_MODEL else reported_model,
                    "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": answer}]}],
                    "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                }})
                if reported_model is None:
                    events[-1]["response"].pop("model")
            if protocol == "anthropic":
                events = [{"type": "message_start", "message": {
                    "id": "history-anthropic-fixture", "type": "message", "role": "assistant",
                    "model": body["model"], "content": [],
                    "usage": {"input_tokens": 4, "output_tokens": 0},
                }}, {"type": "content_block_start", "index": 0,
                     "content_block": {"type": "text", "text": parts[0]}}]
                events.extend({"type": "content_block_delta", "index": 0,
                               "delta": {"type": "text_delta", "text": text}} for text in parts[1:])
                events.extend([{"type": "content_block_stop", "index": 0},
                               {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                                "usage": {"output_tokens": 2,
                                          **({"server_tool_use": server_tool_usage}
                                             if server_tool_usage is not None else {})}},
                               {"type": "message_stop"}])
            data = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
            self.send_response(status(body) if callable(status) else status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=False)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        if gate is not None:
            gate[1].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class LocalHistoryCaptureWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture = destination_workflow.ExecutionDestinationWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.path = self.root / "gateway.json"
        self.fixture.environment["HORMUZ_HISTORY_HOME"] = str(self.root / "private-history")

    def cloud_configuration(self, endpoint):
        value = self.fixture.configuration(endpoint, endpoint)
        value["model_routes"] = {"cloud-code": {"protocol": "openai", "upstream_model": "cloud-model"}}
        value["policies"]["organization"]["allowed_models"] = ["cloud-code"]
        value["egress_controls"] = {"secrets": {"mode": "off"}}
        self.path.write_text(json.dumps(value))
        return value

    def history(self, *options):
        result = subprocess.run([sys.executable, "-m", "hormuz", "--config", str(self.path),
                                 "history", *options],
                                env={**os.environ, **self.fixture.environment},
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_opted_in_stream_is_redacted_when_egress_is_off_and_missing_price_stays_unknown(self):
        secret = self.fixture.environment["DESTINATION_CLOUD_KEY"]
        with streaming_provider(["Alpha ", secret[:12], secret[12:], " omega."]) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                result = submit(request={"model": "cloud-code", "stream": True,
                                         "input": "Summarize the fixture with " + secret})
                self.assertEqual(result["status"], 200, result)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["request_content_status"], "captured")
                self.assertEqual(entry["response_content_status"], "captured")
                self.assertEqual(entry["response_text"], "Alpha [REDACTED:HORMUZ_SECRET] omega.")
                self.assertNotIn(secret, json.dumps(entry))
                self.assertIn("[REDACTED:HORMUZ_SECRET]", json.dumps(entry["request_text"]))
                self.assertIsNone(entry["estimated_cost_microusd"])
                self.assertTrue(entry["cost_reason"])
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4, 2))
                self.assertEqual(entry["outcome"], "succeeded")
            self.assertEqual(len(received), 1)
            self.assertIn(secret, received[0]["input"])

    def test_explicit_cloud_rates_produce_a_configured_estimate(self):
        with streaming_provider(["A priced fixture."]) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["model_routes"]["cloud-code"].update(
                input_cost_per_million=2, output_cost_per_million=3)
            self.path.write_text(json.dumps(value))
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit(request={"model": "cloud-code", "stream": True})["status"], 200)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["estimated_cost_microusd"], 14)
                self.assertEqual(entry["cost_reason"], "configured_rate_card_estimate")
                self.assertEqual(entry["outcome"], "succeeded")
            self.assertEqual(len(received), 1)

    def test_provider_model_mismatch_does_not_reuse_the_configured_route_price(self):
        with streaming_provider(["A differently billed model."], reported_model="unexpected-billing-model") as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["model_routes"]["cloud-code"].update(
                input_cost_per_million=2, output_cost_per_million=3)
            self.path.write_text(json.dumps(value))
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit(request={"model": "cloud-code", "stream": True})["status"], 200)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["outcome"], "succeeded")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4, 2))
                self.assertIsNone(entry["estimated_cost_microusd"])
                self.assertEqual(entry["cost_reason"], "provider_model_mismatch")
            self.assertEqual(len(received), 1)

    def test_missing_provider_model_cannot_certify_the_configured_route_price(self):
        with streaming_provider(["A model identity is missing."], reported_model=None) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["model_routes"]["cloud-code"].update(
                input_cost_per_million=2, output_cost_per_million=3)
            self.path.write_text(json.dumps(value))
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit(request={"model": "cloud-code", "stream": True})["status"], 200)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["outcome"], "succeeded")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4, 2))
                self.assertIsNone(entry["estimated_cost_microusd"])
                self.assertEqual(entry["cost_reason"], "provider_model_unknown")
            self.assertEqual(len(received), 1)

    def test_unfinished_stream_preserves_partial_content_and_unknown_outcome(self):
        with streaming_provider(["A partial answer."], terminal=False) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit(request={"model": "cloud-code", "stream": True})["status"], 200)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["outcome"], "outcome_unknown")
                self.assertEqual(entry["response_content_status"], "partial")
                self.assertEqual(entry["response_text"], "A partial answer.")
                self.assertIsNone(entry["estimated_cost_microusd"])
                self.assertIsNone(entry["input_tokens"])
                self.assertIsNone(entry["output_tokens"])
            self.assertEqual(len(received), 1)

    def test_oversized_response_text_and_wire_are_omitted_instead_of_truncated(self):
        for size in (65 * 1024, 140 * 1024):
            with self.subTest(bytes=size), streaming_provider(["x" * size]) as (endpoint, received):
                value = self.cloud_configuration(endpoint)
                self.history("enable")
                with self.fixture.connected_client(value) as submit:
                    result = submit(request={"model": "cloud-code", "stream": True})
                    self.assertEqual(result["status"], 200)
                    self.assertIn("x" * size, result["body"])
                    entry = self.history("show", self.history("list")["entries"][0]["id"])["entry"]
                    self.assertEqual(entry["response_content_status"], "omitted_limit")
                    self.assertEqual(entry["response_text"], "")
                self.assertEqual(len(received), 1)

    def test_disabling_while_provider_is_running_discards_that_capture(self):
        gate = threading.Event(), threading.Event()
        with streaming_provider(["This answer was in flight."], gate=gate) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(submit, request={"model": "cloud-code", "stream": True})
                    try:
                        self.assertTrue(gate[0].wait(timeout=3), "The provider did not receive the request")
                        self.history("disable")
                    finally:
                        gate[1].set()
                    self.assertEqual(pending.result(timeout=10)["status"], 200)
                self.assertEqual(self.history("list")["entries"], [])
                self.assertFalse(self.history("status")["enabled"])
            self.assertEqual(len(received), 1)

    def test_delete_all_prevents_in_flight_requests_from_repopulating_history(self):
        gate = threading.Event(), threading.Event()
        gate[1].set()
        with streaming_provider(["An answer crossing deletion."], gate=gate) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                self.assertEqual(submit(request={"model": "cloud-code", "stream": True})["status"], 200)
                self.assertEqual(len(self.history("list")["entries"]), 1)
                gate[0].clear()
                gate[1].clear()
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(submit, request={"model": "cloud-code", "stream": True})
                    try:
                        self.assertTrue(gate[0].wait(timeout=3), "The provider did not receive the request")
                        self.assertEqual(self.history("delete", "--all")["deleted"], 1)
                    finally:
                        gate[1].set()
                    self.assertEqual(pending.result(timeout=10)["status"], 200)
                self.assertEqual(self.history("list")["entries"], [])
                self.assertTrue(self.history("status")["enabled"])
                self.assertEqual(submit(request={"model": "cloud-code", "stream": True})["status"], 200)
                self.assertEqual(len(self.history("list")["entries"]), 1)
            self.assertEqual(len(received), 3)

    def test_delete_all_invalidates_the_whole_in_flight_request_including_its_failover(self):
        gate = threading.Event(), threading.Event()
        with streaming_provider(["A successful fallback answer."], gate=gate,
                                status=lambda body: 429 if body["model"] == "cloud-model" else 200) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["model_routes"]["cloud-code"].update(
                input_cost_per_million=2, output_cost_per_million=3,
                failover_alias="cloud-alternate")
            value["model_routes"]["cloud-alternate"] = {
                "protocol": "openai", "upstream_model": "cloud-fallback-model",
                "input_cost_per_million": 2, "output_cost_per_million": 3}
            value["policies"]["organization"]["allowed_models"].append("cloud-alternate")
            self.path.write_text(json.dumps(value))
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(submit, request={"model": "cloud-code", "stream": True})
                    try:
                        self.assertTrue(gate[0].wait(timeout=3), "The provider did not receive the initial request")
                        self.history("delete", "--all")
                    finally:
                        gate[1].set()
                    response = pending.result(timeout=10)
                    self.assertEqual(response["status"], 200, response)
                    self.assertIn("x-hormuz-failover", response["headers"])
                self.assertEqual([body["model"] for body in received],
                                 ["cloud-model", "cloud-fallback-model"])
                self.assertEqual(self.history("list")["entries"], [])
                self.assertTrue(self.history("status")["enabled"])

    def test_enabling_history_during_a_request_does_not_capture_its_later_failover(self):
        gate = threading.Event(), threading.Event()
        with streaming_provider(["An answer begun before consent."], gate=gate,
                                status=lambda body: 429 if body["model"] == "cloud-model" else 200) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["model_routes"]["cloud-code"]["failover_alias"] = "cloud-alternate"
            value["model_routes"]["cloud-alternate"] = {
                "protocol": "openai", "upstream_model": "cloud-fallback-model"}
            value["policies"]["organization"]["allowed_models"].append("cloud-alternate")
            self.path.write_text(json.dumps(value))
            self.assertFalse(self.history("status")["enabled"])
            with self.fixture.connected_client(value) as submit:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(submit, request={"model": "cloud-code", "stream": True})
                    try:
                        self.assertTrue(gate[0].wait(timeout=3), "The provider did not receive the initial request")
                        self.history("enable")
                    finally:
                        gate[1].set()
                    response = pending.result(timeout=10)
                    self.assertEqual(response["status"], 200, response)
                    self.assertIn("x-hormuz-failover", response["headers"])
                self.assertEqual([body["model"] for body in received],
                                 ["cloud-model", "cloud-fallback-model"])
                self.assertEqual(self.history("list")["entries"], [])
                self.assertTrue(self.history("status")["enabled"])

    def test_anthropic_stream_assembles_text_and_redacts_across_delta_boundaries(self):
        secret = self.fixture.environment["DESTINATION_CLOUD_KEY"]
        with streaming_provider(["Claude ", secret[:12], secret[12:], " answer."],
                                protocol="anthropic") as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["upstreams"]["anthropic"] = {
                "base_url": endpoint, "api_key_env": "DESTINATION_CLOUD_KEY"}
            value["model_routes"]["cloud-claude"] = {
                "protocol": "anthropic", "upstream_model": "claude-fixture"}
            value["identities"][0]["allowed_clients"].append("claude-code")
            value["policies"]["organization"]["allowed_clients"].append("claude-code")
            value["policies"]["organization"]["allowed_models"].append("cloud-claude")
            self.path.write_text(json.dumps(value))
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                response = submit(control=True, path="/messages", request={
                    "model": "cloud-claude", "stream": True, "max_tokens": 8,
                    "messages": [{"role": "user", "content": "Summarize the fixture."}],
                })
                self.assertEqual(response["status"], 200, response)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["protocol"], "anthropic")
                self.assertEqual(entry["response_text"], "Claude [REDACTED:HORMUZ_SECRET] answer.")
                self.assertEqual(entry["response_content_status"], "captured")
                self.assertEqual(entry["outcome"], "succeeded")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4, 2))
            self.assertEqual(len(received), 1)

    def test_provider_server_tools_cannot_be_priced_as_only_input_and_output_tokens(self):
        with streaming_provider(["An answer using a provider tool."], protocol="anthropic",
                                server_tool_usage={"web_search_requests": 1}) as (endpoint, received):
            value = self.cloud_configuration(endpoint)
            value["upstreams"]["anthropic"] = {
                "base_url": endpoint, "api_key_env": "DESTINATION_CLOUD_KEY"}
            value["model_routes"]["cloud-claude"] = {
                "protocol": "anthropic", "upstream_model": "claude-fixture",
                "input_cost_per_million": 2, "output_cost_per_million": 3}
            value["identities"][0]["allowed_clients"].append("claude-code")
            value["policies"]["organization"]["allowed_clients"].append("claude-code")
            value["policies"]["organization"]["allowed_models"].append("cloud-claude")
            self.path.write_text(json.dumps(value))
            self.history("enable")
            with self.fixture.connected_client(value) as submit:
                response = submit(control=True, path="/messages", request={
                    "model": "cloud-claude", "stream": True, "max_tokens": 8,
                    "messages": [{"role": "user", "content": "Summarize the fixture."}],
                })
                self.assertEqual(response["status"], 200, response)
                entries = self.history("list")["entries"]
                self.assertEqual(len(entries), 1)
                entry = self.history("show", entries[0]["id"])["entry"]
                self.assertEqual(entry["outcome"], "succeeded")
                self.assertEqual((entry["input_tokens"], entry["output_tokens"]), (4, 2))
                self.assertIsNone(entry["estimated_cost_microusd"])
                self.assertEqual(entry["cost_reason"], "additional_provider_charges_unknown")
            self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
