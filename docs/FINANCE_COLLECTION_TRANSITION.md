# Provider finance collection transition checkpoint

Hormuz 1.1.0 needs provider-reported usage and cost evidence before it can
reconcile gateway estimates for accounting, analytics, and management. This
checkpoint freezes that collection boundary before runtime or schema changes.
It extends the accepted native-attempt finance runtime; it does not claim that
provider collection, reconciliation, invoice finalization, or live customer
finance validation exists.

The machine-readable decision is split between
[`finance-transition-plan-v5.json`](finance-transition-plan-v5.json),
[`finance-collection-contract-v1.json`](finance-collection-contract-v1.json),
and the already frozen
[`finance-source-contract-v1.json`](finance-source-contract-v1.json). A changed
decision requires a reviewed successor instead of silently editing an accepted
contract.

## The management result

The runtime successor will create a durable, auditable snapshot for one Hormuz
organization, one exact provider-account binding version, one collection
profile, one query window, and one complete page chain. Four initial profiles
cover OpenAI organization completions usage and costs plus Anthropic
organization messages usage and costs. Each stores only typed, allowlisted
dimensions and exact numeric observations.

That evidence answers useful questions without claiming more than the source
proves: what the provider reported for a period and scope, which model and tier
dimensions were available, what part of the source is unsupported or
unclassified, and whether a complete bounded query was collected. It does not
make a provider API aggregate a final invoice or assign it as final cost to a
request, team, employee, application, or work scope. Reconciliation and any
allocation method remain separate successor decisions.

## Authority and privacy before collection

Only an exact current `portfolio_admin` for the server-resolved Hormuz tenant
may prepare or publish a collection. A body organization or provider account
claim grants no authority. Before file or network I/O, the repository commits a
content-free pending attempt bound to an immutable source-binding version and a
versioned credential reference. A credential value, authorization header, or
environment-variable content is never stored. The same authority, binding, and
credential-reference version are revalidated before publication, so a
mid-collection revocation cannot publish new evidence.

The first profiles deliberately exclude OpenAI `user_id` and Anthropic
`account_id`, `service_account_id`, and beta `speed` groupings. Provider project,
workspace, and API-key identifiers become tenant-keyed fingerprints. Free-form
OpenAI `line_item` and Anthropic `description` values may be classified against
a bounded known catalogue and fingerprinted, then are discarded. They are not
stored, displayed, or interpreted as employee, model, credit, or adjustment
authority. `finance_viewer` receives no collection or raw-record access here;
role-scoped aggregate delivery remains gated by #223.

## Complete snapshots, not partial success

External I/O occurs outside database transactions and uses only the fixed
versioned profile. TLS verification is mandatory, redirects are refused, no
provider model request is made, and the existing bounded retry/deadline policy
applies. The parser rejects duplicate JSON members, non-finite or inexact
amounts, invalid Unicode, repeated cursors, inconsistent or out-of-window
buckets, conflicting records, unsafe dimensions, and all size/count/depth
overruns. Before any digest, idempotency comparison, or persistence, provider
numeric lexemes are limited to 128 bytes; known usage counts must be integers
from zero through `2^63 - 1`; and provider-native and canonical money values
must each be finite, exact, strictly between `-10^18` and `10^18`, with at most
18 integer digits, 18 fractional digits, and 36 significant digits. Booleans,
out-of-range derived sums, exponent values outside the normalized `-18..17`
range, and any required rounding fail closed. OpenAI cost `quantity` is also an
exact provider-native decimal under those same magnitude, precision, scale, and
exponent limits. Its allowlisted unit or null is retained separately without
unit conversion; an absent quantity remains unknown rather than zero.

Only a complete page chain can publish. The snapshot, exact bucket-coverage
rows, typed observations, receipt, append-only terminal attempt event, and
audit-chain entries commit or roll back together. A failure stores only a fixed
content-free terminal event, keeps the prior complete snapshot, and leaves an
explicit stale or failed coverage gap. It never substitutes zero. Persistence
retry may reuse already normalized in-memory evidence; it may not contact the
provider or replay provider model work automatically.

File import uses the same profile validation and complete page-chain rules, but
its evidence remains customer-supplied and scope-unverified. File fields and
fixtures can never set an authenticated or live-finance label.

## Accounting semantics

Usage is a provider-native aggregate observation. Cost is
`provider_reported_aggregate`. Neither is `provider_final` or invoice-final.
Amounts use exact bounded decimal arithmetic; OpenAI major-currency values stay
in their stated currency, while Anthropic fractional cents are divided by 100
exactly. Signs are preserved. Unknown positive or negative rows remain
unclassified and included in aggregate and coverage totals rather than being
dropped or guessed to be a credit or discount. There is no implicit currency
conversion.

