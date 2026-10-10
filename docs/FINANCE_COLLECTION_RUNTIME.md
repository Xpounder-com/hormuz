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

The fixed `openai.organization-costs.v1` parser treats required numeric bucket
epochs as authoritative. Optional `start_time_iso` / `end_time_iso` aliases
may be absent or null. Non-null aliases accept bounded ASCII calendar dates
and times with `T` or a space, `Z`/`z` or standard numeric offsets, and up to
nine fractional digits. A missing timezone means UTC for these redundant
aliases; explicit offsets must convert to the exact numeric UTC boundary.
Negative-zero offsets, invalid dates/offsets and any nonzero fraction are
rejected, as integer-second epochs cannot agree with a fractional instant.
No fraction is silently truncated or coverage reinterpreted. The query and
Usage-profile parsers retain their existing contracts. This compatibility
rule does not claim a particular live provider timestamp spelling.

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

## OpenAI provider decimal policy extension v1

The current Costs runtime applies the versioned
[OpenAI provider decimal policy](finance-openai-provider-decimal-policy-v1.json)
as an explicit profile exception to the frozen generic numeric domains in
`finance-collection-contract-v1.json`. Required JSON-number amounts and optional
quantities keep their original numeric text, bounded to 128 ASCII bytes. Their
exact values permit 18 integer digits and 36 fractional places, with magnitude
strictly below `10^18` and at most 54 coefficient digits after removing only
insignificant trailing zeroes. Canonical amounts never pass through binary
floats, rounding or ambient `Decimal.normalize()`.

The same policy validates stored OpenAI observations, selected provider cost
subtotals and comparable-account variance. Arithmetic uses the owned 96-digit
context with inexact/rounded operations trapped. Reports remain limited to
10,000 rows; even valid individual rows can make a subtotal or variance whose
absolute magnitude reaches `10^18`. That result is unavailable, never rounded
or clipped. Canonical text fits the existing 128-character SQLite/PostgreSQL
columns, so no migration or numeric cast is introduced.

Configured rate/estimate values, the legacy `ProviderAmount` foundation, and
Anthropic native/converted money retain the existing 18-place policy. Old
accepted observations retain their native/canonical values, semantic identity
and digests; schema/profile identities and provider/invoice finality do not
change. The historical contract and its proof hashes remain unchanged.
This bounded extension does not claim the failed live response's field/value
is known or that a 36-place bound has been live-qualified.

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
