"""Offline #219/#214 witnesses. Synthetic verification is not GitHub authority."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from hormuz.outcome_ingest import AuthenticatedDelivery, OutcomeIngestor
from hormuz.outcome_repository import OutcomeRepository
from hormuz.outcome_wire import OutcomeKeys
from hormuz.portfolio_repository import create_portfolio_repository
from hormuz.portfolio_wire import PortfolioError, canonical
from hormuz.store import UsageStore
from tools.verify_github_connector_preflight import REQUIRED_FILES, ROOT, verify

from tests._outcome_fixture import SyntheticOutcomeAdapter
from tests._portfolio_fixture import registry_config
from tests._registry_transition_fixture import sqlite_backup, sqlite_snapshot
from tests.test_outcome_contract import observation


DELIVERY = "20000000-0000-4000-8000-000000000001"
HEADERS = {"synthetic-signature": "verified-test-only", "delivery": DELIVERY}
KEYS = OutcomeKeys("github-preflight-test-v1", {"github-preflight-test-v1": b"g" * 32})


class RejectingNormalizer(SyntheticOutcomeAdapter):
    def normalize(self, *, binding, verified, body):
        raise PortfolioError("invalid_request")


class GitHubPreflightPlanTests(unittest.TestCase):
    def test_plan_is_frozen_proposal_with_no_connector_or_schema_claim(self):
        result = verify()
        self.assertEqual(result["fixture_cases_pending"], 8)
        self.assertFalse(any(result[key] for key in (
            "feature_preflight_accepted", "runtime_implemented",
            "live_integration_verified", "final_candidate_accepted")))
        plan = json.loads((ROOT / "docs/github-connector-preflight-v1.json").read_text())
        self.assertEqual(plan["baseline"]["source_commit"], "7c8e5296329255bca35ef5e7d2cda9735885e7df")
        self.assertEqual((plan["baseline"]["sqlite_schema_version"],
                          plan["baseline"]["postgresql_schema_version"]), (12, 17))
        self.assertIsNone(plan["compatibility"]["connector_owned_migration_number"])
        self.assertIsNone(plan["normalization_decisions"]["accepted_event_action_conclusion_matrix"])
        self.assertEqual(plan["authentication_decisions"]["enrollment_profile"],
                         "undecided_dedicated_or_shared_app_requires_review")

    def test_body_hmac_does_not_bind_unsigned_delivery_headers(self):
        # GitHub's body HMAC authenticates these exact bytes under a secret;
        # changing unsigned routing headers leaves the same tag. A future
        # adapter needs separately reviewed channel/delivery identity proof.
        key = b"synthetic-test-only-webhook-secret"
        body = b'{"installation":{"id":123},"repository":{"id":456}}'
        signature = hmac.new(key, body, hashlib.sha256).hexdigest()
        headers_a = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": DELIVERY}
        headers_b = {"X-GitHub-Event": "check_run", "X-GitHub-Delivery": "20000000-0000-4000-8000-000000000004"}
        self.assertNotEqual(headers_a, headers_b)
        self.assertEqual(hmac.new(key, body, hashlib.sha256).hexdigest(), signature)

    def test_source_kit_and_frozen_fixture_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan = json.loads((ROOT / "docs/github-connector-preflight-v1.json").read_text())
            for name in {*REQUIRED_FILES, *plan["frozen_inputs_sha256"]}:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            self.assertEqual(verify(root)["fixture_cases_pending"], 8)
            plan_path = root / "docs/github-connector-preflight-v1.json"
            changed = {**plan, "gates": {**plan["gates"], "feature_preflight_accepted": True}}
            plan_path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "github_preflight_plan_changed"):
                verify(root)
            shutil.copyfile(ROOT / "docs/github-connector-preflight-v1.json", plan_path)
            target = root / "tests/fixtures/connectors/github/cases.json"
            target.write_bytes(target.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "github_preflight_frozen_input_changed"):
                verify(root)
            shutil.copyfile(ROOT / "tests/fixtures/connectors/github/cases.json", target)
            (root / "docs/GITHUB_CONNECTOR_PREFLIGHT.md").unlink()
            with self.assertRaisesRegex(ValueError, "github_preflight_source_kit_incomplete"):
                verify(root)


class GitHubOfflineTransitionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = registry_config(self.root)
        UsageStore(self.config.database_path)
        self.repository = create_portfolio_repository(self.config).outcomes
        self.source = observation(
            source_event_id="20000000-0000-4000-8000-000000000002",
            source_revision=None, ordering_domain=None, revision_order=None,
            event_at=None,
        )
        self.raw = canonical({"observations": [self.source], "ignored_content": "SYNTHETIC_EXCLUDED"}).encode()

    def ingestor(self, adapter=None, *, config=None, repository=None):
        return OutcomeIngestor(config or self.config, repository or self.repository,
                               "acme", "github-one", adapter or SyntheticOutcomeAdapter(), KEYS)

    def error(self, code, action):
        with self.assertRaises(PortfolioError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("SYNTHETIC_EXCLUDED", str(caught.exception))

    def test_unverified_delivery_and_disabled_binding_never_parse_or_write(self):
        before = sqlite_snapshot(self.config.database_path)
        with mock.patch("hormuz.outcome_ingest.decode_source_body", side_effect=AssertionError("parsed-before-auth")):
            self.error("unauthenticated", lambda: self.ingestor().ingest({}, b"invalid json"))
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

        class WrongInstallation(SyntheticOutcomeAdapter):
            def verify(self, *, binding, headers, raw):
                return AuthenticatedDelivery(binding.organization_id, binding.connector_id,
                                             binding.provider, "987", None, DELIVERY, "test-auth-v1")

        with mock.patch("hormuz.outcome_ingest.decode_source_body", side_effect=AssertionError("parsed-foreign-installation")):
            self.error("forbidden", lambda: self.ingestor(WrongInstallation()).ingest(HEADERS, self.raw))
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)
        disabled = replace(self.config, portfolio_control=replace(self.config.portfolio_control, connectors=()))
        with mock.patch("hormuz.outcome_ingest.decode_source_body", side_effect=AssertionError("parsed-while-disabled")):
            self.error("forbidden", lambda: self.ingestor(config=disabled).ingest(HEADERS, self.raw))
        self.assertEqual(sqlite_snapshot(self.config.database_path), before)

    def test_zero_post_checkpoint_write_restores_a_separate_snapshot(self):
        self.error("invalid_request", lambda: self.ingestor(RejectingNormalizer()).ingest(HEADERS, self.raw))
        checkpoint_state = sqlite_snapshot(self.config.database_path)
        self.assertEqual(len(checkpoint_state["rows"]["portfolio_outcome_dead_letters"]), 1)
        checkpoint = self.root / "zero-write-checkpoint.sqlite3"
        sqlite_backup(self.config.database_path, checkpoint)

        disabled = replace(self.config, portfolio_control=replace(self.config.portfolio_control, connectors=()))
        self.error("forbidden", lambda: self.ingestor(config=disabled).ingest(HEADERS, self.raw))
        self.assertEqual(sqlite_snapshot(self.config.database_path), checkpoint_state)

        restored = self.root / "zero-write-restored.sqlite3"
        sqlite_backup(checkpoint, restored)
        UsageStore(restored, read_only=True).verify_ready()
        self.assertEqual(sqlite_snapshot(restored), checkpoint_state)
        self.assertEqual(sqlite_snapshot(self.config.database_path), checkpoint_state)

    def test_failure_repair_replay_disable_and_post_write_forward_recovery(self):
        original = sqlite_snapshot(self.config.database_path)
        self.error("invalid_request", lambda: self.ingestor(RejectingNormalizer()).ingest(HEADERS, self.raw))
        failed = sqlite_snapshot(self.config.database_path)
        self.assertEqual(failed["rows"]["portfolio_outcome_events"], original["rows"]["portfolio_outcome_events"])
        self.assertEqual(failed["rows"]["portfolio_outcome_receipts"], original["rows"]["portfolio_outcome_receipts"])
        self.assertEqual(len(failed["rows"]["portfolio_outcome_dead_letters"]), 1)
        self.assertNotIn("SYNTHETIC_EXCLUDED", repr(failed))
        checkpoint = self.root / "pre-write-checkpoint.sqlite3"
        sqlite_backup(self.config.database_path, checkpoint)

        receipt = self.ingestor().ingest(HEADERS, self.raw)
        self.assertEqual(receipt["disposition"], "accepted")
        accepted = sqlite_snapshot(self.config.database_path)
        self.assertEqual(len(accepted["rows"]["portfolio_outcome_events"]), 1)
        self.assertEqual(len(accepted["rows"]["portfolio_outcome_receipts"]), 1)
        self.assertEqual(self.ingestor().ingest(HEADERS, self.raw), receipt)
        self.error("idempotency_conflict", lambda: self.ingestor().ingest(HEADERS, self.raw + b" "))
        self.assertEqual(sqlite_snapshot(self.config.database_path), accepted)

        disabled = replace(self.config, portfolio_control=replace(self.config.portfolio_control, connectors=()))
        self.error("forbidden", lambda: self.ingestor(config=disabled).ingest(
            {**HEADERS, "delivery": "20000000-0000-4000-8000-000000000003"}, self.raw))
        self.assertEqual(sqlite_snapshot(self.config.database_path), accepted)

        self.assertEqual(sqlite_snapshot(checkpoint), failed)
        self.assertNotEqual(failed, accepted)
        forward = self.root / "forward.sqlite3"
        sqlite_backup(self.config.database_path, forward)
        forward_config = replace(self.config, database_path=forward)
        forward_repository = create_portfolio_repository(forward_config).outcomes
        self.assertEqual(self.ingestor(config=forward_config, repository=forward_repository).ingest(HEADERS, self.raw), receipt)
        self.assertEqual(sqlite_snapshot(forward), accepted)
        self.assertEqual(sqlite_snapshot(self.config.database_path), accepted)

    def test_storage_outage_cannot_acknowledge_or_create_database(self):
        absent = self.root / "absent.sqlite3"
        config = replace(self.config, database_path=absent)
        repository = OutcomeRepository(config, dsn="")
        self.error("unavailable", lambda: self.ingestor(config=config, repository=repository).ingest(HEADERS, self.raw))
        self.assertFalse(absent.exists())


if __name__ == "__main__":
    unittest.main()
