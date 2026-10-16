"""Privacy and qualification admission, independent of optional work accounting."""
from dataclasses import dataclass, replace
from contextlib import contextmanager
import json
import threading
import time
import urllib.error
import urllib.request
from types import MappingProxyType

from .work_learning import contains_provider_state, request_characteristics


@dataclass(frozen=True)
class LocalQualification:
    qualification_id: str
    model_revision: str
    max_request_bytes: int
    max_output_tokens: int
    task_types: tuple[str, ...]


@dataclass(frozen=True)
class RouteAdmissionConfig:
    mode: str = "configured"
    local_routes: tuple[tuple[str, LocalQualification], ...] = ()


class RouteAdmissionError(Exception):
    def __init__(self, reason, status=403):
        self.reason, self.status = reason, status
        super().__init__(reason)


def build_route_admission(raw, routes, upstreams):
    from .config import ConfigError
    value = raw.get("route_admission", {})
    if type(value) is not dict or set(value) - {"mode", "local_routes"}:
        raise ConfigError("route_admission is invalid")
    mode = value.get("mode", "configured")
    if type(mode) is not str or mode not in {"configured", "auto", "local_only"}:
        raise ConfigError("route_admission.mode is invalid")
    local = value.get("local_routes", {})
    if type(local) is not dict or len(local) > 8:
        raise ConfigError("route_admission.local_routes is invalid")
    result = []
    fields = {"qualification_id", "model_revision", "max_request_bytes", "max_output_tokens", "task_types"}
    tasks = {"summarization", "translation", "structured_extraction", "code_change", "code_review", "interactive"}
    for alias, record in local.items():
        route = routes.get(alias)
        if route is None or upstreams[route.destination_id].locality != "local":
            raise ConfigError("route_admission must reference local model routes")
        if type(record) is not dict or set(record) != fields:
            raise ConfigError("local qualification fields are invalid")
        for key in ("qualification_id", "model_revision"):
            text = record[key]
            if type(text) is not str or not 1 <= len(text) <= 128 or any(ord(c) < 33 or ord(c) > 126 for c in text):
                raise ConfigError("local qualification identity is invalid")
        if record["model_revision"] != route.model_revision or not route.capabilities:
            raise ConfigError("local qualification must match model revision and declare capabilities")
        if type(record["max_request_bytes"]) is not int or not 1 <= record["max_request_bytes"] <= 25 * 1024 * 1024:
            raise ConfigError("local qualification request bound is invalid")
        if type(record["max_output_tokens"]) is not int or not 1 <= record["max_output_tokens"] <= 1_000_000:
            raise ConfigError("local qualification output bound is invalid")
        kinds = record["task_types"]
        if type(kinds) is not list or not kinds or any(type(k) is not str or k not in tasks for k in kinds) or len(kinds) != len(set(kinds)):
            raise ConfigError("local qualification task types are invalid")
        if "interactive" in kinds and "codex_llama_v1" not in route.capabilities:
            raise ConfigError("interactive qualification requires the Codex local transport")
        result.append((alias, LocalQualification(record["qualification_id"], record["model_revision"],
            record["max_request_bytes"], record["max_output_tokens"], tuple(kinds))))
    return RouteAdmissionConfig(mode, tuple(result))


