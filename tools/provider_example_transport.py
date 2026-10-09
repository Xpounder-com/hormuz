"""Bounded, reusable loopback transport for the disposable provider tour."""
from __future__ import annotations

import http.client
import errno
from http.server import ThreadingHTTPServer
import io
import json
import threading
from urllib.parse import urlsplit

from hormuz.work_client import WorkClientError

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
CONNECT_TIMEOUT = 5
REQUEST_TIMEOUT = 60
UPSTREAM_TIMEOUT = 30


class LoopbackTransport:
    """One serial connection; no redirects or automatic retries."""

    def __init__(self, port):
        self.endpoint = f"http://127.0.0.1:{port}"
        self.connection = http.client.HTTPConnection("127.0.0.1", port, timeout=CONNECT_TIMEOUT)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self.connection.close()

    def request(self, method, path, body=None, headers=None, *, timeout=REQUEST_TIMEOUT):
        if not path.startswith("/") or path.startswith("//") or urlsplit(path).netloc:
            raise WorkClientError("invalid_gateway_path")
        try:
            if self.connection.sock is None:
                self.connection.connect()
            self.connection.sock.settimeout(timeout)
            self.connection.request(method, path, body, headers or {})
            with self.connection.getresponse() as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise WorkClientError("gateway_response_too_large")
                return response.status, {key.lower(): value for key, value in response.getheaders()}, raw
        except OSError as failure:
            self.close()
            reason = "example_socket_capacity_unavailable" if failure.errno == errno.EADDRNOTAVAIL else "example_transport_timeout" if isinstance(failure, TimeoutError) else "example_transport_unavailable"
            raise WorkClientError(reason) from None
        except BaseException:
            self.close()
            raise

    def open(self, request, *, timeout):
        """WorkClient opener adapter keeps its existing auth/path contracts."""
        parsed = urlsplit(request.full_url)
        if parsed.scheme + "://" + parsed.netloc != self.endpoint:
            raise WorkClientError("invalid_gateway_url")
        status, _headers, raw = self.request(request.get_method(), request.selector, request.data, dict(request.header_items()), timeout=timeout)
        if 300 <= status < 400:
            raise WorkClientError("gateway_redirect_refused", status)
        if not 200 <= status < 300:
            reason = "gateway_request_failed"
            try:
                code = json.loads(raw).get("error", {}).get("code")
                if isinstance(code, str) and len(code) < 128 and all(c.isalnum() or c == "_" for c in code):
                    reason = code
            except (ValueError, AttributeError):
                pass
            raise WorkClientError(reason, status)
        return io.BytesIO(raw)


class BoundedFixtureServer(ThreadingHTTPServer):
    """At most four accepted handlers, each with a bounded idle/read timeout."""

    daemon_threads = False
    block_on_close = True
    request_queue_size = 4

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(4)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(CONNECT_TIMEOUT)
        return request, address

    def process_request(self, request, address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self._slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self._slots.release()
