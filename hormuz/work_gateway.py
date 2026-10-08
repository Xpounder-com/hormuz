"""Join authenticated provider attempts to work; HTTP success is not job success."""
from __future__ import annotations

import json
import os
import hashlib
import time
from dataclasses import replace
from uuid import uuid4
from urllib.parse import urlsplit

from .work_runtime import WorkRuntime, WorkRuntimeError

WORK_HEADER = "X-Hormuz-Work-ID"


def initialize(server, environ=None):
    settings = server.config.ai_work
    server.work_runtime = None
    server.work_billing = None
    if settings.enabled:
        server.work_runtime = WorkRuntime(
            settings.database_path or server.config.source_path.parent / "hormuz-work.sqlite3",
            config=server.config, cache_enabled=settings.cache_enabled,
            minimum_samples=settings.minimum_samples,
        )
        if settings.billing_price_id:
            from .work_billing import WorkBilling
            environment = os.environ if environ is None else environ
            server.work_billing = WorkBilling(
                server.work_runtime.path.with_suffix(".billing.sqlite3"), settings.billing_price_id,
                settings.billing_bindings, environment.get(settings.billing_webhook_secret_env, ""),
                api_key=environment.get(settings.billing_api_key_env, ""))
            if settings.require_paid and not server.work_billing.webhook_configured:
                raise WorkRuntimeError("billing_webhook_not_configured", 503)


def prepare(handler, identity, decision, request_value, *, client, protocol, output, account_usage):
    handler._work_id = None
    handler._work_route = None
    handler._work_started_ns = time.monotonic_ns()
    runtime = handler.server.work_runtime
    headers = handler.headers.get_all(WORK_HEADER, [])
    if len(headers) > 1 or headers and (not headers[0] or len(headers[0]) > 128):
        raise WorkRuntimeError("invalid_work_header")
    if runtime is None:
        if headers:
            raise WorkRuntimeError("work_not_enabled", 503)
        return decision
    if not account_usage:
        return decision
    billing = getattr(handler.server, "work_billing", None)
    if handler.server.config.ai_work.require_paid:
        if billing is None or not billing.entitled(identity.organization_id):
            raise WorkRuntimeError("paid_workspace_required", 402)
    work = runtime.get_work(identity, headers[0]) if headers else runtime.create_work(
        identity, "unattributed", task_type="unattributed")
    handler._work_id = work["work_id"]
    handler._work_cache_generation = runtime.cache_generation(identity, handler._work_id)
    candidates = {}
    # The same policy snapshot gates every destination before local selection.
    for alias, route in handler.server.config.model_routes.items():
        if route.protocol != protocol:
            continue
        candidate = handler.server.policy_engine.evaluate(
            identity=identity, client=client, protocol=protocol,
            requested_model=alias, requested_output_tokens=output, snapshot=decision.snapshot)
        if candidate.allowed and candidate.resolved_alias == alias:
            candidates[alias] = candidate
    candidates[decision.resolved_alias] = decision
    if len(candidates) > 100:
        raise WorkRuntimeError("too_many_route_candidates", 503)
    selection = runtime.choose_route(identity, handler._work_id,
        [value.route for value in candidates.values()], decision.route, _cache_request(handler, request_value))
    selected = candidates[selection["alias"]]
    handler._work_route = selection
    handler._work_logical_id = uuid4().hex
    return replace(selected, requested_model=decision.requested_model,
        action=selected.action if selected.route == decision.route else "fallback" + ("+capped" if "capped" in selected.action else ""))


def reserve(handler, identity, attempt, decision, request_value, maximum, *, bounded, retry_of=None):
    if getattr(handler, "_work_id", None) is None:
        return
    if not bounded:
        raise WorkRuntimeError("request_cost_unbounded", 422)
    runtime = handler.server.work_runtime
    request_value = _cache_request(handler, request_value)
    options = {"logical_request_id": handler._work_logical_id, "retry_of": retry_of}
    if hasattr(runtime, "pattern_for_request"):
        options.update(request_pattern=runtime.pattern_for_request(request_value), automatic_retry=retry_of is not None)
    if hasattr(runtime, "shape_for_request"):
        options["request_shape"] = runtime.shape_for_request(request_value)
    result = runtime.reserve(identity, handler._work_id, attempt.attempt_id,
        decision.route, decision.route.protocol, maximum,
        reason=handler._work_route["reason"] if retry_of is None else "provider_failover", **options)
    if result.get("idempotent_replay"):
        raise WorkRuntimeError("request_already_dispatched", 409)


