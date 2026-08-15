# Hormuz

Hormuz is a CLI-first enterprise control plane that puts organization policy between employees' existing AI clients and model providers. It currently proxies the OpenAI Responses API used by Codex and the Anthropic Messages API used by Claude Code.

The first executable milestone enforces client, model, output-token, monthly-token, team-budget, and per-person budget rules. It records metadata-only usage in SQLite, estimates cost from configured rate cards, and keeps provider API keys on the Hormuz server rather than distributing them to employees.

Hormuz is alpha software. The local prototype proves routing and policy behavior; it is not yet a production-ready multi-tenant service.

## What works

- OpenAI-compatible `POST /v1/responses` proxying, including streaming.
- Anthropic-compatible `POST /v1/messages`, `/v1/messages/count_tokens`, and streaming.
- Company aliases that map to provider-specific model IDs.
- Organization, team, and person policy overlays that can only become more restrictive.
- Model fallback, output-token caps, monthly token limits, and USD budget limits.
- Per-person attribution using unique Hormuz identity tokens.
- Input, output, cache-read, cache-write, and reasoning-token accounting when providers report them.
- Metadata-only SQLite usage ledger. Prompts and responses are relayed, not persisted.
- Configuration output for installed Codex and Claude Code clients.

## Quick start

Hormuz uses only the Python standard library at runtime and requires Python 3.11 or newer.

```bash
cp config.example.json hormuz.json
export HORMUZ_TOKEN="replace-with-a-long-random-employee-token"
export OPENAI_API_KEY="your-company-openai-key"
export ANTHROPIC_API_KEY="your-company-anthropic-key"
python3 -m hormuz --config hormuz.json doctor
python3 -m hormuz --config hormuz.json serve
```

In another terminal, print the configuration for an existing client:

```bash
python3 -m hormuz --config hormuz.json client-config codex
python3 -m hormuz --config hormuz.json client-config claude
```

Employees authenticate to Hormuz with their unique `HORMUZ_TOKEN`. Hormuz removes that credential and authenticates upstream with the company's provider key.

## Policies and usage

Evaluate a request without calling a model:

```bash
python3 -m hormuz --config hormuz.json policy-check \
  --actor alice \
  --client codex \
  --protocol openai \
  --model engineering-deep \
  --max-output-tokens 50000
```

Inspect current-month usage:

```bash
python3 -m hormuz --config hormuz.json status
python3 -m hormuz --config hormuz.json status --json
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request path and current trust boundary.

## Test

```bash
python3 -m unittest -v
```

The suite uses local fake OpenAI and Anthropic endpoints and does not need real provider credentials. The Codex test runs against an installed `codex` executable when present. The official Claude Code executable test is opt-in because `npx` may download the client:

```bash
HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 python3 -m unittest -v
```

## Roadmap boundary

The current milestone is the enforcement and accounting kernel. Before an enterprise release, Hormuz still needs secret/PII redaction, durable identity and tenancy, TLS and deployment hardening, immutable audit export, invoice reconciliation, provider conformance coverage, and the governed reusable-context subsystem.
