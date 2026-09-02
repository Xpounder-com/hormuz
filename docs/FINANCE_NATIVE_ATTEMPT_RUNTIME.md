# Native attempt finance runtime candidate

This 1.1.0 successor implements the analytics-first attempt fact accepted in
the version-3 preflight. It does not make Hormuz a provider invoice ledger and
does not close issue #8 or the final transition gate in issue #214.

Every new provider-egress attempt freezes the exact configured route-price ID,
version, digest, and currency in its immutable request root. On a known
terminal result, the same storage transaction appends the existing usage fact,
the exact terminal event, one provider-native finance sidecar, optional
provider timing, and both audit-chain entries. An unknown or stale result gets
one sidecar with a null usage link, an explicit `attempt_outcome_unknown`
estimate reason, and its conservative budget hold intact. Pending attempts
have no sidecar.

The provider-native estimate is also the amount committed to the linked usage
fact and budget reconciliation whenever all required price dimensions are
available. A database guard enforces that equality, so management totals,
budget settlement, and the sidecar cannot silently disagree. OpenAI
`response.failed` and `response.incomplete` terminal events remain successful
HTTP relays to the caller but commit as known non-success attempts rather than
as successful work.

The sidecar separates three meanings that management and accounting must not
conflate: provider-observed native usage, Hormuz's configured-rate estimate,
and future provider-final invoice truth. Unknown values remain null; an
observed zero remains zero. Missing pricing components make the estimate
unavailable, never zero and never equal to the reservation merely because the
reservation exists.

## Necessary audit-chain correction

The accepted preflight counted one new table and five columns, while also
requiring the sidecar itself to enter the existing version-2 commit audit
chain. Those requirements cannot both be implemented without expanding the
chain's finite source allowlist. Version 4 records that dependency explicitly.

SQLite therefore rebuilds `gateway_audit_chain_entries` transactionally to add
the three source-identity columns and permit exactly the existing custody
sources plus `hormuz.finance-attempt-evidence` version 1. Every predecessor row
is copied without changing its event JSON, digest, position, head, or
checkpoint. PostgreSQL already has the source columns, so migration 15 changes
only the finite source constraint and its security-definer insert guard. The
runtime still cannot insert arbitrary version-2 JSON: the guard requires an
exact tenant-local sidecar row with identical canonical evidence bytes.
Canonical source bytes use the audit chain's UTF-8 JSON representation, so an
already-valid tenant identity containing Unicode or spaces crosses the root,
terminal, usage, finance, and audit writes without a post-egress failure.

## Compatibility and coverage

The public usage repository protocol, version-1 usage projection, request
attempt event schema, HTTP and CLI surfaces, budget reports, and provider
request bodies remain unchanged. Pre-migration attempts retain an explicit
`legacy_unavailable` price-binding state and receive no guessed sidecar,
backfill, or repricing.

Direct callers of the version-1 usage repository contract have no route-price
object to supply. Their new attempts therefore bind the deterministic
`usage-repository-v1-unpriced` compatibility identity before egress and record
an unavailable estimate. That identity is not a configured production rate
card and must not be grouped with one; the gateway path always binds the exact
configured route-price ID, version, digest, and currency.

The parser performs one bounded strict parse and emits both the legacy usage
projection and the provider-native observation. The initial profiles cover
OpenAI Responses and Anthropic Messages metadata enumerated by the frozen
attempt contract. Unrecognized response content is neither retained nor
included in evidence.

## Acceptance boundary

Source implementation is not runtime acceptance. Acceptance still requires an
exact-head tech-lead review, all protected checks, real SQLite and PostgreSQL
transition and recovery evidence, a normal merge, and the same checks against
the exact merged-main commit. Provider billing import, invoice reconciliation,
shared-cost allocation, accounting-period policy, management delivery, and
live provider validation remain later gates on issue #8.
