# Hormuz

Hormuz is a CLI-first enterprise control plane that puts organization policy between employees' existing AI clients and model providers. It currently proxies the OpenAI Responses API used by Codex and the Anthropic Messages API used by Claude Code.

The first executable milestone enforces client, model, output-token, monthly-token, team-budget, and per-person budget rules. It records metadata-only usage in SQLite, estimates cost from configured rate cards, and keeps provider API keys on the Hormuz server rather than distributing them to employees.

The included rate cards are examples current as of August 15, 2026. Treat them as versioned configuration: verify them against provider pricing before production use and reconcile estimated spend against provider invoices.

Hormuz is alpha software. The local prototype proves routing and policy behavior; it is not yet a production-ready multi-tenant service.

## What works

- OpenAI-compatible `POST /v1/responses` proxying, including streaming.
- Anthropic-compatible `POST /v1/messages`, `/v1/messages/count_tokens`, and streaming.
- Provider model IDs by default, preserving native client model behavior; optional company aliases remain supported.
- Organization, team, and person policy overlays that can only become more restrictive.
- Model fallback, output-token caps, monthly token limits, and USD budget limits.
- Per-person attribution using unique bootstrap tokens, generic OIDC JWT access tokens, or revocable Hormuz human sessions mapped by issuer and subject.
- Input, output, cache-read, cache-write, reasoning, and normalized billable-token accounting when providers report them.
- Immutable USD rate-card version, cost-basis, and allowlisted provider-native usage snapshots for every accounted gateway outcome, plus exact-decimal, idempotent local import and honest aggregate reconciliation of complete OpenAI and Anthropic cost-report API responses.
- Metadata-only SQLite usage ledger. Prompts and responses are relayed, not persisted.
- Metadata-only JSONL audit export for usage, secret-egress, and structured-DLP evidence, with private file permissions and a SHA-256 checksum.
- Pre-provider credential, valid hyphenated US SSN, Luhn-valid card, low-confidence email, and organization dictionary detection with provider/model-scoped detect, redact, deny, or approval-required actions. Identity-derived team/person DLP overlays can narrow scope and only strengthen organization actions. Recognized OpenAI and Anthropic image/file content is denied by default when its bytes cannot be inspected; inspectable inline text documents continue through the ordinary redaction path. Optional approvals are metadata-only, non-self, exact-payload/model bound, 15-minute, and atomically single-use.
- OpenAI response storage and background mode disabled by default as enforceable provider privacy policy.
- Configuration output for installed Codex and Claude Code clients.
- A separate local governed-context repository with atomic idempotent import, verification evidence, classification/scope authorization, optimistic concurrency, metadata-only mutation/read audit export, private content export, and physical deletion controls.
- Opt-in, capability-gated lifecycle automation that promotes provisional records through configured merge, CI, review, ADR, incident, human, or validated-failure evidence; invalidates them on newer negative evidence or changed trusted source state; and runs in resumable, lease-safe, tenant-scoped batches.
- Config-independent `hormuz lifecycle` connector commands and authenticated evidence/snapshot/revalidation HTTP endpoints for trusted CI workloads, with server-owned organization scope, strict versioned schemas, idempotent retries, and metadata-only responses.
- Explicit, provider-neutral governed context packs retrieved authorization-first from that repository, with versioned retrieval/render contracts, explicit authorized freshness/provisional exclusions, expiry, supersession, trusted dependency snapshots, prompt-injection quarantine, structured contradiction outcomes, deterministic lexical ranking, and token-budget enforcement.
- Authenticated `POST /v1/context/packs` retrieval with identity-derived scope, server-owned caps, per-actor rate limiting, stable errors, and no provider or usage-ledger side effects.
- A real `hormuz_get_context` MCP stdio tool for Codex and Claude Code that reuses the authenticated Context Pack API, supports current and next-generation MCP handshakes, and cannot override employee identity or organization policy.
- A bundled 60-task synthetic context benchmark with no-memory, full-history, simple-lexical, and governed baselines; its strict lifecycle release profile is green on the frozen version-2 corpus.
- Generic OIDC discovery/JWKS verification with strict issuer, audience, expiry, asymmetric-algorithm, subject-mapping, and signing-key-rotation enforcement.
- Generic OIDC authorization-code + PKCE browser login with opaque 10-minute Hormuz access credentials, atomic refresh rotation, replay-family revocation, and fail-closed OS secure-store custody.
- Capability-gated, tenant-scoped `hormuz sessions` listing, metadata-only security-event inspection, and immediate session, employee, team, or organization revocation.
- Capability-gated, tenant-scoped `hormuz usage report` administration over the authenticated gateway, with frozen-window pagination, team/person/model/client/provider drill-downs, and mandatory metadata-only read audit.

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

For the approved employee SSO path, configure the session broker as described in [docs/OIDC.md](docs/OIDC.md), then log in without distributing either provider key:

