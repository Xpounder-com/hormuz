from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timezone
from typing import Callable

from . import __version__
from .billing import (
    MAX_REPORT_PAGE_BYTES,
    MAX_REPORT_TOTAL_BYTES,
    ProviderBillingError,
    ProviderCostReport,
    ProviderCostSource,
    decode_provider_cost_page,
    parse_provider_cost_pages,
)


_MAX_FETCH_PAGES = 100
_MAX_CURSOR_BYTES = 4_096
_MAX_CREDENTIAL_BYTES = 64 * 1024
_MAX_WINDOW_DAYS = 366
_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/organization/costs",
    "anthropic": "https://api.anthropic.com/v1/organizations/cost_report",
}


class ProviderBillingClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProviderCostFetchResult:
    report: ProviderCostReport
    source: ProviderCostSource


class ProviderBillingClient:
    def __init__(
        self,
        provider: str,
        *,
        credential: str,
        timeout_seconds: float = 30,
        opener=None,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if provider not in _ENDPOINTS:
            raise ProviderBillingClientError("invalid_provider")
        if (
            not isinstance(credential, str)
            or not credential
            or len(credential.encode("utf-8")) > _MAX_CREDENTIAL_BYTES
            or any(ord(character) < 0x21 or ord(character) == 0x7F for character in credential)
        ):
            raise ProviderBillingClientError("invalid_credential")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= timeout_seconds <= 300
        ):
            raise ProviderBillingClientError("invalid_timeout")
        self.provider = provider
        self.credential = credential
        self.timeout_seconds = float(timeout_seconds)
        self._endpoint = _ENDPOINTS[provider]
        self._opener = opener or urllib.request.build_opener(_NoRedirect)
        self._sleeper = sleeper

    def fetch(self, *, start: date, end: date) -> ProviderCostFetchResult:
        if (
            not isinstance(start, date)
            or isinstance(start, datetime)
            or not isinstance(end, date)
            or isinstance(end, datetime)
            or end <= start
            or (end - start).days > _MAX_WINDOW_DAYS
        ):
            raise ProviderBillingClientError("invalid_billing_window")
        start_at = datetime.combine(start, datetime_time.min, tzinfo=timezone.utc)
        end_at = datetime.combine(end, datetime_time.min, tzinfo=timezone.utc)
        query_start = start_at.isoformat()
        query_end = end_at.isoformat()
        base_query = self._base_query(start_at=start_at, end_at=end_at)

        pages: list[dict[str, object]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        total_bytes = 0
        for _page_index in range(_MAX_FETCH_PAGES):
            query = [*base_query]
            if cursor is not None:
                query.append(("page", cursor))
            url = self._endpoint + "?" + urllib.parse.urlencode(query)
            payload = self._read_page(url)
            total_bytes += len(payload)
            if total_bytes > MAX_REPORT_TOTAL_BYTES:
                raise ProviderBillingClientError("provider_report_too_large")
            try:
                page = decode_provider_cost_page(payload)
            except ProviderBillingError:
                raise ProviderBillingClientError("invalid_provider_response") from None
            pages.append(page)
            has_more = page.get("has_more")
            next_page = page.get("next_page")
            if not isinstance(has_more, bool):
                raise ProviderBillingClientError("invalid_provider_response")
            if not has_more:
                if next_page is not None:
                    raise ProviderBillingClientError("invalid_provider_response")
                break
            if not _valid_cursor(next_page):
                raise ProviderBillingClientError("invalid_provider_response")
            assert isinstance(next_page, str)
            if next_page in seen_cursors:
                raise ProviderBillingClientError("repeated_provider_cursor")
            seen_cursors.add(next_page)
            cursor = next_page
        else:
            raise ProviderBillingClientError("provider_page_limit_exceeded")

        try:
            report = parse_provider_cost_pages(
                self.provider,
                pages,
                expected_start=query_start,
                expected_end=query_end,
            )
            source = ProviderCostSource.authenticated(
                provider=self.provider,
                query_start=query_start,
                query_end=query_end,
            )
        except ProviderBillingError:
            raise ProviderBillingClientError("invalid_provider_response") from None
        return ProviderCostFetchResult(report=report, source=source)

    def _base_query(
        self,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[tuple[str, str]]:
        if self.provider == "openai":
            return [
                ("start_time", str(int(start_at.timestamp()))),
                ("end_time", str(int(end_at.timestamp()))),
                ("bucket_width", "1d"),
                ("limit", "180"),
                ("group_by", "project_id"),
                ("group_by", "line_item"),
            ]
        return [
            ("starting_at", start_at.strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("ending_at", end_at.strftime("%Y-%m-%dT%H:%M:%SZ")),
            ("bucket_width", "1d"),
            ("limit", "31"),
            ("group_by[]", "workspace_id"),
            ("group_by[]", "description"),
        ]

    def _read_page(self, url: str) -> bytes:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"Hormuz/{__version__} billing",
        }
        if self.provider == "openai":
            headers["Authorization"] = "Bearer " + self.credential
        else:
            headers["x-api-key"] = self.credential
            headers["anthropic-version"] = "2023-06-01"
        request = urllib.request.Request(url, headers=headers, method="GET")

        for attempt in range(3):
            try:
                response = self._opener.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as error:
                status = int(error.code)
                delay = _retry_delay(error.headers, attempt)
                error.close()
                if status in _RETRYABLE_STATUSES and attempt < 2:
                    self._sleeper(delay)
                    continue
                raise ProviderBillingClientError(_status_error(status)) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                if attempt < 2:
                    self._sleeper(0.25 * (2**attempt))
                    continue
                raise ProviderBillingClientError("provider_unavailable") from None

            with response:
                status = int(response.getcode())
                if not _same_origin(self._endpoint, response.geturl()):
                    raise ProviderBillingClientError("unexpected_provider_redirect")
                if not 200 <= status < 300:
                    raise ProviderBillingClientError(_status_error(status))
                payload = response.read(MAX_REPORT_PAGE_BYTES + 1)
            if len(payload) > MAX_REPORT_PAGE_BYTES:
                raise ProviderBillingClientError("provider_response_too_large")
            return payload
        raise ProviderBillingClientError("provider_unavailable")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _same_origin(expected: str, actual: str) -> bool:
    left = urllib.parse.urlparse(expected)
    right = urllib.parse.urlparse(actual)
    return (
        left.scheme.lower(),
        (left.hostname or "").lower(),
        left.port or 443,
    ) == (
        right.scheme.lower(),
        (right.hostname or "").lower(),
        right.port or 443,
    )


def _valid_cursor(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value.encode("utf-8")) <= _MAX_CURSOR_BYTES
        and not any(character in value for character in ("\n", "\r", "\x00"))
    )


def _status_error(status: int) -> str:
    if status in {401, 403}:
        return "provider_authentication_failed"
    if status == 429:
        return "provider_rate_limited"
    if 300 <= status < 400:
        return "unexpected_provider_redirect"
    if 400 <= status < 500:
        return "provider_request_rejected"
    return "provider_unavailable"


def _retry_delay(headers, attempt: int) -> float:
    raw = headers.get("Retry-After") if headers is not None else None
    if isinstance(raw, str) and raw.isdigit():
        return float(min(int(raw), 30))
    return 0.25 * (2**attempt)
