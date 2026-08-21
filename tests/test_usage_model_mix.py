from __future__ import annotations

import unittest

from hormuz.usage_reporting import build_model_mix


class ModelMixTests(unittest.TestCase):
    def test_model_mix_has_request_token_and_estimated_spend_shares(self) -> None:
        model_mix = build_model_mix(
            [
                {
                    "scope_id": "gpt-5.4",
                    "protocol": "openai",
                    "requests": 2,
                    "succeeded": 1,
                    "failed": 0,
                    "denied": 1,
                    "total_tokens": 12,
                    "estimated_cost_microusd": 3_000,
                    "unpriced_requests": 0,
                },
                {
                    "scope_id": "claude-sonnet",
                    "protocol": "anthropic",
                    "requests": 1,
                    "succeeded": 0,
                    "failed": 1,
                    "denied": 0,
                    "total_tokens": 23,
                    "estimated_cost_microusd": 0,
                    "unpriced_requests": 1,
                },
            ]
        )

        self.assertEqual(
            model_mix["model_identity_basis"],
            "actual_model_or_routed_fallback",
        )
        self.assertEqual(
            model_mix["request_basis"],
            "all_accounted_gateway_attempts",
        )
        self.assertEqual(
            model_mix["totals"],
            {
                "requests": 3,
                "succeeded": 1,
                "failed": 1,
                "denied": 1,
                "total_tokens": 35,
                "estimated_spend_microusd": 3_000,
                "unpriced_requests": 1,
                "estimated_spend_usd": 0.003,
                "partial_estimated_spend": True,
            },
        )
        self.assertEqual(
            model_mix["models"],
            [
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
        )

    def test_model_mix_leaves_zero_denominator_shares_null(self) -> None:
        model_mix = build_model_mix(
            [
                {
                    "scope_id": "gpt-5.4",
                    "protocol": "openai",
                    "requests": 1,
                    "succeeded": 0,
                    "failed": 0,
                    "denied": 1,
                    "total_tokens": 0,
                    "estimated_cost_microusd": 0,
                    "unpriced_requests": 0,
                }
            ]
        )

        model = model_mix["models"][0]
        self.assertEqual(model["request_share_percent"], 100.0)
        self.assertIsNone(model["token_share_percent"])
        self.assertIsNone(model["estimated_spend_share_percent"])

    def test_model_mix_rejects_inconsistent_aggregate_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "status counts"):
            build_model_mix(
                [
                    {
                        "scope_id": "gpt-5.4",
                        "protocol": "openai",
                        "requests": 1,
                        "succeeded": 1,
                        "failed": 1,
                        "denied": 0,
                        "total_tokens": 12,
                        "estimated_cost_microusd": 1,
                        "unpriced_requests": 0,
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
