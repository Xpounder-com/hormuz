"""Tenant-scoped AI work transport using existing browser and bearer authority."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from importlib.resources import files
from urllib.parse import urlsplit

from . import console_http, work_pages, workspace_http
from .config import Identity
from .console_store import ConsoleError
from .session import SessionBrokerError
from .session_http import _form, _read_body
from .session_store import SessionStoreError
from .work_runtime import WorkRuntimeError
from .workspace_store import WorkspaceError


@dataclass(frozen=True)
class WorkPrincipal:
    identity: Identity
    can_manage_plans: bool
    csrf: str = ""
    credential: str = ""
    browser: str = ""
    origin: str = ""
    can_mutate_work: bool = True


def is_work_path(path):
    return path in {"/work", "/work/", "/work.css"} or path.startswith(("/work/jobs/", "/v1/work/"))


def handle_work_request(handler):
    """Return whether this adapter owns the path; close malformed request bodies."""
    if not is_work_path(urlsplit(handler.path).path):
        return False
    handler.close_connection = True
    try:
        _dispatch(handler)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except WorkRuntimeError as error:
        _failure(handler, error.status, error.reason)
    except (SessionStoreError, SessionBrokerError) as error:
        reason = error.code
        status = 503 if reason.startswith("session_store_") else 401 if reason in {"workspace_session_required", "admin_session_required"} else 403 if "csrf" in reason or "origin" in reason or "access" in reason else 400
        _failure(handler, status, reason)
    except sqlite3.Error:
        _failure(handler, 503, "work_storage_unavailable")
    except (ValueError, TypeError, UnicodeError, OSError, RecursionError):
        _failure(handler, 400, "work_invalid_request")
    return True


def _dispatch(handler):
    runtime = getattr(handler.server, "work_runtime", None)
    if runtime is None:
        raise WorkRuntimeError("work_not_enabled", 404)
    request = urlsplit(handler.path)
    if request.scheme or request.netloc or request.fragment or request.query or "\\" in handler.path or handler.path.startswith("//"):
        raise WorkRuntimeError("work_invalid_request")
    if any(len(handler.headers.get_all(name, [])) > 1 for name in ("Host", "Authorization", "Cookie", "Origin", "Content-Length", "Content-Type")) or handler.headers.get_all("Transfer-Encoding", []):
        raise WorkRuntimeError("work_invalid_request")
    if any(name.lower() in {"proxy-authorization", "x-api-key", "forwarded", "x-forwarded-host"} or name.lower().startswith(("x-hormuz-organization", "x-hormuz-team", "x-hormuz-actor", "x-hormuz-role")) for name in handler.headers):
        raise WorkRuntimeError("work_invalid_request")
    if handler.command == "GET" and handler.headers.get_all("Content-Length", []) not in ([], ["0"]):
        raise WorkRuntimeError("work_invalid_request")
    path = request.path
    _host(handler)
    if path == "/work.css" and handler.command == "GET":
        _send(handler, 200, files("hormuz").joinpath("console.css").read_text() + "\n" + files("hormuz").joinpath("work.css").read_text(), "text/css; charset=utf-8")
        return
    principal = _authenticate(handler)
    if principal is None:
        if not handler.headers.get("Authorization") and path in {"/work", "/work/"} and handler.command == "GET":
            _send(handler, 200, work_pages.login(**_navigation(handler)))
        return
    if handler.command == "GET":
        if path in {"/work", "/work/"}:
            _dashboard(handler, principal)
        elif re.fullmatch(r"/work/jobs/[A-Za-z0-9][A-Za-z0-9._-]{0,127}", path):
            work = runtime.get_work(principal.identity, path.rsplit("/", 1)[1])
            _send(handler, 200, work_pages.job_detail(work, csrf=principal.csrf, can_manage=principal.can_mutate_work, **_navigation(handler)))
        elif path in {"/v1/work/state", "/v1/work/connect", "/v1/work/jobs"}:
            state = _state(handler, principal)
            if path.endswith("/connect"):
                state = {"schema_id": "hormuz.ai-work-connect", "schema_version": 1, "connection": state["connection"], "billing": state["billing"]}
            _json(handler, 200, state)
        elif re.fullmatch(r"/v1/work/jobs/[^/]{1,128}", path):
            _json(handler, 200, runtime.get_work(principal.identity, path.rsplit("/", 1)[1]))
        else:
            raise WorkRuntimeError("work_not_found", 404)
        return
    if handler.command != "POST":
        raise WorkRuntimeError("work_not_found", 404)
    action = re.fullmatch(r"/v1/work/jobs/([^/]{1,128})/(actions|observations)", path)
    if path == "/v1/work/jobs":
        allowed, required = {"repository", "title", "task_type", "context_revision"}, {"repository"}
    elif path == "/v1/work/policies":
        allowed, required = {"scope_type", "scope_id", "budget_microusd", "budget_usd", "objective", "expected_version"}, {"scope_type", "scope_id", "objective"}
    elif action:
        allowed = {"action", "budget_microusd", "budget_usd", "expected_version"} if action[2] == "actions" else {"status", "source", "reference"}
        required = {"action"} if action[2] == "actions" else {"status", "source"}
    elif path == "/v1/work/billing/portal":
        allowed, required = set(), set()
    else:
        raise WorkRuntimeError("work_not_found", 404)
    values = _values(handler, allowed | {"csrf_token"}, required)
    _verify_mutation(handler, principal, values.pop("csrf_token", ""))
    if path == "/v1/work/billing/portal":
        if not principal.can_manage_plans:
            raise WorkRuntimeError("work_administrator_required", 403)
        billing = getattr(handler.server, "work_billing", None)
        if billing is None:
            raise WorkRuntimeError("billing_not_configured", 409)
        origin = principal.origin or handler.server.config.session_broker.public_base_url
        target = billing.portal(principal.identity.organization_id, origin + "/work")
        if isinstance(target, dict):
            target = target["url"]
        if not isinstance(target, str) or urlsplit(target).scheme != "https" or urlsplit(target).hostname != "billing.stripe.com":
            raise WorkRuntimeError("billing_portal_unavailable", 503)
        if handler.headers.get_content_type() == "application/x-www-form-urlencoded":
            _send(handler, 303, "", location=target)
        else:
            _json(handler, 200, {"url": target})
        return
    if path == "/v1/work/jobs":
        for name in ("title", "context_revision"):
            if values.get(name) == "":
                values[name] = None
        result = runtime.create_work(principal.identity, **values)
        status, message = 201, "Job created. Attach its work ID to your supported agent requests."
    elif path == "/v1/work/policies":
        scope_type, scope_id = values.pop("scope_type"), values.pop("scope_id")
        if scope_type == "job":
            runtime.get_work(principal.identity, scope_id)
        elif not principal.can_manage_plans:
            raise WorkRuntimeError("work_administrator_required", 403)
        result = runtime.set_plan(principal.identity.organization_id, scope_type, scope_id, **_budget(values))
        status, message = 200, "Budget and priority saved. Parent budgets continue to apply."
    elif action[2] == "observations":
        # A bearer holder may report workflow evidence for owned jobs, never
        # claim an authenticated connector/webhook source through this endpoint.
        if values["source"] not in {"workflow", "agent", "operator"}:
            raise WorkRuntimeError("work_invalid_observation_source")
        if values.get("reference") == "":
            values["reference"] = None
        result = runtime.observe(principal.identity, action[1], **values)
        status, message = 200, "Observation recorded with its submitted source. It is not an independently verified quality score."
    else:
        result = runtime.act(principal.identity, action[1], **_budget(values))
        status, message = 200, "Job updated. Existing parent budgets and provider policies still apply."
    if handler.headers.get_content_type() == "application/x-www-form-urlencoded":
        _dashboard(handler, principal, message=message)
    else:
        _json(handler, status, result)


def _host(handler):
    settings = handler.server.config.session_broker
    origin = settings.public_base_url
    if getattr(handler.server, "workspace", None) is not None:
        resolved, domain = workspace_http.request_origin(handler)
        handler._workspace_origin = resolved
        handler._work_domain = domain
        return resolved
    host, port = handler.server.server_address[:2]
    host = f"[{host}]" if ":" in host else host
    configured = urlsplit(origin).netloc
    acceptable = {configured} if settings.enabled else {configured, f"{host}:{port}"}
    if len(handler.headers.get_all("Host", [])) != 1 or handler.headers["Host"] not in acceptable:
        raise WorkRuntimeError("work_host_rejected", 403)
    return origin


def _authenticate(handler):
    authorization = handler.headers.get("Authorization")
    if authorization:
        if handler.headers.get("Cookie") or handler.headers.get("Origin") or not authorization.startswith("Bearer "):
            raise WorkRuntimeError("work_invalid_authentication", 403)
        identity = handler._authenticate()
        if identity is None:
            return None  # Existing authentication already wrote the response.
        settings = getattr(handler.server.config, "ai_work", None)
        administrators = getattr(settings, "administrator_actor_ids", ())
        checker = getattr(handler.server, "work_is_admin", None)
        return WorkPrincipal(identity, bool(checker(identity)) if checker else identity.actor_id in administrators)
    workspace = getattr(handler.server, "workspace", None)
    origin = getattr(handler, "_workspace_origin", handler.server.config.session_broker.public_base_url)
    if handler.headers.get("Origin") is not None and handler.headers.get_all("Origin", []) != [origin]:
        raise WorkRuntimeError("work_origin_rejected", 403)
    if workspace:
        credential = workspace_http._cookie(handler, "session")
        if credential:
            current = workspace.sessions.authenticate(credential, origin)
            domain = getattr(handler, "_work_domain", None)
            if domain and current["workspace_id"] != domain["workspace_id"]:
                raise WorkRuntimeError("work_access_denied", 403)
            identity = _browser_identity(handler, current["organization_id"], current["membership_id"], current["workspace_name"])
            return WorkPrincipal(identity, current["role"] == "member_admin", workspace.sessions.csrf(credential, origin), credential, "workspace", origin, current["role"] == "member_admin")
    console = getattr(handler.server, "console", None)
    if console and origin == handler.server.config.session_broker.public_base_url:
        credential = console_http._cookie(handler, "session")
        if credential:
            current = console.sessions.authenticate(credential)
            identity = _browser_identity(handler, current.organization_id, current.membership_id, current.name)
            return WorkPrincipal(identity, current.role == "member_admin", console.sessions.csrf_token(credential), credential, "console", origin, current.role == "member_admin")
    if handler.path not in {"/work", "/work/"} or handler.command != "GET":
        _failure(handler, 401, "work_session_required")
    return None


def _browser_identity(handler, organization_id, membership_id, name):
    # Membership identity is identical to native broker identity. Authenticate
    # session and re-read membership without exposing claims or credential data.
    broker = handler.server.session_broker
    with broker.store._connection() as connection:
        member = broker.directory._member(connection, organization_id, membership_id)
        return Identity(token_env="", token="", actor_id=membership_id, actor_name=name, team_id=member["team_id"], team_name="", organization_id=organization_id, allowed_clients=tuple(json.loads(member["allowed_clients"])), clearance=member["clearance"], authentication_source="browser_session")


def _verify_mutation(handler, principal, token):
    if not principal.can_mutate_work:
        raise WorkRuntimeError("work_read_only", 403)
    if not principal.browser:
        if token or handler.headers.get_content_type() != "application/json":
            raise WorkRuntimeError("work_invalid_request")
        return
    if handler.headers.get_all("Origin", []) != [principal.origin]:
        raise WorkRuntimeError("work_origin_rejected", 403)
    if principal.browser == "workspace":
        handler.server.workspace.sessions.require_csrf(principal.credential, principal.origin, token)
    else:
        handler.server.console.sessions.require_csrf(principal.credential, token)


def _values(handler, allowed, required):
    if len(handler.headers.get_all("Content-Type", [])) != 1:
        raise WorkRuntimeError("work_invalid_request")
    content_type = handler.headers.get_content_type()
    raw = _read_body(handler, content_type)
    if content_type == "application/x-www-form-urlencoded":
        value = _form(raw.decode("utf-8"), allowed=allowed)
    elif content_type == "application/json":
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise WorkRuntimeError("work_invalid_request")
                result[key] = item
            return result
        value = json.loads(raw, object_pairs_hook=unique)
    else:
        raise WorkRuntimeError("work_invalid_request")
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise WorkRuntimeError("work_invalid_request")
    for key, item in value.items():
        if key in {"budget_microusd", "expected_version"}:
            if key == "budget_microusd" and item is None:
                continue
            if isinstance(item, str) and re.fullmatch(r"[0-9]{1,18}", item):
                value[key] = int(item)
            elif type(item) is not int or item < 0 or item > 10**18:
                raise WorkRuntimeError("work_invalid_request")
        elif not isinstance(item, str) or len(item) > 4096 or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item):
            raise WorkRuntimeError("work_invalid_request")
    return value


def _budget(values):
    values = dict(values)
    if "budget_usd" in values:
        if "budget_microusd" in values:
            raise WorkRuntimeError("work_invalid_request")
        amount = values.pop("budget_usd")
        if amount == "":
            values["budget_microusd"] = None
        elif not re.fullmatch(r"[0-9]{1,9}(\.[0-9]{1,6})?", amount):
            raise WorkRuntimeError("work_invalid_budget")
        else:
            try:
                values["budget_microusd"] = int(Decimal(amount) * 1_000_000)
            except (InvalidOperation, ValueError):
                raise WorkRuntimeError("work_invalid_budget") from None
    return values


def _state(handler, principal):
    state = handler.server.work_runtime.report(principal.identity, administrator=principal.can_manage_plans)
    state["principal"] = {"organization_id": principal.identity.organization_id, "actor_id": principal.identity.actor_id, "can_manage_plans": principal.can_manage_plans, "can_mutate_work": principal.can_mutate_work, "csrf_token": principal.csrf}
    policy = handler.server.config.resolved_policy(principal.identity)
    routes = []
    for alias in policy.allowed_models or ():
        route = handler.server.config.model_routes.get(alias)
        if route:
            routes.append({"model": alias, "protocol": route.protocol, "credential_configured": bool(handler.server.upstream_credentials.get(route.protocol)), "eligible": bool(handler.server.upstream_credentials.get(route.protocol))})
    state["connection"] = {"endpoint": getattr(handler, "_workspace_origin", handler.server.config.session_broker.public_base_url), "routes": routes, "supported_protocols": ["openai-responses", "openai-chat-completions", "anthropic-messages"], "credential_location": "gateway_server", "application_access_enabled": bool(principal.identity.allowed_clients), "coverage": "only_authenticated_requests_attached_to_a_work_id", "billing_basis": "configured_provider_rates_estimate"}
    state["billing"] = {"status": "self_hosted", "provider_fees": "separate", "paid_activation": "not_configured"}
    billing = getattr(handler.server, "work_billing", None)
    if billing is not None:
        state["billing"] = billing.status(principal.identity.organization_id)
    return state


def _navigation(handler):
    return {"workspace_enabled": getattr(handler.server, "workspace", None) is not None,
            "console_enabled": getattr(handler.server, "console", None) is not None}


def _dashboard(handler, principal, message=""):
    _send(handler, 200, work_pages.dashboard(_state(handler, principal), message=message, **_navigation(handler)))


def _failure(handler, status, reason):
    if handler.path.startswith("/v1/work/") and handler.headers.get_content_type() != "application/x-www-form-urlencoded":
        _json(handler, status, {"error": {"code": reason, "message": work_pages.error_message(reason)}})
    else:
        _send(handler, status, work_pages.failure(work_pages.error_message(reason), **_navigation(handler)))


def _json(handler, status, value):
    _send(handler, status, json.dumps(value, separators=(",", ":")), "application/json; charset=utf-8")


def _send(handler, status, body, content_type="text/html; charset=utf-8", *, location=None):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    # Native form POSTs need their actual Origin; no-referrer serializes it as
    # null in browsers. API responses do not need browser form authority.
    referrer = "strict-origin" if content_type.startswith("text/html") else "no-referrer"
    for key, value in {"Content-Type": content_type, "Content-Length": str(len(encoded)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": referrer, "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self' https://billing.stripe.com"}.items():
        handler.send_header(key, value)
    if location:
        handler.send_header("Location", location)
    handler.end_headers()
    handler.wfile.write(encoded)
