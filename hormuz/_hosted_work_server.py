"""Bounded private-hop adapter for the existing full SQLite gateway services."""
from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from urllib.parse import urlsplit

from ._hosted_config import HostedError
from ._hosted_provider import deployment_metadata
from ._hosted_server import (
    PROVIDER_CONNECTION_LIFETIME_MARGIN, PROVIDER_MAX_CONNECTIONS,
    PROVIDER_MAX_INFERENCE_CONNECTIONS, ProviderPilotGatewayServer,
    StagingRequestHandler, _ai_work_route,
)
from ._hosted_state import _private, check_initialized
from ._hosted_work import check_work_stores
from .portfolio_wire import PREFIX as PORTFOLIO_PREFIX, PortfolioError, route as portfolio_route
from .server import GatewayRequestHandler, GatewayServer
from .session_http import _DESKTOP_REDEEM, _REDEEM
from .work_recovery import owned_databases

CONNECTOR_PATHS = frozenset({"/v1/connectors/github/events", "/v1/connectors/linear/events", "/v1/connectors/linear/snapshots"})
WORKSPACE_PATHS = frozenset({
    "/workspace", "/workspace/", "/workspace/styles.css", "/workspace/login",
    "/v1/workspaces/me", "/v1/workspaces/auth/start", "/v1/workspaces/auth/callback",
    "/v1/workspaces/auth/handoff", "/v1/workspaces/logout", "/v1/workspaces/domains",
    "/v1/workspaces/domains/check", "/v1/workspaces/domains/remove",
})
AUTH_PATHS = frozenset({
    "/v1/auth/login", "/v1/auth/join", "/v1/auth/invitations/accept", "/v1/auth/callback",
    "/v1/auth/enrollments", "/v1/auth/desktop/enrollments", "/v1/auth/desktop/profile",
    "/v1/auth/refresh", "/v1/auth/logout", "/v1/admin/me", "/v1/admin/usage",
    "/v1/admin/operations", "/v1/admin/auth/callback", "/v1/admin/auth/start", "/v1/admin/logout",
    "/v1/admin/members", "/v1/admin/teams", "/v1/admin/members/disable",
})
GATEWAY_PATHS = frozenset({
    "/health", "/ready", "/console", "/console/styles.css", "/v1/gateway/whoami",
    "/v1/gateway/reliability", "/v1/gateway/usage", "/v1/responses", "/v1/responses/compact",
    "/v1/messages", "/v1/messages/count_tokens", "/v1/models", "/v1/chat/completions",
})
POLICY_PATHS = frozenset({"/console/policy", "/console/policy.css", "/console/policy/activity",
                          "/console/policy/results", "/v1/admin/policy/scopes", "/v1/admin/policy/results"})
AI_WORK_HEALTH_PROBE_CONNECTIONS = 2
AI_WORK_HEALTH_PROBE_TIMEOUT = 2
AI_WORK_MAX_CONNECTIONS = PROVIDER_MAX_CONNECTIONS + AI_WORK_HEALTH_PROBE_CONNECTIONS


def workspace_route(path):
    return path in WORKSPACE_PATHS or re.fullmatch(r"/w/workspace-[a-f0-9]{16}", path) is not None or re.fullmatch(
        r"/\.well-known/hormuz-domain/[A-Za-z0-9_-]{1,128}", path) is not None


def allowed_route(method, path):
    if path.startswith(PORTFOLIO_PREFIX):
        try:
            portfolio_route(method, path)
            return True
        except PortfolioError:
            return False
    if _REDEEM.fullmatch(path) or _DESKTOP_REDEEM.fullmatch(path):
        return method == "POST"
    return path in AUTH_PATHS | GATEWAY_PATHS | CONNECTOR_PATHS | POLICY_PATHS or workspace_route(path) or _ai_work_route(path)


