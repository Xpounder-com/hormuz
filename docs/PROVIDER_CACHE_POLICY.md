# Provider-native cache policy

Hormuz governs and measures **provider-native** prompt caching. It does not
store a prompt, response, cache key, reusable context pack, or customer
content, and it does not operate a Hormuz-owned cache.

This first policy slice is deliberately narrow: it gates documented,
client-requested cache controls without changing their meaning. It does not
claim to turn off provider-automatic caching.

## Configuration

`provider_cache` is an optional policy block at organization, team, and actor
scope:

```json
{
  "provider_cache": {
    "mode": "allow",
    "allowed_clients": ["codex", "claude-code"],
    "allowed_models": ["gpt-5.4", "claude-sonnet-5"]
  }
}
```

- `mode: "allow"` (the default) passes known explicit cache controls through
  unchanged when their client and resolved Hormuz model alias are eligible.
- `mode: "deny"` rejects a request containing a known explicit cache control
  before provider egress. It does not remove or rewrite that field.
- `allowed_clients` and `allowed_models` limit only known explicit cache
  controls. A client or resolved alias outside either list receives the same
  denial.

Organization, team, and actor blocks overlay monotonically: a narrower scope
can intersect the eligible client/model lists or change `allow` to `deny`; it
cannot re-enable an organization or team `deny`.

## Exact enforcement boundary

Hormuz recognizes only static protocol field names, not field values:

| Provider | Known explicit controls |
| --- | --- |
| OpenAI Responses | `prompt_cache_key`, `prompt_cache_retention`, `prompt_cache_options` |
| Anthropic Messages | `cache_control` anywhere in the JSON request |

For a permitted request, Hormuz preserves those fields, their values, and their
position in the client payload. It does not inject a `prompt_cache_key`, an
OpenAI explicit-mode option, an Anthropic `cache_control`, or a longer TTL. On
a denial it returns `403` with
`hormuz_provider_cache_denied`, records the content-free policy action
`provider_cache_explicit_denied`, and sends no request to the provider.

DLP and secret processing occurs before this policy check. A successful
redaction therefore changes the transient provider-bound payload before a
provider could create a cache entry. No cache-control value or prompt fragment
is added to the usage, security, policy, or audit ledgers.

### What “deny” does not mean

OpenAI documents automatic caching for eligible prompt prefixes. This Hormuz
control does not inject an explicit-only mode or otherwise alter OpenAI's
automatic behavior, so `mode: "deny"` must not be described as a universal
OpenAI no-cache guarantee. [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
also makes effective behavior model- and option-dependent.

Anthropic documents cache requests using `cache_control`; Hormuz can deny that
known field without mutating a request. Provider tools, thinking, model
changes, undocumented future fields, and structural inspection beyond the
bounded parser are not claimed as governed cache behavior. See
[Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

A policy requiring a strict provider-no-cache guarantee needs a reviewed,
versioned provider/model capability catalog and a deny-or-reroute decision for
every automatic or unknown combination. That is not implemented in this first
slice. It is intentionally safer to expose the limitation than to label an
uncontrolled provider behavior "disabled."

## Accounting and privacy

Hormuz separately records provider-reported `cache_read_tokens` and
`cache_write_tokens` alongside input, output, reasoning, and normalized
billable-token categories. Provider-native usage metadata is allowlisted; new
or unknown provider response fields are discarded rather than persisted.

The configured, versioned route rate card can support a **request-time cost
estimate**. Hormuz does not currently report a cache-savings metric: cached
tokens alone do not prove savings unless the provider accounting semantics and
every applicable base/cache price are known for that exact rate-card version.
No request-level or allocated team/person estimate is a final invoice.

Use `hormuz billing fetch`, `billing import`, and `billing reconcile` for
provider aggregate evidence. Those records retain cache/token-type dimensions
when supplied, but only provider aggregate billing is provider-reported; a
team or person amount remains an explicitly labeled allocation unless the
customer has isolated provider projects/workspaces/credentials at that same
boundary. See [billing reconciliation](BILLING_RECONCILIATION.md).

Provider documentation is not a customer data-processing agreement. OpenAI
states that caching/retention behavior depends on endpoint, model, organization
data controls, and settings; extended caching can have separate residency
implications. Review the customer's contract and data controls before allowing
sensitive workloads. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)
and [Anthropic's cache retention notes](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
are the current public provider references, verified 2026-08-20.

## Scope and follow-on gates

This policy is part of [issue #22](https://github.com/Xpounder-com/hormuz/issues/22)
and follows the supported gateway boundary in [ADR 0008](decisions/0008-gateway-product-boundary.md).
It does not create a reusable Hormuz context/content cache, final-answer cache,
or an organizational-memory system.

The remaining issue requires provider capability review, real provider contract
checks for each supported model family, aggregate billing reconciliation,
tenant deployment proof, and a separately approved strict no-cache capability
contract before any broader no-cache promise is made.
