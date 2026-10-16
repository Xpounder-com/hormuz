"""Native Codex inference without crossing into API-key or local routes."""
import http.client
import json
import re
import subprocess
from contextlib import closing
from urllib.parse import urlsplit

from .auth import AuthenticationError
from .native_codex_auth import single_header, retry_after_headers, native_credentials, gateway_identity
from ._contract_schemas.constants import ERROR_SCHEMA_ID, NATIVE_STATUS_SCHEMA_ID, NATIVE_RESPONSES_SCHEMA_ID
from ._native_responses_worker import RESPONSES_PATH
from .native_responses_transport import NativeClientCanceled, stream_native_response
from .compaction_contract import CONTEXT_FORMAT_HEADER
from .compaction_enforcement import inspect_request
from .redaction import SecretRedactor
from .native_codex_stream import NativeCodexStream
from .native_codex_state import NativeConversationBusy


REQUEST_FIELDS = {"model", "input", "instructions", "stream", "store", "background", "max_output_tokens",
    "tools", "tool_choice", "parallel_tool_calls", "reasoning", "include", "text", "temperature", "top_p",
    "truncation", "service_tier", "metadata", "client_metadata", "prompt_cache_key", "stream_options"}


def _native_request(handler):
    if any(handler.headers.get_all(name, []) for name in ("X-Hormuz-Work-ID", "X-Hormuz-Work-Scope")):
        handler._send_error("hormuz_policy_denied", "Native plan execution does not support work-accounting bindings.", 403)
        return None
    try:
        if urlsplit(handler.path).query or handler.headers.get_all("Transfer-Encoding", []) or handler.headers.get_all("Upgrade", []):
            raise ValueError()
        length = single_header(handler.headers, "Content-Length", limit=9)
        if re.fullmatch(r"[0-9]{1,9}", length) is None:
            raise ValueError()
        if int(length) > min(handler.server.config.max_request_bytes, 25 * 1024 * 1024):
            handler._send_error("request_too_large", "Native request exceeds its byte limit.", 413)
            return None
        content_type = single_header(handler.headers, "Content-Type", limit=128)
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise ValueError()
        mode = single_header(handler.headers, "X-Hormuz-Routing-Mode", required=False, limit=32)
        if mode not in {None, "local_only"}:
            raise ValueError()
        for name in ("thread-id", "session-id"):
            single_header(handler.headers, name, required=False, limit=256)
        value = handler._read_json_body(strict=True)
        if value is None:
            return None
        if (set(value) - REQUEST_FIELDS or type(value.get("model")) is not str or not 1 <= len(value["model"]) <= 256
                or any(not 33 <= ord(c) <= 126 for c in value["model"])
                or value.get("stream") is not True or value.get("store", False) is not False
                or value.get("background", False) is not False
                or type(value.get("input")) not in {str, list}
                or isinstance(value.get("input"), list) and any(type(item) is not dict for item in value["input"])):
            raise ValueError()
        output = value.get("max_output_tokens", 1)
        if type(output) is not int or not 1 <= output <= 1_000_000:
            raise ValueError()
        for key in ("client_metadata", "metadata", "reasoning", "text", "stream_options"):
            if key in value and type(value[key]) is not dict:
                raise ValueError()
        if "client_metadata" in value and any(type(v) is not str or not 1 <= len(v) <= 2048 for v in value["client_metadata"].values()):
            raise ValueError()
        for key in ("tools", "include"):
            if key in value and (type(value[key]) is not list or len(value[key]) > 256):
                raise ValueError()
        return {**value, "store": False}
    except ValueError:
        handler._send_error("invalid_request", "Native HTTP Responses requires an unambiguous bounded streaming request without storage, background or server-side conversation state.", 400)
        return None


def handle_native_status(handler):
    try:
        identity = gateway_identity(handler)
    except (AuthenticationError, ValueError):
        handler._send_error("unauthorized", "A gateway credential is required.", 401)
        return
    handler._send_contract_json(200, NATIVE_STATUS_SCHEMA_ID, handler.server.native_codex_state.view(identity,
        enabled=handler.server.config.native_codex_responses))


