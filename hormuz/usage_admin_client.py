from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .session_client import validate_session_gateway
from .usage_reporting import LATENCY_BUCKETS_MS


_MAX_RESPONSE_BYTES = 512 * 1024
_MAX_SQLITE_INTEGER = 2**63 - 1
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
        include_latency: bool = False,
    ) -> dict[str, object]:
        if group_by not in _DIMENSIONS:
            raise UsageAdminClientError("invalid_usage_report_request")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise UsageAdminClientError("invalid_usage_report_request")
        if not isinstance(include_latency, bool):
            raise UsageAdminClientError("invalid_usage_report_request")
        query: dict[str, str] = {"group_by": group_by, "limit": str(limit)}
        if include_latency:
            query["include"] = "latency"
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
        expected_schemas = {3, 5} if include_latency else {2, 4}
        schema_version = response.get("schema_version")
        if schema_version not in expected_schemas:
            raise UsageAdminClientError("invalid_gateway_response")
        constrained_scope = schema_version in {4, 5}
        if constrained_scope:
            required.add("access")
        if set(response) != required:
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
        if constrained_scope:
            if not _valid_constrained_access(
                response.get("access"),
                filters,
                group_by=group_by,
                requested_actor_id=actor_id,
                requested_team_id=team_id,
            ):
                raise UsageAdminClientError("invalid_gateway_response")
        elif filters != {"actor_id": actor_id, "team_id": team_id}:
            raise UsageAdminClientError("invalid_gateway_response")
        expected_coverage = {
            "scope": "gateway_captured_requests_only",
            "legacy_unattributed_rows_excluded": True,
            "provider_invoice_reconciled": False,
        }
        if include_latency:
            expected_coverage.update(
                {
                    "latency_scope": "accounted_gateway_requests_only",
                    "latency_historical_rows_excluded": True,
                    "latency_targets_configured": False,
                }
            )
        if not _valid_window(response.get("window")) or response.get("coverage") != expected_coverage:
            raise UsageAdminClientError("invalid_gateway_response")
        for row in rows:
            if not _valid_row(
                row,
                group_by=group_by,
                include_latency=include_latency,
            ):
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


def _valid_constrained_access(
    access: object,
    filters: object,
    *,
    group_by: str,
    requested_actor_id: str | None,
    requested_team_id: str | None,
) -> bool:
    if not isinstance(access, dict) or set(access) != {"scope"}:
        return False
    scope = access.get("scope")
    if scope not in {"self", "team", "finance"}:
        return False
    if not isinstance(filters, dict) or set(filters) != {"actor_id", "team_id"}:
        return False
    actor_id = filters.get("actor_id")
    team_id = filters.get("team_id")
    for value in (actor_id, team_id):
        if value is not None:
            try:
                _bounded_value(value, max_bytes=256)
            except UsageAdminClientError:
                return False
    if requested_actor_id is not None and actor_id != requested_actor_id:
        return False
    if requested_team_id is not None and team_id != requested_team_id:
        return False
    if scope == "self":
        return isinstance(actor_id, str)
    if scope == "team":
        return group_by != "person" and actor_id is None and isinstance(team_id, str)
    return (
        group_by in {"organization", "model", "client", "provider"}
        and actor_id is None
        and team_id is None
    )


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


def _valid_row(
    value: object,
    *,
    group_by: str,
    include_latency: bool = False,
) -> bool:
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
        "context_injected_requests",
        "context_required_denials",
        "context_estimated_tokens",
        "context_packs_used",
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
    if include_latency:
        extras = extras | {"latency"}
    if set(value) != required | extras:
        return False
    if not all(
        isinstance(value.get(key), str) and bool(value[key])
        for key in {"scope_id", "scope_name"} | (extras - {"latency"})
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
    if include_latency and not _valid_latency(value.get("latency")):
        return False
    return True


def _valid_latency(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "gateway",
        "policy",
        "provider",
        "context",
    }:
        return False
    expected_limits = [*LATENCY_BUCKETS_MS, None]
    for histogram in value.values():
        if not isinstance(histogram, dict) or set(histogram) != {
            "count",
            "average_ms",
            "max_ms",
            "buckets",
        }:
            return False
        count = histogram.get("count")
        if (
            isinstance(count, bool)
            or not isinstance(count, int)
            or not 0 <= count <= _MAX_SQLITE_INTEGER
        ):
            return False
        average = histogram.get("average_ms")
        maximum = histogram.get("max_ms")
        if count == 0:
            if average is not None or maximum is not None:
                return False
        elif (
            isinstance(average, bool)
            or not isinstance(average, (int, float))
            or not math.isfinite(average)
            or average < 0
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 0 <= maximum <= _MAX_SQLITE_INTEGER
            or average > maximum
        ):
            return False
        buckets = histogram.get("buckets")
        if not isinstance(buckets, list) or len(buckets) != len(expected_limits):
            return False
        previous = -1
        for bucket, expected_limit in zip(buckets, expected_limits, strict=True):
            if not isinstance(bucket, dict) or set(bucket) != {"le_ms", "count"}:
                return False
            if bucket.get("le_ms") != expected_limit:
                return False
            bucket_count = bucket.get("count")
            if (
                isinstance(bucket_count, bool)
                or not isinstance(bucket_count, int)
                or bucket_count > _MAX_SQLITE_INTEGER
                or not previous <= bucket_count <= count
            ):
                return False
            previous = bucket_count
        if previous != count:
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
