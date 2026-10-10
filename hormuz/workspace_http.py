"""Strict customer transport: membership, host, Origin and CSRF are independent."""

from __future__ import annotations

import json
import re
from http.cookies import CookieError, SimpleCookie
from importlib.resources import files
from urllib.parse import urlencode, urlsplit

from .console_http import _values
from .session_http import _form
from .session import SessionBrokerError
from .session_store import SessionStoreError
from .workspace_store import WorkspaceError
from . import workspace_pages

ERRORS = {
    "workspace_not_found": (404, "That page is not available."),
    "workspace_session_required": (401, "Sign in to open your workspace."),
    "workspace_access_denied": (403, "This workspace is not available to your account."),
    "workspace_host_rejected": (421, "This domain is not connected. Use your default Hormuz address."),
    "workspace_origin_rejected": (403, "Reload your workspace and try again."),
    "workspace_csrf_rejected": (403, "The form could not be verified. Reload and try again."),
    "workspace_invalid_request": (400, "The request could not be verified."),
    "workspace_login_invalid": (400, "Sign-in could not be verified. Start a new sign-in."),
    "workspace_email_required": (403, "Verify your email with your account provider, then sign in again."),
    "workspace_login_unavailable": (503, "Sign-in is temporarily unavailable. Please try again."),
    "workspace_login_capacity": (503, "Too many sign-ins are pending. Please try again shortly."),
    "workspace_storage_unavailable": (503, "Your workspace is temporarily unavailable. Please try again."),
    "workspace_domains_disabled": (409, "Custom domain connections are not enabled yet."),
    "workspace_domain_invalid": (400, "Enter a subdomain you control, such as ai.yourcompany.com."),
    "workspace_domain_claimed": (409, "This domain is already connected or awaiting verification."),
    "workspace_domain_capacity": (409, "The domain limit has been reached."),
    "workspace_domain_busy": (409, "Verification is in progress. Please try again shortly."),
    "workspace_domain_unavailable": (503, "Domain verification is temporarily unavailable."),
    "workspace_rate_limited": (429, "Too many requests. Please try again shortly."),
}


def is_workspace_path(path):
    return path in {"/workspace", "/workspace/"} or path.startswith(("/workspace/", "/w/", "/v1/workspaces/", "/.well-known/hormuz-domain/"))


def request_origin(handler, *, probe=False):
    service = handler.server.workspace
    hosts = handler.headers.get_all("Host", [])
    canonical = service.sessions.origin
    if len(hosts) != 1:
        raise WorkspaceError("workspace_host_rejected")
    if hosts == [urlsplit(canonical).netloc]:
        return canonical, None
    hostname = hosts[0]
    if not re.fullmatch(r"[a-z0-9.-]{1,253}", hostname):
        raise WorkspaceError("workspace_host_rejected")
    if probe:
        return "https://" + hostname, None
    return "https://" + hostname, service.domains.resolve(hostname)


def handle_workspace_request(handler):
    handler.close_connection = True
    try:
        _dispatch(handler)
    except (BrokenPipeError, ConnectionResetError):
        return
    except SessionStoreError as failure:
        code = failure.code if failure.code in ERRORS else "workspace_storage_unavailable" if failure.code.startswith("session_store_") else "workspace_invalid_request"
        status, message = ERRORS[code]
        _send(handler, status, workspace_pages.failure(message))
    except (SessionBrokerError, OSError, ValueError, TypeError, UnicodeError, RecursionError):
        _send(handler, 400, workspace_pages.failure(ERRORS["workspace_invalid_request"][1]))


