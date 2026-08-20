# Hormuz enterprise roadmap

Hormuz is being built as a model-neutral AI policy and governed-context control plane for employees' existing Codex and Claude Code workflows. The durable product asset is authorized, source-linked organizational context; model routing and caching are governed execution mechanisms around it.

This roadmap is evidence-gated. A milestone is not complete because code exists, a narrow test passes, or an issue has no remaining checklist text. Every acceptance criterion must link to authoritative source, executable tests, package/deployment evidence, and any required product-owner decision.

## Current verified foundation

The private alpha currently has:

- OpenAI Responses and Anthropic Messages proxy compatibility, including streaming;
- organization/team/actor model policy, fallback, output caps, atomic token/spend reservations, metadata-only reporting, and authenticated policy-filtered Claude Code model discovery;
- request-level actual-model, provider-native usage, billable-token, cost-basis, currency, and immutable rate-card-version snapshots, plus local exact-decimal OpenAI/Anthropic offline import, operator-authenticated fixed-query ingestion, and aggregate variance; scheduled polling, secure hosted credential custody, final invoice/credit reconciliation, and finance workflows remain open;
- pre-provider secret controls plus a bounded structured-DLP subset with high-confidence SSN/card redaction, low-confidence email detection, provider/model-scoped company dictionaries, monotonic identity-derived team/person tightening, JSON string-key, three-view nested provider-query decoding, and allowlisted forwarded-header fail-closed enforcement, bounded recursive printable UTF-8 base64/base64url, textual-data-URI, percent, and hexadecimal inspection with ambiguous percent/hex redaction denied rather than rewritten, provider-format-aware opaque-media denial, metadata-only non-self, 15-minute, exact single-use approval grants, an offline content-free aggregate detector-evaluation CLI, and one opt-in content-free live OpenAI observation in which a fixed synthetic secret was redacted before the provider returned only the sanitized placeholder; an actual organization-representative corpus, threshold decision, and remaining #10 gates stay open;
- generic OIDC JWT discovery/JWKS verification with explicit issuer-subject identity mapping;
- accepted generic OIDC authorization-code + PKCE login, opaque rotating human sessions, replay-family revocation, and fail-closed OS secure-store custody;
- tenant/actor/team/clearance-bound human sessions plus capability-gated, tenant-scoped local session and security-event inspection with immediate administrative revocation;
- capability-gated remote usage administration with identity-derived organization scope, frozen-window pagination, per-page read audit, and organization-scoped self usage, secret summaries, policy totals, and concurrent reservations;
- metadata-only audit export;
- a separate local persistent governed-context repository plus deterministic authorization-first context packs;
- authenticated authorization-first context-pack REST retrieval with server-owned caps, versioned retrieval/render manifests, explicit authorized freshness/provisional outcomes, fail-closed durable metadata-only read audit, and no provider side effects;
- a dual-era read-only MCP adapter that connects Codex and Claude Code to that REST boundary without granting repository access or identity overrides, using either the original inherited workload credential or a client-bound OS secure-store session resolved and refreshed per tool call;
- an opt-in installed-client proof on macOS 26.2 arm64 in which pinned Codex and Claude Code use a real Keychain-backed Hormuz session for inference, execute a model-requested governed-context MCP call through the same profile, and return the authorized pack without a provider or static employee credential in the child environment; this is local compatibility evidence rather than a blocking cross-platform or real-IdP gate;
- disabled-by-default gateway-side user-priority injection of verified organization/team/actor packs into supported OpenAI Responses and Anthropic Messages requests, including exact administrator-granted repository/branch/trusted-revision selection carried by supported Codex and Claude Code configuration, with monotonic repository/classification/model/client caps, consumed-header and post-injection DLP, content-free usage lineage, required-mode denials, and installed-client compatibility evidence;
- trusted, versioned repository/dependency snapshots with immediate per-read source/dependency invalidation, bounded high-confidence prompt-injection quarantine, and explicit structured contradiction outcomes;
- opt-in `context_promoter`-gated evidence import and policy-driven promotion/invalidation for merge, CI, review, ADR, incident, human, and validated-failure signals, with subject-bound evidence, false-invalidation recovery, and durable local record/evidence-set-bound revalidation jobs;
- a config-independent, authenticated remote lifecycle CLI/API through which capability-gated workloads can submit normalized evidence, snapshots, and bounded revalidation work without provider calls or raw-reference audit retention;
- a reproducible 60-task synthetic retrieval benchmark whose strict version-2 release profile passes current authorization, lifecycle, dependency, quarantine, contradiction, budget, determinism, leakage, and latency thresholds;
- local and GitHub package/client verification, including a six-wheel Python 3.11+ cross-platform hash-locked build closure, no backend re-resolution, hash-locked binary runtime/test installation followed by no-dependency editable or wheel installation and dependency-integrity checks, an exact Node/npm toolchain plus a 16-package SHA-512 integrity-locked Codex/Claude Code closure for ordinary and tag verification, an intentionally dynamic upstream-client canary, two independent checked-out-commit package builds with byte-identical wheels and canonical source archives, plus two clean exact-source BuildKit exports per platform for `linux/amd64` and `linux/arm64` with strict OCI validation and byte-equality evidence; this does not prove universal cross-builder or cross-host equality, offline build-input availability, arbitrary downstream wheel-install environments, every future client/protocol version, or an observed published release;
- a versioned, machine-validated internal threat register covering 9 assets, 7 trust boundaries, 18 threats, all six STRIDE categories, and all seven incident scenarios named by issue #9, with repository-resolved control evidence and explicit residual release gates; it reports 1 mitigated, 12 partially mitigated, and 5 open threats and keeps enterprise readiness false while independent review is pending;
- a strict repository-local incident-drill catalog that binds those seven scenarios to exact executable regressions and retains private content-free aggregate evidence in ordinary and tag CI, while explicitly leaving real infrastructure exercises, named on-call ownership, external communications, response/recovery targets, and enterprise readiness incomplete;
- nullable content-free gateway, policy, and provider timing snapshots plus opt-in tenant-scoped cumulative histograms that preserve the exact usage-report v2 contract and bind the v3 latency view into pagination; this is SLI instrumentation, not approved SLO targets, alerting, end-to-end coverage, or production observability;
- a versioned, machine-validated compatibility matrix that binds exact Codex, Claude Code, Python, provider-protocol, OIDC, persistence, package, and OCI claims to repository evidence while keeping live-provider conformance, real IdPs, PostgreSQL, and production deployment explicitly unsupported or owner-pending;
- optional canonical usage/security and governed-context audit chains with strict config-free verification against an externally retained count/head and exact-file digest; this is export-time tamper/gap evidence, not database append-only storage, a signed anchor, KMS custody, continuous cross-store sequencing, or immutable retention;
- an explicit bounded accept-backlog hint, a pre-thread accepted-connection ceiling, absolute total request-header deadlines for initial and keep-alive requests, an absolute exact-length request-body deadline, shallow versioned liveness/readiness probes, atomic parsed-request capacity with saturation-aware readiness, a total provider-response relay deadline, and bounded `SIGTERM` request draining, with activation, capacity recovery, idle keep-alive, in-flight, continuous-trickle, early-EOF, and content-free failure regression coverage;
- fail-closed, size-bounded startup configuration parsing with duplicate-member/non-standard-number rejection across Hormuz-owned root and nested objects, non-reflective diagnostics, strict model/team/actor policy reference integrity, optional exact-file SHA-256 deployment binding, and an explicit immutable-snapshot replacement/rollback contract;
- a tag-only, least-privilege private GHCR release contract with multi-platform build, keyless exact-digest and SLSA-predicate signing, private-package validation, immutable alias checks, content-free evidence, and digest-based rollback documentation. It is release automation, not evidence of an observed package or production deployment; public-transparency approval, tag governance, TLS, HA, backup/restore, RPO/RTO, and the rest of #11 remain open.

