"""Private, bounded HTTP adapter for hosted login and console staging only."""

from __future__ import annotations

import hmac
import json
import re
import socket
import threading
from http import HTTPStatus
from urllib.parse import urlsplit

from ._hosted_config import HostedError
from ._hosted_provider import PROVIDER_FAILOVER_REHEARSAL_ENV, deployment_metadata
from ._hosted_state import DATABASES, MARKER, _private, check_initialized
from .server import GatewayRequestHandler, GatewayServer


MAX_CONNECTIONS = 32
SOCKET_TIMEOUT = 5
CONNECTION_LIFETIME = 30
PROVIDER_MAX_INFERENCE_CONNECTIONS = 8
PROVIDER_RESERVED_LIVENESS_CONNECTIONS = 1
PROVIDER_MAX_CONNECTIONS = (
    PROVIDER_MAX_INFERENCE_CONNECTIONS + PROVIDER_RESERVED_LIVENESS_CONNECTIONS
)
PROVIDER_SOCKET_TIMEOUT = 45
PROVIDER_CONNECTION_LIFETIME_MARGIN = 30


class StagingGatewayServer(GatewayServer):
    request_queue_size = 32

    def __init__(self, config):
        if config.upstreams or config.model_routes or config.identities_by_token or config.identities_by_subject or config.listen.host != "127.0.0.1" or config.ingress.mode != "external_tls_proxy":
            raise HostedError("hosted_gateway_configuration_unsafe")
        self._state_identity = check_initialized(config)
        self._connection_slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(config, environ={})
        self.RequestHandlerClass = StagingRequestHandler

    def state_intact(self) -> bool:
        try:
            directory = self.config.database_path.parent
            _private(directory, directory=True)
            identities = []
            for name in (MARKER, *DATABASES):
                info = _private(directory / name)
                identities.append((info.st_dev, info.st_ino))
            return tuple(identities) == self._state_identity
        except OSError:
            return False
        except HostedError:
            return False

    def readiness_reason(self):
        if not self.state_intact():
            self.begin_drain()
            return "state_unavailable"
        return super().readiness_reason()

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(SOCKET_TIMEOUT)
        return request, address

    def process_request(self, request, client_address):
        if not self._connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()

    def handle_error(self, request, client_address):
        # No exception traceback, peer address, request line or payload logging.
        pass