def handle_native_responses(handler):
    server = handler.server
    if not server.config.native_codex_responses:
        handler._send_error("not_found", "Route not found", 404)
        return
    try:
        credentials = native_credentials(handler)
        identity, authorization, account = credentials.identity, credentials.authorization, credentials.account
    except (AuthenticationError, ValueError):
        handler._send_error("unauthorized", "Separate gateway and native ChatGPT credentials are required.", 401)
        return
    value = _native_request(handler)
    if value is None:
        return
    model = value.get("model")
    routes = [route for alias, route in server.config.model_routes.items()
              if model in (alias, route.upstream_model) and route.protocol == "openai"
              and server.config.upstreams[route.destination_id].authentication == "codex_chatgpt"]
    if len(routes) != 1:
        handler._send_error("hormuz_policy_denied", "A unique configured native model is required.", 403)
        return
    route = routes[0]
    decision = server.policy_engine.evaluate(identity=identity, client="codex", protocol="openai",
        requested_model=route.alias, requested_output_tokens=value.get("max_output_tokens"))
    if (not decision.allowed or decision.resolved_alias != route.alias
            or server.config.route_admission.mode == "local_only"
            or handler.headers.get_all("X-Hormuz-Routing-Mode", [])
            or any(getattr(scope, field) is not None
                   for scope in (decision.snapshot.organization_policy, decision.snapshot.team_policy,
                                 decision.snapshot.actor_policy) if scope is not None
                   for field in ("monthly_token_limit", "monthly_budget_usd", "per_actor_monthly_budget_usd"))):
        handler._send_error("hormuz_policy_denied", "Native execution is not permitted by the selected policy or budget controls.", 403)
        return
    value = {**value, "model": route.upstream_model}
    if decision.max_output_tokens is not None:
        value["max_output_tokens"] = min(value.get("max_output_tokens", decision.max_output_tokens), decision.max_output_tokens)
    try:
        context = single_header(handler.headers, CONTEXT_FORMAT_HEADER, required=False, limit=128)
        redactor = SecretRedactor(server.config.secret_controls,
            server.secret_redactor.protected_values + (("native_chatgpt_credential", authorization[7:]),))
        redaction = inspect_request(value, protocol="openai", redactor=redactor,
            mode=decision.snapshot.secret_mode, declared_version=context).redaction
    except ValueError:
        handler._send_error("invalid_request", "The native request context is invalid.", 400)
        return
    if redaction.count and decision.snapshot.secret_mode == "deny":
        handler._send_error("hormuz_secret_detected", "The native request contains protected secret material.", 403)
        return
    value = redaction.value
    destination = server.destinations.resolve(route)
    binding = server.native_codex_state.binding(credentials, destination, value,
        {name: handler.headers.get(name) for name in ("thread-id", "session-id")})
    try:
        permitted = server.native_codex_state.permits(binding, value.get("input"))
    except ValueError:
        permitted = False
    if not permitted:
        handler._send_error("hormuz_policy_denied", "Native continuation requires retained successful affinity for the same identity, account credential, session and model.", 403)
        return
    if not server.native_catalog_slots.acquire(blocking=False):
        handler._send_error("gateway_upstream_error", "Native capacity is unavailable.", 429)
        return
    try:
        server.native_codex_state.begin(identity, binding)
    except NativeConversationBusy:
        server.native_catalog_slots.release()
        handler._send_error("gateway_upstream_error", "The native conversation already has an active request.", 429)
        return
    parser = NativeCodexStream(route.upstream_model)
    outcome = "outcome_unknown"
    try:
        with closing(stream_native_response(authorization=authorization, account=account,
                body=json.dumps(value, separators=(",", ":")).encode(),
                timeout=server.config.upstream_timeout_seconds, connection=handler.connection)) as response:
            metadata = next(response)
            upstream_status = metadata["status"]
            if upstream_status != 200 or not metadata["event_stream"]:
                status = upstream_status if upstream_status in {401, 403, 429} else 502
                if status in {401, 403, 429}:
                    outcome = "failed"
                handler._send_contract_json(status, ERROR_SCHEMA_ID,
                    {"error": {"code": "gateway_upstream_error", "message": "Native response access is unavailable; no alternate billing path was selected."}},
                    extra_headers=retry_after_headers(metadata["retry_after"]) if status == 429 else None)
                return
            handler._response_started = True
            handler.send_response(metadata["status"])
            for key, item in {"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                "Connection": "close", "X-Hormuz-Contract": f"{NATIVE_RESPONSES_SCHEMA_ID};v=1",
                "X-Hormuz-Billing": "chatgpt_plan", "X-Hormuz-Cost-Status": "unknown",
                "X-Hormuz-Redactions": str(redaction.count),
                "X-Hormuz-Policy-Decision": decision.action,
                "X-Hormuz-Requested-Model": model,
                "X-Hormuz-Routed-Model": route.upstream_model,
                **server.destinations.response_headers(route)}.items():
                handler.send_header(key, item)
            handler.end_headers()
            for data in response:
                parser.feed(data)
                handler._write_downstream_chunk(data)
                if parser.terminal_state is not None:
                    break
            parser.finish()
            if not parser.invalid:
                if parser.terminal_state == "completed":
                    outcome = "succeeded"
                    server.native_codex_state.remember(binding, parser.output)
                elif parser.terminal_state in {"failed", "incomplete"}:
                    outcome = "failed"
    except NativeClientCanceled:
        handler.close_connection = True
    except (OSError, ValueError, http.client.HTTPException, subprocess.TimeoutExpired):
        if not handler._response_started:
            handler._send_error("gateway_upstream_error", "Native response is unavailable.", 502)
    finally:
        server.native_codex_state.finish(identity, binding, outcome, parser.finish())
        server.native_catalog_slots.release()
