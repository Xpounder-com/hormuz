# Hormuz architecture decisions

Hormuz uses architecture decision records (ADRs) for choices that materially affect customer security, privacy, compatibility, operating cost, or data ownership.

## Statuses

- **Proposed:** researched and implementation-ready, but not approved.
- **Accepted:** explicitly approved by the product owner and safe to implement as the governing direction.
- **Rejected:** considered and deliberately declined.
- **Superseded:** replaced by a later ADR that links back to the original.

An agent, test result, or convenient implementation detail cannot move a decision from Proposed to Accepted. Acceptance requires an explicit product-owner decision recorded in both the ADR and its linked GitHub issue.

## Decision index

| ADR | Status | Decision | GitHub issue |
| --- | --- | --- | --- |
| [0001](0001-oidc-login-and-session-architecture.md) | Accepted | OIDC login and Hormuz session architecture | [#2](https://github.com/Xpounder-com/hormuz/issues/2) |
| [0002](0002-enterprise-tenancy-and-persistence.md) | Accepted | Enterprise tenancy, authorization, and persistence | [#1](https://github.com/Xpounder-com/hormuz/issues/1) |
| [0003](0003-cache-privacy-tiers.md) | Accepted | Provider and Hormuz cache privacy tiers | [#3](https://github.com/Xpounder-com/hormuz/issues/3) |
| [0004](0004-structured-dlp-and-approval-boundary.md) | Accepted | Structured DLP and approval boundary | [#10](https://github.com/Xpounder-com/hormuz/issues/10) |
| [0005](0005-github-lifecycle-event-trust.md) | Superseded | GitHub lifecycle event trust and collection | [#12](https://github.com/Xpounder-com/hormuz/issues/12) |
| [0006](0006-automatic-context-injection.md) | Superseded | Automatic governed-context injection | [#5](https://github.com/Xpounder-com/hormuz/issues/5) |
| [0007](0007-accepted-task-evaluation.md) | Superseded | Accepted-task economics evaluation | [#15](https://github.com/Xpounder-com/hormuz/issues/15) |
| [0008](0008-gateway-product-boundary.md) | Accepted | Hormuz gateway product boundary | [#20](https://github.com/Xpounder-com/hormuz/issues/20) |

## Acceptance record

When the owner decides, update the ADR with:

- status and decision date;
- the chosen option and any requested changes;
- the owner's approving GitHub comment URL;
- implementation issues unblocked by the decision.

Do not rewrite the alternatives or rationale after acceptance. Supersede the ADR if the decision later changes.

ADR 0002 and ADR 0003 were accepted by the product owner on 2026-08-20. They
authorize the hybrid PostgreSQL tenancy contract and conservative cache privacy
defaults respectively; acceptance does not claim implementation or release
completion. ADR 0006 was accepted on 2026-08-16 and authorizes gateway-side,
user-priority injection under issue #5 without closing the remaining
implementation, compatibility, privacy, or enterprise-release gates.

ADR 0008 was accepted by the product owner on 2026-08-20. It supersedes the
Hormuz-owned memory, retrieval, lifecycle, and accepted-task-quality direction.
Those implementations remain deprecated experimental compatibility evidence;
the supported product is the enterprise AI gateway and control plane.
