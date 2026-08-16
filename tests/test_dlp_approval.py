from __future__ import annotations

import unittest

from hormuz.dlp_approval import DLPApprovalError, payload_fingerprint


class DLPApprovalFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_canonical_keyed_and_content_free(self) -> None:
        protected = "PROJECT-TRIDENT-MUST-NOT-APPEAR"
        first = payload_fingerprint(
            {"input": protected, "model": "gpt-test", "options": {"b": 2, "a": 1}},
            key=b"k" * 32,
        )
        reordered = payload_fingerprint(
            {"options": {"a": 1, "b": 2}, "model": "gpt-test", "input": protected},
            key=b"k" * 32,
        )
        mutated = payload_fingerprint(
            {"input": protected + " changed", "model": "gpt-test", "options": {"a": 1, "b": 2}},
            key=b"k" * 32,
        )
        other_key = payload_fingerprint(
            {"input": protected, "model": "gpt-test", "options": {"a": 1, "b": 2}},
            key=b"x" * 32,
        )

        self.assertEqual(first, reordered)
        self.assertNotEqual(first, mutated)
        self.assertNotEqual(first, other_key)
        self.assertTrue(first.startswith("hdf_v1_"))
        self.assertNotIn(protected, first)

    def test_invalid_key_and_non_finite_json_fail_closed(self) -> None:
        with self.assertRaisesRegex(DLPApprovalError, "approval_fingerprint_key_unavailable"):
            payload_fingerprint({"input": "x"}, key=b"short")
        with self.assertRaisesRegex(DLPApprovalError, "approval_payload_not_canonicalizable"):
            payload_fingerprint({"temperature": float("nan")}, key=b"k" * 32)


if __name__ == "__main__":
    unittest.main()
