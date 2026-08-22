# Hormuz

Hormuz is a CLI-first enterprise control plane that puts organization policy between employees' existing AI clients and model providers. It currently proxies the OpenAI Responses API used by Codex and the Anthropic Messages API used by Claude Code.

The first executable milestone enforces client, model, output-token, monthly-token, team-budget, and per-person budget rules. It records metadata-only usage in a local SQLite ledger or an explicitly configured PostgreSQL usage/evidence repository, estimates cost from configured rate cards, and keeps provider API keys on the Hormuz server rather than distributing them to employees.

The included rate cards are examples current as of August 15, 2026. Treat them as versioned configuration: verify them against provider pricing before production use and reconcile estimated spend against provider invoices.

Hormuz is alpha software. The local prototype proves routing and policy behavior; it is not yet a production-ready multi-tenant service.

## What works

- OpenAI-compatible `POST /v1/responses` proxying, including streaming.
- Anthropic-compatible `POST /v1/messages`, `/v1/messages/count_tokens`, and streaming.
- Provider model IDs by default, preserving native client model behavior; optional company aliases remain supported.
- Organization, team, and person policy overlays that can only become more restrictive.
- Optional PostgreSQL-backed immutable policy documents with one-time authenticated administrator bootstrap, audited activation/rollback, and request-pinned policy versions.
- Model fallback, output-token caps, monthly token limits, and USD budget limits.
- Per-person attribution using unique bootstrap tokens or generic OIDC JWT access tokens mapped by issuer and subject.
- Input, output, cache-read, cache-write, and reasoning-token accounting when providers report them.
- Metadata-only usage ledger: SQLite by default, with an optional PostgreSQL adapter for the same narrow usage/evidence contract. Prompts and responses are relayed, not persisted.
- Metadata-only JSONL audit export for usage and secret-egress evidence, with private file permissions and a SHA-256 checksum.
- Pre-provider secret redaction or denial with built-in detectors, custom environment-provided values, and metadata-only detection evidence.
- OpenAI response storage and background mode disabled by default as enforceable provider privacy policy.
- Configuration output for installed Codex and Claude Code clients.
- Generic OIDC discovery/JWKS verification with strict issuer, audience, expiry, asymmetric-algorithm, subject-mapping, and signing-key-rotation enforcement.
- Versioned unauthenticated liveness and dependency-readiness probes for deployment health checks; readiness never calls a provider and turns unavailable before graceful shutdown drains requests.
- Bounded, duplicate-free, schema-strict configuration loading before any identity, secret, storage, provider, or listener initialization.

## Quick start

Hormuz requires Python 3.11 or newer. OIDC verification uses PyJWT and `cryptography`; signature verification is intentionally delegated to maintained security libraries rather than implemented in Hormuz.

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
  --model gpt-5.5 \
  --max-output-tokens 50000
```

Inspect current-month usage:

```bash
python3 -m hormuz --config hormuz.json status
python3 -m hormuz --config hormuz.json status --group-by team
python3 -m hormuz --config hormuz.json status --group-by model --team engineering
python3 -m hormuz --config hormuz.json status --json
```

Inspect the versioned policy/evidence schemas before integrating a report or audit export:

```bash
python3 -m hormuz contract-manifest
```

Export metadata-only audit evidence for the current month:

```bash
python3 -m hormuz --config hormuz.json audit-export \
  --kind all \
  --output hormuz-audit.jsonl
```

The deprecated context-pack experiment is intentionally outside the core gateway. See [docs/CONTEXT_EXPERIMENT_MIGRATION.md](docs/CONTEXT_EXPERIMENT_MIGRATION.md) for the separate package and its temporary compatibility shim.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the evidence-gated enterprise program, [docs/POLICY_CONTROL.md](docs/POLICY_CONTROL.md) for bootstrap, root-authority, activation, rollback, and recovery boundaries, [docs/CONTRACTS.md](docs/CONTRACTS.md) for the versioned policy/evidence contract and migration boundary, [docs/STORAGE.md](docs/STORAGE.md) for SQLite/PostgreSQL setup, upgrade, rollback, and recovery boundaries, [docs/OPERATIONS.md](docs/OPERATIONS.md) for liveness, readiness, and shutdown behavior, [docs/decisions/README.md](docs/decisions/README.md) for proposed and accepted architecture decisions, [docs/CLIENTS.md](docs/CLIENTS.md) for Codex and Claude Code setup, [docs/OIDC.md](docs/OIDC.md) for generic enterprise identity, [docs/USAGE.md](docs/USAGE.md) for team/person/model cost and budget reporting, [docs/AUDIT.md](docs/AUDIT.md) for the export contract and limitations, [docs/CUSTODY.md](docs/CUSTODY.md) for optional AWS KMS and S3 Object Lock custody, [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) for the egress boundary, [docs/VERIFICATION.md](docs/VERIFICATION.md) for executable compatibility evidence, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request path and current trust boundary.

## Test

```bash
python3 -m unittest -v
```

The suite uses local fake OpenAI and Anthropic endpoints and does not need real provider credentials. The Codex test runs against an installed `codex` executable when present. The official Claude Code executable test is opt-in because `npx` may download the client:

```bash
HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 python3 -m unittest -v
```

The GitHub publication gate also tests Python 3.11 through 3.14, builds and installs the distribution wheel, verifies pinned official Codex and Claude Code releases, and runs a non-blocking weekly canary against their latest releases. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the exact boundary.

## Roadmap boundary

The current hardening program focuses on a minimal gateway core: policy enforcement, versioned PostgreSQL policy administration, accounting, deterministic secret egress, metadata-only audit, and OIDC JWT verification. The package boundary and policy/evidence contract are explicit, and the SQLite/PostgreSQL compatibility seam is tested. The first AWS KMS/Object Lock custody implementation is available, but before an enterprise release Hormuz still needs live customer-account certification, migration of every secret class, TLS and deployment hardening, shared PostgreSQL operations, backup/restore evidence, multi-instance coordination, and independent review. It is not building an organizational-memory or workflow product.
