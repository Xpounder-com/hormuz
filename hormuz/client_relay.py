"""Launcher-owned loopback relay for client-side context optimization."""

from __future__ import annotations

import hmac
import http.client
import json
import os
import queue
import re
import secrets
import shutil
import socket
import ssl
import stat
import subprocess
import threading
import time
import urllib.parse
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, cast

from .adapters import AdapterError, adapter_for
from .compaction import (
    MAX_REQUEST_BYTES,
    CompactionResult,
    Protocol,
    optimize_request,
    serialize_request,
)
from .client_versions import SUPPORTED_CLIENT_VERSIONS
from .compaction_formats import CompactionFormatError, strict_json_loads
from .compaction_contract import (
    CONTEXT_FORMAT_HEADER,
    CONTEXT_FORMAT_VERSION,
    CONTEXT_FORMATS_HEADER,
)
from .compaction_protocols import derive_selections
from .compaction_runtime import (
    ContextPreferenceStore,
    ContextRuntimeError,
    load_token_counters,
)
from .personal_metrics import (
    OptimizationMeasurement,
    PersonalMetricsError,
    PersonalMetricsStore,
    ProviderMeasurement,
    ResponseUsageAccumulator,
)
from .session_client import SessionClientError, validate_session_gateway


MAX_RELAY_BODY_BYTES = 25 * 1024 * 1024
RELAY_CHUNK_BYTES = 16 * 1024
_LOCAL_CREDENTIAL = re.compile(r"hox_l_[A-Za-z0-9_-]{43}")
_ACCESS_CREDENTIAL = re.compile(r"hox_a_[A-Za-z0-9_-]{43}")
_CLIENT_VERSION = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
MAX_OPTIMIZER_OVERHEAD_US = 100_000
_MAX_PENDING_OPTIMIZATION_METRICS = 1024
_HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
)


class ClientRelayError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RelayStatus:
    code: str
    changed_blocks: int = 0
    before_bytes: int | None = None
    after_bytes: int | None = None
    before_tokens: dict[str, int] | None = None
    after_tokens: dict[str, int] | None = None


@dataclass(frozen=True)
class SavedClientProfile:
    key: str
    gateway: str
    client: Literal["codex", "claude-code"]
    model: str
    allow_insecure_http: bool


@dataclass(frozen=True)
class _OptimizationMetricTask:
    store: PersonalMetricsStore
    measurement: OptimizationMeasurement
    finished: threading.Event
    on_failure: Callable[[], None]


_OPTIMIZATION_METRIC_TASKS: queue.Queue[_OptimizationMetricTask] = queue.Queue(
    maxsize=_MAX_PENDING_OPTIMIZATION_METRICS
)
_OPTIMIZATION_METRIC_WORKER_LOCK = threading.Lock()
_optimization_metric_worker: threading.Thread | None = None


def _run_optimization_metric_worker() -> None:
    while True:
        task = _OPTIMIZATION_METRIC_TASKS.get()
        try:
            task.store.record_optimization(task.measurement)
        except Exception:
            # Measurement failure must never block or replace provider traffic.
            task.on_failure()
        finally:
            task.finished.set()
            _OPTIMIZATION_METRIC_TASKS.task_done()


def _submit_optimization_metric(
    store: PersonalMetricsStore,
    measurement: OptimizationMeasurement,
    on_failure: Callable[[], None],
) -> threading.Event:
    global _optimization_metric_worker
    finished = threading.Event()
    with _OPTIMIZATION_METRIC_WORKER_LOCK:
        if (
            _optimization_metric_worker is None
            or not _optimization_metric_worker.is_alive()
        ):
            _optimization_metric_worker = threading.Thread(
                target=_run_optimization_metric_worker,
                name="hormuz-optimization-metrics",
                daemon=True,
            )
            _optimization_metric_worker.start()
    try:
        _OPTIMIZATION_METRIC_TASKS.put_nowait(
            _OptimizationMetricTask(store, measurement, finished, on_failure)
        )
    except queue.Full:
        on_failure()
        finished.set()
    return finished


