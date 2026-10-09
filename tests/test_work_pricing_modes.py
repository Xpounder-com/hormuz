"""Real loopback admission and observations for standard-only work pricing."""
from __future__ import annotations

import http.client
import json
import unittest
from unittest.mock import Mock, patch

from hormuz.work_gateway import WorkUsageParser
from tests import test_work_gateway as fixtures


def provider_reply(modes=None, *, stream_prefix=b""):
    """Vendor-shaped synthetic replies; no provider rate/multiplier is assumed."""
    modes = {} if modes is None else modes

    def reply(handler):
        body = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
        with handler.lock:
            handler.requests.append({"path": handler.path, "body": body, "headers": dict(handler.headers)})
        anthropic = handler.path == "/v1/messages"
        chat = handler.path == "/v1/chat/completions"
        usage = {"input_tokens": 8, "output_tokens": 3, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0, **modes} if anthropic else {
            "input_tokens": 8, "output_tokens": 3, "total_tokens": 11,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
        if chat:
            usage = {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11,
                     "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0}}
        if anthropic:
            result = {"id": "message-test", "type": "message", "model": body["model"],
                      "role": "assistant", "stop_reason": "end_turn",
                      "content": [{"type": "text", "text": "three"}], "usage": usage}
        elif chat:
            result = {"id": "chat-test", "object": "chat.completion", "model": body["model"],
                      "choices": [{"index": 0, "finish_reason": "stop",
                                   "message": {"role": "assistant", "content": "three"}}],
                      "usage": usage, **modes}
        else:
            result = {"id": "response-test", "object": "response", "model": body["model"],
                      "status": "completed", "output": [{"type": "message", "role": "assistant",
                          "content": [{"type": "output_text", "text": "three"}]}],
                      "usage": usage, **modes}
        if not body.get("stream"):
            handler.send(200, result)
            return
        if anthropic:
            events = [{"type": "message_start", "message": result},
                      {"type": "message_delta", "usage": {"output_tokens": 3, **modes}},
                      {"type": "message_stop"}]
        elif chat:
            events = [{"id": "chat-test", "object": "chat.completion.chunk",
                       "choices": [{"index": 0, "delta": {"content": "three"}, "finish_reason": "stop"}],
                       "model": body["model"], **modes},
                      {"id": "chat-test", "object": "chat.completion.chunk",
                       "choices": [], "usage": usage, "model": body["model"], **modes}]
        else:
            events = [{"type": "response.completed", "response": result}]
        data = stream_prefix + b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
        if chat:
            data += b"data: [DONE]\n\n"
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    return reply


class WorkPricingModeTests(unittest.TestCase):
    setUp = fixtures.WorkGatewayTests.setUp
    start = fixtures.WorkGatewayTests.start
    tearDown = fixtures.WorkGatewayTests.tearDown
    job = fixtures.WorkGatewayTests.job

    def post(self, work, endpoint, *, headers=None, **fields):
        payload = {"model": "claude-standard" if endpoint == "/v1/messages" else "engineering-fast",
                   "messages": [{"role": "user", "content": "count three"}],
                   "max_tokens": 20, "temperature": 0, "stream": False}
        if endpoint == "/v1/responses":
            payload.pop("messages")
            payload.pop("max_tokens")
            payload.update(input="count three", max_output_tokens=20)
        payload.update(fields)
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        try:
            connection.request("POST", endpoint, body=json.dumps(payload), headers={
                "Authorization": "Bearer " + fixtures.GATEWAY_TOKEN,
                "Content-Type": "application/json", "X-Hormuz-Work-ID": work, **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_openai_omitted_and_null_tier_pin_standard_and_share_exact_cache(self):
        for endpoint in ("/v1/responses", "/v1/chat/completions"):
            with self.subTest(endpoint=endpoint), patch.object(fixtures.WorkProvider, "do_POST", provider_reply()):
                work = self.job()
                previous = len(fixtures.WorkProvider.requests)
                self.assertEqual(200, self.post(work, endpoint)[0])
                sent = fixtures.WorkProvider.requests[-1]["body"]
                self.assertEqual("default", sent["service_tier"])
                status, headers, _ = self.post(work, endpoint, service_tier=None,
                                               speed=None, inference_geo=None)
                self.assertEqual(200, status)
                self.assertEqual("exact-answer-hit", headers["X-Hormuz-Cache"])
                self.assertEqual(previous + 1, len(fixtures.WorkProvider.requests))

    def test_anthropic_pins_request_enum_preserves_omitted_geo_and_unrelated_beta(self):
        with patch.object(fixtures.WorkProvider, "do_POST", provider_reply({"service_tier": "standard"})):
            work = self.job()
            status, _, body = self.post(work, "/v1/messages", service_tier=None, speed=None,
                inference_geo=None, headers={"Anthropic-Beta": "fixture-unrelated-beta"})
            self.assertEqual(200, status, body)
            sent = fixtures.WorkProvider.requests[-1]
            self.assertEqual("standard_only", sent["body"]["service_tier"])
            self.assertNotIn("speed", sent["body"])
            self.assertNotIn("inference_geo", sent["body"])
            self.assertEqual("fixture-unrelated-beta", sent["headers"]["Anthropic-Beta"])
            state = self.gateway.work_runtime.get_work(self.identity, work)
            self.assertEqual("succeeded", state["attempts"][0]["state"])
            self.assertEqual(0, state["costs"]["uncertain_microusd"])
            self.assertEqual(200, self.post(self.job(), "/v1/messages",
                service_tier="standard_only", speed="standard", inference_geo="global")[0])

    def test_unqualified_modes_reject_before_cache_selection_or_provider_egress(self):
        modes = [("/v1/responses", {"service_tier": value})
                 for value in ("auto", "priority", "flex", "fast", "ultrafast", True, {})]
        modes += [("/v1/chat/completions", {"service_tier": "priority"}),
                  ("/v1/messages", {"service_tier": "auto"}),
                  ("/v1/messages", {"service_tier": "standard"}),
                  ("/v1/messages", {"service_tier": "priority"}),
                  ("/v1/messages", {"speed": "fast"}),
                  ("/v1/messages", {"inference_geo": "us"})]
        with patch.object(fixtures.WorkProvider, "do_POST", provider_reply()):
            work = self.job()
            self.assertEqual(200, self.post(work, "/v1/responses")[0])
            before = self.gateway.work_runtime.get_work(self.identity, work)["costs"]
            with patch.object(self.gateway.work_runtime, "cache_get", side_effect=AssertionError("cache lookup")), \
                    patch.object(self.gateway.work_runtime, "choose_route", side_effect=AssertionError("route selection")):
                for endpoint, fields in modes:
                    with self.subTest(endpoint=endpoint, fields=fields):
                        status, _, body = self.post(work, endpoint, **fields)
                        self.assertEqual(422, status, body)
                        self.assertIn(b"request_cost_unbounded", body)
            self.assertEqual(1, len(fixtures.WorkProvider.requests))
            self.assertEqual(before, self.gateway.work_runtime.get_work(self.identity, work)["costs"])

    def test_unexpected_provider_modes_keep_full_hold_affinity_and_never_seed_cache_or_impact_cost(self):
        modes = [("/v1/responses", {"service_tier": "priority"}),
                 ("/v1/chat/completions", {"service_tier": "ultrafast"}),
                 ("/v1/messages", {"service_tier": "priority"}),
                 ("/v1/messages", {"speed": "fast"}),
                 ("/v1/messages", {"inference_geo": "us"}),
                 ("/v1/responses", {"service_tier": None}),
                 ("/v1/responses", {"service_tier": {"invalid": "shape"}})]
        recorder = Mock()
        self.gateway.impact_recorder = recorder
        for endpoint, fields in modes:
            with self.subTest(endpoint=endpoint, fields=fields), \
                    patch.object(fixtures.WorkProvider, "do_POST", provider_reply(fields)):
                work = self.job()
                initial = len(fixtures.WorkProvider.requests)
                self.assertEqual(200, self.post(work, endpoint)[0])
                state = self.gateway.work_runtime.get_work(self.identity, work)
                attempt = state["attempts"][0]
                self.assertEqual("unknown", attempt["state"])
                self.assertIsNone(attempt["cost_microusd"])
                self.assertTrue(attempt["response_succeeded"])
                self.assertEqual(attempt["reserved_microusd"], state["costs"]["uncertain_microusd"])
                self.assertEqual(attempt["model"], state["pinned_model"])
                self.assertEqual(200, self.post(work, endpoint)[0])
                self.assertEqual(initial + 2, len(fixtures.WorkProvider.requests))
        completed = [call.args[0] for call in recorder.submit.call_args_list
                     if call.args[0].status == "succeeded"]
        self.assertTrue(completed)
        self.assertTrue(all(item.cost_microusd is None for item in completed))
        # Preserve canonical predecessor accounting; only Work certainty changes.
        self.assertGreater(self.gateway.store.monthly_totals(actor_id="alice").cost_microusd, 0)
        self.gateway.impact_recorder = None

    def test_stream_modes_are_observed_for_all_three_endpoints(self):
        for endpoint, fields in [("/v1/responses", {"service_tier": "priority"}),
                                 ("/v1/chat/completions", {"service_tier": "priority"}),
                                 ("/v1/messages", {"speed": "fast"})]:
            with self.subTest(endpoint=endpoint), patch.object(fixtures.WorkProvider, "do_POST", provider_reply(fields)):
                work = self.job()
                self.assertEqual(200, self.post(work, endpoint, stream=True)[0])
                state = self.gateway.work_runtime.get_work(self.identity, work)
                self.assertEqual("unknown", state["attempts"][0]["state"])
                self.assertIsNone(state["attempts"][0]["cost_microusd"])
                self.assertGreater(state["costs"]["uncertain_microusd"], 0)

    def test_malformed_earlier_stream_metadata_cannot_release_hold_after_valid_final_usage(self):
        prefixes = (b'data: {"service_tier":"priority","service_tier":"default"}\n\n',
                    b'data: {"service_tier":\n\n')
        for endpoint in ("/v1/responses", "/v1/chat/completions", "/v1/messages"):
            for prefix in prefixes:
                with self.subTest(endpoint=endpoint, prefix=prefix), \
                        patch.object(fixtures.WorkProvider, "do_POST", provider_reply(stream_prefix=prefix)):
                    work = self.job()
                    self.assertEqual(200, self.post(work, endpoint, stream=True)[0])
                    state = self.gateway.work_runtime.get_work(self.identity, work)
                    self.assertEqual("unknown", state["attempts"][0]["state"])
                    self.assertIsNone(state["attempts"][0]["cost_microusd"])
                    self.assertEqual(state["attempts"][0]["reserved_microusd"],
                                     state["costs"]["uncertain_microusd"])

    def test_legacy_no_work_requests_keep_their_requested_tier(self):
        self.gateway.work_runtime.close()
        self.gateway.work_runtime = None
        with patch.object(fixtures.WorkProvider, "do_POST", provider_reply({"service_tier": "priority"})):
            connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
            try:
                connection.request("POST", "/v1/responses", body=json.dumps({"model": "engineering-fast",
                    "input": "text", "max_output_tokens": 20, "service_tier": "priority"}),
                    headers={"Authorization": "Bearer " + fixtures.GATEWAY_TOKEN,
                             "Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(200, response.status, response.read())
            finally:
                connection.close()
            self.assertEqual("priority", fixtures.WorkProvider.requests[-1]["body"]["service_tier"])
            self.assertGreater(self.gateway.store.monthly_totals(actor_id="alice").cost_microusd, 0)


class WorkPricingMetadataTests(unittest.TestCase):
    def test_duplicate_and_invalid_metadata_before_valid_terminal_remain_unpriceable(self):
        for prefix in (b'data: {"service_tier":"priority","service_tier":"default"}\n\n',
                       b'data: {"service_tier":\n\n'):
            parser = WorkUsageParser("openai", is_event_stream=True)
            parser.feed(prefix)
            parser.feed(b'data: {"type":"response.completed","response":{"service_tier":"default",'
                        b'"usage":{"input_tokens":8,"output_tokens":3}}}\n\n')
            parsed = parser.finish_with_finance()
            self.assertTrue(parsed.usage.evidence_complete)
            self.assertFalse(parser.work_pricing_known)

    def test_stream_conflict_cannot_be_undone_by_later_standard_and_content_is_not_metadata(self):
        parser = WorkUsageParser("openai", is_event_stream=True)
        for tier in ("priority", "default"):
            parser.feed(("data: " + json.dumps({"type": "response.completed", "response": {
                "service_tier": tier, "usage": {"input_tokens": 8, "output_tokens": 3},
                "output": [{"content": [{"type": "output_text", "text": "three"}]}]}}) + "\n\n").encode())
        parser.finish_with_finance()
        self.assertFalse(parser.work_pricing_known)
        standard = WorkUsageParser("openai", is_event_stream=False)
        standard.feed(json.dumps({"service_tier": "default", "usage": {"input_tokens": 8, "output_tokens": 3},
            "output": [{"service_tier": "priority", "text": "customer content"}],
            "metadata": {"speed": "fast"}}).encode())
        standard.finish_with_finance()
        self.assertTrue(standard.work_pricing_known)
