"""Owned loopback HTTP transport: no proxy, redirect, or orphaned connection."""
import http.client
import socket
import threading
import time
from urllib.parse import urlsplit


class LocalResponse:
    def __init__(self, connection, response, transport_socket, timer, expired, stopped):
        self.connection, self.response, self.transport_socket = connection, response, transport_socket
        self.status, self.headers = response.status, response.headers
        self.timer, self.expired = timer, expired
        self.stopped = stopped

    def _check_deadline(self):
        if self.expired.is_set():
            raise TimeoutError("local_request_deadline")

    def getcode(self):
        return self.status

    def _read(self, method, size):
        self._check_deadline()
        try:
            data = method(size)
        except (OSError, http.client.HTTPException):
            self._check_deadline()
            raise
        self._check_deadline()
        return data

    def read(self, size=-1):
        return self._read(self.response.read, size)

    def read1(self, size=-1):
        return self._read(self.response.read1, size)

    def set_read_timeout(self, seconds):
        if not self.response.isclosed():
            self.transport_socket.settimeout(seconds)

    def close(self):
        self.stopped.set()
        if self.timer is not None:
            self.timer.cancel()
        try:
            self.response.close()
        finally:
            self.connection.close()
            if self.timer is not None:
                self.timer.join(timeout=1)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def open_local_upstream(request, *, timeout, absolute_timeout=None, cancel_check=None):
    origin = urlsplit(request.full_url)
    connection_type = http.client.HTTPSConnection if origin.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(origin.hostname, origin.port, timeout=timeout)
    timer = None
    expired = threading.Event()
    stopped = threading.Event()
    deadline = time.monotonic() + absolute_timeout if absolute_timeout is not None else None
    try:
        connection.connect()
        transport_socket = connection.sock
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("local_request_deadline")
            def expire():
                if cancel_check is not None:
                    while not stopped.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or cancel_check():
                            break
                        stopped.wait(min(0.05, remaining))
                    if stopped.is_set():
                        return
                expired.set()
                try:
                    transport_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(remaining if cancel_check is None else 0, expire)
            timer.daemon = True
            timer.start()
        path = (origin.path or "/") + ("?" + origin.query if origin.query else "")
        connection.request(request.get_method(), path, body=request.data,
                           headers=dict(request.header_items()))
        response = connection.getresponse()
        return LocalResponse(connection, response, transport_socket, timer, expired, stopped)
    except BaseException as error:
        stopped.set()
        if timer is not None:
            timer.cancel()
        connection.close()
        if timer is not None:
            timer.join(timeout=1)
        if expired.is_set() and isinstance(error, (OSError, http.client.HTTPException)):
            raise TimeoutError("local_request_deadline") from None
        raise
