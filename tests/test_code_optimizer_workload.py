"""Prove the optional external optimizer workload still describes Hormuz code."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = ROOT / "benchmarks/code_optimizer/compaction.py"


class CodeOptimizerWorkloadTests(unittest.TestCase):
    def test_reference_workload_covers_primary_and_heldout_cases(self) -> None:
        spec = importlib.util.spec_from_file_location("hormuz_optimizer_workload", WORKLOAD)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        workload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workload)

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


if __name__ == "__main__":
    unittest.main()
