"""Real HTTP gateway checks against disposable provider simulators."""

from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hormuz.config import GatewayConfig
from hormuz.server import GatewayServer, serve_in_thread
from hormuz.usage import ResponseUsageParser
from hormuz.work_runtime import WorkRuntimeError
from tests import test_gateway as gateway_fixtures

ANTHROPIC_KEY = gateway_fixtures.ANTHROPIC_KEY
CLAUDE_ONLY_TOKEN = gateway_fixtures.CLAUDE_ONLY_TOKEN
GATEWAY_TOKEN = gateway_fixtures.GATEWAY_TOKEN
OPENAI_KEY = gateway_fixtures.OPENAI_KEY
_free_port = gateway_fixtures._free_port


class WorkProvider(BaseHTTPRequestHandler):
    requests = []
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with self.lock:
            self.requests.append({"path": self.path, "body": body,
                                  "headers": dict(self.headers)})
        if body.get("simulate_failover") and body["model"] == "gpt-test-fast":
            self.send(429, {"error": {"type": "rate_limit_error"}})
            return
        chat = self.path == "/v1/chat/completions"
        usage = {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11,
                 "prompt_tokens_details": {"cached_tokens": 3},
                 "completion_tokens_details": {"reasoning_tokens": 1}} if chat else {
                     "input_tokens": 8, "output_tokens": 3, "total_tokens": 11,
                     "input_tokens_details": {"cached_tokens": 3},
                     "output_tokens_details": {"reasoning_tokens": 1}}
        if body.get("simulate_invalid_usage"):
            usage["prompt_tokens" if chat else "input_tokens"] = -1
        if chat:
            result = {"id": "chat-test", "object": "chat.completion", "model": body["model"],
                      "choices": [{"index": 0, "finish_reason": "stop",
                                   "message": {"role": "assistant", "content": "three"}}]}
        else:
            result = {"id": "response-test", "object": "response", "model": body["model"], "status": "completed",
                      "output": [{"type": "message", "role": "assistant",
                                  "content": [{"type": "output_text", "text": "three"}]}]}
        if not body.get("simulate_missing_usage"):
            result["usage"] = usage
        if body.get("stream"):
            if chat:
                chunks = [
                    {"id": "chat-test", "object": "chat.completion.chunk", "model": body["model"],
                     "choices": [{"index": 0, "delta": {"content": "three"}, "finish_reason": "stop"}]},
                    {"id": "chat-test", "object": "chat.completion.chunk", "model": body["model"],
                     "choices": [], **({"usage": usage} if "usage" in result else {})},
                ]
                data = b"".join(("data: " + json.dumps(chunk) + "\n\n").encode() for chunk in chunks) + b"data: [DONE]\n\n"
            else:
                data = ("event: response.completed\ndata: " + json.dumps({"type": "response.completed", "response": result}) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send(200, result)

    def send(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WorkGatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        WorkProvider.requests = []
        self.provider = ThreadingHTTPServer(("127.0.0.1", 0), WorkProvider)
        self.provider_thread = threading.Thread(target=self.provider.serve_forever, daemon=True)
        self.provider_thread.start()
        self.environment = patch.dict(os.environ, {
            "TEST_GATEWAY_TOKEN": GATEWAY_TOKEN, "TEST_CLAUDE_ONLY_TOKEN": CLAUDE_ONLY_TOKEN,
            "TEST_OPENAI_KEY": OPENAI_KEY, "TEST_ANTHROPIC_KEY": ANTHROPIC_KEY,
        })
        self.environment.start()
        self.config_value = gateway_fixtures.GatewayIntegrationTests._config(self, self.provider.server_port, _free_port())
        self.config_value["ai_work"] = {"enabled": True, "database": str(self.root / "work.sqlite3"),
                                        "cache_enabled": True, "minimum_samples": 3}
        self.config_value["model_routes"]["engineering-fast"]["failover_alias"] = "engineering-deep"
        self.config_value["model_routes"]["engineering-deep"].update(input_cost_per_million=0.5, output_cost_per_million=1)
        self.path = self.root / "gateway.json"
        self.start()

    def start(self):
        self.path.write_text(json.dumps(self.config_value))
        self.config = GatewayConfig.load(self.path)
        self.gateway = GatewayServer(self.config)
        self.thread = serve_in_thread(self.gateway)
        self.identity = self.config.identities_by_token[GATEWAY_TOKEN]

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.provider.shutdown()
        self.provider.server_close()
        self.provider_thread.join(timeout=2)
        self.environment.stop()
        self.directory.cleanup()

    def job(self):
        return self.gateway.work_runtime.create_work(self.identity, "example/repository", task_type="diagnosis", context_revision="commit-1")["work_id"]

    def post(self, work_id, *, endpoint="/v1/chat/completions", token=GATEWAY_TOKEN, **fields):
        payload = {"model": "engineering-fast", "messages": [{"role": "user", "content": "count three"}],
                   "temperature": 0, "max_tokens": 20, "stream": False}
        if endpoint == "/v1/responses":
            payload = {"model": "engineering-fast", "input": "count three", "temperature": 0,
                       "max_output_tokens": 20, "stream": False}
        payload.update(fields)
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "X-Hormuz-Work-ID": work_id}
        connection.request("POST", endpoint, body=json.dumps(payload), headers=headers)
        response = connection.getresponse()
        data = response.read()
        response_headers = {name.lower(): value for name, value in response.getheaders()}
        connection.close()
        return response.status, response_headers, data

    def test_chat_native_usage_projects_tokens_without_fabricated_responses_finance(self):
        work = self.job()
        status, headers, body = self.post(work)
        self.assertEqual(200, status, body)
        self.assertEqual(work, headers["x-hormuz-work-id"])
        self.assertEqual("unknown", headers["x-hormuz-work-outcome"])
        totals = self.gateway.store.monthly_totals(actor_id="alice")
        self.assertEqual((8, 3, 3, 1), (totals.input_tokens, totals.output_tokens, totals.cache_read_tokens, totals.reasoning_tokens))
        parser = ResponseUsageParser("openai", is_event_stream=False, chat_completions=True)
        parser.feed(body)
        evidence = parser.finish_with_finance()
        self.assertTrue(evidence.usage.evidence_complete)
        self.assertEqual("absent", evidence.finance.state)
        self.assertIsNone(evidence.finance.native_payload_json)
        work_state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual("unknown", work_state["outcome_evidence"])
        self.assertEqual("succeeded", work_state["attempts"][0]["state"])
        self.assertNotIn("X-Hormuz-Work-ID", WorkProvider.requests[0]["headers"])

    def test_exact_cache_is_zero_provider_work_and_correction_bypasses_it(self):
        work = self.job()
        self.assertEqual(200, self.post(work)[0])
        status, headers, _ = self.post(work)
        self.assertEqual(200, status)
        self.assertEqual("exact-answer-hit", headers["x-hormuz-cache"])
        self.assertEqual(1, len(WorkProvider.requests))
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(1, state["costs"]["cache_hits"])
        self.assertEqual(1, state["costs"]["repeated_requests"])
        self.assertEqual("unknown", state["outcome_evidence"])
        self.gateway.work_runtime.observe(self.identity, work, "corrected", source="workflow", reference="correction-1")
        self.assertEqual(200, self.post(work)[0])
        self.assertEqual(2, len(WorkProvider.requests))

    def test_unknown_usage_keeps_hold_after_gateway_restart(self):
        work = self.job()
        self.assertEqual(200, self.post(work, simulate_missing_usage=True)[0])
        before = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertGreater(before["costs"]["uncertain_microusd"], 0)
        self.assertIsNone(before["attempts"][0]["cost_microusd"])
        self.gateway.shutdown()
        self.gateway.server_close()
        self.config_value["listen"]["port"] = _free_port()
        self.start()
        after = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(before["costs"]["uncertain_microusd"], after["costs"]["uncertain_microusd"])

    def test_work_budget_denial_has_no_provider_call_and_releases_unstarted_legacy_hold(self):
        work = self.job()
        self.gateway.work_runtime.set_plan(self.identity.organization_id, "workspace", self.identity.organization_id, 0, "cost")
        status, _, body = self.post(work)
        self.assertEqual(402, status, body)
        self.assertEqual([], WorkProvider.requests)
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual("paused", state["state"])
        self.assertEqual(0, state["costs"]["consumed_microusd"])
        self.gateway.work_runtime.act(self.identity, work, "approve_budget", budget_microusd=1_000_000)
        self.assertEqual(402, self.post(work)[0])
        self.assertEqual([], WorkProvider.requests)

    def test_unowned_work_rejected_before_egress_and_unknown_usage_not_cached(self):
        work = self.job()
        bob = self.config.identities_by_token[CLAUDE_ONLY_TOKEN]
        bob = replace(bob, allowed_clients=("codex",))
        self.gateway.config.identities_by_token[CLAUDE_ONLY_TOKEN] = bob
        status, _, body = self.post(work, token=CLAUDE_ONLY_TOKEN)
        self.assertEqual(404, status, body)
        self.assertEqual([], WorkProvider.requests)
        self.assertEqual(200, self.post(work, simulate_invalid_usage=True)[0])
        self.assertEqual(200, self.post(work, simulate_invalid_usage=True)[0])
        self.assertEqual(2, len(WorkProvider.requests))
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(0, state["costs"]["cache_hits"])
        self.assertGreater(state["costs"]["uncertain_microusd"], 0)

    def test_streamed_chat_preserves_wire_contract_and_usage_without_answer_cache(self):
        work = self.job()
        status, headers, body = self.post(work, stream=True, stream_options={"include_usage": True})
        self.assertEqual(200, status, body)
        self.assertIn("text/event-stream", headers["content-type"])
        self.assertIn(b"data: [DONE]", body)
        self.assertIn(b'"prompt_tokens": 8', body)
        self.assertNotIn("x-hormuz-cache", headers)
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual("succeeded", state["attempts"][0]["state"])
        self.assertEqual(0, state["costs"]["cache_hits"])

    def test_failover_attempts_share_work_and_retry_link_without_repeat_quality_signal(self):
        work = self.job()
        status, _, body = self.post(work, endpoint="/v1/responses", simulate_failover=True)
        self.assertEqual(200, status, body)
        self.assertEqual("gpt-test-deep", json.loads(body)["model"])
        self.assertEqual(2, len(WorkProvider.requests))
        state = self.gateway.work_runtime.get_work(self.identity, work)
        self.assertEqual(2, state["costs"]["attempts"])
        linked = [attempt for attempt in state["attempts"] if attempt["retry_of"]]
        self.assertEqual(1, len(linked))
        self.assertEqual("none", linked[0]["repeat_signal"])
        self.assertEqual("unknown", state["outcome_evidence"])

    def test_real_http_workflows_train_local_routing_and_correction_retires_choice(self):
        runtime = self.gateway.work_runtime
        ready = threading.Event()
        original = runtime._save_profiles
        def save(key, profiles, version):
            stored = original(key, profiles, version)
            if stored and all(profiles.get((route.alias, runtime._route_fingerprint(route)), {}).get("samples", 0) >= 3
                              for route in self.config.model_routes.values() if route.protocol == "openai"):
                ready.set()
            return stored
        runtime._save_profiles = save
        cheap_work = None
        for model in ("engineering-fast", "engineering-deep"):
            for _ in range(3):
                work = self.job()
                status, _, body = self.post(work, model=model)
                self.assertEqual(200, status, body)
                self.assertEqual(self.config.model_routes[model].upstream_model, json.loads(body)["model"])
                runtime.observe(self.identity, work, "completed", source="workflow")
                if model == "engineering-deep":
                    cheap_work = work
        self.assertTrue(ready.wait(timeout=2), "real workflow observations did not refresh routing")
        target = self.job()
        status, headers, body = self.post(target)
        self.assertEqual(200, status, body)
        self.assertEqual("gpt-test-deep", json.loads(body)["model"])
        self.assertEqual("local_cost_evidence", headers["x-hormuz-work-route"])
        runtime.observe(self.identity, cheap_work, "corrected", source="workflow")
        status, _, body = self.post(self.job())
        self.assertEqual(200, status, body)
        self.assertEqual("gpt-test-fast", json.loads(body)["model"])

    def test_work_storage_failure_fails_closed_before_provider_dispatch(self):
        work = self.job()
        with patch.object(self.gateway.work_runtime, "reserve", side_effect=WorkRuntimeError("storage_unavailable", 503)):
            status, _, body = self.post(work)
        self.assertEqual(503, status, body)
        self.assertEqual([], WorkProvider.requests)


if __name__ == "__main__":
    unittest.main()
