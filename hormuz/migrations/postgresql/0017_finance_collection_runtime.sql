-- Owner-approved finance collection runtime boundary: exactly 14 new grants.
-- Schema 16 remains immutable; schemas 15/16 retain their literal 185 ACLs.
-- Existing forced RLS, append-only triggers and source-reader guards remain.
GRANT SELECT, INSERT ON {schema}.portfolio_finance_source_binding_versions TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_collection_attempts TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_collection_events TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_snapshots TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_snapshot_bucket_coverage TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_usage_observations TO {runtime_role};
GRANT SELECT, INSERT ON {schema}.portfolio_finance_cost_observations TO {runtime_role};
