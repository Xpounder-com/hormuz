# Provider-native cache policy

Hormuz governs and measures **provider-native** prompt caching. It does not
store a prompt, response, cache key, reusable context pack, or customer
content, and it does not operate a Hormuz-owned cache.

This is policy and accounting for a cache operated by OpenAI or Anthropic. It
is not a response cache and never returns an earlier model answer.

## Policy modes

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

- `allow` is the default. It passes recognized explicit cache controls through
  unchanged when the effective client and routed Hormuz model alias are
  eligible.
- `deny` rejects a request containing a recognized explicit cache control
  before provider egress. It never removes or rewrites that control.
- `disabled` is the strict no-provider-cache mode. It allows a request only
  when a fresh, route-bound capability record proves an exact client-supplied
  provider opt-out; otherwise it denies before egress.

Organization, team, and actor policies are monotonic. A lower scope can narrow
eligible clients or models, make `allow` into `deny`, or make either mode into
`disabled`; it cannot relax a higher-scope `deny` or `disabled` policy.

`disabled` cannot contain `allowed_clients` or `allowed_models`: strict
no-cache coverage cannot have a hidden exception inside its own policy block.
The ordinary model/client authorization policy still decides which routes are
admitted at all.

## Strict `disabled` mode

Strict mode requires a content-free capability catalog under `policies` and a
maximum review age:

```json
{
  "policies": {
    "provider_cache_capabilities": {
      "gpt-5.6": {
        "protocol": "openai",
        "upstream_model": "gpt-5.6",
        "operations": ["/v1/responses"],
        "capability_version": "openai-responses-gpt-5-6-2026-08-21",
        "reviewed_at": "2026-08-21",
        "source_urls": [
          "https://developers.openai.com/api/docs/guides/prompt-caching"
        ],
        "strict_no_cache": "openai_explicit_without_breakpoints"
      },
      "claude-sonnet-5": {
        "protocol": "anthropic",
        "upstream_model": "claude-sonnet-5",
        "operations": ["/v1/messages"],
        "capability_version": "anthropic-messages-2026-08-21",
        "reviewed_at": "2026-08-21",
        "source_urls": [
          "https://platform.claude.com/docs/en/build-with-claude/prompt-caching"
        ],
        "strict_no_cache": "unsupported"
      }
    },
    "organization": {
      "provider_cache": {
        "mode": "disabled",
        "capability_max_age_days": 30
      }
    }
  }
}
```

Each entry is bound to the Hormuz route alias, exact provider protocol and
upstream model, and allowed Hormuz egress operation. Every configured model
route needs a fresh entry whenever any configured policy scope uses `disabled`;
an unsupported entry is valid, but every request on that route is denied before
egress. Startup rejects a missing, stale, mismatched, future-dated, or
structurally unsupported record.

The catalog is deployment-supported configuration. Policy projection v5
contains a canonical, content-free copy, and the policy API rejects a staged
copy that does not exactly match the running deployment's catalog. A
`policy_admin` can require strict mode but cannot invent a provider guarantee;
the deployment owner must review the source documentation and update the
catalog before policy staging/synchronization.

### Current strict strategy

`openai_explicit_without_breakpoints` is supported only where the catalog
explicitly records the OpenAI Responses operation and an appropriate model
family. The client must send, at the request root, exactly:

```json
"prompt_cache_options": {"mode": "explicit"}
```

