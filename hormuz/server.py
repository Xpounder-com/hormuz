from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .auth import AuthenticationError, Authenticator
from .config import GatewayConfig, Identity, ModelRoute, UpstreamConfig
from .policy import PolicyDecision, PolicyEngine
from .redaction import RedactionError, SecretRedactor
from .store import ReservationDenied, UsageStore
from .usage import ResponseUsageParser


LOGGER = logging.getLogger("hormuz")


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: GatewayConfig):
        self.config = config
        self.authenticator = Authenticator(config)
        self.store = UsageStore(config.database_path)
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
        self.secret_redactor = SecretRedactor(config.secret_controls, tuple(protected_values))
        super().__init__((config.listen.host, config.listen.port), GatewayRequestHandler)


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server: GatewayServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "hormuz",
                    "protocols": ["openai-responses", "anthropic-messages"],
                },
            )
            return
        identity = self._authenticate()
        if identity is None:
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
                    "authentication_source": identity.authentication_source,
                },
            )
            return
        if path == "/v1/gateway/usage":
            totals = self.server.store.monthly_totals(actor_id=identity.actor_id)
            secret_totals = self.server.store.monthly_secret_totals(actor_id=identity.actor_id)
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
                },
            )
            return
        self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        routes = {
            "/v1/responses": ("openai", "codex", True),
            "/v1/responses/compact": ("openai", "codex", True),
            "/v1/messages": ("anthropic", "claude-code", True),
            "/v1/messages/count_tokens": ("anthropic", "claude-code", False),
        }
        route = routes.get(path)
        if route is None:
            self._send_error("not_found", "Route not found", HTTPStatus.NOT_FOUND)
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

        upstream = self.server.config.upstreams[protocol]
        is_responses_create = protocol == "openai" and urlsplit(self.path).path == "/v1/responses"
        if is_responses_create and request_body.get("background") is True and not upstream.allow_background:
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
            request_body["store"] = False

        request_body["model"] = decision.route.upstream_model
        if decision.max_output_tokens is not None:
            current_output = request_body.get(output_field)
            if current_output is None or current_output > decision.max_output_tokens:
                request_body[output_field] = decision.max_output_tokens
        try:
            redaction = self.server.secret_redactor.inspect(request_body)
        except RedactionError as error:
            self._send_protocol_error(protocol, str(error), HTTPStatus.BAD_REQUEST)
            return

        if redaction.count:
            self.server.store.record_secret_event(
                identity=identity,
                client=client,
                protocol=protocol,
                requested_model=decision.requested_model,
                action="denied" if self.server.config.secret_controls.mode == "deny" else "redacted",
                detection_count=redaction.count,
                rules=redaction.rules,
            )

        if redaction.count and self.server.config.secret_controls.mode == "deny":
            if account_usage:
                self.server.store.record(
                    identity=identity,
                    client=client,
                    protocol=protocol,
                    requested_model=decision.requested_model,
                    resolved_alias=decision.resolved_alias,
                    upstream_model=decision.route.upstream_model,
                    policy_action="secret_denied",
                    status="denied",
                    redaction_count=redaction.count,
                    redaction_rules=redaction.rules,
                )
            LOGGER.warning(
                "secret_egress_denied actor=%s team=%s client=%s protocol=%s requested_model=%s detections=%d rules=%s",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.requested_model,
                redaction.count,
                ",".join(redaction.rules),
            )
            self._send_protocol_error(
                protocol,
                "Request blocked because Hormuz detected protected secret material.",
                HTTPStatus.FORBIDDEN,
                code="hormuz_secret_detected",
            )
            return

        policy_action = decision.action
        if redaction.count:
            policy_action = f"{policy_action}+redacted"
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
                    redaction_count=redaction.count,
                    redaction_rules=redaction.rules,
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
                redaction_count=redaction.count,
                redaction_rules=redaction.rules,
                reservation_id=reservation_id,
                reservation_ttl_seconds=self.server.config.upstream_timeout_seconds + 60,
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
        reservation_id: str | None,
        reservation_ttl_seconds: int,
    ) -> None:
        route = decision.route
        assert route is not None
        upstream = self.server.config.upstreams[protocol]
        upstream_key = os.environ.get(upstream.api_key_env, "")
        if not upstream_key:
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
                policy_action=policy_action,
                status="succeeded" if successful else "failed",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cost_microusd=cost,
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
            try:
                return self.server.authenticator.authenticate(candidate)
            except AuthenticationError as error:
                LOGGER.info("authentication_denied reason=%s", error.code)
        self._send_error("unauthorized", "Missing or invalid Hormuz identity credential", HTTPStatus.UNAUTHORIZED)
        return None

    def _read_json_body(self) -> dict[str, Any] | None:
        content_length_value = self.headers.get("Content-Length")
        if content_length_value is None:
            self._send_error("length_required", "Content-Length is required", HTTPStatus.LENGTH_REQUIRED)
            return None
        try:
            content_length = int(content_length_value)
        except ValueError:
            self._send_error("invalid_content_length", "Content-Length must be an integer", HTTPStatus.BAD_REQUEST)
            return None
        if content_length < 0 or content_length > self.server.config.max_request_bytes:
            self._send_error("request_too_large", "Request body exceeds the configured limit", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
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
    ) -> None:
        if protocol == "anthropic":
            payload = {"type": "error", "error": {"type": "permission_error" if status == 403 else code, "message": message}}
        else:
            payload = {"error": {"message": message, "type": "policy_error" if status == 403 else code, "code": code}}
        self._send_json(status, payload)

    def _send_error(self, code: str, message: str, status: HTTPStatus) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _send_json(self, status: HTTPStatus, value: Any) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        LOGGER.debug("http " + format, *args)


def serve_in_thread(server: GatewayServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="hormuz", daemon=True)
    thread.start()
    return thread
