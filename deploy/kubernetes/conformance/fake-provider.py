from __future__ import annotations

import json
import re
import threading
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
                }
            self._send(value)
            return
        self._send({"error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.partition("?")[0] != "/v1/responses":
            self._send({"error": "not_found"}, status=404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        authorization = self.headers.get("Authorization", "")
        with self.lock:
            type(self).requests += 1
            type(self).redaction_marker_seen |= _REDACTION_MARKER in encoded
            type(self).unredacted_secret_seen |= _SECRET_PATTERN.search(encoded) is not None
            type(self).provider_authorization_seen |= (
                authorization.startswith("Bearer ") and len(authorization) > len("Bearer ")
            )
            type(self).capped_output_seen |= body.get("max_output_tokens") == 64
            type(self).routed_model_seen |= body.get("model") == "gpt-kubernetes-proof"
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