class ProviderPilotGatewayServer(GatewayServer):
    """A state-bound gateway with a small, fixed Render compute envelope."""

    request_queue_size = PROVIDER_MAX_CONNECTIONS

    def __init__(self, config, *, environ):
        self._state_identity = check_initialized(config)
        self._deployment_metadata = deployment_metadata(environ)
        self._failover_rehearsal_key = environ[PROVIDER_FAILOVER_REHEARSAL_ENV]
        self._connection_slots = threading.BoundedSemaphore(PROVIDER_MAX_CONNECTIONS)
        self._provider_slots = threading.BoundedSemaphore(PROVIDER_MAX_INFERENCE_CONNECTIONS)
        self._provider_metrics_lock = threading.Lock()
        self._provider_inflight = 0
        self._provider_peak_inflight = 0
        self._provider_admitted_total = 0
        self._provider_saturated_total = 0
        self.connection_lifetime = min(
            config.upstream_timeout_seconds + PROVIDER_CONNECTION_LIFETIME_MARGIN,
            630,
        )
        super().__init__(config, environ=environ)
        self.RequestHandlerClass = ProviderPilotRequestHandler

    def try_admit_provider(self) -> bool:
        if not self._provider_slots.acquire(blocking=False):
            with self._provider_metrics_lock:
                self._provider_saturated_total += 1
            return False
        with self._provider_metrics_lock:
            self._provider_inflight += 1
            self._provider_admitted_total += 1
            self._provider_peak_inflight = max(
                self._provider_peak_inflight,
                self._provider_inflight,
            )
        return True

    def release_provider(self) -> None:
        with self._provider_metrics_lock:
            if self._provider_inflight < 1:
                raise RuntimeError("hosted_provider_capacity_accounting_invalid")
            self._provider_inflight -= 1
        self._provider_slots.release()

    def operational_stats(self) -> dict[str, object]:
        """Return bounded, content-free saturation and pool-wait evidence."""

        with self._provider_metrics_lock:
            provider = {
                "capacity": PROVIDER_MAX_INFERENCE_CONNECTIONS,
                "inflight": self._provider_inflight,
                "peak_inflight": self._provider_peak_inflight,
                "admitted_total": self._provider_admitted_total,
                "saturated_total": self._provider_saturated_total,
            }
        if self.postgres_pool is None:
            postgres: dict[str, object] = {
                "configured": False,
                "closed": False,
                "min_connections": 0,
                "max_connections": 0,
                "pool_size": 0,
                "available_connections": 0,
                "requests_waiting": 0,
                "requests_total": 0,
                "requests_queued_total": 0,
                "requests_error_total": 0,
                "requests_wait_milliseconds_total": 0,
            }
        else:
            postgres = self.postgres_pool.operational_stats()
        return {
            "schema_id": "hormuz.provider-operations",
            "schema_version": 1,
            "content_boundary": "aggregate_content_free_counters",
            "provider": provider,
            "postgresql_pool": postgres,
            "deployment": dict(self._deployment_metadata),
        }

    def deployment_contract(self) -> dict[str, object]:
        issuer_hostnames = {
            urlsplit(issuer.issuer).hostname or ""
            for issuer in self.config.oidc_issuers.values()
        }
        identity_provider = (
            "okta"
            if len(issuer_hostnames) == 1
            and next(iter(issuer_hostnames)).endswith((".okta.com", ".oktapreview.com"))
            else "configured_oidc"
        )
        on_render = self._deployment_metadata["platform"] == "render"
        return {
            "profile": "external_pilot" if on_render else "local_provider_fixture",
            "identity_provider": identity_provider,
            "provider_protocols": ["anthropic", "openai"],
            "https": on_render and self.config.session_broker.public_base_url.startswith("https://"),
            "inference_enabled": True,
            "provider_credentials_server_only": True,
            "postgresql_durable": self.config.usage_storage.backend == "postgresql",
            "tenant_rls": self.config.usage_storage.backend == "postgresql",
            "durable_sessions": on_render,
            "monitoring_configured": True,
            "worker_saturation_monitoring": True,
            "postgresql_pool_wait_monitoring": self.config.usage_storage.backend == "postgresql",
            "single_region_acknowledged": True,
            "availability_sla_claimed": False,
            "max_inflight_streams": PROVIDER_MAX_INFERENCE_CONNECTIONS,
        }

    state_intact = StagingGatewayServer.state_intact

    def readiness_reason(self):
        if not self.state_intact():
            self.begin_drain()
            return "state_unavailable"
        return super().readiness_reason()

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(PROVIDER_SOCKET_TIMEOUT)
        return request, address

    def process_request(self, request, client_address):
        if not self._connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connection_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connection_slots.release()

    def handle_error(self, request, client_address):
        # No exception traceback, peer address, request line or payload logging.
        pass


class StagingRequestHandler(GatewayRequestHandler):
    def setup(self):
        super().setup()
        self._deadline = threading.Timer(
            getattr(self.server, "connection_lifetime", CONNECTION_LIFETIME),
            self._expire_connection,
        )
        self._deadline.daemon = True
        self._deadline.start()

    def _expire_connection(self):
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def finish(self):
        self._deadline.cancel()
        super().finish()

    def handle_expect_100(self):
        self.send_error(HTTPStatus.EXPECTATION_FAILED)
        return False

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self._stage_response(code, "request_rejected")

    def _stage_response(self, status, state):
        body = json.dumps({"schema_id": "hormuz.hosted-staging", "schema_version": 1,
                           "status": state, "inference_enabled": False}, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def parse_request(self):
        if not super().parse_request():
            return False
        self.close_connection = True
        origin = urlsplit(self.server.config.session_broker.public_base_url)
        original_target = self.requestline.split()[1]
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get_all("Host", []) != [origin.netloc]
            or self.headers.get_all("Transfer-Encoding", [])
            or any(len(self.headers.get_all(name, [])) > 1 for name in ("Content-Type", "Authorization", "Origin", "Cookie"))
            or len(lengths) > 1 or (self.command == "POST" and len(lengths) != 1)
            or lengths and not re.fullmatch(r"0|[1-9][0-9]{0,5}", lengths[0])
            or not original_target.startswith("/") or original_target.startswith("//") or "\\" in original_target
        ):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        length = int(lengths[0]) if lengths else 0
        if length > self.server.config.max_request_bytes:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return False
        if self.command != "POST" and length:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        request = urlsplit(self.path)
        if request.fragment:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        allowed = request.path in {"/health", "/ready", "/console", "/v1/gateway/whoami", "/v1/gateway/usage"} or request.path.startswith(("/v1/auth/", "/v1/admin/", "/console/"))
        if not allowed:
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "route_disabled")
            return False
        if not self.server.state_intact() or not self.server._accepting_requests.is_set():
            self.server.begin_drain()
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "not_ready")
            return False
        return True

    def do_GET(self):  # noqa: N802
        if urlsplit(self.path).path in {"/health", "/ready"}:
            if self.path not in {"/health", "/ready"}:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            ready = self.server.readiness_reason() is None
            self._stage_response(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                                 "authentication_staging" if ready else "not_ready")
            return
        super().do_GET()

    def do_HEAD(self):  # noqa: N802
        if self.path in {"/health", "/ready"}:
            self.do_GET()
        else:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def log_message(self, format, *args):
        pass


