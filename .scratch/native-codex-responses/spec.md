# Native Codex Responses transport

Owner requested October 16, 2026 after #547 and #550: implement native Codex
`/responses` with TDD, preserve parallel work, complete CI/reviews and normal
protected merge. Baseline c7f8e1d includes the catalog and local coding admission.

Use the already owner-approved connect -> request -> inspect result/status ->
disconnect seam. Tests run the real CLI/router against disposable client and
native TLS/DNS/trust-store fixtures. No real accounts, auth-cache access,
settings writes outside fixtures, model downloads or paid API calls.

- Separate default-off `native_codex_responses` enables only POST
  `/backend-api/codex/responses`. Fixed verified TLS authority/path, separate
  gateway/native credentials and optional native account ID. Never select an
  API/local destination, redirect, proxy, retry, replay or billing fallback.
- Intersect identity/actor/team/organization client/model permissions, explicit
  native configuration, output caps, secret controls and hard/request local-only
  ceilings before egress. Resolve a configured alias or unambiguous native
  catalog slug, with no policy fallback. Budgeted scopes whose native usage/cost
  cannot be safely reserved fail closed; optional work/cache selection and
  content-history capture do not silently claim native coverage.
- Require strict bounded unambiguous JSON, SSE `stream: true`, no storage or
  background execution, no query/upgrade/transfer-encoding. Preserve supported
  native request fields, tool calls/results and opaque reasoning bytes. HTTP uses
  replayed input; server-side previous_response_id and WebSockets remain unsupported.
- Deliver SSE incrementally and preserve events/payloads including native
  terminal failure events. Success requires a valid `response.completed` with
  matching response/model, not EOF, `[DONE]`, or an event-name alone. Truncation,
  invalid events, timeout and disconnect never become success or trigger replay.
- Continuations require retained successful affinity to gateway identity,
  native credential/account, route/model and destination generation. Keep only
  bounded keyed digests of provider-issued identifiers/opaque state in memory;
  failed or undelivered completions cannot grant affinity. Restart/expiry or
  credential/account/model changes block rather than migrate state. Fresh
  native text replay remains supported. Concurrent admission cannot bypass
  capacity or continuation ownership.
- Share the existing eight-slot native outbound limit. Parent-owned isolated
  worker, bounded private pipes and filtered environment; no credentials in
  argv/environment/logs/files. Bound system DNS/headers, whole stream, idle
  reads, downstream writes and worker cleanup. Detect cancellation before
  headers and while idle/streaming; close/reap owned resources and leave other
  processes alone.
- Native 401/403/429 retain status and safe Retry-After; other pre-stream
  upstream errors use content-free 502. Partial streams close without a second
  HTTP response. Provider error bodies and arbitrary response headers are not
  reflected in gateway errors.
- Publish additive versioned HTTP/status contracts and compatibility fixtures.
  Status is authenticated, content-free, identity-scoped process observation,
  showing succeeded/failed/unknown outcomes, observed tokens and `chatgpt_plan`
  billing with unknown monetary cost. Exclude native work from generic API
  configured-rate estimates. Do not persist native credentials or transcripts.
- CLI/plugin generic setup stays independently qualified. Cloud-readiness keeps
  the full native path unimplemented until actual host/build, managed setup,
  consent and account eligibility are qualified. This does not close #523,
  #541 or #543. Update transport limitations, migration and rollback guidance.

Official source checked October 16, 2026:
https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway
The native guide defines native auth/authority; its linked gateway compatibility
checklist informs stream/tool/continuation tests without implying API-key auth.
