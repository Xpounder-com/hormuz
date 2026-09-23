# v1.3 finance successor schema and ACL proposal

This document makes the remaining storage decision for finance account binding
and privileged finance-report reads reviewable. It proposes one combined
successor: SQLite schema 13 and PostgreSQL schema 18. It is not a migration,
does not alter a database, and does not authorize a production rollout.

## Decision requested

Approve exactly six PostgreSQL runtime-role privileges: `SELECT` and `INSERT`
on each of the following new append-only, tenant-scoped tables:

1. `portfolio_finance_account_binding_versions`
2. `gateway_finance_attempt_account_bindings`
3. `portfolio_finance_query_audit_events`

The literal proposal is
[`finance-successor-schema-acl-proposal.sql`](finance-successor-schema-acl-proposal.sql).
No other role or privilege changes are requested. In particular, the proposal
adds no update, delete, truncate, DDL, sequence, schema, routine, ownership,
grant-option, policy-management, superuser, or bypass-RLS authority.

The first two tables are the already reviewed account-registration and
attempt-capture design from `finance-transition-plan-v8.json`. The third table
closes the release-blocking audit gap in `hormuz finance report`: every
successful privileged report read must commit its metadata-only query audit in
the same tenant transaction before any result is returned.

## Query-audit row

`portfolio_finance_query_audit_events` is an immutable append-only table with
one row per successful privileged report. Its closed v1 evidence contains:

- `organization_id`, a gateway-generated `query_event_id`, `actor_id`, and the
  fixed query class `finance_coverage_report_v1`;
- the bounded source scope: `binding_id`, `binding_version`,
  `collection_profile`, canonical start/end timestamps, resolved collection
  cutoff, and normalized currency;
- result-count metadata only: selected snapshot, coverage-bucket,
  provider-observation, terminal-attempt, and missing-sidecar counts;
- `occurred_at` and canonical `evidence_json` containing exactly the same
  metadata fields.

The row stores no provider payload, provider account identifier, credential,
prompt, response, title, body, comment, source, filename, employee score, or
free-form text. All identifiers, timestamps, counts, query classes, profiles,
and currencies remain closed and bounded by the existing report contract.

The repository must authorize the token-derived tenant and current
`portfolio_admin` role before query planning or storage access. It validates
the complete result, appends the query row and the matching
`hormuz.finance-query-audit-event` v1 source to the finite v2 commit-audit
chain, commits, and only then returns the report. An audit or chain failure
rolls back the read transaction and returns no successful result.

## Storage controls

All three new tables use the existing tenant key, forced PostgreSQL RLS, and
statement-level mutation-rejection trigger. SQLite operations remain explicitly
tenant-qualified with immutable update/delete/replace guards. Account-binding
versions use compare-and-swap append semantics. Each new attempt captures one
bound or explicitly unbound sidecar atomically with the attempt root before
provider egress. Historical attempts are not backfilled or inferred.

Schema 18 must extend the finite v2 audit-source union for the account-binding
version, attempt-binding sidecar, and finance-query audit sources while
preserving all earlier rows, heads, epochs, indexes, and source guards. The old
binary must refuse schema 13/18 and every partial or newer state. Rollback uses
a separately restored, quiesced predecessor database and matching old binary;
it never rewrites a database that received successor writes.

## ACL acceptance evidence

The current schema-17 complete non-owner ACL boundary is 199 canonical entries
with SHA-256
`1fa41892fb1206e7e70b922768ac27a39fce6ed98441a9fb78ce1511e1582906`.
The six proposed table privileges produce an expected count of 205 entries.
The implementation must measure the exact schema-18 digest twice from clean,
independently bootstrapped disposable PostgreSQL databases and pin that literal
digest in source. It must also measure an injected extra privilege, pin the
different rejected boundary, and prove both deployment bootstrap and runtime
verification reject it. The accepted digest is never computed from the
database under test at runtime.

Approval of this proposal authorizes source implementation and review of this
specific schema/ACL surface. Applying a migration to a persistent environment,
switching traffic, configuring live provider credentials, or claiming live
finance reconciliation remains a separate rollout action and evidence gate.
