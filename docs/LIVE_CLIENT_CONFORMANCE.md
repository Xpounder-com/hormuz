# Live Codex and Claude Code conformance

This gate proves that the exact supported Codex and Claude Code releases can
send governed streaming requests through Hormuz to real OpenAI and Anthropic
provider endpoints. It is opt-in because it uses provider credentials, spends
provider tokens, and cannot run safely for pull requests from forks.

It complements two provider-free checks:

- blocking CI runs the pinned official clients through Hormuz against
  loopback provider simulators;
- the weekly non-blocking upstream canary repeats those simulator checks with
  the latest client releases.

Neither provider-free check is described as live provider evidence.

## Supported client baseline

| Client | Exact release | Native gateway path |
| --- | --- | --- |
| OpenAI Codex CLI | `0.147.0` | user-level custom `model_providers` entry using the Responses wire API |
| Anthropic Claude Code | `2.1.233` | `ANTHROPIC_BASE_URL` plus a bearer identity in `ANTHROPIC_AUTH_TOKEN` |

Codex documents custom provider `base_url`, `env_key`, command-backed `auth`,
and the `responses` wire API in its
[configuration reference](https://developers.openai.com/codex/config-reference).
Provider settings belong in user-level configuration; project-local
configuration cannot override them.

Claude Code documents `ANTHROPIC_BASE_URL` for a gateway and gives
`ANTHROPIC_AUTH_TOKEN` precedence as the bearer-token credential. See its
[LLM gateway](https://code.claude.com/docs/en/llm-gateway) and
[authentication](https://code.claude.com/docs/en/authentication) references.
The provider key stays in Hormuz and is not placed in the Claude Code process.

## What the harness proves

`tools/verify_live_client_conformance.py` starts the normal Hormuz HTTP,
policy, redaction, request-attempt, streaming relay, and SQLite evidence path
on loopback. For each selected client it requires:

- the exact expected client version plus entrypoint and runtime SHA-256 values;
- a tenant-qualified human identity and selected team policy;
- the configured model to be requested, routed, and reported by the provider;
- a 64-token policy cap in the post-policy request immediately before egress;
- a synthetic credential to be absent and the redaction marker to be present
  immediately before egress;
- a streaming generation request and a successful real provider response;
- provider-reported input and output tokens, a provider request-ID presence
  flag, and a positive configured-rate-card estimate;
- a strict v2 usage event plus a content-free secret event;
- no provider credential in either client environment.

The tool-local pre-egress observer retains only booleans, the routed model,
and the numeric output limit. It does not retain the request body.

## Credential boundary

Use dedicated, least-privilege project/workspace credentials with low spend
limits. Hormuz cannot introspect every provider's key scopes, so the evidence
records this as an operator attestation rather than claiming independent scope
verification.

The command requires this exact acknowledgement:

```text
I_UNDERSTAND_LIVE_PROVIDER_CALLS_HAVE_COST_AND_USE_DEDICATED_KEYS
```

The safest automated path is the manually dispatched
`Live BYO-provider client conformance` workflow. Configure the protected
`live-provider-conformance` GitHub environment with these secrets:

```text
HORMUZ_LIVE_OPENAI_PROVIDER_KEY
HORMUZ_LIVE_ANTHROPIC_PROVIDER_KEY
```

The workflow supplies model IDs as non-secret dispatch inputs, scopes both
secrets only to the conformance step, grants only `contents: read`, and never
runs on push, pull request, or a schedule.

For a local operator-controlled run, place credentials in a non-symlinked
`0600` environment file. The parser accepts literal `NAME=value` entries and
does not execute shell syntax:

```bash
chmod 600 /secure/path/provider-conformance.env

python tools/verify_live_client_conformance.py \
  --provider openai \
  --provider anthropic \
  --credential-env-file /secure/path/provider-conformance.env \
  --openai-credential-env OPENAI_API_KEY \
  --anthropic-credential-env ANTHROPIC_API_KEY \
  --openai-model YOUR_OPENAI_MODEL_ID \
  --anthropic-model YOUR_ANTHROPIC_MODEL_ID \
  --acknowledgement I_UNDERSTAND_LIVE_PROVIDER_CALLS_HAVE_COST_AND_USE_DEDICATED_KEYS \
  --evidence-out /secure/path/live-client-evidence.json
```

Both exact client executables must already be first on `PATH`. A one-provider
run is allowed for diagnosis, but its artifact is marked `scope: partial` and
cannot close the bounded two-client conformance gate.

## Evidence contract

The output is `hormuz.live-client-conformance` schema version 1. Validation
uses an exact field allowlist and fails on unknown fields. The artifact may
contain only:

- source revision, UTC time, runner OS/architecture;
- provider, client version, entrypoint digest, and native runtime digest;
- requested, routed, and provider-reported model identifiers;
- policy version/action and tenant identity IDs;
- token counts and configured-rate-card micro-USD estimates;
- booleans for request-ID presence, streaming, capping, pre-egress redaction,
  and client credential isolation;
- fixed checks and explicit nonclaims.

It never contains prompts, responses, provider request IDs, identity tokens,
provider keys, redaction replacements, or client debug logs. The file is
created atomically with mode `0600` and never overwrites an existing path. The
runner also refuses a dirty checkout or an asserted source revision that is
not the exact checked-out `HEAD`, so a retained artifact cannot be attributed
to uncommitted harness code. The GitHub artifact is retained for seven days.

## Unsupported and failure behavior

- This gate covers OpenAI Responses over HTTP/SSE and Anthropic
  Messages/count-tokens over HTTP/SSE. It does not certify Responses
  WebSockets, background Responses, stored-response retrieval, provider file
  APIs, or every future client feature.
- Hormuz does not expose the private Codex model-catalog metadata. A catalog
  refresh warning may occur while native model IDs continue using bundled
  client metadata.
- Hormuz does not currently expose Claude Code's optional gateway `/v1/models`
  discovery endpoint. Discovery stays disabled in the gate; use an explicit
  model ID.
- Hormuz governs provider inference traffic, not shell commands, MCP servers,
  Git operations, browser traffic, or other client-side tools.
- A missing gateway identity or denied model fails before provider egress. A
  missing provider credential fails closed. An ambiguous network failure is
  retained as `outcome_unknown`; Hormuz does not automatically replay it.
- A new client release is unsupported until the pinned simulator test and a
  deliberate live conformance run pass. The weekly latest-release canary is a
  warning signal, not an automatic support declaration.

This proof does not establish provider invoiced cost, traffic that bypassed
Hormuz, general client feature support, or enterprise production readiness.
