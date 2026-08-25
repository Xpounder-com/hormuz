<!-- hormuz-launch-asset-v1 {"asset_id":"landing_page","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","CLIENT_VERIFICATION","COST_REPORTING","EVIDENCE_BOUNDARY","GATEWAY_SCOPE","OCI_ROADMAP","POLICY_CONTROLS","PROVIDER_FREE_DEMO","SECRET_EGRESS"]} -->

# DRAFT — DO NOT PUBLISH

## Keep Codex and Claude Code. Put company policy in between.

Hormuz is an open-source, CLI-first AI gateway for organizations that want one
place to govern how existing Codex and Claude Code clients reach OpenAI and
Anthropic. Employees keep the tools they already use; Hormuz becomes the
company-controlled request path. <!-- claims: GATEWAY_SCOPE -->

[Run the five-minute provider-free demo](../../README.md#try-the-real-gateway-without-a-provider-account)
· [Book an AI Governance Review]({{AI_GOVERNANCE_REVIEW_URL}})
· [Apply for a paid design-partner pilot]({{PAID_PILOT_URL}})

Hormuz v0.1.1 is an evaluation alpha. It is not a production, compliance,
enterprise-HA, disaster-recovery, or independent-security-review claim.
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

Pinned official Codex and Claude Code clients are exercised through Hormuz
against loopback providers in blocking CI. Real two-provider BYO release
evidence remains a separate open gate and must close before formal launch copy
describes that proof as complete. <!-- claims: CLIENT_VERIFICATION -->

## Evidence without prompt surveillance

Hormuz stores versioned metadata about the governed event—identity binding,
team, client, requested and routed model, policy outcome, token counts,
estimated cost, request status, and security-rule counts. Prompt and response
bodies are excluded from the usage ledger. <!-- claims: EVIDENCE_BOUNDARY -->

Usage views can answer how much an organization, team, person, client, or model
consumed within Hormuz's capture and pricing boundary. Estimated cost is not a
provider invoice, and unpriced requests remain visible rather than being
silently treated as free. <!-- claims: COST_REPORTING -->

## A portable release target

The first image target is one signed `linux/amd64` OCI digest. Private GHCR is
the first publication registry, not the product contract, so an organization
can verify the digest and mirror it elsewhere. Image publication and signature
verification are still release work, not a completed alpha claim.
<!-- claims: OCI_ROADMAP -->

## Start with your real policy problem

Book an AI Governance Review to map the clients, model choices, budget
boundaries, identity facts, and secret-egress risks that matter in your
organization. If Hormuz fits the bounded problem, apply for a paid
design-partner pilot. Every review, qualification decision, scope, price,
security boundary, and proposal is handled by a human.

[Book an AI Governance Review]({{AI_GOVERNANCE_REVIEW_URL}})
· [Apply for a paid design-partner pilot]({{PAID_PILOT_URL}})

Do not submit credentials, prompts, responses, employee records, customer
data, private hostnames, or proprietary logs through either intake path.
