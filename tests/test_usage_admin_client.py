from __future__ import annotations

import unittest
from unittest import mock

from hormuz.usage_admin_client import UsageAdminClient, UsageAdminClientError


def _row() -> dict[str, object]:
    return {
        "scope_id": "engineering",
        "scope_name": "Engineering",
        "requests": 1,
        "succeeded": 1,
        "failed": 0,
        "denied": 0,
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 12,
        "billable_tokens": 12,
        "cost_microusd": 1000,
        "estimated_cost_microusd": 1000,
        "unpriced_requests": 0,
        "cost_bases": ["estimated"],
        "currencies": ["USD"],
        "rate_card_versions": ["test-v1"],
        "active_actors": 1,
        "redactions": 0,
        "context_injected_requests": 0,
        "context_required_denials": 0,
        "context_estimated_tokens": 0,
        "context_packs_used": 0,
        "cost_usd": 0.001,
        "estimated_cost_usd": 0.001,
        "budget_usd": 10.0,
        "budget_remaining_usd": 9.999,
        "budget_used_percent": 0.01,
    }


def _response() -> dict[str, object]:
    return {
        "schema_version": 2,
        "organization_id": "xpounder",
        "group_by": "team",
        "filters": {"actor_id": None, "team_id": None},
        "window": {
            "start": "2026-08-01T00:00:00+00:00",
            "end": "2026-08-16T12:00:00+00:00",
            "timezone": "UTC",
        },
        "coverage": {
            "scope": "gateway_captured_requests_only",
            "legacy_unattributed_rows_excluded": True,
            "provider_invoice_reconciled": False,
        },
        "rows": [_row()],
        "next_cursor": None,
    }


def _latency_response() -> dict[str, object]:
    response = _response()
    response["schema_version"] = 3
    response["coverage"] = {
        **response["coverage"],  # type: ignore[arg-type]
        "latency_scope": "accounted_gateway_requests_only",
        "latency_historical_rows_excluded": True,
        "latency_targets_configured": False,
    }
    response["rows"][0]["latency"] = {  # type: ignore[index]
        name: {
            "count": 1,
            "average_ms": average,
            "max_ms": maximum,
            "buckets": [
                {"le_ms": 1, "count": int(maximum <= 1)},
                {"le_ms": 5, "count": int(maximum <= 5)},
                {"le_ms": 10, "count": int(maximum <= 10)},
                {"le_ms": 25, "count": int(maximum <= 25)},
                {"le_ms": 50, "count": int(maximum <= 50)},
                {"le_ms": 100, "count": int(maximum <= 100)},
                {"le_ms": 250, "count": int(maximum <= 250)},
                {"le_ms": 500, "count": int(maximum <= 500)},
                {"le_ms": 1000, "count": int(maximum <= 1000)},
                {"le_ms": 10000, "count": int(maximum <= 10000)},
                {"le_ms": 60000, "count": int(maximum <= 60000)},
                {"le_ms": 600000, "count": int(maximum <= 600000)},
                {"le_ms": None, "count": 1},
            ],
        }
        for name, average, maximum in (
            ("gateway", 18.0, 18),
            ("policy", 2.0, 2),
            ("provider", 14.0, 14),
            ("context", 3.0, 3),
        )
    }
    return response


class UsageAdminClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = UsageAdminClient(
            "http://127.0.0.1:8787",
            credential="viewer-token-long",
            allow_insecure_http=True,
        )

    def test_exact_metadata_only_response_shape_is_required(self) -> None:
        response = _response()
        with mock.patch.object(self.client, "_request", return_value=response):
            self.assertEqual(self.client.report(group_by="team"), response)

        unsafe = _response()
        unsafe["rows"][0]["prompt"] = "must-not-be-accepted"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team")

        wrong_scope = _response()
        wrong_scope["filters"] = {"actor_id": "someone-else", "team_id": None}
        with mock.patch.object(self.client, "_request", return_value=wrong_scope):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team")

    def test_invalid_local_query_fails_before_network_work(self) -> None:
        with mock.patch.object(self.client, "_request") as request:
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_usage_report_request"):
                self.client.report(group_by="team", actor_id="x" * 257)
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_usage_report_request"):
                self.client.report(group_by="team", limit=101)
        request.assert_not_called()

    def test_latency_view_is_explicit_versioned_and_strict(self) -> None:
        response = _latency_response()
        with mock.patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(
                self.client.report(group_by="team", include_latency=True),
                response,
            )
        self.assertIn("include=latency", request.call_args.args[0])

        wrong_version = _latency_response()
        wrong_version["schema_version"] = 2
        with mock.patch.object(self.client, "_request", return_value=wrong_version):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_latency=True)

        unsafe = _latency_response()
        unsafe["rows"][0]["latency"]["gateway"]["buckets"][0]["prompt"] = "unsafe"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_latency=True)

        non_finite = _latency_response()
        non_finite["rows"][0]["latency"]["gateway"]["average_ms"] = float("nan")  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=non_finite):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_latency=True)

        oversized_integer = _latency_response()
        oversized_integer["rows"][0]["latency"]["gateway"]["max_ms"] = 2**63  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=oversized_integer):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_latency=True)


if __name__ == "__main__":
    unittest.main()
