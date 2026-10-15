# ADR 0018: Retire off-direction products around one local-router workflow

- Status: Accepted by product owner
- Date: 2026-10-14
- Approval: The owner requested repository/backlog cleanup and chose
  “Keep operational cleanup; close unrelated product issues.”
- Specification: [#523](https://github.com/Xpounder-com/hormuz/issues/523)

Hormuz is a customer-local router for existing coding clients, with CLI and
skills-only plugin entry points. The approved public test seam is connect →
coding-client request through the router → result/status → disconnect. Existing
clients retain their execution, permissions and agent loop. MCP is optional for
a demonstrated capability gap.

Retire standalone apps, hosted/enterprise front doors, billing, SSO/admin,
PostgreSQL deployment support and unrelated product roadmaps. Retire their
source, delivery machinery and CI together. Keep no fake passing legacy jobs.
The 2.0 development line records breaking command/configuration/HTTP changes
and versions the narrower work state as v3.

Preserve destination and credential ownership, hard local-only enforcement,
automatic admission, final policy/readiness checks, secret inspection, stream
and tool continuity, conservative accounting, unknown outcomes, cancellation,
recoverable setup and conditional restoration. Keep shared SQLite schema and
historical readers where the local router depends on them. User data, releases,
active implementation branches and external resources are outside this source
retirement.

Operational cleanup remains open in #494–501. No closure implies resource or
billing retirement or completed acceptance. This supersedes enterprise/UI
product obligations in ADRs 0001, 0002 and 0004–0012 for new development. ADRs
0013–0017 remain applicable where their protections are retained. Historical
ADRs are evidence, not instructions to reintroduce retired products.

Next qualify one real local runtime/model/client combination, one documented
cloud authentication path and skills-only setup over the shared connection
implementation. Synthetic tests do not prove quality, subscription eligibility,
installed-platform support or plugin acceptance.

See [scope](../ROUTER_SCOPE.md) and [migration/rollback](../MIGRATION_2.md).
