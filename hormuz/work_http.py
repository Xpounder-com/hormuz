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
    return path in {"/work", "/work/", "/work.css", "/work/acquisition"} or path.startswith(("/work/jobs/", "/v1/work/"))


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
    if request.scheme or request.netloc or request.fragment or (request.query and not (request.path == "/work/acquisition" and handler.command == "GET")) or "\\" in handler.path or handler.path.startswith("//"):
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
    labels = None
    if path == "/work/acquisition" and handler.command == "GET":
        from .work_workflow_http import request_labels
        labels = request_labels(request)
    principal = _authenticate(handler)
    if principal is None:
        if not handler.headers.get("Authorization") and labels is not None:
            from .work_workflow_http import anonymous_handoff
            anonymous_handoff(handler, labels)
            return
        if not handler.headers.get("Authorization") and path in {"/work", "/work/"} and handler.command == "GET":
            _send(handler, 200, work_pages.login(**_navigation(handler)))
        return
    if getattr(handler.server, "work_workflow", None) is not None:
        from .work_workflow_http import dispatch
        if dispatch(handler, principal, request):
            return
    if handler.command == "GET":
        if path in {"/work", "/work/"}:
            from .work_workflow_http import consume_handoff
            if consume_handoff(handler, principal):
                return
            _dashboard(handler, principal)
        elif re.fullmatch(r"/work/jobs/[A-Za-z0-9][A-Za-z0-9._-]{0,127}", path):
            work = runtime.get_work(principal.identity, path.rsplit("/", 1)[1])
            _work_capabilities(handler, principal, work)
            _receipt_opened(handler, principal, work)
            _send(handler, 200, work_pages.job_detail(work, csrf=principal.csrf, can_manage=principal.can_mutate_work, **_navigation(handler)))
        elif path in {"/v1/work/state", "/v1/work/connect", "/v1/work/jobs", "/v1/work/activation", "/v1/work/support/receipt"}:
            state = _state(handler, principal)
            if path.endswith("/connect"):
                workflow = getattr(handler.server, "work_workflow", None)
                if workflow is not None:
                    for route in state["connection"]["routes"]:
                        client = "codex" if route["protocol"] == "openai" else "claude-code"
                        if route["eligible"] and client in principal.identity.allowed_clients:
                            workflow.event(principal.identity, "qualified_connection", route["model"] + ":" + client)
                state = {"schema_id": "hormuz.ai-work-connect", "schema_version": 1, "connection": state["connection"], "billing": state["billing"]}
            if path == "/v1/work/activation":
                state = state["onboarding"]
            elif path == "/v1/work/support/receipt":
                # No title, repository name, response, checkout URL, credential,
                # invoice/customer identifier or arbitrary reference is exported.
                state = {"schema_id": "hormuz.ai-work-support-receipt", "schema_version": 1,
                    "gateway_version": __import__('hormuz').__version__,
                    "scope": "authenticated_owner", "billing_status": state["billing"]["status"],
                    "activation_state": state["onboarding"]["state"],
                    "jobs": [{"work_id": job["work_id"], "state": job["state"], "costs": job["costs"],
                        "outcome_evidence": job["outcome_evidence"]} for job in state.get("works", []) if job.get("actor_id") == principal.identity.actor_id]}
            _json(handler, 200, state)
        elif re.fullmatch(r"/v1/work/jobs/[^/]{1,128}", path):
            work = runtime.get_work(principal.identity, path.rsplit("/", 1)[1])
            _work_capabilities(handler, principal, work)
            _receipt_opened(handler, principal, work)
            _json(handler, 200, work)
        else:
            raise WorkRuntimeError("work_not_found", 404)
        return
    if handler.command != "POST":
        raise WorkRuntimeError("work_not_found", 404)
    action = re.fullmatch(r"/v1/work/jobs/([^/]{1,128})/(actions|observations)", path)
    if path == "/v1/work/jobs":
        allowed, required = {"repository", "title", "task_type", "context_revision", "completion_condition"}, {"repository"}
    elif path == "/v1/work/policies":
        allowed, required = {"scope_type", "scope_id", "budget_microusd", "budget_usd", "objective", "expected_version", "exploration_enabled"}, {"scope_type", "scope_id", "objective"}
    elif action:
        allowed = {"action", "budget_microusd", "budget_usd", "expected_version"} if action[2] == "actions" else {"status", "source", "reference"}
        required = {"action"} if action[2] == "actions" else {"status", "source"}
    elif path in {"/v1/work/billing/portal", "/v1/work/billing/checkout"}:
        allowed, required = set(), set()
    elif path == "/v1/work/activation/request":
        allowed, required = {"model", "client"}, {"model", "client"}
    elif path == "/v1/work/activation/review":
        allowed, required = {"action", "reference"}, {"action", "reference"}
    elif path == "/v1/work/activation/reset":
        allowed, required = {"reference"}, {"reference"}
    elif path == "/v1/work/activation/reverify":
        allowed, required = set(), set()
    else:
        raise WorkRuntimeError("work_not_found", 404)
    values = _values(handler, allowed | {"csrf_token"}, required)
    _verify_mutation(handler, principal, values.pop("csrf_token", ""))
    if path.startswith("/v1/work/activation/"):
        _activation_action(handler, principal, path, values)
        return
    if path in {"/v1/work/billing/portal", "/v1/work/billing/checkout"}:
        if not principal.can_manage_plans:
            raise WorkRuntimeError("work_administrator_required", 403)
        billing = getattr(handler.server, "work_billing", None)
        if billing is None:
            raise WorkRuntimeError("billing_not_configured", 409)
        origin = principal.origin or handler.server.config.session_broker.public_base_url
        target = billing.activation.checkout(principal.identity.organization_id, origin + "/work") if path.endswith("/checkout") else billing.portal(principal.identity.organization_id, origin + "/work")
        if isinstance(target, dict):
            target = target["url"]
        if not isinstance(target, str) or urlsplit(target).scheme != "https" or urlsplit(target).hostname != ("checkout.stripe.com" if path.endswith("/checkout") else "billing.stripe.com"):
            raise WorkRuntimeError("billing_portal_unavailable", 503)
        if handler.headers.get_content_type() == "application/x-www-form-urlencoded":
            _send(handler, 303, "", location=target)
        else:
            _json(handler, 200, {"url": target})
        return
    if path == "/v1/work/jobs":
        if values.get("completion_condition") == "":
            values.pop("completion_condition")
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
    if urlsplit(handler.path).path not in {"/work", "/work/", "/work/acquisition"} or handler.command != "GET":
        _failure(handler, 401, "work_session_required")
    return None


