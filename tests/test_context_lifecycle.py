from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hormuz.context import ContextArtifact, ContextLifecycleSnapshot, ContextRecord
from hormuz.context_lifecycle import (
    ContextEvidence,
    LifecyclePolicy,
    LifecyclePromotionPath,
    evaluate_record_lifecycle,
    lifecycle_subject_sha256,
)
from hormuz.context_store import ContextConflict, SQLiteContextRepository


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def record(record_id: str, **overrides: object) -> ContextRecord:
    values: dict[str, object] = {
        "record_id": record_id,
        "record_kind": "claim",
        "title": f"Context {record_id}",
        "content": f"Provisional engineering observation for {record_id}.",
        "owner_id": "writer",
        "organization_id": "xpounder",
        "visibility": "organization",
        "scope_id": "xpounder",
        "classification": "internal",
        "source_uri": f"https://example.test/{record_id}",
        "source_revision": "git:abc123",
        "source_item_key": record_id,
        "repository_id": "acme/api",
        "branch": "main",
        "verification": "provisional",
        "effective_at": NOW - timedelta(days=1),
        "verified_at": None,
        "invalidation_rules": ("source_revision_changed",),
        "tags": ("engineering",),
    }
    values.update(overrides)
    return ContextRecord(**values)  # type: ignore[arg-type]


