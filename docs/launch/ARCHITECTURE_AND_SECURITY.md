<!-- hormuz-launch-asset-v2 {"asset_id":"architecture_security","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","DATA_BOUNDARY","DURABLE_DATA_BOUNDARY","EVIDENCE_BOUNDARY","GATEWAY_SCOPE","OCI_RELEASE","POLICY_CONTROLS","REFERENCE_DEPLOYMENTS","SECRET_EGRESS"]} -->

# DRAFT — DO NOT PUBLISH

## Architecture and security story

Hormuz is a governed provider-egress path, not a new employee AI application.
Codex and Claude Code keep their familiar interfaces and send supported
provider protocols to Hormuz. Hormuz authenticates the caller, evaluates
company policy, applies deterministic secret controls, and only then calls the
configured OpenAI or Anthropic API. <!-- claims: GATEWAY_SCOPE -->

```text
Codex / Claude Code
        |
authenticated Hormuz identity
        |
active versioned policy
allow | deny | reroute | cap
        |
secret egress control
redact | deny
        |
OpenAI / Anthropic

governed outcome -> metadata-only evidence
```

## The request boundary

1. **Authenticate.** Hormuz resolves a tenant-qualified person and team from a
   unique bootstrap credential or configured OIDC JWT mapping.
2. **Pin policy.** The request uses one exact active policy version from start
   to finish.
3. **Evaluate.** Client, requested model, output limit, token budget, and
   estimated-cost budget are checked before egress.
4. **Control secrets.** Configured and detected values are redacted or cause a
   denial before the upstream request is serialized.
5. **Route once.** An allowed request is sent with the company provider
   credential. Hormuz does not automatically replay an ambiguous attempt.
6. **Record outcome.** Versioned metadata-only evidence records the governed
   result and provider-reported usage fields when available.

The alpha policy path can allow, deny, reroute, or cap a request. Forecast or
budget-pacing views are advisory; actual usage and active reservations enforce
the configured limits. <!-- claims: POLICY_CONTROLS -->

## Credential and content flow

The employee's Hormuz credential authenticates only to Hormuz and is removed
before provider egress. The provider credential is read on the service side
and replaces it upstream. Prompt and response bodies are relayed for the
request but are not written to the usage database. <!-- claims: DATA_BOUNDARY -->

Secret detection is a bounded egress control. Hormuz can match configured
credentials, exact environment-supplied values, and supported high-confidence
text patterns; it can redact or deny before upstream serialization. Evidence
contains rule identifiers and counts, never the matched value. The alpha does
not claim arbitrary encoding, archive, image, or semantic-company-secret
detection. <!-- claims: SECRET_EGRESS -->

## Evidence boundary

The durable usage/evidence contract is metadata-only. It can include the
organization, event-time team and person binding, client, protocol, requested
and routed model, policy version and outcome, token categories, estimated
cost, status, provider request metadata, and content-free security counts.
Prompt and response bodies are outside that ledger. <!-- claims: EVIDENCE_BOUNDARY -->

This boundary supports organizational policy and cost review without turning
Hormuz into an employee-productivity or organizational-memory system. It does
not prove traffic that bypassed Hormuz, provider invoice totals, or the content
of a request.

The alpha is self-hosted and has no Hormuz-operated customer-data service. Its
versioned [durable-data inventory](../DURABLE_DATA.md) binds every created
SQLite/PostgreSQL table and operator-requested artifact to a content boundary.
Customer operators remain responsible for database export, retention, backup,
restore, and deletion. Hormuz has no universal-erasure authority over provider,
IdP, KMS, Object Lock, backup, client, or observability systems.
<!-- claims: DURABLE_DATA_BOUNDARY -->

## Deployment story

The portable product contract is the signed `v0.1.3` OCI digest,
`sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67`.
Public GHCR is only the first publication registry. The published image is
`linux/amd64`; a multi-architecture manifest remains blocked on separate
native `linux/arm64` verification. Exact keyless workflow identity, public
Rekor image signature, registry SBOM/provenance attestations, and anonymous
pull were verified for this release. The documented recursive-mirror procedure
requires destination verification; no v0.1.3 destination-registry proof is
claimed here.
<!-- claims: OCI_RELEASE -->

The built-in server does not manage public TLS certificates. A customer-owned
ingress terminates TLS and connects to Hormuz over an authenticated,
network-restricted hop.

The exact account-free reference set covers a single-VM Compose pilot,
multi-replica Kubernetes and Helm application operation, CloudNativePG
failover and quorum loss, and a disaster-recovery rehearsal within its
accepted reference targets. Compose remains evaluation/pilot-only; the other
proofs apply only to their pinned disposable combinations. None establishes a
customer SLA, broad platform portability, or production certification.
<!-- claims: REFERENCE_DEPLOYMENTS -->

## What the alpha does not establish

Hormuz v0.1.3 is a public alpha and is not production-ready. External onboarding validation pending: 0/5 independent testers. It does not
establish validated onboarding, production suitability, a universal HA/DR
guarantee, compliance, provider-invoice accuracy, a customer SLA,
cloud-specific certification, protection from a host-root administrator, or
an independent security review. Evaluate it with synthetic data and report
installation or comprehension failures through the documented public paths.
<!-- claims: ALPHA_BOUNDARY -->