```bash
hormuz login \
  --gateway https://hormuz.example.com \
  --profile codex \
  --client codex

hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com \
  --actor alice \
  --auth-mode session \
  --profile codex
```

The login page opens in the operating system's external browser. The CLI stores the revocable Hormuz session in macOS Keychain, Windows Credential Manager, or Linux Secret Service/KWallet through `keyring`; it refuses plaintext fallback.

Connect governed context to either existing client without placing a provider key on the employee machine:

```bash
python3 -m hormuz mcp-config codex --url http://127.0.0.1:8787
python3 -m hormuz mcp-config claude --url http://127.0.0.1:8787
```

Install the generated Codex TOML or Claude Code `.mcp.json` entry, then verify it with `codex mcp get hormuz --json` or `claude mcp get hormuz`. See [docs/MCP.md](docs/MCP.md) for the complete, secret-free setup and the distinction between a callable governed tool and future mandatory context injection.

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

Give an authorized operator the configured `usage_viewer` capability, then inspect the same metadata without granting server filesystem or database access:

```bash
hormuz usage report \
  --gateway https://hormuz.example.com \
  --profile ai-operations \
  --group-by team
```

The server derives organization scope from the credential. See [docs/USAGE_ADMIN_API.md](docs/USAGE_ADMIN_API.md) for pagination, RBAC, audit, and coverage semantics.

Import a provider cost-report snapshot and compare it with Hormuz request-time estimates without giving the Hormuz process a provider administrator key:

```bash
python3 -m hormuz --config hormuz.json billing import \
  --organization xpounder \
  --provider openai \
  --input openai-costs-page-1.json

python3 -m hormuz --config hormuz.json billing reconcile \
  --organization xpounder \
  --provider openai \
  --json
```

Provider cost is aggregate evidence, not a universal final cost per request, employee, or team. See [docs/BILLING_RECONCILIATION.md](docs/BILLING_RECONCILIATION.md) for the official report contracts, pagination, exact-decimal treatment, coverage labels, and remaining invoice boundary.

Export metadata-only audit evidence for the current month:

```bash
python3 -m hormuz --config hormuz.json audit-export \
  --kind all \
  --output hormuz-audit.jsonl
```

When an enforced DLP rule requires approval, the employee keeps using Codex or Claude Code. Hormuz returns an opaque `apr_...` request ID; a separately authorized approver inspects metadata and approves it, then the employee retries the unchanged request once:

```bash
hormuz dlp approval show apr_0123456789abcdef0123456789abcdef \
  --gateway https://hormuz.example.com \
  --profile security-approver

hormuz dlp approval approve apr_0123456789abcdef0123456789abcdef \
  --gateway https://hormuz.example.com \
  --profile security-approver
```

No prompt or matched value is returned to the approver or stored in the approval ledger. Recognized opaque images and files are not approval-eligible: the secure default denies them because Hormuz cannot inspect their bytes. See [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) for media coverage, key generation, capability configuration, exact retry semantics, and failure behavior.

Import the sample into the separate local context repository, inspect metadata, and build a pack without injecting or sending it to a provider:

```bash
python3 -m hormuz --config hormuz.json context-import \
  --records examples/context-records.jsonl \
  --actor alice \
  --policy-version engineering-v1
python3 -m hormuz --config hormuz.json context-list \
  --actor alice \
  --repository Xpounder-com/hormuz
python3 -m hormuz --config hormuz.json context-snapshot-import \
  --snapshot examples/context-lifecycle-snapshot.json \
  --actor alice \
  --policy-version engineering-lifecycle-v1
python3 -m hormuz --config hormuz.json context-pack \
  --query "How should API retries work?" \
  --organization xpounder \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main \
  --token-budget 2000 \
  --policy-version engineering-v1
python3 -m hormuz --config hormuz.json context-audit-export \
  --actor alice \
  --output hormuz-context-audit.jsonl
```

To exercise automatic promotion, first enable `context_service.lifecycle` and grant a trusted identity `context_promoter`, then use the provisional lifecycle sample:

```bash
python3 -m hormuz --config hormuz.json context-import \
  --records examples/context-lifecycle-records.jsonl \
  --actor alice
python3 -m hormuz --config hormuz.json context-snapshot-import \
  --snapshot examples/context-lifecycle-snapshot.json \
  --actor alice
python3 -m hormuz --config hormuz.json context-evidence-import \
  --evidence examples/context-evidence-commit-merged.json \
  --actor alice
python3 -m hormuz --config hormuz.json context-evidence-import \
  --evidence examples/context-evidence-ci-passed.json \
  --actor alice
python3 -m hormuz --config hormuz.json context-revalidate \
  --actor alice \
  --repository Xpounder-com/hormuz \
  --branch main
```

Lifecycle automation is disabled in the sample configuration until that capability and policy are reviewed. See [docs/CONTEXT_LIFECYCLE.md](docs/CONTEXT_LIFECYCLE.md) for the complete setup, evidence schema, invalidation/recovery behavior, and current connector boundary.

