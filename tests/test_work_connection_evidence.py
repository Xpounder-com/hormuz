"""Bounded loopback API-response evidence; no provider/native grade is inferred."""
import http.client
import json
import sqlite3
import unittest
from unittest.mock import patch

from hormuz.work_gateway import WorkUsageParser
from hormuz.usage import ResponseUsageParser
from tests import test_work_gateway as fixtures


def provider_reply(*, terminal="complete", truncate=False):
    def reply(handler):
        body = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
        with handler.lock:
            handler.requests.append({"path": handler.path, "body": body, "headers": dict(handler.headers)})
        anthropic = handler.path == "/v1/messages"
        chat = handler.path == "/v1/chat/completions"
        usage = {"input_tokens": 8, "output_tokens": 3}
        if chat:
            usage = {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
            reason = "stop" if terminal == "complete" else "length"
            result = {"object": "chat.completion", "model": body["model"], "choices": [{"index": 0, "finish_reason": reason, "message": {"role": "assistant", "content": "three"}}], "usage": usage}
            events = [{"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "three"}, "finish_reason": reason}]}, {"object": "chat.completion.chunk", "choices": [], "usage": usage}]
        elif anthropic:
            reason = "end_turn" if terminal == "complete" else "max_tokens"
            result = {"type": "message", "model": body["model"], "role": "assistant", "content": [{"type": "text", "text": "three"}], "stop_reason": reason, "usage": usage}
            events = [{"type": "message_start", "message": {**result, "stop_reason": None}}, {"type": "message_delta", "delta": {"stop_reason": reason}, "usage": {"output_tokens": 3}}, {"type": "message_stop"}]
        else:
            result = {"object": "response", "status": "completed" if terminal == "complete" else "incomplete", "model": body["model"], "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "three"}]}], "usage": usage}
            events = [{"type": "response.completed" if terminal == "complete" else "response.incomplete", "response": result}]
        stream = body.get("stream", False)
        data = (b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events) + (b"data: [DONE]\n\n" if chat else b"")) if stream else json.dumps(result).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream" if stream else "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data[:-5] if truncate else data)
        handler.wfile.flush()
        handler.close_connection = True
    return reply


class WorkConnectionHTTPTests(unittest.TestCase):
    setUp = fixtures.WorkGatewayTests.setUp
    start = fixtures.WorkGatewayTests.start
    tearDown = fixtures.WorkGatewayTests.tearDown
    job = fixtures.WorkGatewayTests.job
    post = fixtures.WorkGatewayTests.post

    def get(self, path, token=fixtures.GATEWAY_TOKEN):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=5)
        try:
            connection.request("GET", path, headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def consent(self, identity=None):
        self.gateway.work_workflow.acquire(identity or self.identity, {"utm_source": "synthetic-fixture"}, consent=True)

    def connections(self, identity=None):
        return self.gateway.work_workflow.funnel(identity or self.identity)["events"].get("qualified_connection", 0)

    def test_setup_read_is_not_a_connection_and_cached_response_cannot_supply_new_proof(self):
        work = self.job()
        self.assertEqual(200, self.post(work)[0])
        self.assertEqual({"consent": False, "events": {}}, self.gateway.work_workflow.funnel(self.identity))
        self.consent()
        self.assertEqual(200, self.get("/v1/work/connect")[0])
        self.assertEqual(0, self.connections())
        before = len(fixtures.WorkProvider.requests)
        status, headers, _ = self.post(work)
        self.assertEqual(200, status)
        self.assertEqual("exact-answer-hit", headers["x-hormuz-cache"])
        self.assertEqual(before, len(fixtures.WorkProvider.requests))
        self.assertEqual(0, self.connections())
        self.assertEqual(200, self.post(work, messages=[{"role": "user", "content": "another response"}])[0])
        self.assertEqual(1, self.connections())

    def test_success_is_owned_deduplicated_and_does_not_complete_the_job(self):
        self.consent()
        other = self.config.identities_by_token[fixtures.CLAUDE_ONLY_TOKEN]
        self.consent(other)
        work = self.job()
        self.assertEqual(403, self.post(work, token=fixtures.CLAUDE_ONLY_TOKEN)[0])
        self.assertEqual(0, self.connections(other))
        self.assertEqual(200, self.post(work)[0])
        self.assertEqual(1, self.connections())
        self.assertEqual(0, self.connections(other))
        self.assertEqual("active", self.gateway.work_runtime.get_work(self.identity, work)["state"])
        self.assertEqual(200, self.post(work)[0])
        self.assertEqual(1, self.connections())

    def test_denial_provider_failure_and_truncated_transport_never_qualify(self):
        self.consent()
        work = self.job()
        self.gateway.work_runtime.set_plan(self.identity.organization_id, "job", work, 0, "cost", expected_version=0)
        self.assertEqual(402, self.post(work)[0])
        self.assertEqual(0, len(fixtures.WorkProvider.requests))
        self.assertEqual(0, self.connections())
        self.assertEqual(0, self.gateway.work_workflow.funnel(self.identity)["events"]["first_attributed_work"])
        self.gateway.work_runtime.act(self.identity, work, "approve_budget", budget_microusd=1_000_000, expected_version=1)
        def failure(handler):
            handler.rfile.read(int(handler.headers["Content-Length"]))
            handler.send(500, {"error": {"type": "synthetic_error"}})
        with patch.object(fixtures.WorkProvider, "do_POST", failure):
            self.assertEqual(500, self.post(work)[0])
        self.assertEqual(0, self.connections())
        self.assertEqual(1, self.gateway.work_workflow.funnel(self.identity)["events"]["first_attributed_work"])
        with patch.object(fixtures.WorkProvider, "do_POST", provider_reply(truncate=True)):
            self.assertEqual(200, self.post(work)[0])
        self.assertEqual(0, self.connections())

    def test_complete_protocol_json_and_sse_responses_supply_evidence_but_length_does_not(self):
        self.consent()
        for endpoint in ("/v1/responses", "/v1/chat/completions", "/v1/messages"):
            for stream in (False, True):
                with self.subTest(endpoint=endpoint, stream=stream):
                    work = self.job()
                    fields = {"stream": stream}
                    if endpoint == "/v1/messages":
                        fields["model"] = "claude-standard"
                    before = self.connections()
                    with patch.object(fixtures.WorkProvider, "do_POST", provider_reply(terminal="length")):
                        self.assertEqual(200, self.post(work, endpoint=endpoint, **fields)[0])
                    self.assertEqual(before, self.connections())
                    with patch.object(fixtures.WorkProvider, "do_POST", provider_reply()):
                        self.assertEqual(200, self.post(work, endpoint=endpoint, **fields)[0])
                    self.assertEqual(1 if endpoint != "/v1/messages" else 2, self.connections())

    def test_job_setup_filters_identity_policy_and_configured_protocol(self):
        other = self.config.identities_by_token[fixtures.CLAUDE_ONLY_TOKEN]
        work = self.gateway.work_runtime.create_work(other, "example/repository")["work_id"]
        status, raw = self.get("/v1/work/jobs/" + work, fixtures.CLAUDE_ONLY_TOKEN)
        self.assertEqual(200, status)
        choices = json.loads(raw)["agent_choices"]
        self.assertTrue(choices)
        self.assertTrue(all(row["protocol"] == "anthropic" and row["client"] == "claude-code" for row in choices))
        status, rendered = self.get("/work/jobs/" + work, fixtures.CLAUDE_ONLY_TOKEN)
        self.assertEqual(200, status)
        self.assertIn(b"hormuz client config claude", rendered)
        self.assertNotIn(b"hormuz client config codex", rendered)
        with patch.object(type(self.gateway), "upstream_credentials", property(lambda server: {"openai": "synthetic-configured"})):
            status, raw = self.get("/v1/work/jobs/" + work, fixtures.CLAUDE_ONLY_TOKEN)
            self.assertEqual([], json.loads(raw)["agent_choices"])
            status, rendered = self.get("/work/jobs/" + work, fixtures.CLAUDE_ONLY_TOKEN)
            self.assertIn(b"No configured provider route and application grant", rendered)

    def test_optional_funnel_storage_failure_preserves_response_charge_and_cache(self):
        self.consent()
        for error in (sqlite3.OperationalError("private-error"), OSError("private-error")):
            with self.subTest(error=type(error).__name__):
                work = self.job()
                with patch.object(self.gateway.work_workflow, "observed_connection", side_effect=error), self.assertLogs("hormuz.work_gateway", level="WARNING") as logs:
                    self.assertEqual(200, self.post(work)[0])
                self.assertEqual(["WARNING:hormuz.work_gateway:work_connection_recording_unavailable"], logs.output)
                self.assertEqual("succeeded", self.gateway.work_runtime.get_work(self.identity, work)["attempts"][0]["state"])
                before = len(fixtures.WorkProvider.requests)
                status, headers, _ = self.post(work)
                self.assertEqual(200, status)
                self.assertEqual("exact-answer-hit", headers["x-hormuz-cache"])
                self.assertEqual(before, len(fixtures.WorkProvider.requests))
                self.assertEqual(0, self.connections())


class WorkConnectionParserTests(unittest.TestCase):
    def assert_stream_qualification(self, protocol, wire, expected, *, chat=False):
        parser = WorkUsageParser(protocol, is_event_stream=True, chat_completions=chat)
        legacy = ResponseUsageParser(protocol, is_event_stream=True, chat_completions=chat)
        # Split the wire across transport reads; framing and the optional metric
        # must not depend on one complete server write.
        middle = len(wire) // 2
        for part in (wire[:middle], wire[middle:]):
            parser.feed(part); legacy.feed(part)
        self.assertEqual(legacy.finish_with_finance(), parser.finish_with_finance())
        self.assertEqual(expected, parser.work_connection_complete)

    def test_responses_exactly_one_terminal_and_no_resumed_frames_preserves_accounting(self):
        def event(value, *, header=None):
            prefix = "event: " + header + "\n" if header else ""
            return (prefix + "data: " + json.dumps(value) + "\n\n").encode()
        progress = {"type": "response.created", "response": {"object": "response", "status": "in_progress"}}
        complete = {"type": "response.completed", "response": {"object": "response", "status": "completed", "usage": {"input_tokens": 2, "output_tokens": 3}}}
        failed = {"type": "response.failed", "response": {"object": "response", "status": "failed"}}
        incomplete = {"type": "response.incomplete", "response": {"object": "response", "status": "incomplete"}}
        first = event(complete, header="response.completed")
        self.assert_stream_qualification("openai", event(progress, header="response.created") + first, True)
        cases = {"duplicate_completed": first + event(complete),
                 "failed_after_completed": first + event(failed),
                 "incomplete_after_completed": first + event(incomplete),
                 "completed_after_failed": event(failed) + first,
                 "completed_after_incomplete": event(incomplete) + first,
                 "resumed_progress": first + event(progress),
                 "resumed_output": first + event({"type": "response.output_text.delta", "delta": "later"}),
                 "duplicate_terminal_header": first + b"event: response.completed\n\n",
                 "resumed_progress_header": first + b"event: response.in_progress\n\n",
                 "error_header_before_completed": b"event: error\n\n" + first,
                 "wrong_protocol_done": first + b"data: [DONE]\n\n"}
        for name, wire in cases.items():
            with self.subTest(case=name):
                self.assert_stream_qualification("openai", wire, False)

    def test_chat_finish_optional_final_usage_then_done_preserves_accounting(self):
        def event(value):
            return ("data: " + json.dumps(value) + "\n\n").encode()
        ordinary = {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": None}], "usage": None}
        complete = {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
        usage = {"object": "chat.completion.chunk", "choices": [], "usage": complete["usage"]}
        done = b"data: [DONE]\n\n"
        for wire in (event(ordinary) + event(complete) + done,
                     event(ordinary) + event({**complete, "usage": None}) + event(usage) + done,
                     event(complete) + event(usage) + done + b": keepalive\n\n"):
            self.assert_stream_qualification("openai", wire, True, chat=True)
        cases = {"duplicate_finish": event(complete) + event(complete) + done,
                 "resumed_choice": event(complete) + event(ordinary) + done,
                 "resumed_after_usage": event(complete) + event(usage) + event(ordinary) + done,
                 "duplicate_final_usage": event(complete) + event(usage) + event(usage) + done,
                 "empty_before_finish": event(usage) + event(complete) + done,
                 "empty_without_usage": event(complete) + event({"object": "chat.completion.chunk", "choices": []}) + done,
                 "empty_with_null_usage": event(complete) + event({**usage, "usage": None}) + done,
                 "done_before_finish": done + event(complete) + done,
                 "done_before_usage": event(complete) + done + event(usage),
                 "duplicate_done": event(complete) + done + done,
                 "choice_after_done": event(complete) + done + event(ordinary),
                 "finish_after_done": event(complete) + done + event(complete),
                 "event_header_after_finish": event(complete) + b"event: message\n\n" + done,
                 "event_header_after_done": event(complete) + done + b"event: message\n\n",
                 "error_header_before_finish": b"event: error\n\n" + event(complete) + done,
                 "missing_done": event(complete) + event(usage)}
        for name, wire in cases.items():
            with self.subTest(case=name):
                self.assert_stream_qualification("openai", wire, False, chat=True)

    def test_anthropic_terminal_suffix_headers_and_wrong_protocol_done_preserve_accounting(self):
        events = [{"type": "message_start", "message": {"type": "message", "role": "assistant", "stop_reason": None, "usage": {"input_tokens": 2, "output_tokens": 0}}},
                  {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
                  {"type": "message_stop"}]
        wire = b"".join(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode() for event in events)
        self.assert_stream_qualification("anthropic", wire, True)
        for tail in (b"event: message_stop\n\n", b"event: ping\n\n", b"data: [DONE]\n\n"):
            with self.subTest(tail=tail):
                self.assert_stream_qualification("anthropic", wire + tail, False)
        stop = ("event: message_stop\ndata: " + json.dumps(events[-1]) + "\n\n").encode()
        for header in (b"event: message_delta\n\n", b"event: content_block_delta\n\n", b"event: message_start\n\n"):
            with self.subTest(header=header):
                self.assert_stream_qualification("anthropic", wire[:-len(stop)] + header + stop, False)

    def test_terminal_sse_frame_must_close_before_optional_qualification_preserves_accounting(self):
        def event(value):
            return ("data: " + json.dumps(value) + "\n\n").encode()
        usage = {"input_tokens": 2, "output_tokens": 3}
        cases = [("openai", False, event({"type": "response.completed", "response": {"object": "response", "status": "completed", "usage": usage}})),
                 ("openai", True, event({"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}) + b"data: [DONE]\n\n"),
                 ("anthropic", False, event({"type": "message_start", "message": {"type": "message", "role": "assistant", "stop_reason": None, "usage": {"input_tokens": 2, "output_tokens": 0}}})
                  + event({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}) + event({"type": "message_stop"}))]
        for protocol, chat, wire in cases:
            with self.subTest(protocol=protocol, chat=chat):
                self.assert_stream_qualification(protocol, wire, True, chat=chat)
                self.assert_stream_qualification(protocol, wire[:-1], False, chat=chat)
                self.assert_stream_qualification(protocol, wire[:-2], False, chat=chat)
                self.assert_stream_qualification(protocol, wire[:-1] + b": keepalive\n", False, chat=chat)

    def test_anthropic_stream_order_allows_repeated_nonterminal_deltas_and_dispersed_pings(self):
        start = {"type": "message_start", "message": {"type": "message", "role": "assistant", "stop_reason": None, "usage": {"input_tokens": 2, "output_tokens": 0}}}
        ordinary = {"type": "message_delta", "delta": {"stop_reason": None}, "usage": {"output_tokens": 1}}
        terminal = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}
        events = [{"type": "ping"}, start, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                  {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "three"}},
                  {"type": "ping"}, {"type": "content_block_stop", "index": 0}, ordinary, ordinary, terminal, {"type": "ping"}, {"type": "message_stop"}]
        wire = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
        parser, legacy = WorkUsageParser("anthropic", is_event_stream=True), ResponseUsageParser("anthropic", is_event_stream=True)
        parser.feed(wire); legacy.feed(wire)
        self.assertEqual(legacy.finish_with_finance(), parser.finish_with_finance())
        self.assertTrue(parser.work_connection_complete)

    def test_anthropic_duplicate_or_out_of_order_message_frames_withhold_only_connection_evidence(self):
        start = {"type": "message_start", "message": {"type": "message", "role": "assistant", "stop_reason": None, "usage": {"input_tokens": 2, "output_tokens": 0}}}
        terminal = {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}}
        ordinary = {"type": "message_delta", "delta": {"stop_reason": None}, "usage": {"output_tokens": 4}}
        stop = {"type": "message_stop"}
        cases = {"missing_start": [{**terminal, "usage": {"input_tokens": 2, "output_tokens": 3}}, stop],
                 "duplicate_start": [start, start, terminal, stop], "duplicate_terminal": [start, terminal, terminal, stop],
                 "duplicate_stop": [start, terminal, stop, stop], "delta_after_stop": [start, terminal, stop, ordinary],
                 "terminal_after_stop": [start, terminal, stop, terminal], "delta_after_terminal": [start, terminal, ordinary, stop],
                 "start_after_terminal": [start, terminal, start, stop], "premature_stop": [start, stop, terminal, stop],
                 "content_after_metadata": [start, ordinary, {"type": "content_block_stop", "index": 0}, terminal, stop],
                 "no_stop": [start, terminal], "no_terminal": [start, ordinary, stop], "ping_after_stop": [start, terminal, stop, {"type": "ping"}]}
        for name, events in cases.items():
            with self.subTest(case=name):
                wire = b"".join(("data: " + json.dumps(event) + "\n\n").encode() for event in events)
                parser, legacy = WorkUsageParser("anthropic", is_event_stream=True), ResponseUsageParser("anthropic", is_event_stream=True)
                parser.feed(wire); legacy.feed(wire)
                self.assertEqual(legacy.finish_with_finance(), parser.finish_with_finance())
                self.assertFalse(parser.work_connection_complete)

    def test_json_requires_protocol_envelope_and_supported_terminal_not_metadata_alone(self):
        cases = [
            ("openai", False, {"object": "response", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}, "object"),
            ("openai", True, {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}, "object"),
            ("anthropic", False, {"type": "message", "role": "assistant", "stop_reason": "end_turn", "usage": {"input_tokens": 1, "output_tokens": 1}}, "type"),
        ]
        for protocol, chat, valid, required in cases:
            for altered in ({key: value for key, value in valid.items() if key != required}, {**valid, "error": {"type": "error"}}, {**valid, "service_tier": "priority"}):
                parser = WorkUsageParser(protocol, is_event_stream=False, chat_completions=chat)
                parser.feed(json.dumps(altered).encode())
                parser.finish_with_finance()
                self.assertFalse(parser.work_connection_complete, altered)
            parser = WorkUsageParser(protocol, is_event_stream=False, chat_completions=chat)
            parser.feed(json.dumps(valid).encode())
            parser.finish_with_finance()
            self.assertTrue(parser.work_connection_complete)

    def test_malformed_non_scalar_terminal_metadata_is_conservative_and_does_not_raise(self):
        values = [("openai", False, {"object": "response", "status": {"bad": "status"}, "usage": {"input_tokens": 1, "output_tokens": 1}}),
                  ("openai", True, {"object": "chat.completion", "type": [], "choices": [{"index": 0, "finish_reason": ["stop"], "message": {"role": "assistant"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
                  ("anthropic", False, {"type": "message", "role": "assistant", "stop_reason": {"bad": "reason"}, "usage": {"input_tokens": 1, "output_tokens": 1}})]
        for protocol, chat, value in values:
            parser = WorkUsageParser(protocol, is_event_stream=False, chat_completions=chat)
            parser.feed(json.dumps(value).encode())
            parser.finish_with_finance()
            self.assertFalse(parser.work_connection_complete)
        for protocol in ("openai", "anthropic"):
            parser = WorkUsageParser(protocol, is_event_stream=True)
            parser.feed(b'data: {"type": []}\n\n')
            parser.finish_with_finance()
            self.assertFalse(parser.work_connection_complete)

    def test_stream_progress_is_ordinary_but_error_and_unexpected_tail_poison_completion(self):
        complete = {"type": "response.completed", "response": {"object": "response", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}}
        progress = {"type": "response.created", "response": {"object": "response", "status": "in_progress"}}
        def event(value):
            return ("data: " + json.dumps(value) + "\n\n").encode()
        parser = WorkUsageParser("openai", is_event_stream=True)
        parser.feed(event(progress) + event(complete))
        parser.finish_with_finance()
        self.assertTrue(parser.work_connection_complete)
        for bad in ({"type": "error", "error": {"type": "synthetic"}}, [], {"unexpected": "tail"}, {"type": "response.incomplete", "response": {"object": "response", "status": "incomplete"}}):
            for wire in (event(bad) + event(complete), event(complete) + event(bad)):
                parser = WorkUsageParser("openai", is_event_stream=True)
                parser.feed(wire)
                parser.finish_with_finance()
                self.assertFalse(parser.work_connection_complete, bad)

    def test_stream_utf8_validation_rejects_bad_or_incomplete_bytes_and_accepts_split_text(self):
        terminal = {"type": "response.completed", "response": {"object": "response", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}}
        event = ("data: " + json.dumps(terminal) + "\n\n").encode()
        for bad in (b'\xff', b'\xe2'):
            parser = WorkUsageParser("openai", is_event_stream=True)
            parser.feed(event + b': comment ' + bad)
            parser.finish_with_finance()
            self.assertFalse(parser.work_connection_complete)
        parser = WorkUsageParser("openai", is_event_stream=True)
        parser.feed(b': comment \xe2')
        parser.feed(b'\x82\xac\n\n' + event)
        parser.finish_with_finance()
        self.assertTrue(parser.work_connection_complete)

    def test_partial_sse_and_malformed_predecessor_frames_cannot_supply_success(self):
        for chat in (False, True):
            terminal = {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}} if chat else {"type": "response.completed", "response": {"object": "response", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}}
            valid = ("data: " + json.dumps(terminal) + "\n\n").encode()
            for prefix in (b'data: {malformed}\n\n', b'data: {"service_tier":"priority","service_tier":"default"}\n\n'):
                parser = WorkUsageParser("openai", is_event_stream=True, chat_completions=chat)
                parser.feed(prefix + valid + (b"data: [DONE]\n\n" if chat else b""))
                parser.finish_with_finance()
                self.assertFalse(parser.work_connection_complete)
        parser = WorkUsageParser("openai", is_event_stream=True, chat_completions=True)
        parser.feed(valid)
        parser.finish_with_finance()
        self.assertFalse(parser.work_connection_complete)  # No stream terminator.
