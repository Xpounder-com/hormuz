from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .session_client import validate_session_gateway


_MAX_RESPONSE_BYTES = 512 * 1024
_DIMENSIONS = {"organization", "team", "person", "model", "client", "provider"}


class UsageAdminClientError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UsageAdminClient:
    def __init__(
        self,
        gateway: str,
        *,
        credential: str,
        allow_insecure_http: bool = False,
        timeout_seconds: float = 10,
    ):
        self.gateway = validate_session_gateway(
            gateway,
            allow_insecure_http=allow_insecure_http,
        )
        if (
            not credential
            or len(credential.encode("utf-8")) > 64 * 1024
            or any(character in credential for character in ("\n", "\r", "\x00"))
        ):
            raise UsageAdminClientError("invalid_credential")
        self.credential = credential
        self.timeout_seconds = timeout_seconds
        self._opener = urllib.request.build_opener(_NoRedirect)

    def report(
        self,
        *,
        group_by: str,
        actor_id: str | None = None,
        team_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, object]:
        if group_by not in _DIMENSIONS:
            raise UsageAdminClientError("invalid_usage_report_request")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise UsageAdminClientError("invalid_usage_report_request")
        query: dict[str, str] = {"group_by": group_by, "limit": str(limit)}
        for key, value in (("actor_id", actor_id), ("team_id", team_id)):
            if value is not None:
                _bounded_value(value, max_bytes=256)
                query[key] = value
        if cursor is not None:
            _bounded_value(cursor, max_bytes=4096)
            query["cursor"] = cursor
        response = self._request(
            "/v1/admin/usage?" + urllib.parse.urlencode(query)
        )
        required = {
            "schema_version",
            "organization_id",
            "group_by",
            "filters",
            "window",
            "coverage",
            "rows",
            "next_cursor",
        }
        if set(response) != required or response.get("schema_version") != 1:
            raise UsageAdminClientError("invalid_gateway_response")
        if response.get("group_by") != group_by:
            raise UsageAdminClientError("invalid_gateway_response")
        if not isinstance(response.get("organization_id"), str) or not response["organization_id"]:
            raise UsageAdminClientError("invalid_gateway_response")
        rows = response.get("rows")
        next_cursor = response.get("next_cursor")
        if not isinstance(rows, list) or len(rows) > limit:
            raise UsageAdminClientError("invalid_gateway_response")
        if next_cursor is not None:
            if not isinstance(next_cursor, str):
                raise UsageAdminClientError("invalid_gateway_response")
            try:
                _bounded_value(next_cursor, max_bytes=4096)
            except UsageAdminClientError as error:
                raise UsageAdminClientError("invalid_gateway_response") from error
        filters = response.get("filters")
        if filters != {"actor_id": actor_id, "team_id": team_id}:
            raise UsageAdminClientError("invalid_gateway_response")
        if not _valid_window(response.get("window")) or response.get("coverage") != {
            "scope": "gateway_captured_requests_only",
            "legacy_unattributed_rows_excluded": True,
            "provider_invoice_reconciled": False,
        }:
            raise UsageAdminClientError("invalid_gateway_response")
        for row in rows:
            if not _valid_row(row, group_by=group_by):
                raise UsageAdminClientError("invalid_gateway_response")
        return response

    def _request(self, path: str) -> dict[str, object]:
        request = urllib.request.Request(
            self.gateway + path,
            headers={
                "Accept": "application/json",
                "Authorization": "Bearer " + self.credential,
            },
            method="GET",
        )
        try:
            response = self._opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise UsageAdminClientError("gateway_unavailable") from error
        with response:
            status = int(response.getcode())
            if not _same_origin(self.gateway, response.geturl()):
                raise UsageAdminClientError("unexpected_gateway_redirect")
            body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise UsageAdminClientError("gateway_response_too_large")
        try:
            value = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as error:
            raise UsageAdminClientError("invalid_gateway_response") from error
        if not isinstance(value, dict):
            raise UsageAdminClientError("invalid_gateway_response")
        if not 200 <= status < 300:
            error_value = value.get("error")
            code = error_value.get("code") if isinstance(error_value, dict) else None
            raise UsageAdminClientError(
                code if isinstance(code, str) and code else "gateway_request_rejected"
            )
        return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _bounded_value(value: str, *, max_bytes: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > max_bytes
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise UsageAdminClientError("invalid_usage_report_request")


def _valid_window(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"start", "end", "timezone"}:
        return False
    if value.get("timezone") != "UTC" or not all(
        isinstance(value.get(key), str) for key in ("start", "end")
    ):
        return False
    try:
        start = datetime.fromisoformat(value["start"])
        end = datetime.fromisoformat(value["end"])
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        start.tzinfo is not None
        and start.utcoffset() is not None
        and end.tzinfo is not None
        and end.utcoffset() is not None
        and start <= end
    )


def _valid_row(value: object, *, group_by: str) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "scope_id",
        "scope_name",
        "requests",
        "succeeded",
        "failed",
        "denied",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "total_tokens",
        "billable_tokens",
        "cost_microusd",
        "estimated_cost_microusd",
        "unpriced_requests",
        "cost_bases",
        "currencies",
        "rate_card_versions",
        "active_actors",
        "redactions",
        "cost_usd",
        "estimated_cost_usd",
        "budget_usd",
        "budget_remaining_usd",
        "budget_used_percent",
    }
    extras = {
        "person": {"team_id", "team_name"},
        "model": {"protocol"},
        "client": {"client"},
        "provider": {"protocol"},
    }.get(group_by, set())
    if set(value) != required | extras:
        return False
    if not all(
        isinstance(value.get(key), str) and bool(value[key])
        for key in {"scope_id", "scope_name"} | extras
    ):
        return False
    integer_fields = required - {
        "scope_id",
        "scope_name",
        "cost_bases",
        "currencies",
        "rate_card_versions",
        "cost_usd",
        "estimated_cost_usd",
        "budget_usd",
        "budget_remaining_usd",
        "budget_used_percent",
    }
    if not all(
        isinstance(value.get(key), int)
        and not isinstance(value[key], bool)
        and value[key] >= 0
        for key in integer_fields
    ):
        return False
    if not all(
        isinstance(value.get(key), list)
        and all(isinstance(item, str) and bool(item) for item in value[key])
        for key in ("cost_bases", "currencies", "rate_card_versions")
    ):
        return False
    for key in (
        "cost_usd",
        "estimated_cost_usd",
        "budget_usd",
        "budget_remaining_usd",
        "budget_used_percent",
    ):
        item = value.get(key)
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0
        ):
            return False
    return True


def _same_origin(expected: str, actual: str) -> bool:
    left = urllib.parse.urlparse(expected)
    right = urllib.parse.urlparse(actual)
    return (
        left.scheme.lower(),
        (left.hostname or "").lower(),
        left.port or (443 if left.scheme.lower() == "https" else 80),
    ) == (
        right.scheme.lower(),
        (right.hostname or "").lower(),
        right.port or (443 if right.scheme.lower() == "https" else 80),
    )
