# Provider reliability: streaming, latency, and bounded failover

Hormuz has a narrow availability mechanism for
accounted OpenAI and Anthropic generation requests. It preserves streaming,
records content-free timing evidence for each provider egress, and can make one
alternate provider call after an explicit capacity rejection. It is disabled
unless an operator configures an alternate route.

Local and PostgreSQL integration tests cover the mechanism. A separate
protected workflow must still prove it against the exact live Render commit and
real provider accounts. Neither form of evidence creates a customer SLA,
provider uptime claim, or multi-region design.

## Configuration

Configure the primary route with one `failover_alias`. The alternate must
exist, use the same protocol and provider-credential boundary, and resolve to a
different upstream model:

~~~json
{
  "model_routes": {
    "engineering-fast": {
      "protocol": "openai",
      "upstream_model": "gpt-fast",
      "failover_alias": "engineering-deep"
    },
    "engineering-deep": {
      "protocol": "openai",
      "upstream_model": "gpt-deep"
    }
  }
}
~~~

The effective organization, team, and actor policy must allow the alternate
alias. A configured route does not override an allowlist. An unknown alias,
self-reference, cross-protocol target, or target with the same upstream model
is a configuration error. Existing configurations have no `failover_alias`,
retain their previous local-policy fingerprint, and make exactly one provider
call.

## Exact failover boundary

Hormuz can use the configured alternate only when all of these facts hold:

- the route and effective policy permit it;
- the request is an accounted generation request;
- no response has started downstream;
- the provider returned HTTP `429` or HTTP `529`; and
- this request has not already used its one failover hop.

`429` is recorded as `provider_rate_limited`; `529` is recorded as
`provider_overloaded`. Hormuz closes that response, settles the first attempt,
commits a separate reservation and immutable attempt for the alternate, and
then makes the second call. A mutual or chained route configuration cannot
produce a third call.

Hormuz does not fail over after a connection error, timeout, generic 5xx,
partial response, downstream cancellation, or any other ambiguous outcome.
Those cases may have executed provider work and therefore must not be replayed.
OpenAI requests with `background: true` or `store: true` also remain single-call
requests because replaying stored or asynchronous work has different
semantics. Token-count and other unaccounted endpoints do not fail over.

If the alternate fails policy, attribution, custody, or budget enforcement, it
does not leave Hormuz. Each egress has its own model-specific reservation,
attribution, attempt lifecycle, usage settlement, and configured-rate estimate.
The original capacity rejection remains a terminal attempt even when the
alternate cannot begin.

## Streaming and latency evidence

Responses remain streamed. Hormuz does not buffer a provider response to make
the failover decision. For event streams it uses one available socket read at a
time so a small server-sent event is delivered without waiting for a 16 KiB
buffer or end of stream. If the client disconnects after delivery starts,
Hormuz closes the upstream response and records the attempt as outcome-unknown;
it does not retry.

One append-only `hormuz.provider-attempt-metrics` row records these gateway
observations for each terminal or outcome-unknown attempt:

| Field | Meaning |
| --- | --- |
| `response_headers_us` | Monotonic microseconds from provider-call start until response headers were available; null when no response arrived. |
| `first_body_byte_us` | Monotonic microseconds until Hormuz read the first response-body byte; null for an empty or unread response. |
| `total_us` | Monotonic microseconds until the response closed or the outcome became unknown. |
| `provider_bytes_read` | Response-body bytes Hormuz read from the provider. |
| `downstream_bytes_sent` | Response-body bytes Hormuz completed writing to the client. |
| `provider_status` | Provider HTTP status, or null for a transport-level ambiguity. |

Relayed HTTP responses expose the header-arrival observation as
`Server-Timing: hormuz_upstream_headers;dur=...`. A response served by the
alternate also carries `X-Hormuz-Failover: v1;reason=...`. These headers contain
no prompt or response content. They describe the observed gateway path and are
not a provider SLA measurement or full end-user latency.

`gateway_provider_failover_events` links the original and alternate immutable
attempt IDs with the exact trigger. Both new tables are append-only. SQLite
uses foreign keys, uniqueness constraints, and mutation triggers. PostgreSQL
uses the same constraints plus forced tenant row-level security and a
restricted SELECT/INSERT runtime grant. Neither table stores request or
response bodies.

## Protected live rehearsals

The Render pilot has two deterministic probes so live qualification does not
depend on waiting for a real provider incident:

- `X-Hormuz-Failover-Rehearsal` records a synthetic 429 for an eligible primary
  route without sending that first attempt upstream, then makes exactly one
  real request to its configured secondary. The response identifies both the
  normal failover reason and rehearsal version.
- `X-Hormuz-Cancellation-Rehearsal` makes one real streaming request, relays its
  first available chunk, closes the upstream response, records the attempt as
  outcome-unknown, and makes no alternate call. If that first read already
  contains the provider's terminal event, the response is treated as completed
  and cannot count as cancellation evidence.

Each header must contain the exact high-entropy
`HORMUZ_FAILOVER_REHEARSAL_KEY`. Supplying both headers, repeating one, or using
an invalid value returns 403 before provider egress. The credential belongs
only in the Render service and the protected qualification environment. It is
never a customer feature, client setting, URL value, artifact field, or log
value.

## Compute structure and bottlenecks

The normal path adds a monotonic clock read and one small metrics row. It adds
no worker, queue, cache, polling loop, response copy, or background task. The
relay holds the existing parsed request plus one response chunk, so response
memory remains bounded by the 16 KiB relay chunk rather than response size.
Only an eligible capacity rejection adds a second provider call, a second
attempt/reservation lifecycle, and one failover-link row.

The main pressure points are deliberate and visible:

- provider header and token latency dominate the synchronous request thread;
- every accounted egress requires a durable begin and terminal transaction;
- SQLite serializes writers and is intended for one-process evaluation;
- PostgreSQL concurrency is bounded by the configured connection pool and its
  wait limits;
- flushing each available streaming chunk favors interactive latency over
  maximum bulk throughput; and
- a failover can nearly double provider time and attempt evidence for that one
  customer request.

There is no hidden retry storm: the hop count is fixed at one, the accept queue
is bounded, reservations remain conservative, and ambiguous provider work is
never replayed. Provider-slot and connection-slot rejections have separate
content-free counters, so slow headers or non-inference connections cannot hide
worker pressure. Capacity planning still needs live traffic, a paid runtime,
provider quotas, and customer-specific latency targets. The stored observations
are the inputs for that later qualification; they do not establish it by
themselves.

## Verification boundary

The provider-free integration suite proves prompt streaming before provider
completion, upstream closure on downstream cancellation, one-hop `429` and
`529` behavior, policy and request exclusions, separate attempts and metrics,
and refusal to replay ambiguous transport or partial-stream outcomes. Migration
tests prove exact PostgreSQL schema v14, SQLite schema v10 rollback/retry,
append-only evidence, and tenant-bound links. The Render qualification tool
also checks actor-scoped counters, the eight-stream admission limit, the
four-connection PostgreSQL pool, the exact service and source commit, restart
survival, and qualification-session revocation. It accepts deployment evidence
only after authenticating the successful exact-main GitHub run and downloading
the uniquely named artifact that binds the same Render origin and service. A
normal streaming observation counts only when its first socket read excludes a
provider terminal event and a later read contains that terminal event.

Successful protected live runs, Render resource measurements under sustained
load, alerts, availability targets, and customer pilot results remain separate
gates.
