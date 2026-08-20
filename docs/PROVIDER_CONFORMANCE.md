# Live provider and stock-client conformance

`hormuz provider-conformance` sends one of three fixed package-owned probes
through a running Hormuz gateway: connectivity, synthetic-secret redaction, or
OpenAI compaction with required governed context. It verifies only the explicit
conditions for the selected probe and observes provider-reported token usage.

This command is opt in. It creates a real billable provider request. It does not
send customer repository, customer, employee, prompt-file, or ticket content.
Each fixed probe is versioned in the package and cannot be supplied on the
command line.
The example rate card is pinned to the public
[OpenAI GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
observed on August 20, 2026; operators must recheck provider pricing rather than
treating a repository example as an invoice.

## OpenAI local proof

Keep `OPENAI_API_KEY` in an ignored `.env.local` file or inject it from a secret
manager. Generate a separate employee-facing gateway credential and start the
example on loopback:

```bash
export HORMUZ_CONFORMANCE_TOKEN="$(openssl rand -hex 32)"
export HORMUZ_UNUSED_ANTHROPIC_KEY="$(openssl rand -hex 32)"
set -a
source .env.local
set +a
hormuz --config examples/provider-conformance-openai.json serve
```

In the same secured operator environment, run:

```bash
hormuz provider-conformance \
  --provider openai \
  --gateway http://127.0.0.1:8791 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --max-output-tokens 16 \
  --output /tmp/hormuz-openai-conformance.json
```

The provider key exists only in the gateway process. The probe process receives
only the Hormuz employee credential. OpenAI response storage and background mode
remain disabled by the example configuration. The strict two-protocol
configuration schema requires an Anthropic upstream object; its example-only
credential is random, has no model route, and is never sent.

## Synthetic-secret redaction proof

The optional `secret-redaction` probe sends one package-owned synthetic value
that matches the built-in OpenAI-key detector. It cannot accept an operator,
customer, employee, repository, or ticket value. Hormuz must return an
`allowed+redacted` policy decision and exactly one redaction, the provider must
return the fixed sanitized placeholder, provider usage must be present, and the
synthetic value must be absent from the complete response. Every condition fails
closed with a content-free error code.

Start the separate loopback example, whose 64-token ceiling accommodates
provider-reported reasoning tokens while keeping the probe bounded:

```bash
export HORMUZ_CONFORMANCE_TOKEN="$(openssl rand -hex 32)"
export HORMUZ_UNUSED_ANTHROPIC_KEY="$(openssl rand -hex 32)"
set -a
source .env.local
set +a
hormuz --config examples/provider-redaction-conformance-openai.json serve
```

Then run:

```bash
hormuz provider-conformance \
  --provider openai \
  --gateway http://127.0.0.1:8792 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --probe secret-redaction \
  --max-output-tokens 64 \
  --output /tmp/hormuz-openai-redaction-conformance.json
```

This proves only the exact built-in synthetic-secret path at one observed
endpoint. It does not evaluate customer-specific dictionaries, PII detectors,
encoded or opaque content, every request position, every model, or an
organization-representative corpus.

## Governed-context compaction proof

The OpenAI-only `compaction` probe imports one fixed, package-owned verified
context record, requires Hormuz to inject a valid governed Context Pack, and
requires OpenAI to return a Responses compaction object plus usage according to
the official [compact reference](https://developers.openai.com/api/reference/java/resources/responses/methods/compact).
The result retains neither the context, pack identifier, fixed prompt, opaque
compaction, nor provider request/response IDs. OpenAI's compact endpoint has no
generation-style hard output-cap field; Hormuz reserves the configured
allowance locally, but actual provider output can exceed it.

Generate the two local-only credentials, source the provider key only into the
gateway environment, import the fixed record, and start the dedicated example:

```bash
export HORMUZ_CONFORMANCE_TOKEN="$(openssl rand -hex 32)"
export HORMUZ_UNUSED_ANTHROPIC_KEY="$(openssl rand -hex 32)"
set -a
source .env.local
set +a
hormuz --config examples/provider-compaction-conformance-openai.json context-import \
  --records examples/provider-compaction-context.jsonl \
  --actor provider-compaction-conformance \
  --policy-version provider-compaction-conformance-v1
hormuz --config examples/provider-compaction-conformance-openai.json serve
```

Then run the fixed probe:

```bash
hormuz provider-conformance \
  --provider openai \
  --gateway http://127.0.0.1:8793 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --probe compaction \
  --output /tmp/hormuz-openai-compaction-conformance.json
```

The `--max-output-tokens` compatibility option is validated but is not sent to
`/responses/compact`. This probe does not test client continuation state,
automatic history selection, cache policy, or Anthropic.

## Stock Codex and Claude Code proof

`hormuz client-conformance` exercises an installed official client against the
same running gateway. It uses a separate fixed probe and cannot accept customer
content. The gateway configuration, identity, client allowlist, model route, and
policy cap must already authorize the requested client and alias.

For Codex:

```bash
hormuz client-conformance \
  --client codex \
  --gateway http://127.0.0.1:8791 \
  --allow-insecure-http \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model openai-live-luna \
  --executable deploy/clients/node_modules/.bin/codex \
  --expected-version 0.147.0 \
  --expected-executable-sha256 134063e133f0b4244fa3b251acf973d4fe4b4aeeacbdc135211bf480f59f1477 \
  --output /tmp/hormuz-codex-conformance.json
```

For Claude Code, use an Anthropic-authorized gateway identity and model alias:

```bash
hormuz client-conformance \
  --client claude \
  --gateway https://hormuz.example.com \
  --credential-env HORMUZ_CONFORMANCE_TOKEN \
  --model claude-live \
  --executable /approved/path/to/claude \
  --expected-version 2.1.233 \
  --expected-executable-sha256 "$APPROVED_CLAUDE_SHA256" \
  --output /tmp/hormuz-claude-conformance.json
```

The runner refuses to start until the resolved executable matches the
operator-approved SHA-256, then requires the client-reported semantic version to
match the separately approved version. The digest binds the resolved launcher;
the pinned npm integrity lock remains the separate dependency-closure proof.
The runner creates an empty private workspace and client home and constructs a
minimal child environment instead of inheriting host secrets. Codex uses
ephemeral, read-only execution, disables its shell, multi-agent, and web-search
tools, and verifies its dedicated final-message file. Claude Code uses bare
mode, no tools, non-persistent structured output, and an environment-backed
API-key helper.
The helper returns the employee-facing Hormuz credential, not the Anthropic
provider key. See the official
[Codex CLI reference](https://developers.openai.com/codex/cli/reference),
[Claude Code programmatic-mode guide](https://code.claude.com/docs/en/headless),
and [Claude Code gateway guide](https://code.claude.com/docs/en/llm-gateway).

Client stdout/stderr, the temporary final message, structured result, settings,
client home, gateway URL, fixed prompt, and credential are deleted rather than
retained. Output is capped at 1 MiB, the final marker is capped at 4 KiB, the
whole process group is terminated on timeout or excessive output, and evidence
writing keeps the same exclusive mode-`0600` contract as provider conformance.

## Evidence boundary

The provider JSON result contains the provider and protocol,
requested/routed model identifiers, the actual model when the provider response
exposes it, Hormuz policy decision, normalized
token categories, HTTP status, latency, version metadata, and boolean
assurances. The redaction result additionally retains the redaction count and
boolean proof of the gateway header plus sanitized provider echo, without the
synthetic value or placeholder. The compact result records proof of the required
context header and provider compact shape without the Context Pack ID, governed
text, or opaque compaction. The client JSON result contains the client name, exact version,
resolved-launcher digest, protocol, gateway interface, requested policy alias,
exit code, latency, and boolean assurances. All results omit:

- the gateway URL and network address;
- provider and employee credentials;
- the fixed prompt, marker, and provider response content;
- provider request and response identifiers; and
- customer or repository content, which the command never accepts.

File output uses mode `0600` and refuses to replace an existing path unless
`--force` is explicit. Redirects, non-JSON or oversized responses, missing policy
headers, a wrong marker, missing usage, timeouts, and provider/gateway errors fail
without reflecting remote bodies.

## Claim boundary

One successful probe is an observed endpoint check, not a provider SLA,
availability, retention, residency, quota, every-model, every-client, or
production-readiness certification. The repository compatibility matrix remains
conservative until both provider paths are independently exercised under a
defined release gate. Anthropic uses the same command with `--provider anthropic`
and a policy-authorized Claude model; a secure Anthropic credential must be
present only in that gateway process.

A successful `client-conformance` result proves only that the exact recorded
client version and resolved-executable digest completed the fixed request
through that gateway path. It does
not independently record gateway policy headers, provider-returned model, token
usage, or cost; pair it with `provider-conformance` and the gateway usage ledger
when those claims are required.

## Recorded OpenAI observation

The content-free evidence at
[`evidence/provider-conformance-openai-2026-08-19.json`](../evidence/provider-conformance-openai-2026-08-19.json)
records one successful local-evening August 19, 2026 observation through Hormuz:
the requested `openai-live-luna` policy alias routed to and returned
`gpt-5.6-luna`, with 20 input tokens, 10 output tokens, 30 billable tokens, and
2,967 milliseconds of measured gateway round-trip latency. Hormuz estimated
`$0.000016` for that request from the versioned example rate card. The output
file was mode `0600`; exact-value scans found neither the OpenAI credential nor
the generated Hormuz employee credential in the evidence or gateway log, and
the fixed marker was absent from the evidence.

This is an OpenAI-only endpoint observation. No Anthropic live conformance is
claimed, and the combined compatibility flag remains false.

The separate content-free
[`evidence/codex-openai-live-2026-08-19.json`](../evidence/codex-openai-live-2026-08-19.json)
records the same path exercised by the pinned stock Codex CLI `0.147.0`. Codex
received only the generated employee-facing Hormuz credential; its environment
explicitly excluded `OPENAI_API_KEY`. It exited successfully and returned the
fixed client marker. Hormuz recorded `gpt-5.6-luna`, 12,332 input tokens, 9 output
tokens, 12,341 billable tokens, 2,029 milliseconds gateway latency, and an
estimated `$0.002477` cost. The organization policy still capped output at 16
tokens. Exact-value scans found neither credential in Codex stdout, stderr, or
the gateway log; those transient client files and the marker are not retained in
the repository evidence.

The same client path was then repeated through the reusable command. Its
content-free result is
[`evidence/client-conformance-codex-openai-2026-08-19.json`](../evidence/client-conformance-codex-openai-2026-08-19.json):
pinned Codex `0.147.0` with the approved resolved-executable SHA-256 completed
the fixed request through
`POST /v1/responses` in 1,527 milliseconds. The command verified the dedicated
final-message file, used an isolated empty workspace and sanitized child
environment with shell, multi-agent, and web-search tools disabled, and retained
none of the prompt, response, client output, gateway address, or employee
credential. This repeat does not broaden the earlier OpenAI-only claim.

The separate fixed synthetic-secret probe then traversed the same OpenAI route.
Hormuz returned `allowed+redacted` with exactly one redaction, and the provider
returned only the sanitized placeholder. The request recorded 21 input, 37
output, 21 reasoning, and 58 billable tokens with 1,570 milliseconds measured
latency. Exact-value scans found no provider credential, employee credential, or
synthetic value in the evidence or gateway log. The result retains neither the
fixed prompt nor provider response and is recorded at
[`evidence/provider-redaction-conformance-openai-2026-08-19.json`](../evidence/provider-redaction-conformance-openai-2026-08-19.json).
This is one synthetic built-in detector observation, not complete DLP or
Anthropic evidence.

The fixed governed-context compact probe then traversed Hormuz
`POST /v1/responses/compact`. Hormuz returned `allowed+context-injected`, and
OpenAI returned a compaction object with 436 input, 390 output, and 826 billable
tokens in 4,154 milliseconds. The versioned rate card estimated `$0.000555`.
The OpenAI compact response did not expose an actual-model field, so the
content-free result deliberately omits it rather than copying the routed model.
Exact-value scans found no provider credential, fixed prompt, governed context,
Context Pack ID, or opaque compaction in the evidence; the recorded artifact is
[`evidence/provider-compaction-conformance-openai-2026-08-20.json`](../evidence/provider-compaction-conformance-openai-2026-08-20.json).
This is one OpenAI endpoint observation, not continuation binding, cache-policy,
Anthropic, SLA, retention, residency, or production-readiness evidence.
