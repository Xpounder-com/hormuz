# Hormuz

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Keep Codex and Claude Code. Put company policy in between.

Hormuz is a CLI-first AI gateway and control plane. It authenticates the person
and team behind each request, enforces which clients, models, output limits,
budgets, and secret-egress rules apply, routes allowed traffic to OpenAI or
Anthropic, and records versioned metadata-only evidence. Employees keep their
existing AI clients; company provider credentials stay on the Hormuz service.

Hormuz is a **public open-source alpha**, not a production-ready or
enterprise-HA release. Use it with synthetic data for evaluation. Its current
proofs do not establish production security, availability, disaster recovery,
provider-invoice accuracy, or suitability for customer secrets.

## Try the real gateway without a provider account

From a clean checkout on the release-gated Linux source path:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz demo
```

The install may download Python dependencies. The demonstration itself uses
only disposable loopback listeners: it needs no configuration, API key, model
account, or provider network call. It sends synthetic requests through the
real Hormuz HTTP, policy, redaction, request-attempt, and SQLite evidence path,
then reports:

```text
PASS allowed request reached the loopback provider simulator
PASS unapproved model was rerouted and output-capped
PASS detected secret was redacted before provider egress
PASS denied request made no provider call
PASS content-free evidence validated: 4 usage events, 1 security event
PASS external provider calls: 0 (3 loopback simulator calls)
```

The command validates the existing versioned evidence contracts, proves its
synthetic request, response, identity, and credential values are absent from
the ledger, and removes the temporary configuration and database before it
exits. It is an executable product tour, not a provider-compatibility or
production-deployment claim. Its `PASS` lines are human diagnostic output, not
a new machine-readable compatibility contract.

Invited independent reviewers should follow the
[quiet-alpha verification guide](docs/QUIET_ALPHA.md). Its strict aggregate
uses opaque participant IDs and fixed metadata enums; Hormuz adds no product
telemetry, and a synthetic fixture can never satisfy the launch gate.

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
- A deterministic `linux/amd64`, non-root OCI runtime with hash-locked Python wheels, externally mounted configuration, and durable SQLite data.
- A published `v0.1.1` `linux/amd64` OCI artifact identified by immutable digest: keyless GitHub OIDC/Cosign signing with public Rekor, strictly validated registry-only CycloneDX and bounded provenance attestations, exact-workflow verification, anonymous GHCR pull, and no mutable `latest` tag.
- OCI supply-chain evidence that blocks fixable HIGH/CRITICAL findings while retaining all other scanner observations, plus a two-build byte-for-byte reproducibility gate.
- A versioned single-Linux-VM Docker Compose profile with one signed Hormuz digest, one private persistent PostgreSQL digest, protected file-mounted secrets, a customer-operated external-DSN path, and a provider-free clean-VM proof; it is for evaluation and pilots, not HA or production certification.
- A digest-pinned, disposable PostgreSQL logical backup-and-restore exercise that verifies metadata-only governed state and retains only a content-free recovery summary.

The included rate cards are examples current as of August 15, 2026. Treat them
as versioned configuration: verify them against provider pricing before
production use and reconcile estimated spend against provider invoices.

## Architecture and maturity

```text
Codex / Claude Code
        |
authenticated person + team
        |
policy -> allow / deny / reroute / cap
        |
secret control -> redact / deny
        |
OpenAI / Anthropic

