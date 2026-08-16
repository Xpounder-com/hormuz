# Hormuz operations contract

This document defines the current process-level health and shutdown contract. It is a bounded local/runtime foundation, not evidence of a production deployment, high availability, disaster recovery, or dependency-wide health.

## Health endpoints

Health endpoints are intentionally unauthenticated so an orchestrator can call them without an employee or provider credential. They return `Cache-Control: no-store`, use the fixed schema `hormuz.health.v1`, and never reflect a request query, header, body, credential, prompt, response, filename, or internal exception.

| Endpoint | Ready state | Draining state | Meaning |
| --- | --- | --- | --- |
| `GET /health/live` | `200`, `status=live` | `200`, `status=live` | The Hormuz HTTP process can serve the probe. |
| `GET /health/ready` | `200`, `status=ready` | `503`, `status=draining`, `Retry-After: 1` | The process is or is not admitting new application work. |
| `GET /health` | `200`, `status=ok` | `503`, `status=draining`, `Retry-After: 1` | Compatibility endpoint with protocol and enabled-feature metadata; it follows readiness. |

These are shallow process probes. They do not query an upstream model provider, OIDC issuer, SQLite database, external KMS, or any future shared service. A provider or identity outage therefore does not turn liveness into a process restart loop. Dependency monitoring and a supported multi-node readiness policy remain open production-design work.

Example:

```bash
curl --fail-with-body http://127.0.0.1:8787/health/live
curl --fail-with-body http://127.0.0.1:8787/health/ready
```

## Graceful shutdown

`SIGTERM` starts one idempotent shutdown sequence:

1. Hormuz atomically withdraws readiness.
2. A syntactically valid HTTP request that was admitted before that transition may finish. Later implemented application requests receive a fixed content-free `503 gateway_draining` response, `Retry-After: 1`, and connection closure before authentication, body processing, policy evaluation, storage, or provider work.
3. The accept loop is stopped from a helper thread. This avoids the Python server deadlock caused by calling `shutdown()` from its own serving thread.
4. Hormuz waits for admitted requests for `listen.shutdown_grace_seconds`, which defaults to `30` and must be an integer from `1` through `300`.
5. If all admitted requests finish, the process exits successfully. If the deadline expires with work still active, Hormuz logs only the active-request count, closes the listener, and exits non-zero.

An idle HTTP/1.1 keep-alive socket is not active work and cannot hold shutdown open. Repeated `SIGTERM` signals do not create multiple shutdown workers.

Suggested orchestrator settings:

- use `/health/live` for liveness and `/health/ready` for readiness;
- stop routing new traffic before or when sending `SIGTERM`;
- set the orchestrator termination grace longer than `listen.shutdown_grace_seconds` so Hormuz, rather than the orchestrator, owns the bounded drain decision; and
- alert on `shutdown_grace_expired` without collecting raw request data.

## Current deployment boundary

This contract does not make the alpha production-ready. The reference server is plain HTTP and is intended for loopback or a separately hardened private TLS boundary. A pinned, non-root, scanned single-node container and executable restricted-runtime smoke test are documented in [CONTAINER.md](CONTAINER.md), but no image is signed or published. Usage, approval, context, and session stores remain local SQLite implementations; there is no accepted shared persistence topology, HA/failover proof, backup/restore proof, RPO/RTO, deep dependency probe, shared load-balancer drain protocol, or independent security review. Those gates remain tracked in the enterprise roadmap and production-deployment issue.
