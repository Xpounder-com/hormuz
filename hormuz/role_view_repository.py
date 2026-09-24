"""Authorized, audited aggregate portfolio views for finance, platform, and teams."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import urlencode
from uuid import uuid4

from ._budget_schema import ACTIVE_TABLE as BUDGET_ACTIVE_TABLE
from ._portfolio_sql import ROLE_VIEW_TABLES, portfolio_transaction
from ._role_view_schema import AUDIT_TABLE, CURSOR_TABLE
from .budget_repository import BudgetRepositoryError, WorkBudgetRepository
from .config import GatewayConfig
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import (
    RESPONSE_BYTES,
    ROLE_VIEW_OPERATIONS,
    PortfolioError,
    canonical,
    query_parameters,
    route,
    validate,
)
from .scorecard_repository import ScorecardRepository


_OPERATIONS = {
    "list_finance_budgets": ("finance_viewer", "finance", "budget"),
    "list_platform_scorecards": ("platform_viewer", "platform", "scorecard"),
    "list_team_budgets": ("team_lead", "team", "budget"),
    "list_team_scorecards": ("team_lead", "team", "scorecard"),
}
_MAX_CANDIDATES = 10_000
_MAX_SCOPE_ROWS = 10_000
_CURSOR_SECONDS = 3600
_PAGE_SCHEMA_ID = "hormuz.portfolio-role-view-page"
_ITEM_SCHEMA_ID = "hormuz.portfolio-role-view-item"
_EXCLUSIONS = (
    "prompts",
    "responses",
    "work_item_titles",
    "work_item_bodies",
    "comments",
    "code",
    "filenames",
    "credentials",
    "employee_rankings",
    "person_comparisons",
)
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_PROVENANCE_REASONS = {
    "missing_budget_plan",
    "missing_scorecard",
    "missing_connector_provenance",
    "missing_association_rule",
    "unverified_connector_authority",
}


def _timestamp(value: object) -> datetime:
    if type(value) is not str:
        raise PortfolioError("unavailable")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PortfolioError("unavailable") from None
    if instant.tzinfo != timezone.utc:
        raise PortfolioError("unavailable")
    return instant


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _unique(values: list[object]) -> list[object]:
    return [
        json.loads(item)
        for item in sorted({canonical(value) for value in values})
    ]


class PortfolioRoleViewRepository:
    """Read aggregate evidence without granting access to raw owners."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        dsn: str,
        budgets: WorkBudgetRepository,
        scorecards: ScorecardRepository,
        connection_pool=None,
        read_only: bool = False,
    ) -> None:
        self.config = config
        self._dsn = dsn
        self._pool = connection_pool
        self._budgets = budgets
        self._scorecards = scorecards
        self._read_only = read_only

    def authorize_operation(
        self, principal: PortfolioPrincipal, operation: str
    ) -> tuple[str, str, str]:
        """Authorize before query parsing, planning, or storage acquisition."""

        expected = _OPERATIONS.get(operation)
        control = self.config.portfolio_control
        if (
            expected is None
            or self._read_only
            or control is None
            or not isinstance(principal, PortfolioPrincipal)
        ):
            raise PortfolioError("forbidden")
        required_role, view, resource = expected
        if required_role not in principal.roles or not any(
            (binding.organization_id, binding.actor_id, binding.roles)
            == (principal.organization_id, principal.actor_id, principal.roles)
            for binding in control.role_bindings
        ):
            raise PortfolioError("forbidden")
        if required_role == "team_lead":
            if principal.team_id is None or not any(
                (identity.organization_id, identity.actor_id, identity.team_id)
                == (principal.organization_id, principal.actor_id, principal.team_id)
                for identity in (
                    *self.config.identities_by_token.values(),
                    *self.config.identities_by_subject.values(),
                )
            ):
                raise PortfolioError("forbidden")
        return required_role, view, resource

    @contextmanager
    def _transaction(self, organization_id: str):
        with portfolio_transaction(
            self.config,
            organization_id,
            dsn=self._dsn,
            connection_pool=self._pool,
            tables=ROLE_VIEW_TABLES,
            statement_timeout_ms=5_000,
            mutable_tables=frozenset({BUDGET_ACTIVE_TABLE}),
        ) as sql:
            yield sql

    @staticmethod
    def _authority_digest(principal: PortfolioPrincipal) -> str:
        return hashlib.sha256(principal.cursor_authority.encode("ascii")).hexdigest()

    @staticmethod
    def _sequences(sql, organization_id: str, resource: str) -> tuple[int, int]:
        budget = int(sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence "
            "FROM portfolio_work_budget_audit_events WHERE organization_id=?",
            (organization_id,),
        )["sequence"])
        scorecard = int(sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence "
            "FROM portfolio_scorecard_audit_events WHERE organization_id=?",
            (organization_id,),
        )["sequence"])
        return (budget, scorecard) if resource == "budget" else (scorecard, budget)

    @staticmethod
    def _audit_sequence(sql, organization_id: str) -> int:
        maximum = sql.one(
            "SELECT COALESCE(MAX(sequence),0) AS sequence FROM "
            "portfolio_role_view_audit_events WHERE organization_id=?",
            (organization_id,),
        )["sequence"]
        if type(maximum) is not int or not 0 <= maximum < 9_223_372_036_854_775_807:
            raise PortfolioError("unavailable")
        return maximum + 1

    @staticmethod
    def _restore_cursor(
        sql,
        principal: PortfolioPrincipal,
        operation: str,
        reader_role: str,
        query: Mapping[str, object],
        now: str,
    ) -> tuple[dict[str, object], str, int, int, tuple[str, str] | None, int]:
        cursor_id = query.get("cursor")
        if cursor_id is None:
            filters = {key: value for key, value in query.items() if key != "limit"}
            return filters, now, -1, -1, None, int(query.get("limit", 50))
        row = sql.one(
            "SELECT * FROM portfolio_role_view_cursors WHERE organization_id=? AND cursor_id=?",
            (principal.organization_id, cursor_id),
        )
        scope_id = principal.team_id if reader_role == "team_lead" else None
        if (
            row is None
            or row["actor_id"] != principal.actor_id
            or row["authority_digest"]
            != PortfolioRoleViewRepository._authority_digest(principal)
            or row["reader_role"] != reader_role
            or row["query_class"] != operation.removeprefix("list_").removesuffix("s")
            or row["scope_id"] != scope_id
            or row["schema_id"] != _PAGE_SCHEMA_ID
            or row["schema_version"] != 1
            or (
                "limit" in query
                and query["limit"] != row["page_limit"]
            )
            or _timestamp(now) > _timestamp(row["expires_at"])
        ):
            raise PortfolioError("cursor_invalid")
        try:
            filters = json.loads(row["filters_json"])
            if (
                type(filters) is not dict
                or canonical(filters) != row["filters_json"]
                or "cursor" in filters
                or "limit" in filters
            ):
                raise ValueError
            filters = query_parameters(urlencode(filters), operation)
        except (PortfolioError, TypeError, ValueError, json.JSONDecodeError):
            raise PortfolioError("unavailable") from None
        return (
            filters,
            row["as_of"],
            int(row["snapshot_sequence"]),
            int(row["companion_snapshot_sequence"]),
            (row["after_at"], row["after_id"]),
            int(row["page_limit"]),
        )

    @staticmethod
    def _team_scopes(sql, principal: PortfolioPrincipal) -> set[tuple[str, int]] | None:
        if "team_lead" not in principal.roles:
            return None
        rows = [dict(row) for row in sql.execute(
            "SELECT work_scope_id,version,parent_work_scope_id,parent_version,owner_team_id "
            "FROM portfolio_work_scope_versions WHERE organization_id=? "
            "ORDER BY work_scope_id,version LIMIT ?",
            (principal.organization_id, _MAX_SCOPE_ROWS + 1),
        ).fetchall()]
        if len(rows) > _MAX_SCOPE_ROWS:
            raise PortfolioError("unavailable")
        by_ref = {(row["work_scope_id"], row["version"]): row for row in rows}
        allowed: set[tuple[str, int]] = set()
        for reference in by_ref:
            current = by_ref[reference]
            for _ in range(3):
                owner = current["owner_team_id"]
                if owner is not None:
                    if owner == principal.team_id:
                        allowed.add(reference)
                    break
                parent = current["parent_work_scope_id"]
                if parent is None:
                    break
                parent_ref = (parent, current["parent_version"])
                current = by_ref.get(parent_ref)
                if current is None:
                    raise PortfolioError("unavailable")
            else:
                raise PortfolioError("unavailable")
        return allowed

    @staticmethod
    def _budget_rows(
        sql,
        organization_id: str,
        as_of: str,
        snapshot: int,
        filters: Mapping[str, object],
        after: tuple[str, str] | None,
    ) -> list[dict[str, object]]:
        where = [
            "e.organization_id=?",
            "e.committed_at<=?",
            "EXISTS (SELECT 1 FROM portfolio_work_budget_audit_events a "
            "WHERE a.organization_id=e.organization_id AND a.operation='activate' "
            "AND a.entity_id=e.budget_plan_id AND a.entity_version=e.current_version "
            "AND a.occurred_at=e.committed_at AND a.sequence<=?)",
            "NOT EXISTS (SELECT 1 FROM portfolio_work_budget_activation_events n "
            "JOIN portfolio_work_budget_audit_events na ON "
            "na.organization_id=n.organization_id AND na.operation='activate' "
            "AND na.entity_id=n.budget_plan_id AND na.entity_version=n.current_version "
            "AND na.occurred_at=n.committed_at WHERE n.organization_id=e.organization_id "
            "AND n.budget_plan_id=e.budget_plan_id AND n.activation_generation>e.activation_generation "
            "AND n.committed_at<=? AND na.sequence<=?)",
        ]
        values: list[object] = [organization_id, as_of, snapshot, as_of, snapshot]
        if "work_scope_id" in filters:
            where.append("p.work_scope_id=?")
            values.append(filters["work_scope_id"])
        if "start_at" in filters:
            where.extend(("p.window_start_at>=?", "p.window_end_at<=?"))
            values.extend((filters["start_at"], filters["end_at"]))
        if after is not None:
            where.append("(e.committed_at<? OR (e.committed_at=? AND e.budget_plan_id<?))")
            values.extend((after[0], after[0], after[1]))
        rows = sql.execute(
            "SELECT e.budget_plan_id,e.current_version,e.activation_generation,e.committed_at,"
            "p.work_scope_id,p.work_scope_version,p.window_start_at,p.window_end_at "
            "FROM portfolio_work_budget_activation_events e JOIN "
            "portfolio_work_budget_plan_versions p ON p.organization_id=e.organization_id "
            "AND p.budget_plan_id=e.budget_plan_id AND p.version=e.current_version WHERE "
            + " AND ".join(where)
            + " ORDER BY e.committed_at DESC,e.budget_plan_id DESC LIMIT ?",
            (*values, _MAX_CANDIDATES + 1),
        ).fetchall()
        if len(rows) > _MAX_CANDIDATES:
            raise PortfolioError("unavailable")
        return [dict(row) for row in rows]

    @staticmethod
    def _scorecard_rows(
        sql,
        organization_id: str,
        snapshot: int,
        filters: Mapping[str, object],
        after: tuple[str, str] | None,
    ) -> list[dict[str, object]]:
        where = [
            "s.organization_id=?",
            "s.sequence<=?",
            "NOT EXISTS (SELECT 1 FROM portfolio_model_scorecard_snapshots n "
            "WHERE n.organization_id=s.organization_id AND n.scorecard_id=s.scorecard_id "
            "AND n.version>s.version AND n.sequence<=?)",
        ]
        values: list[object] = [organization_id, snapshot, snapshot]
        if "work_scope_id" in filters:
            where.append("s.work_scope_id=?")
            values.append(filters["work_scope_id"])
        if "start_at" in filters:
            where.extend(("s.window_start_at>=?", "s.window_end_at<=?"))
            values.extend((filters["start_at"], filters["end_at"]))
        if after is not None:
            where.append("(s.generated_at<? OR (s.generated_at=? AND s.scorecard_id<?))")
            values.extend((after[0], after[0], after[1]))
        rows = sql.execute(
            "SELECT s.* FROM portfolio_model_scorecard_snapshots s WHERE "
            + " AND ".join(where)
            + " ORDER BY s.generated_at DESC,s.scorecard_id DESC LIMIT ?",
            (*values, _MAX_CANDIDATES + 1),
        ).fetchall()
        if len(rows) > _MAX_CANDIDATES:
            raise PortfolioError("unavailable")
        return [dict(row) for row in rows]

    @staticmethod
    def _active_budget_refs(
        sql,
        organization_id: str,
        work_scope: tuple[str, int],
        as_of: str,
        snapshot: int,
    ) -> list[dict[str, object]]:
        rows = PortfolioRoleViewRepository._budget_rows(
            sql,
            organization_id,
            as_of,
            snapshot,
            {"work_scope_id": work_scope[0]},
            None,
        )
        result = []
        for row in rows:
            if row["work_scope_version"] != work_scope[1]:
                continue
            plan = WorkBudgetRepository._plan(
                sql,
                organization_id,
                row["budget_plan_id"],
                row["current_version"],
            )
            if plan is None:
                raise PortfolioError("unavailable")
            result.append({
                "id": plan["budget_plan_id"],
                "version": plan["version"],
                "content_digest": plan["content_digest"],
            })
        return _unique(result)

    @staticmethod
    def _matching_scorecard(
        sql,
        organization_id: str,
        work_scope: tuple[str, int],
        snapshot: int,
    ) -> tuple[dict[str, object], dict[str, object], dict[str, object]] | None:
        row = sql.one(
            "SELECT * FROM portfolio_model_scorecard_snapshots s WHERE "
            "s.organization_id=? AND s.work_scope_id=? AND s.work_scope_version=? "
            "AND s.sequence<=? AND NOT EXISTS (SELECT 1 FROM "
            "portfolio_model_scorecard_snapshots n WHERE n.organization_id=s.organization_id "
            "AND n.scorecard_id=s.scorecard_id AND n.version>s.version AND n.sequence<=?) "
            "ORDER BY s.generated_at DESC,s.scorecard_id DESC LIMIT 1",
            (organization_id, work_scope[0], work_scope[1], snapshot, snapshot),
        )
        if row is None:
            return None
        stored = dict(row)
        evaluation = ScorecardRepository._stored(stored)
        try:
            raw_input = json.loads(stored["input_json"])
            if canonical(raw_input) != stored["input_json"]:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PortfolioError("unavailable") from None
        return stored, evaluation["scorecard"], raw_input

    @staticmethod
    def _scorecard_provenance(
        scorecard: Mapping[str, object],
        raw_input: Mapping[str, object],
        budget_plans: list[dict[str, object]],
        snapshot_digest: str,
        configured_connectors: set[str],
    ) -> dict[str, object]:
        cohorts = scorecard["cohorts"]
        raw_cohorts = raw_input.get("cohorts")
        if type(raw_cohorts) is not list:
            raise PortfolioError("unavailable")
        models, policies, rate_cards, association_rules = [], [], [], []
        cost_bases, metric_rules, connectors = [], [], []
        for cohort in cohorts:
            if cohort["actual_model"] is not None:
                models.append(cohort["actual_model"])
            if cohort["policy"] is not None:
                policies.append(cohort["policy"])
            if cohort["rate_card"] is not None:
                rate_cards.append(cohort["rate_card"])
            if cohort["association_rule"] is not None:
                association_rules.append(cohort["association_rule"])
            cost_bases.append(cohort["cost_basis"])
            for component in cohort["cost_components"]:
                cost_bases.append(component["basis"])
                if component["rate_card"] is not None:
                    rate_cards.append(component["rate_card"])
            for metric_name, metric in cohort["metrics"].items():
                if metric["rule"] is not None:
                    metric_rules.append({"metric": metric_name, "rule": metric["rule"]})
            for guardrail_name, guardrail in cohort["guardrails"].items():
                if guardrail["rule"] is not None:
                    metric_rules.append({"metric": guardrail_name, "rule": guardrail["rule"]})
        for cohort in raw_cohorts:
            if type(cohort) is not dict or type(cohort.get("connector_ids")) is not list:
                raise PortfolioError("unavailable")
            connectors.extend(cohort["connector_ids"])
        reasons = []
        if not budget_plans:
            reasons.append("missing_budget_plan")
        connector_values = [
            {
                "id": connector,
                "authority_state": (
                    "configured" if connector in configured_connectors else "declared_unverified"
                ),
            }
            for connector in sorted(set(connectors))
        ]
        if not connector_values:
            reasons.append("missing_connector_provenance")
        elif any(item["authority_state"] != "configured" for item in connector_values):
            reasons.append("unverified_connector_authority")
        if not association_rules:
            reasons.append("missing_association_rule")
        return {
            "models": _unique(models),
            "policies": _unique(policies),
            "budget_plans": budget_plans,
            "rate_cards": _unique(rate_cards),
            "connectors": connector_values,
            "association_rules": _unique(association_rules),
            "cost_bases": sorted(set(cost_bases)),
            "metric_definitions": _unique(metric_rules),
            "snapshot_digest": snapshot_digest,
            "state": "complete" if not reasons else "partial",
            "reason_codes": reasons,
        }

    @staticmethod
    def _budget_provenance(
        sql,
        organization_id: str,
        report: Mapping[str, object],
        matching: tuple[dict[str, object], dict[str, object], dict[str, object]] | None,
        configured_connectors: set[str],
    ) -> dict[str, object]:
        plan = WorkBudgetRepository._plan(
            sql,
            organization_id,
            report["plan"]["id"],
            report["plan"]["version"],
        )
        if plan is None:
            raise PortfolioError("unavailable")
        try:
            models = (
                []
                if plan["allowed_models_json"] is None
                else json.loads(plan["allowed_models_json"])
            )
            if type(models) is not list:
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            raise PortfolioError("unavailable") from None
        rates, rules = [], []
        cost_bases = [report["enforcement"]["cost_basis"], report["forecast"]["cost_basis"]]
        if report["enforcement"]["valuation_rule"] is not None:
            rules.append({"metric": "budget_enforcement", "rule": report["enforcement"]["valuation_rule"]})
        if report["forecast"]["rule"] is not None:
            rules.append({"metric": "forecast", "rule": report["forecast"]["rule"]})
        if report["coverage"]["rule"] is not None:
            rules.append({"metric": "coverage", "rule": report["coverage"]["rule"]})
        for observation in report["financial_observations"]:
            cost_bases.append(observation["basis"])
            if observation["rate_card"] is not None:
                rates.append(observation["rate_card"])
            if observation["allocation_rule"] is not None:
                rules.append({"metric": "allocation", "rule": observation["allocation_rule"]})
        connectors, association_rules, scorecard_ref = [], [], None
        reasons = []
        if matching is None:
            reasons.extend(("missing_scorecard", "missing_connector_provenance", "missing_association_rule"))
        else:
            row, scorecard, raw_input = matching
            scorecard_ref = {
                "id": scorecard["scorecard_id"],
                "version": scorecard["version"],
                "content_digest": row["evaluation_digest"],
            }
            for cohort in raw_input["cohorts"]:
                connectors.extend(cohort["connector_ids"])
            association_rules.extend(
                cohort["association_rule"] for cohort in scorecard["cohorts"]
                if cohort["association_rule"] is not None
            )
            if not connectors:
                reasons.append("missing_connector_provenance")
            if not association_rules:
                reasons.append("missing_association_rule")
        connector_values = [
            {
                "id": connector,
                "authority_state": (
                    "configured" if connector in configured_connectors else "declared_unverified"
                ),
            }
            for connector in sorted(set(connectors))
        ]
        if any(item["authority_state"] != "configured" for item in connector_values):
            reasons.append("unverified_connector_authority")
        return {
            "models": _unique(list(models)),
            "policies": [report["policy"]],
            "budget_plans": [report["plan"]],
            "rate_cards": _unique(rates),
            "connectors": connector_values,
            "association_rules": _unique(association_rules),
            "cost_bases": sorted(set(cost_bases)),
            "metric_definitions": _unique(rules),
            "scorecard": scorecard_ref,
            "snapshot_digest": report["input_snapshot_digest"],
            "state": "complete" if not reasons else "partial",
            "reason_codes": reasons,
        }

    @staticmethod
    def _freshness(payload: Mapping[str, object], resource: str, as_of: str) -> dict[str, object]:
        if resource == "scorecard":
            expires = payload["expires_at"]
            return {
                "generated_at": _utc(_timestamp(payload["generated_at"])),
                "expires_at": _utc(_timestamp(expires)),
                "review_after": _utc(_timestamp(payload["review_after"])),
                "state": "expired" if _timestamp(as_of) >= _timestamp(expires) else "current",
            }
        window_end = payload["window"]["end_at"]
        return {
            "generated_at": _utc(_timestamp(payload["generated_at"])),
            "expires_at": _utc(_timestamp(window_end)),
            "review_after": None,
            "state": "historical" if _timestamp(as_of) >= _timestamp(window_end) else "current",
        }

    @classmethod
    def _item(
        cls,
        *,
        view: str,
        resource: str,
        reader_role: str,
        principal: PortfolioPrincipal,
        payload: dict[str, object],
        provenance: dict[str, object],
        as_of: str,
    ) -> dict[str, object]:
        work_scope = payload["work_scope"]
        state = payload["state"] if resource == "scorecard" else (
            "inconclusive" if payload["enforcement"]["reason_code"] != "known" else "eligible"
        )
        evidence_level = payload["evidence_level"] if resource == "scorecard" else "descriptive"
        delivery = "inconclusive" if state == "inconclusive" else provenance["state"]
        return {
            "schema_id": _ITEM_SCHEMA_ID,
            "schema_version": 1,
            "view": view,
            "resource": resource,
            "organization_id": principal.organization_id,
            "team_id": principal.team_id if reader_role == "team_lead" else None,
            "work_scope": work_scope,
            "window": payload["window"],
            "delivery_state": delivery,
            "evidence_level": evidence_level,
            "freshness": cls._freshness(payload, resource, as_of),
            "provenance": provenance,
            "exclusions": list(_EXCLUSIONS),
            "payload": payload,
        }

    @staticmethod
    def _validate_budget_report(report: Mapping[str, object], principal: PortfolioPrincipal, reader_role: str) -> None:
        required = {
            "schema_id", "schema_version", "organization_id", "report_id", "reader_role",
            "reader_scope_digest", "work_scope", "plan", "activation_generation", "policy",
            "window", "as_of", "generated_at", "input_snapshot_digest", "plan_amount",
            "currency", "plan_change", "enforcement", "financial_observations",
            "observation_combination", "forecast", "coverage",
        }
        if (
            type(report) is not dict
            or set(report) != required
            or report["schema_id"] != "hormuz.work-budget-report"
            or report["schema_version"] != 2
            or report["organization_id"] != principal.organization_id
            or report["reader_role"] != reader_role
            or report["reader_scope_digest"]
            != hashlib.sha256(principal.cursor_authority.encode("ascii")).hexdigest()
        ):
            raise PortfolioError("unavailable")

    @staticmethod
    def _validate_provenance(
        provenance: object,
        *,
        resource: str,
    ) -> None:
        common = {
            "models", "policies", "budget_plans", "rate_cards", "connectors",
            "association_rules", "cost_bases", "metric_definitions",
            "snapshot_digest", "state", "reason_codes",
        }
        expected = common | ({"scorecard"} if resource == "budget" else set())
        if type(provenance) is not dict or set(provenance) != expected:
            raise ValueError
        bounded = {
            "models": 100,
            "policies": 100,
            "budget_plans": 100,
            "rate_cards": 100,
            "connectors": 100,
            "association_rules": 100,
            "cost_bases": 6,
            "metric_definitions": 1200,
            "reason_codes": 5,
        }
        for field, maximum in bounded.items():
            values = provenance[field]
            if (
                type(values) is not list
                or len(values) > maximum
                or len({canonical(value) for value in values}) != len(values)
            ):
                raise ValueError
        for model in provenance["models"]:
            validate(model, "model_ref")
        for rule in provenance["association_rules"]:
            validate(rule, "rule_ref")
        for basis in provenance["cost_bases"]:
            validate(basis, "cost_basis")
        for reference in (
            *provenance["policies"],
            *provenance["budget_plans"],
            *provenance["rate_cards"],
        ):
            if (
                type(reference) is not dict
                or not {"version"}.issubset(reference)
                or set(reference) - {"id", "version", "content_digest"}
                or (
                    "id" in reference
                    and (type(reference["id"]) is not str or _OPAQUE.fullmatch(reference["id"]) is None)
                )
                or (
                    type(reference["version"]) not in {str, int}
                    or isinstance(reference["version"], bool)
                )
                or (
                    type(reference["version"]) is str
                    and _OPAQUE.fullmatch(reference["version"]) is None
                )
                or (
                    type(reference["version"]) is int
                    and not 1 <= reference["version"] <= 2_147_483_647
                )
                or (
                    "content_digest" in reference
                    and (
                        type(reference["content_digest"]) is not str
                        or _DIGEST.fullmatch(reference["content_digest"]) is None
                    )
                )
            ):
                raise ValueError
        connectors = provenance["connectors"]
        if any(
            type(value) is not dict
            or set(value) != {"id", "authority_state"}
            or type(value["id"]) is not str
            or _OPAQUE.fullmatch(value["id"]) is None
            or value["authority_state"] not in {"configured", "declared_unverified"}
            for value in connectors
        ):
            raise ValueError
        if any(
            type(value) is not dict
            or set(value) != {"metric", "rule"}
            or type(value["metric"]) is not str
            or _OPAQUE.fullmatch(value["metric"]) is None
            or type(value["rule"]) is not dict
            or len(value["rule"]) != 3
            for value in provenance["metric_definitions"]
        ):
            raise ValueError
        reasons = provenance["reason_codes"]
        if any(reason not in _PROVENANCE_REASONS for reason in reasons):
            raise ValueError
        if (
            provenance["state"] not in {"complete", "partial"}
            or (provenance["state"] == "complete") != (not reasons)
            or type(provenance["snapshot_digest"]) is not str
            or _DIGEST.fullmatch(provenance["snapshot_digest"]) is None
        ):
            raise ValueError
        if resource == "budget":
            scorecard = provenance["scorecard"]
            if scorecard is not None:
                if (
                    type(scorecard) is not dict
                    or set(scorecard) != {"id", "version", "content_digest"}
                    or type(scorecard["id"]) is not str
                    or _OPAQUE.fullmatch(scorecard["id"]) is None
                    or type(scorecard["version"]) is not int
                    or not 1 <= scorecard["version"] <= 2_147_483_647
                    or type(scorecard["content_digest"]) is not str
                    or _DIGEST.fullmatch(scorecard["content_digest"]) is None
                ):
                    raise ValueError

    @classmethod
    def _public(
        cls,
        page: dict[str, object],
        principal: PortfolioPrincipal,
    ) -> dict[str, object]:
        try:
            required = {
                "schema_id", "schema_version", "organization_id", "reader_role", "view",
                "resource", "scope", "grouping", "as_of", "snapshot", "items",
                "result_count", "has_more", "next_cursor",
            }
            if (
                set(page) != required
                or page["schema_id"] != _PAGE_SCHEMA_ID
                or page["schema_version"] != 1
                or page["grouping"] != "work_scope"
                or page["result_count"] != len(page["items"])
                or not 0 <= page["result_count"] <= 100
                or type(page["has_more"]) is not bool
                or page["organization_id"] != principal.organization_id
                or page["reader_role"] not in {
                    "finance_viewer", "platform_viewer", "team_lead",
                }
                or (page["view"], page["resource"], page["reader_role"])
                not in {
                    ("finance", "budget", "finance_viewer"),
                    ("platform", "scorecard", "platform_viewer"),
                    ("team", "budget", "team_lead"),
                    ("team", "scorecard", "team_lead"),
                }
                or type(page["scope"]) is not dict
                or set(page["scope"]) != {"kind", "id"}
                or page["scope"] != (
                    {"kind": "team", "id": principal.team_id}
                    if page["reader_role"] == "team_lead"
                    else {"kind": "organization", "id": principal.organization_id}
                )
                or _utc(_timestamp(page["as_of"])) != page["as_of"]
                or type(page["snapshot"]) is not dict
                or set(page["snapshot"]) != {
                    "primary_sequence", "companion_sequence",
                }
                or any(
                    type(page["snapshot"][field]) is not int
                    or not 0 <= page["snapshot"][field] <= 9_223_372_036_854_775_807
                    for field in ("primary_sequence", "companion_sequence")
                )
                or (page["next_cursor"] is not None) != page["has_more"]
                or (
                    page["next_cursor"] is not None
                    and (
                        type(page["next_cursor"]) is not str
                        or _DIGEST.fullmatch(page["next_cursor"]) is None
                    )
                )
            ):
                raise ValueError
            for item in page["items"]:
                expected = {
                    "schema_id", "schema_version", "view", "resource", "organization_id",
                    "team_id", "work_scope", "window", "delivery_state", "evidence_level",
                    "freshness", "provenance", "exclusions", "payload",
                }
                if (
                    type(item) is not dict
                    or set(item) != expected
                    or item["schema_id"] != _ITEM_SCHEMA_ID
                    or item["schema_version"] != 1
                    or item["view"] != page["view"]
                    or item["resource"] != page["resource"]
                    or item["organization_id"] != page["organization_id"]
                    or item["exclusions"] != list(_EXCLUSIONS)
                    or item["team_id"] != (
                        principal.team_id if page["reader_role"] == "team_lead" else None
                    )
                    or item["delivery_state"] not in {
                        "complete", "partial", "inconclusive",
                    }
                    or type(item["freshness"]) is not dict
                    or set(item["freshness"]) != {
                        "generated_at", "expires_at", "review_after", "state",
                    }
                    or item["freshness"]["state"] not in {
                        "current", "expired", "historical",
                    }
                    or any(
                        value is not None and _utc(_timestamp(value)) != value
                        for value in (
                            item["freshness"]["generated_at"],
                            item["freshness"]["expires_at"],
                            item["freshness"]["review_after"],
                        )
                    )
                ):
                    raise ValueError
                cls._validate_provenance(
                    item["provenance"], resource=item["resource"],
                )
                if item["resource"] == "scorecard":
                    validate(item["payload"], "hormuz.model-scorecard")
                    expected_delivery = (
                        "inconclusive"
                        if item["payload"]["state"] == "inconclusive"
                        else item["provenance"]["state"]
                    )
                else:
                    cls._validate_budget_report(
                        item["payload"],
                        principal,
                        page["reader_role"],
                    )
                    expected_delivery = (
                        "inconclusive"
                        if item["payload"]["enforcement"]["reason_code"] != "known"
                        else item["provenance"]["state"]
                    )
                if (
                    item["work_scope"] != item["payload"]["work_scope"]
                    or item["window"] != item["payload"]["window"]
                    or item["evidence_level"] != (
                        item["payload"]["evidence_level"]
                        if item["resource"] == "scorecard"
                        else "descriptive"
                    )
                    or item["delivery_state"] != expected_delivery
                    or item["freshness"]
                    != cls._freshness(item["payload"], item["resource"], page["as_of"])
                ):
                    raise ValueError
            if len(canonical(page).encode("ascii")) > RESPONSE_BYTES:
                raise ValueError
        except (KeyError, TypeError, ValueError, PortfolioError):
            raise PortfolioError("unavailable") from None
        return page

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
    ) -> tuple[int, dict[str, object]]:
        reader_role, view, resource = self.authorize_operation(principal, operation)
        if (
            operation not in ROLE_VIEW_OPERATIONS
            or scope_id is not None
            or route("GET", path) != (operation, None)
            or body is not None
            or idempotency_key is not None
        ):
            raise PortfolioError("invalid_request")
        query = query_parameters(urlencode(query), operation)
        with self._transaction(principal.organization_id) as sql:
            now = sql.now()
            filters, as_of, snapshot, companion, after, limit = self._restore_cursor(
                sql, principal, operation, reader_role, query, now,
            )
            if snapshot < 0:
                snapshot, companion = self._sequences(
                    sql, principal.organization_id, resource,
                )
            team_scopes = (
                self._team_scopes(sql, principal)
                if reader_role == "team_lead"
                else None
            )
            if resource == "budget":
                candidates = self._budget_rows(
                    sql, principal.organization_id, as_of, snapshot, filters, after,
                )
            else:
                candidates = self._scorecard_rows(
                    sql, principal.organization_id, snapshot, filters, after,
                )
            if team_scopes is not None:
                candidates = [
                    row for row in candidates
                    if (row["work_scope_id"], row["work_scope_version"]) in team_scopes
                ]
            selected, has_more = candidates[:limit], len(candidates) > limit
            items = []
            configured_connectors = {
                binding.connector_id
                for binding in self.config.portfolio_control.connectors
                if binding.organization_id == principal.organization_id
            }
            for row in selected:
                work_scope = (row["work_scope_id"], row["work_scope_version"])
                if resource == "budget":
                    try:
                        payload = self._budgets._current_report_in_transaction(
                            sql,
                            principal,
                            row["budget_plan_id"],
                            as_of=as_of,
                            generated_at=as_of,
                            reader_role=reader_role,
                            report_id=hashlib.sha256(canonical({
                                "organization_id": principal.organization_id,
                                "actor_id": principal.actor_id,
                                "reader_role": reader_role,
                                "budget_plan_id": row["budget_plan_id"],
                                "budget_plan_version": row["current_version"],
                                "as_of": as_of,
                                "snapshot_sequence": snapshot,
                            }).encode("ascii")).hexdigest(),
                        )
                    except BudgetRepositoryError as error:
                        raise PortfolioError(error.code) from None
                    self._validate_budget_report(payload, principal, reader_role)
                    matching = self._matching_scorecard(
                        sql, principal.organization_id, work_scope, companion,
                    )
                    provenance = self._budget_provenance(
                        sql,
                        principal.organization_id,
                        payload,
                        matching,
                        configured_connectors,
                    )
                else:
                    evaluation = ScorecardRepository._stored(row)
                    payload = evaluation["scorecard"]
                    try:
                        raw_input = json.loads(row["input_json"])
                    except (TypeError, ValueError, json.JSONDecodeError):
                        raise PortfolioError("unavailable") from None
                    budget_refs = self._active_budget_refs(
                        sql, principal.organization_id, work_scope, as_of, companion,
                    )
                    provenance = self._scorecard_provenance(
                        payload,
                        raw_input,
                        budget_refs,
                        row["evaluation_digest"],
                        configured_connectors,
                    )
                items.append(self._item(
                    view=view,
                    resource=resource,
                    reader_role=reader_role,
                    principal=principal,
                    payload=payload,
                    provenance=provenance,
                    as_of=as_of,
                ))
            next_cursor = None
            if has_more:
                last = selected[-1]
                next_cursor = uuid4().hex + uuid4().hex
                after_at = last["committed_at"] if resource == "budget" else last["generated_at"]
                after_id = last["budget_plan_id"] if resource == "budget" else last["scorecard_id"]
                sql.insert(CURSOR_TABLE, {
                    "organization_id": principal.organization_id,
                    "cursor_id": next_cursor,
                    "actor_id": principal.actor_id,
                    "authority_digest": self._authority_digest(principal),
                    "reader_role": reader_role,
                    "query_class": operation.removeprefix("list_").removesuffix("s"),
                    "scope_id": principal.team_id if reader_role == "team_lead" else None,
                    "schema_id": _PAGE_SCHEMA_ID,
                    "schema_version": 1,
                    "as_of": as_of,
                    "expires_at": _utc(_timestamp(as_of) + timedelta(seconds=_CURSOR_SECONDS)),
                    "snapshot_sequence": snapshot,
                    "companion_snapshot_sequence": companion,
                    "page_limit": limit,
                    "after_at": after_at,
                    "after_id": after_id,
                    "filters_json": canonical(filters),
                })
            filter_digest = hashlib.sha256(canonical(filters).encode("ascii")).hexdigest()
            sql.insert(AUDIT_TABLE, {
                "organization_id": principal.organization_id,
                "event_id": str(uuid4()),
                "sequence": self._audit_sequence(sql, principal.organization_id),
                "actor_id": principal.actor_id,
                "reader_role": reader_role,
                "query_class": operation.removeprefix("list_").removesuffix("s"),
                "scope_kind": "team" if reader_role == "team_lead" else "organization",
                "scope_id": principal.team_id if reader_role == "team_lead" else None,
                "filter_digest": filter_digest,
                "snapshot_sequence": snapshot,
                "companion_snapshot_sequence": companion,
                "result_count": len(items),
                "partial_count": sum(
                    item["provenance"]["state"] == "partial" for item in items
                ),
                "occurred_at": now,
            })
            page = {
                "schema_id": _PAGE_SCHEMA_ID,
                "schema_version": 1,
                "organization_id": principal.organization_id,
                "reader_role": reader_role,
                "view": view,
                "resource": resource,
                "scope": {
                    "kind": "team" if reader_role == "team_lead" else "organization",
                    "id": principal.team_id if reader_role == "team_lead" else principal.organization_id,
                },
                "grouping": "work_scope",
                "as_of": as_of,
                "snapshot": {
                    "primary_sequence": snapshot,
                    "companion_sequence": companion,
                },
                "items": items,
                "result_count": len(items),
                "has_more": has_more,
                "next_cursor": next_cursor,
            }
            # Validate before the transaction commits its read audit and cursor.
            self._public(page, principal)
        return 200, page
