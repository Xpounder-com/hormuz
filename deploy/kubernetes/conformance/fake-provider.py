from __future__ import annotations

import json
import re
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_SECRET_PATTERN = re.compile(r"\bsk-(?:proj-|svcacct-)[A-Za-z0-9_-]{20,}\b")
_REDACTION_MARKER = "[REDACTED:HORMUZ_SECRET]"


class Provider(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    lock = threading.Lock()
    requests = 0
    redaction_marker_seen = False
    unredacted_secret_seen = False
    provider_authorization_seen = False
    capped_output_seen = False
    routed_model_seen = False
    blocking_requests = 0
    blocking_gateway_ip: str | None = None
    blocking_started = threading.Event()
    blocking_release = threading.Event()
    blocking_abort = threading.Event()
    blocking_gateway_disconnected = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send({"status": "ok"})
            return
        if self.path == "/stats":
            with self.lock:
                value = {
                    "requests": self.requests,
                    "redaction_marker_seen": self.redaction_marker_seen,
                    "unredacted_secret_seen": self.unredacted_secret_seen,
                    "provider_authorization_seen": self.provider_authorization_seen,
                    "capped_output_seen": self.capped_output_seen,
                    "routed_model_seen": self.routed_model_seen,
                    "blocking_requests": self.blocking_requests,
                }
            self._send(value)
            return
        if self.path == "/control/block/status":
            with self.lock:
                value = {
                    "started": self.blocking_started.is_set(),
                    "released": self.blocking_release.is_set(),
                    "aborted": self.blocking_abort.is_set(),
                    "gateway_ip": self.blocking_gateway_ip,
                    "gateway_disconnected": self.blocking_gateway_disconnected.is_set(),
                }
            self._send(value)
            return
        self._send({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/control/block/release":
            self.blocking_release.set()
            self._send({"released": True})
            return
        if self.path == "/control/block/abort":
            self.blocking_abort.set()
            self.blocking_release.set()
            self._send({"aborted": True})
            return
        if self.path == "/control/block/reset":
            with self.lock:
                if self.blocking_started.is_set() and not self.blocking_release.is_set():
                    self._send({"error": "blocking_request_active"}, status=409)
                    return
                type(self).blocking_gateway_ip = None
                self.blocking_started.clear()
                self.blocking_release.clear()
                self.blocking_abort.clear()
                self.blocking_gateway_disconnected.clear()
            self._send({"reset": True})
            return
        if self.path.partition("?")[0] != "/v1/responses":
            self._send({"error": "not_found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        authorization = self.headers.get("Authorization", "")
        blocking = "HORMUZ_BLOCKING_OPERATION_PROBE" in encoded
        with self.lock:
            type(self).requests += 1
            type(self).redaction_marker_seen |= _REDACTION_MARKER in encoded
            type(self).unredacted_secret_seen |= _SECRET_PATTERN.search(encoded) is not None
            type(self).provider_authorization_seen |= (
                authorization.startswith("Bearer ") and len(authorization) > len("Bearer ")
            )
            type(self).capped_output_seen |= body.get("max_output_tokens") == 64
            type(self).routed_model_seen |= body.get("model") == "gpt-kubernetes-proof"
            if blocking:
                type(self).blocking_requests += 1
                type(self).blocking_gateway_ip = self.client_address[0]
                self.blocking_started.set()
        if blocking and not self._wait_for_block_release(timeout=120):
            self._send({"error": "blocking_probe_timeout"}, status=504)
            return
        if blocking and self.blocking_abort.is_set():
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        try:
            self._send(
                {
                    "id": "resp_kubernetes_proof",
                    "object": "response",
                    "status": "completed",
                    "model": body.get("model"),
                    "output": [],
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 4,
                        "input_tokens_details": {"cached_tokens": 2},
                        "output_tokens_details": {"reasoning_tokens": 1},
                        "total_tokens": 16,
                    },
                },
                request_id="req_kubernetes_proof",
            )
        except (BrokenPipeError, ConnectionResetError):
            # An intentionally force-killed gateway cannot receive the
            # provider response. The provider still records one egress and
            # never retries it.
            return

    def _wait_for_block_release(self, *, timeout: int) -> bool:
        deadline = time.monotonic() + timeout
        disconnected = False
        while time.monotonic() < deadline:
            if self.blocking_release.wait(timeout=0.1):
                return True
            if disconnected:
                continue
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                continue
            try:
                disconnected = self.connection.recv(1, socket.MSG_PEEK) == b""
            except (BrokenPipeError, ConnectionResetError, OSError):
                disconnected = True
            if disconnected:
                self.blocking_gateway_disconnected.set()
        return False

    def _send(
        self,
        value: dict[str, object],
        *,
        status: int = 200,
        request_id: str | None = None,
    ) -> None:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        if request_id is not None:
            self.send_header("x-request-id", request_id)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


ThreadingHTTPServer(("0.0.0.0", 8090), Provider).serve_forever()