def _text_envelope(value, protocol, endpoint):
    """Only self-contained text and inline function tools are qualified here."""
    from .codex_llama import CAPABILITY, CodexLlamaRequest, needs_adapter
    if protocol == 'openai' and endpoint == '/v1/responses' and needs_adapter(value):
        try:
            required = _text_envelope(CodexLlamaRequest(value).admission_value(), protocol, endpoint)
        except (ValueError, TypeError, RecursionError):
            return None
        return required | {CAPABILITY} if required is not None else None
    common = {"model", "stream", "temperature", "top_p", "tools", "tool_choice", "metadata"}
    fields = {
        "/v1/responses": {"input", "instructions", "max_output_tokens", "text", "store", "parallel_tool_calls", "service_tier",
                          "client_metadata", "prompt_cache_key", "reasoning", "include"},
        "/v1/responses/compact": {"input", "instructions", "max_output_tokens", "store", "service_tier"},
        "/v1/chat/completions": {"messages", "max_tokens", "max_completion_tokens", "response_format", "stop", "stream_options", "service_tier"},
        "/v1/messages": {"messages", "system", "max_tokens", "top_k", "stop_sequences", "service_tier"},
        "/v1/messages/count_tokens": {"messages", "system", "max_tokens"},
    }
    if set(value) - (common | fields[endpoint]) or value.get("store") not in (None, False):
        return None
    if value.get("service_tier") not in (None, "default" if protocol == "openai" else "standard_only"):
        return None
    required = {"text"}
    native_fields = {"client_metadata", "prompt_cache_key", "reasoning", "include"}
    if native_fields.intersection(value):
        codex_capability = "codex_http"
        # The pinned profiles supply no reasoning state. 0.160 may explicitly
        # disable effort to override a saved setting; every execution control
        # and actual opaque input remains unsupported.
        reasoning = value.get("reasoning", {})
        if endpoint != "/v1/responses" or reasoning not in ({}, {"effort": "none"}):
            return None
        if reasoning:
            codex_capability = "codex_http_0160"
        if "include" in value and value["include"] != ["reasoning.encrypted_content"]:
            return None
        def bounded_tag(item, limit):
            return type(item) is str and 0 < len(item) <= limit and all(32 <= ord(c) <= 126 for c in item)
        if "prompt_cache_key" in value and not bounded_tag(value["prompt_cache_key"], 128):
            return None
        if "client_metadata" in value:
            tags = value["client_metadata"]
            allowed_tags = {"thread_id", "session_id", "turn_id", "x-codex-window-id",
                            "x-codex-installation-id", "x-codex-turn-metadata", "root_turn_id"}
            if (type(tags) is not dict or not tags or set(tags) - allowed_tags
                    or any(not bounded_tag(item, 2048) for item in tags.values())):
                return None
            if "root_turn_id" in tags:
                codex_capability = "codex_http_0160"
        required.add(codex_capability)
    if value.get("stream"):
        required.add("streaming")
    if request_characteristics(value)["structured_output"]:
        required.add("structured_output")
    tools = value.get("tools", [])
    if type(tools) is not list:
        return None
    for tool in tools:
        if type(tool) is not dict:
            return None
        if protocol == "openai":
            if tool.get("type") != "function":
                return None
            definition = tool.get("function", tool)
            if type(definition) is not dict or type(definition.get("name")) is not str or type(definition.get("parameters")) is not dict:
                return None
            if set(tool) - {"type", "function", "name", "description", "parameters", "strict"}:
                return None
        elif set(tool) - {"name", "description", "input_schema"} or type(tool.get("name")) is not str or type(tool.get("input_schema")) is not dict:
            return None
    if tools:
        required.add("tools")
    if value.get("tool_choice") is not None and not tools:
        return None
    pending = [value.get(key) for key in ("input", "messages", "system") if key in value]
    kinds = {"text", "input_text", "output_text", "message", "function_call", "function_call_output", "tool_use", "tool_result"}
    while pending:
        item = pending.pop()
        if type(item) is str:
            continue
        if type(item) is list:
            pending.extend(item)
            continue
        if type(item) is not dict or set(item) - {"role", "content", "type", "text", "name", "call_id", "arguments", "output", "id", "input", "tool_use_id", "is_error", "tool_calls", "tool_call_id", "function"}:
            return None
        kind = item.get("type")
        if kind is not None and (type(kind) is not str or kind not in kinds):
            return None
        if kind in {"function_call", "function_call_output", "tool_use", "tool_result"} or item.get("tool_calls") or item.get("role") == "tool":
            required.add("tools")
        pending.extend(item[key] for key in ("content", "output") if key in item)
    return required


