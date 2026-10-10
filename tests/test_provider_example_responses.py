"""Complete responses differ from successful headers or partial stream content."""
import json
import unittest

from tools.provider_example_responses import validate_response


RESPONSES = {"object": "response", "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "42"}]}]}
CHAT = {"object": "chat.completion", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "42"}}]}
MESSAGES = {"type": "message", "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "42"}]}


def stream(*values):
    return "".join("data: " + (value if isinstance(value, str) else json.dumps(value)) + "\n\n" for value in values).encode()


def chunk(index=0, finish=None, content="42"):
    return {"object": "chat.completion.chunk", "choices": [{"index": index, "delta": {"content": content}, "finish_reason": finish}]}


def message_stream(stop="end_turn"):
    return [
        {"type": "message_start", "message": {**MESSAGES, "content": [], "stop_reason": None}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "42"}},
        {"type": "message_delta", "delta": {"stop_reason": stop}},
        {"type": "message_stop"},
    ]


class ProviderResponseTests(unittest.TestCase):
    def validate(self, api, raw, behavior="stream"):
        return validate_response(api, behavior, 200,
                                 {"Content-Type": "text/event-stream" if behavior == "stream" else "application/json; charset=utf-8"}, raw)

    def rejects(self, api, raw, reason, behavior="stream"):
        with self.assertRaisesRegex(ValueError, "^" + reason + "$"):
            self.validate(api, raw, behavior)

    def test_complete_json_and_tool_replies(self):
        for api, value in (("/v1/responses", RESPONSES), ("/v1/chat/completions", CHAT), ("/v1/messages", MESSAGES)):
            with self.subTest(api=api):
                self.assertTrue(self.validate(api, json.dumps(value).encode(), "request"))
        response_tool = {**RESPONSES, "output": [{"type": "function_call", "name": "record_result", "call_id": "call-1", "arguments": '{"value":42}'}]}
        message_tool = {**MESSAGES, "stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "tool-1", "name": "record_result", "input": {"value": 42}}]}
        self.assertTrue(self.validate("/v1/responses", json.dumps(response_tool), "tools"))
        self.assertTrue(self.validate("/v1/messages", json.dumps(message_tool), "tools"))
        chat_tool = {**CHAT, "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"type": "function", "id": "tool-1", "function": {"name": "record_result", "arguments": '{"value":42}'}}]}}]}
        self.assertTrue(self.validate("/v1/chat/completions", json.dumps(chat_tool), "tools"))

    def test_json_partial_and_failed_states_are_not_success(self):
        for status, reason in (("incomplete", "incomplete_provider_response"), ("in_progress", "incomplete_provider_response"), ("failed", "failed_provider_response")):
            self.rejects("/v1/responses", json.dumps({**RESPONSES, "status": status}), reason, "request")
        self.rejects("/v1/responses", json.dumps({**RESPONSES, "error": {"message": "private-body"}}), "failed_provider_response", "request")
        self.rejects("/v1/chat/completions", json.dumps({**CHAT, "choices": [{**CHAT["choices"][0], "finish_reason": "length"}]}), "incomplete_provider_response", "request")
        self.rejects("/v1/messages", json.dumps({**MESSAGES, "stop_reason": "max_tokens"}), "incomplete_provider_response", "request")

    def test_responses_stream_requires_completed_terminal_response(self):
        delta = {"type": "response.output_text.delta", "delta": "42"}
        completed = {"type": "response.completed", "response": RESPONSES}
        self.assertTrue(self.validate("/v1/responses", stream(delta, completed)))
        self.rejects("/v1/responses", stream(delta), "incomplete_provider_response")
        self.rejects("/v1/responses", stream(delta, {"type": "response.incomplete", "response": {**RESPONSES, "status": "incomplete"}}), "incomplete_provider_response")
        self.rejects("/v1/responses", stream(delta, {"type": "response.failed", "response": {"error": {"message": "private-provider-text"}}}), "failed_provider_response")
        self.rejects("/v1/responses", stream({"type": "response.completed", "response": {**RESPONSES, "status": "in_progress"}}), "incomplete_provider_response")

    def test_chat_stream_requires_finish_reason_then_done(self):
        self.assertTrue(self.validate("/v1/chat/completions", stream(chunk(), chunk(finish="stop", content=""), "[DONE]")))
        self.rejects("/v1/chat/completions", stream(chunk(), "[DONE]"), "incomplete_provider_response")
        self.rejects("/v1/chat/completions", stream(chunk(finish="stop")), "incomplete_provider_response")
        self.rejects("/v1/chat/completions", stream(chunk(finish="length"), "[DONE]"), "incomplete_provider_response")
        self.rejects("/v1/chat/completions", stream(chunk(finish="content_filter"), "[DONE]"), "failed_provider_response")
        self.rejects("/v1/chat/completions", stream(chunk(), {"error": {"message": "private-provider-text"}}, "[DONE]"), "failed_provider_response")

    def test_chat_usage_is_optional_and_all_choices_must_finish(self):
        usage = {"object": "chat.completion.chunk", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
        self.assertTrue(self.validate("/v1/chat/completions", stream(chunk(finish="stop"), usage, "[DONE]")))
        self.assertTrue(self.validate("/v1/chat/completions", stream(chunk(finish="tool_calls"), "[DONE]")))
        self.rejects("/v1/chat/completions", stream(chunk(0, "stop"), chunk(1), "[DONE]"), "incomplete_provider_response")
        self.rejects("/v1/chat/completions", stream(chunk(finish="stop"), chunk(), "[DONE]"), "invalid_provider_response")

    def test_anthropic_stream_requires_start_reason_and_stop(self):
        self.assertTrue(self.validate("/v1/messages", stream(*message_stream())))
        self.assertTrue(self.validate("/v1/messages", stream(*message_stream("tool_use"))))
        self.rejects("/v1/messages", stream(*message_stream()[:-1]), "incomplete_provider_response")
        self.rejects("/v1/messages", stream(message_stream()[0], {"type": "message_stop"}), "incomplete_provider_response")
        self.rejects("/v1/messages", stream(*message_stream("max_tokens")), "incomplete_provider_response")
        self.rejects("/v1/messages", stream(message_stream()[0], {"type": "error", "error": {"message": "private-provider-text"}}), "failed_provider_response")

    def test_sse_supports_comments_crlf_and_multiline_data(self):
        raw = ': keepalive\r\nevent: response.completed\r\ndata: {"type": "response.completed",\r\ndata: "response": ' + json.dumps(RESPONSES) + '}\r\n\r\n'
        self.assertTrue(self.validate("/v1/responses", raw))
        self.rejects("/v1/responses", raw.rstrip("\r\n"), "incomplete_provider_response")

    def test_malformed_terminal_and_events_after_completion_are_rejected(self):
        self.rejects("/v1/responses", stream({"type": "response.completed"}), "invalid_provider_response")
        self.rejects("/v1/responses", stream({"type": "response.completed", "response": RESPONSES}, {"type": "error"}), "invalid_provider_response")
        raw = 'event: response.completed\ndata: {"type":"response.failed"}\n\n'
        self.rejects("/v1/responses", raw, "failed_provider_response")

    def test_invalid_json_utf8_structure_and_content_type_use_fixed_errors(self):
        for raw in (b'private-provider-text', b'\xff', b'[]', b'{"object":"response","object":"response"}', b'{"output":NaN}'):
            with self.subTest(raw=raw):
                self.rejects("/v1/responses", raw, "invalid_provider_response", "request")
        for api, value in (("/v1/responses", {**RESPONSES, "output": []}), ("/v1/messages", {**MESSAGES, "content": [{"type": "text"}]}), ("/v1/chat/completions", {**CHAT, "choices": [{}]})):
            self.rejects(api, json.dumps(value), "invalid_provider_response", "request")
        with self.assertRaisesRegex(ValueError, "^invalid_provider_response$"):
            validate_response("/v1/messages", "stream", 200, {"content-type": "application/json"}, stream(*message_stream()))
        with self.assertRaisesRegex(ValueError, "^response_status_not_successful$"):
            validate_response("/v1/messages", "request", 429, {}, b'private-provider-text')

    def test_nested_invalid_types_are_sanitized(self):
        invalid = (
            ("/v1/responses", {**RESPONSES, "type": ["private-body"]}),
            ("/v1/responses", {**RESPONSES, "status": {"private-body": True}}),
            ("/v1/responses", {**RESPONSES, "output": [{"type": "private-body"}]}),
            ("/v1/messages", {**MESSAGES, "stop_reason": ["private-body"]}),
            ("/v1/chat/completions", {**CHAT, "choices": [{**CHAT["choices"][0], "message": {"role": "assistant", "content": None, "tool_calls": True}}]}),
        )
        for api, value in invalid:
            with self.subTest(api=api):
                self.rejects(api, json.dumps(value), "invalid_provider_response", "request")


if __name__ == "__main__":
    unittest.main()
