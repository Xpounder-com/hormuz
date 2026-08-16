from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import urlsplit

from .auth import AuthenticationError, Authenticator
from .config import (
    GatewayConfig,
    Identity,
    ModelRoute,
    UpstreamConfig,
    is_context_selector,
)
from .context import (
    CLASSIFICATIONS,
    CONTEXT_RETRIEVAL_VERSION,
    ContextError,
    ContextPackRequest,
    ContextPrincipal,
    build_context_pack,
)
from .context_injection import (
    CONTEXT_INJECTION_RENDER_VERSION,
    ContextInjectionError,
    extract_user_query,
    inject_context_pack,
)
from .context_api import (
    ContextRevalidationBatchRequest,
    ContextSnapshotWriteRequest,
    context_evidence_result,
    context_revalidation_result,
    context_snapshot_result,
)
from .context_lifecycle import ContextEvidence, LifecyclePolicy
from .context_store import (
    ContextConflict,
    ContextNotFound,
    ContextStoreError,
    SQLiteContextRepository,
)
from .dlp_approval import DLPApprovalError, payload_fingerprint
from .policy import PolicyDecision, PolicyEngine
from .redaction import RedactionError, SecretRedactor
from .session import SessionBroker, SessionBrokerError
from .session_store import (
    SESSION_SECURITY_EVENT_TYPES,
    SQLiteSessionStore,
    SessionStoreError,
)
from .store import (
    ContextLineage,
    DLPApprovalStoreError,
    ReservationDenied,
    SecurityStoreError,
    UsageStore,
)
from .usage import ResponseUsageParser
from .usage_reporting import REPORT_DIMENSIONS, enrich_usage_rows


LOGGER = logging.getLogger("hormuz")
MAX_CONTEXT_REQUEST_BYTES = 64 * 1024
MAX_CONTEXT_SNAPSHOT_REQUEST_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_EVIDENCE_REQUEST_BYTES = 64 * 1024
MAX_CONTEXT_REVALIDATION_REQUEST_BYTES = 8 * 1024
MAX_AUTH_REQUEST_BYTES = 8 * 1024
MAX_DLP_APPROVAL_REQUEST_BYTES = 2 * 1024
MAX_PROVIDER_QUERY_DECODE_DEPTH = 3
_CONTEXT_SCOPE_HEADERS = (
    ("X-Hormuz-Repository", "repository_id"),
    ("X-Hormuz-Branch", "branch"),
    ("X-Hormuz-Revision", "revision"),
)


@dataclass(frozen=True)
class _AutomaticContextScope:
    repository_id: str | None = None
    branch: str | None = None
    revision: str | None = None
    error: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        if self.error is not None:
            return {}
        return {
            **(
                {"X-Hormuz-Repository": self.repository_id}
                if self.repository_id is not None
                else {}
            ),
            **(
                {"X-Hormuz-Branch": self.branch}
                if self.branch is not None
                else {}
            ),
            **(
                {"X-Hormuz-Revision": self.revision}
                if self.revision is not None
                else {}
            ),
        }


