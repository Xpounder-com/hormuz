"""Parent-owned deadline for native DNS, TLS and response transport."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import select
import socket


class NativeClientCanceled(OSError):
    """The client is gone; never replay its native request."""


def client_canceled(connection):
    try:
        ready, _, _ = select.select([connection], [], [], 0)
        return bool(ready) and connection.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True

def stream_native_response(*, authorization, account, body, timeout, connection):
    deadline = time.monotonic() + min(timeout, 600)
    header_deadline = min(deadline, time.monotonic() + 3)
    worker = Path(__file__).with_name("_native_responses_worker.py").resolve()
    environment = {key: os.environ[key] for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SYSTEMROOT") if key in os.environ}
    with subprocess.Popen([sys.executable, "-I", "-S", str(worker)], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment) as process:
        stopped = threading.Event()
        events = queue.Queue(maxsize=2)
        def put(item):
            while not stopped.is_set():
                try:
                    events.put(item, timeout=0.05)
                    return
                except queue.Full:
                    pass
        def pump():
            try:
                process.stdin.write(json.dumps({"authorization": authorization,
                    "account": account, "body": body.decode("utf-8")}).encode())
                process.stdin.close()
                header = process.stdout.readline(8193)
                if len(header) > 8192 or not header.endswith(b"\n"):
                    raise ValueError("native_transport_header")
                if json.loads(header) != {"connected": True}:
                    raise ValueError("native_transport_connect")
                put(("connected", None))
                header = process.stdout.readline(8193)
                if len(header) > 8192 or not header.endswith(b"\n"):
                    raise ValueError("native_transport_header")
                put(("headers", json.loads(header)))
                while not stopped.is_set():
                    chunk = process.stdout.read1(65536)
                    if not chunk:
                        break
                    put(("chunk", chunk))
                process.wait(timeout=1)
                if process.returncode:
                    raise OSError("native_transport_failed")
                put(("end", None))
            except (OSError, ValueError, subprocess.TimeoutExpired):
                put(("error", None))
        reader = threading.Thread(target=pump, name="native-response-pipe")
        reader.start()
        try:
            headers_received = False
            while True:
                if client_canceled(connection):
                    raise NativeClientCanceled("native_client_canceled")
                remaining = (deadline if headers_received else header_deadline) - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("native_response_deadline")
                try:
                    kind, data = events.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    continue
                if kind == "error":
                    raise OSError("native_response_unavailable")
                if kind == "end":
                    return
                if kind == "connected":
                    header_deadline = min(deadline, time.monotonic() + 30)
                    continue
                headers_received = headers_received or kind == "headers"
                yield data
        finally:
            stopped.set()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            reader.join(timeout=1)
