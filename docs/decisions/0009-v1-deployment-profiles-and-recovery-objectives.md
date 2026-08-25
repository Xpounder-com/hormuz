# ADR 0009: v1 deployment profiles and reference recovery objectives

- **Status:** Accepted
- **Decision date:** August 25, 2026
- **Decision record:** [issue #100 comment](https://github.com/Xpounder-com/hormuz/issues/100#issuecomment-5414302641)
- **Implementation gate:** [issue #100](https://github.com/Xpounder-com/hormuz/issues/100)
- **Machine-readable contract:** [deployment-contract-v1.json](../deployment-contract-v1.json)

## Context

Hormuz needs one portable application artifact and two deliberately different
deployment profiles. A single-VM pilot must stay simple without inheriting an
enterprise high-availability claim. The optional Kubernetes profile must be
able to prove multi-replica, database-failover, disaster-recovery, and rolling
operation behavior without making Kubernetes part of the application itself.

The recovery gate also needs targets that cannot hide operator and coordination
delay. A narrow restore timer is useful for diagnosing Hormuz's internal
procedure, but customers also need the complete elapsed time from failure to a
successfully governed request. Neither measurement can be represented as a
universal promise for infrastructure that Hormuz does not operate.

## Decision

### Application and profile hierarchy

```text
Hormuz application contract
└── signed OCI manifest digest

Verified deployment profiles
├── Compose single VM: local use, evaluation, and pilots
└── Kubernetes + Helm: optional v1 enterprise reference
```

The signed OCI digest is the application contract. A registry, Compose,
Kubernetes, Helm, Kind, Cilium, a PostgreSQL distribution, an ingress, an IdP,
and a custody backend are deployment or customer-infrastructure choices, not
Hormuz application dependencies. The initial supported deployment platform is
`linux/amd64`. General `linux/arm64` support remains gated by issue #109.

Browser-session brokerage, cookies, refresh-token custody, and opaque-token
introspection are excluded from v1. Bootstrap credentials and generic OIDC JWT
bearer-resource-server authentication remain supported.

Hormuz never automatically replays provider work. A client retry is a new
attempt in v1; there is no cross-provider client-idempotency-key promise. The
durable attempt ledger preserves ambiguous outcomes and their uncertain budget
reservation instead of inferring success or retrying work.

### Compose single-VM profile

```text
employee client
      |
      | customer HTTPS
      v
customer TLS ingress on or beside one Linux AMD64 VM
      |
      | authenticated, network-restricted private hop
      v
one Hormuz gateway ────────> customer-approved IdP/provider/custody egress
      |
      | private Compose network
      v
one persistent PostgreSQL service
      |
      └── or a customer-operated external PostgreSQL DSN
```

This profile is for local use, evaluation, and pilots. Replacement may require
a measured interruption. It has no HA, failure-domain, zero-downtime, or v1
enterprise recovery claim.

### Kubernetes enterprise-reference profile

```text
employee client
      |
      | customer HTTPS
      v
customer ingress / service mesh
      |
      | authenticated, source-restricted private hop
      v
internal ClusterIP Service
      |
      +───────────────+
      v               v
Hormuz replica A   Hormuz replica B   ...
      |               |
      +───────+───────+
              |
              | restricted runtime role through customer HA endpoint
              v
      customer-operated PostgreSQL

IdP, provider egress, immutable configuration and secrets, custody services,
backups, Object Lock, cluster operation, and traffic promotion remain under
customer-controlled infrastructure and authorization.
```

The chart deploys only Hormuz through standard Kubernetes APIs. The enterprise
reference consumes customer-operated PostgreSQL and customer-created immutable
configuration and Secret generations. The v1 reference places at least two
gateway replicas on two distinct Kubernetes nodes using hostname topology; it
does not claim availability-zone or regional failure tolerance. Profile
packaging is already proven by issue #108. Issues #103 through #107 remain the executable multi-replica,
database HA, recovery, tenant-lifecycle, and release-transition gates; no
unproven claim is created merely by accepting this ADR.

### Component ownership

Hormuz owns the signed application artifact, application configuration schema,
private-hop authentication behavior, policy enforcement, metadata-only
evidence contracts, migrations, probes, and reference verification tooling.
Customers own public TLS and ingress, private networking, IdP operation,
provider accounts and egress policy, production PostgreSQL, runtime secret
delivery, optional custody infrastructure, backup retention, recovery
authorization, traffic promotion, and the underlying deployment platform.

The exact component assignments and every launch-supported durable or cached
state class are frozen in `docs/deployment-contract-v1.json`. PostgreSQL is the
authoritative Hormuz durable store in the enterprise reference. Derived runtime
projections and process caches never become editable authority. Customer-owned
configuration, secret, IdP, provider, custody, and immutable-anchor state stays
outside Hormuz's database and must be supplied or verified by the customer
recovery procedure.

### Recovery objectives and clocks

The v1 Kubernetes enterprise-reference rehearsal must meet:

- **RPO:** no more than **300 seconds**. At the injected failure timestamp, the
  maximum gap between that timestamp and the latest recovered committed marker
  across every recovery-covered Hormuz durable state class must be at most five
  minutes.
- **Internal RTO:** no more than **3,600 seconds**. This clock starts when an
  authorized operator begins the approved recovery execution with required
  recovery inputs available. It stops only when the isolated recovered
  environment passes every admission, state-integrity, tenant-isolation,
  custody, and audit-continuity check and is ready for controlled promotion.
- **Complete end-to-end recovery time:** always measured and published. This
  clock starts at failure injection and stops after detection, declaration,
  authorization, restore, validation, traffic promotion, and the first
  successful governed request through the recovered environment.

Evidence must record ordered timestamps for failure injection, detection,
declaration, recovery authorization/execution start, restore start, recovered
environment readiness, promotion, and the first successful governed request.
Publishing both clocks prevents detection, approval, and promotion delays from
being hidden outside the internal RTO.

These values are acceptance criteria for the exact pinned reference rehearsal.
They are **not a customer SLA**, do not guarantee any customer's achieved
recovery, and do not apply to the Compose profile. A customer can adopt stricter
or looser objectives only through its own architecture, operations, and
contract.

## Consequences

- Issue #100 can close once this contract, its strict validator, and merged
  evidence are green.
- Issues #103 and #104 can choose pinned disposable coordination and PostgreSQL
  HA implementations without turning either implementation into the product
  contract.
- Issue #105 has objective pass/fail thresholds and cannot report only a
  restore subprocess duration.
- Issues #106 and #107 must preserve the state ownership and profile boundaries
  in the machine-readable inventory.
- Changing a target, state owner, supported platform, or profile claim requires
  a new versioned contract and an explicit owner decision.

## Nonclaims

This ADR does not itself prove multi-replica correctness, PostgreSQL promotion,
split-brain prevention, backup durability, achieved RPO/RTO, tenant deletion,
release upgrade/rollback, customer operations, or an independent security
review. It does not certify Kubernetes, Helm, Cilium, PostgreSQL, a cloud,
customer ingress, an IdP, a model provider, or a custody backend.
