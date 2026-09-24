"""Deterministic run-to-outcome links and immutable association evidence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from ._association_schema import TABLE_DDL
from ._portfolio_sql import portfolio_transaction
from .association_evidence import (
    ASSOCIATION_SCHEMA_ID,
    LINK_SCHEMA_ID,
    association_source_identity,
    validate_association_evidence,
)
from .association_metrics import AssociationMetricError, build_association_metric_vector
from .audit_chain import AuditChainSource, build_audit_chain_entry, canonical_json_text
from .config import GatewayConfig
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import (
    ASSOCIATIONS,
    RESPONSE_BYTES,
    RUN_WORK_LINKS,
    PortfolioError,
    canonical,
    query_parameters,
    route,
    validate,
)


RULE_ID = "explicit-run-work-link-v1"
RULE_VERSION = 1
RULE_DIGEST = hashlib.sha256(canonical({
    "rule_id": RULE_ID,
    "version": RULE_VERSION,
    "join": "exact_explicit_run_work_link",
    "states": ["unmatched", "ambiguous", "associated", "excluded"],
    "window": "source_event_at_start_inclusive_end_exclusive",
}).encode("ascii")).hexdigest()
IDEMPOTENCY_DOMAIN = b"hormuz.run-work-link-idempotency:v1\x00"


class AssociationRepository:
    def __init__(self, config: GatewayConfig, *, dsn: str, connection_pool=None, read_only: bool = False):
        self.config = config
        self._dsn = dsn
        self._pool = connection_pool
        self._read_only = read_only

    def _authorize(self, principal: PortfolioPrincipal) -> None:
        control = self.config.portfolio_control
        if self._read_only or control is None or not isinstance(principal, PortfolioPrincipal):
            raise PortfolioError("forbidden")
        if not any(
            (item.organization_id, item.actor_id, item.roles)
            == (principal.organization_id, principal.actor_id, principal.roles)
            and "portfolio_admin" in item.roles
            for item in control.role_bindings
        ):
            raise PortfolioError("forbidden")

    def _authorize_connector(self, principal: PortfolioPrincipal, connector_id: str) -> None:
        control = self.config.portfolio_control
        if control is None or not any(
            (item.organization_id, item.connector_id)
            == (principal.organization_id, connector_id)
            for item in control.connectors
        ):
            raise PortfolioError("forbidden")

    @contextmanager
    def _transaction(self, organization_id: str):
        with portfolio_transaction(
            self.config,
            organization_id,
            dsn=self._dsn,
            connection_pool=self._pool,
            tables=TABLE_DDL,
            statement_timeout_ms=5000,
        ) as sql:
            yield sql

    def execute(
        self,
        principal: PortfolioPrincipal,
        operation: str,
        *,
        path: str,
        scope_id: str | None,
        query: dict[str, Any],
        body: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> tuple[int, dict[str, Any]]:
        self._authorize(principal)
        methods = {
            "list_links": ("GET", RUN_WORK_LINKS),
            "link_run": ("POST", RUN_WORK_LINKS),
            "list_associations": ("GET", ASSOCIATIONS),
            "evaluate_association": ("POST", ASSOCIATIONS),
        }
        if operation not in methods or scope_id is not None:
            raise PortfolioError("not_found")
        method, expected_path = methods[operation]
        if path != expected_path or route(method, path) != (operation, None):
            raise PortfolioError("invalid_request")
        query = query_parameters(urlencode(query), operation)
        if method == "GET":
            if body is not None or idempotency_key is not None:
                raise PortfolioError("invalid_request")
            result = self._list(principal, operation, query)
            return 200, result
        if query:
            raise PortfolioError("invalid_request")
        if operation == "link_run":
            result = self._link(principal, body, idempotency_key)
        else:
            # Evaluation replay is bound by outcome, fixed rule, window, and
            # expected predecessor.  Reject an unrelated idempotency header
            # rather than implying that an unpersisted key has any effect.
            if idempotency_key is not None:
                raise PortfolioError("invalid_request")
            result = self._evaluate(principal, body)
        return 201, result

    @staticmethod
    def _sequence(sql, organization_id: str) -> int:
        row = sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence "
            "FROM portfolio_association_audit_events WHERE organization_id=?",
            (organization_id,),
        )
        sequence = int(row["sequence"]) + 1
        if sequence > 9223372036854775807:
            raise PortfolioError("unavailable")
        return sequence

    @staticmethod
    def _audit(sql, principal, operation, entity_id, reason_code, now, sequence):
        sql.insert("portfolio_association_audit_events", {
            "organization_id": principal.organization_id,
            "event_id": str(uuid4()),
            "sequence": sequence,
            "actor_id": principal.actor_id,
            "operation": operation,
            "entity_id": entity_id,
            "reason_code": reason_code,
            "occurred_at": now,
        })

    @staticmethod
    def _public(value: dict[str, Any]) -> dict[str, Any]:
        try:
            validate_association_evidence(value["schema_id"], value)
            if len(canonical(value).encode("ascii")) > RESPONSE_BYTES:
                raise ValueError
        except (KeyError, TypeError, ValueError, PortfolioError):
            raise PortfolioError("unavailable") from None
        return value

    @classmethod
    def _stored_evidence(cls, row: dict[str, Any], schema_id: str) -> dict[str, Any]:
        try:
            value = json.loads(row["evidence_json"])
            if (
                not isinstance(value, dict)
                or canonical(value) != row["evidence_json"]
                or association_source_identity(schema_id, value)
                != row["link_event_id" if schema_id == LINK_SCHEMA_ID else "association_event_id"]
                or value["organization_id"] != row["organization_id"]
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise PortfolioError("unavailable") from None
        return cls._public(value)

    def _link(self, principal, body, idempotency_key):
        validate(body, "hormuz.run-work-link-request")
        if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key):
            raise PortfolioError("invalid_request")
        reason = body["reason_code"]
        expected = body["expected_prior_link_event_id"]
        if (reason == "explicit_link") != (expected is None):
            raise PortfolioError("invalid_request")
        organization = principal.organization_id
        outcome_ref = body["outcome"]
        self._authorize_connector(principal, outcome_ref["connector_id"])
        request_mac = hmac.new(
            idempotency_key.encode("ascii"),
            IDEMPOTENCY_DOMAIN
            + organization.encode("ascii")
            + b"\x00"
            + principal.actor_id.encode("ascii")
            + b"\x00"
            + canonical(body).encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        with self._transaction(organization) as sql:
            replay = sql.one(
                "SELECT request_mac,key_version,link_event_id FROM portfolio_run_work_link_idempotency "
                "WHERE organization_id=? AND actor_id=? AND idempotency_key=?",
                (organization, principal.actor_id, idempotency_key),
            )
            if replay is not None:
                if replay["key_version"] != 1 or not hmac.compare_digest(replay["request_mac"], request_mac):
                    raise PortfolioError("idempotency_conflict")
                row = sql.one(
                    "SELECT * FROM portfolio_run_work_link_events "
                    "WHERE organization_id=? AND link_event_id=?",
                    (organization, replay["link_event_id"]),
                )
                if row is None:
                    raise PortfolioError("unavailable")
                return self._stored_evidence(row, LINK_SCHEMA_ID)

            predecessor = None
            if expected is not None:
                predecessor = self._current_link(sql, organization, expected)
                if predecessor is None:
                    raise PortfolioError("version_conflict")
            if reason == "tombstoned":
                if (
                    predecessor is None
                    or predecessor["state"] != "active"
                    or predecessor["request_attempt_id"] != body["request_attempt_id"]
                    or predecessor["connector_id"] != outcome_ref["connector_id"]
                    or predecessor["source_event_id"] != outcome_ref["source_event_id"]
                ):
                    raise PortfolioError("version_conflict")
                basis = predecessor
            else:
                basis = self._link_basis(
                    sql,
                    principal,
                    body["request_attempt_id"],
                    outcome_ref["connector_id"],
                    outcome_ref["source_event_id"],
                )
                collision = self._current_pair(
                    sql,
                    organization,
                    body["request_attempt_id"],
                    outcome_ref["connector_id"],
                    outcome_ref["source_event_id"],
                )
                if reason == "explicit_link":
                    root = sql.one(
                        "SELECT link_event_id FROM portfolio_run_work_link_events "
                        "WHERE organization_id=? AND request_attempt_id=? AND connector_id=? "
                        "AND source_event_id=? AND supersedes_link_event_id IS NULL",
                        (
                            organization,
                            body["request_attempt_id"],
                            outcome_ref["connector_id"],
                            outcome_ref["source_event_id"],
                        ),
                    )
                    if root is not None:
                        raise PortfolioError("version_conflict")
                elif collision is not None and collision["link_event_id"] != expected:
                    raise PortfolioError("version_conflict")

            now = sql.now()
            sequence = self._sequence(sql, organization)
            link_event_id = str(uuid4())
            state = "tombstoned" if reason == "tombstoned" else "active"
            event = {
                "schema_id": LINK_SCHEMA_ID,
                "schema_version": 1,
                "organization_id": organization,
                "link_event_id": link_event_id,
                "request_attempt_id": basis["request_attempt_id"],
                "attribution_event_id": basis["attribution_event_id"],
                "outcome": {
                    "connector_id": basis["connector_id"],
                    "source_event_id": basis["source_event_id"],
                },
                "external_object_id": basis["external_object_id"],
                "source_revision": basis["source_revision"],
                "work_scope": {
                    "work_scope_id": basis["work_scope_id"],
                    "version": int(basis["work_scope_version"]),
                },
                "binding_event_id": basis["binding_event_id"],
                "state": state,
                "evidence_level": "associated",
                "supersedes_link_event_id": expected,
                "actor_id": principal.actor_id,
                "reason_code": reason,
                "event_at": now,
                "observed_at": now,
                "ingested_at": now,
            }
            self._public(event)
            evidence_json = canonical(event)
            operation = {
                "explicit_link": "link",
                "corrected": "correct",
                "tombstoned": "tombstone",
            }[reason]
            self._audit(sql, principal, operation, link_event_id, reason, now, sequence)
            sql.insert("portfolio_run_work_link_events", {
                "organization_id": organization,
                "link_event_id": link_event_id,
                "request_attempt_id": basis["request_attempt_id"],
                "attribution_event_id": basis["attribution_event_id"],
                "connector_id": basis["connector_id"],
                "source_event_id": basis["source_event_id"],
                "external_object_id": basis["external_object_id"],
                "source_revision": basis["source_revision"],
                "work_scope_id": basis["work_scope_id"],
                "work_scope_version": basis["work_scope_version"],
                "binding_event_id": basis["binding_event_id"],
                "state": state,
                "evidence_level": "associated",
                "supersedes_link_event_id": expected,
                "actor_id": principal.actor_id,
                "reason_code": reason,
                "event_at": now,
                "observed_at": now,
                "ingested_at": now,
                "sequence": sequence,
                "evidence_json": evidence_json,
            })
            sql.insert("portfolio_run_work_link_idempotency", {
                "organization_id": organization,
                "actor_id": principal.actor_id,
                "idempotency_key": idempotency_key,
                "request_mac": request_mac,
                "key_version": 1,
                "link_event_id": link_event_id,
            })
            self._append_chain(sql, event, LINK_SCHEMA_ID)
            return event

    @staticmethod
    def _current_link(sql, organization, link_event_id):
        return sql.one(
            "SELECT candidate.* FROM portfolio_run_work_link_events candidate "
            "WHERE candidate.organization_id=? AND candidate.link_event_id=? "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_run_work_link_events successor "
            "WHERE successor.organization_id=candidate.organization_id "
            "AND successor.supersedes_link_event_id=candidate.link_event_id)",
            (organization, link_event_id),
        )

    @staticmethod
    def _current_pair(sql, organization, attempt, connector, source):
        rows = sql.execute(
            "SELECT candidate.* FROM portfolio_run_work_link_events candidate "
            "WHERE candidate.organization_id=? AND candidate.request_attempt_id=? "
            "AND candidate.connector_id=? AND candidate.source_event_id=? "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_run_work_link_events successor "
            "WHERE successor.organization_id=candidate.organization_id "
            "AND successor.supersedes_link_event_id=candidate.link_event_id) "
            "LIMIT 2",
            (organization, attempt, connector, source),
        ).fetchall()
        if len(rows) > 1:
            raise PortfolioError("unavailable")
        return dict(rows[0]) if rows else None

    @staticmethod
    def _link_basis(sql, principal, attempt, connector, source):
        attribution = sql.one(
            "SELECT attribution.* FROM portfolio_attribution_events attribution "
            "JOIN gateway_request_attempts attempt ON "
            "attempt.organization_id=attribution.organization_id "
            "AND attempt.attempt_id=attribution.request_attempt_id "
            "WHERE attribution.organization_id=? AND attribution.request_attempt_id=? "
            "AND attribution.state='active' AND attribution.work_scope_id IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_attribution_events successor "
            "WHERE successor.organization_id=attribution.organization_id "
            "AND successor.supersedes_event_id=attribution.attribution_event_id) "
            "ORDER BY attribution.sequence DESC LIMIT 1",
            (principal.organization_id, attempt),
        )
        outcome = sql.one(
            "SELECT event.*,context.work_scope_id AS context_work_scope_id,"
            "context.work_scope_version AS context_work_scope_version,"
            "context.binding_event_id,context.ordering_state,context.scope_state "
            "FROM portfolio_outcome_events event JOIN portfolio_outcome_contexts context ON "
            "context.organization_id=event.organization_id "
            "AND context.connector_id=event.connector_id "
            "AND context.source_event_id=event.source_event_id "
            "WHERE event.organization_id=? AND event.connector_id=? AND event.source_event_id=?",
            (principal.organization_id, connector, source),
        )
        if attribution is None or outcome is None:
            raise PortfolioError("not_found")
        selected = AssociationRepository._selected_outcome(
            sql,
            principal.organization_id,
            outcome["connector_id"],
            outcome["external_object_id"],
            outcome["object_type"],
        )
        retained = sql.one(
            "SELECT retention_event_id FROM portfolio_outcome_retention_events "
            "WHERE organization_id=? AND connector_id=? AND source_event_id=? LIMIT 1",
            (principal.organization_id, connector, source),
        )
        scope = sql.one(
            "SELECT kind,state FROM portfolio_work_scope_versions "
            "WHERE organization_id=? AND work_scope_id=? AND version=?",
            (
                principal.organization_id,
                attribution["work_scope_id"],
                attribution["work_scope_version"],
            ),
        )
        if (
            retained is not None
            or selected is None
            or selected["source_event_id"] != outcome["source_event_id"]
            or outcome["state"] != "observed"
            or outcome["event_type"] == "unsupported"
            or outcome["ordering_state"] != "authoritative"
            or outcome["scope_state"] != "matched"
            or outcome["binding_event_id"] is None
            or outcome["context_work_scope_id"] != attribution["work_scope_id"]
            or outcome["context_work_scope_version"] != attribution["work_scope_version"]
            or scope is None
            or scope["kind"] != "use_case"
            or scope["state"] != "active"
        ):
            raise PortfolioError("version_conflict")
        return {
            "request_attempt_id": attribution["request_attempt_id"],
            "attribution_event_id": attribution["attribution_event_id"],
            "connector_id": outcome["connector_id"],
            "source_event_id": outcome["source_event_id"],
            "external_object_id": outcome["external_object_id"],
            "source_revision": outcome["source_revision"],
            "work_scope_id": attribution["work_scope_id"],
            "work_scope_version": int(attribution["work_scope_version"]),
            "binding_event_id": outcome["binding_event_id"],
        }

    def _evaluate(self, principal, body):
        validate(body, "hormuz.run-outcome-association-evaluation-request")
        organization = principal.organization_id
        connector = body["outcome"]["connector_id"]
        source = body["outcome"]["source_event_id"]
        self._authorize_connector(principal, connector)
        start_at = self._timestamp(body["window"]["start_at"])
        end_at = self._timestamp(body["window"]["end_at"])
        if datetime.fromisoformat(start_at) >= datetime.fromisoformat(end_at):
            raise PortfolioError("invalid_request")
        window_id = hashlib.sha256(canonical({
            "start_at": start_at,
            "end_at": end_at,
        }).encode("ascii")).hexdigest()
        expected = body["expected_prior_association_event_id"]
        with self._transaction(organization) as sql:
            replay = self._association_replay(
                sql, organization, connector, source, window_id, expected,
            )
            if replay is not None:
                return self._stored_evidence(replay, ASSOCIATION_SCHEMA_ID)
            if expected is not None:
                current = self._current_association(
                    sql, organization, connector, source, window_id,
                )
                if current is None or current["association_event_id"] != expected:
                    raise PortfolioError("version_conflict")
            event_row = sql.one(
                "SELECT event.*,context.ordering_state,context.scope_state "
                "FROM portfolio_outcome_events event JOIN portfolio_outcome_contexts context ON "
                "context.organization_id=event.organization_id "
                "AND context.connector_id=event.connector_id "
                "AND context.source_event_id=event.source_event_id "
                "WHERE event.organization_id=? AND event.connector_id=? AND event.source_event_id=?",
                (organization, connector, source),
            )
            if event_row is None:
                raise PortfolioError("not_found")
            now = sql.now()
            snapshot = int(sql.one(
                "SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_run_work_link_events "
                "WHERE organization_id=?",
                (organization,),
            )["sequence"])
            state, internal_reason, public_reason, candidates = self._decision(
                sql, organization, event_row, start_at, end_at,
            )
            chosen = candidates[0] if state == "associated" else None
            candidate_count = (
                max(2, len(candidates))
                if internal_reason == "source_conflict"
                else len(candidates)
            )
            association_event_id = str(uuid4())
            sequence = self._sequence(sql, organization)
            event = {
                "schema_id": ASSOCIATION_SCHEMA_ID,
                "schema_version": 1,
                "organization_id": organization,
                "association_event_id": association_event_id,
                "outcome": {"connector_id": connector, "source_event_id": source},
                "request_attempt_id": None if chosen is None else chosen["request_attempt_id"],
                "work_scope": None if chosen is None else {
                    "work_scope_id": chosen["work_scope_id"],
                    "version": int(chosen["work_scope_version"]),
                },
                "rule": {"rule_id": RULE_ID, "version": RULE_VERSION, "digest": RULE_DIGEST},
                "candidate_count": candidate_count,
                "state": state,
                "evidence_level": "associated" if state == "associated" else "descriptive",
                "supersedes_event_id": expected,
                "reason_code": public_reason,
                "event_at": self._timestamp(event_row["event_at"]),
                "observed_at": self._timestamp(event_row["observed_at"]),
                "ingested_at": now,
            }
            self._public(event)
            evidence_json = canonical(event)
            self._audit(sql, principal, "evaluate", association_event_id, state, now, sequence)
            sql.insert("portfolio_run_outcome_association_events", {
                "organization_id": organization,
                "association_event_id": association_event_id,
                "connector_id": connector,
                "source_event_id": source,
                "external_object_id": event_row["external_object_id"],
                "source_revision": event_row["source_revision"],
                "request_attempt_id": None if chosen is None else chosen["request_attempt_id"],
                "work_scope_id": None if chosen is None else chosen["work_scope_id"],
                "work_scope_version": None if chosen is None else chosen["work_scope_version"],
                "rule_id": RULE_ID,
                "rule_version": RULE_VERSION,
                "rule_digest": RULE_DIGEST,
                "window_id": window_id,
                "window_start_at": start_at,
                "window_end_at": end_at,
                "evaluation_as_of": now,
                "snapshot_sequence": snapshot,
                "candidate_count": candidate_count,
                "state": state,
                "evidence_level": event["evidence_level"],
                "link_event_id": None if chosen is None else chosen["link_event_id"],
                "supersedes_event_id": expected,
                "reason_code": internal_reason,
                "event_at": event["event_at"],
                "observed_at": event["observed_at"],
                "ingested_at": now,
                "sequence": sequence,
                "evidence_json": evidence_json,
            })
            self._append_chain(sql, event, ASSOCIATION_SCHEMA_ID)
            return event

    @staticmethod
    def _association_replay(sql, organization, connector, source, window_id, expected):
        if expected is None:
            return sql.one(
                "SELECT * FROM portfolio_run_outcome_association_events "
                "WHERE organization_id=? AND connector_id=? AND source_event_id=? "
                "AND rule_id=? AND rule_version=? AND window_id=? "
                "AND supersedes_event_id IS NULL",
                (organization, connector, source, RULE_ID, RULE_VERSION, window_id),
            )
        return sql.one(
            "SELECT * FROM portfolio_run_outcome_association_events "
            "WHERE organization_id=? AND connector_id=? AND source_event_id=? "
            "AND rule_id=? AND rule_version=? AND window_id=? AND supersedes_event_id=?",
            (organization, connector, source, RULE_ID, RULE_VERSION, window_id, expected),
        )

    @staticmethod
    def _current_association(sql, organization, connector, source, window_id):
        return sql.one(
            "SELECT candidate.* FROM portfolio_run_outcome_association_events candidate "
            "WHERE candidate.organization_id=? AND candidate.connector_id=? "
            "AND candidate.source_event_id=? AND candidate.rule_id=? "
            "AND candidate.rule_version=? AND candidate.window_id=? "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_run_outcome_association_events successor "
            "WHERE successor.organization_id=candidate.organization_id "
            "AND successor.supersedes_event_id=candidate.association_event_id) "
            "ORDER BY candidate.sequence DESC LIMIT 1",
            (organization, connector, source, RULE_ID, RULE_VERSION, window_id),
        )

    @staticmethod
    def _decision(sql, organization, outcome, start_at, end_at):
        event_at = AssociationRepository._timestamp(outcome["event_at"])
        retained = sql.one(
            "SELECT retention_event_id FROM portfolio_outcome_retention_events "
            "WHERE organization_id=? AND connector_id=? AND source_event_id=? LIMIT 1",
            (organization, outcome["connector_id"], outcome["source_event_id"]),
        )
        if outcome["event_type"] == "unsupported":
            return "excluded", "unsupported", "unsupported", []
        selected = AssociationRepository._selected_outcome(
            sql,
            organization,
            outcome["connector_id"],
            outcome["external_object_id"],
            outcome["object_type"],
        )
        source_conflict = False
        if selected is None:
            source_conflict = AssociationRepository._has_authority_conflict(
                sql, organization, outcome
            )
            if not source_conflict:
                return "excluded", "source_superseded", "superseded", []
        elif selected["source_event_id"] != outcome["source_event_id"]:
            return "excluded", "source_superseded", "superseded", []
        if outcome["state"] != "observed" or outcome["ordering_state"] != "authoritative":
            return "excluded", "source_superseded", "superseded", []
        if retained is not None or outcome["scope_state"] == "excluded" or not (start_at <= event_at < end_at):
            return "excluded", "source_excluded", "excluded", []
        rows = sql.execute(
            "SELECT candidate.* FROM portfolio_run_work_link_events candidate "
            "WHERE candidate.organization_id=? AND candidate.connector_id=? "
            "AND candidate.source_event_id=? AND candidate.state='active' "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_run_work_link_events successor "
            "WHERE successor.organization_id=candidate.organization_id "
            "AND successor.supersedes_link_event_id=candidate.link_event_id) "
            "ORDER BY candidate.sequence,candidate.link_event_id LIMIT 101",
            (organization, outcome["connector_id"], outcome["source_event_id"]),
        ).fetchall()
        candidates = [dict(row) for row in rows]
        if len(candidates) > 100:
            raise PortfolioError("unavailable")
        if source_conflict or AssociationRepository._has_authority_conflict(
            sql, organization, outcome
        ):
            return "ambiguous", "source_conflict", "ambiguous", candidates
        if len(candidates) == 1:
            return "associated", "explicit_link", "eligible", candidates
        if len(candidates) > 1:
            return "ambiguous", "multiple_eligible_links", "ambiguous", candidates
        tombstone = sql.one(
            "SELECT link_event_id FROM portfolio_run_work_link_events candidate "
            "WHERE candidate.organization_id=? AND candidate.connector_id=? "
            "AND candidate.source_event_id=? AND candidate.state='tombstoned' "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_run_work_link_events successor "
            "WHERE successor.organization_id=candidate.organization_id "
            "AND successor.supersedes_link_event_id=candidate.link_event_id) LIMIT 1",
            (organization, outcome["connector_id"], outcome["source_event_id"]),
        )
        if tombstone is not None:
            return "excluded", "link_tombstoned", "excluded", []
        return "unmatched", "missing_evidence", "missing_evidence", []

    @staticmethod
    def _selected_outcome(sql, organization, connector, external_object_id, object_type):
        return sql.one(
            "SELECT event.source_event_id,context.ordering_domain,context.revision_order "
            "FROM portfolio_outcome_events event JOIN portfolio_outcome_contexts context ON "
            "context.organization_id=event.organization_id "
            "AND context.connector_id=event.connector_id "
            "AND context.source_event_id=event.source_event_id "
            "WHERE event.organization_id=? AND event.connector_id=? "
            "AND event.external_object_id=? AND event.object_type=? "
            "AND context.ordering_state='authoritative' "
            "AND event.event_type<>'unsupported' "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_outcome_events successor "
            "WHERE successor.organization_id=event.organization_id "
            "AND successor.connector_id=event.connector_id "
            "AND successor.supersedes_source_event_id=event.source_event_id) "
            "ORDER BY context.revision_order DESC,event.sequence DESC,event.source_event_id DESC LIMIT 1",
            (organization, connector, external_object_id, object_type),
        )

    @staticmethod
    def _has_authority_conflict(sql, organization, outcome):
        return sql.one(
            "SELECT event.source_event_id FROM portfolio_outcome_events event "
            "JOIN portfolio_outcome_contexts context ON "
            "context.organization_id=event.organization_id "
            "AND context.connector_id=event.connector_id "
            "AND context.source_event_id=event.source_event_id "
            "WHERE event.organization_id=? AND event.connector_id=? "
            "AND event.external_object_id=? AND event.object_type=? "
            "AND event.source_event_id<>? AND event.state='observed' "
            "AND event.event_type<>'unsupported' AND context.ordering_state='uncertain' "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_outcome_events successor "
            "WHERE successor.organization_id=event.organization_id "
            "AND successor.connector_id=event.connector_id "
            "AND successor.supersedes_source_event_id=event.source_event_id) "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_outcome_retention_events retained "
            "WHERE retained.organization_id=event.organization_id "
            "AND retained.connector_id=event.connector_id "
            "AND retained.source_event_id=event.source_event_id) LIMIT 1",
            (
                organization,
                outcome["connector_id"],
                outcome["external_object_id"],
                outcome["object_type"],
                outcome["source_event_id"],
            ),
        ) is not None

    def _list(self, principal, operation, query):
        organization = principal.organization_id
        if "connector_id" in query:
            self._authorize_connector(principal, query["connector_id"])
        limit = query.get("limit", 50)
        filters = {key: value for key, value in query.items() if key not in {"limit", "cursor"}}
        authority_digest = hashlib.sha256(principal.cursor_authority.encode("ascii")).hexdigest()
        filters_digest = hashlib.sha256(canonical({
            "operation": operation,
            "filters": filters,
        }).encode("ascii")).hexdigest()
        with self._transaction(organization) as sql:
            cursor = None
            if "cursor" in query:
                cursor = sql.one(
                    "SELECT * FROM portfolio_run_outcome_association_cursors "
                    "WHERE organization_id=? AND cursor_id=?",
                    (organization, query["cursor"]),
                )
                if (
                    cursor is None
                    or cursor["actor_id"] != principal.actor_id
                    or not hmac.compare_digest(cursor["authority_digest"], authority_digest)
                    or not hmac.compare_digest(cursor["filters_digest"], filters_digest)
                ):
                    raise PortfolioError("cursor_invalid")
                age = (
                    datetime.fromisoformat(sql.now())
                    - datetime.fromisoformat(self._timestamp(cursor["as_of"]))
                ).total_seconds()
                if not 0 <= age <= 3600:
                    raise PortfolioError("cursor_invalid")
                as_of = cursor["as_of"]
                snapshot = int(cursor["snapshot_sequence"])
            else:
                as_of = sql.now()
                snapshot = int(sql.one(
                    "SELECT COALESCE(MAX(sequence),0) AS sequence "
                    "FROM portfolio_association_audit_events WHERE organization_id=?",
                    (organization,),
                )["sequence"])
            rows = self._page_rows(sql, organization, operation, filters, cursor, as_of, snapshot, limit)
            selected = [dict(row) for row in rows[:limit]]
            has_more = len(rows) > limit
            next_cursor = None
            if has_more:
                next_cursor = uuid4().hex + uuid4().hex
                last = selected[-1]
                identifier = "link_event_id" if operation == "list_links" else "association_event_id"
                sql.insert("portfolio_run_outcome_association_cursors", {
                    "organization_id": organization,
                    "cursor_id": next_cursor,
                    "actor_id": principal.actor_id,
                    "authority_digest": authority_digest,
                    "as_of": as_of,
                    "snapshot_sequence": snapshot,
                    "after_at": last["event_at"],
                    "after_id": last[identifier],
                    "filters_digest": filters_digest,
                })
            schema_id = LINK_SCHEMA_ID if operation == "list_links" else ASSOCIATION_SCHEMA_ID
            items = [self._stored_evidence(row, schema_id) for row in selected]
            sequence = self._sequence(sql, organization)
            self._audit(sql, principal, operation, None, "observed", sql.now(), sequence)
            result = {
                "schema_id": "hormuz.run-work-link-page" if operation == "list_links" else "hormuz.run-outcome-association-page",
                "schema_version": 1,
                "organization_id": organization,
                "items": items,
                "as_of": as_of,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }
            try:
                validate(result, result["schema_id"])
                if (result["next_cursor"] is None) == result["has_more"]:
                    raise ValueError
                if len(canonical(result).encode("ascii")) > RESPONSE_BYTES:
                    raise ValueError
            except (PortfolioError, TypeError, ValueError):
                raise PortfolioError("unavailable") from None
            return result

    def metric_reference(
        self,
        principal: PortfolioPrincipal,
        *,
        work_scope_id: str,
        work_scope_version: int,
        start_at: str,
        end_at: str,
        evaluated_at: str,
    ) -> dict[str, object]:
        """Audited internal metric input for the later scorecard runtime.

        This is deliberately not routed as a public API.  The caller is
        authorized before the transaction and the returned vector contains
        only closed metadata fields accepted by ``association_metrics``.
        """

        self._authorize(principal)
        validate(work_scope_id, "opaque_id")
        if type(work_scope_version) is not int or not 1 <= work_scope_version <= 2_147_483_647:
            raise PortfolioError("invalid_request")
        start = self._timestamp(start_at)
        end = self._timestamp(end_at)
        evaluated = self._timestamp(evaluated_at)
        if not datetime.fromisoformat(start) < datetime.fromisoformat(end) <= datetime.fromisoformat(evaluated):
            raise PortfolioError("invalid_request")
        organization = principal.organization_id
        with self._transaction(organization) as sql:
            if datetime.fromisoformat(evaluated) > datetime.fromisoformat(sql.now()):
                raise PortfolioError("invalid_request")
            scope = sql.one(
                "SELECT kind FROM portfolio_work_scope_versions "
                "WHERE organization_id=? AND work_scope_id=? AND version=?",
                (organization, work_scope_id, work_scope_version),
            )
            if scope is None or scope["kind"] != "use_case":
                raise PortfolioError("not_found")
            attempts = self._metric_attempts(sql, organization, start, end)
            costs = self._metric_costs(sql, organization, start, end)
            outcomes = self._metric_outcomes(sql, organization, evaluated)
            associations = self._metric_associations(sql, organization, start, end, evaluated)
            deliveries = self._metric_deliveries(
                sql, organization, start, end, evaluated
            )
            try:
                result = build_association_metric_vector(
                    context={
                        "organization_id": organization,
                        "work_scope_id": work_scope_id,
                        "work_scope_version": work_scope_version,
                        "start_at": start,
                        "end_at": end,
                        "evaluated_at": evaluated,
                        "rule_id": RULE_ID,
                        "rule_version": RULE_VERSION,
                        "rule_digest": RULE_DIGEST,
                    },
                    attempts=tuple(attempts),
                    costs=tuple(costs),
                    outcomes=tuple(outcomes),
                    associations=tuple(associations),
                    deliveries=tuple(deliveries),
                    # Webhook/event receipt success does not prove complete
                    # source history.  A future authorized snapshot source may
                    # supply completeness explicitly to the pure reference.
                    complete_connector_ids=(),
                )
            except AssociationMetricError:
                raise PortfolioError("unavailable") from None
            sequence = self._sequence(sql, organization)
            self._audit(
                sql,
                principal,
                "read_metrics",
                work_scope_id,
                "observed",
                sql.now(),
                sequence,
            )
        return result

    @staticmethod
    def _bounded_rows(cursor):
        rows = cursor.fetchall()
        if len(rows) > 10_000:
            raise PortfolioError("unavailable")
        return [dict(row) for row in rows]

    @classmethod
    def _metric_attempts(cls, sql, organization, start, end):
        rows = cls._bounded_rows(sql.execute(
            "SELECT root.organization_id,root.attempt_id AS request_attempt_id,"
            "root.created_at AS occurred_at,attribution.state AS attribution_state,"
            "attribution.work_scope_id,attribution.work_scope_version "
            "FROM gateway_request_attempts root LEFT JOIN portfolio_attribution_events attribution ON "
            "attribution.organization_id=root.organization_id "
            "AND attribution.request_attempt_id=root.attempt_id "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_attribution_events successor "
            "WHERE successor.organization_id=attribution.organization_id "
            "AND successor.supersedes_event_id=attribution.attribution_event_id) "
            "WHERE root.organization_id=? AND root.created_at>=? AND root.created_at<? "
            "ORDER BY root.attempt_id LIMIT 10001",
            (organization, start, end),
        ))
        for row in rows:
            row["occurred_at"] = cls._timestamp(row["occurred_at"])
            row["attribution_state"] = (
                "unmatched" if row["attribution_state"] is None
                else "active" if row["attribution_state"] == "active"
                else "excluded"
            )
        return rows

    @classmethod
    def _metric_costs(cls, sql, organization, start, end):
        rows = cls._bounded_rows(sql.execute(
            "SELECT organization_id,evidence_event_id AS cost_event_id,request_attempt_id,"
            "configured_estimate_availability,configured_estimate_amount,"
            "configured_estimate_currency,configured_rate_card_digest,occurred_at "
            "FROM gateway_finance_attempt_evidence WHERE organization_id=? "
            "AND occurred_at>=? AND occurred_at<? ORDER BY evidence_event_id LIMIT 10001",
            (organization, start, end),
        ))
        result = []
        for row in rows:
            available = row.pop("configured_estimate_availability") == "available"
            result.append({
                "organization_id": row["organization_id"],
                "cost_event_id": row["cost_event_id"],
                "request_attempt_id": row["request_attempt_id"],
                "basis": "configured_rate_card_estimate" if available else "not_available",
                "amount": row["configured_estimate_amount"] if available else None,
                "currency": row["configured_estimate_currency"] if available else None,
                "provenance_digest": row["configured_rate_card_digest"],
                "occurred_at": cls._timestamp(row["occurred_at"]),
            })
        return result

    @classmethod
    def _metric_outcomes(cls, sql, organization, evaluated):
        rows = cls._bounded_rows(sql.execute(
            "SELECT event.organization_id,event.connector_id,event.source_event_id,"
            "event.external_object_id,event.object_type,event.source_revision,"
            "context.ordering_domain,context.revision_order,context.ordering_state,"
            "event.event_type,event.quality_state,event.duration_ms,event.state,"
            "context.scope_state,context.work_scope_id,context.work_scope_version,"
            "event.supersedes_source_event_id,event.event_at,event.observed_at,"
            "CASE WHEN retained.retention_event_id IS NULL THEN 0 ELSE 1 END AS retained "
            "FROM portfolio_outcome_events event JOIN portfolio_outcome_contexts context ON "
            "context.organization_id=event.organization_id "
            "AND context.connector_id=event.connector_id "
            "AND context.source_event_id=event.source_event_id "
            "LEFT JOIN portfolio_outcome_retention_events retained ON "
            "retained.organization_id=event.organization_id "
            "AND retained.connector_id=event.connector_id "
            "AND retained.source_event_id=event.source_event_id "
            "WHERE event.organization_id=? AND event.observed_at<=? "
            "ORDER BY event.connector_id,event.source_event_id LIMIT 10001",
            (organization, evaluated),
        ))
        for row in rows:
            row["retained"] = bool(row["retained"])
            row["event_at"] = cls._timestamp(row["event_at"])
            row["observed_at"] = cls._timestamp(row["observed_at"])
        return rows

    @classmethod
    def _metric_associations(cls, sql, organization, start, end, evaluated):
        rows = cls._bounded_rows(sql.execute(
            "SELECT organization_id,association_event_id,connector_id,source_event_id,"
            "request_attempt_id,state,candidate_count,rule_id,rule_version,rule_digest,"
            "window_start_at,window_end_at,sequence "
            "FROM portfolio_run_outcome_association_events WHERE organization_id=? "
            "AND rule_id=? AND rule_version=? AND rule_digest=? "
            "AND window_start_at=? AND window_end_at=? AND evaluation_as_of<=? "
            "ORDER BY sequence LIMIT 10001",
            (organization, RULE_ID, RULE_VERSION, RULE_DIGEST, start, end, evaluated),
        ))
        for row in rows:
            row["window_start_at"] = cls._timestamp(row["window_start_at"])
            row["window_end_at"] = cls._timestamp(row["window_end_at"])
        return rows

    @classmethod
    def _metric_deliveries(cls, sql, organization, start, end, evaluated):
        rows = cls._bounded_rows(sql.execute(
            "SELECT receipt.organization_id,receipt.connector_id,"
            "receipt.source_delivery_id AS delivery_id,receipt.disposition AS state,"
            "COALESCE(failed.occurred_at,receipt.observed_at) AS occurred_at "
            "FROM portfolio_outcome_receipts receipt "
            "LEFT JOIN portfolio_outcome_dead_letters failed ON "
            "failed.organization_id=receipt.organization_id "
            "AND failed.connector_id=receipt.connector_id "
            "AND failed.source_delivery_id=receipt.source_delivery_id "
            "WHERE receipt.organization_id=? AND receipt.observed_at<=? "
            "AND COALESCE(failed.occurred_at,receipt.observed_at)>=? "
            "AND COALESCE(failed.occurred_at,receipt.observed_at)<? UNION ALL "
            "SELECT failed.organization_id,failed.connector_id,"
            "failed.source_delivery_id AS delivery_id,'failed' AS state,failed.occurred_at "
            "FROM portfolio_outcome_dead_letters failed "
            "WHERE failed.organization_id=? AND failed.occurred_at>=? AND failed.occurred_at<? "
            "AND NOT EXISTS (SELECT 1 FROM portfolio_outcome_receipts recovered "
            "WHERE recovered.organization_id=failed.organization_id "
            "AND recovered.connector_id=failed.connector_id "
            "AND recovered.source_delivery_id=failed.source_delivery_id "
            "AND recovered.observed_at<=?) "
            "ORDER BY connector_id,delivery_id LIMIT 10001",
            (
                organization, evaluated, start, end,
                organization, start, end, evaluated,
            ),
        ))
        for row in rows:
            row["occurred_at"] = cls._timestamp(row["occurred_at"])
        return rows

    @staticmethod
    def _page_rows(sql, organization, operation, filters, cursor, as_of, snapshot, limit):
        link = operation == "list_links"
        table = "portfolio_run_work_link_events" if link else "portfolio_run_outcome_association_events"
        identifier = "link_event_id" if link else "association_event_id"
        where = ["organization_id=?", "sequence<=?", "event_at<=?"]
        values: list[object] = [organization, snapshot, as_of]
        for field in ("connector_id", "source_event_id", "request_attempt_id", "work_scope_id", "state"):
            if field in filters:
                where.append(f"{field}=?")
                values.append(filters[field])
        if "start_at" in filters:
            where.extend(("event_at>=?", "event_at<?"))
            values.extend((filters["start_at"], filters["end_at"]))
        if cursor is not None:
            where.append(f"(event_at<? OR (event_at=? AND {identifier}<?))")
            values.extend((cursor["after_at"], cursor["after_at"], cursor["after_id"]))
        return sql.execute(
            f"SELECT * FROM {table} WHERE {' AND '.join(where)} "
            f"ORDER BY event_at DESC,{identifier} DESC LIMIT ?",
            (*values, limit + 1),
        ).fetchall()

    @staticmethod
    def _append_chain(sql, event, schema_id):
        source_id = association_source_identity(schema_id, event)
        source = AuditChainSource(schema_id, 1, source_id)
        organization = event["organization_id"]
        now = sql.now()
        if sql.postgres:
            sql.execute(
                "INSERT INTO gateway_audit_chain_epochs (organization_id,chain_version,chain_epoch,created_at,reason_code,predecessor_chain_epoch,predecessor_sequence,predecessor_head_digest) "
                "VALUES (?,1,1,?,'initial_adoption',NULL,NULL,NULL) "
                "ON CONFLICT (organization_id,chain_epoch) DO NOTHING",
                (organization, now),
            )
            sql.execute(
                "INSERT INTO gateway_audit_chain_heads (organization_id,chain_version,chain_epoch,sequence,head_digest) "
                "VALUES (?,1,1,0,NULL) ON CONFLICT (organization_id) DO NOTHING",
                (organization,),
            )
            head = sql.one(
                "SELECT * FROM gateway_audit_chain_heads WHERE organization_id=? FOR UPDATE",
                (organization,),
            )
        else:
            sql.execute(
                "INSERT OR IGNORE INTO gateway_audit_chain_epochs (organization_id,chain_version,chain_epoch,created_at,reason_code,predecessor_chain_epoch,predecessor_sequence,predecessor_head_digest) "
                "VALUES (?,1,1,?,'initial_adoption',NULL,NULL,NULL)",
                (organization, now),
            )
            sql.execute(
                "INSERT OR IGNORE INTO gateway_audit_chain_heads (organization_id,chain_version,chain_epoch,sequence,head_digest) "
                "VALUES (?,1,1,0,NULL)",
                (organization,),
            )
            head = sql.one(
                "SELECT * FROM gateway_audit_chain_heads WHERE organization_id=?",
                (organization,),
            )
        if head is None:
            raise PortfolioError("unavailable")
        try:
            entry = build_audit_chain_entry(
                event,
                chain_version=int(head["chain_version"]),
                chain_epoch=int(head["chain_epoch"]),
                sequence=int(head["sequence"]) + 1,
                previous_digest=head["head_digest"],
                entry_schema_version=2,
                source=source,
            )
        except Exception:
            raise PortfolioError("unavailable") from None
        inserted = sql.execute(
            "INSERT INTO gateway_audit_chain_entries "
            "(organization_id,chain_version,chain_epoch,sequence,entry_schema_id,entry_schema_version,event_id,previous_digest,event_digest,event_json,appended_at,source_schema_id,source_schema_version,source_event_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                organization,
                entry["chain_version"],
                entry["chain_epoch"],
                entry["sequence"],
                entry["schema_id"],
                entry["schema_version"],
                source_id,
                entry["previous_digest"],
                entry["event_digest"],
                canonical_json_text(event),
                now,
                schema_id,
                1,
                source_id,
            ),
        )
        if inserted.rowcount != 1:
            raise PortfolioError("unavailable")
        updated = sql.execute(
            "UPDATE gateway_audit_chain_heads SET sequence=?,head_digest=? "
            "WHERE organization_id=? AND chain_epoch=? AND sequence=?",
            (
                entry["sequence"],
                entry["event_digest"],
                organization,
                head["chain_epoch"],
                head["sequence"],
            ),
        )
        if updated.rowcount != 1:
            raise PortfolioError("unavailable")

    @staticmethod
    def _timestamp(value: str) -> str:
        try:
            instant = value if isinstance(value, datetime) else datetime.fromisoformat(value)
            if instant.tzinfo is None:
                raise ValueError
            return instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        except (TypeError, ValueError):
            raise PortfolioError("invalid_request") from None
