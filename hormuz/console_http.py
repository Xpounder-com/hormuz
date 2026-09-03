"""Strict browser transport for the opt-in console; never accepts bearer auth."""

from __future__ import annotations

import json
import re
import sqlite3
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from importlib.resources import files
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from . import console_pages
from .console_store import ConsoleError
from .evidence import EvidenceStorageError
from .postgres import PostgresStorageError
from .session import SessionBrokerError
from .session_http import _form, _read_body
from .session_store import SessionStoreError
from .store import StorageSchemaError

if TYPE_CHECKING:
    from .server import GatewayRequestHandler


_ERRORS = {
    "admin_not_found": (404, "This console route is not available."),
    "admin_session_required": (401, "Your console session expired or access changed. Sign in again."),
    "admin_access_denied": (403, "This account does not have the required console permission."),
    "admin_invalid_request": (400, "The request has missing, invalid, or unexpected fields."),
    "admin_host_rejected": (400, "Open the console at its configured gateway address."),
    "admin_origin_rejected": (403, "Submit this request from the gateway console."),
    "admin_bearer_refused": (400, "The console requires a separate browser session."),
    "admin_csrf_rejected": (403, "The form could not be verified. Reload the console and try again."),
    "admin_invalid_window": (400, "Choose up to 31 inclusive UTC days, ending no later than today."),
    "admin_team_unavailable": (404, "That team is not available in your organization."),
    "admin_member_unavailable": (404, "That member is not available in your organization."),
    "admin_self_removal_refused": (409, "Ask another administrator or the server operator to remove your access."),
    "admin_member_changed": (409, "This member's access changed. Reload the console before removing access."),
    "admin_login_invalid": (400, "Sign-in could not be verified. Start a new sign-in from the console."),
    "admin_login_unavailable": (503, "The identity provider is unavailable. Start a new sign-in later."),
    "admin_login_capacity_reached": (503, "Too many sign-ins are pending. Try again later."),
    "admin_rate_limited": (429, "Too many console requests. Try again shortly."),
    "admin_storage_unavailable": (503, "Console storage is temporarily unavailable."),
}
_QUERY_FIELDS = {"from_date", "through_date", "team_id", "members_after", "teams_after"}


def handle_console_request(handler: GatewayRequestHandler) -> None:
    handler.close_connection = True
    try:
        _dispatch(handler)
    except (BrokenPipeError, ConnectionResetError):
        # The browser navigated away; do not attempt a second response.
        return
    except SessionStoreError as error:
        code = error.code
        if code.startswith("session_store_"):
            code = "admin_storage_unavailable"
        elif code == "onboarding_membership_unavailable":
            code = "admin_member_unavailable"
        elif code not in _ERRORS:
            code = "admin_invalid_request"
        _failure(handler, code)
    except (sqlite3.Error, EvidenceStorageError, PostgresStorageError, StorageSchemaError):
        _failure(handler, "admin_storage_unavailable")
    except (SessionBrokerError, OSError, UnicodeError, ValueError, TypeError, RecursionError):
        _failure(handler, "admin_invalid_request")


