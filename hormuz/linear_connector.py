"""Metadata-only Linear lifecycle normalization and signed-channel dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
import threading
import time
from typing import Mapping

from .linear_webhook_auth import LinearVerifiedDelivery, LinearWebhookAuthenticator
from .outcome_connector_config import LinearOutcomeChannelConfig
from .outcome_ingest import registered_binding
from .outcome_wire import timestamp
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError, canonical


NORMALIZER_RULES = {
    "schema_id": "hormuz.linear-webhook-normalizer",
    "schema_version": 1,
    "actions": ["create", "remove", "update"],
    "entities": ["cycle", "initiative", "issue", "project"],
    "revision": "source_updated_at_v1",
    "content": "opaque_ids_and_timestamps_only",
}
NORMALIZER_DIGEST = hashlib.sha256(canonical(NORMALIZER_RULES).encode("ascii")).hexdigest()
_INGEST_SLOTS = threading.BoundedSemaphore(8)
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


@dataclass(frozen=True)
class LinearProjection:
    context: Mapping[str, object]
    outcome: Mapping[str, object] | None
    source_fact: Mapping[str, object]


def _uuid(value: object) -> str:
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        raise PortfolioError("invalid_request")
    return value


def _optional_timestamp(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortfolioError("invalid_request")
    return timestamp(value)


def _event_clock(value: str) -> tuple[str, str]:
    instant = timestamp(value)
    parsed = datetime.fromisoformat(instant)
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    order = delta.days * 86400000000 + delta.seconds * 1000000 + delta.microseconds
    if not 0 <= order <= 9223372036854775807:
        raise PortfolioError("invalid_request")
    return instant, str(order)


def _linked_id(data: dict, scalar: str, child: str) -> tuple[bool, str | None]:
    present = scalar in data or child in data
    scalar_value = data.get(scalar)
    child_value = data.get(child)
    if scalar_value is not None:
        scalar_value = _uuid(scalar_value)
    if child_value is not None:
        if not isinstance(child_value, dict):
            raise PortfolioError("invalid_request")
        child_value = _uuid(child_value.get("id"))
    if scalar_value is not None and child_value is not None and scalar_value != child_value:
        raise PortfolioError("invalid_request")
    return present, scalar_value if scalar_value is not None else child_value


class LinearOutcomeAdapter:
    def __init__(self, channel: LinearOutcomeChannelConfig) -> None:
        self.channel = channel
        self._typed = {
            kind: frozenset(values) for kind, values in channel.typed_enrollment.items()
        }

    def normalize(
        self,
        *,
        binding: PortfolioConnectorBinding,
        verified: LinearVerifiedDelivery,
        body: dict,
    ) -> LinearProjection:
        if (
            verified.object_kind not in self._typed
            or verified.object_id not in self._typed[verified.object_kind]
            or body.get("action") != verified.action
        ):
            raise PortfolioError("forbidden")
        data = body.get("data")
        if not isinstance(data, dict):
            raise PortfolioError("invalid_request")
        updated_from = body.get("updatedFrom", {})
        if not isinstance(updated_from, dict):
            raise PortfolioError("invalid_request")

        event_at = _optional_timestamp(body.get("createdAt"))
        revision_value = _optional_timestamp(data.get("updatedAt"))
        if revision_value is None:
            revision = {"kind": "unknown", "value": None}
            revision_order = None
        else:
            revision_value, revision_order = _event_clock(revision_value)
            revision = {"kind": "source_updated_at_v1", "value": revision_value}

        lifecycle = self._lifecycle(verified.action, data, updated_from)
        normalized_state = self._state(data)
        relationships, coverage, project_id = self._relationships(
            verified.object_kind,
            data,
        )
        context = {
            "object": {"kind": verified.object_kind, "id": verified.object_id},
            "source_team_ids": (
                list(verified.source_team_ids)
                if verified.source_team_ids is not None
                else None
            ),
            "lifecycle": lifecycle,
            "normalized_state": normalized_state,
            "relationships": relationships,
            "relationship_coverage": coverage,
            "revision": revision,
            "event_at": event_at,
        }
        source_fact = {
            "workspace_id": verified.workspace_id,
            "webhook_id": verified.source_webhook_id,
            "object": context["object"],
            "action": verified.action,
            "source_team_ids": context["source_team_ids"],
            "lifecycle": lifecycle,
            "normalized_state": normalized_state,
            "relationships": relationships,
            "relationship_coverage": coverage,
            "revision": revision,
            "event_at": event_at,
        }
        outcome = None
        if verified.object_kind == "issue" and project_id in binding.external_object_ids:
            event_type = self._outcome_event(verified.action, lifecycle, normalized_state)
            if event_type is not None:
                state = "tombstoned" if event_type == "deleted" else "observed"
                outcome = {
                    "schema_id": "hormuz.source-outcome-observation",
                    "schema_version": 1,
                    "source_event_id": None,
                    "external_object_id": verified.object_id,
                    "container_id": project_id,
                    "source_revision": revision_value,
                    "ordering_domain": (
                        "source_updated_at_v1" if revision_value is not None else None
                    ),
                    "revision_order": revision_order,
                    "object_type": "issue",
                    "event_type": event_type,
                    "quality_state": "unknown",
                    "duration_ms": None,
                    "state": state,
                    "supersedes_source_event_id": None,
                    "reason_code": "tombstoned" if state == "tombstoned" else "observed",
                    "event_at": event_at,
                }
        return LinearProjection(context, outcome, source_fact)

    @staticmethod
    def _lifecycle(action: str, data: dict, updated_from: dict) -> str:
        if action == "create":
            return "created"
        if action == "remove":
            return "deleted"
        current_present = "archivedAt" in data
        previous_present = "archivedAt" in updated_from
        current = _optional_timestamp(data.get("archivedAt")) if current_present else None
        previous = _optional_timestamp(updated_from.get("archivedAt")) if previous_present else None
        if current_present and previous_present:
            if previous is None and current is not None:
                return "archived"
            if previous is not None and current is None:
                return "restored"
        return "updated"

    @staticmethod
    def _state(data: dict) -> str:
        completed = _optional_timestamp(data.get("completedAt"))
        canceled = _optional_timestamp(data.get("canceledAt"))
        started = _optional_timestamp(data.get("startedAt"))
        if completed is not None and canceled is not None:
            raise PortfolioError("invalid_request")
        if completed is not None:
            return "completed"
        if canceled is not None:
            return "canceled"
        if started is not None:
            return "in_progress"
        child = data.get("state") if "state" in data else data.get("status")
        if child is None:
            return "unknown"
        if not isinstance(child, dict) or not isinstance(child.get("type"), str):
            raise PortfolioError("invalid_request")
        return {
            "backlog": "not_started",
            "unstarted": "not_started",
            "planned": "not_started",
            "started": "in_progress",
            "completed": "completed",
            "canceled": "canceled",
            "cancelled": "canceled",
        }.get(child["type"], "unknown")

    def _relationships(
        self,
        kind: str,
        data: dict,
    ) -> tuple[list[dict[str, object]], str, str | None]:
        object_id = _uuid(data.get("id"))
        candidates: list[tuple[str, str, str]] = []
        present = False
        unrepresented = False
        project_id = None
        if kind == "issue":
            project_present, project_id = _linked_id(data, "projectId", "project")
            cycle_present, cycle_id = _linked_id(data, "cycleId", "cycle")
            present = project_present or cycle_present or "parentId" in data
            if project_id is not None:
                candidates.append(("project_issue", "project", project_id))
            if cycle_id is not None:
                candidates.append(("cycle_issue", "cycle", cycle_id))
            if data.get("parentId") is not None:
                _uuid(data.get("parentId"))
                unrepresented = True
        elif kind == "project":
            if "initiatives" in data:
                present = True
                values = data["initiatives"]
                if not isinstance(values, list) or len(values) > 100:
                    raise PortfolioError("invalid_request")
                for value in values:
                    if not isinstance(value, dict):
                        raise PortfolioError("invalid_request")
                    candidates.append(("initiative_project", "initiative", _uuid(value.get("id"))))
        elif kind == "initiative":
            parent_present, parent_id = _linked_id(
                data,
                "parentInitiativeId",
                "parentInitiative",
            )
            present = parent_present
            if parent_id is not None:
                candidates.append(("initiative_parent", "initiative", parent_id))
            if "parentInitiatives" in data:
                present = True
                values = data["parentInitiatives"]
                if not isinstance(values, list) or len(values) > 100:
                    raise PortfolioError("invalid_request")
                for value in values:
                    if not isinstance(value, dict):
                        raise PortfolioError("invalid_request")
                    candidates.append(("initiative_parent", "initiative", _uuid(value.get("id"))))
        else:
            return [], "not_applicable", None

        if len({(relationship_kind, parent_id) for relationship_kind, _, parent_id in candidates}) > 100:
            raise PortfolioError("invalid_request")
        relationships: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        excluded = unrepresented
        for relationship_kind, parent_kind, parent_id in candidates:
            if parent_id == object_id:
                raise PortfolioError("invalid_request")
            key = (relationship_kind, parent_id)
            if key in seen:
                continue
            seen.add(key)
            if parent_id not in self._typed[parent_kind]:
                excluded = True
                continue
            relationships.append({
                "kind": relationship_kind,
                "parent": {"kind": parent_kind, "id": parent_id},
            })
        if len(relationships) > 100:
            raise PortfolioError("invalid_request")
        relationships.sort(key=lambda item: (item["kind"], item["parent"]["id"]))
        if not present:
            coverage = "unknown"
        elif excluded:
            coverage = "partial"
        elif not candidates:
            coverage = "not_applicable"
        else:
            coverage = "complete"
        return relationships, coverage, project_id

    @staticmethod
    def _outcome_event(action: str, lifecycle: str, state: str) -> str | None:
        if action == "create":
            return "created"
        if action == "remove":
            return "deleted"
        if lifecycle == "restored":
            return "reopened"
        return {
            "in_progress": "started",
            "completed": "completed",
            "canceled": "canceled",
        }.get(state)


class LinearOutcomeReceiver:
    """Select and process one server-enrolled Linear channel."""

    def __init__(self, config, repository) -> None:
        runtime = config.outcome_connectors
        if runtime is None or not runtime.linear:
            raise PortfolioError("forbidden")
        channels = []
        seen_material: set[bytes] = set()
        for channel in runtime.linear:
            authenticator = LinearWebhookAuthenticator(config, channel)
            material = [value[0] for value in authenticator.secrets.values()]
            material.extend(
                reference.value for reference in channel.identity_keys
                if reference.value is not None
            )
            if len(material) != len(set(material)) or seen_material.intersection(material):
                raise PortfolioError("invalid_request")
            seen_material.update(material)
            channels.append((
                channel,
                authenticator,
                LinearOutcomeAdapter(channel),
                authenticator.keys,
            ))
        self.config = config
        self.repository = repository
        self._channels = tuple(channels)

    def ingest(
        self,
        headers: Mapping[str, str],
        raw: bytes,
        *,
        deadline: float | None = None,
        now_ms: int | None = None,
    ) -> dict:
        deadline = time.monotonic() + 4 if deadline is None else deadline
        if not _INGEST_SLOTS.acquire(blocking=False):
            raise PortfolioError("rate_limited")
        try:
            failure: PortfolioError | None = None
            for channel, authenticator, adapter, keys in self._channels:
                if time.monotonic() >= deadline:
                    raise PortfolioError("unavailable")
                try:
                    verified, body = authenticator.authenticate(headers, raw, now_ms=now_ms)
                except PortfolioError as error:
                    if error.code in {"unauthenticated", "forbidden"}:
                        failure = error
                        continue
                    raise
                binding = registered_binding(
                    self.config,
                    channel.organization_id,
                    channel.connector_id,
                )
                prior = self.repository.replay_body(
                    channel=channel,
                    binding=binding,
                    verified=verified,
                    raw=raw,
                    keys=keys,
                    deadline=deadline,
                )
                if prior is not None:
                    return prior
                if not verified.fresh:
                    raise PortfolioError("unauthenticated")
                projection = adapter.normalize(
                    binding=binding,
                    verified=verified,
                    body=body,
                )
                return self.repository.accept(
                    channel=channel,
                    binding=binding,
                    verified=verified,
                    raw=raw,
                    keys=keys,
                    projection=projection,
                    deadline=deadline,
                )
            if failure is not None:
                raise failure
            raise PortfolioError("unauthenticated")
        finally:
            _INGEST_SLOTS.release()
