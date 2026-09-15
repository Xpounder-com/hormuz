"""Closed same-origin browser/API transport for the isolated policy console."""
from __future__ import annotations

import re
from importlib.resources import files

from . import policy_impact_pages as pages
from .console_store import ConsoleError
from .policy_repository import PolicyControlError
from .session_http import _form


def dispatch(handler, request):
    from .console_http import _cookie, _html, _json_response, _send, _values
    service = getattr(handler.server, "policy_console", None)
    if service is None:
        raise ConsoleError("admin_not_found")
    origin = handler.server.config.session_broker.public_base_url
    if handler.headers.get("Origin") is not None and handler.headers.get_all("Origin", []) != [origin]:
        raise ConsoleError("admin_origin_rejected")
    if handler.command == "GET":
        if handler.headers.get("Transfer-Encoding") is not None or handler.headers.get_all("Content-Length", []) not in ([], ["0"]):
            raise ConsoleError("admin_invalid_request")
        if request.path == "/console/policy.css" and not request.query:
            _send(handler, 200, files("hormuz").joinpath("policy.css").read_bytes(), content_type="text/css; charset=utf-8")
            return
        credential = _cookie(handler, "session")
        if request.path == "/console/policy/activity" and not request.query:
            history, proposals = service.activity(credential)
            _html(handler, pages.activity(history, proposals))
            return
        if request.path in {"/console/policy", "/v1/admin/policy/scopes"} and not request.query:
            scopes = service.scopes(credential)
            if request.path.startswith("/console/"):
                _html(handler, pages.setup(scopes, service.sessions.csrf_token(credential)))
            else:
                _json_response(handler, scopes)
            return
        if request.path in {"/console/policy/results", "/v1/admin/policy/results"}:
            query = _form(request.query, allowed={"preview_id"})
            if set(query) != {"preview_id"}:
                raise ConsoleError("admin_invalid_request")
            result = service.results(credential, query["preview_id"])
            if request.path.startswith("/console/"):
                _html(handler, pages.results(result, service.sessions.csrf_token(credential)))
            else:
                _json_response(handler, result)
            return
        raise ConsoleError("admin_not_found")
    if handler.command != "POST" or request.query:
        raise ConsoleError("admin_not_found")
    if handler.headers.get_all("Origin", []) != [origin]:
        raise ConsoleError("admin_origin_rejected")
    actions = {"/v1/admin/policy/" + name: name for name in ("preview", "review", "apply", "rollback-review", "rollback")}
    action = actions.get(request.path)
    if action is None:
        raise ConsoleError("admin_not_found")
    credential = _cookie(handler, "session")
    # Authenticate and authorize before reading submitted scope or doing work.
    service.baseline(credential)
    fields = {"csrf_token"} | ({"team_id", "model_alias", "proposed_limit"} if action == "preview" else {"preview_id"})
    if action in {"apply", "rollback"}:
        fields.add("acknowledged")
    values = _values(handler, allowed=fields, required=fields, integer_fields={"proposed_limit"}, boolean_fields={"acknowledged"})
    service.sessions.require_csrf(credential, values["csrf_token"])
    csrf = service.sessions.csrf_token(credential)
    if action == "preview":
        cap = values["proposed_limit"]
        if isinstance(cap, str) and re.fullmatch(r"[1-9][0-9]{0,6}", cap):
            cap = int(cap)
        result = service.preview(credential, team_id=values["team_id"], model_alias=values["model_alias"], cap=cap)
        rendered = lambda: pages.preview(result, csrf)
    elif action == "review":
        result = service.review(credential, values["preview_id"])
        rendered = lambda: pages.preview(result, csrf, reviewing=True)
    elif action == "rollback-review":
        result = service.results(credential, values["preview_id"])
        if not result["rollback_available"]:
            raise PolicyControlError("policy_active_version_mismatch")
        rendered = lambda: pages.results(result, csrf, rollback_review=True)
    else:
        acknowledged = values["acknowledged"] is True or handler.headers.get_content_type() == "application/x-www-form-urlencoded" and values["acknowledged"] == "true"
        result = getattr(service, action)(credential, preview_id=values["preview_id"], acknowledged=acknowledged)
        rendered = lambda: pages.results(result, csrf)
    if handler.headers.get_content_type() == "application/json":
        _json_response(handler, result)
    else:
        _html(handler, rendered())
