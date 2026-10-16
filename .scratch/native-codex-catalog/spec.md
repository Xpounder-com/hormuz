# Native Codex catalog authentication boundary

Selected October 15, 2026 as the next independent cloud-auth implementation
after [#547](https://github.com/Xpounder-com/hormuz/pull/547), advancing
[#523](https://github.com/Xpounder-com/hormuz/issues/523). Acquisition (#548),
coding qualification (#546) and the current CLI adapter (#549) have separate
owners. Preserve their work and integrate main normally.

Implement opt-in native model discovery through the approved public seam:
connect/configure a disposable coding client, submit through the real gateway,
inspect its result/status and external traffic, disconnect. Write failing
workflow tests before production changes; stand in only for external services,
DNS/trust stores and the coding-client executable.

- Default-off GET `/backend-api/codex/models`, with only an optional numeric
  `client_version` query. Never accept an upstream URL from the caller.
- Separate `X-Gateway-Key` authenticates the Hormuz identity. A single native
  bearer token and optional single `ChatGPT-Account-ID` go only to the fixed,
  certificate-verified `https://chatgpt.com/backend-api/codex/models` endpoint.
  Reject missing, malformed, duplicate or known gateway/API credentials used
  as native auth; consume the gateway key and discard unrelated headers.
- Introduce an explicit `codex_chatgpt` authentication profile, fixed to that
  cloud OpenAI backend, without an API credential or storage/background opt-in.
  Native-only configuration must not need an API key. Existing destination
  ownership and policy remain authoritative; no new credential store.
- Identity, actor, team and organization client/model restrictions and config
  or per-request local-only mode block discovery before egress. Return only
  configured native model slugs allowed by the effective policy.
- Require a bounded, unambiguous JSON `models` array. Preserve provider model
  metadata for allowed slugs, discard unconfigured models/root extras, reject
  duplicate/malformed records and over-limit bodies.
- At most eight native outbound requests per server, three-second socket and
  response deadlines, 1 MiB body and 256 model records. Close sockets/timers;
  no redirects, environment proxies, retries or alternate billing path. Return
  content-free errors; preserve native 401/403/429 status and safe Retry-After.
- Native intent on generic endpoints or selecting a native profile for
  inference must fail before provider execution. Exclude native profiles from
  generic model discovery and API-key readiness counts. Catalog requests do
  not create model-usage charges. Keep full native authorization readiness
  `not_implemented` until inference, client/host, consent and actual account
  qualification are complete.
- Publish the additive `hormuz.native-codex-catalog;v=1` HTTP header contract,
  compatibility fixture, migration/rollback instructions and skill guidance.
  Leave existing error/receipt/journal versions intact.

No real-account credentials, live provider calls, native sign-in/cache access,
managed workspace configuration, inference/streaming/continuations, app-specific
OAuth, deployment choice or release qualification belongs to this slice.
Synthetic TLS success establishes transport behavior only. Do not close the
parent or human decision gates #541/#543.

Source: [OpenAI native gateway guide](https://learn.chatgpt.com/docs/enterprise/sign-in-with-chatgpt-through-a-gateway),
checked October 15, 2026, plus the retained cloud-auth research linked by #539.
