"""Finite loopback capacity checks; all provider bytes are synthetic."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import http.client
import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from hormuz._hosted_server import PROVIDER_MAX_CONNECTIONS, PROVIDER_MAX_INFERENCE_CONNECTIONS
from hormuz._hosted_work import initialize
from hormuz._hosted_work_server import (
    AI_WORK_HEALTH_PROBE_CONNECTIONS, AI_WORK_MAX_CONNECTIONS,
    AIWorkGatewayServer, AIWorkRequestHandler,
)
from hormuz.server import GatewayRequestHandler, GatewayServer
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import directory_setup
from tests.test_hosted_integrated import full_profile


class AIWorkHostedCapacityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config, selected, _, _ = full_profile(Path(temporary.name).resolve(), billing=False, portfolio=False)
        initialize(config, selected)
        self.config = replace(config, listen=replace(config.listen, port=0))
        self.gateway = AIWorkGatewayServer(self.config, environ=selected)
        self.handlers = []
        self.released_connections = set()
        self.handler_condition = threading.Condition()
        original_setup = AIWorkRequestHandler.setup
        original_release = self.gateway._release_connection

        def record_setup(handler):
            original_setup(handler)
            with self.handler_condition:
                self.handlers.append(handler)
                self.handler_condition.notify_all()

        def record_release(connection):
            original_release(connection)
            with self.handler_condition:
                self.released_connections.add(connection)
                self.handler_condition.notify_all()

        setup = patch.object(AIWorkRequestHandler, "setup", record_setup)
        setup.start()
        self.addCleanup(setup.stop)
        release = patch.object(self.gateway, "_release_connection", record_release)
        release.start()
        self.addCleanup(release.stop)
        self.thread = threading.Thread(target=self.gateway.serve_forever, kwargs={"poll_interval": 0.01})
        self.thread.start()
        self.addCleanup(self.stop)
        directory_setup(self.gateway.session_broker.directory, self.config)
        _, pair = activate_member(self.gateway.session_broker.store, self.gateway.session_broker.directory)
        self.headers = {"Authorization": "Bearer " + pair.access_token}
        self.identity = self.gateway.session_broker.authenticate(pair.access_token)

    def stop(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual({}, self.gateway._connection_lanes)
        self.assertEqual(PROVIDER_MAX_CONNECTIONS, self.gateway._connection_slots._value)
        self.assertEqual(AI_WORK_HEALTH_PROBE_CONNECTIONS, self.gateway._health_probe_slots._value)
        self.assertTrue(all(not handler._deadline.is_alive() for handler in self.handlers))

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=6)
        fields = {"Host": "gateway.example.test", "X-Hormuz-Ingress-Credential": self.config.ingress.credential,
                  **(headers or {})}
        encoded = None if body is None else json.dumps(body).encode()
        if encoded is not None:
            fields["Content-Type"] = "application/json"
        try:
            connection.connect()
            client_address = connection.sock.getsockname()
            connection.request(method, path, body=encoded, headers=fields)
            response = connection.getresponse()
            try:
                result = response.status, response.read(65537)
            finally:
                response.close()
        finally:
            connection.close()
        # Reading the response does not prove that finish() and admission-slot
        # cleanup have completed. Match this client to its actual server socket.
        self.wait_for_connection_release(client_address)
        return result

    def wait_for_connection_release(self, client_address):
        with self.handler_condition:
            self.assertTrue(self.handler_condition.wait_for(lambda: any(
                handler.client_address == client_address and handler.request in self.released_connections
                for handler in self.handlers), timeout=3), client_address)

    def wait_for_handlers(self, count):
        with self.handler_condition:
            self.assertTrue(self.handler_condition.wait_for(lambda: len(self.handlers) >= count, timeout=3))

    def test_health_and_readiness_survive_eight_streams_and_incomplete_normal_and_probe_clients(self):
        released = threading.Event()
        provider_condition = threading.Condition()
        responses = []

        class SyntheticStream:
            status = 200
            headers = {"Content-Type": "text/event-stream", "x-request-id": "synthetic-capacity"}

            def __init__(inner):
                inner.closed = False
                inner.sent = False

            def getcode(inner):
                return inner.status

            def read(inner, _size=-1):
                if inner.sent:
                    return b""
                if not released.wait(timeout=10):
                    raise TimeoutError("synthetic stream release deadline")
                inner.sent = True
                return (b'data: {"object":"chat.completion.chunk","model":"openai-primary-model",'
                        b'"choices":[{"index":0,"delta":{"content":"done"},"finish_reason":"stop"}],'
                        b'"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}\n\n'
                        b'data: [DONE]\n\n')

            def close(inner):
                inner.closed = True

        def open_synthetic(*_args, **_kwargs):
            response = SyntheticStream()
            with provider_condition:
                responses.append(response)
                provider_condition.notify_all()
            return response

        jobs = [self.gateway.work_runtime.create_work(self.identity, "fixture/repo-" + str(index))["work_id"]
                for index in range(PROVIDER_MAX_INFERENCE_CONNECTIONS)]
        clients = []
        with patch("hormuz.server._open_upstream", side_effect=open_synthetic) as provider, ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            try:
                for index, work_id in enumerate(jobs):
                    body = {"model": "openai-primary", "messages": [{"role": "user", "content": "capacity-" + str(index)}],
                            "max_tokens": 8, "stream": True}
                    futures.append(executor.submit(self.request, "POST", "/v1/chat/completions", body=body,
                        headers={**self.headers, "X-Hormuz-Work-ID": work_id}))
                with provider_condition:
                    self.assertTrue(provider_condition.wait_for(lambda: len(responses) == 8, timeout=4))
                self.assertEqual(8, self.gateway.operational_stats()["provider"]["inflight"])
                for count in (9, 10):
                    client = socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=3)
                    clients.append(client)
                    client.sendall(b"GET /work HTTP/1.1\r\nHost:")
                    self.wait_for_handlers(count)
                self.assertEqual((False, True), tuple(handler._health_only for handler in self.handlers[8:10]))
                for path in ("/health", "/ready"):
                    self.assertEqual(200, self.request("GET", path)[0])
                status, raw = self.request("GET", "/work")
                self.assertEqual(503, status)
                self.assertEqual("capacity_exhausted", json.loads(raw)["status"])
                self.assertEqual(8, provider.call_count)
            finally:
                for client in clients:
                    client.close()
                released.set()
                for future in futures:
                    self.assertEqual(200, future.result(timeout=6)[0])
        self.assertTrue(all(response.closed for response in responses))
        stats = self.gateway.operational_stats()["provider"]
        self.assertEqual((8, 0, AI_WORK_MAX_CONNECTIONS),
                         (stats["peak_inflight"], stats["inflight"], stats["connection_capacity"]))
        self.assertEqual(200, self.request("GET", "/work")[0])

    def test_sequential_probe_helper_waits_for_its_completed_connection_release(self):
        completed = threading.Event()
        helper_waiting = threading.Event()
        resume_finish = threading.Event()
        second_started = threading.Event()
        original_finish = AIWorkRequestHandler.finish
        original_wait = self.wait_for_connection_release

        def hold_completed_probe(handler):
            original_finish(handler)
            if getattr(handler, "path", None) == "/health":
                completed.set()
                if not resume_finish.wait(timeout=5):
                    raise TimeoutError("synthetic completed-probe release deadline")

        def observe_wait(client_address):
            helper_waiting.set()
            original_wait(client_address)

        def sequential_probes():
            first = self.request("GET", "/health")
            second_started.set()
            return first, self.request("GET", "/ready")

        for _ in range(PROVIDER_MAX_CONNECTIONS):
            self.assertTrue(self.gateway._connection_slots.acquire(blocking=False))
        # Reserve the other probe slot deterministically. The eight-stream test
        # above proves the same occupancy with actual incomplete clients.
        self.assertTrue(self.gateway._health_probe_slots.acquire(blocking=False))
        try:
            with patch.object(AIWorkRequestHandler, "finish", hold_completed_probe), patch.object(
                    self, "wait_for_connection_release", observe_wait), ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(sequential_probes)
                try:
                    self.assertTrue(completed.wait(timeout=3))
                    self.assertTrue(helper_waiting.wait(timeout=3))
                    self.assertFalse(second_started.is_set())
                    self.assertFalse(future.done())
                    self.assertEqual(0, self.gateway._health_probe_slots._value)
                finally:
                    resume_finish.set()
                first, second = future.result(timeout=6)
                self.assertEqual((200, 200), (first[0], second[0]))
                self.assertTrue(second_started.is_set())
        finally:
            resume_finish.set()
            self.gateway._health_probe_slots.release()
            for _ in range(PROVIDER_MAX_CONNECTIONS):
                self.gateway._connection_slots.release()

    def test_overflow_hard_deadline_closes_incomplete_headers_after_another_byte(self):
        expired = threading.Event()
        original_setup = AIWorkRequestHandler.setup
        original_expire = AIWorkRequestHandler._expire_connection

        def isolate_hard_deadline(handler):
            original_setup(handler)
            # Keep the read timeout from racing the real hard-deadline timer;
            # the production two-second timer itself remains unchanged.
            handler.connection.settimeout(4)

        def record_expiry(handler):
            original_expire(handler)
            expired.set()

        for _ in range(PROVIDER_MAX_CONNECTIONS):
            self.assertTrue(self.gateway._connection_slots.acquire(blocking=False))
        try:
            with patch.object(AIWorkRequestHandler, "setup", isolate_hard_deadline), patch.object(
                    AIWorkRequestHandler, "_expire_connection", record_expiry):
                with socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=3) as client:
                    client_address = client.getsockname()
                    client.sendall(b"GET /work HTTP/1.1\r\nHost:")
                    self.wait_for_handlers(1)
                    self.assertTrue(self.handlers[0]._health_only)
                    self.assertEqual(2, self.handlers[0]._deadline.interval)
                    client.sendall(b" ")
                    self.assertTrue(expired.wait(timeout=3))
                    self.assertEqual(b"", client.recv(1))
                    self.wait_for_connection_release(client_address)
        finally:
            for _ in range(PROVIDER_MAX_CONNECTIONS):
                self.gateway._connection_slots.release()
        self.assertEqual(200, self.request("GET", "/health")[0])

    def test_probe_lane_accepts_only_exact_health_methods_and_retains_ingress_host_and_drain_checks(self):
        for _ in range(PROVIDER_MAX_CONNECTIONS):
            self.assertTrue(self.gateway._connection_slots.acquire(blocking=False))
        try:
            with patch.object(GatewayRequestHandler, "do_GET") as ordinary_get, patch.object(GatewayRequestHandler, "do_POST") as ordinary_post:
                for method, path, body in (("GET", "/work", None), ("POST", "/health", {}),
                                           ("GET", "/health?extra=1", None), ("GET", "/%68ealth", None)):
                    self.assertEqual(503, self.request(method, path, body=body)[0], (method, path))
                for path in ("/health", "/ready"):
                    self.assertEqual((200, b""), self.request("HEAD", path))
                self.assertEqual(401, self.request("GET", "/health", headers={"X-Hormuz-Ingress-Credential": "wrong"})[0])
                self.assertEqual(400, self.request("GET", "/health", headers={"Host": "foreign.example.test"})[0])
                self.assertEqual(400, self.request("GET", "//health")[0])
                self.gateway.begin_drain()
                self.assertEqual(200, self.request("GET", "/health")[0])
                self.assertEqual(503, self.request("GET", "/ready")[0])
                ordinary_get.assert_not_called()
                ordinary_post.assert_not_called()
        finally:
            for _ in range(PROVIDER_MAX_CONNECTIONS):
                self.gateway._connection_slots.release()

    def test_handler_start_failures_release_each_owned_lane(self):
        request = Mock()
        with patch.object(GatewayServer, "process_request", side_effect=RuntimeError("synthetic start failure")):
            with self.assertRaisesRegex(RuntimeError, "start failure"):
                self.gateway.process_request(request, ("127.0.0.1", 1))
            for _ in range(PROVIDER_MAX_CONNECTIONS):
                self.assertTrue(self.gateway._connection_slots.acquire(blocking=False))
            try:
                with self.assertRaisesRegex(RuntimeError, "start failure"):
                    self.gateway.process_request(request, ("127.0.0.1", 1))
                request.settimeout.assert_called_once_with(2)
                self.assertEqual(AI_WORK_HEALTH_PROBE_CONNECTIONS, self.gateway._health_probe_slots._value)
                request.settimeout.side_effect = OSError("synthetic socket failure")
                with self.assertRaisesRegex(OSError, "socket failure"):
                    self.gateway.process_request(request, ("127.0.0.1", 1))
                self.assertEqual(AI_WORK_HEALTH_PROBE_CONNECTIONS, self.gateway._health_probe_slots._value)
            finally:
                for _ in range(PROVIDER_MAX_CONNECTIONS):
                    self.gateway._connection_slots.release()
        self.assertEqual({}, self.gateway._connection_lanes)
        original_release = self.gateway._release_connection
        for health_only in (False, True):
            released = threading.Event()

            def record_release(connection):
                original_release(connection)
                released.set()

            held = PROVIDER_MAX_CONNECTIONS if health_only else 0
            for _ in range(held):
                self.assertTrue(self.gateway._connection_slots.acquire(blocking=False))
            try:
                with patch.object(self.gateway, "_release_connection", side_effect=record_release), patch.object(
                        self.gateway, "RequestHandlerClass", side_effect=OSError("synthetic constructor failure")):
                    with socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=3) as client:
                        self.assertEqual(b"", client.recv(1))
                        self.assertTrue(released.wait(timeout=3))
                self.assertEqual({}, self.gateway._connection_lanes)
                self.assertEqual(PROVIDER_MAX_CONNECTIONS - held, self.gateway._connection_slots._value)
                self.assertEqual(AI_WORK_HEALTH_PROBE_CONNECTIONS, self.gateway._health_probe_slots._value)
            finally:
                for _ in range(held):
                    self.gateway._connection_slots.release()
