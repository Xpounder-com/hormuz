# ADR 0003: Retire the Hormuz-owned context-cache proposal from the core

- Status: **Superseded for the core gateway**
- Scope change recorded: 2026-08-21
- Decision owner: Product owner
- Historical tracking issue: [#3](https://github.com/Xpounder-com/hormuz/issues/3)
- Superseding release gate: [#23](https://github.com/Xpounder-com/hormuz/issues/23)

## Decision

The former proposal mixed provider prompt-caching controls with a Hormuz-owned context-pack cache. Hormuz core no longer implements or plans to implement the latter. The detailed historical proposal is archived with the separately packaged experiment at [`../../experiments/context/docs/decisions/0003-cache-privacy-tiers.md`](../../experiments/context/docs/decisions/0003-cache-privacy-tiers.md).

The core gateway continues to record provider-reported cache token fields when providers expose them. It makes no claim to store, retrieve, reuse, or invalidate customer context or model answers. Any future provider-cache policy must be proposed as a new, versioned core contract after the policy/evidence stabilization gate; it must not reintroduce a context or memory runtime by implication.

## Consequence

No work under this retired proposal may add a Hormuz context cache, content store, lifecycle, retrieval path, or cache-reporting surface to the core package. The experimental archive is not part of the core release, security, or operational boundary.
