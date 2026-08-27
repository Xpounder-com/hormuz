"""Semantic policy comparison and read-only request preview primitives."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from .config import GatewayConfig, Identity
from .policy import PolicyDecision, PolicyEngine
from .policy_document import PolicyDocument
from .store import MonthlyTotals, UsageRepository


_SIMPLE_PATH_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SET_LIKE_POLICY_FIELDS = frozenset({"allowed_clients", "allowed_models"})
_MISSING = object()


class PolicyAnalysisError(RuntimeError):
    """Stable failure for policy comparison or read-only preview."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PolicyVersionIdentity:
    version_id: str
    content_sha256: str

    @classmethod
    def from_document(cls, document: PolicyDocument) -> "PolicyVersionIdentity":
        return cls(version_id=document.version_id, content_sha256=document.content_sha256)


@dataclass(frozen=True)
class PolicyChange:
    path: str
    change_type: str
    before: object | None
    after: object | None


@dataclass(frozen=True)
class PolicyComparison:
    organization_id: str
    baseline: PolicyVersionIdentity
    candidate: PolicyVersionIdentity
    changes: tuple[PolicyChange, ...]

    @property
    def identical(self) -> bool:
        return not self.changes


@dataclass(frozen=True)
class UsagePeriod:
    starts_at: datetime
    ends_before: datetime


@dataclass(frozen=True)
class PolicyPreview:
    organization_id: str
    evaluated_at: datetime
    usage_period: UsagePeriod
    usage_basis: str
    identity: Identity
    client: str
    protocol: str
    requested_model: str
    requested_output_tokens: int | None
    baseline: PolicyVersionIdentity
    baseline_decision: PolicyDecision
    candidate: PolicyVersionIdentity
    candidate_decision: PolicyDecision


def compare_policy_documents(
    baseline: PolicyDocument,
    candidate: PolicyDocument,
) -> PolicyComparison:
    """Compare normalized policy meaning while retaining content identities."""

    if baseline.organization_id != candidate.organization_id:
        raise PolicyAnalysisError("policy_comparison_organization_mismatch")
    baseline_mapping = _semantic_policy_mapping(baseline)
    candidate_mapping = _semantic_policy_mapping(candidate)
    changes: list[PolicyChange] = []
    _compare_values(baseline_mapping, candidate_mapping, (), changes)
    return PolicyComparison(
        organization_id=baseline.organization_id,
        baseline=PolicyVersionIdentity.from_document(baseline),
        candidate=PolicyVersionIdentity.from_document(candidate),
        changes=tuple(changes),
    )


def preview_policy_request(
    *,
    config: GatewayConfig,
    usage_store: UsageRepository,
    identity: Identity,
    baseline: PolicyDocument,
    candidate: PolicyDocument,
    client: str,
    protocol: str,
    requested_model: str,
    requested_output_tokens: int | None,
    evaluated_at: datetime | None = None,
) -> PolicyPreview:
    """Evaluate one request against two pinned documents and one usage snapshot."""

    if baseline.organization_id != candidate.organization_id or baseline.organization_id != identity.organization_id:
        raise PolicyAnalysisError("policy_preview_organization_mismatch")
    if client not in {"codex", "claude-code"}:
        raise PolicyAnalysisError("policy_preview_client_invalid")
    if protocol not in {"openai", "anthropic"}:
        raise PolicyAnalysisError("policy_preview_protocol_invalid")
    if (
        not isinstance(requested_model, str)
        or not requested_model
        or any(character in requested_model for character in ("\x00", "\n", "\r"))
    ):
        raise PolicyAnalysisError("policy_preview_model_invalid")
    if requested_output_tokens is not None and (
        isinstance(requested_output_tokens, bool)
        or not isinstance(requested_output_tokens, int)
        or requested_output_tokens < 1
    ):
        raise PolicyAnalysisError("policy_preview_output_tokens_invalid")
    current = evaluated_at or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise PolicyAnalysisError("policy_preview_time_invalid")
    current = current.astimezone(timezone.utc)
    usage_period = _utc_month_period(current)
    current_usage = _CurrentUsageSnapshot.capture(
        usage_store,
        identity=identity,
        usage_period=usage_period,
    )
    engine = PolicyEngine(config, cast(UsageRepository, current_usage))
    baseline_decision = engine.evaluate(
        identity=identity,
        client=client,
        protocol=protocol,
        requested_model=requested_model,
        requested_output_tokens=requested_output_tokens,
        snapshot=baseline.snapshot_for(identity),
    )
    candidate_decision = engine.evaluate(
        identity=identity,
        client=client,
        protocol=protocol,
        requested_model=requested_model,
        requested_output_tokens=requested_output_tokens,
        snapshot=candidate.snapshot_for(identity),
    )
    return PolicyPreview(
        organization_id=identity.organization_id,
        evaluated_at=current,
        usage_period=usage_period,
        usage_basis="current",
        identity=identity,
        client=client,
        protocol=protocol,
        requested_model=requested_model,
        requested_output_tokens=requested_output_tokens,
        baseline=PolicyVersionIdentity.from_document(baseline),
        baseline_decision=baseline_decision,
        candidate=PolicyVersionIdentity.from_document(candidate),
        candidate_decision=candidate_decision,
    )