Every governed outcome -> versioned metadata-only evidence
```

Hormuz governs provider-bound requests and their organizational usage
evidence. It is not an identity provider, model, organizational memory,
metadata compiler, or employee-productivity system.

| Status | Public-alpha boundary |
| --- | --- |
| Production-ready | None claimed. The alpha is for evaluation and design-partner hardening. |
| Implemented alpha | OpenAI/Anthropic-compatible gateway paths, policy enforcement, secret controls, identity binding, and metadata-only usage/evidence. |
| Verified reference | Only the exact evidence-gated profiles in [SUPPORT.md](SUPPORT.md), including the Linux Python matrix and published signed `v0.1.1` `linux/amd64` OCI runtime. A verified reference is not unrestricted certification. |
| Experimental | The context experiment is a separate package and is absent from the core wheel and gateway runtime. |
| Deferred | Organizational memory, ticketing, workflow/productivity measurement, and new reporting dimensions are outside the current core. |
| Unfinished | Live BYO-provider release evidence, production HA/DR, cloud-specific certification, and independent review remain separate release gates. |

## Configure real providers and clients

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

Continue with [Codex setup](docs/CLIENTS.md#codex),
[Claude Code setup](docs/CLIENTS.md#claude-code), the
[signed OCI digest verification boundary](docs/OCI.md#protected-release-workflow),
or the [single-VM Compose pilot](deploy/compose/README.md).

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

See [docs/PUBLIC_DISCLOSURE.md](docs/PUBLIC_DISCLOSURE.md) for the private-to-public disclosure and licensing gate, [docs/ROADMAP.md](docs/ROADMAP.md) for the evidence-gated enterprise program, [docs/POLICY_CONTROL.md](docs/POLICY_CONTROL.md) for policy root authority, [docs/CUSTODY_CONTROL.md](docs/CUSTODY_CONTROL.md) for tenant custody authority and lifecycle approvals, [docs/CONTRACTS.md](docs/CONTRACTS.md) for the versioned policy/evidence contract and migration boundary, [docs/STORAGE.md](docs/STORAGE.md) for SQLite/PostgreSQL setup, upgrade, rollback, and recovery boundaries, [docs/OPERATIONS.md](docs/OPERATIONS.md) for liveness, readiness, and shutdown behavior, [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the customer-controlled TLS and trusted-proxy boundary, [docs/OCI.md](docs/OCI.md) for the non-root reference container boundary, [deploy/compose/README.md](deploy/compose/README.md) for the single-VM pilot deployment, [docs/decisions/README.md](docs/decisions/README.md) for proposed and accepted architecture decisions, [docs/CLIENTS.md](docs/CLIENTS.md) for Codex and Claude Code setup, [docs/LIVE_CLIENT_CONFORMANCE.md](docs/LIVE_CLIENT_CONFORMANCE.md) for the real BYO-provider release gate, [docs/OIDC.md](docs/OIDC.md) for generic enterprise identity, [docs/OIDC_PROVIDER_CONFORMANCE.md](docs/OIDC_PROVIDER_CONFORMANCE.md) for the bounded external-provider reference proof, [docs/USAGE.md](docs/USAGE.md) for team/person/model cost and budget reporting, [docs/AUDIT.md](docs/AUDIT.md) for the export contract and limitations, [docs/CUSTODY.md](docs/CUSTODY.md) for optional self-hosted or AWS custody and immutable audit anchors, [docs/SECRET_CUSTODY_INVENTORY.md](docs/SECRET_CUSTODY_INVENTORY.md) for the machine-enforced active secret-ownership boundary, [docs/CEPH_RGW_CONFORMANCE.md](docs/CEPH_RGW_CONFORMANCE.md) for the first verified self-hosted reference and its exact boundary, [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) for the egress boundary, [docs/VERIFICATION.md](docs/VERIFICATION.md) for executable compatibility evidence, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request path and current trust boundary.

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

The current hardening program focuses on a minimal gateway core: policy enforcement, versioned PostgreSQL policy administration, accounting, deterministic secret egress, metadata-only audit, and OIDC JWT verification. The package boundary and policy/evidence contract are explicit, the SQLite/PostgreSQL compatibility seam is tested, and the non-root OCI reference runtime is published as a signed `v0.1.1` digest with validated SBOM/provenance and a fix-aware vulnerability gate. A disposable logical PostgreSQL backup/restore exercise now proves a narrow recovery path, but it is not production backup/PITR or DR evidence. Before an enterprise release Hormuz still needs live customer-account certification, migration of every secret class, TLS and deployment hardening, shared PostgreSQL operations, production backup/PITR, multi-instance coordination, and independent review. It is not building an organizational-memory or workflow product.

## Community and support

Read [contribution guidance](CONTRIBUTING.md) before proposing a change and
[public-alpha support](SUPPORT.md) before filing an installation or
compatibility report. Suspected vulnerabilities must follow the
[private security path](SECURITY.md), never a public issue. Participation is
governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

The evidence-grounded [launch package](docs/launch/README.md) is intentionally
marked as a non-publishable draft until the disclosure, live-provider,
repository, signed-image, community, quiet-alpha, commercial-URL, and owner-
approval gates are complete.
