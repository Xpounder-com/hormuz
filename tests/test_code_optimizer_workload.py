"""Prove the optional external optimizer workload still describes Hormuz code."""

from __future__ import annotations

import importlib.util
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
        self.assertIn("crlf_paths", heldout)
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


if __name__ == "__main__":
    unittest.main()
