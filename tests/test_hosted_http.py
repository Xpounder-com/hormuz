from __future__ import annotations

import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from hormuz._hosted_config import HostedError
from hormuz._hosted_server import StagingGatewayServer
from hormuz._hosted_state import initialize
from tests._console_fixtures import activate_member
from tests._hosted_fixtures import directory_setup, profile


@unittest.skipUnless(os.name == "posix", "The staging runtime uses POSIX file locks and permissions")
class HostedHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.config, self.settings, _ = profile(self.root)
        initialize(self.config)
        self.start()

    def start(self):
        self.gateway = StagingGatewayServer(replace(self.config, listen=replace(self.config.listen, port=0)))
        self.thread = threading.Thread(target=self.gateway.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def stop(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=2)

    def tearDown(self):
        self.stop()

    def request(self, method, path, *, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=2)
        fields = {"Host": "gateway.example.test", "X-Hormuz-Ingress-Credential": self.config.ingress.credential}
        fields.update(headers or {})
        if body is not None:
            fields.setdefault("Content-Type", "application/json")
            body = json.dumps(body).encode()
        try:
            connection.request(method, path, body=body, headers=fields)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def raw(self, request: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=2) as connection:
            connection.sendall(request)
            chunks = []
            while True:
                chunk = connection.recv(16384)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def test_private_hop_host_and_provider_boundaries(self):
        self.assertEqual(self.request("GET", "/health")[0], 200)
        status, _, body = self.request("HEAD", "/health")
        self.assertEqual((status, body), (200, b""))
        self.assertFalse(json.loads(self.request("GET", "/health")[2])["inference_enabled"])
        for path in ("/health", "/ready", "/console", "/v1/auth/enrollments", "/v1/responses"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, headers={"X-Hormuz-Ingress-Credential": "wrong"})[0], 401)
                self.assertEqual(self.request("GET", path, headers={"Host": "foreign.example.test"})[0], 400)
        for path in ("/v1/responses", "/v1/messages", "/v1/models", "/v1/portfolio/projects", "/v1/policy/evaluate"):
            with self.subTest(path=path):
                status, _, body = self.request("POST", path, body={"model": "any", "input": "synthetic boundary canary"})
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body)["status"], "route_disabled")
        self.assertEqual(self.gateway.upstream_credentials, {})

    def test_malformed_and_ambiguous_framing_closes_without_echo(self):
        ingress = self.config.ingress.credential
        base = f"Host: gateway.example.test\r\nX-Hormuz-Ingress-Credential: {ingress}\r\n"
        suffixes = [
            "Content-Length: 0\r\nContent-Length: 0\r\n",
            "Content-Length: 0\r\nTransfer-Encoding: chunked\r\n",
            "Content-Length: -1\r\n", "Content-Length: +1\r\n",
            "Host: foreign.example.test\r\nContent-Length: 0\r\n",
            "Authorization: first\r\nAuthorization: second\r\nContent-Length: 0\r\n",
            "Origin: null\r\nOrigin: https://gateway.example.test\r\nContent-Length: 0\r\n",
            "Content-Length: 16385\r\n", "Content-Length: 0\r\nExpect: 100-continue\r\n",
        ]
        for suffix in suffixes:
            with self.subTest(headers=suffix):
                response = self.raw(("POST /v1/auth/enrollments HTTP/1.1\r\n" + base + suffix + "\r\n" + "synthetic_never_echo_this").encode())
                self.assertIn(response.split(b"\r\n", 1)[0].split()[1], (b"400", b"413", b"417"))
                self.assertNotIn(b"synthetic_never_echo_this", response)
                self.assertNotIn(ingress.encode(), response)
        for target in ("https://gateway.example.test/health", "//gateway.example.test/health", "/health#fragment", "/health?canary=synthetic_never_echo_this"):
            response = self.raw((f"GET {target} HTTP/1.1\r\n" + base + "\r\n").encode())
            self.assertIn(b"400", response.split(b"\r\n", 1)[0])
            self.assertNotIn(b"synthetic_never_echo_this", response)
        response = self.raw(("GET /health HTTP/1.1\r\n" + base + f"X-Hormuz-Ingress-Credential: {ingress}\r\n\r\n").encode())
        self.assertIn(b"401", response.split(b"\r\n", 1)[0])

    def test_authenticated_usage_survives_restart_and_removal_stays_revoked(self):
        store = self.gateway.session_broker.store
        directory = self.gateway.session_broker.directory
        directory_setup(directory, self.config)
        member, pair = activate_member(store, directory)
        headers = {"Authorization": "Bearer " + pair.access_token}
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=headers)[0], 200)
        self.assertEqual(self.request("GET", "/v1/gateway/usage", headers=headers)[0], 200)
        self.stop()
        self.start()
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=headers)[0], 200)
        self.gateway.session_broker.directory.disable_member(organization_id="customer-a", membership_id=member.membership_id)
        self.stop()
        self.start()
        self.assertEqual(self.request("GET", "/v1/gateway/whoami", headers=headers)[0], 401)
        self.assertEqual(self.request("POST", "/v1/auth/refresh", body={"refresh_token": pair.refresh_token})[0], 401)

    def test_missing_live_state_fails_closed_without_recreating_it(self):
        self.config.database_path.unlink()
        self.assertEqual(self.request("GET", "/ready")[0], 503)
        self.assertEqual(self.request("GET", "/console")[0], 503)
        self.assertFalse(self.config.database_path.exists())

    def test_slow_connections_are_bounded_and_release_capacity(self):
        self.stop()
        with patch("hormuz._hosted_server.MAX_CONNECTIONS", 2), patch("hormuz._hosted_server.SOCKET_TIMEOUT", 0.2), patch("hormuz._hosted_server.CONNECTION_LIFETIME", 0.5):
            self.start()
            connections = []
            try:
                for _ in range(2):
                    connection = socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=1)
                    connections.append(connection)
                    connection.sendall(b"GET ")
                deadline = time.monotonic() + 1
                while self.gateway._connection_slots._value and time.monotonic() < deadline:
                    time.sleep(0.005)
                with socket.create_connection(("127.0.0.1", self.gateway.server_port), timeout=1) as rejected:
                    self.assertEqual(rejected.recv(1), b"")
                for connection in connections:
                    self.assertEqual(connection.recv(1), b"")
            finally:
                for connection in connections:
                    connection.close()
            self.assertEqual(self.request("GET", "/health")[0], 200)

    def test_profile_cannot_construct_a_gateway_with_provider_routes(self):
        with self.assertRaisesRegex(HostedError, "configuration_unsafe"):
            StagingGatewayServer(replace(self.config, model_routes={"unexpected": object()}))