def settle(handler, identity, attempt, *, cost=None, status="unknown", started_ns=None):
    if getattr(handler, "_work_id", None) is None or attempt is None:
        return
    latency = (time.monotonic_ns() - started_ns) / 1_000_000 if started_ns is not None else None
    handler.server.work_runtime.settle(identity, attempt.attempt_id,
        cost_microusd=cost, status=status, latency_ms=latency)


def response_headers(handler):
    if getattr(handler, "_work_id", None):
        handler.send_header(WORK_HEADER, handler._work_id)
        handler.send_header("X-Hormuz-Work-Outcome", "unknown")
        handler.send_header("X-Hormuz-Work-Route", handler._work_route["reason"])


def _cache_request(handler, request_value):
    return {**request_value, "_hormuz_endpoint": urlsplit(handler.path).path}


def _cache_policy(handler, decision):
    # Bound reuse to provider origin, credential generation and actual policy
    # content as well as its public version; this digest is never exported.
    upstream = handler.server._egress_configuration
    values = [decision.policy_version, decision.snapshot.content_sha256,
        upstream.upstreams[decision.route.protocol].base_url,
        hashlib.sha256(upstream.credentials[decision.route.protocol].encode()).hexdigest(),
        decision.max_output_tokens]
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


def cache_hit(handler, identity, decision, request_value):
    if not getattr(handler, "_work_id", None):
        return False
    runtime = handler.server.work_runtime
    entry = runtime.cache_get(identity, handler._work_id, _cache_request(handler, request_value),
        decision.route, _cache_policy(handler, decision))
    if entry is None:
        return False
    request_id = uuid4().hex
    try:
        runtime.reserve(identity, handler._work_id, request_id, decision.route, decision.route.protocol, 0,
            reason="exact_answer_cache", logical_request_id=handler._work_logical_id, cache_source="exact_answer_cache",
            expected_cache_generation=entry["cache_generation"],
            request_pattern=runtime.pattern_for_request(_cache_request(handler, request_value)), request_shape=runtime.shape_for_request(_cache_request(handler, request_value)))
    except WorkRuntimeError as error:
        if error.reason == "cache_generation_changed":
            return False
        raise
    runtime.settle(identity, request_id, cost_microusd=0, status="cache_hit",
        latency_ms=(time.monotonic_ns() - handler._work_started_ns) / 1_000_000)
    handler._response_started = True
    handler.send_response(200)
    handler.send_header("Content-Type", entry["headers"].get("content-type", "application/json"))
    handler.send_header("Content-Length", str(len(entry["body"])))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Hormuz-Cache", "exact-answer-hit")
    handler.send_header("X-Hormuz-Provider-Cost-Microusd", "0")
    handler.send_header("X-Hormuz-Usage-Origin", "cached-original-response")
    handler.send_header("X-Hormuz-Routed-Model", decision.route.upstream_model)
    response_headers(handler)
    handler.end_headers()
    handler.wfile.write(entry["body"])
    return True


def cache_response(handler, identity, decision, request_value, body, *, status, content_type):
    if not getattr(handler, "_work_id", None) or status != 200 or "application/json" not in content_type.lower():
        return
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return
    if not isinstance(value, dict):
        return
    endpoint = urlsplit(handler.path).path
    # Complete text only: refusals, tool actions and truncated outputs bypass reuse.
    if endpoint == "/v1/chat/completions":
        choices = value.get("choices")
        safe = isinstance(choices, list) and bool(choices) and all(
            isinstance(item, dict) and item.get("finish_reason") == "stop"
            and isinstance(item.get("message"), dict)
            and isinstance(item["message"].get("content"), str)
            and not item["message"].get("tool_calls") and not item["message"].get("refusal") for item in choices)
    elif endpoint == "/v1/messages":
        content = value.get("content")
        safe = value.get("stop_reason") == "end_turn" and isinstance(content, list) and bool(content) and all(
            isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) for item in content)
    elif endpoint == "/v1/responses":
        output = value.get("output")
        safe = value.get("status") == "completed" and isinstance(output, list) and bool(output) and all(
            isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "assistant"
            and isinstance(item.get("content"), list) and bool(item["content"])
            and all(isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)
                for part in item["content"]) for item in output)
    else:
        safe = False
    if safe:
        handler.server.work_runtime.cache_put(identity, handler._work_id, _cache_request(handler, request_value),
            decision.route, _cache_policy(handler, decision), body, headers={"content-type": content_type},
            expected_generation=handler._work_cache_generation)
