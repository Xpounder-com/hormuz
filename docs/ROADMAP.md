# Hormuz enterprise roadmap

Hormuz is being built as a model-neutral AI policy and governed-context control plane for employees' existing Codex and Claude Code workflows. The durable product asset is authorized, source-linked organizational context; model routing and caching are governed execution mechanisms around it.

This roadmap is evidence-gated. A milestone is not complete because code exists, a narrow test passes, or an issue has no remaining checklist text. Every acceptance criterion must link to authoritative source, executable tests, package/deployment evidence, and any required product-owner decision.

## Current verified foundation

The private alpha currently has:

- OpenAI Responses and Anthropic Messages proxy compatibility, including streaming;
- organization/team/actor model policy, fallback, output caps, atomic token/spend reservations, and metadata-only reporting;
- pre-provider secret redaction/denial and default OpenAI storage restrictions;
- generic OIDC JWT discovery/JWKS verification with explicit issuer-subject identity mapping;
- accepted generic OIDC authorization-code + PKCE login, opaque rotating human sessions, replay-family revocation, and fail-closed OS secure-store custody;
- metadata-only audit export;
- a separate local persistent governed-context repository plus deterministic authorization-first context packs;
- authenticated authorization-first context-pack REST retrieval with server-owned caps, fail-closed durable metadata-only read audit, and no provider side effects;
- a dual-era read-only MCP adapter that connects Codex and Claude Code to that REST boundary without granting repository access or identity overrides;
- trusted, versioned repository/dependency snapshots with immediate per-read source/dependency invalidation, bounded high-confidence prompt-injection quarantine, and explicit structured contradiction outcomes;
- a reproducible 60-task synthetic retrieval benchmark whose strict version-2 release profile passes current authorization, lifecycle, dependency, quarantine, contradiction, budget, determinism, leakage, and latency thresholds;
- local and GitHub package/client verification.

The foundation is not an enterprise release. The local context database is single-node and plaintext, and the local session broker is also single-node; neither is an accepted hosted persistence design. Real-IdP validation, shared tenancy, SCIM, administrator revocation, source connectors, automatic verification/promotion/decay and resumable revalidation, context injection, structured DLP, cache policy, invoice reconciliation, HA deployment, KMS, and independent security review remain open gates.

## Material decisions awaiting owner approval

These issues record product decisions and block dependent implementation. They must not be closed by implementation convenience.

1. [#1 — Approve the enterprise tenancy, authorization, and persistence topology](https://github.com/Xpounder-com/hormuz/issues/1) — [Proposed ADR 0002](decisions/0002-enterprise-tenancy-and-persistence.md)
2. [#3 — Define provider and Hormuz cache privacy tiers](https://github.com/Xpounder-com/hormuz/issues/3) — [Proposed ADR 0003](decisions/0003-cache-privacy-tiers.md)

[ADR 0001](decisions/0001-oidc-login-and-session-architecture.md) was accepted by the product owner on 2026-08-15. Its implementation evidence is tracked under #13; acceptance of the decision does not close the remaining real-IdP, SCIM, administrator-revocation, and HA gates.

## v0.2 — Enterprise identity and tenancy

[Milestone](https://github.com/Xpounder-com/hormuz/milestone/2)

- [#2 — accepted OIDC login and session architecture decision](https://github.com/Xpounder-com/hormuz/issues/2)
- [#1 — Tenancy, authorization, and persistence decision](https://github.com/Xpounder-com/hormuz/issues/1)
- [#13 — Browser SSO and rotating Hormuz sessions](https://github.com/Xpounder-com/hormuz/issues/13)
- [#6 — Tenant-aware RBAC and PostgreSQL migrations](https://github.com/Xpounder-com/hormuz/issues/6)
- [#7 — SCIM provisioning, service accounts, and immediate revocation](https://github.com/Xpounder-com/hormuz/issues/7)

Exit outcome: employees and workloads authenticate through a deployable company identity boundary; every authorization and durable operation is tenant-scoped; revocation is immediate and testable.

## v0.3 — Governed context lifecycle

[Milestone](https://github.com/Xpounder-com/hormuz/milestone/1)

- [#4 — Persist governed context outside the usage ledger](https://github.com/Xpounder-com/hormuz/issues/4)
- [#18 — Authorization-first Context Pack API and MCP tool](https://github.com/Xpounder-com/hormuz/issues/18)
- [#12 — Automatic verification, promotion, decay, contradiction, and invalidation](https://github.com/Xpounder-com/hormuz/issues/12)
- [#5 — Inject governed context into Codex and Claude without a new UI](https://github.com/Xpounder-com/hormuz/issues/5)
- [#16 — completed retrieval, freshness, and authorization benchmark](https://github.com/Xpounder-com/hormuz/issues/16)

Exit outcome: Hormuz owns a persistent, source-linked and automatically maintained organizational-memory lifecycle and can inject the smallest authorized pack into supported clients with measurable retrieval quality and zero scope leaks.

## v0.4 — Privacy, cache, and accepted-task economics

[Milestone](https://github.com/Xpounder-com/hormuz/milestone/4)

- [#3 — Provider and Hormuz cache privacy decision](https://github.com/Xpounder-com/hormuz/issues/3)
- [#14 — Reusable context-pack cache with dependency-aware invalidation](https://github.com/Xpounder-com/hormuz/issues/14)
- [#10 — Structured DLP, PII policy, and untrusted-context defenses](https://github.com/Xpounder-com/hormuz/issues/10)
- [#8 — Provider invoice reconciliation and versioned rate cards](https://github.com/Xpounder-com/hormuz/issues/8)
- [#15 — Cost and quality per verified accepted engineering task](https://github.com/Xpounder-com/hormuz/issues/15)

Exit outcome: reuse is privacy-policy controlled and invalidated correctly; provider and Hormuz cache effects are distinguishable; cost claims reconcile to billing and improve accepted-task economics without weakening quality or security guardrails.

## v1.0 — Enterprise release gate

[Milestone](https://github.com/Xpounder-com/hormuz/milestone/3)

- [#11 — Production deployment with TLS, HA, readiness, and disaster recovery](https://github.com/Xpounder-com/hormuz/issues/11)
- [#17 — KMS/BYOK custody and tamper-evident audit retention](https://github.com/Xpounder-com/hormuz/issues/17)
- [#9 — Operational and independent security release gate](https://github.com/Xpounder-com/hormuz/issues/9)

Exit outcome: a supported deployment can be installed, upgraded, operated, restored, audited, and independently reviewed against a documented compatibility, privacy, security, and support boundary.

## Universal definition of done

An issue may close only when all applicable evidence exists:

1. the contract and threat/failure boundary are documented;
2. authorization and tenant isolation are enforced before data access or expensive work;
3. success, denial, concurrency, stale data, dependency outage, and rollback paths have executable tests;
4. content and credentials do not leak into metadata logs, usage storage, audit output, build artifacts, or errors;
5. source, wheel/container, supported-version, and migration/upgrade checks pass where relevant;
6. metrics distinguish estimated from reconciled facts and consumption from quality/productivity;
7. GitHub CI and any milestone-specific benchmark/release gate are green;
8. the issue links the exact evidence, and every `decision-required` item has explicit product-owner approval.

The [verification record](VERIFICATION.md) captures executed evidence. It is not a substitute for the open milestone acceptance criteria.
