"""Authorized immutable policy recommendations and explicit decisions.

Generation is an internal, typed operation.  The public HTTP surface exposes
only audited reads and append-only administrator decisions.  Acceptance never
mutates a policy or budget; the existing activation paths remain separate.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
from typing import Any, Mapping
from urllib.parse import urlencode
from uuid import uuid4

from ._budget_schema import (
    MAX_ACTIVE_BUDGET_PLANS,
    BudgetIntegrityError,
    BudgetPlanIntegrityError,
    validate_budget_plan_row,
    validate_budget_pointer_row,
)
from ._portfolio_sql import portfolio_transaction
from ._recommendation_schema import (
    AUDIT_TABLE,
    CURSOR_TABLE,
    EVENT_TABLE,
    SNAPSHOT_TABLE,
    TABLE_DDL,
)
from .budget_repository import BudgetRepositoryError, WorkBudgetRepository
from .config import GatewayConfig, Identity
from .policy_analysis import (
    PolicyAnalysisError,
    compare_policy_documents,
    evaluate_policy_scenario_suite,
    preview_policy_request,
)
from .policy_document import PolicyDocument, local_policy_content_sha256
from .policy_runtime import PolicyRuntime
from .policy_scenarios import PolicyScenarioSuite
from .portfolio_config import PortfolioPrincipal
from .portfolio_wire import (
    RECOMMENDATION_OPERATIONS,
    RESPONSE_BYTES,
    PortfolioError,
    canonical,
    query_parameters,
    route,
    validate,
)
from .recommendation_kernel import (
    MAX_EVALUATION_BYTES,
    RecommendationKernelError,
    build_recommendation_evaluation,
    parse_generation_request,
)
from .scorecard_repository import ScorecardRepository


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_PREVIEW_FIELDS = frozenset({
    "actor_id",
    "client",
    "protocol",
    "requested_model",
    "requested_output_tokens",
})
_APPLIED_FIELDS = frozenset({
    "activation_event_id",
    "activated_policy_digest",
    "activated_budget_plan",
    "activation_digest",
})
_MAX_CONFLICTS = 1000
_CURSOR_TTL = timedelta(minutes=5)


def _sha256(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("ascii")).hexdigest()


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise PortfolioError("invalid_request")
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise PortfolioError("invalid_request")
    return value


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        raise PortfolioError("invalid_request")
    return value


def _instant(value: object, *, persisted: bool = False) -> datetime:
    error = "unavailable" if persisted else "invalid_request"
    if type(value) is not str:
        raise PortfolioError(error)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PortfolioError(error) from None
    if parsed.tzinfo is None:
        raise PortfolioError(error)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _persisted_timestamp(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PortfolioError("unavailable")
        return _timestamp(value)
    return _timestamp(_instant(value, persisted=True))


def _generation_digest(evaluation: Mapping[str, object]) -> str:
    """Hash normalized generation meaning while excluding server commit time."""

    try:
        stable = json.loads(canonical(evaluation))
        stable["recommendation"].pop("created_at")
        # Evaluation time is server-owned. Stable replay is instead bound to
        # the closed request and usage context retained in bindings.
        stable["pre_apply_evidence"].pop("request_preview")
    except (KeyError, TypeError, PortfolioError, json.JSONDecodeError):
        raise PortfolioError("unavailable") from None
    return _sha256(stable)


class RecommendationRepository:
    """Own recommendation generation, audited reads, and lifecycle events."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        dsn: str,
        environ: Mapping[str, str] | None = None,
        connection_pool=None,
        read_only: bool = False,
    ) -> None:
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
            raise PortfolioError("forbidden")

    @contextmanager
    def _transaction(self, principal: PortfolioPrincipal):
        # Authorization precedes both parsing at callers and storage access.
        self._authorize(principal)
        with portfolio_transaction(
            self.config,
            principal.organization_id,
            dsn=self._dsn,
            connection_pool=self._pool,
            tables=TABLE_DDL,
            statement_timeout_ms=5000,
            budget_lock=True,
            policy_lock=self.config.policy_control.mode == "postgresql",
        ) as sql:
            self._authorize(principal)
            yield sql
            self._authorize(principal)

    def _identity(self, organization: str, actor_id: str) -> Identity:
        matches = {
            (identity.organization_id, identity.actor_id): identity
            for identity in (
                *self.config.identities_by_token.values(),
                *self.config.identities_by_subject.values(),
            )
        }
        identity = matches.get((organization, actor_id))
        if identity is None:
            raise PortfolioError("invalid_request")
        return identity

    def _active_policy(self, principal: PortfolioPrincipal) -> dict[str, str]:
        identity = self._identity(principal.organization_id, principal.actor_id)
        try:
            snapshot = PolicyRuntime(
                self.config,
                environ=self._environment,
                connection_pool=self._pool,
            ).snapshot_for(identity)
            digest = snapshot.content_sha256 or local_policy_content_sha256(
                self.config
            )
        except Exception:
            raise PortfolioError("unavailable") from None
        if (
            type(snapshot.policy_version) is not str
            or _ID.fullmatch(snapshot.policy_version) is None
            or type(digest) is not str
            or _DIGEST.fullmatch(digest) is None
        ):
            raise PortfolioError("unavailable")
        return {"version": snapshot.policy_version, "digest": digest}

    def _active_policy_in_transaction(
        self,
        sql,
        principal: PortfolioPrincipal,
    ) -> dict[str, str]:
        """Read managed policy identity while its tenant lock is held."""

        if self.config.policy_control.mode != "postgresql":
            return self._active_policy(principal)
        row = sql.one(
            "SELECT active.version_id, versions.content_sha256 "
            "FROM policy_active_versions active "
            "JOIN policy_versions versions "
            "ON versions.organization_id=active.organization_id "
            "AND versions.version_id=active.version_id "
            "WHERE active.organization_id=?",
            (principal.organization_id,),
        )
        if (
            row is None
            or type(row["version_id"]) is not str
            or _ID.fullmatch(row["version_id"]) is None
            or type(row["content_sha256"]) is not str
            or _DIGEST.fullmatch(row["content_sha256"]) is None
        ):
            raise PortfolioError("unavailable")
        return {
            "version": row["version_id"],
            "digest": row["content_sha256"],
        }

    @staticmethod
    def _event_sequence(sql, organization: str) -> int:
        row = sql.one(
            f"SELECT COALESCE(MAX(sequence),0) AS sequence FROM {EVENT_TABLE} "
            "WHERE organization_id=?",
            (organization,),
        )
        value = row["sequence"] if row is not None else None
        if type(value) is not int or not 0 <= value < 9_223_372_036_854_775_807:
            raise PortfolioError("unavailable")
        return value + 1

    @staticmethod
    def _audit_sequence(sql, organization: str) -> int:
        row = sql.one(
            f"SELECT COALESCE(MAX(sequence),0) AS sequence FROM {AUDIT_TABLE} "
            "WHERE organization_id=?",
            (organization,),
        )
        value = row["sequence"] if row is not None else None
        if type(value) is not int or not 0 <= value < 9_223_372_036_854_775_807:
            raise PortfolioError("unavailable")
        return value + 1

    @staticmethod
    def _scope_chain(sql, organization: str, scope_ref: Mapping[str, object]):
        scope_id = scope_ref.get("work_scope_id")
        scope_version = scope_ref.get("version")
        if type(scope_id) is not str or type(scope_version) is not int:
            raise PortfolioError("unavailable")
        chain: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        current_id, current_version = scope_id, scope_version
        for _ in range(3):
            exact = sql.one(
                "SELECT * FROM portfolio_work_scope_versions "
                "WHERE organization_id=? AND work_scope_id=? AND version=?",
                (organization, current_id, current_version),
            )
            latest = sql.one(
                "SELECT version,state FROM portfolio_work_scope_versions "
                "WHERE organization_id=? AND work_scope_id=? "
                "ORDER BY version DESC LIMIT 1",
                (organization, current_id),
            )
            if (
                exact is None
                or latest is None
                or latest["version"] != current_version
                or latest["state"] != "active"
                or exact["state"] != "active"
                or exact["kind"] not in {"portfolio", "initiative", "use_case"}
                or (current_id, current_version) in seen
            ):
                raise PortfolioError("version_conflict")
            seen.add((current_id, current_version))
            chain.append(exact)
            parent_id, parent_version = exact["parent_work_scope_id"], exact["parent_version"]
            if parent_id is None:
                if parent_version is not None:
                    raise PortfolioError("unavailable")
                return tuple(chain)
            if type(parent_id) is not str or type(parent_version) is not int:
                raise PortfolioError("unavailable")
            current_id, current_version = parent_id, parent_version
        raise PortfolioError("unavailable")

    @staticmethod
    def _active_budget_bindings(
        sql,
        organization: str,
        scope_chain: tuple[dict[str, Any], ...],
        now: str,
    ) -> list[dict[str, object]]:
        rows = sql.execute(
            "SELECT * FROM portfolio_work_budget_active_plans "
            "WHERE organization_id=? ORDER BY budget_plan_id LIMIT ?",
            (organization, MAX_ACTIVE_BUDGET_PLANS + 1),
        ).fetchall()
        if len(rows) > MAX_ACTIVE_BUDGET_PLANS:
            raise PortfolioError("unavailable")
        scopes = {
            (row["work_scope_id"], row["version"])
            for row in scope_chain
        }
        result: list[dict[str, object]] = []
        for raw_pointer in rows:
            try:
                pointer = validate_budget_pointer_row(dict(raw_pointer))
                active = WorkBudgetRepository._active_budget(
                    sql, organization, pointer["budget_plan_id"], now
                )
            except (BudgetIntegrityError, BudgetRepositoryError):
                raise PortfolioError("unavailable") from None
            if active is None:
                raise PortfolioError("unavailable")
            plan, activation, verified_pointer, _history = active
            if (
                plan["work_scope_id"],
                plan["work_scope_version"],
            ) not in scopes:
                continue
            result.append({
                "budget_plan_id": plan["budget_plan_id"],
                "active_version": plan["version"],
                "activation_generation": verified_pointer["activation_generation"],
                "activation_event_id": activation["activation_event_id"],
                "content_digest": plan["content_digest"],
            })
        return sorted(result, key=canonical)

    @staticmethod
    def _candidate_budget(
        sql,
        organization: str,
        reference: Mapping[str, object],
        scope_ref: Mapping[str, object],
    ) -> dict[str, Any]:
        row = sql.one(
            "SELECT * FROM portfolio_work_budget_plan_versions "
            "WHERE organization_id=? AND budget_plan_id=? AND version=?",
            (organization, reference["id"], reference["version"]),
        )
        if row is None:
            raise PortfolioError("not_found")
        try:
            row = validate_budget_plan_row(row)
        except BudgetPlanIntegrityError:
            raise PortfolioError("unavailable") from None
        if (
            row["work_scope_id"] != scope_ref["work_scope_id"]
            or row["work_scope_version"] != scope_ref["version"]
        ):
            raise PortfolioError("invalid_request")
        return row

    @staticmethod
    def _parse_preview(value: Mapping[str, object]) -> dict[str, object]:
        if type(value) is not dict or set(value) != _PREVIEW_FIELDS:
            raise PortfolioError("invalid_request")
        actor_id = _identifier(value["actor_id"])
        client, protocol, model = value["client"], value["protocol"], value["requested_model"]
        if client not in {"codex", "claude-code"} or protocol not in {"openai", "anthropic"}:
            raise PortfolioError("invalid_request")
        if type(model) is not str or not model or any(mark in model for mark in ("\x00", "\n", "\r")):
            raise PortfolioError("invalid_request")
        output = value["requested_output_tokens"]
        if output is not None and (type(output) is not int or output < 1):
            raise PortfolioError("invalid_request")
        return {
            "actor_id": actor_id,
            "client": client,
            "protocol": protocol,
            "requested_model": model,
            "requested_output_tokens": output,
        }

    @staticmethod
    def _scorecard(sql, organization: str, reference: Mapping[str, object]):
        row = sql.one(
            "SELECT * FROM portfolio_model_scorecard_snapshots "
            "WHERE organization_id=? AND scorecard_id=? AND version=?",
            (organization, reference["id"], reference["version"]),
        )
        if row is None:
            raise PortfolioError("not_found")
        latest = sql.one(
            "SELECT version FROM portfolio_model_scorecard_snapshots "
            "WHERE organization_id=? AND scorecard_id=? "
            "ORDER BY version DESC LIMIT 1",
            (organization, reference["id"]),
        )
        if latest is None or latest["version"] != reference["version"]:
            raise PortfolioError("version_conflict")
        return row, ScorecardRepository._stored(row)

    @staticmethod
    def _stored_snapshot(row: Mapping[str, object]) -> dict[str, object]:
        try:
            recommendation = json.loads(str(row["recommendation_json"]))
            evaluation = json.loads(str(row["evaluation_json"]))
            expected = json.loads(str(row["expected_pre_apply_json"]))
            bindings = json.loads(str(row["bindings_json"]))
            if (
                canonical(recommendation) != row["recommendation_json"]
                or canonical(evaluation) != row["evaluation_json"]
                or canonical(expected) != row["expected_pre_apply_json"]
                or canonical(bindings) != row["bindings_json"]
                or evaluation.get("schema_id") != "hormuz.recommendation-evaluation"
                or evaluation.get("schema_version") != 1
                or evaluation.get("recommendation") != recommendation
                or evaluation.get("pre_apply_evidence") != expected
                or evaluation.get("bindings") != bindings
                or _generation_digest(evaluation) != row["generation_digest"]
            ):
                raise ValueError
            validate(recommendation, "hormuz.policy-recommendation")
            proposal = recommendation["proposal"]
            candidate_budget = proposal["candidate_budget_plan"]
            if (
                recommendation["organization_id"] != row["organization_id"]
                or recommendation["recommendation_id"] != row["recommendation_id"]
                or recommendation["version"] != row["version"]
                or recommendation["work_scope"]["work_scope_id"] != row["work_scope_id"]
                or recommendation["work_scope"]["version"] != row["work_scope_version"]
                or recommendation["scorecard"]["id"] != row["scorecard_id"]
                or recommendation["scorecard"]["version"] != row["scorecard_version"]
                or bindings["scorecard_evaluation_digest"] != row["scorecard_evaluation_digest"]
                or recommendation["policy"]["id"] != row["policy_id"]
                or recommendation["policy"]["version"] != row["policy_version"]
                or recommendation["policy_digest"] != row["policy_digest"]
                or proposal["change_type"] != row["change_type"]
                or proposal["candidate_policy_digest"] != row["candidate_policy_digest"]
                or (None if candidate_budget is None else candidate_budget["id"])
                != row["candidate_budget_plan_id"]
                or (None if candidate_budget is None else candidate_budget["version"])
                != row["candidate_budget_plan_version"]
                or recommendation["evidence_level"] != row["evidence_level"]
                or _timestamp(_instant(recommendation["window"]["start_at"], persisted=True))
                != row["window_start_at"]
                or _timestamp(_instant(recommendation["window"]["end_at"], persisted=True))
                != row["window_end_at"]
                or _timestamp(_instant(recommendation["created_at"], persisted=True))
                != row["created_at"]
                or _timestamp(_instant(recommendation["expires_at"], persisted=True))
                != row["expires_at"]
                or recommendation["supersedes_version"] != row["supersedes_version"]
                or recommendation["state"] != "pending"
                or recommendation["latest_decision_event_id"] is not None
            ):
                raise ValueError
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            PortfolioError,
        ):
            raise PortfolioError("unavailable") from None
        return {
            "recommendation": recommendation,
            "evaluation": evaluation,
            "expected_pre_apply": expected,
            "bindings": bindings,
        }

    @staticmethod
    def _latest_event(
        sql,
        organization: str,
        recommendation_id: str,
        version: int,
        *,
        ceiling: int | None = None,
    ) -> dict[str, Any]:
        statement = (
            f"SELECT * FROM {EVENT_TABLE} WHERE organization_id=? "
            "AND recommendation_id=? AND recommendation_version=?"
        )
        values: list[object] = [organization, recommendation_id, version]
        if ceiling is not None:
            statement += " AND sequence<=?"
            values.append(ceiling)
        statement += " ORDER BY sequence DESC LIMIT 1"
        row = sql.one(statement, tuple(values))
        if row is None:
            raise PortfolioError("unavailable")
        return RecommendationRepository._stored_event(row)

    @staticmethod
    def _stored_event(row: Mapping[str, object]) -> dict[str, Any]:
        stored = dict(row)
        try:
            evidence = json.loads(str(stored["evidence_json"]))
            if canonical(evidence) != stored["evidence_json"]:
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError, PortfolioError):
            raise PortfolioError("unavailable") from None
        stored["evidence"] = evidence
        return stored

    @staticmethod
    def _public(
        row: Mapping[str, object],
        stored: Mapping[str, object],
        event: Mapping[str, object],
    ) -> dict[str, object]:
        result = dict(stored["recommendation"])
        result["pre_apply_evidence"] = stored["expected_pre_apply"]
        event_type = event["event_type"]
        if event_type == "generated":
            result.update({
                "state": "pending",
                "latest_decision_event_id": None,
                "reason_code": "eligible",
            })
        else:
            result.update({
                "state": event["public_state"],
                "latest_decision_event_id": event["event_id"],
                "reason_code": (
                    "accepted" if event_type == "applied" else event["reason_code"]
                ),
            })
        try:
            validate(result, "hormuz.policy-recommendation")
            if len(canonical(result).encode("utf-8")) > RESPONSE_BYTES:
                raise ValueError
        except (PortfolioError, TypeError, ValueError):
            raise PortfolioError("unavailable") from None
        return result

    def _append_event(
        self,
        sql,
        principal: PortfolioPrincipal,
        row: Mapping[str, object],
        *,
        event_type: str,
        public_state: str,
        reason_code: str,
        now: str,
        evidence: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        request_digest: str | None = None,
        sequence: int | None = None,
    ) -> dict[str, Any]:
        event = {
            "organization_id": principal.organization_id,
            "event_id": str(uuid4()),
            "sequence": self._event_sequence(sql, principal.organization_id)
            if sequence is None
            else sequence,
            "actor_id": principal.actor_id,
            "recommendation_id": row["recommendation_id"],
            "recommendation_version": row["version"],
            "event_type": event_type,
            "public_state": public_state,
            "reason_code": reason_code,
            "occurred_at": now,
            "idempotency_key": idempotency_key,
            "request_digest": request_digest,
            "evidence_json": canonical({} if evidence is None else evidence),
        }
        sql.insert(EVENT_TABLE, event)
        event["evidence"] = {} if evidence is None else dict(evidence)
        return event

    def generate(
        self,
        principal: PortfolioPrincipal,
        value: Mapping[str, object],
        *,
        baseline_policy: PolicyDocument,
        candidate_policy: PolicyDocument,
        usage_store,
        preview_request: Mapping[str, object],
        scenario_suite: PolicyScenarioSuite,
    ) -> dict[str, object] | None:
        """Generate or replay one exact content-free recommendation."""

        self._authorize(principal)
        try:
            request = parse_generation_request(value)
            preview_input = self._parse_preview(preview_request)
            if (
                not isinstance(baseline_policy, PolicyDocument)
                or not isinstance(candidate_policy, PolicyDocument)
                or not isinstance(scenario_suite, PolicyScenarioSuite)
                or baseline_policy.organization_id != principal.organization_id
                or candidate_policy.organization_id != principal.organization_id
                or scenario_suite.organization_id != principal.organization_id
            ):
                raise PortfolioError("invalid_request")
            preview_identity = self._identity(
                principal.organization_id, preview_input["actor_id"]
            )
        except (
            RecommendationKernelError,
            PolicyAnalysisError,
            PortfolioError,
            TypeError,
            ValueError,
        ):
            raise PortfolioError("invalid_request") from None

        preflight_policy = self._active_policy(principal)
        if preflight_policy["digest"] != baseline_policy.content_sha256:
            raise PortfolioError("version_conflict")
        organization = principal.organization_id
        with self._transaction(principal) as sql:
            active_policy = self._active_policy_in_transaction(sql, principal)
            if (
                active_policy != preflight_policy
                or active_policy["digest"] != baseline_policy.content_sha256
            ):
                raise PortfolioError("version_conflict")
            now = sql.now()
            try:
                evaluated_at = _instant(now, persisted=True)
                comparison = compare_policy_documents(
                    baseline_policy, candidate_policy
                )
                preview = preview_policy_request(
                    config=self.config,
                    usage_store=usage_store,
                    identity=preview_identity,
                    baseline=baseline_policy,
                    candidate=candidate_policy,
                    client=preview_input["client"],
                    protocol=preview_input["protocol"],
                    requested_model=preview_input["requested_model"],
                    requested_output_tokens=preview_input[
                        "requested_output_tokens"
                    ],
                    evaluated_at=evaluated_at,
                )
                scenarios = evaluate_policy_scenario_suite(
                    config=self.config,
                    usage_store=usage_store,
                    suite=scenario_suite,
                    baseline=baseline_policy,
                    candidate=candidate_policy,
                    evaluated_at=evaluated_at,
                )
            except (PolicyAnalysisError, PortfolioError, TypeError, ValueError):
                raise PortfolioError("invalid_request") from None
            scorecard_row, scorecard_evaluation = self._scorecard(
                sql, organization, request["scorecard"]
            )
            scorecard = scorecard_evaluation["scorecard"]
            scope_chain = self._scope_chain(sql, organization, scorecard["work_scope"])
            budget_bindings = self._active_budget_bindings(
                sql, organization, scope_chain, now
            )
            candidate_budget_digest = None
            candidate_budget_expired = False
            proposal = request["proposal"]
            if proposal["change_type"] == "budget_plan_change":
                candidate = self._candidate_budget(
                    sql,
                    organization,
                    proposal["candidate_budget_plan"],
                    scorecard["work_scope"],
                )
                candidate_budget_expired = (
                    _instant(candidate["window_end_at"], persisted=True)
                    <= _instant(now, persisted=True)
                )
                candidate_budget_digest = candidate["content_digest"]
                if any(
                    binding["budget_plan_id"] == candidate["budget_plan_id"]
                    and binding["active_version"] == candidate["version"]
                    for binding in budget_bindings
                ):
                    return None
            try:
                evaluation = build_recommendation_evaluation(
                    request=request,
                    scorecard_evaluation=scorecard_evaluation,
                    scorecard_evaluation_digest=scorecard_row["evaluation_digest"],
                    created_at=now,
                    comparison=comparison,
                    preview=preview,
                    scenarios=scenarios,
                    budget_bindings=budget_bindings,
                    active_policy_version=active_policy["version"],
                    candidate_budget_digest=candidate_budget_digest,
                )
                if evaluation is None:
                    return None
                recommendation = evaluation["recommendation"]
                validate(recommendation, "hormuz.policy-recommendation")
                evaluation_json = canonical(evaluation)
                if len(evaluation_json.encode("ascii")) > MAX_EVALUATION_BYTES:
                    raise RecommendationKernelError()
            except (RecommendationKernelError, PortfolioError, TypeError, ValueError):
                raise PortfolioError("invalid_request") from None

            recommendation_id = recommendation["recommendation_id"]
            version = recommendation["version"]
            generation_digest = _generation_digest(evaluation)
            exact = sql.one(
                f"SELECT * FROM {SNAPSHOT_TABLE} WHERE organization_id=? "
                "AND recommendation_id=? AND version=?",
                (organization, recommendation_id, version),
            )
            if exact is not None:
                if not hmac.compare_digest(
                    str(exact["generation_digest"]), generation_digest
                ):
                    raise PortfolioError("version_conflict")
                stored = self._stored_snapshot(exact)
                self._latest_event(sql, organization, recommendation_id, version)
                return stored["evaluation"]

            if candidate_budget_expired:
                return None

            latest = sql.one(
                f"SELECT * FROM {SNAPSHOT_TABLE} WHERE organization_id=? "
                "AND recommendation_id=? ORDER BY version DESC LIMIT 1",
                (organization, recommendation_id),
            )
            supersedes = recommendation["supersedes_version"]
            if latest is None:
                if version != 1 or supersedes is not None:
                    raise PortfolioError("version_conflict")
            else:
                if version != int(latest["version"]) + 1 or supersedes != latest["version"]:
                    raise PortfolioError("version_conflict")
                prior_event = self._latest_event(
                    sql, organization, recommendation_id, int(latest["version"])
                )
                if prior_event["public_state"] == "accepted":
                    raise PortfolioError("version_conflict")

            sequence = self._event_sequence(sql, organization)
            proposal = recommendation["proposal"]
            candidate_budget = proposal["candidate_budget_plan"]
            snapshot = {
                "organization_id": organization,
                "recommendation_id": recommendation_id,
                "version": version,
                "work_scope_id": recommendation["work_scope"]["work_scope_id"],
                "work_scope_version": recommendation["work_scope"]["version"],
                "scorecard_id": recommendation["scorecard"]["id"],
                "scorecard_version": recommendation["scorecard"]["version"],
                "scorecard_evaluation_digest": scorecard_row["evaluation_digest"],
                "policy_id": recommendation["policy"]["id"],
                "policy_version": recommendation["policy"]["version"],
                "policy_digest": recommendation["policy_digest"],
                "change_type": proposal["change_type"],
                "candidate_policy_digest": proposal["candidate_policy_digest"],
                "candidate_budget_plan_id": None if candidate_budget is None else candidate_budget["id"],
                "candidate_budget_plan_version": None if candidate_budget is None else candidate_budget["version"],
                "evidence_level": recommendation["evidence_level"],
                "window_start_at": _timestamp(_instant(recommendation["window"]["start_at"])),
                "window_end_at": _timestamp(_instant(recommendation["window"]["end_at"])),
                "created_at": _timestamp(_instant(recommendation["created_at"])),
                "expires_at": _timestamp(_instant(recommendation["expires_at"])),
                "supersedes_version": supersedes,
                "generation_digest": generation_digest,
                "recommendation_json": canonical(recommendation),
                "evaluation_json": evaluation_json,
                "expected_pre_apply_json": canonical(evaluation["pre_apply_evidence"]),
                "bindings_json": canonical(evaluation["bindings"]),
                "sequence": sequence,
            }
            sql.insert(SNAPSHOT_TABLE, snapshot)
            generated = self._append_event(
                sql,
                principal,
                snapshot,
                event_type="generated",
                public_state="pending",
                reason_code="eligible",
                now=now,
                sequence=sequence,
            )
            if latest is not None:
                prior_event = self._latest_event(
                    sql, organization, recommendation_id, int(latest["version"])
                )
                if prior_event["event_type"] == "generated":
                    self._append_event(
                        sql,
                        principal,
                        latest,
                        event_type="superseded",
                        public_state="superseded",
                        reason_code="superseded",
                        now=now,
                    )
            self._stored_snapshot(snapshot)
            if generated["event_type"] != "generated":
                raise PortfolioError("unavailable")
            return evaluation

    @staticmethod
    def _snapshot(
        sql,
        organization: str,
        recommendation_id: str,
        version: int | None = None,
    ) -> dict[str, Any]:
        statement = (
            f"SELECT * FROM {SNAPSHOT_TABLE} WHERE organization_id=? "
            "AND recommendation_id=?"
        )
        values: tuple[object, ...] = (organization, recommendation_id)
        if version is None:
            statement += " ORDER BY version DESC LIMIT 1"
        else:
            statement += " AND version=?"
            values = (*values, version)
        row = sql.one(statement, values)
        if row is None:
            raise PortfolioError("not_found")
        return row

    def _bound_context(
        self,
        sql,
        row: Mapping[str, object],
        stored: Mapping[str, object],
        now: str,
    ) -> tuple[str | None, list[dict[str, object]] | None]:
        latest_scorecard = sql.one(
            "SELECT * FROM portfolio_model_scorecard_snapshots "
            "WHERE organization_id=? AND scorecard_id=? "
            "ORDER BY version DESC LIMIT 1",
            (row["organization_id"], row["scorecard_id"]),
        )
        if (
            latest_scorecard is None
            or latest_scorecard["version"] != row["scorecard_version"]
            or latest_scorecard["evaluation_digest"] != row["scorecard_evaluation_digest"]
            or latest_scorecard["state"] != "eligible"
            or latest_scorecard["expires_at"] <= now
        ):
            return "scorecard_drift", None
        try:
            scorecard_eval = ScorecardRepository._stored(latest_scorecard)
            chain = self._scope_chain(
                sql, str(row["organization_id"]), scorecard_eval["scorecard"]["work_scope"]
            )
        except PortfolioError:
            return "scorecard_drift", None
        current_budgets = self._active_budget_bindings(
            sql, str(row["organization_id"]), chain, now
        )
        proposal = stored["recommendation"]["proposal"]
        if proposal["change_type"] == "budget_plan_change":
            try:
                candidate = self._candidate_budget(
                    sql,
                    str(row["organization_id"]),
                    proposal["candidate_budget_plan"],
                    stored["recommendation"]["work_scope"],
                )
            except PortfolioError:
                return "policy_drift", None
            if (
                candidate["content_digest"]
                != stored["bindings"]["candidate_budget_digest"]
            ):
                return "policy_drift", None
        return None, current_budgets

    def _drift_reason(
        self,
        sql,
        row: Mapping[str, object],
        stored: Mapping[str, object],
        active_policy: Mapping[str, str],
        now: str,
    ) -> str | None:
        bindings = stored["bindings"]
        if (
            active_policy["digest"] != bindings["policy_digest"]
            or active_policy["version"] != bindings["active_policy_version"]
        ):
            return "policy_drift"
        reason, current_budgets = self._bound_context(sql, row, stored, now)
        if reason is not None:
            return reason
        if current_budgets != bindings["budget_bindings"]:
            return "policy_drift"
        return None

    def _expire_if_due(
        self,
        sql,
        principal: PortfolioPrincipal,
        row: Mapping[str, object],
        event: Mapping[str, object],
        now: str,
    ) -> dict[str, Any]:
        if (
            event["event_type"] in {"generated", "accepted"}
            and row["expires_at"] <= now
        ):
            return self._append_event(
                sql,
                principal,
                row,
                event_type="expired",
                public_state="expired",
                reason_code="expired",
                now=now,
            )
        return dict(event)

    def _audit_read(
        self,
        sql,
        principal: PortfolioPrincipal,
        *,
        operation: str,
        recommendation_id: str | None,
        version: int | None,
        filters: Mapping[str, object],
        snapshot_sequence: int,
        count: int,
        now: str,
    ) -> None:
        sql.insert(AUDIT_TABLE, {
            "organization_id": principal.organization_id,
            "event_id": str(uuid4()),
            "sequence": self._audit_sequence(sql, principal.organization_id),
            "actor_id": principal.actor_id,
            "operation": operation,
            "recommendation_id": recommendation_id,
            "recommendation_version": version,
            "filter_digest": _sha256(filters),
            "snapshot_sequence": snapshot_sequence,
            "result_count": count,
            "occurred_at": now,
        })

    def show(
        self,
        principal: PortfolioPrincipal,
        recommendation_id: str,
        version: int | None,
    ) -> dict[str, object]:
        self._authorize(principal)
        recommendation_id = _identifier(recommendation_id)
        if version is not None:
            version = _version(version)
        with self._transaction(principal) as sql:
            now = sql.now()
            row = self._snapshot(
                sql, principal.organization_id, recommendation_id, version
            )
            stored = self._stored_snapshot(row)
            event = self._latest_event(
                sql, principal.organization_id, recommendation_id, int(row["version"])
            )
            event = self._expire_if_due(sql, principal, row, event, now)
            ceiling = int(event["sequence"])
            result = self._public(row, stored, event)
            self._audit_read(
                sql,
                principal,
                operation="show",
                recommendation_id=recommendation_id,
                version=int(row["version"]),
                filters={"version": version},
                snapshot_sequence=ceiling,
                count=1,
                now=now,
            )
        return result

    def _cursor(
        self,
        sql,
        principal: PortfolioPrincipal,
        token: str,
        requested_limit: int | None,
        now: str,
    ) -> dict[str, Any]:
        row = sql.one(
            f"SELECT * FROM {CURSOR_TABLE} WHERE organization_id=? AND cursor_id=?",
            (principal.organization_id, token),
        )
        authority = _sha256(principal.cursor_authority)
        if (
            row is None
            or row["actor_id"] != principal.actor_id
            or not hmac.compare_digest(str(row["authority_digest"]), authority)
            or row["schema_id"] != "hormuz.policy-recommendation-page"
            or row["schema_version"] != 1
            or row["expires_at"] <= now
            or (requested_limit is not None and requested_limit != row["page_limit"])
        ):
            raise PortfolioError("cursor_invalid")
        try:
            filters = json.loads(str(row["filters_json"]))
            if canonical(filters) != row["filters_json"]:
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError, PortfolioError):
            raise PortfolioError("cursor_invalid") from None
        row["filters"] = filters
        return row

    def _list_rows(
        self,
        sql,
        organization: str,
        *,
        snapshot: int,
        filters: Mapping[str, object],
        after_at: str,
        after_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        statement = (
            f"SELECT s.* FROM {SNAPSHOT_TABLE} s WHERE s.organization_id=? "
            "AND s.sequence<=? AND NOT EXISTS ("
            f"SELECT 1 FROM {SNAPSHOT_TABLE} newer "
            "WHERE newer.organization_id=s.organization_id "
            "AND newer.recommendation_id=s.recommendation_id "
            "AND newer.version>s.version AND newer.sequence<=?) "
            "AND (s.created_at>? OR (s.created_at=? AND s.recommendation_id>?))"
        )
        values: list[object] = [
            organization,
            snapshot,
            snapshot,
            after_at,
            after_at,
            after_id,
        ]
        if filters.get("start_at") is not None:
            statement += " AND s.created_at>=? AND s.created_at<?"
            values.extend([filters["start_at"], filters["end_at"]])
        if filters.get("work_scope_id") is not None:
            statement += " AND s.work_scope_id=?"
            values.append(filters["work_scope_id"])
        if filters.get("evidence_level") is not None:
            statement += " AND s.evidence_level=?"
            values.append(filters["evidence_level"])
        statement += " ORDER BY s.created_at,s.recommendation_id LIMIT ?"
        values.append(limit + 1)
        return [dict(row) for row in sql.execute(statement, tuple(values)).fetchall()]

    def list(
        self,
        principal: PortfolioPrincipal,
        query: Mapping[str, object],
    ) -> dict[str, object]:
        self._authorize(principal)
        limit = int(query.get("limit", 50))
        cursor_token = query.get("cursor")
        with self._transaction(principal) as sql:
            now = sql.now()
            if cursor_token is None:
                filters = {
                    key: query[key]
                    for key in ("start_at", "end_at", "work_scope_id", "evidence_level")
                    if key in query
                }
                if "start_at" in filters:
                    filters["start_at"] = _timestamp(_instant(filters["start_at"]))
                    filters["end_at"] = _timestamp(_instant(filters["end_at"]))
                ceiling_row = sql.one(
                    f"SELECT COALESCE(MAX(sequence),0) AS sequence FROM {EVENT_TABLE} "
                    "WHERE organization_id=?",
                    (principal.organization_id,),
                )
                snapshot = int(ceiling_row["sequence"])
                as_of = now
                after_at = "0001-01-01T00:00:00.000000Z"
                after_id = "0"
            else:
                cursor = self._cursor(
                    sql, principal, str(cursor_token), query.get("limit"), now
                )
                filters = cursor["filters"]
                limit = int(cursor["page_limit"])
                snapshot = int(cursor["snapshot_sequence"])
                as_of = str(cursor["as_of"])
                after_at = str(cursor["after_at"])
                after_id = str(cursor["after_id"])

            rows = self._list_rows(
                sql,
                principal.organization_id,
                snapshot=snapshot,
                filters=filters,
                after_at=after_at,
                after_id=after_id,
                limit=limit,
            )
            candidates: list[tuple[dict[str, Any], dict[str, object]]] = []
            for row in rows[:limit]:
                stored = self._stored_snapshot(row)
                frozen_event = self._latest_event(
                    sql,
                    principal.organization_id,
                    row["recommendation_id"],
                    int(row["version"]),
                    ceiling=snapshot,
                )
                event = frozen_event
                if (
                    frozen_event["event_type"] in {"generated", "accepted"}
                    and row["expires_at"] <= as_of
                ):
                    current_event = self._latest_event(
                        sql,
                        principal.organization_id,
                        row["recommendation_id"],
                        int(row["version"]),
                    )
                    if current_event["event_type"] == "expired":
                        event = current_event
                    elif current_event["event_type"] in {"generated", "accepted"}:
                        event = self._expire_if_due(
                            sql, principal, row, current_event, as_of
                        )
                    else:
                        raise PortfolioError("unavailable")
                candidates.append((row, self._public(row, stored, event)))

            natural_more = len(rows) > limit
            selected: list[tuple[dict[str, Any], dict[str, object]]] = []
            for candidate in candidates:
                proposed = [*selected, candidate]
                placeholder_more = natural_more or len(proposed) < len(candidates)
                envelope = {
                    "schema_id": "hormuz.policy-recommendation-page",
                    "schema_version": 1,
                    "organization_id": principal.organization_id,
                    "items": [item for _row, item in proposed],
                    "as_of": as_of,
                    "has_more": placeholder_more,
                    "next_cursor": "0" * 64 if placeholder_more else None,
                }
                if len(canonical(envelope).encode("utf-8")) > RESPONSE_BYTES:
                    break
                selected = proposed
            if candidates and not selected:
                raise PortfolioError("unavailable")
            has_more = natural_more or len(selected) < len(candidates)
            next_cursor = None
            if has_more:
                last_row = selected[-1][0]
                next_cursor = uuid4().hex + uuid4().hex
                sql.insert(CURSOR_TABLE, {
                    "organization_id": principal.organization_id,
                    "cursor_id": next_cursor,
                    "actor_id": principal.actor_id,
                    "authority_digest": _sha256(principal.cursor_authority),
                    "schema_id": "hormuz.policy-recommendation-page",
                    "schema_version": 1,
                    "as_of": as_of,
                    "expires_at": _timestamp(_instant(now) + _CURSOR_TTL),
                    "snapshot_sequence": snapshot,
                    "page_limit": limit,
                    "after_at": last_row["created_at"],
                    "after_id": last_row["recommendation_id"],
                    "filters_json": canonical(filters),
                })
            result = {
                "schema_id": "hormuz.policy-recommendation-page",
                "schema_version": 1,
                "organization_id": principal.organization_id,
                "items": [item for _row, item in selected],
                "as_of": as_of,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }
            try:
                validate(result, "hormuz.policy-recommendation-page")
            except PortfolioError:
                raise PortfolioError("unavailable") from None
            self._audit_read(
                sql,
                principal,
                operation="list",
                recommendation_id=None,
                version=None,
                filters=filters,
                snapshot_sequence=snapshot,
                count=len(selected),
                now=now,
            )
        return result

    def _conflicts(
        self,
        sql,
        principal: PortfolioPrincipal,
        row: Mapping[str, object],
        now: str,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        change_group = (
            "candidate.change_type='budget_plan_change'"
            if row["change_type"] == "budget_plan_change"
            else "candidate.change_type<>'budget_plan_change'"
        )
        rows = sql.execute(
            f"SELECT candidate.* FROM {SNAPSHOT_TABLE} candidate "
            "WHERE candidate.organization_id=? AND candidate.work_scope_id=? "
            f"AND candidate.work_scope_version=? AND {change_group} "
            "AND candidate.policy_digest=? AND candidate.recommendation_id<>? "
            "AND NOT EXISTS ("
            f"SELECT 1 FROM {SNAPSHOT_TABLE} newer "
            "WHERE newer.organization_id=candidate.organization_id "
            "AND newer.recommendation_id=candidate.recommendation_id "
            "AND newer.version>candidate.version) "
            "ORDER BY candidate.created_at,candidate.recommendation_id LIMIT ?",
            (
                row["organization_id"],
                row["work_scope_id"],
                row["work_scope_version"],
                row["policy_digest"],
                row["recommendation_id"],
                _MAX_CONFLICTS + 1,
            ),
        ).fetchall()
        if len(rows) > _MAX_CONFLICTS:
            raise PortfolioError("unavailable")
        result = []
        for raw in rows:
            other = dict(raw)
            event = self._latest_event(
                sql,
                str(row["organization_id"]),
                other["recommendation_id"],
                int(other["version"]),
            )
            event = self._expire_if_due(sql, principal, other, event, now)
            if event["public_state"] in {"pending", "accepted"}:
                result.append((other, event))
        return result

    def decide(
        self,
        principal: PortfolioPrincipal,
        recommendation_id: str,
        value: Mapping[str, object],
        idempotency_key: str,
    ) -> dict[str, object]:
        self._authorize(principal)
        recommendation_id = _identifier(recommendation_id)
        if type(idempotency_key) is not str or _KEY.fullmatch(idempotency_key) is None:
            raise PortfolioError("invalid_request")
        try:
            validate(value, "hormuz.policy-recommendation-decision-request")
        except PortfolioError:
            raise
        expected_version = _version(value["expected_version"])
        decision = value["decision"]
        if value["reason_code"] != decision:
            raise PortfolioError("invalid_request")
        if (decision == "accepted") != (value["pre_apply_evidence"] is not None):
            raise PortfolioError("invalid_request")
        request_digest = _sha256({
            "recommendation_id": recommendation_id,
            "body": value,
        })
        failure: str | None = None
        result: dict[str, object] | None = None
        with self._transaction(principal) as sql:
            active_policy = self._active_policy_in_transaction(sql, principal)
            now = sql.now()
            replay = sql.one(
                f"SELECT * FROM {EVENT_TABLE} WHERE organization_id=? "
                "AND actor_id=? AND idempotency_key=?",
                (principal.organization_id, principal.actor_id, idempotency_key),
            )
            if replay is not None:
                if (
                    replay["request_digest"] != request_digest
                    or replay["recommendation_id"] != recommendation_id
                    or replay["recommendation_version"] != expected_version
                    or replay["event_type"] != decision
                ):
                    raise PortfolioError("idempotency_conflict")
                row = self._snapshot(
                    sql, principal.organization_id, recommendation_id, expected_version
                )
                stored = self._stored_snapshot(row)
                return self._public(row, stored, self._stored_event(replay))

            row = self._snapshot(
                sql, principal.organization_id, recommendation_id, expected_version
            )
            if row["version"] != expected_version:
                raise PortfolioError("version_conflict")
            latest_row = self._snapshot(
                sql, principal.organization_id, recommendation_id
            )
            if latest_row["version"] != expected_version:
                raise PortfolioError("version_conflict")
            stored = self._stored_snapshot(row)
            event = self._latest_event(
                sql, principal.organization_id, recommendation_id, expected_version
            )
            if event["event_type"] != "generated":
                raise PortfolioError("version_conflict")
            if row["expires_at"] <= now:
                event = self._append_event(
                    sql,
                    principal,
                    row,
                    event_type="expired",
                    public_state="expired",
                    reason_code="expired",
                    now=now,
                )
                failure = "version_conflict"
            else:
                drift = self._drift_reason(
                    sql, row, stored, active_policy, now
                )
                if drift is not None:
                    event = self._append_event(
                        sql,
                        principal,
                        row,
                        event_type="invalidated",
                        public_state="invalidated",
                        reason_code=drift,
                        now=now,
                    )
                    failure = "version_conflict"
            if failure is None and decision == "accepted" and not hmac.compare_digest(
                canonical(value["pre_apply_evidence"]),
                canonical(stored["expected_pre_apply"]),
            ):
                raise PortfolioError("invalid_request")
            if failure is None:
                conflicts = (
                    self._conflicts(sql, principal, row, now)
                    if decision == "accepted"
                    else []
                )
                if any(other_event["public_state"] == "accepted" for _other, other_event in conflicts):
                    raise PortfolioError("version_conflict")
                event = self._append_event(
                    sql,
                    principal,
                    row,
                    event_type=decision,
                    public_state=decision,
                    reason_code=decision,
                    now=now,
                    evidence=(
                        stored["expected_pre_apply"] if decision == "accepted" else {}
                    ),
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                )
                for other, other_event in conflicts:
                    if other_event["public_state"] != "pending":
                        continue
                    if other["expires_at"] <= now:
                        self._append_event(
                            sql,
                            principal,
                            other,
                            event_type="expired",
                            public_state="expired",
                            reason_code="expired",
                            now=now,
                        )
                    else:
                        self._append_event(
                            sql,
                            principal,
                            other,
                            event_type="superseded",
                            public_state="superseded",
                            reason_code="superseded",
                            now=now,
                        )
            result = self._public(row, stored, event)
        if failure is not None:
            raise PortfolioError(failure)
        if result is None:
            raise PortfolioError("unavailable")
        return result

    @staticmethod
    def _parse_applied(value: Mapping[str, object]) -> dict[str, object]:
        if type(value) is not dict or set(value) != _APPLIED_FIELDS:
            raise PortfolioError("invalid_request")
        event_id = _identifier(value["activation_event_id"])
        activation_digest = _digest(value["activation_digest"])
        policy_digest = value["activated_policy_digest"]
        budget = value["activated_budget_plan"]
        if policy_digest is None:
            if type(budget) is not dict or set(budget) != {"id", "version"}:
                raise PortfolioError("invalid_request")
            budget = {"id": _identifier(budget["id"]), "version": _version(budget["version"])}
        else:
            policy_digest = _digest(policy_digest)
            if budget is not None:
                raise PortfolioError("invalid_request")
        return {
            "activation_event_id": event_id,
            "activated_policy_digest": policy_digest,
            "activated_budget_plan": budget,
            "activation_digest": activation_digest,
        }

    def _authoritative_application_evidence(
        self,
        sql,
        principal: PortfolioPrincipal,
        proposal: Mapping[str, object],
        now: str,
    ) -> dict[str, object]:
        if proposal["change_type"] == "budget_plan_change":
            reference = proposal["candidate_budget_plan"]
            try:
                active = WorkBudgetRepository._active_budget(
                    sql,
                    principal.organization_id,
                    reference["id"],
                    now,
                )
            except (BudgetIntegrityError, BudgetRepositoryError):
                raise PortfolioError("version_conflict") from None
            if active is None:
                raise PortfolioError("version_conflict")
            plan, activation, pointer, _history = active
            if (
                plan["version"] != reference["version"]
                or pointer["active_version"] != reference["version"]
                or pointer["current_activation_event_id"]
                != activation["activation_event_id"]
            ):
                raise PortfolioError("version_conflict")
            receipt = {
                "kind": "work_budget_activation",
                "organization_id": principal.organization_id,
                "activation_event_id": activation["activation_event_id"],
                "budget_plan": {
                    "id": plan["budget_plan_id"],
                    "version": plan["version"],
                },
                "activation_generation": activation["activation_generation"],
                "previous_version": activation["previous_version"],
                "actor_id": activation["actor_id"],
                "reason_code": activation["reason_code"],
                "policy": {
                    "version": activation["policy_version"],
                    "digest": activation["policy_digest"],
                },
                "committed_at": activation["committed_at"],
            }
            return {
                "activation_event_id": activation["activation_event_id"],
                "activated_policy_digest": None,
                "activated_budget_plan": dict(reference),
                "activation_digest": _sha256(receipt),
            }

        if self.config.policy_control.mode != "postgresql":
            # Local policy mode has no immutable activation ledger from which an
            # application receipt can be derived.
            raise PortfolioError("version_conflict")
        rows = sql.execute(
            "SELECT * FROM "
            "portfolio_policy_recommendation_active_policy_receipt()"
        ).fetchall()
        if len(rows) != 1:
            raise PortfolioError("version_conflict")
        activation = dict(rows[0])
        try:
            event_id = _identifier(activation["event_id"])
            version_id = _identifier(activation["version_id"])
            content_digest = _digest(activation["content_sha256"])
            generation = activation["generation"]
            if type(generation) is not int or not 1 <= generation <= 9_007_199_254_740_991:
                raise PortfolioError("unavailable")
            activated_at = _persisted_timestamp(activation["activated_at"])
            occurred_at = _persisted_timestamp(activation["occurred_at"])
            actor_kind = _identifier(activation["actor_kind"])
            actor_key = _identifier(activation["actor_identity_key"])
            if (
                activation["organization_id"] != principal.organization_id
                or activation["event_type"]
                not in {"policy_activated", "policy_rolled_back"}
                or activation["activated_by_kind"] != actor_kind
                or activation["activated_by_identity_key"] != actor_key
                or activated_at != occurred_at
                or activated_at > _persisted_timestamp(now)
                or content_digest != proposal["candidate_policy_digest"]
            ):
                raise PortfolioError("version_conflict")
        except (KeyError, TypeError, PortfolioError):
            raise PortfolioError("version_conflict") from None
        receipt = {
            "kind": "policy_activation",
            "organization_id": principal.organization_id,
            "activation_event_id": event_id,
            "event_type": activation["event_type"],
            "activated_policy": {
                "version": version_id,
                "digest": content_digest,
            },
            "activation_generation": generation,
            "activated_at": activated_at,
            "actor": {"kind": actor_kind, "identity_key": actor_key},
        }
        return {
            "activation_event_id": event_id,
            "activated_policy_digest": content_digest,
            "activated_budget_plan": None,
            "activation_digest": _sha256(receipt),
        }

    def application_evidence(
        self,
        principal: PortfolioPrincipal,
        recommendation_id: str,
        version: int,
    ) -> dict[str, object]:
        """Derive the exact receipt for a separately completed activation."""

        self._authorize(principal)
        recommendation_id, version = _identifier(recommendation_id), _version(version)
        with self._transaction(principal) as sql:
            now = sql.now()
            row = self._snapshot(
                sql, principal.organization_id, recommendation_id, version
            )
            stored = self._stored_snapshot(row)
            event = self._latest_event(
                sql, principal.organization_id, recommendation_id, version
            )
            if event["event_type"] == "applied":
                return dict(event["evidence"])
            if event["event_type"] != "accepted" or row["expires_at"] <= now:
                raise PortfolioError("version_conflict")
            return self._authoritative_application_evidence(
                sql, principal, stored["recommendation"]["proposal"], now
            )

    def record_applied(
        self,
        principal: PortfolioPrincipal,
        recommendation_id: str,
        version: int,
        value: Mapping[str, object],
    ) -> dict[str, object]:
        """Append an outcome after a separate activation path has succeeded."""

        self._authorize(principal)
        recommendation_id, version = _identifier(recommendation_id), _version(version)
        evidence = self._parse_applied(value)
        failure = False
        result: dict[str, object] | None = None
        with self._transaction(principal) as sql:
            now = sql.now()
            row = self._snapshot(
                sql, principal.organization_id, recommendation_id, version
            )
            stored = self._stored_snapshot(row)
            event = self._latest_event(
                sql, principal.organization_id, recommendation_id, version
            )
            if event["event_type"] == "applied":
                if not hmac.compare_digest(canonical(event["evidence"]), canonical(evidence)):
                    raise PortfolioError("version_conflict")
                return self._public(row, stored, event)
            event = self._expire_if_due(sql, principal, row, event, now)
            if event["event_type"] != "accepted":
                failure = True
            else:
                proposal = stored["recommendation"]["proposal"]
                current_budgets: list[dict[str, object]] | None = None
                drift, current_budgets = self._bound_context(
                    sql, row, stored, now
                )
                bindings = stored["bindings"]
                if drift is None and proposal["change_type"] == "budget_plan_change":
                    active_policy = self._active_policy_in_transaction(sql, principal)
                    expected_policy = {
                        "version": bindings["active_policy_version"],
                        "digest": bindings["policy_digest"],
                    }
                    target_id = proposal["candidate_budget_plan"]["id"]
                    original_other_budgets = [
                        binding
                        for binding in bindings["budget_bindings"]
                        if binding["budget_plan_id"] != target_id
                    ]
                    current_other_budgets = [
                        binding
                        for binding in current_budgets
                        if binding["budget_plan_id"] != target_id
                    ]
                    if (
                        active_policy != expected_policy
                        or current_other_budgets != original_other_budgets
                    ):
                        drift = "policy_drift"
                elif (
                    drift is None
                    and current_budgets != bindings["budget_bindings"]
                ):
                    drift = "policy_drift"
                if drift is not None:
                    event = self._append_event(
                        sql,
                        principal,
                        row,
                        event_type="invalidated",
                        public_state="invalidated",
                        reason_code=drift,
                        now=now,
                    )
                    failure = True
                else:
                    authoritative_evidence = (
                        self._authoritative_application_evidence(
                            sql, principal, proposal, now
                        )
                    )
                    if not hmac.compare_digest(
                        canonical(evidence), canonical(authoritative_evidence)
                    ):
                        raise PortfolioError("version_conflict")
                    event = self._append_event(
                        sql,
                        principal,
                        row,
                        event_type="applied",
                        public_state="accepted",
                        reason_code="not_applicable",
                        now=now,
                        evidence=authoritative_evidence,
                    )
                    result = self._public(row, stored, event)
        if failure or result is None:
            raise PortfolioError("version_conflict")
        return result

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
        if operation not in RECOMMENDATION_OPERATIONS:
            raise PortfolioError("not_found")
        method = "POST" if operation == "decide_recommendation" else "GET"
        if route(method, path) != (operation, scope_id):
            raise PortfolioError("invalid_request")
        parsed = query_parameters(urlencode(query), operation)
        if parsed != query:
            raise PortfolioError("invalid_request")
        if operation == "list_recommendations":
            if body is not None or idempotency_key is not None:
                raise PortfolioError("invalid_request")
            result = self.list(principal, query)
        elif operation == "show_recommendation":
            if body is not None or idempotency_key is not None or scope_id is None:
                raise PortfolioError("invalid_request")
            result = self.show(principal, scope_id, query.get("version"))
        else:
            if body is None or scope_id is None or query:
                raise PortfolioError("invalid_request")
            result = self.decide(
                principal,
                scope_id,
                body,
                idempotency_key,
            )
        return 200, result
