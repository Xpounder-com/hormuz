"""Private generation-authenticated process host for connected-client lifetime."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import subprocess

from .connected_client import _read, _parse_record, _lock_descriptor
from .config import GatewayConfig
from .server import GatewayServer
from .managed_llama import OwnedLlama


class _OwnedGatewayServer(GatewayServer):
    def __init__(self, config):
        self._capacity = threading.BoundedSemaphore(8)
        self._clients = set()
        self._clients_lock = threading.Lock()
        super().__init__(config)

    def process_request(self, request, client_address):
        if not self._capacity.acquire(blocking=False):
            self.close_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._capacity.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._capacity.release()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        with self._clients_lock:
            self._clients.add(connection)
        return connection, address

    def close_request(self, connection):
        try:
            super().close_request(connection)
        finally:
            with self._clients_lock:
                self._clients.discard(connection)

    def cancel_clients(self):
        with self._clients_lock:
            connections = tuple(self._clients)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()


def main(record_path, generation):
    import fcntl
    record = _parse_record(_read(Path(record_path), private=True))
    if record["generation"] != generation or record["phase"] not in {"prepared", "connected"}:
        return
    lock = _lock_descriptor(str(record_path) + ".runtime-lock")
    server = control = timer = engine = None
    thread = None
    stopping = threading.Event()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = _parse_record(_read(Path(record_path), private=True))
        if current["generation"] != record["generation"] or current["phase"] not in {"prepared", "connected"}:
            return
        source = _read(Path(record["gateway_config"]))
        if source is None or hashlib.sha256(source.encode()).hexdigest() != record["gateway_digest"]:
            return
        config = GatewayConfig.load(record["gateway_config"])
        if record.get("credential_helper"):
            identity = config.identities_by_actor.get(record["actor_id"])
            if (identity is None or identity.token_file is None
                    or hashlib.sha256(identity.token.encode()).hexdigest() != record["credential_helper"]["credential_sha256"]):
                return
        if _read(Path(record["gateway_config"])) != source:
            return
        if config.managed_llama is not None:
            engine = OwnedLlama(config.managed_llama, lock)
        server = _OwnedGatewayServer(config)
        current = _parse_record(_read(Path(record_path), private=True))
        if current["generation"] != record["generation"] or current["phase"] not in {"prepared", "connected"}:
            return
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        # The lock proves no prior generation host is using this endpoint.
        path = Path(record["control"])
        if path.exists():
            path.unlink()
        control.bind(str(path))
        os.chmod(path, 0o600)
        control.listen(2)
        control.settimeout(0.2)
        thread = threading.Thread(target=server.serve_forever, daemon=False)
        thread.start()
        current = _parse_record(_read(Path(record_path), private=True))
        if current["generation"] != generation or current["phase"] not in {"prepared", "connected"}:
            return
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        while not stopping.is_set():
            if engine is not None and engine.process.poll() is not None:
                server.begin_drain()
                break
            try:
                client, _ = control.accept()
            except socket.timeout:
                continue
            with client:
                client.settimeout(1)
                try:
                    with client.makefile("rb") as stream:
                        data = stream.readline(4097)
                    request = json.loads(data) if len(data) <= 4096 else {}
                    if not isinstance(request, dict) or request.get("command") not in {"stop", "status"} or request.get("generation") != record["generation"]:
                        continue
                    if request.get("command") == "stop":
                        server.begin_drain()
                        stopping.set()
                    client.sendall(json.dumps({"generation": record["generation"],
                        "router": "stopping" if stopping.is_set() else "ready" if server.readiness_reason() is None
                        and (engine is None or engine.ready()) else "unavailable"}).encode() + b"\n")
                except (OSError, ValueError):
                    continue
    finally:
        if engine is not None:
            try:
                engine.close()
            except subprocess.TimeoutExpired:
                # The guardian retains the inherited runtime lease until its
                # engine cleanup completes, even if this host must exit.
                pass
        if server is not None:
            def force_stop():
                # Only this owned host exits; the kernel releases all its sockets
                # and lease. Existing unknown-work recovery remains authoritative.
                os._exit(75)
            timer = threading.Timer(5, force_stop)
            timer.daemon = True
            timer.start()
            if thread is not None:
                server.shutdown()
                thread.join(timeout=2)
            server.cancel_clients()
            server.server_close()
        if control is not None:
            control.close()
            Path(record["control"]).unlink(missing_ok=True)
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1)
        os.close(lock)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
