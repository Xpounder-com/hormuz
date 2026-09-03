# Provider finance collection runtime candidate

This is the implementation candidate for the analytics-first provider
collection slice in Hormuz 1.1.0. It stores bounded, typed provider usage and
cost aggregates as complete append-only snapshots, with source-binding
versions, content-free attempt roots, terminal events, exact bucket coverage,
and audit-chain entries. The local SQLite adapter and the file-import command
are executable candidate paths; no provider response, credential value, raw
cursor, or free-form provider text is persisted.

The candidate is deliberately not an acceptance claim. `finance collect` and
`finance import` remain subject to the existing administrator and content
validation gates, and reconciliation, allocation, role-scoped reporting, live
customer evidence, and final release remain separate decisions.

## PostgreSQL security gate

PostgreSQL schema 16 provisions the seven owner-controlled collection tables,
RLS policies, immutability triggers, indexes, and audit-source constraints.
Runtime-role grants on those new tables are intentionally withheld. This
preserves the exact protected PostgreSQL ACL boundary established for schema
15: 185 canonical non-owner entries with digest
`46c2bf134047c4720d0d6236dfb9efa62e22e37b70c9b6ef8df4b166c656249a`. A clean
bootstrap must still reject an injected 186th permission with the fixed
`postgres_bootstrap_acl_boundary_invalid` code and digest
`d06ec615d82a176b107e1131c00e1dceb5f629d9504a7519f64e1eb77a0c7246`.

The PostgreSQL collection repository therefore fails closed with
`unavailable` until a separately reviewed successor defines and accepts a new
literal ACL boundary. It never accepts multiple fingerprints and never
calculates an expected fingerprint from the database under test. This is a
security gate, not a claim that PostgreSQL collection runtime is enabled.

## Verification

Run the candidate verifier and focused suites from the repository root:

```console
python3 tools/verify_finance_collection_runtime.py
python3 -m unittest -v tests.test_finance_collection_runtime_plan
python3 -m unittest -v tests.test_finance_collection_runtime tests.test_finance_collection_cli
```

The protected PostgreSQL job must additionally prove first and repeated
bootstrap produce exactly 185, an injected 186th permission is rejected, and
the complete PostgreSQL migration and runtime suites pass. A local skip is not
transition evidence. Exact-head review and exact-main verification are still
required before #214 can be accepted.
