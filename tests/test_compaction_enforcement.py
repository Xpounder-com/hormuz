from __future__ import annotations

import copy
import json
import unittest

from hormuz.compaction import compact_text
from hormuz.compaction_enforcement import (
    CONTEXT_FORMAT_VERSION,
    CompactionEnforcementError,
    inspect_request,
)
from hormuz.config import SecretControls
from hormuz.redaction import REPLACEMENT, SecretRedactor


def redactor(secret: str, mode: str = "redact") -> SecretRedactor:
    return SecretRedactor(
        SecretControls(
            mode=mode,
            builtins=True,
            custom_secret_values=(("custom:test", secret),),
        )
    )


def openai_payload(output: str) -> dict:
    return {
        "model": "approved",
        "input": [
            {"type": "function_call", "call_id": "x", "name": "list_paths", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "x", "output": output},
        ],
    }


class CompactionEnforcementTests(unittest.TestCase):
    def test_split_secret_is_detected_in_reconstructed_path_list(self) -> None:
        secret = "private/file_"
        original = "".join(f"src/private/file_{i}.txt\n" for i in range(80))
        compact = compact_text(original, "path_list")
        self.assertNotEqual(compact, original)
        self.assertNotIn(secret, compact)

        result = inspect_request(
            openai_payload(compact),
            protocol="openai",
            redactor=redactor(secret, mode="deny"),
            mode="deny",
            declared_version=CONTEXT_FORMAT_VERSION,
        )
        self.assertEqual(result.redaction.count, 80)
        self.assertEqual(result.redaction.rules, ("custom:test",))
        self.assertTrue(result.expanded_for_egress)

    def test_multiline_private_key_is_detected_after_line_run_expansion(self) -> None:
        key = "-----BEGIN PRIVATE KEY-----\nsecret-body\n-----END PRIVATE KEY-----"
        original = key + "\n"
        compact = json.dumps(
            {"format": "hormuz-line-runs-v1", "runs": [
                ["-----BEGIN PRIVATE KEY-----", 1], ["secret-body", 1],
                ["-----END PRIVATE KEY-----", 1], ["", 1],
            ]},
            separators=(",", ":"),
        )
        result = inspect_request(
            openai_payload(compact),
            protocol="openai",
            redactor=redactor("unused"),
            mode="redact",
            declared_version=CONTEXT_FORMAT_VERSION,
        )
        self.assertEqual(result.redaction.count, 1)
        self.assertNotIn("secret-body", str(result.redaction.value))
        self.assertIn(REPLACEMENT, str(result.redaction.value))

    def test_representation_only_match_expands_instead_of_damaging_envelope(self) -> None:
        original = "same\n" * 100
        compact = compact_text(original, "line_runs")
        result = inspect_request(
            openai_payload(compact),
            protocol="openai",
            redactor=redactor("hormuz-line-runs-v1"),
            mode="redact",
            declared_version=CONTEXT_FORMAT_VERSION,
        )
        self.assertEqual(result.redaction.count, 1)
        self.assertEqual(result.redaction.value["input"][1]["output"], original)
        self.assertTrue(result.expanded_for_egress)

    def test_valid_compaction_without_secret_stays_compact(self) -> None:
        original = "same\n" * 100
        compact = compact_text(original, "line_runs")
        payload = openai_payload(compact)
        expected = copy.deepcopy(payload)
        result = inspect_request(
            payload,
            protocol="openai",
            redactor=redactor("not-present"),
            mode="redact",
            declared_version=CONTEXT_FORMAT_VERSION,
        )
        self.assertEqual(result.redaction.value, expected)
        self.assertEqual(result.redaction.count, 0)
        self.assertFalse(result.expanded_for_egress)

    def test_valid_compaction_does_not_reinterpret_unselected_marker_like_content(self) -> None:
        compact = compact_text("same\n" * 100, "line_runs")
        malformed = '{"format":"hormuz-line-runs-v1","runs":[["ordinary",true]]}'
        payload = openai_payload(compact)
        payload["input"].extend([
            {"type": "function_call", "call_id": "y", "name": "other_tool", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "y", "output": malformed},
        ])
        expected = copy.deepcopy(payload)

        result = inspect_request(
            payload,
            protocol="openai",
            redactor=redactor("not-present"),
            mode="redact",
            declared_version=CONTEXT_FORMAT_VERSION,
        )

        self.assertEqual(result.recognized_blocks, 1)
        self.assertEqual(result.redaction.value, expected)
        self.assertFalse(result.expanded_for_egress)

    def test_header_cannot_authorize_or_hide_missing_and_malformed_envelopes(self) -> None:
        with self.assertRaisesRegex(CompactionEnforcementError, "unsupported_compaction_version"):
            inspect_request(
                openai_payload("ordinary"), protocol="openai", redactor=redactor("x"),
                mode="redact", declared_version="future-v2"
            )
        with self.assertRaisesRegex(CompactionEnforcementError, "declared_compaction_missing"):
            inspect_request(
                openai_payload("ordinary"), protocol="openai", redactor=redactor("x"),
                mode="redact", declared_version=CONTEXT_FORMAT_VERSION
            )
        malformed = '{"format":"hormuz-line-runs-v1","runs":[["x",true]]}'
        ordinary = inspect_request(
            openai_payload(malformed), protocol="openai", redactor=redactor("not-present"),
            mode="redact", declared_version=None
        )
        self.assertEqual(ordinary.recognized_blocks, 0)
        self.assertEqual(ordinary.redaction.value, openai_payload(malformed))
        with self.assertRaisesRegex(CompactionEnforcementError, "declared_compaction_missing"):
            inspect_request(
                openai_payload(malformed), protocol="openai", redactor=redactor("not-present"),
                mode="redact", declared_version=CONTEXT_FORMAT_VERSION
            )

    def test_anthropic_only_decodes_string_tool_results(self) -> None:
        original = "same\n" * 100
        compact = compact_text(original, "line_runs")
        payload = {
            "model": "approved",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": compact},
                {"type": "tool_result", "tool_use_id": "x", "content": compact},
                {"type": "tool_result", "tool_use_id": "y", "content": [{"type": "text", "text": compact}]},
            ]}],
        }
        result = inspect_request(
            payload, protocol="anthropic", redactor=redactor("unused"), mode="redact",
            declared_version=CONTEXT_FORMAT_VERSION
        )
        self.assertEqual(result.recognized_blocks, 1)
        self.assertEqual(result.redaction.value, payload)


if __name__ == "__main__":
    unittest.main()