class AIWorkGatewayServer(ProviderPilotGatewayServer):
    """Bound normal traffic separately from short, health-only overflow probes."""
    request_queue_size = AI_WORK_MAX_CONNECTIONS

    def __init__(self, config, *, environ):
        self._state_identity = check_initialized(config)
        self._work_state_identity = check_work_stores(config)
        self._deployment_metadata = deployment_metadata(environ)
        self._connection_slots = threading.BoundedSemaphore(PROVIDER_MAX_CONNECTIONS)
        self._health_probe_slots = threading.BoundedSemaphore(AI_WORK_HEALTH_PROBE_CONNECTIONS)
        self._connection_lanes_lock = threading.Lock()
        self._connection_lanes = {}
        self._provider_slots = threading.BoundedSemaphore(PROVIDER_MAX_INFERENCE_CONNECTIONS)
        self._provider_metrics_lock = threading.Lock()
        self._provider_inflight = self._provider_peak_inflight = self._provider_admitted_total = 0
        self._provider_saturated_total = self._connection_saturated_total = 0
        self.connection_lifetime = min(config.upstream_timeout_seconds + PROVIDER_CONNECTION_LIFETIME_MARGIN, 630)
        GatewayServer.__init__(self, config, environ=environ)
        self.RequestHandlerClass = AIWorkRequestHandler

    def process_request(self, request, client_address):
        health_only = not self._connection_slots.acquire(blocking=False)
        if health_only and not self._health_probe_slots.acquire(blocking=False):
            with self._provider_metrics_lock:
                self._connection_saturated_total += 1
            self.shutdown_request(request)
            return
        with self._connection_lanes_lock:
            self._connection_lanes[request] = health_only
        try:
            if health_only:
                request.settimeout(AI_WORK_HEALTH_PROBE_TIMEOUT)
            # Bypass the pilot's shared pre-classification semaphore. The
            # overflow handler may parse headers but cannot serve normal work.
            GatewayServer.process_request(self, request, client_address)
        except BaseException:
            self._release_connection(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            GatewayServer.process_request_thread(self, request, client_address)
        finally:
            self._release_connection(request)

    def health_only_connection(self, request):
        with self._connection_lanes_lock:
            return self._connection_lanes[request]

    def _release_connection(self, request):
        with self._connection_lanes_lock:
            health_only = self._connection_lanes.pop(request)
        (self._health_probe_slots if health_only else self._connection_slots).release()

    def state_intact(self):
        if not super().state_intact():
            return False
        try:
            identities = []
            for name in owned_databases(self.config):
                info = _private(self.config.database_path.parent / name)
                identities.append((info.st_dev, info.st_ino))
            return tuple(identities) == self._work_state_identity
        except (OSError, HostedError):
            return False

    def deployment_contract(self):
        return {
            "profile": "ai_work_sqlite_single_node", "provider_protocols": sorted(self.config.upstreams),
            "inference_enabled": True, "provider_credentials_server_only": True,
            "storage_backend": "sqlite", "workspace_enabled": True,
            "billing_configured": bool(self.config.ai_work.billing_price_id),
            "paid_access_required": self.config.ai_work.require_paid,
            "signed_connectors_configured": self.config.outcome_connectors is not None,
            "consistent_closed_recovery": True, "availability_sla_claimed": False,
            "max_inflight_streams": PROVIDER_MAX_INFERENCE_CONNECTIONS,
            "normal_connection_capacity": PROVIDER_MAX_CONNECTIONS,
            "health_probe_connection_capacity": AI_WORK_HEALTH_PROBE_CONNECTIONS,
            "health_probe_timeout_seconds": AI_WORK_HEALTH_PROBE_TIMEOUT,
        }

    def operational_stats(self):
        result = super().operational_stats()
        result["schema_id"] = "hormuz.ai-work-operations"
        result["provider"].update(connection_capacity=AI_WORK_MAX_CONNECTIONS,
            normal_connection_capacity=PROVIDER_MAX_CONNECTIONS,
            health_probe_connection_capacity=AI_WORK_HEALTH_PROBE_CONNECTIONS,
            health_probe_timeout_seconds=AI_WORK_HEALTH_PROBE_TIMEOUT)
        return result


class AIWorkRequestHandler(StagingRequestHandler):
    def setup(self):
        self._health_only = self.server.health_only_connection(self.request)
        GatewayRequestHandler.setup(self)
        # A hard deadline also bounds a client that keeps trickling headers.
        lifetime = AI_WORK_HEALTH_PROBE_TIMEOUT if self._health_only else self.server.connection_lifetime
        self._deadline = threading.Timer(lifetime, self._expire_connection)
        self._deadline.daemon = True
        self._deadline.start()

    def finish(self):
        try:
            super().finish()
        finally:
            self._deadline.join(timeout=1)

    def _stage_response(self, status, state):
        body = json.dumps({"schema_id": "hormuz.hosted-ai-work", "schema_version": 1,
                           "status": state, "inference_enabled": True,
                           "deployment": dict(self.server._deployment_metadata),
                           "contract": self.server.deployment_contract()}, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_allowed(self):
        hosts = self.headers.get_all("Host", [])
        origin = urlsplit(self.server.config.session_broker.public_base_url)
        if hosts == [origin.netloc]:
            return True
        path = urlsplit(self.path).path
        if path in {"/health", "/ready"}:
            return len(hosts) == 1 and self.server.workspace.domains.health_host(hosts[0])
        if not (workspace_route(path) or _ai_work_route(path)):
            return False
        from .workspace_http import request_origin
        from .workspace_store import WorkspaceError
        try:
            request_origin(self, probe=path.startswith("/.well-known/hormuz-domain/"))
            return True
        except WorkspaceError:
            return False

    def parse_request(self):
        if not GatewayRequestHandler.parse_request(self):
            return False
        self.close_connection = True
        lengths = self.headers.get_all("Content-Length", [])
        target = self.requestline.split()[1]
        if (not self._host_allowed() or self.headers.get_all("Transfer-Encoding", [])
                or any(len(self.headers.get_all(name, [])) > 1 for name in (
                    "Content-Type", "Authorization", "Origin", "Cookie", "Stripe-Signature",
                    "Idempotency-Key", "X-Hub-Signature-256", "Linear-Signature"))
                or self.headers.get_all("X-Hormuz-Failover-Rehearsal", [])
                or self.headers.get_all("X-Hormuz-Cancellation-Rehearsal", [])
                or len(lengths) > 1 or self.command == "POST" and len(lengths) != 1
                or lengths and re.fullmatch(r"0|[1-9][0-9]{0,7}", lengths[0]) is None
                or not target.startswith("/") or target.startswith("//") or "\\" in target):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        length = int(lengths[0]) if lengths else 0
        if length > self.server.config.max_request_bytes:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return False
        if self.command != "POST" and length:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        if self._health_only and (self.command not in {"GET", "HEAD"} or target not in {"/health", "/ready"}):
            with self.server._provider_metrics_lock:
                self.server._connection_saturated_total += 1
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "capacity_exhausted")
            return False
        request = urlsplit(self.path)
        if request.fragment:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        if not allowed_route(self.command, request.path):
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "route_disabled")
            return False
        if request.path != "/health" and (not self.server.state_intact() or not self.server._accepting_requests.is_set()):
            self.server.begin_drain()
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "not_ready")
            return False
        return True

    def do_GET(self):  # noqa: N802
        if urlsplit(self.path).path in {"/health", "/ready"}:
            if self.path not in {"/health", "/ready"}:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            ready = self.path == "/health" or self.server.readiness_reason() is None
            self._stage_response(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE, "ai_work" if ready else "not_ready")
            return
        GatewayRequestHandler.do_GET(self)

    def _proxy_generation(self, *, identity, protocol, client, account_usage):
        if not self.server.try_admit_provider():
            self._send_protocol_error(protocol, "Gateway capacity is currently full", HTTPStatus.SERVICE_UNAVAILABLE,
                                      code="hormuz_provider_capacity_exhausted")
            return
        try:
            GatewayRequestHandler._proxy_generation(self, identity=identity, protocol=protocol,
                                                     client=client, account_usage=account_usage)
        finally:
            self.server.release_provider()