class _CurrentUsageSnapshot:
    """The three current monthly totals read once and reused by both decisions."""

    def __init__(self, values: Mapping[tuple[str | None, str | None, str], MonthlyTotals]) -> None:
        self._values = dict(values)

    @classmethod
    def capture(
        cls,
        source: UsageRepository,
        *,
        identity: Identity,
        usage_period: UsagePeriod,
    ) -> "_CurrentUsageSnapshot":
        organization_id = identity.organization_id
        keys = (
            (identity.actor_id, None, organization_id),
            (None, None, organization_id),
            (None, identity.team_id, organization_id),
        )
        values = {
            key: source.monthly_totals(
                actor_id=key[0],
                team_id=key[1],
                organization_id=key[2],
                starts_at=usage_period.starts_at,
                ends_before=usage_period.ends_before,
            )
            for key in keys
        }
        return cls(values)

    def monthly_totals(
        self,
        *,
        actor_id: str | None = None,
        team_id: str | None = None,
        organization_id: str | None = None,
    ) -> MonthlyTotals:
        if organization_id is None:
            raise PolicyAnalysisError("policy_preview_usage_scope_invalid")
        try:
            return self._values[(actor_id, team_id, organization_id)]
        except KeyError as error:
            raise PolicyAnalysisError("policy_preview_usage_scope_invalid") from error


def _semantic_policy_mapping(document: PolicyDocument) -> dict[str, object]:
    mapping = document.to_mapping()
    return cast(
        dict[str, object],
        _normalize_value(
            {
                "policies": mapping["policies"],
                "egress_controls": mapping["egress_controls"],
            },
            (),
        ),
    )


def _normalize_value(value: object, path: tuple[str, ...]) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_value(item, (*path, str(key)))
            for key, item in sorted(value.items(), key=lambda entry: str(entry[0]))
        }
    if isinstance(value, list):
        normalized = [_normalize_value(item, (*path, str(index))) for index, item in enumerate(value)]
        if path and path[-1] in _SET_LIKE_POLICY_FIELDS:
            return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")))
        return normalized
    return value


def _compare_values(
    before: object,
    after: object,
    path: tuple[str, ...],
    changes: list[PolicyChange],
) -> None:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after)):
            before_value = before.get(key, _MISSING)
            after_value = after.get(key, _MISSING)
            child_path = (*path, str(key))
            if before_value is _MISSING:
                _append_subtree_change("added", None, after_value, child_path, changes)
            elif after_value is _MISSING:
                _append_subtree_change("removed", before_value, None, child_path, changes)
            else:
                _compare_values(before_value, after_value, child_path, changes)
        return
    if before != after:
        changes.append(
            PolicyChange(
                path=_render_policy_path(path),
                change_type="changed",
                before=before,
                after=after,
            )
        )


def _append_subtree_change(
    change_type: str,
    before: object | None,
    after: object | None,
    path: tuple[str, ...],
    changes: list[PolicyChange],
) -> None:
    value = after if change_type == "added" else before
    if isinstance(value, Mapping) and value:
        for key in sorted(value):
            child = value[key]
            _append_subtree_change(
                change_type,
                None if change_type == "added" else child,
                child if change_type == "added" else None,
                (*path, str(key)),
                changes,
            )
        return
    changes.append(
        PolicyChange(
            path=_render_policy_path(path),
            change_type=change_type,
            before=before,
            after=after,
        )
    )


def _render_policy_path(path: tuple[str, ...]) -> str:
    if not path:
        raise PolicyAnalysisError("policy_comparison_path_invalid")
    result = path[0]
    for segment in path[1:]:
        if _SIMPLE_PATH_SEGMENT.fullmatch(segment):
            result += f".{segment}"
        else:
            result += f"[{json.dumps(segment, ensure_ascii=True)}]"
    return result


def _utc_month_period(value: datetime) -> UsagePeriod:
    starts_at = value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if starts_at.month == 12:
        ends_before = starts_at.replace(year=starts_at.year + 1, month=1)
    else:
        ends_before = starts_at.replace(month=starts_at.month + 1)
    return UsagePeriod(starts_at=starts_at, ends_before=ends_before)