def _browser_identity(handler, organization_id, membership_id, name):
    # Membership identity is identical to native broker identity. Authenticate
    # session and re-read membership without exposing claims or credential data.
    broker = handler.server.session_broker
    with broker.store._connection() as connection:
        member = broker.directory._member(connection, organization_id, membership_id)
        if member["status"] != "active":
            raise WorkRuntimeError("activation_membership_inactive", 403)
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
        elif key == "exploration_enabled":
            if item is None or item == "inherit":
                value[key] = None
            elif isinstance(item, str) and item in {"true", "false"}:
                value[key] = item == "true"
            elif type(item) is not bool:
                raise WorkRuntimeError("work_invalid_request")
        elif key == "analytics_consent":
            if isinstance(item, str) and item in {"true", "false"}:
                value[key] = item == "true"
            elif type(item) is not bool:
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
    state["connection"]["supported_inputs"] = ["text"]
    state["connection"]["unsupported_budgeted_inputs"] = ["image", "audio", "document", "provider_resolved_input"]
    connectors = _connector_choices(handler, principal)
    state["connection"]["connectors"] = connectors
    state["billing"] = {"status": "self_hosted", "provider_fees": "separate", "paid_activation": "not_configured"}
    billing = getattr(handler.server, "work_billing", None)
    if billing is not None:
        state["billing"] = billing.status(principal.identity.organization_id)
    activation = billing.activation.status(principal.identity.organization_id) if billing else {"state": "not_configured", "version": 0}
    if state["billing"].get("entitled"):
        activation["state"] = "active"
    choices = [{**route, "client": "codex" if route["protocol"] == "openai" else "claude-code"} for route in routes if route["credential_configured"] and (policy.allowed_clients is None or ("codex" if route["protocol"] == "openai" else "claude-code") in policy.allowed_clients)]
    operator = principal.can_manage_plans and principal.identity.actor_id in handler.server.config.ai_work.administrator_actor_ids
    activation.update({"identity_verified": True, "application_access_enabled": bool(principal.identity.allowed_clients),
        "choices": choices, "can_request_qualification": bool(billing and choices and principal.can_manage_plans and state["billing"].get("status") != "configuration_changed" and activation["state"] in {"qualification_required", "requested", "rejected", "recovery_required"}),
        "can_review_qualification": bool(billing and operator and activation["state"] in {"requested", "qualified", "rejected", "recovery_required"}),
        "can_checkout": bool(billing and principal.can_manage_plans and activation["state"] in {"qualified", "checkout_pending"} and billing.binding(principal.identity.organization_id) is None and billing.management_binding(principal.identity.organization_id) is None),
        "can_open_portal": bool(state["billing"].get("portal_available") and principal.can_manage_plans),
        "can_reset": bool(billing and operator), "can_reverify": bool(billing and operator and billing.binding(principal.identity.organization_id) is not None and activation["state"] in {"qualified", "payment_unverified"}), "next_step": "connect_application" if activation["state"] == "active" and not principal.identity.allowed_clients else "create_first_job" if activation["state"] == "active" else "checkout" if activation["state"] in {"qualified", "checkout_pending"} else "await_signed_payment" if activation["state"] == "payment_unverified" else "operator_review" if activation["state"] in {"requested", "recovery_required"} else "request_qualification"})
    if not billing:
        activation["next_step"] = "create_first_job" if principal.identity.allowed_clients and choices else "connect_application" if choices else "configure_provider"
    elif state["billing"].get("status") == "configuration_changed":
        activation["next_step"] = "manage_subscription" if activation["can_open_portal"] else "operator_review"
    elif activation["state"] in {"qualified", "payment_unverified"} and billing.management_binding(principal.identity.organization_id) is not None:
        activation["next_step"] = "reverify_payment" if operator else "await_payment_verification"
    elif activation["state"] == "recovery_required" and not activation.get("model"):
        activation["next_step"] = "request_qualification"
    workflow = getattr(handler.server, "work_workflow", None)
    if workflow is not None:
        state["funnel"] = workflow.funnel(principal.identity)
        if billing is not None and state["funnel"].get("consent"):
            # Billing facts remain private to the verified organization; the
            # browser cannot submit conversion claims or payment identifiers.
            state["funnel"]["events"].update(billing.funnel(principal.identity.organization_id))
    activation["activation"] = {key: activation.get(key) for key in ("state", "model", "client", "reference")}
    activation["routes"] = choices
    activation["client_choices"] = sorted({row["client"] for row in choices})
    state["support"] = {"receipt_available": True, "recovery_state": activation["state"], "can_reset": activation["can_reset"]}
    state["capabilities"] = {"completion_condition": True, "workflow_bindings": bool(connectors)}
    for work in state.get("works", []):
        _work_capabilities(handler, principal, work, connectors=connectors)
    state["onboarding"] = activation
    return state


