# Portfolio policy recommendations

The Hormuz 1.3 source candidate includes a provider-free recommendation runtime
for issue #224. It turns an exact, immutable model scorecard into a reviewable
policy or work-budget proposal. The runtime does not call a model, read a live
connector, or apply a change automatically.

Generation is an internal typed operation. An authorized caller supplies an
exact scorecard reference, selected cohort, current policy, candidate policy or
candidate budget-plan reference, saved scenario suite, usage summary, preview
request, expiry, and lineage. The deterministic kernel returns either a closed
`hormuz.recommendation-evaluation` or no recommendation. It suppresses results
when coverage is incomplete, the scorecard is inconclusive or expired, a
mandatory guardrail fails, the selected cohort is not eligible, or the declared
change type does not match the semantic policy difference.

The public administrator surface is limited to:

- `GET /v1/admin/portfolio/recommendations`
- `GET /v1/admin/portfolio/recommendations/{recommendation_id}`
- `POST /v1/admin/portfolio/recommendations/{recommendation_id}/decisions`

All three operations require the configured `portfolio_admin` role before query
parsing or storage access. There is no public generation route. List and show
reads commit metadata-only audit evidence before returning a response. List
pagination uses a five-minute, actor-, organization-, authority-, filter-,
schema-, limit-, as-of-, and sequence-bound cursor. Responses remain under the
one-megabyte portfolio response limit and database work uses a five-second
statement timeout.

Authorized list and show responses include the exact `pre_apply_evidence`
object required by the decision request. An administrator can review and echo
server-derived evidence without access to the internal generation operation.
Request-preview evidence binds the actor and team, client, protocol, requested
model and output tokens, evaluation time, monthly usage period, and a digest of
the exact aggregate usage snapshot.

Each recommendation identifies exactly one of these changes:

- limited work-budget plan
- model allowlist
- model fallback
- output or cost cap
- routing policy

The record binds the exact organization, work scope and version, scorecard and
evaluation digest, active policy version and digest, model and route evidence,
metric references, rate-card evidence, observation window, coverage,
guardrails, and candidate policy or budget-plan identity. Its explanation names
the tradeoff, expected direction, affected scopes, losing alternatives, and
confidence boundary without claiming causality or realized savings.

Every accepted decision must echo four pre-apply references from the evaluation:
semantic comparison, request preview, scenario evaluation, and rollback plan.
The runtime rechecks expiry, active-policy identity, scorecard lineage, expected
version, evidence bytes, and conflicts in the same transaction that appends the
decision. Exact idempotent replay returns the original result. Changed replay,
stale versions, cross-tenant references, policy drift, scorecard drift, expired
recommendations, and conflicting accepted changes fail closed. Drift and expiry
are recorded before the conflict is returned.

All policy-change categories conflict when they share the same work scope,
scope version, and baseline policy digest; a full candidate policy cannot be
split into independently accepted category labels. Work-budget changes remain
a separate conflict group. Candidate budget plans whose window has ended are
suppressed. Cursor continuations evaluate expiry against the first page's
frozen `as_of` time and append at most one expiry event when the expired item
appears only on a later page.

In managed-policy mode, generation and decisions share policy control's tenant
advisory lock and re-read the active version inside the recommendation
transaction. A policy activation that races evaluation therefore completes
either before the locked recheck and invalidates the operation, or after the
recommendation transaction commits; it cannot slip between validation and the
append-only event.

Acceptance is not activation. Existing policy-control or work-budget activation
must run separately with its own authorization and compare-and-set rules. Only
after that succeeds may the internal `record_applied` operation append an
`applied` event bound to the activation evidence. The public state remains
`accepted`; `applied` is lifecycle evidence rather than a second public state.
Late application after expiry is rejected. The application receipt also
rechecks the frozen scorecard and every policy or budget binding other than the
intentional target change; post-acceptance drift appends an `invalidated` event
instead of an `applied` event.

Application evidence is derived again inside the recommendation transaction.
Budget receipts bind the current validated plan pointer to its immutable
activation event. Managed-policy receipts bind the current active version and
generation to the unique immutable `policy_activated` or
`policy_rolled_back` event. Caller-supplied event identifiers or digests cannot
stand in for that ledger proof. Local-policy mode has no immutable activation
ledger, so policy recommendations in that mode cannot record an `applied`
event.

SQLite migration 19 and PostgreSQL migration 24 add four tenant-keyed tables:

- `portfolio_policy_recommendation_snapshots`
- `portfolio_policy_recommendation_events`
- `portfolio_policy_recommendation_read_audit`
- `portfolio_policy_recommendation_cursors`

Snapshots, events, audit records, and cursors are append-only. PostgreSQL forces
row-level security and grants the runtime role only `SELECT` and `INSERT`.
SQLite installs equivalent mutation triggers. The tables retain bounded
operational evidence and canonical recommendation documents; they exclude
prompts, responses, ticket text, comments, code, paths, filenames, raw provider
payloads, credentials, employee rankings, and person comparisons.

PostgreSQL exposes a tenant-scoped, read-only security-definer receipt function
that binds the active policy pointer to its immutable activation event. The
runtime cannot browse the policy-control ledger. The complete non-owner ACL
boundary is measured twice from independent clean managed-role schema-24
bootstraps and pinned by the runtime plan.

Upgrading from SQLite 18 or PostgreSQL 23 is additive and atomic. A failed DDL
or partial migration ledger is not repaired in place. The previous binary
refuses schema 19/24, and the current binary refuses a newer or incomplete
schema. Rollback therefore requires a quiesced, verified pre-migration backup
and the matching previous application binary.

The source candidate includes a frozen provider-free evaluation and accepted or
rejected decision fixture, deterministic kernel tests covering every required
coverage field plus sample, freshness, overlap, and quality suppression,
adversarial SQLite runtime tests, restricted PostgreSQL parity, two-writer
conflict races, forced RLS and append-only checks, migration failure and retry
tests, old-reader and partial-ledger refusal, distribution checks, and a
source-integrity plan. These tests do not prove live connector completeness,
customer acceptance, exact-main CI, final-candidate acceptance, or a v1.3.0
release.
