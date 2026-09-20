#!/usr/bin/env python3
"""Render four checked-in, synthetic portfolio records for design review.

This developer helper is not a public CLI, a live report, or a role-authorized
view. It accepts no paths or records from the command line.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
import textwrap


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools._portfolio_wire_contract import validate_wire_payload
from tools.verify_budget_transition_plan import validate_budget_report_v2


FILES = {
    "budget_v1": (
        "docs/work-budget-reports-wire-v1.json",
        "6a45a010de84273be45da85115d8d41267d5689addd29df592c47e8704a29cbf",
    ),
    "budget_v2": (
        "docs/work-budget-reports-wire-v2.json",
        "1e09eb42bedc8d91dc5ec230adb1f21360832940b237e38c8a88b86285a2c6d2",
    ),
    "budget_examples": (
        "tests/fixtures/portfolio_intelligence/budget-report-v2-examples.json",
        "2a51eeadbe3d7cce38ee94ed3b23933672bbd5c29d40315010e5fecbb110e3d6",
    ),
    "scorecard_wire": (
        "docs/portfolio-intelligence-wire-v1.json",
        "26ef36b12d475d5b8354e45abfc92c76187ca6ff5aafb4b0ebce6fab439feb80",
    ),
    "scorecard_examples": (
        "tests/fixtures/portfolio_intelligence/wire-v1-examples.json",
        "9a8d4ce3d98812ecd487f32b20f488bc006a978c92a921c1bde9496ef56da14b",
    ),
}
MAX_FILE_BYTES = 256 * 1024
WIDTH = 80


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("portfolio_display_duplicate_member")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("portfolio_display_nonfinite")


def _read_known(name: str) -> dict[str, object]:
    relative, digest = FILES[name]
    with (ROOT / relative).open("rb") as source:
        raw = source.read(MAX_FILE_BYTES + 1)
    if not 1 <= len(raw) <= MAX_FILE_BYTES or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("portfolio_display_fixture_changed")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("portfolio_display_root_invalid")
    return value


def _line(label: str, value: object) -> str:
    return "\n".join(textwrap.wrap(
        f"{label}: {value}", width=WIDTH, subsequent_indent="  ",
        break_long_words=True, break_on_hyphens=False,
    ))


def _known(value: object) -> str:
    return "unknown" if value is None else str(value)


def _money(value: str | None, currency: str | None) -> str:
    """Format a validated decimal string exactly, without float conversion."""
    if value is None:
        return "unknown"
    return f"{_known(currency)} {format(Decimal(value), 'f')}"


def _ref(value: dict[str, object] | None) -> str:
    return "unknown" if value is None else f"{value['id']} v{value['version']}"


def _coverage(value: dict[str, object]) -> str:
    return (
        f"{_known(value['numerator'])}/{_known(value['denominator'])}; "
        f"ratio {_known(value['ratio'])} ({value['reason_code']})"
    )


def _observation(value: dict[str, object]) -> str:
    return (
        f"{value['basis']}; {_money(value['amount'], value['currency'])}; "
        f"{value['reason_code']}"
    )


def _budget_case(case: dict[str, object]) -> str:
    report = case["value"]
    change = report["plan_change"]
    enforcement = report["enforcement"]
    forecast = report["forecast"]
    coverage = report["coverage"]
    currency = report["currency"]
    lines = [
        "=== Budget report (synthetic) ===",
        _line("Case", case["name"]),
        _line("Input", f"{case['schema_id']} v{report['schema_version']}"),
        _line("Window", f"{report['window']['start_at']} to {report['window']['end_at']}"),
        _line("As of", f"{report['as_of']}; generated {report['generated_at']}"),
        _line("Scope", f"{report['work_scope']['work_scope_id']} v{report['work_scope']['version']}"),
        _line("Plan", f"{_ref(report['plan'])}; activation {report['activation_generation']}; "
              f"{_money(report['plan_amount'], currency)}"),
        _line("Plan digest", report["plan"]["content_digest"]),
    ]
    if change["comparison_status"] == "first_activation":
        change_text = "established; prior amount unknown (first activation)"
    elif change["comparison_status"] == "known":
        change_text = (
            f"{change['kind']}; prior {_money(change['previous_amount'], change['previous_currency'])}; "
            f"delta {_money(change['amount_delta'], currency)} "
            f"({change['percent_delta']}%)"
        )
    else:
        change_text = f"{change['kind']}; delta unknown ({change['comparison_status']})"
    lines.extend((
        _line("Change", change_text),
        _line("Cost basis", f"{enforcement['cost_basis']} (gateway estimate)"),
        _line("Committed", _money(enforcement["committed_amount"], currency)),
        _line("Pending reservation", _money(enforcement["pending_reservation_amount"], currency)),
        _line("Uncertain reservation", _money(enforcement["uncertain_reservation_amount"], currency)),
        _line("Remaining", f"{_money(enforcement['remaining_amount'], currency)} "
              f"({enforcement['reason_code']})"),
    ))
    for observation in report["financial_observations"]:
        lines.append(_line("Observation", _observation(observation)))
        if observation["rate_card"] is not None:
            lines.append(_line("Rate card", _ref(observation["rate_card"])))
            lines.append(_line("Rate card digest", observation["rate_card"]["content_digest"]))
    if forecast["projected_amount"] is None:
        forecast_text = f"not available ({forecast['reason_code']})"
    else:
        forecast_text = (
            f"{_money(forecast['projected_amount'], forecast['currency'])}; "
            f"{forecast['cost_basis']}; {forecast['method']}; excludes reservations"
        )
    lines.extend((
        _line("Forecast", forecast_text),
        _line("Coverage", f"included {_known(coverage['included_attempts'])}/"
              f"{_known(coverage['population_attempts'])}; priced "
              f"{_known(coverage['priced_attempts'])}/"
              f"{_known(coverage['pricing_eligible_attempts'])} "
              f"({coverage['reason_code']})"),
        _line("Valuation rule", _ref(enforcement["valuation_rule"])),
        _line("Coverage rule", _ref(coverage["rule"])),
        _line("Policy", f"{report['policy']['version']}; digest "
              f"{report['policy']['content_digest']}"),
    ))
    return "\n".join(lines)


SCORECARD_COVERAGE = (
    "eligible_governed_attempts", "eligible_governed_spend",
    "eligible_external_outcome_events", "eligible_association_candidates",
    "pricing", "connector", "association",
)
SCORECARD_METRICS = (
    "use_case_attributed_spend_coverage",
    "quality_qualified_cost_per_accepted_work_item",
    "optimization_lift_vs_declared_baseline",
)


def _scorecard_case(case: dict[str, object], bundle: dict[str, object]) -> str:
    scorecard = case["value"]
    validate_wire_payload(bundle, "hormuz.model-scorecard", scorecard)
    cohort = scorecard["cohorts"][0]
    model = cohort["actual_model"]
    model_text = "unknown" if model is None else (
        f"provider {model['provider_id']}; model {model['model_id']}; "
        f"version {model['model_version']}"
    )
    lines = [
        "=== Model scorecard (planned, synthetic) ===",
        _line("Case", case["name"]),
        _line("Input", f"{case['schema_id']} v{scorecard['schema_version']}"),
        _line("Window", f"{scorecard['window']['start_at']} to {scorecard['window']['end_at']}"),
        _line("Freshness", f"generated {scorecard['generated_at']}; "
              f"expires {scorecard['expires_at']}; review {scorecard['review_after']}"),
        _line("Scope", f"{scorecard['work_scope']['work_scope_id']} "
              f"v{scorecard['work_scope']['version']}"),
        _line("Scorecard", f"{scorecard['scorecard_id']} v{scorecard['version']}"),
        _line("State", f"{scorecard['state']} ({scorecard['reason_code']}); "
              f"evidence {scorecard['evidence_level']}"),
    ]
    for name in SCORECARD_COVERAGE:
        lines.append(_line(f"Coverage {name}", _coverage(scorecard["coverage"][name])))
    lines.extend((
        _line("Cohort", cohort["cohort_id"]),
        _line("Model", model_text),
        _line("Cost basis", f"{cohort['cost_basis']} (declared for this cohort)"),
    ))
    if cohort["cost_components"]:
        for component in cohort["cost_components"]:
            lines.append(_line("Cost component", f"{_money(component['amount'], component['currency'])}; "
                               f"{component['basis']} (not summed)"))
    else:
        lines.append(_line("Cost component", "not available (no components); currency unknown"))
    eligibility = cohort["eligibility"]
    lines.append(_line("Eligibility", f"{eligibility['status']}; sample "
                       f"{eligibility['sample_count']}, minimum "
                       f"{_known(eligibility['minimum_sample'])}; observed coverage "
                       f"{_known(eligibility['observed_coverage'])}, minimum "
                       f"{_known(eligibility['minimum_coverage'])}"))
    for name in SCORECARD_METRICS:
        metric = cohort["metrics"][name]
        lines.append(_line(f"Metric {name}", f"{_known(metric['value'])} {metric['unit']}; "
                           f"{metric['status']} ({metric['reason_code']})"))
    lines.append(_line("Guardrails", "; ".join(
        f"{name} {item['state']}" for name, item in cohort["guardrails"].items()
    )))
    lines.append(_line("Baseline", _known(scorecard["baseline_cohort_id"])))
    lines.append(_line("Pareto cohorts", ", ".join(scorecard["pareto_cohort_ids"]) or "unknown"))
    lines.extend((
        _line("Policy", _ref(cohort["policy"])),
        _line("Rate card", _ref(cohort["rate_card"])),
        _line("Association rule", "unknown" if cohort["association_rule"] is None else
              f"{cohort['association_rule']['rule_id']} v{cohort['association_rule']['version']}"),
    ))
    return "\n".join(lines)


def _select_case(examples: dict[str, object], name: str) -> dict[str, object]:
    cases = examples["cases"]
    matches = [case for case in cases if case["name"] == name]
    if len(matches) != 1 or matches[0]["schema_id"] != name.split(":", 1)[0]:
        raise ValueError("portfolio_display_case_invalid")
    return matches[0]


def render_examples() -> str:
    budget_v1 = _read_known("budget_v1")
    budget_v2 = _read_known("budget_v2")
    budget_examples = _read_known("budget_examples")
    scorecard_wire = _read_known("scorecard_wire")
    scorecard_examples = _read_known("scorecard_examples")
    validate_budget_report_v2(budget_v1, budget_v2, budget_examples)
    if (
        scorecard_examples.get("schema_id") != "hormuz.portfolio-wire-examples"
        or scorecard_examples.get("schema_version") != 1
        or not isinstance(scorecard_examples.get("cases"), list)
    ):
        raise ValueError("portfolio_display_scorecard_fixture_invalid")
    sections = [
        "Synthetic examples for #223; no live result or role authorization.",
        _budget_case(_select_case(budget_examples, "hormuz.work-budget-report:first-activation")),
        _budget_case(_select_case(budget_examples, "hormuz.work-budget-report:increased")),
        _scorecard_case(_select_case(scorecard_examples, "hormuz.model-scorecard:minimal"), scorecard_wire),
        _scorecard_case(_select_case(scorecard_examples, "hormuz.model-scorecard:populated"), scorecard_wire),
    ]
    return "\n\n".join(sections) + "\n"


def main() -> int:
    if len(sys.argv) != 1:
        print("usage: python tools/render_portfolio_display_examples.py", file=sys.stderr)
        return 2
    try:
        print(render_examples(), end="")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError):
        print("portfolio_display_invalid_input", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
