"""Private generation-authenticated process host for connected-client lifetime."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading

from .connected_client import _read
from .config import GatewayConfig
from .server import GatewayServer


def main(record_path):
    record = json.loads(_read(Path(record_path), private=True))
    lock = os.open(str(record_path) + ".runtime-lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    server = control = None
    thread = None
    stopping = threading.Event()
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        server = GatewayServer(GatewayConfig.load(record["gateway_config"]))
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
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        while not stopping.is_set():
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
                    if request.get("generation") != record["generation"]:
                        continue
                    if request.get("command") == "stop":
                        server.begin_drain()
                        stopping.set()
                    client.sendall(json.dumps({"generation": record["generation"],
                        "router": "stopping" if stopping.is_set() else "ready"}).encode() + b"\n")
                except (OSError, ValueError):
                    continue
    finally:
        if server is not None:
            if thread is not None:
                server.shutdown()
                thread.join(timeout=2)
            server.server_close()
        if control is not None:
            control.close()
            Path(record["control"]).unlink(missing_ok=True)
        os.close(lock)


if __name__ == "__main__":
    main(sys.argv[1])
