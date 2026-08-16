from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .context import ContextError, ContextLifecycleSnapshot
from .context_store import (
    ContextRevalidationJob,
    LifecycleEvidenceResult,
    StoredLifecycleSnapshot,
)


CONTEXT_SNAPSHOT_WRITE_SCHEMA = "hormuz.context-lifecycle-snapshot-write.v1"
CONTEXT_SNAPSHOT_RESULT_SCHEMA = "hormuz.context-lifecycle-snapshot-result.v1"
CONTEXT_EVIDENCE_RESULT_SCHEMA = "hormuz.context-evidence-result.v1"
CONTEXT_REVALIDATION_REQUEST_SCHEMA = "hormuz.context-revalidation-batch-request.v1"
CONTEXT_REVALIDATION_RESULT_SCHEMA = "hormuz.context-revalidation-batch-result.v1"


@dataclass(frozen=True)
class ContextSnapshotWriteRequest:
    organization_id: str
    repository_id: str
    branch: str
    snapshot: ContextLifecycleSnapshot
    expected_version: int | None = None

    @classmethod
    def from_dict(cls, value: object) -> "ContextSnapshotWriteRequest":
        if not isinstance(value, dict):
            raise ContextError("context lifecycle snapshot write must be an object")
        allowed = {
            "schema_version",
            "organization_id",
            "repository_id",
            "branch",
            "snapshot",
            "expected_version",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ContextError(
                "unknown context lifecycle snapshot write fields: " + ", ".join(unknown)
            )
        if value.get("schema_version") != CONTEXT_SNAPSHOT_WRITE_SCHEMA:
            raise ContextError("unsupported context lifecycle snapshot write schema_version")
        expected_version = value.get("expected_version")
        if expected_version is not None and (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise ContextError(
                "context lifecycle expected_version must be null or a positive integer"
            )
        return cls(
            organization_id=_bounded_scope(value.get("organization_id"), "organization_id"),
            repository_id=_bounded_scope(value.get("repository_id"), "repository_id"),
            branch=_bounded_scope(value.get("branch"), "branch"),
            snapshot=ContextLifecycleSnapshot.from_dict(value.get("snapshot")),
            expected_version=expected_version,
        )


@dataclass(frozen=True)
class ContextRevalidationBatchRequest:
    repository_id: str
    branch: str
    batch_size: int | None = None

    @classmethod
    def from_dict(cls, value: object) -> "ContextRevalidationBatchRequest":
        if not isinstance(value, dict):
            raise ContextError("context revalidation batch request must be an object")
        allowed = {"schema_version", "repository_id", "branch", "batch_size"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ContextError(
                "unknown context revalidation batch request fields: " + ", ".join(unknown)
            )
        if value.get("schema_version") != CONTEXT_REVALIDATION_REQUEST_SCHEMA:
            raise ContextError("unsupported context revalidation batch request schema_version")
        batch_size = value.get("batch_size")
        if batch_size is not None and (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 1_000
        ):
            raise ContextError(
                "context revalidation batch_size must be null or between 1 and 1000"
            )
        return cls(
            repository_id=_bounded_scope(value.get("repository_id"), "repository_id"),
            branch=_bounded_scope(value.get("branch"), "branch"),
            batch_size=batch_size,
        )


def context_evidence_result(result: LifecycleEvidenceResult) -> dict[str, object]:
    stored = result.stored
    evidence = stored.evidence
    return {
        "schema_version": CONTEXT_EVIDENCE_RESULT_SCHEMA,
        "created": result.created,
        "evidence_id": evidence.evidence_id,
        "organization_id": evidence.organization_id,
        "record_id": evidence.record_id,
        "record_version": evidence.record_version,
        "signal": evidence.signal,
        "signal_family": evidence.signal_family,
        "observed_at": _isoformat(evidence.observed_at),
        "policy_version": stored.policy_version,
        "raw_evidence_ref_retained": False,
    }


def context_snapshot_result(stored: StoredLifecycleSnapshot) -> dict[str, object]:
    snapshot = stored.snapshot
    return {
        "schema_version": CONTEXT_SNAPSHOT_RESULT_SCHEMA,
        "organization_id": stored.organization_id,
        "repository_id": stored.repository_id,
        "branch": stored.branch,
        "repository_revision": snapshot.repository_revision,
        "snapshot_sha256": snapshot.snapshot_sha256,
        "version": stored.version,
        "artifact_count": len(snapshot.artifacts),
        "observed_at": _isoformat(stored.observed_at),
        "policy_version": stored.policy_version,
    }


def context_revalidation_result(job: ContextRevalidationJob) -> dict[str, object]:
    return {
        "schema_version": CONTEXT_REVALIDATION_RESULT_SCHEMA,
        "job_id": job.job_id,
        "organization_id": job.organization_id,
        "repository_id": job.repository_id,
        "branch": job.branch,
        "status": job.status,
        "snapshot_sha256": job.snapshot_sha256,
        "snapshot_version": job.snapshot_version,
        "policy_version": job.policy_version,
        "policy_sha256": job.policy_sha256,
        "record_set_sha256": job.record_set_sha256,
        "evidence_set_sha256": job.evidence_set_sha256,
        "total_records": job.total_records,
        "processed_records": job.processed_records,
        "promoted_records": job.promoted_records,
        "invalidated_records": job.invalidated_records,
        "unchanged_records": job.unchanged_records,
        "deferred_records": job.deferred_records,
        "updated_at": _isoformat(job.updated_at),
    }


def _bounded_scope(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.encode("utf-8")) > 512
        or any(character in value for character in ("\n", "\r", "\x00"))
    ):
        raise ContextError(f"context lifecycle {name} must be a bounded non-empty string")
    return value.strip()


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