Refreshing the exact same binding version, profile, and query window may append
a new snapshot with an explicit whole-snapshot supersession relationship. A
partially overlapping window does not supersede either whole snapshot.
Within one organization, binding ID and version, and collection profile,
reporting partitions append-only coverage rows by exact provider-native bucket
start and end and selects the newest complete snapshot by database commit
sequence for each exact interval. Only observations owned by that selected
snapshot are current. A selected `no_observation` coverage row suppresses an
older value for the interval but yields no numeric value; it never becomes
zero. Thus a January 15 through January 31 refresh replaces both populated and
empty duplicate daily buckets without dropping January 1 through January 14,
retaining stale January 20 usage, or adding either interval twice. Overlapping
buckets with non-identical boundaries remain separate evidence and cannot be
silently summed or selected as one partition. The snapshot content digest
covers canonical typed coverage, canonical typed observations, and stable
source identity: organization, binding ID and version, collection profile, and
query window. Attempt identity, requested page size, page boundaries, raw
cursors, and page-chain mechanics are excluded from that digest. A separate
page-chain digest binds validated pagination provenance, including requested
page size and canonical returned page boundaries or counts, while raw cursors
are discarded after validation. A page-size change can therefore preserve
content identity without erasing how the evidence was collected.
Historical gateway attempts and rate-card estimates are not repriced or rebound.

The initial coverage explicitly excludes ChatGPT seat/Work analytics, unmapped
Scale Tier commitments, reseller invoices, Anthropic Priority Tier costs,
Claude Platform on AWS reporting, Claude Enterprise Analytics, cloud reseller
billing, the Anthropic speed beta, and invoice/credit/tax/discount/foreign-
exchange finalization. These remain visible coverage gaps.

## Planned storage and migration

The runtime successor reserves additive SQLite schema 12 and PostgreSQL schema
16 for seven objects:

1. immutable source-binding versions;
2. immutable collection attempt roots;
3. append-only collection terminal events, with pending derived from absence;
4. complete immutable snapshots;
5. exact append-only snapshot bucket coverage, including non-numeric empty
   markers;
6. typed usage observations; and
7. typed cost observations.

Snapshot publication, source-binding changes, and collection terminal events
become the exact version-1 audit sources `hormuz.finance-snapshot`,
`hormuz.finance-source-binding-version`, and
`hormuz.finance-collection-event`. Each audit entry must match the source row's
tenant, event identity, and canonical `evidence_json`. SQLite must rebuild its
fixed source-union check transactionally while preserving every audit column,
row, index, immutability trigger, and existing source guard. PostgreSQL must
replace the corresponding fixed constraint and source-validation function and
retain forced RLS, least-privilege
runtime roles, and a new literal count/digest ACL boundary established from a
clean reviewed migration. It may not accept multiple fingerprints or derive
the expected production fingerprint from the database under test. This
preflight does not consume either migration number or alter the current 185-row
schema-15 ACL boundary.

All seven collection tables are append-only. Their reviewed migrations must
reject every update and delete, and PostgreSQL must reject `TRUNCATE`. SQLite
must additionally reject `INSERT OR REPLACE` whenever any primary or unique
identity would conflict, so replacement cannot delete a source row behind an
existing audit entry.

No current usage repository protocol, request-attempt schema, native finance
record, route, command, public wire contract, role, or error changes. The
runtime successor may add local operator commands for `finance source bind`,
`finance collect`, and `finance import`; this checkpoint registers none.

## Recovery and acceptance gates

The exact predecessor is protected merged main
`cf30256760b68b133208b4013bdd31b22639b172`, archived deterministically with
prefix `hormuz-finance-native-runtime-baseline/`, umask `0000`, and SHA-256
`35cecfb4dbb1b4a972a4f43a30941e91e38c049636bd98cc4869cb145c65d1da`.
It runs SQLite 11 and PostgreSQL 15 and contains 145 verified runtime files,
including every packaged non-code asset, whose canonical path-and-byte digest
is `163b8ebec0a519f2d07b7c2b2b53a169f69eb0abef6122ee91b6194a2df21b2a`.

The preflight must prove that the current runtime tree matches those exact
predecessor bytes, the absent 12/16 migrations fail without partial state,
representative append-only seven-table DDL plus the SQLite audit-table rebuild
and PostgreSQL audit constraint/function changes roll back and retry
idempotently,
partial and newer states are refused by current and exact predecessor binaries,
an old-pair restore preserves every accepted populated fact and audit byte, and
post-checkpoint writes require retained-candidate forward recovery without
provider replay. PostgreSQL evidence must run with the complete existing
migration and runtime suites, including the unchanged literal ACL boundary.

Local contract checks run from the repository root:

```console
python3 tools/verify_finance_collection_transition_plan.py
python3 -m unittest -v tests.test_finance_collection_transition_plan
python3 -m unittest -v tests.test_sqlite_finance_collection_transition
python3 -m unittest -v tests.test_finance_collection_packaging
```

The protected PostgreSQL job supplies a disposable database, matched backup
tools, and the digest-pinned predecessor interpreter. A local skip is not
transition evidence.

Exact-head technical review, every protected check, normal merge, and exact
merged-main transition evidence are required before a #214 preflight
acceptance can be recorded. Runtime implementation then requires a separately
reviewed successor. #8 remains open for collection runtime, reconciliation,
coverage and exception reporting, live customer-authorized proof, and the rest
of its acceptance criteria. #214 remains open for the final candidate. No tag,
release, deployment, credential use, or provider call is authorized here.