class RouteAdmission:
    def __init__(self, config, destinations):
        self.config, self.destinations = config, destinations
        self.settings = config.route_admission
        self.qualifications = MappingProxyType(dict(self.settings.local_routes))
        self._health_lock = threading.Lock()
        self._health = {}
        self._local_capacity = threading.BoundedSemaphore(1)
        self._capacity_lock = threading.Lock()
        self._active_local = 0

    @staticmethod
    def _within_envelope(handler, route, qualification, value, output_field):
        limit = 0 if handler.path.split("?", 1)[0] == "/v1/messages/count_tokens" else value.get(output_field)
        return (qualification is not None and type(limit) is int and limit <= qualification.max_output_tokens
                and len(handler._provider_body(value, route)) <= qualification.max_request_bytes)

    @contextmanager
    def lease(self, handler, route):
        local = self.destinations.resolve(route).locality == "local"
        if not local or getattr(handler, "_route_admission_mode", "configured") == "configured" or getattr(handler, "_local_route_lease", False):
            yield
            return
        if not self._local_capacity.acquire(blocking=False):
            raise RouteAdmissionError("local_capacity_busy", 429)
        handler._local_route_lease = True
        handler._local_route_deadline = time.monotonic() + self.config.upstream_timeout_seconds
        with self._capacity_lock:
            self._active_local = 1
        try:
            yield
        finally:
            handler._local_route_lease = False
            handler._local_route_deadline = None
            with self._capacity_lock:
                self._local_capacity.release()
                self._active_local = 0

    def remaining_timeout(self, handler):
        deadline = getattr(handler, "_local_route_deadline", None)
        if deadline is None:
            return self.config.upstream_timeout_seconds
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("local_request_deadline")
        return remaining

    def _ready(self, route):
        # llama.cpp /health is prompt-free. One bounded probe per destination
        # every five seconds; serialization also bounds probe concurrency.
        from .server import _open_local_upstream
        destination = self.destinations.resolve(route)
        with self._health_lock:
            previous = self._health.get(destination.generation)
            if previous and time.monotonic() - previous[0] < 5:
                return previous[1]
            ready = False
            # The optional /v1 inference prefix does not scope runtime health.
            base = destination.upstream.base_url.rstrip("/").removesuffix("/v1")
            request = urllib.request.Request(base + "/health")
            try:
                with _open_local_upstream(request, timeout=2, absolute_timeout=2) as response:
                    data = response.read(4097)
                    ready = response.status == 200 and len(data) <= 4096 and json.loads(data) == {"status": "ok"}
            except urllib.error.HTTPError as error:
                error.close()
            except (OSError, ValueError, RecursionError):
                pass
            self._health[destination.generation] = (time.monotonic(), ready)
            return ready

    def select(self, handler, identity, decision, value, *, client, protocol, output_field):
        handler._route_admission_candidates = None
        handler._route_admission_selection_candidates = None
        handler._route_admission_headers = {}
        requested = handler.headers.get_all("X-Hormuz-Routing-Mode", [])
        if len(requested) > 1 or requested and requested[0] != "local_only":
            raise RouteAdmissionError("invalid_routing_mode")
        mode = "local_only" if requested else self.settings.mode
        handler._route_admission_mode = mode
        if mode == "configured":
            return decision
        continuation = contains_provider_state(value)
        work_headers = handler.headers.get_all("X-Hormuz-Work-Id", [])
        if continuation and (handler.server.work_runtime is None
                             or not (len(work_headers) == 1 or getattr(handler, "_work_id", None))
                             or handler.path.split("?", 1)[0] == "/v1/messages/count_tokens"):
            raise RouteAdmissionError("affinity_required", 409)
        from .policy import PolicyEngine
        endpoint = handler.path.split("?", 1)[0]
        capability = {"/v1/responses": "responses", "/v1/responses/compact": "compact",
                      "/v1/chat/completions": "chat_completions", "/v1/messages": "messages",
                      "/v1/messages/count_tokens": "count_tokens"}[endpoint]
        required = _text_envelope(value, protocol, endpoint)
        if "?" in handler.path or handler.headers.get("OpenAI-Beta") or handler.headers.get("Anthropic-Beta"):
            required = None
        if required is not None:
            required.add(capability)
        limit = 0 if endpoint == "/v1/messages/count_tokens" else value.get(output_field)
        hint = request_characteristics(value)["task_hint"]
        engine = PolicyEngine(self.config, handler.server.store,
            policy_runtime=handler.server.policy_engine.policy_runtime)
        candidates = {}
        for alias, qualification in self.qualifications.items():
            route = self.config.model_routes[alias]
            if route.protocol != protocol or required is None or not required.issubset(route.capabilities):
                continue
            destination = self.destinations.resolve(route)
            if destination.upstream.authentication != "none" and not destination.credential:
                continue
            if not self._within_envelope(handler, route, qualification, value, output_field):
                continue
            explicitly_selected = (alias == decision.requested_model and
                                   "interactive" in qualification.task_types)
            if not continuation and hint not in qualification.task_types and not explicitly_selected:
                continue
            candidate = engine.evaluate(identity=identity, client=client, protocol=protocol,
                requested_model=alias, requested_output_tokens=limit, snapshot=decision.snapshot)
            if candidate.allowed and candidate.resolved_alias == alias:
                if self._ready(route):
                    candidates[alias] = candidate
        if continuation and mode == "auto" and self.destinations.resolve(decision.route).locality == "cloud":
            candidates[decision.resolved_alias] = decision
        if candidates:
            selection_candidates = candidates
            if not continuation and decision.requested_model in candidates:
                # Initial ranking respects an explicit qualified local choice;
                # dispatch retains admitted operational capacity alternates.
                selection_candidates = {decision.requested_model: candidates[decision.requested_model]}
            selected = next(iter(selection_candidates.values()))
            reason = "qualified_local"
        elif mode == "local_only" or self.destinations.resolve(decision.route).locality == "local":
            raise RouteAdmissionError("local_route_unavailable", 503)
        else:
            selected, candidates, reason = decision, {decision.resolved_alias: decision}, "permitted_baseline"
            selection_candidates = candidates
        handler._route_admission_candidates = candidates
        handler._route_admission_selection_candidates = selection_candidates
        handler._route_admission_headers = {"X-Hormuz-Routing-Mode": mode,
                                            "X-Hormuz-Routing-Reason": reason}
        if selected.resolved_alias in self.qualifications:
            handler._route_admission_headers["X-Hormuz-Routing-Qualification"] = self.qualifications[selected.resolved_alias].qualification_id
        action = selected.action
        if selected.resolved_alias != decision.resolved_alias:
            action = "fallback" + ("+capped" if "capped" in selected.action or "capped" in decision.action else "")
        return replace(selected, requested_model=decision.requested_model, action=action)

    def receipt(self, handler, route):
        headers = handler._route_admission_headers
        if not headers:
            return
        headers.pop("X-Hormuz-Routing-Qualification", None)
        if route.alias in self.qualifications:
            headers["X-Hormuz-Routing-Qualification"] = self.qualifications[route.alias].qualification_id
        if getattr(handler, "_work_route", None) and handler._work_route["reason"] == "session_affinity":
            headers["X-Hormuz-Routing-Reason"] = "session_affinity"

    def final_check(self, handler, identity, decision, value, *, client, protocol, output_field):
        mode = getattr(handler, "_route_admission_mode", "configured")
        if mode == "configured":
            return
        destination = self.destinations.resolve(decision.route)
        admitted = handler._route_admission_candidates
        if decision.resolved_alias not in admitted or mode == "local_only" and destination.locality != "local":
            raise RouteAdmissionError("destination_not_admitted")
        current = handler.server.policy_engine.evaluate(identity=identity, client=client, protocol=protocol,
            requested_model=decision.resolved_alias, requested_output_tokens=value.get(output_field))
        if not current.allowed or current.resolved_alias != decision.resolved_alias or current.snapshot.content_sha256 != decision.snapshot.content_sha256:
            raise RouteAdmissionError("policy_changed")
        if destination.locality == "local":
            qualification = self.qualifications.get(decision.resolved_alias)
            if not self._within_envelope(handler, decision.route, qualification, value, output_field) or not self._ready(decision.route):
                raise RouteAdmissionError("local_route_unavailable", 503)
        self.receipt(handler, decision.route)

    def contract_header(self, handler, route):
        if getattr(handler, "_route_admission_headers", {}):
            return "hormuz.relay-metadata;v=3"
        return self.destinations.contract_header(route)

    def response_headers(self, handler, route):
        headers = dict(getattr(handler, "_route_admission_headers", {}))
        if not headers:
            return self.destinations.response_headers(route)
        destination = self.destinations.resolve(route)
        headers.update({"X-Hormuz-Destination": destination.name,
                        "X-Hormuz-Destination-Locality": destination.locality,
                        "X-Hormuz-Destination-Generation": destination.generation})
        return headers

    def status(self, handler, identity):
        routes = []
        for alias, qualification in self.qualifications.items():
            route = self.config.model_routes[alias]
            client = "codex" if route.protocol == "openai" else "claude-code"
            decision = handler.server.policy_engine.evaluate(identity=identity, client=client, protocol=route.protocol,
                requested_model=alias, requested_output_tokens=None)
            if not decision.allowed or decision.resolved_alias != alias:
                continue
            destination = self.destinations.resolve(route)
            with self._health_lock:
                health = self._health.get(destination.generation)
            readiness = "unchecked" if health is None else "stale" if time.monotonic() - health[0] >= 5 else "ready" if health[1] else "unavailable"
            routes.append({"model": alias, "destination": destination.name, "generation": destination.generation,
                "model_revision": qualification.model_revision, "qualification_id": qualification.qualification_id,
                "evidence_basis": "operator_declared", "capabilities": list(route.capabilities),
                "max_request_bytes": qualification.max_request_bytes, "max_output_tokens": qualification.max_output_tokens,
                "task_types": list(qualification.task_types), "readiness": readiness})
        with self._capacity_lock:
            capacity = {"limit": 1, "active": self._active_local}
        return {"schema_id": "hormuz.route-admission", "schema_version": 1, "mode": self.settings.mode,
                "task_classifier": "bounded_local_heuristic", "local_capacity": capacity, "routes": routes}
