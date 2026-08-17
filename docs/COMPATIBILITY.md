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
| OpenAI Responses subset | `POST /v1/responses` through a protocol-shaped loopback fake | No live OpenAI conformance, quota, retention, residency, or availability proof. `/v1/responses/compact` is implemented but excluded from the current tested compatibility claim. |
| Anthropic Messages subset | `POST /v1/messages`, `POST /v1/messages/count_tokens`, and policy-filtered `GET /v1/models` through local tests | No live Anthropic conformance, quota, retention, residency, or availability proof. Model discovery does not call Anthropic. |
| Generic OIDC | Discovery/JWKS, mapped JWTs, authorization code plus PKCE, refresh rotation, and replay revocation against a standards-shaped fake IdP | No real vendor-specific IdP is certified. SCIM, shared revocation, and production key custody remain open. |
| SQLite | Single-process local and CI behavior | Development only; not hosted tenancy, concurrency, HA, backup, or restore evidence. |
| PostgreSQL | Proposed hybrid shared-schema RLS plus dedicated option | Unsupported pending owner approval of ADR 0002; there is no schema or isolation evidence. |
| Python package `0.1.0` | Reproducible wheel/source build and clean Linux installation | Artifact installation does not prove a production deployment. |
| OCI `linux/amd64` and `linux/arm64` | Exact-source BuildKit `0.32.2` reproducibility; restricted runtime smoke on amd64 | A single-node alpha artifact, not production TLS, HA, backup, disaster recovery, or support evidence. |

The weekly latest-client canary is intentionally dynamic and non-blocking. It
detects likely drift; it does not automatically expand the release-tested client
versions. Changing a pinned client or its integrity lock is an explicit
compatibility and supply-chain review.

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
