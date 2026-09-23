"""Production GitHub.com outcome normalization and signed-channel dispatch."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from .github_webhook_auth import GitHubWebhookAuthenticator
from .outcome_ingest import AuthenticatedDelivery, OutcomeIngestor
from .outcome_wire import timestamp
from .portfolio_config import PortfolioConnectorBinding
from .portfolio_wire import PortfolioError


def _numeric_id(value: object) -> str:
    if type(value) is not int or not 1 <= value <= 99999999999999999999:
        raise PortfolioError("invalid_request")
    return str(value)


def _event_clock(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise PortfolioError("invalid_request")
    instant = timestamp(value)
    parsed = datetime.fromisoformat(instant)
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    order = delta.days * 86400000000 + delta.seconds * 1000000 + delta.microseconds
    if not 0 <= order <= 9223372036854775807:
        raise PortfolioError("invalid_request")
    return instant, str(order)


def _observation(
    verified: AuthenticatedDelivery,
    *,
    external_object_id: str,
    container_id: str,
    event_type: str,
    quality_state: str,
    event_at: object,
) -> dict:
    source_time, order = _event_clock(event_at)
    return {
        "schema_id": "hormuz.source-outcome-observation",
        "schema_version": 1,
        "source_event_id": verified.source_delivery_id + ":0",
        "external_object_id": external_object_id,
        "container_id": container_id,
        "source_revision": source_time,
        "ordering_domain": "source_updated_at_v1",
        "revision_order": order,
        "object_type": "pull_request",
        "event_type": event_type,
        "quality_state": quality_state,
        "duration_ms": None,
        "state": "observed",
        "supersedes_source_event_id": None,
        "reason_code": "observed",
        "event_at": source_time,
    }


class GitHubOutcomeAdapter:
    """Accept only reviewed body shapes; unsigned headers never select semantics."""

    def __init__(self, authenticator: GitHubWebhookAuthenticator) -> None:
        self.authenticator = authenticator

    def verify(
        self,
        *,
        binding: PortfolioConnectorBinding,
        headers: Mapping[str, str],
        raw: bytes,
    ) -> AuthenticatedDelivery:
        return self.authenticator.authenticate(headers, raw)

    def normalize(
        self,
        *,
        binding: PortfolioConnectorBinding,
        verified: AuthenticatedDelivery,
        body: dict,
    ) -> list[dict]:
        repository = body.get("repository")
        if repository is None:
            if any(name in body for name in ("pull_request", "review", "check_run")):
                raise PortfolioError("invalid_request")
            # Installation control deliveries have no work-object container.
            # Their durable unsupported receipt is operational coverage only.
            return []
        if type(repository) is not dict:
            raise PortfolioError("invalid_request")
        repository_id = _numeric_id(repository.get("id"))
        if repository_id not in binding.external_object_ids:
            raise PortfolioError("forbidden")

        has_pull_request = "pull_request" in body
        has_review = "review" in body
        has_check_run = "check_run" in body
        pull_request = body.get("pull_request")
        review = body.get("review")
        check_run = body.get("check_run")
        if has_check_run:
            if has_pull_request or has_review or type(check_run) is not dict:
                raise PortfolioError("invalid_request")
            return self._check_run(verified, repository_id, body, check_run)
        if has_review:
            if not has_pull_request or type(pull_request) is not dict or type(review) is not dict:
                raise PortfolioError("invalid_request")
            return self._review(verified, repository_id, body, pull_request, review)
        if has_pull_request:
            if type(pull_request) is not dict:
                raise PortfolioError("invalid_request")
            return self._pull_request(verified, repository_id, body, pull_request)
        # Repository, installation, ping, and every unrecognized signed event
        # are acknowledged only through a durable unsupported receipt.
        return []

    @staticmethod
    def _pull_request(
        verified: AuthenticatedDelivery,
        repository_id: str,
        body: dict,
        pull_request: dict,
    ) -> list[dict]:
        action = body.get("action")
        if action == "opened":
            event_type, event_at = "created", pull_request.get("created_at")
        elif action == "reopened":
            event_type, event_at = "reopened", pull_request.get("updated_at")
        elif action == "synchronize":
            event_type, event_at = "started", pull_request.get("updated_at")
        elif action == "closed":
            merged = pull_request.get("merged")
            if type(merged) is not bool:
                raise PortfolioError("invalid_request")
            event_type, event_at = (
                ("completed", pull_request.get("closed_at"))
                if merged
                else ("canceled", pull_request.get("closed_at"))
            )
        else:
            return []
        return [_observation(
            verified,
            external_object_id=_numeric_id(pull_request.get("id")),
            container_id=repository_id,
            event_type=event_type,
            quality_state="unknown",
            event_at=event_at,
        )]

    @staticmethod
    def _review(
        verified: AuthenticatedDelivery,
        repository_id: str,
        body: dict,
        pull_request: dict,
        review: dict,
    ) -> list[dict]:
        if body.get("action") != "submitted":
            return []
        state = review.get("state")
        if state == "approved":
            event_type, quality_state = "accepted", "accepted"
        elif state == "changes_requested":
            event_type, quality_state = "defect_reported", "rejected"
        else:
            return []
        return [_observation(
            verified,
            external_object_id=_numeric_id(pull_request.get("id")),
            container_id=repository_id,
            event_type=event_type,
            quality_state=quality_state,
            event_at=review.get("submitted_at"),
        )]

    @staticmethod
    def _check_run(
        verified: AuthenticatedDelivery,
        repository_id: str,
        body: dict,
        check_run: dict,
    ) -> list[dict]:
        if body.get("action") != "completed":
            return []
        if check_run.get("status") != "completed":
            raise PortfolioError("invalid_request")
        pull_requests = check_run.get("pull_requests")
        if not isinstance(pull_requests, list):
            raise PortfolioError("invalid_request")
        if len(pull_requests) != 1:
            return []
        association = pull_requests[0]
        if type(association) is not dict:
            raise PortfolioError("invalid_request")
        conclusion = check_run.get("conclusion")
        matrix = {
            "success": ("completed", "accepted"),
            "failure": ("defect_reported", "rejected"),
            "timed_out": ("defect_reported", "rejected"),
            "startup_failure": ("defect_reported", "rejected"),
            "action_required": ("defect_reported", "rejected"),
            "cancelled": ("canceled", "not_applicable"),
            "neutral": ("completed", "unknown"),
            "skipped": ("completed", "not_applicable"),
            "stale": ("canceled", "unknown"),
        }
        if conclusion not in matrix:
            return []
        event_type, quality_state = matrix[conclusion]
        return [_observation(
            verified,
            external_object_id=_numeric_id(association.get("id")),
            container_id=repository_id,
            event_type=event_type,
            quality_state=quality_state,
            event_at=check_run.get("completed_at"),
        )]


class GitHubOutcomeReceiver:
    """Select a dedicated signed channel without trusting request metadata."""

    def __init__(self, config, repository) -> None:
        runtime = config.outcome_connectors
        if runtime is None:
            raise PortfolioError("forbidden")
        ingestors: list[OutcomeIngestor] = []
        seen_material: set[bytes] = set()
        for channel in runtime.github:
            webhook_secrets = channel.resolved_webhook_secrets()
            material = [*webhook_secrets.values(), *(
                reference.value
                for reference in channel.identity_keys
                if reference.value is not None
            )]
            if len(material) != len(set(material)) or seen_material.intersection(material):
                raise PortfolioError("invalid_request")
            seen_material.update(material)
            keys = channel.resolved_identity_keys()
            authenticator = GitHubWebhookAuthenticator(
                config,
                channel.organization_id,
                channel.connector_id,
                webhook_secrets,
                keys,
                channel.delivery_identity_key_version,
            )
            adapter = GitHubOutcomeAdapter(authenticator)
            ingestors.append(OutcomeIngestor(
                config,
                repository,
                channel.organization_id,
                channel.connector_id,
                adapter,
                keys,
            ))
        if not ingestors:
            raise PortfolioError("forbidden")
        self._ingestors = tuple(ingestors)

    def ingest(self, headers: Mapping[str, str], raw: bytes) -> dict:
        failure: PortfolioError | None = None
        for ingestor in self._ingestors:
            try:
                return ingestor.ingest(headers, raw)
            except PortfolioError as error:
                if error.code == "unauthenticated":
                    continue
                failure = error
        if failure is not None:
            raise failure
        raise PortfolioError("unauthenticated")
