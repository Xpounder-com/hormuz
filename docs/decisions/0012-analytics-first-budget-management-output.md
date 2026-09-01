# ADR 0012: Analytics-first budget management output

- Status: Accepted product direction; preflight and runtime remain gated
- Date: 2026-08-31
- Target: Hormuz 1.1.0
- Issues: #214, #217, #222, #223, #224
- Supersedes: only the planned default budget-report output version in ADR 0011
- Approval: owner-approved direction recorded in
  https://github.com/Xpounder-com/hormuz/issues/217#issuecomment-5487469699
- Open gates: #214 preflight acceptance and #217 runtime acceptance

## Context

ADR 0010 correctly made a use case the primary analytics and model-selection
unit. ADR 0011 then froze separate version-1 budget preview and report records.
The version-1 report exposes the active plan amount, burn, remaining amount,
forecast, uncertainty and coverage, but it does not tell a manager what changed
in the budget at the moment the current plan became effective.

Requiring a consumer to fetch and join plan-version history merely to render
"budget increased 20% at 09:30" makes the first result storage-friendly rather
than management-friendly. Adding a required field to the digest-pinned
version-1 report would be a breaking response change.

## Decision

The first runtime budget report will be `hormuz.work-budget-report` version 2.
Version 1 remains unchanged and is never silently upgraded. The version-1
preview remains version 1.

The version-2 report adds one required `plan_change` object to the current
work-scope row. It identifies the immediately prior active plan, the activation
commit time, exact prior amount and its prior scope/window/currency basis, exact
signed amount delta, and a presentation percentage. The percentage is decimal
half-even at six fractional places; the exact amounts remain authoritative for
accounting and downstream recomputation.

The first activation reports `established`, not a fabricated percentage. A
zero prior amount retains the exact amount delta but has no percentage. Currency,
work-scope or window changes are not comparable; the prior basis and every
non-comparability reason remain explicit so an old amount is never labeled with
current units. Missing evidence is never converted to zero. `changed_at` is the
active-pointer transaction's commit time, not candidate creation time or a
client clock.

Hormuz stores immutable plan and activation facts plus a mutable active-pointer
projection. It derives the compact change fact from the current and immediately
prior active plan. It does not add a duplicated user-facing revision ledger.
The internal facts remain necessary because pre-egress enforcement, concurrent
replicas, rollback and historical attempt binding must resolve the exact plan
that was active at event time.

The version-2 report is a management projection, not an activation command.
It cannot alter a plan, policy, model, route or cap. The first #217 runtime may
deliver it only to `portfolio_admin`; #223 must separately prove broader
finance, platform and team-lead authorization and stable CLI/API delivery.

## Compatibility

- The version-1 report bundle and every frozen portfolio/v1 contract remain
  byte-for-byte unchanged.
- No existing route, request, response, error, pagination, auth or CLI behavior
  changes in the preflight.
- Runtime must select report version 2 explicitly. A version-1 consumer is not
  handed version 2 under the same negotiated contract.
- No deprecation or sunset is declared: version 1 is a preserved design
  contract, while version 2 is the initial runtime management output.

## Consequences

Managers and analytics consumers receive a useful current row from the first
implementation: current plan, burn/remaining/coverage, and the latest effective
change. Enforcement still has the exact immutable facts it needs. The tradeoff
is one explicit output version transition before runtime, plus deterministic
rounding and non-comparability rules that implementation and client tests must
honor.

This ADR is not #214 preflight acceptance, #217 runtime acceptance, role-view
acceptance, finance-grade reconciliation, an external pilot, or a release.
