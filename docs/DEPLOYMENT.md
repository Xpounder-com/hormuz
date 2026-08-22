# Customer-controlled TLS deployment boundary

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
content-free HTTP 401 `hormuz.gateway-error` v2 response with the existing
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

## Deliberate limits

This is a gateway-side ingress boundary, not proof of a customer deployment.
It does not implement public TLS, certificate custody or rotation, mTLS,
certificate authority integration, a proxy/WAF/firewall, DNS, private
networking, HA/failover, production secret rotation, zero-downtime deployment,
or a cloud-specific reference architecture. Those remain separate release
gates under [ROADMAP.md](ROADMAP.md).