def _dispatch(handler: GatewayRequestHandler) -> None:
    service = handler.server.console
    settings = handler.server.config.session_broker
    if service is None or not (settings.enabled and settings.console_enabled and settings.onboarding_enabled):
        raise ConsoleError("admin_not_found")
    origin = handler.server.config.session_broker.public_base_url
    if handler.headers.get_all("Host", []) != [urlsplit(origin).netloc]:
        raise ConsoleError("admin_host_rejected")
    if any(key.lower() in {"authorization", "proxy-authorization"}
           or key.lower().startswith(("x-hormuz-organization", "x-hormuz-team", "x-hormuz-actor", "x-hormuz-role"))
           for key in handler.headers):
        raise ConsoleError("admin_bearer_refused")
    if not handler.server.console_request_limit.allow():
        raise ConsoleError("admin_rate_limited")
    request = urlsplit(handler.path)
    if request.scheme or request.netloc or request.fragment or len(request.query) > 2048:
        raise ConsoleError("admin_invalid_request")
    path = request.path
    if handler.command == "GET":
        if handler.headers.get("Transfer-Encoding") is not None or handler.headers.get_all("Content-Length", []) not in ([], ["0"]):
            raise ConsoleError("admin_invalid_request")
        if handler.headers.get("Origin") is not None and handler.headers.get_all("Origin", []) != [origin]:
            raise ConsoleError("admin_origin_rejected")
        if path == "/console/styles.css":
            if request.query:
                raise ConsoleError("admin_invalid_request")
            _send(handler, 200, files("hormuz").joinpath("console.css").read_bytes(), content_type="text/css; charset=utf-8")
            return
        if path == "/console":
            query = _form(request.query, allowed=_QUERY_FIELDS)
            credential = _cookie(handler, "session")
            if not credential:
                _html(handler, console_pages.login_page())
                return
            try:
                principal = service.sessions.authenticate(credential)
            except ConsoleError as error:
                if error.code != "admin_session_required":
                    raise
                _html(handler, console_pages.login_page(message=_ERRORS[error.code][1]), cookies=[_cookie_header(handler, "session", "")])
                return
            _dashboard(handler, principal, credential, query)
            return
        if path not in {
            "/v1/admin/me",
            "/v1/admin/usage",
            "/v1/admin/teams",
            "/v1/admin/members",
            "/v1/admin/operations",
        }:
            raise ConsoleError("admin_not_found")
        credential = _cookie(handler, "session")
        principal = service.sessions.authenticate(credential)
        if path == "/v1/admin/me":
            if request.query:
                raise ConsoleError("admin_invalid_request")
            _json_response(handler, {"schema_id": "hormuz.admin-identity", "schema_version": 1, **principal.to_dict(),
                                     "csrf_token": service.sessions.csrf_token(credential)})
        elif path == "/v1/admin/usage":
            query = _form(request.query, allowed={"from_date", "through_date", "team_id"})
            _json_response(handler, service.report(principal, **query))
        elif path == "/v1/admin/operations":
            if request.query:
                raise ConsoleError("admin_invalid_request")
            if principal.role != "member_admin":
                raise ConsoleError("admin_access_denied")
            snapshot = getattr(handler.server, "operational_stats", None)
            if snapshot is None:
                raise ConsoleError("admin_not_found")
            _json_response(handler, snapshot())
        else:
            query = _form(request.query, allowed={"after", "limit"})
            limit = query.get("limit", "20")
            if not re.fullmatch(r"[0-9]{1,3}", limit):
                raise ConsoleError("admin_invalid_request")
            kind = "memberships" if path.endswith("/members") else "teams"
            _json_response(handler, service.list_records(principal, kind, after=query.get("after", ""), limit=int(limit)))
        return
    if handler.command != "POST":
        raise ConsoleError("admin_not_found")
    if request.query:
        raise ConsoleError("admin_invalid_request")
    if path == "/v1/admin/auth/callback":
        values = _values(handler, allowed={"code", "state", "iss", "error", "error_description", "error_uri", "session_state"},
                         required={"state"}, form_only=True)
        credential = service.complete_login(state=values["state"], browser_cookie=_cookie(handler, "flow"),
                                            code=values.get("code"), provider_error=values.get("error"), response_issuer=values.get("iss"))
        _redirect(handler, cookies=[_cookie_header(handler, "flow", ""), _cookie_header(handler, "session", credential)])
        return
    if handler.headers.get_all("Origin", []) != [origin]:
        raise ConsoleError("admin_origin_rejected")
    if path == "/v1/admin/auth/start":
        values = _values(handler, allowed={"organization_id"}, required={"organization_id"}, form_only=True)
        url, cookie = service.begin_login(values["organization_id"])
        _html(handler, console_pages.continue_page(url), cookies=[_cookie_header(handler, "flow", cookie)])
        return
    if path not in {"/v1/admin/members/disable", "/v1/admin/logout"}:
        raise ConsoleError("admin_not_found")
    credential = _cookie(handler, "session")
    service.sessions.authenticate(credential)
    fields = {"csrf_token"} | ({"membership_id", "expected_version"} if path.endswith("/disable") else set())
    values = _values(handler, allowed=fields, required=fields)
    service.sessions.require_csrf(credential, values["csrf_token"])
    if path == "/v1/admin/logout":
        service.sessions.logout(credential)
        if handler.headers.get_content_type() == "application/json":
            _json_response(handler, {"schema_id": "hormuz.admin-logout", "schema_version": 1, "revoked": True},
                           cookies=[_cookie_header(handler, "session", "")])
        else:
            _redirect(handler, cookies=[_cookie_header(handler, "session", "")])
        return
    version = values["expected_version"]
    if isinstance(version, str) and re.fullmatch(r"[0-9]{1,9}", version):
        version = int(version)
    if type(version) is not int or not 1 <= version <= 2_147_483_647:
        raise ConsoleError("admin_invalid_request")
    result = service.sessions.disable_member(credential, membership_id=values["membership_id"], expected_version=version)
    if handler.headers.get_content_type() == "application/json":
        _json_response(handler, {"schema_id": "hormuz.admin-member-removal", "schema_version": 1, **result})
    else:
        principal = service.sessions.authenticate(credential)
        _dashboard(handler, principal, credential, {}, message="Access removed. Existing client and console sessions are revoked.")


