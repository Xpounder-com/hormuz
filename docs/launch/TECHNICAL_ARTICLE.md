<!-- hormuz-launch-asset-v2 {"asset_id":"technical_article","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","CLIENT_VERIFICATION","COST_REPORTING","DATA_BOUNDARY","DURABLE_DATA_BOUNDARY","EVIDENCE_BOUNDARY","GATEWAY_SCOPE","POLICY_CONTROLS","PROVIDER_FREE_DEMO","REFERENCE_DEPLOYMENTS","SECRET_EGRESS"]} -->

# DRAFT — DO NOT PUBLISH

## The missing organizational layer between AI clients and model providers

Developers already have AI tools they like. The organizational problem is not
usually a lack of another chat interface. It is that a company cannot express,
in one enforceable place, which client and model a team may use, how much it
may consume, what must be removed before egress, and what evidence should
remain afterward.

Hormuz is an open-source, CLI-first gateway and control plane for that missing
layer. Codex and Claude Code keep speaking supported OpenAI and Anthropic
protocols; the organization inserts Hormuz as its company-controlled provider
path. <!-- claims: GATEWAY_SCOPE -->

This release is a **public alpha** and is **not production-ready**. **External onboarding validation pending: 0/5 independent testers.** The initial
announcement is an invitation to test the first public experience, not a claim
that onboarding or commercial readiness has already been validated.

### Policy should follow the authenticated person

A marketing team may need a small model set and a modest monthly boundary. An
engineering team may need multiple approved models and larger output limits.
A policy administrator may need authority to create those rules while having
no inference entitlement at all.

Hormuz resolves the organization, person, and team behind a request and pins
the exact active policy version when the request starts. It evaluates the
client, requested model, output limit, token budget, and estimated-cost budget
before provider egress. The result can be allow, deny, reroute, or cap.
<!-- claims: POLICY_CONTROLS -->

That is the enterprise-parental-control metaphor behind the product, with an
important distinction: the purpose is not to score employees. The purpose is
to make the organization's AI rules explicit, consistent, and reviewable at
the point where provider-bound traffic can actually be controlled.

### The gateway is also a data boundary

An employee credential should not become a provider credential. Hormuz removes
the employee's Hormuz credential before egress and supplies the configured
company provider credential on the service side. Prompt and response bodies
are relayed for the request, but they are not written to the usage database.
<!-- claims: DATA_BOUNDARY -->

Before the upstream request is serialized, deterministic egress controls can
redact or deny configured values and supported high-confidence secret
patterns. The durable security evidence records the rule identifier and a
detection count, not the matched value. <!-- claims: SECRET_EGRESS -->

This is deliberately a bounded claim. Text detectors do not understand every
encoded payload, image, archive, or semantically sensitive company fact. A
gateway can reduce a class of accidental disclosure and fail closed for exact
configured secrets; it cannot replace an organization's complete data-loss
prevention, endpoint, network, or provider-contract program.

### Evidence should explain policy without retaining the conversation

For each governed event, Hormuz can retain metadata such as the event-time
organization, team and person binding, client, protocol, requested and routed
model, policy version and outcome, provider-reported token categories,
estimated cost, request status, and content-free security counts. These fields
use versioned contracts; prompt and response bodies stay outside the usage
ledger. <!-- claims: EVIDENCE_BOUNDARY -->

The same boundary supports organizational cost views. Hormuz can group
captured token and estimated-cost usage by organization, team, person, client,
or model while keeping unpriced requests and coverage limits explicit. An
estimate is not a provider invoice, and Hormuz should never turn person-level
consumption into a productivity score. <!-- claims: COST_REPORTING -->

The public alpha is self-hosted. Hormuz does not operate a hosted customer-data
service or a remote deletion control plane. Its versioned durable-data
inventory enumerates every created database class and operator artifact;
customer database and backup operators own export, retention, backup, restore,
and deletion. Deleting one deployment does not erase provider, IdP, KMS,
Object Lock, backup, client, or observability copies.
<!-- claims: DURABLE_DATA_BOUNDARY -->

### A useful alpha needs an executable first experience

Infrastructure projects often ask users to create accounts and secrets before
they can see whether the core abstraction makes sense. Hormuz takes a smaller
first step:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz demo
```

The command exercises the real Hormuz HTTP, policy, redaction,
request-attempt, and SQLite evidence path using synthetic requests and
disposable loopback provider simulators. It demonstrates an allowed request,
a rerouted and capped request, a redacted synthetic secret, and a denied
request that makes no provider call. It validates content-free evidence and
removes the temporary state before exiting. No provider account or external
provider call is required. <!-- claims: PROVIDER_FREE_DEMO -->

Blocking CI also installs pinned Codex 0.147.0 and Claude Code 2.1.233 clients
and routes them through Hormuz against loopback providers. A separate
same-revision, content-free artifact verifies governed streaming calls through
real OpenAI and Anthropic endpoints. That proof covers the exact supported
paths; it does not certify every feature or future client release.
<!-- claims: CLIENT_VERIFICATION -->

### Deployment evidence should stay narrower than the marketing

Exact account-free proofs now cover the single-VM Compose pilot,
multi-replica Kubernetes and Helm application operation, CloudNativePG
failover and quorum loss, and a disaster-recovery rehearsal within its
accepted reference targets. They prove those pinned disposable combinations,
not production certification, broad platform portability, or a customer SLA.
<!-- claims: REFERENCE_DEPLOYMENTS -->

### What Hormuz is—and is not—trying to become

Hormuz governs provider-bound AI requests and their organizational evidence.
It is not an identity provider, model, organizational memory, metadata
compiler, ticketing system, or employee-productivity platform. Keeping that
surface small makes the core policy and evidence boundary easier to inspect,
test, and operate.

The current release is an open-source public alpha for evaluation and tester
recruitment. It does not claim validated onboarding, production suitability,
universal HA/DR, compliance, provider-invoice accuracy, a customer SLA, or an
independent security review. Those boundaries are part of the product claim,
not marketing footnotes.
<!-- claims: ALPHA_BOUNDARY -->

If this is the control point your organization is missing, run the provider-
free demo first. Then [follow the public-alpha tester
guide](https://github.com/Xpounder-com/hormuz/blob/main/docs/QUIET_ALPHA.md)
and use the documented issue or private-security path for failures. Do not
submit credentials, prompts, responses, employee records, customer data, or
participant identity mappings. Governance reviews and paid design-partner
intake remain a later human-controlled phase after onboarding validation.
