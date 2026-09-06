# PostgreSQL collection runtime candidate — v1.1.0

The owner authorized this bounded implementation on [#8](https://github.com/Xpounder-com/hormuz/issues/8#issuecomment-5559866894).
Transition plan v7 adds schema 17; SQLite stays at 12. It does not rewrite plan v6
or migration 0016, and does not grant release or finance-feature acceptance.

Migration 0017 adds only SELECT and INSERT on the seven collection tables: fourteen
permissions. Schemas 15 and 16 retain the literal 185 boundary and reject the
injected 186th permission. Schema 17 has one literal 199 boundary and rejects the
injected 200th permission. Neither bootstrap nor runtime verification accepts
alternative fingerprints or computes its expectation from the database.

Forced tenant RLS, immutable rows, exact source/audit guards, authorization before
I/O and reauthorization under the transaction lock remain in force. Pending and
failed attempt retries remain fail-closed; completed retries return the stored
receipt without reopening a source file or contacting a provider. An empty
provider bucket is coverage, not a fabricated numerical zero; newer overlapping
coverage suppresses stale observations.

## Transition and recovery

The exact schema-16 predecessor is commit
`0973662e57636b1dd6bbb6351c6cdde6676d3384`. CI verifies the frozen archive digest,
then the predecessor driver verifies all 150 installed runtime files against it.
Tests populate previously accepted evidence domains before upgrading. The ACL-only
migration preserves their rows, constraints, triggers and source-reader function.
A missing migration or a fault after executing the grants must roll back the
entire transition; retry must be idempotent.

Stop writers and capture a matched database checkpoint before migration. A rollback
to the old binary requires restoring that checkpoint as an old binary/database
pair. Keep the newer database intact. After any schema-17 write, an old checkpoint
does not preserve that write: retain the newer pair and use forward recovery. The
real predecessor must reject both newer and partial migration state without repair.
The executable recovery tests use isolated databases, matched pg_dump/pg_restore,
and provider-free synthetic collection writes; they authorize no production restore.

## Acceptance gates

`POSTGRES_FINANCE_COLLECTION_RUNTIME_ENABLED` describes code capability;
`POSTGRES_FINANCE_COLLECTION_RUNTIME_ACCEPTED` remains false in this candidate.
Plan v7 likewise leaves collection runtime, reconciliation, finance completion,
final-candidate and release gates open. Verify the exact PR head, every protected
check (including the complete PostgreSQL suite), normal merge and exact merged-main
CI before recording bounded acceptance on #8 and #214. Budget runtime #217 is a
separate already-accepted feature and is not reopened by this work.

Run `python tools/verify_finance_collection_postgres_runtime.py` for the source-kit
contract. Optional `--predecessor-archive` validates the pinned archive. CI also runs
the installed-wheel runtime and transition suites; a source contract pass alone is
not runtime, provider, deployment or release evidence.
