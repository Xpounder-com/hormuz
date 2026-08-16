from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


CONTEXT_PACK_SCHEMA = "hormuz.context-pack.v1"
CONTEXT_LIFECYCLE_SCHEMA = "hormuz.context-lifecycle-snapshot.v1"
CONTEXT_RETRIEVAL_VERSION = "lexical-v1"
CONTEXT_RENDER_VERSION = "json-v1"
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
VISIBILITIES = ("organization", "team", "actor")
VERIFICATION_STATES = ("provisional", "verified")
RECORD_KINDS = ("claim", "decision")
_CLASSIFICATION_RANK = {name: index for index, name in enumerate(CLASSIFICATIONS)}
_TERM_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_TOKEN_ESTIMATE_INTEGER_SENTINEL = 9_999_999_999
_RETRIEVAL_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "with",
        "without",
    }
)
_PROMPT_INJECTION_INDICATORS = (
    (
        "policy_override",
        re.compile(
            r"\b(?:bypass|disregard|ignore|override)\b.{0,96}\b(?:instructions?|policy|policies)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "secret_exfiltration",
        re.compile(
            r"\b(?:exfiltrate|print|reveal|send)\b.{0,96}\b(?:api[ -]?keys?|credentials?|environment (?:variables?|credentials?)|secrets?)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "instruction_escalation",
        re.compile(
            r"\btreat\b.{0,96}\b(?:developer|system) instructions?\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


class ContextError(ValueError):
    pass


@dataclass(frozen=True, order=True)
class ContextArtifact:
    """A content-free immutable artifact identity used for lifecycle evaluation."""

    uri: str
    revision: str
    sha256: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri.strip():
            raise ContextError("context artifact uri must be a non-empty string")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ContextError("context artifact revision must be a non-empty string")
        if len(self.uri.encode("utf-8")) > 4096 or len(self.revision.encode("utf-8")) > 4096:
            raise ContextError("context artifact identity must not exceed 4096 bytes")
        if not isinstance(self.sha256, str):
            raise ContextError("context artifact sha256 must be a string")
        if self.sha256 and not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ContextError(
                "context artifact sha256 must be 64 lowercase hexadecimal characters"
            )
        if any(
            character in self.uri or character in self.revision
            for character in ("\n", "\r", "\x00")
        ):
            raise ContextError("context artifact identity contains a control character")

    def to_dict(self) -> dict[str, str]:
        return {"uri": self.uri, "revision": self.revision, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, value: object) -> "ContextArtifact":
        if not isinstance(value, dict):
            raise ContextError("context artifact must be an object")
        unknown = sorted(set(value) - {"uri", "revision", "sha256"})
        if unknown:
            raise ContextError(f"unknown context artifact fields: {', '.join(unknown)}")
        return cls(
            uri=_required_string(value, "uri"),
            revision=_required_string(value, "revision"),
            sha256=_optional_empty_string(value, "sha256"),
        )


@dataclass(frozen=True)
class ContextLifecycleSnapshot:
    """Trusted repository/dependency state evaluated before context ranking."""

    repository_revision: str
    artifacts: tuple[ContextArtifact, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.repository_revision, str) or not self.repository_revision.strip():
            raise ContextError("context lifecycle repository_revision must be a non-empty string")
        if len(self.repository_revision.encode("utf-8")) > 512 or any(
            character in self.repository_revision for character in ("\n", "\r", "\x00")
        ):
            raise ContextError(
                "context lifecycle repository_revision must be a bounded single-line string"
            )
        if not isinstance(self.artifacts, tuple) or any(
            not isinstance(item, ContextArtifact) for item in self.artifacts
        ):
            raise ContextError("context lifecycle artifacts must be ContextArtifact values")
        if len(self.artifacts) > 10_000:
            raise ContextError("context lifecycle snapshot cannot exceed 10000 artifacts")
        uris = [item.uri for item in self.artifacts]
        if len(uris) != len(set(uris)):
            raise ContextError("context lifecycle artifact URIs must be unique")

    @property
    def snapshot_sha256(self) -> str:
        canonical = json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_LIFECYCLE_SCHEMA,
            "repository_revision": self.repository_revision,
            "artifacts": [item.to_dict() for item in sorted(self.artifacts)],
        }

    @classmethod
    def from_dict(cls, value: object) -> "ContextLifecycleSnapshot":
        if not isinstance(value, dict):
            raise ContextError("context lifecycle snapshot must be an object")
        unknown = sorted(
            set(value) - {"schema_version", "repository_revision", "artifacts"}
        )
        if unknown:
            raise ContextError(f"unknown context lifecycle fields: {', '.join(unknown)}")
        if value.get("schema_version") != CONTEXT_LIFECYCLE_SCHEMA:
            raise ContextError("unsupported context lifecycle schema_version")
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list):
            raise ContextError("context lifecycle artifacts must be an array")
        return cls(
            repository_revision=_required_string(value, "repository_revision"),
            artifacts=tuple(ContextArtifact.from_dict(item) for item in artifacts),
        )


@dataclass(frozen=True)
class ContextRecord:
    record_id: str
    title: str
    content: str = field(repr=False)
    record_kind: str = "claim"
    owner_id: str = ""
    organization_id: str = ""
    visibility: str = "organization"
    scope_id: str = ""
    classification: str = "internal"
    source_uri: str = ""
    source_revision: str = ""
    source_sha256: str = ""
    source_item_key: str = ""
    repository_id: str | None = None
    branch: str | None = None
    verification: str = "provisional"
    verification_evidence: tuple[str, ...] = ()
    effective_at: datetime | None = None
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    supersedes_id: str | None = None
    invalidation_rules: tuple[str, ...] = ()
    dependencies: tuple[ContextArtifact, ...] = ()
    assertion_key: str | None = None
    assertion_value: str | None = None
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("record_id", self.record_id),
            ("title", self.title),
            ("content", self.content),
            ("organization_id", self.organization_id),
            ("scope_id", self.scope_id),
            ("source_uri", self.source_uri),
            ("source_revision", self.source_revision),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ContextError(f"context record {name} must be a non-empty string")
        for name, value in (
            ("owner_id", self.owner_id),
            ("source_sha256", self.source_sha256),
            ("source_item_key", self.source_item_key),
        ):
            if not isinstance(value, str):
                raise ContextError(f"context record {name} must be a string")
        if self.visibility not in VISIBILITIES:
            raise ContextError(f"context record visibility must be one of: {', '.join(VISIBILITIES)}")
        if self.record_kind not in RECORD_KINDS:
            raise ContextError(f"context record kind must be one of: {', '.join(RECORD_KINDS)}")
        if self.classification not in CLASSIFICATIONS:
            raise ContextError(
                f"context record classification must be one of: {', '.join(CLASSIFICATIONS)}"
            )
        if self.verification not in VERIFICATION_STATES:
            raise ContextError(
                f"context record verification must be one of: {', '.join(VERIFICATION_STATES)}"
            )
        if self.verification == "verified" and self.verified_at is None:
            raise ContextError("verified context records require verified_at")
        if self.verification == "provisional" and self.verified_at is not None:
            raise ContextError("provisional context records cannot set verified_at")
        for name, value in (
            ("effective_at", self.effective_at),
            ("verified_at", self.verified_at),
            ("expires_at", self.expires_at),
        ):
            if value is not None and not isinstance(value, datetime):
                raise ContextError(f"context record {name} must be a datetime")
            if value is not None and value.tzinfo is None:
                raise ContextError(f"context record {name} must include a timezone")
        if self.supersedes_id == self.record_id:
            raise ContextError("context record cannot supersede itself")
        if self.supersedes_id is not None and (
            not isinstance(self.supersedes_id, str) or not self.supersedes_id.strip()
        ):
            raise ContextError("context record supersedes_id must be null or a non-empty string")
        if self.visibility == "organization" and self.scope_id != self.organization_id:
            raise ContextError("organization-visible context scope_id must equal organization_id")
        for name, value in (("repository_id", self.repository_id), ("branch", self.branch)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ContextError(f"context record {name} must be null or a non-empty string")
        if self.branch is not None and self.repository_id is None:
            raise ContextError("branch-scoped context records require repository_id")
        if (
            self.verified_at is not None
            and self.expires_at is not None
            and self.expires_at <= self.verified_at
        ):
            raise ContextError("context record expires_at must be after verified_at")
        if self.effective_at is not None and self.expires_at is not None and self.expires_at <= self.effective_at:
            raise ContextError("context record expires_at must be after effective_at")
        if self.source_sha256 and not _SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ContextError("context record source.sha256 must be 64 lowercase hexadecimal characters")
        for name, values in (
            ("verification_evidence", self.verification_evidence),
            ("invalidation_rules", self.invalidation_rules),
            ("tags", self.tags),
        ):
            if not isinstance(values, tuple):
                raise ContextError(f"context record {name} must be a tuple")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ContextError(f"context record {name} must contain non-empty strings")
        if not isinstance(self.dependencies, tuple) or any(
            not isinstance(item, ContextArtifact) for item in self.dependencies
        ):
            raise ContextError("context record dependencies must be ContextArtifact values")
        if len(self.dependencies) > 1_000:
            raise ContextError("context record cannot exceed 1000 dependencies")
        dependency_uris = [item.uri for item in self.dependencies]
        if len(dependency_uris) != len(set(dependency_uris)):
            raise ContextError("context record dependency URIs must be unique")
        if (self.assertion_key is None) != (self.assertion_value is None):
            raise ContextError("context record assertion requires both key and value")
        for name, value in (
            ("assertion_key", self.assertion_key),
            ("assertion_value", self.assertion_value),
        ):
            if value is not None and (
                not isinstance(value, str)
                or not value.strip()
                or len(value.encode("utf-8")) > 4096
                or any(character in value for character in ("\n", "\r", "\x00"))
            ):
                raise ContextError(
                    f"context record {name} must be null or a bounded non-empty string"
                )

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.record_id,
            "kind": self.record_kind,
            "title": self.title,
            "content": self.content,
            "owner_id": self.owner_id,
            "organization_id": self.organization_id,
            "visibility": self.visibility,
            "scope_id": self.scope_id,
            "classification": self.classification,
            "source": {
                "uri": self.source_uri,
                "revision": self.source_revision,
                "sha256": self.source_sha256,
                "item_key": self.source_item_key,
            },
            "repository_id": self.repository_id,
            "branch": self.branch,
            "verification": self.verification,
            "verification_evidence": list(self.verification_evidence),
            "effective_at": _isoformat(self.effective_at),
            "verified_at": _isoformat(self.verified_at),
            "expires_at": _isoformat(self.expires_at),
            "supersedes_id": self.supersedes_id,
            "invalidation_rules": list(self.invalidation_rules),
            "dependencies": [item.to_dict() for item in sorted(self.dependencies)],
            "assertion": (
                {"key": self.assertion_key, "value": self.assertion_value}
                if self.assertion_key is not None
                else None
            ),
            "tags": list(self.tags),
            "content_sha256": self.content_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ContextRecord":
        allowed = {
            "id",
            "title",
            "content",
            "kind",
            "owner_id",
            "organization_id",
            "visibility",
            "scope_id",
            "classification",
            "source",
            "repository_id",
            "branch",
            "verification",
            "verification_evidence",
            "effective_at",
            "verified_at",
            "expires_at",
            "supersedes_id",
            "invalidation_rules",
            "dependencies",
            "assertion",
            "tags",
            "content_sha256",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ContextError(f"unknown context record fields: {', '.join(unknown)}")
        source = value.get("source")
        if not isinstance(source, dict):
            raise ContextError("context record source must be an object")
        unknown_source = sorted(set(source) - {"uri", "revision", "sha256", "item_key"})
        if unknown_source:
            raise ContextError(f"unknown context source fields: {', '.join(unknown_source)}")
        tags = value.get("tags", [])
        if not isinstance(tags, list):
            raise ContextError("context record tags must be an array")
        evidence = value.get("verification_evidence", [])
        if not isinstance(evidence, list):
            raise ContextError("context record verification_evidence must be an array")
        invalidation_rules = value.get("invalidation_rules", [])
        if not isinstance(invalidation_rules, list):
            raise ContextError("context record invalidation_rules must be an array")
        dependencies = value.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ContextError("context record dependencies must be an array")
        assertion = value.get("assertion")
        if assertion is not None:
            if not isinstance(assertion, dict):
                raise ContextError("context record assertion must be null or an object")
            unknown_assertion = sorted(set(assertion) - {"key", "value"})
            if unknown_assertion:
                raise ContextError(
                    f"unknown context assertion fields: {', '.join(unknown_assertion)}"
                )
        expected_content_sha256 = value.get("content_sha256")
        if expected_content_sha256 is not None and (
            not isinstance(expected_content_sha256, str)
            or not _SHA256_PATTERN.fullmatch(expected_content_sha256)
        ):
            raise ContextError(
                "context record content_sha256 must be 64 lowercase hexadecimal characters"
            )
        record = cls(
            record_id=_required_string(value, "id"),
            title=_required_string(value, "title"),
            content=_required_string(value, "content"),
            record_kind=_optional_string(value, "kind", "claim"),
            owner_id=_optional_empty_string(value, "owner_id"),
            organization_id=_required_string(value, "organization_id"),
            visibility=_optional_string(value, "visibility", "organization"),
            scope_id=_required_string(value, "scope_id"),
            classification=_optional_string(value, "classification", "internal"),
            source_uri=_required_string(source, "uri", prefix="source."),
            source_revision=_required_string(source, "revision", prefix="source."),
            source_sha256=_optional_empty_string(source, "sha256"),
            source_item_key=_optional_empty_string(source, "item_key"),
            repository_id=_nullable_string(value, "repository_id"),
            branch=_nullable_string(value, "branch"),
            verification=_optional_string(value, "verification", "provisional"),
            verification_evidence=tuple(evidence),
            effective_at=_nullable_datetime(value, "effective_at"),
            verified_at=_nullable_datetime(value, "verified_at"),
            expires_at=_nullable_datetime(value, "expires_at"),
            supersedes_id=_nullable_string(value, "supersedes_id"),
            invalidation_rules=tuple(invalidation_rules),
            dependencies=tuple(ContextArtifact.from_dict(item) for item in dependencies),
            assertion_key=(
                _required_string(assertion, "key") if assertion is not None else None
            ),
            assertion_value=(
                _required_string(assertion, "value") if assertion is not None else None
            ),
            tags=tuple(tags),
        )
        if expected_content_sha256 is not None and record.content_sha256 != expected_content_sha256:
            raise ContextError("context record content_sha256 does not match content")
        return record


@dataclass(frozen=True)
class ContextPrincipal:
    organization_id: str
    team_id: str
    actor_id: str
    clearance: str = "internal"
    repository_id: str | None = None
    branch: str | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("organization_id", self.organization_id),
            ("team_id", self.team_id),
            ("actor_id", self.actor_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ContextError(f"context principal {name} must be a non-empty string")
        if self.clearance not in CLASSIFICATIONS:
            raise ContextError(f"context principal clearance must be one of: {', '.join(CLASSIFICATIONS)}")
        for name, value in (("repository_id", self.repository_id), ("branch", self.branch)):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ContextError(f"context principal {name} must be null or a non-empty string")
        if self.branch is not None and self.repository_id is None:
            raise ContextError("context principal branch requires repository_id")


@dataclass(frozen=True)
class ContextPackRequest:
    query: str
    principal: ContextPrincipal
    token_budget: int
    policy_version: str
    max_items: int = 20
    include_provisional: bool = False
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not isinstance(self.principal, ContextPrincipal):
            raise ContextError("context principal must be a ContextPrincipal")
        if not isinstance(self.query, str) or not self.query.strip():
            raise ContextError("context query must be a non-empty string")
        if len(self.query) > 4096:
            raise ContextError("context query cannot exceed 4096 characters")
        if not _terms(self.query):
            raise ContextError("context query must contain at least one letter or number")
        if (
            isinstance(self.token_budget, bool)
            or not isinstance(self.token_budget, int)
            or not 1 <= self.token_budget <= 1_000_000
        ):
            raise ContextError("context token_budget must be between 1 and 1000000")
        if (
            isinstance(self.max_items, bool)
            or not isinstance(self.max_items, int)
            or not 1 <= self.max_items <= 100
        ):
            raise ContextError("context max_items must be between 1 and 100")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ContextError("context policy_version must be a non-empty string")
        if not isinstance(self.include_provisional, bool):
            raise ContextError("context include_provisional must be a boolean")
        if not isinstance(self.as_of, datetime):
            raise ContextError("context as_of must be a datetime")
        if self.as_of.tzinfo is None:
            raise ContextError("context as_of must include a timezone")


@dataclass(frozen=True)
class ContextPackItem:
    record: ContextRecord
    relevance_score: int
    estimated_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return _context_pack_item_dict(
            self.record,
            relevance_score=self.relevance_score,
            estimated_tokens=self.estimated_tokens,
        )


def _context_pack_item_dict(
    record: ContextRecord,
    *,
    relevance_score: int,
    estimated_tokens: int,
) -> dict[str, Any]:
    return {
        "id": record.record_id,
        "title": record.title,
        "content": record.content,
        "kind": record.record_kind,
        "owner_id": record.owner_id,
        "classification": record.classification,
        "visibility": record.visibility,
        "scope_id": record.scope_id,
        "repository_id": record.repository_id,
        "branch": record.branch,
        "verification": record.verification,
        "verification_evidence": list(record.verification_evidence),
        "effective_at": _isoformat(record.effective_at),
        "verified_at": _isoformat(record.verified_at),
        "expires_at": _isoformat(record.expires_at),
        "supersedes_id": record.supersedes_id,
        "invalidation_rules": list(record.invalidation_rules),
        "dependencies": [item.to_dict() for item in sorted(record.dependencies)],
        "assertion": (
            {"key": record.assertion_key, "value": record.assertion_value}
            if record.assertion_key is not None
            else None
        ),
        "tags": list(record.tags),
        "source": {
            "uri": record.source_uri,
            "revision": record.source_revision,
            "sha256": record.source_sha256,
            "item_key": record.source_item_key,
        },
        "content_sha256": record.content_sha256,
        "relevance_score": relevance_score,
        "estimated_tokens": estimated_tokens,
    }


@dataclass(frozen=True)
class ContextPackExclusion:
    record_id: str
    reason: str
    source_uri: str
    source_revision: str
    source_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "record_id": self.record_id,
            "reason": self.reason,
            "source_uri": self.source_uri,
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class ContextContradictionSource:
    record_id: str
    assertion_value: str
    source_uri: str
    source_revision: str
    source_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "record_id": self.record_id,
            "assertion_value": self.assertion_value,
            "source_uri": self.source_uri,
            "source_revision": self.source_revision,
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True)
class ContextContradiction:
    assertion_key: str
    sources: tuple[ContextContradictionSource, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "assertion_key": self.assertion_key,
            "sources": [item.to_dict() for item in self.sources],
        }


@dataclass(frozen=True)
class ContextPack:
    pack_id: str
    manifest_sha256: str
    request: ContextPackRequest
    items: tuple[ContextPackItem, ...]
    estimated_tokens: int
    eligible_records: int
    matched_records: int
    outcome: str
    lifecycle_snapshot_sha256: str | None
    repository_revision: str | None
    exclusions: tuple[ContextPackExclusion, ...]
    contradictions: tuple[ContextContradiction, ...]

    def to_dict(self) -> dict[str, Any]:
        principal = self.request.principal
        return {
            "schema_version": CONTEXT_PACK_SCHEMA,
            "pack_id": self.pack_id,
            "manifest_sha256": self.manifest_sha256,
            "query": self.request.query,
            "policy_version": self.request.policy_version,
            "retrieval_version": CONTEXT_RETRIEVAL_VERSION,
            "render_version": CONTEXT_RENDER_VERSION,
            "as_of": _isoformat(self.request.as_of),
            "scope": {
                "organization_id": principal.organization_id,
                "team_id": principal.team_id,
                "actor_id": principal.actor_id,
                "clearance": principal.clearance,
                "repository_id": principal.repository_id,
                "branch": principal.branch,
            },
            "token_budget": self.request.token_budget,
            "estimated_tokens": self.estimated_tokens,
            "eligible_records": self.eligible_records,
            "matched_records": self.matched_records,
            "selected_records": len(self.items),
            "lifecycle": {
                "version": "v1",
                "outcome": self.outcome,
                "snapshot_sha256": self.lifecycle_snapshot_sha256,
                "repository_revision": self.repository_revision,
                "excluded_records": len(self.exclusions),
                "contradiction_groups": len(self.contradictions),
            },
            "exclusions": [item.to_dict() for item in self.exclusions],
            "contradictions": [item.to_dict() for item in self.contradictions],
            "items": [item.to_dict() for item in self.items],
        }


def build_context_pack(
    records: Iterable[ContextRecord],
    request: ContextPackRequest,
    *,
    lifecycle_snapshot: ContextLifecycleSnapshot | None = None,
) -> ContextPack:
    if lifecycle_snapshot is not None and not isinstance(
        lifecycle_snapshot, ContextLifecycleSnapshot
    ):
        raise ContextError("context lifecycle_snapshot must be a ContextLifecycleSnapshot")
    by_id: dict[str, ContextRecord] = {}
    for record in records:
        if not isinstance(record, ContextRecord):
            raise ContextError("context records must be ContextRecord instances")
        if record.record_id in by_id:
            raise ContextError(f"duplicate context record id: {record.record_id}")
        by_id[record.record_id] = record
    _validate_supersession_graph(by_id)

    access_authorized = [
        record for record in by_id.values() if _is_access_authorized(record, request)
    ]
    eligible = [
        record
        for record in access_authorized
        if _eligibility_exclusion_reason(record, request) is None
    ]
    superseded_ids = {
        record.supersedes_id
        for record in eligible
        if record.supersedes_id is not None and record.supersedes_id in by_id
    }
    active = [record for record in eligible if record.record_id not in superseded_ids]

    query_terms = _terms(request.query)
    exclusions: list[ContextPackExclusion] = []
    for record in access_authorized:
        reason = _eligibility_exclusion_reason(record, request)
        if reason is None or _relevance_score(record, request.query, query_terms) <= 0:
            continue
        exclusions.append(_pack_exclusion(record, reason))

    matched_candidates: list[tuple[int, datetime, str, ContextRecord]] = []
    for record in active:
        score = _relevance_score(record, request.query, query_terms)
        if score <= 0:
            continue
        verified_at = record.verified_at or datetime.min.replace(tzinfo=timezone.utc)
        matched_candidates.append(
            (score, verified_at.astimezone(timezone.utc), record.record_id, record)
        )

    lifecycle_active: list[tuple[int, datetime, str, ContextRecord]] = []
    for candidate in matched_candidates:
        record = candidate[3]
        reason = _lifecycle_exclusion_reason(record, lifecycle_snapshot)
        if reason is None:
            lifecycle_active.append(candidate)
        else:
            exclusions.append(_pack_exclusion(record, reason))

    contradictions, contradictory_ids = _find_contradictions(
        candidate[3] for candidate in lifecycle_active
    )
    if contradictory_ids:
        for _score, _verified_at, _record_id, record in lifecycle_active:
            if record.record_id in contradictory_ids:
                exclusions.append(_pack_exclusion(record, "active_contradiction"))
        lifecycle_active = [
            candidate
            for candidate in lifecycle_active
            if candidate[3].record_id not in contradictory_ids
        ]

    ranked = lifecycle_active
    ranked.sort(key=lambda item: (-item[0], -_datetime_rank(item[1]), item[2]))

    selected: list[ContextPackItem] = []
    total_tokens = 0
    for score, _verified_at, _record_id, record in ranked:
        estimated = estimate_record_tokens(record)
        if total_tokens + estimated > request.token_budget:
            continue
        selected.append(
            ContextPackItem(
                record=record,
                relevance_score=score,
                estimated_tokens=estimated,
            )
        )
        total_tokens += estimated
        if len(selected) >= request.max_items:
            break

    exclusions.sort(key=lambda item: (item.reason, item.record_id))
    outcome = (
        "requires_resolution"
        if contradictions
        else "partial"
        if exclusions
        else "complete"
    )
    manifest = _pack_manifest(
        request,
        selected,
        total_tokens,
        lifecycle_snapshot=lifecycle_snapshot,
        outcome=outcome,
        exclusions=exclusions,
        contradictions=contradictions,
    )
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ContextPack(
        pack_id=f"ctxpack_{checksum[:24]}",
        manifest_sha256=checksum,
        request=request,
        items=tuple(selected),
        estimated_tokens=total_tokens,
        eligible_records=len(active),
        matched_records=len(ranked),
        outcome=outcome,
        lifecycle_snapshot_sha256=(
            lifecycle_snapshot.snapshot_sha256 if lifecycle_snapshot is not None else None
        ),
        repository_revision=(
            lifecycle_snapshot.repository_revision if lifecycle_snapshot is not None else None
        ),
        exclusions=tuple(exclusions),
        contradictions=contradictions,
    )


def estimate_record_tokens(record: ContextRecord) -> int:
    # Budget the complete content-bearing item that the API emits, not only
    # the prose body. The fixed-width sentinels make this an upper bound for
    # the two computed integer fields without creating a circular estimate.
    rendered = json.dumps(
        _context_pack_item_dict(
            record,
            relevance_score=_TOKEN_ESTIMATE_INTEGER_SENTINEL,
            estimated_tokens=_TOKEN_ESTIMATE_INTEGER_SENTINEL,
        ),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return max(1, math.ceil(len(rendered.encode("utf-8")) / 3))


def _is_access_authorized(record: ContextRecord, request: ContextPackRequest) -> bool:
    principal = request.principal
    if record.organization_id != principal.organization_id:
        return False
    expected_scope = {
        "organization": principal.organization_id,
        "team": principal.team_id,
        "actor": principal.actor_id,
    }[record.visibility]
    if record.scope_id != expected_scope:
        return False
    if _CLASSIFICATION_RANK[record.classification] > _CLASSIFICATION_RANK[principal.clearance]:
        return False
    if record.repository_id is not None and record.repository_id != principal.repository_id:
        return False
    if record.branch is not None and record.branch != principal.branch:
        return False
    return True


def _eligibility_exclusion_reason(
    record: ContextRecord,
    request: ContextPackRequest,
) -> str | None:
    if record.verification != "verified" and not request.include_provisional:
        return "provisional_not_allowed"
    as_of = request.as_of.astimezone(timezone.utc)
    if record.effective_at is not None and record.effective_at.astimezone(timezone.utc) > as_of:
        return "not_yet_effective"
    if record.verified_at is not None and record.verified_at.astimezone(timezone.utc) > as_of:
        return "verification_in_future"
    if record.expires_at is not None and record.expires_at.astimezone(timezone.utc) <= as_of:
        return "expired"
    return None


def _lifecycle_exclusion_reason(
    record: ContextRecord,
    snapshot: ContextLifecycleSnapshot | None,
) -> str | None:
    indicator = _prompt_injection_indicator(record)
    if indicator is not None:
        return f"quarantined_prompt_injection:{indicator}"
    if (
        snapshot is not None
        and "source_revision_changed" in record.invalidation_rules
        and record.source_revision.startswith("git:")
        and not _revisions_match(record.source_revision, snapshot.repository_revision)
    ):
        return "source_revision_changed"
    if not record.dependencies:
        return None
    observations = {
        artifact.uri: artifact for artifact in (() if snapshot is None else snapshot.artifacts)
    }
    for dependency in sorted(record.dependencies):
        observed = observations.get(dependency.uri)
        if observed is None:
            return "dependency_observation_missing"
        if not _revisions_match(dependency.revision, observed.revision):
            return "dependency_revision_mismatch"
        if dependency.sha256 and dependency.sha256 != observed.sha256:
            return "dependency_hash_mismatch"
    return None


def _prompt_injection_indicator(record: ContextRecord) -> str | None:
    model_visible_values = (
        record.title,
        record.content,
        record.owner_id,
        record.scope_id,
        record.source_uri,
        record.source_revision,
        record.source_item_key,
        record.repository_id or "",
        record.branch or "",
        *record.verification_evidence,
        *record.invalidation_rules,
        *(item.uri for item in record.dependencies),
        *(item.revision for item in record.dependencies),
        record.assertion_key or "",
        record.assertion_value or "",
        *record.tags,
    )
    content = "\n".join(model_visible_values)
    for indicator, pattern in _PROMPT_INJECTION_INDICATORS:
        if pattern.search(content):
            return indicator
    return None


def _revisions_match(expected: str, observed: str) -> bool:
    def normalized(value: str) -> str:
        return value[4:] if value.startswith("git:") else value

    return normalized(expected) == normalized(observed)


def _pack_exclusion(record: ContextRecord, reason: str) -> ContextPackExclusion:
    return ContextPackExclusion(
        record_id=record.record_id,
        reason=reason,
        source_uri=record.source_uri,
        source_revision=record.source_revision,
        source_sha256=record.source_sha256,
    )


def _find_contradictions(
    records: Iterable[ContextRecord],
) -> tuple[tuple[ContextContradiction, ...], frozenset[str]]:
    grouped: dict[str, list[ContextRecord]] = {}
    for record in records:
        if record.assertion_key is not None:
            grouped.setdefault(record.assertion_key, []).append(record)
    contradictions: list[ContextContradiction] = []
    record_ids: set[str] = set()
    for assertion_key, candidates in grouped.items():
        values = {record.assertion_value for record in candidates}
        if len(values) <= 1:
            continue
        ordered = sorted(candidates, key=lambda record: record.record_id)
        record_ids.update(record.record_id for record in ordered)
        contradictions.append(
            ContextContradiction(
                assertion_key=assertion_key,
                sources=tuple(
                    ContextContradictionSource(
                        record_id=record.record_id,
                        assertion_value=record.assertion_value or "",
                        source_uri=record.source_uri,
                        source_revision=record.source_revision,
                        source_sha256=record.source_sha256,
                    )
                    for record in ordered
                ),
            )
        )
    contradictions.sort(key=lambda item: item.assertion_key)
    return tuple(contradictions), frozenset(record_ids)


def _relevance_score(record: ContextRecord, query: str, query_terms: set[str]) -> int:
    title_terms = _terms(record.title)
    tag_terms = _terms(" ".join(record.tags))
    content_terms = _terms(record.content)
    all_terms = title_terms | tag_terms | content_terms
    overlap = query_terms & all_terms
    if not overlap:
        return 0
    coverage = len(overlap) * 10_000 // len(query_terms)
    title_hits = len(query_terms & title_terms)
    tag_hits = len(query_terms & tag_terms)
    content_hits = len(query_terms & content_terms)
    phrase_bonus = 1_000 if query.casefold().strip() in f"{record.title}\n{record.content}".casefold() else 0
    verification_bonus = 10 if record.verification == "verified" else 0
    return coverage + title_hits * 200 + tag_hits * 150 + content_hits * 50 + phrase_bonus + verification_bonus


def _terms(value: str) -> set[str]:
    return {
        term
        for match in _TERM_PATTERN.finditer(value)
        if len(term := match.group(0).casefold()) > 1 and term not in _RETRIEVAL_STOP_WORDS
    }


def _validate_supersession_graph(records: dict[str, ContextRecord]) -> None:
    for start_id in records:
        seen: set[str] = set()
        current_id: str | None = start_id
        while current_id is not None and current_id in records:
            if current_id in seen:
                raise ContextError(f"context supersession cycle includes: {current_id}")
            seen.add(current_id)
            current_id = records[current_id].supersedes_id


def _pack_manifest(
    request: ContextPackRequest,
    selected: list[ContextPackItem],
    total_tokens: int,
    *,
    lifecycle_snapshot: ContextLifecycleSnapshot | None,
    outcome: str,
    exclusions: list[ContextPackExclusion],
    contradictions: tuple[ContextContradiction, ...],
) -> dict[str, Any]:
    principal = request.principal
    return {
        "schema_version": CONTEXT_PACK_SCHEMA,
        "query": request.query,
        "policy_version": request.policy_version,
        "retrieval_version": CONTEXT_RETRIEVAL_VERSION,
        "render_version": CONTEXT_RENDER_VERSION,
        "scope": {
            "organization_id": principal.organization_id,
            "team_id": principal.team_id,
            "actor_id": principal.actor_id,
            "clearance": principal.clearance,
            "repository_id": principal.repository_id,
            "branch": principal.branch,
        },
        "token_budget": request.token_budget,
        "max_items": request.max_items,
        "include_provisional": request.include_provisional,
        "estimated_tokens": total_tokens,
        "lifecycle": {
            "version": "v1",
            "outcome": outcome,
            "snapshot_sha256": (
                lifecycle_snapshot.snapshot_sha256
                if lifecycle_snapshot is not None
                else None
            ),
            "repository_revision": (
                lifecycle_snapshot.repository_revision
                if lifecycle_snapshot is not None
                else None
            ),
            "exclusions": [item.to_dict() for item in exclusions],
            "contradictions": [item.to_dict() for item in contradictions],
        },
        "items": [
            {
                "id": item.record.record_id,
                "title": item.record.title,
                "kind": item.record.record_kind,
                "owner_id": item.record.owner_id,
                "organization_id": item.record.organization_id,
                "visibility": item.record.visibility,
                "scope_id": item.record.scope_id,
                "classification": item.record.classification,
                "source_uri": item.record.source_uri,
                "source_revision": item.record.source_revision,
                "source_sha256": item.record.source_sha256,
                "source_item_key": item.record.source_item_key,
                "repository_id": item.record.repository_id,
                "branch": item.record.branch,
                "verification": item.record.verification,
                "verification_evidence": list(item.record.verification_evidence),
                "effective_at": _isoformat(item.record.effective_at),
                "verified_at": _isoformat(item.record.verified_at),
                "expires_at": _isoformat(item.record.expires_at),
                "supersedes_id": item.record.supersedes_id,
                "invalidation_rules": list(item.record.invalidation_rules),
                "dependencies": [
                    dependency.to_dict()
                    for dependency in sorted(item.record.dependencies)
                ],
                "assertion": (
                    {
                        "key": item.record.assertion_key,
                        "value": item.record.assertion_value,
                    }
                    if item.record.assertion_key is not None
                    else None
                ),
                "tags": list(item.record.tags),
                "content_sha256": item.record.content_sha256,
                "relevance_score": item.relevance_score,
                "estimated_tokens": item.estimated_tokens,
            }
            for item in selected
        ],
    }


def _required_string(value: dict[str, Any], name: str, *, prefix: str = "") -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item.strip():
        raise ContextError(f"context record {prefix}{name} must be a non-empty string")
    return item


def _optional_string(value: dict[str, Any], name: str, default: str) -> str:
    item = value.get(name, default)
    if not isinstance(item, str) or not item.strip():
        raise ContextError(f"context record {name} must be a non-empty string")
    return item


def _optional_empty_string(value: dict[str, Any], name: str) -> str:
    item = value.get(name, "")
    if not isinstance(item, str):
        raise ContextError(f"context record {name} must be a string")
    return item


def _nullable_string(value: dict[str, Any], name: str) -> str | None:
    item = value.get(name)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContextError(f"context record {name} must be null or a non-empty string")
    return item


def _nullable_datetime(value: dict[str, Any], name: str) -> datetime | None:
    item = value.get(name)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ContextError(f"context record {name} must be null or an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContextError(f"context record {name} must be a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ContextError(f"context record {name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _datetime_rank(value: datetime) -> int:
    utc = value.astimezone(timezone.utc)
    return (
        utc.toordinal() * 86_400_000_000
        + utc.hour * 3_600_000_000
        + utc.minute * 60_000_000
        + utc.second * 1_000_000
        + utc.microsecond
    )
