# Local route admission

This implements the admission slice of [router spec #523](https://github.com/Xpounder-com/hormuz/issues/523)
on top of [execution destinations](EXECUTION_DESTINATIONS.md). Existing coding
apps submit requests to one router. No additional Hormuz UI is introduced.
Runtime/model installation, subscription adapters and persistent CLI/plugin setup
remain separate work.

## Configure an already qualified local runtime

The following is a fragment to add to an existing gateway configuration. The
referenced local route must already declare its destination, exact model revision
and capabilities. Its identity and effective policy must permit the coding client
and alias. A cloud baseline is the explicitly requested, policy-permitted route;
this slice does not invent a paid provider fallback.

```json
{
  "route_admission": {
    "mode": "auto",
    "local_routes": {
      "local-code": {
        "qualification_id": "your-tested-envelope-v1",
        "model_revision": "your-qualified-artifact-revision",
        "max_request_bytes": 16384,
        "max_output_tokens": 512,
        "task_types": ["summarization", "translation"]
      }
    }
  }
}
```

| Mode | Behavior |
| --- | --- |
| `configured` (default) | Retains existing configured/observational selection and relay contracts. |
| `auto` | Prefers an admitted local route for recognized fresh work; otherwise keeps the permitted cloud baseline or blocks an ineligible local baseline. |
| `local_only` | Admits qualified local execution or returns an explicit error. It never dispatches to a configured cloud destination. |

`X-Hormuz-Routing-Mode: local_only` tightens an individual request, including a
helper request. Other values and duplicate headers are rejected. A caller cannot
relax an operator's local-only mode. This header is consumed by Hormuz.

Qualification is an **operator declaration**, not a benchmark result. Before
adding it, qualify the exact runtime, model weights, client/protocol, request
features, byte/output envelope, tool and streaming behavior, and runtime egress.
The revision must match the route. Configurations accept at most eight records;
unknown fields, malformed bounds and mismatched revisions fail startup.
Available task labels are `summarization`, `translation`, `structured_extraction`,
`code_change` and `code_review`. A fixed, bounded local heuristic recognizes
leading instructions; it neither measures task difficulty nor calls a model.
Unknown work never gains local qualification merely from its size.

## Admission and resource ownership

Admission is independent of AI Work accounting, billing and answer reuse. It
runs after bounded context reconstruction and redaction, before optional work
selection. Work selection receives only admitted aliases. Local-only enforcement
covers Responses, Chat Completions, Messages, Responses compaction and token-count
helpers; helpers need their own capability and qualification.

This slice accepts self-contained text and declared inline function tools.
Local candidates require the endpoint capability (`responses`, `chat_completions`,
`messages`, `compact` or `count_tokens`), `text`, and feature capabilities
`tools`, `streaming` and `structured_output` when used. Unknown parameters,
multimodal content, remote/provider references, hosted tools, beta headers,
query options and unqualified provider features exclude a local candidate.
The final serialized body must fit the operator's byte envelope, and generation
must fit the output envelope. Byte bounds describe qualified request sizes;
they do not calculate a model's tokenizer context window. Token-count helpers
do not need a generation output limit.

A prompt-free llama.cpp-compatible `/health` probe requires HTTP 200 with
`{"status":"ok"}`. The probe has a two-second timeout, a 4 KiB bound, no retries,
no proxies or redirects, and a five-second observation lifetime. Probes are
serialized. They establish recent reachability/readiness, not model identity or
quality. A ready model can fail after admission; that failure never enables a
cloud retry. See the [llama.cpp server contract](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#api-endpoints).

One local request may be active per router in admission mode. A second receives
HTTP 429 `hormuz_route_local_capacity_busy`; there is no unbounded local queue.
The lease covers the entire response/stream and existing same-destination retry,
and releases in `finally` after completion, failure or disconnect cleanup. A
silent provider remains bounded by the configured upstream timeout; a disconnect
is observed by the existing relay when it next reads/writes, not by a separate
background cancellation worker.

Immediately before cache delivery and provider open, Hormuz rechecks admission,
the actual final body, current policy digest, destination custody and readiness.
A policy change blocks the in-flight request and requires a fresh request. A
known rejection before provider open settles its reservation at zero. Existing
ambiguous transport or partial-stream outcomes retain conservative accounting.
Capacity alternates must be admitted and retain the same destination/protocol;
there is no local-to-cloud replay.

Continuations require a retained `X-Hormuz-Work-Id`, successful prior response,
eligible pinned route and matching live destination generation. Missing affinity
blocks with HTTP 409. A known cloud continuation is blocked when local-only is
requested; it is never translated into a local session. Helpers without this
retained accounting path conservatively block opaque continuation state.

Locality enforcement concerns Hormuz's configured transport destinations: local
origins are literal loopback addresses and bypass proxies/redirects. The operator
must separately establish that the process listening there performs inference
locally and does not forward it elsewhere. No subscription entitlement or model
quality claim follows from these synthetic workflow tests.

## Status, compatibility and rollback

Authenticated `GET /v1/router/state` returns `hormuz.route-admission;v=1`, even
with AI Work disabled. It exposes policy-permitted qualification records, opaque
generation, declared feature/bounds and readiness (`unchecked`, `ready`,
`unavailable`, `stale`). Reading status does not send a probe or inference call.
`local_capacity` reports the configured limit and actual active lease count.

Admitted responses and exact-answer hits declare `hormuz.relay-metadata;v=3`.
Provider bodies remain unchanged. Destination fields retain version 2 meanings;
`X-Hormuz-Routing-Mode` reports effective mode, `X-Hormuz-Routing-Reason` reports
`qualified_local`, `permitted_baseline` or `session_affinity`, and
`X-Hormuz-Routing-Qualification` appears only for the actual qualified alias.
Protocol-shaped admission errors carry `X-Hormuz-Error-Code: hormuz_route_*`.
Legacy configurations keep their prior version 1/2 response shapes. The canonical
manifest retains both historical relay versions and adds version 3 plus router
status version 1. No ledger schema, transcript store or credential format changes.

Changing qualification or mode requires a router restart. Removing the admission
settings restores legacy configured selection; an older binary rejects these
new settings, so remove them before downgrading. Removing `local_only` also removes
that privacy ceiling and must be an explicit operator choice. Existing evidence
remains readable; destination generations continue to prevent reuse/continuation
across a restart. Readiness and active leases are process-local and never stored
as durable qualification or model-quality evidence.
