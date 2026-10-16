# 0028: Keep native Responses separate from API accounting and selection

Accepted October 16, 2026 for the owner-requested Responses implementation after
#550. Extend the catalog's separate native/gateway authentication boundary with
an independently default-off HTTP/SSE transport to the fixed native backend.
Generic API/local setup, automatic selection and failover remain separate, so a
native grant cannot become an API credential or authorize another destination.

Bind replayed tool/opaque state to successful delivered output, the exact native
credential/account, identity, session, model and destination generation. Retain
only bounded keyed digests in process memory. This keeps native credentials and
transcripts out of persistence, at the cost of losing affinity on refresh,
restart, expiry or eviction. Such changes require a fresh conversation.
Client-originated messages/results cannot supply provider item IDs; only
qualified provider-owned replay retains those identities. The native flag also
does not restore the external ingress configuration retired by ADR 0018.

Message and tool-result content use a qualified text profile. Active media/file
and nested provider references remain closed until their ownership path is
qualified. Assistant text/refusal replay and passive citation metadata remain
supported; a citation is not an input-file retrieval authority.
Reasoning summaries/content share their terminal text-part validation on replay,
and issued provider item status stays bound to its identity. Redaction refuses
configured protected literals, including native grants, contained in its
replacement marker rather than retaining a credential in transformed egress.
Native inspection also rejects protected member names under redact/deny modes,
preserving schema semantics without forwarding a credential in a key.
Tool declarations and selectors likewise remain limited to host-owned
function/custom tools and their namespaces, without provider-hosted state.

Observe native outcomes and strictly validated tokens through a separate
process-scoped contract with unknown plan cost. Monthly-budget/token-limited
scopes and explicit work-accounting bindings fail closed until native reservation
semantics exist. This avoids silently treating subscription requests as priced
API usage or bypassing a budget. It adds no durable evidence ledger.

An isolated owned worker bounds DNS/TLS and the whole operation; bounded private
pipes preserve incremental SSE, cancellation and downstream backpressure. A
verified terminal closes transport promptly. Transport tests do not establish
native account eligibility, supported client activation or release readiness.
See [the contract, limits and rollback](../NATIVE_CODEX_RESPONSES.md). ADR 0027
remains the historical discovery decision; this record extends its inference
boundary without changing its catalog contract.
