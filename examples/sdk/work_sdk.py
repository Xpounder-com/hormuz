#!/usr/bin/env python3
"""One explicit SDK request through an authorized Hormuz AI Work gateway.

Optional vendor SDKs are imported only after --execute. Provider credentials
remain on the gateway; the SDK receives the authorized Hormuz credential.
"""

from __future__ import annotations

import argparse
from decimal import Context, Decimal, InvalidOperation, localcontext
from importlib import import_module
import json
import os
import random
import time


APIS = ("openai-responses", "openai-chat", "anthropic-messages")
INSTRUCTION = "Return only the sum of 17 and 25."
MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_STREAM_EVENTS = 8192
_CHAT_FINISHED = {"stop", "tool_calls", "function_call"}
_MESSAGE_FINISHED = {"end_turn", "tool_use", "stop_sequence"}
MAX_CONFIRMATION_REREADS = 3


class SDKExampleFailure(Exception):
    """A sanitized failure that keeps the already-created work inspectable."""

    def __init__(self, work_id: str, failure_type: str):
        self.work_id, self.failure_type = work_id, failure_type
        super().__init__("sdk_example_failed")


def work_id(value: str) -> str:
    if not isinstance(value, str) or not value or not value.isascii() or len(value) > 128 or any(not (c.isalnum() or c in "-_.") for c in value):
        raise argparse.ArgumentTypeError("Use the work ID from your private gateway record.")
    return value


def budget(value: str) -> int:
    try:
        # Invalid input sets Decimal signal flags; keep parsing and arithmetic
        # isolated from the caller's flags, precision, rounding, and traps.
        with localcontext(Context(prec=28)):
            amount = Decimal(value)
            if not amount.is_finite() or not 0 < amount <= 100 or amount != amount.quantize(Decimal("0.000001")):
                raise ValueError
            return int(amount * 1_000_000)
    except (InvalidOperation, ValueError, OverflowError):
        raise argparse.ArgumentTypeError("Use a positive USD amount up to 100, with at most six decimal places.") from None


def payload(api: str, model: str, headers: dict, *, stream: bool, max_output_tokens: int) -> dict:
    common = {"model": model, "stream": stream, "extra_headers": dict(headers)}
    if api == "openai-responses":
        return {**common, "input": INSTRUCTION, "max_output_tokens": max_output_tokens, "store": False}
    if api == "openai-chat":
        value = {**common, "messages": [{"role": "user", "content": INSTRUCTION}], "max_completion_tokens": max_output_tokens, "store": False}
        if stream:
            value["stream_options"] = {"include_usage": True}
        return value
    if api == "anthropic-messages":
        return {**common, "messages": [{"role": "user", "content": INSTRUCTION}], "max_tokens": max_output_tokens}
    raise ValueError("unsupported_example_api")


def _field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _failure(value):
    kind, status = _field(value, "type"), _field(value, "status")
    if _field(value, "error") is not None or kind in {"error", "response.failed", "response.error"} or status in {"failed", "cancelled", "canceled"}:
        raise ValueError("sdk_provider_response_failed")
    if kind == "response.incomplete" or status in {"incomplete", "in_progress", "queued"}:
        raise ValueError("sdk_provider_response_incomplete")


def _finished(reason, accepted):
    if reason is None or reason in {"length", "max_tokens", "pause_turn"}:
        raise ValueError("sdk_provider_response_incomplete")
    if reason not in accepted:
        raise ValueError("sdk_provider_response_failed")


def _choices(value):
    choices = _field(value, "choices")
    if not isinstance(choices, list) or len(choices) > 128:
        raise ValueError("sdk_provider_response_invalid")
    return choices


def validate_sdk_response(api, response):
    """Check SDK response metadata directly; do not copy or serialize its body."""
    _failure(response)
    if api == "openai-responses":
        if _field(response, "object") != "response":
            raise ValueError("sdk_provider_response_invalid")
        if _field(response, "status") != "completed":
            raise ValueError("sdk_provider_response_incomplete")
        output = _field(response, "output")
        if not isinstance(output, list) or not output:
            raise ValueError("sdk_provider_response_invalid")
        for item in output:
            _failure(item)
        if not any(_field(item, "type") in {"message", "function_call"} for item in output):
            raise ValueError("sdk_provider_response_invalid")
    elif api == "openai-chat":
        if _field(response, "object") != "chat.completion" or not _choices(response):
            raise ValueError("sdk_provider_response_invalid")
        for choice in _choices(response):
            _finished(_field(choice, "finish_reason"), _CHAT_FINISHED)
            if _field(_field(choice, "message"), "role") != "assistant":
                raise ValueError("sdk_provider_response_invalid")
    elif api == "anthropic-messages":
        if _field(response, "type") != "message" or _field(response, "role") != "assistant":
            raise ValueError("sdk_provider_response_invalid")
        _finished(_field(response, "stop_reason"), _MESSAGE_FINISHED)
        if not isinstance(_field(response, "content"), list) or not _field(response, "content"):
            raise ValueError("sdk_provider_response_invalid")
    else:
        raise ValueError("unsupported_example_api")


