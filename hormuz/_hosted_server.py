"""Private, bounded HTTP adapter for hosted login and console staging only."""

from __future__ import annotations

import json
import re
import socket
import threading
from http import HTTPStatus
from urllib.parse import urlsplit

from ._hosted_config import HostedError
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
        self._connection_slots = threading.BoundedSemaphore(PROVIDER_MAX_CONNECTIONS)
        self._provider_slots = threading.BoundedSemaphore(PROVIDER_MAX_INFERENCE_CONNECTIONS)
        self.connection_lifetime = min(
            config.upstream_timeout_seconds + PROVIDER_CONNECTION_LIFETIME_MARGIN,
            630,
        )
        super().__init__(config, environ=environ)
        self.RequestHandlerClass = ProviderPilotRequestHandler

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
        if not self.server._provider_slots.acquire(blocking=False):
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
            self.server._provider_slots.release()
