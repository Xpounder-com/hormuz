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
from .work_learning import REQUEST_KINDS
from .policy import PolicyEngine
from .usage import ResponseUsageParser

WORK_HEADER = "X-Hormuz-Work-ID"


def pricing_request_bounded(protocol, request_value):
    """The configured token envelope has no alternate pricing-mode profile.

    Omitted geography remains the operator-qualified account/route envelope;
    never override a workspace's residency or add parameters to older models.
    """
    tier = "default" if protocol == "openai" else "standard_only"
    if protocol not in {"openai", "anthropic"}:
        return False
    if request_value.get("service_tier") not in (None, tier):
        return False
    if protocol == "openai":
        return request_value.get("speed") is None and request_value.get("inference_geo") is None
    return (request_value.get("speed") in (None, "standard")
        and request_value.get("inference_geo") in (None, "global"))


def _prepare_pricing(protocol, request_value):
    if not pricing_request_bounded(protocol, request_value):
        raise WorkRuntimeError("request_cost_unbounded", 422)
    # Auto/omitted service tiers may select account-configured premium service.
    # These are request enums; Anthropic's observed response enum is "standard".
    if request_value.get("service_tier") is None:
        request_value["service_tier"] = "default" if protocol == "openai" else "standard_only"
    for name in ("speed", "inference_geo"):
        if request_value.get(name) is None:
            request_value.pop(name, None)


class WorkUsageParser(ResponseUsageParser):
    """Observe pricing metadata without changing frozen provider finance wire.

    Reuse the existing JSON/SSE decoder and inspect bounded-depth metadata
    containers, never answer/tool content. An unexpected observed mode cannot
    certify a numeric standard-rate work charge or seed an answer cache.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.work_pricing_known = True

    def finish_with_finance(self):
        result = super().finish_with_finance()
        if result.finance.reason_code == "provider_usage_invalid":
            # A rejected earlier JSON/SSE object could contain a conflicting
            # pricing mode. Later complete token counts cannot erase that gap.
            self.work_pricing_known = False
        return result

    def _parse_object(self, value):
        def observe(item, depth=0):
            if not isinstance(item, dict) or depth > 3:
                return
            expected = {"service_tier": "default" if self.protocol == "openai" else "standard",
                        "speed": "standard", "inference_geo": "global"}
            if any(name in item and item[name] != mode for name, mode in expected.items()):
                self.work_pricing_known = False
            for name in ("response", "message", "usage"):
                observe(item.get(name), depth + 1)
        observe(value)
        super()._parse_object(value)


class _RequestTotals:
    """One request's read snapshot; admission still rechecks caps atomically."""
    def __init__(self, store):
        self.store, self.values = store, {}

    def monthly_totals(self, **scope):
        key = tuple(sorted(scope.items()))
        if key not in self.values:
            self.values[key] = self.store.monthly_totals(**scope)
        return self.values[key]


def initialize(server, environ=None):
    settings = server.config.ai_work
    server.work_runtime = None
    server.work_billing = None
    server.work_workflow = None
    server._work_owner_lock = None
    if settings.enabled:
        from .work_recovery import owner_lock
        server._work_owner_lock = owner_lock(settings.database_path or server.config.source_path.parent / "hormuz-work.sqlite3")
        server._work_owner_lock.__enter__()
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
        from .work_workflow import attach
        attach(server)


def close(server):
    lock = getattr(server, "_work_owner_lock", None)
    if lock is not None:
        server._work_owner_lock = None
        lock.__exit__(None, None, None)