The foundation is not an enterprise release. The local usage/approval, context, and session databases are single-node, and the local context codec is plaintext; none is an accepted hosted persistence design. Real-IdP validation, shared tenancy and multi-node revocation, SCIM, source-specific lifecycle collectors and signed-event verification, hosted scheduling, remaining time/confidence decay policy, automatic trusted repository discovery, continuation binding and complete automatic-injection quality evidence, remaining structured-DLP coverage, cache policy, scheduled billing ingestion with secure hosted credential custody, final invoice reconciliation, configuration signing/change approval, live secret rotation, deployment-coordinated rollback, HA deployment, KMS, and independent security review remain open gates.

## Material decisions awaiting owner approval

These issues record product decisions and block dependent implementation. They must not be closed by implementation convenience.

1. [#1 — Approve the enterprise tenancy, authorization, and persistence topology](https://github.com/Xpounder-com/hormuz/issues/1) — [Proposed ADR 0002](decisions/0002-enterprise-tenancy-and-persistence.md)
2. [#3 — Define provider and Hormuz cache privacy tiers](https://github.com/Xpounder-com/hormuz/issues/3) — [Proposed ADR 0003](decisions/0003-cache-privacy-tiers.md)
3. [#12 — Choose GitHub lifecycle event trust and collection](https://github.com/Xpounder-com/hormuz/issues/12) — [Proposed ADR 0005](decisions/0005-github-lifecycle-event-trust.md)
4. [#15 — Choose the accepted-task economics evaluation rule](https://github.com/Xpounder-com/hormuz/issues/15) — [Proposed ADR 0007](decisions/0007-accepted-task-evaluation.md)

[ADR 0001](decisions/0001-oidc-login-and-session-architecture.md) was accepted by the product owner on 2026-08-15. Its implementation evidence is tracked under #13; acceptance of the decision does not close the remaining real-IdP, SCIM, shared-revocation, and HA gates.

[ADR 0004](decisions/0004-structured-dlp-and-approval-boundary.md) was accepted by the product owner on 2026-08-15. The `hormuz-deterministic-v2` detector/action subset, monotonic team/person overlays, JSON string-key, bounded three-view provider-query decoding, allowlisted forwarded-header fail-closed enforcement, bounded base64/percent/hex encoded-text inspection, recognized compression/archive-container denial, provider-format-aware opaque-media denial, local replay-safe approval workflow, and content-free aggregate detector evaluator now exist. Actual organization-representative evaluation and threshold approval, source classification, application-specific parsing beyond the bounded query views, unknown encoding/container and archive-content inspection, semantic detection, multi-node approval operations, cache invalidation, and the full #10 release evidence remain open.

[ADR 0006](decisions/0006-automatic-context-injection.md) was accepted by the product owner on 2026-08-16. It authorizes gateway-side, user-priority automatic context injection under #5 with injection disabled by default and verified-only as the production default. Exact repository grants, supported-client selectors, branch/revision narrowing, classification caps, consumed-header DLP, and content-free lineage are implemented; continuation binding remains a separate material owner decision, and acceptance does not close the remaining provider compatibility, quality, hosted persistence, or enterprise release gates.

[ADR 0007](decisions/0007-accepted-task-evaluation.md) proposes a matched-task, paired task-cluster evaluation with predeclared cost, quality, time, and safety gates. It remains unapproved; no evaluator implementation or claim that Hormuz improves accepted-task economics is authorized until the product owner selects a method, and numeric release thresholds require a later approval before treatment results are unblinded.

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
