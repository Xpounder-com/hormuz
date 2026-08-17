from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from hormuz.audit_chain import (
    AUDIT_CHAIN_GENESIS_SHA256,
    AUDIT_CHAIN_MAX_FILE_BYTES,
    AUDIT_CHAIN_MAX_LINE_BYTES,
    AUDIT_CHAIN_SCHEMA_VERSION,
    AuditChainError,
    verify_audit_chain,
    write_audit_chain,
)


EVENTS = [
    {
        "event_type": "usage",
        "id": "evt_001",
        "occurred_at": "2026-08-17T00:00:00+00:00",
        "organization_id": "org-one",
        "status": "succeeded",
    },
    {
        "event_type": "security.dlp",
        "id": "evt_002",
        "occurred_at": "2026-08-17T00:00:01+00:00",
        "organization_id": "org-one",
        "status": "denied",
    },
]


def _canonical_line(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )


class AuditChainTests(unittest.TestCase):
    def test_writer_is_deterministic_and_anchored_verifier_accepts_exact_bytes(self) -> None:
        first = io.BytesIO()
        first_summary = write_audit_chain(EVENTS, first)
        second = io.BytesIO()
        second_summary = write_audit_chain(EVENTS, second)

        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertEqual(first_summary, second_summary)
        self.assertEqual(first_summary.count, 2)
        self.assertEqual(
            first_summary.head_sha256,
            "c236d599c8b8c3463a626cf352caf8254e694e30e4ca24882acac1e2b2c1c730",
        )
        self.assertEqual(
            first_summary.file_sha256,
            "2655f738b92b7065f7c62924bab6473123e1146639d9b59f22efef3883314844",
        )
        self.assertNotEqual(first_summary.head_sha256, AUDIT_CHAIN_GENESIS_SHA256)
        records = [json.loads(line) for line in first.getvalue().splitlines()]
        self.assertEqual(
            [record["schema_version"] for record in records],
            [AUDIT_CHAIN_SCHEMA_VERSION, AUDIT_CHAIN_SCHEMA_VERSION],
        )
        self.assertEqual([record["sequence"] for record in records], [1, 2])
        self.assertEqual(
            records[0]["previous_chain_sha256"],
            AUDIT_CHAIN_GENESIS_SHA256,
        )
        self.assertEqual(
            records[1]["previous_chain_sha256"],
            records[0]["chain_sha256"],
        )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "audit-chain.jsonl"
            path.write_bytes(first.getvalue())
            verified = verify_audit_chain(
                path,
                expected_head_sha256=first_summary.head_sha256,
                expected_count=first_summary.count,
                expected_file_sha256=first_summary.file_sha256,
            )
        self.assertEqual(verified, first_summary)

    def test_empty_chain_has_explicit_genesis_anchor(self) -> None:
        output = io.BytesIO()
        summary = write_audit_chain([], output)
        self.assertEqual(output.getvalue(), b"")
        self.assertEqual(summary.count, 0)
        self.assertEqual(summary.head_sha256, AUDIT_CHAIN_GENESIS_SHA256)

    def test_mutation_deletion_reordering_duplication_and_wrong_anchor_fail(self) -> None:
        output = io.BytesIO()
        summary = write_audit_chain(EVENTS, output)
        lines = output.getvalue().splitlines(keepends=True)
        first = json.loads(lines[0])
        first["event"]["status"] = "hormuz-sentinel"
        mutations = {
            "mutated event": [_canonical_line(first), lines[1]],
            "deleted first": [lines[1]],
            "deleted suffix": [lines[0]],
            "reordered": [lines[1], lines[0]],
            "duplicated": [lines[0], lines[0], lines[1]],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, selected in mutations.items():
                with self.subTest(name=name):
                    path = root / (name.replace(" ", "-") + ".jsonl")
                    path.write_bytes(b"".join(selected))
                    with self.assertRaises(AuditChainError) as raised:
                        verify_audit_chain(
                            path,
                            expected_head_sha256=summary.head_sha256,
                            expected_count=summary.count,
                        )
                    self.assertNotIn("hormuz-sentinel", str(raised.exception))

            alternate = io.BytesIO()
            alternate_summary = write_audit_chain([EVENTS[0]], alternate)
            alternate_path = root / "alternate.jsonl"
            alternate_path.write_bytes(alternate.getvalue())
            with self.assertRaises(AuditChainError):
                verify_audit_chain(
                    alternate_path,
                    expected_head_sha256=summary.head_sha256,
                    expected_count=alternate_summary.count,
                )
            with self.assertRaises(AuditChainError):
                verify_audit_chain(
                    alternate_path,
                    expected_head_sha256=alternate_summary.head_sha256,
                    expected_count=alternate_summary.count,
                    expected_file_sha256="f" * 64,
                )

    def test_verifier_rejects_noncanonical_ambiguous_and_unsafe_inputs(self) -> None:
        output = io.BytesIO()
        summary = write_audit_chain(EVENTS[:1], output)
        canonical = output.getvalue()
        value = json.loads(canonical)
        cases = {
            "missing newline": canonical.rstrip(b"\n"),
            "noncanonical": json.dumps(value, indent=2).encode("utf-8") + b"\n",
            "unknown field": _canonical_line({**value, "unexpected": True}),
            "invalid utf8": b"\xff\n",
            "duplicate member": (
                canonical[:-2]
                + b',"sequence":1}\n'
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in cases.items():
                with self.subTest(name=name):
                    path = root / (name.replace(" ", "-") + ".jsonl")
                    path.write_bytes(content)
                    with self.assertRaises(AuditChainError):
                        verify_audit_chain(
                            path,
                            expected_head_sha256=summary.head_sha256,
                            expected_count=summary.count,
                        )

            if hasattr(os, "O_NOFOLLOW"):
                target = root / "target.jsonl"
                target.write_bytes(canonical)
                link = root / "link.jsonl"
                link.symlink_to(target)
                with self.assertRaises(AuditChainError):
                    verify_audit_chain(
                        link,
                        expected_head_sha256=summary.head_sha256,
                        expected_count=summary.count,
                    )

    def test_anchor_inputs_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "empty.jsonl"
            path.write_bytes(b"")
            invalid = [
                {"expected_head_sha256": "A" * 64, "expected_count": 0},
                {"expected_head_sha256": "0" * 63, "expected_count": 0},
                {"expected_head_sha256": "0" * 64, "expected_count": -1},
                {"expected_head_sha256": "0" * 64, "expected_count": True},
                {
                    "expected_head_sha256": "0" * 64,
                    "expected_count": 0,
                    "expected_file_sha256": "invalid",
                },
            ]
            for arguments in invalid:
                with self.subTest(arguments=arguments), self.assertRaises(
                    AuditChainError
                ):
                    verify_audit_chain(path, **arguments)

    def test_writer_and_verifier_enforce_resource_and_json_bounds(self) -> None:
        with self.assertRaises(AuditChainError):
            write_audit_chain(
                [{"value": "x" * AUDIT_CHAIN_MAX_LINE_BYTES}],
                io.BytesIO(),
            )
        with self.assertRaises(AuditChainError):
            write_audit_chain([{"value": float("nan")}], io.BytesIO())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oversized = root / "oversized.jsonl"
            with oversized.open("wb") as stream:
                stream.truncate(AUDIT_CHAIN_MAX_FILE_BYTES + 1)
            with self.assertRaises(AuditChainError):
                verify_audit_chain(
                    oversized,
                    expected_head_sha256=AUDIT_CHAIN_GENESIS_SHA256,
                    expected_count=0,
                )
            with self.assertRaises(AuditChainError):
                verify_audit_chain(
                    root,
                    expected_head_sha256=AUDIT_CHAIN_GENESIS_SHA256,
                    expected_count=0,
                )


if __name__ == "__main__":
    unittest.main()
