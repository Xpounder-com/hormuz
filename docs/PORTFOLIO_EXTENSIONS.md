# Budget reports and Linear context: v1.1.0 design contracts

These are owner-approved, separately versioned **planned records**, not
implemented budget enforcement, a running connector, or live/customer proof.
The existing `hormuz contract manifest` describes the installed surface and is
unchanged. No new route or CLI command is activated by these files.

The separately gated #217 transition checkpoint now selects
`hormuz.work-budget-report` version 2 as the first runtime management result.
ADR 0012 and [`BUDGET_TRANSITION.md`](BUDGET_TRANSITION.md) define that
analytics-first successor. The digest-pinned version-1 bundle below remains
unchanged and is never silently upgraded.

## Record map

| Record | Purpose | Planned reader |
| --- | --- | --- |
| `hormuz.work-budget-preview` v1 | Bounded dry-run of an immutable candidate against all ceilings | Portfolio administrator |
| `hormuz.work-budget-report` v1 | Work-scope burn, remaining budget, projection and coverage | Authorized role-scoped aggregates |
| `hormuz.work-budget-report` v2 | The v1 current row plus the latest effective plan change, exact delta and comparable percentage | Portfolio administrator at first runtime; broader views remain #223 |
| `hormuz.linear-context-event` v1 | Typed source lifecycle/parent metadata | Portfolio administrator |
| `hormuz.linear-context-page` v1 | Frozen, scoped context collection | Portfolio administrator |
| `hormuz.linear-context-retention` v1 | Separate operator logical-retention marker | Portfolio administrator |

The [manifest](portfolio-extension-contract-v1.json) pins both
[budget](work-budget-reports-wire-v1.json) and
[Linear](linear-context-wire-v1.json) wire bundles, their synthetic examples and
the unchanged v1/portfolio/finance predecessor files. Its digests cover exact
file bytes, not an alternative canonicalization.

## Budget meaning

A preview evaluates an already-created immutable plan through the existing
policy comparison/scenario boundary. It binds the exact plan/work-scope,
policy, scenario and data snapshot plus the active-pointer generation. All
organization, team, actor, application, policy, portfolio, initiative and
use-case ceiling classes are evaluated, including the absence of a configured
ceiling. Applicable limits intersect; a child cannot relax a parent.
An incomplete simulation is inconclusive, never a permit. A preview creates no
reservation and cannot activate policy or issue provider work. At activation,
recheck authority, candidate and policy digests, expected generation, expiry
and all current caps. Rejected or expired previews do not weaken any cap.
`compatible` requires a complete, nonempty all-allowed simulation with no
restriction reasons. `would_restrict` requires known denials and reasons;
an empty simulation cannot establish compatibility.

Burn reporting keeps enforcement accounting separate from provider facts.
Remaining amount is exactly plan ceiling minus committed estimates, pending
reservations and uncertain reservations. Negative remaining amounts remain
visible after, for example, emergency tightening. Unknown input gives null,
not fabricated zero. Currency must match; no implicit FX or mixed-basis sum.
Plan replacement never resets accumulated spend or releases unknown holds.
The derived remaining field allows 19 integer digits so subtraction of three
18-integer-digit charges is always representable; financial inputs still have
the 18/18 bound. A report identifies a positive historical activation generation;
a never-activated candidate belongs to preview, not enforcement reporting.

Financial observations are not an additive invoice table: final facts,
aggregates, configured/allocated estimates and signed credits retain their
own basis/provenance. A configured estimate pins its rate-card version. An
allocated estimate must separately pin its allocation rule/version; the shape
does not approve an algorithm or authorize allocation. Provider facts cannot
use a configured-rate or derived-allocation source label, and unavailable
values cannot simultaneously claim finalized finance evidence.
Only authoritative finalized evidence at the exact work scope can populate a
final work-scope fact. A broader/unattributed observation carries a scope gap
with its amount/currency withheld, not an allocation or a private account total.
An exact-scope observation with an unavailable amount retains its known
basis/source/provenance. Amount and currency remain null with the applicable
missing-evidence, unsupported-currency or not-available reason; this is not a
known monetary fact and does not replace missing evidence with zero.

The optional `linear_committed_projection` is exactly committed estimates times
full-period seconds divided by elapsed seconds. It requires an open period,
complete attributed/priced population, matching currency, a versioned rule,
and an exactly representable 18/18 decimal result. Otherwise projection fields
are null with a fixed reason. It explicitly excludes pending/uncertain holds,
is not a statistical prediction and claims no confidence or guaranteed savings.
The report exposes those holds separately. Never silently round an
unrepresentable projection or reuse an older plan's input snapshot.

Readers receive only authorized work-scope aggregates; a role field or scope
digest does not authorize a request. Team leads cannot inspect peers or
organization-wide source totals. Platform readers receive authorized operational
aggregates, not raw finance/source documents. #223 must implement and prove
those views before granting currently unsupported role capabilities.

## Linear meaning and source limits

