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
