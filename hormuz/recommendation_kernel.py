"""Deterministic, content-free policy recommendation evaluation.

The repository resolves and authorizes immutable scorecard, policy, budget,
and scenario inputs before calling this module.  The kernel returns either a
reviewable evaluation or a fixed no-recommendation reason.  It never accepts
or emits work content and it never applies a policy or budget change.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Mapping

from .policy_analysis import PolicyComparison, PolicyEvaluation, PolicyPreview
from .portfolio_wire import canonical


MAX_EVALUATION_BYTES = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_CHANGE_TYPES = frozenset({
    "budget_plan_change",
    "model_allowlist_change",
    "model_fallback_change",
    "output_or_cost_cap_change",
    "routing_policy_change",
})
_ELIGIBILITY_COVERAGE_FIELDS = (
    "eligible_governed_attempts",
    "eligible_governed_spend",
    "eligible_external_outcome_events",
    "eligible_association_candidates",
    "pricing",
    "connector",
    "association",
)
_PUBLIC_COVERAGE_FIELDS = (*_ELIGIBILITY_COVERAGE_FIELDS, "excluded")


class RecommendationKernelError(ValueError):
    """Stable rejection for malformed or inconsistent recommendation input."""

    def __init__(self, code: str = "recommendation_evidence_invalid") -> None:
        self.code = code
        super().__init__(code)


def _invalid(code: str = "recommendation_evidence_invalid") -> None:
    raise RecommendationKernelError(code)


def _closed(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        _invalid()
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        _invalid()
    return value


def _version(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2_147_483_647:
        _invalid()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _invalid()
    return value


def _timestamp(value: object) -> tuple[str, datetime]:
    if type(value) is not str:
        _invalid()
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid()
    if instant.tzinfo != timezone.utc:
        _invalid()
    return (
        instant.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
        instant,
    )


def _ref(value: object) -> dict[str, object]:
    item = _closed(value, {"id", "version"})
    return {"id": _identifier(item["id"]), "version": _version(item["version"])}


def _proposal(value: object) -> dict[str, object]:
    proposal = _closed(
        value,
        {"change_type", "candidate_policy_digest", "candidate_budget_plan"},
    )
    change_type = proposal["change_type"]
    if type(change_type) is not str or change_type not in _ALLOWED_CHANGE_TYPES:
        _invalid()
    policy_digest = proposal["candidate_policy_digest"]
    budget_plan = proposal["candidate_budget_plan"]
    if change_type == "budget_plan_change":
        if policy_digest is not None or budget_plan is None:
            _invalid()
        budget_plan = _ref(budget_plan)
    else:
        if budget_plan is not None or policy_digest is None:
            _invalid()
        policy_digest = _digest(policy_digest)
    return {
        "change_type": change_type,
        "candidate_policy_digest": policy_digest,
        "candidate_budget_plan": budget_plan,
    }


def parse_generation_request(value: Mapping[str, object]) -> dict[str, object]:
    """Validate the closed internal generation request.

    Policy documents, usage facts, and saved scenarios are separate typed
    arguments so their content is never persisted in the recommendation kit.
    """

    request = _closed(
        value,
        {
            "recommendation_id",
            "version",
            "supersedes_version",
            "scorecard",
            "selected_cohort_id",
            "proposal",
            "expires_at",
        },
    )
    version = _version(request["version"])
    supersedes = request["supersedes_version"]
    if supersedes is not None:
        supersedes = _version(supersedes)
    if (version == 1 and supersedes is not None) or (
        version > 1 and supersedes != version - 1
    ):
        _invalid()
    expires_at, _ = _timestamp(request["expires_at"])
    return {
        "recommendation_id": _identifier(request["recommendation_id"]),
        "version": version,
        "supersedes_version": supersedes,
        "scorecard": _ref(request["scorecard"]),
        "selected_cohort_id": _identifier(request["selected_cohort_id"]),
        "proposal": _proposal(request["proposal"]),
        "expires_at": expires_at,
    }


def _evidence_ref(kind: str, payload: Mapping[str, object]) -> dict[str, str]:
    digest = hashlib.sha256(canonical(payload).encode("ascii")).hexdigest()
    return {"evidence_id": f"{kind}-{digest}", "digest": digest}


def _decision_behavior(decision) -> dict[str, object]:
    return {
        "allowed": bool(decision.allowed),
        "action": str(decision.action),
        "resolved_alias": decision.resolved_alias,
        "upstream_model": (
            None if decision.route is None else decision.route.upstream_model
        ),
        "max_output_tokens": decision.max_output_tokens,
    }


def _policy_change_allowed(change_type: str, comparison: PolicyComparison) -> bool:
    paths = tuple(change.path for change in comparison.changes)
    if change_type == "budget_plan_change":
        return not paths
    if not paths:
        return False
    if change_type == "model_allowlist_change":
        return all("allowed_models" in path for path in paths)
    if change_type == "model_fallback_change":
        return all("fallback_model" in path for path in paths)
    if change_type == "output_or_cost_cap_change":
        markers = (
            "max_output_tokens",
            "monthly_token_limit",
            "monthly_budget_usd",
            "per_actor_monthly_budget_usd",
            "team_model_output_limits",
        )
        return all(any(marker in path for marker in markers) for path in paths)
    # Routing-policy recommendations are the remaining typed policy changes;
    # they still cannot smuggle egress or secret-control changes.
    return all(
        path.startswith("policies.")
        and "allowed_models" not in path
        and "fallback_model" not in path
        and not any(
            marker in path
            for marker in (
                "max_output_tokens",
                "monthly_token_limit",
                "monthly_budget_usd",
                "per_actor_monthly_budget_usd",
                "team_model_output_limits",
            )
        )
        for path in paths
    )


def _coverage(scorecard: Mapping[str, object]) -> dict[str, object] | None:
    value = scorecard.get("coverage")
    if type(value) is not dict:
        _invalid()
    selected: dict[str, object] = {}
    for name in _PUBLIC_COVERAGE_FIELDS:
        item = value.get(name)
        if type(item) is not dict:
            _invalid()
        selected[name] = item
        if (
            name in _ELIGIBILITY_COVERAGE_FIELDS
            and (item.get("ratio") is None or item.get("reason_code") != "eligible")
        ):
            return None
    return selected


def _cohort_map(scorecard: Mapping[str, object]) -> dict[str, dict[str, object]]:
    cohorts = scorecard.get("cohorts")
    if type(cohorts) is not list or not cohorts:
        _invalid()
    result: dict[str, dict[str, object]] = {}
    for raw in cohorts:
        if type(raw) is not dict:
            _invalid()
        cohort_id = _identifier(raw.get("cohort_id"))
        if cohort_id in result:
            _invalid()
        result[cohort_id] = raw
    return result


def _qualified_cohort(cohort: Mapping[str, object]) -> bool:
    eligibility = cohort.get("eligibility")
    guardrails = cohort.get("guardrails")
    model = cohort.get("actual_model")
    if type(eligibility) is not dict or type(guardrails) is not dict or type(model) is not dict:
        _invalid()
    if (
        eligibility.get("status") != "eligible"
        or type(eligibility.get("sample_count")) is not int
        or type(eligibility.get("minimum_sample")) is not int
        or eligibility["sample_count"] < eligibility["minimum_sample"]
        or model.get("model_version") is None
    ):
        return False
    return all(
        type(item) is dict
        and item.get("state") == "pass"
        and item.get("reason_code") == "eligible"
        for item in guardrails.values()
    )


def _cost_direction(
    baseline: Mapping[str, object], candidate: Mapping[str, object]
) -> str:
    try:
        before = Decimal(str(baseline["metrics"]["quality_qualified_cost_per_accepted_work_item"]["value"]))
        after = Decimal(str(candidate["metrics"]["quality_qualified_cost_per_accepted_work_item"]["value"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return "unknown"
    return "lower" if after < before else "higher" if after > before else "unchanged"


def _models_and_rate_cards(
    cohorts: Mapping[str, Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    models: dict[str, dict[str, object]] = {}
    rate_cards: dict[str, dict[str, object]] = {}
    for cohort in cohorts.values():
        model = cohort.get("actual_model")
        if type(model) is not dict:
            _invalid()
        key = canonical(model)
        models[key] = dict(model)
        rate_card = cohort.get("rate_card")
        if rate_card is not None:
            item = _ref(rate_card)
            rate_cards[canonical(item)] = item
    return (
        [models[key] for key in sorted(models)],
        [rate_cards[key] for key in sorted(rate_cards)],
    )


def _metric_rules(
    cohorts: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    rules: dict[str, dict[str, object]] = {}
    for cohort in cohorts.values():
        metrics = cohort.get("metrics")
        if type(metrics) is not dict or not metrics:
            _invalid()
        for metric in metrics.values():
            if type(metric) is not dict:
                _invalid()
            rule = metric.get("rule")
            if type(rule) is not dict:
                _invalid()
            item = _closed(rule, {"rule_id", "version", "digest"})
            normalized = {
                "rule_id": _identifier(item["rule_id"]),
                "version": _version(item["version"]),
                "digest": _digest(item["digest"]),
            }
            rules[canonical(normalized)] = normalized
    return [rules[key] for key in sorted(rules)]


def build_recommendation_evaluation(
    *,
    request: Mapping[str, object],
    scorecard_evaluation: Mapping[str, object],
    scorecard_evaluation_digest: str,
    created_at: str,
    comparison: PolicyComparison,
    preview: PolicyPreview,
    scenarios: PolicyEvaluation,
    budget_bindings: list[dict[str, object]],
    active_policy_version: str | None = None,
    candidate_budget_digest: str | None = None,
) -> dict[str, object] | None:
    """Return a frozen recommendation evaluation, or ``None`` when suppressed."""

    selected = parse_generation_request(request)
    created_at, created = _timestamp(created_at)
    _, expires = _timestamp(selected["expires_at"])
    if created >= expires:
        _invalid()
    _digest(scorecard_evaluation_digest)
    if type(scorecard_evaluation) is not dict:
        _invalid()
    scorecard = scorecard_evaluation.get("scorecard")
    if type(scorecard) is not dict:
        _invalid()
    if (
        scorecard.get("schema_id") != "hormuz.model-scorecard"
        or scorecard.get("organization_id") != comparison.organization_id
        or selected["scorecard"]
        != {"id": scorecard.get("scorecard_id"), "version": scorecard.get("version")}
        or scorecard.get("state") != "eligible"
    ):
        return None
    _, scorecard_expiry = _timestamp(scorecard.get("expires_at"))
    if created >= scorecard_expiry or _coverage(scorecard) is None:
        return None

    cohorts = _cohort_map(scorecard)
    baseline_id = scorecard.get("baseline_cohort_id")
    target_id = selected["selected_cohort_id"]
    if (
        type(baseline_id) is not str
        or baseline_id not in cohorts
        or target_id not in cohorts
        or target_id == baseline_id
        or target_id not in scorecard.get("pareto_cohort_ids", [])
        or not _qualified_cohort(cohorts[baseline_id])
        or not _qualified_cohort(cohorts[target_id])
    ):
        return None
    proposal = selected["proposal"]
    if not _policy_change_allowed(proposal["change_type"], comparison):
        _invalid("recommendation_change_type_mismatch")
    if (
        comparison.organization_id != preview.organization_id
        or comparison.organization_id != scenarios.organization_id
        or comparison.baseline != preview.baseline
        or comparison.baseline != scenarios.baseline
        or comparison.candidate != preview.candidate
        or comparison.candidate != scenarios.candidate
    ):
        _invalid()
    if proposal["change_type"] == "budget_plan_change":
        if comparison.baseline != comparison.candidate:
            _invalid("recommendation_change_type_mismatch")
        if candidate_budget_digest is None:
            _invalid()
        candidate_budget_digest = _digest(candidate_budget_digest)
    elif proposal["candidate_policy_digest"] != comparison.candidate.content_sha256:
        _invalid()
    elif candidate_budget_digest is not None:
        _invalid()

    policy = cohorts[baseline_id].get("policy")
    if type(policy) is not dict:
        _invalid()
    policy = _ref(policy)
    if any(cohort.get("policy") != policy for cohort in cohorts.values()):
        return None
    models, rate_cards = _models_and_rate_cards(cohorts)
    metric_rules = _metric_rules(cohorts)
    if any(model.get("model_version") is None for model in models):
        return None

    comparison_payload = {
        "kind": "semantic_compare",
        "baseline": comparison.baseline.__dict__,
        "candidate": comparison.candidate.__dict__,
        "changes": [
            {"path": change.path, "change_type": change.change_type}
            for change in comparison.changes
        ],
        "budget_plan": proposal["candidate_budget_plan"],
    }
    preview_baseline = _decision_behavior(preview.baseline_decision)
    preview_candidate = _decision_behavior(preview.candidate_decision)
    preview_context = {
        "actor": {
            "actor_id": preview.identity.actor_id,
            "team_id": preview.identity.team_id,
            "organization_id": preview.identity.organization_id,
            "identity_type": preview.identity.identity_type,
            "clearance": preview.identity.clearance,
            "authentication_source": preview.identity.authentication_source,
        },
        "client": preview.client,
        "protocol": preview.protocol,
        "requested_model": preview.requested_model,
        "requested_output_tokens": preview.requested_output_tokens,
        "usage_basis": preview.usage_basis,
        "usage_period": {
            "starts_at": preview.usage_period.starts_at.astimezone(
                timezone.utc
            ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
            "ends_before": preview.usage_period.ends_before.astimezone(
                timezone.utc
            ).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        },
        "usage_snapshot_sha256": preview.usage_snapshot_sha256,
    }
    preview_payload = {
        "kind": "request_preview",
        "baseline": preview.baseline.__dict__,
        "candidate": preview.candidate.__dict__,
        **preview_context,
        "evaluated_at": preview.evaluated_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
        "behavior_changed": preview_baseline != preview_candidate,
        "baseline_behavior": preview_baseline,
        "candidate_behavior": preview_candidate,
    }
    scenario_payload = {
        "kind": "saved_scenario_evaluation",
        "suite_id": scenarios.suite.suite_id,
        "suite_digest": scenarios.suite.content_sha256,
        "scenario_count": len(scenarios.scenarios),
        "changed_count": scenarios.changed_count,
        "baseline_allowed_count": scenarios.baseline_allowed_count,
        "candidate_allowed_count": scenarios.candidate_allowed_count,
    }
    rollback_payload = {
        "kind": "rollback_plan",
        "baseline": comparison.baseline.__dict__,
        "candidate": comparison.candidate.__dict__,
        "budget_plan": proposal["candidate_budget_plan"],
        "activation": "separately_authorized",
        "rollback": "explicit_previous_immutable_version",
    }
    verification = {
        "semantic_compare": _evidence_ref("semantic-compare", comparison_payload),
        "request_preview": _evidence_ref("request-preview", preview_payload),
        "saved_scenario_evaluation": _evidence_ref("scenario-evaluation", scenario_payload),
        "rollback_plan": _evidence_ref("rollback-plan", rollback_payload),
    }

    losing = []
    for cohort_id in sorted(cohorts):
        if cohort_id in {baseline_id, target_id}:
            continue
        losing.append({
            "cohort_id": cohort_id,
            "reason_code": (
                "below_threshold" if not _qualified_cohort(cohorts[cohort_id])
                else "not_applicable"
            ),
        })
    target = cohorts[target_id]
    explanation = {
        "observed_tradeoff": {
            "baseline_cohort_id": baseline_id,
            "candidate_cohort_id": target_id,
            "cost_direction": _cost_direction(cohorts[baseline_id], target),
            "quality_guardrail": target["guardrails"]["quality"]["state"],
            "reliability_guardrail": target["guardrails"]["reliability"]["state"],
        },
        "expected_direction": (
            "lower_cost_with_guardrails"
            if _cost_direction(cohorts[baseline_id], target) == "lower"
            else "policy_behavior_change_with_guardrails"
        ),
        "affected_scopes": [dict(scorecard["work_scope"])],
        "losing_alternatives": losing,
        "confidence": {
            "state": "eligible",
            "evidence_level": scorecard["evidence_level"],
            "reason_code": "eligible",
        },
    }
    coverage = {
        name: scorecard["coverage"][name]
        for name in _PUBLIC_COVERAGE_FIELDS
    }
    recommendation = {
        "schema_id": "hormuz.policy-recommendation",
        "schema_version": 1,
        "organization_id": scorecard["organization_id"],
        "recommendation_id": selected["recommendation_id"],
        "version": selected["version"],
        "work_scope": dict(scorecard["work_scope"]),
        "scorecard": dict(selected["scorecard"]),
        "policy": policy,
        "policy_digest": comparison.baseline.content_sha256,
        "proposal": proposal,
        "evidence_level": scorecard["evidence_level"],
        "window": dict(scorecard["window"]),
        "coverage": coverage,
        "guardrails": dict(target["guardrails"]),
        "actual_models": models,
        "rate_cards": rate_cards,
        "state": "pending",
        "created_at": created_at,
        "expires_at": selected["expires_at"],
        "latest_decision_event_id": None,
        "supersedes_version": selected["supersedes_version"],
        "reason_code": "eligible",
        "automatic_application": False,
    }
    result = {
        "schema_id": "hormuz.recommendation-evaluation",
        "schema_version": 1,
        "recommendation": recommendation,
        "explanation": explanation,
        "pre_apply_evidence": verification,
        "verification_summary": {
            "semantic_change_count": len(comparison.changes),
            "preview_behavior_changed": preview_payload["behavior_changed"],
            "scenario_count": len(scenarios.scenarios),
            "scenario_changed_count": scenarios.changed_count,
            "automatic_application": False,
        },
        "bindings": {
            "scorecard_evaluation_digest": scorecard_evaluation_digest,
            "scorecard_input_digest": scorecard_evaluation["lineage"]["input_digest"],
            "policy_digest": comparison.baseline.content_sha256,
            "policy_version_id": comparison.baseline.version_id,
            "active_policy_version": _identifier(
                comparison.baseline.version_id
                if active_policy_version is None
                else active_policy_version
            ),
            "candidate_policy_digest": comparison.candidate.content_sha256,
            "candidate_budget_digest": candidate_budget_digest,
            "request_preview_context": preview_context,
            "budget_bindings": budget_bindings,
            "metric_rules": metric_rules,
            "actual_models": models,
            "rate_cards": rate_cards,
        },
    }
    if len(canonical(result).encode("ascii")) > MAX_EVALUATION_BYTES:
        _invalid()
    return result
