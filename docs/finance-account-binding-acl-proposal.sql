-- Review-only v8 PostgreSQL successor proposal. This file is not a migration.
-- Schema 17 retains its literal 199-entry ACL and rejects this 203-entry state.
-- Applying these grants requires separate owner approval and a reviewed schema-18
-- migration with both real append-only, forced-RLS tables and audit guards.
GRANT SELECT, INSERT ON {schema}.portfolio_finance_account_binding_versions TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.gateway_finance_attempt_account_bindings TO {runtime_role};
