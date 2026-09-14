"""Metadata capture must stay bounded, tenant scoped and honest about unknowns."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from hormuz.policy_impact import ImpactStore, ImpactRecorder, Observation, compare, iso, utcnow
from hormuz.policy_repository import PolicyControlError


def observation(**changes):
    return replace(Observation("request-1", "org-a", "team-a", "actor-a", "model-a", "policy-a",
                               8000, 8000, iso(utcnow()), "routes-a"), **changes)


class ImpactStoreTests(unittest.TestCase):
    def test_capture_accepts_configured_alias_punctuation_and_unicode(self):
        for index, alias in enumerate(("gpt@prod", "model + preview", "模型")):
            item = observation(request_id=f"alias-{index}", model_alias=alias)
            self.store.record(item)
            self.assertEqual(self.rows(model_alias=alias), (item,))

    def test_expired_metadata_is_physically_purged_on_read_and_restart(self):
        for action in (lambda: self.rows(), lambda: ImpactStore(self.path)):
            self.store.record(observation())
            self.store.save_preview(preview_id="retained", organization_id="org-a", membership_id="member-a",
                expires_at=iso(utcnow() + timedelta(days=7)), value={})
            with patch("hormuz.policy_impact.utcnow", return_value=utcnow() + timedelta(days=8)):
                action()
            with self.store.connection() as db:
                for table in ("observations", "previews"):
                    self.assertEqual(db.execute("SELECT count(*) FROM " + table).fetchone()[0], 0)

    def test_one_organization_cannot_fill_global_preview_capacity(self):
        def save(identifier, organization):
            self.store.save_preview(preview_id=identifier, organization_id=organization, membership_id="member-a",
                expires_at=iso(utcnow() + timedelta(days=7)), value={})
        with patch("hormuz.policy_impact.MAX_ORGANIZATION_PREVIEWS", 1), patch("hormuz.policy_impact.MAX_PREVIEWS", 2):
            save("a1", "org-a")
            with self.assertRaisesRegex(PolicyControlError, "impact_capacity_reached"):
                save("a2", "org-a")
            save("b1", "org-b")
            self.assertEqual(self.store.preview(preview_id="b1", organization_id="org-b", membership_id="member-a"), {})

    def test_idle_recorder_sweeps_without_request_writes(self):
        swept = threading.Event()
        class IdleStore:
            def purge_expired(self):
                swept.set()
        with patch("hormuz.policy_impact.SWEEP_INTERVAL_SECONDS", .01):
            recorder = ImpactRecorder(IdleStore())
            try:
                self.assertTrue(swept.wait(1))
            finally:
                recorder.close()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "impact.sqlite3"
        self.store = ImpactStore(self.path)

    def rows(self, **changes):
        return self.store.observations(**({"organization_id": "org-a", "team_id": "team-a",
                                           "model_alias": "model-a", "policy_version": "policy-a"} | changes))

    def test_capture_lifecycle_is_idempotent_and_scope_is_exact(self):
        pending = observation()
        self.store.record(pending)
        completed = replace(pending, status="succeeded", output_tokens=4500, cost_microusd=123)
        self.store.record(completed)
        self.store.record(pending)
        self.assertEqual(self.rows(), (completed,))
        for field in ("organization_id", "team_id", "model_alias", "policy_version"):
            self.assertEqual(self.rows(**{field: "other"}), ())
        with self.assertRaisesRegex(PolicyControlError, "impact_observation_conflict"):
            self.store.record(replace(pending, organization_id="org-b"))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_retention_and_row_caps(self):
        with patch("hormuz.policy_impact.MAX_SCOPE_OBSERVATIONS", 2), patch("hormuz.policy_impact.MAX_OBSERVATIONS", 3):
            self.store.record(observation(request_id="old", started_at=iso(utcnow() - timedelta(days=8))))
            for i in range(4):
                self.store.record(observation(request_id=f"request-{i}"))
            self.assertEqual(len(self.rows()), 2)
            for i in range(2):
                self.store.record(observation(request_id=f"other-{i}", organization_id="org-b"))
            with self.store.connection() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM observations").fetchone()[0], 3)

    def test_unknown_is_not_zero_and_existing_stricter_request_is_unchanged(self):
        rows = (observation(), observation(request_id="2", effective_limit=2000, status="succeeded", output_tokens=1000),
                observation(request_id="3", status="succeeded", output_tokens=5000),
                observation(request_id="4", status="failed"))
        result = compare(rows, 4000)
        self.assertEqual((result["lower_limit_requests"], result["known_completions"],
                          result["completions_above_limit"], result["unknown_completions"]), (3, 2, 1, 2))
        self.assertIsNone(result["savings"])
        self.assertIsNone(result["quality_effect"])
        self.assertEqual(compare((), 4000)["availability"], "no_observations")
        with self.assertRaises(PolicyControlError):
            compare(rows, True)

    def test_previews_are_bound_to_tenant_membership_and_expiry(self):
        self.store.save_preview(preview_id="p1", organization_id="org-a", membership_id="member-a",
                                expires_at=iso(utcnow() + timedelta(minutes=1)), value={"example": True})
        for org, member in (("org-b", "member-a"), ("org-a", "member-b")):
            with self.assertRaisesRegex(PolicyControlError, "impact_preview_expired"):
                self.store.preview(preview_id="p1", organization_id=org, membership_id=member)
        with patch("hormuz.policy_impact.utcnow", return_value=utcnow() + timedelta(minutes=2)):
            with self.assertRaises(PolicyControlError):
                self.store.preview(preview_id="p1", organization_id="org-a", membership_id="member-a")

    def test_unsafe_database_and_unrecognized_schema_fail_closed(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(PolicyControlError, "impact_storage_unsafe"):
            self.rows()
        self.path.chmod(0o600)
        with self.store.connection() as db:
            db.execute("PRAGMA user_version = 999")
        with self.assertRaisesRegex(PolicyControlError, "impact_schema_unsupported"):
            ImpactStore(self.path)

    def test_slow_or_failed_capture_cannot_block_request_submission(self):
        entered, release = threading.Event(), threading.Event()
        class SlowStore:
            def record(self, item):
                entered.set()
                release.wait(2)
                raise PolicyControlError("impact_storage_unavailable")
        with patch("hormuz.policy_impact.MAX_QUEUE", 1):
            recorder = ImpactRecorder(SlowStore())
            try:
                recorder.submit(observation())
                self.assertTrue(entered.wait(1))
                recorder.submit(observation(request_id="2"))
                recorder.submit(observation(request_id="3"))
                self.assertEqual(recorder.dropped, 1)
            finally:
                release.set()
                recorder.close()
        self.assertFalse(recorder._worker.is_alive())
        self.assertEqual(recorder.dropped, 3)
