-- REVIEW ONLY: proposed v1.3 SQLite-13/PostgreSQL-18 successor ACL.
-- This file is not a migration and must not be applied directly.
-- The runtime role receives exactly SELECT and INSERT on the three new
-- append-only, tenant-RLS tables. No UPDATE, DELETE, TRUNCATE, DDL, sequence,
-- schema, routine, ownership, grant-option, policy, or bypass-RLS privilege is
-- requested. The accepted migration must replace placeholders safely and pin
-- the measured complete non-owner ACL fingerprint for schema 18.
GRANT SELECT, INSERT ON {schema}.portfolio_finance_account_binding_versions TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.gateway_finance_attempt_account_bindings TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_query_audit_events TO {runtime_role};