class _StreamObserver:
    """Count bytes and recognize Chat's hidden DONE frame without storing text.

    The SDK consumes [DONE] before yielding typed chunks. Fixed-pattern match
    positions, line lengths and frame counters are the only retained wire data.
    """

    _patterns = (b"data: [DONE]", b"data:[DONE]")

    def __init__(self, chat):
        self.chat, self.bytes, self.done = chat, 0, False
        self.position, self.matches, self.data_prefix = 0, [True, True], True
        self.frame_data, self.frame_done, self.previous_cr = 0, False, False

    def _line_end(self):
        if self.position == 0:
            if self.frame_done:
                if self.frame_data != 1 or self.done:
                    raise ValueError("sdk_provider_response_invalid")
                self.done = True
            elif self.done and self.frame_data:
                raise ValueError("sdk_provider_response_invalid")
            self.frame_data, self.frame_done = 0, False
        elif self.data_prefix and self.position >= 5:
            self.frame_data += 1
            if any(match and self.position == len(pattern) for match, pattern in zip(self.matches, self._patterns)):
                self.frame_done = True
        self.position, self.matches, self.data_prefix = 0, [True, True], True

    def feed(self, chunk):
        self.bytes += len(chunk)
        if self.bytes > MAX_STREAM_BYTES:
            raise ValueError("sdk_stream_limit_exceeded")
        if not self.chat:
            return
        for item in chunk:
            if item == 10 and self.previous_cr:
                self.previous_cr = False
                continue
            self.previous_cr = item == 13
            if item in {10, 13}:
                self._line_end()
                continue
            for index, pattern in enumerate(self._patterns):
                self.matches[index] = self.matches[index] and self.position < len(pattern) and item == pattern[self.position]
            if self.position < 5:
                self.data_prefix = self.data_prefix and item == b"data:"[self.position]
            self.position += 1


def consume_sdk_stream(api, response):
    """Validate yielded terminal metadata while the SDK owns stream decoding."""
    http_response = _field(response, "response")
    original = _field(http_response, "iter_bytes")
    if not callable(original):
        raise ValueError("sdk_stream_response_unavailable")
    observer = _StreamObserver(api == "openai-chat")

    def observed_bytes(*args, **kwargs):
        for chunk in original(*args, **kwargs):
            observer.feed(chunk)
            yield chunk

    # SDK stream construction is lazy. Observe the public response byte iterator
    # once, then restore it; the vendor stream still consumes and closes it.
    http_response.iter_bytes = observed_bytes
    terminal, started, reason = False, False, False
    choices, finished = set(), set()
    try:
        for count, event in enumerate(response, 1):
            if count > MAX_STREAM_EVENTS:
                raise ValueError("sdk_stream_limit_exceeded")
            _failure(event)
            if terminal:
                raise ValueError("sdk_provider_response_invalid")
            kind = _field(event, "type")
            if api == "openai-responses":
                if kind == "response.completed":
                    validate_sdk_response(api, _field(event, "response"))
                    terminal = True
            elif api == "openai-chat":
                if _field(event, "object") != "chat.completion.chunk":
                    raise ValueError("sdk_provider_response_invalid")
                for choice in _choices(event):
                    index = _field(choice, "index")
                    if type(index) is not int or index < 0 or index in finished or len(choices) >= 128 and index not in choices:
                        raise ValueError("sdk_provider_response_invalid")
                    choices.add(index)
                    if _field(choice, "finish_reason") is not None:
                        _finished(_field(choice, "finish_reason"), _CHAT_FINISHED)
                        finished.add(index)
            elif api == "anthropic-messages":
                if kind == "message_start":
                    message = _field(event, "message")
                    if started or _field(message, "type") != "message" or _field(message, "role") != "assistant":
                        raise ValueError("sdk_provider_response_invalid")
                    _failure(message)
                    started = True
                elif kind == "message_delta":
                    if not started:
                        raise ValueError("sdk_provider_response_invalid")
                    _finished(_field(_field(event, "delta"), "stop_reason"), _MESSAGE_FINISHED)
                    reason = True
                elif kind == "message_stop":
                    if not started or not reason:
                        raise ValueError("sdk_provider_response_incomplete")
                    terminal = True
            else:
                raise ValueError("unsupported_example_api")
        if api == "openai-chat":
            terminal = bool(choices) and choices == finished and observer.done
        if not terminal:
            raise ValueError("sdk_provider_response_incomplete")
    finally:
        http_response.iter_bytes = original


