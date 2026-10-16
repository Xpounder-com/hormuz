"""Bounded SSE observation; only complete native terminal data grants success."""
from .compaction_formats import strict_json_loads
from .usage import Usage
from .native_codex_state import continuation_tags
from .finance_attempts import NativeUsageAccumulator


MAX_EVENT_BYTES = 1024 * 1024


class NativeCodexStream:
    def __init__(self, model):
        self.model = model
        self.usage = Usage()
        self.terminal_state = None
        self.output = []
        self.response_id = None
        self.invalid = False
        self._buffer = bytearray()
        self._data = []
        self._event = None
        self._event_bytes = 0

    def feed(self, chunk):
        try:
            self._buffer.extend(chunk)
            while b"\n" in self._buffer:
                line, _, tail = self._buffer.partition(b"\n")
                self._buffer = bytearray(tail)
                if len(line) > MAX_EVENT_BYTES:
                    raise ValueError("native_event_oversized")
                line = line.rstrip(b"\r")
                if not line:
                    self._dispatch()
                    self._data, self._event, self._event_bytes = [], None, 0
                elif line.startswith(b"data:"):
                    self._event_bytes += len(line)
                    if self._event_bytes > MAX_EVENT_BYTES:
                        raise ValueError("native_event_oversized")
                    self._data.append(bytes(line[5:]).removeprefix(b" "))
                elif line.startswith(b"event:"):
                    self._event = bytes(line[6:]).strip().decode("utf-8")
            if len(self._buffer) > MAX_EVENT_BYTES:
                raise ValueError("native_event_oversized")
        except (ValueError, RecursionError):
            self.invalid = True
            raise ValueError("native_event_invalid") from None

    def _dispatch(self):
        if not self._data:
            return
        data = b"\n".join(self._data)
        if data.strip() == b"[DONE]":
            return
        value = strict_json_loads(data.decode("utf-8"))
        if type(value) is not dict or type(value.get("type")) is not str or self.terminal_state is not None:
            raise ValueError("native_event_invalid")
        kind = value["type"]
        if self._event is not None and self._event not in {"message", kind}:
            raise ValueError("native_event_mismatch")
        if "response" in value:
            response = value["response"]
            if type(response) is not dict or response.get("model") != self.model:
                raise ValueError("native_response_invalid")
            self._bind_response_id(response.get("id"))
        if "response_id" in value:
            self._bind_response_id(value["response_id"])
        if kind == "error":
            self.terminal_state = "failed"
        elif kind in {"response.completed", "response.failed", "response.incomplete"}:
            response = value.get("response")
            state = kind.removeprefix("response.")
            if (type(response) is not dict or response.get("model") != self.model
                    or response.get("status") != state or type(response.get("id")) is not str
                    or not 1 <= len(response["id"]) <= 256
                    or any(not 33 <= ord(c) <= 126 for c in response["id"])
                    or type(response.get("output", [])) is not list
                    or state == "completed" and "output" not in response):
                raise ValueError("native_terminal_invalid")
            output = response.get("output", [])
            self._validate_output(output)
            self.terminal_state, self.output = state, output
            usage = response.get("usage")
            observed = NativeUsageAccumulator("openai")
            observed.observe_openai_response(response)
            if observed.finish().state == "complete":
                self.usage = Usage(input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                                   provider_reported_model=self.model, evidence_complete=True)

    def _bind_response_id(self, response_id):
        if (type(response_id) is not str or not 1 <= len(response_id) <= 256
                or any(not 33 <= ord(c) <= 126 for c in response_id)
                or self.response_id is not None and self.response_id != response_id):
            raise ValueError("native_response_invalid")
        self.response_id = response_id

    @staticmethod
    def _validate_output(output):
        for item in output:
            if type(item) is not dict or type(item.get("type")) is not str or not item["type"]:
                raise ValueError("native_output_invalid")
            if item["type"] == "message":
                if item.get("role") != "assistant" or type(item.get("content")) is not list:
                    raise ValueError("native_output_invalid")
                for part in item["content"]:
                    if (type(part) is not dict or type(part.get("type")) is not str
                            or part["type"] not in {"output_text", "refusal"}):
                        raise ValueError("native_output_invalid")
                    field = "text" if part["type"] == "output_text" else "refusal"
                    if type(part.get(field)) is not str:
                        raise ValueError("native_output_invalid")
        continuation_tags(output)

    def finish(self):
        if self._buffer or self._data:
            self.invalid = True
        if self.invalid:
            self.usage.evidence_complete = False
        return self.usage
