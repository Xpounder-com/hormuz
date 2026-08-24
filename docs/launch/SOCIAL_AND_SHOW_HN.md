<!-- hormuz-launch-asset-v1 {"asset_id":"social_show_hn","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","CLIENT_VERIFICATION","COST_REPORTING","GATEWAY_SCOPE","PROVIDER_FREE_DEMO"]} -->

# DRAFT — DO NOT PUBLISH

Publish none of these drafts until issue #110 is closed, the repository and
release artifacts are publicly verifiable, and the owner approves the final
copy.

## X post

> I built Hormuz: an open-source AI gateway that lets teams keep Codex and
> Claude Code while company policy governs models, budgets, and usage evidence.
> The alpha includes a five-minute provider-free demo. Try it:
> https://github.com/Xpounder-com/hormuz

<!-- claims: GATEWAY_SCOPE COST_REPORTING PROVIDER_FREE_DEMO -->

Suggested reply, only after the live-provider gate closes:

> Pinned Codex and Claude Code clients run through Hormuz in blocking CI. The
> release record also separates loopback compatibility from live BYO-provider
> evidence so the claim stays inspectable.

<!-- claims: CLIENT_VERIFICATION -->

## LinkedIn post

Teams are adopting Codex and Claude Code faster than most organizations can
write enforceable AI policy.

I built Hormuz to put the control point in the request path rather than in a
spreadsheet. Employees keep their existing clients. The organization gets one
gateway for identity-bound model policy, budgets, and metadata-only usage and
cost evidence. <!-- claims: GATEWAY_SCOPE COST_REPORTING -->

The first experience does not require a model account or API key. `hormuz demo`
sends synthetic requests through the real gateway path against disposable
loopback provider simulators, proves allow/reroute/cap/redact/deny behavior,
validates content-free evidence, and removes the temporary state.
<!-- claims: PROVIDER_FREE_DEMO -->

This is an open-source alpha for evaluation and design-partner hardening—not a
production, compliance, enterprise-HA, disaster-recovery, or independent-
security-review claim. <!-- claims: ALPHA_BOUNDARY -->

Repository: https://github.com/Xpounder-com/hormuz

[Book an AI Governance Review]({{AI_GOVERNANCE_REVIEW_URL}}) or
[apply for a paid design-partner pilot]({{PAID_PILOT_URL}}). Every review,
qualification decision, scope, price, and proposal remains human-controlled.

## Show HN

### Title

```text
Show HN: Hormuz – company policy between Codex/Claude Code and model providers
```

### Submission body

I built Hormuz because the developers I want to support already use Codex or
Claude Code. Asking them to adopt another AI UI would avoid the real problem:
an organization needs one enforceable place for model choices, output and
budget limits, and metadata-only usage evidence. <!-- claims: GATEWAY_SCOPE COST_REPORTING -->

Hormuz is a CLI-first gateway. The smallest way to evaluate it is:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
hormuz demo
```

That command needs no provider account or API key. It runs synthetic requests
through the real HTTP, policy, redaction, request-attempt, and SQLite evidence
path using disposable loopback providers, then removes the temporary state.
<!-- claims: PROVIDER_FREE_DEMO -->

Blocking CI exercises pinned official Codex and Claude Code clients through
the same gateway boundary. The repository distinguishes that loopback client
proof from the separate live BYO-provider gate. <!-- claims: CLIENT_VERIFICATION -->

The project is an evaluation alpha. It does not claim production suitability,
HA/DR, compliance, provider-invoice accuracy, or an independent security
review. I would especially value feedback on installation clarity, policy
semantics, and whether the content-free evidence boundary is understandable.
<!-- claims: ALPHA_BOUNDARY -->

Repository: https://github.com/Xpounder-com/hormuz

