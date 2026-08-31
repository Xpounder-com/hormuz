"""Bounded HTTP adapter for the opt-in single-node login broker."""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from .session import SessionBrokerError
from .session_store import SessionStoreError

if TYPE_CHECKING:
    from .server import GatewayRequestHandler


_MAX_BODY = 16 * 1024
_ENROLLMENT = re.compile(r"[A-Za-z0-9_-]{32}\Z")
_REDEEM = re.compile(r"/v1/auth/enrollments/([A-Za-z0-9_-]{32})/redeem\Z")
LOGGER = logging.getLogger("hormuz.session")


class SessionRequestLimit:
    """A bounded, process-wide guard; not a distributed production rate limit."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._count = 0

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._started >= 60:
                self._started, self._count = now, 0
            self._count += 1
            return self._count <= 600


def handle_session_request(handler: GatewayRequestHandler) -> None:
    """Handle only /v1/auth routes. No inference body or credential is logged."""
    broker = handler.server.session_broker
    # Closing bounds partial/malformed bodies and prevents keep-alive desync.
    handler.close_connection = True
    if broker is None:
        handler._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)
        return
    try:
        _dispatch(handler)
    except (SessionBrokerError, SessionStoreError) as error:
        unavailable = error.code.startswith("session_store_") or error.code in {
            "oidc_metadata_unavailable", "oidc_token_exchange_failed", "oidc_userinfo_failed",
            "enrollment_capacity_reached",
        }
        status = HTTPStatus.SERVICE_UNAVAILABLE if unavailable else HTTPStatus.BAD_REQUEST
        if error.code == "enrollment_not_redeemable":
            status = HTTPStatus.CONFLICT
        elif error.code in {"invalid_session_credential", "expired_session_credential", "refresh_replay_detected", "session_authorization_removed"}:
            status = HTTPStatus.UNAUTHORIZED
        _send_failure(handler, error.code, status)
    except (OSError, UnicodeError, ValueError):
        _send_failure(handler, "invalid_session_request", HTTPStatus.BAD_REQUEST)


def _send_failure(handler: GatewayRequestHandler, reason: str, status: HTTPStatus) -> None:
    """Preserve the published error envelope and enum; reasons are fixed metadata."""
    LOGGER.info("session_request_denied reason=%s", reason)
    code = "invalid_request"
    if status == HTTPStatus.UNAUTHORIZED:
        code = "unauthorized"
    elif status == HTTPStatus.SERVICE_UNAVAILABLE:
        code = "hormuz_storage_unavailable" if reason.startswith("session_store_") else "gateway_upstream_error"
    handler._send_error(code, "Session request failed: " + reason, status)


def _dispatch(handler: GatewayRequestHandler) -> None:
    broker = handler.server.session_broker
    assert broker is not None
    base = urlsplit(broker.config.session_broker.public_base_url)
    if handler.headers.get_all("Host", []) != [base.netloc]:
        raise SessionBrokerError("invalid_session_host")
    if not handler.server.session_request_limit.allow():
        _send_failure(handler, "session_rate_limited", HTTPStatus.TOO_MANY_REQUESTS)
        return
    request = urlsplit(handler.path)
    path = request.path
    if request.fragment:
        raise SessionBrokerError("invalid_session_request")
    if handler.command == "GET" and path == "/v1/auth/login":
        values = _form(request.query, allowed={"enrollment"})
        enrollment_id = values.get("enrollment", "")
        if not _ENROLLMENT.fullmatch(enrollment_id):
            raise SessionBrokerError("invalid_enrollment")
        authorization_url, cookie = broker.begin_authorization(enrollment_id)
        _browser_page(
            handler,
            "Connect your AI client",
            "Continue only if you just started Hormuz login in your own app or terminal. "
            "This connects that client to your organization's governed AI access.",
            cookie=cookie,
            authorization_url=authorization_url,
            invitation_form=(enrollment_id, parse_qs(urlsplit(authorization_url).query)["state"][0])
                if broker.config.session_broker.onboarding_enabled else None,
        )
        return
    if request.query:
        # Codes, credentials, or callback state in URL queries are never accepted.
        raise SessionBrokerError("session_query_not_allowed")
    if handler.command == "POST" and path == "/v1/auth/invitations/accept":
        if handler.headers.get_all("Origin", []) != [broker.config.session_broker.public_base_url]:
            raise SessionBrokerError("invalid_invitation_origin")
        values = _form(_read_body(handler, "application/x-www-form-urlencoded").decode("utf-8"),
                       allowed={"enrollment", "state", "invitation_code"})
        enrollment_id = values.get("enrollment", "")
        if not _ENROLLMENT.fullmatch(enrollment_id):
            raise SessionBrokerError("invalid_enrollment")
        authorization_url = broker.attach_invitation(
            enrollment_id=enrollment_id, state=values.get("state", ""),
            browser_cookie=_browser_cookie(handler), code=values.get("invitation_code", ""),
        )
        # A fresh link avoids forwarding the form (or relying on browsers' CSP
        # handling of cross-origin form redirects) to the identity provider.
        _browser_page(handler, "Invitation ready", "Sign in with the identity your operator invited.",
                      cookie=_browser_cookie(handler), authorization_url=authorization_url)
        return
    if handler.command == "POST" and path == "/v1/auth/callback":
        raw = _read_body(handler, "application/x-www-form-urlencoded")
        values = _form(raw.decode("utf-8"), allowed={"code", "state", "iss", "error", "error_description", "error_uri", "session_state"})
        broker.complete_authorization(
            state=values.get("state", ""),
            browser_cookie=_browser_cookie(handler),
            code=values.get("code"),
            provider_error=values.get("error"),
            response_issuer=values.get("iss"),
        )
        _browser_page(handler, "Connected", "Return to your terminal to finish. You can close this page.", cookie="")
        return
    if handler.command != "POST":
        handler._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)
        return
    # These are native-client endpoints, not cookie-authenticated browser APIs.
    if handler.headers.get("Origin") is not None:
        raise SessionBrokerError("session_browser_api_forbidden")
    if path == "/v1/auth/enrollments":
        value = _json(handler, allowed={"client", "enrollment_secret", "issuer", "organization_id"}, required={"client", "enrollment_secret"})
        enrollment, login_url = broker.create_enrollment(
            issuer_name=value.get("issuer"), organization_id=value.get("organization_id"),
            client_name=value["client"], enrollment_secret=value["enrollment_secret"],
        )
        handler._send_json(HTTPStatus.CREATED, {
            "enrollment_id": enrollment.enrollment_id, "login_url": login_url,
            "expires_at": enrollment.expires_at.isoformat(), "poll_interval_seconds": 1,
        })
        return
    match = _REDEEM.fullmatch(path)
    if match:
        value = _json(handler, allowed={"enrollment_secret"}, required={"enrollment_secret"})
        pair = broker.redeem(enrollment_id=match[1], enrollment_secret=value["enrollment_secret"])
        handler._send_json(HTTPStatus.OK, pair.to_dict())
        return
    if path == "/v1/auth/refresh":
        value = _json(handler, allowed={"refresh_token"}, required={"refresh_token"})
        handler._send_json(HTTPStatus.OK, broker.refresh(value["refresh_token"]).to_dict())
        return
    if path == "/v1/auth/logout":
        value = _json(handler, allowed={"credential"}, required={"credential"})
        broker.revoke(value["credential"])
        # Idempotent without revealing whether a supplied credential existed.
        handler._send_json(HTTPStatus.OK, {"revoked": True})
        return
    handler._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)


def _read_body(handler: GatewayRequestHandler, content_type: str) -> bytes:
    lengths = handler.headers.get_all("Content-Length", [])
    if handler.headers.get_all("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0]):
        raise SessionBrokerError("invalid_session_body_length")
    length = int(lengths[0])
    if not 0 < length <= _MAX_BODY:
        raise SessionBrokerError("invalid_session_body_length")
    if handler.headers.get_content_type() != content_type:
        raise SessionBrokerError("invalid_session_content_type")
    handler.connection.settimeout(5)
    body = handler.rfile.read(length)
    if len(body) != length:
        raise SessionBrokerError("incomplete_session_body")
    return body


def _json(handler: GatewayRequestHandler, *, allowed: set[str], required: set[str]) -> dict[str, str]:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise SessionBrokerError("invalid_session_request")
            value[key] = item
        return value

    try:
        value = json.loads(_read_body(handler, "application/json"), object_pairs_hook=unique)
    except (ValueError, RecursionError):
        raise SessionBrokerError("invalid_session_request") from None
    if (
        not isinstance(value, dict) or set(value) - allowed or not required <= set(value)
        or any(not isinstance(item, str) or not item or len(item) > 4096 or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item) for item in value.values())
    ):
        raise SessionBrokerError("invalid_session_request")
    return value


def _form(raw: str, *, allowed: set[str]) -> dict[str, str]:
    value = parse_qs(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=10, errors="strict")
    if set(value) - allowed or any(len(items) != 1 or len(items[0]) > 4096 for items in value.values()):
        raise SessionBrokerError("invalid_callback_parameters")
    return {key: items[0] for key, items in value.items()}


def _cookie_name(handler: GatewayRequestHandler) -> str:
    return "__Host-hormuz_login" if handler.server.config.session_broker.public_base_url.startswith("https:") else "hormuz_login_local"


def _browser_cookie(handler: GatewayRequestHandler) -> str:
    values = handler.headers.get_all("Cookie", [])
    if len(values) != 1 or len(values[0]) > 8192:
        raise SessionBrokerError("invalid_browser_cookie")
    # A duplicate same-name cookie is ambiguous across domain/path scopes.
    if sum(part.strip().startswith(_cookie_name(handler) + "=") for part in values[0].split(";")) != 1:
        raise SessionBrokerError("invalid_browser_cookie")
    try:
        cookies = SimpleCookie(values[0])
    except CookieError:
        raise SessionBrokerError("invalid_browser_cookie") from None
    item = cookies.get(_cookie_name(handler))
    return item.value if item is not None else ""


def _browser_page(handler: GatewayRequestHandler, title: str, message: str, *, cookie: str, authorization_url: str | None = None, invitation_form: tuple[str, str] | None = None) -> None:
    content = f"<!doctype html><html lang='en'><meta charset='utf-8'><title>Hormuz</title><h1>{title}</h1><p>{message}</p>"
    if authorization_url is not None:
        content += f'<p><a href="{html.escape(authorization_url, quote=True)}" rel="noreferrer">Continue to sign in</a></p>'
    if invitation_form is not None:
        enrollment_id, state = invitation_form
        content += (
            '<p>Joining a team for the first time? Enter the invitation code your operator gave you.</p>'
            '<form method="post" action="/v1/auth/invitations/accept" autocomplete="off">'
            f'<input type="hidden" name="enrollment" value="{html.escape(enrollment_id, quote=True)}">'
            f'<input type="hidden" name="state" value="{html.escape(state, quote=True)}">'
            '<label>Invitation code <input name="invitation_code" type="password" required '
            'minlength="49" maxlength="49" autocomplete="off" spellcheck="false"></label> '
            '<button type="submit">Use invitation</button></form>'
        )
    content += "</html>"
    body = content.encode("utf-8")
    config = handler.server.config.session_broker
    secure = config.public_base_url.startswith("https:")
    local_path = "/v1/auth" if config.onboarding_enabled else "/v1/auth/callback"
    attrs = "; Path=/; Secure; HttpOnly; SameSite=None" if secure else f"; Path={local_path}; HttpOnly; SameSite=Lax"
    max_age = config.enrollment_ttl_seconds if cookie else 0
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    # Native form POSTs need a non-null Origin for the exact-origin check.
    # strict-origin sends no URL path/query; external sign-in links remain noreferrer.
    handler.send_header("Referrer-Policy", "strict-origin" if invitation_form is not None else "no-referrer")
    handler.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("Set-Cookie", f"{_cookie_name(handler)}={cookie}; Max-Age={max_age}{attrs}")
    handler.end_headers()
    handler.wfile.write(body)
