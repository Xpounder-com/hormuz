# Portfolio role views

Hormuz v1.3 adds four aggregate-only portfolio reads. They extend the existing
v1 portfolio surface and do not change any prior route, request, response, or
CLI behavior.

| HTTP route | Required configured role | CLI |
| --- | --- | --- |
| `GET /v1/admin/portfolio/views/finance/budgets` | `finance_viewer` | `hormuz portfolio view finance budgets` |
| `GET /v1/admin/portfolio/views/platform/scorecards` | `platform_viewer` | `hormuz portfolio view platform scorecards` |
| `GET /v1/admin/portfolio/views/team/budgets` | `team_lead` | `hormuz portfolio view team budgets` |
| `GET /v1/admin/portfolio/views/team/scorecards` | `team_lead` | `hormuz portfolio view team scorecards` |

Every route returns `hormuz.portfolio-role-view-page` version 1. Budget items
carry the implemented `hormuz.work-budget-report` version 2 payload. Scorecard
items carry the implemented `hormuz.model-scorecard` version 1 payload. The
wrapper binds the reader role, identity-derived scope, frozen primary and
companion sequences, evidence level, freshness, exclusions, and model, policy,
budget-plan, rate-card, connector, association-rule, cost-basis, and metric-rule
provenance. A declared connector that is absent from the configured tenant
authority remains visible as `declared_unverified`; the view never upgrades it
to verified evidence.

Finance and platform readers are restricted to their authenticated
organization. A team lead is restricted to the identity's configured team and
work scopes whose nearest explicit owner in the exact versioned ancestry is
that team. Query parameters can narrow a time window or work-scope ID but
cannot select another organization, team, role, grouping, or payload type.
Authorization happens before query parsing and before a connection is opened.
The raw registry, outcome, attribution, finance collection, and connector
owners continue to require their existing administrator or source authority.

Collections use a fixed `work_scope` grouping, a default limit of 50 and a
maximum of 100. Optional `start_at` and `end_at` must be supplied together and
span at most 366 days. Ordering is deterministic: budget activations use
`committed_at` then budget-plan ID, and scorecards use `generated_at` then
scorecard ID, both descending. Opaque one-hour cursors are bound to the actor,
organization, exact roles, team, query class, filters, page size, schema,
as-of time, and both snapshot sequences. Omitting `limit` on a continuation
reuses the original page size; supplying a different value is rejected. The
runtime limits an organization scan to 10,000
candidate records and team-scope resolution to 10,000 versioned scopes, applies
a five-second database statement timeout, and refuses a response over 1 MiB.

Each privileged read appends one metadata-only audit event before delivery.
The event stores the actor ID, role, query class, organization or team scope,
filter digest, frozen sequences, result count, and partial count. It stores no
submitted labels, source content, prompts, responses, work-item text, code,
filenames, credentials, or provider payloads. SQLite schema 18 and PostgreSQL
schema 23 add only the append-only audit and cursor tables. Existing data is
unchanged; rollback uses the prior binary only after restoring a pre-migration
database backup, because older binaries correctly reject a newer schema.

Use `--format terminal` for the concise display, `--format json` for the exact
versioned envelope, or `--format csv` for a flat export. CSV cells whose first
non-whitespace character is `=`, `+`, `-`, or `@` are prefixed with an
apostrophe before standard CSV quoting. Exported fields remain metadata-only.

These views support portfolio decisions. They do not certify provider invoices,
measure individual productivity, rank employees, expose cross-team work, or
establish causal business impact beyond the payload's displayed evidence level.
