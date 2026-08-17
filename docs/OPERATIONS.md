# Hormuz operations contract

This document defines the current process-level health and shutdown contract. It is a bounded local/runtime foundation, not evidence of a production deployment, high availability, disaster recovery, or dependency-wide health.

## Configuration rollout

Hormuz strictly validates its complete configuration before constructing `GatewayServer`; unknown fields and unresolved or ambiguous policy scopes fail startup. The accepted configuration is immutable for the process lifetime. Hormuz does not implement `SIGHUP` or in-place reload because authenticators, policy engines, redactors, rate limiters, database handles, and secret-derived state must change as one coherent snapshot.

Run `hormuz --config CANDIDATE doctor` with the exact target package and target secret/network environment, record its printed configuration SHA-256 in the separately controlled deployment definition, and require that digest when starting the replacement revision. Wait for liveness and readiness, then shift traffic and drain the old revision. Retain the previous image digest, configuration artifact, and expected configuration digest as the rollback set. `doctor` performs OIDC discovery/JWKS validation when configured, but does not issue provider generation requests. See [CONFIGURATION.md](CONFIGURATION.md) for the strict schema, exact-file binding, secret boundary, and full change procedure.

## Health endpoints

Health endpoints are intentionally unauthenticated so an orchestrator can call them without an employee or provider credential. They return `Cache-Control: no-store`, use the fixed schema `hormuz.health.v1`, and never reflect a request query, header, body, credential, prompt, response, filename, or internal exception.

| Endpoint | Ready state | Saturated state | Draining state | Meaning |
| --- | --- | --- | --- | --- |
| `GET /health/live` | `200`, `status=live` | `200`, `status=live` | `200`, `status=live` | The Hormuz HTTP process can serve the probe. |
| `GET /health/ready` | `200`, `status=ready` | `503`, `status=busy`, `Retry-After: 1` | `503`, `status=draining`, `Retry-After: 1` | Whether the process is currently admitting new application work. |
| `GET /health` | `200`, `status=ok` | `503`, `status=busy`, `Retry-After: 1` | `503`, `status=draining`, `Retry-After: 1` | Compatibility endpoint with protocol and enabled-feature metadata; it follows readiness. |

Health probes do not consume an application-request slot, so liveness and readiness remain observable at the parsed-request capacity ceiling. They do consume one accepted-connection slot like every HTTP exchange. If all pre-parse connection slots are already occupied, Hormuz cannot read enough of a new socket to identify it as a probe and closes it; operators should set connection capacity above application capacity and retain an external connection limit. These are shallow process probes. They do not query an upstream model provider, OIDC issuer, SQLite database, external KMS, or any future shared service. A provider or identity outage therefore does not turn liveness into a process restart loop. Dependency monitoring and a supported multi-node readiness policy remain open production-design work.

Example:

```bash
curl --fail-with-body http://127.0.0.1:8787/health/live
curl --fail-with-body http://127.0.0.1:8787/health/ready
```

## Single-process resource limits

Hormuz applies seven process-local limits:

- `max_request_bytes` defaults to 25 MiB and rejects an announced larger generation body before reading it. Narrow administration and context routes use smaller fixed limits.
- `listen.accept_backlog` defaults to `256` and accepts values from `1` through `65535`. Hormuz applies it as the listening socket's accept-queue hint before activation; the default aligns with the separate application connection ceiling. It bounds the application-requested queue depth for completed connections waiting to be accepted, but the operating system may cap or reinterpret it and manages SYN queues independently.
- `listen.max_connections` defaults to `256` and accepts values from `1` through `10000`. The accept loop acquires a slot before `ThreadingHTTPServer` creates a handler thread, so incomplete request lines and headers cannot create more than the configured number of connection workers. A socket arriving at the ceiling is closed immediately without a handler, authentication, body read, policy, storage, provider work, or usage event. Because an incomplete request cannot safely receive a protocol-shaped HTTP response, this boundary rate-limits the fixed content-free event `connection_capacity_exhausted` and performs a hard close.
- `listen.request_header_timeout_seconds` defaults to `15` and accepts values from `1` through `120`. One server-wide watchdog enforces an absolute wall-clock deadline from accepted connection to complete request headers, and re-arms the deadline while an HTTP/1.1 keep-alive connection waits for its next request. A client cannot extend it by continuously trickling bytes. Expiry emits one content-free `request_header_deadline_exceeded` event with only the number of sockets in that deadline batch, shuts down those sockets, and releases their connection slots when the bounded workers exit.
- `listen.request_body_timeout_seconds` defaults to `30` and accepts values from `1` through `600`. For every `POST` and `PUT`, an absolute wall-clock deadline starts when headers finish and ends only after exactly the announced `Content-Length` bytes are received. The reader reduces each socket wait to the remaining total time, so continuously trickled bytes cannot reset the deadline. Expiry returns fixed `408 request_body_timeout`, closes the connection, rate-limits the content-free `request_body_deadline_exceeded` event, and performs no JSON expansion, provider call, or usage accounting. Early EOF returns fixed `400 incomplete_request_body` and also closes the connection.
- `listen.max_concurrent_requests` defaults to `128` and accepts values from `1` through `10000`. After HTTP parsing, one atomic admission check occurs before authentication, body reading, policy, storage, or provider work. When capacity is full, the request receives a fixed content-free `503 gateway_busy`, `Retry-After: 1`, and connection closure. Completion, error, client disconnect, and total-deadline paths release the slot exactly once.
- `upstream_timeout_seconds` defaults to `600` and is a total wall-clock deadline beginning immediately before the provider open. Provider reads use deadline-aware single-read streaming, and both provider reads and downstream writes are tightened to the remaining interval. Expiry before downstream response headers returns a fixed provider-shaped `504 gateway_upstream_timeout`. Once response headers have begun, expiry closes both sides and records the accounted request as failed; Hormuz cannot replace an already-started provider-compatible response with a new JSON error body.

