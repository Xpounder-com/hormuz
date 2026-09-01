"""Durable work-budget plans, activations, bindings, and safe audit facts.

The schema keeps immutable accounting facts separate from the one mutable
active-pointer projection.  Runtime code may advance that pointer by one
generation, but cannot rewrite a plan, activation, attempt binding, or audit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Any, Mapping

from .finance_values import FinanceValueError, currency_code, decimal_text


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_EVENT_ID = re.compile(r"[0-9a-f]{32}\Z")
_PLAN_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,9})?\Z")


class BudgetIntegrityError(ValueError):
    """A fixed failure for malformed persisted work-budget evidence."""


class BudgetPlanIntegrityError(BudgetIntegrityError):
    """A fixed failure for a malformed immutable plan fact."""

    def __init__(self):
        super().__init__("budget_plan_malformed")


class BudgetActivationIntegrityError(BudgetIntegrityError):
    """A fixed failure for a malformed immutable activation fact."""

    def __init__(self):
        super().__init__("budget_activation_malformed")


class BudgetPointerIntegrityError(BudgetIntegrityError):
    """A fixed failure for a malformed active-plan projection."""

    def __init__(self):
        super().__init__("budget_pointer_malformed")


def budget_amount_text(value: object) -> str:
    """Return the frozen work-plan 18/9 non-negative decimal form."""

    if type(value) is not str or _PLAN_DECIMAL.fullmatch(value) is None:
        raise FinanceValueError("finance_invalid_amount")
    result = decimal_text(value)
    if result != value or Decimal(result) < 0:
        raise FinanceValueError("finance_invalid_amount")
    return result


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )


def _canonical_timestamp(value: object, error: type[BudgetIntegrityError]) -> str:
    if type(value) is not str or _TIME.fullmatch(value) is None:
        raise error()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise error() from None
    if parsed.tzinfo != timezone.utc:
        raise error()
    result = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if result != value:
        raise error()
    return result


def validate_budget_plan_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a persisted plan before either reporting or enforcement."""

    try:
        organization_id = row["organization_id"]
        plan_id = row["budget_plan_id"]
        version = row["version"]
        scope_id = row["work_scope_id"]
        scope_version = row["work_scope_version"]
        if any(
            type(value) is not str or _ID.fullmatch(value) is None
            for value in (organization_id, plan_id, scope_id, row["actor_id"])
        ):
            raise BudgetPlanIntegrityError()
        if any(
            type(value) is not int or not 1 <= value <= 2147483647
            for value in (version, scope_version)
        ):
            raise BudgetPlanIntegrityError()

        times = [
            _canonical_timestamp(value, BudgetPlanIntegrityError)
            for value in (row["window_start_at"], row["window_end_at"], row["created_at"])
        ]
        start, end, created = times
        if start >= end or (start, end, created) != (
            row["window_start_at"], row["window_end_at"], row["created_at"],
        ):
            raise BudgetPlanIntegrityError()

        currency = currency_code(row["currency"])
        amount = budget_amount_text(row["amount"])
        if currency != row["currency"]:
            raise BudgetPlanIntegrityError()

        if row["allowed_models_json"] is None:
            allowed_models = None
        else:
            allowed_models = json.loads(row["allowed_models_json"])
            if type(allowed_models) is not list or len(allowed_models) > 100:
                raise BudgetPlanIntegrityError()
            for item in allowed_models:
                if type(item) is not dict or set(item) != {"provider_id", "model_id", "model_version"}:
                    raise BudgetPlanIntegrityError()
                if any(
                    type(item[key]) is not str or _ID.fullmatch(item[key]) is None
                    for key in ("provider_id", "model_id")
                ) or (
                    item["model_version"] is not None
                    and (type(item["model_version"]) is not str or _ID.fullmatch(item["model_version"]) is None)
                ):
                    raise BudgetPlanIntegrityError()
            identities = [_canonical(item) for item in allowed_models]
            if (
                len(identities) != len(set(identities))
                or allowed_models != sorted(allowed_models, key=_canonical)
                or row["allowed_models_json"] != _canonical(allowed_models)
            ):
                raise BudgetPlanIntegrityError()

        output_cap = row["output_token_cap"]
        if output_cap is not None and (
            type(output_cap) is not int or not 0 <= output_cap <= 9007199254740991
        ):
            raise BudgetPlanIntegrityError()
        request_cap = row["per_request_cost_cap"]
        if request_cap is not None:
            budget_amount_text(request_cap)

        supersedes = row["supersedes_version"]
        reason = row["reason_code"]
        if (
            (version == 1 and (supersedes is not None or reason != "created"))
            or (
                version > 1
                and (
                    type(supersedes) is not int
                    or supersedes != version - 1
                    or reason != "corrected"
                )
            )
        ):
            raise BudgetPlanIntegrityError()
        sequence = row["sequence"]
        if type(sequence) is not int or not 1 <= sequence <= 9223372036854775807:
            raise BudgetPlanIntegrityError()

        content = {
            "work_scope": {"work_scope_id": scope_id, "version": scope_version},
            "window": {"start_at": start, "end_at": end},
            "currency": currency,
            "amount": amount,
            "allowed_models": allowed_models,
            "output_token_cap": output_cap,
            "per_request_cost_cap": request_cap,
        }
        digest = row["content_digest"]
        if (
            type(digest) is not str
            or _DIGEST.fullmatch(digest) is None
            or digest != hashlib.sha256(_canonical(content).encode()).hexdigest()
        ):
            raise BudgetPlanIntegrityError()
    except BudgetPlanIntegrityError:
        raise
    except (
        ArithmeticError,
        FinanceValueError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise BudgetPlanIntegrityError() from None
    return dict(row)


def validate_budget_activation_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an immutable activation before it affects state or reporting."""

    try:
        organization_id = row["organization_id"]
        plan_id = row["budget_plan_id"]
        actor_id = row["actor_id"]
        policy_version = row["policy_version"]
        if any(
            type(value) is not str or _ID.fullmatch(value) is None
            for value in (organization_id, plan_id, actor_id, policy_version)
        ):
            raise BudgetActivationIntegrityError()
        if (
            type(row["activation_event_id"]) is not str
            or _EVENT_ID.fullmatch(row["activation_event_id"]) is None
            or type(row["policy_digest"]) is not str
            or _DIGEST.fullmatch(row["policy_digest"]) is None
        ):
            raise BudgetActivationIntegrityError()
        generation = row["activation_generation"]
        current = row["current_version"]
        previous = row["previous_version"]
        if (
            type(generation) is not int
            or not 1 <= generation <= 9007199254740991
            or type(current) is not int
            or not 1 <= current <= 2147483647
            or (
                previous is not None
                and (type(previous) is not int or not 1 <= previous <= 2147483647)
            )
            or previous == current
        ):
            raise BudgetActivationIntegrityError()
        reason = row["reason_code"]
        if (
            generation == 1
            and (previous is not None or reason != "accepted")
        ) or (
            generation > 1
            and (previous is None or reason not in {"accepted", "reactivated"})
        ):
            raise BudgetActivationIntegrityError()
        _canonical_timestamp(row["committed_at"], BudgetActivationIntegrityError)
    except BudgetActivationIntegrityError:
        raise
    except (KeyError, TypeError, ValueError):
        raise BudgetActivationIntegrityError() from None
    return dict(row)


def validate_budget_pointer_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the mutable active pointer before following its references."""

    try:
        if any(
            type(row[key]) is not str or _ID.fullmatch(row[key]) is None
            for key in ("organization_id", "budget_plan_id")
        ):
            raise BudgetPointerIntegrityError()
        if (
            type(row["active_version"]) is not int
            or not 1 <= row["active_version"] <= 2147483647
            or type(row["activation_generation"]) is not int
            or not 1 <= row["activation_generation"] <= 9007199254740991
            or type(row["current_activation_event_id"]) is not str
            or _EVENT_ID.fullmatch(row["current_activation_event_id"]) is None
        ):
            raise BudgetPointerIntegrityError()
        _canonical_timestamp(row["changed_at"], BudgetPointerIntegrityError)
    except BudgetPointerIntegrityError:
        raise
    except (KeyError, TypeError, ValueError):
        raise BudgetPointerIntegrityError() from None
    return dict(row)


def validate_active_budget_rows(
    plan: Mapping[str, Any],
    activation: Mapping[str, Any],
    pointer: Mapping[str, Any],
    *,
    observed_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate an active pointer and its complete immutable evidence chain."""

    verified_plan = validate_budget_plan_row(plan)
    verified_activation = validate_budget_activation_row(activation)
    verified_pointer = validate_budget_pointer_row(pointer)
    observed = _canonical_timestamp(observed_at, BudgetPointerIntegrityError)
    if (
        (
            verified_plan["organization_id"],
            verified_plan["budget_plan_id"],
            verified_plan["version"],
        )
        != (
            verified_pointer["organization_id"],
            verified_pointer["budget_plan_id"],
            verified_pointer["active_version"],
        )
        or (
            verified_activation["organization_id"],
            verified_activation["budget_plan_id"],
            verified_activation["current_version"],
            verified_activation["activation_generation"],
            verified_activation["activation_event_id"],
            verified_activation["committed_at"],
        )
        != (
            verified_pointer["organization_id"],
            verified_pointer["budget_plan_id"],
            verified_pointer["active_version"],
            verified_pointer["activation_generation"],
            verified_pointer["current_activation_event_id"],
            verified_pointer["changed_at"],
        )
        or not (
            verified_plan["window_start_at"]
            <= verified_activation["committed_at"]
            < verified_plan["window_end_at"]
        )
        or verified_plan["created_at"] > verified_activation["committed_at"]
        or verified_activation["committed_at"] > observed
    ):
        raise BudgetPointerIntegrityError()
    return verified_plan, verified_activation, verified_pointer


APPEND_ONLY_TABLE_DDL = {
    "portfolio_work_budget_plan_versions": """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
        version INTEGER NOT NULL CHECK (version BETWEEN 1 AND 2147483647),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        window_start_at TEXT NOT NULL CHECK (length(window_start_at) BETWEEN 20 AND 27),
        window_end_at TEXT NOT NULL CHECK (length(window_end_at) BETWEEN 20 AND 27),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        amount TEXT NOT NULL CHECK (length(amount) BETWEEN 1 AND 28),
        allowed_models_json TEXT CHECK (allowed_models_json IS NULL OR length(allowed_models_json) BETWEEN 2 AND 65536),
        output_token_cap BIGINT CHECK (output_token_cap IS NULL OR output_token_cap BETWEEN 0 AND 9007199254740991),
        per_request_cost_cap TEXT CHECK (per_request_cost_cap IS NULL OR length(per_request_cost_cap) BETWEEN 1 AND 28),
        content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
        supersedes_version INTEGER CHECK (supersedes_version IS NULL OR supersedes_version BETWEEN 1 AND 2147483647),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','corrected')),
        created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 20 AND 27),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        PRIMARY KEY (organization_id, budget_plan_id, version),
        UNIQUE (organization_id, sequence),
        UNIQUE (organization_id, budget_plan_id, content_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        FOREIGN KEY (organization_id, budget_plan_id, supersedes_version)
            REFERENCES {prefix}portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        CHECK (window_start_at < window_end_at),
        CHECK ((version = 1 AND supersedes_version IS NULL AND reason_code = 'created')
               OR (version > 1 AND supersedes_version = version - 1 AND reason_code = 'corrected'))
    """,
    "portfolio_work_budget_activation_events": """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        activation_event_id TEXT NOT NULL CHECK (length(activation_event_id) = 32),
        budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
        activation_generation BIGINT NOT NULL CHECK (activation_generation BETWEEN 1 AND 9007199254740991),
        previous_version INTEGER CHECK (previous_version IS NULL OR previous_version BETWEEN 1 AND 2147483647),
        current_version INTEGER NOT NULL CHECK (current_version BETWEEN 1 AND 2147483647),
        actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('accepted','reactivated')),
        policy_version TEXT NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 128),
        policy_digest TEXT NOT NULL CHECK (length(policy_digest) = 64),
        committed_at TEXT NOT NULL CHECK (length(committed_at) BETWEEN 20 AND 27),
        PRIMARY KEY (organization_id, activation_event_id),
        UNIQUE (organization_id, budget_plan_id, activation_generation),
        UNIQUE (organization_id, budget_plan_id, current_version, activation_generation),
        UNIQUE (organization_id, budget_plan_id, current_version, activation_generation,
                activation_event_id, committed_at),
        UNIQUE (organization_id, budget_plan_id, current_version, activation_generation,
                policy_version, policy_digest),
        FOREIGN KEY (organization_id, budget_plan_id, current_version)
            REFERENCES {prefix}portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        FOREIGN KEY (organization_id, budget_plan_id, previous_version)
            REFERENCES {prefix}portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        CHECK ((activation_generation = 1 AND previous_version IS NULL AND reason_code = 'accepted')
               OR (activation_generation > 1 AND previous_version IS NOT NULL))
    """,
    "portfolio_work_budget_reservation_bindings": """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        request_attempt_id TEXT NOT NULL CHECK (length(request_attempt_id) BETWEEN 1 AND 128),
        attribution_event_id TEXT NOT NULL CHECK (length(attribution_event_id) = 32),
        budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
        budget_plan_version INTEGER NOT NULL CHECK (budget_plan_version BETWEEN 1 AND 2147483647),
        activation_generation BIGINT NOT NULL CHECK (activation_generation BETWEEN 1 AND 9007199254740991),
        work_scope_id TEXT NOT NULL CHECK (length(work_scope_id) BETWEEN 1 AND 128),
        work_scope_version INTEGER NOT NULL CHECK (work_scope_version BETWEEN 1 AND 2147483647),
        window_start_at TEXT NOT NULL CHECK (length(window_start_at) BETWEEN 20 AND 27),
        window_end_at TEXT NOT NULL CHECK (length(window_end_at) BETWEEN 20 AND 27),
        currency TEXT NOT NULL CHECK (length(currency) = 3),
        reserved_amount TEXT NOT NULL CHECK (length(reserved_amount) BETWEEN 1 AND 37),
        reserved_output_tokens BIGINT NOT NULL CHECK (reserved_output_tokens BETWEEN 0 AND 9007199254740991),
        provider_id TEXT NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 128),
        model_id TEXT NOT NULL CHECK (length(model_id) BETWEEN 1 AND 128),
        model_version TEXT CHECK (model_version IS NULL OR length(model_version) BETWEEN 1 AND 128),
        activation_policy_version TEXT NOT NULL CHECK (length(activation_policy_version) BETWEEN 1 AND 128),
        activation_policy_digest TEXT NOT NULL CHECK (length(activation_policy_digest) = 64),
        request_policy_version TEXT NOT NULL CHECK (length(request_policy_version) BETWEEN 1 AND 128),
        request_policy_digest TEXT NOT NULL CHECK (length(request_policy_digest) = 64),
        rate_card_id TEXT NOT NULL CHECK (length(rate_card_id) BETWEEN 1 AND 128),
        rate_card_version INTEGER NOT NULL CHECK (rate_card_version BETWEEN 1 AND 2147483647),
        rate_card_digest TEXT NOT NULL CHECK (length(rate_card_digest) = 64),
        rate_card_currency TEXT NOT NULL CHECK (length(rate_card_currency) = 3),
        valuation_rule_id TEXT NOT NULL CHECK (length(valuation_rule_id) BETWEEN 1 AND 128),
        valuation_rule_version INTEGER NOT NULL CHECK (valuation_rule_version BETWEEN 1 AND 2147483647),
        valuation_rule_digest TEXT NOT NULL CHECK (length(valuation_rule_digest) = 64),
        bound_at TEXT NOT NULL CHECK (length(bound_at) BETWEEN 20 AND 27),
        PRIMARY KEY (organization_id, request_attempt_id, budget_plan_id),
        FOREIGN KEY (organization_id, budget_plan_id, budget_plan_version)
            REFERENCES {prefix}portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
        FOREIGN KEY (organization_id, attribution_event_id)
            REFERENCES {prefix}portfolio_attribution_events (organization_id, attribution_event_id),
        FOREIGN KEY (organization_id, budget_plan_id, budget_plan_version, activation_generation,
                     activation_policy_version, activation_policy_digest)
            REFERENCES {prefix}portfolio_work_budget_activation_events
                (organization_id, budget_plan_id, current_version, activation_generation,
                 policy_version, policy_digest),
        FOREIGN KEY (organization_id, work_scope_id, work_scope_version)
            REFERENCES {prefix}portfolio_work_scope_versions (organization_id, work_scope_id, version),
        CHECK (window_start_at < window_end_at),
        CHECK (currency = rate_card_currency)
    """,
    "portfolio_work_budget_audit_events": """
        organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
        event_id TEXT NOT NULL CHECK (length(event_id) = 32),
        sequence BIGINT NOT NULL CHECK (sequence BETWEEN 1 AND 9223372036854775807),
        actor_id TEXT CHECK (actor_id IS NULL OR length(actor_id) BETWEEN 1 AND 128),
        operation TEXT NOT NULL CHECK (operation IN ('create','activate','preview','report','reserve_denied')),
        entity_id TEXT NOT NULL CHECK (length(entity_id) BETWEEN 1 AND 128),
        entity_version INTEGER CHECK (entity_version IS NULL OR entity_version BETWEEN 1 AND 2147483647),
        reason_code TEXT NOT NULL CHECK (reason_code IN ('created','corrected','accepted','reactivated','observed','budget_ceiling','model_intersection','output_token_ceiling','request_cost_ceiling','attribution_invalid','unsupported_currency')),
        occurred_at TEXT NOT NULL CHECK (length(occurred_at) BETWEEN 20 AND 27),
        PRIMARY KEY (organization_id, event_id),
        UNIQUE (organization_id, sequence)
    """,
}


ACTIVE_TABLE = "portfolio_work_budget_active_plans"
ACTIVE_TABLE_DDL = """
    organization_id TEXT NOT NULL CHECK (length(organization_id) BETWEEN 1 AND 128),
    budget_plan_id TEXT NOT NULL CHECK (length(budget_plan_id) BETWEEN 1 AND 128),
    active_version INTEGER NOT NULL CHECK (active_version BETWEEN 1 AND 2147483647),
    activation_generation BIGINT NOT NULL CHECK (activation_generation BETWEEN 1 AND 9007199254740991),
    current_activation_event_id TEXT NOT NULL CHECK (length(current_activation_event_id) = 32),
    changed_at TEXT NOT NULL CHECK (length(changed_at) BETWEEN 20 AND 27),
    PRIMARY KEY (organization_id, budget_plan_id),
    FOREIGN KEY (organization_id, budget_plan_id, active_version)
        REFERENCES {prefix}portfolio_work_budget_plan_versions (organization_id, budget_plan_id, version),
    FOREIGN KEY (organization_id, budget_plan_id, active_version, activation_generation,
                 current_activation_event_id, changed_at)
        REFERENCES {prefix}portfolio_work_budget_activation_events
            (organization_id, budget_plan_id, current_version, activation_generation,
             activation_event_id, committed_at)
