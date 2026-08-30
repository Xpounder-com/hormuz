"""Bounded new-route transport; no changes to provider-compatible handlers."""

from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from .portfolio_wire import PortfolioError, REQUEST_BYTES


def _read_body(handler, length: int) -> bytes:
    deadline = time.monotonic() + 10
    chunks = []
    remaining = length
    while remaining:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise PortfolioError("invalid_request")
        handler.connection.settimeout(budget)
        # read1 performs at most one raw read. A slowly arriving body cannot
        # reset an inactivity timeout indefinitely inside BufferedReader.read.
        chunk = handler.rfile.read1(min(remaining, 65536))
        if not chunk or time.monotonic() > deadline:
            raise PortfolioError("invalid_request")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def handle_registry(handler) -> None:
    previous_timeout = handler.connection.gettimeout()
    try:
        # Authenticate and authorize before reading/parsing a submitted body,
        # touching the registry, or resolving any caller-supplied reference.
        credentials = handler.headers.get_all("Authorization", [])
        if len(credentials) != 1 or not credentials[0].lower().startswith("bearer "):
            raise PortfolioError("unauthenticated")
        principal = handler.server.portfolio_service.authenticate(credentials[0][7:].strip())
        if handler.headers.get_all("Transfer-Encoding", []):
            raise PortfolioError("invalid_request")
        lengths = handler.headers.get_all("Content-Length", [])
        data, key = b"", None
        if handler.command == "POST":
            types = handler.headers.get_all("Content-Type", [])
            if len(types) != 1 or types[0].lower().replace(" ", "") not in {"application/json", "application/json;charset=utf-8"}:
                raise PortfolioError("invalid_request")
            if len(lengths) != 1 or not re.fullmatch(r"(?:0|[1-9][0-9]{0,6})", lengths[0]):
                raise PortfolioError("invalid_request")
            length = int(lengths[0])
            if not 1 <= length <= REQUEST_BYTES:
                raise PortfolioError("invalid_request")
            keys = handler.headers.get_all("Idempotency-Key", [])
            if len(keys) != 1 or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", keys[0]):
                raise PortfolioError("invalid_request")
            key = keys[0]
            data = _read_body(handler, length)
        elif lengths and lengths != ["0"]:
            raise PortfolioError("invalid_request")
        parts = urlsplit(handler.path)
        status, result = handler.server.portfolio_service.dispatch_authorized(
            principal, handler.command, parts.path, query=parts.query, body=data, idempotency_key=key,
        )
    except PortfolioError as error:
        handler.close_connection = True
        status, result = error.status, error.envelope()
    except (OSError, TimeoutError):
        handler.close_connection = True
        error = PortfolioError("invalid_request")
        status, result = error.status, error.envelope()
    finally:
        handler.connection.settimeout(previous_timeout)
    handler._send_json(status, result, contract_header_value=f'{result["schema_id"]};version=1')
