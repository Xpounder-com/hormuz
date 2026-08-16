from __future__ import annotations

import json
import unittest

from hormuz.usage import ResponseUsageParser, sanitize_provider_usage


class ResponseUsageParserTests(unittest.TestCase):
    def test_openai_usage_is_normalized_and_native_metadata_is_allowlisted(self) -> None:
        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(
            json.dumps(
                {
                    "id": "response-content-must-not-be-retained",
                    "model": "gpt-test",
                    "output": [{"type": "message", "content": "company-content"}],
                    "usage": {
                        "input_tokens": 120,
                        "output_tokens": 30,
                        "total_tokens": 150,
                        "input_tokens_details": {
                            "cached_tokens": 20,
                            "audio_tokens": 3,
                            "unsupported_detail": "must-not-be-retained",
                        },
                        "output_tokens_details": {
                            "reasoning_tokens": 7,
                            "accepted_prediction_tokens": 2,
                        },
                        "request_body": "must-not-be-retained",
                    },
                }
            ).encode("utf-8")
        )

        usage = parser.finish()

        self.assertEqual(usage.input_tokens, 120)
        self.assertEqual(usage.output_tokens, 30)
        self.assertEqual(usage.cache_read_tokens, 20)
        self.assertEqual(usage.reasoning_tokens, 7)
        self.assertEqual(usage.billable_tokens, 150)
        self.assertEqual(usage.actual_model, "gpt-test")
        self.assertEqual(
            usage.provider_usage,
            {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
                "input_tokens_details": {"cached_tokens": 20, "audio_tokens": 3},
                "output_tokens_details": {
                    "reasoning_tokens": 7,
                    "accepted_prediction_tokens": 2,
                },
            },
        )
        self.assertNotIn("company-content", repr(usage))
        self.assertNotIn("must-not-be-retained", repr(usage.provider_usage))

    def test_anthropic_stream_merges_usage_and_counts_separate_cache_tokens(self) -> None:
        parser = ResponseUsageParser("anthropic", is_event_stream=True)
        events = [
            {
                "type": "message_start",
                "message": {
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "must-not-be-retained"}],
                    "usage": {
                        "input_tokens": 80,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 10,
                        "cache_creation": {
                            "ephemeral_5m_input_tokens": 4,
                            "ephemeral_1h_input_tokens": 6,
                        },
                        "service_tier": "standard",
                    },
                },
            },
            {
                "type": "message_delta",
                "usage": {
                    "output_tokens": 12,
                    "server_tool_use": {"web_search_requests": 1},
                },
            },
        ]
        parser.feed(
            "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode("utf-8")
        )

        usage = parser.finish()

        self.assertEqual(usage.input_tokens, 80)
        self.assertEqual(usage.output_tokens, 12)
        self.assertEqual(usage.cache_read_tokens, 20)
        self.assertEqual(usage.cache_write_tokens, 10)
        self.assertEqual(usage.billable_tokens, 122)
        self.assertEqual(usage.actual_model, "claude-test")
        self.assertEqual(usage.provider_usage["service_tier"], "standard")
        self.assertEqual(usage.provider_usage["server_tool_use"], {"web_search_requests": 1})
        self.assertNotIn("must-not-be-retained", repr(usage.provider_usage))

    def test_native_usage_rejects_unknown_protocols_and_unsafe_values(self) -> None:
        self.assertEqual(sanitize_provider_usage("unknown", {"input_tokens": 1}), {})
        self.assertEqual(
            sanitize_provider_usage(
                "anthropic",
                {
                    "input_tokens": True,
                    "output_tokens": -1,
                    "cache_read_input_tokens": 2**63,
                    "service_tier": "bad\nvalue",
                    "inference_geo": "x" * 129,
                    "content": "must-not-be-retained",
                },
            ),
            {},
        )

        parser = ResponseUsageParser("openai", is_event_stream=False)
        parser.feed(
            json.dumps(
                {
                    "model": "bad\nmodel",
                    "usage": {"input_tokens": 2**63, "output_tokens": 1},
                }
            ).encode("utf-8")
        )
        usage = parser.finish()
        self.assertIsNone(usage.actual_model)
        self.assertEqual(usage.input_tokens, 0)
        self.assertEqual(usage.output_tokens, 1)

        for model in ("model with content", "unsafe-model-🚀", "m" * 257):
            with self.subTest(model=model[:32]):
                parser = ResponseUsageParser("openai", is_event_stream=False)
                parser.feed(json.dumps({"model": model}).encode("utf-8"))
                self.assertIsNone(parser.finish().actual_model)


if __name__ == "__main__":
    unittest.main()