A trusted CI job or internal connector can submit the same envelopes without direct access to the Hormuz configuration or database. Give its mapped bootstrap, workload OIDC, or session identity `context_promoter`, expose its short-lived credential as `HORMUZ_TOKEN`, and call the remote gateway:

```bash
hormuz lifecycle snapshot \
  --input examples/context-lifecycle-snapshot.json \
  --gateway https://hormuz.example.com
hormuz lifecycle evidence \
  --input examples/context-evidence-ci-passed.json \
  --gateway https://hormuz.example.com
hormuz lifecycle revalidate \
  --repository Xpounder-com/hormuz \
  --branch main \
  --gateway https://hormuz.example.com
```

This authenticates who submitted the normalized attestation; it does not yet verify a GitHub webhook or independently prove the external event. See [docs/CONTEXT_LIFECYCLE_API.md](docs/CONTEXT_LIFECYCLE_API.md) for the exact connector contract, status codes, retry behavior, and trust boundary.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the evidence-gated enterprise program, [docs/decisions/README.md](docs/decisions/README.md) for proposed and accepted architecture decisions, [docs/CLIENTS.md](docs/CLIENTS.md) for provider routing, [docs/MCP.md](docs/MCP.md) for governed context in Codex and Claude Code, [docs/OIDC.md](docs/OIDC.md) for generic enterprise identity, [docs/SESSION_ADMIN_API.md](docs/SESSION_ADMIN_API.md) for tenant-scoped session control, [docs/USAGE.md](docs/USAGE.md) for team/person/model cost and budget reporting, [docs/USAGE_ADMIN_API.md](docs/USAGE_ADMIN_API.md) for authenticated tenant usage administration, [docs/BILLING_RECONCILIATION.md](docs/BILLING_RECONCILIATION.md) for provider cost imports and aggregate reconciliation, [docs/AUDIT.md](docs/AUDIT.md) for the export contract and limitations, [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) for the egress boundary, [docs/DLP_APPROVAL_API.md](docs/DLP_APPROVAL_API.md) for the approver contract, [docs/CONTEXT.md](docs/CONTEXT.md) for governed record/pack semantics, [docs/CONTEXT_LIFECYCLE.md](docs/CONTEXT_LIFECYCLE.md) for evidence-driven promotion and revalidation, [docs/CONTEXT_API.md](docs/CONTEXT_API.md) for authenticated retrieval, [docs/CONTEXT_LIFECYCLE_API.md](docs/CONTEXT_LIFECYCLE_API.md) for authenticated lifecycle mutation, [docs/VERIFICATION.md](docs/VERIFICATION.md) for executable compatibility evidence, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request path and current trust boundary.

Measure the bundled governed-context contract without a gateway configuration or provider credential:

```bash
python3 -m hormuz context-benchmark \
  --profile release \
  --iterations 5 \
  --output context-benchmark-release.json
```

See [docs/CONTEXT_BENCHMARK.md](docs/CONTEXT_BENCHMARK.md) for the corpus, formulas, evidence format, strict thresholds, and interpretation limits.

## Test

```bash
python3 -m unittest -v
```

The suite uses local fake OpenAI and Anthropic endpoints and does not need real provider credentials. The Codex test runs against an installed `codex` executable when present. The official Claude Code executable test is opt-in because `npx` may download the client:

```bash
HORMUZ_RUN_CLAUDE_CLIENT_TEST=1 python3 -m unittest -v
```

The GitHub publication gate also tests Python 3.11 through 3.14, validates the frozen benchmark and preserves CI evidence, builds and installs the distribution wheel, verifies pinned official Codex and Claude Code releases, and runs a non-blocking weekly canary against their latest releases. See [docs/VERIFICATION.md](docs/VERIFICATION.md) for the exact boundary.

## Roadmap boundary

The current milestone includes the enforcement, versioned estimate accounting, offline provider cost-report reconciliation, deterministic secret egress, a bounded structured-DLP detector/action subset, provider-format-aware opaque-media denial, replay-safe approval grants, metadata audit, local persistent context records/packs, trusted lifecycle snapshot evaluation, opt-in evidence-driven promotion/invalidation with resumable local revalidation, authenticated provider-neutral connector transport, MCP retrieval, OIDC JWT verification, and a single-node OIDC login/session kernel with tenant-scoped administrator revocation. The usage, approval, context, and session databases are deliberately local implementations; they are not the pending enterprise tenancy, HA, or KMS design. Before an enterprise release, Hormuz still needs one owner-selected real-IdP validation, SCIM-driven deprovisioning, shared multi-node revocation, remaining accepted DLP architecture, durable multi-tenant persistence, TLS and deployment hardening, signed or externally immutable audit retention, authenticated provider polling and final invoice/credit reconciliation, broader provider conformance coverage, source-specific lifecycle collectors and signed-event verification, remaining decay policy, cache, mandatory injection, and outcome writeback.
