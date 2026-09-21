# Reproducible finance collection selection

`FinanceCollectionRepository.observations_as_of` provides an internal,
administrator-authorized read of the existing append-only collection tables.
With no cutoff it captures the tenant-wide maximum snapshot `commit_sequence`
inside the same transaction used to select coverage and observations. A caller
can retain the returned `as_of_commit_sequence` and pass it back to reproduce
the same selected buckets after later collection publishes. A cutoff above the
current tenant high-water, a negative or non-integer cutoff, and booleans fail
with `invalid_request`; zero is a stable empty view.

Selection remains scoped to the authenticated tenant, source binding version,
collection profile, and exact requested UTC window. The newest snapshot at or
below the cutoff wins per exact bucket. An empty newer bucket suppresses an
older observation at the newer cutoff, while replaying an earlier cutoff
returns the older observation. The result includes selected snapshot IDs,
content digests, sequences, bucket coverage, and typed observations. One
snapshot can cover several buckets but appears once in the selected-snapshot
list. The existing `current_observations` method retains its latest-view
behavior.

This pins collection inputs only. It does not bind gateway attempts to a
provider account, authenticate account ownership, calculate variance, classify
an invoice as final, allocate provider aggregates, or create a report. The
SQLite 12/PostgreSQL 17 schemas and PostgreSQL 199-permission boundary are
unchanged. PostgreSQL uses its existing restricted `SELECT` grants and tenant
RLS. No migration, provider call, credential, CLI/HTTP route, release or #214
final-candidate acceptance is involved.

The historical reconciliation scope probe keeps its seven original predecessor
hashes and fails on this changed repository module by default. CI uses its
explicit `--current-runtime` mode to recheck the still-unbound account gap and
the unchanged latest-read signature without claiming the old seven-file source
binding for this checkout.
