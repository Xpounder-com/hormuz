"""Four-second bounded HTTP transport for Linear webhook deliveries."""

from __future__ import annotations

from http import HTTPStatus
import re
import threading
import time

from .contracts import contract_header
from .outcome_wire import REQUEST_BYTES
from .portfolio_wire import PortfolioError


LINEAR_EVENTS_PATH = "/v1/connectors/linear/events"
_TRANSPORT_SLOTS = threading.BoundedSemaphore(8)
_INTERNAL_BUDGET_SECONDS = 4


def _request_headers(handler) -> dict[str, str]:
    items = list(handler.headers.items())
    if len(items) > 64:
        raise PortfolioError("invalid_request")
    total = 0
    for name, value in items:
        try:
            total += len(name.encode("ascii")) + len(value.encode("utf-8"))
        except UnicodeError:
            raise PortfolioError("invalid_request") from None
    if total > 8192:
        raise PortfolioError("invalid_request")
    return dict(items)


def _read_body(handler, length: int, deadline: float) -> bytes:
    remaining, chunks = length, []
    while remaining:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise PortfolioError("unavailable")
        handler.connection.settimeout(budget)
        chunk = handler.rfile.read1(min(remaining, 65536))
        if (
            type(chunk) is not bytes
            or not chunk
            or len(chunk) > remaining
            or time.monotonic() >= deadline
        ):
            raise PortfolioError("invalid_request")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def handle_linear_webhook(handler) -> None:
    started = time.monotonic()
    deadline = started + _INTERNAL_BUDGET_SECONDS
    previous_timeout = handler.connection.gettimeout()
    acquired = False
    try:
        receiver = handler.server.linear_outcome_receiver
        if receiver is None:
            raise PortfolioError("forbidden")
        if handler.headers.get_all("Transfer-Encoding", []):
            raise PortfolioError("invalid_request")
        lengths = handler.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or re.fullmatch(r"[1-9][0-9]{0,6}", lengths[0]) is None:
            raise PortfolioError("invalid_request")
        length = int(lengths[0])
        if length > REQUEST_BYTES:
            raise PortfolioError("invalid_request")
        content_types = handler.headers.get_all("Content-Type", [])
        if (
            len(content_types) != 1
            or content_types[0].lower().replace(" ", "")
            not in {"application/json", "application/json;charset=utf-8"}
        ):
            raise PortfolioError("invalid_request")
        for name in ("Linear-Signature", "Linear-Delivery", "Linear-Event"):
            if len(handler.headers.get_all(name, [])) != 1:
                raise PortfolioError(
                    "unauthenticated" if name == "Linear-Signature" else "invalid_request"
                )
        if not _TRANSPORT_SLOTS.acquire(blocking=False):
            raise PortfolioError("rate_limited")
        acquired = True
        headers = _request_headers(handler)
        raw = _read_body(handler, length, deadline)
        result = receiver.ingest(headers, raw, deadline=deadline)
        if time.monotonic() >= deadline:
            raise PortfolioError("unavailable")
        status = HTTPStatus.OK
    except PortfolioError as error:
        handler.close_connection = True
        status, result = error.status, error.envelope()
    except (OSError, TimeoutError):
        handler.close_connection = True
        error = PortfolioError("invalid_request")
        status, result = error.status, error.envelope()
    finally:
        if acquired:
            _TRANSPORT_SLOTS.release()
        handler.connection.settimeout(previous_timeout)
    handler._send_json(
        status,
        result,
        contract_header_value=contract_header(result["schema_id"], result["schema_version"]),
    )
