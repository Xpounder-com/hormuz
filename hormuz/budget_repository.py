"""Authorized work-budget plan control and analytics-first projections.

Plan versions and activation facts are immutable.  Only the active pointer is
advanced, by compare-and-set, under the same tenant budget lock used by gateway
reservations.  This owner exposes internal records for #217; it does not add a
public HTTP route or broaden viewer roles.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import os
import re
from typing import Any, Mapping
from uuid import uuid4

from ._budget_schema import (
    ACTIVE_TABLE,
    MAX_ACTIVE_BUDGET_PLANS,
    MAX_BUDGET_ACTIVATIONS_PER_PLAN,
    TABLE_DDL,
    BudgetIntegrityError,
    BudgetPlanIntegrityError,
    budget_amount_text,
    validate_active_budget_rows,
    validate_budget_activation_row,
    validate_budget_plan_row,
    validate_budget_pointer_row,
)
from ._portfolio_sql import portfolio_transaction
from .config import GatewayConfig, Identity
from .finance_values import FinanceValueError, currency_code, decimal_text, exact_context
from .policy_document import PolicySnapshot, local_policy_content_sha256
from .policy_runtime import PolicyRuntime
from .policy_scenarios import PolicyScenarioSuite
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import PortfolioError, canonical
from .postgres import PostgresConnectionPool


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ERRORS = frozenset({"forbidden", "invalid_request", "version_conflict", "not_found", "unavailable"})
_PLAN_FIELDS = frozenset({
    "schema_id", "schema_version", "budget_plan_id", "expected_version", "work_scope",
    "window", "currency", "amount", "allowed_models", "output_token_cap",
    "per_request_cost_cap", "reason_code",
})
_ACTIVATION_FIELDS = frozenset({
    "schema_id", "schema_version", "version", "expected_active_version",
    "expected_activation_generation", "reason_code",
})
_CEILING_CLASSES = (
    "organization", "team", "actor", "application", "policy", "portfolio", "initiative", "use_case",
)
_RESTRICTION_ORDER = (
    "budget_ceiling", "model_intersection", "output_token_ceiling", "request_cost_ceiling",
    "attribution_invalid", "missing_evidence", "policy_drift", "plan_drift",
)
_VALUATION_RULE_ID = "gateway-route-microusd-v1"
_VALUATION_RULE_DIGEST = hashlib.sha256(_VALUATION_RULE_ID.encode()).hexdigest()
_COVERAGE_RULE_ID = "bound-governed-attempts-v1"
_COVERAGE_RULE_DIGEST = hashlib.sha256(_COVERAGE_RULE_ID.encode()).hexdigest()
_FORECAST_RULE_ID = "linear-committed-projection-v1"
_FORECAST_RULE_DIGEST = hashlib.sha256(_FORECAST_RULE_ID.encode()).hexdigest()
_MAX_REPORT_ATTEMPTS = 10_000
_MAX_SAFE_COUNT = 9007199254740991
_KIND_ORDER = {"portfolio": 0, "initiative": 1, "use_case": 2}


class BudgetRepositoryError(RuntimeError):
    """A fixed, content-free budget control failure."""

    def __init__(self, code: str):
        self.code = code if code in _ERRORS else "unavailable"
        super().__init__(self.code)


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise BudgetRepositoryError("invalid_request")
    return value


def _timestamp(value: object) -> str:
    if type(value) is not str or _TIME.fullmatch(value) is None:
        raise BudgetRepositoryError("invalid_request")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise BudgetRepositoryError("invalid_request") from None
    if parsed.tzinfo != timezone.utc:
        raise BudgetRepositoryError("invalid_request")
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _version(value: object, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or not 1 <= value <= 2147483647:
        raise BudgetRepositoryError("invalid_request")
    return value


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE_COUNT:
        raise BudgetRepositoryError("invalid_request")
    return value


def _stored_count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_SAFE_COUNT:
        raise BudgetRepositoryError("unavailable")
    return value


def _amount(value: object) -> str:
    try:
        result = budget_amount_text(value)
    except FinanceValueError:
        raise BudgetRepositoryError("invalid_request") from None
    return result


def _models(value: object) -> tuple[dict[str, str | None], ...] | None:
    if value is None:
        return None
    if type(value) is not list or len(value) > 100:
        raise BudgetRepositoryError("invalid_request")
    normalized = []
    for item in value:
        if type(item) is not dict or set(item) != {"provider_id", "model_id", "model_version"}:
            raise BudgetRepositoryError("invalid_request")
        provider_id, model_id = _identifier(item["provider_id"]), _identifier(item["model_id"])
        model_version = None if item["model_version"] is None else _identifier(item["model_version"])
        normalized.append({
            "provider_id": provider_id, "model_id": model_id, "model_version": model_version,
        })
    identities = [canonical(item) for item in normalized]
    if len(identities) != len(set(identities)):
        raise BudgetRepositoryError("invalid_request")
    return tuple(sorted(normalized, key=canonical))


def _version_ref(identifier: str, version: int, digest: str) -> dict[str, object]:
    return {"id": identifier, "version": version, "content_digest": digest}


def _rule(identifier: str, digest: str) -> dict[str, object]:
    return _version_ref(identifier, 1, digest)


def _microusd(value: int) -> str:
    if type(value) is not int or value < 0:
        raise BudgetRepositoryError("unavailable")
    with exact_context():
        return decimal_text(Decimal(value) / Decimal(1_000_000))


def _sum(values: list[str]) -> str:
    with exact_context():
        return decimal_text(sum((Decimal(value) for value in values), Decimal(0)))


def _signed(value: Decimal) -> str:
    if not value.is_finite():
        raise BudgetRepositoryError("unavailable")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


class WorkBudgetRepository:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        dsn: str = "",
        environ: Mapping[str, str] | None = None,
        connection_pool: PostgresConnectionPool | None = None,
        read_only: bool = False,
    ):
        self.config = config
        self._dsn = dsn
        self._environment = os.environ if environ is None else environ
        self._pool = connection_pool
        self._read_only = read_only

    def _authorize(self, principal: PortfolioPrincipal) -> None:
        control = self.config.portfolio_control
        if (
            self._read_only
            or control is None
            or type(principal) is not PortfolioPrincipal
            or principal.organization_id not in self.config.organization_ids
            or not any(
                (binding.organization_id, binding.actor_id, binding.roles)
                == (principal.organization_id, principal.actor_id, principal.roles)
                and "portfolio_admin" in binding.roles
                for binding in control.role_bindings
            )
        ):
            raise BudgetRepositoryError("forbidden")

    @contextmanager
    def _transaction(self, principal: PortfolioPrincipal):
        self._authorize(principal)
        try:
            with portfolio_transaction(
                self.config,
                principal.organization_id,
                dsn=self._dsn,
                connection_pool=self._pool,
                tables=TABLE_DDL,
                statement_timeout_ms=5000,
                budget_lock=True,
                mutable_tables=frozenset({ACTIVE_TABLE}),
            ) as sql:
                self._authorize(principal)
                yield sql
                self._authorize(principal)
        except BudgetRepositoryError:
            raise
        except PortfolioError:
            raise BudgetRepositoryError("unavailable") from None

    def _identity(self, principal: PortfolioPrincipal) -> Identity:
        candidates = (*self.config.identities_by_token.values(), *self.config.identities_by_subject.values())
        for identity in candidates:
            if (identity.organization_id, identity.actor_id) == (principal.organization_id, principal.actor_id):
                return identity
        raise BudgetRepositoryError("forbidden")

    def _policy(self, principal: PortfolioPrincipal) -> tuple[PolicySnapshot, dict[str, str]]:
        return self._policy_for_identity(self._identity(principal))

    def _policy_for_identity(self, identity: Identity) -> tuple[PolicySnapshot, dict[str, str]]:
        try:
            snapshot = PolicyRuntime(
                self.config, environ=self._environment, connection_pool=self._pool,
            ).snapshot_for(identity)
        except Exception as error:
            if isinstance(error, BudgetRepositoryError):
                raise
            raise BudgetRepositoryError("unavailable") from None
        digest = snapshot.content_sha256 or local_policy_content_sha256(self.config)
        if _DIGEST.fullmatch(digest) is None or _ID.fullmatch(snapshot.policy_version) is None:
            raise BudgetRepositoryError("unavailable")
        return snapshot, {"version": snapshot.policy_version, "content_digest": digest}

    @staticmethod
    def _scope(sql, organization: str, work_scope: Mapping[str, object]) -> dict[str, Any]:
        if type(work_scope) is not dict or set(work_scope) != {"work_scope_id", "version"}:
            raise BudgetRepositoryError("invalid_request")
        scope_id = _identifier(work_scope["work_scope_id"])
        version = _version(work_scope["version"])
        row = sql.one(
            "SELECT * FROM portfolio_work_scope_versions WHERE organization_id=? "
            "AND work_scope_id=? ORDER BY version DESC LIMIT 1",
            (organization, scope_id),
        )
        if row is None or row["version"] != version or row["state"] != "active":
            raise BudgetRepositoryError("version_conflict")
        return row

    @staticmethod
    def _scope_chain(sql, organization: str,
                     scope: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        chain: list[dict[str, Any]] = []
        current = dict(scope)
        seen: set[tuple[str, int]] = set()
        for _ in range(3):
            scope_id = current.get("work_scope_id")
            scope_version = current.get("version")
            kind = current.get("kind")
            if (
                type(scope_id) is not str
                or type(scope_version) is not int
                or kind not in _KIND_ORDER
                or (
                    chain
                    and _KIND_ORDER[kind] >= _KIND_ORDER[chain[-1]["kind"]]
                )
            ):
                raise BudgetRepositoryError("unavailable")
            reference = (scope_id, scope_version)
            if reference in seen:
                raise BudgetRepositoryError("unavailable")
            seen.add(reference)
            chain.append(current)
            parent_id = current.get("parent_work_scope_id")
            parent_version = current.get("parent_version")
            if parent_id is None:
                if parent_version is not None:
                    raise BudgetRepositoryError("unavailable")
                return tuple(reversed(chain))
            if type(parent_id) is not str or type(parent_version) is not int:
                raise BudgetRepositoryError("unavailable")
            parent = sql.one(
                "SELECT * FROM portfolio_work_scope_versions "
                "WHERE organization_id=? AND work_scope_id=? "
                "ORDER BY version DESC LIMIT 1",
                (organization, parent_id),
            )
            if (
                parent is None
                or parent["version"] != parent_version
                or parent["state"] != "active"
            ):
                raise BudgetRepositoryError("version_conflict")
            current = parent
        raise BudgetRepositoryError("unavailable")

    @staticmethod
    def _preview_plans(sql, organization: str, candidate: Mapping[str, Any],
                       scope_chain: tuple[dict[str, Any], ...],
                       observed_at: str) -> tuple[dict[str, Any], ...]:
        pointers = sql.execute(
            "SELECT budget_plan_id FROM portfolio_work_budget_active_plans "
            "WHERE organization_id=? ORDER BY budget_plan_id LIMIT ?",
            (organization, MAX_ACTIVE_BUDGET_PLANS + 1),
        ).fetchall()
        if len(pointers) > MAX_ACTIVE_BUDGET_PLANS:
            raise BudgetRepositoryError("unavailable")
        scope_refs = {
            (row["work_scope_id"], row["version"])
            for row in scope_chain
        }
        kind_by_scope = {
            (row["work_scope_id"], row["version"]): row["kind"]
            for row in scope_chain
        }
        plans = [dict(candidate)]
        for pointer_row in pointers:
            plan_id = dict(pointer_row).get("budget_plan_id")
            if type(plan_id) is not str or _ID.fullmatch(plan_id) is None:
                raise BudgetRepositoryError("unavailable")
            if plan_id == candidate["budget_plan_id"]:
                continue
            active = WorkBudgetRepository._active_budget(
                sql, organization, plan_id, observed_at,
            )
            if active is None:
                raise BudgetRepositoryError("unavailable")
            plan, activation = active[0], active[1]
            if (
                max(plan["window_start_at"], activation["committed_at"])
                <= observed_at < plan["window_end_at"]
                and (plan["work_scope_id"], plan["work_scope_version"])
                in scope_refs
            ):
                plans.append(plan)
        return tuple(sorted(plans, key=lambda row: (
            _KIND_ORDER[kind_by_scope[(
                row["work_scope_id"], row["work_scope_version"],
            )]],
            row["budget_plan_id"],
        )))

    @staticmethod
    def _plan(sql, organization: str, plan_id: str, version: int | None = None) -> dict[str, Any] | None:
        statement = (
            "SELECT * FROM portfolio_work_budget_plan_versions WHERE organization_id=? AND budget_plan_id=?"
            + (" AND version=?" if version is not None else " ORDER BY version DESC LIMIT 1")
        )
        values = (organization, plan_id, version) if version is not None else (organization, plan_id)
        row = sql.one(statement, values)
        return None if row is None else WorkBudgetRepository._verified_plan(row)

    @staticmethod
    def _verified_plan(row: Mapping[str, Any]) -> dict[str, Any]:
        """Refuse malformed persisted facts instead of reflecting or repairing them."""

        try:
            return validate_budget_plan_row(row)
        except BudgetPlanIntegrityError:
            raise BudgetRepositoryError("unavailable") from None

    @staticmethod
    def _pointer(sql, organization: str, plan_id: str) -> dict[str, Any] | None:
        row = sql.one(
            "SELECT * FROM portfolio_work_budget_active_plans WHERE organization_id=? AND budget_plan_id=?",
            (organization, plan_id),
        )
        if row is None:
            return None
        try:
            return validate_budget_pointer_row(row)
        except BudgetIntegrityError:
            raise BudgetRepositoryError("unavailable") from None

    @staticmethod
    def _active_budget(sql, organization: str, plan_id: str,
                       observed_at: str) -> tuple[
                           dict[str, Any], dict[str, Any], dict[str, Any],
                           tuple[tuple[dict[str, Any], dict[str, Any]], ...],
                       ] | None:
        pointer = WorkBudgetRepository._pointer(sql, organization, plan_id)
        history = WorkBudgetRepository._activation_history(
            sql, organization, plan_id, observed_at,
        )
        if pointer is None:
            if history:
                raise BudgetRepositoryError("unavailable")
            return None
        if not history:
            raise BudgetRepositoryError("unavailable")
        plan, activation = history[-1]
        try:
            verified = validate_active_budget_rows(
                plan, activation, pointer, observed_at=observed_at,
            )
        except BudgetIntegrityError:
            raise BudgetRepositoryError("unavailable") from None
        return verified[0], verified[1], verified[2], history

    @staticmethod
    def _activation_history(sql, organization: str, plan_id: str,
                            observed_at: str) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
        rows = sql.execute(
            "SELECT * FROM portfolio_work_budget_activation_events "
            "WHERE organization_id=? AND budget_plan_id=? "
            "ORDER BY activation_generation LIMIT ?",
            (organization, plan_id, MAX_BUDGET_ACTIVATIONS_PER_PLAN + 1),
        ).fetchall()
        if len(rows) > MAX_BUDGET_ACTIVATIONS_PER_PLAN:
            raise BudgetRepositoryError("unavailable")
        result: list[tuple[dict[str, Any], dict[str, Any]]] = []
        activated_versions: set[int] = set()
        for raw in rows:
            activation = dict(raw)
            try:
                validate_budget_activation_row(activation)
            except BudgetIntegrityError:
                raise BudgetRepositoryError("unavailable") from None
            plan = WorkBudgetRepository._plan(
                sql, organization, plan_id, activation["current_version"],
            )
            if plan is None:
                raise BudgetRepositoryError("unavailable")
            synthetic_pointer = {
                "organization_id": organization,
                "budget_plan_id": plan_id,
                "active_version": activation["current_version"],
                "activation_generation": activation["activation_generation"],
                "current_activation_event_id": activation["activation_event_id"],
                "changed_at": activation["committed_at"],
            }
            try:
                validate_active_budget_rows(
                    plan, activation, synthetic_pointer, observed_at=observed_at,
                )
            except BudgetIntegrityError:
                raise BudgetRepositoryError("unavailable") from None
            prior = None if not result else result[-1][1]
            expected_generation = len(result) + 1
            expected_reason = (
                "reactivated"
                if activation["current_version"] in activated_versions
                else "accepted"
            )
            if (
                activation["activation_generation"] != expected_generation
                or (
                    prior is not None
                    and (
                        activation["previous_version"] != prior["current_version"]
                        or activation["committed_at"] < prior["committed_at"]
                    )
                )
                or activation["reason_code"] != expected_reason
            ):
                raise BudgetRepositoryError("unavailable")
            activated_versions.add(activation["current_version"])
            result.append((plan, activation))
        return tuple(result)

    @staticmethod
    def _effective_activation(
        history: tuple[tuple[dict[str, Any], dict[str, Any]], ...], as_of: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for plan, activation in reversed(history):
            if max(plan["window_start_at"], activation["committed_at"]) <= as_of:
                return plan, activation
        return None

    @staticmethod
    def _audit(sql, principal: PortfolioPrincipal, operation: str, entity_id: str,
               version: int | None, reason: str, *, now: str) -> int:
        maximum = sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_work_budget_audit_events "
            "WHERE organization_id=?", (principal.organization_id,),
        )["sequence"]
        if type(maximum) is not int or not 0 <= maximum < 9223372036854775807:
            raise BudgetRepositoryError("unavailable")
        sequence = maximum + 1
        sql.insert("portfolio_work_budget_audit_events", {
            "organization_id": principal.organization_id,
            "event_id": uuid4().hex,
            "sequence": sequence,
            "actor_id": principal.actor_id,
            "operation": operation,
            "entity_id": entity_id,
            "entity_version": version,
            "reason_code": reason,
            "occurred_at": now,
        })
        return sequence

    @staticmethod
    def _parse_plan_request(value: object) -> dict[str, Any]:
        if type(value) is not dict or set(value) != _PLAN_FIELDS:
            raise BudgetRepositoryError("invalid_request")
        if value["schema_id"] != "hormuz.work-budget-plan-request" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise BudgetRepositoryError("invalid_request")
        plan_id = None if value["budget_plan_id"] is None else _identifier(value["budget_plan_id"])
        expected = _version(value["expected_version"], nullable=True)
        if (plan_id is None) != (expected is None):
            raise BudgetRepositoryError("invalid_request")
        window = value["window"]
        if type(window) is not dict or set(window) != {"start_at", "end_at"}:
            raise BudgetRepositoryError("invalid_request")
        start, end = _timestamp(window["start_at"]), _timestamp(window["end_at"])
        if start >= end:
            raise BudgetRepositoryError("invalid_request")
        try:
            currency = currency_code(value["currency"])
        except FinanceValueError:
            raise BudgetRepositoryError("invalid_request") from None
        if currency != value["currency"]:
            raise BudgetRepositoryError("invalid_request")
        allowed_models = _models(value["allowed_models"])
        output_cap = value["output_token_cap"]
        if output_cap is not None and (type(output_cap) is not int or not 0 <= output_cap <= 9007199254740991):
            raise BudgetRepositoryError("invalid_request")
        request_cap = None if value["per_request_cost_cap"] is None else _amount(value["per_request_cost_cap"])
        expected_reason = "created" if plan_id is None else "corrected"
        if value["reason_code"] != expected_reason:
            raise BudgetRepositoryError("invalid_request")
        return {
            "budget_plan_id": plan_id,
            "expected_version": expected,
            "work_scope": value["work_scope"],
            "window_start_at": start,
            "window_end_at": end,
            "currency": currency,
            "amount": _amount(value["amount"]),
            "allowed_models": allowed_models,
            "output_token_cap": output_cap,
            "per_request_cost_cap": request_cap,
            "reason_code": expected_reason,
        }

    @staticmethod
    def _parse_activation(value: object) -> dict[str, Any]:
        if type(value) is not dict or set(value) != _ACTIVATION_FIELDS:
            raise BudgetRepositoryError("invalid_request")
        if value["schema_id"] != "hormuz.work-budget-plan-activation-request" or type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise BudgetRepositoryError("invalid_request")
        reason = value["reason_code"]
        if reason not in {"accepted", "reactivated"}:
            raise BudgetRepositoryError("invalid_request")
        return {
            "version": _version(value["version"]),
            "expected_active_version": _version(value["expected_active_version"], nullable=True),
            "expected_activation_generation": _count(value["expected_activation_generation"]),
            "reason_code": reason,
        }

    def _plan_response(self, sql, row: Mapping[str, Any]) -> dict[str, Any]:
        row = self._verified_plan(row)
        organization, plan_id, version = row["organization_id"], row["budget_plan_id"], row["version"]
        now = sql.now()
        active = self._active_budget(sql, organization, plan_id, now)
        pointer = None if active is None else active[2]
        lifecycle = next(
            (
                activation
                for _plan, activation in reversed(() if active is None else active[3])
                if activation["current_version"] == version
            ),
            None,
        )
        if lifecycle is None:
            state = None
        else:
            if pointer is None:
                raise BudgetRepositoryError("unavailable")
            synthetic_pointer = {
                "organization_id": organization,
                "budget_plan_id": plan_id,
                "active_version": version,
                "activation_generation": lifecycle["activation_generation"],
                "current_activation_event_id": lifecycle["activation_event_id"],
                "changed_at": lifecycle["committed_at"],
            }
            try:
                validate_active_budget_rows(
                    row, lifecycle, synthetic_pointer, observed_at=now,
                )
            except BudgetIntegrityError:
                raise BudgetRepositoryError("unavailable") from None
            if pointer["active_version"] == version:
                state = "expired" if row["window_end_at"] <= now else "active"
            else:
                state = "superseded"
        allowed = None if row["allowed_models_json"] is None else json.loads(row["allowed_models_json"])
        return {
            "schema_id": "hormuz.work-budget-plan", "schema_version": 1,
            "organization_id": organization, "budget_plan_id": plan_id, "version": version,
            "work_scope": {"work_scope_id": row["work_scope_id"], "version": row["work_scope_version"]},
            "window": {"start_at": row["window_start_at"], "end_at": row["window_end_at"]},
            "currency": row["currency"], "amount": row["amount"], "allowed_models": allowed,
            "output_token_cap": row["output_token_cap"], "per_request_cost_cap": row["per_request_cost_cap"],
            "active_version": None if pointer is None else pointer["active_version"],
            "activation_generation": 0 if pointer is None else pointer["activation_generation"],
            "state": state, "supersedes_version": row["supersedes_version"],
            "actor_id": row["actor_id"], "reason_code": row["reason_code"],
            "event_at": row["created_at"], "observed_at": row["created_at"], "ingested_at": row["created_at"],
        }

    def create_plan(self, principal: PortfolioPrincipal, value: object) -> dict[str, Any]:
        self._authorize(principal)
        request = self._parse_plan_request(value)
        with self._transaction(principal) as sql:
            scope = self._scope(sql, principal.organization_id, request["work_scope"])
            plan_id = request["budget_plan_id"] or uuid4().hex
            prior = self._plan(sql, principal.organization_id, plan_id)
            if request["expected_version"] != (None if prior is None else prior["version"]):
                raise BudgetRepositoryError("version_conflict")
            if prior is not None and prior["version"] >= 2147483647:
                raise BudgetRepositoryError("version_conflict")
            version = 1 if prior is None else prior["version"] + 1
            now = sql.now()
            content = {
                "work_scope": {"work_scope_id": scope["work_scope_id"], "version": scope["version"]},
                "window": {"start_at": request["window_start_at"], "end_at": request["window_end_at"]},
                "currency": request["currency"], "amount": request["amount"],
                "allowed_models": request["allowed_models"], "output_token_cap": request["output_token_cap"],
                "per_request_cost_cap": request["per_request_cost_cap"],
            }
            sequence = self._audit(sql, principal, "create", plan_id, version, request["reason_code"], now=now)
            row = {
                "organization_id": principal.organization_id, "budget_plan_id": plan_id, "version": version,
                "work_scope_id": scope["work_scope_id"], "work_scope_version": scope["version"],
                "window_start_at": request["window_start_at"], "window_end_at": request["window_end_at"],
                "currency": request["currency"], "amount": request["amount"],
                "allowed_models_json": None if request["allowed_models"] is None else canonical(list(request["allowed_models"])),
                "output_token_cap": request["output_token_cap"], "per_request_cost_cap": request["per_request_cost_cap"],
                "content_digest": _sha256(content), "supersedes_version": None if prior is None else prior["version"],
                "actor_id": principal.actor_id, "reason_code": request["reason_code"], "created_at": now,
                "sequence": sequence,
            }
            sql.insert("portfolio_work_budget_plan_versions", row)
            result = self._plan_response(sql, row)
        return result

    def activate_plan(self, principal: PortfolioPrincipal, plan_id: str, value: object) -> dict[str, Any]:
        self._authorize(principal)
        plan_id = _identifier(plan_id)
        request = self._parse_activation(value)
        _snapshot, policy = self._policy(principal)
        with self._transaction(principal) as sql:
            row = self._plan(sql, principal.organization_id, plan_id, request["version"])
            if row is None:
                raise BudgetRepositoryError("not_found")
            self._scope(sql, principal.organization_id, {
                "work_scope_id": row["work_scope_id"], "version": row["work_scope_version"],
            })
            active = self._active_budget(
                sql, principal.organization_id, plan_id, sql.now(),
            )
            pointer = None if active is None else active[2]
            current_version = None if pointer is None else pointer["active_version"]
            generation = 0 if pointer is None else pointer["activation_generation"]
            if (
                request["expected_active_version"] != current_version
                or request["expected_activation_generation"] != generation
            ):
                raise BudgetRepositoryError("version_conflict")
            previously_active = any(
                activation["current_version"] == request["version"]
                for _plan, activation in (() if active is None else active[3])
            )
            expected_reason = "reactivated" if previously_active else "accepted"
            if request["reason_code"] != expected_reason:
                raise BudgetRepositoryError("invalid_request")
            if generation >= MAX_BUDGET_ACTIVATIONS_PER_PLAN:
                raise BudgetRepositoryError("version_conflict")
            next_generation, event_id, now = generation + 1, uuid4().hex, sql.now()
            if not row["window_start_at"] <= now < row["window_end_at"]:
                raise BudgetRepositoryError("invalid_request")
            if current_version == request["version"]:
                raise BudgetRepositoryError("version_conflict")
            if pointer is None:
                active_rows = sql.execute(
                    "SELECT budget_plan_id FROM portfolio_work_budget_active_plans "
                    "WHERE organization_id=? ORDER BY budget_plan_id LIMIT ?",
                    (principal.organization_id, MAX_ACTIVE_BUDGET_PLANS),
                ).fetchall()
                if len(active_rows) >= MAX_ACTIVE_BUDGET_PLANS:
                    raise BudgetRepositoryError("unavailable")
            sql.insert("portfolio_work_budget_activation_events", {
                "organization_id": principal.organization_id, "activation_event_id": event_id,
                "budget_plan_id": plan_id, "activation_generation": next_generation,
                "previous_version": current_version, "current_version": request["version"],
                "actor_id": principal.actor_id, "reason_code": request["reason_code"],
                "policy_version": policy["version"], "policy_digest": policy["content_digest"],
                "committed_at": now,
            })
            if pointer is None:
                sql.insert(ACTIVE_TABLE, {
                    "organization_id": principal.organization_id, "budget_plan_id": plan_id,
                    "active_version": request["version"], "activation_generation": next_generation,
                    "current_activation_event_id": event_id, "changed_at": now,
                })
            else:
                cursor = sql.execute(
                    "UPDATE portfolio_work_budget_active_plans SET active_version=?, activation_generation=?, "
                    "current_activation_event_id=?, changed_at=? WHERE organization_id=? AND budget_plan_id=? "
                    "AND active_version=? AND activation_generation=?",
                    (request["version"], next_generation, event_id, now, principal.organization_id,
                     plan_id, current_version, generation),
                )
                if cursor.rowcount != 1:
                    raise BudgetRepositoryError("version_conflict")
            self._audit(sql, principal, "activate", plan_id, request["version"], request["reason_code"], now=now)
            result = self._plan_response(sql, row)
        return result

    def get_plan(self, principal: PortfolioPrincipal, plan_id: str, version: int | None = None) -> dict[str, Any]:
        self._authorize(principal)
        plan_id, version = _identifier(plan_id), _version(version, nullable=True)
        with self._transaction(principal) as sql:
            row = self._plan(sql, principal.organization_id, plan_id, version)
            if row is None:
                raise BudgetRepositoryError("not_found")
            result = self._plan_response(sql, row)
            self._audit(sql, principal, "report", plan_id, row["version"], "observed", now=sql.now())
        return result

    @staticmethod
    def _plan_change(current: Mapping[str, Any], previous: Mapping[str, Any] | None,
                     activation: Mapping[str, Any]) -> dict[str, Any]:
        base = {
            "changed_at": activation["committed_at"], "percentage_scale": 6,
            "percentage_rounding": "half_even", "comparison_reasons": [],
            "comparison_basis": "immediately_prior_active_plan",
        }
        if previous is None:
            return {
                "kind": "established", **base, "previous_plan": None, "previous_amount": None,
                "previous_currency": None, "previous_work_scope": None, "previous_window": None,
                "amount_delta": None, "percent_delta": None, "comparison_status": "first_activation",
            }
        previous_ref = _version_ref(previous["budget_plan_id"], previous["version"], previous["content_digest"])
        previous_scope = {"work_scope_id": previous["work_scope_id"], "version": previous["work_scope_version"]}
        previous_window = {"start_at": previous["window_start_at"], "end_at": previous["window_end_at"]}
        reasons = []
        if current["currency"] != previous["currency"]:
            reasons.append("currency_changed")
        if (current["work_scope_id"], current["work_scope_version"]) != (previous["work_scope_id"], previous["work_scope_version"]):
            reasons.append("work_scope_changed")
        if (current["window_start_at"], current["window_end_at"]) != (previous["window_start_at"], previous["window_end_at"]):
            reasons.append("window_changed")
        common = {
            **base, "previous_plan": previous_ref, "previous_amount": previous["amount"],
            "previous_currency": previous["currency"], "previous_work_scope": previous_scope,
            "previous_window": previous_window,
        }
        if reasons:
            return {
                "kind": "not_comparable", **common, "amount_delta": None, "percent_delta": None,
                "comparison_status": "not_comparable", "comparison_reasons": reasons,
            }
        with exact_context():
            delta = Decimal(current["amount"]) - Decimal(previous["amount"])
        kind = "increased" if delta > 0 else "decreased" if delta < 0 else "unchanged"
        if Decimal(previous["amount"]) == 0:
            return {
                "kind": kind, **common, "amount_delta": _signed(delta), "percent_delta": None,
                "comparison_status": "previous_amount_zero",
            }
        with localcontext(Context(prec=96, rounding=ROUND_HALF_EVEN)):
            percentage = (delta * Decimal(100) / Decimal(previous["amount"])).quantize(Decimal("0.000001"))
        return {
            "kind": kind, **common, "amount_delta": _signed(delta), "percent_delta": _signed(percentage),
            "comparison_status": "known",
        }

    @staticmethod
    def _attempt_rows(sql, organization: str, plan: Mapping[str, Any], as_of: str) -> list[dict[str, Any]]:
        rows = sql.execute(
            "SELECT b.*, e.state AS attempt_state, u.cost_microusd AS committed_cost_microusd "
            "FROM portfolio_work_budget_reservation_bindings b "
            "JOIN gateway_request_attempts a ON a.organization_id=b.organization_id AND a.attempt_id=b.request_attempt_id "
            "JOIN gateway_request_attempt_events e ON e.organization_id=a.organization_id AND e.attempt_id=a.attempt_id "
            "AND e.sequence=(SELECT MAX(n.sequence) FROM gateway_request_attempt_events n "
            "WHERE n.organization_id=a.organization_id AND n.attempt_id=a.attempt_id AND n.occurred_at<=?) "
            "LEFT JOIN gateway_usage_events u ON u.organization_id=e.organization_id AND u.id=e.usage_event_id "
            "WHERE b.organization_id=? AND b.budget_plan_id=? AND b.window_start_at=? "
            "AND b.window_end_at=? AND b.currency=? AND b.bound_at<=? "
            "ORDER BY b.request_attempt_id LIMIT ?",
            (as_of, organization, plan["budget_plan_id"], plan["window_start_at"],
             plan["window_end_at"], plan["currency"], as_of, _MAX_REPORT_ATTEMPTS + 1),
        ).fetchall()
        if len(rows) > _MAX_REPORT_ATTEMPTS:
            raise BudgetRepositoryError("unavailable")
        return [dict(row) for row in rows]

    def _accounting(self, sql, plan: Mapping[str, Any], as_of: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
        rows = self._attempt_rows(sql, plan["organization_id"], plan, as_of)
        if plan["currency"] != "USD":
            nulls = {
                "committed_amount": None, "pending_reservation_amount": None,
                "uncertain_reservation_amount": None, "remaining_amount": None,
            }
            reason = "unsupported_currency"
        else:
            terminal_rows = [
                row for row in rows
                if row["attempt_state"] in {"succeeded", "failed", "rate_limited"}
            ]
            committed_values = [
                _microusd(row["committed_cost_microusd"])
                for row in terminal_rows
                if row["committed_cost_microusd"] is not None
            ]
            pending_values = [row["reserved_amount"] for row in rows if row["attempt_state"] == "pending"]
            uncertain_values = [row["reserved_amount"] for row in rows if row["attempt_state"] == "outcome_unknown"]
            pending, uncertain = _sum(pending_values), _sum(uncertain_values)
            if len(committed_values) != len(terminal_rows):
                committed, remaining, reason = None, None, "missing_evidence"
            else:
                committed = _sum(committed_values)
                with exact_context():
                    remaining_value = (
                        Decimal(plan["amount"]) - Decimal(committed)
                        - Decimal(pending) - Decimal(uncertain)
                    )
                remaining, reason = _signed(remaining_value), "known"
            nulls = {
                "committed_amount": committed, "pending_reservation_amount": pending,
                "uncertain_reservation_amount": uncertain, "remaining_amount": remaining,
            }
        denial_rows = [dict(row) for row in sql.execute(
            "SELECT e.reason_code,COUNT(*) AS count FROM portfolio_work_budget_audit_events e "
            "JOIN portfolio_work_budget_plan_versions v ON v.organization_id=e.organization_id "
            "AND v.budget_plan_id=e.entity_id AND v.version=e.entity_version "
            "WHERE e.organization_id=? AND e.entity_id=? AND e.operation='reserve_denied' "
            "AND v.window_start_at=? AND v.window_end_at=? AND v.currency=? "
            "AND e.occurred_at>=? AND e.occurred_at<? AND e.occurred_at<=? GROUP BY e.reason_code",
            (plan["organization_id"], plan["budget_plan_id"], plan["window_start_at"],
             plan["window_end_at"], plan["currency"], plan["window_start_at"],
             plan["window_end_at"], as_of),
        ).fetchall()]
        denials = {
            row["reason_code"]: _stored_count(row["count"])
            for row in denial_rows
        }
        denied = _stored_count(sum(denials.get(reason, 0) for reason in (
            "budget_ceiling", "output_token_ceiling", "request_cost_ceiling",
        )))
        enforcement = {
            "cost_basis": "configured_rate_card_estimate",
            "valuation_rule": _rule(_VALUATION_RULE_ID, _VALUATION_RULE_DIGEST),
            **nulls, "over_cap_attempts": denied, "reason_code": reason,
        }
        accepted = _stored_count(len(rows))
        denied_total = _stored_count(sum(denials.values()))
        population = _stored_count(accepted + denied_total)
        terminal_attempts = sum(
            row["attempt_state"] in {"succeeded", "failed", "rate_limited"}
            for row in rows
        )
        terminal_priced = sum(
            row["attempt_state"] in {"succeeded", "failed", "rate_limited"}
            and row["committed_cost_microusd"] is not None for row in rows
        )
        unattributed = _stored_count(denials.get("attribution_invalid", 0))
        unsupported = _stored_count(
            denials.get("unsupported_currency", 0)
            if plan["currency"] == "USD"
            else population - unattributed
        )
        included = _stored_count(population - unattributed - unsupported)
        if population == 0:
            coverage_reason = "missing_evidence"
        elif (
            denials.get("attribution_invalid", 0)
            or unsupported
            or terminal_priced != terminal_attempts
        ):
            coverage_reason = "incomplete_coverage"
        else:
            coverage_reason = "known"
        coverage = {
            "population_attempts": population, "included_attempts": included,
            "unattributed_attempts": unattributed,
            "unsupported_attempts": unsupported,
            "pricing_eligible_attempts": terminal_attempts if plan["currency"] == "USD" else 0,
            "priced_attempts": terminal_priced if plan["currency"] == "USD" else 0,
            "rule": _rule(_COVERAGE_RULE_ID, _COVERAGE_RULE_DIGEST),
            "reason_code": coverage_reason,
        }
        if not rows or plan["currency"] != "USD":
            observations = [{
                "basis": "not_available", "amount": None, "currency": None,
                "scope_status": "not_available", "source_kind": "not_available",
                "finalization": "not_applicable", "rate_card": None,
                "provenance_digest": _sha256(rows),
                "reason_code": reason if reason != "known" else "missing_evidence",
                "allocation_rule": None,
            }]
        else:
            grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
            for row in rows:
                key = (row["rate_card_id"], row["rate_card_version"], row["rate_card_digest"])
                grouped.setdefault(key, []).append(row)
            if len(grouped) > 100:
                raise BudgetRepositoryError("unavailable")
            observations = []
            for (card_id, card_version, card_digest), group in sorted(grouped.items()):
                terminal_group = [
                    row for row in group
                    if row["attempt_state"] in {"succeeded", "failed", "rate_limited"}
                ]
                amounts = [
                    _microusd(row["committed_cost_microusd"])
                    for row in terminal_group
                    if row["committed_cost_microusd"] is not None
                ]
                complete = len(amounts) == len(terminal_group)
                observations.append({
                    "basis": "configured_rate_card_estimate",
                    "amount": _sum(amounts) if complete else None,
                    "currency": plan["currency"] if complete else None,
                    "scope_status": "matches_work_scope",
                    "source_kind": "configured_rates", "finalization": "not_applicable",
                    "rate_card": _version_ref(card_id, card_version, card_digest),
                    "provenance_digest": _sha256(group),
                    "reason_code": "known" if complete else "missing_evidence",
                    "allocation_rule": None,
                })
        forecast = self._forecast(plan, enforcement, coverage, as_of)
        return enforcement, observations, forecast, coverage

    @staticmethod
    def _forecast(plan: Mapping[str, Any], enforcement: Mapping[str, Any], coverage: Mapping[str, Any], as_of: str) -> dict[str, Any]:
        unavailable = {
            "method": "not_available", "rule": None, "projected_amount": None, "currency": None,
            "cost_basis": "not_available", "basis_amount": None, "elapsed_seconds": None,
            "period_seconds": None, "excludes_reservations": True,
        }
        if enforcement["reason_code"] != "known":
            return {**unavailable, "reason_code": enforcement["reason_code"]}
        start, end, current = map(datetime.fromisoformat, (plan["window_start_at"], plan["window_end_at"], as_of))
        if current >= end:
            return {**unavailable, "reason_code": "closed_period"}
        if current <= start:
            return {**unavailable, "reason_code": "missing_evidence"}
        if coverage["reason_code"] == "incomplete_coverage":
            return {**unavailable, "reason_code": "incomplete_coverage"}
        if (
            coverage["reason_code"] != "known"
            or coverage["pricing_eligible_attempts"] == 0
        ):
            return {**unavailable, "reason_code": "missing_evidence"}
        elapsed_delta, period_delta = current - start, end - start
        if elapsed_delta.microseconds or period_delta.microseconds:
            return {**unavailable, "reason_code": "precision_exceeded"}
        elapsed = elapsed_delta.days * 86_400 + elapsed_delta.seconds
        period = period_delta.days * 86_400 + period_delta.seconds
        try:
            with exact_context():
                projected = Decimal(enforcement["committed_amount"]) * Decimal(period) / Decimal(elapsed)
                rendered = decimal_text(projected)
        except (ArithmeticError, FinanceValueError):
            return {**unavailable, "reason_code": "precision_exceeded"}
        return {
            "method": "linear_committed_projection", "rule": _rule(_FORECAST_RULE_ID, _FORECAST_RULE_DIGEST),
            "projected_amount": rendered, "currency": plan["currency"],
            "cost_basis": "configured_rate_card_estimate", "basis_amount": enforcement["committed_amount"],
            "elapsed_seconds": elapsed, "period_seconds": period, "excludes_reservations": True,
            "reason_code": "known",
        }

    def current_report(self, principal: PortfolioPrincipal, plan_id: str, *, as_of: str | None = None) -> dict[str, Any]:
        self._authorize(principal)
        plan_id = _identifier(plan_id)
        selected_as_of = None if as_of is None else _timestamp(as_of)
        with self._transaction(principal) as sql:
            generated = sql.now()
            selected_as_of = generated if selected_as_of is None else selected_as_of
            if selected_as_of > generated:
                raise BudgetRepositoryError("invalid_request")
            active = self._active_budget(
                sql, principal.organization_id, plan_id, generated,
            )
            if active is None:
                raise BudgetRepositoryError("not_found")
            selected = self._effective_activation(active[3], selected_as_of)
            if selected is None:
                raise BudgetRepositoryError("not_found")
            plan, activation = selected
            previous = None if activation["previous_version"] is None else self._plan(
                sql, principal.organization_id, plan_id, activation["previous_version"],
            )
            if selected_as_of < activation["committed_at"]:
                raise BudgetRepositoryError("invalid_request")
            try:
                enforcement, observations, forecast, coverage = self._accounting(
                    sql, plan, selected_as_of,
                )
            except BudgetRepositoryError:
                raise
            except (
                ArithmeticError,
                FinanceValueError,
                KeyError,
                RecursionError,
                TypeError,
                ValueError,
            ):
                raise BudgetRepositoryError("unavailable") from None
            plan_change = self._plan_change(plan, previous, activation)
            facts = {
                "plan": dict(plan), "previous_plan": None if previous is None else dict(previous),
                "activation": dict(activation), "plan_change": plan_change,
                "enforcement": enforcement,
                "observations": observations, "forecast": forecast, "coverage": coverage,
                "as_of": selected_as_of,
            }
            report = {
                "schema_id": "hormuz.work-budget-report", "schema_version": 2,
                "organization_id": principal.organization_id, "report_id": uuid4().hex,
                "reader_role": "portfolio_admin",
                "reader_scope_digest": hashlib.sha256(principal.cursor_authority.encode()).hexdigest(),
                "work_scope": {"work_scope_id": plan["work_scope_id"], "version": plan["work_scope_version"]},
                "plan": _version_ref(plan_id, plan["version"], plan["content_digest"]),
                "activation_generation": activation["activation_generation"],
                "policy": {"version": activation["policy_version"], "content_digest": activation["policy_digest"]},
                "window": {"start_at": plan["window_start_at"], "end_at": plan["window_end_at"]},
                "as_of": selected_as_of, "generated_at": generated, "input_snapshot_digest": _sha256(facts),
                "plan_amount": plan["amount"], "currency": plan["currency"],
                "plan_change": plan_change,
                "enforcement": enforcement, "financial_observations": observations,
                "observation_combination": "separate_bases_do_not_sum_overlapping_observations",
                "forecast": forecast, "coverage": coverage,
            }
            self._audit(sql, principal, "report", plan_id, plan["version"], "observed", now=generated)
        return report

    def _scenario_policy_projection(
        self, snapshot: PolicySnapshot | None, organization_id: str, scenario,
    ) -> tuple[dict[str, str | None], int | None] | None:
        if snapshot is None:
            return None
        identities = (*self.config.identities_by_token.values(), *self.config.identities_by_subject.values())
        identity = next((item for item in identities if item.organization_id == organization_id
                         and item.actor_id == scenario.actor_id), None)
        if identity is None or (identity.allowed_clients and scenario.client not in identity.allowed_clients):
            return None
        policy = snapshot.effective_policy
        if policy.allowed_clients is not None and scenario.client not in policy.allowed_clients:
            return None
        alias = scenario.requested_model
        route = self.config.model_routes.get(alias)
        allowed = policy.allowed_models is None or alias in policy.allowed_models
        if route is None or route.protocol != scenario.protocol or not allowed:
            fallback = (
                (policy.fallback_models or {}).get(scenario.protocol)
                or policy.fallback_model
            )
            route = self.config.model_routes.get(fallback) if fallback else None
            if (
                route is None
                or route.protocol != scenario.protocol
                or (
                    policy.allowed_models is not None
                    and fallback not in policy.allowed_models
                )
            ):
                return None
        output_tokens = scenario.requested_output_tokens
        if policy.max_output_tokens is not None and (
            output_tokens is None or output_tokens > policy.max_output_tokens
        ):
            output_tokens = policy.max_output_tokens
        return ({
            "provider_id": route.protocol,
            "model_id": route.upstream_model,
            "model_version": None,
        }, output_tokens)

    def preview_plan(self, principal: PortfolioPrincipal, plan_id: str, version: int,
                     suite: PolicyScenarioSuite) -> dict[str, Any]:
        self._authorize(principal)
        plan_id, version = _identifier(plan_id), _version(version)
        if type(suite) is not PolicyScenarioSuite or suite.organization_id != principal.organization_id or not suite.scenarios:
            raise BudgetRepositoryError("invalid_request")
        snapshot, policy = self._policy(principal)
        snapshots: dict[str, PolicySnapshot | None] = {
            principal.actor_id: snapshot,
        }
        identities = self.config.identities_by_actor
        for actor_id in sorted({scenario.actor_id for scenario in suite.scenarios}):
            if actor_id in snapshots:
                continue
            identity = identities.get(actor_id)
            if identity is None or identity.organization_id != principal.organization_id:
                snapshots[actor_id] = None
                continue
            actor_snapshot, actor_policy = self._policy_for_identity(identity)
            snapshots[actor_id] = actor_snapshot if actor_policy == policy else None
        with self._transaction(principal) as sql:
            plan = self._plan(sql, principal.organization_id, plan_id, version)
            if plan is None:
                raise BudgetRepositoryError("not_found")
            scope = self._scope(sql, principal.organization_id, {
                "work_scope_id": plan["work_scope_id"], "version": plan["work_scope_version"],
            })
            scope_chain = self._scope_chain(
                sql, principal.organization_id, scope,
            )
            now = sql.now()
            plans = self._preview_plans(
                sql, principal.organization_id, plan, scope_chain, now,
            )
            active = self._active_budget(
                sql, principal.organization_id, plan_id, now,
            )
            pointer = None if active is None else active[2]
            denied, inconclusive, reasons = 0, 0, set()
            for scenario in suite.scenarios:
                projection = self._scenario_policy_projection(
                    snapshots.get(scenario.actor_id), principal.organization_id, scenario,
                )
                if projection is None:
                    denied += 1
                    reasons.add("policy_drift")
                    continue
                selected_model, output_tokens = projection
                restricted = output_tokens is None
                if output_tokens is None:
                    reasons.add("request_cost_ceiling")
                for effective_plan in plans:
                    allowed_models = (
                        None
                        if effective_plan["allowed_models_json"] is None
                        else json.loads(effective_plan["allowed_models_json"])
                    )
                    if allowed_models is not None and selected_model not in allowed_models:
                        restricted, reasons = True, reasons | {"model_intersection"}
                    if (
                        output_tokens is not None
                        and effective_plan["output_token_cap"] is not None
                        and output_tokens > effective_plan["output_token_cap"]
                    ):
                        restricted, reasons = True, reasons | {"output_token_ceiling"}
                    if effective_plan["currency"] != "USD":
                        restricted, reasons = True, reasons | {"plan_drift"}
                if restricted:
                    denied += 1
                else:
                    # The frozen scenario carries no request body, input-token
                    # reservation, or cost estimate. Therefore the candidate's
                    # monetary ceiling cannot be proven compatible from this
                    # suite, even when its model/output checks pass.
                    inconclusive += 1
                    reasons.add("missing_evidence")
            evaluated = len(suite.scenarios)
            allowed = evaluated - denied - inconclusive
            if denied:
                result = "would_restrict"
            elif inconclusive:
                result = "inconclusive"
            else:
                result = "compatible"
            expires = (datetime.fromisoformat(now) + timedelta(minutes=15)).isoformat(timespec="microseconds").replace("+00:00", "Z")
            simulation = {
                "scenario_suite": _version_ref("policy-scenario-suite", 1, suite.content_sha256),
                "evaluated_attempts": evaluated, "allowed_attempts": allowed,
                "denied_attempts": denied, "inconclusive_attempts": inconclusive,
            }
            facts = {
                "plan": dict(plan), "effective_plans": plans,
                "pointer": pointer, "policy": policy,
                "suite": suite.to_mapping(), "simulation": simulation,
            }
            preview = {
                "schema_id": "hormuz.work-budget-preview", "schema_version": 1,
                "organization_id": principal.organization_id, "preview_id": uuid4().hex,
                "reader_role": "portfolio_admin",
                "work_scope": {"work_scope_id": plan["work_scope_id"], "version": plan["work_scope_version"]},
                "candidate_plan": _version_ref(plan_id, version, plan["content_digest"]),
                "expected_active_version": None if pointer is None else pointer["active_version"],
                "expected_activation_generation": 0 if pointer is None else pointer["activation_generation"],
                "policy": policy, "as_of": now, "expires_at": expires,
                "input_snapshot_digest": _sha256(facts), "ceiling_classes_evaluated": list(_CEILING_CLASSES),
                "simulation": simulation, "result": result,
                "restriction_reasons": [item for item in _RESTRICTION_ORDER if item in reasons],
                "dry_run": True, "activation_permitted": False, "provider_egress_permitted": False,
            }
            self._audit(sql, principal, "preview", plan_id, version, "observed", now=now)
        return preview


def create_budget_repository(
    config: GatewayConfig,
    *,
    environ: Mapping[str, str] | None = None,
    connection_pool: PostgresConnectionPool | None = None,
    read_only: bool = False,
) -> WorkBudgetRepository:
    storage = config.usage_storage
    environment = os.environ if environ is None else environ
    if storage.backend == "sqlite":
        dsn = ""
    elif storage.backend == "postgresql":
        dsn = environment.get(storage.postgres_dsn_env, "")
        if not dsn:
            raise BudgetRepositoryError("unavailable")
    else:
        raise BudgetRepositoryError("unavailable")
    return WorkBudgetRepository(
        config, dsn=dsn, environ=environment, connection_pool=connection_pool, read_only=read_only,
    )
