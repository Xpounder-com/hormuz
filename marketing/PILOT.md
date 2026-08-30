# Proposed Hormuz evaluation pilot

Status: discussion brief, not a quote, service agreement, SLA, or certification.
Owner: Mehrdad Zaker · zaker.mehrdad@gmail.com.

## Fit and entry criteria

The initial buyer hypothesis is a platform/engineering lead adopting Codex or
Claude Code with company provider accounts. A pilot needs a named sponsor,
operator, policy owner, and security reviewer; one non-production workflow;
authorized accounts and unique user/workload identities; approved synthetic or
otherwise explicitly authorized test data; and an agreed evidence boundary.

A short fit discussion comes first. A shorter evaluation can be scoped before
a 90-day commitment. Timing starts only after scope, prerequisites, delivery
capacity, and commercial terms are agreed.

## Proposed 90-day sequence

| Phase | Work | Deliverable and decision |
| --- | --- | --- |
| Days 1–15: Map | Inventory one client/identity/provider route; define policy, secret rules, budget, retention, and operator responsibilities | Control map, written acceptance plan, prerequisite checklist; stop or rescope if fit is poor |
| Days 16–45: Prove | Configure the non-production route; exercise allowed and denied requests, routing/caps, attribution, usage export, and agreed policy changes | Versioned evidence pack, issue log, and reproducible operator walkthrough |
| Days 46–90: Decide | Review actual friction and operating effort; exercise agreed operational checks; identify production gaps | Go/no-go memo, remaining gates with owners, handoff and post-pilot scope if warranted |

## Acceptance criteria to agree before starting

1. The named, pinned client/protocol path works in the agreed environment.
2. A forbidden request is rejected without an upstream model-provider call.
3. Configured routing/caps and deterministic secret modes behave as agreed on
   approved test inputs; no comprehensive DLP guarantee is inferred.
4. A unique identity, policy version, outcome, and captured usage can be traced
   in metadata-only evidence; prompt/response bodies and credentials are absent.
5. The operator can reproduce the agreed checks and identify the next action
   after a failure. Deployment-specific recovery/rollback is tested only to the
   extent explicitly included in the scope.
6. Gaps have named owners and an explicit decision to resolve, defer, or stop.

Numerical latency, throughput, availability, RPO/RTO, billing accuracy, and
business-value targets must be separately agreed and measured. The public
demo's elapsed time and internal five-run result are not acceptance evidence
for those targets or for independent human usability.

## Responsibilities

| Area | Customer | Engagement, only as scoped |
| --- | --- | --- |
| Infrastructure | Hosts gateway/store, TLS/ingress, access, patching, backups, retention | Configuration review and bounded assistance |
| Provider and identity accounts | Account authorization, charges, credentials, JWT issuance/refresh, unique identities | Integration guidance, without custody of customer secrets by default |
| Policy and data | Approves model/budget/secret rules and test data; decides acceptable use | Helps map requirements to implemented controls |
| Evidence | Controls access and retention; approves any sharing | Produces agreed, content-free test results and gap summary |
| Security and production | Owns risk acceptance, independent review, compliance, HA/DR qualification | Identifies gaps; no certification is supplied |

Do not send credentials, raw prompts, customer content, production databases,
or full configurations through the marketing inquiry form or public issues.
Any access to private environments or data requires an approved access method,
minimal privileges, a written data-handling scope, and appropriate review.

## Explicit exclusions unless separately established

Managed hosting, fleet-wide coverage, client-side tool governance, 24/7 on-call,
certifications, legal/compliance determinations, comprehensive semantic DLP,
per-inference human approvals, native Hormuz login/refresh sessions, provider
invoice reconciliation, guaranteed savings, and future portfolio features.

## Terms that remain to be decided

Price and payment schedule; start/end dates; time budget; meeting cadence;
support hours/time zone and response targets; named delivery contact; customer
prerequisites and delays; acceptance procedure; change control; confidentiality;
data processing/access; liability; termination; and post-pilot support.
No default amounts, promises, or legal terms are invented here. Obtain
appropriate contractual review before signing an engagement.

## Post-pilot decision

Choose one: stop and retain the documented lessons; continue self-service;
resolve specific gaps in another bounded engagement; or propose an expanded
deployment after its qualification gates are satisfied. Expansion is not the
automatic outcome of a successful demonstration.

Sources: [support](../SUPPORT.md), [client boundary](../docs/CLIENTS.md),
[operations](../docs/OPERATIONS.md), [deployment](../docs/DEPLOYMENT.md),
[trust brief](TRUST.md).
