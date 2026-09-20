"""Prove the optional external optimizer workload still describes Hormuz code."""

from __future__ import annotations

import importlib.util
import math
import unittest
from pathlib import Path
from unittest.mock import patch

from hormuz import compaction


ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = ROOT / "benchmarks/code_optimizer/compaction.py"


class CodeOptimizerWorkloadTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("hormuz_optimizer_workload", WORKLOAD)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        workload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workload)
        self.workload = workload

    def test_reference_workload_covers_primary_and_heldout_cases(self) -> None:
        workload = self.workload
        measured = workload.cases()
        heldout = workload.heldout_cases()
        self.assertIn("paths_typical", measured)
        self.assertIn("paths_large", measured)
        self.assertIn("paths_empty", measured)
        self.assertIn("paths_malformed_marker", measured)
        self.assertIn("paths_duplicate_unicode", measured)
        self.assertIn("json_typical", measured)
        self.assertIn("crlf_paths", heldout)
        self.assertIn("lines_mixed", heldout)
        self.assertIn("search_framed", heldout)
        self.assertIn("json_table", heldout)
        self.assertFalse(set(measured) & set(heldout))

        baseline = workload.evaluate(ROOT, "validate")
        holdout = workload.evaluate(ROOT, "heldout")
        self.assertEqual(len(baseline["outputs"]), len(measured))
        self.assertEqual(len(holdout["outputs"]), len(heldout))
        self.assertNotEqual(baseline["fixture_sha256"], holdout["fixture_sha256"])
        self.assertEqual(
            Path(baseline["source"]).resolve(), (ROOT / "hormuz/compaction.py").resolve()
        )
        self.assertTrue(all(len(record["sha256"]) == 64 for record in baseline["outputs"].values()))

    def test_mixed_runs_and_framed_search_are_effective_heldout_cases(self) -> None:
        for name in ("lines_mixed", "search_framed"):
            with self.subTest(name=name):
                value, format_name = self.workload.heldout_cases()[name]
                compacted = compaction.compact_text(value, format_name)
                self.assertLess(len(compacted.encode("utf-8")), len(value.encode("utf-8")))
                self.assertEqual(compaction.decode_text(compacted).text, value)

        baseline = self.workload.evaluate(ROOT, "heldout")
        original_lines = compaction._compact_line_runs

        def homogeneous_only(text: str) -> str:
            if len(set(text.splitlines())) > 1:
                return text
            return original_lines(text)

        with patch.object(compaction, "_compact_line_runs", side_effect=homogeneous_only):
            disabled_mixed = self.workload.evaluate(ROOT, "heldout")
        self.assertNotEqual(baseline["outputs"]["lines_mixed"], disabled_mixed["outputs"]["lines_mixed"])

        original_framed = compaction._compact_framed_lines

        def no_framed_search(text: str, *, format: str) -> str:
            return text if format == "search_lines" else original_framed(text, format=format)

        with patch.object(compaction, "_compact_framed_lines", side_effect=no_framed_search):
            disabled_framed = self.workload.evaluate(ROOT, "heldout")
        self.assertNotEqual(baseline["outputs"]["search_framed"], disabled_framed["outputs"]["search_framed"])

    def test_heldout_json_table_detects_disabled_compaction(self) -> None:
        value, format_name = self.workload.heldout_cases()["json_table"]
        compacted = compaction.compact_text(value, format_name)
        self.assertLess(len(compacted.encode("utf-8")), len(value.encode("utf-8")))
        self.assertTrue(compaction.decode_text(compacted).recognized)
        self.assertEqual(compaction.decode_text(compacted).text, value)

        baseline = self.workload.evaluate(ROOT, "heldout")
        with patch.object(compaction, "_compact_json_table", side_effect=lambda text: text):
            disabled = self.workload.evaluate(ROOT, "heldout")
        self.assertEqual(baseline["fixture_sha256"], disabled["fixture_sha256"])
        self.assertNotEqual(baseline["outputs"]["json_table"], disabled["outputs"]["json_table"])

    def test_benchmark_uses_distinct_inputs_and_fingerprints_their_outputs(self) -> None:
        measured = self.workload.cases()
        value, format_name = measured["json_typical"]
        self.assertLess(
            len(compaction.compact_text(value, format_name).encode("utf-8")),
            len(value.encode("utf-8")),
        )
        seen: set[str] = set()
        repeated: set[str] = set()
        original = compaction.compact_text

        def record_input(text: str, format_name: str) -> str:
            if text in seen:
                repeated.add(text)
            seen.add(text)
            return original(text, format_name)

        with patch.object(compaction, "compact_text", side_effect=record_input):
            benchmark = self.workload.evaluate(ROOT, "benchmark")
        self.assertFalse(repeated)
        samples = benchmark["samples_ns"]
        self.assertEqual(set(samples), set(measured) - {"paths_empty", "paths_malformed_marker"})
        self.assertIn("json_typical", samples)
        self.assertTrue(all(
            len(values) == 17 and all(math.isfinite(sample) and sample > 0 for sample in values)
            for values in samples.values()
        ))
        self.assertTrue(all(
            len(benchmark["outputs"][name]["timed_sha256"]) == 64 for name in samples
        ))

        def corrupt_one_timed_input(text: str, format_name: str) -> str:
            result = original(text, format_name)
            return result + "synthetic mutation" if "src/generated/000020/" in text else result

        with patch.object(compaction, "compact_text", side_effect=corrupt_one_timed_input):
            mutated = self.workload.evaluate(ROOT, "benchmark")
        self.assertEqual(
            benchmark["outputs"]["paths_typical"]["sha256"],
            mutated["outputs"]["paths_typical"]["sha256"],
        )
        self.assertNotEqual(
            benchmark["outputs"]["paths_typical"]["timed_sha256"],
            mutated["outputs"]["paths_typical"]["timed_sha256"],
        )


if __name__ == "__main__":
    unittest.main()