def _encode_usage_cursor(
    *,
    group_by: str,
    actor_id: str | None,
    team_id: str | None,
    window_end: str,
    offset: int,
) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "group_by": group_by,
            "actor_id": actor_id,
            "team_id": team_id,
            "window_end": window_end,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_usage_cursor(cursor: str) -> dict[str, object]:
    if len(cursor.encode("utf-8")) > 4096:
        raise ValueError("invalid cursor")
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = base64.b64decode(
            (cursor + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        value = json.loads(payload)
    except (ValueError, UnicodeError, binascii.Error, json.JSONDecodeError) as error:
        raise ValueError("invalid cursor") from error
    if not isinstance(value, dict) or set(value) != {
        "v",
        "group_by",
        "actor_id",
        "team_id",
        "window_end",
        "offset",
    }:
        raise ValueError("invalid cursor")
    if value["v"] != 1 or value["group_by"] not in REPORT_DIMENSIONS:
        raise ValueError("invalid cursor")
    if not isinstance(value["window_end"], str):
        raise ValueError("invalid cursor")
    if (
        isinstance(value["offset"], bool)
        or not isinstance(value["offset"], int)
        or not 0 <= value["offset"] <= 1_000_000
    ):
        raise ValueError("invalid cursor")
    for key in ("actor_id", "team_id"):
        item = value[key]
        if item is not None and (
            not isinstance(item, str)
            or not item
            or len(item.encode("utf-8")) > 256
            or any(character in item for character in ("\n", "\r", "\x00"))
        ):
            raise ValueError("invalid cursor")
    return value


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
        if path == "/v1/models":
            self._discover_claude_models(identity, request_url.query)
            return
        if path == "/v1/admin/sessions":
            self._list_human_sessions(identity, request_url.query)
            return
        if path == "/v1/admin/session-events":
            self._list_session_security_events(identity, request_url.query)
            return
        if path == "/v1/admin/usage":
            self._list_usage_report(identity, request_url.query)
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
            totals = self.server.store.monthly_totals(
                organization_id=identity.organization_id,
                actor_id=identity.actor_id,
            )
            secret_totals = self.server.store.monthly_secret_totals(
                organization_id=identity.organization_id,
                actor_id=identity.actor_id,
            )
            approval_totals = self.server.store.monthly_dlp_approval_totals(
                organization_id=identity.organization_id,
                actor_id=identity.actor_id,
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

    def _discover_claude_models(self, identity: Identity, query: str) -> None:
        try:
            values = urllib.parse.parse_qs(
                query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=1,
            )
        except (UnicodeDecodeError, ValueError):
            self._send_model_discovery_error()
            return
        if values != {"limit": ["1000"]}:
            self._send_model_discovery_error()
            return

        decision = self.server.policy_engine.model_catalog(
            identity=identity,
            client="claude-code",
            protocol="anthropic",
        )
        if not decision.allowed:
            LOGGER.info(
                "model_discovery_denied actor=%s team=%s client=claude-code reason=%s",
                identity.actor_id,
                identity.team_id,
                decision.reason,
            )
            self._send_error(
                "hormuz_model_discovery_denied",
                decision.reason,
                HTTPStatus.FORBIDDEN,
            )
            return

        aliases = tuple(
            alias
            for alias in decision.aliases
            if "claude" in alias.casefold() or "anthropic" in alias.casefold()
        )[:1000]
        LOGGER.info(
            "model_discovery_complete actor=%s team=%s client=claude-code count=%d",
            identity.actor_id,
            identity.team_id,
            len(aliases),
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "data": [
                    {"id": alias, "display_name": alias}
                    for alias in aliases
                ]
            },
        )

    def _send_model_discovery_error(self) -> None:
        self._send_error(
            "invalid_model_discovery_request",
            "Claude Code model discovery requires exactly limit=1000",
            HTTPStatus.BAD_REQUEST,
        )

    def _list_usage_report(self, identity: Identity, query: str) -> None:
        if "usage_viewer" not in identity.capabilities:
            self._send_error(
                "usage_viewer_capability_required",
                "Usage viewer capability is required",
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            values = urllib.parse.parse_qs(
                query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=5,
            )
        except ValueError:
            self._send_usage_report_error()
            return
        allowed = {"group_by", "actor_id", "team_id", "cursor", "limit"}
        if set(values) - allowed or any(
            len(items) != 1 or not items[0] for items in values.values()
        ):
            self._send_usage_report_error()
            return
        group_by = values.get("group_by", [None])[0]
        actor_id = values.get("actor_id", [None])[0]
        team_id = values.get("team_id", [None])[0]
        cursor = values.get("cursor", [None])[0]
        try:
            limit = int(values.get("limit", ["50"])[0])
            if group_by not in REPORT_DIMENSIONS or not 1 <= limit <= 100:
                raise ValueError
            for scope_filter in (actor_id, team_id):
                if scope_filter is not None and (
                    len(scope_filter.encode("utf-8")) > 256
                    or any(character in scope_filter for character in ("\n", "\r", "\x00"))
                ):
                    raise ValueError
            if cursor is None:
                window_end = datetime.now(timezone.utc)
                offset = 0
            else:
                cursor_value = _decode_usage_cursor(cursor)
                if (
                    cursor_value["group_by"] != group_by
                    or cursor_value["actor_id"] != actor_id
                    or cursor_value["team_id"] != team_id
                ):
                    raise ValueError
                window_end = datetime.fromisoformat(str(cursor_value["window_end"]))
                offset = int(cursor_value["offset"])
                if window_end.tzinfo is None or window_end.utcoffset() is None:
                    raise ValueError
                window_end = window_end.astimezone(timezone.utc)
                if window_end > datetime.now(timezone.utc) + timedelta(seconds=5):
                    raise ValueError
            window_start = window_end.replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        except (ValueError, TypeError, OverflowError):
            self._send_usage_report_error()
            return
        try:
            raw_rows = self.server.store.report_rows(
                group_by=str(group_by),
                organization_id=identity.organization_id,
                actor_id=actor_id,
                team_id=team_id,
                start=window_start.isoformat(),
                end=window_end.isoformat(),
                limit=limit + 1,
                offset=offset,
            )
            has_more = len(raw_rows) > limit
            rows = enrich_usage_rows(
                self.server.config,
                raw_rows[:limit],
                group_by=str(group_by),
                actor_filter=actor_id,
                team_filter=team_id,
            )
            self.server.store.record_admin_usage_read(
                administrator=identity,
                group_by=str(group_by),
                actor_filter=actor_id,
                team_filter=team_id,
                window_start=window_start.isoformat(),
                window_end=window_end.isoformat(),
                result_count=len(rows),
            )
        except (ValueError, sqlite3.Error, SecurityStoreError):
            self._send_error(
                "usage_admin_unavailable",
                "Usage administration is temporarily unavailable",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        next_cursor = None
        if has_more:
            next_cursor = _encode_usage_cursor(
                group_by=str(group_by),
                actor_id=actor_id,
                team_id=team_id,
                window_end=window_end.isoformat(),
                offset=offset + len(rows),
            )
        LOGGER.info(
            "usage_admin_read organization=%s decision_actor=%s group_by=%s rows=%d",
            identity.organization_id,
            identity.actor_id,
            group_by,
            len(rows),
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "schema_version": 2,
                "organization_id": identity.organization_id,
                "group_by": group_by,
                "filters": {"actor_id": actor_id, "team_id": team_id},
                "window": {
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "timezone": "UTC",
                },
                "coverage": {
                    "scope": "gateway_captured_requests_only",
                    "legacy_unattributed_rows_excluded": True,
                    "provider_invoice_reconciled": False,
                },
                "rows": rows,
                "next_cursor": next_cursor,
            },
        )

    def _send_usage_report_error(self) -> None:
        self._send_error(
            "invalid_usage_report_request",
            "Usage report query is invalid",
            HTTPStatus.BAD_REQUEST,
        )

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
        if path == "/v1/context/evidence":
            identity = self._authenticate()
            if identity is None:
                return
            policy = self._context_promoter_policy(identity)
            if policy is None or not self._check_context_rate_limit(identity):
                return
            self._create_context_evidence(identity, policy)
            return
        if path == "/v1/context/revalidation-batches":
            identity = self._authenticate()
            if identity is None:
                return
            policy = self._context_promoter_policy(identity)
            if policy is None or not self._check_context_rate_limit(identity):
                return
            self._run_context_revalidation_batch(identity, policy)
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

    def do_PUT(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path != "/v1/context/lifecycle-snapshots":
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
        policy = self._context_promoter_policy(identity)
        if policy is None or not self._check_context_rate_limit(identity):
            return
        self._put_context_lifecycle_snapshot(identity, policy)

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

    def _list_session_security_events(self, identity: Identity, query: str) -> None:
        broker = self._require_session_broker()
        if broker is None:
            return
        try:
            values = urllib.parse.parse_qs(
                query,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=6,
            )
        except ValueError:
            self._send_error(
                "invalid_session_event_request",
                "Session event query is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        allowed = {"actor_id", "team_id", "event_type", "since", "cursor", "limit"}
        if set(values) - allowed or any(
            len(items) != 1 or not items[0] for items in values.values()
        ):
            self._send_error(
                "invalid_session_event_request",
                "Session event query contains unsupported or repeated fields",
                HTTPStatus.BAD_REQUEST,
            )
            return
        event_type = values.get("event_type", [None])[0]
        if event_type is not None and event_type not in SESSION_SECURITY_EVENT_TYPES:
            self._send_error(
                "invalid_session_event_request",
                "Session event type is unsupported",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            limit = int(values.get("limit", ["50"])[0])
            since_value = values.get("since", [None])[0]
            since = _parse_utc_filter(since_value) if since_value is not None else None
        except (ValueError, TypeError, OverflowError):
            self._send_error(
                "invalid_session_event_request",
                "Session event query values are invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            events, next_cursor = broker.list_security_events(
                administrator=identity,
                limit=limit,
                cursor=values.get("cursor", [None])[0],
                actor_id=values.get("actor_id", [None])[0],
                team_id=values.get("team_id", [None])[0],
                event_type=event_type,
                since=since,
            )
        except SessionBrokerError as error:
            self._send_session_event_error(error)
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "schema_version": 1,
                "events": [event.to_dict() for event in events],
                "next_cursor": next_cursor,
            },
        )

    def _send_session_event_error(self, error: SessionBrokerError) -> None:
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
            "invalid_session_event_type",
            "invalid_session_event_since",
        }
        if error.code in invalid:
            self._send_error(
                "invalid_session_event_request",
                "Session event query is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_error(
            "session_admin_unavailable",
            "Session administration is temporarily unavailable",
            HTTPStatus.SERVICE_UNAVAILABLE,
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

    def _create_context_evidence(
        self,
        identity: Identity,
        policy: LifecyclePolicy,
    ) -> None:
        request_body = self._read_json_body(
            max_bytes=MAX_CONTEXT_EVIDENCE_REQUEST_BYTES,
            strict=True,
        )
        if request_body is None:
            return
        try:
            evidence = ContextEvidence.from_dict(request_body)
        except ValueError:
            self._send_error(
                "context_lifecycle_invalid_request",
                "Context lifecycle request is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if evidence.organization_id != identity.organization_id:
            self._send_error(
                "context_lifecycle_scope_denied",
                "Context lifecycle organization exceeds the authenticated identity",
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            result = self.server.context_repository.record_lifecycle_evidence(
                evidence,
                actor_id=identity.actor_id,
                policy_version=policy.policy_version,
            )
        except ContextConflict:
            self._send_error(
                "context_lifecycle_conflict",
                "Context lifecycle evidence conflicts with current governed state",
                HTTPStatus.CONFLICT,
            )
            return
        except ContextStoreError:
            self._send_context_lifecycle_store_error(identity, "evidence")
            return
        LOGGER.info(
            "context_evidence_recorded actor=%s organization=%s record=%s signal=%s created=%s",
            identity.actor_id,
            identity.organization_id,
            evidence.record_id,
            evidence.signal,
            result.created,
        )
        self._send_json(
            HTTPStatus.CREATED if result.created else HTTPStatus.OK,
            context_evidence_result(result),
        )

    def _put_context_lifecycle_snapshot(
        self,
        identity: Identity,
        policy: LifecyclePolicy,
    ) -> None:
        request_body = self._read_json_body(
            max_bytes=MAX_CONTEXT_SNAPSHOT_REQUEST_BYTES,
            strict=True,
        )
        if request_body is None:
            return
        try:
            request = ContextSnapshotWriteRequest.from_dict(request_body)
        except ContextError:
            self._send_error(
                "context_lifecycle_invalid_request",
                "Context lifecycle request is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        if request.organization_id != identity.organization_id:
            self._send_error(
                "context_lifecycle_scope_denied",
                "Context lifecycle organization exceeds the authenticated identity",
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            stored = self.server.context_repository.observe_lifecycle_snapshot(
                organization_id=identity.organization_id,
                repository_id=request.repository_id,
                branch=request.branch,
                snapshot=request.snapshot,
                expected_version=request.expected_version,
                actor_id=identity.actor_id,
                policy_version=policy.policy_version,
            )
        except ContextConflict:
            self._send_error(
                "context_lifecycle_conflict",
                "Context lifecycle snapshot conflicts with the current version",
                HTTPStatus.CONFLICT,
            )
            return
        except ContextStoreError:
            self._send_context_lifecycle_store_error(identity, "snapshot")
            return
        LOGGER.info(
            "context_snapshot_recorded actor=%s organization=%s repository=%s branch=%s version=%d artifacts=%d",
            identity.actor_id,
            identity.organization_id,
            request.repository_id,
            request.branch,
            stored.version,
            len(stored.snapshot.artifacts),
        )
        self._send_json(HTTPStatus.OK, context_snapshot_result(stored))

    def _run_context_revalidation_batch(
        self,
        identity: Identity,
        policy: LifecyclePolicy,
    ) -> None:
        request_body = self._read_json_body(
            max_bytes=MAX_CONTEXT_REVALIDATION_REQUEST_BYTES,
            strict=True,
        )
        if request_body is None:
            return
        try:
            request = ContextRevalidationBatchRequest.from_dict(request_body)
        except ContextError:
            self._send_error(
                "context_lifecycle_invalid_request",
                "Context lifecycle request is invalid",
                HTTPStatus.BAD_REQUEST,
            )
            return
        lifecycle = self.server.config.context_service.lifecycle
        batch_size = request.batch_size or lifecycle.job_batch_size
        if batch_size > lifecycle.job_batch_size:
            self._send_error(
                "context_lifecycle_policy_denied",
                "Requested revalidation batch exceeds organization policy",
                HTTPStatus.FORBIDDEN,
            )
            return
        try:
            job = self.server.context_repository.start_revalidation_job(
                organization_id=identity.organization_id,
                repository_id=request.repository_id,
                branch=request.branch,
                policy=policy,
                actor_id=identity.actor_id,
            )
            result = self.server.context_repository.run_revalidation_batch(
                job_id=job.job_id,
                policy=policy,
                actor_id=identity.actor_id,
                batch_size=batch_size,
                lease_seconds=lifecycle.lease_seconds,
            )
        except (ContextConflict, ContextNotFound):
            self._send_error(
                "context_lifecycle_conflict",
                "Context revalidation cannot run against the current governed state",
                HTTPStatus.CONFLICT,
            )
            return
        except ContextStoreError:
            self._send_context_lifecycle_store_error(identity, "revalidation")
            return
        LOGGER.info(
            "context_revalidation_batch actor=%s organization=%s repository=%s branch=%s job=%s status=%s processed=%d total=%d",
            identity.actor_id,
            identity.organization_id,
            request.repository_id,
            request.branch,
            result.job_id,
            result.status,
            result.processed_records,
            result.total_records,
        )
        self._send_json(HTTPStatus.OK, context_revalidation_result(result))

    def _context_promoter_policy(self, identity: Identity) -> LifecyclePolicy | None:
        if "context_promoter" not in identity.capabilities:
            self._send_error(
                "context_promotion_forbidden",
                "Context promoter capability is required",
                HTTPStatus.FORBIDDEN,
                close_connection=True,
            )
            return None
        lifecycle = self.server.config.context_service.lifecycle
        if not lifecycle.enabled or lifecycle.policy is None:
            self._send_error(
                "context_lifecycle_disabled",
                "Context lifecycle automation is disabled",
                HTTPStatus.FORBIDDEN,
                close_connection=True,
            )
            return None
        return lifecycle.policy

    def _check_context_rate_limit(self, identity: Identity) -> bool:
        retry_after = self.server.context_rate_limiter.check(
            organization_id=identity.organization_id,
            actor_id=identity.actor_id,
        )
        if retry_after is None:
            return True
        self._send_error(
            "context_rate_limited",
            "Context request limit exceeded",
            HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
            close_connection=True,
        )
        return False

    def _send_context_lifecycle_store_error(
        self,
        identity: Identity,
        operation: str,
    ) -> None:
        LOGGER.error(
            "context_lifecycle_store_failed actor=%s organization=%s operation=%s",
            identity.actor_id,
            identity.organization_id,
            operation,
        )
        self._send_error(
            "context_store_unavailable",
            "Governed context is temporarily unavailable",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, PUT, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _automatic_context_scope(
        self,
        allowed_repositories: tuple[str, ...] | None,
    ) -> _AutomaticContextScope:
        selected: dict[str, str] = {}
        for header_name, field_name in _CONTEXT_SCOPE_HEADERS:
            values = self.headers.get_all(header_name, [])
            if len(values) > 1:
                return _AutomaticContextScope(error="repository_selector_ambiguous")
            if not values:
                continue
            value = values[0]
            if not is_context_selector(value):
                return _AutomaticContextScope(error="repository_selector_invalid")
            selected[field_name] = value
        repository_id = selected.get("repository_id")
        branch = selected.get("branch")
        revision = selected.get("revision")
        if repository_id is None and (branch is not None or revision is not None):
            return _AutomaticContextScope(error="repository_selector_invalid")
        if revision is not None and branch is None:
            return _AutomaticContextScope(error="repository_selector_invalid")
        if repository_id is not None and (
            allowed_repositories is None or repository_id not in allowed_repositories
        ):
            return _AutomaticContextScope(error="repository_not_granted")
        return _AutomaticContextScope(
            repository_id=repository_id,
            branch=branch,
            revision=revision,
        )

    def _prepare_automatic_context(
        self,
        *,
        identity: Identity,
        protocol: str,
        client: str,
        resolved_alias: str,
        operation: str,
        request_body: dict[str, Any],
    ) -> tuple[dict[str, Any], ContextLineage, dict[str, str]]:
        injection = self.server.config.resolved_policy(identity).context_injection
        mode = injection.mode or "off"
        if mode == "off":
            return request_body, ContextLineage(), {}
        base = {
            "mode": mode,
            "policy_version": self.server.config.context_service.policy_version,
        }
        if injection.allowed_clients is not None and client not in injection.allowed_clients:
            return request_body, ContextLineage(
                **base,
                outcome="not_injected",
                reason="client_not_enabled",
            ), {}
        if injection.allowed_models is not None and resolved_alias not in injection.allowed_models:
            return request_body, ContextLineage(
                **base,
                outcome="not_injected",
                reason="model_not_enabled",
            ), {}
        if operation not in {
            "/v1/responses",
            "/v1/messages",
            "/v1/messages/count_tokens",
        }:
            return request_body, ContextLineage(
                **base,
                outcome="not_injected",
                reason="unsupported_operation",
            ), {}
        query = extract_user_query(protocol, request_body)
        if query is None:
            return request_body, ContextLineage(
                **base,
                outcome="denied" if mode == "required" else "not_injected",
                reason="no_eligible_query",
            ), {}

        scope = self._automatic_context_scope(injection.allowed_repositories)
        if scope.error is not None and mode == "required":
            return request_body, ContextLineage(
                **base,
                outcome="denied",
                reason=scope.error,
            ), {}

        service = self.server.config.context_service
        token_budget = min(
            service.max_token_budget,
            injection.token_budget or service.max_token_budget,
        )
        max_items = min(
            service.max_items,
            injection.max_items or service.max_items,
        )
        started = time.monotonic()
        as_of = datetime.now(timezone.utc)
        clearance = identity.clearance
        if injection.max_classification is not None:
            clearance = CLASSIFICATIONS[
                min(
                    CLASSIFICATIONS.index(clearance),
                    CLASSIFICATIONS.index(injection.max_classification),
                )
            ]
        principal = ContextPrincipal(
            organization_id=identity.organization_id,
            team_id=identity.team_id,
            actor_id=identity.actor_id,
            clearance=clearance,
            repository_id=scope.repository_id if scope.error is None else None,
            branch=scope.branch if scope.error is None else None,
        )
        lifecycle_snapshot = None
        scope_failure = scope.error
        if principal.repository_id is not None and principal.branch is not None:
            stored_snapshot = self.server.context_repository.get_lifecycle_snapshot(
                organization_id=principal.organization_id,
                repository_id=principal.repository_id,
                branch=principal.branch,
            )
            if scope.revision is not None:
                if stored_snapshot is None:
                    scope_failure = "repository_revision_unverified"
                elif stored_snapshot.snapshot.repository_revision != scope.revision:
                    scope_failure = "repository_revision_mismatch"
            if scope_failure is None and stored_snapshot is not None:
                lifecycle_snapshot = stored_snapshot.snapshot
        if scope_failure is not None and principal.repository_id is not None:
            if mode == "required":
                return request_body, ContextLineage(
                    **base,
                    outcome="denied",
                    reason=scope_failure,
                ), scope.headers
            principal = ContextPrincipal(
                organization_id=identity.organization_id,
                team_id=identity.team_id,
                actor_id=identity.actor_id,
                clearance=clearance,
            )
            lifecycle_snapshot = None
        pack_request = ContextPackRequest(
            query=query,
            principal=principal,
            token_budget=token_budget,
            policy_version=service.policy_version,
            max_items=max_items,
            include_provisional=False,
            as_of=as_of,
        )
        stored = self.server.context_repository.list_access_authorized(principal)
        pack = build_context_pack(
            (item.record for item in stored),
            pack_request,
            lifecycle_snapshot=lifecycle_snapshot,
        )
        # The same authorization-first read boundary applies to automatic
        # injection: no selected content can leave Hormuz unless this audit
        # commit succeeds.
        self.server.context_repository.record_pack_read(pack, occurred_at=as_of)
        elapsed = max(0, round((time.monotonic() - started) * 1000))
        pack_lineage = {
            **base,
            "pack_id": pack.pack_id,
            "record_ids": tuple(item.record.record_id for item in pack.items),
            "retrieval_version": CONTEXT_RETRIEVAL_VERSION,
            "repository_revision": pack.repository_revision,
            "assembly_milliseconds": elapsed,
        }
        if not pack.items:
            return request_body, ContextLineage(
                **pack_lineage,
                outcome="denied" if mode == "required" else "not_injected",
                reason=scope_failure or "empty_pack",
                reuse_status="fresh",
            ), scope.headers
        try:
            rendered = inject_context_pack(protocol, request_body, pack)
        except ContextInjectionError as error:
            return request_body, ContextLineage(
                **pack_lineage,
                outcome="denied" if mode == "required" else "not_injected",
                reason=error.code,
                reuse_status="fresh",
            ), scope.headers
        lineage = ContextLineage(
            **pack_lineage,
            outcome="injected",
            reason=(
                "authorized_pack_already_present"
                if rendered.already_present
                else "pack_injected"
            ),
            render_version=CONTEXT_INJECTION_RENDER_VERSION,
            estimated_tokens=rendered.estimated_tokens,
            reuse_status="already_present" if rendered.already_present else "fresh",
        )
        LOGGER.info(
            "context_injection_ready actor=%s team=%s organization=%s client=%s protocol=%s pack_id=%s selected=%d outcome=%s estimated_tokens=%d assembly_ms=%d",
            identity.actor_id,
            identity.team_id,
            identity.organization_id,
            client,
            protocol,
            pack.pack_id,
            len(pack.items),
            pack.outcome,
            rendered.estimated_tokens,
            elapsed,
        )
        return rendered.body, lineage, scope.headers

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
            request_body, context_lineage, context_scope_headers = self._prepare_automatic_context(
                identity=identity,
                protocol=protocol,
                client=client,
                resolved_alias=decision.resolved_alias or requested_model,
                operation=urlsplit(self.path).path,
                request_body=request_body,
            )
        except (ContextError, ContextStoreError, sqlite3.Error):
            mode = (
                self.server.config.resolved_policy(identity).context_injection.mode
                or "off"
            )
            context_lineage = ContextLineage(
                mode=mode,
                outcome="denied",
                reason="context_store_unavailable",
                policy_version=self.server.config.context_service.policy_version,
            )
            context_scope_headers = {}
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="context_unavailable",
                    status="denied",
                    cost_basis="not_applicable",
                    currency=decision.route.currency,
                    rate_card_version=decision.route.rate_card_version,
                    context_lineage=context_lineage,
                )
            LOGGER.error(
                "context_injection_unavailable actor=%s team=%s organization=%s client=%s protocol=%s",
                identity.actor_id,
                identity.team_id,
                identity.organization_id,
                client,
                protocol,
            )
            self._send_protocol_error(
                protocol,
                "Governed context is temporarily unavailable.",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="hormuz_context_unavailable",
            )
            return
        if context_lineage.outcome == "denied":
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="context_required_denied",
                    status="denied",
                    cost_basis="not_applicable",
                    currency=decision.route.currency,
                    rate_card_version=decision.route.rate_card_version,
                    context_lineage=context_lineage,
                )
            LOGGER.info(
                "context_injection_denied actor=%s team=%s organization=%s client=%s protocol=%s reason=%s",
                identity.actor_id,
                identity.team_id,
                identity.organization_id,
                client,
                protocol,
                context_lineage.reason,
            )
            self._send_protocol_error(
                protocol,
                "Organization policy requires authorized governed context for this request.",
                HTTPStatus.FORBIDDEN,
                code="hormuz_context_required",
            )
            return
        forwarded_headers = self._forwarded_client_headers(protocol)
        forwarded_query = urlsplit(self.path).query
        try:
            query_strings = _provider_query_inspection_strings(forwarded_query)
            redaction = self.server.redactor_for(
                identity,
                protocol=protocol,
                model=decision.route.upstream_model,
            ).inspect(
                request_body,
                protocol=protocol,
                model=decision.route.upstream_model,
                unredactable_strings=(
                    *forwarded_headers.values(),
                    *context_scope_headers.values(),
                ),
                unredactable_string_groups=(query_strings,) if query_strings else (),
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
                    approval_material = {
                        "operation": urlsplit(self.path).path,
                        "payload": redaction.value,
                        "provider_headers": forwarded_headers,
                    }
                    if context_scope_headers:
                        approval_material["context_scope_headers"] = context_scope_headers
                    if forwarded_query:
                        approval_material["provider_query"] = forwarded_query
                    fingerprint = payload_fingerprint(
                        approval_material,
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
                    context_lineage=context_lineage,
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
                    context_lineage=context_lineage,
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
                    context_lineage=context_lineage,
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
        if context_lineage.outcome == "injected":
            policy_action = f"{policy_action}+context-injected"
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
                    context_lineage=context_lineage,
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
                forwarded_headers=forwarded_headers,
                forwarded_query=forwarded_query,
                account_usage=account_usage,
                policy_action=policy_action,
                redaction_count=redaction.redaction_count,
                redaction_rules=redaction_rules,
                dlp_detection_count=dlp_detection_count,
                reservation_id=reservation_id,
                reservation_ttl_seconds=self.server.config.upstream_timeout_seconds + 60,
                approval_request_id=(approval_request_id if approval_authorized else None),
                context_lineage=context_lineage,
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
        forwarded_headers: dict[str, str],
        forwarded_query: str,
        account_usage: bool,
        policy_action: str,
        redaction_count: int,
        redaction_rules: tuple[str, ...],
        dlp_detection_count: int,
        reservation_id: str | None,
        reservation_ttl_seconds: int,
        approval_request_id: str | None,
        context_lineage: ContextLineage,
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
                    context_lineage=context_lineage,
                )
            self._send_protocol_error(
                protocol,
                f"Gateway upstream credential is unavailable: {upstream.api_key_env}",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="gateway_upstream_not_configured",
            )
            return
        request_url = self._upstream_url(upstream, forwarded_query)
        headers = self._upstream_headers(
            protocol,
            upstream_key,
            forwarded_headers,
        )
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
                    context_lineage=context_lineage,
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
        if context_lineage.outcome == "injected" and context_lineage.pack_id is not None:
            self.send_header("X-Hormuz-Context-Pack", context_lineage.pack_id)
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
                context_lineage=context_lineage,
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

    def _read_json_body(
        self,
        *,
        max_bytes: int | None = None,
        strict: bool = False,
    ) -> dict[str, Any] | None:
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
            value = json.loads(
                data,
                parse_constant=_reject_json_constant if strict else None,
                object_pairs_hook=_unique_json_object if strict else None,
            )
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError, ValueError):
            self._send_error("invalid_json", "Request body must be valid JSON", HTTPStatus.BAD_REQUEST)
            return None
        if not isinstance(value, dict):
            self._send_error("invalid_request", "Request body must be a JSON object", HTTPStatus.BAD_REQUEST)
            return None
        return value

    def _upstream_url(self, upstream: UpstreamConfig, forwarded_query: str) -> str:
        request_parts = urlsplit(self.path)
        request_path = request_parts.path
        base = upstream.base_url
        if base.endswith("/v1") and request_path.startswith("/v1/"):
            request_path = request_path[3:]
        query = f"?{forwarded_query}" if forwarded_query else ""
        return f"{base}{request_path}{query}"

    def _forwarded_client_headers(self, protocol: str) -> dict[str, str]:
        headers = {
            "Accept": self.headers.get("Accept", "application/json"),
            "User-Agent": self.headers.get("User-Agent", "Hormuz/0.1.0"),
        }
        if protocol == "openai":
            beta = self.headers.get("OpenAI-Beta")
            if beta:
                headers["OpenAI-Beta"] = beta
        else:
            headers["Anthropic-Version"] = self.headers.get("Anthropic-Version", "2023-06-01")
            beta = self.headers.get("Anthropic-Beta")
            if beta:
                headers["Anthropic-Beta"] = beta
        return headers

    @staticmethod
    def _upstream_headers(
        protocol: str,
        upstream_key: str,
        forwarded_headers: dict[str, str],
    ) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            **forwarded_headers,
        }
        if protocol == "openai":
            headers["Authorization"] = f"Bearer {upstream_key}"
        else:
            headers["X-Api-Key"] = upstream_key
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


def _reject_json_constant(_value: str) -> object:
    raise ValueError("non-standard JSON numeric constant")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object member")
        value[key] = item
    return value


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


def _provider_query_inspection_strings(query: str) -> tuple[str, ...]:
    if not query:
        return ()
    views: list[str] = []
    current = query
    for depth in range(1, MAX_PROVIDER_QUERY_DECODE_DEPTH + 1):
        try:
            decoded = (
                urllib.parse.unquote_plus(
                    current,
                    encoding="utf-8",
                    errors="strict",
                )
                if depth == 1
                else urllib.parse.unquote(
                    current,
                    encoding="utf-8",
                    errors="strict",
                )
            )
        except UnicodeDecodeError as error:
            raise RedactionError(
                "Provider query percent-encoding must decode to valid UTF-8"
            ) from error
        if not views or decoded != views[-1]:
            views.append(decoded)
        if decoded == current:
            return tuple(views)
        current = decoded

    try:
        next_view = urllib.parse.unquote(
            current,
            encoding="utf-8",
            errors="strict",
        )
    except UnicodeDecodeError as error:
        raise RedactionError(
            "Provider query percent-encoding must decode to valid UTF-8"
        ) from error
    if next_view != current:
        raise RedactionError(
            "Provider query exceeds the maximum nested percent-decoding depth of "
            f"{MAX_PROVIDER_QUERY_DECODE_DEPTH}"
        )
    return tuple(views)


def _safe_identifier(value: str) -> bool:
    return (
        20 <= len(value) <= 128
        and all(character.isalnum() or character in {"-", "_"} for character in value)
    )


def _parse_utc_filter(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


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
