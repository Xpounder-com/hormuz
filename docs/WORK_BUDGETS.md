# Governed work budgets — v1.1.0 implementation slice

This slice implements Hormuz's internal work-budget owner, pre-egress
enforcement, and the analytics-first current report selected by ADR 0012. It
does not add an HTTP route or CLI command, close #217, complete #214, or release
v1.1.0. The frozen [v1 preflight](budget-transition-plan-v1.json) remains
historical; the [v2 successor](budget-transition-plan-v2.json) records the
bounded implementation and its still-open acceptance gates.

## Management result

`hormuz.work-budget-report` version 2 is one current, administrator-authorized
row. It shows the active plan amount, committed configured-rate estimates,
pending reservations, uncertain reservations, remaining amount, coverage, and
an optional conservative forecast. Its required `plan_change` says what changed
when the current version became active. A move from USD 100 to USD 120 reports
an exact `20` amount delta and `20` percent increase at the activation's
database timestamp.

The first activation is `established`, with no invented prior value or percent.
A zero prior amount keeps the exact delta but has no percentage. Currency,
work-scope, or window changes are explicitly not comparable and retain the
prior basis. Percentages are display values rounded decimal half-even to six
places; exact plan amounts remain authoritative. Managers do not need to browse
a revision ledger, while enforcement retains the immutable facts needed to
bind each attempt to the plan actually in force.

Amounts retain their accounting states. Reservation and settlement both use
the configured rate strings under one 96-digit decimal context: reservations
round upward, while terminal estimates retain half-even integer-microusd
rounding. This preserves the pre-egress upper bound even at the largest
supported token and database-integer values; previously stored terminal facts
remain immutable and are never repriced.

- committed is the terminal request-time configured-rate estimate;
- pending is a reservation for a request without a terminal outcome;
- uncertain is a retained hold after an ambiguous provider outcome;
- remaining is plan amount minus all three; and
- a negative remainder stays visible after emergency tightening.

These are gateway estimates, not provider invoices or general-ledger facts.
There is no implicit FX, allocation, repricing, procurement approval, or claim
of savings. A configured-rate observation pins its exact rate identity and is
never relabeled as provider-final spend.

Before producing those observations, the report validates every persisted
rate-card ID, version, and digest and fails closed on malformed evidence. Up to
100 distinct identities remain exact one-card observations. If a plan window
contains more, the report keeps the first 99 identities in canonical order and
combines the remainder into one deterministic overflow observation. That row
carries a `hormuz-rate-card-overflow:<digest>` composite reference rather than
pretending one constituent card represents the group. The reference digest
binds the exact ordered member-card identities, while the provenance digest
binds every constituent row. Its amount remains the exact sum only when every
terminal estimate is present. Consumers must treat this reserved prefix as a
report aggregate identity, not a configured route card. Enforcement totals and
coverage always include every binding, so reporting stays bounded without
turning rate-card rotation into a gateway denial or silently dropping spend.
The aggregate prefix is reserved and therefore refused if it appears in a
persisted binding rather than being derived by the report.

## Plan lifecycle and preview

`WorkBudgetRepository` is an internal integration owner composed beside the
existing registry, attribution, outcome, and v1 usage owners. It owns no pool
or migration lifecycle. Only a currently configured `portfolio_admin` for the
tenant may create, activate, preview, or read a plan. Authorization is checked
before I/O, again under the tenant lock, and before commit. Successful reads
append their audit before the result is delivered.

A creation appends an immutable version. Activation separately appends an
immutable event and advances one active pointer by compare-and-set using the
expected version and generation. The pointer's plan, generation, event, and
timestamp are protected by a composite foreign key. Reactivation appends a new
generation; it never edits an earlier version or event. Only a plan whose
declared window contains the database time may activate. Emergency tightening
and rollback are explicit activations and do not erase prior consumption or
unknown holds. A tenant may retain at most 1,000 activated plan IDs in this
runtime version; activation and request-time scans both enforce that bound.
Every request validates the current event against the immediately preceding
generation, including the prior active version and nondecreasing database
timestamp. Management reads additionally validate the bounded full activation
history.

