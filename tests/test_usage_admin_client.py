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
        "cost_usd": 0.001,
        "estimated_cost_usd": 0.001,
        "budget_usd": 10.0,
        "budget_remaining_usd": 9.999,
        "budget_used_percent": 0.01,
    }


def _response() -> dict[str, object]:
    return {
        "schema_version": 1,
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


if __name__ == "__main__":
    unittest.main()
