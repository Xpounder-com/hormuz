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
including an opt-in live conformance gate. The AWS adapter is available but not
yet live-certified; [#94](https://github.com/Xpounder-com/hormuz/issues/94)
tracks that customer-authorized cloud proof separately. It does not block the
vendor-neutral core #17 custody gate.

PR [#59](https://github.com/Xpounder-com/hormuz/pull/59) adds the account-free
OpenBao Transit plus S3-compatible Object Lock profile for
[#58](https://github.com/Xpounder-com/hormuz/issues/58). Ceph RGW Tentacle
20.2.3 is the first optional self-hosted target, but the product contract
remains vendor-neutral. The implementation includes a strict, opt-in local
conformance runner and a content-free evidence schema; the live single-host
Cephadm proof is recorded in [#60](https://github.com/Xpounder-com/hormuz/issues/60).
Ceph RGW Tentacle 20.2.3 is Hormuz's first verified self-hosted reference for
RGW-level enforcement only, never host-root or production-immutability
protection. Native ARM64 conformance for that exact Ceph target remains in
[#68](https://github.com/Xpounder-com/hormuz/issues/68). General native ARM64
Hormuz OCI runtime verification is separately tracked in
[#109](https://github.com/Xpounder-com/hormuz/issues/109) and blocks any future
multi-architecture Hormuz image, not this Ceph reference.

[#95](https://github.com/Xpounder-com/hormuz/issues/95) binds that reference
to the exact OpenBao Transit image, version, and platform. Its live,
content-free schema-v3 custody/retention record and schema-v2
rotation/recovery record verify the exact OpenBao + Ceph pairing, without
changing the vendor-neutral product contract or claiming unrestricted
production certification.

The #17 checkpoint [#69](https://github.com/Xpounder-com/hormuz/issues/69) is
completed in [PR #70](https://github.com/Xpounder-com/hormuz/pull/70): a
separately credentialed OpenBao Transit key-version rotation and fresh
artifact-recovery proof for that same self-hosted lab. The final live run proved
that a normal data-plane token cannot rotate either named key, while the
rotation-only administrator has no data-key authority; fresh clients recovered
a pre-rotation provider envelope and exact retained metadata-only audit artifact
after same-named key-version rotation. Its content-free evidence is published
with the PR. It does not claim OpenBao backend recovery, customer RPO/RTO,
production KMS/BYOK, HA/DR, or host-root protection. The parent #17 gate remains
open after this checkpoint.

The #17 checkpoint [#72](https://github.com/Xpounder-com/hormuz/issues/72) is
completed in [PR #73](https://github.com/Xpounder-com/hormuz/pull/73): one
versioned, metadata-only commit-time chain per organization, atomic
event/entry/head commits, restricted runtime mutation of historical entries,
explicit recovery/migration epochs, and asynchronous Object Lock checkpoints.
Its evidence includes SQLite/PostgreSQL parity, concurrent-writer and
tamper/recovery tests, checkpoint-age readiness, full CI, a clean-wheel
boundary check, and a bounded self-hosted Ceph RGW `COMPLIANCE` checkpoint and
exact-recovery proof. It does not claim gateway-bypass detection, protection
for events in the post-anchor window, cloud certification, host-root
protection, or production-retention readiness. The parent #17 gate remains
open for its broader custody and operational criteria.

The #17 checkpoint [#89](https://github.com/Xpounder-com/hormuz/issues/89) is
implemented in [PR #90](https://github.com/Xpounder-com/hormuz/pull/90):
tenant-scoped PostgreSQL-backed `custody_admin` authority, a dedicated
least-privileged custody-control role, fixed content-free lifecycle intents,
one approval for routine work, and two distinct active administrators for
destructive work. Managed mode blocks the legacy direct CLI execution path;
the customer KMS remains authoritative. Its evidence covers strict v1 status
and event contracts, migration and forced-RLS isolation, role separation,
expiry, replay denial, last-administrator protection, append-only history,
rollback on invalid evidence, full PostgreSQL recovery drills, and a clean
wheel boundary. It does not implement the separately permissioned machine
executor, all-administrator-loss break glass, customer IAM/KMS changes, cloud
certification, or production readiness. The parent #17 gate remains open.

The revised core #17 custody closure requires one proven customer-controlled
backend, not live certification of every future cloud adapter. [#95](https://github.com/Xpounder-com/hormuz/issues/95)
now supplies the exact OpenBao + Ceph self-hosted-reference evidence. The
remaining sequence proceeds to [#91](https://github.com/Xpounder-com/hormuz/issues/91)
for the isolated routine executor, [#92](https://github.com/Xpounder-com/hormuz/issues/92)
for destructive lifecycle execution, and
[#93](https://github.com/Xpounder-com/hormuz/issues/93) for retention/export,
custody-event anchoring, and integrated recovery evidence. The reference does
not claim unrestricted production certification. AWS certification remains an
independent, customer-authorized gate in #94.

The first separately verifiable #11 slice, [#32](https://github.com/Xpounder-com/hormuz/issues/32), is completed in [PR #33](https://github.com/Xpounder-com/hormuz/pull/33): bounded PostgreSQL runtime pooling with tenant-safe checkout reuse. The second, [#34](https://github.com/Xpounder-com/hormuz/issues/34), is completed in [PR #35](https://github.com/Xpounder-com/hormuz/pull/35): a versioned liveness/readiness contract that checks only Hormuz's local policy/evidence dependencies and graceful-drain state. The third, [#36](https://github.com/Xpounder-com/hormuz/issues/36), is completed in [PR #37](https://github.com/Xpounder-com/hormuz/pull/37): bounded, unambiguous, schema-strict configuration parsing before secret or dependency initialization. The fourth, [#38](https://github.com/Xpounder-com/hormuz/issues/38), is completed in [PR #39](https://github.com/Xpounder-com/hormuz/pull/39): a non-root OCI reference runtime with only mounted runtime configuration and data. The fifth, [#40](https://github.com/Xpounder-com/hormuz/issues/40), is completed in [PR #41](https://github.com/Xpounder-com/hormuz/pull/41): candidate SBOM evidence and a fix-aware OCI vulnerability gate. The sixth, [#42](https://github.com/Xpounder-com/hormuz/issues/42), is completed in [PR #43](https://github.com/Xpounder-com/hormuz/pull/43): a disposable logical PostgreSQL backup-and-restore drill. The seventh, [#44](https://github.com/Xpounder-com/hormuz/issues/44), is completed in [PR #45](https://github.com/Xpounder-com/hormuz/pull/45): a customer-controlled TLS reference with an authenticated, network-restricted gateway proxy hop. The eighth, [#46](https://github.com/Xpounder-com/hormuz/issues/46), is completed in [PR #47](https://github.com/Xpounder-com/hormuz/pull/47): two independent gateway instances with separate bounded PostgreSQL pools preserve one organization budget reservation, stable pre-egress denial, shared metadata-only evidence, and tenant isolation. Those slices remain intentionally narrower than customer TLS/certificate operations, HA/failover, production backup/PITR, multi-region coordination, replicated sessions/revocation/approval grants/idempotency, and operational recovery evidence.

The ninth, [#48](https://github.com/Xpounder-com/hormuz/issues/48), is completed in [PR #49](https://github.com/Xpounder-com/hormuz/pull/49): an audited immutable policy activation is observed at request start by the other live gateway instance, causes a pre-egress denial, and can reactivate the previous immutable policy without rewriting earlier evidence.

The tenth, [#50](https://github.com/Xpounder-com/hormuz/issues/50), is completed in [PR #51](https://github.com/Xpounder-com/hormuz/pull/51): a gateway whose own bounded PostgreSQL runtime pool is closed becomes unready and fails generation requests closed before provider egress, while an independent sibling continues serving and a newly constructed replacement receives a fresh pool and recovers. This deterministic process-level proof does not claim PostgreSQL database failover, HA, or zero-downtime replacement.

The eleventh, [#52](https://github.com/Xpounder-com/hormuz/issues/52), is completed in [PR #53](https://github.com/Xpounder-com/hormuz/pull/53): terminating one live replica's idle PostgreSQL backend connection causes its bounded pool to replace that connection before the next readiness check and governed request, while an independent sibling remains usable. This bounded connection-churn proof does not claim a PostgreSQL database outage, automatic failover, HA, credential rotation, or zero-downtime deployment.

The twelfth, [#61](https://github.com/Xpounder-com/hormuz/issues/61), is completed in [PR #62](https://github.com/Xpounder-com/hormuz/pull/62): an operator-controlled rolling PostgreSQL runtime-login rotation starts a replacement gateway with a distinct `NOINHERIT` login that can assume the stable restricted runtime role, requires readiness before traffic movement, drains and closes the old pool, then verifies that the disabled old login fails closed while the replacement retains policy, evidence, and tenant-RLS behavior. It does not claim automatic DSN reload, a secret-manager integration, customer load-balancer coordination, database failover, or HA.

The thirteenth, [#63](https://github.com/Xpounder-com/hormuz/issues/63), is completed in [PR #64](https://github.com/Xpounder-com/hormuz/pull/64): one live gateway instance withdraws readiness and denies new governed requests before provider egress during an abrupt disposable PostgreSQL stop, then recovers through its existing bounded pool after the same database restarts. It does not claim database failover, HA, automatic promotion, production RPO/RTO, or provider replay.

The fourteenth, [#66](https://github.com/Xpounder-com/hormuz/issues/66), is completed in [PR #67](https://github.com/Xpounder-com/hormuz/pull/67): a digest-pinned, disposable physical PostgreSQL WAL/PITR drill recovers exactly to a named restore point, proves the post-target marker is excluded, verifies Hormuz's restricted metadata state and RLS boundary, and fails closed for an unreachable target or missing archived WAL. It does not claim production backup retention, customer RPO/RTO, HA/failover, managed-database operations, or DR certification.

The approved deployment hierarchy in [#100](https://github.com/Xpounder-com/hormuz/issues/100)
keeps the signed OCI digest as the application contract and treats deployment
profiles as separately verified operational references. [#101](https://github.com/Xpounder-com/hormuz/issues/101)
is complete with the signed, anonymously pullable `v0.1.1` Linux AMD64 digest.
The first deployment profile, [#102](https://github.com/Xpounder-com/hormuz/issues/102),
is implemented in [PR #137](https://github.com/Xpounder-com/hormuz/pull/137):
one hardened gateway and one private persistent PostgreSQL service on a single
Linux AMD64 VM, plus a customer-operated external-PostgreSQL path and a clean
native-VM proof. It is for local use, evaluation, and pilots only; it does not
claim HA, failure-domain isolation, zero-downtime upgrades, production PITR/DR,
or certification of customer infrastructure or operations. The optional
Kubernetes/Helm multi-replica enterprise profile remains a separate #100/#11
gate.

That optional gate,
[#108](https://github.com/Xpounder-com/hormuz/issues/108), is bounded to a
vendor-neutral Helm chart plus an account-free disposable Kind/Cilium proof.
The chart uses only standard Kubernetes APIs, deploys Hormuz behind an
internal ClusterIP, and consumes customer-operated PostgreSQL through an
existing Secret. Its first proof covers two-replica application placement,
readiness-gated replacement, network denial, synthetic request/evidence
persistence, Pod replacement, and clean removal. Cilium is the first tested
CNI rather than a product dependency. HA, RPO, RTO, zone failure, broad CNI
portability, and deeper coordinated state/admission claims remain with their
dedicated #103/#104/#105 gates.

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