def dispatch(api: str, endpoint: str, credential: str, request: dict) -> None:
    if api not in APIS:
        raise ValueError("unsupported_example_api")
    vendor = import_module("openai" if api.startswith("openai-") else "anthropic")
    # Use the installed vendor's exported transport rather than a raw httpx
    # client: current SDKs can require HTTPX2 even under the legacy class name.
    transport_class = getattr(vendor, "DefaultHttpx2Client", None) or getattr(vendor, "DefaultHttpxClient", None)
    if transport_class is None:
        raise ValueError("sdk_transport_unavailable")
    # The SDK must not replay requests invisibly or follow a gateway redirect.
    # Each intentional retry should stay attached to its existing work record.
    transport = transport_class(follow_redirects=False, timeout=30)
    try:
        if api.startswith("openai-"):
            sdk = vendor.OpenAI(api_key=credential, base_url=endpoint + "/v1", http_client=transport, max_retries=0, timeout=30)
        else:
            sdk = vendor.Anthropic(api_key=credential, base_url=endpoint, http_client=transport, max_retries=0, timeout=30)
        with sdk:
            target = sdk.responses if api == "openai-responses" else sdk.chat.completions if api == "openai-chat" else sdk.messages
            response = target.create(**request)
            if request["stream"]:
                try:
                    consume_sdk_stream(api, response)
                finally:
                    response.close()
            else:
                validate_sdk_response(api, response)
    finally:
        transport.close()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--api", choices=APIS, required=True)
    value.add_argument("--model", required=True, help="A model alias approved by your gateway operator.")
    value.add_argument("--execute", action="store_true", help="Make one gateway request; the provider may bill it.")
    value.add_argument("--stream", action="store_true")
    value.add_argument("--objective", choices=("cost", "speed", "quality"), default="cost")
    value.add_argument("--budget-usd", type=budget, default=100_000, help="Job budget based on the gateway's configured estimates (default: 0.10 USD).")
    value.add_argument("--max-output-tokens", type=int, choices=range(1, 1025), metavar="1..1024", default=256)
    value.add_argument("--repository", default="examples/sdk-demo")
    value.add_argument("--context-revision", default="sdk-example-v1")
    value.add_argument("--work-id", type=work_id, help="Reuse an existing authorized work ID for an intentional retry.")
    return value


def confirm_gateway_response(job, previous):
    """Wait briefly for settlement after SDK terminal delivery, without replay."""
    for reread in range(MAX_CONFIRMATION_REREADS + 1):
        state = job.state()
        attempts = [row for row in state.get("attempts", []) if row["request_id"] not in previous]
        groups = {("logical", row["logical_request_id"]) if row.get("logical_request_id") is not None
                  else ("request", row["request_id"]) for row in attempts}
        if len(groups) > 1:
            raise ValueError("sdk_gateway_confirmation_ambiguous")
        if any(row.get("response_succeeded") is True for row in attempts):
            return state, reread + 1
        # A pending gateway settlement may follow terminal stream delivery.
        # Terminal failure/uncertainty and missing attribution are not retried.
        if reread == MAX_CONFIRMATION_REREADS or not any(row.get("state") == "pending" for row in attempts):
            raise ValueError("sdk_response_not_confirmed_by_gateway")
        time.sleep(0.05 * 2**reread + random.uniform(0, 0.025))


def execute(args, client, credential: str) -> dict:
    identifier = args.work_id
    if identifier is None:
        created = client.create_job(args.repository, title="SDK connection example", task_type="sdk-connection", context_revision=args.context_revision)
        identifier = created["work_id"]
    identifier = work_id(identifier)
    try:
        job = client.job(identifier)
        client.set_plan("job", job.work_id, budget_microusd=args.budget_usd, objective=args.objective)
        request = payload(args.api, args.model, job.headers, stream=args.stream, max_output_tokens=args.max_output_tokens)
        previous = {row["request_id"] for row in job.state().get("attempts", [])}
        dispatch(args.api, client.endpoint, credential, request)
        state, confirmation_reads = confirm_gateway_response(job, previous)
        costs = state.get("costs", {})
        # An SDK response never supplies a completion observation. The private
        # gateway record holds attempts, estimates and unknown-charge reservations.
        receipt = {"api": args.api, "work_id": job.work_id, "state": state.get("state"), "attempts_visible": len(state.get("attempts", [])), "gateway_confirmation_reads": confirmation_reads, "endpoint_completion_verified": True, "response_confirmed_by_gateway": True, "completion_observation_submitted": False, "cost_basis": costs.get("cost_basis", "unknown"), "unsettled_job_charges": {"pending_microusd": costs.get("pending_microusd"), "uncertain_microusd": costs.get("uncertain_microusd")}, "provider_invoice_reconciled": False}
    except Exception as failure:
        # Keep the work handle across import, transport, plan and receipt errors.
        # The original SDK exception may contain credentials or response bodies.
        raise SDKExampleFailure(identifier, type(failure).__name__) from None
    return receipt


def main(argv=None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if not args.execute:
        arguments.error("Add --execute to make one request through your authorized gateway.")
    try:
        from hormuz.work_client import WorkClient
        client = WorkClient.from_environment()
        receipt = execute(args, client, os.environ["HORMUZ_TOKEN"])
    except Exception as failure:
        # SDK exceptions can include upstream response text. Report only a
        # fixed classification and exception type in this shareable example.
        receipt = {"status": "example_failed", "failure_type": failure.failure_type if isinstance(failure, SDKExampleFailure) else type(failure).__name__, "detail": "Inspect your private gateway record and installed SDK compatibility."}
        if isinstance(failure, SDKExampleFailure):
            receipt["work_id"] = failure.work_id
        print(json.dumps(receipt))
        return 1
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
