<!-- hormuz-launch-asset-v2 {"asset_id":"conversion_analytics","publication_status":"draft_do_not_publish","claim_ids":["ALPHA_BOUNDARY","COST_REPORTING"]} -->

# DRAFT — DO NOT PUBLISH

## Tester recruitment and later human-controlled conversion

The bounded announcement has two public-alpha calls to action:

1. [Test the public alpha](https://github.com/Xpounder-com/hormuz/blob/main/docs/QUIET_ALPHA.md)
2. [Report an installation problem](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)

Hormuz is a **public alpha** and is **not production-ready**. External onboarding validation pending: **0/5** independent completions. Internal,
maintainer-assisted, and synthetic runs do not increase that count. Issue #110
continues after publication and must close before Hormuz claims validated
onboarding, advances beyond alpha, or makes a stronger commercial-readiness
claim. <!-- claims: ALPHA_BOUNDARY -->

The announcement recruits testers; it does not announce a mature commercial
offering. No hidden telemetry, automated outreach, or customer-data intake is
introduced by these links. Public testing is self-service, but inclusion in the
#110 aggregate is invitation-only through a separately agreed private channel;
successful testers must never post participant or session IDs publicly.

## Later commercial conversion

An AI Governance Review and paid design-partner pilot may be offered only after
the owner chooses appropriate intake surfaces and external onboarding has been
validated. Those later links must have an explicit privacy notice and
data-handling boundary and must never point to an unrelated generic form.

### AI Governance Review

The future review is a human-led working session for organizations evaluating whether
a governed provider-egress path fits their current AI use. The agenda is:

- which employee AI clients and provider families are in scope;
- which teams need distinct model, output, token, or estimated-cost limits;
- which identity source can supply stable person and team facts;
- which exact secret-egress and content-retention risks matter first;
- which alpha nonclaims prevent a pilot today; and
- whether there is one small, synthetic-data-first pilot outcome worth
  scoping.

The reviewer produces a content-free summary of the policy problem, blockers,
and recommended next step. The review is not a production architecture
approval, security certification, compliance opinion, or sales commitment.

### Paid design-partner pilot

A future pilot application is reviewed by a human. If there is a fit, the next steps
are discovery, a written scope, explicit data and deployment boundaries,
success criteria, commercial terms, and approval by both parties. Duration,
price, support expectations, and deliverables are agreed before work begins;
none are inferred from a form submission.

The pilot should begin with the smallest authorized team and synthetic or
otherwise approved data. It must not use the public issue tracker for private
architecture, employee, provider, prompt, response, credential, or customer
information.

### Minimum intake fields

- work email and organization;
- role and approximate team-size range;
- current AI clients and provider families, without account identifiers;
- the single governance problem to evaluate;
- desired timing and deployment constraint;
- confirmation that no credential, prompt, response, or customer data was
  submitted; and
- consent to human follow-up.

Do not automate account selection, acceptance, rejection, outreach, replies,
discovery, pricing, security claims, proposals, or contract decisions. Tools
may help summarize approved intake and maintain an internal task list only.

## Launch measurement contract

Stars and impressions measure exposure. They are not the primary outcome. The
launch should report this small funnel:

| Measure | Count only when | Source | Public reporting boundary |
| --- | --- | --- | --- |
| Successful installations | An independent evaluator reports a clean supported-environment install and successful `hormuz --help`. | Opt-in completion report | Aggregate count by release; no local path or identity. |
| Completed demos | An independent evaluator reports every documented `hormuz demo` PASS line. | Opt-in completion report | Aggregate count by release; no demo input. |
| Governed requests | An opted-in evaluator confirms an authorized request reached a provider through Hormuz with a policy outcome. | Opt-in completion report | Aggregate count and provider family only. |
| Returning users | An opted-in evaluator reports meaningful use in two distinct seven-day periods. | Opt-in follow-up | No hidden CLI identifier or cross-site fingerprint. |
| Useful reports | A report leads to a confirmed documentation, compatibility, security, or product improvement. | Maintainer triage | Count type and resolution; private details stay private. |
| Design-partner conversations | A human completes substantive governance discovery with a matching organization. | Owner-recorded pipeline | Aggregate stage count; identity and notes stay private. |
| Pilot applications | A human submits the owner-approved application and consents to follow-up. | Owner-controlled form | Aggregate count; application contents stay private. |

Hormuz usage and estimated-cost reports remain governed product evidence with
explicit coverage and pricing boundaries; they are not launch telemetry,
provider invoices, or employee-productivity measures. <!-- claims: COST_REPORTING -->

### Weekly readout

For each release week, record:

- release or commit identifier;
- successful installations and completed demos;
- governed-request and returning-user counts where users opted in;
- useful public reports and privately tracked security-report count;
- design-partner conversations and pilot applications;
- the top installation or comprehension blocker; and
- the one highest-leverage corrective action with an owner.

Never publish names, email addresses, organization identities, free-text intake,
security-report contents, prompts, responses, credentials, or customer data in
the launch readout.
