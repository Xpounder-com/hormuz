from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from .context import ContextLifecycleSnapshot, ContextRecord


CONTEXT_EVIDENCE_SCHEMA = "hormuz.context-evidence.v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_SAFE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")

POSITIVE_EVIDENCE_SIGNALS = (
    "commit_merged",
    "ci_passed",
    "review_accepted",
    "adr_approved",
    "incident_resolved",
    "human_confirmed",
    "failed_attempt_validated",
)
NEGATIVE_EVIDENCE_SIGNALS = (
    "commit_reverted",
    "ci_failed",
    "review_rejected",
    "adr_superseded",
    "incident_reopened",
    "human_withdrawn",
    "failed_attempt_rejected",
)
EVIDENCE_SIGNALS = POSITIVE_EVIDENCE_SIGNALS + NEGATIVE_EVIDENCE_SIGNALS
_SIGNAL_FAMILY = {
    "commit_merged": "commit",
    "commit_reverted": "commit",
    "ci_passed": "ci",
    "ci_failed": "ci",
    "review_accepted": "review",
    "review_rejected": "review",
    "adr_approved": "adr",
    "adr_superseded": "adr",
    "incident_resolved": "incident",
    "incident_reopened": "incident",
    "human_confirmed": "human",
    "human_withdrawn": "human",
    "failed_attempt_validated": "failed_attempt",
    "failed_attempt_rejected": "failed_attempt",
}


