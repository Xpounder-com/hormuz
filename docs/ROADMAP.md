# Hormuz core hardening roadmap

Hormuz is a model-neutral enterprise AI gateway and policy control plane for employees' existing Codex and Claude Code workflows. Its core job is to authenticate a request, apply organization policy, route or deny it, protect secrets before provider egress, and retain metadata-only evidence of governed usage.

This roadmap is evidence-gated. A milestone is not complete because code exists or a narrow test passes. Every closure needs an explicit scope, executable checks, package or deployment proof where relevant, and a truthful statement of what remains unproven.

## Current implementation order

### 1. Separate the deprecated context experiment

Completed release gate: [#23](https://github.com/Xpounder-com/hormuz/issues/23), merged in [PR #24](https://github.com/Xpounder-com/hormuz/pull/24).

Move the former context-pack kernel out of the primary `hormuz` distribution and runtime into a separately buildable experiment in this repository. The core wheel must have no context retrieval, lifecycle, cache, provenance, memory, content-storage, active route, or active CLI implementation. Its only temporary compatibility behavior is a content-free `context_experiment_moved` error for the former CLI command.

Exit evidence:

- clean-wheel and source-distribution inspection;
- a gateway-start test proving no context module import or storage initialization;
- a route test proving `/v1/context/...` is not active;
- a separately buildable experimental package and migration documentation.

### 2. Stabilize the policy and evidence contract

Completed release gate: [#25](https://github.com/Xpounder-com/hormuz/issues/25), merged in [PR #27](https://github.com/Xpounder-com/hormuz/pull/27). After the package boundary is merged, freeze what the core gateway promises before adding operational integrations.

The contract includes authenticated identity and event-time organization/team/person binding; requested, routed, and provider-reported models; policy version and enforcement outcome; allow/deny/reroute/cap/redact/rate-limit meanings; token and cost bases; allocation and coverage labels; metadata-only audit fields; stable public error codes; and SQLite/PostgreSQL parity.

Every public response and durable evidence format has an explicit schema version, strict validation, compatibility fixtures, and documented migration rules. New fields are not casual additions after this point. [#26](https://github.com/Xpounder-com/hormuz/issues/26) is completed in [PR #28](https://github.com/Xpounder-com/hormuz/pull/28), proving upgrades, rollback, SQLite/PostgreSQL parity, and failure paths. [#21](https://github.com/Xpounder-com/hormuz/issues/21) is completed in [PR #29](https://github.com/Xpounder-com/hormuz/pull/29), adding focused versioned policy administration: one-time audited bootstrap, PostgreSQL-backed root authority, immutable policy documents, atomic activation/rollback, request-pinned versions, and break-glass recovery.

### 3. Close production-readiness gates

Only after the core contract is stable, the program proceeds through real operational gates:

1. validate generic OIDC resource-server behavior against an external identity provider, without claiming browser SSO or a Hormuz session broker ([#13](https://github.com/Xpounder-com/hormuz/issues/13));
2. add KMS/BYOK custody, secret rotation, and tamper-evident audit retention ([#17](https://github.com/Xpounder-com/hormuz/issues/17));
3. prove TLS deployment, shared PostgreSQL operation, pooling, backup/restore/PITR, multi-instance revocation and coordination, health/readiness/SLOs, alerts, and incident procedures ([#11](https://github.com/Xpounder-com/hormuz/issues/11));
4. prove container signing, SBOM and vulnerability gates, and independent release review ([#9](https://github.com/Xpounder-com/hormuz/issues/9)).

PR [#30](https://github.com/Xpounder-com/hormuz/pull/30) completes the
bounded external generic-OIDC resource-server reference checkpoint for #13:
live Okta discovery/JWKS, a public native authorization-code PKCE S256 proof
with `form_post`, explicit subject mapping through Hormuz's real `whoami`
route, and tampered-token denial. The generic resource-server contract remains
provider-neutral. #13 remains open for the deliberately separate browser SSO,
Hormuz session broker, refresh-token custody, session rotation, secure-store,
and lifecycle/revocation requirements.

PR [#31](https://github.com/Xpounder-com/hormuz/pull/31) supplies the generic
custody contract and AWS KMS/S3 Object Lock reference implementation for #17,
including an opt-in live conformance gate. #17 remains open until that gate is
run in a customer-controlled AWS environment and the broader secret migration,
immutable-history, retention operations, recovery, SIEM, and independent-review
evidence is complete.

The first separately verifiable #11 slice, [#32](https://github.com/Xpounder-com/hormuz/issues/32), is completed in [PR #33](https://github.com/Xpounder-com/hormuz/pull/33): bounded PostgreSQL runtime pooling with tenant-safe checkout reuse. The second, [#34](https://github.com/Xpounder-com/hormuz/issues/34), is completed in [PR #35](https://github.com/Xpounder-com/hormuz/pull/35): a versioned liveness/readiness contract that checks only Hormuz's local policy/evidence dependencies and graceful-drain state. The third, [#36](https://github.com/Xpounder-com/hormuz/issues/36), is completed in [PR #37](https://github.com/Xpounder-com/hormuz/pull/37): bounded, unambiguous, schema-strict configuration parsing before secret or dependency initialization. The fourth, [#38](https://github.com/Xpounder-com/hormuz/issues/38), is completed in [PR #39](https://github.com/Xpounder-com/hormuz/pull/39): a non-root OCI reference runtime with only mounted runtime configuration and data. The fifth, [#40](https://github.com/Xpounder-com/hormuz/issues/40), is completed in [PR #41](https://github.com/Xpounder-com/hormuz/pull/41): candidate SBOM evidence and a fix-aware OCI vulnerability gate. The sixth, [#42](https://github.com/Xpounder-com/hormuz/issues/42), is completed in [PR #43](https://github.com/Xpounder-com/hormuz/pull/43): a disposable logical PostgreSQL backup-and-restore drill. The seventh, [#44](https://github.com/Xpounder-com/hormuz/issues/44), is completed in [PR #45](https://github.com/Xpounder-com/hormuz/pull/45): a customer-controlled TLS reference with an authenticated, network-restricted gateway proxy hop. The eighth, [#46](https://github.com/Xpounder-com/hormuz/issues/46), is completed in [PR #47](https://github.com/Xpounder-com/hormuz/pull/47): two independent gateway instances with separate bounded PostgreSQL pools preserve one organization budget reservation, stable pre-egress denial, shared metadata-only evidence, and tenant isolation. Those slices remain intentionally narrower than customer TLS/certificate operations, HA/failover, production backup/PITR, multi-region coordination, replicated sessions/revocation/approval grants/idempotency, and operational recovery evidence.

The ninth, [#48](https://github.com/Xpounder-com/hormuz/issues/48), is completed in [PR #49](https://github.com/Xpounder-com/hormuz/pull/49): an audited immutable policy activation is observed at request start by the other live gateway instance, causes a pre-egress denial, and can reactivate the previous immutable policy without rewriting earlier evidence.

The tenth, [#50](https://github.com/Xpounder-com/hormuz/issues/50), is completed in [PR #51](https://github.com/Xpounder-com/hormuz/pull/51): a gateway whose own bounded PostgreSQL runtime pool is closed becomes unready and fails generation requests closed before provider egress, while an independent sibling continues serving and a newly constructed replacement receives a fresh pool and recovers. This deterministic process-level proof does not claim PostgreSQL database failover, HA, or zero-downtime replacement.

The eleventh, [#52](https://github.com/Xpounder-com/hormuz/issues/52), is completed in [PR #53](https://github.com/Xpounder-com/hormuz/pull/53): terminating one live replica's idle PostgreSQL backend connection causes its bounded pool to replace that connection before the next readiness check and governed request, while an independent sibling remains usable. This bounded connection-churn proof does not claim a PostgreSQL database outage, automatic failover, HA, credential rotation, or zero-downtime deployment.

## Feature-freeze rule

> A change is current-priority only if it removes deprecated context coupling, stabilizes the policy/evidence contract, fixes a security or correctness defect, or closes a production-readiness gate.

Work is organized as a small PR for one issue and one verifiable outcome. Contract and package evidence are required before merge. Schema compatibility, migration, rollback, recovery, and failure behavior are implementation work, not deferred operational cleanup.

## Deferred during hardening

The existing local allocation engine and CLI remain part of gateway economics, but receive only correctness, compatibility, or security fixes during this phase. Do not expand:

- remote cost-allocation APIs, allocation roles, response variants, or reporting dimensions;
- new DLP detector families unless they close a demonstrated bypass;
- ticketing, productivity, quality, or workflow integrations;
- new context, memory, lifecycle, cache, retrieval, provenance, or content-governance capabilities.

The separately packaged context experiment is outside the core release surface. It does not make Hormuz an organizational-memory system; the AI Metadata Compiler remains the separate product for enterprise asset ingestion, normalization, claims, provenance, and freshness.

## Definition of done

An issue closes only when its contract and threat/failure boundary are documented; authorization happens before data access or expensive work; success, denial, concurrency, dependency outage, migration, rollback, and recovery paths are tested where applicable; content and credentials are absent from logs, storage, exports, errors, and build artifacts; and the linked GitHub evidence records the exact command/test/package result.
