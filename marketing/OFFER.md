# Hormuz: free Personal, hosted Pro, custom Enterprise

## Hosted pricing direction

Approved October 1, 2026:

- **Personal Optimizer: $0**, running locally with the customer's own provider
  account. No paid personal subscription or guaranteed provider savings.
- **Pro: $99 USD per workspace per month**, including 100,000 gateway requests.
  Additional requests cost **$10 per 100,000**, prorated by request.
  Administrative users are included. Customers pay model providers directly.
- **Enterprise: custom proposal**, covering agreed capacity, deployment,
  integration, and operating support. Availability and response commitments
  depend on qualification and agreed operating capacity.

Pro is intended for production application API traffic on Hormuz-operated
hosting. The existing Render setup is the intended starting point; this pricing
decision does not authorize new paid infrastructure or establish capacity.
It is currently **accepting inquiries**, with no Pro checkout or automatic
activation. Hosted onboarding, per-workspace provider credential isolation,
application API compatibility, subscription billing, durable request metering,
usage controls, and production reliability qualification are launch prerequisites.

The proposed billable unit is one accepted client request; streaming chunks,
internal retries, and failover attempts must not multiply the charge. Failed,
canceled, and rejected request treatment must be defined before activation.
For 1,000,000 monthly requests, the proposed Hormuz fee is $189, excluding
provider charges and applicable taxes. Monthly request allowance is separate
from request-rate, concurrency, and payload capacity.

The target-buyer and pricing assumptions remain hypotheses to validate with
qualified workloads, willingness to pay, and measured hosting/support costs.
Existing self-hosted support and pilot offers below remain separate services.

## The operational problem

When coding clients use company model-provider accounts, someone must decide
which identities can use which models, how budgets are enforced, what happens
to detected secrets, and what evidence is retained. Hormuz places those controls
in the governed model-request path while employees keep Codex or Claude Code.

**Positioning:** a self-hosted, Apache-2.0 AI policy, usage, and evidence gateway
for coding-client workflows. Provider credentials stay on the gateway. Routine
ledgers retain bounded metadata, not prompts and responses.

## Who should evaluate it first

Hypothesis to test, not validated demand: platform or engineering leads rolling
out Codex/Claude Code under organizational provider accounts, with security as
an evaluator. Start where a named team can route a named workflow through the
gateway and assign an operator. Traffic that bypasses Hormuz is not covered.

## What remains open source

The existing gateway, identity verification, policy overlays, budgets,
deterministic secret controls, usage reports, audit exports, demos, and reference
deployment material remain in the Apache-2.0 core. Nothing in this marketing
work changes the license or withdraws existing features.

## What the initial paid engagement adds

Subject to fit, agreed capacity, and a written scope: workflow/control mapping,
configuration assistance, one bounded non-production integration, an agreed
acceptance run, evidence/gap review, and handoff. A named engagement contact and
support schedule are negotiated; no general SLA or fixed response time is
established by this document.

There is not yet an established separate proprietary enterprise edition,
managed SaaS service, certification-backed offering, or 24/7 operations service.
Future software differentiation should follow repeated buyer needs, not an
arbitrary paywall around the useful core.

## Next step

Discuss one workflow with **Mehrdad Zaker**, **mehrdadz@neuralint.io**.
Begin with fit and scope; a shorter evaluation may precede the proposed
[90-day pilot](PILOT.md). Agree price, timing, responsibilities, terms, and
support expectations before work begins.

Evidence: [architecture](../docs/ARCHITECTURE.md), [clients](../docs/CLIENTS.md),
[usage](../docs/USAGE.md), [support boundaries](../SUPPORT.md),
[v1.2.0 release](https://github.com/Xpounder-com/hormuz/releases/tag/v1.2.0).

## Enterprise pricing

Approved September 6, 2026: **$15,000 USD total for a 90-day pilot**, covering
one team and one workflow under an agreed scope. **Enterprise support starts
at $2,000 USD/month**, with defined support hours, response targets, upgrade
assistance, and policy guidance in a separate recurring agreement. The pilot
does not automatically convert to a subscription. Provider usage, infrastructure,
and applicable taxes are additional. A free governance review qualifies fit.

Larger deployments and extra implementation work require a separate quote.
Live payment collection requires verified merchant/account access, checkout
prices, support/cancellation terms, and the publication checks in
[commercial setup](COMMERCIAL_SETUP.md).
