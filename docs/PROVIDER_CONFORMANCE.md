# Live provider conformance

`hormuz provider-conformance` sends one fixed, low-output text probe through a
running Hormuz gateway. It verifies that the gateway authenticated the employee,
applied a policy route, reached the selected provider, received the fixed marker,
and observed provider-reported token usage.

This command is opt in. It creates a real billable provider request. It does not
send repository, customer, employee, prompt-file, or ticket content. The fixed
probe is versioned in the package and cannot be supplied on the command line.
The example rate card is pinned to the public
[OpenAI GPT-5.6 Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
observed on August 19, 2026; operators must recheck provider pricing rather than
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

## Evidence boundary

The JSON result contains the provider and protocol, requested/routed/actual model
identifiers, Hormuz policy decision, normalized token categories, HTTP status,
latency, version metadata, and boolean assurances. It omits:

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
