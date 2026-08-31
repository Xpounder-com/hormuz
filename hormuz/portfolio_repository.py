"""Registry-owned SQL and transactions beside, never inside, the v1 ledger.

The two adapters share only these registry queries. PostgreSQL owns its tenant
transaction and organization advisory lock; SQLite owns BEGIN IMMEDIATE. The
lock covers version comparison, append, idempotency result, and audit together.
Reads use the same boundary to freeze a committed sequence and audit delivery.
No connection pool is owned or closed here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator, Mapping, Protocol
from urllib.parse import urlencode

from ._portfolio_sql import PortfolioSQL as _SQL, portfolio_transaction
from .config import GatewayConfig
from .attribution_repository import AttributionRepository
from .outcome_repository import OutcomeRepository
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError, RESPONSE_BYTES, canonical, query_parameters, route, validate
from .postgres import PostgresConnectionPool


class PortfolioRepository(Protocol):
    def execute(
        self, principal: PortfolioPrincipal, operation: str, *, path: str,
        scope_id: str | None, query: dict[str, Any], body: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> tuple[int, dict[str, Any]]: ...


class RegistryRepository:
    def __init__(self, config: GatewayConfig, *, environ: Mapping[str, str] | None = None,
                 connection_pool: PostgresConnectionPool | None = None, read_only: bool = False):
        self.config = config
        self._pool = connection_pool
        self._read_only = read_only
        environment = os.environ if environ is None else environ
        self._dsn = environment.get(config.usage_storage.postgres_dsn_env, "")

    def _authorize(self, principal: PortfolioPrincipal) -> None:
        config = self.config.portfolio_control
        if self._read_only or config is None or not isinstance(principal, PortfolioPrincipal):
            raise PortfolioError("forbidden")
        if not any(
            (binding.organization_id, binding.actor_id, binding.roles) ==
            (principal.organization_id, principal.actor_id, principal.roles)
            and "portfolio_admin" in binding.roles for binding in config.role_bindings
        ):
            raise PortfolioError("forbidden")

    @contextmanager
    def _transaction(self, principal: PortfolioPrincipal) -> Iterator[_SQL]:
        self._authorize(principal)  # Before opening a connection or lookup.
        with portfolio_transaction(
            self.config, principal.organization_id, dsn=self._dsn, connection_pool=self._pool,
        ) as sql:
            yield sql

    def execute(
        self, principal: PortfolioPrincipal, operation: str, *, path: str,
        scope_id: str | None, query: dict[str, Any], body: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> tuple[int, dict[str, Any]]:
        self._authorize(principal)
        if operation not in {"create_scope", "version_scope", "bind", "show_scope", "list_scopes", "list_bindings"}:
            raise PortfolioError("not_found")
        mutation = operation in {"create_scope", "version_scope", "bind"}
        if route("POST" if mutation else "GET", path) != (operation, scope_id):
            raise PortfolioError("invalid_request")
        validate(query, "hormuz.portfolio-query")
        query = query_parameters(urlencode(query), operation)
        if not mutation and body is not None:
            raise PortfolioError("invalid_request")
        if mutation:
            if not isinstance(idempotency_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", idempotency_key):
                raise PortfolioError("invalid_request")
            name = {"create_scope": "work-scope-create-request", "version_scope": "work-scope-version-request", "bind": "external-work-binding-request"}[operation]
            validate(body, "hormuz." + name)
            if operation == "bind":
                self._connector(principal, body["connector_id"], body["external_object_id"])
            elif body["owner_team_id"] is not None and not any(
                identity.organization_id == principal.organization_id and identity.team_id == body["owner_team_id"]
                for identity in (*self.config.identities_by_token.values(), *self.config.identities_by_subject.values())
            ):
                raise PortfolioError("invalid_request", "invalid_reference")
        if "connector_id" in query:
            self._connector(principal, query["connector_id"])
        with self._transaction(principal) as sql:
            if body is not None:
                result = self._mutate(sql, principal, operation, path, scope_id, body, idempotency_key)
            elif operation == "show_scope":
                record = self._scope(sql, principal.organization_id, scope_id, query.get("version"))
                result = self._scope_response(record)
                self._audit(sql, principal, operation, scope_id, record["version"], "observed")
            else:
                result = self._list(sql, principal, operation, query)
            try:
                validate(result, result["schema_id"])
                oversized = len(canonical(result).encode("utf-8")) > RESPONSE_BYTES
            except PortfolioError:
                # Invalid persisted state is not a caller input error. Refuse
                # delivery and roll back its audit/cursor in the same unit.
                raise PortfolioError("unavailable") from None
            if oversized:
                raise PortfolioError("unavailable")
        # The context manager has committed before any response leaves the owner.
        return (201 if body is not None else 200), result

    def _connector(self, principal, connector_id, external_object_id=None):
        for binding in self.config.portfolio_control.connectors:
            if (binding.organization_id, binding.connector_id) == (principal.organization_id, connector_id):
                if external_object_id is None or external_object_id in binding.external_object_ids:
                    return binding
        raise PortfolioError("forbidden")

    @staticmethod
    def _scope(sql, organization_id, scope_id, version=None):
        row = sql.one(
            "SELECT * FROM portfolio_work_scope_versions WHERE organization_id=? AND work_scope_id=?"
            + (" AND version=?" if version is not None else " ORDER BY version DESC LIMIT 1"),
            (organization_id, scope_id, version) if version is not None else (organization_id, scope_id),
        )
        if row is None:
            raise PortfolioError("not_found")
        return row

    @staticmethod
    def _sequence(sql, organization_id):
        return int(sql.one("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM portfolio_audit_events WHERE organization_id=?", (organization_id,))["sequence"])

    def _audit(self, sql, principal, operation, entity_id, version, reason, *, sequence=None, now=None):
        sequence = self._sequence(sql, principal.organization_id) + 1 if sequence is None else sequence
        now = sql.now() if now is None else now
        sql.insert("portfolio_audit_events", {
            "organization_id": principal.organization_id, "event_id": uuid.uuid4().hex,
            "sequence": sequence, "actor_id": principal.actor_id, "operation": operation,
            "entity_id": entity_id, "entity_version": version, "reason_code": reason, "occurred_at": now,
        })
        return sequence

    def _mutate(self, sql, principal, operation, path, scope_id, body, key):
        organization = principal.organization_id
        request_mac = hmac.new(key.encode("ascii"), canonical(body).encode("utf-8"), hashlib.sha256).hexdigest()
        previous = sql.one(
            "SELECT request_mac, work_scope_id, work_scope_version, binding_event_id FROM portfolio_idempotency WHERE organization_id=? "
            "AND actor_id=? AND method='POST' AND route=? AND idempotency_key=?",
            (organization, principal.actor_id, path, key),
        )
        if previous is not None:
            if not hmac.compare_digest(previous["request_mac"], request_mac):
                raise PortfolioError("idempotency_conflict")
            if previous["work_scope_id"] is not None:
                return self._scope_response(self._scope(sql, organization, previous["work_scope_id"], previous["work_scope_version"]))
            row = sql.one("SELECT * FROM portfolio_binding_events WHERE organization_id=? AND binding_event_id=?",
                          (organization, previous["binding_event_id"]))
            if row is None:
                raise PortfolioError("unavailable")
            return self._binding_response(row)
        sequence, now = self._sequence(sql, organization) + 1, sql.now()
        common = {"organization_id": organization, "actor_id": principal.actor_id,
                  "sequence": sequence, "event_at": now, "observed_at": now, "ingested_at": now}
        if operation in {"create_scope", "version_scope"}:
            previous_scope = None if operation == "create_scope" else self._scope(sql, organization, scope_id)
            if previous_scope is not None and previous_scope["version"] != body["expected_version"]:
                raise PortfolioError("version_conflict")
            if previous_scope is not None and previous_scope["version"] >= 2147483647:
                raise PortfolioError("version_conflict")
            kind = body["kind"] if previous_scope is None else previous_scope["kind"]
            state = "active" if previous_scope is None else body["state"]
            reason = "created" if previous_scope is None else body["reason_code"]
            parent_id = body["parent_work_scope_id"]
            parent = None
            if parent_id is not None:
                parent = self._scope(sql, organization, parent_id)
                if {"portfolio": 0, "initiative": 1, "use_case": 2}[parent["kind"]] >= {"portfolio": 0, "initiative": 1, "use_case": 2}[kind]:
                    raise PortfolioError("invalid_request", "invalid_reference")
                if state == "active" and parent["state"] != "active":
                    raise PortfolioError("invalid_request", "invalid_reference")
            if previous_scope is not None:
                self._lifecycle(previous_scope, body)
            row = {**common, "work_scope_id": uuid.uuid4().hex if previous_scope is None else scope_id,
                   "version": 1 if previous_scope is None else previous_scope["version"] + 1,
                   "kind": kind, "parent_work_scope_id": parent_id,
                   "parent_version": parent["version"] if parent else None,
                   "owner_team_id": body["owner_team_id"], "display_name": body["display_name"],
                   "state": state, "supersedes_version": previous_scope["version"] if previous_scope else None,
                   "reason_code": reason}
            self._audit(sql, principal, operation, row["work_scope_id"], row["version"], reason, sequence=sequence, now=now)
            sql.insert("portfolio_work_scope_versions", row)
            result = self._scope_response(row)
        else:
            work_scope = body["work_scope"]
            scope = self._scope(sql, organization, work_scope["work_scope_id"], work_scope["version"])
            latest = self._scope(sql, organization, work_scope["work_scope_id"])
            if body["state"] == "active" and (latest["state"] != "active" or scope["state"] != "active" or latest["version"] != scope["version"]):
                raise PortfolioError("version_conflict")
            prior = sql.one(
                "SELECT * FROM portfolio_binding_events WHERE organization_id=? AND connector_id=? "
                "AND external_object_id=? ORDER BY sequence DESC LIMIT 1",
                (organization, body["connector_id"], body["external_object_id"]),
            )
            if (prior["binding_event_id"] if prior else None) != body["expected_binding_event_id"]:
                raise PortfolioError("version_conflict")
            expected_reason = "tombstoned" if body["state"] == "tombstoned" else "corrected" if prior else "bound"
            if body["reason_code"] != expected_reason or (prior is None and body["state"] != "active"):
                raise PortfolioError("invalid_request")
            row = {**common, "binding_event_id": uuid.uuid4().hex, "connector_id": body["connector_id"],
                   "external_object_id": body["external_object_id"], "work_scope_id": scope["work_scope_id"],
                   "work_scope_version": scope["version"], "state": body["state"],
                   "supersedes_event_id": prior["binding_event_id"] if prior else None, "reason_code": body["reason_code"]}
            self._audit(sql, principal, operation, row["binding_event_id"], None, row["reason_code"], sequence=sequence, now=now)
            sql.insert("portfolio_binding_events", row)
            result = self._binding_response(row)
        sql.insert("portfolio_idempotency", {
            "organization_id": organization, "actor_id": principal.actor_id, "method": "POST", "route": path,
            "idempotency_key": key, "request_mac": request_mac, "sequence": sequence,
            "work_scope_id": result.get("work_scope_id"), "work_scope_version": result.get("version"),
            "binding_event_id": result.get("binding_event_id"),
        })
        return result

    @staticmethod
    def _lifecycle(previous, body):
        if previous["state"] == "tombstoned":
            raise PortfolioError("version_conflict")
        state, reason = body["state"], body["reason_code"]
        if state == "tombstoned":
            expected = "tombstoned"
        elif state != previous["state"]:
            expected = "archived" if state == "archived" else "reactivated"
        elif body["parent_work_scope_id"] != previous["parent_work_scope_id"]:
            expected = "reparented"
        else:
            expected = "corrected"
        if reason != expected or (body["display_name"] is None) != (state == "tombstoned"):
            raise PortfolioError("invalid_request")

    @staticmethod
    def _scope_response(row):
        fields = ("organization_id", "work_scope_id", "version", "kind", "owner_team_id", "display_name", "state",
                  "supersedes_version", "actor_id", "reason_code", "event_at", "observed_at", "ingested_at")
        return {"schema_id": "hormuz.work-scope-version", "schema_version": 1,
                **{key: row[key] for key in fields}, "parent": None if row["parent_work_scope_id"] is None else
                {"work_scope_id": row["parent_work_scope_id"], "version": row["parent_version"]}}

    @staticmethod
    def _binding_response(row):
        fields = ("organization_id", "binding_event_id", "connector_id", "external_object_id", "state",
                  "supersedes_event_id", "actor_id", "reason_code", "event_at", "observed_at", "ingested_at")
        return {"schema_id": "hormuz.external-work-binding-event", "schema_version": 1,
                **{key: row[key] for key in fields}, "work_scope":
                {"work_scope_id": row["work_scope_id"], "version": row["work_scope_version"]}}

    def _list(self, sql, principal, operation, query):
        organization = principal.organization_id
        now = sql.now()
        limit = query.get("limit", 50)
        cursor = None
        filters = {key: value for key, value in query.items() if key != "limit"}
        snapshot, as_of = self._sequence(sql, organization), now
        if "cursor" in query:
            cursor = sql.one("SELECT * FROM portfolio_cursors WHERE organization_id=? AND cursor_id=?", (organization, query["cursor"]))
            if cursor is None or cursor["actor_id"] != principal.actor_id or cursor["authority_json"] != principal.cursor_authority or cursor["operation"] != operation:
                raise PortfolioError("cursor_invalid")
            # Cursors are durable, actor/role-bound server state, not signed SQL
            # offsets. They survive process replacement without a secret key.
            if (datetime.fromisoformat(now) - datetime.fromisoformat(cursor["as_of"])).total_seconds() > 3600:
                raise PortfolioError("cursor_invalid")
            snapshot, as_of = cursor["snapshot_sequence"], cursor["as_of"]
            filters = json.loads(cursor["filters_json"])
            if "connector_id" in filters:
                self._connector(principal, filters["connector_id"])
        scope_page = operation == "list_scopes"
        table = "portfolio_work_scope_versions" if scope_page else "portfolio_binding_events"
        id_column = "work_scope_id" if scope_page else "binding_event_id"
        where, values = ["r.organization_id=?", "r.sequence<=?"], [organization, snapshot]
        if scope_page:
            where.append("NOT EXISTS (SELECT 1 FROM portfolio_work_scope_versions n WHERE n.organization_id=r.organization_id "
                         "AND n.work_scope_id=r.work_scope_id AND n.version>r.version AND n.sequence<=?)")
            values.append(snapshot)
        for field in ("work_scope_id", "connector_id"):
            if field in filters:
                where.append(f"r.{field}=?")
                values.append(filters[field])
        for field, comparator in (("start_at", ">="), ("end_at", "<")):
            if field in filters:
                where.append(f"r.event_at{comparator}?")
                values.append(datetime.fromisoformat(filters[field]).isoformat(timespec="microseconds").replace("+00:00", "Z"))
        if cursor:
            where.append(f"(r.event_at<? OR (r.event_at=? AND r.{id_column}<?))")
            values.extend((cursor["after_at"], cursor["after_at"], cursor["after_id"]))
        rows = sql.execute(f"SELECT r.* FROM {table} r WHERE {' AND '.join(where)} "
                           f"ORDER BY r.event_at DESC, r.{id_column} DESC LIMIT ?", (*values, limit + 1)).fetchall()
        selected = [dict(row) for row in rows[:limit]]
        more, next_cursor = len(rows) > limit, None
        if more:
            next_cursor = uuid.uuid4().hex + uuid.uuid4().hex
            last = selected[-1]
            sql.insert("portfolio_cursors", {
                "organization_id": organization, "cursor_id": next_cursor, "actor_id": principal.actor_id,
                "authority_json": principal.cursor_authority, "operation": operation, "as_of": as_of,
                "snapshot_sequence": snapshot, "after_at": last["event_at"], "after_id": last[id_column],
                "filters_json": canonical(filters),
            })
        self._audit(sql, principal, operation, None, None, "observed", now=now)
        return {"schema_id": "hormuz.work-scope-page" if scope_page else "hormuz.external-work-binding-page",
                "schema_version": 1, "organization_id": organization,
                "items": [(self._scope_response(row) if scope_page else self._binding_response(row)) for row in selected],
                "as_of": as_of, "has_more": more, "next_cursor": next_cursor}


@dataclass
class PortfolioRepositories:
    registry: RegistryRepository
    attributions: AttributionRepository
    outcomes: OutcomeRepository | None = None

    def execute(self, principal: PortfolioPrincipal, operation: str, *, path: str,
                scope_id: str | None, query: dict[str, Any], body: dict[str, Any] | None,
                idempotency_key: str | None) -> tuple[int, dict[str, Any]]:
        owner = self.outcomes if operation == "list_outcomes" else self.attributions if operation in {"attribute", "list_attributions"} else self.registry
        if owner is None:
            raise PortfolioError("not_found")
        return owner.execute(principal, operation, path=path, scope_id=scope_id, query=query,
                             body=body, idempotency_key=idempotency_key)


def create_portfolio_repository(config: GatewayConfig, *, environ: Mapping[str, str] | None = None,
                                connection_pool: PostgresConnectionPool | None = None,
                                read_only: bool = False) -> PortfolioRepositories:
    registry = RegistryRepository(config, environ=environ, connection_pool=connection_pool, read_only=read_only)
    return PortfolioRepositories(registry, AttributionRepository(
        config, dsn=registry._dsn, connection_pool=connection_pool, read_only=read_only,
    ), OutcomeRepository(
        config, dsn=registry._dsn, connection_pool=connection_pool, read_only=read_only,
    ))
