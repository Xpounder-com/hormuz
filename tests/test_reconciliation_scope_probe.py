"""Keep the historical gap witness strict as collection selection evolves."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest
from unittest import mock


PROBE_PATH = Path(__file__).resolve().parents[1] / "docs/evidence/reconciliation_scope_probe.py"
SPEC = spec_from_file_location("reconciliation_scope_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class ReconciliationScopeProbeTests(unittest.TestCase):
    def test_historical_default_rejects_changed_collection_repository(self):
        with self.assertRaisesRegex(RuntimeError, "reconciliation_scope_probe_predecessor_changed"):
            probe.probe()

    def test_current_mode_checks_gap_without_claiming_historical_source_binding(self):
        result = probe.probe(current_runtime=True)
        self.assertEqual(result["status"], "current_runtime_scope_gap_observed")
        self.assertTrue(result["current_runtime_checked"])
        self.assertNotIn("baseline_commit", result)
        self.assertNotIn("baseline_binding", result)
        self.assertTrue(result["checks"]["as_of_collection_selector_has_explicit_cutoff"])
        self.assertFalse(result["provider_io"])
        self.assertFalse(result["credentials_read"])

    def test_current_mode_keeps_latest_selection_signature_frozen(self):
        def widened_latest(*args, extra: object = None, **kwargs):
            return None

        with mock.patch.object(
            probe.FinanceCollectionRepository, "current_observations", widened_latest,
        ), self.assertRaisesRegex(RuntimeError, "collection_selection_boundary_changed"):
            probe.probe(current_runtime=True)


if __name__ == "__main__":
    unittest.main()
