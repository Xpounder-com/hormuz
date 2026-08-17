# Hormuz

Hormuz is a CLI-first enterprise control plane that puts organization policy between employees' existing AI clients and model providers. It currently proxies the OpenAI Responses API used by Codex and the Anthropic Messages API used by Claude Code.

The first executable milestone enforces client, model, output-token, monthly-token, team-budget, and per-person budget rules. It records metadata-only usage in SQLite, estimates cost from configured rate cards, and keeps provider API keys on the Hormuz server rather than distributing them to employees.

The included rate cards are examples current as of August 15, 2026. Treat them as versioned configuration: verify them against provider pricing before production use and reconcile estimated spend against provider invoices.

Hormuz is alpha software. The local prototype proves routing and policy behavior; it is not yet a production-ready multi-tenant service. The machine-validated [compatibility matrix](docs/COMPATIBILITY.md) separates exact blocking-CI evidence from protocol-shaped local fakes, development-only storage, unsupported production surfaces, and owner-pending architecture.

## What works

- OpenAI-compatible `POST /v1/responses` proxying, including streaming.
- Anthropic-compatible `POST /v1/messages`, `/v1/messages/count_tokens`, and streaming.
- Provider model IDs by default, preserving native client model behavior; optional company aliases remain supported.
- Authenticated Claude Code model discovery that exposes only Claude-compatible aliases allowed by the employee's effective policy, without calling a provider or creating usage charges.
- Organization, team, and person policy overlays that can only become more restrictive.
- Model fallback, output-token caps, monthly token limits, and USD budget limits.
- Per-person attribution using unique bootstrap tokens, generic OIDC JWT access tokens, or revocable Hormuz human sessions mapped by issuer and subject.
- Input, output, cache-read, cache-write, reasoning, and normalized billable-token accounting when providers report them.
- Immutable USD rate-card version, cost-basis, and allowlisted provider-native usage snapshots for every accounted gateway outcome, plus exact-decimal, idempotent offline or authenticated OpenAI/Anthropic cost-report ingestion and honest aggregate reconciliation.
- Content-free control-plane observability: the SQLite usage/security ledgers, approvals, and built-in HTTP access logs retain bounded metadata but not prompts, responses, raw request targets/queries/headers, OAuth codes, filenames, source content, matched values, credentials, or internal provider exceptions. A versioned exact-column manifest makes routine telemetry stores fail closed at startup if an unreviewed field appears. Prompts and responses are inspected and relayed transiently, not persisted in those surfaces; the governed-context repository is a separate, intentionally content-bearing store.
- Fail-closed, 1 MiB-bounded configuration parsing that rejects duplicate members, non-standard numbers, invalid UTF-8, and unknown root/nested fields without reflecting attacker-chosen values; exact-file SHA-256 enforcement can bind a rollout to a separately approved configuration artifact.
- Metadata-only JSONL audit export for usage, secret-egress, structured-DLP, and governed-context evidence, with private file permissions, raw checksums, and an optional versioned hash chain verified against an externally retained head/count.
- Exact-source `linux/amd64` and `linux/arm64` OCI rebuilding exports one committed revision twice per platform through a reviewed digest-pinned BuildKit, validates every referenced OCI descriptor and layer, requires each platform's layouts to be byte-identical, and emits deterministic image tars plus content-free digest evidence before any artifact is accepted.
- Pre-provider JSON string value/key, provider URL-query, and allowlisted provider-header inspection for credentials, valid hyphenated US SSNs, Luhn-valid cards, low-confidence email syntax, and organization dictionaries, with provider/model-scoped detect, redact, deny, or approval-required actions. Queries receive one strict UTF-8 form decode followed by bounded nested percent-decoding through at most three inspection views. Keys, raw queries, and forwarded header values are never mutated: enforced redaction findings in any of them fail closed. Identity-derived team/person DLP overlays can narrow scope and only strengthen organization actions. Bounded UTF-8 base64/base64url strings and textual data URIs, including ASCII MIME whitespace inside the encoded payload, are decoded, recursively inspected, and safely re-encoded after value transformation. Recognized encoded compression/archive containers and OpenAI/Anthropic image/file content are denied by default when their bytes cannot be inspected; inspectable inline text documents continue through the ordinary redaction path. Optional approvals are metadata-only, non-self, exact-request-material/model bound, 15-minute, and atomically single-use.
- Offline evaluation of one configured DLP detector against a strict organization-labeled JSONL corpus, producing only versioned aggregate confusion metrics and never retaining cases, samples, matches, or corpus hashes.
- OpenAI response storage and background mode disabled by default as enforceable provider privacy policy.
- Configuration output for installed Codex and Claude Code clients.
- A separate local governed-context repository with atomic idempotent import, verification evidence, classification/scope authorization, optimistic concurrency, metadata-only mutation/read audit export, private content export, and physical deletion controls.
- Opt-in, capability-gated lifecycle automation that promotes provisional records through configured merge, CI, review, ADR, incident, human, or validated-failure evidence; invalidates them on newer negative evidence or changed trusted source state; and runs in resumable, lease-safe, tenant-scoped batches.
- Config-independent `hormuz lifecycle` connector commands and authenticated evidence/snapshot/revalidation HTTP endpoints for trusted CI workloads, with server-owned organization scope, strict versioned schemas, idempotent retries, and metadata-only responses.
- Explicit, provider-neutral governed context packs retrieved authorization-first from that repository, with versioned retrieval/render contracts, explicit authorized freshness/provisional exclusions, expiry, supersession, trusted dependency snapshots, prompt-injection quarantine, structured contradiction outcomes, deterministic lexical ranking, and token-budget enforcement.
- Disabled-by-default automatic injection of verified organization/team/person Context Packs into user-priority OpenAI Responses and Anthropic Messages content, including Anthropic token counts and exact administrator-granted repository/branch/trusted-revision selection through the official clients, with monotonic repository/classification/model caps, consumed-header DLP, generation-budget accounting, and metadata-only pack lineage.
- Authenticated `POST /v1/context/packs` retrieval with identity-derived scope, server-owned caps, per-actor rate limiting, stable errors, and no provider or usage-ledger side effects.
- A real `hormuz_get_context` MCP stdio tool for Codex and Claude Code that reuses the authenticated Context Pack API, supports current and next-generation MCP handshakes, and cannot override employee identity or organization policy.
- An opt-in local macOS proof in which pinned stock Codex and Claude Code binaries authenticate inference through a real Keychain-backed Hormuz profile, execute a model-requested `hormuz_get_context` call through the same profile, and return the authorized pack without receiving the provider credential.
- A bundled 60-task synthetic context benchmark with no-memory, full-history, simple-lexical, and governed baselines; its strict lifecycle release profile is green on the frozen version-2 corpus.
- Generic OIDC discovery/JWKS verification with strict issuer, audience, expiry, asymmetric-algorithm, subject-mapping, and signing-key-rotation enforcement.
- Generic OIDC authorization-code + PKCE browser login with opaque 10-minute Hormuz access credentials, atomic refresh rotation, replay-family revocation, and fail-closed OS secure-store custody.
- Capability-gated, tenant-scoped `hormuz sessions` listing, metadata-only security-event inspection, and immediate session, employee, team, or organization revocation.
- Capability-gated, tenant-scoped `hormuz usage report` administration over the authenticated gateway, with frozen-window pagination, team/person/model/client/provider drill-downs, mandatory metadata-only read audit, and an opt-in version-3 content-free latency histogram view that leaves the exact version-2 contract unchanged.
- An explicit kernel accept-backlog hint, pre-thread connection ceiling, absolute request-header and request-body deadlines, exact `Content-Length` body ingestion, versioned content-free liveness/readiness endpoints, atomic parsed-request capacity with saturation-aware readiness, a total provider-response relay deadline, and idempotent `SIGTERM` draining with a bounded in-flight request grace period.

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

