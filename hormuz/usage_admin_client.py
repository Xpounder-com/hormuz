from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .session_client import validate_session_gateway
from .usage_reporting import (
    BUDGET_PACING_METHODOLOGY,
    IDENTITY_TYPES,
    LATENCY_BUCKETS_MS,
    utc_month_bounds,
)


_MAX_RESPONSE_BYTES = 512 * 1024
_MAX_SQLITE_INTEGER = 2**63 - 1
_DIMENSIONS = {"organization", "team", "person", "model", "client", "provider"}
_GATEWAY_CLIENTS = {"codex", "claude-code"}


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
        include_outcomes: bool = False,
    ) -> dict[str, object]:
        if group_by not in _DIMENSIONS:
            raise UsageAdminClientError("invalid_usage_report_request")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise UsageAdminClientError("invalid_usage_report_request")
        if not isinstance(include_latency, bool) or not isinstance(include_outcomes, bool):
            raise UsageAdminClientError("invalid_usage_report_request")
        query: dict[str, str] = {"group_by": group_by, "limit": str(limit)}
        include = (
            "latency,outcomes"
            if include_latency and include_outcomes
            else "latency"
            if include_latency
            else "outcomes"
            if include_outcomes
            else None
        )
        if include is not None:
            query["include"] = include
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
        expected_schemas = {
            (False, False): {2, 4},
            (True, False): {3, 5},
            (False, True): {6, 8},
            (True, True): {7, 9},
        }[(include_latency, include_outcomes)]
        schema_version = response.get("schema_version")
        if schema_version not in expected_schemas:
            raise UsageAdminClientError("invalid_gateway_response")
        constrained_scope = schema_version in {4, 5, 8, 9}
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
        if include_outcomes:
            expected_coverage.update(
                {
                    "outcome_scope": "recorded_gateway_policy_actions_only",
                    "historical_policy_denial_reasons_separately_classified": False,
                }
            )
        if not _valid_window(response.get("window")) or response.get("coverage") != expected_coverage:
            raise UsageAdminClientError("invalid_gateway_response")
        for row in rows:
            if not _valid_row(
                row,
                group_by=group_by,
                include_latency=include_latency,
                include_outcomes=include_outcomes,
            ):
                raise UsageAdminClientError("invalid_gateway_response")
        return response

    def coverage(self) -> dict[str, object]:
        """Read exactly the bounded gateway coverage the credential may inspect."""

        response = self._request("/v1/admin/usage/coverage")
        required = {"schema_version", "organization_id", "access", "window", "coverage"}
        if set(response) != required or response.get("schema_version") != 1:
            raise UsageAdminClientError("invalid_gateway_response")
        organization_id = response.get("organization_id")
        if not isinstance(organization_id, str) or not organization_id:
            raise UsageAdminClientError("invalid_gateway_response")
        if not _valid_coverage_access(response.get("access")):
            raise UsageAdminClientError("invalid_gateway_response")
        if not _valid_window(response.get("window")) or not _valid_coverage(response.get("coverage")):
            raise UsageAdminClientError("invalid_gateway_response")
        return response

    def pacing(self) -> dict[str, object]:
        """Read an advisory calendar-pace estimate for this credential's scope."""

        response = self._request("/v1/admin/usage/pacing")
        required = {
            "schema_version",
            "organization_id",
            "access",
            "filters",
            "window",
            "coverage",
            "budget_pacing",
        }
        if set(response) != required or response.get("schema_version") != 1:
            raise UsageAdminClientError("invalid_gateway_response")
        if not isinstance(response.get("organization_id"), str) or not response["organization_id"]:
            raise UsageAdminClientError("invalid_gateway_response")
        if not _valid_pacing_access(response.get("access"), response.get("filters")):
            raise UsageAdminClientError("invalid_gateway_response")
        if not _valid_pacing_window(response.get("window")):
            raise UsageAdminClientError("invalid_gateway_response")
        pacing = response.get("budget_pacing")
        if not _valid_budget_pacing(pacing) or not _valid_pacing_coverage(
            response.get("coverage"),
            partial_projection=bool(pacing["partial_projection"]),
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


def _valid_coverage_access(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"scope"}
        and value.get("scope") in {"self", "team", "finance", "organization"}
    )