The connection ceiling is deliberately higher than the parsed-request default so ordinary clients and probes have header/keep-alive headroom. Both ceilings are hard process-local maxima, not workload-sizing recommendations. The accept backlog is only a kernel hint, not another active-connection allowance or a portable guarantee. The header deadline ends when parsing completes and the body deadline begins at that boundary for body-bearing methods. These controls do not provide per-source rate limits, control platform SYN queues, guarantee an operating-system DNS lookup deadline, coordinate capacity across replicas, or replace reverse-proxy/WAF TLS, connection, header, request-rate, and independently configured slow-body controls. Those remain required deployment work under issue #11.

## Content-free latency telemetry

Every newly accounted generation attempt snapshots bounded integer timing
metadata for gateway handling and policy evaluation, plus provider timing only
when an upstream attempt begins. Existing automatic-context assembly timing is
aggregated only for injected packs. An authorized `usage_viewer` can request
tenant-scoped cumulative histograms through `hormuz usage report
--include-latency`; the ordinary report remains schema v2 and unchanged. See
[USAGE_ADMIN_API.md](USAGE_ADMIN_API.md) for the exact v3 contract and coverage.

This is an SLI input, not an SLO. Hormuz does not yet select availability,
latency, error-rate, authentication-failure, budget-correctness, or audit-delivery
targets; it does not alert, page an owner, or claim end-to-end client latency.
Pre-authentication failures and work outside accounted generation routes are not
represented in the tenant report. Numeric SLO targets, severities, owners,
escalation paths, external collector topology, and retention remain release and
operator decisions under issue #9.

## Graceful shutdown

`SIGTERM` starts one idempotent shutdown sequence:

1. Hormuz atomically withdraws readiness.
2. A syntactically valid HTTP request that was admitted before that transition may finish within its other limits. Later implemented application requests receive a fixed content-free `503 gateway_draining` response, `Retry-After: 1`, and connection closure before authentication, body processing, policy evaluation, storage, or provider work.
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

This contract does not make the alpha production-ready. The reference server is plain HTTP and is intended for loopback or a separately hardened private TLS boundary. Remote OpenAI and Anthropic upstreams must use HTTPS; plaintext HTTP is accepted only for a literal loopback/`localhost` development provider, and base URLs cannot contain credentials, queries, or fragments. Provider redirects are refused with a fixed content-free gateway error so the server-held credential and request remain bound to the configured origin. Those application invariants do not prove DNS integrity, certificate policy beyond the platform trust store, private endpoint configuration, or workload egress enforcement. A pinned, non-root, scanned single-node container and executable restricted-runtime smoke test are documented in [CONTAINER.md](CONTAINER.md). [RELEASES.md](RELEASES.md) defines tag-driven private GHCR publication, keyless signing, provenance, verification, and digest-based rollback, but no image is claimed as published until a qualifying tag run succeeds. Usage, approval, context, and session stores remain local SQLite implementations; there is no accepted shared persistence topology, live configuration reload, deployment-coordinated rollback, HA/failover proof, backup/restore proof, RPO/RTO, deep dependency probe, shared load-balancer drain protocol, release-tag governance, or independent security review. Those gates remain tracked in the enterprise roadmap and production-deployment issue.