Source entity identity includes kind and UUID; relationships remain separate
from Hormuz's single-parent work hierarchy. The representation can retain
multiple project/initiative parents without guessing a primary one, and
issue project/cycle context without relabeling the parent as an issue.
A partial or missing relationship set is not evidence of removal.
Only a complete comparable source revision can replace the current projection;
every change appends history. Relation lookup must independently authorize
both ends, including private-team resources.
A supersession target is permitted only for a known revision, current ordering
state and complete relationship set. Where no parents apply, an explicitly
complete empty set can qualify; partial or unknown evidence cannot supersede.

Linear documents organization-scoped webhooks, raw-body HMAC signatures,
data-change actions and a five-second response deadline. These are provider
constraints for the later adapter, not proof this repository has a compliant
live receiver. [Official webhook reference](https://linear.app/developers/webhooks).
Linear also documents cross-team projects and private-project visibility inside
broader initiatives; a visible parent therefore cannot grant child access.
[Projects](https://linear.app/docs/projects), [initiatives](https://linear.app/docs/initiatives).
Reviewed August 31, 2026; pin the actual payload/schema/action mapping in #220's
provider-specific checkpoint before implementing it.

Known source time, observation time and authoritative commit time remain
separate. Source time may be unknown or skewed and never grants authority.
Unknown source time cannot produce a historical matched work binding.
Wall-clock timestamps from different components do not establish causality;
the frozen database sequence controls snapshot membership. Revisions compare
only in their verified source domain; UUID, receipt and delivery order are not
source revisions. Late, conflicting or incomparable facts cannot erase newer
authoritative state.
Each stored context record carries a positive server-assigned `commit_sequence`
from its organization's context stream. Page items have distinct sequences at
or below the frozen `snapshot_sequence`; `ingested_at` versus `as_of` is not a
membership test. Fixture sequence values are not proof of real database state.

Exact raw bytes, independently registered workspace/team/webhook scope and
explicitly typed entity enrollment must be verified before normalization.
Neither `source_authentication` in a fixture nor a digest is that verification.
The current connector's project-only allowlist is not authority for every
initiative/cycle/team. A later separate authorization-binding contract and
implementation must close that gap under #214/#220.

The context page is distinct from the existing outcome page. It defaults to 50
items, at most 100, with one-hour, scope/filter/authority/snapshot/schema/order/
retention-bound cursors. Order by source time (observation time only when source
time is unknown), connector and context-event ID, descending. Fallback order
does not manufacture a source timestamp. Query authorization precedes storage
and audit commit precedes delivery. Retention invalidates older cursors,
hides the target without resurrecting an older fact, and preserves linked
financial/audit records. It is not erasure from customer backups/exports.
The emitted compact ASCII JSON record/page is at most 1 MiB. The offline linter
does not apply a smaller aggregate-member limit that rejects otherwise valid
wire arrays. Provider ingress limits remain a separate #220 receiver boundary.

## Consumer compatibility and verification

Existing consumers make **no changes**: no old field, enum, route, schema ID,
error, authentication rule, pagination/retry behavior or command is replaced.
No deprecation or sunset is required. New consumers must explicitly opt into
these separate schema IDs only after their corresponding implementation
ships; never accept a context record as an old outcome or a preview as a plan.

```bash
python tools/verify_portfolio_extensions.py
python -m unittest -v tests.test_portfolio_extensions tests.test_portfolio_extension_packaging
python tools/verify_portfolio_intelligence_contract.py
```

The verifier checks exact file digests, closed/bounded schemas, duplicate JSON,
all five minimal/populated examples and selected semantic invariants. Examples
are explicitly synthetic. These tests do not authenticate a request, access
storage, perform cross-tenant queries, reserve spend, verify source signatures,
measure ingestion deadlines or prove migration/recovery. They are not a runtime
JSON Schema implementation and must not be used as a request authorization
boundary. The existing small schema vocabulary is reused without modification.

## Remaining gates

- #217: accepted #214 atomic-reservation and transition preflight, actual
  enforcement and independent-replica tests, conservative failure/settlement,
  audit, period/activation behavior, migration and populated recovery.
- #220: accepted #214 source-authority/transport/storage preflight, exact
  provider mapping, bounded durable acknowledgment, typed relation/privacy
  enforcement, idempotency/late/move/delete/backfill behavior, runbooks and
  an owner-authorized live workspace delivery/redelivery.
- #8: durable financial evidence, adapters, reconciliation and authorized live
  finance support. These contracts do not repair the earlier credential 403.
- #221/#223 and other downstream issues: explicit evidence quality and
  authorized aggregate readers, never inferred by schema availability.
- #214 final candidate, #225 real preregistered external pilot, and separate
  owner release/tag authorization: still open. No deployment or integration
  credentials are provisioned or modified by this work.

This frozen extension checkpoint itself reserves no migration. Finance history
subsequently established SQLite 8 / PostgreSQL 12 on main. The #217 red-first
budget checkpoint binds that exact predecessor and plans additive 9/13
transitions; it does not implement or activate those migrations.
