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


def _outcome_response() -> dict[str, object]:
    response = _response()
    response["schema_version"] = 6
    response["coverage"] = {
        **response["coverage"],  # type: ignore[arg-type]
        "outcome_scope": "recorded_gateway_policy_actions_only",
        "historical_policy_denial_reasons_separately_classified": False,
    }
    response["rows"][0]["outcomes"] = {  # type: ignore[index]
        "model_fallback_requests": 1,
        "output_capped_requests": 1,
        "reservation_budget_denied_requests": 0,
    }
    return response


def _latency_and_outcome_response() -> dict[str, object]:
    response = _latency_response()
    response["schema_version"] = 7
    response["coverage"] = {
        **response["coverage"],  # type: ignore[arg-type]
        "outcome_scope": "recorded_gateway_policy_actions_only",
        "historical_policy_denial_reasons_separately_classified": False,
    }
    response["rows"][0]["outcomes"] = {  # type: ignore[index]
        "model_fallback_requests": 1,
        "output_capped_requests": 1,
        "reservation_budget_denied_requests": 0,
    }
    return response


def _coverage_response() -> dict[str, object]:
    return {
        "schema_version": 1,
        "organization_id": "xpounder",
        "access": {"scope": "organization"},
        "window": {
            "start": "2026-08-01T00:00:00+00:00",
            "end": "2026-08-16T12:00:00+00:00",
            "timezone": "UTC",
        },
        "coverage": {
            "scope": "accounted_authenticated_gateway_requests_only",
            "accounted_gateway_requests": 3,
            "identity_bound_gateway_requests": 3,
            "unattributed_accounted_gateway_requests": 0,
            "active_identities": 2,
            "active_teams": 1,
            "identity_type_requests": {
                "human": 2,
                "service_account": 0,
                "ci": 1,
                "connector": 0,
            },
            "observed_gateway_clients": [
                {"client": "claude-code", "requests": 1},
                {"client": "codex", "requests": 2},
            ],
            "legacy_unattributed_rows_excluded": True,
            "pre_authentication_attempts_included": False,
            "outside_gateway_traffic_observable": False,
            "provider_invoice_reconciled": False,
            "provider_invoice_reconciliation": "separate_billing_reconciliation_required",
            "organization_total": False,
        },
    }


def _pacing_response() -> dict[str, object]:
    return {
        "schema_version": 1,
        "organization_id": "xpounder",
        "access": {"scope": "organization"},
        "filters": {"actor_id": None, "team_id": None},
        "window": {
            "start": "2026-08-01T00:00:00+00:00",
            "as_of": "2026-08-16T12:00:00+00:00",
            "end": "2026-09-01T00:00:00+00:00",
            "timezone": "UTC",
        },
        "coverage": {
            "scope": "gateway_captured_requests_only",
            "legacy_unattributed_rows_excluded": True,
            "outside_gateway_traffic_observable": False,
            "provider_invoice_reconciled": False,
            "partial_projection": False,
        },
        "budget_pacing": {
            "methodology": "calendar_pace_estimate",
            "advisory_only": True,
            "policy_enforcement_basis": "actual_usage_plus_active_reservations_only",
            "elapsed_fraction": 0.5,
            "early_period": False,
            "projection_available": True,
            "month_to_date_estimated_spend_microusd": 15_500_000,
            "month_to_date_estimated_spend_usd": 15.5,
            "average_estimated_spend_per_calendar_day_microusd": 1_000_000,
            "average_estimated_spend_per_calendar_day_usd": 1.0,
            "projected_month_end_estimated_spend_microusd": 31_000_000,
            "projected_month_end_estimated_spend_usd": 31.0,
            "configured_monthly_budget_usd": 30.0,
            "projected_budget_utilization_percent": 103.333333,
            "projected_budget_overage_usd": 1.0,
            "unpriced_requests": 0,
            "partial_projection": False,
        },
    }