Preview is a dry run against a nonempty frozen policy scenario suite. It cannot
activate a plan, reserve spend, or call a provider. It evaluates the effective
routed model and policy-capped output bound against the candidate plus every
effective ancestor plan. Known policy, model, output-token,
unsupported-currency, or unbounded-output restrictions produce
`would_restrict`. The current scenario schema intentionally contains no
request body, input-token
reservation, or cost estimate, so a scenario that passes those known checks
remains `inconclusive` with `missing_evidence`; even a zero ceiling is not
treated as a known denial when the request cost is absent. Activation is never
authorized by a preview and request-time enforcement rechecks all actual facts.

## Atomic request enforcement

When any plan is effective for a tenant, a governed request needs one current,
authorized use-case attribution before provider egress. The gateway resolves
the exact use-case version, walks its portfolio/initiative hierarchy, and
checks every applicable active plan. Parent and child model allowlists
intersect; every output-token, per-request-cost, and total amount ceiling must
pass. Missing, ambiguous, stale, archived, malformed, or unsupported evidence
fails closed.

One usage-owner transaction covers:

1. the immutable request-attempt root and pending event;
2. the event-time attribution fact;
3. existing organization/team/actor/application/policy ceilings;
4. applicable portfolio/initiative/use-case ceilings;
5. the legacy reservation; and
6. every immutable attempt-to-plan binding.

Any failure rolls back that entire unit before provider egress. A denied
request then commits a separate fixed-class audit event under the same tenant
lock order; if that mandatory audit cannot commit, the outcome is a storage
failure, not an unaudited success. PostgreSQL locks organization-month, then
work-budget tenant, then portfolio tenant. Independent runtime instances use
the same order, so two replicas cannot both spend the same remaining amount.
An exhausted attribution-audit sequence under an effective plan follows this
same coordinated denial path, ensuring the management population records the
refusal; if that audit cannot commit, the adapter reports storage unavailable.
If neither the request nor effective policy supplies an output-token maximum,
the reservation has no defensible upper cost bound and the request fails closed
before provider egress. Serialized request bytes bound only self-contained
input. A provider-resolved response/conversation/prompt, URL, file identifier,
or server-side tool can add billed input that is absent from the body; an
active work budget therefore rejects those forms until a reviewed
modality-specific bound exists. Inline text, file data, and base64 media remain
self-contained and retain the byte-based conservative reservation.

Each accepted binding pins the attribution event, plan version and activation
generation, work scope, window, currency, reserved amount/output tokens,
effective provider/model reference, activation and request policy identities,
configured route-rate identity, and valuation rule. Terminal settlement uses
the immutable request usage estimate only after the response parser observes
valid nonnegative input- and output-token evidence. A successful response with
missing, malformed, partial, or oversized usage metadata becomes
`outcome_unknown` under the frozen provider-transport ambiguity class; it
retains the conservative reservation and never fabricates a zero-cost usage
fact. For an Anthropic stream, the `message_start` output-token placeholder is
not terminal evidence; settlement requires a valid cumulative output count in
the final `message_delta`. PostgreSQL records a terminal attempt and its linked
usage fact with one database timestamp, so current and historical `as_of`
reports never compare gateway-process and database clock domains. An
`outcome_unknown` attempt uses the same database clock, retains the reserved
amount, and is never automatically replayed. PostgreSQL also uses a database
timestamp for work-plan eligibility when attribution is absent, so a skewed
gateway clock cannot make a database-active plan disappear. The frozen process
clock remains on the legacy attempt and monthly-accounting timestamps; it is
not used for work-plan eligibility.