@dataclass(frozen=True)
class ContextEvidence:
    organization_id: str
    record_id: str
    record_version: int
    signal: str
    evidence_ref_sha256: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _bounded_string(self.organization_id, "organization_id", maximum=512)
        _bounded_string(self.record_id, "record_id", maximum=512)
        if (
            isinstance(self.record_version, bool)
            or not isinstance(self.record_version, int)
            or self.record_version < 1
        ):
            raise ValueError("context evidence record_version must be a positive integer")
        if self.signal not in EVIDENCE_SIGNALS:
            raise ValueError("context evidence signal is unsupported")
        if not isinstance(self.evidence_ref_sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.evidence_ref_sha256
        ):
            raise ValueError("context evidence reference fingerprint is invalid")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("context evidence observed_at must include a timezone")

    @property
    def evidence_id(self) -> str:
        canonical = json.dumps(
            {
                "schema_version": CONTEXT_EVIDENCE_SCHEMA,
                "organization_id": self.organization_id,
                "record_id": self.record_id,
                "record_version": self.record_version,
                "signal": self.signal,
                "evidence_ref_sha256": self.evidence_ref_sha256,
                "observed_at": _isoformat(self.observed_at),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "ctxev_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    @property
    def signal_family(self) -> str:
        return _SIGNAL_FAMILY[self.signal]

    @property
    def is_positive(self) -> bool:
        return self.signal in POSITIVE_EVIDENCE_SIGNALS

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTEXT_EVIDENCE_SCHEMA,
            "evidence_id": self.evidence_id,
            "organization_id": self.organization_id,
            "record_id": self.record_id,
            "record_version": self.record_version,
            "signal": self.signal,
            "signal_family": self.signal_family,
            "evidence_ref_sha256": self.evidence_ref_sha256,
            "observed_at": _isoformat(self.observed_at),
            "raw_evidence_ref_retained": False,
        }

    def to_envelope_dict(self, *, evidence_ref: str) -> dict[str, object]:
        """Build an import envelope without retaining the supplied reference."""
        if hashlib.sha256(evidence_ref.encode("utf-8")).hexdigest() != self.evidence_ref_sha256:
            raise ValueError("context evidence reference does not match its fingerprint")
        return {
            "schema_version": CONTEXT_EVIDENCE_SCHEMA,
            "organization_id": self.organization_id,
            "record_id": self.record_id,
            "record_version": self.record_version,
            "signal": self.signal,
            "evidence_ref": evidence_ref,
            "observed_at": _isoformat(self.observed_at),
        }

    @classmethod
    def from_dict(cls, value: object) -> "ContextEvidence":
        if not isinstance(value, dict):
            raise ValueError("context evidence must be an object")
        allowed = {
            "schema_version",
            "organization_id",
            "record_id",
            "record_version",
            "signal",
            "evidence_ref",
            "observed_at",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unknown context evidence fields: " + ", ".join(unknown))
        if value.get("schema_version") != CONTEXT_EVIDENCE_SCHEMA:
            raise ValueError("unsupported context evidence schema_version")
        organization_id = _bounded_string(
            value.get("organization_id"), "organization_id", maximum=512
        )
        record_id = _bounded_string(value.get("record_id"), "record_id", maximum=512)
        record_version = value.get("record_version")
        if (
            isinstance(record_version, bool)
            or not isinstance(record_version, int)
            or record_version < 1
        ):
            raise ValueError("context evidence record_version must be a positive integer")
        signal = _bounded_string(value.get("signal"), "signal", maximum=64)
        if signal not in EVIDENCE_SIGNALS:
            raise ValueError("context evidence signal is unsupported")
        reference = _bounded_string(value.get("evidence_ref"), "evidence_ref", maximum=2_048)
        observed_at = _parse_datetime(value.get("observed_at"), "observed_at")
        return cls(
            organization_id=organization_id,
            record_id=record_id,
            record_version=record_version,
            signal=signal,
            evidence_ref_sha256=hashlib.sha256(reference.encode("utf-8")).hexdigest(),
            observed_at=observed_at,
        )


@dataclass(frozen=True)
class LifecyclePromotionPath:
    path_id: str
    record_kinds: tuple[str, ...]
    required_signals: tuple[str, ...]
    required_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.path_id, str) or not _SAFE_ID_PATTERN.fullmatch(self.path_id):
            raise ValueError("lifecycle promotion path id must be a safe identifier")
        if (
            not isinstance(self.record_kinds, tuple)
            or not self.record_kinds
            or not set(self.record_kinds).issubset({"claim", "decision"})
            or len(self.record_kinds) != len(set(self.record_kinds))
        ):
            raise ValueError("lifecycle promotion path record_kinds are invalid")
        if (
            not isinstance(self.required_signals, tuple)
            or not self.required_signals
            or not set(self.required_signals).issubset(POSITIVE_EVIDENCE_SIGNALS)
            or len(self.required_signals) != len(set(self.required_signals))
        ):
            raise ValueError("lifecycle promotion path required_signals are invalid")
        if not isinstance(self.required_tags, tuple) or len(self.required_tags) != len(
            set(self.required_tags)
        ):
            raise ValueError("lifecycle promotion path required_tags are invalid")
        for tag in self.required_tags:
            _bounded_string(tag, "required_tags", maximum=128)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.path_id,
            "record_kinds": list(self.record_kinds),
            "required_signals": list(self.required_signals),
            "required_tags": list(self.required_tags),
        }

    @classmethod
    def from_dict(cls, value: object) -> "LifecyclePromotionPath":
        if not isinstance(value, dict):
            raise ValueError("lifecycle promotion path must be an object")
        unknown = sorted(
            set(value) - {"id", "record_kinds", "required_signals", "required_tags"}
        )
        if unknown:
            raise ValueError("unknown lifecycle promotion path fields: " + ", ".join(unknown))
        return cls(
            path_id=_bounded_string(value.get("id"), "id", maximum=64),
            record_kinds=_string_tuple(value.get("record_kinds"), "record_kinds", maximum=2),
            required_signals=_string_tuple(
                value.get("required_signals"), "required_signals", maximum=16
            ),
            required_tags=_string_tuple(
                value.get("required_tags", []), "required_tags", maximum=16
            ),
        )