def _receipt_opened(handler, principal, work):
    workflow = getattr(handler.server, "work_workflow", None)
    if workflow is not None:
        workflow.event(principal.identity, "receipt_opened", work["work_id"])


def _connector_choices(handler, principal):
    channels = handler.server.config.outcome_connectors
    portfolio = handler.server.config.portfolio_control
    if channels is None or portfolio is None:
        return []
    result = []
    for provider in ("github", "linear"):
        active = {(row.organization_id, row.connector_id) for row in getattr(channels, provider)}
        for binding in portfolio.connectors:
            if binding.organization_id == principal.identity.organization_id and binding.provider == provider and (binding.organization_id, binding.connector_id) in active:
                result.append({"provider": provider, "connector_id": binding.connector_id,
                    "container_ids": list(binding.external_object_ids[:100]), "container_ids_truncated": len(binding.external_object_ids) > 100,
                    "qualification": "configured_signed_channel"})
    return result[:100]


def _work_capabilities(handler, principal, work, *, connectors=None):
    from .work_workflow import CONDITIONS
    owned = work.get("actor_id") == principal.identity.actor_id
    choices = _connector_choices(handler, principal) if connectors is None else connectors
    choices = [row for row in choices if row["provider"] == CONDITIONS.get(work.get("completion_condition"))]
    work["receipt_available"] = owned
    work["binding_available"] = bool(owned and choices)
    work["connector_choices"] = choices if owned else []
    workflow = getattr(handler.server, "work_workflow", None)
    work["bindings"] = workflow.bindings(principal.identity, work["work_id"])["bindings"] if owned and workflow is not None else []