"""


TABLE_DDL = {
    **APPEND_ONLY_TABLE_DDL,
    ACTIVE_TABLE: ACTIVE_TABLE_DDL,
}


def sqlite_statements() -> tuple[str, ...]:
    ordered = (
        "portfolio_work_budget_plan_versions",
        "portfolio_work_budget_activation_events",
        ACTIVE_TABLE,
        "portfolio_work_budget_reservation_bindings",
        "portfolio_work_budget_audit_events",
    )
    statements = [
        f"CREATE TABLE {name} ({TABLE_DDL[name].format(prefix='')}) WITHOUT ROWID"
        for name in ordered
    ]
    for table in APPEND_ONLY_TABLE_DDL:
        for operation in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'portfolio_budget_append_only'); END"
            )
        statements.append(
            f"CREATE TRIGGER {table}_no_replace BEFORE INSERT ON {table} "
            f"WHEN EXISTS (SELECT 1 FROM {table} WHERE organization_id=NEW.organization_id AND "
            + (
                "budget_plan_id=NEW.budget_plan_id AND version=NEW.version"
                if table == "portfolio_work_budget_plan_versions" else
                "activation_event_id=NEW.activation_event_id"
                if table == "portfolio_work_budget_activation_events" else
                "request_attempt_id=NEW.request_attempt_id AND budget_plan_id=NEW.budget_plan_id"
                if table == "portfolio_work_budget_reservation_bindings" else
                "event_id=NEW.event_id"
            )
            + ") BEGIN SELECT RAISE(ABORT, 'portfolio_budget_append_only'); END"
        )
    statements.extend((
        f"CREATE TRIGGER {ACTIVE_TABLE}_no_delete BEFORE DELETE ON {ACTIVE_TABLE} "
        "BEGIN SELECT RAISE(ABORT, 'portfolio_budget_pointer_invalid'); END",
        f"CREATE TRIGGER {ACTIVE_TABLE}_no_replace BEFORE INSERT ON {ACTIVE_TABLE} "
        f"WHEN EXISTS (SELECT 1 FROM {ACTIVE_TABLE} WHERE organization_id=NEW.organization_id "
        "AND budget_plan_id=NEW.budget_plan_id) "
        "BEGIN SELECT RAISE(ABORT, 'portfolio_budget_pointer_invalid'); END",
        f"CREATE TRIGGER {ACTIVE_TABLE}_update_guard BEFORE UPDATE ON {ACTIVE_TABLE} "
        "WHEN NEW.organization_id<>OLD.organization_id OR NEW.budget_plan_id<>OLD.budget_plan_id "
        "OR NEW.activation_generation<>OLD.activation_generation+1 "
        "BEGIN SELECT RAISE(ABORT, 'portfolio_budget_pointer_invalid'); END",
        "CREATE TRIGGER portfolio_work_budget_binding_attempt_exists BEFORE INSERT ON "
        "portfolio_work_budget_reservation_bindings WHEN NOT EXISTS "
        "(SELECT 1 FROM gateway_request_attempts a WHERE a.organization_id=NEW.organization_id "
        "AND a.attempt_id=NEW.request_attempt_id) OR NOT EXISTS "
        "(SELECT 1 FROM portfolio_attribution_events e WHERE e.organization_id=NEW.organization_id "
        "AND e.attribution_event_id=NEW.attribution_event_id "
        "AND e.request_attempt_id=NEW.request_attempt_id) "
        "BEGIN SELECT RAISE(ABORT, 'portfolio_budget_attempt_invalid'); END",
    ))
    return tuple(statements)


def verify_sqlite_budget(connection, error_factory) -> None:
    observed = {" ".join(row[0].split()) for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND "
        "(name LIKE 'portfolio_work_budget_%')"
    ).fetchall()}
    if any(" ".join(statement.split()) not in observed for statement in sqlite_statements()):
        raise error_factory("storage_schema_partial_upgrade")


def postgres_statements(schema: str, runtime_role: str) -> str:
    """Generate PostgreSQL 13 DDL; the checked-in migration is compared in tests."""

    prefix = schema + "."
    ordered = (
        "portfolio_work_budget_plan_versions",
        "portfolio_work_budget_activation_events",
        ACTIVE_TABLE,
        "portfolio_work_budget_reservation_bindings",
        "portfolio_work_budget_audit_events",
    )
    statements = [f"CREATE TABLE {prefix}{name} ({TABLE_DDL[name].format(prefix=prefix)});" for name in ordered]
    statements.append(
        f"ALTER TABLE {prefix}portfolio_work_budget_reservation_bindings "
        "ADD CONSTRAINT portfolio_work_budget_binding_attempt_fk "
        f"FOREIGN KEY (organization_id, request_attempt_id) REFERENCES {prefix}gateway_request_attempts (organization_id, attempt_id);"
    )
    statements.append(
        f"CREATE FUNCTION {prefix}portfolio_work_budget_binding_guard() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        f"IF NOT EXISTS (SELECT 1 FROM {prefix}portfolio_attribution_events e "
        "WHERE e.organization_id=NEW.organization_id "
        "AND e.attribution_event_id=NEW.attribution_event_id "
        "AND e.request_attempt_id=NEW.request_attempt_id) THEN "
        "RAISE EXCEPTION 'portfolio_budget_attempt_invalid' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$;"
    )
    statements.append(
        f"CREATE FUNCTION {prefix}portfolio_work_budget_pointer_guard() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.organization_id<>OLD.organization_id OR NEW.budget_plan_id<>OLD.budget_plan_id "
        "OR NEW.activation_generation<>OLD.activation_generation+1 THEN "
        "RAISE EXCEPTION 'portfolio_budget_pointer_invalid' USING ERRCODE = '23514'; END IF; "
        "RETURN NEW; END; $$;"
    )
    for table in ordered:
        statements.extend((
            f"ALTER TABLE {prefix}{table} ENABLE ROW LEVEL SECURITY;",
            f"ALTER TABLE {prefix}{table} FORCE ROW LEVEL SECURITY;",
            f"CREATE POLICY {table}_tenant ON {prefix}{table} "
            "USING (organization_id = current_setting('hormuz.organization_id', true)) "
            "WITH CHECK (organization_id = current_setting('hormuz.organization_id', true));",
            f"REVOKE ALL ON {prefix}{table} FROM PUBLIC;",
        ))
        if table == ACTIVE_TABLE:
            statements.extend((
                f"CREATE TRIGGER {table}_pointer_guard BEFORE UPDATE ON {prefix}{table} "
                f"FOR EACH ROW EXECUTE FUNCTION {prefix}portfolio_work_budget_pointer_guard();",
                f"CREATE TRIGGER {table}_immutable BEFORE DELETE OR TRUNCATE ON {prefix}{table} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {prefix}portfolio_reject_mutation();",
                f"GRANT SELECT, INSERT ON {prefix}{table} TO {runtime_role};",
                f"GRANT UPDATE (active_version, activation_generation, current_activation_event_id, changed_at) "
                f"ON {prefix}{table} TO {runtime_role};",
            ))
        else:
            statements.extend((
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON {prefix}{table} "
                f"FOR EACH STATEMENT EXECUTE FUNCTION {prefix}portfolio_reject_mutation();",
                f"GRANT SELECT, INSERT ON {prefix}{table} TO {runtime_role};",
            ))
        if table == "portfolio_work_budget_reservation_bindings":
            statements.insert(
                len(statements) - 1,
                f"CREATE TRIGGER {table}_attempt_guard BEFORE INSERT ON {prefix}{table} "
                f"FOR EACH ROW EXECUTE FUNCTION {prefix}portfolio_work_budget_binding_guard();",
            )
    return "\n\n".join(statements) + "\n"


def verify_postgres_budget(cursor, schema: str, error_factory) -> None:
    from ._portfolio_schema import verify_postgres_owned_tables

    def rows_as_tuples(rows):
        return [
            tuple(row.values()) if isinstance(row, dict) else tuple(row)
            for row in rows
        ]

    expected = {
        table: {
            kind: ddl.count(marker)
            for kind, marker in (("p", "PRIMARY KEY"), ("u", "UNIQUE ("), ("f", "FOREIGN KEY"), ("c", "CHECK ("))
            if marker in ddl
        }
        for table, ddl in APPEND_ONLY_TABLE_DDL.items()
    }
    expected["portfolio_work_budget_reservation_bindings"]["f"] = (
        expected["portfolio_work_budget_reservation_bindings"].get("f", 0) + 1
    )
    verify_postgres_owned_tables(
        cursor, schema, error_factory, APPEND_ONLY_TABLE_DDL, {}, expected, trigger_type=58,
    )
    cursor.execute(
        "SELECT c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=%s AND c.relkind='r'", (schema, ACTIVE_TABLE),
    )
    row = cursor.fetchone()
    values = tuple(row.values()) if isinstance(row, dict) else row
    if values != (True, True):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT qual,with_check,roles,cmd,permissive FROM pg_policies "
        "WHERE schemaname=%s AND tablename=%s",
        (schema, ACTIVE_TABLE),
    )
    policies = cursor.fetchall()
    expected_policy = (
        "(organization_id = current_setting('hormuz.organization_id'::text, true))",
        "(organization_id = current_setting('hormuz.organization_id'::text, true))",
        ["public"],
        "ALL",
        "PERMISSIVE",
    )
    if len(policies) != 1 or next(iter(rows_as_tuples(policies))) != expected_policy:
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
        (schema, ACTIVE_TABLE),
    )
    observed = {str(row["column_name"] if isinstance(row, dict) else row[0]) for row in cursor.fetchall()}
    if observed != {
        "organization_id", "budget_plan_id", "active_version", "activation_generation",
        "current_activation_event_id", "changed_at",
    }:
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT con.contype,count(*) FROM pg_constraint con "
        "JOIN pg_class c ON c.oid=con.conrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=%s AND con.convalidated "
        "GROUP BY con.contype",
        (schema, ACTIVE_TABLE),
    )
    constraints = dict(rows_as_tuples(cursor.fetchall()))
    expected_active_constraints = {
        kind: ACTIVE_TABLE_DDL.count(marker)
        for kind, marker in (
            ("p", "PRIMARY KEY"),
            ("f", "FOREIGN KEY"),
            ("c", "CHECK ("),
        )
    }
    if any(
        constraints.get(kind) != count
        for kind, count in expected_active_constraints.items()
    ):
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT t.tgname,t.tgenabled,t.tgtype,p.proname,p.prosrc,p.prosecdef,pn.nspname "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE n.nspname=%s AND c.relname=%s AND NOT t.tgisinternal",
        (schema, ACTIVE_TABLE),
    )
    triggers = {
        row[:4] + (" ".join(row[4].split()),) + row[5:]
        for row in rows_as_tuples(cursor.fetchall())
    }
    pointer_body = (
        "BEGIN IF NEW.organization_id<>OLD.organization_id OR "
        "NEW.budget_plan_id<>OLD.budget_plan_id OR "
        "NEW.activation_generation<>OLD.activation_generation+1 THEN "
        "RAISE EXCEPTION 'portfolio_budget_pointer_invalid' USING ERRCODE = '23514'; "
        "END IF; RETURN NEW; END;"
    )
    immutable_body = (
        "BEGIN RAISE EXCEPTION 'portfolio_append_only' USING ERRCODE = '23514'; END;"
    )
    if triggers != {
        (
            ACTIVE_TABLE + "_pointer_guard", "O", 19,
            "portfolio_work_budget_pointer_guard", pointer_body, False, schema,
        ),
        (
            ACTIVE_TABLE + "_immutable", "O", 42,
            "portfolio_reject_mutation", immutable_body, False, schema,
        ),
    }:
        raise error_factory("storage_schema_partial_upgrade")
    cursor.execute(
        "SELECT t.tgname,t.tgenabled,t.tgtype,p.proname,p.prosrc,p.prosecdef,pn.nspname "
        "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid "
        "JOIN pg_namespace pn ON pn.oid=p.pronamespace "
        "WHERE n.nspname=%s AND c.relname=%s AND NOT t.tgisinternal",
        (schema, "portfolio_work_budget_reservation_bindings"),
    )
    binding_triggers = {
        row[:4] + (" ".join(row[4].split()),) + row[5:]
        for row in rows_as_tuples(cursor.fetchall())
    }
    binding_body = (
        f'BEGIN IF NOT EXISTS (SELECT 1 FROM "{schema}".portfolio_attribution_events e '
        "WHERE e.organization_id=NEW.organization_id "
        "AND e.attribution_event_id=NEW.attribution_event_id "
        "AND e.request_attempt_id=NEW.request_attempt_id) THEN "
        "RAISE EXCEPTION 'portfolio_budget_attempt_invalid' USING ERRCODE = '23514'; "
        "END IF; RETURN NEW; END;"
    )
    if binding_triggers != {
        (
            "portfolio_work_budget_reservation_bindings_immutable", "O", 58,
            "portfolio_reject_mutation", immutable_body, False, schema,
        ),
        (
            "portfolio_work_budget_reservation_bindings_attempt_guard", "O", 7,
            "portfolio_work_budget_binding_guard", binding_body, False, schema,
        ),
    }:
        raise error_factory("storage_schema_partial_upgrade")
