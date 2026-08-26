<!-- hormuz-launch-asset-v2 {"asset_id":"landing_page","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","CLIENT_VERIFICATION","COST_REPORTING","DURABLE_DATA_BOUNDARY","EVIDENCE_BOUNDARY","GATEWAY_SCOPE","OCI_RELEASE","POLICY_CONTROLS","PROVIDER_FREE_DEMO","REFERENCE_DEPLOYMENTS","SECRET_EGRESS"]} -->

# DRAFT — DO NOT PUBLISH

## Keep Codex and Claude Code. Put company policy in between.

Hormuz is an open-source, CLI-first AI gateway for organizations that want one
place to govern how existing Codex and Claude Code clients reach OpenAI and
Anthropic. Employees keep the tools they already use; Hormuz becomes the
company-controlled request path. <!-- claims: GATEWAY_SCOPE -->

[Run the five-minute provider-free demo](../../README.md#try-the-real-gateway-without-a-provider-account)
· [Test the public alpha](https://github.com/Xpounder-com/hormuz/blob/main/docs/QUIET_ALPHA.md)
· [Report an installation problem](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)

Hormuz v0.1.3 is a **public alpha**. It is **not production-ready**.
**External onboarding validation pending: 0/5 independent testers.** This
announcement recruits early testers; it does not claim validated onboarding,
a production certification, universal HA/DR guarantee, compliance, a customer
SLA, or an independent security review.
<!-- claims: ALPHA_BOUNDARY -->

## One organizational control point

Different teams need different AI boundaries. Hormuz evaluates the
authenticated person and team before provider egress, then applies the active
policy for the client, requested model, output limit, token budget, and
estimated-cost budget. A request can be allowed, denied, rerouted, or capped.
<!-- claims: POLICY_CONTROLS -->

| Need | Hormuz alpha behavior |
| --- | --- |
| Let teams keep existing clients | Route supported Codex and Claude Code protocols through one gateway. |
| Limit model and spend choices | Apply identity-bound model, output, token, and estimated-cost policy before egress. |
| Reduce accidental secret exposure | Redact or deny configured and detected values before upstream serialization. |
| Understand organizational use | Report tokens and estimated cost by organization, team, person, client, and model. |
| Review what policy did | Keep versioned metadata-only policy, usage, cost, status, and security evidence. |

Hormuz secret control is deterministic and content-free: evidence identifies
the rule and detection count, not the matched value. It is a useful egress
control, not a promise that every encoded, visual, or semantically sensitive
company fact will be detected. <!-- claims: SECRET_EGRESS -->

## See the real path without a provider account

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz demo
```

The demo sends synthetic requests through the real Hormuz HTTP, policy,
redaction, request-attempt, and SQLite evidence path. Disposable loopback
listeners stand in for providers, so the demonstration needs no API key and
makes no external provider call. <!-- claims: PROVIDER_FREE_DEMO -->

Pinned Codex 0.147.0 and Claude Code 2.1.233 clients are exercised through
Hormuz against loopback providers in blocking CI. A same-revision,
content-free live artifact also verifies governed streaming calls through
real OpenAI and Anthropic endpoints. The proof covers the supported protocol
paths, not every client feature. <!-- claims: CLIENT_VERIFICATION -->

## Evidence without prompt surveillance

Hormuz stores versioned metadata about the governed event—identity binding,
team, client, requested and routed model, policy outcome, token counts,
estimated cost, request status, and security-rule counts. Prompt and response
bodies are excluded from the usage ledger. <!-- claims: EVIDENCE_BOUNDARY -->

Usage views can answer how much an organization, team, person, client, or model
consumed within Hormuz's capture and pricing boundary. Estimated cost is not a
provider invoice, and unpriced requests remain visible rather than being
silently treated as free. <!-- claims: COST_REPORTING -->

## Your deployment owns its durable data

The public alpha is self-hosted; Hormuz does not operate a hosted customer-data
service. The versioned [durable-data inventory](../DURABLE_DATA.md) names every
SQLite/PostgreSQL class and operator-created artifact. Customer database and
backup operators own export, retention, backup, restore, and deletion. Hormuz
does not claim universal erasure across provider, IdP, KMS, Object Lock,
backup, client, or observability systems.
<!-- claims: DURABLE_DATA_BOUNDARY -->

## A portable release target

The current published image is the signed `v0.1.3` `linux/amd64` OCI digest,
`sha256:8ac24f5c7afb8ce09ec133616de06702f568a2e70594d8034146a131d86e5b67`.
Public GHCR is the first publication registry, not the product contract, so an
organization can verify the exact workflow identity and recursively mirror the
digest, signature, and attestations elsewhere using the documented procedure.
Anonymous pull, signature/Rekor, and both attestations were exercised for this
release; this does not claim a v0.1.3 destination-registry mirror proof.
<!-- claims: OCI_RELEASE -->

## Bounded reference deployments

Account-free proofs cover a single-VM Compose pilot, multi-replica Kubernetes
and Helm application operation, CloudNativePG failover and quorum loss, and a
disaster-recovery rehearsal within its accepted reference targets. These are
proofs of exact pinned combinations—not production certification, a customer
SLA, or broad platform portability. The boundaries are recorded in the
[verification record](../VERIFICATION.md) and the
[disaster-recovery runbook](../DISASTER_RECOVERY.md).
<!-- claims: REFERENCE_DEPLOYMENTS -->

## Help validate the first public experience

Anyone may follow the tester guide from a clean checkout. Installation and
documentation problems belong in the public issue forms; suspected
vulnerabilities use the private security path. Counted session evidence is
invitation-only and returns through a separately agreed private channel so an
opaque participant ID is never attached to a public GitHub identity. Internal
rehearsals and synthetic fixtures never increase the `0/5` count.

[Test the public alpha](https://github.com/Xpounder-com/hormuz/blob/main/docs/QUIET_ALPHA.md)
· [Report an installation problem](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)

Do not submit credentials, prompts, responses, employee records, customer
data, private hostnames, proprietary logs, or participant identity mappings.
Governance reviews and paid design-partner applications remain a later,
human-controlled phase after onboarding validation.
