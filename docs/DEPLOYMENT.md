# Deployment contract and customer-controlled boundaries

## V1 deployment and recovery contract

Hormuz's application contract is one signed OCI manifest digest. Docker
Compose and Kubernetes/Helm are separately supported operational profiles, not
application dependencies:

- the single-VM Compose profile is for local use, evaluation, and pilots and
  has no HA, zero-downtime, disaster-recovery, RPO, or RTO claim;
- the optional Kubernetes/Helm profile is the v1 enterprise reference and uses
  customer-operated external PostgreSQL, ingress, identity, custody, backup,
  and recovery infrastructure.

The enterprise-reference disaster-recovery rehearsal must prove an RPO of no
more than 300 seconds and an internal RTO of no more than 3,600 seconds. It must
also publish the complete end-to-end interval from failure injection through
detection, authorization, restore, validation, traffic promotion, and the first
successful governed request. These are acceptance criteria for the exact
pinned reference rehearsal, not a customer SLA.

The accepted [ADR 0009](decisions/0009-v1-deployment-profiles-and-recovery-objectives.md)
defines the clocks and ownership boundary. The strictly validated
[v1 deployment contract](deployment-contract-v1.json) inventories the
supported platforms, profile claims, component owners, durable and cached
state, recovery treatment, child gates, and nonclaims.

The operator-reviewed [Kubernetes disaster-recovery runbook](DISASTER_RECOVERY.md)
defines the backup policy, separated recovery authorities, admission checks,
negative paths, promotion boundary, rollback procedure, and exact disposable
rehearsal that supplies strict content-free evidence for issue #105.

## Customer-controlled TLS deployment boundary

Hormuz does **not** manage public TLS certificates in its enterprise reference
architecture. A customer-controlled reverse proxy, load balancer, service
mesh, or equivalent ingress terminates public TLS and reaches a private
Hormuz listener through an authenticated, network-restricted hop.

```text
Employee client
      |
      | public HTTPS; customer hostname and certificate
      v
Customer-controlled TLS ingress
      |
      | private network + source allowlist + ingress credential
      v
Hormuz private HTTP listener
      |
      v
Model provider
```

Hormuz still authenticates the employee separately with its bootstrap token or
OIDC JWT. The proxy credential proves only that the request reached Hormuz
through the configured customer ingress; it never grants an employee identity,
team, policy entitlement, or provider access.

The first runnable customer-controlled-ingress profile is the
[single-VM Docker Compose pilot](../deploy/compose/README.md). It binds Hormuz
only to host loopback and leaves the public proxy, certificate, DNS, firewall,
and outbound destination policy under customer control.

The optional [Kubernetes + Helm reference](../deploy/kubernetes/README.md)
keeps the same boundary behind an internal `ClusterIP` Service. The chart
creates no public ingress, certificate, or browser session. A customer-owned
proxy or mesh workload must be selected by standard NetworkPolicy, overwrite
the private-hop credential header, and remain the only admitted ingress source.
The first executable proof uses Cilium, but the chart contains no Cilium API.

## Gateway configuration

Local development is intentionally unchanged: `127.0.0.1`, `::1`, and
`localhost` use `ingress.mode: local` implicitly. A listener on any other host
fails configuration unless it explicitly opts into the external proxy boundary:

```json
{
  "listen": {"host": "0.0.0.0", "port": 8787},
  "ingress": {
    "mode": "external_tls_proxy",
    "trusted_proxy_cidrs": ["10.42.0.0/16", "127.0.0.1/32"],
    "credential_env": "HORMUZ_INGRESS_CREDENTIAL"
  }
}
```

`trusted_proxy_cidrs` is a bounded, canonical CIDR allowlist; a configuration
that admits every IPv4 or IPv6 address is rejected. The environment value must
be at least 16 characters, must use an environment variable not assigned to
any other Hormuz secret, is loaded before the listener starts, and is never
printed by `hormuz serve` or `hormuz doctor`.

The customer ingress must do all of the following:

1. Terminate the public HTTPS connection and own certificate/key issuance,
   renewal, and revocation.
2. Restrict the direct Hormuz listener at the network layer to the configured
   proxy or mesh ranges.
3. Remove any incoming `X-Hormuz-Ingress-Credential` header and inject exactly
   one header with the value of `HORMUZ_INGRESS_CREDENTIAL` on the private hop.
4. Send both `/health` and `/ready` checks through that same authenticated
   private hop. `/health` is process liveness; `/ready` is traffic readiness.

When external proxy mode is enabled, Hormuz checks the direct TCP peer address
against the allowlist and verifies the dedicated
`X-Hormuz-Ingress-Credential` header with a constant-time comparison before
any route dispatch. A missing, duplicated, wrong, or untrusted request gets a
content-free HTTP 401 `hormuz.gateway-error` v3 response with the existing
`unauthorized` code. It does not reach employee authentication, policy,
provider egress, usage storage, or audit storage.

Hormuz does not trust `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Proto`, or
`X-Real-IP` for identity, tenant binding, policy, evidence, or provider
egress. They are not relayed upstream. The proxy must still handle client IP,
hostname, redirect, and edge logging decisions itself.

## Configuration migration

An existing loopback configuration needs no change. An existing non-loopback
listener must add the `ingress` object above, generate and inject a distinct
ingress credential, restrict the backend network path, configure the proxy to
overwrite the header, and validate the replacement configuration before
draining the old process:

```bash
hormuz --config hormuz.json doctor
```

There is no live configuration reload or dual-ingress-credential window in
this release line. Rotate by deploying a replacement gateway configuration and
customer proxy/backend mapping through the customer's controlled rollout
procedure. Do not place the credential in source control, client settings, or
provider configuration.

## Custody restriction coordination

When `custody_lifecycle` is enabled, each gateway opens its normal bounded
PostgreSQL runtime pool plus one restricted `LISTEN` connection for custody
invalidation notifications. A frequent durable coordination scan is the
fallback and renews a fixed five-second replica lease. Size database connection
limits for that additional session per gateway instance.

Do not route traffic to a process until `/ready` succeeds. Startup first loads
the active projection and any prepared barriers, installs those barriers
locally, and acknowledges them. Loss of the listener alone can fall back to
the durable scan; failure to synchronize makes `/ready` unhealthy immediately
and leaves admission fail closed. During shutdown, Hormuz stops advertising
readiness, drains active handlers, retires its replica lease, and only then
closes the runtime pool.

The disposable Kubernetes coordinated-operation proof exercises this boundary
through the private ClusterIP Service. A normally terminated replica must
withdraw readiness and disappear from Service endpoints while its already
pinned request finishes. A force-killed replica may interrupt its pinned
request; Hormuz preserves that attempt as `outcome_unknown`, retains the
uncertain budget reservation, and never replays provider work. A client retry
is a new attempt because v1 defines no client idempotency key. The retained
evidence publishes observed timing and exact nonclaims; it is not a universal
zero-downtime or provider exactly-once promise.

## Deliberate limits

This is a gateway-side ingress boundary, not proof of a customer deployment.
It does not implement public TLS, certificate custody or rotation, mTLS,
certificate authority integration, a proxy/WAF/firewall, DNS, private
networking, HA/failover, production secret rotation, zero-downtime deployment,
or a cloud-specific reference architecture. Those remain separate release
gates under [ROADMAP.md](ROADMAP.md).

The disposable Kubernetes proof additionally demonstrates the private
authenticated hop and standard default-deny policy between synthetic Pods. It
does not validate a customer's public TLS, ingress controller, service mesh,
certificate operations, cluster network, CNI portability, or browser login.
