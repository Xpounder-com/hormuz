# Portfolio terminal display examples (synthetic prototype)

These four terminal views illustrate the planned
[portfolio views in #223](https://github.com/Xpounder-com/hormuz/issues/223).
They are rendered only from checked-in synthetic fixtures. The budget records
use the internal version-2 report shape described in
[WORK_BUDGETS](WORK_BUDGETS.md); the scorecards use the planned version-1 wire
shape described in [PORTFOLIO_INTELLIGENCE](PORTFOLIO_INTELLIGENCE.md). The
older additive design boundary is in
[PORTFOLIO_EXTENSIONS](PORTFOLIO_EXTENSIONS.md). These are not live portfolio
results, public commands, authorization decisions, or proof that #222
scorecards are implemented. The existing public portfolio command still prints
JSON.

From a source checkout after the [development setup](../CONTRIBUTING.md), run:

```bash
python tools/render_portfolio_display_examples.py
```

The developer-only helper accepts no arguments, paths, credentials, or network
input. It reads the exact
[budget examples](../tests/fixtures/portfolio_intelligence/budget-report-v2-examples.json)
and [planned wire examples](../tests/fixtures/portfolio_intelligence/wire-v1-examples.json),
checks their pinned bytes, and applies the existing budget schema/domain and
scorecard wire validators before formatting. The scorecard schema validator
checks structure; there is no runtime scorecard computation or role
authorization here. Ordering, dates, money strings, and evidence qualifiers
come from the fixtures. `unknown` denotes a missing value; `0` appears only
where the fixture explicitly says zero. Cost bases are displayed separately;
the helper performs no currency conversion or financial summation.

<!-- generated example output -->
```text
Synthetic examples for #223; no live result or role authorization.

=== Budget report (synthetic) ===
Case: hormuz.work-budget-report:first-activation
Input: hormuz.work-budget-report v2
Window: 2026-08-01T00:00:00Z to 2026-09-01T00:00:00Z
As of: 2026-08-16T12:00:00Z; generated 2026-08-31T12:00:00Z
Scope: use-case-test v1
Plan: budget-test v1; activation 1; USD 100
Plan digest: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Change: established; prior amount unknown (first activation)
Cost basis: configured_rate_card_estimate (gateway estimate)
Committed: unknown
Pending reservation: unknown
Uncertain reservation: unknown
Remaining balance (derived): unknown (missing_evidence)
Observation: not_available; unknown; missing_evidence
Forecast: not available (missing_evidence)
Coverage: included unknown/unknown; priced unknown/unknown (missing_evidence)
Valuation rule: reservation-rule-test v1
Coverage rule: coverage-test v1
Policy: policy-test; digest
  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

=== Budget report (synthetic) ===
Case: hormuz.work-budget-report:increased
Input: hormuz.work-budget-report v2
Window: 2026-08-01T00:00:00Z to 2026-09-01T00:00:00Z
As of: 2026-08-16T12:00:00Z; generated 2026-08-31T12:00:00Z
Scope: use-case-test v1
Plan: budget-test v2; activation 2; USD 120
Plan digest: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
Change: increased; prior USD 100; delta USD 20 (20%)
Cost basis: configured_rate_card_estimate (gateway estimate)
Committed: USD 10
Pending reservation: USD 1
Uncertain reservation: USD 1
Remaining balance (derived): USD 108 (known)
Observation: configured_rate_card_estimate; USD 10; known
Rate card: rate-card-test v1
Rate card digest:
  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
Forecast: USD 20; configured_rate_card_estimate; linear_committed_projection;
  excludes reservations
Coverage: included 20/20; priced 20/20 (known)
Valuation rule: reservation-rule-test v1
Coverage rule: coverage-test v1
Policy: policy-test; digest
  aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa

=== Model scorecard (planned, synthetic) ===
Case: hormuz.model-scorecard:minimal
Input: hormuz.model-scorecard v1
Window: 2026-08-01T00:00:00Z to 2026-08-02T00:00:00Z
Freshness: generated 2026-08-03T00:00:00Z; expires 2026-09-01T00:00:00Z; review
  2026-08-15T00:00:00Z
Scope: example-id v1
Scorecard: example-id v1
State: inconclusive (missing_evidence); evidence descriptive
Coverage eligible_governed_attempts: 0/0; ratio unknown (missing_evidence)
Coverage eligible_governed_spend: 0/0; ratio unknown (missing_evidence)
Coverage eligible_external_outcome_events: 0/0; ratio unknown (missing_evidence)
Coverage eligible_association_candidates: 0/0; ratio unknown (missing_evidence)
Coverage pricing: 0/0; ratio unknown (missing_evidence)
Coverage connector: 0/0; ratio unknown (missing_evidence)
Coverage association: 0/0; ratio unknown (missing_evidence)
Cohort: cohort-1
Model: unknown
Cost basis: provider_final (declared for this cohort)
Cost component: not available (no components); currency unknown
Eligibility: inconclusive; sample 0, minimum unknown; observed coverage 0,
  minimum unknown
Metric use_case_attributed_spend_coverage: unknown ratio; inconclusive
  (missing_evidence)
Metric quality_qualified_cost_per_accepted_work_item: unknown
  currency_per_accepted_item; inconclusive (missing_evidence)
Metric optimization_lift_vs_declared_baseline: unknown relative_lift;
  inconclusive (missing_evidence)
Guardrails: quality inconclusive; reversion_or_defect inconclusive; reliability
  inconclusive; privacy inconclusive; budget inconclusive;
  coverage_and_sample_eligibility inconclusive; freshness inconclusive;
  comparability inconclusive
Baseline: unknown
Pareto cohorts: unknown
Policy: unknown
Rate card: unknown
Association rule: unknown

=== Model scorecard (planned, synthetic) ===
Case: hormuz.model-scorecard:populated
Input: hormuz.model-scorecard v1
Window: 2026-08-01T00:00:00Z to 2026-08-02T00:00:00Z
Freshness: generated 2026-08-03T00:00:00Z; expires 2026-09-01T00:00:00Z; review
  2026-08-15T00:00:00Z
Scope: example-id v1
Scorecard: example-id v1
State: eligible (eligible); evidence associated
Coverage eligible_governed_attempts: 1/2; ratio 0.5 (eligible)
Coverage eligible_governed_spend: 1/2; ratio 0.5 (eligible)
Coverage eligible_external_outcome_events: 1/2; ratio 0.5 (eligible)
Coverage eligible_association_candidates: 1/2; ratio 0.5 (eligible)
Coverage pricing: 1/2; ratio 0.5 (eligible)
Coverage connector: 1/2; ratio 0.5 (eligible)
Coverage association: 1/2; ratio 0.5 (eligible)
Cohort: cohort-1
Model: provider example-id; model example-id; version example-id
Cost basis: provider_final (declared for this cohort)
Cost component: USD 1; provider_final (not summed)
Eligibility: eligible; sample 1, minimum 1; observed coverage 0.5, minimum 0.5
Metric use_case_attributed_spend_coverage: 0.5 ratio; eligible (eligible)
Metric quality_qualified_cost_per_accepted_work_item: 0.5
  currency_per_accepted_item; eligible (eligible)
Metric optimization_lift_vs_declared_baseline: 0.5 relative_lift; eligible
  (eligible)
Guardrails: quality pass; reversion_or_defect pass; reliability pass; privacy
  pass; budget pass; coverage_and_sample_eligibility pass; freshness pass;
  comparability pass
Baseline: cohort-1
Pareto cohorts: cohort-1
Policy: example-id v1
Rate card: example-id v1
Association rule: example-id v1
```

The first report has no supported financial observation or forecast. In the
increase report, committed and projected costs use configured-rate estimates;
pending and uncertain reservations are holds under that basis. The remaining
balance is derived from the plan amount less those obligations. It is not an
independently observed cash balance or provider-final cost. The minimal
scorecard has explicit zero denominators with **unknown ratios** and remains
inconclusive.
The populated scorecard is only a synthetic, associated-evidence example; its
metric names and `eligible` fixture state do not establish causal improvement
or a universal model winner. #223 remains the owner for actual role-scoped
commands, APIs, and authorization.
