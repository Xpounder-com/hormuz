# Native Codex Responses

The default-off `native_codex_responses` flag adds HTTP/SSE inference at
`POST /backend-api/codex/responses`, alongside separately enabled
[native model discovery](NATIVE_CODEX_CATALOG.md). Both use the client's native
ChatGPT grant and a separate Hormuz gateway credential. This transport checkpoint
has synthetic workflow evidence; actual account consent/eligibility, managed
client setup and supported host/build qualification remain incomplete.
[Cloud readiness](CLOUD_READINESS.md) therefore keeps the full native path
`not_implemented`.

## Explicit configuration and request boundary

Add `"native_codex_responses": true` to an existing reviewed configuration with
`codex_chatgpt` upstreams and permitted model routes described by the catalog
contract. Discovery has its own `native_codex_catalog` flag. Existing generic
CLI/plugin setup still excludes native profiles; it does not install managed
native settings or activate this transport.

Supply one `X-Gateway-Key`, one native `Authorization: Bearer ...` and, when
available, one `ChatGPT-Account-ID`. Hormuz consumes its gateway key and forwards
only native authorization/account headers, JSON content type and SSE accept to
the fixed, certificate-verified `https://chatgpt.com/backend-api/codex/responses`.
The client owns sign-in and refresh. The gateway never reads a login cache or
puts native credentials in process arguments, environment, files or logs.

Requests need strict JSON, an ASCII model name, `input` and `stream: true`.
`store` is forced false when omitted and rejects any explicit value other than
false. Background execution, server-side `previous_response_id`/conversation
handles, queries, duplicate framing headers, transfer encodings and upgrades
are unsupported. The accepted request fields are the explicit `REQUEST_FIELDS`
profile in `native_codex_responses.py`; tools, reasoning, text options and
client metadata retain their native JSON after shared secret enforcement.
Native compaction endpoints and WebSockets remain unsupported. Input items are
limited to messages, reasoning, compaction, function/custom calls and their
results. Unqualified computer/MCP/shell/reference or other provider-state types
are blocked before egress; a role-less object cannot masquerade as fresh text.
Message content and tool-result lists use a closed text profile: strings or
typed `input_text`, plus assistant `output_text`/`refusal` replay with validated
passive citations. Active file/image/media inputs and nested provider reference
parts remain unsupported until separately qualified. Literal text mentioning
file IDs is still text; passive citation metadata does not fetch a file.
Tool declarations accept host-owned `function`/`custom` tools, optionally grouped
in one namespace. Tool selectors accept those same kinds, their allowlists, or
`auto`/`none`/`required`. Provider-hosted file search, containers, MCP and other
unqualified tools are rejected before egress, including nested declarations.

A configured alias takes precedence over native catalog slugs; aliases owned by
other destinations are blocked. Without an exact alias, an unambiguous native
slug selects one immutable destination. Policy fallback and operational failover are absent.
Identity, organization, team and actor Codex/model restrictions apply; hard or
request-level local-only blocks before egress. Shared output caps and compacted
context/secret redaction or denial apply before transport. Native credentials
are also protected during request redaction. Requests with monthly token/dollar
limits or per-actor dollar caps fail closed: this path cannot safely reserve
those limits from unknown native plan charges. Explicit work ID/scope headers
also fail. Optional work selection, caching, shadow routing, content history and
API configured-rate accounting do not claim native coverage.

## Streaming, continuation and outcomes

SSE bytes are delivered incrementally through bounded private pipes. Framing
accepts LF, CRLF or CR lines, one leading UTF-8 BOM and empty/colonless event
resets according to the [SSE parsing standard](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream).
A complete,
strict `response.completed` data event with matching model, ID and completed
status establishes success after delivery. Response envelopes and response-ID
fields must keep the same identity throughout the stream. Supported terminal
items are messages, reasoning, function/custom calls and compaction. Messages
require provider IDs, status and text/refusal content; text includes annotations.
Reasoning requires an ID and typed summary, with optional typed reasoning text
and nullable encrypted content. Compaction requires an ID and encrypted content.
Function/custom calls retain their required call/name/argument or input fields;
their optional item IDs may be omitted or null without weakening call binding.
Text annotations accept the documented URL/file/container-file citation and
file-path shapes, including their required fields and nonnegative ordered indices.
These follow the [official output message](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_output_message.py)
and [reasoning item](https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response_reasoning_item.py)
schemas checked October 16, 2026. Unsupported variants remain unknown until qualified.
A bare event name, `[DONE]`, EOF,
invalid/mismatched JSON or truncation cannot establish success. Native failed,
incomplete and error events retain their payload and establish failure. A valid
terminal ends delivery at that event boundary and closes the owned transport
without waiting for provider EOF. Trailing bytes do not change the validated
outcome or affinity. Partial
output closes without a second HTTP response, retry or alternate provider.

