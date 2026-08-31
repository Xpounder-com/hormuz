# ADR 0011: Additive budget reports and Linear parent/context records

- **Status:** Accepted design direction; runtime and feature checkpoints remain open.
- **Decision date:** August 31, 2026.
- **Owner decision records:** [budget #217](https://github.com/Xpounder-com/hormuz/issues/217#issuecomment-5482219903) and [Linear #220](https://github.com/Xpounder-com/hormuz/issues/220#issuecomment-5482220483).
- **Release:** v1.1.0, under #226; no release/tag or deployment authorization.
- **Contract:** [portfolio-extension-contract-v1.json](../portfolio-extension-contract-v1.json).

## Context and decision

The owner approved two separately versioned additions to ADR 0010. The frozen
budget-plan envelope has no preview/burn/forecast fields. The frozen outcome
envelope represents issues and pull requests, not Linear initiatives, projects
or cycles. Editing those digest-pinned schemas would silently break the
accepted contract. Reducing Linear to issue-only ingestion was not approved.

Add five independent version-1 record schemas: work-budget preview and report,
Linear context event and page, and a separate context-retention marker. The
existing v1 manifest, routes, errors, CLI behavior, budget plan/request/
activation, outcome event/page/receipt and installed wire catalogues remain
unchanged. This is a design/fixture layer, not an installed feature.

The budget preview pins the immutable candidate, work scope, policy, scenario
suite, active generation and input snapshot. It is administrator-only, expires
within 15 minutes and cannot activate policy, reserve spend or call a provider.
Reports are scoped aggregates; unknowns remain null, evidence bases separate,
and broader provider totals cannot become work-scope costs. An optional exact
linear committed-estimate projection is explicitly not predictive confidence,
guaranteed savings or final invoice spend, and excludes pending/uncertain holds.

Linear context retains typed metadata and parent sets independently of issue
outcomes. Parent relationships do not grant authority, select a primary work
scope or prove that an AI run caused an outcome. Raw context remains
administrator-only. Missing/partial parent coverage cannot remove prior links.
A separate operator retention marker cannot impersonate provider deletion.

## Alternatives

- Mutating the frozen version-1 plan/outcome shapes: rejected for compatibility.
- Treating initiatives/projects/cycles as issues or dropping them: rejected
  because it mislabels evidence or narrows the approved work order.
- Replacing all outcome/page contracts with a new expanded version: not selected;
  the narrower independent-record extension preserves existing consumers.

## Consequences and implementation sequence

Reuse the existing offline closed-schema validator, with a separate
digest-pinned extension verifier and explicit synthetic semantic checks.
This avoids a second runtime validation framework or a premature persistence
refactor. No new runtime imports, dependencies, credentials, service, CLI
command, storage table or migration is introduced.

This record resolves only the owner design choices. Budget #217 needs its own
#214 preflight proving one real transaction over old and work-scope ceilings,
fixed lock order, conservative settlement, replica races and recovery. The
shared SQL helper and attribution seam do not establish cross-repository
atomicity. Preserve the public facades, pool/credential ownership and off-path
behavior in any later transaction-aware extraction.

Linear #220 needs its own typed authority, raw-byte signature, replay,
normalizer/action allowlist, bounded durable acknowledgment and recovery
checkpoint. Existing project-only registry enrollment does not grant access to
all initiatives/cycles/teams. Keep any later authorization-binding extension
separate rather than overloading that existing configuration.

Finance's accepted preflight still reserves SQLite 7-to-8 / PostgreSQL 11-to-12.
This contract-only work consumes no schema number. Sequence real migrations
explicitly and supersede the affected plan if their order changes; never edit a
frozen plan or pretend an unimplemented predecessor exists.

See [PORTFOLIO_EXTENSIONS.md](../PORTFOLIO_EXTENSIONS.md) for consumer guidance,
source references, test boundaries and the remaining gates. The owner still
must select/authorize live integration resources and the independent pilot.
