# Execution destinations

This is the first implementation slice of [router spec #523](https://github.com/Xpounder-com/hormuz/issues/523).
It makes an explicitly selected model route reach its own origin with its own
authentication. Task selection, a hard local-only admission policy, model/runtime
acquisition, subscription authorization and persistent CLI/plugin setup are later
slices. Existing coding apps remain the client; this adds no Hormuz UI.

## Configuration

Existing `upstreams.openai` and `upstreams.anthropic` configurations keep their
protocol defaults and credential behavior. A route without `destination` still
selects the upstream named by its protocol. Additional upstream names require an
explicit `protocol`; routes refer to them using `destination`.

The following fragment selects an already running local runtime. It is not a
complete gateway configuration; identity, policy, listener and storage are still
required as in the existing configuration example.

```json
{
  "upstreams": {
    "local-llama": {
      "protocol": "openai",
      "base_url": "http://127.0.0.1:8080",
      "locality": "local",
      "authentication": "none"
    }
  },
  "model_routes": {
    "local-code": {
      "protocol": "openai",
      "destination": "local-llama",
      "upstream_model": "your-installed-model",
      "model_revision": "your-qualified-artifact-revision",
      "capabilities": ["responses", "text"]
    }
  }
}
```

`locality` is `local` or `cloud` (default `cloud`). A local origin must use a
literal loopback address, including `127.0.0.1` or `::1`; DNS hostnames are not
accepted as local. This identifies the transport address, not proof that the
process behind it performs inference locally. Runtime/model qualification is
still required. URL credentials, query strings and fragments are rejected.
Local dispatch bypasses environment/system HTTP proxies and refuses redirects,
so proxy settings cannot silently substitute another origin.

`authentication` is `api_key` (default) or `none`. `api_key` requires exactly one
environment source or custody envelope. `none` requires a local destination and
forbids credential sources. Client/gateway credentials are never forwarded to an
unauthenticated local origin. A cloud destination receives only its configured
credential. This slice adds no subscription login or paid billing fallback.

`model_revision` is optional printable text up to 128 characters. `capabilities`
is an optional list of at most 16 distinct lowercase identifiers, each at most
32 characters. These fields record declared model facts and qualify identity;
they do not establish model quality, protocol parity or admission eligibility.
The current observational selector's existing qualification rules still apply.

Managed `custody_lifecycle` continues to support its existing protocol-scoped
destinations. Named destinations are explicitly rejected with that feature until
their lifecycle contract is qualified. Existing envelope custody and tenant
checks remain in place for other supported configurations.

## Dispatch and status

One immutable startup generation binds destination name, wire protocol, origin,
authentication, locality, model, revision and capability metadata. Editing a
configuration file or its input dictionaries does not repoint an active router.
Restart is required to publish changed facts. The resolved generation supplies
forwarding, public status, observational identity and exact-reuse identity.

Storage/background controls are checked against the actual selected destination
as well as effective organization policy. The local policy preflight considers
permitted routes; managed organization restrictions retain their authority.
Optional optimization fallback does not change the selected destination.

The ADR 0013 capacity alternate must use the same destination and protocol with
a distinct upstream model. Cross-destination failover is rejected at configuration
and policy selection. Transport ambiguity, partial streams, cancellation and
unaccounted helpers remain ineligible for replay. Helper dispatch uses the same
destination resolution as accounted requests.

`/v1/work/state` and `/v1/work/connect` use schema version **2** when destination
metadata is explicitly configured. Route records retain `model`, `protocol`,
`credential_configured` and `eligible`, and add `destination`, `locality`,
`authentication`, `generation`, `upstream_model`, `model_revision` and
`capabilities`. `eligible` remains configuration/authentication availability,
not runtime readiness or model quality. A local `none` destination can be
available while `credential_configured` is false. Onboarding and job choices
consume the same availability. Legacy configurations keep the version 1 shape.

Opt-in destination responses declare `X-Hormuz-Contract:
hormuz.relay-metadata;v=2` and add:

| Header | Meaning |
| --- | --- |
| `X-Hormuz-Destination` | Configured destination identifier |
| `X-Hormuz-Destination-Locality` | Configured `local` or `cloud` classification |
| `X-Hormuz-Destination-Generation` | Opaque startup generation; not a credential digest |

All required version 1 relay headers retain their meanings. Legacy routes keep
version 1 and do not emit these added headers. Version 2 consumers must recognize
the marker before interpreting destination fields; provider response bodies are
unchanged. Exact-answer hits report their source destination generation with
`X-Hormuz-Usage-Origin: cached-original-response`; they do not claim new inference.

## Existing evidence, recovery and rollback

The durable route-fingerprint payload is explicitly versioned as
`destination-v1:<64 hex characters>`. It is an opaque identity, never a verifier
derived from credential bytes. The existing AI Work database schema stays version
1: no columns or existing records are rewritten. Previous unprefixed fingerprints
remain readable for costs, attempts and submitted observations, but cannot qualify
a new resolved destination generation. A fresh router generation conservatively
requires fresh comparable observations; it does not inherit old quality claims.

Established jobs carrying assistant/tool/provider state must match the pinned
destination generation. After restart, their continuation is rejected with HTTP
409 and `hormuz_ai_work_session_destination_generation_changed`. Start a fresh
conversation/job to re-establish state; do not resend opaque state to another
destination. This check applies to attributed and unattributed jobs whose work ID
the client retains. General session admission without a retained work ID belongs
to the next slice; this implementation does not claim that broader guarantee.

Rollback requires stopping the router, reverting destination configuration to the
legacy protocol entries, and starting fresh conversations. Older builds reject
named destination configuration. Existing work ledgers and cost/outcome evidence
are retained. Do not downgrade and resume destination-qualified opaque state;
older builds cannot enforce this generation contract.

## Validation boundary

The approved test seam is connect → submit a coding-client request through the
router → inspect result/status → disconnect. Tests run the real configuration
CLI, gateway, policy, transport and temporary storage with bounded disposable
provider and coding-client executables. They do not inspect private state to
verify outcomes. Real llama.cpp model quality, native subscription eligibility,
installed Codex versions, OS installation and marketplace acceptance remain
separate qualification work.
