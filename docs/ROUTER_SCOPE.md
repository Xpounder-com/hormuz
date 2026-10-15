# Customer-local router scope

Decision owner: product owner. Approved October 14, 2026 in the cleanup
conversation; recorded in [#523](https://github.com/Xpounder-com/hormuz/issues/523)
and [ADR 0018](decisions/0018-customer-local-router-scope.md).

One public workflow governs implementation and TDD:
**connect → submit a coding-client request through the router → inspect
result/status → disconnect**.

| Keep | Retire |
| --- | --- |
| Local router, CLI and skills-only setup adapter | Standalone coding/chat UI and native/Electron clients |
| Local/open-source inference; exact runtime/model qualification | Hormuz-owned agent execution app |
| Automatic admission and hard local-only enforcement | Hosted/enterprise accounts, console and workspace platform |
| Destination/credential ownership, policy and secret controls | Billing, SSO and commercial entitlement front doors |
| Streaming, tool affinity, cancellation and unknown outcomes | PostgreSQL, HA, Kubernetes and hosted deployment gates |
| Recoverable setup and conditional configuration restoration | Unrelated product roadmaps, research UI and campaign tooling |

Coding clients own execution permissions, tools and their agent loop. MCP is not
the inference-routing path; add it only for a demonstrated integration need.
The public plugin should call shared CLI operations, not embed another installer.

## Implementation order

1. Destination ownership [#524](https://github.com/Xpounder-com/hormuz/pull/524),
   admission [#525](https://github.com/Xpounder-com/hormuz/pull/525) and lifetime
   [#526](https://github.com/Xpounder-com/hormuz/pull/526) are merged at
   `8489d9d3a467442dca4b91d196928e325dd01a18`. The retirement branch is rebased
   onto that checkpoint and retains their final routing and lifetime fixes.
2. Retire unrelated source, packaging, documentation and CI. Preserve shared
   local accounting, privacy controls and historical SQLite evidence readers.
3. Qualify one real llama.cpp/runtime/model/client combination: task results,
   readiness, tools, streaming, cancellation and exact versions. No fabricated
   qualification IDs or quality claims.
4. Qualify one documented cloud/authentication path. Plan use requires actual
   consent and eligibility; login is not subscription entitlement. Never
   silently fall back to paid API usage.
5. The [skills-only package](AGENT_INSTALLATION.md) now exposes setup, status,
   troubleshooting and disconnect over the shared connection implementation.
   Qualify runtime setup, installation/platform behavior and distribution
   separately. Its manifests do not complete the real qualification steps
   above. Additional client adapters require their own work; non-Claude
   main-model substitution is not promised.

The cleanup does not finish #523. Synthetic tests establish routing/lifetime
behavior, not real model quality, subscription eligibility, OS support or plugin
acceptance. The executable pin is in [connection lifetime](CONNECTED_CLIENT_LIFETIME.md).

56 off-direction product issues were closed as **not planned**, not completed.
[Operational cleanup](OPERATIONS_CLEANUP.md) remains open. Cloud resources,
bills, credentials, user data and published releases were not changed. Removed
source and historical acceptance evidence remain recoverable from Git commit
`8489d9d3a467442dca4b91d196928e325dd01a18`.