The bound model dimension uses the selected `model_routes` mapping key when it
fits the frozen opaque-ID contract and is outside the reserved generated-ID
namespace. Any other key is mapped deterministically to
`configured-model-sha256:<sha256(key)>`; a configured key already shaped like
that generated form is re-encoded so it cannot impersonate another route's
generated identity. The provider-native model name remains in request evidence
and the exact rate-card digest, so names such as `vendor/model`—including names
longer than the durable model-ID field—do not become runtime-only work-budget
failures. Management previews use the same selected mapping key and identity
rule as request-time enforcement, including fallback routing.

Coverage counters form a declared partition: included, unattributed, and
unsupported attempts sum exactly to the population. Numeric- or model-ceiling
denials remain included because their governing scope is known; missing
attribution and unsupported currency remain explicit excluded populations.

## Storage, migration, and failure behavior

SQLite migration 9 and PostgreSQL migration 13 add five tables without
rewriting predecessor data. Four are append-only facts; only the active pointer
has column-scoped update authority. SQLite uses `BEGIN IMMEDIATE`, append-only
triggers, foreign keys, and `WITHOUT ROWID`. PostgreSQL uses forced row-level
security, a non-superuser/non-`BYPASSRLS` runtime, SELECT/INSERT on facts, and
UPDATE only on the four pointer projection columns. Startup and every owner
transaction verify the exact schema shape. Malformed persisted plan,
activation, or active-pointer evidence is refused with a fixed error and is
never repaired or reflected. Both adapters require the same tenant-plan-window
binding index so request-time enforcement and reporting do not scan unrelated
historical bindings. They also require a tenant/plan/operation/evaluation-time
audit index whose six declared columns are all true key attributes, so denial
aggregation does not devolve into a tenant-wide audit scan. PostgreSQL
`INCLUDE`-only copies do not satisfy that shape. SQLite and PostgreSQL readiness
both reject a missing, malformed, partial, or invalid copy of either required
index without repairing it in place. A report reads at most 10,001 matching
denial facts in index order and fails closed when a plan window exceeds the
supported 10,000-denial reporting bound. Before that bounded aggregation, it
also fails closed if a denial in the reported window has a null or nonexistent
plan-version coordinate; the join cannot silently remove damaged immutable
evidence. The immutable audit facts are neither deleted nor silently truncated.

Stop writers and pools before migration, serialize the operator action, and
restart fresh processes. Old binaries refuse schema 9/13. Before any candidate
writes, a verified old application/database pair may be restored only into a
separate destination. After writes, or when their count is unknown, retain the
candidate and recover forward. Never decrement migration ledgers, drop budget
facts to imitate downgrade, release uncertain holds, or replay model work.

The real transition tests start from the digest-pinned finance predecessor,
preserve all 53 PostgreSQL predecessor tables and their populated replay state,
then reach 58 tables. They cover transactional DDL failure/retry, partial and
newer refusal, old-pair backup/restore, post-write forward recovery, and the
same rules in SQLite. Runtime tests cover both adapters, exact decimals,
hierarchy denial, settlement, uncertain holds, activation/replacement/rollback,
mandatory auditing, process-clock skew, large-number reservation coverage,
corruption refusal, indexed denial reporting and readiness refusal, configured
provider-model names, immediate activation-predecessor integrity, generated
model-namespace collision resistance, preview/runtime mapping-key parity, and
independent PostgreSQL replicas. They also prove that incomplete successful
provider usage retains its reservation, that an unbound identity without a
scope header cannot bypass an effective plan even under process-clock skew,
that provider-side URLs/files/state/tools cannot use request bytes as a false
input bound while inline data remains supported, and that per-window denial
reporting is bounded without dropping or hiding audit facts.

## Remaining gates

The implementation is not accepted merely because these files or local tests
exist. #217 still requires exact-head technical review, every protected check,
normal merge, and CI on the exact merged-main commit. #214 remains open for the
final v1.0.0-to-v1.1.0 candidate artifact, migration, rollback, and populated
recovery proof. Finance reconciliation (#8), external observations and
associations (#219–#221), model scorecards (#222), role-scoped delivery (#223),
reviewable recommendations (#224), the preregistered independent pilot (#225),
and owner authorization for release, tag, or deployment remain separate.