@dataclass(frozen=True)
class LifecyclePolicy:
    policy_version: str
    promotion_paths: tuple[LifecyclePromotionPath, ...]

    def __post_init__(self) -> None:
        _bounded_string(self.policy_version, "policy_version", maximum=128)
        if (
            not isinstance(self.promotion_paths, tuple)
            or not self.promotion_paths
            or len(self.promotion_paths) > 100
            or any(not isinstance(path, LifecyclePromotionPath) for path in self.promotion_paths)
        ):
            raise ValueError("lifecycle policy must contain 1 to 100 promotion paths")
        ids = [path.path_id for path in self.promotion_paths]
        if len(ids) != len(set(ids)):
            raise ValueError("lifecycle promotion path ids must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy_version": self.policy_version,
            "promotion_paths": [path.to_dict() for path in self.promotion_paths],
        }

    @property
    def policy_sha256(self) -> str:
        canonical = json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, value: object) -> "LifecyclePolicy":
        if not isinstance(value, dict):
            raise ValueError("lifecycle policy must be an object")
        unknown = sorted(set(value) - {"policy_version", "promotion_paths"})
        if unknown:
            raise ValueError("unknown lifecycle policy fields: " + ", ".join(unknown))
        paths = value.get("promotion_paths")
        if not isinstance(paths, list):
            raise ValueError("lifecycle promotion_paths must be an array")
        return cls(
            policy_version=_bounded_string(
                value.get("policy_version"), "policy_version", maximum=128
            ),
            promotion_paths=tuple(LifecyclePromotionPath.from_dict(path) for path in paths),
        )


@dataclass(frozen=True)
class LifecycleDecision:
    target_verification: str
    reason: str
    evidence_ids: tuple[str, ...] = ()
    matched_path_id: str | None = None
    deferred: bool = False

    def __post_init__(self) -> None:
        if self.target_verification not in {"provisional", "verified"}:
            raise ValueError("lifecycle target verification is invalid")
        _bounded_string(self.reason, "reason", maximum=256)