It must not include `prompt_cache_key`, `prompt_cache_retention`, or an OpenAI
`prompt_cache_breakpoint` anywhere in the payload. Hormuz performs a bounded
structural inspection, retains none of the values, and forwards an allowed
request unchanged. It does not insert the option for Codex or another client.
The current OpenAI documentation says explicit mode without explicit
breakpoints does not use prompt caching or incur cache-write charges. See the
[OpenAI prompt-caching guide](https://developers.openai.com/api/docs/guides/prompt-caching).

An unsupported operation, model/protocol mismatch, missing/stale catalog
record, unsupported strategy, malformed opt-out, or incomplete inspection
returns `403 hormuz_provider_cache_disabled` with no provider call. The
content-free usage action is `provider_cache_disabled_denied`.

The initial catalog treats Anthropic strict no-cache as `unsupported`.
Anthropic documents top-level automatic caching with `cache_control`, and
documents cache behavior around thinking and tool flows that cannot be reduced
to a safe request-wide opt-out in this gateway contract. Hormuz therefore
denies rather than silently stripping a field or labeling normal Claude traffic
as no-cache. See [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

Provider documentation can change. Strict mode is fail-closed for an unknown,
unsupported, stale, or out-of-catalog **capability**; it does not claim to
infer the semantics of arbitrary future JSON fields. Review the provider
contract, model, operation, and customer data-controls agreement before adding
or renewing a catalog record.

## Explicit-control boundary

For `allow` and `deny`, Hormuz recognizes only static, documented field names:

| Provider | Recognized controls |
| --- | --- |
| OpenAI Responses | `prompt_cache_key`, `prompt_cache_retention`, `prompt_cache_options`, `prompt_cache_breakpoint` |
| Anthropic Messages | `cache_control` anywhere in the JSON request |

For an allowed request, Hormuz preserves a recognized field, its value, and its
position in the client payload. It never injects a `prompt_cache_key`, changes
an OpenAI caching mode, adds an Anthropic `cache_control`, or changes a TTL.
The `deny` path returns `403 hormuz_provider_cache_denied`, records
`provider_cache_explicit_denied`, and sends no request to the provider.

`deny` is not a universal no-cache promise. OpenAI enables prompt caching
automatically for eligible recent models, and provider features, model changes,
or future fields may have cache behavior outside this bounded inspection. A
strict requirement must use `disabled` and a reviewed capability record.

## Accounting and reconciliation

Hormuz separately records provider-reported `cache_read_tokens` and
`cache_write_tokens` beside input, output, reasoning, and normalized billable
token categories. Provider usage metadata is allowlisted; unknown response
fields are discarded rather than persisted. No cache-control value, cache key,
prompt fragment, or response text is written to usage, security, policy, or
audit ledgers.

The route's versioned rate card can produce a **request-time cost estimate**.
Hormuz does not emit a cache-savings metric: cache token categories alone do
not prove savings unless the provider accounting semantics and every applicable
base/cache price are known for that exact rate-card version.

`hormuz billing fetch`, `billing import`, and `billing reconcile` compare
request-time estimates with provider aggregate cost reports. `billing allocate`
uses those estimates as relative weights for a provider-total team/person/
unattributed breakdown. None of those commands turns a request, employee, or
team allocation into a final invoice. See
[billing reconciliation](BILLING_RECONCILIATION.md).

## Retention and privacy boundary

OpenAI and Anthropic control their own prompt-cache retention, storage, and
organization/project or workspace isolation. A Hormuz catalog review records
what the deployment relies on, but it does not replace a customer's executed
provider contract, data-controls configuration, Zero Data Retention
eligibility, residency setting, or provider billing configuration.

DLP and secret processing occurs before the cache-policy check. An allowed
provider-bound payload is therefore the redacted transient payload. Hormuz does
not persist a separate cache artifact, cache key, or reusable context pack for
this feature.

## Current boundaries

This implementation has loopback compatibility tests for OpenAI and Anthropic
request paths, policy scoping, strict denial before egress, separate cache
token accounting, unknown usage-field discard, tenant isolation, and
content-free SQLite/PostgreSQL evidence. It is not a live assertion of a
customer's provider contract or a replacement for a provider's retention and
invoice controls. A client adapter that deliberately emits the OpenAI strict
opt-out is a separate compatibility integration; Hormuz will not silently
rewrite an existing client's request to create that behavior.
