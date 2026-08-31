"""Synthetic verifier tests; these are not live GitHub/Linear integration proof."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest import mock

from hormuz.outcome_ingest import AuthenticatedDelivery, OutcomeIngestor
from hormuz.outcome_wire import OutcomeKeys
from hormuz.portfolio_wire import PortfolioError

if __package__:
    from ._portfolio_fixture import registry_config
    from .test_outcome_contract import DELIVERY, observation
else:
    from _portfolio_fixture import registry_config
    from test_outcome_contract import DELIVERY, observation


class OutcomeIngestTests(unittest.TestCase):
    def setUp(self):
        self.config = registry_config(Path("/unused/synthetic-outcome-ingest"))
        self.binding = self.config.portfolio_control.connectors[0]
        self.verified = AuthenticatedDelivery("acme", "github-one", "github", "123", None, DELIVERY, "synthetic-key-v1")
        self.adapter = mock.Mock()
        self.adapter.verify.return_value = self.verified
        self.adapter.normalize.return_value = [observation()]
        self.repository = mock.Mock()
        self.repository._replay_verified.return_value = None
        self.repository._accept_verified.return_value = {"synthetic": "receipt"}
        self.keys = OutcomeKeys("v1", {"v1": b"k" * 32})
        self.ingestor = OutcomeIngestor(self.config, self.repository, "acme", "github-one", self.adapter, self.keys)

    def error(self, code, action):
        with self.assertRaises(PortfolioError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("SYNTHETIC_EXCLUDED", json.dumps(caught.exception.envelope()))

    def test_verification_precedes_parser_normalizer_or_storage(self):
        for error in (PortfolioError("unauthenticated"), RuntimeError("SYNTHETIC_EXCLUDED")):
            self.adapter.verify.side_effect = error
            with mock.patch("hormuz.outcome_ingest.decode_source_body", side_effect=AssertionError("parser-before-auth")):
                self.error("unauthenticated" if isinstance(error, PortfolioError) else "unavailable",
                           lambda: self.ingestor.ingest({}, b"invalid json"))
        self.adapter.normalize.assert_not_called()
        self.repository._replay_verified.assert_not_called()
        self.repository._record_failure.assert_not_called()
        self.repository._accept_verified.assert_not_called()

    def test_body_tenant_is_not_authority_and_forged_verified_scope_is_refused(self):
        self.adapter.verify.return_value = replace(self.verified, organization_id="beta", installation_id="987")
        with mock.patch("hormuz.outcome_ingest.decode_source_body", side_effect=AssertionError("forged-parser")):
            self.error("forbidden", lambda: self.ingestor.ingest({}, b'{"organization_id":"acme"}'))
        self.repository._replay_verified.assert_not_called()
        self.adapter.verify.return_value = self.verified
        self.ingestor.ingest({}, b'{"organization_id":"beta","installation_id":"987"}')
        args = self.repository._accept_verified.call_args.kwargs
        self.assertEqual(args["binding"], self.binding)
        self.assertEqual(args["verified"], self.verified)
        self.assertNotIn("organization_id", self.adapter.normalize.call_args.kwargs)

    def test_registration_disablement_precedes_verification(self):
        disabled = replace(self.config, portfolio_control=replace(self.config.portfolio_control, connectors=()))
        ingestor = OutcomeIngestor(disabled, self.repository, "acme", "github-one", self.adapter, self.keys)
        self.error("forbidden", lambda: ingestor.ingest({}, b"{}"))
        self.adapter.verify.assert_not_called()
        self.repository._replay_verified.assert_not_called()

    def test_exact_replay_does_not_reparse_renormalize_or_write(self):
        original = {"synthetic": "original-receipt"}
        self.repository._replay_verified.return_value = original
        with mock.patch("hormuz.outcome_ingest.decode_source_body", side_effect=AssertionError("duplicate-parse")):
            self.assertEqual(self.ingestor.ingest({}, b"{}"), original)
        self.adapter.normalize.assert_not_called()
        self.repository._accept_verified.assert_not_called()
        self.repository._record_failure.assert_not_called()

    def test_bounds_safe_failures_and_no_automatic_retries(self):
        self.error("invalid_request", lambda: self.ingestor.ingest({}, b"x" * 1048577))
        self.adapter.verify.assert_not_called()
        for normalized in ([observation()] * 101, [{"title": "SYNTHETIC_EXCLUDED"}], {"not": "a-list"}):
            self.adapter.normalize.return_value = normalized
            self.error("invalid_request", lambda: self.ingestor.ingest({}, b"{}"))
        self.assertEqual(self.adapter.verify.call_count, 3)
        self.assertEqual(self.repository._record_failure.call_count, 3)
        self.repository._accept_verified.assert_not_called()
        self.adapter.normalize.side_effect = RuntimeError("SYNTHETIC_EXCLUDED")
        self.error("unavailable", lambda: self.ingestor.ingest({}, b"{}"))
        self.assertEqual(self.adapter.normalize.call_count, 4)
        self.assertEqual(self.repository._record_failure.call_args.kwargs["reason"], "dependency_unavailable")

    def test_process_capacity_is_bounded_and_slots_recover_after_failure(self):
        import hormuz.outcome_ingest as module
        for _ in range(8):
            self.assertTrue(module._INGEST_SLOTS.acquire(blocking=False))
        try:
            self.error("rate_limited", lambda: self.ingestor.ingest({}, b"{}"))
            self.adapter.verify.assert_not_called()
        finally:
            for _ in range(8):
                module._INGEST_SLOTS.release()
        self.assertEqual(self.ingestor.ingest({}, b"{}"), {"synthetic": "receipt"})

    def test_stream_has_absolute_read_deadline_and_exact_length(self):
        reader, set_timeout = mock.Mock(), mock.Mock()
        reader.read1.side_effect = [b"{", b"}"]
        with mock.patch("hormuz.outcome_ingest.time.monotonic", side_effect=[0, 0, 6, 6, 11]):
            self.error("invalid_request", lambda: self.ingestor.ingest_stream({}, reader, 2, set_timeout))
        self.adapter.verify.assert_not_called()
        reader.read1.side_effect = [b"{}"]
        self.assertEqual(self.ingestor.ingest_stream({}, reader, 2, set_timeout), {"synthetic": "receipt"})


if __name__ == "__main__":
    unittest.main()
