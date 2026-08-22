# Operational probes and graceful shutdown

Hormuz exposes two unauthenticated deployment probes. They are intentionally
small, content-free, and unsuitable as an employee or administration API. Put
them behind the deployment network boundary that your platform uses for health
checks; they do not identify a tenant, employee, policy, model, provider key,
or database location.

| Endpoint | Healthy response | What it proves | What it deliberately does not prove |
| --- | --- | --- | --- |
| `GET /health` | HTTP 200, `hormuz.gateway-health` v1 | The HTTP process can answer a request. | Storage, active policy, provider, TLS proxy, network, or credential health. |
| `GET /ready` | HTTP 200, `hormuz.gateway-readiness` v1, `status: ready` | The process is accepting traffic; the configured usage/evidence store can perform a read-only check; and managed-policy tenants have active immutable policies. | A model provider is reachable, a request will fit budget, or any cross-replica/deployment guarantee. |

`GET /ready` returns HTTP 503 with the same versioned readiness schema when
Hormuz should not receive new traffic:

- `reason: dependency_unavailable` means the local evidence or managed-policy
  read-only check failed. The response does not expose the underlying
  connection, schema, tenant, or policy failure.
- `reason: draining` means shutdown has started. The process remains live
  until the listener and in-flight work have drained.

Handling either endpoint does not authenticate a caller, resolve an upstream
credential, or call an AI provider. Configure the load balancer's liveness
action from `/health` and its traffic-readiness action from `/ready`. Do not
use either endpoint for provider availability monitoring.

## Graceful shutdown

`hormuz serve` handles `SIGTERM` by clearing readiness before it stops the
listener. It then waits for accepted gateway handlers to finish before it
closes the PostgreSQL runtime pool. That ordering preserves a handler's final
post-provider evidence write rather than turning a normal drain into an
accounting gap.

Set the deployment platform's termination grace period to accommodate the
configured `upstream_timeout_seconds` plus load-balancer propagation and
connection-drain time. A platform that force-kills the process sooner can still
interrupt an in-flight provider relay; Hormuz does not claim to make forced
termination lossless.

This probe boundary is single-process behavior only. TLS termination, trusted
proxy configuration, provider reachability, multi-instance draining,
autoscaling, failover, backup/PITR, recovery objectives, alerts, and incident
procedures remain separate production-readiness gates.
