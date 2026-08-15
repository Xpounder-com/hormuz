from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


CONTEXT_PACK_SCHEMA = "hormuz.context-pack.v1"
CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
VISIBILITIES = ("organization", "team", "actor")
VERIFICATION_STATES = ("provisional", "verified")
RECORD_KINDS = ("claim", "decision")
_CLASSIFICATION_RANK = {name: index for index, name in enumerate(CLASSIFICATIONS)}
_TERM_PATTERN = re.compile(r"[^\W_]+", re.UNICODE)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ContextError(ValueError):
    pass


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
        record = self.record
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
            "tags": list(record.tags),
            "source": {
                "uri": record.source_uri,
                "revision": record.source_revision,
                "sha256": record.source_sha256,
                "item_key": record.source_item_key,
            },
            "content_sha256": record.content_sha256,
            "relevance_score": self.relevance_score,
            "estimated_tokens": self.estimated_tokens,
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

    def to_dict(self) -> dict[str, Any]:
        principal = self.request.principal
        return {
            "schema_version": CONTEXT_PACK_SCHEMA,
            "pack_id": self.pack_id,
            "manifest_sha256": self.manifest_sha256,
            "query": self.request.query,
            "policy_version": self.request.policy_version,
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
            "items": [item.to_dict() for item in self.items],
        }


def build_context_pack(
    records: Iterable[ContextRecord],
    request: ContextPackRequest,
) -> ContextPack:
    by_id: dict[str, ContextRecord] = {}
    for record in records:
        if not isinstance(record, ContextRecord):
            raise ContextError("context records must be ContextRecord instances")
        if record.record_id in by_id:
            raise ContextError(f"duplicate context record id: {record.record_id}")
        by_id[record.record_id] = record
    _validate_supersession_graph(by_id)

    active = [record for record in by_id.values() if _is_authorized(record, request)]
    superseded_ids = {
        record.supersedes_id
        for record in active
        if record.supersedes_id is not None and record.supersedes_id in by_id
    }
    active = [record for record in active if record.record_id not in superseded_ids]

    query_terms = _terms(request.query)
    ranked: list[tuple[int, datetime, str, ContextRecord]] = []
    for record in active:
        score = _relevance_score(record, request.query, query_terms)
        if score <= 0:
            continue
        verified_at = record.verified_at or datetime.min.replace(tzinfo=timezone.utc)
        ranked.append((score, verified_at.astimezone(timezone.utc), record.record_id, record))
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

    manifest = _pack_manifest(request, selected, total_tokens)
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
    )


def estimate_record_tokens(record: ContextRecord) -> int:
    rendered = (
        f"[context id={record.record_id} title={record.title} "
        f"source={record.source_uri} revision={record.source_revision} "
        f"classification={record.classification}]\n{record.content}\n"
    )
    return max(1, math.ceil(len(rendered.encode("utf-8")) / 3))


def _is_authorized(record: ContextRecord, request: ContextPackRequest) -> bool:
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
    if record.verification != "verified" and not request.include_provisional:
        return False
    as_of = request.as_of.astimezone(timezone.utc)
    if record.effective_at is not None and record.effective_at.astimezone(timezone.utc) > as_of:
        return False
    if record.verified_at is not None and record.verified_at.astimezone(timezone.utc) > as_of:
        return False
    if record.expires_at is not None and record.expires_at.astimezone(timezone.utc) <= as_of:
        return False
    return True


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
    return {match.group(0).casefold() for match in _TERM_PATTERN.finditer(value)}


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
) -> dict[str, Any]:
    principal = request.principal
    return {
        "schema_version": CONTEXT_PACK_SCHEMA,
        "query": request.query,
        "policy_version": request.policy_version,
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
