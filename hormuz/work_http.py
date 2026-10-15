"""Authenticated JSON receipts for intercepted coding-client requests."""
from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import urlsplit

from .work_runtime import WorkRuntimeError


def handle_work_request(handler):
    request = urlsplit(handler.path)
    path = request.path
    if path not in {"/v1/work/state", "/v1/work/jobs"} and not re.fullmatch(
        r"/v1/work/jobs/[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:/(?:attempts|observations|actions))?", path
    ):
        return False
    handler.close_connection = True
    try:
        runtime = handler.server.work_runtime
        if runtime is None:
            raise WorkRuntimeError("work_not_enabled", 404)
        host, port = handler.server.server_address[:2]
        expected_host = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        if (request.scheme or request.netloc or request.query or request.fragment
                or handler.headers.get_all("Host", []) != [expected_host]
                or any(len(handler.headers.get_all(name, [])) > 1 for name in
                       ("Authorization", "Content-Length", "Content-Type"))
                or any(handler.headers.get_all(name, []) for name in
                       ("Cookie", "Origin", "Transfer-Encoding", "X-Api-Key", "Proxy-Authorization"))):
            raise WorkRuntimeError("work_invalid_request")
        if not handler.headers.get("Authorization", "").startswith("Bearer "):
            raise WorkRuntimeError("work_session_required", 401)
        identity = handler._authenticate()
        if identity is None:
            return True
        if handler.command == "GET":
            if handler.headers.get_all("Content-Length", []) not in ([], ["0"]):
                raise WorkRuntimeError("work_invalid_request")
            if path == "/v1/work/state":
                value = _state(handler, identity)
            elif path == "/v1/work/jobs":
                value = _state(handler, identity)
            elif path.endswith("/attempts"):
                value = runtime.attempt_history(identity, path.split("/")[4])
            elif path.count("/") == 4:
                value = runtime.get_work(identity, path.split("/")[4])
                _receipt_capabilities(handler, identity, value)
            else:
                raise WorkRuntimeError("work_not_found", 404)
            handler._send_json(200, value)
            return True
        if handler.command != "POST":
            raise WorkRuntimeError("work_not_found", 404)
        if handler.headers.get_content_type() != "application/json":
            raise WorkRuntimeError("work_invalid_request")
        length = handler.headers.get("Content-Length", "")
        if not length.isdigit() or not 0 < int(length) <= 65536:
            raise WorkRuntimeError("work_invalid_request")
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise WorkRuntimeError("work_invalid_request")
                result[key] = item
            return result

        def reject_constant(_value):
            raise WorkRuntimeError("work_invalid_request")

        raw = handler.rfile.read(int(length))
        if len(raw) != int(length):
            raise WorkRuntimeError("work_invalid_request")
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise WorkRuntimeError("work_invalid_request")
        if path == "/v1/work/jobs":
            allowed = {"repository", "title", "task_type", "context_revision", "completion_condition"}
            if set(value) - allowed or "repository" not in value:
                raise WorkRuntimeError("work_invalid_request")
            result = runtime.create_work(identity, **value)
            status = 201
        elif path.endswith("/observations"):
            if set(value) - {"status", "source", "reference"} or not {"status", "source"} <= set(value):
                raise WorkRuntimeError("work_invalid_request")
            if value["source"] not in {"workflow", "agent", "operator"}:
                raise WorkRuntimeError("work_invalid_observation_source")
            result = runtime.observe(identity, path.split("/")[4], **value)
            status = 200
        elif path.endswith("/actions"):
            if set(value) - {"action", "budget_microusd", "expected_version"} or "action" not in value:
                raise WorkRuntimeError("work_invalid_request")
            result = runtime.act(identity, path.split("/")[4], **value)
            status = 200
        else:
            raise WorkRuntimeError("work_not_found", 404)
        handler._send_json(status, result)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except WorkRuntimeError as error:
        handler._send_json(error.status, {"error": {"code": "hormuz_ai_work_" + error.reason}})
    except (sqlite3.Error, OSError):
        handler._send_json(503, {"error": {"code": "hormuz_ai_work_storage_unavailable"}})
    except (ValueError, TypeError, RecursionError):
        handler._send_json(400, {"error": {"code": "hormuz_ai_work_invalid_request"}})
    return True


def _state(handler, identity):
    state = handler.server.work_runtime.report(identity)
    policy = handler.server.config.resolved_policy(identity)
    extended = handler.server.destinations.metadata_enabled
    state["schema_version"] = 3
    routes = []
    for alias, route in handler.server.config.model_routes.items():
        if policy.allowed_models is not None and alias not in policy.allowed_models:
            continue
        destination = handler.server.destinations.describe(route)
        routes.append({"model": alias, **(destination if extended else {
            key: destination[key] for key in ("protocol", "credential_configured", "eligible")})})
    host, port = handler.server.server_address[:2]
    state["connection"] = {"endpoint": f"http://{host}:{port}", "routes": routes,
        "shadow_router_enabled": handler.server.config.shadow_router is not None,
        "credential_location": "router", "coverage": "only_authenticated_intercepted_model_requests"}
    # Existing receipt readers use these choices. Eligibility describes configured
    # destinations; it is not a claim of real model or subscription qualification.
    choices = []
    for route in routes:
        client = "codex" if route["protocol"] == "openai" else "claude-code"
        if (route["eligible"] and (not identity.allowed_clients or client in identity.allowed_clients)
                and (policy.allowed_clients is None or client in policy.allowed_clients)):
            choices.append({**route, "client": client})
    state["onboarding"] = {"choices": choices}
    return state


def _receipt_capabilities(handler, identity, work):
    owned = work.get("actor_id") == identity.actor_id
    policy = handler.server.config.resolved_policy(identity)
    choices = []
    for alias, route in handler.server.config.model_routes.items():
        client = "codex" if route.protocol == "openai" else "claude-code"
        if (identity.allowed_clients and client not in identity.allowed_clients
                or policy.allowed_clients is not None and client not in policy.allowed_clients
                or policy.allowed_models is not None and alias not in policy.allowed_models
                or not handler.server.destinations.describe(route)["eligible"]):
            continue
        choices.append({"client": client, "protocol": route.protocol, "model": alias})
        if len(choices) == 100:
            break
    work.update(receipt_available=owned, binding_available=False, connector_choices=[],
                agent_choices=choices if owned else [], bindings=[])