def _model_mix_response() -> dict[str, object]:
    return {
        "schema_version": 1,
        "organization_id": "xpounder",
        "access": {"scope": "organization"},
        "filters": {"actor_id": None, "team_id": None},
        "window": {
            "start": "2026-08-01T00:00:00+00:00",
            "as_of": "2026-08-16T12:00:00+00:00",
            "timezone": "UTC",
        },
        "coverage": {
            "scope": "gateway_captured_requests_only",
            "legacy_unattributed_rows_excluded": True,
            "outside_gateway_traffic_observable": False,
            "provider_invoice_reconciled": False,
            "partial_estimated_spend": True,
        },
        "model_mix": {
            "model_identity_basis": "actual_model_or_routed_fallback",
            "request_basis": "all_accounted_gateway_attempts",
            "totals": {
                "requests": 3,
                "succeeded": 1,
                "failed": 1,
                "denied": 1,
                "total_tokens": 35,
                "estimated_spend_microusd": 3_000,
                "estimated_spend_usd": 0.003,
                "unpriced_requests": 1,
                "partial_estimated_spend": True,
            },
            "models": [
                {
                    "model_id": "gpt-5.4",
                    "provider": "openai",
                    "requests": 2,
                    "succeeded": 1,
                    "failed": 0,
                    "denied": 1,
                    "total_tokens": 12,
                    "estimated_spend_microusd": 3_000,
                    "estimated_spend_usd": 0.003,
                    "unpriced_requests": 0,
                    "request_share_percent": 66.666667,
                    "token_share_percent": 34.285714,
                    "estimated_spend_share_percent": 100.0,
                },
                {
                    "model_id": "claude-sonnet",
                    "provider": "anthropic",
                    "requests": 1,
                    "succeeded": 0,
                    "failed": 1,
                    "denied": 0,
                    "total_tokens": 23,
                    "estimated_spend_microusd": 0,
                    "estimated_spend_usd": 0.0,
                    "unpriced_requests": 1,
                    "request_share_percent": 33.333333,
                    "token_share_percent": 65.714286,
                    "estimated_spend_share_percent": 0.0,
                },
            ],
        },
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
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_usage_report_request"):
                self.client.report(group_by="team", include_outcomes="true")  # type: ignore[arg-type]
        request.assert_not_called()

    def test_explicit_routing_and_outcome_dimensions_are_validated(self) -> None:
        requested = _response()
        requested["group_by"] = "requested_model"
        requested["rows"][0].update(
            {
                "scope_id": "gpt-policy",
                "scope_name": "gpt-policy",
                "protocol": "openai",
            }
        )
        with mock.patch.object(self.client, "_request", return_value=requested) as request:
            self.assertEqual(self.client.report(group_by="requested_model"), requested)
        self.assertIn("group_by=requested_model", request.call_args.args[0])

        actual = _response()
        actual["group_by"] = "actual_model"
        actual["rows"][0].update(
            {
                "scope_id": "not_reported",
                "scope_name": "not_reported",
                "protocol": "openai",
                "actual_model_reported": False,
            }
        )
        with mock.patch.object(self.client, "_request", return_value=actual):
            self.assertEqual(self.client.report(group_by="actual_model"), actual)

        invalid_unreported = _response()
        invalid_unreported["group_by"] = "actual_model"
        invalid_unreported["rows"][0].update(
            {
                "scope_id": "gpt-provider-v1",
                "scope_name": "gpt-provider-v1",
                "protocol": "openai",
                "actual_model_reported": False,
            }
        )
        with mock.patch.object(self.client, "_request", return_value=invalid_unreported):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="actual_model")

        for group_by in ("policy", "status"):
            response = _response()
            response["group_by"] = group_by
            response["rows"][0].update(
                {
                    "scope_id": "allowed" if group_by == "policy" else "succeeded",
                    "scope_name": "allowed" if group_by == "policy" else "succeeded",
                }
            )
            with self.subTest(group_by=group_by), mock.patch.object(
                self.client,
                "_request",
                return_value=response,
            ):
                self.assertEqual(self.client.report(group_by=group_by), response)

        rate_limited = _response()
        rate_limited["group_by"] = "status"
        rate_limited["rows"][0].update(
            {
                "scope_id": "rate_limited",
                "scope_name": "rate_limited",
                "succeeded": 0,
                "failed": 1,
            }
        )
        with mock.patch.object(self.client, "_request", return_value=rate_limited):
            self.assertEqual(self.client.report(group_by="status"), rate_limited)

    def test_constrained_scope_response_is_explicit_and_schema_checked(self) -> None:
        self_view = _response()
        self_view["schema_version"] = 4
        self_view["filters"] = {"actor_id": "alice", "team_id": None}
        self_view["access"] = {"scope": "self"}
        with mock.patch.object(self.client, "_request", return_value=self_view):
            self.assertEqual(self.client.report(group_by="team"), self_view)

        team_view = _response()
        team_view["schema_version"] = 4
        team_view["filters"] = {"actor_id": None, "team_id": "engineering"}
        team_view["access"] = {"scope": "team"}
        with mock.patch.object(self.client, "_request", return_value=team_view):
            self.assertEqual(self.client.report(group_by="team"), team_view)

        invalid = _response()
        invalid["schema_version"] = 4
        invalid["filters"] = {"actor_id": None, "team_id": "engineering"}
        with mock.patch.object(self.client, "_request", return_value=invalid):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team")

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

        scoped = _latency_response()
        scoped["schema_version"] = 5
        scoped["filters"] = {"actor_id": None, "team_id": "engineering"}
        scoped["access"] = {"scope": "team"}
        with mock.patch.object(self.client, "_request", return_value=scoped):
            self.assertEqual(
                self.client.report(group_by="team", include_latency=True),
                scoped,
            )

    def test_outcome_views_are_explicit_versioned_and_strict(self) -> None:
        response = _outcome_response()
        with mock.patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(
                self.client.report(group_by="team", include_outcomes=True),
                response,
            )
        self.assertIn("include=outcomes", request.call_args.args[0])

        unrequested = _response()
        unrequested["rows"][0]["outcomes"] = response["rows"][0]["outcomes"]  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unrequested):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team")

        wrong_version = _outcome_response()
        wrong_version["schema_version"] = 2
        with mock.patch.object(self.client, "_request", return_value=wrong_version):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_outcomes=True)

        unsafe = _outcome_response()
        unsafe["rows"][0]["outcomes"]["prompt"] = "must-not-be-accepted"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_outcomes=True)

        impossible = _outcome_response()
        impossible["rows"][0]["outcomes"]["model_fallback_requests"] = 2  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=impossible):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.report(group_by="team", include_outcomes=True)

        scoped = _outcome_response()
        scoped["schema_version"] = 8
        scoped["filters"] = {"actor_id": "alice", "team_id": None}
        scoped["access"] = {"scope": "self"}
        with mock.patch.object(self.client, "_request", return_value=scoped):
            self.assertEqual(
                self.client.report(group_by="team", include_outcomes=True),
                scoped,
            )

        combined = _latency_and_outcome_response()
        with mock.patch.object(self.client, "_request", return_value=combined) as request:
            self.assertEqual(
                self.client.report(
                    group_by="team",
                    include_latency=True,
                    include_outcomes=True,
                ),
                combined,
            )
        self.assertIn("include=latency%2Coutcomes", request.call_args.args[0])

        constrained_combined = _latency_and_outcome_response()
        constrained_combined["schema_version"] = 9
        constrained_combined["filters"] = {"actor_id": None, "team_id": "engineering"}
        constrained_combined["access"] = {"scope": "team"}
        with mock.patch.object(self.client, "_request", return_value=constrained_combined):
            self.assertEqual(
                self.client.report(
                    group_by="team",
                    include_latency=True,
                    include_outcomes=True,
                ),
                constrained_combined,
            )

    def test_coverage_requires_the_bounded_non_total_contract(self) -> None:
        response = _coverage_response()
        with mock.patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(self.client.coverage(), response)
        self.assertEqual(request.call_args.args[0], "/v1/admin/usage/coverage")

        unsafe = _coverage_response()
        unsafe["coverage"]["identity_type_requests"]["prompt"] = "must-not-be-accepted"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.coverage()

        misleading = _coverage_response()
        misleading["coverage"]["organization_total"] = True  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=misleading):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.coverage()

    def test_pacing_requires_the_advisory_calendar_month_contract(self) -> None:
        response = _pacing_response()
        with mock.patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(self.client.pacing(), response)
        self.assertEqual(request.call_args.args[0], "/v1/admin/usage/pacing")

        unsafe = _pacing_response()
        unsafe["budget_pacing"]["prompt"] = "must-not-be-accepted"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.pacing()

        enforcing = _pacing_response()
        enforcing["budget_pacing"]["advisory_only"] = False  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=enforcing):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.pacing()

        partial_mismatch = _pacing_response()
        partial_mismatch["budget_pacing"]["unpriced_requests"] = 1  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=partial_mismatch):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.pacing()

    def test_model_mix_requires_exact_content_free_shares(self) -> None:
        response = _model_mix_response()
        with mock.patch.object(self.client, "_request", return_value=response) as request:
            self.assertEqual(self.client.model_mix(), response)
        self.assertEqual(request.call_args.args[0], "/v1/admin/usage/model-mix")

        unsafe = _model_mix_response()
        unsafe["model_mix"]["models"][0]["prompt"] = "must-not-be-accepted"  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=unsafe):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.model_mix()

        incorrect_share = _model_mix_response()
        incorrect_share["model_mix"]["models"][0]["request_share_percent"] = 50.0  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=incorrect_share):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.model_mix()

        partial_mismatch = _model_mix_response()
        partial_mismatch["coverage"]["partial_estimated_spend"] = False  # type: ignore[index]
        with mock.patch.object(self.client, "_request", return_value=partial_mismatch):
            with self.assertRaisesRegex(UsageAdminClientError, "invalid_gateway_response"):
                self.client.model_mix()


if __name__ == "__main__":
    unittest.main()