class ProviderPilotRequestHandler(StagingRequestHandler):
    """Strict private-hop framing with only customer and generation routes."""

    def _stage_response(self, status, state):
        body = json.dumps({
            "schema_id": "hormuz.hosted-provider-pilot",
            "schema_version": 1,
            "status": state,
            "inference_enabled": True,
            "deployment": dict(self.server._deployment_metadata),
            "contract": self.server.deployment_contract(),
        }, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def parse_request(self):
        if not GatewayRequestHandler.parse_request(self):
            return False
        self.close_connection = True
        origin = urlsplit(self.server.config.session_broker.public_base_url)
        original_target = self.requestline.split()[1]
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get_all("Host", []) != [origin.netloc]
            or self.headers.get_all("Transfer-Encoding", [])
            or any(len(self.headers.get_all(name, [])) > 1 for name in (
                "Content-Type", "Authorization", "Origin", "Cookie",
                "X-Hormuz-Failover-Rehearsal",
                "X-Hormuz-Cancellation-Rehearsal",
            ))
            or len(lengths) > 1
            or (self.command == "POST" and len(lengths) != 1)
            or lengths and not re.fullmatch(r"0|[1-9][0-9]{0,7}", lengths[0])
            or not original_target.startswith("/")
            or original_target.startswith("//")
            or "\\" in original_target
        ):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        length = int(lengths[0]) if lengths else 0
        if length > self.server.config.max_request_bytes:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return False
        if self.command != "POST" and length:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        request = urlsplit(self.path)
        if request.fragment:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return False
        allowed = request.path in {
            "/health",
            "/ready",
            "/console",
            "/v1/gateway/whoami",
            "/v1/gateway/reliability",
            "/v1/gateway/usage",
            "/v1/responses",
            "/v1/responses/compact",
            "/v1/messages",
            "/v1/messages/count_tokens",
        } or request.path.startswith(("/v1/auth/", "/v1/admin/", "/console/"))
        if not allowed:
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "route_disabled")
            return False
        if request.path != "/health" and (
            not self.server.state_intact() or not self.server._accepting_requests.is_set()
        ):
            self.server.begin_drain()
            self._stage_response(HTTPStatus.SERVICE_UNAVAILABLE, "not_ready")
            return False
        return True

    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/health", "/ready"}:
            if self.path not in {"/health", "/ready"}:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            ready = path == "/health" or self.server.readiness_reason() is None
            self._stage_response(
                HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                "provider_pilot" if ready else "not_ready",
            )
            return
        GatewayRequestHandler.do_GET(self)

    def _proxy_generation(self, *, identity, protocol, client, account_usage) -> None:
        rehearsal_values = self.headers.get_all("X-Hormuz-Failover-Rehearsal", [])
        cancellation_values = self.headers.get_all("X-Hormuz-Cancellation-Rehearsal", [])
        supplied = [values for values in (rehearsal_values, cancellation_values) if values]
        if len(supplied) > 1 or any(
            len(values) != 1
            or not hmac.compare_digest(values[0], self.server._failover_rehearsal_key)
            for values in supplied
        ):
            self._send_protocol_error(
                protocol,
                "Failover rehearsal authorization was rejected",
                HTTPStatus.FORBIDDEN,
                code="hormuz_failover_rehearsal_rejected",
            )
            return
        self._failover_rehearsal_requested = bool(rehearsal_values)
        self._cancellation_rehearsal_requested = bool(cancellation_values)
        if not self.server.try_admit_provider():
            self._send_protocol_error(
                protocol,
                "Provider pilot capacity is currently full",
                HTTPStatus.SERVICE_UNAVAILABLE,
                code="hormuz_provider_capacity_exhausted",
            )
            return
        try:
            super()._proxy_generation(
                identity=identity,
                protocol=protocol,
                client=client,
                account_usage=account_usage,
            )
        finally:
            self._failover_rehearsal_requested = False
            self._cancellation_rehearsal_requested = False
            self.server.release_provider()
