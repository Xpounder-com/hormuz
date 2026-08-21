from __future__ import annotations

import unittest
from datetime import date, timedelta

from hormuz.config import Policy, ProviderCacheCapability, ProviderCachePolicy
from hormuz.provider_cache import (
    evaluate_provider_cache_request,
    inspect_explicit_cache_controls,
)


class ProviderCacheInspectionTests(unittest.TestCase):
    def test_openai_controls_are_detected_without_returning_values(self) -> None:
        secret_cache_key = "customer-cache-key-must-not-persist"

        inspection = inspect_explicit_cache_controls(
            "openai",
            {
                "model": "gpt-test",
                "prompt_cache_key": secret_cache_key,
                "prompt_cache_options": {"mode": "explicit"},
            },
        )

        self.assertTrue(inspection.requested)
        self.assertTrue(inspection.complete)
        self.assertEqual(
            inspection.controls,
            ("openai.prompt_cache_key", "openai.prompt_cache_options"),
        )
        self.assertNotIn(secret_cache_key, repr(inspection))

    def test_anthropic_nested_control_is_detected(self) -> None:
        inspection = inspect_explicit_cache_controls(
            "anthropic",
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "ordinary request",
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                ]
            },
        )

        self.assertEqual(inspection.controls, ("anthropic.cache_control",))

    def test_unknown_provider_fields_and_protocols_are_not_claimed_as_governed(self) -> None:
        payload = {
            "model": "future-model",
            "future_cache_directive": {"customer": "must-not-persist"},
        }

        self.assertEqual(
            inspect_explicit_cache_controls("openai", payload).controls,
            (),
        )
        self.assertEqual(
            inspect_explicit_cache_controls("future-provider", payload).controls,
            (),
        )

    def test_strict_openai_opt_out_requires_explicit_mode_without_breakpoints(self) -> None:
        inspection = inspect_explicit_cache_controls(
            "openai",
            {
                "model": "gpt-test",
                "prompt_cache_options": {"mode": "explicit"},
            },
        )
        capability = ProviderCacheCapability(
            protocol="openai",
            upstream_model="gpt-test",
            operations=("/v1/responses",),
            capability_version="openai-gpt-test-v1",
            reviewed_at=date.today(),
            source_urls=("https://example.invalid/openai-cache",),
            strict_no_cache="openai_explicit_without_breakpoints",
        )
        decision = evaluate_provider_cache_request(
            policy=ProviderCachePolicy(
                mode="disabled",
                capability_max_age_days=30,
            ),
            capability=capability,
            protocol="openai",
            upstream_model="gpt-test",
            operation="/v1/responses",
            client="codex",
            model_alias="gpt-test",
            inspection=inspection,
        )

        self.assertTrue(inspection.openai_explicit_without_breakpoints)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "strict_no_cache_verified")

    def test_strict_no_cache_fails_closed_for_breakpoints_unknown_and_stale_capabilities(self) -> None:
        policy = ProviderCachePolicy(mode="disabled", capability_max_age_days=30)
        with_breakpoint = inspect_explicit_cache_controls(
            "openai",
            {
                "prompt_cache_options": {"mode": "explicit"},
                "input": [
                    {
                        "prompt_cache_breakpoint": {"mode": "explicit"},
                        "text": "must-not-be-retained",
                    }
                ],
            },
        )
        current = ProviderCacheCapability(
            protocol="openai",
            upstream_model="gpt-test",
            operations=("/v1/responses",),
            capability_version="openai-gpt-test-v1",
            reviewed_at=date.today(),
            source_urls=("https://example.invalid/openai-cache",),
            strict_no_cache="openai_explicit_without_breakpoints",
        )
        stale = ProviderCacheCapability(
            protocol="openai",
            upstream_model="gpt-test",
            operations=("/v1/responses",),
            capability_version="openai-gpt-test-v0",
            reviewed_at=date.today() - timedelta(days=31),
            source_urls=("https://example.invalid/openai-cache",),
            strict_no_cache="openai_explicit_without_breakpoints",
        )

        breakpoint = evaluate_provider_cache_request(
            policy=policy,
            capability=current,
            protocol="openai",
            upstream_model="gpt-test",
            operation="/v1/responses",
            client="codex",
            model_alias="gpt-test",
            inspection=with_breakpoint,
        )
        unknown = evaluate_provider_cache_request(
            policy=policy,
            capability=None,
            protocol="openai",
            upstream_model="gpt-test",
            operation="/v1/responses",
            client="codex",
            model_alias="gpt-test",
            inspection=with_breakpoint,
        )
        stale_result = evaluate_provider_cache_request(
            policy=policy,
            capability=stale,
            protocol="openai",
            upstream_model="gpt-test",
            operation="/v1/responses",
            client="codex",
            model_alias="gpt-test",
            inspection=with_breakpoint,
        )

        self.assertFalse(breakpoint.allowed)
        self.assertEqual(breakpoint.reason, "strict_no_cache_not_verified")
        self.assertFalse(unknown.allowed)
        self.assertEqual(unknown.reason, "capability_unknown")
        self.assertFalse(stale_result.allowed)
        self.assertEqual(stale_result.reason, "capability_stale")

        unsupported_operation = evaluate_provider_cache_request(
            policy=policy,
            capability=current,
            protocol="openai",
            upstream_model="gpt-test",
            operation="/v1/responses/compact",
            client="codex",
            model_alias="gpt-test",
            inspection=inspect_explicit_cache_controls(
                "openai",
                {"prompt_cache_options": {"mode": "explicit"}},
            ),
        )
        self.assertFalse(unsupported_operation.allowed)
        self.assertEqual(
            unsupported_operation.reason,
            "capability_operation_unsupported",
        )

    def test_strict_no_cache_requires_the_request_wide_openai_option(self) -> None:
        inspection = inspect_explicit_cache_controls(
            "openai",
            {
                "model": "gpt-test",
                "input": [
                    {
                        "type": "message",
                        "prompt_cache_options": {"mode": "explicit"},
                    }
                ],
            },
        )
        capability = ProviderCacheCapability(
            protocol="openai",
            upstream_model="gpt-test",
            operations=("/v1/responses",),
            capability_version="openai-gpt-test-v1",
            reviewed_at=date.today(),
            source_urls=("https://example.invalid/openai-cache",),
            strict_no_cache="openai_explicit_without_breakpoints",
        )
        decision = evaluate_provider_cache_request(
            policy=ProviderCachePolicy(mode="disabled", capability_max_age_days=30),
            capability=capability,
            protocol="openai",
            upstream_model="gpt-test",
            operation="/v1/responses",
            client="codex",
            model_alias="gpt-test",
            inspection=inspection,
        )

        self.assertFalse(inspection.openai_explicit_without_breakpoints)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "strict_no_cache_not_verified")

    def test_strict_no_cache_overlay_cannot_be_relaxed(self) -> None:
        effective = Policy(
            provider_cache=ProviderCachePolicy(
                mode="disabled",
                capability_max_age_days=30,
            )
        ).overlaid(Policy(provider_cache=ProviderCachePolicy(mode="allow")))

        self.assertTrue(effective.provider_cache.strict_no_cache_required)
        self.assertEqual(effective.provider_cache.capability_max_age_days, 30)

        raised = Policy(
            provider_cache=ProviderCachePolicy(mode="deny")
        ).overlaid(
            Policy(
                provider_cache=ProviderCachePolicy(
                    mode="disabled",
                    capability_max_age_days=30,
                )
            )
        )
        self.assertTrue(raised.provider_cache.strict_no_cache_required)


if __name__ == "__main__":
    unittest.main()