def prepare(handler, identity, decision, request_value, *, client, protocol, output, output_field=None, account_usage):
    handler._work_id = None
    handler._work_route = None
    if not hasattr(handler, "_work_started_ns"):
        handler._work_started_ns = time.monotonic_ns()
    routing_started_ns = time.monotonic_ns()
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
    _prepare_pricing(protocol, request_value)
    billing = getattr(handler.server, "work_billing", None)
    if handler.server.config.ai_work.require_paid:
        if billing is None or not billing.entitled(identity.organization_id):
            raise WorkRuntimeError("paid_workspace_required", 402)
    work = runtime.get_work(identity, headers[0]) if headers else runtime.create_work(
        identity, "unattributed", task_type="unattributed")
    handler._work_id = work["work_id"]
    handler._work_request_context = runtime.bind_request_context(identity, handler._work_id,
        _cache_request(handler, request_value), completion_condition=work["completion_condition"])["context_signature"]
    kinds = handler.headers.get_all("X-Hormuz-Request-Kind", [])
    if len(kinds) > 1 or kinds and kinds[0] not in REQUEST_KINDS:
        raise WorkRuntimeError("invalid_request_kind")
    handler._work_request_kind = kinds[0] if kinds else "interactive"
    guard = runtime.recurrence_guard(identity, handler._work_id, _cache_request(handler, request_value), request_kind=handler._work_request_kind)
    handler._work_cache_guard = guard
    handler._work_cache_generation = guard.get("cache_generation", runtime.cache_generation(identity, handler._work_id))
    candidates = {}
    engine = PolicyEngine(handler.server.config, _RequestTotals(handler.server.store),
        policy_runtime=handler.server.policy_engine.policy_runtime)
    # The same policy snapshot gates every destination before local selection.
    for alias, route in handler.server.config.model_routes.items():
        if route.protocol != protocol:
            continue
        candidate = engine.evaluate(
            identity=identity, client=client, protocol=protocol,
            requested_model=alias, requested_output_tokens=output, snapshot=decision.snapshot)
        if candidate.allowed and candidate.resolved_alias == alias:
            candidates[alias] = candidate
    candidates[decision.resolved_alias] = decision
    if len(candidates) > 100:
        raise WorkRuntimeError("too_many_route_candidates", 503)
    quotes = {}
    if output_field:
        from .server import _provider_input_tokens_bounded
        if _provider_input_tokens_bounded(protocol, request_value, text_only=True):
            # Serialize the potentially large sanitized request once, then
            # account exactly for JSON model/limit substitutions per alias.
            quote_body = dict(request_value)
            quote_body[output_field] = 0
            base_bytes = len(handler._provider_body(quote_body, decision.route))
            baseline_model_bytes = len(json.dumps(decision.route.upstream_model).encode())
            for alias, candidate in candidates.items():
                limit = request_value.get(output_field)
                if candidate.max_output_tokens is not None:
                    limit = min(limit, candidate.max_output_tokens) if type(limit) is int else candidate.max_output_tokens
                if type(limit) is int and limit > 0:
                    input_bytes = base_bytes - baseline_model_bytes + len(json.dumps(candidate.route.upstream_model).encode()) + len(str(limit)) - 1
                    quotes[alias] = candidate.route.estimate_reservation_cost_microusd(
                        input_tokens=input_bytes, output_tokens=limit)
    selection = runtime.choose_route(identity, handler._work_id,
        [value.route for value in candidates.values()], decision.route, _cache_request(handler, request_value),
        max_costs=quotes, request_kind=handler._work_request_kind)
    selected = candidates[selection["alias"]]
    handler._work_route = selection
    handler._work_logical_id = uuid4().hex
    handler._work_routing_ms = (time.monotonic_ns() - routing_started_ns) / 1_000_000
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
    options.update(request_context=handler._work_request_context,
        request_kind="automatic_retry" if retry_of else handler._work_request_kind,
        cache_bypass_reason=handler._work_cache_guard.get("reason") if handler._work_cache_guard.get("bypass") else None)
    if hasattr(runtime, "pattern_for_request"):
        options.update(request_pattern=runtime.pattern_for_request(request_value), automatic_retry=retry_of is not None)
    if hasattr(runtime, "shape_for_request"):
        options["request_shape"] = runtime.shape_for_request(request_value)
    result = runtime.reserve(identity, handler._work_id, attempt.attempt_id,
        decision.route, decision.route.protocol, maximum,
        reason=handler._work_route["reason"] if retry_of is None else "provider_failover", **options)
    if result.get("idempotent_replay"):
        raise WorkRuntimeError("request_already_dispatched", 409)
    handler._work_attempt_started_ns = time.monotonic_ns() if retry_of else handler._work_started_ns
    handler._work_is_retry = retry_of is not None


def settle(handler, identity, attempt, *, cost=None, status="unknown", started_ns=None):
    if getattr(handler, "_work_id", None) is None or attempt is None:
        return
    latency = (time.monotonic_ns() - started_ns) / 1_000_000 if started_ns is not None else None
    measured_provider = getattr(handler, "_work_provider_ms", None)
    if measured_provider is not None:
        latency = measured_provider
    handler.server.work_runtime.settle(identity, attempt.attempt_id,
        cost_microusd=cost, status=status, latency_ms=latency)
    if latency is not None:
        _record_timing(handler, identity, attempt.attempt_id, latency)


def _record_timing(handler, identity, request_id, provider_ms):
    total = (time.monotonic_ns() - getattr(handler, "_work_attempt_started_ns", handler._work_started_ns)) / 1_000_000
    total = max(total, provider_ms)
    stages = {}
    if not getattr(handler, "_work_is_retry", False):
        for attribute, label in (("_work_routing_ms", "routing"), ("_work_redaction_ms", "redaction")):
            value = getattr(handler, attribute, 0)
            if value > 0:
                stages[label] = value
    overhead = total - provider_ms
    if sum(stages.values()) > overhead:
        stages = {}  # Never invent a decomposition of a partial measurement.
    handler.server.work_runtime.record_timing(identity, request_id, provider_ms=provider_ms,
        total_ms=total, gateway_overhead_ms=overhead, stages=stages)


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
    if handler._work_cache_guard.get("bypass"):
        return False
    entry = runtime.cache_get(identity, handler._work_id, _cache_request(handler, request_value),
        decision.route, _cache_policy(handler, decision))
    if entry is None:
        return False
    request_id = uuid4().hex
    try:
        runtime.reserve(identity, handler._work_id, request_id, decision.route, decision.route.protocol, 0,
            reason="exact_answer_cache", logical_request_id=handler._work_logical_id, cache_source="exact_answer_cache",
            expected_cache_generation=entry["cache_generation"],
            request_context=handler._work_request_context, request_kind=handler._work_request_kind,
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
    _record_timing(handler, identity, request_id, 0)
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