def lifecycle_subject_sha256(record: ContextRecord) -> str:
    if not isinstance(record, ContextRecord):
        raise ValueError("lifecycle subject must be a context record")
    value = record.to_dict()
    value.pop("verification", None)
    value.pop("verification_evidence", None)
    value.pop("verified_at", None)
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_record_lifecycle(
    record: ContextRecord,
    evidences: Iterable[ContextEvidence],
    snapshot: ContextLifecycleSnapshot,
    policy: LifecyclePolicy,
) -> LifecycleDecision:
    if not isinstance(record, ContextRecord):
        raise ValueError("lifecycle record is required")
    if not isinstance(snapshot, ContextLifecycleSnapshot):
        raise ValueError("lifecycle snapshot is required")
    if not isinstance(policy, LifecyclePolicy):
        raise ValueError("lifecycle policy is required")
    candidates = tuple(evidences)
    if any(not isinstance(item, ContextEvidence) for item in candidates):
        raise ValueError("lifecycle evidences must be context evidence values")
    if any(
        item.organization_id != record.organization_id or item.record_id != record.record_id
        for item in candidates
    ):
        raise ValueError("lifecycle evidence scope does not match the record")

    source_reason = _source_invalidation_reason(record, snapshot)
    if source_reason == "dependency_observation_missing":
        return LifecycleDecision(
            target_verification=record.verification,
            reason=source_reason,
            evidence_ids=tuple(sorted(item.evidence_id for item in candidates)),
            deferred=True,
        )
    if source_reason is not None:
        return LifecycleDecision(
            target_verification="provisional",
            reason=source_reason,
            evidence_ids=tuple(sorted(item.evidence_id for item in candidates)),
        )

    if not candidates:
        if record.verification == "verified":
            return LifecycleDecision("verified", "legacy_verified_unmanaged")
        return LifecycleDecision("provisional", "configured_evidence_required")

    latest_by_family: dict[str, tuple[ContextEvidence, ...]] = {}
    for family in sorted(set(item.signal_family for item in candidates)):
        family_items = [item for item in candidates if item.signal_family == family]
        latest_at = max(item.observed_at.astimezone(timezone.utc) for item in family_items)
        latest = tuple(
            sorted(
                (
                    item
                    for item in family_items
                    if item.observed_at.astimezone(timezone.utc) == latest_at
                ),
                key=lambda item: item.evidence_id,
            )
        )
        if len({item.signal for item in latest}) > 1:
            return LifecycleDecision(
                target_verification="provisional",
                reason=f"conflicting_evidence:{family}",
                evidence_ids=tuple(item.evidence_id for item in latest),
            )
        latest_by_family[family] = latest

    active = tuple(items[0] for items in latest_by_family.values())
    negative = sorted(
        (item for item in active if not item.is_positive),
        key=lambda item: (item.signal_family, item.signal, item.evidence_id),
    )
    if negative:
        return LifecycleDecision(
            target_verification="provisional",
            reason=f"negative_evidence:{negative[0].signal}",
            evidence_ids=tuple(item.evidence_id for item in negative),
        )

    positive_by_signal = {item.signal: item for item in active if item.is_positive}
    record_tags = set(record.tags)
    matching_paths = [
        path
        for path in policy.promotion_paths
        if record.record_kind in path.record_kinds
        and set(path.required_tags).issubset(record_tags)
        and (
            "negative_knowledge" not in record_tags
            or "negative_knowledge" in path.required_tags
        )
    ]
    if not matching_paths:
        return LifecycleDecision("provisional", "no_configured_promotion_path")
    satisfied = [
        path
        for path in matching_paths
        if set(path.required_signals).issubset(positive_by_signal)
    ]
    if not satisfied:
        return LifecycleDecision(
            target_verification="provisional",
            reason="configured_evidence_incomplete",
            evidence_ids=tuple(sorted(item.evidence_id for item in active)),
        )
    selected = min(satisfied, key=lambda path: path.path_id)
    selected_evidence = tuple(
        sorted(positive_by_signal[signal].evidence_id for signal in selected.required_signals)
    )
    return LifecycleDecision(
        target_verification="verified",
        reason="configured_evidence_satisfied",
        evidence_ids=selected_evidence,
        matched_path_id=selected.path_id,
    )


def _source_invalidation_reason(
    record: ContextRecord,
    snapshot: ContextLifecycleSnapshot,
) -> str | None:
    if (
        "source_revision_changed" in record.invalidation_rules
        and record.source_revision.startswith("git:")
        and not _revisions_match(record.source_revision, snapshot.repository_revision)
    ):
        return "source_revision_changed"
    if not record.dependencies:
        return None
    observations = {artifact.uri: artifact for artifact in snapshot.artifacts}
    for dependency in sorted(record.dependencies):
        observed = observations.get(dependency.uri)
        if observed is None:
            return "dependency_observation_missing"
        if not _revisions_match(dependency.revision, observed.revision):
            return "dependency_revision_mismatch"
        if dependency.sha256 and dependency.sha256 != observed.sha256:
            return "dependency_hash_mismatch"
    return None


def _revisions_match(expected: str, observed: str) -> bool:
    def normalized(value: str) -> str:
        return value[4:] if value.startswith("git:") else value

    return normalized(expected) == normalized(observed)


def _bounded_string(value: object, name: str, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\n", "\r", "\x00"))
        or not all(character.isprintable() for character in value)
    ):
        raise ValueError(f"context evidence {name} must be a bounded single-line string")
    return value


def _string_tuple(value: object, name: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"lifecycle {name} must be a bounded string array")
    result = tuple(_bounded_string(item, name, maximum=128) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"lifecycle {name} values must be unique")
    return result


def _parse_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"context evidence {name} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"context evidence {name} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError(f"context evidence {name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
