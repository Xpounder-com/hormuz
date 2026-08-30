"""Append-only attribution beside the immutable v1 attempt/usage ledger.

Admission uses operator identity/client grants, not administrator roles.
Corrections and reads use administrator authority. Both share the registry
organization lock, but neither opens a transaction across the v1 reservation.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import urlencode

from ._attribution_schema import TABLE_DDL
from ._portfolio_sql import portfolio_transaction
from .attribution_admission import Admission, AdmissionError, select_admission
from .config import GatewayConfig, Identity
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import ATTRIBUTIONS, PortfolioError, RESPONSE_BYTES, canonical, query_parameters, route, validate


def _utc(value: str | datetime) -> str:
    instant = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class AttributionRepository:
    def __init__(self, config: GatewayConfig, *, dsn: str, connection_pool=None, read_only=False):
        self.config, self._dsn, self._pool, self._read_only = config, dsn, connection_pool, read_only

    @contextmanager
    def _transaction(self, organization_id):
        with portfolio_transaction(self.config, organization_id, dsn=self._dsn, connection_pool=self._pool, tables=TABLE_DDL) as sql:
            yield sql

    def _authorize(self, principal):
        control = self.config.portfolio_control
        if self._read_only or control is None or not isinstance(principal, PortfolioPrincipal) or not any(
            (binding.organization_id, binding.actor_id, binding.roles) ==
            (principal.organization_id, principal.actor_id, principal.roles)
            and "portfolio_admin" in binding.roles for binding in control.role_bindings
        ):
            raise PortfolioError("forbidden")

    def _identity(self, identity, client, protocol):
        if self._read_only or not isinstance(identity, Identity) or (client, protocol) not in {("codex", "openai"), ("claude-code", "anthropic")}:
            raise AdmissionError("unauthorized_scope", 403)
        if not any((known.organization_id, known.actor_id) == (identity.organization_id, identity.actor_id)
                   for known in (*self.config.identities_by_token.values(), *self.config.identities_by_subject.values())):
            raise AdmissionError("unauthorized_scope", 403)
        for value in (identity.organization_id, identity.actor_id):
            try:
                validate(value, "opaque_id")
            except PortfolioError:
                raise AdmissionError("unsupported", 403) from None

    def _grant(self, identity, client, protocol, admission):
        self._identity(identity, client, protocol)
        if not isinstance(admission, Admission):
            raise AdmissionError("unauthorized_scope", 403)
        headers = []
        if admission.confidence == "explicit_authorized" and admission.work_scope is not None:
            headers = [f"v1;work_scope_id={admission.work_scope.work_scope_id};version={admission.work_scope.version}"]
        if select_admission(self.config, identity, client, headers, account_usage=True) != admission:
            raise AdmissionError("unauthorized_scope", 403)

    @staticmethod
    def _scope(sql, organization, reference):
        if reference is None:
            return
        latest = sql.one("SELECT version, state, kind FROM portfolio_work_scope_versions "
                         "WHERE organization_id=? AND work_scope_id=? ORDER BY version DESC LIMIT 1",
                         (organization, reference.work_scope_id))
        if latest is None or latest["kind"] != "use_case":
            raise AdmissionError("invalid_reference")
        if latest["version"] != reference.version or latest["state"] != "active":
            raise AdmissionError("stale_version", 409)

    @staticmethod
    def _sequence(sql, organization):
        return int(sql.one("SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_attribution_audit_events WHERE organization_id=?", (organization,))["sequence"])

    def _audit(self, sql, organization, actor, operation, entity, reason, *, now=None):
        sequence = self._sequence(sql, organization) + 1
        sql.insert("portfolio_attribution_audit_events", {
            "organization_id": organization, "event_id": uuid.uuid4().hex, "sequence": sequence,
            "actor_id": actor, "operation": operation, "entity_id": entity, "reason_code": reason,
            "occurred_at": now or sql.now(),
        })
        return sequence

    @staticmethod
    def _attempt(sql, organization, attempt_id, *, admission_authority=None):
        # Singular tenant-qualified joins through immutable IDs. Actual model
        # and cost come only from the linked usage event, never an alias.
        where, values = "a.organization_id=? AND a.attempt_id=?", [organization, attempt_id]
        if admission_authority is not None:
            where += " AND a.actor_id=? AND a.team_id=? AND a.client=? AND a.protocol=?"
            values.extend(admission_authority)
        result = sql.one("SELECT a.attempt_id, a.organization_id, a.created_at, a.actor_id, a.team_id, "
                         "a.client, a.protocol, a.policy_version, a.requested_model, a.resolved_alias, a.upstream_model, "
                         "e.id AS attempt_event_id, e.state, e.usage_event_id, u.provider_reported_model, "
                         "u.cost_microusd, u.cost_basis, u.allocation_basis "
                         "FROM gateway_request_attempts a LEFT JOIN gateway_request_attempt_events e "
                         "ON e.organization_id=a.organization_id AND e.attempt_id=a.attempt_id "
                         "AND e.sequence=(SELECT MAX(n.sequence) FROM gateway_request_attempt_events n "
                         "WHERE n.organization_id=a.organization_id AND n.attempt_id=a.attempt_id) "
                         "LEFT JOIN gateway_usage_events u ON u.organization_id=a.organization_id AND u.id=e.usage_event_id "
                         "WHERE " + where, tuple(values))
        if result is None:
            if admission_authority is not None:
                raise AdmissionError("unauthorized_scope", 403)
            raise PortfolioError("not_found")
        result["created_at"] = _utc(result["created_at"])
        return result

    @staticmethod
    def _latest(sql, organization, attempt_id):
        return sql.one("SELECT * FROM portfolio_attribution_events WHERE organization_id=? AND request_attempt_id=? ORDER BY sequence DESC LIMIT 1",
                       (organization, attempt_id))

    @staticmethod
    def _event(row):
        fields = ("organization_id", "attribution_event_id", "request_attempt_id", "confidence", "state",
                  "supersedes_event_id", "actor_id", "reason_code", "event_at", "observed_at", "ingested_at")
        return {"schema_id": "hormuz.governed-run-attribution-event", "schema_version": 1,
                **{key: row[key] for key in fields}, "work_scope": None if row["work_scope_id"] is None else
                {"work_scope_id": row["work_scope_id"], "version": row["work_scope_version"]}}

    @staticmethod
    def _validated(result):
        try:
            validate(result, result["schema_id"])
            if len(canonical(result).encode("utf-8")) > RESPONSE_BYTES:
                raise PortfolioError("unavailable")
        except (PortfolioError, KeyError, TypeError):
            raise PortfolioError("unavailable") from None
        return result

    def preflight(self, identity, client, protocol, admission):
        self._grant(identity, client, protocol, admission)  # Before any lookup.
        try:
            with self._transaction(identity.organization_id) as sql:
                self._scope(sql, identity.organization_id, admission.work_scope)
        except PortfolioError:
            raise AdmissionError("dependency_unavailable", 503) from None

    def admit(self, identity, client, protocol, admission, attempt_id):
        self._grant(identity, client, protocol, admission)
        validate(attempt_id, "opaque_id")
        organization = identity.organization_id
        try:
            with self._transaction(organization) as sql:
                facts = self._attempt(sql, organization, attempt_id, admission_authority=(
                    identity.actor_id, identity.team_id, client, protocol,
                ))
                existing = sql.one("SELECT * FROM portfolio_attribution_events WHERE organization_id=? "
                                   "AND request_attempt_id=? AND supersedes_event_id IS NULL", (organization, attempt_id))
                reference = admission.work_scope
                if existing is not None:
                    if (existing["confidence"], existing["reason_code"], existing["work_scope_id"], existing["work_scope_version"]) != (
                        admission.confidence, admission.reason, reference.work_scope_id if reference else None, reference.version if reference else None,
                    ):
                        raise AdmissionError("ambiguous", 409)
                    result = self._validated(self._event(existing))
                else:
                    if facts["state"] != "pending":
                        raise AdmissionError("invalid_reference", 409)
                    # Recheck under the exact lock used by registry mutations.
                    self._scope(sql, organization, reference)
                    now, event_id = sql.now(), uuid.uuid4().hex
                    sequence = self._audit(sql, organization, None, "admit", event_id, admission.reason, now=now)
                    row = {"organization_id": organization, "attribution_event_id": event_id, "request_attempt_id": attempt_id,
                           "work_scope_id": reference.work_scope_id if reference else None, "work_scope_version": reference.version if reference else None,
                           "confidence": admission.confidence, "state": "active", "supersedes_event_id": None, "actor_id": None,
                           "reason_code": admission.reason, "event_at": _utc(facts["created_at"]), "observed_at": now, "ingested_at": now, "sequence": sequence}
                    sql.insert("portfolio_attribution_events", row)
                    result = self._validated(self._event(row))
            return result
        except PortfolioError:
            raise AdmissionError("dependency_unavailable", 503) from None

    def record_rejection(self, identity, client, protocol, error):
        self._identity(identity, client, protocol)
        if not isinstance(error, AdmissionError) or error.reason == "bound":
            raise AdmissionError("unsupported")
        try:
            with self._transaction(identity.organization_id) as sql:
                now, receipt = sql.now(), uuid.uuid4().hex
                sequence = self._audit(sql, identity.organization_id, identity.actor_id, "reject_admission", receipt, error.reason, now=now)
                sql.insert("portfolio_attribution_rejections", {
                    "organization_id": identity.organization_id, "receipt_id": receipt, "actor_id": identity.actor_id,
                    "client": client, "protocol": protocol, "result_status": error.result_status,
                    "reason_code": error.reason, "occurred_at": now, "sequence": sequence,
                })
        except PortfolioError:
            raise AdmissionError("dependency_unavailable", 503) from None

    def execute(self, principal, operation, *, path, scope_id, query, body, idempotency_key):
        self._authorize(principal)
        mutation = operation == "attribute"
        if operation not in {"attribute", "list_attributions"}:
            raise PortfolioError("not_found")
        if route("POST" if mutation else "GET", path) != (operation, scope_id) or path != ATTRIBUTIONS:
            raise PortfolioError("invalid_request")
        validate(query, "hormuz.portfolio-query")
        query = query_parameters(urlencode(query), operation)
        if mutation:
            validate(body, "hormuz.governed-run-attribution-request")
            if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key):
                raise PortfolioError("invalid_request")
        elif body is not None:
            raise PortfolioError("invalid_request")
        with self._transaction(principal.organization_id) as sql:
            result = self._correct(sql, principal, body, idempotency_key) if mutation else self._list(sql, principal, query)
            self._validated(result)
        return (201 if mutation else 200), result

    def _correct(self, sql, principal, body, key):
        from .attribution_config import WorkScopeRef

        organization = principal.organization_id
        mac = hmac.new(key.encode("ascii"), canonical(body).encode("utf-8"), hashlib.sha256).hexdigest()
        prior_key = sql.one("SELECT request_mac, attribution_event_id FROM portfolio_attribution_idempotency "
                            "WHERE organization_id=? AND actor_id=? AND idempotency_key=?", (organization, principal.actor_id, key))
        if prior_key is not None:
            if not hmac.compare_digest(prior_key["request_mac"], mac):
                raise PortfolioError("idempotency_conflict")
            event = sql.one("SELECT * FROM portfolio_attribution_events WHERE organization_id=? AND attribution_event_id=?", (organization, prior_key["attribution_event_id"]))
            if event is None:
                raise PortfolioError("unavailable")
            return self._event(event)
        facts = self._attempt(sql, organization, body["request_attempt_id"])
        if facts["state"] in {None, "pending"}:
            raise PortfolioError("version_conflict")
        prior = self._latest(sql, organization, body["request_attempt_id"])
        if (prior["attribution_event_id"] if prior else None) != body["expected_attribution_event_id"]:
            raise PortfolioError("version_conflict")
        expected_reason = "voided" if body["state"] == "voided" else "corrected" if prior else "bound"
        if body["reason_code"] != expected_reason or (body["state"] == "voided" and prior is None):
            raise PortfolioError("invalid_request")
        reference = body["work_scope"]
        if (body["state"] == "voided") != (reference is None):
            raise PortfolioError("invalid_request")
        if reference is not None:
            try:
                self._scope(sql, organization, WorkScopeRef(**reference))
            except AdmissionError as error:
                raise PortfolioError("version_conflict" if error.reason == "stale_version" else "invalid_request", error.reason) from None
        now, event_id = sql.now(), uuid.uuid4().hex
        sequence = self._audit(sql, organization, principal.actor_id, "correct", event_id, expected_reason, now=now)
        row = {"organization_id": organization, "attribution_event_id": event_id, "request_attempt_id": body["request_attempt_id"],
               "work_scope_id": reference["work_scope_id"] if reference else None, "work_scope_version": reference["version"] if reference else None,
               "confidence": "authorized_post_run", "state": body["state"], "supersedes_event_id": prior["attribution_event_id"] if prior else None,
               "actor_id": principal.actor_id, "reason_code": expected_reason, "event_at": now, "observed_at": now, "ingested_at": now, "sequence": sequence}
        sql.insert("portfolio_attribution_events", row)
        sql.insert("portfolio_attribution_idempotency", {"organization_id": organization, "actor_id": principal.actor_id,
                   "idempotency_key": key, "request_mac": mac, "attribution_event_id": event_id})
        return self._event(row)

    def _list(self, sql, principal, query):
        organization, now, cursor = principal.organization_id, sql.now(), None
        snapshot, as_of = self._sequence(sql, organization), now
        filters = {key: value for key, value in query.items() if key != "limit"}
        if "cursor" in query:
            cursor = sql.one("SELECT * FROM portfolio_attribution_cursors WHERE organization_id=? AND cursor_id=?", (organization, query["cursor"]))
            if cursor is None or (cursor["actor_id"], cursor["authority_json"]) != (principal.actor_id, principal.cursor_authority):
                raise PortfolioError("cursor_invalid")
            try:
                validate(cursor["as_of"], "timestamp")
                validate(cursor["after_at"], "timestamp")
                validate(cursor["after_id"], "opaque_id")
                if not isinstance(cursor["filters_json"], str) or len(cursor["filters_json"]) > 4096:
                    raise PortfolioError("unavailable")
                filters = json.loads(cursor["filters_json"])
                validate(filters, "hormuz.portfolio-query")
                if (type(cursor["snapshot_sequence"]) is not int or cursor["snapshot_sequence"] < 0
                        or set(filters) - {"work_scope_id", "start_at", "end_at"}
                        or canonical(filters) != cursor["filters_json"]):
                    raise PortfolioError("unavailable")
                filters = query_parameters(urlencode(filters), "list_attributions")
            except (PortfolioError, ValueError, TypeError, RecursionError):
                raise PortfolioError("unavailable") from None
            if (datetime.fromisoformat(now) - datetime.fromisoformat(cursor["as_of"])).total_seconds() > 3600:
                raise PortfolioError("cursor_invalid")
            snapshot, as_of = cursor["snapshot_sequence"], cursor["as_of"]
        where, values = ["organization_id=?", "sequence<=?"], [organization, snapshot]
        if "work_scope_id" in filters:
            where.append("work_scope_id=?")
            values.append(filters["work_scope_id"])
        for field, comparator in (("start_at", ">="), ("end_at", "<")):
            if field in filters:
                where.append(f"event_at{comparator}?")
                values.append(_utc(filters[field]))
        if cursor:
            where.append("(event_at<? OR (event_at=? AND attribution_event_id<?))")
            values.extend((cursor["after_at"], cursor["after_at"], cursor["after_id"]))
        limit = query.get("limit", 50)
        rows = [dict(row) for row in sql.execute("SELECT * FROM portfolio_attribution_events WHERE " + " AND ".join(where)
                + " ORDER BY event_at DESC, attribution_event_id DESC LIMIT ?", (*values, limit + 1)).fetchall()]
        selected, more, next_cursor = rows[:limit], len(rows) > limit, None
        if more:
            last, next_cursor = selected[-1], uuid.uuid4().hex + uuid.uuid4().hex
            sql.insert("portfolio_attribution_cursors", {"organization_id": organization, "cursor_id": next_cursor,
                       "actor_id": principal.actor_id, "authority_json": principal.cursor_authority, "as_of": as_of,
                       "snapshot_sequence": snapshot, "after_at": last["event_at"], "after_id": last["attribution_event_id"], "filters_json": canonical(filters)})
        self._audit(sql, organization, principal.actor_id, "list_attributions", None, "observed", now=now)
        return {"schema_id": "hormuz.governed-run-attribution-page", "schema_version": 1, "organization_id": organization,
                "items": [self._event(row) for row in selected], "as_of": as_of, "has_more": more, "next_cursor": next_cursor}

    def attempt_facts(self, principal, attempt_id):
        self._authorize(principal)
        validate(attempt_id, "opaque_id")
        with self._transaction(principal.organization_id) as sql:
            facts = self._attempt(sql, principal.organization_id, attempt_id)
            latest = self._latest(sql, principal.organization_id, attempt_id)
            facts["attribution"] = self._validated(self._event(latest)) if latest else None
            self._audit(sql, principal.organization_id, principal.actor_id, "read_facts", attempt_id, "observed")
        return facts

    def rejection_counts(self, principal):
        self._authorize(principal)
        with self._transaction(principal.organization_id) as sql:
            rows = [dict(row) for row in sql.execute("SELECT result_status, reason_code, count(*) AS receipts "
                    "FROM portfolio_attribution_rejections WHERE organization_id=? GROUP BY result_status, reason_code ORDER BY result_status, reason_code",
                    (principal.organization_id,)).fetchall()]
            self._audit(sql, principal.organization_id, principal.actor_id, "read_facts", None, "observed")
        return rows