HTTP continuation uses replayed input. Function/custom tool calls, matching tool
results and encrypted reasoning/compaction content require retained successful
native affinity. It binds the Hormuz identity, exact native credential/account,
model/destination generation, body thread/session/cache identity and any
`thread-id`/`session-id` headers. Tool kind, namespace, direct/program caller and
async execution mode and status retain their validated issued values. Absent/null optional
values are equivalent. Supplied result names/namespaces/callers must match the
issued call; results and provider item IDs cannot change their item type.
Opaque values stay paired with their issued item kind/ID, and tool item IDs stay
paired with their complete issued call. Individually known IDs and blobs cannot
be recombined, and non-null issued state cannot be stripped on replay.
Those session headers constrain affinity and
are consumed locally. Changed arguments or opaque bytes, unknown provider IDs,
failed turns, restart, expiry or another owner block before egress. Native
credential refresh invalidates old opaque/tool affinity; it does not silently
rebind it. Plain text can start fresh.

Conversation ownership uses the original validated identifiers before secret
redaction. Continuation state must match retained authority both before and
after redaction, so distinct protected identifiers or unknown provider items
cannot gain affinity by collapsing to the same replacement marker.

The process keeps at most 1,024 keyed continuation digests for 15 minutes,
evicting the oldest when full. It retains no native token, response text or
transcript in that cache. Expired/evicted state requires a fresh conversation.
One request owns an identified native conversation or continuation at a time;
another gets 429. Fresh requests without thread/session/cache identifiers have
independent attempt leases while keeping stable continuation affinity.
Independent requests can proceed within the shared eight-request
catalog/Responses limit, which covers body processing and worker execution.

Transport bounds are three seconds for system DNS/TCP/TLS, then 30 seconds for
provider headers and idle reads, and a whole transport deadline of the smaller
of `upstream_timeout_seconds` and 600 seconds. The whole deadline wins over
phase deadlines. Requests are bounded by the smaller of the configured limit
and 25 MiB, including transformed egress JSON. Secret replacements check their
projected serialized growth before allocating replacement strings. Existing
bounded input may shrink during redaction; the selected final representation
must fit the configured egress cap or returns 413 before egress.
For recognized compacted tool results, reconstructed inspection has its own
1 MiB request ceiling; the configured egress cap applies to the representation
selected for forwarding after inspection. Streams are bounded by
64 MiB; each SSE line/event by 1 MiB. Downstream writes
have a separate three-second timeout. Cancellation is checked while awaiting
headers or stream data. Every exit closes pipes/connections and reaps its owned
worker: 0.2 seconds for termination, then a one-second kill/reap bound and a
one-second reader-thread join. These cleanup/write bounds are additional to
the transport deadline. No redirect, proxy, retry or API billing fallback runs.

Pre-stream native 401/403/429 retain status with a fixed gateway error; a safe
Retry-After is retained on 429. Other native statuses, wrong content types, TLS
failures and transport/deadline errors return content-free 502. Gateway auth is
401, unsupported input is 400, policy/affinity denial is 403, byte overflow is
413, and capacity/conversation contention is 429. A disabled endpoint is 404.

## Inspection, contracts and rollback

`GET /v1/router/native-codex` accepts the separate gateway key and returns the
identity's process-scoped observation. `enabled` reflects the configuration
flag, not account permission. Accepted attempts are split into active,
succeeded, failed and outcome-unknown counts. Strictly validated native usage
contributes observed token totals; missing/inconsistent usage contributes an
unknown-usage count. Billing is always `chatgpt_plan`, monetary cost is always
unknown/null, and native attempts are excluded from generic API cost estimates.
These observations reset on restart and are not a durable usage or billing
ledger. A successful response does not establish subscription entitlement.

The additive `hormuz.native-codex-responses;v=1` header and
`hormuz.native-codex-status;v=1` response contracts appear in `contract manifest`.
The [compatibility fixture](../tests/fixtures/native-codex-responses-v1.json)
defines their boundary. Existing errors, receipts, journals and database versions
remain intact.

The flag is an explicit configuration migration: older binaries reject it.
Disconnect through the shared connection owner before editing a running config.
To roll back, drain native requests, remove/disable this flag and remove it
entirely before downgrading. Follow the catalog rollback instructions when also
removing native upstreams/routes. No credential/transcript migration is needed;
restart clears observation and continuation memory while preserving existing
usage records and unrelated client settings.

The [official native gateway guide](https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway),
checked October 16, 2026, defines the credential/authority boundary. Its linked
[gateway compatibility checklist](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility)
informs stream/tool tests without changing native auth ownership. Real-account,
actual-client, model-quality and platform evidence remain separate from CI.