def _valid_pacing_access(access: object, filters: object) -> bool:
    if not isinstance(access, dict) or set(access) != {"scope"}:
        return False
    if not isinstance(filters, dict) or set(filters) != {"actor_id", "team_id"}:
        return False
    scope = access.get("scope")
    actor_id = filters.get("actor_id")
    team_id = filters.get("team_id")
    for value in (actor_id, team_id):
        if value is not None:
            try:
                _bounded_value(value, max_bytes=256)
            except UsageAdminClientError:
                return False
    if scope in {"organization", "finance"}:
        return actor_id is None and team_id is None
    if scope == "self":
        return isinstance(actor_id, str) and team_id is None
    if scope == "team":
        return actor_id is None and isinstance(team_id, str)
    return False


def _valid_pacing_window(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"start", "as_of", "end", "timezone"}:
        return False
    if value.get("timezone") != "UTC" or not all(
        isinstance(value.get(key), str) for key in ("start", "as_of", "end")
    ):
        return False
    try:
        start = datetime.fromisoformat(value["start"])
        as_of = datetime.fromisoformat(value["as_of"])
        end = datetime.fromisoformat(value["end"])
        expected_start, expected_end = utc_month_bounds(as_of)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        start.tzinfo is not None
        and start.utcoffset() is not None
        and as_of.tzinfo is not None
        and as_of.utcoffset() is not None
        and end.tzinfo is not None
        and end.utcoffset() is not None
        and start.utcoffset() == timezone.utc.utcoffset(None)
        and as_of.utcoffset() == timezone.utc.utcoffset(None)
        and end.utcoffset() == timezone.utc.utcoffset(None)
        and start == expected_start
        and end == expected_end
        and start <= as_of <= end
    )


def _valid_pacing_coverage(value: object, *, partial_projection: bool) -> bool:
    expected = {
        "scope": "gateway_captured_requests_only",
        "legacy_unattributed_rows_excluded": True,
        "outside_gateway_traffic_observable": False,
        "provider_invoice_reconciled": False,
        "partial_projection": partial_projection,
    }
    return value == expected


