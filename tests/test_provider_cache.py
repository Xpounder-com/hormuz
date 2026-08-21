from __future__ import annotations

import unittest

from hormuz.provider_cache import inspect_explicit_cache_controls


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


if __name__ == "__main__":
    unittest.main()
