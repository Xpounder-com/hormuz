<!-- hormuz-launch-asset-v2 {"asset_id":"social_show_hn","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","CLIENT_VERIFICATION","COST_REPORTING","GATEWAY_SCOPE","PROVIDER_FREE_DEMO"]} -->

# DRAFT — DO NOT PUBLISH

Publish none of these drafts until the repository and release artifacts are
publicly verifiable and the owner approves the final post-change copy. Issue
#110 remains open after publication as the onboarding-validation ledger.

## X post

> I built Hormuz, a public alpha for putting company policy between Codex/
> Claude Code and model providers. Not production-ready. External onboarding
> validation pending (0/5). Run the provider-free demo and help test it:
> https://github.com/Xpounder-com/hormuz

<!-- claims: GATEWAY_SCOPE COST_REPORTING PROVIDER_FREE_DEMO -->

Evidence note:

> Pinned Codex and Claude Code clients run through Hormuz in blocking CI. A
> same-revision content-free artifact separately verifies governed streaming
> calls through real OpenAI and Anthropic endpoints.

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

This is a **public alpha**, **not production-ready**. **External onboarding
validation pending: 0/5 independent testers.** The announcement recruits
testers; it is not a validated-onboarding, production-certification, universal
HA/DR, compliance, customer-SLA, or independent-security-review claim.
<!-- claims: ALPHA_BOUNDARY -->

Repository: https://github.com/Xpounder-com/hormuz

Tester guide: https://github.com/Xpounder-com/hormuz/blob/main/docs/QUIET_ALPHA.md

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

Blocking CI exercises pinned Codex and Claude Code clients through the same
gateway boundary. A separate same-revision, content-free artifact verifies
governed streaming calls through real OpenAI and Anthropic endpoints.
<!-- claims: CLIENT_VERIFICATION -->

The project is a **public alpha** and is **not production-ready**. **External onboarding validation pending: 0/5 independent testers.** It does not claim
validated onboarding, production suitability, universal HA/DR, compliance,
provider-invoice accuracy, a customer SLA, or an independent security review.
I would especially value feedback on installation clarity, policy semantics,
and whether the content-free evidence boundary is understandable.
<!-- claims: ALPHA_BOUNDARY -->

Repository: https://github.com/Xpounder-com/hormuz
Tester guide: https://github.com/Xpounder-com/hormuz/blob/main/docs/QUIET_ALPHA.md
