# ADR 0013: Policy-bounded provider failover

- Status: Accepted
- Date: 2026-09-01
- Target: source development after the v1.1 work-budget runtime

## Context

Hormuz is expected to sell governed access together with better operational
visibility and resilience. Retrying a model call indiscriminately would weaken
that promise: a transport failure or interrupted stream may already have caused
provider work, and replaying it can duplicate cost or side effects. Buffering a
complete response before delivery would also damage interactive latency and
increase memory with response size.

Availability cannot bypass the existing identity, effective policy,
attribution, custody, reservation, and evidence boundaries. A second egress is
a second governed attempt, even when it serves one client request.

## Decision

An operator may attach one `failover_alias` to a model route. The target must be
a distinct upstream model under the same protocol and credential boundary. The
effective policy must allow the target.

Hormuz may make exactly one alternate call only after receiving HTTP `429`
(rate limited) or HTTP `529` (provider overloaded), before any response begins
downstream. It never retries transport ambiguity, a generic 5xx, partial
streaming, cancellation, OpenAI stored/background work, or unaccounted helper
requests. The alternate is passed into forwarding as a single-use decision;
its own configured fallback is not evaluated.

The original attempt is settled before the alternate is admitted. The
alternate receives a new durable attempt, reservation, route-specific rate
identity, attribution, and terminal settlement. An append-only link records the
original attempt, alternate attempt, trigger status, and fixed reason. One
append-only metric row per attempt records monotonic header, first-byte, and
total timing plus provider/downstream byte counts. No content is retained.

Streaming remains incremental. Server-sent events use an available socket read
rather than waiting for the full relay buffer. Header-arrival latency is
returned with `Server-Timing`; an applied failover is identified with a fixed
`X-Hormuz-Failover` value.

## Consequences

The default path stays single-call and adds only small constant metadata work.
An eligible rejection can add one provider call and one full governed attempt.
SQLite remains a one-process evaluation store; PostgreSQL remains the shared
runtime path with forced tenant isolation. The design does not implement
cross-protocol or cross-credential failover, health-score routing, speculative
requests, automatic provider replay, dashboard aggregation, a customer SLA, or
regional availability.

The exact operational and evidence boundary is documented in
[provider reliability](../PROVIDER_RELIABILITY.md).
