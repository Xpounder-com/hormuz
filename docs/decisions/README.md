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
| [0001](0001-oidc-login-and-session-architecture.md) | Proposed | OIDC login and Hormuz session architecture | [#2](https://github.com/Xpounder-com/hormuz/issues/2) |
| [0002](0002-enterprise-tenancy-and-persistence.md) | Proposed | Enterprise tenancy, authorization, and persistence | [#1](https://github.com/Xpounder-com/hormuz/issues/1) |
| [0003](0003-cache-privacy-tiers.md) | Proposed | Provider and Hormuz cache privacy tiers | [#3](https://github.com/Xpounder-com/hormuz/issues/3) |

## Acceptance record

When the owner decides, update the ADR with:

- status and decision date;
- the chosen option and any requested changes;
- the owner's approving GitHub comment URL;
- implementation issues unblocked by the decision.

Do not rewrite the alternatives or rationale after acceptance. Supersede the ADR if the decision later changes.
