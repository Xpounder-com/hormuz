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
| [0002](0002-enterprise-tenancy-and-persistence.md) | Accepted | Enterprise tenancy, authorization, and persistence | [#1](https://github.com/Xpounder-com/hormuz/issues/1) |
| [0003](0003-cache-privacy-tiers.md) | Superseded for core | Retire the Hormuz-owned context-cache proposal; archive it with the experiment | [#23](https://github.com/Xpounder-com/hormuz/issues/23) |
| [0004](0004-versioned-policy-control-plane.md) | Accepted | Versioned PostgreSQL policy control plane | [#21](https://github.com/Xpounder-com/hormuz/issues/21) |
| [0005](0005-kms-custody-and-immutable-audit-anchors.md) | Accepted | Generic key custody with optional AWS and self-hosted Object Lock profiles | [#17](https://github.com/Xpounder-com/hormuz/issues/17) |
| [0006](0006-commit-time-audit-chain-and-checkpoints.md) | Accepted | Per-organization commit-time audit chains and asynchronous checkpoints | [#72](https://github.com/Xpounder-com/hormuz/issues/72) |
| [0007](0007-tenant-custody-authority.md) | Accepted | Tenant custody authority and governed lifecycle approvals | [#89](https://github.com/Xpounder-com/hormuz/issues/89) |
| [0008](0008-signed-oci-deployment-contract.md) | Accepted | Signed OCI digest contract, first GHCR publication, keyless Sigstore identity, and deployment-profile separation | [#101](https://github.com/Xpounder-com/hormuz/issues/101) |
| [0009](0009-v1-deployment-profiles-and-recovery-objectives.md) | Accepted | v1 Compose and Kubernetes profile boundaries, state ownership, and reference-rehearsal RPO/RTO objectives | [#100](https://github.com/Xpounder-com/hormuz/issues/100) |
| [0010](0010-v1.1-portfolio-intelligence-contract.md) | Accepted | v1.1 additive portfolio, attribution, outcome, budget, scorecard, recommendation, privacy, and claim boundary | [#212](https://github.com/Xpounder-com/hormuz/issues/212) |

## Acceptance record

When the owner decides, update the ADR with:

- status and decision date;
- the chosen option and any requested changes;
- the owner's approving GitHub comment URL;
- implementation issues unblocked by the decision.

Do not rewrite the alternatives or rationale after acceptance. Supersede the ADR if the decision later changes.