def _dispatch(handler):
    service = handler.server.workspace
    if service is None:
        raise WorkspaceError("workspace_not_found")
    request = urlsplit(handler.path)
    if request.scheme or request.netloc or request.fragment or len(request.query) > 1024 or "\\" in handler.path or handler.path.startswith("//"):
        raise WorkspaceError("workspace_invalid_request")
    path = request.path
    probe = path.startswith("/.well-known/hormuz-domain/")
    origin, domain = request_origin(handler, probe=probe)
    handler._workspace_origin = origin
    if any(len(handler.headers.get_all(name, [])) > 1 for name in ("Origin", "Cookie", "Content-Type", "Content-Length")) or handler.headers.get_all("Transfer-Encoding", []):
        raise WorkspaceError("workspace_invalid_request")
    if any(key.lower() in {"authorization", "proxy-authorization", "x-forwarded-host", "forwarded"} or key.lower().startswith(("x-hormuz-organization", "x-hormuz-team", "x-hormuz-actor", "x-hormuz-role")) for key in handler.headers):
        raise WorkspaceError("workspace_invalid_request")
    if not service.request_limit.allow():
        raise WorkspaceError("workspace_rate_limited")
    canonical = service.sessions.origin
    if handler.command == "GET":
        if handler.headers.get_all("Content-Length", []) not in ([], ["0"]):
            raise WorkspaceError("workspace_invalid_request")
        if handler.headers.get("Origin") is not None and handler.headers.get_all("Origin", []) != [origin]:
            raise WorkspaceError("workspace_origin_rejected")
        if probe:
            query = _form(request.query, allowed={"nonce"})
            proof = service.domains.probe(urlsplit(origin).hostname, path.removeprefix("/.well-known/hormuz-domain/"), query.get("nonce", ""))
            _send(handler, 200, json.dumps({"proof": proof}), content_type="application/json")
            return
        if path == "/workspace/styles.css" and not request.query:
            _send(handler, 200, files("hormuz").joinpath("console.css").read_text() + "\n" + files("hormuz").joinpath("workspace.css").read_text(), content_type="text/css; charset=utf-8")
            return
        if path == "/workspace/login" and origin == canonical:
            query = _form(request.query, allowed={"handoff", "workspace"})
            if set(query) != {"handoff", "workspace"}:
                raise WorkspaceError("workspace_invalid_request")
            url, cookie = service.begin_login(workspace_id=query["workspace"], handoff_id=query["handoff"])
            _send(handler, 200, workspace_pages.continue_login(url), cookies=[_cookie_header(handler, "flow", cookie)])
            return
        if path not in {"/workspace", "/workspace/", "/v1/workspaces/me"} and not re.fullmatch(r"/w/workspace-[a-f0-9]{16}", path):
            raise WorkspaceError("workspace_not_found")
        if request.query:
            raise WorkspaceError("workspace_invalid_request")
        credential = _cookie(handler, "session")
        try:
            current = service.sessions.authenticate(credential, origin)
        except WorkspaceError as failure:
            if failure.code != "workspace_session_required" or path == "/v1/workspaces/me":
                raise
            _send(handler, 200, workspace_pages.login(), cookies=[_cookie_header(handler, "session", "")])
            return
        if domain and current["workspace_id"] != domain["workspace_id"] or path.startswith("/w/") and path != "/w/" + current["slug"]:
            raise WorkspaceError("workspace_access_denied")
        if path == "/v1/workspaces/me":
            _send(handler, 200, json.dumps({"workspace_id": current["workspace_id"], "organization_id": current["organization_id"], "slug": current["slug"], "dashboard_url": canonical + "/w/" + current["slug"], "csrf_token": service.sessions.csrf(credential, origin)}), content_type="application/json")
        elif path in {"/workspace", "/workspace/"} and origin == canonical:
            _send(handler, 303, "", location="/w/" + current["slug"])
        else:
            _dashboard(handler, credential, current, origin)
        return
    if handler.command != "POST" or request.query or probe:
        raise WorkspaceError("workspace_not_found")
    if path == "/v1/workspaces/auth/callback" and origin == canonical:
        values = _values(handler, allowed={"state", "code", "iss", "error", "error_description", "error_uri", "session_state"}, required={"state"}, form_only=True)
        result = service.complete_login(state=values["state"], cookie=_cookie(handler, "flow"), code=values.get("code"), error=values.get("error"), response_issuer=values.get("iss"))
        if "handoff_token" in result:
            _send(handler, 200, workspace_pages.handoff(result["handoff_origin"], result["handoff_token"]), cookies=[_cookie_header(handler, "flow", "")], form_origin=result["handoff_origin"])
        else:
            _send(handler, 303, "", location="/w/" + result["slug"], cookies=[_cookie_header(handler, "flow", ""), _cookie_header(handler, "session", result["credential"])])
        return
    if path == "/v1/workspaces/auth/handoff" and domain:
        if handler.headers.get_all("Origin", []) != [canonical]:
            raise WorkspaceError("workspace_origin_rejected")
        values = _values(handler, allowed={"token"}, required={"token"}, form_only=True)
        credential, slug = service.sessions.consume_handoff(values["token"], _cookie(handler, "handoff"), origin)
        _send(handler, 303, "", location="/w/" + slug, cookies=[_cookie_header(handler, "handoff", ""), _cookie_header(handler, "session", credential)])
        return
    if handler.headers.get_all("Origin", []) != [origin]:
        raise WorkspaceError("workspace_origin_rejected")
    if path == "/v1/workspaces/auth/start":
        values = _values(handler, allowed={"intent"}, required={"intent"}, form_only=True)
        if values["intent"] != "signin":
            raise WorkspaceError("workspace_invalid_request")
        if domain:
            handoff, cookie, workspace = service.sessions.begin_handoff(domain["hostname"])
            target = canonical + "/workspace/login?" + urlencode({"handoff": handoff, "workspace": workspace})
            _send(handler, 303, "", location=target, cookies=[_cookie_header(handler, "handoff", cookie)])
        else:
            url, cookie = service.begin_login()
            _send(handler, 200, workspace_pages.continue_login(url), cookies=[_cookie_header(handler, "flow", cookie)])
        return
    if path not in {"/v1/workspaces/logout", "/v1/workspaces/domains", "/v1/workspaces/domains/check", "/v1/workspaces/domains/remove"}:
        raise WorkspaceError("workspace_not_found")
    credential = _cookie(handler, "session")
    fields = {"csrf_token"} | ({"hostname"} if path == "/v1/workspaces/domains" else {"domain_id"} if path.startswith("/v1/workspaces/domains/") else set())
    values = _values(handler, allowed=fields, required=fields, form_only=True)
    service.sessions.require_csrf(credential, origin, values["csrf_token"])
    if path == "/v1/workspaces/logout":
        service.sessions.logout(credential, origin)
        _send(handler, 303, "", location="/workspace", cookies=[_cookie_header(handler, "session", "")])
        return
    if path == "/v1/workspaces/domains":
        service.domains.claim(credential, origin, values["hostname"])
        message = "Domain added. Add the DNS records below, then check the connection."
    elif path.endswith("/check"):
        service.domains.check(credential, origin, values["domain_id"])
        message = "Connection checked. Your domain status is shown below."
    else:
        service.domains.remove(credential, origin, values["domain_id"])
        # Removing the current host also revokes its session and host binding.
        _send(handler, 303, "", location=canonical + "/workspace")
        return
    current = service.sessions.authenticate(credential, origin)
    _dashboard(handler, credential, current, origin, message=message)


