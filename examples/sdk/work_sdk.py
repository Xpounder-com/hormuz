#!/usr/bin/env python3
"""One explicit SDK request through an authorized Hormuz AI Work gateway.

Optional vendor SDKs are imported only after --execute. Provider credentials
remain on the gateway; the SDK receives the authorized Hormuz credential.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from importlib import import_module
import json
import os


APIS = ("openai-responses", "openai-chat", "anthropic-messages")
INSTRUCTION = "Return only the sum of 17 and 25."


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
        amount = Decimal(value)
        micros = amount * 1_000_000
        if not amount.is_finite() or not 0 < amount <= 100 or micros != micros.to_integral_value():
            raise ValueError
        return int(micros)
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
                    for _event in response:
                        pass  # Consume the complete stream, including terminal usage.
                finally:
                    response.close()
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
        dispatch(args.api, client.endpoint, credential, request)
        state = job.state()
        # An SDK response never supplies a completion observation. The private
        # gateway record holds attempts, estimates and unknown-charge reservations.
        receipt = {"api": args.api, "work_id": job.work_id, "state": state.get("state"), "attempts_visible": len(state.get("attempts", [])), "completion_observation_submitted": False, "cost_basis": "gateway_configured_rate_estimate", "provider_invoice_reconciled": False}
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