class RelayOptimizer:
    def __init__(
        self,
        *,
        preference_store: ContextPreferenceStore,
        client: str,
        gateway_compatible: bool,
        counters: Mapping[str, Callable[[str], int]] | None = None,
        metrics: PersonalMetricsStore | None = None,
    ):
        self.preference_store = preference_store
        self.client = client
        self.gateway_compatible = gateway_compatible
        self._counters = counters
        self._resources_checked = counters is not None
        self.metrics = metrics
        self._metrics_lock = threading.Lock()
        self._pending_metrics: set[threading.Event] = set()
        self._lock = threading.Lock()
        self._status = RelayStatus("off")

    @property
    def status(self) -> RelayStatus:
        with self._lock:
            return self._status

    def prepare(self, body: bytes, path: str) -> tuple[bytes, dict[str, str]]:
        started = time.perf_counter_ns()
        try:
            enabled = self.preference_store.load().enabled
        except ContextRuntimeError:
            self._set_status(RelayStatus("settings_invalid"))
            self._observe(
                eligible=False,
                applied=False,
                reason="settings_invalid",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        if not enabled:
            self._set_status(RelayStatus("off"))
            self._observe(
                eligible=False,
                applied=False,
                reason="disabled",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        try:
            adapter_for(self.client)
        except AdapterError:
            self._set_status(RelayStatus("unsupported_client"))
            self._observe(
                eligible=False,
                applied=False,
                reason="unsupported_client",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        if not self.gateway_compatible:
            self._set_status(RelayStatus("gateway_incompatible"))
            self._observe(
                eligible=False,
                applied=False,
                reason="gateway_incompatible",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        protocol = _protocol_for_path(path)
        if protocol is None or len(body) > MAX_REQUEST_BYTES:
            reason = "unsupported_history" if protocol is not None else "unsupported_client"
            self._set_status(RelayStatus(reason))
            self._observe(
                eligible=False,
                applied=False,
                reason=reason,
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        try:
            request_text = body.decode("utf-8")
            payload = strict_json_loads(request_text)
        except (CompactionFormatError, UnicodeDecodeError, RecursionError):
            self._set_status(RelayStatus("unsupported_history"))
            self._observe(
                eligible=False,
                applied=False,
                reason="unsupported_history",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        if not isinstance(payload, dict):
            self._set_status(RelayStatus("unsupported_history"))
            self._observe(
                eligible=False,
                applied=False,
                reason="unsupported_history",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        counters = self._token_counters()
        if counters is None:
            self._observe(
                eligible=False,
                applied=False,
                reason="resources_unavailable",
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=None,
                after_tokens=None,
                started=started,
            )
            return body, {}
        typed_payload = cast(dict[str, object], payload)
        selections = derive_selections(typed_payload, protocol, client=self.client)
        result = optimize_request(typed_payload, protocol, selections, counters, enabled=True)
        outgoing_text = serialize_request(result.payload) if result.changed else request_text
        outgoing = outgoing_text.encode("utf-8") if result.changed else body
        try:
            before_tokens = _count_boundary_tokens(request_text, counters)
            after_tokens = _count_boundary_tokens(outgoing_text, counters)
        except Exception:
            result = CompactionResult(
                payload=typed_payload,
                changed=False,
                reason="counter_unavailable",
                changed_blocks=0,
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens={},
                after_tokens={},
            )
            outgoing = body
            before_tokens = {}
            after_tokens = {}
        elapsed_us = max(0, (time.perf_counter_ns() - started) // 1_000)
        if result.changed and elapsed_us > MAX_OPTIMIZER_OVERHEAD_US:
            result = CompactionResult(
                payload=typed_payload,
                changed=False,
                reason="net_regression",
                changed_blocks=0,
                before_bytes=len(body),
                after_bytes=len(body),
                before_tokens=before_tokens,
                after_tokens=dict(before_tokens),
            )
            outgoing = body
            after_tokens = dict(before_tokens)
        measured_result = CompactionResult(
            payload=result.payload,
            changed=result.changed,
            reason=result.reason,
            changed_blocks=result.changed_blocks,
            before_bytes=len(body),
            after_bytes=len(outgoing),
            before_tokens=before_tokens,
            after_tokens=after_tokens,
        )
        self._record_result(measured_result)
        self._observe(
            eligible=result.reason in {"compacted", "no_savings", "net_regression"},
            applied=result.changed,
            reason=result.reason,
            before_bytes=measured_result.before_bytes,
            after_bytes=measured_result.after_bytes,
            before_tokens=measured_result.before_tokens,
            after_tokens=measured_result.after_tokens,
            started=started,
        )
        if not result.changed:
            return body, {}
        return outgoing, {CONTEXT_FORMAT_HEADER: CONTEXT_FORMAT_VERSION}

    def note_oversized_passthrough(self, request_bytes: int) -> None:
        """Snapshot the toggle without buffering or inspecting a large body."""
        try:
            enabled = self.preference_store.load().enabled
        except ContextRuntimeError:
            self._set_status(RelayStatus("settings_invalid"))
        else:
            if not enabled:
                self._set_status(RelayStatus("off"))
            else:
                try:
                    adapter_for(self.client)
                except AdapterError:
                    self._set_status(RelayStatus("unsupported_client"))
                else:
                    self._set_status(
                        RelayStatus(
                            "gateway_incompatible"
                            if not self.gateway_compatible
                            else "unsupported_history"
                        )
                    )
        reason = self.status.code
        self._queue_observation(
            OptimizationMeasurement(
                eligible=False,
                applied=False,
                reason="disabled" if reason == "off" else reason,
                before_bytes=request_bytes,
                after_bytes=request_bytes,
                before_tokens=None,
                after_tokens=None,
                overhead_us=0,
            )
        )

    def flush_metrics(self, timeout: float = 5.0) -> bool:
        """Wait for this optimizer's queued local measurements to settle."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._metrics_lock:
                self._pending_metrics = {
                    event for event in self._pending_metrics if not event.is_set()
                }
                pending = tuple(self._pending_metrics)
            if not pending:
                return True
            for event in pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not event.wait(remaining):
                    return False

    def _token_counters(self) -> Mapping[str, Callable[[str], int]] | None:
        with self._lock:
            if self._resources_checked:
                return self._counters
            try:
                self._counters = load_token_counters()
            except ContextRuntimeError:
                self._status = RelayStatus("resources_unavailable")
                self._counters = None
            self._resources_checked = True
            return self._counters

    def _record_result(self, result: CompactionResult) -> None:
        code = "ready" if result.reason in {"compacted", "no_savings", "no_eligible_result"} else result.reason
        self._set_status(
            RelayStatus(
                code,
                changed_blocks=result.changed_blocks,
                before_bytes=result.before_bytes,
                after_bytes=result.after_bytes,
                before_tokens=result.before_tokens,
                after_tokens=result.after_tokens,
            )
        )

    def _set_status(self, status: RelayStatus) -> None:
        with self._lock:
            self._status = status

    def _observe(
        self,
        *,
        eligible: bool,
        applied: bool,
        reason: str,
        before_bytes: int | None,
        after_bytes: int | None,
        before_tokens: Mapping[str, int] | None,
        after_tokens: Mapping[str, int] | None,
        started: int,
    ) -> None:
        self._queue_observation(
            OptimizationMeasurement(
                eligible=eligible,
                applied=applied,
                reason=reason,
                before_bytes=before_bytes,
                after_bytes=after_bytes,
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                overhead_us=max(0, (time.perf_counter_ns() - started) // 1_000),
            )
        )

    def _queue_observation(self, measurement: OptimizationMeasurement) -> None:
        with self._metrics_lock:
            metrics = self.metrics
        if metrics is None:
            return

        def disable_failed_store() -> None:
            with self._metrics_lock:
                if self.metrics is metrics:
                    self.metrics = None

        event = _submit_optimization_metric(
            metrics, measurement, disable_failed_store
        )
        with self._metrics_lock:
            self._pending_metrics = {
                pending
                for pending in self._pending_metrics
                if not pending.is_set()
            }
            if not event.is_set():
                self._pending_metrics.add(event)


class LocalRelayServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    _UPSTREAM_DRAIN_SECONDS = 5

    def __init__(
        self,
        *,
        gateway: str,
        client: str,
        local_credential: str,
        gateway_credential: Callable[[], str],
        optimizer: RelayOptimizer,
        upstream_auth: Literal["hormuz", "openai", "anthropic"] = "hormuz",
        metrics: PersonalMetricsStore | None = None,
        timeout_seconds: float = 60,
    ):
        if _LOCAL_CREDENTIAL.fullmatch(local_credential) is None:
            raise ClientRelayError("invalid_local_credential")
        allow_insecure = urllib.parse.urlsplit(gateway).scheme == "http"
        try:
            self.gateway = validate_session_gateway(gateway, allow_insecure_http=allow_insecure)
        except SessionClientError as error:
            raise ClientRelayError("invalid_gateway") from error
        self.client_name = client
        self.local_credential = local_credential
        self.gateway_credential = gateway_credential
        self.optimizer = optimizer
        if upstream_auth not in {"hormuz", "openai", "anthropic"}:
            raise ClientRelayError("invalid_upstream_auth")
        try:
            self.adapter = adapter_for(client)
        except AdapterError as error:
            raise ClientRelayError("unsupported_client") from error
        self.upstream_auth = upstream_auth
        self.metrics = metrics
        self.timeout_seconds = timeout_seconds
        self._upstream_condition = threading.Condition()
        self._upstreams: dict[http.client.HTTPConnection, socket.socket | None] = {}
        self._stopping = False
        super().__init__(("127.0.0.1", 0), LocalRelayHandler)

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def _register_upstream(self, connection: http.client.HTTPConnection) -> bool:
        with self._upstream_condition:
            if self._stopping:
                return False
            self._upstreams[connection] = None
            return True

    def _attach_upstream_socket(self, connection: http.client.HTTPConnection) -> bool:
        current = connection.sock
        if current is None:
            return False
        # HTTPConnection may clear its socket after response headers with
        # Connection: close, while HTTPResponse still reads through makefile.
        # Keep a separate raw socket owner so shutdown can interrupt that read.
        try:
            borrowed = socket.socket(fileno=current.fileno())
            try:
                owned = borrowed.dup()
            finally:
                borrowed.detach()
        except OSError:
            return False
        with self._upstream_condition:
            if self._stopping or connection not in self._upstreams:
                owned.close()
                return False
            self._upstreams[connection] = owned
            return True

    def _unregister_upstream(self, connection: http.client.HTTPConnection) -> None:
        with self._upstream_condition:
            owned = self._upstreams.pop(connection, None)
            if owned is not None:
                owned.close()
            self._upstream_condition.notify_all()

    def _is_stopping(self) -> bool:
        with self._upstream_condition:
            return self._stopping

    def shutdown(self) -> None:
        # Closing HTTPConnection from another thread is insufficient: a
        # response's socket file object can retain a blocking read. Shutdown
        # the separate raw socket owner to wake reads without clearing
        # connection.sock or allowing http.client to reconnect and replay.
        with self._upstream_condition:
            self._stopping = True
            sockets = tuple(value for value in self._upstreams.values() if value is not None)
        for current in sockets:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        super().shutdown()
        with self._upstream_condition:
            self._upstream_condition.wait_for(
                lambda: not self._upstreams, timeout=self._UPSTREAM_DRAIN_SECONDS
            )
        self.optimizer.flush_metrics(timeout=self._UPSTREAM_DRAIN_SECONDS)


class LocalRelayHandler(BaseHTTPRequestHandler):
    server: LocalRelayServer
    protocol_version = "HTTP/1.1"

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        allowed_origins = {self.server.origin, f"http://localhost:{self.server.server_port}"}
        if (
            len(hosts) != 1
            or hosts[0] not in allowed_hosts
            or len(origins) > 1
            or (origins and origins[0] not in allowed_origins)
        ):
            self._error(HTTPStatus.FORBIDDEN, "local_origin_rejected")
            return False
        return True

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path)
        if path.scheme or path.netloc or path.fragment or path.path not in _allowed_paths(self.server.client_name):
            self._error(HTTPStatus.NOT_FOUND, "local_route_not_found")
            return
        if not self._authenticated():
            self._error(HTTPStatus.UNAUTHORIZED, "local_authentication_failed")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            self._error(HTTPStatus.LENGTH_REQUIRED, "local_content_length_required")
            return
        try:
            length = int(lengths[0])
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "local_content_length_invalid")
            return
        if length < 0 or length > MAX_RELAY_BODY_BYTES:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "local_request_too_large")
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._error(HTTPStatus.BAD_REQUEST, "local_transfer_encoding_unsupported")
            return
        started = time.perf_counter()
        provider_recorded = False
        provider_attempted = False
        local_cancelled = False
        try:
            gateway_token = self.server.gateway_credential()
        except Exception:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_credential_unavailable")
            return
        if not _valid_upstream_credential(gateway_token, self.server.upstream_auth):
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "gateway_credential_unavailable")
            return
        connection: http.client.HTTPConnection | None = None
        self._response_started = False
        try:
            connection = _gateway_connection(self.server.gateway, self.server.timeout_seconds)
            if not self.server._register_upstream(connection):
                self.close_connection = True
                return
            upstream_path = _gateway_path(self.server.gateway, path.path, path.query)
            if length <= MAX_REQUEST_BYTES:
                body = self.rfile.read(length)
                if len(body) != length:
                    self.close_connection = True
                    return
                changed, context_headers = self.server.optimizer.prepare(body, path.path)
                headers = _forward_headers(
                    self.headers,
                    gateway_token,
                    len(changed),
                    context_headers if self.server.upstream_auth == "hormuz" else {},
                    upstream_auth=self.server.upstream_auth,
                )
                # Registration precedes connect. A connection racing shutdown
                # closes before its first POST; a connected socket is shut
                # down without clearing connection.sock or allowing reconnect.
                provider_attempted = True
                connection.connect()
                if not self.server._attach_upstream_socket(connection):
                    self.close_connection = True
                    return
                connection.request("POST", upstream_path, body=changed, headers=headers)
            else:
                headers = _forward_headers(
                    self.headers,
                    gateway_token,
                    length,
                    {},
                    upstream_auth=self.server.upstream_auth,
                )
                provider_attempted = True
                connection.connect()
                if not self.server._attach_upstream_socket(connection):
                    self.close_connection = True
                    return
                connection.putrequest("POST", upstream_path, skip_accept_encoding=True)
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.endheaders()
                remaining = length
                while remaining:
                    try:
                        chunk = self.rfile.read(min(RELAY_CHUNK_BYTES, remaining))
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        local_cancelled = True
                        self.close_connection = True
                        return
                    if not chunk:
                        local_cancelled = True
                        self.close_connection = True
                        return
                    connection.send(chunk)
                    remaining -= len(chunk)
                self.server.optimizer.note_oversized_passthrough(length)
            response = connection.getresponse()
            self._relay_response(response, path.path, started)
            provider_recorded = True
        except (BrokenPipeError, ConnectionResetError):
            if provider_attempted:
                self._record_provider_failure(
                    started, cancelled=self.server._is_stopping()
                )
                provider_recorded = True
            self.close_connection = True
        except (OSError, ssl.SSLError, http.client.HTTPException):
            if provider_attempted:
                self._record_provider_failure(
                    started, cancelled=self.server._is_stopping()
                )
                provider_recorded = True
            if not self._response_started:
                self._error(HTTPStatus.BAD_GATEWAY, "gateway_unavailable")
            self.close_connection = True
        finally:
            if provider_attempted and not provider_recorded:
                self._record_provider_failure(
                    started,
                    cancelled=local_cancelled or self.server._is_stopping(),
                )
            if connection is not None:
                connection.close()
                self.server._unregister_upstream(connection)

    def _relay_response(
        self, response: http.client.HTTPResponse, request_path: str, started: float
    ) -> None:
        first_byte_ms: float | None = None
        protocol = self.server.adapter.protocol_for_path(request_path) or "responses"
        accumulator = ResponseUsageAccumulator(
            protocol, response.getheader("Content-Type")
        )
        accepted = 200 <= response.status < 300
        completed = False
        cancelled = False
        upstream_failed = False
        try:
            self._response_started = True
            self.send_response(response.status)
            for name, value in response.getheaders():
                lower = name.lower()
                if lower not in _HOP_HEADERS and lower != "set-cookie":
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                try:
                    # The first read is deliberately one byte so the recorded
                    # TTFT cannot collapse headers-to-first-body delay into
                    # header latency or wait for a larger buffered read.
                    chunk = response.read(
                        1 if first_byte_ms is None else RELAY_CHUNK_BYTES
                    )
                except (OSError, ssl.SSLError, http.client.HTTPException):
                    if self.server._is_stopping():
                        cancelled = True
                    else:
                        upstream_failed = True
                    self.close_connection = True
                    break
                if not chunk:
                    completed = response.length in {None, 0}
                    if not completed and self.server._is_stopping():
                        cancelled = True
                    else:
                        upstream_failed = not completed
                    if cancelled or upstream_failed:
                        self.close_connection = True
                    break
                if first_byte_ms is None:
                    first_byte_ms = max(
                        0.0, (time.perf_counter() - started) * 1000
                    )
                accumulator.feed(chunk)
                try:
                    self.wfile.write(chunk)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    cancelled = True
                    self.close_connection = True
                    break
        except (BrokenPipeError, ConnectionResetError):
            cancelled = True
            self.close_connection = True
        except (OSError, ssl.SSLError, http.client.HTTPException):
            if self.server._is_stopping():
                cancelled = True
            else:
                upstream_failed = True
            self.close_connection = True
        finally:
            response.close()
            if self.server.metrics is not None:
                try:
                    self.server.metrics.record_provider(
                        ProviderMeasurement(
                            succeeded=(
                                accepted
                                and completed
                                and not cancelled
                                and not upstream_failed
                            ),
                            cancelled=cancelled,
                            response_received=True,
                            response_bytes=accumulator.total_bytes,
                            time_to_first_byte_ms=first_byte_ms,
                            total_latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
                            usage=accumulator.usage(),
                        )
                    )
                except PersonalMetricsError:
                    self.server.metrics = None

    def _record_provider_failure(self, started: float, *, cancelled: bool) -> None:
        if self.server.metrics is None:
            return
        try:
            self.server.metrics.record_provider(
                ProviderMeasurement(
                    succeeded=False,
                    cancelled=cancelled,
                    response_received=False,
                    response_bytes=None,
                    time_to_first_byte_ms=None,
                    total_latency_ms=max(0.0, (time.perf_counter() - started) * 1000),
                    usage={},
                )
            )
        except PersonalMetricsError:
            self.server.metrics = None

    def do_CONNECT(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "local_method_not_allowed")

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path)
        # Codex refreshes provider model metadata at startup.  The personal
        # relay deliberately does not expose or proxy an upstream catalog:
        # its configured model is explicit and Codex retains its bundled
        # metadata.  A valid empty catalog keeps that read-only probe local.
        if (
            self.server.client_name == "codex"
            and not path.scheme
            and not path.netloc
            and not path.fragment
            and path.path == "/v1/models"
        ):
            if not self._authenticated():
                self._error(HTTPStatus.UNAUTHORIZED, "local_authentication_failed")
                return
            body = b'{"models":[]}'
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            return
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "local_method_not_allowed")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "local_method_not_allowed")

    def _authenticated(self) -> bool:
        values: list[str] = []
        authorizations = self.headers.get_all("Authorization", [])
        api_keys = self.headers.get_all("X-Api-Key", [])
        if len(authorizations) > 1 or len(api_keys) > 1:
            return False
        if authorizations:
            authorization = authorizations[0]
            if authorization.lower().startswith("bearer "):
                values.append(authorization[7:].strip())
        if api_keys and api_keys[0].strip():
            values.append(api_keys[0].strip())
        return len(values) == 1 and hmac.compare_digest(values[0], self.server.local_credential)

    def _error(self, status: HTTPStatus, code: str) -> None:
        body = json.dumps({"error": {"code": code}}, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # Request paths and model content are intentionally absent from logs.
        return


def new_local_credential() -> str:
    return "hox_l_" + secrets.token_urlsafe(32)


def probe_gateway_capability(gateway: str, *, timeout_seconds: float = 5) -> bool:
    connection: http.client.HTTPConnection | None = None
    try:
        allow_insecure = urllib.parse.urlsplit(gateway).scheme == "http"
        gateway = validate_session_gateway(gateway, allow_insecure_http=allow_insecure)
        connection = _gateway_connection(gateway, timeout_seconds)
        path = _gateway_path(gateway, "/health", "")
        connection.request("GET", path, headers={"Accept": "application/json", "Cache-Control": "no-store"})
        response = connection.getresponse()
        response.read(128 * 1024 + 1)
        compatible = (
            response.status == HTTPStatus.OK
            and response.getheader(CONTEXT_FORMATS_HEADER) == CONTEXT_FORMAT_VERSION
        )
        response.close()
        return compatible
    except (OSError, ssl.SSLError, http.client.HTTPException, SessionClientError, ValueError):
        return False
    finally:
        if connection is not None:
            connection.close()


def load_saved_profile(directory: Path, profile: str) -> SavedClientProfile:
    try:
        root = directory.lstat()
        if (
            not stat.S_ISDIR(root.st_mode)
            or directory.is_symlink()
            or root.st_uid != os.getuid()
            or root.st_mode & 0o077
        ):
            raise ClientRelayError("profile_invalid")
        path = directory / "profile.json"
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_size > 1024 * 1024
            ):
                raise ClientRelayError("profile_invalid")
            chunks: list[bytes] = []
            remaining = 1024 * 1024 + 1
            while remaining:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > 1024 * 1024:
                raise ClientRelayError("profile_invalid")
        finally:
            os.close(fd)
        value = strict_json_loads(data.decode("utf-8"))
    except ClientRelayError:
        raise
    except (OSError, CompactionFormatError, UnicodeDecodeError, RecursionError) as error:
        raise ClientRelayError("profile_unavailable") from error
    if not isinstance(value, dict):
        raise ClientRelayError("profile_invalid")
    required = {"id", "gateway", "organization", "client", "model", "allowLoopbackHTTP"}
    if not required <= set(value) or set(value) - (required | {"issuer", "setup"}):
        raise ClientRelayError("profile_invalid")
    identifier = value.get("id")
    organization = value.get("organization")
    issuer = value.get("issuer")
    client = value.get("client")
    model = value.get("model")
    allow = value.get("allowLoopbackHTTP")
    setup = value.get("setup")
    if (
        not isinstance(identifier, str)
        or _canonical_uuid(identifier) is None
        or identifier.lower() != profile.lower()
        or not _safe_profile_text(organization, maximum=200, required=True)
        or not _safe_profile_text(issuer, maximum=2048, required=False)
        or client not in {"codex", "claude-code"}
        or not isinstance(model, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model)
        or not isinstance(allow, bool)
        or (
            "setup" in value
            and (not isinstance(setup, str) or setup not in {"custom", "openai-pilot"})
        )
    ):
        raise ClientRelayError("profile_invalid")
    try:
        gateway = validate_session_gateway(value.get("gateway"), allow_insecure_http=allow)
    except (SessionClientError, TypeError) as error:
        raise ClientRelayError("profile_invalid") from error
    if setup == "openai-pilot" and (
        client != "codex"
        or model not in {"openai-primary", "openai-secondary"}
        or allow
        or urllib.parse.urlsplit(gateway).scheme != "https"
    ):
        raise ClientRelayError("profile_invalid")
    return SavedClientProfile(profile.lower(), gateway, client, model, allow)


def _canonical_uuid(value: str) -> str | None:
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None
    return parsed if value.lower() == parsed else None


def _safe_profile_text(value: object, *, maximum: int, required: bool) -> bool:
    if value is None:
        return not required
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= maximum
        and value == value.strip()
        and not any(character in value for character in ("\x00", "\r", "\n"))
    )


def run_client(
    *,
    profile: SavedClientProfile,
    state_directory: Path,
    credential_helper: Path,
    counters: Mapping[str, Callable[[str], int]] | None = None,
) -> int:
    if not credential_helper.is_absolute() or not credential_helper.is_file() or not os.access(credential_helper, os.X_OK):
        raise ClientRelayError("credential_helper_invalid")
    client_executable = supported_client_executable(profile.client)

    def gateway_credential() -> str:
        try:
            process = subprocess.run(
                [str(credential_helper), "credential", "--profile", profile.key,
                 "--state-directory", str(state_directory)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ClientRelayError("gateway_credential_unavailable") from error
        if process.returncode != 0 or len(process.stdout) > 4096:
            raise ClientRelayError("gateway_credential_unavailable")
        return process.stdout.decode("utf-8", errors="strict").strip()

    preference = ContextPreferenceStore(state_directory, profile.key)
    optimizer = RelayOptimizer(
        preference_store=preference,
        client=profile.client,
        gateway_compatible=probe_gateway_capability(profile.gateway),
        counters=counters,
    )
    local_credential = new_local_credential()
    server = LocalRelayServer(
        gateway=profile.gateway,
        client=profile.client,
        local_credential=local_credential,
        gateway_credential=gateway_credential,
        optimizer=optimizer,
    )
    thread = threading.Thread(target=server.serve_forever, name="hormuz-context-relay", daemon=True)
    thread.start()
    try:
        command, environment = _client_command(
            profile, server.origin, local_credential, executable=client_executable
        )
        process = subprocess.run(command, env=environment, check=False)
        return int(process.returncode)
    except OSError as error:
        raise ClientRelayError("client_launch_failed") from error
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _client_command(
    profile: SavedClientProfile,
    relay_origin: str,
    local_credential: str,
    *,
    executable: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    try:
        adapter = adapter_for(profile.client)
    except AdapterError as error:
        raise ClientRelayError("unsupported_client") from error
    executable = executable or shutil.which(adapter.identity.executable)
    if executable is None:
        raise ClientRelayError("supported_client_not_installed")
    plan = adapter.launch_plan(
        executable=executable,
        relay_origin=relay_origin,
        local_credential=local_credential,
        model=profile.model,
    )
    return list(plan.argv), plan.environment


def supported_client_executable(
    client: str,
    *,
    expected_version: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    try:
        adapter = adapter_for(client)
    except AdapterError as error:
        raise ClientRelayError("unsupported_client") from error
    command = adapter.identity.executable
    expected = expected_version or SUPPORTED_CLIENT_VERSIONS.get(
        client, adapter.identity.version
    )
    executable = shutil.which(command)
    if executable is None or expected is None:
        raise ClientRelayError("unsupported_client")
    try:
        completed = subprocess.run(
            [executable, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
            env=None if environment is None else dict(environment),
        )
        output = completed.stdout + b" " + completed.stderr
    except (OSError, subprocess.SubprocessError) as error:
        raise ClientRelayError("unsupported_client") from error
    if completed.returncode != 0 or len(output) > 4096:
        raise ClientRelayError("unsupported_client")
    try:
        match = _CLIENT_VERSION.search(output.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as error:
        raise ClientRelayError("unsupported_client") from error
    if match is None or match.group(1) != expected:
        raise ClientRelayError("unsupported_client")
    return executable


def _count_boundary_tokens(
    value: str, counters: Mapping[str, Callable[[str], int]]
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, counter in counters.items():
        count = counter(value)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("invalid counter result")
        counts[name] = count
    return counts


def _protocol_for_path(path: str) -> Protocol | None:
    if path in {"/v1/responses", "/v1/responses/compact"}:
        return "responses"
    if path in {"/v1/messages", "/v1/messages/count_tokens"}:
        return "anthropic"
    if path == "/v1/chat/completions":
        return "chat"
    return None


def _allowed_paths(client: str) -> frozenset[str]:
    try:
        adapter = adapter_for(client)
    except AdapterError:
        return frozenset()
    candidates = (
        "/v1/responses",
        "/v1/responses/compact",
        "/v1/messages",
        "/v1/messages/count_tokens",
        "/v1/chat/completions",
    )
    return frozenset(path for path in candidates if adapter.protocol_for_path(path) is not None)


def _gateway_connection(gateway: str, timeout: float) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlsplit(gateway)
    if parsed.scheme == "https":
        return http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout, context=ssl.create_default_context())
    return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)


def _gateway_path(gateway: str, path: str, query: str) -> str:
    base = urllib.parse.urlsplit(gateway).path.rstrip("/")
    result = base + path
    return result + ("?" + query if query else "")


def _forward_headers(
    headers: http.client.HTTPMessage,
    gateway_token: str,
    content_length: int,
    context_headers: Mapping[str, str],
    *,
    upstream_auth: Literal["hormuz", "openai", "anthropic"] = "hormuz",
) -> dict[str, str]:
    result = {
        "Content-Type": headers.get("Content-Type", "application/json"),
        "Accept": headers.get("Accept", "application/json"),
        "Content-Length": str(content_length),
        "Cache-Control": "no-store",
    }
    if upstream_auth == "anthropic":
        result["X-Api-Key"] = gateway_token
    else:
        result["Authorization"] = "Bearer " + gateway_token
    for name in ("User-Agent", "OpenAI-Beta", "Anthropic-Version", "Anthropic-Beta", "X-Hormuz-Work-Attribution"):
        value = headers.get(name)
        if value is not None and "\r" not in value and "\n" not in value:
            result[name] = value
    result.update(context_headers)
    return result


def _valid_upstream_credential(
    value: object, mode: Literal["hormuz", "openai", "anthropic"]
) -> bool:
    if mode == "hormuz":
        return isinstance(value, str) and _ACCESS_CREDENTIAL.fullmatch(value) is not None
    if not isinstance(value, str):
        return False
    try:
        encoded = value.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return (
        8 <= len(encoded) <= 4096
        and value == value.strip()
        and not any(character in value for character in ("\r", "\n", "\x00"))
    )
