# Hormuz: open source with supported evaluation

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

Discuss one workflow with **Mehrdad Zaker**, **zaker.mehrdad@gmail.com**.
Begin with fit and scope; a shorter evaluation may precede the proposed
[90-day pilot](PILOT.md). Agree price, timing, responsibilities, terms, and
support expectations before work begins.

Evidence: [architecture](../docs/ARCHITECTURE.md), [clients](../docs/CLIENTS.md),
[usage](../docs/USAGE.md), [support boundaries](../SUPPORT.md),
[v1.0.0 release](https://github.com/Xpounder-com/hormuz/releases/tag/v1.0.0).
