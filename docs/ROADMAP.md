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

Every public response and durable evidence format has an explicit schema version, strict validation, compatibility fixtures, and documented migration rules. New fields are not casual additions after this point. The current [#26](https://github.com/Xpounder-com/hormuz/issues/26) gate proves upgrades, rollback, SQLite/PostgreSQL parity, and failure paths. The active [#21](https://github.com/Xpounder-com/hormuz/issues/21) milestone adds focused versioned policy administration: one-time audited bootstrap, PostgreSQL-backed root authority, immutable policy documents, atomic activation/rollback, request-pinned versions, and break-glass recovery. It remains open until its small PR merges with the documented package and test evidence; the former implementation lived only in an unmerged integration branch and is not treated as merged-core evidence.

### 3. Close production-readiness gates

Only after the core contract is stable, the program proceeds through real operational gates:

1. validate generic OIDC against an external identity provider while keeping Hormuz a resource server, not an IdP ([#13](https://github.com/Xpounder-com/hormuz/issues/13));
2. add KMS/BYOK custody, secret rotation, and tamper-evident audit retention ([#17](https://github.com/Xpounder-com/hormuz/issues/17));
3. prove TLS deployment, shared PostgreSQL operation, pooling, backup/restore/PITR, multi-instance revocation and coordination, health/readiness/SLOs, alerts, and incident procedures ([#11](https://github.com/Xpounder-com/hormuz/issues/11));
4. prove container signing, SBOM and vulnerability gates, and independent release review ([#9](https://github.com/Xpounder-com/hormuz/issues/9)).

PR [#31](https://github.com/Xpounder-com/hormuz/pull/31) supplies the generic
custody contract and AWS KMS/S3 Object Lock reference implementation for #17,
including an opt-in live conformance gate. #17 remains open until that gate is
run in a customer-controlled AWS environment and the broader secret migration,
immutable-history, retention operations, recovery, SIEM, and independent-review
evidence is complete.

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