def _activation_action(handler, principal, path, values):
    billing = getattr(handler.server, "work_billing", None)
    if billing is None:
        raise WorkRuntimeError("billing_not_configured", 409)
    if not principal.can_manage_plans:
        raise WorkRuntimeError("work_administrator_required", 403)
    organization, actor = principal.identity.organization_id, principal.identity.actor_id
    if path.endswith("/request"):
        choices = _state(handler, principal)["onboarding"]["choices"]
        if not any(row["model"] == values["model"] and row["client"] == values["client"] for row in choices):
            raise WorkRuntimeError("activation_unsupported_application", 403)
        result = billing.activation.request(organization, actor, **values)
    else:
        if actor not in handler.server.config.ai_work.administrator_actor_ids:
            raise WorkRuntimeError("activation_operator_required", 403)
        if path.endswith("/reverify"):
            result = billing.reverify(organization)
        elif path.endswith("/reset"):
            result = billing.activation.reset(organization, actor, values["reference"])
        else:
            current = billing.activation.status(organization)
            owner = None
            if values["action"] == "approve":
                with billing._connect() as connection:
                    owner = connection.execute("SELECT actor_id FROM work_activation WHERE organization_id=?", (organization,)).fetchone()
                applicant = _browser_identity(handler, organization, owner[0], "") if owner and getattr(handler.server, "session_broker", None) else principal.identity
                choices = _state(handler, WorkPrincipal(applicant, False))["onboarding"]["choices"]
                if not any(row["model"] == current.get("model") and row["client"] == current.get("client") for row in choices):
                    raise WorkRuntimeError("activation_unsupported_application", 403)
            # Commit the proof-bound, version-checked approval before expanding
            # membership authority. A raced/rejected review must grant nothing.
            result = billing.activation.review(organization, actor, **values, expected_version=current["version"])
            if values["action"] == "approve" and owner and getattr(handler.server, "session_broker", None):
                from .work_activation import grant_reviewed_application
                grant_reviewed_application(handler.server.session_broker.directory, organization, owner[0], current["client"], actor)
    if handler.headers.get_content_type() == "application/x-www-form-urlencoded":
        _dashboard(handler, principal, message="Qualification updated. Expanded application access requires signing in/enrolling again.")
    else:
        _json(handler, 200, result)


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


def _send(handler, status, body, content_type="text/html; charset=utf-8", *, location=None, set_cookie=None):
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Connection", "close")
    # Native form POSTs need their actual Origin; no-referrer serializes it as
    # null in browsers. API responses do not need browser form authority.
    referrer = "strict-origin" if content_type.startswith("text/html") else "no-referrer"
    for key, value in {"Content-Type": content_type, "Content-Length": str(len(encoded)), "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": referrer, "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self' https://billing.stripe.com https://checkout.stripe.com"}.items():
        handler.send_header(key, value)
    if location:
        handler.send_header("Location", location)
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.end_headers()
    handler.wfile.write(encoded)
