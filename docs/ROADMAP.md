# Hormuz enterprise roadmap

Hormuz is a CLI-first enterprise AI gateway and control plane for employees' existing Codex and Claude Code workflows. It connects company-managed OpenAI and Anthropic access, enforces organization policy automatically, and provides authorized token, cost, usage, security, and coverage reporting.

Hormuz does not own organizational knowledge quality. Claims, decisions, provenance, source evidence, retrieval, and memory lifecycle belong to a separate product. [ADR 0008](decisions/0008-gateway-product-boundary.md) defines the boundary and the compatibility treatment for the earlier built-in context experiment.

Progress is evidence-gated and ordered by GitHub issue. A milestone is not complete because code exists: its issue must link the exact contract, tests, package/deployment evidence, PR/commit, CI result, and any remaining limitations.

## Current verified foundation

The current draft-PR line has executable evidence for:

- OpenAI Responses and Anthropic Messages compatibility for Codex and Claude Code, including streaming and authenticated model discovery;
- organization/team/person policy overlays, model allowlists and fallback, output caps, atomic token/spend reservations, and metadata-only reporting;
- generic OIDC validation, browser authorization-code + PKCE login, rotating Hormuz sessions, secure-store client custody, scoped session administration, and immediate revocation;
- structured secret/DLP controls, exact-request approval grants, content-free security evidence, and pre-provider enforcement;
- SQLite development storage plus opt-in PostgreSQL usage, cost, identity projection, immutable policy administration, session, approval, and security repositories with tenant keys, forced row-level security, and two-tenant integration evidence;
- provider-native usage normalization, explicit-cache policy enforcement, reviewed strict no-cache capability records for supported route/provider operations, fail-closed unsupported or unknown cache behavior, estimated cost, versioned rate cards, offline/authenticated cost-report ingestion, and aggregate reconciliation; and
- strict configuration parsing, deployment digest binding, connection/request/provider deadlines, health/readiness, graceful draining, reproducible package checks, OCI release-contract evidence, compatibility evidence, threat evidence, and incident regressions.

These are scoped engineering checkpoints, not a production-enterprise claim. Real IdP proof, full tenant/RBAC completion, SCIM, production policy-rollout operations, representative DLP evaluation, final provider invoice reconciliation, KMS/BYOK, immutable retention, TLS/HA/backup/restore, production operations, and independent review remain open.

## Delivery order

### P0 — Gateway boundary, policy administration, identity, and tenancy

Work these before privacy/billing expansion:

1. [#20 — Scope-correct Hormuz around gateway enforcement](https://github.com/Xpounder-com/hormuz/issues/20)
2. [#21 — Add versioned policy administration, activation, and rollback](https://github.com/Xpounder-com/hormuz/issues/21)
3. [#13 — Browser SSO and rotating Hormuz sessions](https://github.com/Xpounder-com/hormuz/issues/13)
4. [#6 — Tenant-aware RBAC and PostgreSQL migrations](https://github.com/Xpounder-com/hormuz/issues/6)
5. [#7 — SCIM provisioning, service accounts, and immediate revocation](https://github.com/Xpounder-com/hormuz/issues/7)

Exit outcome: existing clients use Hormuz without a new employee UI; administrators can stage, activate, audit, and roll back tenant-scoped policy; every supported durable operation is tenant-scoped; and the core gateway has no built-in memory dependency.

### P1 — Privacy, provider economics, and organizational reporting

1. [#10 — Structured DLP, PII policy, and egress defenses](https://github.com/Xpounder-com/hormuz/issues/10)
2. [#22 — Govern provider-native prompt caching and accounting](https://github.com/Xpounder-com/hormuz/issues/22)
3. [#8 — Provider invoice reconciliation and versioned rate cards](https://github.com/Xpounder-com/hormuz/issues/8)
4. [#15 — Organization, team, and person AI consumption reporting](https://github.com/Xpounder-com/hormuz/issues/15)

Exit outcome: administrators can see attributable and unattributed coverage, tokens, estimated versus reconciled cost, model/provider/client mix, budget burn, policy denial/reroute activity, and DLP outcomes by authorized organization/team/person scope. Consumption is not presented as employee productivity or causal work impact.

### P2 — Enterprise release gate

1. [#17 — KMS/BYOK custody and tamper-evident audit retention](https://github.com/Xpounder-com/hormuz/issues/17)
2. [#11 — Production deployment with TLS, HA, readiness, and disaster recovery](https://github.com/Xpounder-com/hormuz/issues/11)
3. [#9 — Operational and independent security release gate](https://github.com/Xpounder-com/hormuz/issues/9)

Exit outcome: a supported deployment can be installed, upgraded, operated, restored, audited, and independently reviewed against a documented security, privacy, compatibility, and support boundary.

## Archived experimental context track

The following work remains in Git history and compatibility tests as bounded experimental evidence, but it is no longer on the Hormuz delivery path:

- [#4 — built-in governed-context persistence](https://github.com/Xpounder-com/hormuz/issues/4) — closed, not planned;
- [#5 — automatic governed-context injection](https://github.com/Xpounder-com/hormuz/issues/5) — closed, not planned;
- [#12 — automatic context lifecycle](https://github.com/Xpounder-com/hormuz/issues/12) — closed, not planned;
- [#14 — reusable context-pack cache](https://github.com/Xpounder-com/hormuz/issues/14) — closed, not planned;
- [#16 — synthetic retrieval/lifecycle benchmark](https://github.com/Xpounder-com/hormuz/issues/16) — completed historical experiment; and
- [#18 — Context Pack API and MCP adapter](https://github.com/Xpounder-com/hormuz/issues/18) — completed historical experiment.

No PostgreSQL migration, lifecycle collector, semantic retrieval, provenance system, or reusable pack cache will be added to Hormuz. Existing context configuration and routes receive a compatibility/deprecation path. Removing them requires a separate versioned migration and sunset plan.

## Universal definition of done

An implementation issue may close only when all applicable evidence exists:

1. The supported contract and threat/failure boundary are documented.
2. Authorization and tenant isolation happen before data access or expensive work.
3. Success, denial, concurrency, dependency outage, migration, and rollback paths have executable tests.
4. Credentials, prompts, responses, and sensitive matched content do not leak into metadata logs, usage storage, audit output, build artifacts, or errors.
5. Source, wheel/container, supported-client/version, and migration/upgrade checks pass where relevant.
6. Reporting distinguishes provider facts from estimates and reconciled charges; coverage gaps remain visible.
7. The linked PR CI is green for the exact commit.
8. The GitHub issue links the exact commit, commands, CI result, and every open gate.

The [verification record](VERIFICATION.md) captures executed evidence. It is not a substitute for an open issue's remaining acceptance criteria.