def _valid_budget_pacing(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "methodology",
        "advisory_only",
        "policy_enforcement_basis",
        "elapsed_fraction",
        "early_period",
        "projection_available",
        "month_to_date_estimated_spend_microusd",
        "month_to_date_estimated_spend_usd",
        "average_estimated_spend_per_calendar_day_microusd",
        "average_estimated_spend_per_calendar_day_usd",
        "projected_month_end_estimated_spend_microusd",
        "projected_month_end_estimated_spend_usd",
        "configured_monthly_budget_usd",
        "projected_budget_utilization_percent",
        "projected_budget_overage_usd",
        "unpriced_requests",
        "partial_projection",
    }
    if set(value) != required or (
        value.get("methodology") != BUDGET_PACING_METHODOLOGY
        or value.get("advisory_only") is not True
        or value.get("policy_enforcement_basis")
        != "actual_usage_plus_active_reservations_only"
        or not isinstance(value.get("early_period"), bool)
        or not isinstance(value.get("projection_available"), bool)
        or not isinstance(value.get("partial_projection"), bool)
    ):
        return False
    if not _valid_nonnegative_int(value.get("month_to_date_estimated_spend_microusd")):
        return False
    if not _valid_nonnegative_int(value.get("unpriced_requests")):
        return False
    if value["partial_projection"] != (value["unpriced_requests"] > 0):
        return False
    elapsed_fraction = value.get("elapsed_fraction")
    if not _valid_nonnegative_number(elapsed_fraction) or float(elapsed_fraction) > 1:
        return False
    month_to_date_usd = value.get("month_to_date_estimated_spend_usd")
    if not _valid_nonnegative_number(month_to_date_usd) or not math.isclose(
        float(month_to_date_usd),
        int(value["month_to_date_estimated_spend_microusd"]) / 1_000_000,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        return False
    budget_usd = value.get("configured_monthly_budget_usd")
    if budget_usd is not None and not _valid_nonnegative_number(budget_usd):
        return False

    projection_available = value["projection_available"]
    projected_microusd = value.get("projected_month_end_estimated_spend_microusd")
    projected_usd = value.get("projected_month_end_estimated_spend_usd")
    average_microusd = value.get("average_estimated_spend_per_calendar_day_microusd")
    average_usd = value.get("average_estimated_spend_per_calendar_day_usd")
    if not projection_available:
        return (
            float(elapsed_fraction) == 0
            and all(
                item is None
                for item in (
                    projected_microusd,
                    projected_usd,
                    average_microusd,
                    average_usd,
                    value.get("projected_budget_utilization_percent"),
                    value.get("projected_budget_overage_usd"),
                )
            )
        )
    if not all(
        (
            _valid_nonnegative_int(average_microusd),
            _valid_nonnegative_number(average_usd),
            _valid_nonnegative_int(projected_microusd),
            _valid_nonnegative_number(projected_usd),
        )
    ):
        return False
    if (
        int(projected_microusd) < int(value["month_to_date_estimated_spend_microusd"])
        or not math.isclose(
            float(average_usd), int(average_microusd) / 1_000_000, rel_tol=0, abs_tol=1e-9
        )
        or not math.isclose(
            float(projected_usd), int(projected_microusd) / 1_000_000, rel_tol=0, abs_tol=1e-9
        )
    ):
        return False
    if budget_usd is None:
        return (
            value.get("projected_budget_utilization_percent") is None
            and value.get("projected_budget_overage_usd") is None
        )
    if not _valid_nonnegative_number(value.get("projected_budget_overage_usd")):
        return False
    utilization = value.get("projected_budget_utilization_percent")
    return utilization is None or _valid_nonnegative_number(utilization)


def _valid_nonnegative_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _valid_coverage(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    required = {
        "scope",
        "accounted_gateway_requests",
        "identity_bound_gateway_requests",
        "unattributed_accounted_gateway_requests",
        "active_identities",
        "active_teams",
        "identity_type_requests",
        "observed_gateway_clients",
        "legacy_unattributed_rows_excluded",
        "pre_authentication_attempts_included",
        "outside_gateway_traffic_observable",
        "provider_invoice_reconciled",
        "provider_invoice_reconciliation",
        "organization_total",
    }
    if set(value) != required:
        return False
    if (
        value.get("scope") != "accounted_authenticated_gateway_requests_only"
        or value.get("legacy_unattributed_rows_excluded") is not True
        or value.get("pre_authentication_attempts_included") is not False
        or value.get("outside_gateway_traffic_observable") is not False
        or value.get("provider_invoice_reconciled") is not False
        or value.get("provider_invoice_reconciliation")
        != "separate_billing_reconciliation_required"
        or value.get("organization_total") is not False
    ):
        return False
    integer_fields = {
        "accounted_gateway_requests",
        "identity_bound_gateway_requests",
        "unattributed_accounted_gateway_requests",
        "active_identities",
        "active_teams",
    }
    if not all(_valid_nonnegative_int(value.get(field)) for field in integer_fields):
        return False
    accounted = int(value["accounted_gateway_requests"])
    identity_bound = int(value["identity_bound_gateway_requests"])
    unattributed = int(value["unattributed_accounted_gateway_requests"])
    if (
        identity_bound + unattributed != accounted
        or int(value["active_identities"]) > identity_bound
        or int(value["active_teams"]) > identity_bound
    ):
        return False
    identity_types = value.get("identity_type_requests")
    if not isinstance(identity_types, dict) or set(identity_types) != set(IDENTITY_TYPES):
        return False
    if not all(_valid_nonnegative_int(identity_types.get(identity_type)) for identity_type in IDENTITY_TYPES):
        return False
    if sum(int(identity_types[identity_type]) for identity_type in IDENTITY_TYPES) != accounted:
        return False
    clients = value.get("observed_gateway_clients")
    if not isinstance(clients, list) or len(clients) > len(_GATEWAY_CLIENTS):
        return False
    observed_clients: set[str] = set()
    observed_requests = 0
    for client in clients:
        if (
            not isinstance(client, dict)
            or set(client) != {"client", "requests"}
            or not isinstance(client.get("client"), str)
            or client["client"] not in _GATEWAY_CLIENTS
            or client["client"] in observed_clients
            or not _valid_nonnegative_int(client.get("requests"))
            or int(client["requests"]) == 0
        ):
            return False
        observed_clients.add(client["client"])
        observed_requests += int(client["requests"])
    return observed_requests == accounted


def _valid_nonnegative_int(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and 0 <= value <= _MAX_SQLITE_INTEGER
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
    include_outcomes: bool = False,
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
    if include_outcomes:
        extras = extras | {"outcomes"}
    if set(value) != required | extras:
        return False
    if not all(
        isinstance(value.get(key), str) and bool(value[key])
        for key in {"scope_id", "scope_name"} | (extras - {"latency", "outcomes"})
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
    if include_outcomes and not _valid_outcomes(
        value.get("outcomes"),
        requests=int(value["requests"]),
    ):
        return False
    return True


def _valid_outcomes(value: object, *, requests: int) -> bool:
    required = {
        "model_fallback_requests",
        "output_capped_requests",
        "reservation_budget_denied_requests",
    }
    if not isinstance(value, dict) or set(value) != required:
        return False
    return all(
        _valid_nonnegative_int(value.get(field)) and int(value[field]) <= requests
        for field in required
    )


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
