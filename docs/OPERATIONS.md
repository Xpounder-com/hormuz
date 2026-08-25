# Operational probes and graceful shutdown

Hormuz exposes two deployment probes. They are intentionally small,
content-free, and unsuitable as an employee or administration API. In local
mode they are unauthenticated. In external TLS proxy mode, the customer proxy
must supply the configured private-hop ingress credential before either probe
is dispatched; the probes still do not identify a tenant, employee, policy,
model, provider key, or database location. See [DEPLOYMENT.md](DEPLOYMENT.md)
for the customer-controlled TLS boundary.

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

When `audit_chain.maximum_anchor_age_seconds` is configured, the durable-store
check also fails closed if a tenant has committed metadata-only audit events
older than that bound without a successful local external-checkpoint receipt.
This is an anchor-age alert, not an Object Lock availability probe: `/ready`
never contacts S3, AWS, Ceph, or another custody service. Use a separate
scheduled `hormuz audit-chain anchor` job and monitor its result. An idle
tenant with no chain entries is not overdue.

Handling either endpoint does not authenticate a caller, resolve an upstream
credential, or call an AI provider. Configure the load balancer's liveness
action from `/health` and its traffic-readiness action from `/ready`. Do not
use either endpoint for provider availability monitoring.

## Configuration input

Hormuz treats its JSON configuration as a deployment control input, not a
permissive application preference file. Before it resolves an
environment-backed identity or secret, opens storage, contacts an IdP, resolves
an upstream credential, or binds a listener, it accepts only one bounded UTF-8
JSON object with the documented fields.

- The file is limited to 1 MiB, 64 structural levels, and 100,000 JSON nodes.
- Duplicate object members, `NaN`/`Infinity`, invalid UTF-8, malformed JSON,
  invalid object/array shape, and unsupported fields fail closed.
- Raw parser and schema failures use fixed, content-free error codes:
  `configuration_unavailable`, `configuration_too_large`,
  `configuration_invalid_encoding`, `configuration_invalid_json`,
  `configuration_duplicate_member`, `configuration_nonfinite_number`,
  `configuration_structure_limit`, `configuration_schema_invalid`, and
  `configuration_unsupported_fields`.

Configuration objects are strict: Hormuz never silently ignores a misspelled
or forward-version field. Treat adding a configuration field as a documented
deployment compatibility change. Validate a candidate in a replacement process
with `hormuz doctor` before draining and replacing a running gateway. Hormuz
does not claim signed configuration, live reload, a secret manager, or a
deployment change-approval workflow in this release line.

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

This probe boundary is single-process behavior only. The gateway-side trusted
proxy contract does not prove customer TLS termination, certificate operations,
proxy/firewall configuration, provider reachability, multi-instance draining,
autoscaling, failover, backup/PITR, recovery objectives, alerts, or incident
procedures. Those remain separate production-readiness gates.

For the narrowly scoped non-root container reference, see [OCI.md](OCI.md),
and for the one-replica pilot lifecycle see the
[single-VM Compose profile](../deploy/compose/README.md).
The image health check uses `/health`; the deployment's traffic control must
still use `/ready` and an adequate termination grace period.

The optional [Kubernetes + Helm profile](../deploy/kubernetes/README.md) maps
these same process contracts to exec probes that obtain the private-hop value
only from the referenced Secret. Its rolling strategy waits for `/ready`, sets
zero unavailable replicas, retains a disruption budget, and gives SIGTERM 660
seconds before a platform force-kill. The disposable two-replica proof observes
one Pod deletion and replacement; this is not an HA, zero-interruption,
in-flight-request, autoscaling, RPO, RTO, or zone-failure guarantee.
