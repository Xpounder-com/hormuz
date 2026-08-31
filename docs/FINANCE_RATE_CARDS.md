# Durable rate-card history — v1.1.0 implementation slice

This implements only append-only rate-card registration and exact-version
administrative reads. It does **not** complete finance issue #8 or the v1.1.0
release. Provider imports, native per-request usage, immutable request-cost
sidecars, reconciliation, budget/report integration, live finance evidence and
the independent pilot remain gated separately.

The [v2 transition plan](finance-transition-plan-v2.json) consumes SQLite schema
8 and PostgreSQL schema 12. The [v1 preflight](finance-transition-plan-v1.json),
[provider-source contract](finance-source-contract-v1.json), existing migration
bytes and [pure value rules](FINANCE_VALUES.md) remain unchanged. V2 records a
bounded implementation; validating the plan alone is not execution evidence.

## Internal integration boundary

`create_finance_repository(config, environ=..., connection_pool=...,
read_only=False)` conforms to `RepositoryFactory`. It performs no network or
database I/O, owns no pool and migrates no schema. The caller initializes and
verifies the configured usage storage through the existing operator workflow.
The factory reads only the configured existing PostgreSQL runtime DSN selector;
that reader is declared in the content-free secret custody inventory. No new
credential, environment-variable name or rotation authority is introduced.
The finance owner can be supplied explicitly to `create_repository_bundle`;
the legacy `create_usage_store` path is unchanged. Sharing a pool does not
provide transactions spanning separate repositories.

There are no new CLI commands, HTTP routes or public wire schemas in this
slice. These Python methods are internal integration points, **not** bearer
authentication APIs. A future entrypoint must authenticate the operator first
and construct a `PortfolioPrincipal` from the verified identity. The repository
then requires a known configured tenant and an exact current `portfolio_admin`
binding before storage access, under the tenant lock and before commit. A
tenant supplied inside a card is never authority. Finance viewers and team/self
roles cannot use these raw administrative reads. `read_only=True` refuses both
operations because even a successful read requires a committed audit.

The two operations are:

- `register_rate_card(principal, card: RateCard)` returns an immutable internal
  `RateCardRegistration` containing the card, receipt ID, original registering
  actor, database registration time and audit sequence.
- `get_rate_card(principal, card_id=..., version=...)` returns that same original
  registration only after its separate read audit commits. Both ID and version
  are mandatory. Missing records return a fixed `not_found` error.

Registration binds `(organization_id, rate_card_id, version)` to canonical
content. Semantically equivalent values normalize using the existing exact
decimal/timestamp rules. A retry of identical canonical content returns the
original receipt with no new rows, even for another currently authorized admin.
It does not claim that the retrying actor originally registered the card.
Different content at that identity returns `rate_card_conflict`. Versions are
explicit integers from 1 through 2147483647; out-of-order registration is allowed.
There is no implicit latest/active version, automatic interval selection or
overlap policy. A later version never updates a prior card.

These methods do not estimate or record request cost. Reading a historical card
supports reproducible pure calculations, but does not prove immutable historical
request-cost capture. No existing usage row is rewritten, repriced, backfilled
or assigned guessed native usage. Cards are operator-configured estimates, never
provider-final invoices or evidence of business impact.

## Storage and failure behavior

`portfolio_finance_rate_cards` contains bounded canonical card JSON, its SHA-256
content digest and original registration receipt fields. The digest identifies
content; it is not a MAC or protection against a database administrator rewriting
both a record and its audit. `portfolio_finance_audit_events` records metadata-only
register/read events and per-tenant sequences. The receipt references the original
registration audit. Returned rows must match their canonical card, indexed tenant/
ID/version, digest and original audit; inconsistent state fails without delivery
or automatic repair. No payload bodies, credentials or provider responses are
stored. Diagnostics use only fixed codes.

One transaction covers card, receipt and audit. SQLite uses `BEGIN IMMEDIATE`;
PostgreSQL uses the existing tenant transaction and shared organization advisory
lock. Both enforce append-only mutation guards. PostgreSQL additionally requires
forced row-level security, SELECT/INSERT-only runtime rights, and a runtime role
without superuser/BYPASSRLS. UPDATE, DELETE and PostgreSQL TRUNCATE are refused.
SQLite also refuses insert-time conflicts on each primary/unique key before
`REPLACE` can delete history, with recursive triggers either enabled or disabled.
Its two finance tables use `WITHOUT ROWID`, so an undeclared rowid cannot bypass
those guards. A conflict aborts the whole insert statement; it cannot leave an
earlier row of a multi-row insert committed. Repository-level exact retries still
return the original receipt without issuing a duplicate insert.
Database statement work is bounded at five seconds; no provider calls or internal
automatic retries occur. Unavailable storage, invalid schema guards, audit failure
or failed commit cannot acknowledge a registration or disclose a read result.

## Migration, recovery and acceptance

Stop writers and pools before migration; serialize the operator migration and
restart fresh processes afterward. Migration creates two empty tables and their
constraints/guards without rewriting earlier data. Missing, partial or newer
schemas fail closed. Schema 8/12 is incompatible with old application binaries;
those binaries must refuse it rather than silently running with incomplete guards.

Before any post-checkpoint writes, the operator may restore a verified old
application/database pair to a **separate** destination. After writes, or when
the write count is unknown, retain the candidate state and recover forward.
Never decrement the migration ledger, remove finance tables to fake a downgrade,
release unknown reservations or replay model work. Customer-owned backup and
retention policies apply; this slice exposes no deletion/redaction operation.

The implementation tests exercise both adapters, actual digest-pinned outcome and
released-v1 binaries, partial-DDL rollback/retry, old-pair recovery with preserved
earlier replays/cursors/attempt facts, and forward restoration with both finance
tables populated. Source and isolated installed-wheel verification must agree.
Protected PR checks, exact-head technical review and exact merged-main checks
remain required before a bounded issue acceptance. No live finance or final
candidate acceptance is implied. Future finance tables need a reviewed successor
plan and new migrations; schema 8/12 must not be rewritten or reused.
