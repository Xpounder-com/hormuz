# Hormuz enterprise roadmap

Hormuz is being built as a model-neutral AI policy and governed-context control plane for employees' existing Codex and Claude Code workflows. The durable product asset is authorized, source-linked organizational context; model routing and caching are governed execution mechanisms around it.

This roadmap is evidence-gated. A milestone is not complete because code exists, a narrow test passes, or an issue has no remaining checklist text. Every acceptance criterion must link to authoritative source, executable tests, package/deployment evidence, and any required product-owner decision.

## Current verified foundation

The private alpha currently has:

- OpenAI Responses and Anthropic Messages proxy compatibility, including streaming;
- organization/team/actor model policy, fallback, output caps, atomic token/spend reservations, and metadata-only reporting;
- request-level actual-model, provider-native usage, billable-token, cost-basis, currency, and immutable rate-card-version snapshots, plus a local exact-decimal OpenAI/Anthropic cost-report import and aggregate variance kernel; authenticated polling, provable report scope, final invoice/credit reconciliation, and finance workflows remain open;
- pre-provider secret controls plus a bounded structured-DLP subset with high-confidence SSN/card redaction, low-confidence email detection, provider/model-scoped company dictionaries, monotonic identity-derived team/person tightening, JSON string-key fail-closed enforcement, bounded recursive UTF-8 base64/base64url and textual-data-URI inspection, provider-format-aware opaque-media denial, and metadata-only non-self, 15-minute, exact single-use approval grants; remaining #10 gates stay open;
- generic OIDC JWT discovery/JWKS verification with explicit issuer-subject identity mapping;
- accepted generic OIDC authorization-code + PKCE login, opaque rotating human sessions, replay-family revocation, and fail-closed OS secure-store custody;
- tenant/actor/team/clearance-bound human sessions plus capability-gated, tenant-scoped local session and security-event inspection with immediate administrative revocation;
- capability-gated remote usage administration with identity-derived organization scope, frozen-window pagination, per-page read audit, and organization-scoped self usage, secret summaries, policy totals, and concurrent reservations;
- metadata-only audit export;
- a separate local persistent governed-context repository plus deterministic authorization-first context packs;
- authenticated authorization-first context-pack REST retrieval with server-owned caps, versioned retrieval/render manifests, explicit authorized freshness/provisional outcomes, fail-closed durable metadata-only read audit, and no provider side effects;
- a dual-era read-only MCP adapter that connects Codex and Claude Code to that REST boundary without granting repository access or identity overrides;
- trusted, versioned repository/dependency snapshots with immediate per-read source/dependency invalidation, bounded high-confidence prompt-injection quarantine, and explicit structured contradiction outcomes;
- opt-in `context_promoter`-gated evidence import and policy-driven promotion/invalidation for merge, CI, review, ADR, incident, human, and validated-failure signals, with subject-bound evidence, false-invalidation recovery, and durable local record/evidence-set-bound revalidation jobs;
- a config-independent, authenticated remote lifecycle CLI/API through which capability-gated workloads can submit normalized evidence, snapshots, and bounded revalidation work without provider calls or raw-reference audit retention;
- a reproducible 60-task synthetic retrieval benchmark whose strict version-2 release profile passes current authorization, lifecycle, dependency, quarantine, contradiction, budget, determinism, leakage, and latency thresholds;
- local and GitHub package/client verification.

The foundation is not an enterprise release. The local usage/approval, context, and session databases are single-node, and the local context codec is plaintext; none is an accepted hosted persistence design. Real-IdP validation, shared tenancy and multi-node revocation, SCIM, source-specific lifecycle collectors and signed-event verification, hosted scheduling, remaining time/confidence decay policy, context injection, remaining structured-DLP coverage, cache policy, authenticated billing ingestion and final invoice reconciliation, HA deployment, KMS, and independent security review remain open gates.

## Material decisions awaiting owner approval

These issues record product decisions and block dependent implementation. They must not be closed by implementation convenience.

1. [#1 — Approve the enterprise tenancy, authorization, and persistence topology](https://github.com/Xpounder-com/hormuz/issues/1) — [Proposed ADR 0002](decisions/0002-enterprise-tenancy-and-persistence.md)
2. [#3 — Define provider and Hormuz cache privacy tiers](https://github.com/Xpounder-com/hormuz/issues/3) — [Proposed ADR 0003](decisions/0003-cache-privacy-tiers.md)
3. [#12 — Choose GitHub lifecycle event trust and collection](https://github.com/Xpounder-com/hormuz/issues/12) — [Proposed ADR 0005](decisions/0005-github-lifecycle-event-trust.md)

[ADR 0001](decisions/0001-oidc-login-and-session-architecture.md) was accepted by the product owner on 2026-08-15. Its implementation evidence is tracked under #13; acceptance of the decision does not close the remaining real-IdP, SCIM, shared-revocation, and HA gates.

[ADR 0004](decisions/0004-structured-dlp-and-approval-boundary.md) was accepted by the product owner on 2026-08-15. The deterministic detector/action subset, monotonic team/person overlays, JSON string-key fail-closed enforcement, bounded supported encoded-text inspection, provider-format-aware opaque-media denial, and local replay-safe approval workflow now exist, but source classification, provider-header inspection, unsupported or compressed/archive decoding, semantic evaluation, multi-node approval operations, cache invalidation, and the full #10 release evidence remain open.

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
- [#18 — completed authorization-first Context Pack API and MCP tool](https://github.com/Xpounder-com/hormuz/issues/18)
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
