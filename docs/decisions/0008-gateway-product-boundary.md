# ADR 0008: Hormuz gateway product boundary

- Status: **Accepted**
- Date accepted: 2026-08-20
- Decision owner: Product owner
- Tracking issue: [#20](https://github.com/Xpounder-com/hormuz/issues/20)
- Supersedes: the Hormuz-owned memory responsibilities in ADR 0003 and all of ADRs 0005, 0006, and 0007

## Decision

Hormuz is an enterprise AI gateway and control plane. Its supported product responsibilities are:

- connect employees' existing Codex and Claude Code clients to company-managed OpenAI and Anthropic access;
- authenticate people and workloads and derive organization, team, person, and application scope;
- enforce model allowlists, fallbacks, output limits, budgets, DLP, egress, approval, retention, and provider-cache policy;
- route requests without exposing provider credentials to employees; and
- record content-free request, token, cost, policy-decision, security, and audit metadata for authorized reporting.

Hormuz does **not** own organizational knowledge quality. It does not create, verify, rank, promote, decay, invalidate, or serve claims, decisions, provenance, source evidence, or reusable engineering memory.

The existing local context repository, lifecycle API, Context Pack API, MCP tool, benchmark, and automatic injection are retained temporarily as deprecated experimental compatibility surfaces. They are not enterprise release gates and will receive no new persistence or lifecycle investment in Hormuz. Ordinary gateway traffic must not initialize their content-bearing SQLite database.

Existing usage columns that describe historical context experiments remain readable. Removing them would be a destructive schema change with no present customer benefit. Supported gateway events leave them at their compatibility defaults.

## Why

The product is bought to control AI access and spend across an organization while employees keep their current tools. Bundling a knowledge system into that enforcement boundary creates unrelated storage, retrieval-quality, provenance, and lifecycle obligations; it also forces a content-bearing database into deployments that only need policy, routing, DLP, and accounting.

Separating the responsibilities gives Hormuz a smaller security boundary and a verifiable adoption path. A future metadata or memory product can evolve independently.

## Compatibility and migration

1. Existing context configuration remains parseable during the compatibility period.
2. Existing context HTTP routes remain callable but return the RFC 9745 structured date `Deprecation: @1787184000` (2026-08-20 UTC).
3. The context database is opened lazily only when an experimental context surface is explicitly used or an administrator has retained legacy automatic injection.
4. Historical context tests remain as compatibility evidence, not Hormuz release criteria.
5. Removal of public commands, configuration, routes, MCP behavior, or stored columns requires a separately documented versioned migration and sunset plan.

## Deferred decision: external context admission

Hormuz may later accept a context payload produced by another system and apply gateway controls to it. That would govern admission and egress properties such as tenant, issuer, signature, classification, expiry, size/token limit, DLP result, and model eligibility. Hormuz would not judge the payload's truth, freshness, provenance, or retrieval quality.

No such protocol is approved by this ADR. Its trust model, failure behavior, client compatibility, and privacy impact require a separate owner decision.

## Consequences

- PostgreSQL work continues for gateway policy, identity, sessions, budgets, approvals, security events, usage, and audit—not for Hormuz-owned memory.
- Provider-native prompt caching may be governed and measured, but Hormuz will not build a reusable content-pack cache.
- Ticket movement and employee productivity are not inferred from token consumption. Hormuz reports consumption, enforcement, coverage, and cost; outcome attribution belongs in an optional integration or analytics layer.
- ADRs 0005, 0006, and 0007 remain historical records of the earlier experiment and are superseded rather than rewritten.

## Owner approval record

The product owner explicitly separated the AI Metadata Compiler from Hormuz, confirmed that Hormuz does not need claim/decision/source/provenance responsibilities, approved the resulting priority change, and directed implementation to continue with GitHub issues and tangible PR milestones.
