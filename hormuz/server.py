from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from .auth import AuthenticationError, Authenticator
from .config import GatewayConfig, Identity, ModelRoute, UpstreamConfig
from .context import (
    CLASSIFICATIONS,
    ContextError,
    ContextPackRequest,
    ContextPrincipal,
    build_context_pack,
)
from .context_store import ContextStoreError, SQLiteContextRepository
from .dlp_approval import DLPApprovalError, payload_fingerprint
from .policy import PolicyDecision, PolicyEngine
from .redaction import RedactionError, SecretRedactor
from .session import SessionBroker, SessionBrokerError
from .session_store import SQLiteSessionStore, SessionStoreError
from .store import DLPApprovalStoreError, ReservationDenied, SecurityStoreError, UsageStore
from .usage import ResponseUsageParser


LOGGER = logging.getLogger("hormuz")
MAX_CONTEXT_REQUEST_BYTES = 64 * 1024
MAX_AUTH_REQUEST_BYTES = 8 * 1024
MAX_DLP_APPROVAL_REQUEST_BYTES = 2 * 1024


class ContextRateLimiter:
    """Single-process actor limiter; distributed enforcement remains a deployment concern."""

    def __init__(self, requests_per_minute: int):
        self.requests_per_minute = requests_per_minute
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, *, organization_id: str, actor_id: str) -> int | None:
        now = time.monotonic()
        cutoff = now - 60
        key = (organization_id, actor_id)
        with self._lock:
            requests = self._requests[key]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.requests_per_minute:
                return max(1, int(60 - (now - requests[0])) + 1)
            requests.append(now)
            return None


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: GatewayConfig):
        self.config = config
        self.authenticator = Authenticator(config)
        self.session_broker: SessionBroker | None = None
        if config.session_broker.enabled:
            session_config = config.session_broker
            if session_config.database_path is None:
                raise SessionStoreError("session_store_path_missing")
            session_store = SQLiteSessionStore(
                session_config.database_path,
                master_key=session_config.master_key,
                access_ttl_seconds=session_config.access_ttl_seconds,
                absolute_ttl_seconds=session_config.absolute_ttl_seconds,
                enrollment_ttl_seconds=session_config.enrollment_ttl_seconds,
            )
            self.session_broker = SessionBroker(config, self.authenticator, session_store)
        self.store = UsageStore(config.database_path)
        self.context_repository = SQLiteContextRepository(config.context_database_path)
        self.context_rate_limiter = ContextRateLimiter(
            config.context_service.requests_per_minute
        )
        self.login_rate_limiter = ContextRateLimiter(20)
        self.policy_engine = PolicyEngine(config, self.store)
        protected_values = [
            ("hormuz_identity_token", identity.token)
            for identity in config.identities_by_token.values()
            if identity.token
        ]
        protected_values.extend(
            ("provider_credential", value)
            for upstream in config.upstreams.values()
            if len(value := os.environ.get(upstream.api_key_env, "")) >= 8
        )
        protected_values.extend(
            ("oidc_client_secret", issuer.login.client_secret)
            for issuer in config.oidc_issuers.values()
            if issuer.login is not None
        )
        if len(config.session_broker.master_key_source) >= 8:
            protected_values.append(
                ("session_master_key", config.session_broker.master_key_source)
            )
        if len(config.dlp_controls.approval.fingerprint_key_source) >= 8:
            protected_values.append(
                (
                    "dlp_approval_fingerprint_key",
                    config.dlp_controls.approval.fingerprint_key_source,
                )
            )
        self.protected_values = tuple(protected_values)
        self._redactor_cache: dict[tuple[str, str, str, str, str], SecretRedactor] = {}
        self._redactor_cache_lock = threading.Lock()
        super().__init__((config.listen.host, config.listen.port), GatewayRequestHandler)

    def redactor_for(
        self,
        identity: Identity,
        *,
        protocol: str,
        model: str,
    ) -> SecretRedactor:
        cache_key = (
            identity.organization_id,
            identity.team_id,
            identity.actor_id,
            protocol,
            model,
        )
        with self._redactor_cache_lock:
            redactor = self._redactor_cache.get(cache_key)
            if redactor is None:
                redactor = SecretRedactor(
                    self.config.secret_controls,
                    self.protected_values,
                    self.config.resolved_dlp_controls(
                        identity,
                        protocol=protocol,
                        model=model,
                    ),
                )
                self._redactor_cache[cache_key] = redactor
            return redactor


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server: GatewayServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        request_url = urlsplit(self.path)
        path = request_url.path
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "hormuz",
                    "protocols": [
                        "openai-responses",
                        "anthropic-messages",
                        "hormuz-context-packs",
                    ],
                    "human_login": self.server.session_broker is not None,
                    "dlp_approval": self.server.config.dlp_controls.approval.enabled,
                },
            )
            return
        if path == "/v1/auth/login":
            self._begin_browser_login(request_url.query)
            return
        if path == "/v1/auth/callback":
            self._complete_browser_login(request_url.query)
            return
        identity = self._authenticate()
        if identity is None:
            return
        if path == "/v1/admin/sessions":
            self._list_human_sessions(identity, request_url.query)
            return
        if path == "/v1/gateway/whoami":
            self._send_json(
                HTTPStatus.OK,
                {
                    "actor_id": identity.actor_id,
                    "actor_name": identity.actor_name,
                    "team_id": identity.team_id,
                    "team_name": identity.team_name,
                    "organization_id": identity.organization_id,
                    "allowed_clients": list(identity.allowed_clients),
                    "capabilities": list(identity.capabilities),
                    "authentication_source": identity.authentication_source,
                },
            )
            return
        if path == "/v1/gateway/usage":
            totals = self.server.store.monthly_totals(actor_id=identity.actor_id)
            secret_totals = self.server.store.monthly_secret_totals(actor_id=identity.actor_id)
            approval_totals = self.server.store.monthly_dlp_approval_totals(
                actor_id=identity.actor_id
            )
            self._send_json(
                HTTPStatus.OK,
                {
                    "month": "current",
                    "requests": totals.requests,
                    "denied_requests": totals.denied_requests,
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "cache_read_tokens": totals.cache_read_tokens,
                    "cache_write_tokens": totals.cache_write_tokens,
                    "reasoning_tokens": totals.reasoning_tokens,
                    "cost_usd": totals.cost_usd,
                    "redactions": totals.redaction_count,
                    "secret_events": secret_totals.events,
                    "secret_detections": secret_totals.detections,
                    "secret_denied_requests": secret_totals.denied_requests,
                    "dlp_events": secret_totals.dlp_events,
                    "dlp_detections": secret_totals.dlp_detections,
                    "dlp_detected_requests": secret_totals.detected_requests,
                    "dlp_approval_required_requests": secret_totals.approval_required_requests,
                    "dlp_approval_requests": approval_totals.requests,
                    "dlp_approvals_granted": approval_totals.approved,
                    "dlp_approvals_consumed": approval_totals.consumed,
                    "dlp_approval_model_mismatches": approval_totals.model_mismatches,
                },
            )
            return
        approval_request_id = _approval_request_id_from_path(path)
        if approval_request_id is not None:
            self._get_dlp_approval_request(identity, approval_request_id)
            return
        self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/v1/auth/enrollments":
            self._create_login_enrollment()
            return
        if path.startswith("/v1/auth/enrollments/") and path.endswith("/redeem"):
            self._redeem_login_enrollment(path)
            return
        if path == "/v1/auth/refresh":
            self._refresh_human_session()
            return
        if path == "/v1/auth/logout":
            self._logout_human_session()
            return
        if path == "/v1/admin/session-revocations":
            identity = self._authenticate()
            if identity is None:
                return
            self._revoke_human_sessions(identity)
            return
        if path == "/v1/context/packs":
            identity = self._authenticate()
            if identity is None:
                return
            retry_after = self.server.context_rate_limiter.check(
                organization_id=identity.organization_id,
                actor_id=identity.actor_id,
            )
            if retry_after is not None:
                self._send_error(
                    "context_rate_limited",
                    "Context pack request limit exceeded",
                    HTTPStatus.TOO_MANY_REQUESTS,
                    headers={"Retry-After": str(retry_after)},
                    close_connection=True,
                )
                return
            self._create_context_pack(identity)
            return
        approval_request_id = _approval_decision_id_from_path(path)
        if approval_request_id is not None:
            identity = self._authenticate()
            if identity is None:
                return
            self._approve_dlp_request(identity, approval_request_id)
            return
        routes = {
            "/v1/responses": ("openai", "codex", True),
            "/v1/responses/compact": ("openai", "codex", True),
            "/v1/messages": ("anthropic", "claude-code", True),
            "/v1/messages/count_tokens": ("anthropic", "claude-code", False),
        }
        route = routes.get(path)
        if route is None:
            self._send_error(
                "not_found",
                "Route not found",
                HTTPStatus.NOT_FOUND,
                close_connection=True,
            )
            return
        identity = self._authenticate()
        if identity is None:
            return
        protocol, default_client, account_usage = route
        self._proxy_generation(
            identity=identity,
            protocol=protocol,
            # Do not trust a caller-supplied application name for enforcement.
            # The compatibility endpoint determines the policy client.
            client=default_client,
            account_usage=account_usage,
        )

    def _create_login_enrollment(self) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        retry_after = self.server.login_rate_limiter.check(
            organization_id="anonymous",
            actor_id=self.client_address[0],
        )
        if retry_after is not None:
            self._send_error(
                "login_rate_limited",
                "Login request limit exceeded",
                HTTPStatus.TOO_MANY_REQUESTS,
                headers={"Retry-After": str(retry_after)},
                close_connection=True,
            )
            return
        request = self._read_json_body(max_bytes=MAX_AUTH_REQUEST_BYTES)
        if request is None:
            return
        unknown = sorted(set(request) - {"issuer", "client", "enrollment_secret"})
        if unknown:
            self._send_error(
                "invalid_enrollment_request",
                "Unknown enrollment fields: " + ", ".join(unknown),
                HTTPStatus.BAD_REQUEST,
            )
            return
        issuer = request.get("issuer")
        if issuer is not None and not isinstance(issuer, str):
            self._send_error(
                "invalid_enrollment_request",
                "issuer must be a string when provided",
                HTTPStatus.BAD_REQUEST,
            )
            return
        client = request.get("client")
        enrollment_secret = request.get("enrollment_secret")
        if not isinstance(client, str) or not isinstance(enrollment_secret, str):
            self._send_error(
                "invalid_enrollment_request",
                "client and enrollment_secret are required strings",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            enrollment, login_url = broker.create_enrollment(
                issuer_name=issuer,
                client_name=client,
                enrollment_secret=enrollment_secret,
            )
        except (SessionBrokerError, SessionStoreError) as error:
            self._send_auth_failure(error.code)
            return
        self._send_json(
            HTTPStatus.CREATED,
            {
                "enrollment_id": enrollment.enrollment_id,
                "login_url": login_url,
                "expires_at": enrollment.expires_at.isoformat(),
            },
        )

    def _begin_browser_login(self, query: str) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        values = _single_query_values(query)
        enrollment_id = values.get("enrollment") if values is not None else None
        if (
            values is None
            or set(values) != {"enrollment"}
            or enrollment_id is None
            or not _safe_identifier(enrollment_id)
        ):
            self._send_browser_result(
                HTTPStatus.BAD_REQUEST,
                "Hormuz login could not be started.",
            )
            return
        try:
            authorization_url, browser_cookie = broker.begin_authorization(enrollment_id)
        except (SessionBrokerError, SessionStoreError):
            self._send_browser_result(
                HTTPStatus.BAD_REQUEST,
                "This Hormuz login request is invalid or expired.",
            )
            return
        cookie_name = self._login_cookie_name()
        cookie = (
            f"{cookie_name}={browser_cookie}; Path=/; Max-Age="
            f"{self.server.config.session_broker.enrollment_ttl_seconds}; HttpOnly; SameSite=Lax"
        )
        if not self.server.config.session_broker.allow_insecure_http:
            cookie += "; Secure"
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", authorization_url)
        self.send_header("Set-Cookie", cookie)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _complete_browser_login(self, query: str) -> None:
        broker = self._require_session_broker(browser=True)
        if broker is None:
            return
        values = _single_query_values(query)
        state = values.get("state") if values is not None else None
        code = values.get("code") if values is not None else None
        provider_error = values.get("error") if values is not None else None
        response_issuer = values.get("iss") if values is not None else None
        browser_cookie = self._login_cookie()
        if (
            values is None
            or not set(values).issubset({"state", "code", "error", "error_description", "iss"})
            or state is None
            or browser_cookie is None
            or (code is None) == (provider_error is None)
        ):
            self._send_browser_result(
                HTTPStatus.BAD_REQUEST,
                "Hormuz could not verify this login response.",
                clear_cookie=True,
            )
            return
        try:
            broker.complete_authorization(
                state=state,
                browser_cookie=browser_cookie,
                code=code,
                provider_error=provider_error,
                response_issuer=response_issuer,
            )
        except (SessionBrokerError, SessionStoreError):
            self._send_browser_result(
                HTTPStatus.BAD_REQUEST,
                "Hormuz could not complete this login. Return to the terminal and try again.",
                clear_cookie=True,
            )
            return
        self._send_browser_result(
            HTTPStatus.OK,
            "Login complete. You can close this window and return to the terminal.",
            clear_cookie=True,
        )

    def _redeem_login_enrollment(self, path: str) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        parts = path.split("/")
        if len(parts) != 6 or not _safe_identifier(parts[4]):
            self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)
            return
        request = self._read_json_body(max_bytes=MAX_AUTH_REQUEST_BYTES)
        if request is None:
            return
        if set(request) != {"enrollment_secret"} or not isinstance(
            request.get("enrollment_secret"), str
        ):
            self._send_error(
                "invalid_redemption_request",
                "enrollment_secret is required",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            pair = broker.redeem(
                enrollment_id=parts[4],
                enrollment_secret=request["enrollment_secret"],
            )
        except (SessionBrokerError, SessionStoreError) as error:
            status = (
                HTTPStatus.CONFLICT
                if error.code == "enrollment_not_redeemable"
                else HTTPStatus.UNAUTHORIZED
            )
            self._send_error("login_not_ready", "Login is not ready or has expired", status)
            return
        self._send_json(HTTPStatus.OK, pair.to_dict())

    def _refresh_human_session(self) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        request = self._read_json_body(max_bytes=MAX_AUTH_REQUEST_BYTES)
        if request is None:
            return
        if set(request) != {"refresh_token"} or not isinstance(request.get("refresh_token"), str):
            self._send_error(
                "invalid_refresh_request",
                "refresh_token is required",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            pair = broker.refresh(request["refresh_token"])
        except (SessionBrokerError, SessionStoreError) as error:
            LOGGER.info("session_refresh_denied reason=%s", error.code)
            self._send_error(
                "invalid_session",
                "Session refresh was rejected",
                HTTPStatus.UNAUTHORIZED,
            )
            return
        self._send_json(HTTPStatus.OK, pair.to_dict())

    def _logout_human_session(self) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        request = self._read_json_body(max_bytes=MAX_AUTH_REQUEST_BYTES)
        if request is None:
            return
        if set(request) != {"credential"} or not isinstance(request.get("credential"), str):
            self._send_error(
                "invalid_logout_request",
                "credential is required",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            broker.revoke(request["credential"])
        except (SessionBrokerError, SessionStoreError):
            pass
        self._send_json(HTTPStatus.OK, {"revoked": True})

    def _list_human_sessions(self, identity: Identity, query: str) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        try:
            values = urllib.parse.parse_qs(
                query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=4,
            )
        except ValueError:
            self._send_error(
                "invalid_session_list_request",
                "Session list query is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if set(values) - {"actor_id", "team_id", "cursor", "limit"} or any(
            len(items) != 1 or not items[0] for items in values.values()
        ):
            self._send_error(
                "invalid_session_list_request",
                "Session list query contains unsupported or repeated fields",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            limit = int(values.get("limit", ["50"])[0])
        except ValueError:
            self._send_error(
                "invalid_session_list_request",
                "Session list limit must be an integer from 1 to 100",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            sessions, next_cursor = broker.list_active_sessions(
                administrator=identity,
                limit=limit,
                cursor=values.get("cursor", [None])[0],
                actor_id=values.get("actor_id", [None])[0],
                team_id=values.get("team_id", [None])[0],
            )
        except SessionBrokerError as error:
            self._send_session_admin_error(error, list_request=True)
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "schema_version": 1,
                "sessions": [session.to_dict() for session in sessions],
                "next_cursor": next_cursor,
            },
        )

    def _revoke_human_sessions(self, identity: Identity) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        request = self._read_json_body(max_bytes=MAX_AUTH_REQUEST_BYTES)
        if request is None:
            return
        if set(request) != {"scope", "reason_code"} and set(request) != {
            "scope",
            "target",
            "reason_code",
        }:
            self._send_error(
                "invalid_session_revocation",
                "Request must contain scope, reason_code, and target when required",
                HTTPStatus.BAD_REQUEST,
            )
            return
        scope = request.get("scope")
        target = request.get("target")
        reason_code = request.get("reason_code")
        if (
            not isinstance(scope, str)
            or not isinstance(reason_code, str)
            or (target is not None and not isinstance(target, str))
        ):
            self._send_error(
                "invalid_session_revocation",
                "Session revocation fields have invalid types",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            revoked = broker.revoke_administratively(
                administrator=identity,
                scope=scope,
                target=target,
                reason_code=reason_code,
            )
        except SessionBrokerError as error:
            self._send_session_admin_error(error, list_request=False)
            return
        LOGGER.info(
            "session_admin_revocation organization=%s decision_actor=%s scope=%s target=%s reason=%s revoked=%d",
            identity.organization_id,
            identity.actor_id,
            scope,
            target or "-",
            reason_code,
            revoked,
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "schema_version": 1,
                "scope": scope,
                "target": target,
                "reason_code": reason_code,
                "revoked_sessions": revoked,
            },
        )

    def _send_session_admin_error(
        self,
        error: SessionBrokerError,
        *,
        list_request: bool,
    ) -> None:
        if error.code == "session_admin_capability_required":
            self._send_error(
                "session_admin_capability_required",
                "Session administrator capability is required",
                HTTPStatus.FORBIDDEN,
            )
            return
        invalid = {
            "invalid_session_page_limit",
            "invalid_session_cursor",
            "invalid_actor_id",
            "invalid_team_id",
            "invalid_session_id",
            "invalid_admin_revocation_reason",
            "invalid_admin_revocation_selector",
        }
        if error.code in invalid:
            self._send_error(
                "invalid_session_list_request" if list_request else "invalid_session_revocation",
                "Session administration request is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_error(
            "session_admin_unavailable",
            "Session administration is temporarily unavailable",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    def _require_session_broker(self, *, browser: bool = False) -> SessionBroker | None:
        broker = self.server.session_broker
        if broker is not None:
            return broker
        if browser:
            self._send_browser_result(HTTPStatus.NOT_FOUND, "Hormuz login is not enabled.")
        else:
            self._send_error("login_disabled", "Human login is not enabled", HTTPStatus.NOT_FOUND)
        return None

    def _send_auth_failure(self, code: str) -> None:
        LOGGER.info("session_enrollment_denied reason=%s", code)
        status = HTTPStatus.BAD_REQUEST
        if code in {"oidc_metadata_unavailable", "session_store_unavailable"}:
            status = HTTPStatus.SERVICE_UNAVAILABLE
        self._send_error("login_unavailable", "Login could not be started", status)

    def _login_cookie_name(self) -> str:
        return (
            "hormuz_login"
            if self.server.config.session_broker.allow_insecure_http
            else "__Host-hormuz_login"
        )

    def _login_cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        if len(raw) > 8192:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None
        item = cookie.get(self._login_cookie_name())
        return item.value if item is not None else None

    def _send_browser_result(
        self,
        status: HTTPStatus,
        message: str,
        *,
        clear_cookie: bool = False,
    ) -> None:
        body = (
            "<!doctype html><meta charset=utf-8><title>Hormuz login</title>"
            "<main><h1>Hormuz</h1><p>" + message + "</p></main>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        if clear_cookie:
            cookie = f"{self._login_cookie_name()}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
            if not self.server.config.session_broker.allow_insecure_http:
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _get_dlp_approval_request(
        self,
        identity: Identity,
        request_id: str,
    ) -> None:
        if not self.server.config.dlp_controls.approval.enabled:
            self._send_error(
                "dlp_approval_disabled",
                "DLP approval is not enabled",
                HTTPStatus.NOT_FOUND,
            )
            return
        if "dlp_approver" not in identity.capabilities:
            self._send_error(
                "dlp_approval_forbidden",
                "DLP approver capability is required",
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            request = self.server.store.get_dlp_approval_request(
                request_id,
                organization_id=identity.organization_id,
            )
        except DLPApprovalStoreError as error:
            self._send_dlp_approval_error(error)
            return
        self._send_json(HTTPStatus.OK, request.to_dict())

    def _approve_dlp_request(
        self,
        identity: Identity,
        request_id: str,
    ) -> None:
        approval_config = self.server.config.dlp_controls.approval
        if not approval_config.enabled:
            self._send_error(
                "dlp_approval_disabled",
                "DLP approval is not enabled",
                HTTPStatus.NOT_FOUND,
            )
            return
        if "dlp_approver" not in identity.capabilities:
            self._send_error(
                "dlp_approval_forbidden",
                "DLP approver capability is required",
                HTTPStatus.FORBIDDEN,
            )
            return
        body = self._read_json_body(max_bytes=MAX_DLP_APPROVAL_REQUEST_BYTES)
        if body is None:
            return
        if body != {"decision": "approve"}:
            self._send_error(
                "invalid_dlp_approval_decision",
                "Request body must contain only decision=approve",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            request = self.server.store.approve_dlp_approval_request(
                request_id,
                approver=identity,
                ttl_seconds=approval_config.ttl_seconds,
            )
        except DLPApprovalStoreError as error:
            self._send_dlp_approval_error(error)
            return
        LOGGER.info(
            "dlp_approval_granted request_id=%s approver=%s actor=%s organization=%s routed_model=%s policy_version=%s",
            request.request_id,
            identity.actor_id,
            request.actor_id,
            identity.organization_id,
            request.routed_model,
            request.policy_version,
        )
        self._send_json(HTTPStatus.OK, request.to_dict())

    def _send_dlp_approval_error(self, error: DLPApprovalStoreError) -> None:
        status = HTTPStatus.SERVICE_UNAVAILABLE
        public_code = "dlp_approval_unavailable"
        message = "DLP approval service is unavailable"
        if error.code == "approval_request_not_found":
            status = HTTPStatus.NOT_FOUND
            public_code = "dlp_approval_not_found"
            message = "DLP approval request was not found"
        elif error.code in {
            "approval_capability_required",
            "approval_self_approval_forbidden",
        }:
            status = HTTPStatus.FORBIDDEN
            public_code = "dlp_approval_forbidden"
            message = (
                "The request actor cannot approve their own DLP exception"
                if error.code == "approval_self_approval_forbidden"
                else "DLP approver capability is required"
            )
        elif error.code in {
            "approval_request_already_decided",
            "approval_request_not_approvable",
            "approval_replay_rejected",
        }:
            status = HTTPStatus.CONFLICT
            public_code = "dlp_approval_conflict"
            message = "DLP approval request is no longer approvable"
        self._send_error(public_code, message, status)

    def _create_context_pack(self, identity: Identity) -> None:
        request_body = self._read_json_body(max_bytes=MAX_CONTEXT_REQUEST_BYTES)
        if request_body is None:
            return
        allowed_fields = {
            "query",
            "token_budget",
            "max_items",
            "repository_id",
            "branch",
            "clearance",
            "include_provisional",
        }
        unknown_fields = sorted(set(request_body) - allowed_fields)
        if unknown_fields:
            self._send_error(
                "context_invalid_request",
                "Unknown request fields: " + ", ".join(unknown_fields),
                HTTPStatus.BAD_REQUEST,
            )
            return
        query = request_body.get("query")
        if not isinstance(query, str) or not query.strip():
            self._send_error(
                "context_invalid_request",
                "Request field query must be a non-empty string",
                HTTPStatus.BAD_REQUEST,
            )
            return
        token_budget = request_body.get("token_budget")
        if isinstance(token_budget, bool) or not isinstance(token_budget, int) or token_budget <= 0:
            self._send_error(
                "context_invalid_request",
                "Request field token_budget must be a positive integer",
                HTTPStatus.BAD_REQUEST,
            )
            return
        service_policy = self.server.config.context_service
        if token_budget > service_policy.max_token_budget:
            self._send_error(
                "context_policy_denied",
                "Requested token budget exceeds organization context policy",
                HTTPStatus.FORBIDDEN,
            )
            return
        max_items = request_body.get("max_items", service_policy.max_items)
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
            self._send_error(
                "context_invalid_request",
                "Request field max_items must be a positive integer",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if max_items > service_policy.max_items:
            self._send_error(
                "context_policy_denied",
                "Requested item limit exceeds organization context policy",
                HTTPStatus.FORBIDDEN,
            )
            return
        repository_id = request_body.get("repository_id")
        if repository_id is not None and not _valid_context_scope(repository_id):
            self._send_error(
                "context_invalid_request",
                "Request field repository_id must be null or a safe non-empty string up to 512 characters",
                HTTPStatus.BAD_REQUEST,
            )
            return
        branch = request_body.get("branch")
        if branch is not None and not _valid_context_scope(branch):
            self._send_error(
                "context_invalid_request",
                "Request field branch must be null or a safe non-empty string up to 512 characters",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if branch is not None and repository_id is None:
            self._send_error(
                "context_invalid_request",
                "Request field branch requires repository_id",
                HTTPStatus.BAD_REQUEST,
            )
            return
        clearance = request_body.get("clearance", identity.clearance)
        if not isinstance(clearance, str) or clearance not in CLASSIFICATIONS:
            self._send_error(
                "context_invalid_request",
                "Request field clearance must be a supported classification",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if CLASSIFICATIONS.index(clearance) > CLASSIFICATIONS.index(identity.clearance):
            self._send_error(
                "context_policy_denied",
                "Requested clearance exceeds the authenticated identity",
                HTTPStatus.FORBIDDEN,
            )
            return
        include_provisional = request_body.get("include_provisional", False)
        if not isinstance(include_provisional, bool):
            self._send_error(
                "context_invalid_request",
                "Request field include_provisional must be a boolean",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if include_provisional and not service_policy.allow_provisional:
            self._send_error(
                "context_policy_denied",
                "Organization context policy does not allow provisional records",
                HTTPStatus.FORBIDDEN,
            )
            return

        as_of = datetime.now(timezone.utc)
        try:
            principal = ContextPrincipal(
                organization_id=identity.organization_id,
                team_id=identity.team_id,
                actor_id=identity.actor_id,
                clearance=clearance,
                repository_id=repository_id.strip() if repository_id is not None else None,
                branch=branch.strip() if branch is not None else None,
            )
            request = ContextPackRequest(
                query=query.strip(),
                principal=principal,
                token_budget=token_budget,
                policy_version=service_policy.policy_version,
                max_items=max_items,
                include_provisional=include_provisional,
                as_of=as_of,
            )
            stored = self.server.context_repository.list_access_authorized(principal)
            lifecycle_snapshot = None
            if principal.repository_id is not None and principal.branch is not None:
                stored_snapshot = self.server.context_repository.get_lifecycle_snapshot(
                    organization_id=principal.organization_id,
                    repository_id=principal.repository_id,
                    branch=principal.branch,
                )
                if stored_snapshot is not None:
                    lifecycle_snapshot = stored_snapshot.snapshot
            pack = build_context_pack(
                (item.record for item in stored),
                request,
                lifecycle_snapshot=lifecycle_snapshot,
            )
            # No content leaves this boundary unless the metadata-only read event
            # has committed successfully.
            self.server.context_repository.record_pack_read(pack, occurred_at=as_of)
        except ContextError as error:
            self._send_error(
                "context_invalid_request",
                str(error),
                HTTPStatus.BAD_REQUEST,
            )
            return
        except ContextStoreError:
            LOGGER.error(
                "context_store_failed actor=%s team=%s organization=%s",
                identity.actor_id,
                identity.team_id,
                identity.organization_id,
            )
            self._send_error(
                "context_store_unavailable",
                "Governed context is temporarily unavailable",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        LOGGER.info(
            "context_pack_created actor=%s team=%s organization=%s repository=%s branch=%s pack_id=%s selected=%d excluded=%d contradictions=%d outcome=%s estimated_tokens=%d",
            identity.actor_id,
            identity.team_id,
            identity.organization_id,
            principal.repository_id or "-",
            principal.branch or "-",
            pack.pack_id,
            len(pack.items),
            len(pack.exclusions),
            len(pack.contradictions),
            pack.outcome,
            pack.estimated_tokens,
        )
        self._send_json(HTTPStatus.OK, pack.to_dict())

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _proxy_generation(
        self,
        *,
        identity: Identity,
        protocol: str,
        client: str,
        account_usage: bool,
    ) -> None:
        request_body = self._read_json_body()
        if request_body is None:
            return
        requested_model = request_body.get("model")
        if not isinstance(requested_model, str) or not requested_model.strip():
            self._send_protocol_error(protocol, "Request field model must be a non-empty string", HTTPStatus.BAD_REQUEST)
            return
        requested_model = requested_model.strip()
        output_field = "max_output_tokens" if protocol == "openai" else "max_tokens"
        requested_output = request_body.get(output_field)
        if requested_output is not None and (
            isinstance(requested_output, bool) or not isinstance(requested_output, int) or requested_output <= 0
        ):
            self._send_protocol_error(protocol, f"Request field {output_field} must be a positive integer", HTTPStatus.BAD_REQUEST)
            return

        decision = self.server.policy_engine.evaluate(
            identity=identity,
            client=client,
            protocol=protocol,
            requested_model=requested_model,
            requested_output_tokens=requested_output,
        )
        if not decision.allowed or decision.route is None:
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=requested_model,
                    resolved_alias=None,
                    upstream_model=None,
                    policy_action="denied",
                    status="denied",
                    cost_basis="not_applicable",
                )
            LOGGER.info(
                "policy_denied actor=%s team=%s client=%s protocol=%s requested_model=%s reason=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                requested_model,
                decision.reason,
            )
            self._send_protocol_error(protocol, decision.reason, HTTPStatus.FORBIDDEN, code="hormuz_policy_denied")
            return

        request_body["model"] = decision.route.upstream_model
        if decision.max_output_tokens is not None:
            current_output = request_body.get(output_field)
            if current_output is None or current_output > decision.max_output_tokens:
                request_body[output_field] = decision.max_output_tokens
        try:
            redaction = self.server.redactor_for(
                identity,
                protocol=protocol,
                model=decision.route.upstream_model,
            ).inspect(
                request_body,
                protocol=protocol,
                model=decision.route.upstream_model,
            )
        except RedactionError as error:
            self._send_protocol_error(protocol, str(error), HTTPStatus.BAD_REQUEST)
            return

        dlp_findings = tuple(finding for finding in redaction.findings if finding.origin == "dlp")
        dlp_detection_count = sum(finding.count for finding in dlp_findings)
        approval_request_id: str | None = None
        approval_authorized = False
        if redaction.action == "require_approval":
            approval_config = self.server.config.dlp_controls.approval
            if approval_config.enabled:
                approval_findings = tuple(
                    finding for finding in dlp_findings if finding.action == "require_approval"
                )
                try:
                    fingerprint = payload_fingerprint(
                        {
                            "operation": urlsplit(self.path).path,
                            "payload": redaction.value,
                        },
                        key=approval_config.fingerprint_key,
                    )
                    approval = self.server.store.authorize_or_request_dlp_approval(
                        identity=identity,
                        client=client,
                        protocol=protocol,
                        requested_model=decision.requested_model,
                        routed_model=decision.route.upstream_model,
                        policy_version=redaction.policy_version,
                        payload_fingerprint=fingerprint,
                        rules=tuple(
                            sorted({finding.rule_id for finding in approval_findings})
                        ),
                        detection_count=sum(finding.count for finding in approval_findings),
                        ttl_seconds=approval_config.ttl_seconds,
                    )
                except (DLPApprovalError, DLPApprovalStoreError):
                    LOGGER.error(
                        "dlp_approval_store_failed actor=%s team=%s client=%s protocol=%s requested_model=%s routed_model=%s",
                        identity.actor_id,
                        identity.team_id,
                        client,
                        protocol,
                        decision.requested_model,
                        decision.route.upstream_model,
                    )
                    self._send_protocol_error(
                        protocol,
                        "Request blocked because DLP approval state could not be committed.",
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        code="hormuz_dlp_approval_unavailable",
                    )
                    return
                approval_request_id = approval.request_id
                approval_authorized = approval.authorized
        redaction_rules = tuple(
            sorted(
                {
                    finding.rule_id
                    for finding in redaction.findings
                    if finding.action == "redact" or finding.origin == "secret"
                }
            )
        )
        try:
            if redaction.count:
                if dlp_findings:
                    security_action = (
                        "approved"
                        if approval_authorized
                        else {
                            "detect": "detected",
                            "redact": "redacted",
                            "deny": "denied",
                            "require_approval": "approval_required",
                        }[redaction.action]
                    )
                    self.server.store.record_dlp_event(
                        identity=identity,
                        client=client,
                        protocol=protocol,
                        requested_model=decision.requested_model,
                        routed_model=decision.route.upstream_model,
                        action=security_action,
                        redaction_count=redaction.redaction_count,
                        policy_version=redaction.policy_version,
                        findings=tuple(finding.to_dict() for finding in redaction.findings),
                    )
                else:
                    self.server.store.record_secret_event(
                        identity=identity,
                        client=client,
                        protocol=protocol,
                        requested_model=decision.requested_model,
                        action="denied" if redaction.action == "deny" else "redacted",
                        detection_count=redaction.count,
                        rules=redaction.rules,
                    )
        except SecurityStoreError:
            LOGGER.error(
                "security_evidence_failed actor=%s team=%s client=%s protocol=%s requested_model=%s routed_model=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                decision.route.upstream_model,
            )
            self._send_protocol_error(
                protocol,
                "Request blocked because DLP evidence could not be committed.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="hormuz_dlp_evidence_unavailable",
            )
            return

        if redaction.action == "deny":
            is_dlp_denial = bool(dlp_findings)
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="dlp_denied" if is_dlp_denial else "secret_denied",
                    status="denied",
                    cost_basis="not_applicable",
                    currency=decision.route.currency,
                    rate_card_version=decision.route.rate_card_version,
                    redaction_count=redaction.redaction_count,
                    redaction_rules=redaction_rules,
                )
            LOGGER.warning(
                "egress_denied actor=%s team=%s client=%s protocol=%s requested_model=%s routed_model=%s control=%s detections=%d rules=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                decision.route.upstream_model,
                "dlp" if is_dlp_denial else "secret",
                redaction.count,
                ",".join(redaction.rules),
            )
            self._send_protocol_error(
                protocol,
                (
                    "Request blocked by the organization's DLP policy."
                    if is_dlp_denial
                    else "Request blocked because Hormuz detected protected secret material."
                ),
                HTTPStatus.FORBIDDEN,
                code="hormuz_dlp_denied" if is_dlp_denial else "hormuz_secret_detected",
            )
            return

        if redaction.action == "require_approval" and not approval_authorized:
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="dlp_approval_required",
                    status="denied",
                    cost_basis="not_applicable",
                    currency=decision.route.currency,
                    rate_card_version=decision.route.rate_card_version,
                    redaction_count=redaction.redaction_count,
                    redaction_rules=redaction_rules,
                )
            LOGGER.warning(
                "dlp_approval_required actor=%s team=%s client=%s protocol=%s requested_model=%s routed_model=%s policy_version=%s detections=%d rules=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                decision.route.upstream_model,
                redaction.policy_version,
                redaction.count,
                ",".join(redaction.rules),
            )
            self._send_protocol_error(
                protocol,
                (
                    "Request requires an authorized DLP approval before provider egress. "
                    f"Approval request: {approval_request_id}."
                    if approval_request_id is not None
                    else "Request requires an authorized DLP approval before provider egress."
                ),
                HTTPStatus.FORBIDDEN,
                code="hormuz_dlp_approval_required",
                headers=(
                    {"X-Hormuz-DLP-Approval-Request": approval_request_id}
                    if approval_request_id is not None
                    else None
                ),
            )
            return

        upstream = self.server.config.upstreams[protocol]
        is_responses_create = protocol == "openai" and urlsplit(self.path).path == "/v1/responses"
        if (
            is_responses_create
            and redaction.value.get("background") is True
            and not upstream.allow_background
        ):
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="provider_policy_denied",
                    status="denied",
                    cost_basis="not_applicable",
                    currency=decision.route.currency,
                    rate_card_version=decision.route.rate_card_version,
                    redaction_count=redaction.redaction_count,
                    redaction_rules=redaction_rules,
                )
            LOGGER.info(
                "provider_policy_denied actor=%s team=%s client=%s protocol=%s requested_model=%s reason=background_disabled",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
            )
            self._send_protocol_error(
                protocol,
                "Organization policy does not allow OpenAI background response storage.",
                HTTPStatus.FORBIDDEN,
                code="hormuz_provider_policy_denied",
            )
            return
        if is_responses_create and not upstream.allow_response_storage:
            redaction.value["store"] = False

        policy_action = decision.action
        if redaction.redaction_count:
            policy_action = f"{policy_action}+redacted"
        if approval_authorized:
            policy_action = f"{policy_action}+dlp-approved"
        body = json.dumps(redaction.value, separators=(",", ":")).encode("utf-8")
        reservation_id: str | None = None
        if account_usage:
            reserved_output_tokens = redaction.value.get(output_field, 0)
            if not isinstance(reserved_output_tokens, int) or isinstance(reserved_output_tokens, bool):
                reserved_output_tokens = 0
            reserved_input_tokens = len(body)
            reserved_cost_microusd = decision.route.estimate_cost_microusd(
                input_tokens=reserved_input_tokens,
                output_tokens=max(0, reserved_output_tokens),
                cache_read_tokens=0,
                cache_write_tokens=0,
            )
            try:
                reservation_id = self.server.policy_engine.reserve_budget(
                    identity=identity,
                    reserved_tokens=reserved_input_tokens + max(0, reserved_output_tokens),
                    reserved_cost_microusd=reserved_cost_microusd,
                    ttl_seconds=self.server.config.upstream_timeout_seconds + 60,
                )
            except ReservationDenied as error:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="budget_reservation_denied",
                    status="denied",
                    cost_basis="not_applicable",
                    currency=decision.route.currency,
                    rate_card_version=decision.route.rate_card_version,
                    redaction_count=redaction.redaction_count,
                    redaction_rules=redaction_rules,
                )
                LOGGER.info(
                    "budget_reservation_denied actor=%s team=%s client=%s protocol=%s requested_model=%s reason=%s",
                    identity.actor_id,
                    identity.team_id,
                    client,
                    protocol,
                    decision.requested_model,
                    str(error),
                )
                self._send_protocol_error(
                    protocol,
                    str(error),
                    HTTPStatus.FORBIDDEN,
                    code="hormuz_budget_denied",
                )
                return
        try:
            self._forward(
                identity=identity,
                protocol=protocol,
                client=client,
                decision=decision,
                body=body,
                account_usage=account_usage,
                policy_action=policy_action,
                redaction_count=redaction.redaction_count,
                redaction_rules=redaction_rules,
                dlp_detection_count=dlp_detection_count,
                reservation_id=reservation_id,
                reservation_ttl_seconds=self.server.config.upstream_timeout_seconds + 60,
                approval_request_id=(approval_request_id if approval_authorized else None),
            )
        finally:
            self.server.store.release_budget_reservation(reservation_id)

    def _forward(
        self,
        *,
        identity: Identity,
        protocol: str,
        client: str,
        decision: PolicyDecision,
        body: bytes,
        account_usage: bool,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        dlp_detection_count: int,
        reservation_id: str | None,
        reservation_ttl_seconds: int,
        approval_request_id: str | None,
    ) -> None:
        route = decision.route
        assert route is not None
        upstream = self.server.config.upstreams[protocol]
        upstream_key = os.environ.get(upstream.api_key_env, "")
        if not upstream_key:
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=route.upstream_model,
                    policy_action=policy_action,
                    status="failed",
                    cost_basis="not_available",
                    currency=route.currency,
                    rate_card_version=route.rate_card_version,
                    redaction_count=redaction_count,
                    redaction_rules=redaction_rules,
                )
            self._send_protocol_error(
                protocol,
                f"Gateway upstream credential is unavailable: {upstream.api_key_env}",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="gateway_upstream_not_configured",
            )
            return
        request_url = self._upstream_url(upstream)
        headers = self._upstream_headers(protocol, upstream_key)
        request = urllib.request.Request(request_url, data=body, headers=headers, method="POST")

        try:
            response = urllib.request.urlopen(request, timeout=self.server.config.upstream_timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=route.upstream_model,
                    policy_action=policy_action,
                    status="failed",
                    cost_basis="not_available",
                    currency=route.currency,
                    rate_card_version=route.rate_card_version,
                    redaction_count=redaction_count,
                    redaction_rules=redaction_rules,
                )
            self._send_protocol_error(
                protocol,
                f"Upstream provider is unavailable: {error}",
                HTTPStatus.BAD_GATEWAY,
                code="gateway_upstream_error",
            )
            return

        status = getattr(response, "status", response.getcode())
        content_type = response.headers.get("Content-Type", "application/json")
        is_event_stream = "text/event-stream" in content_type.lower()
        parser = ResponseUsageParser(protocol, is_event_stream=is_event_stream)
        provider_request_id = response.headers.get("x-request-id") or response.headers.get("request-id")

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Hormuz-Policy-Decision", policy_action)
        self.send_header("X-Hormuz-Requested-Model", decision.requested_model)
        self.send_header("X-Hormuz-Routed-Model", route.upstream_model)
        if redaction_count:
            self.send_header("X-Hormuz-Redactions", str(redaction_count))
        if dlp_detection_count:
            self.send_header("X-Hormuz-DLP-Detections", str(dlp_detection_count))
        if approval_request_id is not None:
            self.send_header("X-Hormuz-DLP-Approval-Request", approval_request_id)
        for name, value in response.headers.items():
            lowered = name.lower()
            if lowered in {"x-request-id", "request-id", "openai-processing-ms"} or lowered.startswith(
                ("x-ratelimit-", "anthropic-ratelimit-")
            ):
                self.send_header(name, value)
        self.end_headers()
        self.close_connection = True

        downstream_ok = True
        refresh_at = time.monotonic() + max(1, reservation_ttl_seconds // 2)
        try:
            while True:
                chunk = response.read(16 * 1024)
                if not chunk:
                    break
                if reservation_id is not None and time.monotonic() >= refresh_at:
                    self.server.store.refresh_budget_reservation(
                        reservation_id,
                        ttl_seconds=reservation_ttl_seconds,
                    )
                    refresh_at = time.monotonic() + max(1, reservation_ttl_seconds // 2)
                parser.feed(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            downstream_ok = False
        except (TimeoutError, OSError) as error:
            downstream_ok = False
            LOGGER.warning(
                "upstream_stream_failed actor=%s team=%s client=%s protocol=%s requested_model=%s error=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                type(error).__name__,
            )
        finally:
            response.close()

        usage = parser.finish()
        if (
            approval_request_id is not None
            and usage.actual_model is not None
            and usage.actual_model != route.upstream_model
        ):
            try:
                self.server.store.record_dlp_approval_model_mismatch(
                    approval_request_id,
                    organization_id=identity.organization_id,
                    actual_model=usage.actual_model,
                )
            except (DLPApprovalStoreError, ValueError):
                LOGGER.critical(
                    "dlp_approval_model_mismatch_evidence_failed request_id=%s actor=%s organization=%s routed_model=%s",
                    approval_request_id,
                    identity.actor_id,
                    identity.organization_id,
                    route.upstream_model,
                )
            else:
                LOGGER.error(
                    "dlp_approval_model_mismatch request_id=%s actor=%s organization=%s routed_model=%s actual_model=%s",
                    approval_request_id,
                    identity.actor_id,
                    identity.organization_id,
                    route.upstream_model,
                    usage.actual_model,
                )
        if account_usage:
            successful = 200 <= status < 300 and downstream_ok
            cost = route.estimate_cost_microusd(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            self.server.store.record(
                identity=identity,
                client=client,
                protocol=protocol,
                requested_model=decision.requested_model,
                resolved_alias=decision.resolved_alias,
                upstream_model=route.upstream_model,
                actual_model=usage.actual_model,
                policy_action=policy_action,
                status="succeeded" if successful else "failed",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                billable_tokens=usage.billable_tokens,
                cost_microusd=cost,
                cost_basis="estimated",
                currency=route.currency,
                rate_card_version=route.rate_card_version,
                provider_usage=usage.provider_usage,
                provider_request_id=provider_request_id,
                redaction_count=redaction_count,
                redaction_rules=redaction_rules,
            )
            LOGGER.info(
                "request_complete actor=%s team=%s client=%s protocol=%s action=%s requested_model=%s routed_model=%s status=%s input_tokens=%d output_tokens=%d cost_microusd=%d redactions=%d",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                policy_action,
                decision.requested_model,
                route.upstream_model,
                "succeeded" if successful else "failed",
                usage.input_tokens,
                usage.output_tokens,
                cost,
                redaction_count,
            )

    def _authenticate(self) -> Identity | None:
        candidates: list[str] = []
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            candidates.append(authorization[7:].strip())
        api_key = self.headers.get("X-Api-Key", "").strip()
        if api_key:
            candidates.append(api_key)
        for candidate in candidates:
            if candidate.startswith("hox_a_") and self.server.session_broker is not None:
                try:
                    return self.server.session_broker.authenticate(candidate)
                except AuthenticationError as error:
                    LOGGER.info("authentication_denied reason=%s", error.code)
                    continue
            try:
                return self.server.authenticator.authenticate(candidate)
            except AuthenticationError as error:
                LOGGER.info("authentication_denied reason=%s", error.code)
        self._send_error(
            "unauthorized",
            "Missing or invalid Hormuz identity credential",
            HTTPStatus.UNAUTHORIZED,
            close_connection=True,
        )
        return None

    def _read_json_body(self, *, max_bytes: int | None = None) -> dict[str, Any] | None:
        content_length_value = self.headers.get("Content-Length")
        if content_length_value is None:
            self._send_error(
                "length_required",
                "Content-Length is required",
                HTTPStatus.LENGTH_REQUIRED,
                close_connection=True,
            )
            return None
        try:
            content_length = int(content_length_value)
        except ValueError:
            self._send_error(
                "invalid_content_length",
                "Content-Length must be an integer",
                HTTPStatus.BAD_REQUEST,
                close_connection=True,
            )
            return None
        request_limit = self.server.config.max_request_bytes
        if max_bytes is not None:
            request_limit = min(request_limit, max_bytes)
        if content_length < 0 or content_length > request_limit:
            self._send_error(
                "request_too_large",
                "Request body exceeds the allowed limit",
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                close_connection=True,
            )
            return None
        data = self.rfile.read(content_length)
        try:
            value = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            self._send_error("invalid_json", "Request body must be valid JSON", HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(value, dict):
            self._send_error("invalid_request", "Request body must be a JSON object", HTTPStatus.BAD_REQUEST)
            return None
        return value

    def _upstream_url(self, upstream: UpstreamConfig) -> str:
        request_parts = urlsplit(self.path)
        request_path = request_parts.path
        base = upstream.base_url
        if base.endswith("/v1") and request_path.startswith("/v1/"):
            request_path = request_path[3:]
        query = f"?{request_parts.query}" if request_parts.query else ""
        return f"{base}{request_path}{query}"

    def _upstream_headers(self, protocol: str, upstream_key: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "application/json"),
            "User-Agent": self.headers.get("User-Agent", "Hormuz/0.1.0"),
        }
        if protocol == "openai":
            headers["Authorization"] = f"Bearer {upstream_key}"
            for name in ("OpenAI-Beta",):
                value = self.headers.get(name)
                if value:
                    headers[name] = value
        else:
            headers["X-Api-Key"] = upstream_key
            headers["Anthropic-Version"] = self.headers.get("Anthropic-Version", "2023-06-01")
            beta = self.headers.get("Anthropic-Beta")
            if beta:
                headers["Anthropic-Beta"] = beta
        return headers

    def _send_protocol_error(
        self,
        protocol: str,
        message: str,
        status: HTTPStatus,
        *,
        code: str = "invalid_request",
        headers: dict[str, str] | None = None,
    ) -> None:
        if protocol == "anthropic":
            payload = {"type": "error", "error": {"type": "permission_error" if status == 403 else code, "message": message}}
        else:
            payload = {"error": {"message": message, "type": "policy_error" if status == 403 else code, "code": code}}
        self._send_json(status, payload, headers=headers)

    def _send_error(
        self,
        code: str,
        message: str,
        status: HTTPStatus,
        *,
        headers: dict[str, str] | None = None,
        close_connection: bool = False,
    ) -> None:
        response_headers = dict(headers or {})
        if close_connection:
            self.close_connection = True
            response_headers["Connection"] = "close"
        self._send_json(
            status,
            {"error": {"code": code, "message": message}},
            headers=response_headers,
        )

    def _send_json(
        self,
        status: HTTPStatus,
        value: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, header_value in (headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.debug("http " + format, *args)


def serve_in_thread(server: GatewayServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="hormuz", daemon=True)
    thread.start()
    return thread


def _valid_context_scope(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= 512
        and all(character.isprintable() for character in value)
    )


def _single_query_values(query: str) -> dict[str, str] | None:
    if len(query.encode("utf-8")) > 8192:
        return None
    try:
        parsed = urllib.parse.parse_qs(
            query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=8,
        )
    except (ValueError, UnicodeError):
        return None
    if any(len(values) != 1 for values in parsed.values()):
        return None
    values = {name: items[0] for name, items in parsed.items()}
    if any(
        not value
        or len(value.encode("utf-8")) > 4096
        or any(character in value for character in ("\n", "\r", "\x00"))
        for value in values.values()
    ):
        return None
    return values


def _safe_identifier(value: str) -> bool:
    return (
        20 <= len(value) <= 128
        and all(character.isalnum() or character in {"-", "_"} for character in value)
    )


def _approval_request_id_from_path(path: str) -> str | None:
    prefix = "/v1/dlp/approval-requests/"
    if not path.startswith(prefix):
        return None
    value = path[len(prefix):]
    if "/" in value or not _valid_approval_request_id(value):
        return None
    return value


def _approval_decision_id_from_path(path: str) -> str | None:
    prefix = "/v1/dlp/approval-requests/"
    suffix = "/decisions"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix):-len(suffix)]
    if "/" in value or not _valid_approval_request_id(value):
        return None
    return value


def _valid_approval_request_id(value: str) -> bool:
    return (
        value.startswith("apr_")
        and len(value) == 36
        and all(character in "0123456789abcdef" for character in value[4:])
    )
