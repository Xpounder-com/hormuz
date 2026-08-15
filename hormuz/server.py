from __future__ import annotations

import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from .config import GatewayConfig, Identity, ModelRoute, UpstreamConfig
from .policy import PolicyDecision, PolicyEngine
from .store import UsageStore
from .usage import ResponseUsageParser


LOGGER = logging.getLogger("hormuz")


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: GatewayConfig):
        self.config = config
        self.store = UsageStore(config.database_path)
        self.policy_engine = PolicyEngine(config, self.store)
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
                    "allowed_clients": list(identity.allowed_clients),
                },
            )
            return
        if path == "/v1/gateway/usage":
            totals = self.server.store.monthly_totals(actor_id=identity.actor_id)
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

        request_body["model"] = decision.route.upstream_model
        if decision.max_output_tokens is not None:
            current_output = request_body.get(output_field)
            if current_output is None or current_output > decision.max_output_tokens:
                request_body[output_field] = decision.max_output_tokens
        body = json.dumps(request_body, separators=(",", ":")).encode("utf-8")
        self._forward(
            identity=identity,
            protocol=protocol,
            client=client,
            decision=decision,
            body=body,
            account_usage=account_usage,
        )

    def _forward(
        self,
        *,
        identity: Identity,
        protocol: str,
        client: str,
        decision: PolicyDecision,
        body: bytes,
        account_usage: bool,
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
                    policy_action=decision.action,
                    status="failed",
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
        self.send_header("X-Hormuz-Policy-Decision", decision.action)
        self.send_header("X-Hormuz-Requested-Model", decision.requested_model)
        self.send_header("X-Hormuz-Routed-Model", route.upstream_model)
        for name, value in response.headers.items():
            lowered = name.lower()
            if lowered in {"x-request-id", "request-id", "openai-processing-ms"} or lowered.startswith(
                ("x-ratelimit-", "anthropic-ratelimit-")
            ):
                self.send_header(name, value)
        self.end_headers()
        self.close_connection = True

        downstream_ok = True
        try:
            while True:
                chunk = response.read(16 * 1024)
                if not chunk:
                    break
                parser.feed(chunk)
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            downstream_ok = False
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
                policy_action=decision.action,
                status="succeeded" if successful else "failed",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cost_microusd=cost,
                provider_request_id=provider_request_id,
            )
            LOGGER.info(
                "request_complete actor=%s team=%s client=%s protocol=%s action=%s requested_model=%s routed_model=%s status=%s input_tokens=%d output_tokens=%d cost_microusd=%d",
                identity.actor_id,
                identity.team_id,
                client,
                protocol,
                decision.action,
                decision.requested_model,
                route.upstream_model,
                "succeeded" if successful else "failed",
                usage.input_tokens,
                usage.output_tokens,
                cost,
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
            for configured_token, identity in self.server.config.identities_by_token.items():
                if hmac.compare_digest(candidate, configured_token):
                    return identity
        self._send_error("unauthorized", "Missing or invalid Hormuz identity token", HTTPStatus.UNAUTHORIZED)
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
        except (json.JSONDecodeError, UnicodeDecodeError):
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
