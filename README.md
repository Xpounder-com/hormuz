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
- Per-organization commit-time audit chains for new metadata-only usage and secret-egress events, with explicit recovery epochs and optional asynchronous Object Lock checkpoints.
- Pre-provider secret redaction or denial with built-in detectors, custom environment-provided values, and metadata-only detection evidence.
- A versioned, machine-enforced active-core secret inventory that assigns every environment or ambient credential read an owner, consumer, custody mode, and rotation boundary without reading or serializing secret values.
- OpenAI response storage and background mode disabled by default as enforceable provider privacy policy.
- Configuration output for installed Codex and Claude Code clients.
- Generic OIDC discovery/JWKS verification with strict issuer, audience, expiry, asymmetric-algorithm, subject-mapping, and signing-key-rotation enforcement.
- Versioned unauthenticated liveness and dependency-readiness probes for deployment health checks; readiness never calls a provider and turns unavailable before graceful shutdown drains requests.
- Bounded, duplicate-free, schema-strict configuration loading before any identity, secret, storage, provider, or listener initialization.
- A customer-controlled TLS reference boundary: public TLS stays at customer ingress, while a non-loopback Hormuz listener requires an authenticated, network-restricted proxy hop.
- A digest-pinned, non-root OCI reference runtime with externally mounted configuration and durable SQLite data; its executable boundary is intentionally narrower than a published or production-certified deployment.
- Candidate OCI supply-chain evidence: a CycloneDX SBOM and pinned Trivy scan that block only HIGH/CRITICAL findings with a scanner-reported fixed version, while retaining all other findings for review.
- A digest-pinned, disposable PostgreSQL logical backup-and-restore exercise that verifies metadata-only governed state and retains only a content-free recovery summary.

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

Inspect the current per-organization commit-time chain without contacting an
external storage service:

```bash
python3 -m hormuz --config hormuz.json audit-chain status
```

The deprecated context-pack experiment is intentionally outside the core gateway. See [docs/CONTEXT_EXPERIMENT_MIGRATION.md](docs/CONTEXT_EXPERIMENT_MIGRATION.md) for the separate package and its temporary compatibility shim.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the evidence-gated enterprise program, [docs/POLICY_CONTROL.md](docs/POLICY_CONTROL.md) for bootstrap, root-authority, activation, rollback, and recovery boundaries, [docs/CONTRACTS.md](docs/CONTRACTS.md) for the versioned policy/evidence contract and migration boundary, [docs/STORAGE.md](docs/STORAGE.md) for SQLite/PostgreSQL setup, upgrade, rollback, and recovery boundaries, [docs/OPERATIONS.md](docs/OPERATIONS.md) for liveness, readiness, and shutdown behavior, [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the customer-controlled TLS and trusted-proxy boundary, [docs/OCI.md](docs/OCI.md) for the non-root reference container boundary, [docs/decisions/README.md](docs/decisions/README.md) for proposed and accepted architecture decisions, [docs/CLIENTS.md](docs/CLIENTS.md) for Codex and Claude Code setup, [docs/OIDC.md](docs/OIDC.md) for generic enterprise identity, [docs/OIDC_PROVIDER_CONFORMANCE.md](docs/OIDC_PROVIDER_CONFORMANCE.md) for the bounded external-provider reference proof, [docs/USAGE.md](docs/USAGE.md) for team/person/model cost and budget reporting, [docs/AUDIT.md](docs/AUDIT.md) for the export contract and limitations, [docs/CUSTODY.md](docs/CUSTODY.md) for optional self-hosted or AWS custody and immutable audit anchors, [docs/SECRET_CUSTODY_INVENTORY.md](docs/SECRET_CUSTODY_INVENTORY.md) for the machine-enforced active secret-ownership boundary, [docs/CEPH_RGW_CONFORMANCE.md](docs/CEPH_RGW_CONFORMANCE.md) for the first self-hosted target and its live-certification boundary, [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) for the egress boundary, [docs/VERIFICATION.md](docs/VERIFICATION.md) for executable compatibility evidence, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request path and current trust boundary.

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

The current hardening program focuses on a minimal gateway core: policy enforcement, versioned PostgreSQL policy administration, accounting, deterministic secret egress, metadata-only audit, and OIDC JWT verification. The package boundary and policy/evidence contract are explicit, the SQLite/PostgreSQL compatibility seam is tested, and the non-root OCI reference runtime has a candidate SBOM and fix-aware vulnerability gate. A disposable logical PostgreSQL backup/restore exercise now proves a narrow recovery path, but it is not production backup/PITR or DR evidence. Before an enterprise release Hormuz still needs live customer-account certification, migration of every secret class, registry publication and image signing/provenance, TLS and deployment hardening, shared PostgreSQL operations, production backup/PITR, multi-instance coordination, and independent review. It is not building an organizational-memory or workflow product.
