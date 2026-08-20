# Compatibility and support boundary

This document translates Hormuz's machine-readable compatibility matrix into an
operator-facing support statement. Hormuz is alpha software. An entry being
tested does not make the overall service production-ready or enterprise-ready.

The authoritative contract is
[`compatibility/compatibility-matrix.json`](../compatibility/compatibility-matrix.json).
CI validates it against the pinned client fixture, Python project metadata,
blocking Python matrix, gateway routes, evidence references, and container base.
The validation evidence contains identifiers, versions, counts, support levels,
and hashes only; it contains no prompts, responses, credentials, configuration
values, or customer content.

The built-in context repository, lifecycle, MCP, benchmark, and automatic
injection entries are deprecated experimental compatibility evidence under
[ADR 0008](decisions/0008-gateway-product-boundary.md). They are not supported
Hormuz release gates. Ordinary provider traffic does not initialize their
content-bearing SQLite database.

## Meaning of each support level

| Level | Meaning |
| --- | --- |
| `release_tested` | The exact version or artifact passes a blocking repository gate in the stated environment. The claim does not extend to other versions, hosts, or production operations. |
| `protocol_tested` | The implemented protocol subset passes against a bounded local standards-shaped fake. It is not live vendor conformance. |
| `development_only` | The surface is for local single-node development and deterministic testing. |
| `unsupported` | Hormuz makes no current compatibility or production-support commitment. |
| `pending_owner_decision` | Implementation is intentionally waiting for an owner-controlled architecture decision. |

No current matrix entry has `production_supported: true`, and the matrix must
fail validation if the alpha silently gains such a claim.

## Current matrix

| Surface | Current evidence | Explicit boundary |
| --- | --- | --- |
| Codex CLI `0.147.0` | Blocking executable test on `ubuntu-latest` Linux x64 using Node.js `24.19.0` and npm `11.17.0` | Other client versions and macOS/Windows runtime behavior are not blocking compatibility claims. |
| Claude Code `2.1.233` | Blocking executable test on `ubuntu-latest` Linux x64 using the same exact toolchain | Other client versions and macOS/Windows runtime behavior are not blocking compatibility claims. |
| CPython `3.11`–`3.14` | Complete source suite on every listed version; clean wheel installation on `3.12` | This is Linux CI evidence, not certification of every OS or Python distribution. |
| OpenAI Responses subset | Blocking `POST /v1/responses` and direct-query `POST /v1/responses/compact` tests through a protocol-shaped loopback fake, plus opt-in content-free live connectivity, synthetic-secret redaction, governed-context compaction, and pinned Codex `0.147.0` observations on macOS 26.2 arm64 | The blocking support level remains `protocol_tested`. The compaction path proves user-priority injection, post-injection DLP, no generation-only output-cap field, conservative local budget reservation, usage accounting, required-mode denial without direct user text against the fake, and one fixed live governed-context compact observation. OpenAI exposes no hard output cap for compaction, so the configured allowance is reservation-only and actual provider usage can exceed it. This is not continuation binding. The live observations are not complete DLP, provider SLA, quota, retention, residency, availability, every-model, or production-readiness proof. |
| Anthropic Messages subset | `POST /v1/messages`, `POST /v1/messages/count_tokens`, and policy-filtered `GET /v1/models` through local tests | Token-count tests prove the gateway does not add the generation-only `max_tokens` field. No live Anthropic conformance, quota, retention, residency, or availability proof. Model discovery does not call Anthropic. |
| Generic OIDC | Discovery/JWKS, mapped JWTs, authorization code plus PKCE, refresh rotation, and replay revocation against a standards-shaped fake IdP, plus shared PostgreSQL session persistence | No real vendor-specific IdP is certified. SCIM and production key custody remain open. |
| SQLite | Single-process local and CI behavior | Development only; not hosted tenancy, concurrency, HA, backup, or restore evidence. |
| PostgreSQL | Opt-in schema-version-4 usage/cost/budget, desired-state identity and policy, authorization-version, human-session, DLP-approval, and security-event repositories; packaged checksummed migrations and a digest-pinned PostgreSQL `16.14` two-tenant observation, including competing budget writers, session refreshers, and approval retries | `development_only`: SCIM, cutover backfill, policy rollout coordination, pooling, backup/PITR, restore, deletion, KMS, HA, and production-persistence proof remain open. The deprecated context experiment is deliberately excluded from the production persistence plan. |
| Python package `0.1.0` | Reproducible wheel/source build and clean Linux installation | Artifact installation does not prove a production deployment. |
| OCI `linux/amd64` and `linux/arm64` | Exact-source BuildKit `0.32.2` reproducibility; restricted runtime smoke on amd64 | A single-node alpha artifact, not production TLS, HA, backup, disaster recovery, or support evidence. |