For a controlled rollout, record the exact digest printed by `doctor` in the separate deployment definition and supply it as `HORMUZ_CONFIG_SHA256` when starting `serve`; the local quick start above leaves that optional binding unset.

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
python3 -m hormuz mcp-config codex \
  --url https://hormuz.example.com \
  --profile codex
python3 -m hormuz mcp-config claude \
  --url https://hormuz.example.com \
  --profile claude
```

Install the generated Codex TOML or Claude Code `.mcp.json` entry, then verify it with `codex mcp get hormuz --json` or `claude mcp get hormuz`. The generated entries contain no access or refresh credential; the long-running adapter obtains a current short-lived credential from the OS secure store for each tool call. Workload-token environment mode remains available for CI and compatibility. See [docs/MCP.md](docs/MCP.md) for the complete setup. Automatic gateway injection is a separate, disabled-by-default policy described in [docs/CONTEXT_INJECTION.md](docs/CONTEXT_INJECTION.md); it does not require the model to call MCP.

When an administrator enables repository-specific automatic context, generate an exact project profile rather than trusting a working-directory string:

```bash
hormuz --config /etc/hormuz/hormuz.json client-config codex \
  --url https://hormuz.example.com \
  --repository Xpounder-com/hormuz \
  --branch main \
  --revision abc123
