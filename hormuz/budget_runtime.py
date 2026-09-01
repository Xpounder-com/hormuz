"""Shared work-budget checks inside adapter-owned reservation transactions.

This module owns no connection, pool, transaction, or tenant authority.  The
SQLite and PostgreSQL adapters acquire their locks first, then lend the current
transaction through :class:`RuntimeBudgetSQL` so attribution, ceilings, hold,
and immutable bindings either commit together or all roll back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from ._budget_schema import (
    MAX_ACTIVE_BUDGET_PLANS,
    MAX_BUDGET_BINDINGS_PER_PLAN_WINDOW,
    BudgetIntegrityError,
    validate_active_budget_rows,
)
from ._persistence import ReservationDenied, WorkBudgetContext
from .attribution_admission import AdmissionError
from .finance_values import FinanceValueError, decimal_text, exact_context


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_CURRENCY = re.compile(r"[A-Z]{3}\Z")
_CONFIDENCE = frozenset({"explicit_authorized", "server_side_default", "unattributed", "ambiguous"})
_VALUATION_RULE_ID = "gateway-route-microusd-v1"
_VALUATION_RULE_DIGEST = hashlib.sha256(_VALUATION_RULE_ID.encode()).hexdigest()
_INSERT_TABLES = frozenset({
    "portfolio_attribution_audit_events",
    "portfolio_attribution_events",
    "portfolio_work_budget_audit_events",
    "portfolio_work_budget_reservation_bindings",
})
_KIND_ORDER = {"portfolio": 0, "initiative": 1, "use_case": 2}


class RuntimeBudgetSQL:
    """The smallest keyed SQL surface common to sqlite3 and Psycopg cursors."""

    def __init__(self, owner: Any, *, postgres: bool):
        self.owner = owner
        self.postgres = postgres

    def execute(self, statement: str, values: tuple[object, ...] = ()):
        return self.owner.execute(statement.replace("?", "%s") if self.postgres else statement, values)

    def one(self, statement: str, values: tuple[object, ...] = ()) -> dict[str, Any] | None:
        row = self.execute(statement, values).fetchone()
        return dict(row) if row is not None else None

    def insert(self, table: str, row: dict[str, object]) -> None:
        if table not in _INSERT_TABLES:
            raise ReservationDenied("Work-budget storage is unavailable.")
        self.execute(
            f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )


@dataclass(frozen=True)
class PreparedWorkBudget:
    attribution_event_id: str | None
    scope_chain: tuple[dict[str, Any], ...]


class WorkBudgetDenied(ReservationDenied):
    """A content-free denial carrying only durable plan audit coordinates."""

    def __init__(self, message: str, reason_code: str,
                 plans: tuple[tuple[str, int], ...], *, evaluated_at: str):
        super().__init__(message)
        self.reason_code = reason_code
        self.plans = tuple(sorted(set(plans)))
        self.evaluated_at = evaluated_at


def audit_work_budget_denials(operation):
    """Require adapter-owned durable denial audit after reservation rollback."""

    @wraps(operation)
    def wrapped(owner, *args, **kwargs):
        try:
            return operation(owner, *args, **kwargs)
        except WorkBudgetDenied as denial:
            identity = kwargs.get("identity")
            if identity is not None:
                owner._record_work_budget_denial(identity, denial)
            raise

    return wrapped


def configured_route_rate_card(*, alias: str, protocol: str, upstream_model: str,
                               input_cost_per_million: float,
                               cache_read_cost_per_million: float,
                               cache_write_cost_per_million: float,
                               output_cost_per_million: float) -> dict[str, object]:
    """Bind the exact configured rates used by reservation and settlement."""

    body = {
        "alias": alias, "protocol": protocol, "upstream_model": upstream_model,
        "input_cost_per_million": repr(input_cost_per_million),
        "cache_read_cost_per_million": repr(cache_read_cost_per_million),
        "cache_write_cost_per_million": repr(cache_write_cost_per_million),
        "output_cost_per_million": repr(output_cost_per_million),
        "currency": "USD", "valuation": _VALUATION_RULE_ID,
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    return {
        "id": "gateway-route-" + digest[:32], "version": 1,
        "content_digest": digest, "currency": "USD",
    }


def configured_model_id(
    *,
    resolved_alias: str | None,
    upstream_model: str | None,
    requested_model: str,
) -> str:
    """Return one wire-safe identity for the configured route selection."""

    candidate = resolved_alias or upstream_model or requested_model
    if type(candidate) is not str or not candidate:
        raise ReservationDenied("Work-budget model evidence is invalid.")
    if _ID.fullmatch(candidate) is not None:
        return candidate
    return "configured-model-" + hashlib.sha256(candidate.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reservation_amount(value: int) -> str:
    if type(value) is not int or value < 0:
        raise ReservationDenied("Work-budget estimate is invalid.")
    try:
        with exact_context():
            return decimal_text(Decimal(value) / Decimal(1_000_000))
    except (ArithmeticError, FinanceValueError):
        raise ReservationDenied("Work-budget estimate is invalid.") from None


def _context(value: WorkBudgetContext) -> WorkBudgetContext:
    if type(value) is not WorkBudgetContext or value.confidence not in _CONFIDENCE:
        raise ReservationDenied("Work attribution is invalid for budget enforcement.")
    if (
        type(value.reserved_output_tokens) is not int
        or not 0 <= value.reserved_output_tokens <= 9007199254740991
        or type(value.output_tokens_bounded) is not bool
    ):
        raise ReservationDenied("Work attribution is invalid for budget enforcement.")
    if (
        type(value.policy_version) is not str or _ID.fullmatch(value.policy_version) is None
        or type(value.policy_digest) is not str or _DIGEST.fullmatch(value.policy_digest) is None
        or type(value.rate_card_id) is not str or _ID.fullmatch(value.rate_card_id) is None
        or type(value.rate_card_version) is not int or not 1 <= value.rate_card_version <= 2147483647
        or type(value.rate_card_digest) is not str or _DIGEST.fullmatch(value.rate_card_digest) is None
        or type(value.rate_card_currency) is not str or _CURRENCY.fullmatch(value.rate_card_currency) is None
    ):
        raise ReservationDenied("Work-budget valuation evidence is invalid.")
    if value.work_scope_id is None or value.work_scope_version is None:
        if value.work_scope_id is not None or value.work_scope_version is not None:
            raise ReservationDenied("Work attribution is invalid for budget enforcement.")
        expected = "missing_evidence" if value.confidence == "unattributed" else "ambiguous"
        if value.confidence not in {"unattributed", "ambiguous"} or value.reason_code != expected:
            raise ReservationDenied("Work attribution is invalid for budget enforcement.")
    elif (
        type(value.work_scope_id) is not str
        or _ID.fullmatch(value.work_scope_id) is None
        or type(value.work_scope_version) is not int
        or not 1 <= value.work_scope_version <= 2147483647
        or value.confidence not in {"explicit_authorized", "server_side_default"}
        or value.reason_code != "bound"
    ):
        raise ReservationDenied("Work attribution is invalid for budget enforcement.")
    return value


def _effective_plan_rows(sql: RuntimeBudgetSQL, organization_id: str, now: str) -> list[dict[str, Any]]:
    rows = sql.execute(
        "SELECT v.*,"
        "p.organization_id AS pointer_organization_id,"
        "p.budget_plan_id AS pointer_budget_plan_id,"
        "p.active_version AS pointer_active_version,"
        "p.activation_generation AS pointer_activation_generation,"
        "p.current_activation_event_id AS pointer_activation_event_id,"
        "p.changed_at AS pointer_changed_at,"
        "a.organization_id AS activation_organization_id,"
        "a.activation_event_id AS activation_event_id,"
        "a.budget_plan_id AS activation_budget_plan_id,"
        "a.activation_generation AS activation_generation,"
        "a.previous_version AS activation_previous_version,"
        "a.current_version AS activation_current_version,"
        "a.actor_id AS activation_actor_id,"
        "a.reason_code AS activation_reason_code,"
        "a.policy_version AS policy_version,"
        "a.policy_digest AS policy_digest,"
        "a.committed_at AS activation_committed_at "
        "FROM portfolio_work_budget_active_plans p "
        "LEFT JOIN portfolio_work_budget_activation_events a ON a.organization_id=p.organization_id "
        "AND a.budget_plan_id=p.budget_plan_id AND a.current_version=p.active_version "
        "AND a.activation_generation=p.activation_generation "
        "AND a.activation_event_id=p.current_activation_event_id "
        "LEFT JOIN portfolio_work_budget_plan_versions v ON v.organization_id=p.organization_id "
        "AND v.budget_plan_id=p.budget_plan_id AND v.version=p.active_version "
        "WHERE p.organization_id=? ORDER BY p.budget_plan_id LIMIT ?",
        (organization_id, MAX_ACTIVE_BUDGET_PLANS + 1),
    ).fetchall()
    if len(rows) > MAX_ACTIVE_BUDGET_PLANS:
        reference: tuple[tuple[str, int], ...] = ()
        for raw in rows:
            candidate = dict(raw)
            plan_id = candidate.get("pointer_budget_plan_id")
            version = candidate.get("pointer_active_version")
            if (
                type(plan_id) is str
                and _ID.fullmatch(plan_id) is not None
                and type(version) is int
                and 1 <= version <= 2147483647
            ):
                reference = ((plan_id, version),)
                break
        raise WorkBudgetDenied(
            "The active work budget is unavailable.",
            "attribution_invalid",
            reference,
            evaluated_at=now,
        )
    result = []
    for raw in rows:
        row = dict(raw)
        pointer = {
            "organization_id": row["pointer_organization_id"],
            "budget_plan_id": row["pointer_budget_plan_id"],
            "active_version": row["pointer_active_version"],
            "activation_generation": row["pointer_activation_generation"],
            "current_activation_event_id": row["pointer_activation_event_id"],
            "changed_at": row["pointer_changed_at"],
        }
        activation = {
            "organization_id": row["activation_organization_id"],
            "activation_event_id": row["activation_event_id"],
            "budget_plan_id": row["activation_budget_plan_id"],
            "activation_generation": row["activation_generation"],
            "previous_version": row["activation_previous_version"],
            "current_version": row["activation_current_version"],
            "actor_id": row["activation_actor_id"],
            "reason_code": row["activation_reason_code"],
            "policy_version": row["policy_version"],
            "policy_digest": row["policy_digest"],
            "committed_at": row["activation_committed_at"],
        }
        reference = ()
        if (
            type(pointer["budget_plan_id"]) is str
            and _ID.fullmatch(pointer["budget_plan_id"]) is not None
            and type(pointer["active_version"]) is int
            and 1 <= pointer["active_version"] <= 2147483647
        ):
            reference = ((pointer["budget_plan_id"], pointer["active_version"]),)
        try:
            validate_active_budget_rows(row, activation, pointer, observed_at=now)
        except (BudgetIntegrityError, KeyError, TypeError):
            raise WorkBudgetDenied(
                "The active work budget is unavailable.", "attribution_invalid", reference,
                evaluated_at=now,
            ) from None
        if max(row["window_start_at"], activation["committed_at"]) <= now < row["window_end_at"]:
            result.append(row)
    return result


def _plan_refs(plans: list[dict[str, Any]]) -> tuple[tuple[str, int], ...]:
    return tuple((row["budget_plan_id"], row["version"]) for row in plans)


def _scope_chain(sql: RuntimeBudgetSQL, organization_id: str, context: WorkBudgetContext) -> tuple[dict[str, Any], ...]:
    scope_id, version = context.work_scope_id, context.work_scope_version
    assert scope_id is not None and version is not None
    chain: list[dict[str, Any]] = []
    for _ in range(3):
        latest = sql.one(
            "SELECT * FROM portfolio_work_scope_versions WHERE organization_id=? AND work_scope_id=? "
            "ORDER BY version DESC LIMIT 1", (organization_id, scope_id),
        )
        if latest is None or latest["version"] != version or latest["state"] != "active":
            raise AdmissionError("stale_version", 409)
        if latest["kind"] not in _KIND_ORDER or (chain and _KIND_ORDER[latest["kind"]] >= _KIND_ORDER[chain[-1]["kind"]]):
            raise ReservationDenied("Work attribution hierarchy is invalid for budget enforcement.")
        chain.append(latest)
        scope_id, version = latest["parent_work_scope_id"], latest["parent_version"]
        if scope_id is None:
            if version is not None:
                raise ReservationDenied("Work attribution hierarchy is invalid for budget enforcement.")
            break
        if type(scope_id) is not str or type(version) is not int:
            raise ReservationDenied("Work attribution hierarchy is invalid for budget enforcement.")
    else:
        raise ReservationDenied("Work attribution hierarchy is invalid for budget enforcement.")
    if chain[0]["kind"] != "use_case":
        raise ReservationDenied("A current use-case attribution is required for budget enforcement.")
    return tuple(reversed(chain))


def _attribution(
    sql: RuntimeBudgetSQL,
    *,
    organization_id: str,
    attempt_id: str,
    context: WorkBudgetContext,
    scope_chain: tuple[dict[str, Any], ...],
    now: str,
) -> str:
    maximum = sql.one(
        "SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_attribution_audit_events "
        "WHERE organization_id=?", (organization_id,),
    )["sequence"]
    if type(maximum) is not int or not 0 <= maximum < 9223372036854775807:
        raise ReservationDenied("Work attribution audit is unavailable.")
    sequence, event_id = maximum + 1, uuid4().hex
    sql.insert("portfolio_attribution_audit_events", {
        "organization_id": organization_id, "event_id": uuid4().hex, "sequence": sequence,
        "actor_id": None, "operation": "admit", "entity_id": event_id,
        "reason_code": context.reason_code, "occurred_at": now,
    })
    scope = scope_chain[-1] if scope_chain else None
    sql.insert("portfolio_attribution_events", {
        "organization_id": organization_id, "attribution_event_id": event_id,
        "request_attempt_id": attempt_id,
        "work_scope_id": None if scope is None else scope["work_scope_id"],
        "work_scope_version": None if scope is None else scope["version"],
        "confidence": context.confidence, "state": "active", "supersedes_event_id": None,
        "actor_id": None, "reason_code": context.reason_code,
        "event_at": now, "observed_at": now, "ingested_at": now, "sequence": sequence,
    })
    return event_id


def prepare_work_budget(
    sql: RuntimeBudgetSQL,
    *,
    organization_id: str,
    attempt_id: str,
    work_budget: WorkBudgetContext | None,
    now: datetime,
) -> PreparedWorkBudget | None:
    """Validate and persist attribution before any work-scope ceiling lookup."""

    now_value = _utc(now)
    effective = _effective_plan_rows(sql, organization_id, now_value)
    if work_budget is None:
        if effective:
            raise WorkBudgetDenied(
                "A current use-case attribution is required for budget enforcement.",
                "attribution_invalid", _plan_refs(effective), evaluated_at=now_value,
            )
        return None
    try:
        context = _context(work_budget)
    except ReservationDenied as error:
        if effective:
            raise WorkBudgetDenied(
                str(error), "attribution_invalid", _plan_refs(effective),
                evaluated_at=now_value,
            ) from None
        raise
    if context.work_scope_id is None:
        if effective:
            raise WorkBudgetDenied(
                "A current use-case attribution is required for budget enforcement.",
                "attribution_invalid", _plan_refs(effective), evaluated_at=now_value,
            )
        chain: tuple[dict[str, Any], ...] = ()
    else:
        try:
            chain = _scope_chain(sql, organization_id, context)
        except AdmissionError:
            if effective:
                raise WorkBudgetDenied(
                    "Work attribution is stale or inactive for budget enforcement.",
                    "attribution_invalid", _plan_refs(effective), evaluated_at=now_value,
                ) from None
            raise
        except ReservationDenied as error:
            if effective:
                raise WorkBudgetDenied(
                    str(error), "attribution_invalid", _plan_refs(effective),
                    evaluated_at=now_value,
                ) from None
            raise
    event_id = _attribution(
        sql, organization_id=organization_id, attempt_id=attempt_id,
        context=context, scope_chain=chain, now=now_value,
    )
    return PreparedWorkBudget(event_id, chain)


def _plans(sql: RuntimeBudgetSQL, organization_id: str,
           chain: tuple[dict[str, Any], ...], now: str) -> list[dict[str, Any]]:
    if not chain:
        return []
    scopes = {(item["work_scope_id"], item["version"]) for item in chain}
    kind_by_scope = {(item["work_scope_id"], item["version"]): item["kind"] for item in chain}
    result = [
        row for row in _effective_plan_rows(sql, organization_id, now)
        if (row["work_scope_id"], row["work_scope_version"]) in scopes
    ]
    return sorted(result, key=lambda row: (
        _KIND_ORDER[kind_by_scope[(row["work_scope_id"], row["work_scope_version"])]], row["budget_plan_id"],
    ))


def _consumed(sql: RuntimeBudgetSQL, plan: dict[str, Any]) -> Decimal:
    rows = sql.execute(
        "SELECT b.reserved_amount,e.state,u.cost_microusd FROM portfolio_work_budget_reservation_bindings b "
        "JOIN gateway_request_attempt_events e ON e.organization_id=b.organization_id AND e.attempt_id=b.request_attempt_id "
        "AND e.sequence=(SELECT MAX(n.sequence) FROM gateway_request_attempt_events n "
        "WHERE n.organization_id=e.organization_id AND n.attempt_id=e.attempt_id) "
        "LEFT JOIN gateway_usage_events u ON u.organization_id=e.organization_id AND u.id=e.usage_event_id "
        "WHERE b.organization_id=? AND b.budget_plan_id=? AND b.window_start_at=? AND b.window_end_at=? "
        "AND b.currency=? ORDER BY b.request_attempt_id LIMIT ?",
        (plan["organization_id"], plan["budget_plan_id"], plan["window_start_at"],
         plan["window_end_at"], plan["currency"], MAX_BUDGET_BINDINGS_PER_PLAN_WINDOW),
    ).fetchall()
    if len(rows) >= MAX_BUDGET_BINDINGS_PER_PLAN_WINDOW:
        raise ReservationDenied("Work-budget accounting is unavailable.")
    total = Decimal(0)
    try:
        with exact_context():
            for raw in rows:
                row = dict(raw)
                if row["state"] in {"pending", "outcome_unknown"}:
                    reserved = decimal_text(row["reserved_amount"])
                    if reserved != row["reserved_amount"] or Decimal(reserved) < 0:
                        raise ReservationDenied("Work-budget reservation evidence is unavailable.")
                    total += Decimal(reserved)
                elif row["state"] in {"succeeded", "failed", "rate_limited"}:
                    if type(row["cost_microusd"]) is not int or row["cost_microusd"] < 0:
                        raise ReservationDenied("Work-budget committed estimate is unavailable.")
                    total += Decimal(row["cost_microusd"]) / Decimal(1_000_000)
                else:
                    raise ReservationDenied("Work-budget attempt evidence is unavailable.")
    except (ArithmeticError, FinanceValueError, ValueError):
        raise ReservationDenied("Work-budget accounting is unavailable.") from None
    return total


def enforce_and_bind_work_budget(
    sql: RuntimeBudgetSQL,
    *,
    prepared: PreparedWorkBudget | None,
    organization_id: str,
    attempt_id: str,
    provider_id: str,
    model_id: str,
    model_version: str | None,
    reserved_cost_microusd: int,
    now: datetime,
    work_budget: WorkBudgetContext | None,
) -> None:
    """Evaluate every applicable plan and bind the accepted estimate."""

    if prepared is None or not prepared.scope_chain:
        return
    if work_budget is None:
        raise ReservationDenied("A current use-case attribution is required for budget enforcement.")
    context = _context(work_budget)
    if (
        _ID.fullmatch(provider_id) is None or _ID.fullmatch(model_id) is None
        or (model_version is not None and _ID.fullmatch(model_version) is None)
    ):
        raise ReservationDenied("Work-budget model evidence is invalid.")
    selected_model = {
        "provider_id": provider_id, "model_id": model_id, "model_version": model_version,
    }
    now_value = _utc(now)
    reserved = _reservation_amount(reserved_cost_microusd)
    plans = _plans(sql, organization_id, prepared.scope_chain, now_value)
    if plans and not context.output_tokens_bounded:
        raise WorkBudgetDenied(
            "A bounded output-token estimate is required for work-budget enforcement.",
            "request_cost_ceiling", _plan_refs(plans), evaluated_at=now_value,
        )
    for plan in plans:
        reference = ((plan["budget_plan_id"], plan["version"]),)
        if plan["currency"] != context.rate_card_currency or context.rate_card_currency != "USD":
            raise WorkBudgetDenied(
                "The active work budget uses an unsupported currency.",
                "unsupported_currency", reference, evaluated_at=now_value,
            )
        try:
            allowed = None if plan["allowed_models_json"] is None else json.loads(plan["allowed_models_json"])
        except (TypeError, ValueError, RecursionError):
            raise WorkBudgetDenied(
                "The active work budget is unavailable.", "attribution_invalid", reference,
                evaluated_at=now_value,
            ) from None
        if allowed is not None and (
            type(allowed) is not list or len(allowed) > 100
            or len({json.dumps(item, sort_keys=True, separators=(",", ":")) for item in allowed}) != len(allowed)
            or any(
                type(item) is not dict or set(item) != {"provider_id", "model_id", "model_version"}
                or type(item["provider_id"]) is not str or _ID.fullmatch(item["provider_id"]) is None
                or type(item["model_id"]) is not str or _ID.fullmatch(item["model_id"]) is None
                or (item["model_version"] is not None and (
                    type(item["model_version"]) is not str or _ID.fullmatch(item["model_version"]) is None
                ))
                for item in allowed
            )
        ):
            raise WorkBudgetDenied(
                "The active work budget is unavailable.", "attribution_invalid", reference,
                evaluated_at=now_value,
            )
        if allowed is not None and selected_model not in allowed:
            raise WorkBudgetDenied(
                "The active work budget does not allow this model.", "model_intersection", reference,
                evaluated_at=now_value,
            )
        if plan["output_token_cap"] is not None and context.reserved_output_tokens > plan["output_token_cap"]:
            raise WorkBudgetDenied(
                "The active work-budget output-token ceiling would be exceeded.",
                "output_token_ceiling", reference, evaluated_at=now_value,
            )
        if plan["per_request_cost_cap"] is not None and Decimal(reserved) > Decimal(plan["per_request_cost_cap"]):
            raise WorkBudgetDenied(
                "The active work-budget per-request cost ceiling would be exceeded.",
                "request_cost_ceiling", reference, evaluated_at=now_value,
            )
        try:
            with exact_context():
                projected = _consumed(sql, plan) + Decimal(reserved)
                ceiling = Decimal(plan["amount"])
        except (ArithmeticError, ValueError, ReservationDenied):
            raise WorkBudgetDenied(
                "Work-budget accounting is unavailable.", "budget_ceiling", reference,
                evaluated_at=now_value,
            ) from None
        if projected > ceiling:
            raise WorkBudgetDenied(
                "The active work-budget ceiling would be exceeded by this request.",
                "budget_ceiling", reference, evaluated_at=now_value,
            )
        if prepared.attribution_event_id is None:
            raise WorkBudgetDenied(
                "Work-budget binding evidence is invalid.", "attribution_invalid", reference,
                evaluated_at=now_value,
            )
        sql.insert("portfolio_work_budget_reservation_bindings", {
            "organization_id": organization_id, "request_attempt_id": attempt_id,
            "attribution_event_id": prepared.attribution_event_id,
            "budget_plan_id": plan["budget_plan_id"], "budget_plan_version": plan["version"],
            "activation_generation": plan["activation_generation"],
            "work_scope_id": prepared.scope_chain[-1]["work_scope_id"],
            "work_scope_version": prepared.scope_chain[-1]["version"],
            "window_start_at": plan["window_start_at"], "window_end_at": plan["window_end_at"],
            "currency": plan["currency"], "reserved_amount": reserved,
            "reserved_output_tokens": context.reserved_output_tokens,
            "provider_id": provider_id, "model_id": model_id,
            "model_version": model_version,
            "activation_policy_version": plan["policy_version"],
            "activation_policy_digest": plan["policy_digest"],
            "request_policy_version": context.policy_version,
            "request_policy_digest": context.policy_digest,
            "rate_card_id": context.rate_card_id, "rate_card_version": context.rate_card_version,
            "rate_card_digest": context.rate_card_digest,
            "rate_card_currency": context.rate_card_currency,
            "valuation_rule_id": _VALUATION_RULE_ID, "valuation_rule_version": 1,
            "valuation_rule_digest": _VALUATION_RULE_DIGEST, "bound_at": now_value,
        })


def record_work_budget_denial(sql: RuntimeBudgetSQL, *, organization_id: str,
                              actor_id: str, denial: WorkBudgetDenied) -> None:
    """Persist one fixed audit fact per affected plan after the denied attempt rolls back."""

    if not denial.plans:
        # A denial without a valid plan coordinate is itself damaged evidence;
        # returning success here would turn mandatory audit into a silent gap.
        raise ReservationDenied("Work-budget denial evidence is unavailable.")
    if type(actor_id) is not str or not 1 <= len(actor_id) <= 128:
        raise ReservationDenied("Work-budget denial evidence is unavailable.")
    if type(denial.evaluated_at) is not str or _TIME.fullmatch(denial.evaluated_at) is None:
        raise ReservationDenied("Work-budget denial evidence is unavailable.")
    try:
        evaluated_at = datetime.fromisoformat(denial.evaluated_at)
    except ValueError:
        raise ReservationDenied("Work-budget denial evidence is unavailable.") from None
    if evaluated_at.tzinfo != timezone.utc or _utc(evaluated_at) != denial.evaluated_at:
        raise ReservationDenied("Work-budget denial evidence is unavailable.")
    maximum = sql.one(
        "SELECT COALESCE(MAX(sequence),0) AS sequence FROM portfolio_work_budget_audit_events "
        "WHERE organization_id=?", (organization_id,),
    )["sequence"]
    if type(maximum) is not int or maximum < 0 or maximum + len(denial.plans) > 9223372036854775807:
        raise ReservationDenied("Work-budget denial evidence is unavailable.")
    for offset, (plan_id, version) in enumerate(denial.plans, 1):
        sql.insert("portfolio_work_budget_audit_events", {
            "organization_id": organization_id, "event_id": uuid4().hex,
            "sequence": maximum + offset, "actor_id": actor_id,
            "operation": "reserve_denied", "entity_id": plan_id,
            "entity_version": version, "reason_code": denial.reason_code,
            "occurred_at": denial.evaluated_at,
        })