Separately from the blocking matrix, an opt-in local test passed on macOS 26.2
arm64 with the pinned Codex CLI `0.147.0`, Claude Code `2.1.233`, and the real
macOS Keychain. It proves profile-authenticated inference plus a model-requested
Hormuz MCP context call through each stock client. This deliberately remains
non-blocking evidence: it does not widen the matrix to all macOS hosts, other
client versions, Linux/Windows secure stores, or a production IdP.

The weekly latest-client canary is intentionally dynamic and non-blocking. It
detects likely drift; it does not automatically expand the release-tested client
versions. Changing a pinned client or its integrity lock is an explicit
compatibility and supply-chain review.

The OpenAI observations are recorded in
[`evidence/provider-conformance-openai-2026-08-19.json`](../evidence/provider-conformance-openai-2026-08-19.json)
and
[`evidence/codex-openai-live-2026-08-19.json`](../evidence/codex-openai-live-2026-08-19.json).
The fixed synthetic-secret redaction observation is recorded in
[`evidence/provider-redaction-conformance-openai-2026-08-19.json`](../evidence/provider-redaction-conformance-openai-2026-08-19.json).
The fixed governed-context compaction observation is recorded in
[`evidence/provider-compaction-conformance-openai-2026-08-20.json`](../evidence/provider-compaction-conformance-openai-2026-08-20.json).
The bounded synthetic PostgreSQL RLS observation is recorded in
[`evidence/postgres-rls-feasibility-2026-08-20.json`](../evidence/postgres-rls-feasibility-2026-08-20.json).
The accepted schema-v4 accounting, identity/session, policy/approval, and runtime-isolation observation is recorded
in [`evidence/postgres-foundation-integration-2026-08-20.json`](../evidence/postgres-foundation-integration-2026-08-20.json).
The reusable stock-client observation is separately recorded in
[`evidence/client-conformance-codex-openai-2026-08-19.json`](../evidence/client-conformance-codex-openai-2026-08-19.json).
They do not change `live_provider_conformance_verified`: that combined flag stays
false until a defined gate independently covers both OpenAI and Anthropic.

## Validate locally

From a source checkout:

```bash
python scripts/compatibility_contract.py \
  --matrix compatibility/compatibility-matrix.json \
  --project-root .
```

To create content-free evidence without overwriting an existing file:

```bash
python scripts/compatibility_contract.py \
  --matrix compatibility/compatibility-matrix.json \
  --project-root . \
  --output /tmp/hormuz-compatibility-evidence.json
```

The output file is created with mode `0600` and exclusive-create semantics.

## Promotion rule

A surface moves to `release_tested` only when its exact version or artifact is
bound to a blocking test, the tested environment is named, evidence references
resolve inside the repository, and limitations remain explicit. Live providers,
real IdPs, production persistence, and production deployment require their own
external or operational evidence; local fakes and green source tests cannot
promote them.

This matrix is one input to issue #9. It does not satisfy independent security
review, production operations, or product-owner release sign-off.