```

The same flags work with `client-config claude`. The effective context policy must independently grant the repository; Hormuz consumes the generated scope headers and never forwards them to a model provider.

Configuration is immutable for a running process. Validate a candidate with `hormuz --config CANDIDATE doctor`, separately retain its printed SHA-256, require that value with `HORMUZ_CONFIG_SHA256` during the readiness-gated replacement rollout, and retain the previous image/configuration/digest set for rollback. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for strict parsing, exact-file binding, secret, change, and rollback semantics.

For process probes, use `GET /health/live` and `GET /health/ready`. Readiness returns `503 busy` while parsed-request capacity is full and `503 draining` after shutdown begins. Health probes do not consume application capacity, but they require one of the bounded accepted-connection slots. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for the exact backlog, connection, header, body, parsed-request, provider-response, and shutdown limits plus the current deployment boundary.

For a repeatable single-node container checkpoint, build the pinned non-root image and run its executable restricted-runtime smoke test:

```bash
docker build --pull=false --tag hormuz:local .
python3 scripts/container_smoke.py --image hormuz:local
```

The reference command, data-volume contract, secret boundary, exact-source OCI reproducibility gate, SBOM/vulnerability gate, and remaining production limitations are documented in [docs/CONTAINER.md](docs/CONTAINER.md). Tag-driven private GHCR publication, exact-digest keyless signing, signed SLSA provenance, evidence assets, and digest-based rollback are specified in [docs/RELEASES.md](docs/RELEASES.md); the workflow is not evidence that an image has already been published.

The versioned [threat model](docs/THREAT_MODEL.md) binds current gateway trust boundaries to concrete controls and explicit open release gates. CI validates its STRIDE and incident-scenario coverage and emits content-free aggregate evidence. This internal model is not an independent penetration test, and it cannot make the alpha enterprise-ready while open or partially mitigated threats remain.

The [incident-response contract](docs/INCIDENT_RESPONSE.md) binds those seven scenarios to exact repository-local regressions and emits a private content-free evidence file only after all pass. The catalog explicitly keeps production exercises, named on-call assignments, external communications, and enterprise readiness false. These checks prove bounded control behavior; they are not live provider, IdP, multi-tenant, disaster-recovery, privacy, or customer-communications exercises.

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

Add `--include-latency` to opt into tenant-scoped gateway, policy, provider,
and injected-context latency histograms. The flag reports measurement inputs;
it does not configure or claim SLO targets.

The server derives organization scope from the credential. See [docs/USAGE_ADMIN_API.md](docs/USAGE_ADMIN_API.md) for pagination, RBAC, audit, and coverage semantics.

Fetch a provider cost-report snapshot through the fixed official endpoint. Inject the provider admin key into the operator process from a secret manager; Hormuz accepts only the environment-variable name and never stores the key or raw provider response:

```bash
python3 -m hormuz --config hormuz.json billing fetch \
  --organization xpounder \
  --provider openai \
  --start 2026-08-01 \
  --end 2026-08-16
```

Or import an already downloaded snapshot without giving the Hormuz process a provider administrator key:

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

Evaluate a configured detector offline before considering a lower-confidence policy promotion:

```bash
hormuz --config hormuz.json dlp evaluate \
  --rule-id email_address \
  --corpus-id security-email-2026-08-v1 \
  --protocol openai \
  --model gpt-5.5 \
  --input /secure/evaluations/email-labeled.jsonl \
  --output /secure/evidence/email-evaluation.json
