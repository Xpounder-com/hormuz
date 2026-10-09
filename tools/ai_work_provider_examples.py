#!/usr/bin/env python3
"""Run an AI Work tour locally, or explicitly select bounded live provider calls."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.provider_example_transport import BoundedFixtureServer, LoopbackTransport, UPSTREAM_TIMEOUT
from tools.provider_example_responses import validate_response
RATES = ("input_cost_per_million", "output_cost_per_million", "cache_read_cost_per_million", "cache_write_cost_per_million")


@dataclass(frozen=True)
class Example:
    name: str
    api: str
    instruction: str
    behavior: str = "request"
    objective: str = "cost"
    live: bool = True

    @property
    def provider(self):
        return "anthropic" if self.api == "/v1/messages" else "openai"


EXAMPLES = (
    Example("openai-smoke", "/v1/responses", "Return only the sum of 17 and 25."),
    Example("openai-explain-code", "/v1/responses", "Explain why Python's sorted([3, 1, 2]) returns [1, 2, 3]."),
    Example("openai-review-change", "/v1/responses", "Review changing a Python default list argument to None. Give one reason."),
    Example("openai-ci-diagnosis", "/v1/responses", "Explain a CI failure that says ModuleNotFoundError: requests."),
    Example("chat-smoke", "/v1/chat/completions", "Return only the sum of 17 and 25."),
    Example("chat-extract-fields", "/v1/chat/completions", "Return a JSON object with language Python and version 3.11."),
    Example("chat-release-note", "/v1/chat/completions", "Write one release note for a corrected off-by-one error."),
    Example("anthropic-smoke", "/v1/messages", "Return only the sum of 17 and 25."),
    Example("anthropic-summary", "/v1/messages", "Summarize: the build passed, deployment is pending, rollback is available."),
    Example("anthropic-marketing-brief", "/v1/messages", "Draft one factual line inviting developers to try an open-source gateway."),
    Example("responses-stream", "/v1/responses", "Return only the sum of 17 and 25.", "stream"),
    Example("chat-stream", "/v1/chat/completions", "Return only the sum of 17 and 25.", "stream"),
    Example("messages-stream", "/v1/messages", "Return only the sum of 17 and 25.", "stream"),
    Example("responses-tool-declaration", "/v1/responses", "Use record_result to record the number 42.", "tools"),
    Example("messages-tool-declaration", "/v1/messages", "Use record_result to record the number 42.", "tools"),
    Example("speed-priority", "/v1/responses", "Explain HTTP 429 in one sentence.", objective="speed"),
    Example("outcome-priority", "/v1/messages", "Explain HTTP 429 in one sentence.", objective="quality"),
    Example("budget-stop", "/v1/responses", "Return 42.", "budget", live=False),
    Example("exact-answer-reuse", "/v1/responses", "Return 42.", "cache", live=False),
    Example("correction-bypasses-reuse", "/v1/responses", "Return 42.", "correction", live=False),
    Example("context-change", "/v1/responses", "Return 42.", "context", live=False),
)


def request_body(example, *, output_tokens=256):
    body = {"model": example.provider}
    if example.api == "/v1/responses":
        body.update(input=example.instruction, max_output_tokens=output_tokens, store=False)
    else:
        body["messages"] = [{"role": "user", "content": example.instruction}]
        body["max_completion_tokens" if example.provider == "openai" else "max_tokens"] = output_tokens
        if example.provider == "openai":
            body["store"] = False
    if example.behavior == "stream":
        body["stream"] = True
        if example.api == "/v1/chat/completions":
            body["stream_options"] = {"include_usage": True}
    if example.behavior in {"cache", "correction", "context"}:
        body["temperature"] = 0
    if example.behavior == "tools":
        definition = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"], "additionalProperties": False}
        body["tools"] = [{"type": "function", "name": "record_result", "description": "Declare a result; this example executes no external action.", "parameters": definition}] if example.provider == "openai" else [{"name": "record_result", "description": "Declare a result; this example executes no external action.", "input_schema": definition}]
    return body


def selected_examples(names, live):
    catalog = {item.name: item for item in EXAMPLES}
    if names:
        if len(names) != len(set(names)) or any(name not in catalog for name in names):
            raise ValueError("invalid_or_duplicate_example")
        result = [catalog[name] for name in names]
    elif live:
        defaults = ["openai-smoke", "chat-smoke", "responses-stream"] if live == "openai" else ["anthropic-smoke", "anthropic-summary", "messages-stream"] if live == "anthropic" else ["openai-smoke", "chat-smoke", "anthropic-smoke"]
        result = [catalog[name] for name in defaults]
    else:
        result = list(EXAMPLES)
    if live and any(not item.live or live != "both" and item.provider != live for item in result):
        raise ValueError("example_not_enabled_for_selected_live_provider")
    return result


def qualified_routes(value, providers):
    if not isinstance(value, dict):
        raise ValueError("invalid_rate_card")
    routes = {}
    for provider in providers:
        profile = value.get(provider)
        if not isinstance(profile, dict) or set(profile) != {"model", *RATES}:
            raise ValueError("rate_card_requires_model_and_all_four_rates")
        model = profile["model"]
        if not isinstance(model, str) or not model or len(model) > 128 or any(ord(c) < 33 or ord(c) == 127 for c in model):
            raise ValueError("invalid_provider_model")
        for name in RATES:
            number = profile[name]
            if type(number) not in (int, float) or not math.isfinite(number) or number < 0 or name in RATES[:2] and number == 0:
                raise ValueError("invalid_rate_card_amount")
        routes[provider] = {"protocol": provider, "upstream_model": model, **{name: profile[name] for name in RATES}}
    return routes


class ProviderFixture(BaseHTTPRequestHandler):
    """Declared synthetic wire responses, never a quality benchmark."""
    calls = []
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 65536:
            self.close_connection = True
            self.send_error(413)
            return
        request = json.loads(self.rfile.read(size))
        self.calls.append({"path": self.path, "model": request["model"]})
        identifier = "synthetic-" + str(len(self.calls))
        usage = {"input_tokens": 24, "output_tokens": 8, "total_tokens": 32}
        if self.path == "/v1/messages":
            answer = {"id": identifier, "type": "message", "role": "assistant", "model": request["model"], "content": [{"type": "text", "text": "42"}], "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 24, "output_tokens": 8}}
            if request.get("tools"):
                answer.update(content=[{"type": "tool_use", "id": "synthetic-tool", "name": "record_result", "input": {"value": 42}}], stop_reason="tool_use")
            events = [("message_start", {"type": "message_start", "message": {**answer, "content": [], "stop_reason": None, "usage": {"input_tokens": 24, "output_tokens": 0}}}), ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}), ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "42"}}), ("content_block_stop", {"type": "content_block_stop", "index": 0}), ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 8}}), ("message_stop", {"type": "message_stop"})]
        elif self.path == "/v1/chat/completions":
            answer = {"id": identifier, "object": "chat.completion", "model": request["model"], "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "42"}}], "usage": {"prompt_tokens": 24, "completion_tokens": 8, "total_tokens": 32}}
            events = [(None, {"id": identifier, "object": "chat.completion.chunk", "model": request["model"], "choices": [{"index": 0, "delta": {"content": "42"}, "finish_reason": None}]}), (None, {"id": identifier, "object": "chat.completion.chunk", "model": request["model"], "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}), (None, {"id": identifier, "object": "chat.completion.chunk", "model": request["model"], "choices": [], "usage": answer["usage"]})]
        else:
            answer = {"id": identifier, "object": "response", "model": request["model"], "status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "42"}]}], "usage": usage}
            if request.get("tools"):
                answer["output"] = [{"type": "function_call", "id": "synthetic-function", "call_id": "synthetic-call", "name": "record_result", "arguments": '{"value":42}'}]
            events = [(None, {"type": "response.output_text.delta", "delta": "42"}), (None, {"type": "response.completed", "response": answer})]
        if request.get("stream"):
            raw = "".join((f"event: {name}\n" if name else "") + "data: " + json.dumps(value) + "\n\n" for name, value in events)
            if self.path == "/v1/chat/completions":
                raw += "data: [DONE]\n\n"
            content_type, encoded = "text/event-stream", raw.encode()
        else:
            content_type, encoded = "application/json", json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    started = False
    try:
        thread.start()
        started = True
        yield server
    finally:
        try:
            if started:
                server.shutdown()
        finally:
            server.server_close()
            if started:
                thread.join(timeout=5)


def gateway_profile(routes, upstreams, *, live):
    providers = sorted(routes)
    return {
        "listen": {"host": "127.0.0.1", "port": 8787},
        "database": "usage.sqlite3", "upstreams": upstreams,
        "upstream_timeout_seconds": UPSTREAM_TIMEOUT,
        "identities": [{"token_env": "HORMUZ_EXAMPLE_TOKEN", "actor_id": "example-operator", "actor_name": "Example operator", "team_id": "examples", "team_name": "Examples", "organization_id": "example-workspace", "allowed_clients": ["codex", "claude-code"]}],
        "model_routes": routes,
        "policies": {"organization": {"allowed_clients": ["codex", "claude-code"], "allowed_models": providers, "max_output_tokens": 256, "fallback_models": {provider: provider for provider in providers}}},
        "ai_work": {"enabled": True, "database": "work.sqlite3", "cache_enabled": not live, "minimum_samples": 3, "administrator_actor_ids": ["example-operator"], "tool_capable_aliases": providers},
    }


def run_gateway(examples, routes, upstreams, environment, *, live, budget_microusd, max_live_calls):
    from dataclasses import replace
    from hormuz.config import GatewayConfig
    from hormuz.server import GatewayServer
    from hormuz.work_client import WorkClient

    token = secrets.token_urlsafe(32)
    environment = {**environment, "HORMUZ_EXAMPLE_TOKEN": token}
    with tempfile.TemporaryDirectory(prefix="hormuz-examples-") as folder:
        path = Path(folder) / "gateway.json"
        config_value = gateway_profile(routes, upstreams, live=live)
        path.write_text(json.dumps(config_value))
        config = GatewayConfig.load(path, environ=environment)
        config = replace(config, listen=replace(config.listen, port=0))
        with serving(GatewayServer(config, environ=environment)) as gateway, LoopbackTransport(gateway.server_port) as transport:
            client = WorkClient(f"http://127.0.0.1:{gateway.server_port}", token, allow_loopback_http=True)
            client._opener = transport
            client.set_plan("workspace", "example-workspace", budget_microusd=budget_microusd, objective="cost")
            checks, attempted, observed = [], 0, 0

            def inference(example, job):
                nonlocal attempted, observed
                if live and attempted >= max_live_calls:
                    raise ValueError("live_request_limit_reached")
                before = {row["request_id"] for row in job.state().get("attempts", [])}
                attempted += 1
                status, headers, raw = transport.request("POST", example.api, json.dumps(request_body(example)), {"Authorization": "Bearer " + token, "Content-Type": "application/json", **job.headers})
                if status == 200:
                    validate_response(example.api, example.behavior, status, headers, raw)
                    attempts = [row for row in job.state().get("attempts", []) if row["request_id"] not in before]
                    if not any(row.get("response_succeeded") is True for row in attempts):
                        raise ValueError("example_response_not_confirmed_by_gateway")
                    if headers.get("x-hormuz-cache") != "exact-answer-hit":
                        observed += 1
                return status, headers

            for example in examples:
                created = client.create_job("examples/provider-tour", title=example.name, task_type="example-" + example.behavior, context_revision="provider-tour-v1")
                job = client.job(created["work_id"])
                client.set_plan("job", job.work_id, budget_microusd=0 if example.behavior == "budget" else budget_microusd, objective=example.objective)
                status, headers = inference(example, job)
                expected = 402 if example.behavior == "budget" else 200
                if status != expected:
                    raise ValueError("example_unexpected_gateway_status")
                if example.behavior in {"cache", "correction"}:
                    repeated, repeated_headers = inference(example, job)
                    if repeated != 200 or repeated_headers.get("x-hormuz-cache") != "exact-answer-hit":
                        raise ValueError("example_expected_exact_answer_hit")
                    if example.behavior == "correction":
                        job.observe("corrected", source="workflow", reference="synthetic-example/correction")
                        corrected, corrected_headers = inference(example, job)
                        if corrected != 200 or corrected_headers.get("x-hormuz-cache") == "exact-answer-hit":
                            raise ValueError("example_correction_did_not_bypass_cache")
                if example.behavior == "context":
                    other = client.create_job("examples/provider-tour", task_type="example-context", context_revision="provider-tour-v2")
                    other_status, other_headers = inference(example, client.job(other["work_id"]))
                    if other_status != 200 or other_headers.get("x-hormuz-cache") == "exact-answer-hit":
                        raise ValueError("example_changed_context_reused_answer")
                state = job.state()
                checks.append({"example": example.name, "api": example.api, "objective": example.objective, "expected_status": expected, "observed_status": status, "job_state": state["state"], "routed_model": headers.get("x-hormuz-routed-model"), "route_reason": headers.get("x-hormuz-work-route"), "attempts_visible": len(state.get("attempts", [])), "passed": True})
            state = client.state()
            return {"checks": checks, "gateway_requests_attempted": attempted, "successful_uncached_provider_responses_observed": observed, "gateway_totals": state.get("totals", {}), "outcome_observations": "one declared synthetic correction only" if any(e.behavior == "correction" for e in examples) else "none; model responses never declare completion"}


def execute(args, examples):
    providers = {example.provider for example in examples}
    if args.live:
        if not args.rate_card:
            raise ValueError("live_rate_card_required")
        routes = qualified_routes(json.loads(Path(args.rate_card).read_text()), providers)
        environment, upstreams = {}, {}
        for provider in providers:
            name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
            if not os.environ.get(name):
                raise ValueError("live_provider_credential_not_loaded")
            environment[name] = os.environ[name]
            upstreams[provider] = {"base_url": "https://api.openai.com" if provider == "openai" else "https://api.anthropic.com", "api_key_env": name}
        result = run_gateway(examples, routes, upstreams, environment, live=True, budget_microusd=args.budget_microusd, max_live_calls=args.max_live_calls)
        conditions = {"provider_mode": "live_provider_accounts", "provider_fixture_calls": 0, "billing_basis": "operator_configured_rate_estimate"}
    else:
        ProviderFixture.calls = []
        with serving(BoundedFixtureServer(("127.0.0.1", 0), ProviderFixture)) as provider:
            routes = {name: {"protocol": name, "upstream_model": "synthetic-" + name, **{rate: 3 for rate in RATES}} for name in providers}
            upstreams = {name: {"base_url": f"http://127.0.0.1:{provider.server_port}", "api_key_env": "HORMUZ_SYNTHETIC_PROVIDER_KEY"} for name in providers}
            result = run_gateway(examples, routes, upstreams, {"HORMUZ_SYNTHETIC_PROVIDER_KEY": "synthetic-key"}, live=False, budget_microusd=args.budget_microusd, max_live_calls=0)
            conditions = {"provider_mode": "local_synthetic_wire_responses", "provider_fixture_calls": len(ProviderFixture.calls), "real_provider_calls": 0, "billing_basis": "synthetic_configured_rate_estimate"}
    conditions.update(provider_invoice_reconciled=False, customer_savings_validated=False, model_quality_validated=False, tool_actions_executed=0, real_payments=0)
    files = ["tools/ai_work_provider_examples.py", "tools/provider_example_transport.py", "tools/provider_example_responses.py", *json.loads((ROOT / "docs/evidence/ai-work-functional/receipt.json").read_text())["source_files"]]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, timeout=5, check=True).stdout.strip()
    return {"schema_id": "hormuz.provider-examples", "schema_version": 1, "source_commit": commit, "source_boundary": "working_tree_files_identified_by_sha256", "source_files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}, "conditions": conditions, **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List scenarios without starting servers or loading keys.")
    parser.add_argument("--example", action="append", help="Select a scenario by name; repeat for several.")
    parser.add_argument("--live", choices=("openai", "anthropic", "both"), help="Explicitly allow real, potentially billed provider requests.")
    parser.add_argument("--rate-card", help="Private JSON with your selected models and qualified account rates.")
    parser.add_argument("--max-live-calls", type=int, choices=range(1, 101), default=3, metavar="1..100")
    parser.add_argument("--budget-microusd", type=int, default=100_000, help="Workspace estimate allowance; default 0.10 USD, not an invoice cap.")
    parser.add_argument("--receipt", help="Write a metadata receipt; inspect before sharing.")
    args = parser.parse_args(argv)
    if args.list:
        for example in EXAMPLES:
            print(f"{example.name:30} {example.api:22} {'live or local' if example.live else 'local mechanics only'}")
        return 0
    try:
        if not 0 < args.budget_microusd <= 100_000_000:
            raise ValueError("invalid_example_budget")
        examples = selected_examples(args.example, args.live)
        if args.live and len(examples) > args.max_live_calls:
            raise ValueError("selected_examples_exceed_live_request_limit")
        # Reserve the destination before any potentially billed request.
        # Delete only our newly created placeholder if execution fails.
        with receipt_destination(args.receipt) as target:
            receipt = execute(args, examples)
            if target is not None:
                target.write(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt, indent=2))
        return 0
    except Exception as failure:
        # No upstream bodies, environment values, request bodies or tracebacks.
        reason = str(failure) if isinstance(failure, ValueError) and str(failure).isidentifier() else "example_execution_unavailable"
        print(json.dumps({"status": "failed", "reason": reason, "failure_type": type(failure).__name__, "receipt_written": False}), file=sys.stderr)
        return 1


@contextmanager
def receipt_destination(path):
    if path is None:
        yield None
        return
    target = open(path, "x", encoding="utf-8")
    succeeded = False
    try:
        with target:
            yield target
        succeeded = True
    finally:
        if not succeeded:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