def evidence(
    record_id: str,
    signal: str,
    *,
    observed_at: datetime = NOW,
    record_version: int = 1,
    reference: str | None = None,
) -> ContextEvidence:
    return ContextEvidence.from_dict(
        {
            "schema_version": "hormuz.context-evidence.v1",
            "organization_id": "xpounder",
            "record_id": record_id,
            "record_version": record_version,
            "signal": signal,
            "evidence_ref": reference or f"fixture:{record_id}:{signal}:{observed_at.isoformat()}",
            "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        }
    )


def policy() -> LifecyclePolicy:
    return LifecyclePolicy(
        policy_version="lifecycle-v1",
        promotion_paths=(
            LifecyclePromotionPath(
                path_id="merged-and-green",
                record_kinds=("claim",),
                required_signals=("commit_merged", "ci_passed"),
            ),
            LifecyclePromotionPath(
                path_id="accepted-review",
                record_kinds=("claim",),
                required_signals=("review_accepted",),
            ),
            LifecyclePromotionPath(
                path_id="approved-adr",
                record_kinds=("decision",),
                required_signals=("adr_approved",),
            ),
            LifecyclePromotionPath(
                path_id="resolved-incident",
                record_kinds=("claim",),
                required_signals=("incident_resolved",),
            ),
            LifecyclePromotionPath(
                path_id="human-confirmation",
                record_kinds=("claim", "decision"),
                required_signals=("human_confirmed",),
            ),
            LifecyclePromotionPath(
                path_id="validated-failure",
                record_kinds=("claim",),
                required_signals=("failed_attempt_validated",),
                required_tags=("negative_knowledge",),
            ),
        ),
    )


def snapshot(
    revision: str = "abc123",
    *,
    artifacts: tuple[ContextArtifact, ...] = (),
) -> ContextLifecycleSnapshot:
    return ContextLifecycleSnapshot(repository_revision=revision, artifacts=artifacts)


class ContextLifecycleDomainTests(unittest.TestCase):
    def test_evidence_discards_raw_reference_and_rejects_unknown_or_nonfinite_json_shape(self) -> None:
        raw_reference = "github-actions:run:secret-looking-private-reference"
        item = evidence("one", "ci_passed", reference=raw_reference)

        self.assertRegex(item.evidence_id, r"\Actxev_[0-9a-f]{24}\Z")
        self.assertRegex(item.evidence_ref_sha256, r"\A[0-9a-f]{64}\Z")
        self.assertNotIn(raw_reference, repr(item))
        self.assertNotIn(raw_reference, json.dumps(item.to_dict()))
        with self.assertRaisesRegex(ValueError, "unknown context evidence fields"):
            ContextEvidence.from_dict(
                {
                    **item.to_envelope_dict(evidence_ref=raw_reference),
                    "unknown": True,
                }
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            item.to_envelope_dict(evidence_ref="different-reference")
        with self.assertRaisesRegex(ValueError, "signal"):
            evidence("one", "model_said_so")

    def test_configured_paths_promote_only_when_complete(self) -> None:
        candidate = record("promotion")
        incomplete = evaluate_record_lifecycle(
            candidate,
            (evidence("promotion", "commit_merged"),),
            snapshot(),
            policy(),
        )
        complete = evaluate_record_lifecycle(
            candidate,
            (
                evidence("promotion", "commit_merged"),
                evidence("promotion", "ci_passed"),
            ),
            snapshot(),
            policy(),
        )

        self.assertEqual(incomplete.target_verification, "provisional")
        self.assertEqual(incomplete.reason, "configured_evidence_incomplete")
        self.assertEqual(complete.target_verification, "verified")
        self.assertEqual(complete.matched_path_id, "merged-and-green")
        self.assertEqual(len(complete.evidence_ids), 2)

    def test_latest_negative_signal_invalidates_and_later_positive_recovers(self) -> None:
        candidate = record("signals")
        passed = evidence("signals", "ci_passed", observed_at=NOW)
        merged = evidence("signals", "commit_merged", observed_at=NOW)
        failed = evidence("signals", "ci_failed", observed_at=NOW + timedelta(minutes=1))
        recovered = evidence("signals", "ci_passed", observed_at=NOW + timedelta(minutes=2))

        invalidated = evaluate_record_lifecycle(
            replace(candidate, verification="verified", verified_at=NOW),
            (merged, passed, failed),
            snapshot(),
            policy(),
        )
        restored = evaluate_record_lifecycle(
            candidate,
            (merged, passed, failed, recovered),
            snapshot(),
            policy(),
        )

        self.assertEqual(invalidated.target_verification, "provisional")
        self.assertEqual(invalidated.reason, "negative_evidence:ci_failed")
        self.assertEqual(restored.target_verification, "verified")

    def test_same_time_conflict_is_not_silently_ordered(self) -> None:
        candidate = record("conflict")
        decision = evaluate_record_lifecycle(
            candidate,
            (
                evidence("conflict", "commit_merged"),
                evidence("conflict", "ci_passed", reference="ci:pass"),
                evidence("conflict", "ci_failed", reference="ci:fail"),
            ),
            snapshot(),
            policy(),
        )

        self.assertEqual(decision.target_verification, "provisional")
        self.assertEqual(decision.reason, "conflicting_evidence:ci")

    def test_reverted_commit_failed_ci_and_conflicting_adrs_are_negative_evidence(self) -> None:
        claim = replace(record("negative-signals"), verification="verified", verified_at=NOW)
        base = (
            evidence("negative-signals", "commit_merged", observed_at=NOW),
            evidence("negative-signals", "ci_passed", observed_at=NOW),
        )
        reverted = evaluate_record_lifecycle(
            claim,
            (*base, evidence("negative-signals", "commit_reverted", observed_at=NOW + timedelta(minutes=1))),
            snapshot(),
            policy(),
        )
        failed = evaluate_record_lifecycle(
            claim,
            (*base, evidence("negative-signals", "ci_failed", observed_at=NOW + timedelta(minutes=1))),
            snapshot(),
            policy(),
        )
        adr = replace(
            record("adr-conflict", record_kind="decision"),
            verification="verified",
            verified_at=NOW,
        )
        conflicting_adr = evaluate_record_lifecycle(
            adr,
            (
                evidence("adr-conflict", "adr_approved", reference="adr:approved"),
                evidence("adr-conflict", "adr_superseded", reference="adr:superseded"),
            ),
            snapshot(),
            policy(),
        )

        self.assertEqual(reverted.reason, "negative_evidence:commit_reverted")
        self.assertEqual(failed.reason, "negative_evidence:ci_failed")
        self.assertEqual(conflicting_adr.reason, "conflicting_evidence:adr")
        self.assertEqual(conflicting_adr.target_verification, "provisional")

    def test_source_and_dependency_changes_invalidate_but_missing_observation_defers(self) -> None:
        dependency = ContextArtifact(uri="repo://acme/api/policy.json", revision="v1", sha256="a" * 64)
        candidate = replace(
            record("dependencies", dependencies=(dependency,)),
            verification="verified",
            verified_at=NOW - timedelta(hours=1),
        )
        signals = (
            evidence("dependencies", "commit_merged"),
            evidence("dependencies", "ci_passed"),
        )

        source_changed = evaluate_record_lifecycle(candidate, signals, snapshot("different"), policy())
        missing = evaluate_record_lifecycle(candidate, signals, snapshot(), policy())
        dependency_changed = evaluate_record_lifecycle(
            candidate,
            signals,
            snapshot(artifacts=(ContextArtifact(dependency.uri, "v2", "b" * 64),)),
            policy(),
        )
        current = evaluate_record_lifecycle(
            candidate,
            signals,
            snapshot(artifacts=(dependency,)),
            policy(),
        )

        self.assertEqual(source_changed.reason, "source_revision_changed")
        self.assertEqual(source_changed.target_verification, "provisional")
        self.assertTrue(missing.deferred)
        self.assertEqual(missing.target_verification, "verified")
        self.assertEqual(dependency_changed.reason, "dependency_revision_mismatch")
        self.assertEqual(dependency_changed.target_verification, "provisional")
        self.assertEqual(current.target_verification, "verified")

    def test_negative_knowledge_requires_its_specific_validation_path(self) -> None:
        candidate = record("failed-attempt", tags=("engineering", "negative_knowledge"))
        malicious = evaluate_record_lifecycle(
            candidate,
            (evidence("failed-attempt", "human_confirmed"),),
            snapshot(),
            policy(),
        )
        validated = evaluate_record_lifecycle(
            candidate,
            (evidence("failed-attempt", "failed_attempt_validated"),),
            snapshot(),
            policy(),
        )

        self.assertEqual(malicious.target_verification, "provisional")
        self.assertEqual(malicious.reason, "configured_evidence_incomplete")
        self.assertEqual(validated.target_verification, "verified")
        self.assertEqual(validated.matched_path_id, "validated-failure")

    def test_legacy_verified_record_without_managed_evidence_is_left_unchanged(self) -> None:
        legacy = replace(
            record("legacy", verification_evidence=("review:approved",)),
            verification="verified",
            verified_at=NOW - timedelta(days=1),
        )
        decision = evaluate_record_lifecycle(legacy, (), snapshot(), policy())

        self.assertEqual(decision.target_verification, "verified")
        self.assertEqual(decision.reason, "legacy_verified_unmanaged")

    def test_subject_fingerprint_ignores_lifecycle_flips_but_changes_with_content(self) -> None:
        provisional = record("fingerprint")
        verified = replace(
            provisional,
            verification="verified",
            verification_evidence=("ctxev_one",),
            verified_at=NOW,
        )
        changed = replace(provisional, content="Different governed content.")

        self.assertEqual(lifecycle_subject_sha256(provisional), lifecycle_subject_sha256(verified))
        self.assertNotEqual(lifecycle_subject_sha256(provisional), lifecycle_subject_sha256(changed))


class ContextLifecycleStoreTests(unittest.TestCase):
    def test_managed_import_requires_new_records_provisional_but_legacy_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            legacy = replace(
                record(
                    "legacy-import",
                    verification_evidence=("manual:approved",),
                ),
                verification="verified",
                verified_at=NOW,
            )
            created = repository.ingest(
                legacy,
                actor_id="writer",
                policy_version="legacy-v1",
            )
            repeated = repository.ingest(
                legacy,
                actor_id="writer",
                policy_version="lifecycle-v1",
                new_records_must_be_provisional=True,
            )
            with self.assertRaisesRegex(ContextConflict, "must_be_provisional"):
                repository.ingest(
                    replace(
                        legacy,
                        record_id="new-verified",
                        source_uri="https://example.test/new-verified",
                        source_item_key="new-verified",
                    ),
                    actor_id="writer",
                    policy_version="lifecycle-v1",
                    new_records_must_be_provisional=True,
                )

            self.assertTrue(created.created)
            self.assertFalse(repeated.created)
            with sqlite3.connect(repository.path) as connection:
                count = int(connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0])
            self.assertEqual(count, 1)

    def test_evidence_is_idempotent_version_bound_tenant_scoped_and_raw_reference_free(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            stored = repository.ingest(record("evidence"), actor_id="writer", policy_version="write-v1").stored
            raw_reference = "github-actions:private-run-reference"
            item = evidence("evidence", "ci_passed", reference=raw_reference)

            first = repository.record_lifecycle_evidence(
                item,
                actor_id="promoter",
                policy_version="lifecycle-v1",
                occurred_at=NOW,
            )
            duplicate = repository.record_lifecycle_evidence(
                item,
                actor_id="promoter",
                policy_version="lifecycle-v1",
                occurred_at=NOW,
            )

            self.assertTrue(first.created)
            self.assertFalse(duplicate.created)
            self.assertEqual(first.stored.evidence.evidence_id, duplicate.stored.evidence.evidence_id)
            self.assertEqual(first.stored.subject_sha256, lifecycle_subject_sha256(stored.record))
            self.assertNotIn(raw_reference.encode(), repository.path.read_bytes())
            with self.assertRaisesRegex(ContextConflict, "observed_at_in_future"):
                repository.record_lifecycle_evidence(
                    evidence(
                        "evidence",
                        "ci_failed",
                        observed_at=NOW + timedelta(minutes=6),
                    ),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )
            with self.assertRaisesRegex(ContextConflict, "record_version"):
                repository.record_lifecycle_evidence(
                    replace(item, record_version=2),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )
            with self.assertRaisesRegex(ContextConflict, "record_not_found"):
                repository.record_lifecycle_evidence(
                    replace(item, organization_id="other-org"),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )

    def test_revalidation_promotes_invalidates_and_recovers_without_reusing_stale_content_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            original = repository.ingest(
                record("lifecycle"),
                actor_id="writer",
                policy_version="write-v1",
            ).stored
            for signal in ("commit_merged", "ci_passed"):
                repository.record_lifecycle_evidence(
                    evidence("lifecycle", signal),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=None,
                actor_id="connector",
                policy_version="snapshot-v1",
                occurred_at=NOW,
            )

            promoted_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW,
            )
            promoted = repository.run_revalidation_batch(
                job_id=promoted_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
                occurred_at=NOW,
            )
            self.assertEqual(promoted.status, "completed")
            self.assertEqual(promoted.promoted_records, 1)
            self.assertEqual(repository.get_record("xpounder", "lifecycle").record.verification, "verified")

            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot("changed"),
                expected_version=1,
                actor_id="connector",
                policy_version="snapshot-v2",
                occurred_at=NOW + timedelta(minutes=1),
            )
            invalidation_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW + timedelta(minutes=1),
            )
            invalidated = repository.run_revalidation_batch(
                job_id=invalidation_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
                occurred_at=NOW + timedelta(minutes=1),
            )
            self.assertEqual(invalidated.invalidated_records, 1)
            self.assertEqual(repository.get_record("xpounder", "lifecycle").record.verification, "provisional")

            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=2,
                actor_id="connector",
                policy_version="snapshot-v3",
                occurred_at=NOW + timedelta(minutes=2),
            )
            recovery_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW + timedelta(minutes=2),
            )
            recovered = repository.run_revalidation_batch(
                job_id=recovery_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
                occurred_at=NOW + timedelta(minutes=2),
            )
            restored = repository.get_record("xpounder", "lifecycle")
            self.assertEqual(recovered.promoted_records, 1)
            self.assertEqual(restored.record.verification, "verified")
            self.assertEqual(lifecycle_subject_sha256(restored.record), lifecycle_subject_sha256(original.record))

            changed = replace(
                restored.record,
                content="A changed claim that requires fresh evidence.",
                source_revision="git:new-content",
                source_item_key="lifecycle-v2",
                verification="provisional",
                verification_evidence=(),
                verified_at=None,
            )
            repository.update(
                changed,
                expected_version=restored.version,
                actor_id="writer",
                policy_version="write-v2",
                occurred_at=NOW + timedelta(minutes=3),
            )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot("new-content"),
                expected_version=3,
                actor_id="connector",
                policy_version="snapshot-v4",
                occurred_at=NOW + timedelta(minutes=3),
            )
            new_content_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW + timedelta(minutes=3),
            )
            repository.run_revalidation_batch(
                job_id=new_content_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
                occurred_at=NOW + timedelta(minutes=3),
            )
            self.assertEqual(repository.get_record("xpounder", "lifecycle").record.verification, "provisional")

    def test_jobs_are_idempotent_batched_resumable_and_lease_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            for record_id in ("a", "b"):
                repository.ingest(record(record_id), actor_id="writer", policy_version="write-v1")
                for signal in ("commit_merged", "ci_passed"):
                    repository.record_lifecycle_evidence(
                        evidence(record_id, signal),
                        actor_id="promoter",
                        policy_version="lifecycle-v1",
                        occurred_at=NOW,
                    )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=None,
                actor_id="connector",
                policy_version="snapshot-v1",
                occurred_at=NOW,
            )
            first = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW,
            )
            duplicate = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW,
            )
            self.assertEqual(first.job_id, duplicate.job_id)

            one = repository.run_revalidation_batch(
                job_id=first.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=1,
                lease_seconds=30,
                occurred_at=NOW,
            )
            self.assertEqual(one.status, "pending")
            self.assertEqual(one.processed_records, 1)
            two = repository.run_revalidation_batch(
                job_id=first.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=1,
                lease_seconds=30,
                occurred_at=NOW + timedelta(seconds=1),
            )
            self.assertEqual(two.status, "completed")
            self.assertEqual(two.processed_records, 2)
            self.assertEqual(two.promoted_records, 2)

            with sqlite3.connect(repository.path) as connection:
                connection.execute(
                    "UPDATE context_revalidation_jobs SET status = 'running', "
                    "lease_owner = 'other', lease_expires_at = ? WHERE id = ?",
                    ((NOW + timedelta(minutes=1)).isoformat(), first.job_id),
                )
            with self.assertRaisesRegex(ContextConflict, "lease"):
                repository.run_revalidation_batch(
                    job_id=first.job_id,
                    policy=policy(),
                    actor_id="promoter",
                    batch_size=1,
                    lease_seconds=30,
                    occurred_at=NOW + timedelta(seconds=2),
                )
            with sqlite3.connect(repository.path) as connection:
                connection.execute(
                    "UPDATE context_revalidation_jobs SET status = 'running', "
                    "lease_owner = 'crashed-worker', lease_expires_at = ? WHERE id = ?",
                    ((NOW - timedelta(seconds=1)).isoformat(), first.job_id),
                )
            resumed = repository.run_revalidation_batch(
                job_id=first.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=1,
                lease_seconds=30,
                occurred_at=NOW + timedelta(seconds=3),
            )
            self.assertEqual(resumed.status, "completed")

    def test_policy_hash_and_record_set_make_job_identity_replay_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            repository.ingest(record("before"), actor_id="writer", policy_version="write-v1")
            for signal in ("commit_merged", "ci_passed"):
                repository.record_lifecycle_evidence(
                    evidence("before", signal),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=None,
                actor_id="connector",
                policy_version="snapshot-v1",
            )
            first = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
            )
            changed_policy = LifecyclePolicy(
                policy_version=policy().policy_version,
                promotion_paths=(
                    LifecyclePromotionPath(
                        path_id="human-only",
                        record_kinds=("claim",),
                        required_signals=("human_confirmed",),
                    ),
                ),
            )
            with self.assertRaisesRegex(ContextConflict, "policy_conflict"):
                repository.run_revalidation_batch(
                    job_id=first.job_id,
                    policy=changed_policy,
                    actor_id="promoter",
                    batch_size=10,
                    lease_seconds=30,
                )
            policy_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=changed_policy,
                actor_id="promoter",
            )
            self.assertNotEqual(first.job_id, policy_job.job_id)

            repository.ingest(record("after"), actor_id="writer", policy_version="write-v1")
            result = repository.run_revalidation_batch(
                job_id=first.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
            )
            self.assertEqual(result.status, "superseded")
            self.assertEqual(result.total_records, 1)
            self.assertEqual(result.processed_records, 0)
            self.assertEqual(repository.get_record("xpounder", "after").record.verification, "provisional")

            next_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
            )
            self.assertNotEqual(first.job_id, next_job.job_id)
            self.assertEqual(next_job.total_records, 2)

            before = repository.get_record("xpounder", "before")
            repository.update(
                replace(
                    before.record,
                    content="Semantically changed governed content.",
                    source_revision="git:def456",
                    source_item_key="before-v2",
                ),
                expected_version=before.version,
                actor_id="writer",
                policy_version="write-v2",
            )
            changed_record_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
            )
            self.assertNotEqual(next_job.job_id, changed_record_job.job_id)
            superseded = repository.run_revalidation_batch(
                job_id=next_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
            )
            self.assertEqual(superseded.status, "superseded")

    def test_new_evidence_creates_a_new_job_after_an_unchanged_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            repository.ingest(record("late-evidence"), actor_id="writer", policy_version="write-v1")
            repository.record_lifecycle_evidence(
                evidence("late-evidence", "commit_merged"),
                actor_id="promoter",
                policy_version="lifecycle-v1",
                occurred_at=NOW,
            )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=None,
                actor_id="connector",
                policy_version="snapshot-v1",
                occurred_at=NOW,
            )
            incomplete_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW,
            )
            incomplete = repository.run_revalidation_batch(
                job_id=incomplete_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
                occurred_at=NOW,
            )
            self.assertEqual(incomplete.status, "completed")
            self.assertEqual(incomplete.unchanged_records, 1)

            repository.record_lifecycle_evidence(
                evidence("late-evidence", "ci_passed", observed_at=NOW + timedelta(minutes=1)),
                actor_id="promoter",
                policy_version="lifecycle-v1",
                occurred_at=NOW + timedelta(minutes=1),
            )
            complete_job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
                occurred_at=NOW + timedelta(minutes=1),
            )
            self.assertNotEqual(incomplete_job.job_id, complete_job.job_id)
            self.assertNotEqual(
                incomplete_job.evidence_set_sha256,
                complete_job.evidence_set_sha256,
            )
            complete = repository.run_revalidation_batch(
                job_id=complete_job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
                occurred_at=NOW + timedelta(minutes=1),
            )

            self.assertEqual(complete.status, "completed")
            self.assertEqual(complete.promoted_records, 1)
            self.assertEqual(
                repository.get_record("xpounder", "late-evidence").record.verification,
                "verified",
            )

    def test_changed_snapshot_supersedes_an_unstarted_job_without_mutating_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = SQLiteContextRepository(Path(temporary) / "context.sqlite3")
            repository.ingest(record("stale-job"), actor_id="writer", policy_version="write-v1")
            for signal in ("commit_merged", "ci_passed"):
                repository.record_lifecycle_evidence(
                    evidence("stale-job", signal),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=None,
                actor_id="connector",
                policy_version="snapshot-v1",
            )
            job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
            )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot("changed"),
                expected_version=1,
                actor_id="connector",
                policy_version="snapshot-v2",
            )

            result = repository.run_revalidation_batch(
                job_id=job.job_id,
                policy=policy(),
                actor_id="promoter",
                batch_size=10,
                lease_seconds=30,
            )

            self.assertEqual(result.status, "superseded")
            self.assertEqual(repository.get_record("xpounder", "stale-job").record.verification, "provisional")

    def test_concurrent_workers_allow_one_lease_holder(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "context.sqlite3"
            repository = SQLiteContextRepository(path)
            repository.ingest(record("race"), actor_id="writer", policy_version="write-v1")
            for signal in ("commit_merged", "ci_passed"):
                repository.record_lifecycle_evidence(
                    evidence("race", signal),
                    actor_id="promoter",
                    policy_version="lifecycle-v1",
                    occurred_at=NOW,
                )
            repository.observe_lifecycle_snapshot(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                snapshot=snapshot(),
                expected_version=None,
                actor_id="connector",
                policy_version="snapshot-v1",
            )
            job = repository.start_revalidation_job(
                organization_id="xpounder",
                repository_id="acme/api",
                branch="main",
                policy=policy(),
                actor_id="promoter",
            )
            barrier = threading.Barrier(2)
            outcomes: list[str] = []
            lock = threading.Lock()

            def worker() -> None:
                local = SQLiteContextRepository(path)
                barrier.wait()
                try:
                    result = local.run_revalidation_batch(
                        job_id=job.job_id,
                        policy=policy(),
                        actor_id="promoter",
                        batch_size=1,
                        lease_seconds=30,
                    )
                    outcome = result.status
                except ContextConflict as error:
                    outcome = str(error)
                with lock:
                    outcomes.append(outcome)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(len(outcomes), 2)
            self.assertIn("completed", outcomes)
            self.assertTrue(
                all(
                    value in {"completed", "context_revalidation_lease_conflict"}
                    for value in outcomes
                )
            )
            with sqlite3.connect(repository.path) as connection:
                changes = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_revalidation_changes WHERE job_id = ?",
                        (job.job_id,),
                    ).fetchone()[0]
                )
            self.assertEqual(changes, 1)


if __name__ == "__main__":
    unittest.main()