```

The input corpus is sensitive and stays local. The private `0600` result contains only detector/policy versions, scope, aggregate case counts, a confusion matrix, and derived metrics; it does not automatically change policy. See [docs/DLP_EVALUATION.md](docs/DLP_EVALUATION.md) for the strict corpus contract and promotion boundary.

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

After verified records exist, an administrator can enable bounded automatic injection for selected clients and model aliases under `policies.organization.context_injection`. Employees then keep using ordinary Codex and Claude Code prompts; Hormuz retrieves the pack, renders it at user priority, applies DLP to the complete request, and records content-free lineage. The sample remains `off`. See [docs/CONTEXT_INJECTION.md](docs/CONTEXT_INJECTION.md) for modes, configuration, failure behavior, and the current endpoint/scope boundary.

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

See [docs/ROADMAP.md](docs/ROADMAP.md) for the evidence-gated enterprise program, [docs/decisions/README.md](docs/decisions/README.md) for proposed and accepted architecture decisions, [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for strict startup and rollout semantics, [docs/CLIENTS.md](docs/CLIENTS.md) for provider routing, [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) for the machine-enforced support boundary, [docs/MCP.md](docs/MCP.md) for explicit governed-context retrieval in Codex and Claude Code, [docs/CONTEXT_INJECTION.md](docs/CONTEXT_INJECTION.md) for automatic user-priority injection, [docs/OIDC.md](docs/OIDC.md) for generic enterprise identity, [docs/SESSION_ADMIN_API.md](docs/SESSION_ADMIN_API.md) for tenant-scoped session control, [docs/USAGE.md](docs/USAGE.md) for team/person/model cost and budget reporting, [docs/USAGE_ADMIN_API.md](docs/USAGE_ADMIN_API.md) for authenticated tenant usage administration, [docs/BILLING_RECONCILIATION.md](docs/BILLING_RECONCILIATION.md) for provider cost imports and aggregate reconciliation, [docs/AUDIT.md](docs/AUDIT.md) for the export contract and limitations, [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) for the egress boundary, [docs/DLP_EVALUATION.md](docs/DLP_EVALUATION.md) for organization-labeled detector measurement, [docs/DLP_APPROVAL_API.md](docs/DLP_APPROVAL_API.md) for the approver contract, [docs/CONTEXT.md](docs/CONTEXT.md) for governed record/pack semantics, [docs/CONTEXT_LIFECYCLE.md](docs/CONTEXT_LIFECYCLE.md) for evidence-driven promotion and revalidation, [docs/CONTEXT_API.md](docs/CONTEXT_API.md) for authenticated retrieval, [docs/CONTEXT_LIFECYCLE_API.md](docs/CONTEXT_LIFECYCLE_API.md) for authenticated lifecycle mutation, [docs/OPERATIONS.md](docs/OPERATIONS.md) for process health and graceful shutdown, [docs/VERIFICATION.md](docs/VERIFICATION.md) for executable compatibility evidence, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the request path and current trust boundary.

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

The GitHub publication gate also tests Python 3.11 through 3.14, validates the frozen benchmark and versioned compatibility/threat-model contracts with content-free evidence, hash-verifies the complete six-wheel cross-platform Python build toolchain, disables backend re-resolution, builds each exact commit twice, requires byte-identical wheels and canonical source archives, installs the verified wheel, proves two clean exact-source OCI layouts byte-identical independently for `linux/amd64` and `linux/arm64`, verifies official Codex and Claude Code releases through a 16-package SHA-512 integrity lock under exact Node/npm versions, and scans the restricted container. A separate tag-only workflow isolates and re-runs client compatibility alongside the release gates before publication, signing, attestation, and private-image verification. The weekly latest-client canary remains intentionally dynamic and non-blocking. See [docs/RELEASES.md](docs/RELEASES.md) and [docs/VERIFICATION.md](docs/VERIFICATION.md) for the exact boundary.

## Roadmap boundary

The current milestone includes the enforcement, versioned estimate accounting, operator-run authenticated and offline provider cost-report ingestion, aggregate reconciliation, deterministic secret egress, bounded encoded-text inspection, a bounded structured-DLP detector/action subset, provider-format-aware opaque-media denial, replay-safe approval grants, metadata audit, local persistent context records/packs, trusted lifecycle snapshot evaluation, opt-in evidence-driven promotion/invalidation with resumable local revalidation, authenticated provider-neutral connector transport, MCP retrieval, and bounded disabled-by-default automatic injection for verified unscoped or exact administrator-granted repository context, plus OIDC JWT verification and a single-node OIDC login/session kernel with tenant-scoped administrator revocation. The usage, approval, context, and session databases are deliberately local implementations; they are not the pending enterprise tenancy, HA, or KMS design. Before an enterprise release, Hormuz still needs one owner-selected real-IdP validation, SCIM-driven deprovisioning, shared multi-node revocation, remaining accepted DLP architecture, durable multi-tenant persistence, TLS and deployment hardening, signed or externally immutable audit retention, scheduled provider polling with hosted credential custody, final invoice/credit reconciliation, broader provider conformance coverage, source-specific lifecycle collectors and signed-event verification, remaining decay policy, automatic trusted repository discovery, continuation binding, cache, accepted-task outcome evidence, and outcome writeback.
