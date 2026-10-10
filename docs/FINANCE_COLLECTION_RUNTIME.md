# Provider finance collection runtime candidate

This document records the schema-16 / plan-v6 checkpoint. The owner-approved
schema-17 successor is specified in [PostgreSQL collection runtime](FINANCE_COLLECTION_POSTGRES_RUNTIME.md)
and plan v7. The historical 185/186 boundary below remains unchanged for schemas
15/16; the successor adds its own fixed 199/200 boundary and open acceptance gates.

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

## OpenAI Costs response metadata

The fixed `openai.organization-costs.v1` parser accepts optional
`start_time_iso` / `end_time_iso` aliases only when they explicitly denote UTC
(`Z` or `+00:00`) and equal the required numeric bucket boundaries. Other
offsets, naive dates and precision beyond six fractional digits are rejected.
The query and Usage-profile parsers retain their existing contracts.

Optional `organization_name` and `project_name` labels must be null or valid
Unicode strings of at most 2048 UTF-8 bytes; validated labels are discarded.
`user_email` must remain null or absent, as this profile does not group by
user. A non-null `organization_id` must match the immutable selected source
binding through the same tenant-scoped `provider-account` HMAC and key version;
it cannot be matched against the Hormuz tenant ID. Both collection and file
import receive this binding-derived verification context. Missing context or
a different provider account is rejected. Only the fingerprint travels in
ephemeral prepared/normalized objects, and publication rechecks the binding
and context. No raw account ID, new database column or label is stored.

Omitted/null account metadata remains compatible. Unknown fields, amount and
grouping validation, coverage, pagination and provider/invoice finality rules
remain strict; accepting this metadata does not allocate account costs to jobs
or establish live-provider acceptance.

OpenAI Costs quantities retain their exact JSON numeric text. In addition to
the previously accepted units, the parser and normalized validation accept
`1000_tokens`, `duration_seconds`, `duration_minutes`, `duration_hours` and
`gibibyte_hours`. A numeric quantity may have a null or omitted unit; no unit
is inferred and no quantity conversion occurs. Quantity and unit remain in
the observation digest, and unit remains in its semantic identity. The
existing nullable text storage needs no migration. Anthropic quantity fields,
unknown units and invalid numeric/unit types remain rejected; provider and
invoice finality remain false.

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
python3 tools/verify_finance_collection_postgres_runtime.py
python3 -m unittest -v tests.test_finance_collection_runtime_plan
python3 -m unittest -v tests.test_finance_collection_runtime tests.test_finance_collection_cli
```

The protected PostgreSQL job must additionally prove first and repeated
bootstrap produce exactly 185, an injected 186th permission is rejected, and
the complete PostgreSQL migration and runtime suites pass. A local skip is not
transition evidence. Exact-head review and exact-main verification are still
required before #214 can be accepted.