def _dashboard(handler, principal, credential, query, *, message="") -> None:
    service = handler.server.console
    report = service.report(principal, **{key: query[key] for key in ("from_date", "through_date", "team_id") if key in query})
    teams = service.list_records(principal, "teams", after=query.get("teams_after", ""))
    members = (service.list_records(principal, "memberships", after=query.get("members_after", ""))
               if principal.role == "member_admin" else None)
    _html(handler, console_pages.dashboard(principal, report, teams, members, service.sessions.csrf_token(credential), query, message=message))


def _values(handler, *, allowed: set[str], required: set[str], form_only=False) -> dict:
    if len(handler.headers.get_all("Content-Type", [])) != 1:
        raise ConsoleError("admin_invalid_request")
    content_type = handler.headers.get_content_type()
    if content_type == "application/x-www-form-urlencoded":
        value = _form(_read_body(handler, content_type).decode("utf-8"), allowed=allowed)
    elif content_type == "application/json" and not form_only:
        def unique(pairs):
            result = {}
            for key, item in pairs:
                if key in result:
                    raise ConsoleError("admin_invalid_request")
                result[key] = item
            return result
        value = json.loads(_read_body(handler, content_type), object_pairs_hook=unique)
    else:
        raise ConsoleError("admin_invalid_request")
    if not isinstance(value, dict) or set(value) - allowed or not required <= set(value):
        raise ConsoleError("admin_invalid_request")
    for key, item in value.items():
        if key == "expected_version" and type(item) is int:
            continue
        if not isinstance(item, str) or len(item) > 4096 or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item):
            raise ConsoleError("admin_invalid_request")
    return value


def _cookie_name(handler, purpose: str) -> str:
    name = "hormuz_console" + ("_flow" if purpose == "flow" else "")
    return "__Host-" + name if handler.server.config.session_broker.public_base_url.startswith("https:") else name + "_local"


def _cookie(handler, purpose: str) -> str:
    values = handler.headers.get_all("Cookie", [])
    if not values:
        return ""
    if len(values) != 1 or len(values[0]) > 8192:
        raise ConsoleError("admin_invalid_request")
    name = _cookie_name(handler, purpose)
    if sum(part.strip().startswith(name + "=") for part in values[0].split(";")) > 1:
        raise ConsoleError("admin_invalid_request")
    try:
        cookies = SimpleCookie(values[0])
    except CookieError:
        raise ConsoleError("admin_invalid_request") from None
    return cookies[name].value if name in cookies else ""


def _cookie_header(handler, purpose: str, credential: str) -> str:
    """Hand opaque credentials to an HttpOnly browser cookie, never browser storage."""
    secure = handler.server.config.session_broker.public_base_url.startswith("https:")
    age = (handler.server.config.session_broker.enrollment_ttl_seconds if purpose == "flow" else 3600) if credential else 0
    same_site = "None" if secure and purpose == "flow" else "Lax"
    return (f"{_cookie_name(handler, purpose)}={credential}; Path=/; HttpOnly; SameSite={same_site}; Max-Age={age}"
            + ("; Secure" if secure else ""))


def _failure(handler, code: str) -> None:
    status, message = _ERRORS[code]
    if urlsplit(handler.path).path == "/console" or handler.headers.get_content_type() == "application/x-www-form-urlencoded":
        _html(handler, console_pages.failure_page(message), status=status)
    else:
        _json_response(handler, {"schema_id": "hormuz.admin-error", "schema_version": 1, "error": {"code": code, "message": message}}, status=status)


def _send(handler, status: int, body: bytes, *, content_type: str, cookies=(), location=None) -> None:
    handler.send_response(HTTPStatus(status))
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    # no-referrer also makes native form POSTs send Origin: null. Preserve the
    # browser's origin without disclosing URL paths, queries, or HTTPS downgrades.
    # External identity-provider links separately use rel=noreferrer.
    referrer_policy = "strict-origin" if content_type == "text/html; charset=utf-8" else "no-referrer"
    handler.send_header("Referrer-Policy", referrer_policy)
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    for cookie in cookies:
        handler.send_header("Set-Cookie", cookie)
    if location:
        handler.send_header("Location", location)
    handler.end_headers()
    handler.wfile.write(body)


def _json_response(handler, value, *, status=200, cookies=()) -> None:
    _send(handler, status, json.dumps(value, separators=(",", ":")).encode("utf-8"), content_type="application/json", cookies=cookies)


def _html(handler, value: str, *, status=200, cookies=()) -> None:
    _send(handler, status, value.encode("utf-8"), content_type="text/html; charset=utf-8", cookies=cookies)


def _redirect(handler, *, cookies=()) -> None:
    _send(handler, 303, b"", content_type="text/plain; charset=utf-8", cookies=cookies, location="/console")
