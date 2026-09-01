# Work-budget transition checkpoint

Hormuz 1.1.0 will keep exact immutable plan and activation facts internally,
while presenting a current management report rather than making consumers
reconstruct budget history. The implementation decision is frozen in
[`budget-transition-plan-v1.json`](budget-transition-plan-v1.json), ADR 0012,
and the separate version-2 report bundle.

## What this preflight changes

- adds a red-first 8→9 SQLite and 12→13 PostgreSQL transition plan for #217;
- binds the actual accepted finance-history predecessor by commit and archive
  digest;
- preserves the version-1 report bundle unchanged;
- defines `hormuz.work-budget-report` version 2 with a required compact
  `plan_change` fact; and
- packages offline verification, synthetic examples and transition witnesses.

It changes no runtime schema version, table, route, command, role, provider
request or active budget.

## Management result

The current report row carries the current active plan and the immediately
preceding activation comparison, including its prior scope, period and currency.
For example, an activation from USD 100 to USD 120 yields exact amount delta
`20`, display percent `20`, and the database commit time in `changed_at`.

The result is `established` for a first activation. A previous zero amount has
an exact amount delta but an undefined percentage. Different currency,
work-scope or period comparisons are marked not comparable, list every reason,
and retain the old basis without pretending it uses current units. Missing
evidence stays missing. Percentages use decimal half-even at six places; exact
plan amounts remain the accounting source.

## Why internal versions remain

The current row is a projection, not the storage model. One governed request
must bind the exact plan, work-scope, policy and rate card active before provider
egress. Competing PostgreSQL replicas must check every organization, team,
actor, application, policy, portfolio, initiative and use-case ceiling in one
transaction. Rollback and ambiguous outcomes also require exact event-time
facts. Those small metadata records support correctness without forcing a
manager to browse a revision ledger.

## Runtime boundary still required

The implementation PR must replace the deliberately red missing-migration
witnesses with real additive migrations and retain every transition guarantee.
It must prove atomic pre-egress reservation, deny-wins hierarchy behavior,
independent-replica concurrency, exact decimals, period boundaries, unknown
holds, activation/replacement/rollback, populated recovery, and current report
version 2. The first runtime role is `portfolio_admin`; broader role delivery
remains #223.

Finance-grade reconciliation remains #8. Scorecards, recommendations, external
validation, release, tagging and deployment remain separately gated.

## Local verification

From the repository root:

```console
python3 tools/verify_budget_transition_plan.py
python3 -m unittest -v tests.test_budget_transition_plan
python3 -m unittest -v tests.test_sqlite_budget_transition
```

PostgreSQL and actual-predecessor cases run in the protected CI transition job
with disposable storage and the digest-bound predecessor interpreter.