def _dashboard(handler, credential, current, origin, *, message=""):
    service = handler.server.workspace
    _send(handler, 200, workspace_pages.dashboard(current, service.sessions.origin, service.domains.list(credential, origin), service.sessions.csrf(credential, origin), domains_enabled=service.domains.provider is not None, message=message, ai_work_enabled=getattr(handler.server, "work_runtime", None) is not None))


def _cookie_name(handler, purpose):
    secure = getattr(handler, "_workspace_origin", handler.server.config.session_broker.public_base_url).startswith("https:")
    name = "hormuz_workspace" + ("_" + purpose if purpose != "session" else "")
    return "__Host-" + name if secure else name + "_local"


def _cookie(handler, purpose):
    header = handler.headers.get("Cookie", "")
    name = _cookie_name(handler, purpose)
    if len(header) > 8192 or sum(part.strip().startswith(name + "=") for part in header.split(";")) > 1:
        raise WorkspaceError("workspace_invalid_request")
    try:
        values = SimpleCookie(header)
    except CookieError:
        raise WorkspaceError("workspace_invalid_request") from None
    return values[name].value if name in values else ""


def _cookie_header(handler, purpose, value):
    secure = getattr(handler, "_workspace_origin", handler.server.config.session_broker.public_base_url).startswith("https:")
    max_age = (3600 if purpose == "session" else 300) if value else 0
    return f"{_cookie_name(handler, purpose)}={value}; Path=/; HttpOnly; SameSite={'None' if secure and purpose != 'session' else 'Lax'}; Max-Age={max_age}" + ("; Secure" if secure else "")


def _send(handler, status, body, *, content_type="text/html; charset=utf-8", cookies=(), location=None, form_origin=None):
    body = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Referrer-Policy", "strict-origin" if content_type.startswith("text/html") else "no-referrer")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" + (" " + form_origin if form_origin else ""))
    for cookie in cookies:
        handler.send_header("Set-Cookie", cookie)
    if location:
        handler.send_header("Location", location)
    handler.end_headers()
    handler.wfile.write(body)
