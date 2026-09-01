# Hormuz

[![CI](https://github.com/Xpounder-com/hormuz/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Xpounder-com/hormuz/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Xpounder-com/hormuz)](https://github.com/Xpounder-com/hormuz/releases)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](pyproject.toml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Self-hosted AI policy, usage, and evidence control for Codex and Claude Code.**

Keep the AI clients people already use. Put organization policy between those
clients and model providers.

Hormuz is a CLI-first gateway that authenticates the person and team behind each
request, applies model and budget policy, controls secret egress, routes allowed
traffic to OpenAI or Anthropic, and records metadata-only evidence. Provider
credentials remain on the Hormuz service; prompts and responses are relayed, not
written to the usage database.

> [!IMPORTANT]
> Hormuz 1.0 is the first stable CLI and policy/evidence-contract release line.
> Release qualification included five isolated internal repetitions of one exact
> offline policy workflow plus exact-byte candidate custody. That bounded result
> does not prove external human usability, blanket production fitness, customer
> SLA coverage, provider billing accuracy, or independent security review.

[Website](https://usehormuz.github.io/) ·
[Recorded demo](https://usehormuz.github.io/demo/) ·
[Enterprise evaluation](https://usehormuz.github.io/enterprise/)

[Benefits](#why-hormuz) · [Quickstart](#quickstart) ·
[Usage reporting](#usage-visibility-without-content-capture) ·
[Documentation](#documentation) · [Contributing](#contributing-and-community)

## Why Hormuz

| Benefit | What Hormuz provides |
| --- | --- |
| Preserve developer workflows | Codex and Claude Code continue to use their native OpenAI- and Anthropic-compatible protocols. |
| Centralize AI policy | Enforce allowed clients and models, policy-bounded one-hop capacity failover, output caps, monthly token limits, and USD budgets at organization, team, and person scope. |
| Understand adoption and spend | Group current-month requests, tokens, and estimated cost by organization, team, person, model, client, or provider. |
| Attribute usage responsibly | Bind each request to a unique human or workload identity and preserve event-time team attribution without storing prompts or responses. |
| Reduce secret-egress risk | Detect, redact, or deny configured credentials and high-confidence secret formats before provider serialization. |
| Keep control in your environment | Self-host the gateway and metadata store, keep provider keys server-side, and export versioned evidence for independent analysis. |

Hormuz is designed for usage governance, not employee surveillance. Token volume
and estimated spend describe consumption; they are not measures of productivity,
quality, or individual performance.

<a id="try-the-real-gateway-without-a-provider-account"></a>

## Quickstart

The provider-free demo exercises the real HTTP gateway, policy, redaction,
request-attempt, and SQLite evidence paths with disposable loopback providers:

~~~bash
git clone --branch v1.0.0 --depth 1 https://github.com/Xpounder-com/hormuz.git
cd hormuz
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz demo
~~~

The commands above select the stable v1.0.0 source tag. Installation downloads
Python dependencies; the demo itself uses only disposable local loopback
providers. No OpenAI or Anthropic account is required. A successful run reports:

~~~text
PASS allowed request reached the loopback provider simulator
PASS unapproved model was rerouted and output-capped
PASS detected secret was redacted before provider egress
PASS denied request made no provider call
PASS content-free evidence validated: 4 usage events, 1 security event
PASS external provider calls: 0 (3 loopback simulator calls)
~~~

Try the separate zero-network policy-administration workflow:

~~~bash
hormuz policy demo
~~~

Both demos remove their temporary state by default. They are executable product
tours, not provider-compatibility or production-deployment certifications. See
[policy administrator usability](docs/POLICY_ADMIN_USABILITY.md) for the exact
v1 repeatability and custody boundary.

## Usage visibility without content capture

Hormuz records one metadata-only event for each accounted generation attempt.
The event includes the organization, person, team, identity type, client,
provider, requested and routed model, provider-reported token categories,
configured-rate-card estimate, policy outcome, status, and redaction count.

Use the built-in report to answer common operational questions:

| Question | Command |
| --- | --- |
| How much is each person using? | `hormuz --config hormuz.json status --group-by person` |
| Which models drive token consumption and cost? | `hormuz --config hormuz.json status --group-by model` |
| What is one team's model mix? | `hormuz --config hormuz.json status --group-by model --team engineering` |
| Which providers does one identity use? | `hormuz --config hormuz.json status --group-by provider --actor alice` |
| How can another tool consume the report? | `hormuz --config hormuz.json status --group-by person --json` |

Rows include request outcomes, input and output tokens, cache-read and
cache-write tokens, reasoning tokens, total tokens, estimated cost, active
actors, redactions, and applicable budget utilization.

<details>
<summary>Example metadata-only JSON report (synthetic)</summary>

~~~json
{
  "schema_id": "hormuz.usage-report",
  "schema_version": 1,
  "month": "current",
  "group_by": "person",
  "filters": {
    "actor_id": null,
    "team_id": null
  },
  "cost_basis": "configured_rate_card_estimate",
  "allocation_basis": "direct_gateway_request",
  "coverage": "gateway_captured_requests_only",
  "rows": [
    {
      "scope_id": "alice",
      "scope_name": "Alice Example",
      "team_id": "engineering",
      "team_name": "Engineering",
      "requests": 3,
      "succeeded": 2,
      "failed": 0,
      "denied": 1,
      "rate_limited": 0,
      "input_tokens": 120,
      "output_tokens": 30,
      "cache_read_tokens": 20,
      "cache_write_tokens": 0,
      "reasoning_tokens": 7,
      "total_tokens": 150,
      "cost_microusd": 120,
      "cost_usd": 0.00012,
      "budget_usd": 500.0,
      "budget_remaining_usd": 499.99988,
      "budget_used_percent": 0.000024,
      "active_actors": 1,
      "redactions": 1
    }
  ]
}
~~~

</details>

Reports cover the current UTC month and only requests captured by Hormuz.
Per-person attribution requires a unique identity for every human, service
account, and CI workload; shared credentials collapse attribution. Costs are
configured-rate-card estimates until separately reconciled with provider
invoices. Read [usage, cost, and budget reporting](docs/USAGE.md) for the exact
field semantics and coverage limits.

## What works

### Gateway, identity, and policy

- OpenAI-compatible `POST /v1/responses` proxying, including streaming.
- Anthropic-compatible `POST /v1/messages`, `/v1/messages/count_tokens`, and
  streaming.
- Provider model IDs by default, with optional organization aliases,
  policy-driven fallback routing, and opt-in, policy-bounded one-hop failover
  after explicit provider capacity rejection.
- Incremental provider streaming plus content-free header, first-byte, total
  latency, and byte-count evidence for each accounted egress.
- Organization, team, and person policy overlays that can only become more
  restrictive.
- Output-token caps, monthly token limits, USD budgets, and atomic reservations
  that close the concurrent-request budget race.
- Unique bootstrap identities and generic OIDC JWT access-token mapping with
  strict discovery, issuer, audience, expiry, asymmetric-signature, subject, and
  signing-key-rotation checks.
- Immutable PostgreSQL-backed policy documents with authenticated bootstrap,
  semantic comparison, preview, audited activation, history, and rollback.

### Usage, evidence, and security

- Per-person, team, model, client, provider, and organization reporting.
- Input, output, cache-read, cache-write, and reasoning-token accounting when
  providers report those values.
- SQLite metadata storage by default, with an optional PostgreSQL adapter for
  the same usage and evidence contract.
- Metadata-only JSONL audit export with private file permissions and a SHA-256
  checksum.
- Per-organization, digest-linked audit chains with explicit recovery epochs
  and optional external Object Lock checkpoints.
- Pre-provider secret redaction or denial without retaining matched values.
- OpenAI response storage and background mode disabled by default as enforceable
  provider policy.
- Versioned content-free contracts and a strict durable-data inventory.

### Packaging and operations

- Python 3.11 through 3.14 in blocking Linux CI.
- A deterministic, non-root Linux `amd64` OCI image identified by immutable
  digest, with keyless Cosign signing, CycloneDX SBOM, bounded provenance, and
  vulnerability and reproducibility gates.
- A provider-free single-VM Docker Compose evaluation profile.
- An optional Kubernetes and Helm multi-replica reference with customer-supplied
  immutable inputs and default-deny network policy.
- Optional PostgreSQL compatibility, migration, row-security, pooling,
  interruption, backup/restore, and bounded HA reference proofs.
- Versioned liveness and readiness endpoints plus graceful shutdown.

The included rate cards are examples current as of August 15, 2026. Verify them
against provider pricing before production use and reconcile estimates against
provider invoices before describing spend as final.

## How it works

~~~text
Codex / Claude Code
        |
        v
authenticated person + team
        |
        v
policy: allow / deny / reroute / cap / reserve budget
        |
        v
secret control: allow / redact / deny
        |
        v
OpenAI / Anthropic

Every accounted outcome -> versioned metadata-only evidence
~~~

Hormuz governs provider-bound requests and their organizational usage evidence.
It is not an identity provider, model, organizational-memory system, metadata
compiler, provider billing authority, or employee-productivity system.

See [architecture](docs/ARCHITECTURE.md) for request flow, trust boundaries, and
component ownership.

## Configure providers and clients

Hormuz requires Python 3.11 or newer. Start from the example configuration and
keep all credentials outside source control:

~~~bash
cp config.example.json hormuz.json
export HORMUZ_TOKEN="replace-with-a-long-random-identity-token"
export OPENAI_API_KEY="your-company-openai-key"
export ANTHROPIC_API_KEY="your-company-anthropic-key"
python3 -m hormuz --config hormuz.json doctor
python3 -m hormuz --config hormuz.json serve
~~~

In another terminal, print the settings for an existing AI client:

~~~bash
python3 -m hormuz --config hormuz.json client config codex
python3 -m hormuz --config hormuz.json client config claude
~~~

People authenticate to Hormuz with unique identities. Hormuz removes the Hormuz
credential before egress and authenticates upstream with the organization's
provider key. Continue with the [Codex and Claude Code client guide](docs/CLIENTS.md).

## Policy workflow

Create and validate a policy locally before touching provider or managed
deployment state:

~~~bash
python3 -m hormuz policy templates
python3 -m hormuz --config hormuz.json policy create \
  --template standard \
  --output engineering-standard.json
python3 -m hormuz --config hormuz.json policy validate engineering-standard.json
~~~

Review behavior and then apply with an optional optimistic-concurrency guard:

~~~bash
python3 -m hormuz --config hormuz.json policy compare engineering-standard.json \
  --organization xpounder
python3 -m hormuz --config hormuz.json policy preview engineering-standard.json \
  --organization xpounder \
  --actor alice \
  --client codex \
  --protocol openai \
  --model gpt-5.4-mini \
  --max-output-tokens 1000
python3 -m hormuz --config hormuz.json policy apply engineering-standard.json \
  --organization xpounder \
  --if-active sha256:...
~~~

Inspect or reverse managed state with `policy show`, `policy history`,
`policy export`, and `policy rollback`. The [policy control
guide](docs/POLICY_CONTROL.md) documents authority, immutable versions,
concurrency, rollback, and compatibility behavior.

## Deployment options

| Path | Intended use | Boundary |
| --- | --- | --- |
| Source + SQLite | Local evaluation and one-process operation | Not a shared or HA store |
| [Signed OCI image](docs/OCI.md) | Separately versioned `v0.1.3` Linux `amd64` reference | Digest is the artifact contract; no mutable `latest` tag; not the v1.0.0 source release |
| [Docker Compose](deploy/compose/README.md) | Provider-free single-VM evaluation or pilot | One gateway replica; not HA or production certification |
| [Kubernetes + Helm](deploy/kubernetes/README.md) | Bounded multi-replica reference | Customer-operated PostgreSQL and ingress; not general HA/DR certification |

Public TLS remains a customer-controlled ingress responsibility. Read
[deployment](docs/DEPLOYMENT.md), [storage](docs/STORAGE.md), and
[operations](docs/OPERATIONS.md) before using a shared environment.

## Documentation

| Topic | Guide |
| --- | --- |
| Architecture and trust boundaries | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Codex and Claude Code setup | [docs/CLIENTS.md](docs/CLIENTS.md) |
| Policy administration | [docs/POLICY_CONTROL.md](docs/POLICY_CONTROL.md) |
| Usage, tokens, cost, and budgets | [docs/USAGE.md](docs/USAGE.md) |
| Provider streaming, latency, failover, and compute boundaries | [docs/PROVIDER_RELIABILITY.md](docs/PROVIDER_RELIABILITY.md) |
| v1.1.0 source development: portfolio registry, attribution, outcomes, finance, and internal work budgets | [docs/PORTFOLIO_INTELLIGENCE.md](docs/PORTFOLIO_INTELLIGENCE.md) and [docs/WORK_BUDGETS.md](docs/WORK_BUDGETS.md) |
| Secret-egress controls | [docs/SECRET_CONTROLS.md](docs/SECRET_CONTROLS.md) |
| Audit contracts and export | [docs/AUDIT.md](docs/AUDIT.md) |
| Persistence and migrations | [docs/STORAGE.md](docs/STORAGE.md) |
| Deployment and recovery | [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/DISASTER_RECOVERY.md](docs/DISASTER_RECOVERY.md) |
| OCI signing and supply chain | [docs/OCI.md](docs/OCI.md) |
| Executable verification evidence | [docs/VERIFICATION.md](docs/VERIFICATION.md) |
| Compatibility and support | [SUPPORT.md](SUPPORT.md) |
| Roadmap and maturity gates | [docs/ROADMAP.md](docs/ROADMAP.md) |

Inspect the versioned public contracts before integrating an external report or
audit consumer:

~~~bash
python3 -m hormuz contract manifest
~~~

## Project status and scope

| Status | Current boundary |
| --- | --- |
| Stable public contract | v1.0.0 stabilizes the CLI and policy/evidence contracts. |
| Production certification | None claimed; deployment fitness remains operator- and environment-specific. |
| Verified references | Only the exact profiles documented in [SUPPORT.md](SUPPORT.md) and their retained evidence. |
| Unfinished | Independent external onboarding, general production HA/DR, cloud certification, complete provider-account coverage, and independent review. |

Exact same-revision Codex/OpenAI and Claude Code/Anthropic BYO-provider evidence
is recorded in [issue #115](https://github.com/Xpounder-com/hormuz/issues/115).
It does not prove provider-invoice reconciliation or every client feature.
It does not prove enterprise production readiness.
It does not cover traffic bypassing Hormuz.

External onboarding is now recruiting under
[issue #110](https://github.com/Xpounder-com/hormuz/issues/110) using the
[v1.0.0 study protocol](docs/EXTERNAL_ONBOARDING.md). As of August 30, 2026, its published count is
**0/5 independent initial completions** and **0 returning users**. Every counted
session is bound to the immutable released source-archive digest, and its strict
aggregate uses only opaque IDs and fixed metadata fields; Hormuz adds no product
telemetry. Issue #110 must close before Hormuz claims validated human
onboarding, but it is not a dependency for the already published v1.0.0 release
or its bounded internal-repeatability claim.

## Open source and enterprise evaluation

The core gateway is Apache-2.0 and independently useful. Existing identity,
policy, budget, secret-control, and evidence features remain in the open core.
The initial paid offer is a scoped, founder-led evaluation or integration
engagement around the same product—not an established proprietary edition,
hosted service, certification, or 24/7 SLA.

See the [OSS/support comparison](https://usehormuz.github.io/enterprise/),
[proposed pilot scope](marketing/PILOT.md),
[buyer resources](https://usehormuz.github.io/resources/), and
[security brief](marketing/TRUST.md). Scope, price, capacity, support hours,
response targets, and terms must be agreed before work begins.

Public maintainer: **Mehrdad Zaker**. For evaluation inquiries, use
`zaker.mehrdad@gmail.com` or the
[local email-draft form](https://usehormuz.github.io/contact/).
Vulnerabilities must still follow [SECURITY.md](SECURITY.md).

The [current marketing packet](marketing/README.md) is separate from the
archived, non-publishable v0.1.3 drafts in [docs/launch](docs/launch/README.md).

## Development and testing

The default suite uses local fake providers and requires no OpenAI or Anthropic
credential:

~~~bash
python tools/verify_secret_inventory.py
python -m unittest -v
~~~

To validate the public contribution, support, and documentation surfaces:

~~~bash
python tools/verify_public_community_paths.py
python -m unittest -v tests.test_public_community_paths
~~~

Blocking CI runs on Python 3.11, 3.12, 3.13, and 3.14, builds and installs the
distribution, executes the provider-free quickstart, and runs the unit and
gateway integration suite. Additional PostgreSQL, deployment, live-provider,
and supply-chain checks retain their own explicit prerequisites and claims. See
[verification](docs/VERIFICATION.md) for the complete test boundary.

## Contributing and community

Contributions that make Hormuz safer, easier to operate, or easier to verify are
welcome.

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change.
- Join [Discussions](https://github.com/Xpounder-com/hormuz/discussions/211) or
  choose a [bounded newcomer task](marketing/CONTRIBUTOR_STARTERS.md).
- Follow the [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) in every project space.
- Check [SUPPORT.md](SUPPORT.md) before filing an installation or compatibility
  report.
- Report suspected vulnerabilities through the private process in
  [SECURITY.md](SECURITY.md), never a public issue.
- Use the [issue tracker](https://github.com/Xpounder-com/hormuz/issues) for
  synthetic, reproducible bugs, documentation problems, and feature proposals.

Community support is best effort and carries no response, remediation, uptime,
compatibility, or enterprise-support SLA.

## License

Hormuz is available under the Apache License 2.0. See `LICENSE` for the full
terms.
