"""Source-neutral append-only outcomes, with one tenant-locked commit per batch.

Only OutcomeIngestor calls the private verified-delivery methods. They are not
public authentication entrypoints. This owner borrows the existing pool/DSN;
it never reads credentials, rewrites prior facts, retries a provider, or infers
associated/controlled evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import hashlib
import hmac
import re
from urllib.parse import urlencode
from uuid import uuid4

from ._outcome_schema import TABLE_DDL
from ._portfolio_sql import portfolio_transaction
from .config import GatewayConfig
from .outcome_ingest import registered_binding, validate_delivery
from .outcome_wire import (
    DEAD_LETTER_BYTES, EVENTS_PER_DELIVERY, OutcomeKeys, SourceObservation,
    decode_source_body, observation_from_mapping, source_id, timestamp,
    validate_context, validate_coverage, validate_retention,
)
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import OUTCOMES, RESPONSE_BYTES, PortfolioError, canonical, outcome_catalogue, query_parameters, validate


class OutcomeRepository:
    def __init__(self, config: GatewayConfig, *, dsn: str, connection_pool=None, read_only: bool = False):
        self.config, self._dsn, self._pool, self._read_only = config, dsn, connection_pool, read_only

    def _authorize(self, principal):
        control = self.config.portfolio_control
        if self._read_only or control is None or not isinstance(principal, PortfolioPrincipal):
            raise PortfolioError("forbidden")
        if not any((item.organization_id, item.actor_id, item.roles) ==
                   (principal.organization_id, principal.actor_id, principal.roles) and "portfolio_admin" in item.roles
                   for item in control.role_bindings):
            raise PortfolioError("forbidden")

    def _authorize_connector(self, binding, verified):
        if self._read_only or registered_binding(self.config, binding.organization_id, binding.connector_id) != binding:
            raise PortfolioError("forbidden")
        validate_delivery(binding, verified)

    @contextmanager
    def _transaction(self, organization):
        with portfolio_transaction(self.config, organization, dsn=self._dsn, connection_pool=self._pool,
                                   tables=TABLE_DDL, statement_timeout_ms=5000) as sql:
            yield sql

    @staticmethod
    def _sequence(sql, organization):
        return int(sql.one("SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_outcome_audit_events WHERE organization_id=?",
                           (organization,))["sequence"])

    def _audit(self, sql, organization, operation, entity, reason, *, connector=None, actor=None, now=None):
        sequence = self._sequence(sql, organization) + 1
        sql.insert("portfolio_outcome_audit_events", {
            "organization_id": organization, "event_id": uuid4().hex, "sequence": sequence,
            "actor_id": actor, "connector_id": connector, "operation": operation, "entity_id": entity,
            "reason_code": reason, "occurred_at": sql.now() if now is None else now,
        })
        return sequence

    @staticmethod
    def _event(row):
        names = outcome_catalogue()["$defs"]["hormuz.work-outcome-event"]["properties"]
        return {name: "hormuz.work-outcome-event" if name == "schema_id" else row[name] for name in names}

    @staticmethod
    def _receipt(row):
        names = outcome_catalogue()["$defs"]["hormuz.connector-ingest-receipt"]["properties"]
        return {name: "hormuz.connector-ingest-receipt" if name == "schema_id" else row[name] for name in names}

    @staticmethod
    def _public(value):
        try:
            validate(value, value["schema_id"])
            if len(canonical(value).encode("ascii")) > RESPONSE_BYTES:
                raise PortfolioError("unavailable")
        except (PortfolioError, KeyError, TypeError, ValueError):
            raise PortfolioError("unavailable") from None
        return value

    @staticmethod
    def _authority_digest(keys, version, binding):
        return keys.metadata_digest(version, binding.organization_id, binding.connector_id, "connector-binding", asdict(binding))

    def _check_fingerprint(self, row, binding, verified, raw, keys):
        try:
            validate(row["fingerprint"], "digest")
            validate(row["authority_digest"], "digest")
            validate(row["key_version"], "opaque_id")
        except (PortfolioError, KeyError, TypeError):
            raise PortfolioError("unavailable") from None
        fingerprint = keys.delivery_digest(row["key_version"], binding.organization_id, binding.connector_id, verified.source_delivery_id, raw)
        authority = self._authority_digest(keys, row["key_version"], binding)
        if not hmac.compare_digest(row["fingerprint"], fingerprint) or not hmac.compare_digest(row["authority_digest"], authority):
            raise PortfolioError("idempotency_conflict")

    def _replay(self, sql, binding, verified, raw, keys):
        identifiers = (binding.organization_id, binding.connector_id, verified.source_delivery_id)
        row = sql.one("SELECT * FROM portfolio_outcome_receipts WHERE organization_id=? AND connector_id=? AND source_delivery_id=?", identifiers)
        if row is not None:
            self._check_fingerprint(row, binding, verified, raw, keys)
            return self._public(self._receipt(row))
        # An authenticated failed delivery also permanently binds its exact
        # bytes. A later normalizer may recover it, but different bytes cannot
        # reuse its identity or erase the first failure/coverage observation.
        failure = sql.one("SELECT * FROM portfolio_outcome_dead_letters WHERE organization_id=? AND connector_id=? AND source_delivery_id=?", identifiers)
        if failure is not None:
            self._check_fingerprint(failure, binding, verified, raw, keys)
        return None

    def _replay_verified(self, *, binding, verified, raw, keys):
        self._authorize_connector(binding, verified)
        with self._transaction(binding.organization_id) as sql:
            return self._replay(sql, binding, verified, raw, keys)

    @staticmethod
    def _lineage_order(observations):
        pending, ordered = {item.source_event_id: item for item in observations}, []
        while pending:
            ready = [item for item in pending.values() if item.supersedes_source_event_id not in pending]
            if not ready:
                raise PortfolioError("invalid_request")
            for item in ready:
                ordered.append(item)
                del pending[item.source_event_id]
        return ordered

    def _accept_verified(self, *, binding, verified, raw, keys, observed_at, observations):
        self._authorize_connector(binding, verified)
        if not isinstance(keys, OutcomeKeys) or not isinstance(observations, tuple) or len(observations) > EVENTS_PER_DELIVERY:
            raise PortfolioError("invalid_request")
        for item in observations:
            if not isinstance(item, SourceObservation):
                raise PortfolioError("invalid_request")
            observation_from_mapping(asdict(item), binding)
        if len({item.source_event_id for item in observations}) != len(observations):
            raise PortfolioError("idempotency_conflict")
        observations = self._lineage_order(observations)
        observed_at = timestamp(observed_at)
        organization, connector, delivery = binding.organization_id, binding.connector_id, verified.source_delivery_id
        with self._transaction(organization) as sql:
            previous = self._replay(sql, binding, verified, raw, keys)
            if previous is not None:
                return previous
            now, receipt_id = sql.now(), uuid4().hex
            unsupported = not observations or all(item.event_type == "unsupported" for item in observations)
            sequence = self._audit(sql, organization, "ingest", receipt_id, "unsupported" if unsupported else "observed",
                                   connector=connector, now=now)
            receipt = {
                "organization_id": organization, "connector_id": connector, "source_delivery_id": delivery,
                "receipt_id": receipt_id, "schema_version": 1,
                "fingerprint": keys.delivery_digest(keys.current_version, organization, connector, delivery, raw),
                "authority_digest": self._authority_digest(keys, keys.current_version, binding),
                "key_version": keys.current_version, "disposition": "unsupported" if unsupported else "accepted",
                "accepted_event_count": len(observations), "observed_at": observed_at, "ingested_at": now, "sequence": sequence,
            }
            sql.insert("portfolio_outcome_receipts", receipt)
            for observation in observations:
                self._append(sql, binding, verified, observation, keys, observed_at, now, sequence)
            if not observations:
                self._coverage(sql, organization, connector, delivery, None, "unsupported", "unsupported", now, sequence)
            result = self._public(self._receipt(receipt))
        return result

    def _append(self, sql, binding, verified, observation, keys, observed_at, now, sequence):
        organization, connector = binding.organization_id, binding.connector_id
        source = observation.source_event_id
        if sql.one("SELECT source_event_id FROM portfolio_outcome_events WHERE organization_id=? AND connector_id=? AND source_event_id=?",
                   (organization, connector, source)) is not None:
            raise PortfolioError("idempotency_conflict")
        if observation.supersedes_source_event_id is not None:
            prior = sql.one("SELECT * FROM portfolio_outcome_events WHERE organization_id=? AND connector_id=? AND source_event_id=?",
                            (organization, connector, observation.supersedes_source_event_id))
            if prior is None:
                raise PortfolioError("not_found")
            if (prior["external_object_id"], prior["object_type"]) != (observation.external_object_id, observation.object_type):
                raise PortfolioError("invalid_request", "invalid_reference")
            if sql.one("SELECT source_event_id FROM portfolio_outcome_events WHERE organization_id=? AND connector_id=? AND supersedes_source_event_id=?",
                       (organization, connector, observation.supersedes_source_event_id)) is not None:
                raise PortfolioError("version_conflict")
        context = self._context(sql, binding, verified, observation, keys.current_version, observed_at)
        validate_context(context)
        raw_metadata = asdict(observation)
        event = {
            "organization_id": organization, "connector_id": connector, "source_event_id": source,
            "source_delivery_id": verified.source_delivery_id, "schema_version": 1,
            **{name: getattr(observation, name) for name in (
                "external_object_id", "source_revision", "object_type", "event_type", "quality_state",
                "duration_ms", "state", "supersedes_source_event_id", "reason_code",
            )},
            "evidence_level": "descriptive",
            "event_at": timestamp(observation.event_at) if observation.event_at is not None else observed_at,
            "observed_at": observed_at, "ingested_at": now, "sequence": sequence,
        }
        if observation.event_at is None and observation.reason_code == "observed":
            event["reason_code"] = "missing_evidence"
        event["provenance_digest"] = keys.metadata_digest(keys.current_version, organization, connector, source,
                                                         {"observation": raw_metadata, "context": context, "event": event})
        self._public(self._event(event))
        sql.insert("portfolio_outcome_observations", {
            "organization_id": organization, "connector_id": connector, "source_event_id": source,
            "source_delivery_id": verified.source_delivery_id, "metadata_json": canonical(raw_metadata),
        })
        sql.insert("portfolio_outcome_events", event)
        sql.insert("portfolio_outcome_contexts", {name: value for name, value in context.items() if name != "schema_id"})
        state, reason = "observed", "observed"
        if observation.state == "tombstoned":
            state, reason = "excluded", "tombstoned"
        elif observation.state == "superseded":
            state, reason = "superseded", "superseded"
        elif observation.event_type == "unsupported":
            state, reason = "unsupported", "unsupported"
        elif context["ordering_state"] == "late":
            state, reason = "late", "excluded"
        elif context["scope_state"] != "matched":
            state = reason = context["scope_state"]
        elif context["ordering_state"] == "uncertain":
            state, reason = "ambiguous", "missing_evidence"
        self._coverage(sql, organization, connector, verified.source_delivery_id, source, state, reason, now, sequence)

    def _context(self, sql, binding, verified, observation, key_version, observed_at):
        organization, connector = binding.organization_id, binding.connector_id
        registry_sequence = int(sql.one("SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_audit_events WHERE organization_id=?",
                                        (organization,))["sequence"])
        scope, scope_version, binding_event, scope_state = None, None, None, "unmatched"
        source_time = timestamp(observation.event_at) if observation.event_at is not None else None
        if source_time is not None and source_time <= observed_at:
            bound = sql.one("SELECT * FROM portfolio_binding_events WHERE organization_id=? AND connector_id=? "
                            "AND external_object_id=? AND event_at<=? ORDER BY sequence DESC LIMIT 1",
                            (organization, connector, observation.container_id, source_time))
            if bound is not None:
                scope, scope_version, binding_event = bound["work_scope_id"], bound["work_scope_version"], bound["binding_event_id"]
                current = sql.one("SELECT * FROM portfolio_work_scope_versions WHERE organization_id=? AND work_scope_id=? "
                                  "AND event_at<=? ORDER BY version DESC LIMIT 1", (organization, scope, source_time))
                scope_state = "matched" if (bound["state"] == "active" and current is not None and current["kind"] == "use_case"
                                             and current["state"] == "active" and current["version"] == scope_version) else "excluded"
        elif source_time is not None:
            scope_state = "excluded"
        ordering = "uncertain"
        if observation.state == "superseded":
            ordering = "superseded"
        elif observation.event_type != "unsupported" and source_time is not None and source_time <= observed_at and observation.ordering_domain is not None:
            prior = sql.one(
                "SELECT c.ordering_domain,c.revision_order FROM portfolio_outcome_contexts c "
                "JOIN portfolio_outcome_events e ON (e.organization_id=c.organization_id AND e.connector_id=c.connector_id AND e.source_event_id=c.source_event_id) "
                "WHERE e.organization_id=? AND e.connector_id=? AND e.external_object_id=? AND e.object_type=? AND c.ordering_state='authoritative' "
                "ORDER BY c.revision_order DESC,e.sequence DESC,e.source_event_id DESC LIMIT 1",
                (organization, connector, observation.external_object_id, observation.object_type),
            )
            if prior is None:
                ordering = "authoritative"
            elif prior["ordering_domain"] == observation.ordering_domain:
                order = int(observation.revision_order)
                ordering = "authoritative" if order > prior["revision_order"] else "late" if order < prior["revision_order"] else "uncertain"
        return {
            "schema_id": "hormuz.outcome-observation-context", "schema_version": 1,
            "organization_id": organization, "connector_id": connector, "source_event_id": observation.source_event_id,
            "provider": binding.provider, "authority_id": binding.installation_id if binding.provider == "github" else binding.workspace_id,
            "source_container_id": observation.container_id, "actor_id": None, "authentication_kind": "verified_connector",
            "work_scope_id": scope, "work_scope_version": scope_version, "binding_event_id": binding_event,
            "registry_sequence": registry_sequence, "key_version": key_version, "credential_version": verified.credential_version,
            "source_time_known": int(source_time is not None), "ordering_domain": observation.ordering_domain,
            "revision_order": int(observation.revision_order) if observation.revision_order is not None else None,
            "ordering_state": ordering, "scope_state": scope_state,
        }

    @staticmethod
    def _coverage(sql, organization, connector, delivery, source, state, reason, now, sequence):
        value = {
            "schema_id": "hormuz.outcome-coverage-event", "schema_version": 1,
            "organization_id": organization, "coverage_event_id": uuid4().hex, "connector_id": connector,
            "source_delivery_id": delivery, "source_event_id": source, "state": state, "reason_code": reason,
            "eligibility_state": "inconclusive", "rule_id": None, "rule_version": None, "member_count": 1,
            "member_unit": "source_event" if source is not None else "delivery", "ingested_at": now, "sequence": sequence,
        }
        validate_coverage(value)
        sql.insert("portfolio_outcome_coverage_events", {name: item for name, item in value.items() if name != "schema_id"})

    def _record_failure(self, *, binding, verified, raw, keys, observed_at, reason):
        self._authorize_connector(binding, verified)
        if reason not in {"invalid_shape", "unauthorized_scope", "conflicting_identity", "dependency_unavailable"}:
            raise PortfolioError("invalid_request")
        organization, connector, delivery = binding.organization_id, binding.connector_id, verified.source_delivery_id
        with self._transaction(organization) as sql:
            # Another normalizer version may have accepted these exact bytes
            # after our initial replay check. That durable receipt wins over
            # the losing worker's error; propagate it without new writes.
            receipt = self._replay(sql, binding, verified, raw, keys)
            if receipt is not None:
                return receipt
            if sql.one("SELECT source_delivery_id FROM portfolio_outcome_dead_letters WHERE organization_id=? AND connector_id=? AND source_delivery_id=?",
                       (organization, connector, delivery)) is not None:
                return
            now = sql.now()
            metadata = canonical({
                "schema_id": "hormuz.outcome-dead-letter", "schema_version": 1,
                "organization_id": organization, "connector_id": connector, "source_delivery_id": delivery,
                "credential_version": verified.credential_version, "reason_code": reason, "request_bytes": len(raw),
            })
            if len(metadata.encode("ascii")) > DEAD_LETTER_BYTES:
                raise PortfolioError("invalid_request")
            sequence = self._audit(sql, organization, "reject", delivery, reason, connector=connector, now=now)
            sql.insert("portfolio_outcome_dead_letters", {
                "organization_id": organization, "connector_id": connector, "source_delivery_id": delivery,
                "fingerprint": keys.delivery_digest(keys.current_version, organization, connector, delivery, raw),
                "authority_digest": self._authority_digest(keys, keys.current_version, binding),
                "key_version": keys.current_version, "reason_code": reason, "metadata_json": metadata,
                "occurred_at": now, "sequence": sequence,
            })
            self._coverage(sql, organization, connector, delivery, None, "failed", reason, now, sequence)

    def execute(self, principal, operation, *, path, scope_id, query, body, idempotency_key):
        self._authorize(principal)
        if operation != "list_outcomes":
            raise PortfolioError("not_found")
        if path != OUTCOMES or scope_id is not None or body is not None:
            raise PortfolioError("invalid_request")
        validate(query, "hormuz.portfolio-query")
        parameters = query_parameters(urlencode(query), "list_outcomes")
        if "connector_id" in parameters:
            registered_binding(self.config, principal.organization_id, parameters["connector_id"])
        with self._transaction(principal.organization_id) as sql:
            result = self._public(self._list(sql, principal, parameters))
        return 200, result

    def _cursor_authority(self, principal):
        # Store a bounded digest of current tenant connector registration, not
        # secret material or an unbounded copy of all allowlists per cursor.
        registrations = [asdict(item) for item in self.config.portfolio_control.connectors if item.organization_id == principal.organization_id]
        digest = hashlib.sha256(canonical(registrations).encode("ascii")).hexdigest()
        return canonical([principal.cursor_authority, digest])

    def _list(self, sql, principal, query):
        organization, now, limit = principal.organization_id, sql.now(), query.get("limit", 50)
        filters = {name: value for name, value in query.items() if name != "limit"}
        snapshot, as_of, cursor = self._sequence(sql, organization), now, None
        retention_sequence = sql.one("SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_outcome_retention_events WHERE organization_id=?",
                                     (organization,))["sequence"]
        # Privacy/retention changes invalidate previous snapshots instead of
        # letting a frozen cursor deliver logically deleted metadata.
        authority = canonical([self._cursor_authority(principal), retention_sequence])
        if "cursor" in query:
            cursor = sql.one("SELECT * FROM portfolio_outcome_cursors WHERE organization_id=? AND cursor_id=?", (organization, query["cursor"]))
            if cursor is None or cursor["actor_id"] != principal.actor_id or cursor["authority_json"] != authority:
                raise PortfolioError("cursor_invalid")
            try:
                timestamp(cursor["as_of"])
                timestamp(cursor["after_at"])
                validate(cursor["after_connector"], "opaque_id")
                source_id(cursor["after_id"])
                if type(cursor["snapshot_sequence"]) is not int or not 0 <= cursor["snapshot_sequence"] <= snapshot:
                    raise PortfolioError("invalid_request")
                if not isinstance(cursor["filters_json"], str) or len(cursor["filters_json"]) > 4096:
                    raise PortfolioError("invalid_request")
                filters = decode_source_body(cursor["filters_json"].encode("ascii"))
                if set(filters) - {"start_at", "end_at", "work_scope_id", "connector_id"}:
                    raise PortfolioError("invalid_request")
                validate(filters, "hormuz.portfolio-query")
                filters = query_parameters(urlencode(filters), "list_outcomes")
            except (PortfolioError, ValueError, TypeError, UnicodeError):
                raise PortfolioError("unavailable") from None
            age = (datetime.fromisoformat(now) - datetime.fromisoformat(cursor["as_of"])).total_seconds()
            if not 0 <= age <= 3600:
                raise PortfolioError("cursor_invalid")
            snapshot, as_of = cursor["snapshot_sequence"], cursor["as_of"]
        where, values = [
            "e.organization_id=?", "e.sequence<=?",
            "NOT EXISTS (SELECT 1 FROM portfolio_outcome_retention_events t WHERE t.organization_id=e.organization_id "
            "AND t.connector_id=e.connector_id AND t.source_event_id=e.source_event_id)",
        ], [organization, snapshot]
        for name, column in (("work_scope_id", "c.work_scope_id"), ("connector_id", "e.connector_id")):
            if name in filters:
                where.append(column + "=?")
                values.append(filters[name])
        for name, comparator in (("start_at", ">="), ("end_at", "<")):
            if name in filters:
                where.append("e.event_at" + comparator + "?")
                values.append(timestamp(filters[name]))
        joined = ("FROM portfolio_outcome_events e JOIN portfolio_outcome_contexts c "
                  "ON (c.organization_id=e.organization_id AND c.connector_id=e.connector_id AND c.source_event_id=e.source_event_id) ")
        if cursor:
            # A well-typed cursor is still corrupt if its anchor is absent,
            # outside the frozen snapshot/filters, or has a different time.
            # Refuse before a partial page or another read audit can commit.
            anchor = sql.one(
                f"SELECT e.* {joined}WHERE {' AND '.join(where)} "
                "AND e.connector_id=? AND e.source_event_id=? AND e.event_at=?",
                (*values, cursor["after_connector"], cursor["after_id"], cursor["after_at"]),
            )
            if anchor is None:
                raise PortfolioError("unavailable")
            self._public(self._event(anchor))
            where.append("(e.event_at<? OR (e.event_at=? AND (e.connector_id<? OR (e.connector_id=? AND e.source_event_id<?))))")
            values.extend((cursor["after_at"], cursor["after_at"], cursor["after_connector"], cursor["after_connector"], cursor["after_id"]))
        rows = sql.execute(
            f"SELECT e.* {joined}WHERE {' AND '.join(where)} ORDER BY e.event_at DESC,e.connector_id DESC,e.source_event_id DESC LIMIT ?",
            (*values, limit + 1),
        ).fetchall()
        selected, more, next_cursor = [dict(row) for row in rows[:limit]], len(rows) > limit, None
        if more:
            next_cursor, last = uuid4().hex + uuid4().hex, selected[-1]
            sql.insert("portfolio_outcome_cursors", {
                "organization_id": organization, "cursor_id": next_cursor, "actor_id": principal.actor_id,
                "authority_json": authority, "as_of": as_of, "snapshot_sequence": snapshot,
                "after_at": last["event_at"], "after_connector": last["connector_id"], "after_id": last["source_event_id"],
                "filters_json": canonical(filters),
            })
        result = {
            "schema_id": "hormuz.work-outcome-page", "schema_version": 1, "organization_id": organization,
            "items": [self._event(row) for row in selected], "as_of": as_of, "has_more": more, "next_cursor": next_cursor,
        }
        self._public(result)
        self._audit(sql, organization, "list_outcomes", None, "observed", actor=principal.actor_id, now=now)
        return result

    def context(self, principal, connector, source):
        """Audited internal provenance read; not an additional public route."""
        self._authorize(principal)
        validate(connector, "opaque_id")
        source_id(source)
        with self._transaction(principal.organization_id) as sql:
            row = sql.one("SELECT * FROM portfolio_outcome_contexts WHERE organization_id=? AND connector_id=? AND source_event_id=?",
                          (principal.organization_id, connector, source))
            if row is None:
                raise PortfolioError("not_found")
            result = {"schema_id": "hormuz.outcome-observation-context", **row}
            try:
                validate_context(result)
            except PortfolioError:
                raise PortfolioError("unavailable") from None
            self._audit(sql, principal.organization_id, "read_context", source, "observed", actor=principal.actor_id, connector=connector)
        return result

    def current(self, principal, connector, external_object_id, *, object_type="issue"):
        """Latest comparable descriptive source fact, never an eligibility claim."""
        self._authorize(principal)
        validate(connector, "opaque_id")
        source_id(external_object_id)
        if object_type not in ("issue", "pull_request"):
            raise PortfolioError("invalid_request")
        with self._transaction(principal.organization_id) as sql:
            row = sql.one(
                "SELECT e.* FROM portfolio_outcome_events e JOIN portfolio_outcome_contexts c "
                "ON (e.organization_id=c.organization_id AND e.connector_id=c.connector_id AND e.source_event_id=c.source_event_id) "
                "WHERE e.organization_id=? AND e.connector_id=? AND e.external_object_id=? AND e.object_type=? AND c.ordering_state='authoritative' "
                "ORDER BY c.revision_order DESC,e.sequence DESC,e.source_event_id DESC LIMIT 1",
                (principal.organization_id, connector, external_object_id, object_type),
            )
            retained = row is not None and sql.one(
                "SELECT retention_event_id FROM portfolio_outcome_retention_events WHERE organization_id=? AND connector_id=? AND source_event_id=? LIMIT 1",
                (principal.organization_id, connector, row["source_event_id"]),
            ) is not None
            # Do not resurrect an older authoritative event after deleting the
            # current one. The retained original and marker remain auditable.
            result = self._public(self._event(row)) if row is not None and not retained else None
            self._audit(sql, principal.organization_id, "read_context", external_object_id, "observed", actor=principal.actor_id, connector=connector)
        return result

    @staticmethod
    def _retention(row):
        result = {"schema_id": "hormuz.outcome-retention-event", **{name: row[name] for name in (
            "schema_version", "organization_id", "retention_event_id", "connector_id", "source_event_id",
            "actor_id", "reason_code", "event_at", "observed_at", "ingested_at",
        )}}
        try:
            validate_retention(result)
        except PortfolioError:
            raise PortfolioError("unavailable") from None
        return result

    def tombstone(self, principal, connector, source, *, idempotency_key, keys):
        """Internal explicit retention action, never a fabricated source receipt.

        No HTTP/CLI mutation or automatic retention schedule is activated here.
        An authorized operator names one exact tenant-owned observation. This
        appends a separate domain tombstone, retains all financial/audit links,
        and works after connector disablement. Backups/exports are operator-owned.
        """
        self._authorize(principal)
        validate(connector, "opaque_id")
        source_id(source)
        if not isinstance(keys, OutcomeKeys) or not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key):
            raise PortfolioError("invalid_request")
        organization = principal.organization_id
        request = {"schema_id": "hormuz.outcome-retention-request", "schema_version": 1,
                   "connector_id": connector, "source_event_id": source, "actor_id": principal.actor_id, "reason_code": "tombstoned"}
        with self._transaction(organization) as sql:
            prior = sql.one("SELECT * FROM portfolio_outcome_retention_events WHERE organization_id=? AND actor_id=? AND idempotency_key=?",
                            (organization, principal.actor_id, idempotency_key))
            if prior is not None:
                try:
                    validate(prior["key_version"], "opaque_id")
                    validate(prior["request_mac"], "digest")
                except (PortfolioError, KeyError, TypeError):
                    raise PortfolioError("unavailable") from None
                expected = keys.metadata_digest(prior["key_version"], organization, "retention", idempotency_key, request)
                if not hmac.compare_digest(prior["request_mac"], expected):
                    raise PortfolioError("idempotency_conflict")
                return self._retention(prior)
            event = sql.one("SELECT * FROM portfolio_outcome_events WHERE organization_id=? AND connector_id=? AND source_event_id=?",
                            (organization, connector, source))
            if event is None:
                raise PortfolioError("not_found")
            self._public(self._event(event))
            now, identifier = sql.now(), uuid4().hex
            sequence = self._audit(sql, organization, "retention", source, "tombstoned", connector=connector, actor=principal.actor_id, now=now)
            row = {
                "organization_id": organization, "retention_event_id": identifier, "connector_id": connector,
                "source_event_id": source, "schema_version": 1, "actor_id": principal.actor_id,
                "idempotency_key": idempotency_key,
                "request_mac": keys.metadata_digest(keys.current_version, organization, "retention", idempotency_key, request),
                "key_version": keys.current_version, "reason_code": "tombstoned",
                "event_at": now, "observed_at": now, "ingested_at": now, "sequence": sequence,
            }
            result = self._retention(row)
            sql.insert("portfolio_outcome_retention_events", row)
            self._coverage(sql, organization, connector, event["source_delivery_id"], source, "excluded", "tombstoned", now, sequence)
        return result

    def coverage(self, principal, *, limit=100):
        """Bounded diagnostic window, not a complete population or metric API."""
        self._authorize(principal)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PortfolioError("invalid_request")
        with self._transaction(principal.organization_id) as sql:
            rows = sql.execute("SELECT * FROM portfolio_outcome_coverage_events WHERE organization_id=? "
                               "ORDER BY sequence DESC,coverage_event_id DESC LIMIT ?", (principal.organization_id, limit)).fetchall()
            result = [{"schema_id": "hormuz.outcome-coverage-event", **dict(row)} for row in rows]
            try:
                for item in result:
                    validate_coverage(item)
            except PortfolioError:
                raise PortfolioError("unavailable") from None
            self._audit(sql, principal.organization_id, "read_context", None, "observed", actor=principal.actor_id)
        return result
